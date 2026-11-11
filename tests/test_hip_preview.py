"""Vista previa de la cadera (pasador + barrido de cadera en la pelvis) sin generar la figura: motor, sesión y controlador."""
import dataclasses

import numpy as np
import pytest

from afm import solids as S
from afm.gui.controller import Controller
from afm.joint_preview import HIP_PREFIX, hip_limb_name, hip_preview_name, prepare_limb, preview_hip
from afm.params import PinSpec
from afm.project import PartRef, demo_project
from afm.testfigure import make_doll

HIP = hip_preview_name("pierna")


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


@pytest.fixture(scope="module")
def params():
    return dataclasses.replace(demo_project().params, decimate=0)


@pytest.fixture(scope="module")
def leg(params):
    body = S.to_manifold(make_doll())
    half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    spec = next(l for l in demo_project().limbs if l.name == "pierna")
    limb, face = prepare_limb(body, spec, params, half=half)
    return half, limb, face, spec


# ------------------------------------------------------------------ nombres
def test_hip_names_roundtrip():
    assert hip_preview_name("pierna") == HIP_PREFIX + "pierna"
    assert hip_limb_name(hip_preview_name("pierna")) == "pierna"
    assert hip_limb_name("codo") is None and hip_limb_name(HIP_PREFIX) is None


# ------------------------------------------------------------------ motor
def test_hip_preview_carves_the_pelvis_and_rotates_the_thigh(leg, params):
    half, limb, face, spec = leg
    pv = preview_hip(half, limb, face, spec, params)
    assert pv.proximal_mesh().is_watertight and len(pv.proximal_mesh().faces) > 100
    assert pv.carved_mm3 > 1.0 and 0.0 < pv.carved_pct < 100.0
    assert pv.flex_range == (0.0, params.sit_angle + params.hip_margin)
    assert pv.mechanism == "hongo"
    a, b = pv.distal_mesh(0.0), pv.distal_mesh(params.sit_angle)
    assert abs(a.volume - b.volume) < 1e-3 * a.volume                  # el mismo sólido movido
    assert np.linalg.norm(a.centroid - b.centroid) > 5.0
    assert "Cadera" in pv.summary() and "Flexión mostrada" in pv.summary()


def test_hip_axis_goes_through_the_pin_and_pivots_the_thigh(leg, params):
    half, limb, face, spec = leg
    pv = preview_hip(half, limb, face, spec, params)
    assert np.allclose(pv.build.center, face.center)
    assert pv.build.axis[0] >= 0 and np.linalg.norm(pv.build.axis) == pytest.approx(1.0)
    c, ax = pv.build.center, pv.build.axis

    def radius(mesh):                                                    # distancia del centroide a la recta del eje
        d = mesh.centroid - c
        return float(np.linalg.norm(d - (d @ ax) * ax))

    r0, r60 = radius(pv.distal_mesh(0.0)), radius(pv.distal_mesh(60.0))
    assert r0 == pytest.approx(r60, rel=1e-4)                            # gira alrededor del eje: no se aleja ni se acerca
    assert np.linalg.norm(pv.distal_mesh(0.0).centroid - pv.distal_mesh(60.0).centroid) > 5.0
    assert np.allclose(pv.distal_mesh(360.0).centroid, pv.distal_mesh(0.0).centroid, atol=1e-6)


def test_longer_sweep_carves_more(leg, params):
    half, limb, face, spec = leg
    short = preview_hip(half, limb, face, dataclasses.replace(spec, sweep_deg=(0.0, 30.0)), params)
    full = preview_hip(half, limb, face, spec, params)
    assert short.carved_mm3 < full.carved_mm3
    assert short.flex_range == (0.0, 30.0)


def test_wider_clearance_carves_more(leg, params):
    half, limb, face, spec = leg
    tight = preview_hip(half, limb, face, spec, dataclasses.replace(params, clearance=0.3))
    loose = preview_hip(half, limb, face, spec, dataclasses.replace(params, clearance=0.8))
    assert tight.carved_mm3 < loose.carved_mm3


def test_oversized_pin_warns_that_it_sticks_out(leg, params):
    half, limb, face, spec = leg
    big = dataclasses.replace(params, hip_pin=PinSpec(60.0, 2.3, 70.0, 2.4))
    pv = preview_hip(half, limb, face, spec, big)
    assert pv.pin_margin_mm is not None and pv.pin_margin_mm < 0
    assert any("sobresale" in w for w in pv.warnings) and "sobresale" in pv.summary()


def test_hip_needs_a_leg(leg, params):
    half, limb, face, _ = leg
    arm = next(l for l in demo_project().limbs if l.name == "brazo")
    with pytest.raises(ValueError, match="no es una pierna"):
        preview_hip(half, limb, face, arm, params)


def test_hip_with_click_mechanism_uses_the_detent_volume(leg, params):
    half, limb, face, spec = leg
    pv = preview_hip(half, limb, face, dataclasses.replace(spec, mechanism="detent"), params)
    assert pv.mechanism == "clic" and pv.carved_mm3 > 1.0 and pv.proximal_mesh().is_watertight


# ------------------------------------------------------------------ sesión
@pytest.fixture()
def ctrl(tmp_path):
    path = str(tmp_path / "muneco.stl")
    make_doll().export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    c.session.project.limbs = demo_project().limbs
    c.session.project.params = dataclasses.replace(c.session.project.params, decimate=0)
    return c


def test_session_lists_one_hip_per_leg(ctrl):
    s = ctrl.session
    assert s.hip_previews() == [HIP]
    assert s.preview_names() == [HIP] and s.is_hip_preview(HIP)
    assert s.preview_exists(HIP) and not s.preview_exists("cadera de brazo") and not s.preview_exists(None)
    assert not s.is_hip_preview("codo")


def test_session_lists_hips_next_to_the_joints(ctrl):
    from afm.planes import Plane

    s = ctrl.session
    s.add_joint("brazo", Plane.from_normal((18.5, 0, 59.0), (0, 0, 1.0), "articulación"), anchor=(18.5, 0, 59.0))
    assert s.preview_names() == ["codo", HIP]


def test_no_hips_with_a_catalog_torso(ctrl):
    ctrl.session.project.parts = {"torso": PartRef(id="torso-demo", linea="demo")}
    assert ctrl.session.hip_previews() == [] and not ctrl.session.preview_exists(HIP)


def test_hip_preview_key_changes_with_every_input(ctrl):
    s = ctrl.session
    k = s.joint_preview_key(HIP)
    assert s.joint_preview_key(HIP) == k                                 # estable si nada cambia
    s.project.params = dataclasses.replace(s.project.params, clearance=0.7)
    k2 = s.joint_preview_key(HIP)
    s.project.params = dataclasses.replace(s.project.params, sit_angle=80.0)
    k3 = s.joint_preview_key(HIP)
    s.limb("pierna").pin_center = np.asarray(s.limb("pierna").pin_center if s.limb("pierna").pin_center is not None
                                             else (10.0, 0.0, 70.0), float) + (0.0, 0.0, 1.0)
    k4 = s.joint_preview_key(HIP)
    assert len({k, k2, k3, k4}) == 4


def test_hip_job_reuses_the_cut_leg_between_runs(ctrl):
    job = ctrl.session.joint_preview_job(HIP)
    cache: dict = {}
    first = job(cache)
    val = cache["val"]
    again = job(cache)
    assert cache["val"] is val                                           # la pierna cortada no se rehace
    assert again.carved_mm3 == pytest.approx(first.carved_mm3)


def test_hip_job_refuses_a_catalog_only_figure(ctrl, monkeypatch):
    monkeypatch.setattr(ctrl.session, "catalog_only", lambda: True)
    with pytest.raises(KeyError):                                         # sin cadera que previsualizar
        ctrl.session.joint_preview_job(HIP)


# ------------------------------------------------------------- controlador
def test_controller_shows_the_hip_pieces_and_hides_the_figure(ctrl):
    scene = FakeScene()
    assert ctrl.set_joint_preview(None, True)                            # por omisión: la primera entrada (la cadera)
    assert ctrl._jp["name"] == HIP and ctrl._jp["flex"] == 0.0
    ctrl.sync(scene)
    assert "junta:prox" in scene.items and "junta:dist" in scene.items and "junta:eje" in scene.items
    assert scene.visible["figura"] is False
    assert ctrl.joint_preview_status().startswith("Cadera")
    before = ctrl._jp["pv"]
    ctrl.set_joint_preview_flex(ctrl.session.project.params.sit_angle)  # solo gira el muslo: no recalcula
    ctrl.sync(scene)
    assert ctrl._jp["pv"] is before
    ctrl.set_joint_preview(HIP, False)
    ctrl.sync(scene)
    assert "junta:prox" not in scene.items and scene.visible["figura"] is True


def test_hip_is_not_editable_in_the_preview(ctrl):
    assert ctrl.is_hip_preview(HIP) and not ctrl.joint_editable(HIP)
    assert not ctrl.is_hip_preview("codo") and not ctrl.is_hip_preview(None)


def test_controller_recomputes_the_hip_when_the_parameters_change(ctrl):
    scene = FakeScene()
    ctrl.set_joint_preview(HIP, True)
    ctrl.sync(scene)
    before = ctrl._jp["pv"]
    ctrl.session.project.params = dataclasses.replace(ctrl.session.project.params, clearance=0.8)
    ctrl.sync(scene)
    assert ctrl._jp["pv"] is not before and ctrl._jp["pv"].carved_mm3 > before.carved_mm3


def test_switching_to_the_hip_resets_the_flexion(ctrl):
    from afm.planes import Plane

    s = ctrl.session
    s.add_joint("brazo", Plane.from_normal((18.5, 0, 59.0), (0, 0, 1.0), "articulación"), anchor=(18.5, 0, 59.0))
    ctrl.set_joint_preview("codo", True)
    ctrl.set_joint_preview_flex(60.0)
    ctrl.set_joint_preview(HIP, True)
    assert ctrl._jp["flex"] == 0.0
    ctrl.set_joint_preview_flex(30.0)
    ctrl.set_joint_preview("codo", True)                                 # volver a una articulación no toca la flexión
    assert ctrl._jp["flex"] == 30.0


def test_controller_reports_a_hip_that_cannot_be_built(ctrl):
    big = dataclasses.replace(ctrl.session.project.params, hip_pin=PinSpec(60.0, 2.3, 70.0, 2.4))
    ctrl.session.project.params = big
    ctrl.set_joint_preview(HIP, True)
    ctrl.sync(FakeScene())
    assert ctrl.show_joint_preview and "sobresale" in ctrl.joint_preview_status()


def test_preview_without_any_joint_or_leg(ctrl):
    ctrl.session.project.limbs = [l for l in ctrl.session.project.limbs if l.kind != "leg"]
    ctrl.set_joint_preview(None, True)
    assert not ctrl.show_joint_preview and "No hay ninguna articulación" in ctrl.message
