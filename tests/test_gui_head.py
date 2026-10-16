"""GUI (sin GTK): plano del cuello, entrada en la lista de planos, generación solo con cabeza."""
import dataclasses

import numpy as np
import pytest

from afm.gui.controller import Controller, Mode
from afm.gui.picking import PickResult
from afm.testfigure import make_doll

NECK = [(-5, -5, 80.0), (5, -5, 80.0), (0, 5, 80.0)]


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


def test_neck_plane_needs_symmetry(tmp_path):
    m = make_doll()
    path = str(tmp_path / "m.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.start_neck_plane()
    assert c.mode == Mode.VIEW and "simetría" in c.message


def test_three_clicks_define_the_head(ctrl):
    ctrl.start_neck_plane()
    assert ctrl.mode == Mode.NECK_PLANE
    feed(ctrl, NECK)
    assert ctrl.mode == Mode.VIEW and ctrl.session.project.head is not None
    ent = {e.id: e for e in ctrl.plane_entries()}
    assert ent["cabeza"].kind == "head"
    assert "cabeza" in ctrl.trim_targets()
    assert ctrl.problems() == []                       # solo la cabeza alcanza para generar


def test_vertical_neck_plane_is_rejected(ctrl):
    ctrl.start_neck_plane()
    feed(ctrl, [(0, 0, 70.0), (0, 10, 70.0), (0, 0, 80.0)])
    assert ctrl.session.project.head is None and "No sirve" in ctrl.message


def test_remove_head_plane_and_symmetry(ctrl):
    ctrl.session.set_head(ctrl.session.plane_from_points(NECK, "cabeza"))
    ctrl.remove_plane("cabeza")
    assert ctrl.session.project.head is None and not any(e.id == "cabeza" for e in ctrl.plane_entries())
    ctrl.session.set_head(ctrl.session.plane_from_points(NECK, "cabeza"))
    ctrl.remove_plane("simetria")
    assert ctrl.session.project.head is None           # depende de la simetría, igual que los miembros


def test_generate_head_only_and_pose_ignores_head(ctrl):
    sess = ctrl.session
    sess.project.params = dataclasses.replace(sess.project.params, decimate=0,
                                              neck_pin=dataclasses.replace(sess.project.params.neck_pin,
                                                                           shaft_d=3.2, head_d=5.2))
    sess.set_head(sess.plane_from_points(NECK, "cabeza"))
    out = sess.build()
    assert set(out.result.pieces) == {"torso", "cabeza"} and out.validation["ok"], out.validation["problems"]
    ctrl.session.outcome = out
    assert ctrl.pose_limit() is None                   # la cabeza no cuenta como cadera
    assert ctrl._posed_pieces(90.0) == {}              # ni entra en la pose sentada


def test_front_change_clears_head(ctrl):
    ctrl.session.set_head(ctrl.session.plane_from_points(NECK, "cabeza"))
    ctrl.session.set_front("-Y")
    assert ctrl.session.project.head is None
