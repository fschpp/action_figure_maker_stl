"""E03: RiderProfile (plan de caballos compatibles). Usa el catálogo de ejemplo; la prueba con Güemes/Belgrano
(`proceres-127`) corre solo si se apunta `AFM_PROCERES_127` a ese catálogo."""
import dataclasses
import os

import numpy as np
import pytest

from afm import recipe as R
from afm.equestrian import RiderError, rider_profile

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture(scope="module")
def doll():
    return R.assemble(R.Recipe.load(os.path.join(ROOT, "recetas", "muneco.receta.json")), ROOT, sit=True)


def test_profile_of_demo_doll(doll):
    rp = rider_profile(doll)
    assert rp.pelvis_name == "torso" and rp.warnings and set(rp.legs) == {"pierna_izq", "pierna_der"}
    assert rp.facing == pytest.approx([0, 1, 0], abs=0.05)                  # el motor entrega la figura mirando a +Y (plan 1.3-3)
    assert rp.hip_left.center[0] > 0 > rp.hip_right.center[0]
    assert rp.hip_left.center == pytest.approx(rp.hip_right.center * [-1, 1, 1])
    assert rp.hip_separation == pytest.approx(2 * rp.hip_left.center[0])
    assert np.allclose(rp.hip_left.axis, [1, 0, 0]) and np.allclose(rp.hip_right.axis, [-1, 0, 0])
    assert rp.sit_angle == doll.params.sit_angle and rp.knee_print_angle == doll.params.knee_print_angle
    assert set(rp.other_pieces) == {"brazo_izq", "brazo_der", "cabeza"}
    assert all(a <= b for a, b in zip(rp.lower_min, rp.lower_max))
    assert rp.lower_max[1] > 30                                              # las piernas salen hacia +Y en pose sentada


def test_profile_does_not_modify_the_figure(doll):
    before = {k: (float(v.volume()), tuple(np.round(np.ravel(v.bounding_box()), 6))) for k, v in doll.res.pieces.items()}
    rider_profile(doll)
    after = {k: (float(v.volume()), tuple(np.round(np.ravel(v.bounding_box()), 6))) for k, v in doll.res.pieces.items()}
    assert before == after


def test_no_legs_is_an_error(doll):
    res = dataclasses.replace(doll.res, pieces={k: v for k, v in doll.res.pieces.items() if not k.startswith("pierna")})
    with pytest.raises(RiderError, match="piernas"):
        rider_profile(dataclasses.replace(doll, res=res))


def test_no_hip_motion_is_an_error(doll):
    res = dataclasses.replace(doll.res, motion={})
    with pytest.raises(RiderError, match="cadera"):
        rider_profile(dataclasses.replace(doll, res=res))


def test_no_pelvis_nor_torso_is_an_error(doll):
    res = dataclasses.replace(doll.res, pieces={k: v for k, v in doll.res.pieces.items() if k != "torso"})
    with pytest.raises(RiderError, match="pelvis"):
        rider_profile(dataclasses.replace(doll, res=res))


def test_facing_is_read_from_the_geometry_not_assumed(doll):
    pieces = dict(doll.res.pieces)
    for k in ("pierna_izq", "pierna_der"):                                   # piernas desplazadas hacia −Y: el frente tiene que salir −Y
        pieces[k] = pieces[k].translate((0, -200, 0))
    rp = rider_profile(dataclasses.replace(doll, res=dataclasses.replace(doll.res, pieces=pieces)))
    assert rp.facing[1] < -0.9


def test_legs_on_top_of_pelvis_have_no_clear_facing(doll):
    pel = doll.res.pieces["torso"]
    pieces = dict(doll.res.pieces)
    pieces["pierna_izq"], pieces["pierna_der"] = pel, pel
    with pytest.raises(RiderError, match="hacia dónde mira"):
        rider_profile(dataclasses.replace(doll, res=dataclasses.replace(doll.res, pieces=pieces)))


def test_module_imports_no_manifold_directly():
    import re
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "afm" / "equestrian.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import manifold3d|from manifold3d)", src, re.M)


PROC = os.environ.get("AFM_PROCERES_127")


@pytest.mark.skipif(not PROC, reason="sin AFM_PROCERES_127 (catálogo proceres-127)")
def test_real_belgrano_profile():
    a = R.assemble(R.Recipe.load(os.path.join(PROC, "figuras", "belgrano.receta.json")), PROC, sit=True)
    rp = rider_profile(a)
    assert rp.pelvis_name == "pelvis" and not rp.warnings
    assert set(rp.legs) == {"muslo_izq", "muslo_der", "pantorrilla_izq", "pantorrilla_der"}
    assert rp.facing == pytest.approx([0, 1, 0], abs=0.1)
    assert rp.lower_min[2] == pytest.approx(22.2, abs=0.2)                   # plan §1.4: pantorrilla z 22,2 … 64,2
    assert rp.legs["muslo_izq"].volume() == pytest.approx(3019, rel=0.01)
