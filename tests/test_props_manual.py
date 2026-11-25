"""Agarre manual (plano de 3 puntos + centro del círculo + centro opcional al final de la mano): motor puro, sin GUI."""
import numpy as np
import pytest
import trimesh

from afm import props as P
from afm import solids as S

PLANE = ((4.4, 0.0, 3.4), (0.0, 4.4, 3.4), (-4.4, 0.0, 3.4))      # cara de arriba del toro (z = 3,4)


@pytest.fixture(scope="module")
def torus():
    """Toro en el plano XY (eje del agujero = Z) con un agujero de Ø2,0: mucho más grande que una mano, pero sirve."""
    return S.to_manifold(trimesh.creation.torus(major_radius=4.4, minor_radius=3.4, major_sections=48, minor_sections=24))


def test_plane_from_points_and_collinear():
    n, o = P.plane_from_points(PLANE)
    assert abs(abs(n[2]) - 1.0) < 1e-9 and np.allclose(o, [0.0, 4.4 / 3, 3.4], atol=1e-9)
    with pytest.raises(P.GripError, match="alineados"):
        P.plane_from_points(((0, 0, 0), (1, 0, 0), (2, 0, 0)))


def test_axis_points_into_the_hand_and_center_is_projected(torus):
    ch = P.manual_channel(torus, P.ManualGrip(PLANE, (0.3, -0.2, 5.0)))      # centro elegido fuera del plano: se proyecta
    assert np.allclose(ch.axis, [0, 0, -1], atol=1e-9)                       # hacia el interior (el centroide está en z = 0)
    assert np.allclose(ch.center, [0.3, -0.2, 3.4], atol=1e-9)
    flipped = P.manual_channel(torus, P.ManualGrip(PLANE[::-1], (0, 0, 3.4)))   # el orden de los puntos no cambia el sentido
    assert np.allclose(flipped.axis, [0, 0, -1], atol=1e-9)


def test_incomplete_choice_is_refused(torus):
    with pytest.raises(P.GripError, match="falta elegir"):
        P.manual_channel(torus, P.ManualGrip(PLANE))


def test_end_center_tilts_the_axis_and_is_limited(torus):
    ch = P.manual_channel(torus, P.ManualGrip(PLANE, (0, 0, 3.4), end_center=(1.0, 0.0, -3.4)))
    # el final de la mano está a 6,8 mm del plano: un desvío de 1 mm da atan(1/6,8) ≈ 8,4°
    assert np.degrees(np.arccos(ch.axis @ [0, 0, -1])) == pytest.approx(np.degrees(np.arctan(1.0 / 6.8)), abs=1e-6)
    with pytest.raises(P.GripError, match="inclina el eje"):
        P.manual_channel(torus, P.ManualGrip(PLANE, (0, 0, 3.4), end_center=(5.0, 0.0, -3.4)))


def test_manual_dict_roundtrip():
    m = P.ManualGrip(PLANE, (0.0, 0.0, 3.4), (1.0, 0.0, -3.4))
    assert P.ManualGrip.from_dict(m.to_dict()) == m
    assert P.ManualGrip(PLANE).to_dict()["centro"] is None and not P.ManualGrip(PLANE).complete


def test_carve_manual_subtracts_the_cylinder(torus):
    spec = P.GripSpec(bore_d=2.4)
    carved, fit = P.carve_grip(torus, spec, P.manual_channel(torus, P.ManualGrip(PLANE, (0, 0, 3.4))), require_cut=True)
    assert len(carved.decompose()) == 1 and carved.volume() < torus.volume()
    assert fit.bore_d == 2.4 and 4.5 < fit.length < 6.8 and fit.wall_min > 0.8   # el toro es redondo: el largo se mide a 1 mm del taladro
    assert np.allclose(fit.axis, [0, 0, -1], atol=1e-9)


def test_carve_manual_rejects_a_center_off_the_hand(torus):
    ch = P.manual_channel(torus, P.ManualGrip(PLANE, (30.0, 0.0, 3.4)))
    with pytest.raises(P.GripError, match="no atraviesa la mano"):
        P.carve_grip(torus, P.GripSpec(), ch, require_cut=True)


def test_compute_grip_manual_records_the_choice(torus, tmp_path):
    path = str(tmp_path / "toro.stl")
    S.to_trimesh(torus).export(path)
    manual = P.ManualGrip(PLANE, (0, 0, 3.4))
    res = P.compute_grip(path, P.GripSpec(), manual=manual)
    assert res.meta["agarre"]["eleccion"]["manual"] == manual.to_dict()
    assert res.channel.free_d == 0.0 and np.allclose(res.fit.axis, [0, 0, -1], atol=1e-9)


def test_ray_plane_point_hits_the_plane_and_refuses_edge_on_rays():
    n, o = np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, 2.0])
    hit = P.ray_plane_point([1.0, 2.0, 10.0], [0.0, 0.0, -1.0], n, o)
    assert np.allclose(hit, [1.0, 2.0, 2.0])
    assert P.ray_plane_point([0.0, 0.0, 10.0], [1.0, 0.0, 0.0], n, o) is None            # paralelo al plano
    assert P.ray_plane_point([0.0, 0.0, 0.0], [0.0, 0.0, -1.0], n, o) is None            # el plano queda detrás
