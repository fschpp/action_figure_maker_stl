"""Mira del cuello y de la cintura: pasador de costado con fantasma / imán / mapa de calor, y la cinta del plano (sin GTK)."""
import os
import shutil

import numpy as np
import pytest

from afm import neck_waist_aid as NW
from afm.catalog import Catalog
from afm.extract import extract_all
from afm.gui.controller import Controller, Mode
from afm.pin_aid import PinAid
from afm.planes import Plane
from afm.ports import coupling_transform
from afm.project import demo_project
from afm.testfigure import make_doll
from afm import solids as S
from afm.waist import WaistSpec, build_waist, waist_pin_center, waist_port_center

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
HEAD = ((-10, -6, 80), (10, -6, 80), (0, 8, 80))
WAIST = ((-10, -6, 55), (10, -6, 55), (0, 8, 55))


class Scene:
    """Escena de mentira: guarda lo que se pinta."""

    def __init__(self):
        self.items = {}

    def set_lines(self, key, pts, color, closed=False, on_top=False):
        self.items[key] = ("lines", pts, color)

    def set_points(self, key, pts, color, size, on_top=False):
        self.items[key] = ("points", np.asarray(pts), color)

    def set_mesh(self, key, *a, **k):
        self.items[key] = ("mesh",)

    def show(self, key, on):
        pass

    def remove(self, key):
        self.items.pop(key, None)


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    from test_pin_aid import clone

    dst = tmp_path_factory.mktemp("nw") / "linea"
    shutil.copytree(EJEMPLO, dst)
    cat = Catalog.open(str(dst))
    for k, i in ((1.0, "a"), (0.5, "c")):
        clone(cat, "torso-muneco-01", f"torso-otra-{i}", k)
    return str(dst)


@pytest.fixture(scope="module")
def stl_path(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("nwstl") / "muneco.stl")
    make_doll().export(path)
    return path


@pytest.fixture()
def ctrl(stl_path, root):
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    s = c.session
    for l in demo_project().limbs:
        s.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
        s.set_limb_polygon(l.name, l.polygon)
    s.set_head(Plane.from_points(*HEAD, "cuello"))
    s.set_waist(Plane.from_points(*WAIST, "cintura"))
    s.open_catalog(root)
    return c


# ------------------------------------------------------------------ regla del puerto de la cintura
def test_waist_port_center_follows_the_pin_only_if_chosen():
    doll = S.to_manifold(make_doll())
    pr = demo_project()
    spec = WaistSpec(Plane.from_points(*WAIST, "cintura"))
    _host, _plane, _pelvis = None, None, None
    face = NW.waist_face(NW.waist_torso(doll, pr), spec)
    assert np.allclose(waist_port_center(face, spec), face.center)            # sin pasador elegido: el centroide, como siempre
    chosen = np.array([0.0, 2.0, 55.0])
    spec2 = WaistSpec(spec.plane, pin_center=tuple(chosen))
    c = waist_port_center(face, spec2)
    assert np.allclose(c, chosen, atol=1e-6) and c[0] == 0.0
    assert np.allclose(waist_pin_center(face, spec2), c)


def test_catalogued_waist_ports_sit_on_the_chosen_pin_and_still_couple():
    doll = S.to_manifold(make_doll())
    pr = demo_project()
    pr.waist = WaistSpec(Plane.from_points(*WAIST, "cintura"))
    base = {e.meta.tipo: e for e in extract_all(doll, pr, "m").pieces}
    c0 = base["torso_sup"].meta.puertos["cintura"].frame.center
    pr.waist = WaistSpec(pr.waist.plane, pin_center=(0.0, c0[1] + 1.5, 55.0))
    got = {e.meta.tipo: e for e in extract_all(doll, pr, "m").pieces}
    up, lo = got["torso_sup"].meta.puertos["cintura"], got["pelvis"].meta.puertos["cintura"]
    assert up.frame.center[1] == pytest.approx(c0[1] + 1.5, abs=1e-6) and up.frame.center[0] == 0.0
    assert np.allclose(coupling_transform(lo.frame, up.frame), np.eye(4), atol=1e-6)
    assert got["torso_sup"].meta.problems() == [] and got["pelvis"].meta.problems() == []


def test_pin_aid_accepts_the_neck_and_the_waist(ctrl):
    for lugar in ("cuello", "cintura"):
        aid = ctrl.session.joint_aid(lugar)
        pa = aid._pin_aid(aid.reading_at(0.0).prev)
        assert isinstance(pa, PinAid) and pa.clase == lugar
    with pytest.raises(ValueError):
        PinAid(ctrl.session.joint_aid("cuello").reading_at(0.0).prev, "mano", ctrl.session._catalog())


# ------------------------------------------------------------------ session
def test_joint_aid_needs_a_plane_and_a_catalog(stl_path):
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    with pytest.raises(ValueError, match="catálogo"):
        c.session.joint_aid("cuello")


def test_joint_aid_reads_the_catalog_and_has_a_strip(ctrl):
    for lugar in ("cuello", "cintura"):
        aid = ctrl.session.joint_aid(lugar)
        assert ctrl.session.joint_aid(lugar) is aid                       # en caché
        r = aid.reading_at(0.0)
        assert r.prev is not None and r.rings_world()
        strip = aid.strip(0.0)
        assert len(strip) == 2 * round(NW.SPAN_MM / NW.STEP_MM) + 1
        best, _improves = aid.best(0.0)
        assert best is None or best.aim is not None


@pytest.fixture(scope="module")
def waist_root(tmp_path_factory):
    """Catálogo con piezas que SÍ tienen puerto `cintura` (torso superior + pelvis de «otra» figura, y dos clones con la cara al 90 % y
    al 20 %): el de ejemplo no trae ninguna, y sin ellas la mira de la cintura no tiene contra qué medir."""
    from test_pin_aid import clone

    pr = demo_project()
    pr.waist = WaistSpec(Plane.from_points(*WAIST, "cintura"))
    res = extract_all(S.to_manifold(make_doll()), pr, "otra", figure="otra.stl")
    cat = Catalog.create(str(tmp_path_factory.mktemp("nwwaist") / "linea"), "prueba", 100.0)
    for e in res.pieces:
        if e.meta.tipo in ("torso_sup", "pelvis"):
            cat.add_piece(e.mesh, e.meta)
    for k, i in ((0.9, "b"), (0.2, "c")):
        for ref in ("torso-sup-otra-01", "pelvis-otra-01"):
            clone(cat, ref, ref.replace("otra", f"x{i}"), k, figura=f"x{i}.stl")
    return cat.root


def test_waist_aid_measures_both_directions_against_waist_pieces(stl_path, waist_root):
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    for l in demo_project().limbs:
        c.session.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
        c.session.set_limb_polygon(l.name, l.polygon)
    c.session.set_waist(Plane.from_points(*WAIST, "cintura"))
    c.session.open_catalog(waist_root)
    aim = c.session.joint_aid("cintura").reading_at(0.0).aim
    assert aim is not None and aim.nombres == ("torsos", "pelvis")
    assert (aim.plug.universo, aim.socket.universo) == (3, 3)                  # la pelvis contra 3 torsos; el torso contra 3 pelvis
    for side in (aim.plug, aim.socket):
        assert {f.ref.split("-")[-2]: f.nivel for f in side.fits} == {"otra": "ok", "xb": "aviso", "xc": "no"}
    assert aim.contador() == "torsos 2/3 · pelvis 2/3" and aim.nivel == "aviso"     # 2 de 3: ni verde ni rojo
    assert "no entra" in aim.peor_motivo() or "coinciden" in aim.peor_motivo()
    best, _ = c.session.joint_aid("cintura").best(0.0)
    assert best is not None and best.aim.contador() == aim.contador()


def test_shift_plane_moves_along_the_normal_and_drops_the_chosen_pin(ctrl):
    s = ctrl.session
    s.set_waist_pin(s.waist_pin_preview().default_center + np.array([0, 0.5, 0.0]))
    assert s.project.waist.pin_center is not None
    z0 = s.project.waist.plane.origin[2]
    s.shift_plane("cintura", 1.5)
    assert s.project.waist.plane.origin[2] == pytest.approx(z0 + 1.5, abs=1e-6) and s.project.waist.pin_center is None
    h0 = s.project.head.plane.origin[2]
    s.shift_plane("cuello", -1.0)
    assert s.project.head.plane.origin[2] == pytest.approx(h0 - 1.0, abs=1e-6)
    s.project.waist = None
    with pytest.raises(ValueError):
        s.shift_plane("cintura", 1.0)


def test_waist_pin_is_projected_on_the_cut_and_must_touch_the_face(ctrl):
    s = ctrl.session
    prev = s.waist_pin_preview()
    s.set_waist_pin(np.array([5.0, 1.0, 70.0]))
    c = np.asarray(s.project.waist.pin_center)
    assert c[0] == 0.0 and c[2] == pytest.approx(s.project.waist.plane.origin[2], abs=1e-6)
    with pytest.raises(ValueError, match="no toca"):
        s.set_waist_pin(np.array([0.0, 500.0, 55.0]))
    s.set_waist_pin(None)
    assert s.project.waist.pin_center is None and prev is s.waist_pin_preview()


# ------------------------------------------------------------------ controlador
def test_waist_pin_mode_has_aim_ghost_heat_and_magnet(ctrl):
    ctrl.start_waist_pin()
    assert ctrl.mode == Mode.WAIST_PIN and ctrl.aim_active()           # el catálogo de ejemplo no tiene piezas con puerto «cintura»: sin contador
    scene = Scene()
    ctrl.pin_aim = ctrl.pin_aid.evaluate(ctrl.joint_prev.project(ctrl._waist_pin_center()))
    ctrl._sync_joint_aim(scene)                              # sin piezas «cintura» en el catálogo no hay calor, pero sí fantasma
    assert "pasador_union:mira" in scene.items and ctrl.aim_box() is not None
    ctrl._sync_waist_pin(scene)
    assert "pasador_cintura:perfil" in scene.items
    ctrl.snap_pin()
    assert ctrl.session.project.waist.pin_center is not None
    c = np.asarray(ctrl.session.project.waist.pin_center)
    assert c[0] == 0.0
    ctrl.nudge_waist_pin(1, 0)
    ctrl.reset_waist_pin()
    assert ctrl.session.project.waist.pin_center is None
    ctrl.cancel(quiet=True)
    ctrl._sync_joint_aim(scene)
    assert not any(k.startswith("pasador_union") for k in scene.items)


def test_neck_pin_mode_also_aims(ctrl):
    ctrl.start_head_pin()
    assert ctrl.mode == Mode.HEAD_PIN and ctrl.aim_active() and ctrl.pin_aid.clase == "cuello"
    assert "Catálogo: " in ctrl.message
    assert any(n for _uv, n in ctrl.pin_aid.heatmap())
    scene = Scene()
    ctrl._sync_joint_aim(scene)
    assert any(k.startswith("pasador_union:calor") for k in scene.items)
    ctrl.pin_aim = ctrl.pin_aid.evaluate(ctrl.joint_prev.project(ctrl._head_pin_center()))
    ctrl._sync_joint_aim(scene)
    assert "pasador_union:mira" in scene.items
    ctrl.snap_pin()
    assert ctrl.session.project.head.pin_center is not None
    assert ctrl.aim_box() is not None


def test_aim_follows_the_pointer_in_the_side_view(ctrl):
    ctrl.start_waist_pin()
    ctrl.camera.look_along((1.0, 0.0, 0.0), ctrl._waist_pin_center(), 20.0)
    assert ctrl.update_hover(400, 300, 800, 600) is True
    assert ctrl.pin_aim is not None and abs(ctrl.pin_aim.uv[0]) < 1e3


def test_no_catalog_means_no_aim_but_the_pin_still_works(stl_path):
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    for l in demo_project().limbs:
        c.session.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
        c.session.set_limb_polygon(l.name, l.polygon)
    c.session.set_waist(Plane.from_points(*WAIST, "cintura"))
    c.start_waist_pin()
    assert c.mode == Mode.WAIST_PIN and not c.aim_active()
    c.nudge_waist_pin(1, 0)
    assert c.session.project.waist.pin_center is not None


def test_joint_strip_is_drawn_and_the_magnet_moves_the_plane(ctrl):
    ctrl.set_joint_focus("cintura")
    aim = ctrl.joint_aim()
    assert aim is not None and "note" not in aim and aim["reading"].prev is not None
    assert ctrl.joint_aim_text().startswith("plano")
    ctrl.set_joint_focus("cuello")                           # el cuello sí tiene piezas en el catálogo de ejemplo
    assert ctrl.joint_aim()["reading"].aim is not None
    scene = Scene()
    ctrl._sync_joint_strip(scene)
    assert any(k.startswith("plano_union:cinta") for k in scene.items)
    ctrl.set_joint_focus(None)
    assert ctrl.joint_aim() is None
    ctrl._sync_joint_strip(scene)
    assert not any(k.startswith("plano_union") for k in scene.items)
    ctrl.set_joint_focus("cuello")
    z0 = ctrl.session.project.head.plane.origin[2]
    assert ctrl.shift_joint_plane("cuello", 0.5)
    assert ctrl.session.project.head.plane.origin[2] == pytest.approx(z0 + 0.5, abs=1e-6)
    moved = ctrl.snap_joint(quiet=True)                      # lo que haga, no debe romper
    assert moved in (True, False)


def test_shifting_a_plane_leaves_the_pin_modes(ctrl):
    ctrl.start_waist_pin()
    assert ctrl.shift_joint_plane("cintura", 0.5)
    assert ctrl.mode == Mode.VIEW


def test_joint_aim_reports_why_it_cannot_measure(stl_path):
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    c.set_joint_focus("cintura")
    aim = c.joint_aim()
    assert aim is not None and "note" in aim
