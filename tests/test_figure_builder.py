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


# ---------------------------------------------------------------------- abrir receta, mano y escalas
def test_hand_adjustments_go_to_the_recipe(root):
    b = FB.FigureBuilder(root, "con-mano")
    for place, ref in (("torso", "torso-muneco-01"), ("brazo", "brazo-muneco-01"), ("mano", "mano-muneco-01")):
        b.set_choice(place, ref)
    assert b.recipe().ajustes["mano"] == {"yaw": 0.0, "union": "solape", "solape": 0.5}
    b.set_hand(yaw=35, union="giro")
    assert b.recipe().ajustes["mano"] == {"yaw": 35.0, "union": "giro"}          # con «giro» el solape no aplica
    b.set_hand(union="solape", solape=1.2)
    assert b.recipe().ajustes["mano"] == {"yaw": 35.0, "union": "solape", "solape": 1.2}
    with pytest.raises(ValueError, match="unión"):
        b.set_hand(union="pegamento")
    with pytest.raises(ValueError, match="giro de la mano"):
        b.set_hand(yaw=900)
    with pytest.raises(ValueError, match="solape"):
        b.set_hand(solape=-1)
    assert b.hand["yaw"] == 35.0 and b.recipe().problems() == []
    b.set_choice("mano", None)
    assert "mano" not in b.recipe().ajustes


def test_scales_per_place_uniform_or_per_axis(root):
    b = FB.FigureBuilder(root, "escalada")
    b.set_choice("torso", "torso-muneco-01")
    b.set_choice("brazo", "brazo-muneco-01")
    b.set_scale("brazo", 1.1)
    b.set_scale("torso", (1, 1.15, 1))
    assert b.scale_of("brazo") == [1.1, 1.1, 1.1] and b.scale_of("pierna") == [1.0, 1.0, 1.0]
    r = b.recipe()
    assert r.ajustes["escalas"] == {"brazo": 1.1, "torso": [1.0, 1.15, 1.0]} and r.problems() == []
    b.set_scale("brazo", (1, 1, 1))                                              # ×1 la quita
    assert "brazo" not in b.recipe().ajustes["escalas"]
    b.set_scale("pierna", 1.2)                                                   # sin pieza en ese lugar: no va a la receta
    assert "pierna" not in b.recipe().ajustes["escalas"]
    for bad in (0, -1, (1, 2), float("nan")):
        with pytest.raises(ValueError, match="escala"):
            b.set_scale("torso", bad)
    with pytest.raises(ValueError, match="no se escala"):
        b.set_scale("mano", 1.1)


def test_open_a_saved_recipe_to_touch_it_up(root):
    b = FB.FigureBuilder(root, "original")
    for place, ref in (("torso", "torso-muneco-01"), ("cabeza", "cabeza-muneco-01"), ("brazo", "brazo-muneco-01"),
                       ("mano", "mano-muneco-01")):
        b.set_choice(place, ref)
    b.options["clic_hombro"] = True
    b.set_hand(yaw=-20, union="giro")
    b.set_scale("brazo", (1.05, 1, 1.1))
    path = b.save_recipe()

    c = FB.FigureBuilder(root)                                                    # ventana nueva: se abre la receta
    assert c.load_recipe(path) == []
    assert c.name == "original" and c.choices == b.choices and c.options == b.options
    assert c.hand["yaw"] == -20.0 and c.hand["union"] == "giro" and c.scale_of("brazo") == [1.05, 1.0, 1.1]
    c.set_hand(yaw=0, union="solape")                                            # se retoca y se guarda con otro nombre
    c.set_scale("brazo", 1)
    c.name = "retocada"
    again = json.load(open(c.save_recipe()))
    assert again["ajustes"]["mano"]["yaw"] == 0 and "escalas" not in again["ajustes"]
    assert json.load(open(path))["ajustes"]["escalas"] == {"brazo": [1.05, 1.0, 1.1]}      # la original no se toca


def test_open_recipe_with_missing_pieces_or_other_line(root, tmp_path):
    b = FB.FigureBuilder(root, "x")
    for place, ref in (("torso", "torso-muneco-01"), ("brazo", "brazo-muneco-01")):
        b.set_choice(place, ref)
    b.set_scale("brazo", 1.2)
    d = b.recipe().to_dict()
    d["lugares"]["brazo"] = "brazo-borrado-09@1"                                  # la pieza ya no está en el catálogo
    p = tmp_path / "vieja.receta.json"
    p.write_text(json.dumps(d))
    c = FB.FigureBuilder(root)
    warns = c.load_recipe(str(p))
    assert any("brazo" in w for w in warns) and any("escala de «brazo»" in w for w in warns)
    assert c.choices["brazo"] is None and c.choices["torso"] == "torso-muneco-01@1" and "brazo" not in c.scales
    d["linea"] = "otra-linea"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="otra-linea"):
        FB.FigureBuilder(root).load_recipe(str(p))
    p.write_text("{no es json")
    with pytest.raises(ValueError, match="no se pudo abrir"):
        FB.FigureBuilder(root).load_recipe(str(p))
    d.update(linea="ejemplo-100", formato="otra-cosa")
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="formato"):
        FB.FigureBuilder(root).load_recipe(str(p))
