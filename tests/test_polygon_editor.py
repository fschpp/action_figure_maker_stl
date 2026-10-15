import numpy as np

from afm.gui.polygon_editor import PolygonDraft, ray_plane_uv
from afm.planes import Plane


def _plane():
    return Plane.from_normal((0, 0, 50), (0, 1, 0), "brazo")        # plano vertical y = 0


def _square(plane, closed=True):
    d = PolygonDraft(plane)
    for uv in ((0, 0), (10, 0), (10, 10), (0, 10)):
        d.add(uv)
    if closed:
        assert d.close()
    return d


def test_ray_plane_uv_roundtrip():
    pl = _plane()
    uv = np.array([3.0, -4.0])
    target = pl.to_world(uv)[0]
    o = target + np.array([0.0, -100.0, 0.0]) + np.array([5.0, 0.0, 2.0])
    got = ray_plane_uv(pl, o, (target - o) / np.linalg.norm(target - o))
    assert np.allclose(got, uv, atol=1e-9)


def test_ray_plane_edge_on_or_behind_is_none():
    pl = _plane()
    assert ray_plane_uv(pl, (0, -100, 50), (1, 0, 0)) is None            # paralelo al plano
    assert ray_plane_uv(pl, (0, 100, 50), (0, 1, 0)) is None             # el plano queda atrás


def test_world_points_lie_on_the_plane():
    pl = _plane()
    d = _square(pl)
    assert np.allclose(pl.signed_distance(d.world()), 0.0)
    again = PolygonDraft.from_world(pl, d.world())
    assert again.closed and np.allclose(np.array(again.pts), np.array(d.pts))


def test_validity():
    pl = _plane()
    assert _square(pl).validity_error() is None
    bow = PolygonDraft(pl, [np.array(p, float) for p in ((0, 0), (10, 10), (10, 0), (0, 10))], closed=True)
    assert "cruza" in bow.validity_error()
    assert "3 vértices" in PolygonDraft(pl, [np.zeros(2), np.ones(2)]).validity_error()
    flat = PolygonDraft(pl, [np.array(p, float) for p in ((0, 0), (5, 0), (10, 0))], closed=True)
    assert flat.validity_error() is not None


def test_close_needs_three_points():
    d = PolygonDraft(_plane())
    d.add((0, 0)); d.add((1, 0))
    assert not d.close() and not d.closed


def test_hit_testing_in_pixels():
    d = _square(_plane())
    screen = np.array([[100, 100], [200, 100], [200, 200], [100, 200]], float)
    assert d.nearest_vertex(screen, (103, 98)) == 0
    assert d.nearest_vertex(screen, (150, 150)) is None
    assert d.nearest_edge(screen, (150, 103)) == 0           # borde 0 -> 1
    assert d.nearest_edge(screen, (97, 150)) == 3            # borde de cierre 3 -> 0
    assert d.nearest_edge(screen, (150, 150)) is None
    d.closed = False
    assert d.nearest_edge(screen, (97, 150)) is None         # abierto: no hay borde de cierre


def test_insert_move_delete():
    d = _square(_plane())
    d.insert(0, (5, 0))
    assert len(d.pts) == 5 and np.allclose(d.pts[1], (5, 0))
    d.move(1, (5, -2))
    assert np.allclose(d.pts[1], (5, -2))
    d.selected = 1
    d.delete(1)
    assert len(d.pts) == 4 and d.selected is None
