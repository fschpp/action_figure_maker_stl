"""«Armar y exportar» escribe un `.proyecto` que la ventana principal abre SIN el STL de origen: el cuerpo base sale del
catálogo y la figura se ve por partes (lógica sin GTK)."""
import json
import os
import shutil

import pytest

from afm import catalog_project as CP
from afm.gui import figure_builder as FB
from afm.gui.controller import Controller
from afm.gui.session import CatalogNeeded, Session

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Arma el muñeco una sola vez (tarda): catálogo copiado, carpeta de salida e informe."""
    base = tmp_path_factory.mktemp("armado")
    root = str(base / "ejemplo")
    shutil.copytree(EJEMPLO, root)
    b = FB.FigureBuilder(root, "muneco-sin-stl")
    for place, ref in (("torso", "torso-muneco-01"), ("cabeza", "cabeza-muneco-01"), ("brazo", "brazo-muneco-01"),
                       ("pierna", "pierna-muneco-01"), ("mano", "mano-muneco-01")):
        b.set_choice(place, ref)
    out = str(base / "salida")
    info = b.generate(out)
    return {"root": root, "out": out, "info": info, "path": os.path.join(out, "muneco-sin-stl.proyecto")}


def _copy(built, tmp_path, mutate=None) -> str:
    d = json.load(open(built["path"]))
    if mutate:
        mutate(d)
    path = str(tmp_path / "copia.proyecto")
    json.dump(d, open(path, "w"))
    return path


def test_armar_y_exportar_writes_a_project_without_stl(built):
    assert built["info"]["proyecto"] == built["path"] and os.path.isfile(built["path"])
    d = json.load(open(built["path"]))
    assert d["source"] is None
    assert CP.origin(d)["linea"] == "ejemplo-100" and CP.origin(d)["receta"]["lugares"]["torso"] == "torso-muneco-01@1"
    assert set(d["parts"]) == {"torso", "cabeza", "brazo", "pierna", "mano"}
    assert all(p["version"] == 1 and p["linea"] == "ejemplo-100" for p in d["parts"].values())    # versión fijada, sin rutas
    assert {l["name"] for l in d["limbs"]} == {"brazo", "pierna"} and d["head"] is not None
    assert "Proyecto: muneco-sin-stl.proyecto" in FB.summary(built["info"])
    assert CP.peek(built["path"])["linea"] == "ejemplo-100"


def test_main_window_opens_it_without_stl_and_shows_the_parts(built):
    from test_gui_phases35 import FakeScene

    c = Controller()
    c.open_project(built["path"])
    s = c.session
    assert c.loaded and s.stl_path is None and s.catalog_only() and s.catalog_root == os.path.abspath(built["root"])
    assert "sin STL" in c.message
    assert s.work is not None and len(s.work.faces) > 100 and s.problems() == []
    assert c.needs_review_on_open() is True                         # la ventana revisa sola las piezas al abrir
    items = c.preview_parts()
    assert items and set(items) == {"torso", "cabeza", "brazo", "pierna", "mano"}
    assert items["brazo"].mesh is not None and items["pierna"].mesh is not None
    sc = FakeScene()
    c.sync(sc)
    assert "figura" in sc.items and "parte:pierna" in sc.items and "parte:brazo" in sc.items


def test_it_builds_again_from_the_main_window(built):
    c = Controller()
    c.open_project(built["path"])
    c.session.project.params.decimate = 0
    out = c.generate()
    assert out is not None, c.message
    names = set(out.result.pieces)
    assert "torso" in names and any(n.startswith("brazo") for n in names) and any(n.startswith("pierna") for n in names)


def test_saving_from_the_main_window_keeps_it_openable_without_stl(built, tmp_path):
    c = Controller()
    c.open_project(built["path"])
    again = str(tmp_path / "otra.proyecto")
    c.save_project(again)
    d = json.load(open(again))
    assert d["source"] is None and CP.origin(d) is not None
    c2 = Controller()
    c2.open_project(again)
    assert c2.loaded and c2.session.catalog_only()


def test_missing_catalog_asks_for_the_folder_and_remembers_it(built, tmp_path):
    def moved(d):
        d["catalogo_solo"]["ruta"] = str(tmp_path / "ya-no-esta")

    path = _copy(built, tmp_path, moved)
    c = Controller()
    with pytest.raises(CatalogNeeded, match="ejemplo-100"):
        c.open_project(path)
    c.open_project(path, catalog_root=built["root"])                 # la carpeta elegida en el diálogo
    assert c.loaded and c.session.catalog_root == os.path.abspath(built["root"])
    c3 = Controller()
    c3.open_project(path)                                            # ahora la GUI recuerda dónde está la línea
    assert c3.loaded


def test_a_project_that_is_not_from_the_catalog_still_asks_for_its_stl(tmp_path):
    from afm.project import demo_project

    path = str(tmp_path / "x.proyecto")
    demo_project().save(path)
    with pytest.raises(FileNotFoundError, match="STL del proyecto"):
        Session.open_project(path)


def test_recipe_scales_are_applied_to_the_base_figure_without_stl(built, tmp_path):
    def scaled(d):
        d["catalogo_solo"]["receta"]["ajustes"]["escalas"] = {"brazo": 1.25}

    c0, c1 = Session.open_project(built["path"]), Session.open_project(_copy(built, tmp_path, scaled))
    e0 = c0._catalog_only_choices()["brazo"].mesh.extents
    e1 = c1._catalog_only_choices()["brazo"].mesh.extents
    assert e1 == pytest.approx(e0 * 1.25, rel=1e-6)
    assert c1._catalog_only_choices()["torso"].mesh.extents == pytest.approx(c0._catalog_only_choices()["torso"].mesh.extents)
