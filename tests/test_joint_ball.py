"""Plugins de articulación, Fase 4 (corte 3): el tipo `ball` (rótula). Todavía no está en `REGISTRY` ni en `pipeline.build` (corte 4)."""
import dataclasses

import numpy as np
import pytest

from afm import solids as S
from afm.joint_types import ConeMotion, FaceRequirements
from afm.joint_types.ball import Ball, BallSpec, lip, lip_margin, mouth_radius
from afm.joint_types.base import moved
from afm.joint_types.offer import offer_one
from afm.joint_types.testing import check_type, failures
from afm.params import Params


@pytest.fixture(scope="module")
def built():
    b = Ball()
    site, parent = b.test_site()
    return b, site, parent, b.build(site)


def test_the_conformance_kit_passes():
    assert failures(check_type(Ball())) == []


def test_build_gives_a_child_with_ball_and_neck_a_socket_and_a_cone_motion(built):
    _b, site, parent, r = built
    assert isinstance(r.motion, ConeMotion) and r.motion.revolute() is None
    assert r.motion.half_angle == site.jp.half_angle
    child = r.pieces["child"]
    assert child.volume() > site.child.volume() + 100                       # la bola (Ø7 ≈ 180 mm³) y el cuello
    # el centro de la bola está a neck_len + ball_d/2 de la cara, hacia adentro del anfitrión
    expect = site.face.center + np.array([-1.0, 0, 0]) * (site.jp.neck_len + site.jp.ball_d / 2)
    assert np.allclose(r.motion.center, expect)
    assert (S.intersection(r.cutters["parent"][0], parent)).volume() > 0     # el zócalo le saca material al padre
    assert r.cutters["host"] == [] and r.parent_adds == [] and r.extras == {}


def test_the_ball_is_held_by_the_lip_in_the_socket():
    """Con el cono de 20° por defecto: la bola no puede salir hacia la cara (desplazarla 1 mm choca con el labio) y el margen es positivo."""
    b = Ball()
    site, parent = b.test_site()
    site = dataclasses.replace(site, jp=BallSpec(), child=S.cylinder_along((0.0, 0.0, -8.0), (1.0, 0.0, 0.0), 1.4, 30.0, 48))   # un brazo fino: solo se mide la retención
    r = b.build(site)
    assert r.report["lip_margin_mm"] == pytest.approx(0.526, abs=1e-3)
    target = S.difference(parent, *r.cutters["parent"])
    limb = r.pieces["child"]
    assert S.intersection(limb, target).volume() < 1e-4                      # armada, en reposo, no toca
    out = limb.translate((1.0, 0.0, 0.0))                                    # 1 mm hacia afuera: la bola topa con el labio
    assert S.intersection(out, target).volume() > 1e-3


def test_lip_geometry_follows_the_cone_angle():
    c = 0.5
    wide, narrow = BallSpec(half_angle=40.0), BallSpec(half_angle=10.0)
    assert lip_margin(narrow, c) > lip_margin(BallSpec(), c) > lip_margin(wide, c)      # más cono, menos labio
    assert lip_margin(BallSpec(half_angle=60.0), c) < 0 < lip_margin(BallSpec(), c)
    z, r = lip(BallSpec(), c)
    assert 0 < z < BallSpec().ball_d / 2 + c and r < BallSpec().ball_d / 2
    assert mouth_radius(wide, c) > mouth_radius(narrow, c)


def test_build_rejects_a_ball_that_would_not_be_held_or_bad_dimensions():
    b = Ball()
    site, _p = b.test_site()
    for jp, match in ((BallSpec(half_angle=60.0), "no retiene"), (BallSpec(half_angle=0.0), "fuera de"),
                      (BallSpec(half_angle=75.0), "fuera de"), (BallSpec(neck_d=8.0), "más fino")):
        with pytest.raises(ValueError, match=match):
            b.build(dataclasses.replace(site, jp=jp))


def test_a_thin_lip_and_a_thin_neck_are_warnings():
    b = Ball()
    site, _p = b.test_site()
    r = b.build(dataclasses.replace(site, jp=BallSpec(half_angle=33.0)))       # el labio queda por debajo de 0,4 mm pero positivo
    assert 0 < r.report["lip_margin_mm"] < 0.4 and any("labio" in w for w in r.warnings)
    p = dataclasses.replace(Params(), min_pin_d=4.0)
    r2 = b.build(dataclasses.replace(site, params=p))
    assert any("cuello" in w for w in r2.warnings)


def test_requires_and_the_cheap_offer():
    b, p = Ball(), Params()
    req = b.requires(BallSpec(), p, "hombro")
    assert isinstance(req, FaceRequirements) and req.separacion_min_mm is None
    assert req.profundidad_mm == pytest.approx(1.5 + 7.0 + p.clearance)
    assert b.requires(BallSpec(), p, "cadera").separacion_min_mm == pytest.approx(2 * p.clearance)   # se enfrenta a su espejo
    assert offer_one(b, p, "hombro", None, 30.0).nivel == "ok"
    o = offer_one(b, p, "hombro", None, 5.0)                                   # un torso de 5 mm de fondo no aloja la bola
    assert o.nivel == "no" and "detrás de la cara" in o.motivos[0]


def test_tilt_inside_the_cone_is_free_and_the_ball_does_not_leave_the_socket():
    b = Ball()
    site, parent = b.test_site()
    r = b.build(site)
    target = S.difference(parent, *r.cutters["parent"])
    m = r.motion
    worst = max(float(S.intersection(moved(m.limb, T), target).volume()) for T in m.poses(2.0))
    assert worst < 1e-4
