"""GUI (sin GTK): articulaciones de rodilla / codo (plan de articulaciones, Fase 5)."""
import dataclasses

import numpy as np
import pytest

from afm.gui.controller import GUIDE_ARM_COLOR, GUIDE_FOREARM_COLOR, JOINT_PREFIX, Controller, Mode
from afm.gui.picking import PickResult
from afm.gui.session import Session
from afm.project import Project, demo_project
from afm.testfigure import make_doll

KNEE = [(5, -5, 14.0), (15, -5, 14.0), (9, 5, 14.0)]
ELBOW = [(14, -4, 59.0), (23, -4, 59.0), (18, 4, 59.0)]


@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    c.session.project.limbs = demo_project().limbs
    c.session.project.params = dataclasses.replace(c.session.project.params, decimate=0)
    return c


def feed(c, pts):
    it = iter(pts)
    c.picker.pick_pixel = lambda *a, **k: PickResult(*(np.asarray(p, float) for p in (next(it),) * 2), 0,
                                                     np.array([0.0, 1.0, 0.0]), True)
    for _ in pts:
        c.click(10, 10, 100, 100)


def test_three_clicks_define_the_knee_and_list_it(ctrl):
    ctrl.start_joint_plane("pierna")
    assert ctrl.mode == Mode.JOINT_PLANE
    feed(ctrl, KNEE)
    assert ctrl.mode == Mode.VIEW
    j = ctrl.session.joint("rodilla")
    assert j is not None and j.kind == "knee" and j.limb == "pierna"
    c = np.asarray(j.axis_center)
    assert c[2] > 14.5                                         # el eje queda arriba del plano (lado proximal)
    assert c[0] == pytest.approx(29 / 3) and c[1] == pytest.approx(-5 / 3)     # x, y = centroide de los 3 clics
    ent = {e.id: e for e in ctrl.plane_entries()}
    assert ent[JOINT_PREFIX + "rodilla"].kind == "joint"
    assert "rango" in ctrl.message.lower() or "flexión" in ctrl.message


def test_elbow_for_arm_and_one_joint_per_limb(ctrl):
    ctrl.start_joint_plane("brazo")
    feed(ctrl, ELBOW)
    assert ctrl.session.joint("codo").kind == "elbow"
    ctrl.start_joint_plane("brazo")
    assert ctrl.mode == Mode.VIEW and "ya tiene" in ctrl.message
    ctrl.start_joint_plane("no existe")
    assert "No hay un miembro" in ctrl.message


def test_vertical_joint_plane_is_rejected(ctrl):
    ctrl.start_joint_plane("pierna")
    feed(ctrl, [(0, 0, 10.0), (0, 10, 10.0), (0, 0, 20.0)])
    assert ctrl.session.project.joints == [] and "No sirve" in ctrl.message


def test_axis_click_keeps_x_and_moves_yz(ctrl):
    ctrl.session.add_joint("pierna", ctrl.session.plane_from_points(KNEE, "a"), anchor=(9.0, 0.0, 14.0))
    x0 = ctrl.session.joint("rodilla").axis_center[0]
    ctrl.start_joint_axis("rodilla")
    assert ctrl.mode == Mode.JOINT_AXIS
    feed(ctrl, [(30.0, 1.5, 18.0)])
    c = ctrl.session.joint("rodilla").axis_center
    assert c[0] == pytest.approx(x0) and c[1] == pytest.approx(1.5) and c[2] == pytest.approx(18.0)
    assert ctrl.mode == Mode.VIEW


def test_axis_too_close_to_plane_is_rejected(ctrl):
    ctrl.session.add_joint("pierna", ctrl.session.plane_from_points(KNEE, "a"), anchor=(9.0, 0.0, 14.0))
    before = ctrl.session.joint("rodilla").axis_center
    ctrl.start_joint_axis("rodilla")
    feed(ctrl, [(30.0, 0.0, 14.2)])
    assert ctrl.session.joint("rodilla").axis_center == before and "No sirve" in ctrl.message
    assert ctrl.mode == Mode.JOINT_AXIS                        # sigue esperando otro clic


def test_tilt_and_range(ctrl):
    ctrl.session.add_joint("pierna", ctrl.session.plane_from_points(KNEE, "a"), anchor=(9.0, 0.0, 14.0))
    assert ctrl.set_joint_tilt("rodilla", 14.0)
    d = np.asarray(ctrl.session.joint("rodilla").axis_dir)
    assert d == pytest.approx([np.cos(np.radians(14)), 0.0, np.sin(np.radians(14))])    # vista de frente: z sube con x
    assert not ctrl.set_joint_tilt("rodilla", 80.0)
    assert ctrl.set_joint_range("rodilla", -5.0, 100.0) and ctrl.joint_range_text("rodilla").startswith("-5°")
    assert not ctrl.set_joint_range("rodilla", 50.0, 20.0)
    assert ctrl.set_joint_range("rodilla", None, None)         # vuelve al de los parámetros
    assert ctrl.session.joint("rodilla").flex_range is None


def test_remove_plane_and_limb_remove_the_joint(ctrl):
    ctrl.session.add_joint("pierna", ctrl.session.plane_from_points(KNEE, "a"), anchor=(9.0, 0.0, 14.0))
    ctrl.remove_plane(JOINT_PREFIX + "rodilla")
    assert ctrl.session.project.joints == []
    ctrl.session.add_joint("pierna", ctrl.session.plane_from_points(KNEE, "a"), anchor=(9.0, 0.0, 14.0))
    ctrl.remove_plane("pierna")
    assert ctrl.session.project.joints == []                   # no queda una articulación huérfana


def test_orphan_joint_blocks_generation(ctrl):
    ctrl.session.add_joint("pierna", ctrl.session.plane_from_points(KNEE, "a"), anchor=(9.0, 0.0, 14.0))
    ctrl.session.project.limbs = [l for l in ctrl.session.project.limbs if l.name != "pierna"]
    assert any("ya no existe" in p for p in ctrl.problems())


def test_joints_survive_save_and_load(ctrl, tmp_path):
    ctrl.session.add_joint("pierna", ctrl.session.plane_from_points(KNEE, "a"), anchor=(9.0, 0.0, 14.0), tilt_deg=14.0)
    ctrl.session.set_joint_range("rodilla", -5.0, 100.0)
    path = str(tmp_path / "p.proyecto")
    ctrl.session.project.save(path)
    back = Project.load(path).joints[0]
    assert back.flex_range == (-5.0, 100.0) and back.axis_dir[2] == pytest.approx(np.sin(np.radians(14)))


def test_sync_draws_axis_and_plane(ctrl):
    from test_gui_phases35 import FakeScene
    ctrl.session.add_joint("pierna", ctrl.session.plane_from_points(KNEE, "a"), anchor=(9.0, 0.0, 14.0))
    sc = FakeScene()
    ctrl.sync(sc)
    assert "eje:rodilla" in sc.items and f"plano:{JOINT_PREFIX}rodilla" in sc.items
    ctrl.session.remove_joint("rodilla")
    ctrl.sync(sc)
    assert "eje:rodilla" not in sc.items


def test_generate_with_clicked_knee(ctrl):
    ctrl.start_joint_plane("pierna")
    feed(ctrl, KNEE)
    ctrl.session.set_joint_axis("rodilla", (9.0, 0.0, 17.4))   # el del test de motor: eje a 3,4 mm sobre el plano
    out = ctrl.session.build()
    for nm in ("muslo_izq", "pantorrilla_izq", "muslo_der", "pantorrilla_der"):
        assert nm in out.result.pieces
    assert out.validation["articulaciones"]["rodilla"]["recorrido_mm3"] <= 1e-4


def test_start_for_kind_picks_limb_without_joint(ctrl):
    ctrl.start_joint_plane_for_kind("leg")
    assert ctrl.mode == Mode.JOINT_PLANE and ctrl.joint_limb == "pierna"
    feed(ctrl, KNEE)
    ctrl.start_joint_plane_for_kind("leg")                      # ya tiene: no queda otra pierna
    assert ctrl.mode == Mode.VIEW and "sin articulación" in ctrl.message
    ctrl.start_joint_plane_for_kind("arm")
    assert ctrl.joint_limb == "brazo"
    ctrl.cancel(quiet=True)
    assert ctrl.joint_limb is None and ctrl.mode == Mode.VIEW


def test_limit_setters_keep_the_other_end_and_tilt_readback(ctrl):
    ctrl.session.add_joint("pierna", ctrl.session.plane_from_points(KNEE, "a"), anchor=(9.0, 0.0, 14.0))
    lo, hi = ctrl.joint_limits("rodilla")
    assert (lo, hi) == (0.0, 90.0)
    assert ctrl.set_joint_limit("rodilla", "hi", 100.0) and ctrl.joint_limits("rodilla") == (0.0, 100.0)
    assert ctrl.set_joint_limit("rodilla", "lo", -5.0) and ctrl.joint_limits("rodilla") == (-5.0, 100.0)
    assert not ctrl.set_joint_limit("rodilla", "lo", 120.0)     # mínimo ≥ máximo: se rechaza
    ctrl.set_joint_tilt("rodilla", 14.5)
    assert ctrl.joint_tilt("rodilla") == pytest.approx(14.5)


def test_export_hinge_probes_from_controller(ctrl, tmp_path):
    info = ctrl.export_probes(str(tmp_path), which="hinge", clearances=(0.4,), shaft_deltas=(0.0,))
    assert info["celdas"] == 1 and (tmp_path / "probetas.stl").stat().st_size > 1000
    assert "bisagra" in ctrl.message


def tilted_elbow(deg):
    t = np.tan(np.radians(deg))
    return [(14.0, -4.0, 59.0), (23.0, -4.0, 59.0 + 9 * t), (18.0, 4.0, 59.0 + 4 * t)]      # z = 59 + t·(x − 14)


@pytest.mark.parametrize("deg", [10.0, -12.0, 0.0])
def test_axis_follows_the_plane_tilt_so_clicks_need_not_be_level(ctrl, deg):
    """Un brazo que cuelga en diagonal se corta con un plano inclinado vista de frente; el eje de la bisagra sigue esa
    inclinación (antes quedaba horizontal y los 3 clics había que darlos a la misma altura)."""
    ctrl.start_joint_plane("brazo")
    feed(ctrl, tilted_elbow(deg))
    assert ctrl.mode == Mode.VIEW and [j.name for j in ctrl.session.project.joints] == ["codo"]
    assert ctrl.joint_tilt("codo") == pytest.approx(deg, abs=1e-6)
    d = np.asarray(ctrl.session.joint("codo").axis_dir)
    n = np.asarray(ctrl.session.joint("codo").plane.normal, float)
    assert abs(d @ n) < 1e-9 and d[1] == 0.0               # el eje queda dentro del plano (traza en XZ)
    assert f"{abs(deg):.1f}°" in ctrl.message
    c = np.asarray(ctrl.session.joint("codo").axis_center, float)
    assert c[:2] == pytest.approx(np.mean(tilted_elbow(deg), axis=0)[:2])    # el eje queda sobre el centro de los clics
    assert (c - ctrl.session.joint("codo").plane.origin) @ n >= 3.2 - 1e-9     # y a la altura de pared mínima del plano


def test_explicit_tilt_overrides_the_plane_and_horizontal_plane_gives_zero(ctrl):
    ctrl.session.add_joint("brazo", ctrl.session.plane_from_points(ELBOW, "a"))
    assert ctrl.joint_tilt("codo") == pytest.approx(0.0)
    assert ctrl.set_joint_tilt("codo", 9.5) and ctrl.joint_tilt("codo") == pytest.approx(9.5)


# ---------------------------------------------------------------- codo / rodilla con 2 líneas de frente
def lines_pts(open_up, open_down, elbow=(18.0, -4.0, 59.0), length=10.0):
    """4 puntos: línea superior (abre `open_up`° al bajar, hacia +X) terminando en el codo; inferior saliendo de él."""
    ex, ey, ez = elbow
    su, sd = np.sin(np.radians(open_up)), np.sin(np.radians(open_down))
    cu, cd = np.cos(np.radians(open_up)), np.cos(np.radians(open_down))
    return [(ex - length * su, ey, ez + length * cu), (ex, ey, ez), (ex, ey, ez), (ex + length * sd, ey, ez - length * cd)]


def test_plane_from_lines_is_the_bisector_and_passes_through_the_elbow():
    pts = lines_pts(3.4, 6.8)
    plane, elbow, a_up, a_down, lift = Session.plane_from_lines(pts[:2], pts[2:])
    assert lift == pytest.approx([-np.sin(np.radians(3.4)), 0.0, np.cos(np.radians(3.4))])      # sube por la línea del brazo
    assert (a_up, a_down) == pytest.approx((3.4, 6.8))
    assert elbow == pytest.approx([18.0, -4.0, 59.0])
    n = np.asarray(plane.normal, float)
    assert n[1] == 0.0 and n[2] > 0
    assert Session.plane_front_tilt_deg(n) == pytest.approx((3.4 + 6.8) / 2)      # la bisectriz de las dos líneas
    assert abs(np.asarray(plane.origin, float) @ n - elbow @ n) < 1e-9


def test_plane_from_lines_vertical_lines_give_a_horizontal_plane_and_direction_order_is_irrelevant():
    pts = lines_pts(0.0, 0.0)
    plane, *_ = Session.plane_from_lines(pts[:2], pts[2:])
    assert np.asarray(plane.normal) == pytest.approx([0.0, 0.0, 1.0])
    swapped = Session.plane_from_lines((pts[1], pts[0]), (pts[3], pts[2]))[0]        # puntos en el otro sentido
    assert np.asarray(swapped.normal) == pytest.approx([0.0, 0.0, 1.0])


@pytest.mark.parametrize("pts, text", [
    ([(0, 0, 60), (0, 0, 60.5), (0, 0, 60), (0, 0, 50)], "demasiado corta"),          # solo cuenta la vista de frente
    ([(0, 0, 60), (10, 0, 59), (10, 0, 59), (10, 0, 50)], "60° de la vertical"),       # casi horizontal
])
def test_plane_from_lines_rejects_unusable_lines(pts, text):
    with pytest.raises(ValueError, match=text):
        Session.plane_from_lines(pts[:2], pts[2:])


def test_two_lines_define_the_elbow_with_the_axis_on_the_elbow_point(ctrl):
    ctrl.start_joint_lines_for_kind("arm")
    assert ctrl.mode == Mode.JOINT_LINES and ctrl.joint_limb == "brazo"
    pts = lines_pts(3.4, 6.8, elbow=(18.0, -4.0, 59.0))
    feed(ctrl, pts[:3])
    assert ctrl.mode == Mode.JOINT_LINES and len(ctrl.pending) == 3 and "antebrazo" in ctrl.message
    ctrl.undo_point()
    assert len(ctrl.pending) == 2
    feed(ctrl, pts[2:])
    assert ctrl.mode == Mode.VIEW and [j.name for j in ctrl.session.project.joints] == ["codo"]
    assert ctrl.joint_tilt("codo") == pytest.approx(5.1, abs=1e-6)
    jt = ctrl.session.joint("codo")
    c = np.asarray(jt.axis_center, float)
    n = np.asarray(jt.plane.normal, float)
    assert c[1] == pytest.approx(-4.0)
    assert (c - np.array([18.0, -4.0, 59.0])) @ n == pytest.approx(3.2)             # 3,2 mm del plano (pared del bulón)
    up = np.array([-np.sin(np.radians(3.4)), 0.0, np.cos(np.radians(3.4))])
    off = c - np.array([18.0, -4.0, 59.0])
    assert np.linalg.norm(off - (off @ up) * up) < 1e-9                  # el eje subió por la línea del brazo, no en vertical
    assert "3.4°" in ctrl.message and "6.8°" in ctrl.message and "5.1°" in ctrl.message


def test_two_lines_can_be_cancelled_and_bad_lines_do_not_create_a_joint(ctrl):
    ctrl.start_joint_lines_for_kind("arm")
    feed(ctrl, [(18.0, -4.0, 70.0), (18.0, -4.0, 70.4), (18.0, -4.0, 59.0), (18.0, -4.0, 49.0)])
    assert ctrl.session.project.joints == [] and "demasiado corta" in ctrl.message
    ctrl.start_joint_lines_for_kind("arm")
    feed(ctrl, lines_pts(0.0, 0.0)[:1])
    ctrl.cancel()
    assert ctrl.mode == Mode.VIEW and ctrl.pending == [] and ctrl.joint_limb is None


def test_two_lines_work_for_the_leg_and_a_limb_with_a_joint_is_refused(ctrl):
    ctrl.start_joint_lines_for_kind("leg")
    assert ctrl.joint_limb == "pierna" and "muslo" in ctrl.message and "rodilla" in ctrl.message
    feed(ctrl, lines_pts(0.0, 0.0, elbow=(9.0, 0.0, 14.0)))
    assert [j.name for j in ctrl.session.project.joints] == ["rodilla"]
    ctrl.start_joint_lines("pierna")
    assert ctrl.mode == Mode.VIEW and "ya tiene su articulación" in ctrl.message


def test_guides_are_visible_while_picking_and_stay_after_the_elbow_is_created(ctrl):
    """Se ven las 2 líneas todo el tiempo: cian = brazo, magenta = antebrazo; y el Estado da el ángulo de la primera."""
    ctrl.start_joint_lines_for_kind("arm")
    assert ctrl.guide_overlays() == []
    pts = lines_pts(3.4, 6.8)
    feed(ctrl, pts[:1])
    kinds = {(k, key) for k, key, *_ in ctrl.guide_overlays()}
    assert kinds == {("points", "guia:pend:brazo")}
    ctrl.cancel()
    ctrl.start_joint_lines_for_kind("arm")
    feed(ctrl, pts[:2])
    names = {key for _, key, *_ in ctrl.guide_overlays()}
    assert names == {"guia:pend:brazo", "guia:pend:brazo:l"} and "3.4°" in ctrl.message and "cian" in ctrl.message
    feed(ctrl, pts[2:3])
    over = {key: color for _, key, _, color in ctrl.guide_overlays()}
    assert over["guia:pend:ante"] == GUIDE_FOREARM_COLOR and over["guia:pend:brazo:l"] == GUIDE_ARM_COLOR
    assert "magenta" in ctrl.message
    feed(ctrl, pts[3:])
    final = {key: (kind, np.asarray(p)) for kind, key, p, _ in ctrl.guide_overlays()}
    assert set(final) == {"guia:codo:brazo", "guia:codo:antebrazo", "guia:codo:codo"}     # quedan dibujadas
    assert final["guia:codo:codo"][1][0] == pytest.approx([18.0, -4.0, 59.0])
    ctrl.remove_plane(JOINT_PREFIX + "codo")
    assert ctrl.guide_overlays() == [] and ctrl.joint_guides == {}


def test_guides_disappear_when_the_joint_is_remade_with_three_points(ctrl):
    ctrl.start_joint_lines_for_kind("arm")
    feed(ctrl, lines_pts(3.4, 6.8))
    assert ctrl.joint_guides
    ctrl.remove_plane(JOINT_PREFIX + "codo")
    ctrl.start_joint_plane_for_kind("arm")
    feed(ctrl, ELBOW)
    assert [j.name for j in ctrl.session.project.joints] == ["codo"] and ctrl.guide_overlays() == []


def test_a_bad_first_line_is_reported_right_after_the_second_click(ctrl):
    ctrl.start_joint_lines_for_kind("arm")
    feed(ctrl, [(18.0, -4.0, 70.0), (18.0, -4.0, 70.4)])
    assert "no sirve" in ctrl.message and "Backspace" in ctrl.message


class _RecordingScene:
    """Escena falsa: guarda qué pide dibujar `Controller.sync` y si lo pide por delante de la figura (`on_top`)."""

    def __init__(self):
        self.items, self.on_top = {}, {}

    def set_mesh(self, name, mesh, color=None, clip_x=-1e9):
        self.items[name] = ("mesh", mesh)

    def set_plane(self, name, plane, hs, center=None, color=None):
        self.items[name] = ("quad", plane)
        self.items[name + ":borde"] = ("lines", None)

    def set_lines(self, name, pl, color=None, closed=False, on_top=False):
        self.items[name], self.on_top[name] = ("lines", pl), on_top

    def set_points(self, name, pts, color=None, size=10.0, on_top=False):
        self.items[name], self.on_top[name] = ("points", np.asarray(pts)), on_top

    def show(self, name, visible):
        pass

    def remove(self, name):
        for k in (name, name + ":borde"):
            self.items.pop(k, None)
            self.on_top.pop(k, None)


def test_sync_draws_the_guides_in_front_of_the_figure_and_keeps_them(ctrl):
    """La geometría no puede taparlas: las líneas y los puntos de las guías se dibujan sin prueba de profundidad."""
    sc = _RecordingScene()
    ctrl.start_joint_lines_for_kind("arm")
    pts = lines_pts(3.4, 6.8)
    feed(ctrl, pts[:2])
    ctrl.sync(sc)
    assert {"guia:pend:brazo", "guia:pend:brazo:l"} <= set(sc.items)
    assert sc.on_top["guia:pend:brazo"] and sc.on_top["guia:pend:brazo:l"]
    assert "pendientes" not in sc.items                          # no se dibuja además con el estilo rojo normal
    feed(ctrl, pts[2:])
    ctrl.sync(sc)
    guides = {k for k in sc.items if k.startswith("guia:")}
    assert guides == {"guia:codo:brazo", "guia:codo:antebrazo", "guia:codo:codo"}
    assert all(sc.on_top[k] for k in guides)                     # quedan dibujadas, por delante, ya con el codo creado
    ctrl.remove_plane(JOINT_PREFIX + "codo")
    ctrl.sync(sc)
    assert not [k for k in sc.items if k.startswith("guia:")]
