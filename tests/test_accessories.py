"""Accesorios que acompañan a la figura armada desde catálogo: receta, exportación aparte, lista de piezas y constructor."""
import json
import os
import shutil

import pytest
import trimesh

from afm import cli
from afm import props as P
from afm import solids as S
from afm.catalog import Catalog
from afm.gui.figure_builder import FigureBuilder
from afm.pipeline import attach_accessories_to_parts_list, parts_list_markdown
from afm.recipe import Recipe, RecipeError, accessory_fit, export_accessories

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECETA = os.path.join(ROOT, "recetas", "muneco.receta.json")


def _d(**over):
    d = json.load(open(RECETA))
    d.update(over)
    return d


def _sword(spec=None, hole=10.0):
    return S.to_printable_trimesh(P.make_prop(spec or P.GripSpec(), hole))


@pytest.fixture()
def cat(tmp_path):
    c = str(tmp_path / "linea")
    shutil.copytree(ROOT, c, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    cat = Catalog.open(c)
    cat.add_accessory(_sword(), "espada-prueba-01", P.accessory_grip(P.GripSpec(), 10.0), descripcion="espada")
    return cat


# ---------------------------------------------------------------- receta
def test_recipe_without_accessories_is_unchanged():
    r = Recipe.from_dict(_d())
    assert r.accesorios == [] and "accesorios" not in r.to_dict()


def test_recipe_accessories_round_trip(tmp_path):
    r = Recipe.from_dict(_d(accesorios=["espada-prueba-01@1", "escudo-01"]))
    assert r.to_dict()["accesorios"] == ["espada-prueba-01@1", "escudo-01"]
    path = str(tmp_path / "r.json")
    r.save(path)
    assert Recipe.load(path).accesorios == ["espada-prueba-01@1", "escudo-01"]


@pytest.mark.parametrize("acc, texto", [
    (["Espada Rara"], "accesorio"),
    (["espada-01", "espada-01@2"], "repetido"),
    ("espada-01", "lista"),
])
def test_bad_accessories_are_rejected(acc, texto):
    with pytest.raises(RecipeError, match=texto):
        Recipe.from_dict(_d(accesorios=acc))


# ---------------------------------------------------------------- compatibilidad con la mano de la figura
def test_accessory_fit_cases(cat):
    acc = cat.meta("espada-prueba-01")
    assert accessory_fit(None, acc)["nivel"] == "sin_datos"                        # la figura no tiene mano de catálogo
    hand = cat.meta(Recipe.load(RECETA).lugares["mano"])
    f = accessory_fit(hand, acc)
    assert f["nivel"] == "no" and "no tiene agarre" in f["motivos"][0]            # mano lisa: no se sujeta
    hand.mecanismo = {"agarre": {"diametro_taladro_mm": 2.4, "centro": [0, 0, 3], "eje": [0, 0, 1], "largo_canal_mm": 10.0,
                                 "t_entrada_mm": -5.0, "t_salida_mm": 5.0, "apoyo_dentado_grados": 300.0}}
    assert accessory_fit(hand, acc)["nivel"] == "ok"
    bare = cat.meta("espada-prueba-01")
    bare.mecanismo = None
    assert accessory_fit(hand, bare)["nivel"] == "sin_datos"


# ---------------------------------------------------------------- exportación
def test_export_accessories_writes_the_stl_flat_on_the_bed(cat, tmp_path):
    r = Recipe.from_dict(_d(accesorios=["espada-prueba-01"]))
    out = str(tmp_path / "fig")
    info = export_accessories(r, cat, out, "muneco")
    assert len(info) == 1 and info[0]["pieza"] == "espada-prueba-01@1" and info[0]["archivo"] == "piezas/muneco_espada-prueba-01.stl"
    assert info[0]["compat"] == "no" and info[0]["sha256"]
    m = trimesh.load(os.path.join(out, info[0]["archivo"]))
    assert m.bounds[0][2] == pytest.approx(0.0, abs=1e-6) and m.is_watertight
    _, orig = cat.load("espada-prueba-01")
    assert m.extents == pytest.approx(orig.extents, abs=1e-4)                       # mm reales: no se reescala


def test_export_accessories_rejects_a_non_accessory(cat, tmp_path):
    r = Recipe.from_dict(_d(accesorios=[Recipe.load(RECETA).lugares["cabeza"]]))
    with pytest.raises(RecipeError, match="no un accesorio"):
        export_accessories(r, cat, str(tmp_path / "x"), "muneco")
    with pytest.raises(RecipeError, match="accesorio"):
        export_accessories(Recipe.from_dict(_d(accesorios=["no-existe-01"])), cat, str(tmp_path / "y"), "muneco")


def test_parts_list_gets_an_accessories_section(tmp_path):
    out = str(tmp_path)
    lst = {"formato": "afm-lista-piezas-1", "figura": "f", "piezas": {"torso": {"pieza": "t@1", "avisos": []}}}
    json.dump(lst, open(os.path.join(out, "f_lista_de_piezas.json"), "w"))
    acc = [{"pieza": "espada-01@1", "archivo": "piezas/f_espada-01.stl", "compat": "aviso", "sha256": "ab" * 32,
            "motivos": [], "avisos": ["agarre flojo"]}]
    assert attach_accessories_to_parts_list(out, "f", acc) is True
    md = open(os.path.join(out, "f_lista_de_piezas.md"), encoding="utf-8").read()
    assert "## Accesorios" in md and "espada-01@1" in md and "agarre flojo" in md
    assert json.load(open(os.path.join(out, "f_lista_de_piezas.json")))["accesorios"] == acc
    assert attach_accessories_to_parts_list(str(tmp_path / "otra"), "f", acc) is False      # sin lista no hace nada
    assert "Accesorios" not in parts_list_markdown(lst)


def test_armar_exports_the_accessory_next_to_the_figure(cat, tmp_path, capsys):
    r = Recipe.from_dict(_d(accesorios=["espada-prueba-01"]))
    rp = str(tmp_path / "con_espada.receta.json")
    r.save(rp)
    out = str(tmp_path / "fig")
    code = cli.main(["armar", cat.root, rp, "--out", out, "--name", "muneco"])
    txt = capsys.readouterr().out
    assert code == 0, txt
    assert "Exportando los accesorios" in txt and "AVISO (espada-prueba-01@1)" in txt
    assert os.path.exists(os.path.join(out, "piezas", "muneco_espada-prueba-01.stl"))
    info = json.load(open(os.path.join(out, "muneco_informe.json")))
    assert info["archivos"]["accesorios"] == ["piezas/muneco_espada-prueba-01.stl"] and info["accesorios"][0]["compat"] == "no"
    lst = json.load(open(os.path.join(out, "muneco_lista_de_piezas.json")))
    assert lst["accesorios"][0]["pieza"] == "espada-prueba-01@1"
    # el accesorio acompaña a la figura pero NO está en el ensamblado (no va en el puño)
    assert not any("espada" in p for p in info["archivos"].get("piezas", []))
    assert json.load(open(os.path.join(out, "muneco.receta.json")))["accesorios"] == ["espada-prueba-01"]   # la receta se guarda tal cual


def test_usages_include_recipe_accessories(cat):
    os.makedirs(os.path.join(cat.root, "figuras"), exist_ok=True)
    Recipe.from_dict(_d(accesorios=["espada-prueba-01@1"])).save(os.path.join(cat.root, "figuras", "x.receta.json"))
    hits = cat.usages("espada-prueba-01")
    assert [h["lugar"] for h in hits] == ["accesorio1"]


# ---------------------------------------------------------------- constructor de figuras (sin GTK)
def test_figure_builder_accessory_choices(cat):
    b = FigureBuilder(cat.root, "mi-figura")
    cat.add_accessory(_sword(P.GripSpec(barb_d=3.0)), "espada-prueba-01", P.accessory_grip(P.GripSpec(barb_d=3.0), 10.0), new_version=True)
    assert [m.ref for m in b.accessory_pieces(cat)] == ["espada-prueba-01@1", "espada-prueba-01@2"]
    b.set_accessory("espada-prueba-01@1")
    b.set_accessory("espada-prueba-01@2")                                        # otra versión del mismo id: reemplaza
    assert b.accessories == ["espada-prueba-01@2"]
    assert b.recipe().accesorios == ["espada-prueba-01@2"]
    with pytest.raises(ValueError, match="no un accesorio"):
        b.set_accessory(Recipe.load(RECETA).lugares["cabeza"])
    b.set_accessory("espada-prueba-01", False)
    assert b.accessories == []


def test_figure_builder_labels_follow_the_chosen_hand(cat):
    b = FigureBuilder(cat.root, "mi-figura")
    assert set(b.accessory_fits(cat).values()) == {"sin_datos"}                  # sin mano elegida
    b.set_choice("mano", Recipe.load(RECETA).lugares["mano"])
    fits = b.accessory_fits(cat)
    assert fits == {"espada-prueba-01@1": "no"}
    assert b.accessory_label(cat.meta("espada-prueba-01"), fits) == "espada-prueba-01@1 — no se sujeta"


def test_figure_builder_load_recipe_restores_accessories(cat, tmp_path):
    p = str(tmp_path / "r.json")
    Recipe.from_dict(_d(accesorios=["espada-prueba-01@1", "fantasma-01"])).save(p)
    b = FigureBuilder(cat.root, "x")
    warns = b.load_recipe(p)
    assert b.accessories == ["espada-prueba-01@1"] and any("fantasma-01" in w for w in warns)
