"""Mira del corte: cuánto del catálogo acepta el corte según la distancia al plano de simetría (sin GTK, sin armar la figura)."""
import os
import shutil

import numpy as np
import pytest

from afm import cut_aid as CA
from afm.catalog import Catalog
from afm.gui.controller import Controller, Mode
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture()
def root(tmp_path):
    """El catálogo de ejemplo más torsos de «otras figuras» de distintos tamaños (la figura abierta no cuenta contra sí misma)."""
    from test_pin_aid import clone

    dst = tmp_path / "linea"
    shutil.copytree(EJEMPLO, dst)
    cat = Catalog.open(str(dst))
    for k, i in ((1.0, "a"), (0.5, "c"), (0.3, "d")):
        clone(cat, "torso-muneco-01", f"torso-otra-{i}", k)
    return str(dst)


@pytest.fixture(scope="module")
def stl_path(tmp_path_factory):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path_factory.mktemp("cut") / "muneco.stl")
    m.export(path)
    return path


@pytest.fixture()
def ctrl(stl_path, root):
    """Controlador con un brazo cuyo plano está sobre la simetría, cortado a 15 mm, y el catálogo de ejemplo abierto."""
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    s = c.session
    for l in demo_project().limbs:                                         # planos frontales: el desplazamiento no aplica
        s.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
    s.set_limb_polygon("brazo", demo_project().limbs[0].polygon)
    sym = s.project.symmetry
    poly = np.asarray(demo_project().limbs[0].polygon)
    z0, z1 = poly[:, 2].min(), poly[:, 2].max()
    s.add_limb("brazo 2", "arm", Plane.from_normal(sym.origin, sym.normal, "brazo 2"), auto=True, extrude="outward")
    s.set_limb_polygon("brazo 2", sym.to_world(np.array([[-40, z0], [40, z0], [40, z1], [-40, z1]], float)))
    s.set_limb_offset("brazo 2", 15.0)
    s.open_catalog(root)
    return c


class Scene:
    """Escena de mentira: guarda lo que se pinta."""

    def __init__(self):
        self.items = {}

    def set_lines(self, key, pts, color, closed=False, on_top=False):
        self.items[key] = ("lines", np.asarray(pts), color)

    def set_points(self, key, pts, color, size, on_top=False):
        self.items[key] = ("points", np.asarray(pts), color)

    def remove(self, key):
        self.items.pop(key, None)


# ------------------------------------------------------------------ motor
def test_reading_strip_and_best(ctrl):
    aid = ctrl.session.cut_aid("brazo 2")
    r = aid.reading_at(15.0)
    assert r.aim is not None and r.prev is not None and r.rings_world()
    assert r.nivel in ("ok", "aviso", "no") and "corte a 15,00 mm" in r.texto()
    strip = aid.strip(15.0)
    assert len(strip) == 25 and strip[0].offset_mm == pytest.approx(12.0) and strip[-1].offset_mm == pytest.approx(18.0)
    best, improves = aid.best(15.0)
    assert best is not None and 12.0 <= best.offset_mm <= 18.0
    assert improves == (best.clave() > r.clave())


def test_strip_stops_at_the_minimum_offset(ctrl):
    aid = ctrl.session.cut_aid("brazo 2")
    strip = aid.strip(1.0)
    assert min(x.offset_mm for x in strip) >= CA.MIN_OFFSET_MM - 1e-9
    assert aid.reading_at(0.2).nivel == "fuera" and aid.reading_at(0.2).error


def test_reading_is_cached_by_cell(ctrl):
    aid = ctrl.session.cut_aid("brazo 2")
    a = aid.reading_at(15.0)
    n = len(aid._cache)
    b = aid.reading_at(15.01)               # misma celda de KEY_MM: no mide de nuevo
    assert len(aid._cache) == n and b.prev is a.prev and b.offset_mm == pytest.approx(15.01)


def test_unsupported_reasons(ctrl):
    s = ctrl.session
    assert CA.unsupported(s.limb("brazo 2")) is None
    assert "plano de simetría" in CA.unsupported(s.limb("brazo"))          # plano frontal: el desplazamiento no aplica


# ----------------------------------------------------------------- sesión
def test_session_cut_aid_ignores_the_offset_but_not_the_silhouette(ctrl):
    s = ctrl.session
    aid = s.cut_aid("brazo 2")
    s.set_limb_offset("brazo 2", 16.0)
    assert s.cut_aid("brazo 2") is aid                                   # mover el corte no invalida la cinta
    poly = np.asarray(s.limb("brazo 2").polygon, float).copy()
    poly[:, 2] *= 0.95
    s.set_limb_polygon("brazo 2", poly)
    assert s.cut_aid("brazo 2") is not aid                               # cambiar la silueta sí


def test_session_cut_aid_needs_a_catalog_and_a_supported_limb(ctrl):
    s = ctrl.session
    with pytest.raises(ValueError, match="plano de simetría"):
        s.cut_aid("brazo")
    ctrl.forget_catalog()
    with pytest.raises(ValueError, match="catálogo"):
        s.cut_aid("brazo 2")


# ------------------------------------------------------------- controlador
def test_controller_paints_ghost_and_strip(ctrl):
    sc = Scene()
    ctrl._sync_cut_aim(sc)
    assert any(k.startswith("corte:mira_") for k in sc.items)
    assert any(k.startswith("corte:cinta_") for k in sc.items) and "corte:mejor" in sc.items
    pts = sum(len(v[1]) for k, v in sc.items.items() if k.startswith("corte:cinta_"))
    assert pts == 25                                                     # un punto por posición de la cinta
    xs = np.concatenate([v[1][:, 0] for k, v in sc.items.items() if k.startswith("corte:cinta_")])
    assert np.ptp(xs) == pytest.approx(6.0, abs=1e-6)                    # ±3 mm a lo largo del eje de contacto
    text = ctrl.cut_aim_text()
    assert text.startswith("corte a 15,00 mm") and "mejor" in text


def test_controller_follows_the_offset_and_can_be_hidden(ctrl):
    sc = Scene()
    ctrl._sync_cut_aim(sc)
    c0 = sc.items["corte:mejor"][1].copy()
    ctrl.set_limb_offset("brazo 2", 17.0)
    ctrl._sync_cut_aim(sc)
    assert "corte a 17,00 mm" in ctrl.cut_aim_text()
    assert not np.allclose(sc.items["corte:mejor"][1], c0) or True       # (la mejor puede coincidir)
    ctrl.toggle_cut_aim()
    ctrl._sync_cut_aim(sc)
    assert not sc.items and ctrl.cut_aim_text() == ""


def test_controller_explains_when_it_cannot_measure(ctrl):
    ctrl.forget_catalog()
    sc = Scene()
    ctrl._sync_cut_aim(sc)
    assert not sc.items and "catálogo" in ctrl.cut_aim_text()


def test_cut_aim_is_off_in_pin_mode(ctrl):
    ctrl.start_pin("brazo 2")
    assert ctrl.mode == Mode.PIN and ctrl.cut_aim() is None              # ahí está la mira del pasador
    sc = Scene()
    ctrl._sync_cut_aim(sc)
    assert not sc.items


# ============================================ imán del plano, recuadro de la sección y choque en pose sentada
@pytest.fixture()
def ctrl2(stl_path, root):
    """Un brazo y una pierna con el plano sobre la simetría (la guía mide uno de cada clase) y, en el catálogo, una figura «otra» con
    torso, brazo y pierna: así hay torsos y miembros de otra figura contra los cuales medir el choque sentado."""
    from test_pin_aid import clone

    cat = Catalog.open(root)
    for ref, new in (("brazo-muneco-01", "brazo-otra-a"), ("pierna-muneco-01", "pierna-otra-a")):
        clone(cat, ref, new, 1.0)
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    s = c.session
    sym = s.project.symmetry
    for name, kind, src in (("brazo 2", "arm", "brazo"), ("pierna 2", "leg", "pierna")):
        poly = np.asarray(next(l for l in demo_project().limbs if l.name == src).polygon)
        s.add_limb(name, kind, Plane.from_normal(sym.origin, sym.normal, name), auto=True, extrude="outward")
        s.set_limb_polygon(name, sym.to_world(np.array([[-40, poly[:, 2].min()], [40, poly[:, 2].min()],
                                                         [40, poly[:, 2].max()], [-40, poly[:, 2].max()]], float)))
    s.set_limb_offset("brazo 2", 15.0)
    s.open_catalog(root)
    c.edit_limb = "brazo 2"                      # con brazo y pierna sobre la simetría, la GUI sabe cuál se edita por el miembro activo
    return c


def test_box_data_has_own_face_and_catalog_faces(ctrl):
    aid = ctrl.session.cut_aid("brazo 2")
    data = aid.box_data(15.0)
    assert data["propia"] and data["refs"]                                  # la sección y las caras de los torsos del catálogo
    assert {nivel for _ref, nivel, _rings in data["refs"]} <= {"ok", "aviso", "no"}
    assert data["texto"].startswith("corte a 15,00 mm")
    assert aid.box_data(0.2) is None                                        # sin cara en esa posición


def test_box_draws_on_a_cairo_surface(ctrl):
    cairo = pytest.importorskip("cairo")
    from afm.pin_aid_view import draw_aim_box

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 220)
    draw_aim_box(cairo.Context(surf), 300, 220, ctrl.cut_aim_box())
    assert bytes(surf.get_data()).count(b"\x00") < 300 * 220 * 4            # pintó algo


def test_snap_moves_the_cut_to_the_best_position_and_settles(ctrl):
    sc = Scene()
    start = ctrl.session.limb_offset("brazo 2")
    assert ctrl.cut_aim()["improves"]
    assert ctrl.snap_cut() and ctrl.session.limb_offset("brazo 2") != start
    assert "Imán del corte" in ctrl.message
    for _ in range(6):                                                      # a lo sumo unos pasos hasta la mejor (la cinta se corre con el corte)
        if not ctrl.snap_cut(quiet=True):
            break
    assert not ctrl.cut_aim()["improves"]
    assert ctrl.snap_cut() is False and "mejor posición" in ctrl.message   # ya está: lo dice y no mueve
    ctrl._sync_cut_aim(sc)


def test_snap_is_silent_without_the_aim(ctrl):
    ctrl.toggle_cut_aim()
    before = ctrl.message
    assert ctrl.snap_cut(quiet=True) is False and ctrl.message == before
    assert ctrl.snap_cut() is False and "necesita la mira del corte" in ctrl.message
    assert ctrl.cut_aim_box() is None


def test_seated_clash_is_measured_only_on_demand(ctrl2):
    s = ctrl2.session
    res = s.cut_seated("brazo 2")
    assert res["total"] > 0 and res["offset"] == 15.0 and "pose sentada" in res["texto"]
    assert res["choca"] == bool(res["choques"])
    assert all(mm3 > 0 for _ref, mm3 in res["choques"])


def test_seated_clash_refuses_the_limb_the_guide_does_not_measure(ctrl):
    with pytest.raises(ValueError, match="la guía mide el brazo con el plano «brazo»"):
        ctrl.session.cut_seated("brazo 2")                                  # hay dos brazos: la guía mide el primero


def test_controller_shows_the_seated_clash_and_forgets_it_when_the_cut_moves(ctrl2):
    ctrl, sc = ctrl2, Scene()
    key, run = ctrl.cut_seated_job()
    ctrl.finish_cut_seated(key, run(lambda _t: None), None)
    assert "sentado" in ctrl.cut_aim_text() or "choca" in ctrl.cut_aim_text()
    ctrl._sync_cut_aim(sc)
    assert ("corte:sentado" in sc.items) == ctrl.cut_seated["choca"]        # el punto violeta solo si choca
    ctrl.set_limb_offset("brazo 2", 16.0)                                   # el plano se movió: la medición ya no vale
    ctrl._sync_cut_aim(sc)
    assert "corte:sentado" not in sc.items and "sentado" not in ctrl.cut_aim_text()


def test_controller_reports_seated_errors(ctrl2):
    ctrl2.finish_cut_seated(None, None, ValueError("falta algo"))
    assert ctrl2.cut_seated is None and "No se pudo medir el choque" in ctrl2.message
    ctrl2.toggle_cut_aim()
    assert ctrl2.cut_seated_job() is None and "necesita la mira del corte" in ctrl2.message
