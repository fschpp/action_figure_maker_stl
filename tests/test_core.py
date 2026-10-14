import numpy as np
import pytest

from afm import meshio, solids as S
from afm.planes import Plane
from afm.testfigure import make_doll


def test_plane_from_points_normal():
    p = Plane.from_points((0, 0, 0), (1, 0, 0), (0, 1, 0))
    assert np.allclose(p.normal, (0, 0, 1))
    assert np.allclose(p.to_world(p.to_uv([(2, 3, 0)])), [[2, 3, 0]])


def test_plane_collinear_raises():
    with pytest.raises(ValueError):
        Plane.from_points((0, 0, 0), (1, 0, 0), (2, 0, 0))


def test_prism_any_winding():
    p = Plane.from_normal((0, 0, 0), (0, 1, 0))
    poly = np.array([[0, 0, 0], [10, 0, 0], [10, 0, 10], [0, 0, 10]], float)
    a = S.prism_from_polygon(p, poly, 4)
    b = S.prism_from_polygon(p, poly[::-1], 4)
    assert a.volume() == pytest.approx(400) and b.volume() == pytest.approx(400)


def test_dilate_is_at_least_r():
    cube = S.Manifold.cube((10, 10, 10), center=True)
    d = S.dilate(cube, 0.5)
    bb = d.bounding_box()
    assert bb[3] >= 5.5 - 1e-6


def test_load_and_normalize(tmp_path):
    doll = make_doll()
    doll.apply_scale(0.5)
    f = tmp_path / "d.stl"
    doll.export(f)
    m, rep = meshio.load_figure(str(f))
    assert m.bounds[0][2] == pytest.approx(0)
    assert m.extents[2] == pytest.approx(100)
    assert rep.scale == pytest.approx(2.0)


def test_remove_loose_triangle(tmp_path):
    import trimesh
    doll = make_doll()
    tri = trimesh.Trimesh([[40, 40, 0], [41, 40, 0], [40, 41, 0]], [[0, 1, 2]], process=False)
    both = trimesh.util.concatenate([doll, tri])
    m = meshio.repair(both)
    assert m.is_watertight
    assert m.bounds[1][0] < 30
