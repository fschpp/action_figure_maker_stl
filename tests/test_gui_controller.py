import numpy as np
import pytest

from afm.gui.controller import Controller, Mode, SYMMETRY_ID
from afm.gui.picking import PickResult
from afm.testfigure import make_doll


@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)                       # el STL "viene" en otras unidades
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    return c


def _hit(p):
    p = np.asarray(p, float)
    return PickResult(p, p, 0, np.array([0.0, 1.0, 0.0]), True)


def feed(c, pts):
    """Simula clics que impactan en los puntos dados (sin depender de la cámara)."""
    it = iter(pts)
    c.picker.pick_pixel = lambda *a, **k: _hit(next(it))
    for _ in pts:
        c.click(10, 10, 100, 100)


def test_open_frames_camera_and_normalizes(ctrl):
    assert ctrl.loaded and ctrl.mode == Mode.VIEW
    assert abs(ctrl.session.work.extents[2] - 100.0) < 1e-6
    assert np.allclose(ctrl.camera.target, ctrl.session.work.bounds.mean(axis=0))


def test_clicks_ignored_in_view_mode(ctrl):
    assert ctrl.click(10, 10, 100, 100) is None and ctrl.pending == []


def test_real_ray_click_hits_the_figure(ctrl):
    ctrl.start_symmetry()
    h, w = 600, 400
    c = ctrl.session.work.bounds.mean(axis=0)
    px, py, _ = ctrl.camera.project(c, w, h)[0]
    hit = ctrl.click(px, py, w, h)
    assert hit is not None and len(ctrl.pending) == 1
    ctrl.click(1, 1, w, h)                    # esquina del visor: no toca la figura
    assert len(ctrl.pending) == 1 and "no tocó" in ctrl.message


def test_crown_rescales_and_requires_no_planes(ctrl):
    ctrl.start_crown()
    assert ctrl.mode == Mode.CROWN
    feed(ctrl, [(0, 0, 90.0)])
    assert ctrl.mode == Mode.VIEW
    assert abs(ctrl.session.work.extents[2] - 100.0 * 100.0 / 90.0) < 1e-6
    ctrl.auto_symmetry()
    ctrl.start_crown()
    assert ctrl.mode == Mode.VIEW and "antes de definir" in ctrl.message


def test_symmetry_from_three_points(ctrl):
    ctrl.start_symmetry()
    feed(ctrl, [(0, -5, 10), (0, -5, 60), (0, 8, 40)])
    assert ctrl.mode == Mode.VIEW and ctrl.pending == []
    sym = ctrl.session.project.symmetry
    assert sym is not None and abs(abs(sym.normal[0]) - 1) < 1e-9
    assert [e.id for e in ctrl.plane_entries()] == [SYMMETRY_ID]


def test_non_vertical_symmetry_is_rejected(ctrl):
    ctrl.start_symmetry()
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 10, 10)])        # plano horizontal
    assert ctrl.session.project.symmetry is None and "No sirve" in ctrl.message and ctrl.pending == []


def test_limb_plane_requires_symmetry_then_adds_unique_names(ctrl):
    ctrl.start_limb_plane("arm")
    assert ctrl.mode == Mode.VIEW and "simetría" in ctrl.message
    ctrl.auto_symmetry()
    for _ in range(2):
        ctrl.start_limb_plane("arm")
        feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    ctrl.start_limb_plane("leg")
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    ids = [e.id for e in ctrl.plane_entries()]
    assert ids == [SYMMETRY_ID, "brazo", "brazo 2", "pierna"]
    assert ctrl.session.limb("pierna").kind == "leg"
    assert "silueta" in ctrl.problems()[0]


def test_collinear_points_rejected_and_undo_cancel(ctrl):
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("arm")
    feed(ctrl, [(0, 0, 10), (0, 0, 20), (0, 0, 30)])
    assert ctrl.session.project.limbs == [] and "No sirve" in ctrl.message
    ctrl.start_limb_plane("arm")
    feed(ctrl, [(0, 0, 10), (5, 0, 20)])
    ctrl.undo_point()
    assert len(ctrl.pending) == 1
    ctrl.cancel()
    assert ctrl.mode == Mode.VIEW and ctrl.pending == []


def test_remove_and_hide_planes(ctrl):
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("arm")
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    ctrl.toggle_plane("brazo")
    assert [e.visible for e in ctrl.plane_entries()] == [True, False]
    ctrl.remove_plane("brazo")
    assert [e.id for e in ctrl.plane_entries()] == [SYMMETRY_ID]
    ctrl.remove_plane(SYMMETRY_ID)
    assert ctrl.plane_entries() == [] and ctrl.session.project.symmetry is None


def test_set_front_resets_planes(ctrl):
    ctrl.auto_symmetry()
    ctrl.set_front("-Y")
    assert ctrl.plane_entries() == [] and ctrl.session.project.front == "-Y"


def test_project_roundtrip(ctrl, tmp_path):
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("leg")
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    p = str(tmp_path / "x.proyecto")
    ctrl.save_project(p)
    other = Controller()
    other.open_project(p)
    assert [e.id for e in other.plane_entries()] == [SYMMETRY_ID, "pierna"]
    assert other.picker is not None


def test_on_change_callback_fires(ctrl):
    calls = []
    ctrl.on_change = lambda: calls.append(1)
    ctrl.start_symmetry()
    ctrl.cancel()
    assert len(calls) >= 2


def test_sync_draws_planes_and_pending_points(ctrl):
    moderngl = pytest.importorskip("moderngl")
    from afm.gui.render import OffscreenRenderer
    try:
        off = OffscreenRenderer(320, 400)
    except Exception as e:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {e}")
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("arm")
    ctrl.pending = [np.array([0.0, 0.0, 10.0]), np.array([10.0, 0.0, 10.0])]
    ctrl.toggle_plane("simetria")
    ctrl.sync(off.scene)
    assert {"figura", "plano:simetria", "plano:simetria:borde", "pendientes", "pendientes:lineas"} <= set(off.scene.items)
    assert not off.scene.items["plano:simetria"].visible
    ctrl.one_side = True
    ctrl.sync(off.scene)
    assert off.scene.items["figura"].clip_x == 0.0
    ctrl.cancel()
    ctrl.remove_plane("simetria")
    ctrl.sync(off.scene)
    assert "plano:simetria" not in off.scene.items and "pendientes" not in off.scene.items
    img = off.render(ctrl.camera)
    assert img.shape == (400, 320, 4)
