"""S.compact: la misma pieza con muchos menos triángulos (suelda vértices duplicados, reconstruye y simplifica)."""
import numpy as np
import trimesh

from afm import solids as S


def _subdivided_box(size=0.5):
    box = trimesh.creation.box(extents=(20.0, 12.0, 8.0))
    v, f = trimesh.remesh.subdivide_to_size(box.vertices, box.faces, max_edge=size)
    return trimesh.Trimesh(v, f, process=False)


def test_compact_collapses_coplanar_triangles_keeping_shape():
    mf = S.to_manifold(_subdivided_box())
    n0 = mf.num_tri()
    assert n0 > 5000                                                    # una caja con las caras llenas de triángulos
    out = S.compact(mf, min_tris=1000)
    assert out.num_tri() < n0 / 20
    assert abs(out.volume() - mf.volume()) <= 2e-3 * mf.volume()
    assert np.allclose(out.bounding_box(), mf.bounding_box(), atol=1e-3)
    assert out.status() == S.m3.Error.NoError


def test_compact_leaves_small_pieces_alone():
    mf = S.Manifold.cube((5, 5, 5), center=True)
    assert S.compact(mf) is mf                                          # por debajo de min_tris
    dense = S.to_manifold(_subdivided_box())
    assert S.compact(dense, min_tris=10_000_000) is dense


def test_compact_does_not_collapse_real_curvature():
    sph = S.to_manifold(trimesh.creation.icosphere(subdivisions=5, radius=10.0))      # 20 480 triángulos, todos necesarios
    out = S.compact(sph, min_tris=1000)
    assert abs(out.volume() - sph.volume()) <= 2e-3 * sph.volume()
    assert out.num_tri() > 0.5 * sph.num_tri()                                         # la curvatura se conserva
