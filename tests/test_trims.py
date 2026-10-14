import numpy as np
from afm import solids as S
from afm.planes import Plane
from afm.project import Project, demo_project
from afm.trims import Trim, apply_trims


def _box():
    return S.Manifold.cube((20, 20, 20), center=True)


def test_subtract_polygon():
    pl = Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría")
    poly = pl.to_world([[-5, -5], [5, -5], [5, 5], [-5, 5]])
    t = Trim("torso", pl, poly, distance=40)
    out = apply_trims(_box(), [t], "torso")
    assert abs(out.volume() - (8000 - 10 * 10 * 20)) < 1e-3   # el prisma de 10x10 atraviesa los 20 mm
    assert apply_trims(_box(), [t], "otro").volume() == _box().volume()


def test_sphere_and_roundtrip():
    pl = Plane.from_normal((0, 0, 0), (1, 0, 0))
    pts = np.array([[0, 0, 0], [4, 0, 0]], float)
    t = Trim("torso", pl, pts, shape="sphere")
    assert apply_trims(_box(), [t], "torso").volume() < _box().volume()
    pr = demo_project()
    pr.trims.append(t)
    pr2 = Project.from_dict(pr.to_dict())
    assert len(pr2.trims) == 1 and pr2.trims[0].shape == "sphere"
