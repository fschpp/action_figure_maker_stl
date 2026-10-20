"""GUI (sin GTK): plano de la cintura, entrada en la lista de planos, generación con pecho + pelvis y vista de pose."""
import dataclasses

import numpy as np
import pytest

from afm.gui.controller import PARAM_FIELDS, Controller, Mode
from afm.gui.picking import PickResult
from afm.testfigure import make_doll
from afm.waist import PELVIS_NAME

WAIST = [(-10, -6, 55.0), (10, -6, 55.0), (0, 8, 55.0)]


@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    return c


def feed(c, pts):
    it = iter(pts)
    c.picker.pick_pixel = lambda *a, **k: PickResult(*(np.asarray(p, float) for p in (next(it),) * 2), 0,
                                                     np.array([0.0, 1.0, 0.0]), True)
    for _ in pts:
        c.click(10, 10, 100, 100)


def test_waist_plane_needs_symmetry(tmp_path):
    m = make_doll()
    path = str(tmp_path / "m.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.start_waist_plane()
    assert c.mode == Mode.VIEW and "simetría" in c.message


def test_three_clicks_define_the_waist(ctrl):
    ctrl.start_waist_plane()
    assert ctrl.mode == Mode.WAIST_PLANE
    feed(ctrl, WAIST)
    assert ctrl.mode == Mode.VIEW and ctrl.session.project.waist is not None
    ent = {e.id: e for e in ctrl.plane_entries()}
    assert ent["cintura"].kind == "waist" and "cintura" in ctrl.message
    assert ctrl.problems() == []                       # solo la cintura alcanza para generar


def test_vertical_waist_plane_is_rejected(ctrl):
    ctrl.start_waist_plane()
    feed(ctrl, [(0, 0, 50.0), (0, 10, 50.0), (0, 0, 60.0)])
    assert ctrl.session.project.waist is None and "No sirve" in ctrl.message


def test_remove_waist_plane_and_symmetry(ctrl):
    ctrl.session.set_waist(ctrl.session.plane_from_points(WAIST, "cintura"))
    ctrl.remove_plane("cintura")
    assert ctrl.session.project.waist is None and not any(e.id == "cintura" for e in ctrl.plane_entries())
    ctrl.session.set_waist(ctrl.session.plane_from_points(WAIST, "cintura"))
    ctrl.remove_plane("simetria")
    assert ctrl.session.project.waist is None         # depende de la simetría, igual que cabeza y miembros


def test_front_change_clears_waist(ctrl):
    ctrl.session.set_waist(ctrl.session.plane_from_points(WAIST, "cintura"))
    ctrl.session.set_front("-Y")
    assert ctrl.session.project.waist is None


def test_changing_the_plane_keeps_the_pin_options(ctrl):
    sess = ctrl.session
    spec = sess.set_waist(sess.plane_from_points(WAIST, "cintura"))
    sess.project.waist = dataclasses.replace(spec, pin_on="upper", twist_deg=30.0)
    spec2 = sess.set_waist(sess.plane_from_points([(-10, -6, 58.0), (10, -6, 58.0), (0, 8, 58.0)], "cintura"))
    assert spec2.pin_on == "upper" and spec2.twist_deg == 30.0 and spec2.pin_center is None


def test_waist_params_are_editable(ctrl):
    keys = {f[0] for f in PARAM_FIELDS}
    assert {"waist_pin.shaft_d", "waist_pin.shaft_len", "waist_pin.head_d", "waist_pin.head_h", "waist_twist"} <= keys
    assert ctrl.set_param("waist_pin.shaft_d", 7.0) is not False
    assert ctrl.session.project.params.waist_pin.shaft_d == pytest.approx(7.0)


def test_generate_waist_only_and_pose_view_shows_both_halves(ctrl):
    sess = ctrl.session
    sess.project.params = dataclasses.replace(sess.project.params, decimate=0)
    sess.set_waist(sess.plane_from_points(WAIST, "cintura"))
    out = sess.build()
    assert set(out.result.pieces) == {"torso", PELVIS_NAME} and out.validation["ok"], out.validation["problems"]
    sess.outcome = out
    assert ctrl._torso_meshes(out).keys() == {"torso", PELVIS_NAME}
    rep = ctrl.pose_check(90.0)
    assert rep["ok"]                                   # pecho y pelvis no cuentan como interferencia entre sí


def test_flat_cut_is_measured_from_the_pelvis_not_the_chest(ctrl):
    """Con cintura, `torso` es el pecho (arriba): el corte «sin soportes» se mide desde la cola, o sea la pelvis."""
    sess = ctrl.session
    sess.project.params = dataclasses.replace(sess.project.params, decimate=0)
    sess.set_waist(sess.plane_from_points(WAIST, "cintura"))
    out = sess.build()
    sess.outcome = out
    pelvis_z = out.result.pieces[PELVIS_NAME].bounding_box()[2]
    chest_z = out.result.pieces["torso"].bounding_box()[2]
    assert pelvis_z < chest_z - 1.0
    assert ctrl.flat_base_z() == pytest.approx(pelvis_z)
    sess.project.params.flat_depth = 2.0
    assert ctrl.flat_cut_z() == pytest.approx(pelvis_z + 2.0)
    ctrl.start_flat_plane()
    assert "z = %.1f" % (pelvis_z + 2.0) in ctrl.message
