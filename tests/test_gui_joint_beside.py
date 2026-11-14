"""Al elegir el punto de la rodilla / el codo, la simulación de la articulación se ve AL COSTADO de la figura (que sigue a
la vista para elegir puntos) y el vértice bajo el puntero se pinta de rojo también al mover el eje."""
import dataclasses

import numpy as np
import pytest

from afm.gui.controller import Controller, Mode
from afm.gui.picking import PickResult
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll

W, H = 600, 400


class FakeScene:
    def __init__(self):
        self.items, self.visible = {}, {}

    def set_mesh(self, name, mesh, color=None, clip_x=-1e9, clip_plane=None):
        self.items[name] = mesh
        self.visible[name] = True

    def show(self, name, visible):
        self.visible[name] = visible

    def remove(self, name):
        self.items.pop(name, None)
        self.visible.pop(name, None)

    def clear(self):
        self.items.clear()

    def __getattr__(self, name):
        if name.startswith("set_"):
            return lambda key, *a, **k: self.items.setdefault(key, None)
        raise AttributeError(name)


@pytest.fixture()
def ctrl(tmp_path):
    path = str(tmp_path / "muneco.stl")
    make_doll().export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    c.session.project.limbs = demo_project().limbs
    c.session.project.params = dataclasses.replace(c.session.project.params, decimate=0)
    c.session.add_joint("brazo", Plane.from_normal((18.5, 0, 59.0), (0, 0.5, 1.0), "articulación"), anchor=(18.5, 0, 59.0))
    c.camera.set_view("frente")
    return c


def _vertex_px(c):
    v = c.session.work.vertices
    sel = v[(v[:, 2] > 40) & (np.abs(v[:, 0]) < 8)]
    p = sel[np.argmin(sel[:, 1])]
    px, py, _ = c.camera.project(p, W, H)[0]
    return float(px), float(py)


def test_hover_is_red_also_when_choosing_the_hinge_axis(ctrl):
    px, py = _vertex_px(ctrl)
    ctrl.start_joint_axis("codo")
    assert ctrl.mode == Mode.JOINT_AXIS and ctrl.hover_active()
    assert ctrl.update_hover(px, py, W, H) is True and ctrl.hover_vertex is not None


def test_hover_is_red_choosing_the_three_knee_points_even_with_the_simulation_on(ctrl):
    px, py = _vertex_px(ctrl)
    ctrl.session.project.joints.clear()
    ctrl.set_joint_preview("cadera de pierna", True)
    ctrl.start_joint_plane("brazo")
    assert ctrl.update_hover(px, py, W, H) is True
    scene = FakeScene()
    ctrl.sync(scene)
    assert "hover:vertice" in scene.items and scene.visible["figura"] is True      # la figura sigue a la vista


def test_simulation_sits_beside_the_figure_while_the_axis_tool_is_active(ctrl):
    scene = FakeScene()
    ctrl.set_joint_preview("codo", True)
    ctrl.sync(scene)
    assert scene.visible["figura"] is False                                          # fuera de la herramienta: reemplaza a la figura
    ctrl.start_joint_axis("codo")
    assert ctrl.joint_preview_beside()
    ctrl.sync(scene)
    assert scene.visible["figura"] is True and "junta:prox" in scene.items
    slab = ctrl.session.work.vertices
    assert scene.items["junta:prox"].bounds[0][0] > slab[:, 0].max()                # a la derecha de la figura
    z0, z1 = scene.items["junta:prox"].bounds[:, 2]
    assert slab[:, 2].min() < z0 and z1 < slab[:, 2].max()                           # a la altura de la articulación
    ctrl.cancel(quiet=True)
    ctrl.sync(scene)
    assert scene.visible["figura"] is True and "junta:prox" not in scene.items      # al terminar vuelve la figura y se apaga la simulación
    assert not ctrl.show_joint_preview


def test_axis_can_be_readjusted_without_leaving_the_tool_when_the_simulation_is_on(ctrl):
    ctrl.set_joint_preview("codo", True)
    ctrl.start_joint_axis("codo")
    it = iter([(18.5, 1.0, 62.0), (18.5, 1.5, 63.0)])
    ctrl.picker.pick_pixel = lambda *a, **k: PickResult(*(np.asarray(next(it), float),) * 2, 0, np.array([0.0, 1.0, 0.0]), True)
    ctrl.click(10, 10, 100, 100)
    assert ctrl.mode == Mode.JOINT_AXIS and ctrl.joint_name == "codo"
    assert ctrl.session.joint("codo").axis_center[2] == pytest.approx(62.0)
    ctrl.click(10, 10, 100, 100)
    assert ctrl.session.joint("codo").axis_center[2] == pytest.approx(63.0)


def test_toggle_from_the_bar_turns_the_simulation_on_and_off(ctrl):
    ctrl.start_joint_axis("codo")
    assert not ctrl.show_joint_preview
    ctrl.toggle_joint_preview_here()
    assert ctrl.show_joint_preview and ctrl._jp["name"] == "codo"
    ctrl.toggle_joint_preview_here()
    assert not ctrl.show_joint_preview
