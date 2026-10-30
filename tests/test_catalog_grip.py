"""Catálogo: la mano con agarre y los accesorios (`mano` + `accesorio`), compatibilidad por agarre, verificación e importación."""
import copy

import numpy as np
import pytest
import trimesh

from afm import props as P
from afm import solids as S
from afm.catalog import Catalog
from afm.cli import main
from afm.hand import load_standard_hand, save_standard_hand

from test_gui_hand_place import std_hand                                  # noqa: F401  (fixture)

HAND_AG = {"diametro_taladro_mm": 2.4, "centro": [0.0, 0.0, 3.0], "eje": [0.0, 0.0, 1.0], "largo_canal_mm": 10.0,
           "t_entrada_mm": -5.0, "t_salida_mm": 5.0, "pared_min_mm": 2.0, "apoyo_dentado_grados": 300.0}


def acc_ag(**kw):
    sp = P.GripSpec()
    d = P.accessory_grip(sp, 10.0)
    d.update(kw)
    return d


# ---------------------------------------------------------------- compatibilidad barata
def test_compat_ok_for_matching_grip():
    c = P.grip_compat(HAND_AG, acc_ag())
    assert c.nivel == "ok" and c.margenes_mm["interferencia"] == pytest.approx(0.25)


@pytest.mark.parametrize("change, texto", [
    ({"taladro_mm": 2.8}, "taladro de Ø2.8"),
    ({"vastago_mm": 2.4}, "no entra"),
    ({"dentado_mm": 2.4}, "no retiene"),
    ({"largo_canal_mm": 8.0, "juego_axial_mm": 0.4}, "dentro del canal"),
    ({"dentado_mm": 5.0}, "se rompe"),
])
def test_compat_no(change, texto):
    c = P.grip_compat(HAND_AG, acc_ag(**change))
    assert c.nivel == "no" and any(texto in m for m in c.motivos), c.motivos


def test_compat_warnings():
    assert P.grip_compat({**HAND_AG, "apoyo_dentado_grados": 180.0}, acc_ag()).nivel == "aviso"
    c = P.grip_compat(HAND_AG, acc_ag(largo_canal_mm=12.5))
    assert c.nivel == "aviso" and any("juego axial" in a for a in c.avisos)
    assert P.grip_compat(HAND_AG, acc_ag(dentado_mm=3.6)).nivel == "aviso"           # ε ≈ 1,05 %: entre 1 y 2 veces el máximo


def test_grip_problems():
    assert P.grip_problems(HAND_AG, "mano") == [] and P.grip_problems(acc_ag(), "accesorio") == []
    assert any("falta" in x for x in P.grip_problems({"eje": [0, 0, 1]}, "mano"))
    assert any("vástago < taladro < dentado" in x for x in P.grip_problems(acc_ag(dentado_mm=1.5), "accesorio"))
    assert P.grip_problems("x", "mano") == ["agarre: no es un objeto"]


# ---------------------------------------------------------------- catálogo
@pytest.fixture()
def cat(tmp_path):
    return Catalog.create(str(tmp_path / "cat"), "linea-prueba", 100.0)


@pytest.fixture()
def grip_hand(tmp_path, std_hand):                                        # noqa: F811
    hand, meta = load_standard_hand(std_hand)
    path = str(tmp_path / "mano-agarre.stl")
    save_standard_hand(path, hand, {**meta, "agarre": HAND_AG})
    return path


def _sword(sp=None, hole=10.0):
    sp = sp or P.GripSpec()
    return S.to_printable_trimesh(P.make_prop(sp, hole))


def test_hand_piece_keeps_the_grip_and_verify_is_clean(cat, grip_hand):
    meta, _ = cat.import_hand(grip_hand, "mano-agarre-01")
    assert meta.tipo == "mano" and meta.mecanismo["agarre"]["diametro_taladro_mm"] == 2.4
    assert cat.meta("mano-agarre-01").mecanismo["agarre"]["largo_canal_mm"] == 10.0     # sobrevive al disco
    assert cat.verify().problems == []
    assert "agarre Ø2.4" in open(f"{cat.root}/CATALOGO.md", encoding="utf-8").read()


def test_plain_hand_has_no_grip(cat, std_hand):                           # noqa: F811
    meta, _ = cat.import_hand(std_hand, "mano-lisa-01")
    assert not (meta.mecanismo or {}).get("agarre") and cat.compatible_grips("mano-lisa-01") == []


def test_accessory_piece_roundtrip_and_compat_both_ways(cat, grip_hand):
    cat.import_hand(grip_hand, "mano-agarre-01")
    acc, warns = cat.add_accessory(_sword(), "espada-prueba-01", acc_ag(), descripcion="espada")
    assert acc.tipo == "accesorio" and acc.puertos == {} and warns == []
    assert cat.verify().problems == []
    sirve = cat.compatible_grips("mano-agarre-01")
    assert [(o.ref, c.nivel) for o, c in sirve] == [("espada-prueba-01@1", "ok")]
    back = cat.compatible_grips("espada-prueba-01")
    assert [(o.ref, c.nivel) for o, c in back] == [("mano-agarre-01@1", "ok")]


def test_incompatible_accessory_is_not_listed(cat, grip_hand):
    cat.import_hand(grip_hand, "mano-agarre-01")
    cat.add_accessory(_sword(P.GripSpec(bore_d=2.8, barb_d=3.3, shaft_d=2.4)), "espada-gruesa-01",
                      P.accessory_grip(P.GripSpec(bore_d=2.8, barb_d=3.3, shaft_d=2.4), 10.0))
    assert cat.compatible_grips("mano-agarre-01") == []


def test_verify_flags_a_broken_grip(cat, grip_hand):
    cat.import_hand(grip_hand, "mano-agarre-01")
    cat.add_accessory(_sword(), "espada-prueba-01", acc_ag())
    import json, os
    path = os.path.join(cat.root, "piezas", "accesorio", "espada-prueba-01@1.pieza.json")
    d = json.load(open(path, encoding="utf-8"))
    del d["mecanismo"]["agarre"]["dentado_mm"]
    json.dump(d, open(path, "w", encoding="utf-8"))
    assert any("falta «dentado_mm»" in p for p in cat.verify().problems)


def test_accessory_piece_needs_a_valid_grip(cat):
    with pytest.raises(Exception, match="vástago < taladro < dentado"):
        cat.add_accessory(_sword(), "espada-mala-01", acc_ag(dentado_mm=1.5))


# ---------------------------------------------------------------- CLI
def test_cli_import_accessory_and_list_compatible(cat, grip_hand, tmp_path, capsys):
    cat.import_hand(grip_hand, "mano-agarre-01")
    stl = str(tmp_path / "espada.stl")
    _sword().export(stl)
    assert main(["catalogo", "importar-accesorio", cat.root, stl, "--id", "espada-uno-01", "--largo-canal", "10"]) == 0
    out = capsys.readouterr().out
    assert "Accesorio guardado como espada-uno-01@1" in out and "sirve en mano-agarre-01@1: ok" in out
    assert main(["catalogo", "listar", cat.root, "--compatible-con", "mano-agarre-01"]) == 0
    out = capsys.readouterr().out
    assert "espada-uno-01@1" in out and "agarre ↔ agarre: ok" in out and "interferencia 0.25" in out


def test_catalog_window_detail_shows_the_grip(cat, grip_hand):
    from afm.gui.catalog_manager import detail_text

    cat.import_hand(grip_hand, "mano-agarre-01")
    cat.add_accessory(_sword(), "espada-prueba-01", acc_ag())
    assert "Agarre: taladro Ø2,4 mm · canal 10 mm" in detail_text(cat.meta("mano-agarre-01"))
    assert "Agarre: para taladro Ø2,4 mm · vástago Ø2 · dentado Ø2,9" in detail_text(cat.meta("espada-prueba-01"))
