"""T10 · Despliegue base «una celda por triángulo»."""
import time

import numpy as np
import pytest
import trimesh

from afm.unwrap import Unwrapped, check_no_overlap, unwrap_triangles


def _sphere(faces_sub=3, radius=20.0):
    return trimesh.creation.icosphere(subdivisions=faces_sub, radius=radius)


def _uneven():
    """Triángulos muy desparejos: una caja larga y fina más una esfera chica, unidas en una sola malla."""
    a = trimesh.creation.box((60.0, 0.5, 3.0))
    b = trimesh.creation.icosphere(subdivisions=2, radius=2.0)
    b.apply_translation((0, 8, 0))
    return trimesh.util.concatenate([a, b])


def _px_area(unw):
    t = unw.tri_px.astype(np.float64)
    return 0.5 * np.abs((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 2, 0] - t[:, 0, 0]) * (t[:, 1, 1] - t[:, 0, 1]))


def _area3d(unw):
    T = unw.vertices[unw.faces]
    return 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)


def test_cells_never_overlap():
    for mesh in (_sphere(3), _uneven()):                       # esfera de 1280 caras y una malla despareja a trozos
        unw = unwrap_triangles(mesh, texel_mm=0.2)
        assert isinstance(unw, Unwrapped)
        assert check_no_overlap(unw) == 0
    big = unwrap_triangles(trimesh.creation.icosphere(subdivisions=5, radius=45.0), texel_mm=0.2)   # ≈ 20 480 caras (≈ 3000 y más)
    assert len(big.faces) >= 3000 and check_no_overlap(big) == 0


def test_check_no_overlap_detects_a_clash():
    unw = unwrap_triangles(_sphere(2), texel_mm=0.3)
    bad = Unwrapped(unw.vertices, unw.faces, unw.src_face, unw.tri_px, unw.uv, unw.size, unw.texel_mm, unw.cells.copy())
    bad.cells[1] = bad.cells[0]                                # dos celdas en el mismo lugar
    assert check_no_overlap(bad) > 0


def test_uv_in_unit_square_and_inside_atlas():
    unw = unwrap_triangles(_uneven(), texel_mm=0.2)
    W, H = unw.size
    assert W & (W - 1) == 0 and H % 4 == 0                      # ancho potencia de 2, alto múltiplo de 4
    assert unw.uv.min() >= 0.0 and unw.uv.max() <= 1.0
    assert unw.tri_px[:, :, 0].min() >= 0 and unw.tri_px[:, :, 0].max() <= W
    assert unw.tri_px[:, :, 1].min() >= 0 and unw.tri_px[:, :, 1].max() <= H
    assert np.allclose(unw.uv[:, :, 0], unw.tri_px[:, :, 0] / W, atol=1e-5)
    assert np.allclose(unw.uv[:, :, 1], 1.0 - unw.tri_px[:, :, 1] / H, atol=1e-5)


def test_isometric_area_preserved():
    mesh = _uneven()
    unw = unwrap_triangles(mesh, texel_mm=0.2, max_cell_px=10_000)       # sin reducción de celdas: s = 1
    area_px = _px_area(unw).sum() * unw.texel_mm ** 2
    assert area_px == pytest.approx(_area3d(unw).sum(), rel=0.01)
    # además, cada triángulo conserva las longitudes de sus aristas (aplanado isométrico)
    T = unw.vertices[unw.faces]
    e3 = np.linalg.norm(T[:, 1] - T[:, 0], axis=1)
    e2 = np.linalg.norm(unw.tri_px[:, 1] - unw.tri_px[:, 0], axis=1) * unw.texel_mm
    assert np.allclose(e2, e3, rtol=1e-3, atol=1e-3)


def test_big_triangle_is_subdivided_and_mapped_back():
    mesh = trimesh.Trimesh([[0, 0, 0], [40, 0, 0], [40, 40, 0], [0, 40, 0]], [[0, 1, 2], [0, 2, 3]], process=False)   # cuadrado de 40 mm
    unw = unwrap_triangles(mesh, texel_mm=0.2)
    assert len(unw.faces) > len(mesh.faces)
    assert unw.src_face.min() >= 0 and unw.src_face.max() < len(mesh.faces)
    assert set(np.unique(unw.src_face)) == {0, 1}
    assert _area3d(unw).sum() == pytest.approx(1600.0, rel=1e-6)
    for f in range(2):                                          # el área de cada cara original se conserva
        assert _area3d(unw)[unw.src_face == f].sum() == pytest.approx(800.0, rel=1e-6)
    # ninguna celda pasa de max_cell_px: sin triángulos gigantes
    assert unw.cells[:, 2].max() <= 64 + 4 and unw.cells[:, 3].max() <= 64 + 4


def test_oversized_piece_grows_texel():
    mesh = _sphere(3, radius=30.0)
    unw = unwrap_triangles(mesh, texel_mm=0.1, max_atlas=256)
    assert unw.texel_mm > 0.1
    assert max(unw.size) <= 256
    assert check_no_overlap(unw) == 0


def test_impossible_piece_raises():
    with pytest.raises(ValueError):
        unwrap_triangles(_sphere(3, radius=30.0), texel_mm=0.1, max_atlas=8)


def test_deterministic():
    mesh = _uneven()
    a, b = unwrap_triangles(mesh), unwrap_triangles(mesh)
    assert a.size == b.size and a.texel_mm == b.texel_mm
    assert np.array_equal(a.tri_px, b.tri_px) and np.array_equal(a.faces, b.faces) and np.array_equal(a.src_face, b.src_face)


def test_geometry_is_unchanged_by_subdivision():
    mesh = _uneven()
    unw = unwrap_triangles(mesh, texel_mm=0.05)                 # texel fino ⇒ más subdivisión
    assert _area3d(unw).sum() == pytest.approx(mesh.area, rel=1e-6)
    assert np.allclose(unw.vertices.min(axis=0), mesh.vertices.min(axis=0)) and np.allclose(unw.vertices.max(axis=0), mesh.vertices.max(axis=0))


def test_torso_like_mesh_under_10_seconds():
    mesh = trimesh.creation.icosphere(subdivisions=5, radius=45.0)      # 20 480 caras de ≈ 1,7 mm (como el torso de Güemes: ≈ 22 000)
    t0 = time.time()
    unw = unwrap_triangles(mesh, texel_mm=0.2)
    dt = time.time() - t0
    assert dt < 10.0, dt
    assert max(unw.size) <= 4096
    assert check_no_overlap(unw) == 0
    util = _px_area(unw).sum() / (unw.size[0] * unw.size[1])
    print(f"torso-like: {len(unw.faces)} caras, atlas {unw.size}, texel {unw.texel_mm:.3f} mm, {dt:.2f} s, útil {util:.0%}")
