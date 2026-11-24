"""Simulación del tamaño de un arma: en el puño de la mano estándar y enfundada frente a la figura (`afm.weapon_fit`)."""
import json

import numpy as np
import pytest
import trimesh

from afm import cli
from afm import props as P
from afm import solids as S
from afm import weapon_fit as WF
from afm.catalog import Catalog
from afm.gui import weapon_sim as WS
from afm.i18n import tr

from test_weapon import cat_hand                                  # noqa: F401,E402  (fixtures)
from test_gui_hand_place import std_hand                          # noqa: F401,E402

GRIP = P.GripSpec()


def _prop(blade_len: float = 34.0, blade_w: float = 4.0, guard_w: float = 10.0) -> trimesh.Trimesh:
    return S.to_printable_trimesh(P.make_prop(GRIP, 10.0, blade_len=blade_len, blade_w=blade_w, guard_w=guard_w))


def _sim(cat, prop, height=100.0):
    _meta, hand, fit = WF.load_hand_with_grip(cat)
    return WF.simulate_in_hand(prop, hand, fit, height)


def test_standard_prop_in_hand_measures_reach_and_guard(cat_hand):          # noqa: F811
    sim = _sim(cat_hand, _prop())
    # hoja 34 mm + guarda 2 mm por delante del puño
    assert sim.reach_mm == pytest.approx(36.0, abs=0.5)
    assert sim.guard_mm == pytest.approx(10.0, abs=0.5)
    assert sim.overlap_mm3 <= WF.OVERLAP_MM3
    assert sim.reach_vs_figure == pytest.approx(sim.reach_mm / 100.0)
    assert sim.tilt_deg < 10.0
    assert "choca_mano" not in dict(sim.notes)


def test_tiny_and_huge_weapons_are_flagged(cat_hand):                       # noqa: F811
    tiny = _sim(cat_hand, _prop(blade_len=6.0))
    codes = [c for c, _v in tiny.notes]
    assert "corta_vs_mano" in codes or "corta_vs_figura" in codes
    assert tiny.level == WF.LEVEL_WARN
    huge = _sim(cat_hand, _prop(blade_len=80.0))
    assert "larga_vs_figura" in [c for c, _v in huge.notes]


def test_a_blade_that_enters_the_hand_is_an_error(cat_hand):                # noqa: F811
    """La mano de prueba es un bloque que empieza en la cara de entrada; le sumamos un «pulgar» que asoma delante de ella."""
    _meta, hand, fit = WF.load_hand_with_grip(cat_hand)
    thumb = trimesh.creation.box((6.0, 12.0, 6.0))
    thumb.apply_translation((-1.0, 0.0, 0.0))                              # marco del accesorio: x ∈ [−4, 2]
    thumb.apply_transform(P.prop_to_hand_matrix(fit))
    hand_thumb = S.to_printable_trimesh(S.union(S.to_manifold(hand), S.to_manifold(thumb)))
    sim = WF.simulate_in_hand(_prop(), hand_thumb, fit, 100.0)
    assert "choca_mano" in [c for c, _v in sim.notes] and sim.level == WF.LEVEL_NO and sim.overlap_mm3 > WF.OVERLAP_MM3
    assert WF.simulate_in_hand(_prop(), hand, fit, 100.0).overlap_mm3 <= WF.OVERLAP_MM3      # sin el pulgar, no toca


def test_gap_to_the_hand_is_measured_when_close(cat_hand):                 # noqa: F811
    sim = _sim(cat_hand, _prop())
    assert sim.gap_mm is None or sim.gap_mm >= 0.0


def test_accessory_without_blade_is_rejected(cat_hand):                    # noqa: F811
    _meta, hand, fit = WF.load_hand_with_grip(cat_hand)
    stub = S.to_printable_trimesh(P.grip_stub(GRIP, 10.0, overlap=0.0).translate((0, 0, 0)))
    with pytest.raises(WF.FitError):
        WF.simulate_in_hand(stub, hand, fit)


def test_catalog_without_grip_hand_fails_clearly(tmp_path):
    from afm.weapon import WeaponError

    cat = Catalog.create(str(tmp_path / "vacia"), "linea-vacia", 100.0)
    with pytest.raises(WeaponError, match="ninguna mano con agarre"):
        WF.load_hand_with_grip(cat)


# ---------------------------------------------------------------- en el cuerpo
def _parts():
    """Figura mínima de 100 mm de alto (z hacia arriba) con cabeza, brazo y pierna del lado izquierdo (+X)."""
    def box(size, center):
        b = trimesh.creation.box(size)
        b.apply_translation(center)
        return b

    return [("torso", box((20, 10, 40), (0, 0, 50)), None), ("cabeza", box((12, 12, 20), (0, 0, 90)), None),
            ("brazo", box((4, 4, 30), (13, 0, 55)), None), ("pierna", box((8, 8, 30), (6, 0, 15)), None)]


def _sword(length=40.0, center=(20.0, 0.0, 40.0)):
    s = trimesh.creation.box((1.0, 2.0, length))
    s.apply_translation(center)
    return s


def test_sheathed_weapon_measures_height_floor_and_head():
    fit = WF.simulate_on_body(_sword(40.0, (30.0, 0.0, 50.0)), _parts(), "izq")
    assert fit.figure_mm == pytest.approx(100.0) and fit.length_vs_figure == pytest.approx(0.40, abs=0.01)
    assert fit.floor_gap_mm == pytest.approx(30.0, abs=0.1) and fit.top_over_head_mm == pytest.approx(-30.0, abs=0.1)
    assert fit.notes == [] and fit.level == WF.LEVEL_OK


def test_weapon_through_the_floor_or_the_arm_is_an_error():
    fit = WF.simulate_on_body(_sword(40.0, (30.0, 0.0, 10.0)), _parts(), "izq")       # punta en z = −10
    assert "atraviesa_piso" in [c for c, _v in fit.notes] and fit.level == WF.LEVEL_NO
    arm = WF.simulate_on_body(_sword(40.0, (13.0, 0.0, 55.0)), _parts(), "izq")       # clavada en el brazo
    assert "brazo" in arm.limb_overlap_mm3 and "choca_miembro" in [c for c, _v in arm.notes]
    other_side = WF.simulate_on_body(_sword(40.0, (13.0, 0.0, 55.0)), _parts(), "der")  # el brazo de ese lado no es el «_der»
    assert other_side.limb_overlap_mm3 == {}


def test_short_and_long_weapons_vs_figure():
    small = WF.simulate_on_body(_sword(10.0, (30.0, 0.0, 50.0)), _parts(), "izq")
    assert "corta_vs_figura" in [c for c, _v in small.notes]
    big = WF.simulate_on_body(_sword(80.0, (30.0, 0.0, 50.0)), _parts(), "izq")
    assert "larga_vs_figura" in [c for c, _v in big.notes]
    with pytest.raises(WF.FitError):
        WF.simulate_on_body(_sword(), [], "izq")


# ---------------------------------------------------------------- textos
def test_every_note_code_has_text_in_every_language():
    from afm import i18n

    variables = {"mm3": 1, "guarda": 1, "puno": 1, "alcance": 1, "mano": 1, "pct": 1, "grados": 1, "mm": 1, "pieza": "brazo"}
    try:
        for lang in ("es", "en", "pt"):
            i18n.set_language(lang)
            for code in WF.NOTE_CODES:
                txt = WS.note_text(code, variables)
                assert txt and "weapon_sim" not in txt, (lang, code)
    finally:
        i18n.set_language("es")


def test_hand_and_body_lines(cat_hand):                                    # noqa: F811
    lines = WS.hand_lines(_sim(cat_hand, _prop()))
    assert lines[0].startswith("En la mano estándar") and len(lines) >= 2
    body = WS.body_lines(WF.simulate_on_body(_sword(40.0, (30.0, 0.0, 50.0)), _parts(), "izq"))
    assert "40 %" in body[0] and body[-1] == tr("weapon_sim.razonable")


# ---------------------------------------------------------------- línea de comandos
def test_cli_simular_arma_writes_hand_and_weapon(cat_hand, tmp_path, capsys):   # noqa: F811
    cat_hand.add_accessory(_prop(), "espada-prueba-01", P.accessory_grip(GRIP, 10.0))
    out = str(tmp_path / "sim")
    assert cli.main(["simular-arma", cat_hand.root, "espada-prueba-01", "--out", out]) == 0
    text = capsys.readouterr().out
    assert "espada-prueba-01 en" in text and "En la mano estándar" in text
    mesh = trimesh.load(f"{out}/arma_en_mano.stl")
    assert mesh.bounds[1][0] - mesh.bounds[0][0] > 0
    assert cli.main(["simular-arma", cat_hand.root, "no-existe-01"]) == 2


def test_cli_result_is_json_serialisable(cat_hand):                        # noqa: F811
    cat_hand.add_accessory(_prop(), "espada-prueba-01", P.accessory_grip(GRIP, 10.0))
    res = cli.run_simular_arma(cat_hand.root, "espada-prueba-01")
    res.pop("_sim")
    assert json.loads(json.dumps(res))["alcance_mm"] == pytest.approx(36.0, abs=0.5)
