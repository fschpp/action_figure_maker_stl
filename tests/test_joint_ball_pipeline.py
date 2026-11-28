"""Plugins de articulación, Fase 4 (corte 4): `ball` registrado y despachado en `pipeline.build` (brazo y cadera), medidas por clase de
puerto (`class_pin`/`_class_jp`), validación de recorrido y de cabezas enfrentadas, y la grilla de poses de un cono menor que el paso."""
import dataclasses

import numpy as np
import pytest

from afm import joint_types as JT
from afm import pipeline as P
from afm import solids as S
from afm.joint_types import ConeMotion, testing as K
from afm.joint_types.ball import Ball, BallSpec, equivalent_pin, mouth_radius
from afm.joint_types.resolve import (class_ball, class_pin, class_pin_shift, class_requirements, class_signature, class_type_id,
                                     limb_type_id)
from afm.params import Params
from afm.project import demo_project
from afm.testfigure import make_doll


def _limbs(proj, **per_limb):
    return [dataclasses.replace(l, **per_limb[l.name]) if l.name in per_limb else l for l in proj.limbs]


def _build(**per_limb):
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0)
    limbs = _limbs(proj, **per_limb)
    res = P.build(S.to_manifold(make_doll()), limbs, p, proj.symmetry)
    return res, p, limbs


@pytest.fixture(scope="module")
def arm_1deg():
    return _build(brazo={"mechanism": "ball", "mechanism_params": {"half_angle": 1.0}})


@pytest.fixture(scope="module")
def arm_4deg():
    return _build(brazo={"mechanism": "ball", "mechanism_params": {"half_angle": 4.0}})


@pytest.fixture(scope="module")
def leg_ball():
    return _build(pierna={"mechanism": "ball", "mechanism_params": {"half_angle": 1.0}})


# --- registro -------------------------------------------------------------------------------------------------------------

def test_ball_is_registered_builtin_and_offered_in_shoulder_and_hip_slots():
    t = JT.get("ball")
    assert t.id == "ball" and "ball" in JT.BUILTIN and {"hombro", "cadera"} <= t.slots
    assert JT.cache_key_info(Params(joints={"hombro": {"type": "ball"}})) == {}        # interno: no cambia la clave de caché
    assert not K.failures(K.check_type(Ball())), "el kit de conformidad no pasa"


def test_unknown_mechanism_message_lists_ball_for_the_shoulder():
    from afm.limbs import LimbSpec
    from afm.planes import Plane

    spec = LimbSpec("brazo", "arm", Plane.from_normal((0, 0, 0), (0, 1, 0), "x"), np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1]], float), 1.0,
                    mechanism="cardan")
    with pytest.raises(ValueError, match=r"mushroom \| detent \| ball"):
        limb_type_id(spec, Params())


# --- medidas por clase de puerto --------------------------------------------------------------------------------------------

def test_class_resolution_follows_the_line_profile_for_a_ball_shoulder_and_hip():
    p = Params(joints={"hombro": {"type": "ball", "params": {"half_angle": 10.0}}, "cadera": {"type": "ball"}})
    assert class_type_id("hombro", p) == "ball" and class_type_id("cadera", p) == "ball"
    assert class_ball("hombro", p).half_angle == 10.0 and class_ball("cadera", p) == BallSpec()
    assert class_ball("hombro", Params()) == BallSpec()                      # sin perfil: las de siempre
    assert class_type_id("cuello", p) == "mushroom"                          # el cuello sigue siendo hongo fijo


def test_class_requirements_come_from_the_ball_not_from_the_mushroom():
    p = Params(joints={"cadera": {"type": "ball"}})
    jp = BallSpec()
    req = class_requirements("cadera", p)
    assert req.profundidad_mm == pytest.approx(jp.neck_len + jp.ball_d + p.clearance)
    assert req.separacion_min_mm == pytest.approx(2 * p.clearance)           # la cadera se enfrenta a su espejo
    assert class_pin_shift("cadera", p) == 0.0
    assert class_requirements("hombro", p).separacion_min_mm is None


def test_class_pin_of_a_ball_is_the_equivalent_pin_and_keeps_the_hongo_otherwise():
    p = Params(joints={"hombro": {"type": "ball"}})
    eq = class_pin("hombro", p)
    assert eq == equivalent_pin(BallSpec(), p.clearance)
    assert eq.shaft_d == pytest.approx(2 * mouth_radius(BallSpec(), p.clearance))     # lo que tiene que caber en la cara
    assert eq.head_d == BallSpec().ball_d
    assert class_pin("cadera", p) == p.hip_pin                                # sin ball en la cadera: el hongo de Params
    assert class_pin("cuello", p) == p.neck_pin


def test_class_signature_distinguishes_the_ball_by_its_measures():
    a = class_signature("hombro", Params(joints={"hombro": {"type": "ball"}}))
    b = class_signature("hombro", Params(joints={"hombro": {"type": "ball", "params": {"ball_d": 8.0}}}))
    assert a[0] == b[0] == "ball" and a[1] != b[1]
    assert class_signature("hombro", Params())[0] == "mushroom"


# --- la grilla de poses -----------------------------------------------------------------------------------------------------

def test_cone_smaller_than_the_step_still_reaches_its_edge():
    m = ConeMotion(S.Manifold.cube((2, 2, 2)), np.zeros(3), np.array([0.0, 0.0, 1.0]), half_angle=2.0)
    z = np.array([0.0, 0.0, 1.0])
    tilts = [np.degrees(np.arccos(np.clip(float((T[:3, :3] @ z) @ z), -1.0, 1.0))) for T in m.poses(2.5)]
    assert max(tilts) == pytest.approx(2.0, abs=1e-6) and len(tilts) > 1      # antes: una sola pose (el reposo)


# --- brazo con rótula -------------------------------------------------------------------------------------------------------

def test_arm_with_ball_builds_a_cone_motion_and_leaves_the_leg_alone(arm_1deg):
    res, _p, _limbs = arm_1deg
    assert isinstance(res.motion["brazo"], ConeMotion) and JT.as_revolute(res.motion["brazo"]) is None
    assert isinstance(res.motion["pierna"], JT.RevoluteMotion)
    assert res.motion["brazo"].half_angle == 1.0
    assert {"brazo_izq", "brazo_der", "pierna_izq", "pierna_der", "torso"} <= set(res.pieces)
    assert res.pieces["brazo_izq"].volume() == pytest.approx(res.pieces["brazo_der"].volume(), rel=1e-6)


def test_arm_ball_report_names_the_mechanism_and_the_lip(arm_1deg):
    res, p, _limbs = arm_1deg
    info = res.report["limbs"]["brazo"]
    assert info["mecanismo"] == "rotula" and "pin_margin_mm" not in info and info["sweep"] == [0.0, 1.0]
    rot = info["rotula"]
    assert rot["lip_margin_mm"] > 0.4 and rot["mouth_radius_mm"] == pytest.approx(mouth_radius(BallSpec(half_angle=1.0), p.clearance), abs=1e-3)
    assert rot["boca_margen_mm"] is not None


def test_arm_ball_carves_a_socket_in_the_torso_and_keeps_the_clearance(arm_1deg):
    res, p, limbs = arm_1deg
    base, _bp, _bl = _build()                                                # el mismo muñeco con hongo
    assert abs(res.pieces["torso"].volume() - base.pieces["torso"].volume()) > 1e-3       # otro zócalo que el del hongo
    v = P.validate(res, p, limbs)
    assert v["ok"], v["problems"]
    assert v["recorrido"]["brazo"] == 0.0 and v["recorrido_poses"]["brazo"] >= 2     # recorrió el borde del cono, no solo el reposo
    assert v["gaps"]["brazo_izq"] >= p.clearance - 0.1 * p.clearance - 1e-6


def test_a_cone_wider_than_the_body_allows_is_reported_as_a_problem(arm_4deg):
    res, p, limbs = arm_4deg
    v = P.validate(res, p, limbs)
    assert not v["ok"] and any(x.startswith("brazo:") and "cono de giro" in x for x in v["problems"])
    assert v["recorrido"]["brazo"] > 1e-4


def test_arm_ball_does_not_trip_the_hongo_shaft_check(arm_1deg):
    res, p, limbs = arm_1deg
    p2 = dataclasses.replace(p, shoulder_pin=dataclasses.replace(p.shoulder_pin, shaft_d=0.5))       # un vástago de hongo inservible
    v = P.validate(res, p2, limbs)
    assert not any("vástago Ø" in x and x.startswith("brazo") for x in v["problems"])


# --- cadera con rótula ------------------------------------------------------------------------------------------------------

def test_hip_ball_validates_the_facing_balls_instead_of_the_pin_walls(leg_ball):
    res, p, limbs = leg_ball
    assert isinstance(res.motion["pierna"], ConeMotion)
    v = P.validate(res, p, limbs)
    cad = v["cadera"]["pierna"]
    assert set(cad) == {"separacion_cabezas_mm"} and cad["separacion_cabezas_mm"] > 2 * p.clearance
    assert not any("pierna" in x and "pared" in x for x in v["problems"])
    assert res.report["limbs"]["pierna"]["sweep"] == [0.0, 1.0]


def test_hip_ball_prints_standing_not_seated(leg_ball):
    res, p, _limbs = leg_ball
    assert res.report["limbs"]["pierna"]["mecanismo"] == "rotula"
    posed = P.posed_pieces(res, 0.0)
    assert "pierna_izq" in posed                                              # la rótula sale en su pose impresa (de pie)
    printed, stand = res.pieces["pierna_izq"], res.motion["pierna"].limb
    assert np.allclose(printed.bounding_box(), stand.bounding_box(), atol=1e-4)       # no se giró por `sit_angle`: sale como de pie


def test_flat_for_print_survives_a_ball_arm_and_leg(arm_1deg, leg_ball):
    for res, p, _limbs in (arm_1deg, leg_ball):
        pieces, info = P.flat_for_print(res, 1.0, p)
        assert "torso" in pieces and isinstance(info, dict)


# --- sin ball nada cambia ---------------------------------------------------------------------------------------------------

def test_without_a_ball_the_build_is_what_it_was():
    res, p, limbs = _build()
    assert all(isinstance(m, JT.RevoluteMotion) for m in res.motion.values())
    assert "rotula" not in res.report["limbs"]["brazo"] and "mecanismo" not in res.report["limbs"]["brazo"]
    assert "recorrido_poses" not in P.validate(res, p, limbs)
