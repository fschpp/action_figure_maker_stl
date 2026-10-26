"""Recetas `afm-receta-1` y `afm armar` (Fase 6 del plan de piezas intercambiables)."""
import json
import os
import shutil

import pytest

from afm import cli
from afm.recipe import Recipe, RecipeError

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECETA = os.path.join(ROOT, "recetas", "muneco.receta.json")


def _d(**over):
    d = json.load(open(RECETA))
    d.update(over)
    return d


def test_example_recipe_loads_and_round_trips(tmp_path):
    r = Recipe.load(RECETA)
    assert r.personaje == "muneco" and r.parts_keys()["torso"] == "torso" and "pelvis" not in r.parts_keys()
    out = tmp_path / "r.json"
    r.save(str(out))
    assert Recipe.load(str(out)).to_dict() == r.to_dict()


def test_with_waist_the_torso_is_torso_sup():
    r = Recipe.from_dict(_d(opciones={"cintura": True}, lugares={**_d()["lugares"], "pelvis": "pelvis-x"}))
    assert r.parts_keys()["torso"] == "torso_sup" and r.parts_keys()["pelvis"] == "pelvis"


@pytest.mark.parametrize("cambio,texto", [
    ({"formato": "otra"}, "formato"),
    ({"lugares": {"torso": "torso-x", "cola": "cola-1"}}, "cola"),
    ({"opciones": {"alas": True}}, "alas"),
    ({"lugares": {"brazo": "brazo-1"}}, "torso"),
    ({"opciones": {"cintura": True}}, "pelvis"),
    ({"ajustes": {"mano": {"union": "pegada"}}}, "union"),
    ({"lugares": {"torso": "Torso Raro"}}, "torso"),
])
def test_bad_recipes_are_rejected(cambio, texto):
    with pytest.raises(RecipeError, match=texto):
        Recipe.from_dict(_d(**cambio))


def test_armar_cli_builds_the_doll_only_from_catalog_parts(tmp_path, capsys):
    out = str(tmp_path / "fig")
    cat = str(tmp_path / "linea")       # `afm armar` guarda compat.json junto al catálogo: no ensuciar el del repo
    shutil.copytree(ROOT, cat, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    code = cli.main(["armar", cat, RECETA, "--out", out])
    txt = capsys.readouterr().out
    assert code == 0, txt
    assert "Validación: OK" in txt and "Pieza de catálogo en cabeza" in txt
    for f in ("muneco_ensamblada.stl", "muneco_lista_de_piezas.json", "muneco_informe.json", "muneco.receta.json"):
        assert os.path.exists(os.path.join(out, f)), f
    lst = json.load(open(os.path.join(out, "muneco_lista_de_piezas.json")))
    assert {"brazo", "pierna", "cabeza"} <= set(lst["piezas"])


def test_armar_cli_reports_a_wrong_line(tmp_path, capsys):
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps(_d(linea="otra-linea")))
    assert cli.main(["armar", ROOT, str(bad), "--out", str(tmp_path / "o")]) == 2
    assert "otra-linea" in capsys.readouterr().err


# ---------------------------------------------------------------------- escalas por pieza en la receta
@pytest.mark.parametrize("escalas,texto", [
    ({"mano": 1.1}, "no se escala"),
    ({"cola": 1.1}, "no se escala"),
    ({"brazo": 0}, "escala de «brazo»"),
    ({"brazo": [1, 1]}, "escala de «brazo»"),
    ({"pelvis": 1.1}, "no tiene pieza"),
])
def test_bad_scales_are_refused(escalas, texto):
    with pytest.raises(RecipeError, match=texto):
        Recipe.from_dict(_d(ajustes={"escalas": escalas}))


def test_scales_round_trip_and_ignore_x1():
    r = Recipe.from_dict(_d(ajustes={"escalas": {"brazo": 1.1, "torso": [1, 1.1, 1], "pierna": 1.0}}))
    assert r.scales() == {"brazo": 1.1, "torso": [1.0, 1.1, 1.0]}               # ×1 no cuenta
    assert Recipe.from_dict(r.to_dict()).to_dict() == r.to_dict()


def test_scale_changes_the_piece_around_its_joint():
    from afm.recipe import load_choices

    base = Recipe.from_dict(_d())
    _, c0 = load_choices(base, ROOT)
    _, c1 = load_choices(Recipe.from_dict(_d(ajustes={"escalas": {"brazo": [1.0, 1.0, 1.2]}})), ROOT)
    h0, h1 = c0["brazo"].mesh.extents, c1["brazo"].mesh.extents
    assert h1[2] == pytest.approx(h0[2] * 1.2, rel=1e-6) and h1[0] == pytest.approx(h0[0]) and h1[1] == pytest.approx(h0[1])
    assert c1["brazo"].meta.origen["escala_pieza"] == [1.0, 1.0, 1.2] and "escala_pieza" not in c0["brazo"].meta.origen
    p0, p1 = c0["brazo"].meta.puertos["hombro"].frame.center, c1["brazo"].meta.puertos["hombro"].frame.center
    assert list(p0) == pytest.approx(list(p1))                                  # el hombro es el punto fijo
    assert c0["torso"].mesh.extents == pytest.approx(c1["torso"].mesh.extents)  # no mueve a las demás


def test_assemble_with_scaled_arm_and_hand_adjusts(tmp_path):
    from afm.recipe import assemble

    r = Recipe.from_dict(_d(ajustes={"mano": {"yaw": 25, "union": "solape", "solape": 0.8}, "escalas": {"brazo": [1, 1, 1.15]}}))
    a = assemble(r, ROOT)
    v = P_validate(a)
    assert v["ok"], v["problems"]
    assert a.res.report["hand"]
    ref = assemble(Recipe.from_dict(_d()), ROOT)
    arm, arm0 = a.res.pieces["brazo_izq"], ref.res.pieces["brazo_izq"]
    h, h0 = (m.bounding_box()[5] - m.bounding_box()[2] for m in (arm, arm0))
    assert h > h0 * 1.05                                                        # el brazo escalado es más alto


def P_validate(a):
    from afm import pipeline as P

    return P.validate(a.res, a.params, a.limbs, joints=a.joints)
