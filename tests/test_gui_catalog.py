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
    assert [r["lugar"] for r in ctrl.catalog_rows()] == ["torso", "pelvis", "brazo", "pierna", "cabeza", "mano"]


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


def test_create_catalog_from_the_gui_is_afm_catalogo_crear(ctrl, tmp_path):
    assert ctrl.default_catalog_line() == ("proceres-100", 100.0)
    path = str(tmp_path / "catalogos" / "proceres-100")
    assert ctrl.create_catalog(path, "proceres-100", 100.0) is True
    assert "creado" in ctrl.message and "proceres-100" in ctrl.message
    cat = Catalog.open(path)                                   # lo mismo que deja `afm catalogo crear`
    assert cat.name == "proceres-100" and cat.height_mm == 100.0 and cat.list() == []
    assert ctrl.session.catalog_root == cat.root
    # queda abierto: se puede catalogar la figura abierta y elegir piezas sin pasar por «Abrir catálogo»
    assert ctrl.catalog_add_current("muneco") is True and ctrl.catalog_choices("pierna")


def test_create_catalog_defaults_and_errors(ctrl, tmp_path):
    path = str(tmp_path / "sin-nombre-explicito")
    assert ctrl.create_catalog(path) is True                   # nombre = carpeta, altura = la de la figura
    cat = Catalog.open(path)
    assert cat.name == "sin-nombre-explicito" and cat.height_mm == ctrl.session.project.params.ref_height
    assert ctrl.create_catalog(path) is False and "ya es un catálogo" in ctrl.message
    ocupada = tmp_path / "ocupada"
    ocupada.mkdir()
    (ocupada / "algo.txt").write_text("x")
    assert ctrl.create_catalog(str(ocupada), "x", 100) is False and "no está vacía" in ctrl.message
    assert ctrl.create_catalog(str(tmp_path / "n"), "nombre con espacios", 100) is False and "inválido" in ctrl.message
    assert ctrl.create_catalog(str(tmp_path / "h"), "ok", 0) is False and "mayor que 0" in ctrl.message


# ---------------------------------------------------------------- mejoras de usabilidad del catálogo
def _catalogued(ctrl, root):
    ctrl.open_catalog(root)
    ctrl.catalog_add_current("muneco")
    ctrl.catalog_set_part("pierna", ctrl.catalog_choices("pierna")[0].ref)


def test_catalog_is_remembered_and_reopened(ctrl, root, tmp_path, doll_stl):
    ctrl.open_catalog(root)
    ctrl.catalog_add_current("muneco")
    ctrl.catalog_set_part("pierna", ctrl.catalog_choices("pierna")[0].ref)
    path = str(tmp_path / "g.proyecto")
    ctrl.save_project(path)
    c2 = Controller()
    c2.open_project(path, doll_stl)                       # sin abrir el catálogo a mano
    assert c2.session.catalog_root == ctrl.session.catalog_root and "reabierto" in c2.message
    c3 = Controller()
    c3.open_stl(doll_stl)                                 # sin piezas: el último usado
    assert c3.session.catalog_root == ctrl.session.catalog_root
    c3.forget_catalog()
    c4 = Controller()
    c4.open_stl(doll_stl)
    assert c4.session.catalog_root is None


def test_project_with_parts_warns_when_the_catalog_is_unknown(ctrl, root, tmp_path, doll_stl, monkeypatch):
    _catalogued(ctrl, root)
    path = str(tmp_path / "g.proyecto")
    ctrl.save_project(path)
    ctrl.forget_catalog()
    c2 = Controller()
    c2.open_project(path, doll_stl)
    assert c2.session.catalog_root is None and "no sé dónde está su catálogo" in c2.message


def test_preview_parts_then_pregenerate_issues(ctrl, root):
    _catalogued(ctrl, root)
    assert ctrl.pregenerate_issues()["stale"] is True            # elegida pero sin revisar
    items = ctrl.preview_parts()
    assert items and "Revisadas 1 pieza(s)" in ctrl.message
    issues = ctrl.pregenerate_issues()
    assert issues["stale"] is False and issues["bloqueantes"] == []
    row = {r["lugar"]: r for r in ctrl.catalog_rows()}["pierna"]
    assert row["previa"] is True and "IoU" in ctrl.catalog_row_text(row)
    d = ctrl.fit_diagnosis("pierna")
    assert d and d["caras"]["pieza"] and d["caras"]["modelo"]
    ctrl.session.project.params.clearance += 0.1
    assert ctrl.pregenerate_issues()["stale"] is True            # cambió algo: hay que revisar de nuevo


def test_pregenerate_issues_block_on_a_mismatch(ctrl, root):
    _catalogued(ctrl, root)
    ctrl.set_ref_height(ctrl.session.project.params.ref_height * 1.5)
    ctrl.preview_parts()
    issues = ctrl.pregenerate_issues()
    assert issues["bloqueantes"] or issues["avisos"]


def test_scale_to_line_and_to_fit_a_part(ctrl, root):
    assert ctrl.scale_to_line() is False and "catálogo" in ctrl.message
    _catalogued(ctrl, root)
    assert ctrl.line_scale() == pytest.approx(100.0 / ctrl.session.project.params.ref_height)
    ctrl.set_ref_height(80.0)
    assert ctrl.scale_to_line() is True and ctrl.session.project.params.ref_height == pytest.approx(100.0)
    ctrl.set_ref_height(120.0)
    assert ctrl.scale_to_fit_part("pierna") is False             # sin revisión vigente
    ctrl.preview_parts()
    assert ctrl.scale_to_fit_part("pierna") is True
    assert ctrl.session.project.params.ref_height == pytest.approx(100.0, rel=0.06)


def test_generate_after_preview_uses_the_real_result(ctrl, root):
    _catalogued(ctrl, root)
    ctrl.preview_parts()
    assert ctrl.outcome_free() is True
    ctrl.session.project.params.decimate = 0
    assert ctrl.generate() is not None and ctrl.outcome_free() is False


def test_previewed_parts_are_drawn_before_generating(ctrl, root):
    from test_gui_phases35 import FakeScene

    _catalogued(ctrl, root)
    sc = FakeScene()
    ctrl.sync(sc)
    assert not [k for k in sc.items if k.startswith("parte:")]          # sin revisar no se dibuja nada
    ctrl.preview_parts()
    ctrl.sync(sc)
    assert "parte:pierna" in sc.items and "parte:pierna:espejo" in sc.items
    ctrl.set_show_part_preview(False)
    ctrl.sync(sc)
    assert not [k for k in sc.items if k.startswith("parte:")]
    ctrl.set_show_part_preview(True)
    ctrl.session.clear_part("pierna")                                   # la pieza ya no está elegida: desaparece sola
    ctrl.sync(sc)
    assert not [k for k in sc.items if k.startswith("parte:")]


def test_cuts_are_numbered_and_aligned_to_the_line(ctrl, root, monkeypatch):
    rows = ctrl.cut_rows()
    assert [r["n"] for r in rows] == list(range(1, len(rows) + 1)) and rows[0]["id"] == "simetria"
    assert ctrl.cut_row_text(rows[1]).startswith("2 · ")
    _catalogued(ctrl, root)
    # el muñeco no dibuja sus miembros sobre el plano de simetría: se lo simula para probar la cuenta del desplazamiento
    monkeypatch.setattr("afm.gui.session.plane_on_symmetry", lambda plane, axis: True)
    sess = ctrl.session
    x = sess.line_cut_x("pierna")
    assert x is not None and x > 0                                     # la pieza catalogada corta de costado
    sess.limb("pierna").cut_offset = x + 2.0                            # se desalinea a propósito
    leg = next(r for r in ctrl.cut_rows() if r["id"] == "pierna")
    assert leg["dif_mm"] == pytest.approx(-2.0, abs=0.01) and "la línea corta a" in ctrl.cut_row_text(leg)
    assert ctrl.align_cuts_to_line() == 1
    assert next(r for r in ctrl.cut_rows() if r["id"] == "pierna")["dif_mm"] == pytest.approx(0.0, abs=0.01)
    assert ctrl.align_cuts_to_line() == 0 and "No había" in ctrl.message
    assert ctrl.align_cut_to_line("brazo") is False and "usa la pieza del modelo" in ctrl.message


def test_plane_status_marks_tilted_and_failed_planes(ctrl, root):
    """Un plano de miembro girado unos grados respecto del corte de la pieza elegida se pinta de amarillo; el que falló
    en la revisión, de rojo con el motivo; y el rojo se apaga solo cuando el usuario mueve ese plano."""
    import numpy as np
    from afm.extract import ExtractError
    from afm.planes import Plane
    from afm.ports import PortError
    from test_gui_phases35 import FakeScene

    class ColorScene(FakeScene):
        def __init__(self):
            super().__init__()
            self.colors = {}

        def set_plane(self, name, plane, hs, center=None, color=None):
            self.colors[name] = color
            super().set_plane(name, plane, hs, center, color)

    _catalogued(ctrl, root)
    sess = ctrl.session
    spec = sess.limb("pierna")
    x = sess.line_cut_x("pierna")
    spec.plane = Plane.from_normal((x, 0.0, 0.0), (1.0, 0.0, 0.0), "pierna")
    assert ctrl.plane_status()["pierna"][0] == "ok"
    t = np.radians(5.0)
    spec.plane = Plane.from_normal((x, 0.0, 0.0), (np.cos(t), np.sin(t), 0.0), "pierna")
    nivel, texto = ctrl.plane_status()["pierna"]
    assert nivel == "aviso" and "5,0°" in texto
    row = next(r for r in ctrl.cut_rows() if r["id"] == "pierna")
    assert row["nivel"] == "aviso" and "⚠" in ctrl.cut_row_text(row)
    sc = ColorScene()
    ctrl.sync(sc)
    assert sc.colors["plano:pierna"][0] == pytest.approx(1.0) and sc.colors["plano:pierna"][2] < 0.2     # amarillo

    err = PortError("«pierna»: el plano del puerto no cae sobre una cara plana — hay una cara girada 5,0°", "pierna", {})
    ctrl.finish_preview_parts(error=err)
    assert ctrl.plane_status()["pierna"][0] == "no" and "pintado de rojo" in ctrl.message
    assert "FALLÓ" in ctrl.cut_row_text(next(r for r in ctrl.cut_rows() if r["id"] == "pierna"))
    ctrl.sync(sc)
    assert sc.colors["plano:pierna"][0] > 0.9 and sc.colors["plano:pierna"][1] < 0.2                     # rojo
    # una excepción de extracción sin plano propio (muñeca) no pinta nada
    ctrl.finish_preview_parts(error=ExtractError("muñeca: algo", "muneca"))
    assert ctrl.plane_faults == {} and "pintado" not in ctrl.message
    # el rojo se apaga al mover el plano
    ctrl.finish_preview_parts(error=err)
    assert "pierna" in ctrl.plane_faults
    spec.plane = Plane.from_normal((x, 0.0, 0.0), (1.0, 0.0, 0.0), "pierna")
    assert ctrl.plane_status()["pierna"][0] == "ok"


def test_cut_numbers_are_drawn_in_the_viewer(ctrl):
    from test_gui_phases35 import FakeScene

    sc = FakeScene()
    ctrl.sync(sc)
    keys = [k for k in sc.items if k.startswith("corte:")]
    assert keys and any(k.startswith("corte:simetria:") for k in keys)
    ctrl.set_show_cut_numbers(False)
    ctrl.sync(sc)
    assert not [k for k in sc.items if k.startswith("corte:")]


# ---------------------------------------------------------------- torso y pelvis como lugares
def test_torso_and_pelvis_are_places_with_their_own_choices(ctrl, root):
    ctrl.open_catalog(root)
    ctrl.catalog_add_current("muneco")
    assert [r["lugar"] for r in ctrl.catalog_rows()][:2] == ["torso", "pelvis"]
    torsos = ctrl.catalog_choices("torso")
    assert torsos and {m.tipo for m in torsos} <= {"torso", "torso_sup"}
    assert ctrl.catalog_choices("pelvis") == []                       # el muñeco no tiene cintura: no hay pelvis
    assert ctrl.catalog_set_part("torso", ctrl.catalog_choices("brazo")[0].ref) is False and "pide «torso o torso_sup»" in ctrl.message
    assert ctrl.catalog_set_part("torso", torsos[0].ref) is True
    assert ctrl.session.catalog_only() is True


def test_pelvis_alone_does_not_make_the_figure_catalog_only(ctrl, root):
    """La pelvis sola reemplaza la del modelo: el torso sigue siendo el del modelo y hace falta su plano de cintura."""
    from afm.project import PartRef
    ctrl.open_catalog(root)
    ctrl.session.project.parts["pelvis"] = PartRef(id="pelvis-x", version=1, linea=ctrl.session._catalog().name)
    assert ctrl.session.catalog_only() is False
    assert any("plano de cintura" in t for t in ctrl.session.problems())


def test_torso_alone_needs_no_pelvis_but_a_pelvis_needs_the_torso(ctrl, root):
    ctrl.open_catalog(root)
    ctrl.catalog_add_current("muneco")
    ctrl.catalog_set_part("torso", ctrl.catalog_choices("torso")[0].ref)
    assert ctrl.session.problems() == []                               # torso entero: se arma solo
    ctrl.catalog_set_part("torso", None)
    assert ctrl.session.catalog_only() is False


def test_catalog_only_preview_and_build(ctrl, root):
    ctrl.open_catalog(root)
    ctrl.catalog_add_current("muneco")
    for place in ("torso", "brazo", "pierna"):
        assert ctrl.catalog_set_part(place, ctrl.catalog_choices(place)[0].ref) is True
    ctrl.session.project.symmetry = None                              # el modelo no hace falta: sale todo del catálogo
    items = ctrl.preview_parts()
    assert items and set(items) == {"torso", "brazo", "pierna"}
    assert items["torso"].compat is None and items["torso"].notes
    assert items["brazo"].compat is not None and items["brazo"].mesh is not None
    assert ctrl.pregenerate_issues()["stale"] is False
    ctrl.session.project.params.decimate = 0
    out = ctrl.generate()
    assert out is not None, ctrl.message
    names = set(out.result.pieces)
    assert "torso" in names and any(n.startswith("brazo") for n in names) and any(n.startswith("pierna") for n in names)
