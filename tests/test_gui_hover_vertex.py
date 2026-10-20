"""Al elegir puntos sobre la figura (planos de 3 puntos, guías), el vértice cercano al puntero se pinta de rojo y un
clic lo elige tal cual."""
import numpy as np
import pytest

from afm.gui.controller import HOVER_RADIUS_PX, Controller, Mode
from afm.testfigure import make_doll

W, H = 600, 400


@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.camera.set_view("frente")
    return c


def _front_vertex(c):
    """Un vértice visible de frente (el de más -Y entre los de la mitad superior, lejos de bordes) y su píxel."""
    v = c.session.work.vertices
    sel = v[(v[:, 2] > 40) & (np.abs(v[:, 0]) < 8)]
    p = sel[np.argmin(sel[:, 1])]
    px, py, _ = c.camera.project(p, W, H)[0]
    return p, float(px), float(py)


def test_nearest_vertex_finds_it_within_the_radius_and_not_far_away(ctrl):
    p, px, py = _front_vertex(ctrl)
    found = ctrl.picker.nearest_vertex(ctrl.camera, px + 3, py - 2, W, H, 12.0)
    assert found is not None
    q = ctrl.camera.project(found[1], W, H)[0, :2]
    assert np.hypot(q[0] - (px + 3), q[1] - (py - 2)) <= 12.0          # el elegido está dentro del radio
    assert ctrl.picker.nearest_vertex(ctrl.camera, 2, 2, W, H, 12.0) is None     # fuera de la figura


def test_hover_only_in_point_picking_modes(ctrl):
    p, px, py = _front_vertex(ctrl)
    assert ctrl.update_hover(px, py, W, H) is False and ctrl.hover_vertex is None          # modo ver: nada
    ctrl.start_symmetry()
    assert ctrl.update_hover(px, py, W, H) is True and ctrl.hover_vertex is not None       # se pinta
    assert ctrl.update_hover(px, py, W, H) is False                                         # igual: no hay que redibujar
    assert ctrl.update_hover(2, 2, W, H) is True and ctrl.hover_vertex is None              # al alejarse se suelta


def test_cancel_clears_hover(ctrl):
    p, px, py = _front_vertex(ctrl)
    ctrl.start_symmetry()
    ctrl.update_hover(px, py, W, H)
    ctrl.cancel(quiet=True)
    assert ctrl.hover_vertex is None


def test_click_on_the_red_vertex_picks_exactly_that_vertex(ctrl):
    p, px, py = _front_vertex(ctrl)
    ctrl.start_symmetry()
    ctrl.update_hover(px + 2, py + 2, W, H)
    red = ctrl.hover_vertex.copy()
    hit = ctrl.click(px + 2, py + 2, W, H)
    assert hit is not None and hit.snapped
    assert np.allclose(ctrl.pending[0], red)                    # el punto elegido es el vértice marcado, sin ajuste
    assert any(np.allclose(red, v) for v in ctrl.session.work.vertices)
    assert ctrl.hover_vertex is None


def test_hover_vertex_is_drawn_in_red(ctrl):
    from afm.gui.render import OffscreenRenderer
    try:
        off = OffscreenRenderer(320, 400)
    except Exception as e:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {e}")
    p, px, py = _front_vertex(ctrl)
    ctrl.start_symmetry()
    ctrl.update_hover(px, py, W, H)
    ctrl.sync(off.scene)
    it = off.scene.items["hover:vertice"]
    assert tuple(it.color[:3]) == (1.0, 0.0, 0.0)
    ctrl.update_hover(2, 2, W, H)
    ctrl.sync(off.scene)
    assert "hover:vertice" not in off.scene.items
