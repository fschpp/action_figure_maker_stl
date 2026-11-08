"""Ajuste de cintura y muñeca contra la pelvis y la mano del patrón (`afm.ajuste`)."""
import dataclasses

import numpy as np
import pytest

from afm import ajuste as A, solids as S
from afm.hand import HandSpec
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll
from afm.waist import WaistSpec, waist_plane

WAIST_Z = 48.0
WRIST_PTS = ((21.0, 3.0, 68.0), (18.0, -3.0, 68.0), (24.0, -3.0, 68.0))     # plano z = 68, punto 1 hacia +Y


def _project(waist_z=WAIST_Z):
    pr = demo_project()
    pr.waist = WaistSpec(Plane.from_points((-10, -6, waist_z), (10, -6, waist_z), (0, 8, waist_z), "cintura"))
    pr.hand = HandSpec(path="", points=WRIST_PTS)
    return pr


@pytest.fixture(scope="module")
def doll():
    return S.to_manifold(make_doll())


@pytest.fixture(scope="module")
def cone_arm_doll():
    """Muñeco con el brazo cónico (Ø8 abajo, Ø5 arriba): el radio de la muñeca depende de dónde se corta."""
    def ell(rx, ry, h, z0, cx=0.0):
        return S.Manifold.cylinder(h, 1, 1, 64).scale((rx, ry, 1)).translate((cx, 0, z0))
    parts = [ell(23, 12, 8, 34), ell(15, 10, 33, 42), S.Manifold.sphere(11, 48).translate((0, 0, 89)), ell(4, 4, 10, 72)]
    for sx in (+1, -1):
        parts.append(ell(7, 7, 38, 0.0, cx=sx * 9))
        parts.append(S.Manifold.cylinder(30, 4.0, 2.5, 48).translate((sx * 18, 0, 45)))
    return S.union(*parts)


def test_waist_radius_steps_at_the_pelvis(doll):
    pr = _project()
    tor = A._torso_below_neck(doll, pr)
    pl = waist_plane(pr.waist)
    assert A._waist_radius(tor, pl, 0.0) == pytest.approx(12.25, abs=0.2)       # torso Ø30 × 20
    assert A._waist_radius(tor, pl, -7.0) < 10.0                                 # por debajo de z = 42: pelvis sin pierna


def test_fit_waist_moves_the_plane_to_match_the_pattern(doll):
    pr = _project()
    tor = A._torso_below_neck(doll, pr)
    r_pelvis = A._waist_radius(tor, waist_plane(pr.waist), -7.0)
    res = A.fit_waist(pr, doll, r_pelvis, pr.params.ref_height)
    assert res.error is None and res.ok
    assert res.cambio and res.desplazamiento_mm < -5.0
    assert res.r_despues_mm == pytest.approx(r_pelvis, abs=0.05)
    assert res.escala == pytest.approx(1.0, abs=0.01)
    z0 = float(waist_plane(pr.waist).origin[2])
    assert A.aplicar(pr, [res]) == 1
    assert float(waist_plane(pr.waist).origin[2]) == pytest.approx(z0 + res.desplazamiento_mm * waist_plane(pr.waist).normal[2], abs=1e-6)
    again = A.fit_waist(pr, doll, r_pelvis, pr.params.ref_height)             # ya calza: no se mueve más
    assert not again.cambio and again.ok


def test_fit_waist_leaves_the_plane_when_it_already_fits(doll):
    pr = _project()
    tor = A._torso_below_neck(doll, pr)
    res = A.fit_waist(pr, doll, A._waist_radius(tor, waist_plane(pr.waist), 0.0), pr.params.ref_height)
    assert not res.cambio and res.ok and res.escala == pytest.approx(1.0, abs=0.01)


def test_fit_waist_reports_when_the_scale_is_out_of_reach(doll):
    pr = _project()
    res = A.fit_waist(pr, doll, 20.0, pr.params.ref_height)
    assert not res.ok and res.escala > 1.1
    assert any("adaptadora" in a for a in res.avisos)


def test_fit_waist_reference_at_another_height_is_rescaled(doll):
    """El radio del héroe se lleva a la altura del patrón (130 mm) antes de compararlo."""
    pr = _project()
    tor = A._torso_below_neck(doll, pr)
    r0 = A._waist_radius(tor, waist_plane(pr.waist), 0.0)
    k = 130.0 / pr.params.ref_height
    res = A.fit_waist(pr, doll, r0 * k, 130.0)
    assert not res.cambio and res.ok and res.r_antes_mm == pytest.approx(r0 * k, abs=1e-6)


def test_fit_waist_without_plane_is_an_error(doll):
    pr = _project()
    pr.waist = None
    assert A.fit_waist(pr, doll, 10.0).error


def test_fit_wrist_slides_along_the_arm(cone_arm_doll):
    pr = _project()
    half = cone_arm_doll
    r_now = A.fit_wrist(pr, half, 99.0).r_antes_mm
    target = r_now + 0.15                                    # el brazo es más grueso hacia abajo: hay que bajar el corte (hasta z = 65)
    res = A.fit_wrist(pr, half, target, pr.params.ref_height)
    assert res.error is None and res.ok and res.cambio
    assert res.r_despues_mm == pytest.approx(target, abs=0.06)
    pts0 = np.asarray(pr.hand.points, float)
    assert A.aplicar(pr, [res]) == 1
    moved = np.asarray(pr.hand.points, float)
    assert np.allclose(moved[:, :2], pts0[:, :2])           # solo se mueve a lo largo del eje del brazo (Z)
    assert moved[0, 2] < pts0[0, 2]
    assert not A.fit_wrist(pr, half, target, pr.params.ref_height).cambio


def test_fit_wrist_without_points_is_an_error(doll):
    pr = _project()
    pr.hand = None
    assert A.fit_wrist(pr, doll, 3.0).error
    pr.hand = HandSpec(path="")
    assert A.fit_wrist(pr, doll, 3.0).error


def test_format_ajuste_texts(doll):
    pr = _project()
    txt = "\n".join(A.format_ajuste(A.fit_waist(pr, doll, 20.0, pr.params.ref_height)))
    assert "cintura" in txt and "no alcanza" in txt
    assert "no se pudo ajustar" in A.format_ajuste(A.AjusteResult("muñeca", error="x"))[0]


def test_reference_radius_needs_a_pelvis_in_the_catalog():
    import os
    from afm.catalog import Catalog
    cat = Catalog.open(os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo"))
    with pytest.raises(ValueError, match="pelvis"):
        A.reference_radius(cat, "pelvis", "cintura")
    r, h, ref = A.reference_radius(cat, "mano", "muneca")
    assert r > 0 and ref.startswith("mano-")
    res = A.ajustar(_project(), S.to_manifold(make_doll()), cat)
    assert res[0].lugar == "cintura" and res[0].error and "pelvis" in res[0].error     # el ejemplo no trae pelvis
    assert res[1].lugar == "muñeca" and res[1].error is None
