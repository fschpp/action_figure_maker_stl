"""GUI: plano de corte «sin soportes» visible en la figura sentada, de costado, ajustable con clic y flechas."""
import numpy as np
import pytest

from afm.gui.controller import Controller, Mode
from tests.test_gui_phases35 import FakeScene, fill_limbs, make_ctrl

W = H = 600


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    c = make_ctrl(tmp_path_factory.mktemp("muneco"))
    fill_limbs(c)
    out = c.generate()
    assert out is not None and out.validation["ok"], out and out.validation["problems"]
    return c


@pytest.fixture()
def c(built):
    yield built
    built.cancel(quiet=True)
    built.set_pose_view(False)
    built.session.project.params.flat_depth = 1.0


def test_needs_a_generated_figure(tmp_path):
    ctrl = make_ctrl(tmp_path)
    ctrl.start_flat_plane()
    assert ctrl.mode == Mode.VIEW and "Primero generá" in ctrl.message


def test_start_shows_seated_pose_from_the_right(c):
    c.start_flat_plane()
    assert c.mode == Mode.FLAT_PLANE and c.pose_active()
    assert c.pose_angle == c.session.project.params.sit_angle      # sentada, como «ver la figura en la pose de cadera»
    assert (c.camera.yaw, c.camera.pitch) == (0.0, 0.0)            # vista Derecha


def test_plane_is_drawn_at_tail_plus_depth(c):
    c.session.project.params.flat_depth = 1.5
    c.start_flat_plane()
    sc = FakeScene()
    c.sync(sc)
    z = c.session.outcome.result.pieces["torso"].bounding_box()[2] + 1.5
    kind, plane = sc.items["plano_sin_soportes"]
    assert kind == "quad" and np.allclose(plane.normal, [0, 0, 1]) and plane.origin[2] == pytest.approx(z)
    line = sc.items["plano_sin_soportes:linea"][1]
    assert np.allclose(line[:, 2], z)
    c.cancel(quiet=True)
    c.sync(sc)
    assert "plano_sin_soportes" not in sc.items                     # solo se dibuja mientras se ajusta


def test_click_sets_depth_from_the_clicked_height(c):
    c.start_flat_plane()
    base = c.flat_base_z()
    target = base + 3.0
    px, py, _ = c.camera.project(np.array([0.0, 0.0, target]), W, H)[0]
    c.click(px, py, W, H)
    assert c.session.project.params.flat_depth == pytest.approx(3.0, abs=0.05)
    assert c.session.outcome is not None                            # ajustar el corte no invalida la figura
    assert c.pose_active()


def test_click_out_of_range_is_clamped_and_says_so(c):
    c.start_flat_plane()
    px, py, _ = c.camera.project(np.array([0.0, 0.0, c.flat_base_z() + 40.0]), W, H)[0]
    c.click(px, py, W, H)
    assert c.session.project.params.flat_depth == 6.0 and "rango" in c.message


def test_arrows_nudge_and_shift_is_ten_times(c):
    c.start_flat_plane()
    c.session.project.params.flat_depth = 2.0
    c.nudge_flat(+1)
    assert c.session.project.params.flat_depth == pytest.approx(2.1)
    c.nudge_flat(-1, big=True)
    assert c.session.project.params.flat_depth == pytest.approx(1.1)
    c.nudge_flat(-1, big=True)
    assert c.session.project.params.flat_depth == pytest.approx(0.2)    # tope inferior del rango


def test_front_view_click_asks_for_a_side_view(c):
    c.start_flat_plane()
    c.camera.set_view("frente")
    before = c.session.project.params.flat_depth
    c.click(W / 2, H / 2, W, H)
    assert c.session.project.params.flat_depth == before and "costado" in c.message


def test_legs_are_straight_while_adjusting_the_cut_and_after_if_the_checkbox_is_on(c, monkeypatch):
    seen = []
    import afm.pipeline as P
    real = P.posed_pieces

    def spy(res, angle, flex=None, p=None):
        seen.append(flex)
        return real(res, angle, flex, p)

    monkeypatch.setattr(P, "posed_pieces", spy)
    c._pose_cache = None
    c.start_flat_plane()
    assert c.legs_straight() and c.pose_report["estiradas"]
    assert seen and seen[-1] is not None                         # rodillas a 0° mientras se ajusta el corte
    c.cancel(quiet=True)
    assert not c.legs_straight()                                 # sin la casilla vuelve la flexión de la pose impresa
    c.set_flat_print(True)
    assert c.flat_print and c.pose_active() and c.legs_straight()
    assert "piernas estiradas" in c.message
    c.start_flat_plane()
    c.cancel(quiet=True)
    assert c.legs_straight()                                     # con la casilla activa sigue estirada
    c.set_flat_print(False)
    assert not c.legs_straight()


def test_click_arm_advice_is_empty_without_click_and_apply_says_so(c):
    assert c.flat_click_advice() == {} and c.flat_click_note() == ""
    assert c.apply_flat_click_angle() is False and "Ningún brazo con clic" in c.message
    assert c.session.outcome is not None                          # no invalidó la figura


def test_changing_flat_depth_in_parameters_keeps_the_generated_figure(c):
    assert c.set_param("flat_depth", 2.5)
    assert c.session.outcome is not None
    assert c.set_param("clearance", c.get_param("clearance") + 0.01)   # los demás parámetros sí invalidan
    assert c.session.outcome is None
