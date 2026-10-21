"""GUI (sin GTK): pegar la mano estándar en otro modelo con 3 clics en su muñeca, ajustarla y verla en la escena."""
import numpy as np
import pytest

from afm import solids as S
from afm.gui.controller import Controller, Mode
from afm.gui.picking import PickResult
from afm.hand import extract_standard_hand, hand_meta, save_standard_hand

from test_gui_phases35 import FakeScene

M = S.Manifold
# plano de muñeca z = 57 del brazo izquierdo (+X); el punto 1 mira a +Y
WRIST = [(21.0, 3.0, 57.0), (18.0, -3.0, 57.0), (24.0, -3.0, 57.0)]


def _ell(rx, ry, h, z0, cx=0.0):
    return M.cylinder(h, 1, 1, 64).scale((rx, ry, 1)).translate((cx, 0, z0))


def _armed(hand_r: float):
    """Muñeco de 100 mm con brazos colgando (unidos al torso solo en el hombro) y una esfera de mano de radio `hand_r`."""
    parts = [_ell(23, 12, 8, 34), _ell(15, 10, 33, 42), M.sphere(11, 48).translate((0, 0, 89)), _ell(4, 4, 10, 72)]
    for sx in (+1, -1):
        parts.append(_ell(7, 7, 38, 0.0, cx=sx * 9))
        bridge = M.cylinder(12, 3.5, 3.5, 32).rotate((0, 90, 0)).translate((10 if sx > 0 else -22, 0, 71))
        parts += [M.cylinder(24, 3, 3, 32).translate((sx * 21, 0, 50)), bridge, M.sphere(hand_r, 32).translate((sx * 21, 0, 50))]
    return S.union(*parts)


@pytest.fixture(scope="module")
def std_hand(tmp_path_factory):
    hand, split = extract_standard_hand(_armed(5.0), WRIST)
    path = str(tmp_path_factory.mktemp("estandar") / "mano.stl")
    save_standard_hand(path, hand, hand_meta(split, height_mm=100.0, source="origen"))
    return path


@pytest.fixture()
def ctrl(tmp_path):
    stl = str(tmp_path / "modelo.stl")
    S.to_trimesh(_armed(3.5)).export(stl)
    c = Controller()
    c.open_stl(stl)
    c.auto_symmetry()
    assert c.session.project.symmetry is not None
    return c


def feed(c, pts):
    it = iter(pts)
    c.picker.pick_pixel = lambda *a, **k: PickResult(*(np.asarray(p, float) for p in (next(it),) * 2), 0,
                                                     np.array([0.0, 1.0, 0.0]), True)
    for _ in pts:
        c.click(10, 10, 100, 100)


def test_place_needs_symmetry(tmp_path, std_hand):
    stl = str(tmp_path / "m.stl")
    S.to_trimesh(_armed(3.5)).export(stl)
    c = Controller()
    c.open_stl(stl)
    c.start_hand_place(std_hand)
    assert c.mode == Mode.VIEW and "simetría" in c.message


def test_place_rejects_unreadable_file(ctrl, tmp_path):
    bad = tmp_path / "roto.stl"
    bad.write_text("no es una malla")
    ctrl.start_hand_place(str(bad))
    assert ctrl.mode == Mode.VIEW and "No se pudo leer" in ctrl.message and ctrl.session.project.hand is None


def test_three_clicks_place_the_hand(ctrl, std_hand):
    ctrl.start_hand_place(std_hand)
    assert ctrl.mode == Mode.HAND_PLACE and ctrl.hover_active()
    assert "punto 1" in ctrl.message
    feed(ctrl, WRIST)
    assert ctrl.mode == Mode.VIEW
    hand = ctrl.session.project.hand
    assert hand is not None and hand.path == std_hand and hand.points[0] == pytest.approx(WRIST[0], abs=1e-6)
    assert "Mano estándar pegada" in ctrl.message and "Ø6.0" in ctrl.message
    fit = ctrl.hand_fit()
    assert fit is not None and fit["info"]["puntos_espejados"] is False
    assert fit["hand"].bounds[1][0] > 25.5                                    # la esfera nueva (radio 5) llega más lejos que la vieja
    assert fit["figure"].volume < ctrl.session.work.volume                    # sin la mano original


def test_points_on_the_right_arm_work_too(ctrl, std_hand):
    ctrl.start_hand_place(std_hand)
    feed(ctrl, [(-x, y, z) for x, y, z in WRIST])
    assert ctrl.session.project.hand is not None
    assert ctrl.hand_fit()["info"]["puntos_espejados"] is True


def test_bad_plane_keeps_the_project_untouched(ctrl, std_hand):
    ctrl.start_hand_place(std_hand)
    feed(ctrl, [(60.0, 0.0, 70.0), (60.0, -2.0, 60.0), (60.0, 2.0, 60.0)])     # fuera de la figura
    assert ctrl.mode == Mode.VIEW and "No sirve" in ctrl.message
    assert ctrl.session.project.hand is None and ctrl.hand_fit() is None


def test_cancel_forgets_the_pending_file(ctrl, std_hand):
    ctrl.start_hand_place(std_hand)
    ctrl.cancel(quiet=True)
    assert ctrl.mode == Mode.VIEW and ctrl._hand_path_pending is None


def test_yaw_invert_and_clear(ctrl, std_hand):
    ctrl.start_hand_place(std_hand)
    feed(ctrl, WRIST)
    assert ctrl.set_hand_adjust(yaw=90.0) and ctrl.session.project.hand.yaw == 90.0
    assert ctrl.invert_hand_place() and ctrl.session.project.hand.invert is True
    assert "AVISO" in ctrl.message                                             # la «mano» es ahora el lado grande
    assert ctrl.invert_hand_place() and ctrl.session.project.hand.invert is False
    ctrl.clear_hand()
    assert ctrl.session.project.hand is None and ctrl.hand_fit() is None
    assert "mano original" in ctrl.message
    assert not ctrl.set_hand_adjust(yaw=10.0) and "Primero elegí" in ctrl.message


def test_adjust_that_fails_changes_nothing(ctrl, std_hand, monkeypatch):
    ctrl.start_hand_place(std_hand)
    feed(ctrl, WRIST)
    before = ctrl.session.project.hand
    monkeypatch.setattr("afm.gui.session.Session._fit_hand", lambda self, spec: (_ for _ in ()).throw(ValueError("plano malo")))
    assert not ctrl.set_hand_adjust(yaw=45.0)
    assert ctrl.session.project.hand == before and "plano malo" in ctrl.message


def test_sync_draws_the_new_hand_and_hides_the_old_one(ctrl, std_hand):
    scene = FakeScene()
    ctrl.sync(scene)
    assert "mano:reemplazo" not in scene.items and scene.items["figura"][1] is ctrl.session.work
    ctrl.start_hand_place(std_hand)
    feed(ctrl, WRIST)
    ctrl.sync(scene)
    fit = ctrl.hand_fit()
    assert scene.items["mano:reemplazo"][1] is fit["hand"]
    assert scene.items["figura"][1] is fit["figure"]                            # la figura sin la mano original
    ctrl.clear_hand()
    ctrl.sync(scene)
    assert "mano:reemplazo" not in scene.items and scene.items["figura"][1] is ctrl.session.work


def test_mirror_live_includes_the_new_hand(ctrl, std_hand):
    ctrl.start_hand_place(std_hand)
    feed(ctrl, WRIST)
    ctrl.set_one_side(True)
    ctrl.set_mirror_live(True)
    scene = FakeScene()
    ctrl.sync(scene)
    assert "espejo:mano:reemplazo" in scene.items and "espejo:figura" in scene.items


def test_missing_hand_file_is_reported_before_building(ctrl, std_hand, tmp_path):
    import shutil

    copy = str(tmp_path / "copia.stl")
    shutil.copy(std_hand, copy)
    ctrl.start_hand_place(copy)
    feed(ctrl, WRIST)
    assert ctrl.session.problems() == [] or all("mano" not in p for p in ctrl.session.problems())
    import os
    os.remove(copy)
    assert any("mano estándar" in p and "ya no existe" in p for p in ctrl.session.problems())
    pf = str(tmp_path / "p.proyecto")
    ctrl.save_project(pf)
    c2 = Controller()
    c2.open_project(pf, ctrl.session.stl_path)                                  # abrir con el STL de la mano ausente no rompe
    assert c2.hand_fit() is None and c2.session.project.hand is not None
    c2.sync(FakeScene())                                                        # ni al dibujar
    assert any("ya no existe" in p for p in c2.session.problems())


def test_project_roundtrip_keeps_the_hand(ctrl, std_hand, tmp_path):
    ctrl.start_hand_place(std_hand)
    feed(ctrl, WRIST)
    ctrl.set_hand_adjust(yaw=30.0)
    pf = str(tmp_path / "p.proyecto")
    ctrl.save_project(pf)
    c2 = Controller()
    c2.open_project(pf, ctrl.session.stl_path)
    assert c2.session.project.hand == ctrl.session.project.hand
    assert c2.hand_fit() is not None
