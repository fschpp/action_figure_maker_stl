"""Al dibujar la silueta de una pierna se puede ver la simulación de la cadera (pasador + hueco en la pelvis) al costado de la
figura, como con la rodilla y el codo; con el botón «Simulación» de la barra del visor."""
import dataclasses

import numpy as np
import pytest

from afm.gui import mode_bar
from afm.gui.controller import Controller, Mode
from afm.project import demo_project
from afm.testfigure import make_doll


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
    c.camera.set_view("frente")
    return c


def _leg(c):
    return next(l.name for l in c.session.project.limbs if l.kind == "leg")


def _arm(c):
    return next(l.name for l in c.session.project.limbs if l.kind == "arm")


def test_leg_silhouette_offers_the_hip_simulation(ctrl):
    leg = _leg(ctrl)
    ctrl.start_polygon(leg)
    assert ctrl.mode == Mode.POLYGON
    assert ctrl.polygon_hip_name() == f"cadera de {leg}" and ctrl.preview_here_available()
    assert "preview" in mode_bar.help_for(Mode.POLYGON)[1]
    ctrl.toggle_joint_preview_here()
    assert ctrl.show_joint_preview and ctrl._jp["name"] == f"cadera de {leg}" and ctrl.joint_preview_beside()


def test_arm_silhouette_offers_the_shoulder_simulation(ctrl):
    arm = _arm(ctrl)
    ctrl.start_polygon(arm)
    assert ctrl.polygon_hip_name() == f"hombro de {arm}" and ctrl.preview_here_available()
    ctrl.toggle_joint_preview_here()
    assert ctrl.show_joint_preview and ctrl.joint_preview_beside()


def test_simulation_sits_beside_the_figure_while_drawing_the_leg_silhouette(ctrl):
    leg = _leg(ctrl)
    scene = FakeScene()
    ctrl.start_polygon(leg)
    ctrl.toggle_joint_preview_here()
    ctrl.sync(scene)
    assert scene.visible["figura"] is True and "junta:prox" in scene.items         # la figura sigue a la vista para dibujar
    slab = ctrl.session.work.vertices
    assert scene.items["junta:prox"].bounds[0][0] > slab[:, 0].max()               # vista de frente: a un costado en X
    ctrl.cancel(quiet=True)
    ctrl.sync(scene)
    assert not ctrl.show_joint_preview and "junta:prox" not in scene.items         # al terminar vuelve la figura sola


def test_side_view_moves_the_simulation_along_y_not_toward_the_eye(ctrl):
    leg = _leg(ctrl)
    ctrl.camera.set_view("izquierda")                       # se mira a lo largo de X: +X apuntaría al ojo
    scene = FakeScene()
    ctrl.start_polygon(leg)
    ctrl.toggle_joint_preview_here()
    ctrl.sync(scene)
    axis, sign = ctrl._jp["shift_axis"]
    assert axis == 1
    v = ctrl.session.work.vertices
    b = scene.items["junta:prox"].bounds
    if sign > 0:
        assert b[0][1] > v[:, 1].max()
    else:
        assert b[1][1] < v[:, 1].min()


def test_simulation_follows_from_the_silhouette_to_the_second_silhouette(ctrl):
    leg = _leg(ctrl)
    ctrl.start_polygon(leg)
    ctrl.toggle_joint_preview_here()
    ctrl.start_polygon(leg, cross=True)
    assert ctrl.show_joint_preview and ctrl.joint_preview_beside()
    ctrl.start_polygon(_arm(ctrl))
    assert not ctrl.show_joint_preview                       # a un brazo no lo acompaña: no hay cadera


def test_recomputes_when_the_silhouette_changes(ctrl):
    leg = _leg(ctrl)
    ctrl.start_polygon(leg)
    ctrl.toggle_joint_preview_here()
    ctrl.sync(FakeScene())
    key0 = ctrl._jp["key"]
    assert key0 is not None
    spec = ctrl.session.limb(leg)
    ctrl.session.set_limb_polygon(leg, np.asarray(spec.polygon, float) + np.array([0.0, 0.0, -1.0]))
    assert ctrl.session.joint_preview_key(f"cadera de {leg}") != key0


def _wait_preview(ctrl, scene, tries=200):
    import time
    for _ in range(tries):
        ctrl.sync(scene)
        if "junta:prox" in scene.items:
            return
        time.sleep(0.05)
    raise AssertionError("la simulación no se calculó")


@pytest.mark.parametrize("view", ["frente", "izquierda"])
def test_simulation_stays_still_while_the_flexion_changes(ctrl, view):
    """Girar «Flexión mostrada» solo mueve la pieza distal: el resto de la simulación (el torso / la pelvis tallados) no se corre."""
    leg = _leg(ctrl)
    ctrl.camera.set_view(view)                               # de costado el corrimiento va sobre Y, el plano en que gira la pierna
    scene = FakeScene()
    ctrl.start_polygon(leg)
    ctrl.toggle_joint_preview_here()
    _wait_preview(ctrl, scene)
    lo, hi = ctrl._jp["pv"].flex_range
    ref = np.array(scene.items["junta:prox"].bounds)
    for flex in (lo, 0.0, 0.5 * (lo + hi), hi):
        ctrl.set_joint_preview_flex(flex)
        ctrl.sync(scene)
        assert np.allclose(scene.items["junta:prox"].bounds, ref, atol=1e-6), flex
