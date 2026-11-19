"""T11 · Rasterizado: de texel a punto 3D."""
import time

import numpy as np
import pytest
import trimesh

from afm.colorbake import TexelGrid, texel_points
from afm.unwrap import unwrap_triangles


@pytest.fixture(scope="module")
def sphere():
    return trimesh.creation.icosphere(subdivisions=3, radius=20.0)


@pytest.fixture(scope="module")
def unw(sphere):
    return unwrap_triangles(sphere, texel_mm=0.2)


@pytest.fixture(scope="module")
def grid(unw):
    return texel_points(unw)


def test_every_face_has_at_least_one_texel(unw, grid):
    assert isinstance(grid, TexelGrid)
    assert set(np.unique(grid.face)) == set(range(len(unw.faces)))


def test_tiny_triangles_still_get_their_barycenter_pixel():
    """Triángulos mucho más chicos que un texel (0,02 mm con texel de 0,2): ninguno queda sin píxel."""
    m = trimesh.creation.icosphere(subdivisions=2, radius=0.2)           # aristas ≈ 0,05 mm
    u = unwrap_triangles(m, texel_mm=0.2, margin_px=1)
    g = texel_points(u)
    assert set(np.unique(g.face)) == set(range(len(u.faces)))


def test_points_lie_on_the_mesh(sphere, unw, grid):
    T = unw.vertices[unw.faces]
    n = grid.normals
    d_plane = np.abs(np.einsum("nc,nc->n", grid.points - T[grid.face][:, 0], n))
    assert d_plane.max() <= 1e-6
    # y sobre la malla original (el despliegue no cambia la geometría): distancia a la superficie ≤ 1e-6
    pytest.importorskip("rtree")
    _, dist, _ = trimesh.proximity.closest_point(sphere, grid.points[::7])
    assert dist.max() <= 1e-6


def test_bary_sum_is_one_and_nonneg(grid):
    assert np.allclose(grid.bary.sum(axis=1), 1.0, atol=1e-5)
    assert grid.bary.min() >= 0.0
    assert grid.bary.dtype == np.float32 and grid.points.dtype == np.float64


def test_normals_are_unit_and_match_the_face(unw, grid):
    assert np.allclose(np.linalg.norm(grid.normals, axis=1), 1.0, atol=1e-9)
    T = unw.vertices[unw.faces][grid.face]
    cr = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    assert (np.einsum("nc,nc->n", cr, grid.normals) > 0).all()


def test_texel_count_close_to_area_over_texel2(sphere, unw, grid):
    expected = sphere.area / unw.texel_mm ** 2
    assert abs(len(grid.face) - expected) / expected <= 0.25, (len(grid.face), expected)


def test_no_python_loop_per_pixel_speed():
    mesh = trimesh.creation.icosphere(subdivisions=5, radius=47.0)        # ≈ 20 480 caras (torso de Güemes: ≈ 22 000)
    u = unwrap_triangles(mesh, texel_mm=0.2)
    t0 = time.time()
    g = texel_points(u)
    dt = time.time() - t0
    assert dt < 5.0, dt
    print(f"{len(u.faces)} caras, {len(g.face)} texeles, {dt:.2f} s")


def test_rows_cols_inside_atlas(unw, grid):
    W, H = grid.size
    assert (W, H) == unw.size
    assert grid.rows.min() >= 0 and grid.rows.max() < H
    assert grid.cols.min() >= 0 and grid.cols.max() < W


def test_pixel_center_is_inside_its_triangle(unw, grid):
    """El texel (fila r, col c) tiene su centro (c+0,5, r+0,5) dentro del triángulo en el atlas (salvo los agregados por baricentro)."""
    tri = unw.tri_px.astype(np.float64)[grid.face]
    est = np.einsum("nk,nkc->nc", grid.bary.astype(np.float64), tri)
    center = np.column_stack([grid.cols + 0.5, grid.rows + 0.5])
    d = np.linalg.norm(est - center, axis=1)
    assert np.median(d) < 1e-3 and np.percentile(d, 99.9) < 1.5     # los agregados (triángulos chicos) quedan a ≤ 1 px
