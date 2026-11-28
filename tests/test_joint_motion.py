"""Plugins de articulación, Fase 4 (corte 1): `Motion` general. `ConeMotion` (rótula) junto a `RevoluteMotion`, y los consumidores
de un solo eje que saltean lo que no es de un solo eje."""
import dataclasses

import numpy as np
import pytest
import trimesh

from afm import joint_types as JT
from afm import pipeline as P, solids as S
from afm.colorreg import IDENTITY_SEED, seeds_for_piece
from afm.equestrian import RiderError, rider_profile
from afm.joint_types import ConeMotion, Motion, RevoluteMotion, as_revolute, revolute_motions, rotation_matrix
from afm.project import demo_project
from afm.testfigure import make_doll


def _box():
    return S.to_manifold(trimesh.creation.box((4, 4, 10)))


def _cone(**kw):
    args = dict(limb=_box(), center=np.array([1.0, 2.0, 3.0]), axis=np.array([0.0, 0.0, -1.0]), half_angle=30.0)
    args.update(kw)
    return ConeMotion(**args)


def _tilt_of(T, axis):
    """Ángulo entre el eje de reposo y el mismo eje ya movido por la pose `T`."""
    ax = np.asarray(axis, float) / np.linalg.norm(axis)
    moved = T[:3, :3] @ ax
    return float(np.degrees(np.arccos(np.clip(ax @ moved, -1.0, 1.0))))


# --- ConeMotion ---------------------------------------------------------------------------------------------------------

def test_cone_is_a_motion_and_has_no_single_axis():
    m = _cone()
    assert isinstance(m, Motion) and isinstance(RevoluteMotion(_box(), (0, 0, 0), (1, 0, 0), (0, 90)), Motion)
    assert m.revolute() is None


def test_cone_poses_start_at_rest_stay_inside_the_cone_and_reach_its_edge():
    m = _cone()
    poses = list(m.poses(10.0))
    assert np.allclose(poses[0], np.eye(4))                                   # la primera es el reposo
    tilts = [_tilt_of(T, m.axis) for T in poses]
    assert max(tilts) == pytest.approx(30.0, abs=1e-6)                        # llega al borde del cono…
    assert all(t <= 30.0 + 1e-6 for t in tilts)                               # …y no lo pasa
    assert len(poses) > 20                                                    # una grilla, no un barrido de un eje


def test_cone_poses_are_rigid_and_keep_the_center_fixed():
    m = _cone(twist=(-20.0, 20.0))
    for T in m.poses(15.0):
        R = T[:3, :3]
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-9) and np.linalg.det(R) == pytest.approx(1.0)
        assert np.allclose(T @ np.append(m.center, 1.0), np.append(m.center, 1.0), atol=1e-9)


def test_cone_grid_gets_denser_with_a_finer_step_and_twist_multiplies_it():
    assert len(list(_cone().poses(5.0))) > len(list(_cone().poses(15.0)))
    base, twisted = len(list(_cone().poses(15.0))), len(list(_cone(twist=(0.0, 30.0)).poses(15.0)))
    assert twisted == 3 * base                                                # giros 0, 15 y 30


def test_cone_with_zero_angle_is_just_the_twist():
    m = _cone(half_angle=0.0, twist=(0.0, 90.0))
    poses = list(m.poses(30.0))
    assert len(poses) == 4
    assert np.allclose(poses[-1], rotation_matrix(m.center, m.axis, 90.0))


def test_cone_print_pose_matches_its_declared_tilt():
    m = _cone(print_tilt=(25.0, 90.0, 10.0))
    assert np.allclose(m.print_pose(), m.pose(25.0, 90.0, 10.0))
    assert _tilt_of(m.print_pose(), m.axis) == pytest.approx(25.0, abs=1e-6)
    assert np.allclose(_cone().print_pose(), np.eye(4))                       # sin pose declarada: de reposo
    assert m.contains(25.0) and not m.contains(31.0)


def test_cone_rejects_an_impossible_cone_or_twist():
    with pytest.raises(ValueError, match="half_angle"):
        _cone(half_angle=120.0)
    with pytest.raises(ValueError, match="menor a mayor"):
        _cone(twist=(10.0, -10.0))


def test_with_limb_swaps_only_the_mesh():
    big = S.to_manifold(trimesh.creation.box((9, 9, 9)))
    c = _cone().with_limb(big)
    assert c.limb is big and c.half_angle == 30.0
    r = RevoluteMotion(_box(), (0, 0, 0), (1, 0, 0), (0, 90)).with_limb(big)
    assert r.limb is big and r.range == (0, 90)


# --- RevoluteMotion sigue igual ------------------------------------------------------------------------------------------

def test_revolute_unpacks_like_the_old_tuple_and_has_a_print_pose():
    r = RevoluteMotion(_box(), np.array([0.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]), (0.0, 90.0))
    _l, c, ax, (a0, a1) = r
    assert (a0, a1) == (0.0, 90.0) and r.revolute() is r
    assert np.allclose(r.print_pose(90.0), rotation_matrix(c, ax, 90.0))
    assert np.allclose(r.print_pose(), np.eye(4))


# --- as_revolute / revolute_motions ---------------------------------------------------------------------------------------

def test_as_revolute_accepts_motions_old_tuples_and_none():
    r = RevoluteMotion(_box(), (0, 0, 0), (1, 0, 0), (0.0, 90.0))
    assert as_revolute(r) is r
    assert as_revolute(_cone()) is None
    assert as_revolute(None) is None
    old = (_box(), (0, 0, 0), (1, 0, 0), (0.0, 90.0))                         # la tupla de siempre
    assert as_revolute(old) == r.__class__(*old)
    assert as_revolute("no es un movimiento") is None


def test_revolute_motions_skips_cones_and_keeps_the_order():
    r1 = RevoluteMotion(_box(), (0, 0, 0), (1, 0, 0), (0.0, 90.0))
    r2 = RevoluteMotion(_box(), (0, 0, 0), (0, 1, 0), (0.0, 10.0))
    out = revolute_motions({"b": r1, "bola": _cone(), "a": r2})
    assert list(out) == ["b", "a"] and out["a"] is r2
    assert revolute_motions(None) == {}


# --- la figura de siempre no cambia, y una rótula no rompe a los consumidores --------------------------------------------------

@pytest.fixture(scope="module")
def doll():
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0)
    return P.build(S.to_manifold(make_doll()), proj.limbs, p, proj.symmetry), p


def test_a_built_figure_only_carries_revolute_motions(doll):
    res, _p = doll
    assert res.motion and all(isinstance(m, RevoluteMotion) for m in res.motion.values())
    assert revolute_motions(res.motion).keys() == res.motion.keys()


def _with_ball_hip(res):
    old = res.motion["pierna"]
    ball = ConeMotion(old.limb, old.center, old.axis, 40.0)
    return dataclasses.replace(res, motion={**res.motion, "pierna": ball})


def test_color_seeds_of_a_ball_limb_are_identity_only(doll):
    res, p = doll
    assert seeds_for_piece("pierna_izq", res, p) != [IDENTITY_SEED]            # con el eje de cadera hay semillas…
    assert seeds_for_piece("pierna_izq", _with_ball_hip(res), p) == [IDENTITY_SEED]   # …con una rótula, solo la identidad


def test_posed_pieces_and_pose_limit_skip_a_ball_limb(doll):
    res, p = doll
    posed = P.posed_pieces(_with_ball_hip(res), 90.0, None, p)
    assert not any(k.startswith("pierna") for k in posed)                      # la rótula no se gira como un eje: sin KeyError ni pieza falsa
    assert any(k.startswith("brazo") for k in posed)


def test_rider_profile_explains_that_a_ball_hip_has_no_single_axis(doll):
    res, _p = doll
    asm = type("A", (), {})()
    asm.res, asm.params = _with_ball_hip(res), doll[1]
    with pytest.raises(RiderError, match="cadera"):
        rider_profile(asm)
