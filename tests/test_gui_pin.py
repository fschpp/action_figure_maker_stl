"""Colocación asistida del pasador de brazo / cadera sobre la cara de corte (modo PIN de la GUI)."""
import numpy as np
import pytest

from afm.gui.controller import Controller, Mode
from afm.project import demo_project
from afm.testfigure import make_doll

W, H = 600, 800


@pytest.fixture(scope="module")
def stl_path(tmp_path_factory):
    m = make_doll()
    m.apply_scale(0.05)                       # el STL "viene" en otras unidades
    path = str(tmp_path_factory.mktemp("pin") / "muneco.stl")
    m.export(path)
    return path


@pytest.fixture()
def ctrl(stl_path):
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    for l in demo_project().limbs:
        c.session.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
    return c


def _with_polygon(c, name):
    spec = next(l for l in demo_project().limbs if l.name == name)
    c.session.set_limb_polygon(name, spec.polygon)
    return c


def _px(c, world):
    return tuple(c.camera.project(np.asarray(world, float), W, H)[0, :2])


def test_needs_a_silhouette_first(ctrl):
    ctrl.start_pin("brazo")
    assert ctrl.mode == Mode.VIEW and "silueta" in ctrl.message
    ctrl.start_pin("no existe")
    assert ctrl.mode == Mode.VIEW and "No existe" in ctrl.message


def test_start_shows_contact_face_and_faces_camera_to_it(ctrl):
    _with_polygon(ctrl, "brazo")
    ctrl.start_pin("brazo")
    assert ctrl.mode == Mode.PIN and ctrl.pin_limb == "brazo"
    face = ctrl.pin_prev.face
    assert abs(face.center[0] - (15.0 + 0.66)) < 0.05 and abs(abs(face.outward[0]) - 1) < 1e-6
    # la cámara mira la cara de frente: el ojo está del lado de la normal
    d = ctrl.camera.eye() - ctrl.camera.target
    assert d @ face.outward / np.linalg.norm(d) > 0.999
    assert np.allclose(ctrl.camera.target, face.center)
    assert "posición automática" in ctrl.message


def test_click_places_pin_on_the_face_plane(ctrl):
    _with_polygon(ctrl, "brazo")
    ctrl.start_pin("brazo")
    prev = ctrl.pin_prev
    target_uv = prev.project(prev.default_center) + np.array([0.0, -6.0])     # 6 mm más abajo, dentro de la cara
    world = prev.plane.to_world(target_uv[None])[0]
    ctrl.session.outcome = object()                                            # simula una figura ya generada
    ctrl.click(*_px(ctrl, world), W, H)
    pin = ctrl.session.limb("brazo").pin_center
    assert pin is not None and np.allclose(pin, world, atol=1e-3)
    assert abs(prev.plane.signed_distance(pin)[0]) < 1e-6                      # sobre el plano de corte
    assert ctrl.session.outcome is None                                        # invalida lo generado
    assert "posición elegida" in ctrl.message


def test_click_outside_the_face_is_refused(ctrl):
    _with_polygon(ctrl, "brazo")
    ctrl.start_pin("brazo")
    prev = ctrl.pin_prev
    outside = prev.plane.to_world((prev.project(prev.face.center) + np.array([30.0, 0.0]))[None])[0]
    ctrl.click(*_px(ctrl, outside), W, H)
    assert ctrl.session.limb("brazo").pin_center is None and "No se movió" in ctrl.message


def test_nudge_reset_and_finish(ctrl):
    _with_polygon(ctrl, "brazo")
    ctrl.start_pin("brazo")
    c0 = ctrl.pin_prev.project(ctrl.pin_prev.default_center)
    ctrl.nudge_pin(0, 1)
    c1 = ctrl.pin_prev.project(np.asarray(ctrl.session.limb("brazo").pin_center))
    assert np.allclose(c1 - c0, [0.0, 0.1], atol=1e-6)
    ctrl.nudge_pin(1, 0, big=True)
    c2 = ctrl.pin_prev.project(np.asarray(ctrl.session.limb("brazo").pin_center))
    assert np.allclose(c2 - c1, [1.0, 0.0], atol=1e-6) or "No se movió" in ctrl.message
    ctrl.reset_pin()
    assert ctrl.session.limb("brazo").pin_center is None and "restablecido" in ctrl.message
    ctrl.nudge_pin(0, 1)
    ctrl.cancel()                                                              # Esc: conserva lo elegido
    assert ctrl.mode == Mode.VIEW and ctrl.pin_prev is None and ctrl.session.limb("brazo").pin_center is not None
    ctrl.nudge_pin(0, 1)                                                       # fuera del modo no hace nada
    assert "terminada" in ctrl.message


def test_margin_warning_when_shaft_does_not_fit(ctrl):
    _with_polygon(ctrl, "brazo")
    ctrl.start_pin("brazo")
    prev = ctrl.pin_prev
    # la cara del brazo del muñeco es una franja de 2,6 mm (cilindro de 8 mm cortado a 3 mm del eje):
    # el vástago de 4,1 mm no entra y el asistente lo avisa
    uv = prev.project(prev.default_center)
    inside, margin = prev.fit(uv, ctrl.session.pin_spec("brazo").shaft_d)
    assert inside and margin < 0
    ctrl.nudge_pin(0, 0)
    assert "sobresale" in ctrl.message


def test_pin_survives_project_roundtrip_and_reaches_the_build(ctrl, tmp_path):
    _with_polygon(ctrl, "brazo")
    ctrl.start_pin("brazo")
    prev = ctrl.pin_prev
    uv = prev.project(prev.default_center) + np.array([0.0, -4.0])
    world = prev.plane.to_world(uv[None])[0]
    ctrl.click(*_px(ctrl, world), W, H)
    ctrl.cancel(quiet=True)
    path = str(tmp_path / "x.proyecto")
    ctrl.save_project(path)
    other = Controller()
    other.open_project(path)
    assert np.allclose(other.session.limb("brazo").pin_center, world, atol=1e-6)


def test_build_uses_the_chosen_pin_and_reports_margin(ctrl):
    for n in ("brazo", "pierna"):
        _with_polygon(ctrl, n)
    ctrl.start_pin("brazo")
    prev = ctrl.pin_prev
    uv = prev.project(prev.default_center) + np.array([0.0, -4.0])
    world = prev.plane.to_world(uv[None])[0]
    ctrl.click(*_px(ctrl, world), W, H)
    ctrl.cancel(quiet=True)
    out = ctrl.generate()
    assert out is not None
    rep = out.result.report["limbs"]
    assert np.allclose(rep["brazo"]["pin_center"], world, atol=1e-3)
    assert "pin_margin_mm" in rep["brazo"] and "pin_margin_mm" in rep["pierna"]
    assert isinstance(out.validation["avisos_pasador"], list)


def test_leg_pin_keeps_auto_cap(ctrl):
    _with_polygon(ctrl, "pierna")
    ctrl.start_pin("pierna")
    assert ctrl.mode == Mode.PIN
    prev = ctrl.pin_prev
    ctrl.nudge_pin(0, 1)
    spec = ctrl.session.limb("pierna")
    assert spec.pin_center is not None and spec.auto
    from afm.limbs import propose_geometry
    p = propose_geometry(prev.face, spec)
    assert p.cap_radius is not None and np.allclose(p.pin_center, spec.pin_center)      # no se pisa lo elegido


def test_remove_plane_leaves_pin_mode(ctrl):
    _with_polygon(ctrl, "brazo")
    ctrl.start_pin("brazo")
    ctrl.remove_plane("brazo")
    assert ctrl.mode == Mode.VIEW and ctrl.pin_prev is None


class _Scene:
    def __init__(self):
        self.items, self.kw = {}, {}

    def set_mesh(self, name, mesh, color=None, clip_x=-1e9, clip_plane=None):
        self.items[name] = ("mesh", mesh)
        self.kw[name] = clip_plane

    def set_plane(self, name, plane, hs, center=None, color=None):
        self.items[name] = ("quad", plane)

    def set_lines(self, name, pl, color=None, closed=False):
        self.items[name] = ("lines", pl)

    def set_points(self, name, pts, color=None, size=10.0):
        self.items[name] = ("points", np.asarray(pts))

    def show(self, name, visible):
        pass

    def remove(self, name):
        self.items.pop(name, None)

    def clear(self):
        self.items.clear()


def test_sync_draws_pin_helpers_and_clips_the_torso_side(ctrl):
    _with_polygon(ctrl, "brazo")
    sc = _Scene()
    ctrl.start_pin("brazo")
    ctrl.sync(sc)
    assert {"pasador:cara", "pasador:borde", "pasador:vastago", "pasador:cabeza", "pasador:centro"} <= set(sc.items)
    assert "pasador:auto" not in sc.items
    n, d = np.array(sc.kw["figura"][:3]), sc.kw["figura"][3]
    f = ctrl.pin_prev.face
    assert np.isclose(n @ f.outward, 1.0) and (f.center @ n) < d < (f.center @ n) + 0.01   # esconde el lado del torso
    c = sc.items["pasador:centro"][1][0]
    r = np.linalg.norm(sc.items["pasador:vastago"][1] - c, axis=1)
    assert np.allclose(r, ctrl.session.pin_spec("brazo").shaft_d / 2, atol=1e-6)
    ctrl.nudge_pin(0, 1)
    ctrl.sync(sc)
    assert "pasador:auto" in sc.items                                           # marca dónde lo habría puesto el programa
    ctrl.cancel()
    ctrl.sync(sc)
    assert not {"pasador:cara", "pasador:borde", "pasador:vastago", "pasador:cabeza", "pasador:centro",
                "pasador:auto"} & set(sc.items)
    assert "pasador:brazo" in sc.items and ctrl.session.limb("brazo").pin_center is not None
    assert "figura" in sc.items and sc.kw["figura"] is None


def test_clip_plane_discards_geometry_when_rendered(ctrl):
    pytest.importorskip("moderngl")
    from afm.gui.render import OffscreenRenderer
    try:
        off = OffscreenRenderer(320, 400)
    except Exception as e:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {e}")
    _with_polygon(ctrl, "brazo")
    ctrl.start_pin("brazo")
    ctrl.sync(off.scene)
    assert off.render(ctrl.camera).shape == (400, 320, 4)


# ---------------------------------------------------- plano de costado = cara de corte (Güemes)
def _lateral_leg(c):
    from afm.planes import Plane
    return Plane.from_normal((5.0, 0, 0), (-1, 0, 0), "costado")


def test_lateral_plane_defaults_to_outward_even_for_legs(ctrl):
    pl = _lateral_leg(ctrl)
    spec = ctrl.session.add_limb("pierna lateral", "leg", pl)
    assert spec.extrude == "outward"
    frontal = ctrl.session.add_limb("pierna frontal", "leg", demo_project().limbs[1].plane)
    assert frontal.extrude == "symmetric"


def test_fix_limb_extrude_repairs_old_projects(ctrl):
    spec = ctrl.session.add_limb("pierna lateral", "leg", _lateral_leg(ctrl), extrude="symmetric")
    spec.pin_center = (1.0, 2.0, 3.0)
    assert ctrl.session.fix_limb_extrude("pierna lateral") is True
    assert spec.extrude == "outward" and spec.pin_center is None
    assert ctrl.session.fix_limb_extrude("pierna lateral") is False          # idempotente
    assert ctrl.session.fix_limb_extrude("pierna") is False                  # frontal: no se toca


def test_start_pin_on_old_lateral_symmetric_limb_uses_the_plane_as_contact_face(ctrl):
    spec = ctrl.session.add_limb("pierna lateral", "leg", _lateral_leg(ctrl), extrude="symmetric")
    ctrl.session.set_limb_polygon("pierna lateral", _box_on_plane(spec.plane))
    ctrl.start_pin("pierna lateral")
    assert ctrl.mode == Mode.PIN and "hacia afuera" in ctrl.message
    assert spec.extrude == "outward"


def _box_on_plane(plane, half=40.0, z0=-1.0, z1=40.0):
    return plane.to_world(np.array([[-half, z0], [half, z0], [half, z1], [-half, z1]], float))


def test_pin_status_warns_when_pins_would_cross_at_the_symmetry_plane(ctrl):
    spec = ctrl.session.add_limb("pierna lateral", "leg", _lateral_leg(ctrl))
    ctrl.session.set_limb_polygon("pierna lateral", _box_on_plane(spec.plane))
    ctrl.start_pin("pierna lateral")
    # la cara está a ~5,7 mm del plano de simetría; el pasador de cadera mide 6,6 mm
    assert ctrl.session.pin_reach_margin("pierna lateral") < 0 and "ATENCIÓN" in ctrl.message


# ------------------------------------------- silueta sobre el plano de simetría + desplazamiento del corte
# (el fixture `ctrl` ya trae «brazo» y «pierna» sobre planos frontales; los nuevos se llaman «brazo 2» y «pierna 2»)
def _sym_box(ctrl, z0, z1):
    return ctrl.session.project.symmetry.to_world(np.array([[-40, z0], [40, z0], [40, z1], [-40, z1]], float))


def _arm_z():
    p = np.asarray(demo_project().limbs[0].polygon)
    return p[:, 2].min(), p[:, 2].max()


def test_limb_on_symmetry_plane_needs_offset_for_arms_and_defaults_for_legs(ctrl):
    from afm.limbs import DEFAULT_LEG_CUT_X, effective_cut_offset
    arm = ctrl.session.add_limb_on_symmetry("brazo 2", "arm")
    leg = ctrl.session.add_limb_on_symmetry("pierna 2", "leg")
    assert arm.extrude == "outward" and leg.extrude == "outward"
    assert effective_cut_offset(leg) == DEFAULT_LEG_CUT_X
    with pytest.raises(ValueError, match="desplazamiento"):
        effective_cut_offset(arm)
    assert ctrl.session.limb_offset("pierna 2") == DEFAULT_LEG_CUT_X
    assert ctrl.session.limb_offset("pierna") is None          # plano frontal: no aplica


def test_cut_offset_moves_the_contact_face(ctrl):
    z0, z1 = _arm_z()
    ctrl.add_limb_on_symmetry("arm")
    ctrl.session.set_limb_polygon("brazo 2", _sym_box(ctrl, z0, z1))
    ctrl.session.set_limb_offset("brazo 2", 15.0)
    ctrl.start_pin("brazo 2")
    assert ctrl.mode == Mode.PIN and ctrl.offset_limb() == "brazo 2"
    assert abs(ctrl.pin_prev.face.center[0] - (15.0 + 0.66)) < 0.05
    ctrl.set_limb_offset("brazo 2", 16.0)                      # en el modo pasador recalcula la cara
    assert ctrl.mode == Mode.PIN and abs(ctrl.pin_prev.face.center[0] - (16.0 + 0.66)) < 0.05
    assert ctrl.session.limb("brazo 2").pin_center is None


def test_set_limb_offset_validates(ctrl):
    ctrl.add_limb_on_symmetry("leg")
    with pytest.raises(ValueError):
        ctrl.session.set_limb_offset("pierna 2", 0.0)
    with pytest.raises(ValueError):
        ctrl.session.set_limb_offset("brazo", 5.0)             # plano frontal: no aplica
    ctrl.set_limb_offset("brazo", 5.0)                         # el controlador no lanza: informa
    assert "no está sobre el plano de simetría" in ctrl.message


def test_cut_offset_roundtrips_in_project(ctrl, tmp_path):
    ctrl.add_limb_on_symmetry("leg")
    ctrl.session.set_limb_offset("pierna 2", 9.5)
    path = tmp_path / "p.proyecto"
    ctrl.session.project.save(str(path))
    from afm.project import Project
    assert any(l.cut_offset == 9.5 for l in Project.load(str(path)).limbs)
