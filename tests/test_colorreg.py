"""T08 · Registro pieza → fuente (semillas simples)."""
import dataclasses
import time
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from afm import pipeline as P, solids as S
from afm.colorreg import IDENTITY_SEED, MIRROR_X, Registration, register_piece, rotation_matrix, seed_forward, seeds_for_piece
from afm.colorsrc import ColorSource
from afm.params import PinSpec
from afm.project import demo_project
from afm.testfigure import make_doll
from color_helpers import doll_textured
from test_detent import DOLL_DETENT


@pytest.fixture(scope="module")
def built():
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0)
    res = P.build(S.to_manifold(make_doll()), proj.limbs, p, proj.symmetry)
    return res, p


@pytest.fixture(scope="module")
def src():
    return ColorSource(doll_textured(), spacing_mm=0.1)


@pytest.fixture(scope="module")
def meshes(built):
    res, _p = built
    return {n: S.to_printable_trimesh(m) for n, m in res.pieces.items()}


@pytest.fixture(scope="module")
def doll_arm_detent():
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0, shoulder_detent=True, detent=DOLL_DETENT, shoulder_pin=PinSpec(3.0, 2.0, 5.0, 1.2))
    res = P.build(S.to_manifold(make_doll()), [proj.limbs[0]], p, proj.symmetry)
    return res, p


def _rest_limb(res, key):
    """El miembro de pie (sin girar) como malla, y su (centro, eje) de `motion`."""
    return S.to_printable_trimesh(res.motion[key][0]), res.motion[key][1], res.motion[key][2]


def _rotated(mesh, center, axis, deg):
    m = mesh.copy()
    m.apply_transform(rotation_matrix(center, axis, deg))
    return m


def test_names_of_the_real_pieces(built):
    """[V] nombres que usa el resto del plan: una entrada de `motion` por miembro y piezas `<miembro>_izq/_der`."""
    res, _p = built
    assert sorted(res.pieces) == ["brazo_der", "brazo_izq", "pierna_der", "pierna_izq", "torso"]
    assert sorted(res.motion) == ["brazo", "pierna"]


def test_identity_for_unrotated_piece(built, meshes, src):
    res, p = built
    for name in ("torso", "brazo_izq", "brazo_der"):
        r = register_piece(name, meshes[name], src, res, p)
        assert isinstance(r, Registration)
        assert r.status == "identidad" and r.angle_deg == 0.0 and r.score >= 0.4, (name, r.status, r.score)
        assert np.allclose(r.matrix, np.eye(4))


def test_doll_arm_is_symmetric_under_180_about_its_axis(built, src):
    """Por qué las pruebas de giro usan la PIERNA: el brazo del muñeco es una caja simétrica respecto de su centro y rotado 180°
    sobre el pasador coincide consigo mismo (empate exacto); ninguna búsqueda puede distinguir 0° de 180°. La pierna (en L) no."""
    from afm.colorreg import _score, _surface_points

    res, _p = built
    arm, c, ax = _rest_limb(res, "brazo")
    leg, lc, lax = _rest_limb(res, "pierna")
    pa, pl = _surface_points(arm, 1500), _surface_points(leg, 1500)
    s0, s180 = (_score(rotation_matrix(c, ax, th), pa, src, 0.3) for th in (0.0, 180.0))
    assert s0 > 0.6 and abs(s0 - s180) < 0.05
    l0, l180 = (_score(rotation_matrix(lc, lax, th), pl, src, 0.3) for th in (0.0, 180.0))
    assert l0 > 0.6 and l180 < 0.4


def test_recovers_synthetic_rotation(built, src):
    """Pieza girada −90° (el respaldo `−sit_angle`, no la semilla exacta): se recupera el giro y la matriz la deja en reposo."""
    res, p = built
    leg, c, ax = _rest_limb(res, "pierna")
    posed = _rotated(leg, c, ax, -90.0)
    r = register_piece("pierna_izq", posed, src, res, p)
    assert r.status == "semilla" and abs(r.angle_deg - 90.0) <= 1.0 and r.score >= 0.4
    back = posed.copy()
    back.apply_transform(r.matrix)
    assert np.abs(back.vertices - leg.vertices).max() < 0.05


def test_exact_seed_for_leg_is_sit_angle(built, meshes, src):
    res, p = built
    seeds = seeds_for_piece("pierna_izq", res, p)
    c, ax = res.motion["pierna"][1:3]
    assert np.allclose(seeds[0][0], c) and np.allclose(seeds[0][1], ax) and seeds[0][2] == p.sit_angle
    r = register_piece("pierna_izq", meshes["pierna_izq"], src, res, p)
    assert r.status == "semilla" and r.angle_deg == pytest.approx(-p.sit_angle) and r.score >= 0.4
    assert r.seed == seeds[0]                                  # ganó la exacta, no una de respaldo


def test_right_side_mirrors_axis_and_negates_angle(built, meshes, src):
    res, p = built
    izq, der = seeds_for_piece("pierna_izq", res, p), seeds_for_piece("pierna_der", res, p)
    assert len(izq) == len(der)
    for (ci, ai, ti), (cd, ad, td) in zip(izq, der):
        assert np.allclose(cd, MIRROR_X @ np.asarray(ci)) and np.allclose(ad, MIRROR_X @ np.asarray(ai)) and td == pytest.approx(-ti)
    r = register_piece("pierna_der", meshes["pierna_der"], src, res, p)
    assert r.status == "semilla" and r.angle_deg == pytest.approx(p.sit_angle)          # signo contrario al de la izquierda
    assert np.allclose(r.axis, MIRROR_X @ np.asarray(res.motion["pierna"][2]))
    # M·R(a, θ)·M = R(M·a, −θ): comprobación numérica del fundamento
    c, a = np.array([3.0, 1.0, 2.0]), np.array([0.3, 0.5, 0.8])
    M4 = np.eye(4)
    M4[:3, :3] = MIRROR_X
    assert np.allclose(M4 @ rotation_matrix(c, a, 37.0) @ M4, rotation_matrix(MIRROR_X @ c, MIRROR_X @ a, -37.0))


def test_exact_seed_for_detent_arm(doll_arm_detent):
    res, p = doll_arm_detent
    assert "brazo" in res.detent
    th = res.detent["brazo"].print_angle
    assert th == DOLL_DETENT.print_angle == 10.0
    seeds = seeds_for_piece("brazo_izq", res, p)
    assert seeds[0][2] == th and any(abs(s[2]) < 1e-9 for s in seeds[1:])
    mesh = S.to_printable_trimesh(res.pieces["brazo_izq"])
    r = register_piece("brazo_izq", mesh, ColorSource(doll_textured(), spacing_mm=0.1), res, p)
    assert r.status == "semilla" and r.angle_deg == pytest.approx(-th) and r.score >= 0.4, (r.status, r.angle_deg, r.score)


def test_unknown_piece_returns_sin_origen(built, src):
    res, p = built
    sphere = trimesh.creation.icosphere(subdivisions=2, radius=8.0)
    sphere.apply_translation([500.0, 500.0, 500.0])
    for name in ("esfera", "brazo_izq"):            # sin entrada en `motion` / con eje pero lejos de la figura
        r = register_piece(name, sphere, src, res, p)
        assert r.status == "sin_origen" and np.allclose(r.matrix, np.eye(4)) and r.angle_deg == 0.0 and r.score < 0.4


def test_sweep_finds_angle_not_in_seeds(built, src):
    res, p = built
    leg, c, ax = _rest_limb(res, "pierna")
    r = register_piece("pierna_izq", _rotated(leg, c, ax, 37.0), src, res, p)
    assert r.status == "barrido" and abs(r.angle_deg - (-37.0)) <= 1.0 and r.score >= 0.4, (r.status, r.angle_deg, r.score)
    # lado derecho: pieza espejada del miembro de pie, girada 37° sobre el eje reflejado
    cd, ad = MIRROR_X @ np.asarray(c), MIRROR_X @ np.asarray(ax)
    mir = leg.copy()
    mir.vertices = np.asarray(leg.vertices) @ MIRROR_X.T
    r2 = register_piece("pierna_der", _rotated(mir, cd, ad, 37.0), src, res, p)
    assert r2.status == "barrido" and abs(r2.angle_deg - (-37.0)) <= 1.0 and r2.score >= 0.4, (r2.status, r2.angle_deg, r2.score)
    assert np.allclose(r2.axis, ad)


def test_seeds_without_motion_entry_is_identity_only(built):
    res, p = built
    for name in ("torso", "pelvis", "cosa_izq", "cosa_der"):
        assert seeds_for_piece(name, res, p) == [IDENTITY_SEED]
    assert np.allclose(seed_forward(IDENTITY_SEED), np.eye(4))


def test_seeds_lists_are_chains_applied_in_order():
    a = ([0, 0, 0], [0, 0, 1], 90.0)
    b = ([10, 0, 0], [0, 1, 0], 30.0)
    F = seed_forward([a, b])
    assert np.allclose(F, rotation_matrix(*b) @ rotation_matrix(*a))
    assert np.allclose(seed_forward(a), rotation_matrix(*a))                     # un paso suelto sigue valiendo


def test_der_seeds_use_right_motion_with_limbs_right(built):
    """Con `res.right` (cortes asimétricos) las piezas `_der` salen del marco espejado: sus semillas son las de `res.right.motion`."""
    res, p = built
    left = SimpleNamespace(motion={"pierna": (None, np.array([1.0, 2.0, 3.0]), np.array([1.0, 0, 0]), (0.0, 95.0))}, detent={}, right=None)
    right = SimpleNamespace(motion={"pierna": (None, np.array([4.0, 5.0, 6.0]), np.array([1.0, 0, 0]), (0.0, 95.0))}, detent={}, right=None)
    asym = SimpleNamespace(motion=left.motion, detent={}, right=right)
    der = seeds_for_piece("pierna_der", asym, p)[0]
    assert np.allclose(der[0], [-4.0, 5.0, 6.0]) and np.allclose(der[1], [-1.0, 0, 0]) and der[2] == -p.sit_angle
    izq = seeds_for_piece("pierna_izq", asym, p)[0]
    assert np.allclose(izq[0], [1.0, 2.0, 3.0]) and izq[2] == p.sit_angle


def test_registration_is_fast(built, meshes, src):
    res, p = built
    leg, c, ax = _rest_limb(res, "pierna")
    cases = [("torso", meshes["torso"]), ("pierna_izq", meshes["pierna_izq"]), ("pierna_izq", _rotated(leg, c, ax, 37.0))]
    for name, m in cases:                              # el último fuerza el barrido completo
        t = time.time()
        register_piece(name, m, src, res, p)
        assert time.time() - t < 3.0, name


def test_registration_is_deterministic(built, meshes, src):
    res, p = built
    a = register_piece("pierna_izq", meshes["pierna_izq"], src, res, p)
    b = register_piece("pierna_izq", meshes["pierna_izq"], src, res, p)
    assert a.score == b.score and np.array_equal(a.matrix, b.matrix)
