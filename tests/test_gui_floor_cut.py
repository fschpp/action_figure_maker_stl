"""Corte paralelo al piso (cabeza / cintura): el plano horizontal se traslada en paralelo, como «Corte a» con el plano de simetría."""
import dataclasses

import numpy as np
import pytest

from afm.gui.controller import Controller, Mode
from afm.gui.picking import PickResult
from afm.head import HEAD_NAME
from afm.planes import floor_height, floor_plane
from afm.project import Project
from afm.testfigure import make_doll
from afm.waist import WAIST_NAME

NECK = [(-5, -5, 80.0), (5, -5, 80.0), (0, 5, 80.0)]


@pytest.fixture()
def ctrl(tmp_path):
    path = str(tmp_path / "muneco.stl")
    make_doll().export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    return c


def _small_pin(ctrl):
    sess = ctrl.session
    sess.project.params = dataclasses.replace(sess.project.params, decimate=0,
                                              neck_pin=dataclasses.replace(sess.project.params.neck_pin, shaft_d=3.2, head_d=5.2))


def _click_at(ctrl, point):
    ctrl.picker.pick_pixel = lambda *a, **k: PickResult(*(np.asarray(point, float),) * 2, 0, np.array([0.0, 1.0, 0.0]), True)
    ctrl.click(10, 10, 100, 100)


# ------------------------------------------------------------------ geometría
def test_floor_plane_is_horizontal_and_reports_its_height():
    pl = floor_plane(42.5, "x")
    assert np.allclose(pl.normal, (0, 0, 1)) and floor_height(pl) == pytest.approx(42.5)


def test_floor_height_is_none_for_a_tilted_or_missing_plane():
    from afm.planes import Plane
    assert floor_height(None) is None
    assert floor_height(Plane.from_points((-5, -5, 80), (5, -5, 80), (0, 5, 82))) is None
    assert floor_height(Plane.from_points(*NECK)) == pytest.approx(80.0)        # 3 puntos a una misma altura = paralelo al piso


# ------------------------------------------------------------------ controlador
@pytest.mark.parametrize("place,attr,name", [("head", "head", HEAD_NAME), ("waist", "waist", WAIST_NAME)])
def test_start_creates_a_floor_parallel_plane_at_a_default_height(ctrl, place, attr, name):
    ctrl.start_floor_cut(place)
    spec = getattr(ctrl.session.project, attr)
    assert ctrl.mode == Mode.FLOOR_CUT and spec is not None
    assert np.allclose(spec.plane.normal, (0, 0, 1))
    assert ctrl.session.floor_cut_height(place) == pytest.approx(ctrl.session.default_floor_cut_height(place))
    assert any(e.id == name for e in ctrl.plane_entries())                       # aparece en la lista de planos


def test_needs_symmetry(tmp_path):
    path = str(tmp_path / "m.stl")
    make_doll().export(path)
    c = Controller()
    c.open_stl(path)
    c.start_floor_cut("head")
    assert c.mode == Mode.VIEW and "simetría" in c.message and c.session.project.head is None


def test_translating_keeps_the_plane_parallel_and_resets_the_pin(ctrl):
    ctrl.start_floor_cut("head")
    ctrl.session.project.head.pin_center = (0.0, 1.0, 70.0)
    ctrl.set_floor_cut_height("head", 70.0)
    spec = ctrl.session.project.head
    assert np.allclose(spec.plane.normal, (0, 0, 1)) and floor_height(spec.plane) == pytest.approx(70.0)
    assert spec.pin_center is None                                              # la cara del corte se movió
    ctrl.set_floor_cut_height("head", 75.5)
    assert ctrl.session.floor_cut_height("head") == pytest.approx(75.5)


def test_translating_keeps_the_rest_of_the_head_config(ctrl):
    ctrl.session.set_head(ctrl.session.plane_from_points(NECK, "cabeza"))
    ctrl.session.project.head = dataclasses.replace(ctrl.session.project.head, replacement_yaw=33.0)
    ctrl.set_floor_cut_height("head", 78.0)
    assert ctrl.session.project.head.replacement_yaw == 33.0


def test_out_of_range_height_is_rejected_and_nothing_changes(ctrl):
    ctrl.start_floor_cut("waist")
    before = ctrl.session.floor_cut_height("waist")
    top = ctrl.session.work.bounds[1][2]
    for bad in (0.0, -3.0, top, top + 10, float("nan")):
        ctrl.set_floor_cut_height("waist", bad)
        assert ctrl.session.floor_cut_height("waist") == pytest.approx(before)
    assert "entre" in ctrl.message


def test_arrows_nudge_and_clamp(ctrl):
    ctrl.start_floor_cut("head")
    h0 = ctrl.session.floor_cut_height("head")
    ctrl.nudge_floor_cut(+1)
    assert ctrl.session.floor_cut_height("head") == pytest.approx(h0 + 0.1)
    ctrl.nudge_floor_cut(-1, big=True)
    assert ctrl.session.floor_cut_height("head") == pytest.approx(h0 + 0.1 - 1.0)
    lo, hi = ctrl.session.floor_cut_range()
    ctrl.set_floor_cut_height("head", hi)
    ctrl.nudge_floor_cut(+1, big=True)
    assert ctrl.session.floor_cut_height("head") == pytest.approx(hi)           # no se sale de la figura


def test_arrows_do_nothing_outside_the_tool(ctrl):
    ctrl.start_floor_cut("head")
    h0 = ctrl.session.floor_cut_height("head")
    ctrl.cancel()
    ctrl.nudge_floor_cut(+1)
    assert ctrl.session.floor_cut_height("head") == pytest.approx(h0)


def test_click_on_the_figure_sets_the_height_and_keeps_the_tool_active(ctrl):
    ctrl.start_floor_cut("waist")
    _click_at(ctrl, (3.0, 4.0, 58.25))
    assert ctrl.session.floor_cut_height("waist") == pytest.approx(58.25)
    assert ctrl.mode == Mode.FLOOR_CUT                                          # se puede reajustar con otro clic
    _click_at(ctrl, (0.0, 0.0, 61.0))
    assert ctrl.session.floor_cut_height("waist") == pytest.approx(61.0)
    ctrl.cancel()
    assert ctrl.mode == Mode.VIEW


def test_replaces_a_tilted_three_point_plane_but_keeps_an_existing_floor_height(ctrl):
    ctrl.session.set_head(ctrl.session.plane_from_points([(-5, -5, 78), (5, -5, 78), (0, 5, 84)], "cabeza"))
    assert ctrl.session.floor_cut_height("head") is None
    ctrl.start_floor_cut("head")
    assert ctrl.session.floor_cut_height("head") == pytest.approx(ctrl.session.default_floor_cut_height("head"))
    ctrl.set_floor_cut_height("head", 77.0)
    ctrl.start_floor_cut("head")                                                # volver a la herramienta no pisa la altura
    assert ctrl.session.floor_cut_height("head") == pytest.approx(77.0)


def test_blocked_reasons_for_the_height_control(ctrl):
    assert ctrl.blocked("floor_cut_head") is not None                           # todavía no hay plano paralelo al piso
    ctrl.start_floor_cut("head")
    assert ctrl.blocked("floor_cut_head") is None and ctrl.blocked("floor_cut_waist") is not None


# ------------------------------------------------------------------ proyecto y escala
def test_project_roundtrip_and_rescale_keep_the_floor_plane(ctrl, tmp_path):
    ctrl.start_floor_cut("head")
    ctrl.set_floor_cut_height("head", 74.0)
    ctrl.start_floor_cut("waist")
    ctrl.set_floor_cut_height("waist", 52.0)
    pr = ctrl.session.project
    path = str(tmp_path / "p.proyecto")
    pr.save(path)
    back = Project.load(path)
    assert floor_height(back.head.plane) == pytest.approx(74.0) and floor_height(back.waist.plane) == pytest.approx(52.0)
    back.scale_geometry(2.0)
    assert floor_height(back.head.plane) == pytest.approx(148.0) and floor_height(back.waist.plane) == pytest.approx(104.0)


# ------------------------------------------------------------------ de punta a punta
def test_generate_with_head_and_waist_cut_by_floor_planes(ctrl):
    _small_pin(ctrl)
    ctrl.start_floor_cut("head")
    ctrl.set_floor_cut_height("head", 80.0)
    ctrl.start_floor_cut("waist")
    ctrl.set_floor_cut_height("waist", 55.0)
    out = ctrl.session.build()
    pieces = out.result.pieces
    assert set(pieces) == {"cabeza", "torso", "pelvis"}, set(pieces)
    assert out.validation["ok"], out.validation["problems"]
    # el torso queda entre los dos planos (la cabeza y la pelvis solo lo penetran con el vástago de su pasador)
    tz0, tz1 = pieces["torso"].bounding_box()[2], pieces["torso"].bounding_box()[5]
    assert 55.0 <= tz0 < 57.0 and 78.0 < tz1 <= 80.0, (tz0, tz1)
    assert pieces["pelvis"].bounding_box()[2] == pytest.approx(0.0, abs=1e-3)            # la pelvis sigue apoyada en el piso
    assert pieces["cabeza"].bounding_box()[5] == pytest.approx(ctrl.session.project.params.ref_height, abs=1e-2)


def test_moving_the_plane_moves_the_cut_in_the_generated_pieces(ctrl):
    _small_pin(ctrl)
    ctrl.start_floor_cut("head")
    tops = []
    for h in (76.0, 82.0):
        ctrl.set_floor_cut_height("head", h)
        out = ctrl.session.build()
        assert out.validation["ok"], out.validation["problems"]
        tops.append(out.result.pieces["torso"].bounding_box()[5])
    assert tops[1] - tops[0] == pytest.approx(6.0, abs=0.05)                           # trasladar 6 mm el plano sube 6 mm el corte
