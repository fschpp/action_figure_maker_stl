"""GUI (sin GTK): sección «Piezas de catálogo» (Fase 8): abrir catálogo, catalogar la figura abierta, elegir una pieza
por lugar, generar y ver la compatibilidad por fila."""
import pytest

from afm.catalog import Catalog
from afm.gui.controller import Controller
from afm.gui.session import Session
from afm.project import demo_project
from afm.testfigure import make_doll


@pytest.fixture(scope="module")
def doll_stl(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("doll") / "muneco.stl")
    make_doll().export(path)
    return path


@pytest.fixture()
def ctrl(tmp_path, doll_stl):
    proj = tmp_path / "muneco.proyecto"
    demo_project().save(str(proj))
    c = Controller()
    c.open_project(str(proj), doll_stl)
    assert c.loaded
    return c


@pytest.fixture()
def root(tmp_path):
    Catalog.create(str(tmp_path / "linea"), "prueba", 100.0)
    return str(tmp_path / "linea")


def test_everything_needs_an_open_catalog(ctrl):
    assert ctrl.catalog_choices("pierna") == []
    assert ctrl.catalog_set_part("pierna", "x") is False and "catálogo" in ctrl.message
    assert ctrl.catalog_add_current("muneco") is False and "catálogo" in ctrl.message
    assert [r["lugar"] for r in ctrl.catalog_rows()] == ["brazo", "pierna", "cabeza", "mano"]


def test_open_catalog_reports_a_bad_path_and_the_line(ctrl, root, tmp_path):
    assert ctrl.open_catalog(str(tmp_path / "no_existe")) is False and "No se pudo abrir" in ctrl.message
    assert ctrl.open_catalog(root) is True and "«prueba»: 0 pieza(s)" in ctrl.message


def test_catalog_the_open_figure_then_choose_parts(ctrl, root):
    ctrl.open_catalog(root)
    assert ctrl.catalog_add_current("muneco") is True and "Catalogadas" in ctrl.message
    pierna = ctrl.catalog_choices("pierna")
    brazo = ctrl.catalog_choices("brazo")
    assert pierna and all(m.tipo == "pierna" for m in pierna) and brazo and all(m.tipo == "brazo" for m in brazo)

    assert ctrl.catalog_set_part("pierna", brazo[0].ref) is False and "pide «pierna»" in ctrl.message
    assert ctrl.catalog_set_part("cola", pierna[0].ref) is False and "no es un lugar" in ctrl.message
    assert ctrl.catalog_set_part("pierna", "no-existe") is False
    assert ctrl.session.project.parts == {}

    assert ctrl.catalog_set_part("pierna", pierna[0].id) is True
    pr = ctrl.session.project.parts["pierna"]
    assert pr.id == pierna[0].id and pr.version == pierna[0].version and pr.linea == "prueba"   # fija la versión elegida
    rows = {r["lugar"]: r for r in ctrl.catalog_rows()}
    assert rows["pierna"]["pieza"] == pr.ref and rows["brazo"]["pieza"] is None
    assert ctrl.catalog_row_text(rows["brazo"]) == "brazo: la del modelo"
    assert ctrl.catalog_row_text(rows["pierna"]) == f"pierna: {pr.ref}"

    assert ctrl.catalog_set_part("pierna", None) is True and ctrl.session.project.parts == {}
    assert ctrl.catalog_set_part("pierna", "") is True and "ya usaba" in ctrl.message


def test_part_is_saved_in_the_project_and_survives_reopening(ctrl, root, tmp_path, doll_stl):
    ctrl.open_catalog(root)
    ctrl.catalog_add_current("muneco")
    ref = ctrl.catalog_choices("brazo")[0].ref
    ctrl.catalog_set_part("brazo", ref)
    path = str(tmp_path / "guardado.proyecto")
    ctrl.session.save_project(path)
    s2 = Session.open_project(path, doll_stl)
    assert s2.project.parts["brazo"].id == ref.split("@")[0] and s2.project.parts["brazo"].linea == "prueba"


def test_generate_with_a_catalog_part_fills_the_row_with_the_compatibility(ctrl, root):
    ctrl.open_catalog(root)
    ctrl.catalog_add_current("muneco")
    ctrl.catalog_set_part("pierna", ctrl.catalog_choices("pierna")[0].ref)
    out = ctrl.session.build()
    assert out.validation["ok"], out.validation["problems"]
    rows = {r["lugar"]: r for r in ctrl.catalog_rows()}
    assert rows["pierna"]["compat"]["nivel"] in ("ok", "aviso") and rows["brazo"]["compat"] is None
    txt = ctrl.catalog_row_text(rows["pierna"])
    assert txt.startswith("pierna: ") and "IoU 1,00" in txt
    ctrl.catalog_set_part("pierna", None)
    assert ctrl.session.outcome is None                       # cambiar una pieza invalida la figura generada
