"""Armar una figura desde cero solo con piezas del catálogo (ventana «Armar figura desde catálogo»), lógica sin GTK."""
import json
import os
import shutil

import pytest

from afm.gui import figure_builder as FB

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture()
def root(tmp_path):
    dst = str(tmp_path / "ejemplo")
    shutil.copytree(EJEMPLO, dst)
    return dst


def test_lists_pieces_by_place_and_marks_the_ones_that_fit(root):
    b = FB.FigureBuilder(root)
    assert [m.ref for m in b.pieces_for("torso")] == ["torso-muneco-01@1"]
    assert [m.ref for m in b.pieces_for("brazo")] == ["brazo-muneco-01@1"] and b.pieces_for("pelvis") == []
    assert b.fit_labels("brazo") == {}                                   # sin torso elegido no hay con qué comparar
    b.set_choice("torso", "torso-muneco-01")                             # sin versión: se fija la que hay
    assert b.choices["torso"] == "torso-muneco-01@1" and b.waist is False
    assert b.fit_labels("brazo")["brazo-muneco-01@1"] in ("ok", "aviso")
    assert b.label(b.pieces_for("brazo")[0], b.fit_labels("brazo")).startswith("brazo-muneco-01@1 — ")
    assert b.fit_labels("pierna") and b.anchor_for("mano") is None
    b.set_choice("brazo", "brazo-muneco-01@1")
    assert b.anchor_for("mano") == "brazo-muneco-01@1" and b.fit_labels("mano")


def test_wrong_piece_for_a_place_and_unknown_place_are_refused(root):
    b = FB.FigureBuilder(root)
    with pytest.raises(ValueError, match="pide «torso o torso_sup»"):
        b.set_choice("torso", "brazo-muneco-01@1")
    with pytest.raises(ValueError, match="no es un lugar"):
        b.set_choice("cola", None)
    with pytest.raises(ValueError, match="no hay ninguna pieza"):
        b.set_choice("brazo", "no-existe-01")
    assert all(v is None for v in b.choices.values())


def test_problems_guide_what_is_missing(root):
    b = FB.FigureBuilder(root, "con espacios")
    assert any("nombre" in p for p in b.problems()) and any("torso" in p for p in b.problems())
    b.name = "muneco-nuevo"
    b.set_choice("torso", "torso-muneco-01@1")
    assert b.problems() == []                                           # solo el torso alcanza (lo demás es opcional)
    b.choices["mano"] = "mano-muneco-01@1"
    assert any("elegí también un brazo" in p for p in b.problems())
    b.set_choice("brazo", "brazo-muneco-01@1")
    assert b.problems() == []
    r = b.recipe()
    assert r.linea == "ejemplo-100" and r.lugares["torso"] == "torso-muneco-01@1" and r.options()["cintura"] is False
    assert r.ajustes["mano"]["union"] == "solape" and r.problems() == []


def test_save_recipe_goes_to_the_catalog_and_counts_as_a_use(root):
    from afm.catalog import Catalog

    b = FB.FigureBuilder(root, "belgrano-nuevo")
    b.set_choice("torso", "torso-muneco-01@1")
    path = b.save_recipe()
    assert path == os.path.join(os.path.abspath(root), "figuras", "belgrano-nuevo.receta.json")
    assert json.load(open(path))["lugares"]["torso"] == "torso-muneco-01@1"
    uses = Catalog.open(root).usages("torso-muneco-01@1")
    assert [os.path.basename(u["archivo"]) for u in uses][-1] == "belgrano-nuevo.receta.json"
    b.choices["torso"] = None
    with pytest.raises(ValueError, match="torso"):
        b.save_recipe()


def test_generate_the_doll_from_scratch_and_export(root, tmp_path):
    b = FB.FigureBuilder(root, "muneco-desde-cero")
    for place, ref in (("torso", "torso-muneco-01"), ("cabeza", "cabeza-muneco-01"), ("brazo", "brazo-muneco-01"),
                       ("pierna", "pierna-muneco-01"), ("mano", "mano-muneco-01")):
        b.set_choice(place, ref)
    out = str(tmp_path / "salida")
    msgs = []
    info = b.generate(out, progress=msgs.append)
    assert info["validacion"]["ok"], info["validacion"]["problems"]
    assert os.path.isfile(info["ensamblada"]) and os.path.isfile(os.path.join(out, "muneco-desde-cero.receta.json"))
    assert os.path.isfile(os.path.join(out, "muneco-desde-cero_informe.json"))
    assert len(info["archivos"]["piezas"]) >= 4
    txt = FB.summary(info)
    assert "sin problemas" in txt and "Piezas exportadas" in txt
    bad = FB.FigureBuilder(root)
    with pytest.raises(ValueError, match="torso"):
        bad.generate(str(tmp_path / "x"))
