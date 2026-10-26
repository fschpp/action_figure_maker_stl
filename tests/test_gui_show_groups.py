"""Interruptores generales de la lista de planos: mostrar / ocultar todos los planos, todos los pines de
articulación y todos los contornos (siluetas y recortes) de una vez."""
import numpy as np
import pytest

from afm.gui.controller import SHOW_GROUPS, Controller
from afm.gui.picking import PickResult
from afm.testfigure import make_doll


@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    c.start_limb_plane("arm")
    it = iter([(30, 0, 70.0), (30, 10, 60.0), (30, -10, 60.0)])
    c.picker.pick_pixel = lambda *a, **k: (lambda p: PickResult(p, p, 0, np.array([1.0, 0, 0]), True))(np.array(next(it), float))
    for _ in range(3):
        c.click(10, 10, 100, 100)
    name = c.session.project.limbs[0].name
    plane = c.session.limb(name).plane
    c.session.set_limb_polygon(name, plane.to_world(np.array([[-10, -10], [10, -10], [10, 10], [-10, 10]], float)))
    c.session.limb(name).pin_center = (30.0, 0.0, 60.0)
    c.add_trim_primitive("torso", "sphere", np.array([5.0, 0.0, 40.0]), 6.0, "subtract", 5.0)
    for g in SHOW_GROUPS:                                  # al iniciar vienen apagados: las pruebas los prenden
        c.set_group_visible(g, True)
    return c, name


@pytest.fixture()
def scene():
    from afm.gui.render import OffscreenRenderer
    try:
        return OffscreenRenderer(160, 200).scene
    except Exception as e:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {e}")


def _vis(scene, prefix):
    return {k: it.visible for k, it in scene.items.items() if k.startswith(prefix) and not k.endswith(":borde")}


def test_the_three_switches_start_off(ctrl):
    assert not any(Controller().group_visible(g) for g in SHOW_GROUPS)       # planos, pines y contornos apagados al iniciar


def test_everything_is_visible_when_switched_on(ctrl, scene):
    c, name = ctrl
    c.sync(scene)
    assert all(c.group_visible(g) for g in SHOW_GROUPS)
    for prefix in (f"plano:{name}", f"silueta:{name}", f"pasador:{name}", "recorte:0"):
        assert _vis(scene, prefix) and all(_vis(scene, prefix).values()), prefix


@pytest.mark.parametrize("group,prefixes", [
    ("planes", ("plano:",)),
    ("pins", ("pasador:",)),
    ("contours", ("silueta:", "recorte:")),
])
def test_group_switch_hides_and_shows_only_its_group(ctrl, scene, group, prefixes):
    c, name = ctrl
    c.sync(scene)
    others = [p for g, ps in (("planes", ("plano:",)), ("pins", ("pasador:",)), ("contours", ("silueta:", "recorte:")))
              if g != group for p in ps]
    c.set_group_visible(group, False)
    assert c.scene_dirty
    c.sync(scene)
    for pre in prefixes:
        v = _vis(scene, pre)
        assert v and not any(v.values()), pre              # todo su grupo, oculto
    for pre in others:
        assert all(_vis(scene, pre).values()), pre        # lo demás sigue a la vista
    c.set_group_visible(group, True)
    c.sync(scene)
    for pre in prefixes:
        assert all(_vis(scene, pre).values()), pre


def test_group_switch_combines_with_the_per_plane_one(ctrl, scene):
    c, name = ctrl
    c.toggle_plane(name)                                   # plano del brazo oculto a mano
    c.sync(scene)
    assert not any(_vis(scene, f"plano:{name}").values())
    c.set_group_visible("planes", False)
    c.set_group_visible("planes", True)                    # volver a mostrar todo no revive el que se ocultó a mano
    c.sync(scene)
    assert not any(_vis(scene, f"plano:{name}").values())


def test_unknown_group_is_rejected(ctrl):
    c, _ = ctrl
    with pytest.raises(ValueError):
        c.set_group_visible("otros", False)
    with pytest.raises(ValueError):
        c.group_visible("otros")


def test_group_switch_also_hides_the_live_mirror(ctrl, scene):
    c, name = ctrl
    c.set_one_side(True)
    c.mirror_live = True
    c.sync(scene)
    assert c.mirror_active()
    mirrored = ("espejo:silueta:", "espejo:pasador:", "espejo:recorte:")
    assert all(_vis(scene, p) and all(_vis(scene, p).values()) for p in mirrored)
    c.set_group_visible("contours", False)
    c.set_group_visible("pins", False)
    c.sync(scene)
    assert not any(any(_vis(scene, p).values()) for p in mirrored)
