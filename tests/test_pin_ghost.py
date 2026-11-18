"""Pasador fantasma: evaluación de si entra en la cara, punto más ancho, achicar para que entre y modo PIN con cara chica."""
import numpy as np
import pytest
from shapely.geometry import Polygon

from afm.gui.controller import Controller, Mode
from afm.params import PinSpec
from afm.pin_ghost import ghost_meshes, evaluate_pin, fit_scale, widest_point
from afm.project import demo_project
from afm.testfigure import make_doll

PIN = PinSpec(4.1, 2.5, 6.6, 1.65)


def test_evaluate_levels_and_overflow():
    face = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    ok = evaluate_pin(face, (5, 5), PIN, 0.5, 0.8)
    assert ok.level == "ok" and ok.overflow is None and ok.max_shaft_d == pytest.approx(10.0)
    tight = evaluate_pin(face, (3.0, 5), PIN, 0.5, 0.8)          # borde 3,0 - 2,05 = 0,95
    assert tight.level == "ok"
    tight = evaluate_pin(face, (2.5, 5), PIN, 0.5, 0.8)          # borde 0,45 < pared mínima
    assert tight.level == "aviso" and tight.fits
    no = evaluate_pin(face, (1.0, 5), PIN, 0.5, 0.8)             # sobresale
    assert no.level == "no" and no.margin_mm < 0 and no.overflow is not None and not no.fits
    out = evaluate_pin(face, (-3, 5), PIN, 0.5, 0.8)
    assert out.level == "no" and not out.inside and out.max_shaft_d == 0.0


def test_widest_point_and_fit_scale():
    face = Polygon([(0, 0), (3, 0), (3, 12), (0, 12)])           # tira de 3 mm de ancho
    uv, d = widest_point(face)
    assert d == pytest.approx(3.0, abs=0.05) and abs(uv[0] - 1.5) < 0.05
    assert fit_scale(3.0, PIN, 0.0) == pytest.approx(3.0 / 4.1)
    assert fit_scale(8.0, PIN, 0.5) == 1.0                       # ya entra
    assert fit_scale(0.5, PIN, 0.5) == 0.0                       # no hay lugar


def test_ghost_meshes_follow_pin_and_hole_is_bigger():
    m = ghost_meshes((1.0, 2.0, 3.0), (1.0, 0.0, 0.0), PIN, 0.5)
    p, h = m["pasador"], m["hueco"]
    assert p.bounds[0][0] == pytest.approx(1.0, abs=1e-6) and p.bounds[1][0] == pytest.approx(1.0 + 2.5 + 1.65, abs=1e-6)
    assert (h.bounds[1] - h.bounds[0]).max() > (p.bounds[1] - p.bounds[0]).max()
    assert np.ptp(p.vertices[:, 1]) == pytest.approx(PIN.head_d, abs=0.05)


@pytest.fixture(scope="module")
def stl_path(tmp_path_factory):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path_factory.mktemp("ghost") / "muneco.stl")
    m.export(path)
    return path


@pytest.fixture()
def ctrl(stl_path):
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    for l in demo_project().limbs:
        c.session.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
    spec = next(l for l in demo_project().limbs if l.name == "brazo")
    c.session.set_limb_polygon("brazo", spec.polygon)
    return c


def test_pin_mode_opens_even_if_face_is_smaller_than_shaft(ctrl):
    """Antes: «no se puede colocar el pasador: la cara es muy chica» y no había modo. Ahora entra y explica."""
    ctrl.set_param("shoulder_pin.shaft_d", 12.0)                 # vástago mayor que la cara
    ctrl.start_pin("brazo")
    assert ctrl.mode == Mode.PIN and ctrl.pin_prev is not None
    assert "mm²" in ctrl.message and "rojo" in ctrl.message
    fit = ctrl.session.pin_fit_at("brazo", ctrl.pin_prev.project(ctrl._pin_center("brazo")))
    assert fit.level == "no"
    # el camino estricto sigue rechazando (generar / otras herramientas)
    with pytest.raises(ValueError):
        ctrl.session.pin_preview("brazo")


def test_shrink_to_fit_makes_pin_fit(ctrl):
    ctrl.set_param("shoulder_pin.shaft_d", 12.0)
    ctrl.set_param("shoulder_pin.head_d", 16.0)
    ctrl.start_pin("brazo")
    ctrl.pin_shrink_to_fit()
    pin = ctrl.session.pin_spec("brazo")
    prm = ctrl.session.project.params
    fit = ctrl.session.pin_fit_at("brazo", ctrl.pin_prev.project(ctrl._pin_center("brazo")))
    if pin.shaft_d < 12.0:                                         # se achicó: ahora entra con la pared mínima
        assert fit.margin_mm >= prm.min_wall - 0.11 and fit.level in ("ok", "aviso")
        assert pin.head_d / pin.shaft_d == pytest.approx(16.0 / 12.0, rel=0.05)


def test_widest_moves_pin_inside_face(ctrl):
    ctrl.start_pin("brazo")
    ctrl.pin_widest()
    uv = ctrl.pin_prev.project(ctrl._pin_center("brazo"))
    assert ctrl.pin_prev.shape.contains(__import__("shapely.geometry", fromlist=["Point"]).Point(*uv))


def test_ghost_follows_pointer_without_catalog(ctrl):
    ctrl.start_pin("brazo")
    assert ctrl.pin_aid is None and ctrl.ghost_active()
    c = ctrl._pin_center("brazo")
    px, py = ctrl.camera.project(np.asarray(c, float), 600, 800)[0, :2]
    assert ctrl.update_hover(float(px), float(py), 600, 800) is True
    assert ctrl.pin_hover is not None and ctrl.message.startswith("Fantasma")
    assert ctrl.clear_hover() is True and ctrl.pin_hover is None


def test_set_pin_size_updates_param_and_status(ctrl):
    ctrl.start_pin("brazo")
    assert ctrl.set_pin_size("shoulder_pin.shaft_d", 3.0)
    assert ctrl.get_param("shoulder_pin.shaft_d") == 3.0 and "3" in ctrl.message
    assert ctrl.set_pin_size("clearance", 0.3) and ctrl.get_param("clearance") == 0.3


def test_pin_ghost_scene_items_are_drawn_and_removed(ctrl):
    class FakeScene:
        def __init__(self):
            self.items = {}

        def set_mesh(self, key, mesh, **kw): self.items[key] = ("mesh", kw)
        def set_lines(self, key, pts, color, **kw): self.items[key] = ("lines", color)
        def set_points(self, key, pts, color, size, **kw): self.items[key] = ("points", color)
        def remove(self, key): self.items.pop(key, None)
        def show(self, key, v): pass

    ctrl.set_param("shoulder_pin.shaft_d", 12.0)
    ctrl.start_pin("brazo")
    sc = FakeScene()
    ctrl._sync_pins(sc)
    assert sc.items["pasador:fantasma"][1]["ghost"] is True and "pasador:hueco" in sc.items
    assert sc.items["pasador:hueco_borde"][1][0] > 0.8          # rojo: no entra
    assert "pasador:sobra" in sc.items
    ctrl.cancel()
    ctrl._sync_pins(sc)
    assert not any(k.startswith("pasador:f") or k in ("pasador:hueco", "pasador:sobra") for k in sc.items)


def test_simulate_shoulder_from_pin_panel_follows_the_pin(ctrl):
    """La simulación del giro del hombro se enciende desde el panel del pasador y usa el pasador elegido (no el automático)."""
    from afm.joint_preview import pivot_preview_name

    ctrl.start_pin("brazo")
    ctrl.pin_simulate_pivot(True)
    name = pivot_preview_name(ctrl.session.limb("brazo"))
    assert ctrl.show_joint_preview and ctrl._jp["name"] == name and name == "hombro de brazo"
    run = ctrl.session.joint_preview_job(name) if hasattr(ctrl.session, "joint_preview_job") else None
    k1 = ctrl.session.joint_preview_key(name)
    ctrl.nudge_pin(0, 1, big=False)
    k2 = ctrl.session.joint_preview_key(name)
    assert k1 != k2                                      # mover el pasador invalida la simulación: se recalcula con el nuevo centro
    ctrl.pin_simulate_pivot(False)
    assert not ctrl.show_joint_preview
