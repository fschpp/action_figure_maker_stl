"""GUI (sin GTK): articulaciones de rodilla / codo (plan de articulaciones, Fase 5)."""
import dataclasses

import numpy as np
import pytest

from afm.gui.controller import JOINT_PREFIX, Controller, Mode
from afm.gui.picking import PickResult
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
