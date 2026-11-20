"""Vista previa a color de una pieza del catálogo que lleva color (`colorpreview`, sin GTK)."""
import numpy as np
import pytest

from afm import colorpreview as CP
from afm.catalog import Catalog

from test_catalog_roles import HEAD_ID, head, line, new_meta  # noqa: F401  (fixtures)


def _split(parts):
    return {tuple(int(round(x * 255)) for x in c[:3]): m for _n, m, c in parts}


def _check_two_colors(parts, mesh):
    cols = _split(parts)
    reds = [k for k in cols if k[0] > k[2]]
    blues = [k for k in cols if k[2] > k[0]]
    assert reds and blues
    mid = (mesh.bounds[0, 2] + mesh.bounds[1, 2]) / 2
    top = np.mean([cols[k].vertices[cols[k].faces].mean(1)[:, 2].mean() for k in reds])
    bottom = np.mean([cols[k].vertices[cols[k].faces].mean(1)[:, 2].mean() for k in blues])
    assert top > mid > bottom                                     # rojo arriba, azul abajo: el color cayó donde debía (la fuente venía en otro marco)
    assert sum(len(m.faces) for _n, m, _c in parts) >= len(mesh.faces)


def test_roles_piece_shows_colors(line, head):  # noqa: F811
    _meta, mesh, _fbx, pkg = head
    cat = Catalog.open(line)
    saved, _w = cat.add_piece(mesh, new_meta(cat.meta(HEAD_ID)), new_version=True, color_roles=pkg)
    parts = CP.piece_color_parts(cat, cat.meta(saved.ref), mesh)
    _check_two_colors(parts, mesh)


def test_rgb_piece_shows_colors(line, head):  # noqa: F811
    _meta, mesh, fbx, _pkg = head
    cat = Catalog.open(line)
    saved, _w = cat.add_piece(mesh, new_meta(cat.meta(HEAD_ID)), new_version=True, color_fbx=fbx)
    _check_two_colors(CP.piece_color_parts(cat, cat.meta(saved.ref), mesh), mesh)


def test_piece_without_color_is_none(line):  # noqa: F811
    cat = Catalog.open(line)
    meta, mesh = cat.load(HEAD_ID)
    assert CP.piece_color_parts(cat, meta, mesh) is None


def test_quantize_limits_groups():
    rng = np.random.default_rng(0)
    rgb = rng.integers(0, 256, (5000, 3)).astype(np.uint8)
    inv, means = CP._quantize(rgb, 24)
    assert len(means) <= 24 and inv.shape == (5000,) and means.dtype == np.uint8


class _FakeSource:
    """Fuente de color falsa: rojo donde x < 0 (con origen); en x ≥ 0 «no tiene origen» y su muestra más cercana es azul."""

    def __init__(self, tm, spacing_mm=0.25):
        pass

    def query(self, points, normals=None, eps=0.15, min_dot=0.3, workers=1):
        from types import SimpleNamespace

        P = np.atleast_2d(np.asarray(points, float))
        left = P[:, 0] < 0
        rgb = np.where(left[:, None], [200, 0, 0], [0, 0, 200]).astype(np.uint8)
        has = left if normals is not None and eps < 1e6 else np.ones(len(P), bool)
        return SimpleNamespace(rgb=rgb, has_origin=has)


def _box(size):
    import trimesh

    return trimesh.creation.box(extents=size)


def test_long_edges_are_subdivided_not_swallowed(monkeypatch):
    """Una arista de ~40 mm necesita más de 4 pasadas para bajar a 1,2 mm: antes el ValueError se tragaba y la vista salía sin subdividir."""
    import warnings

    from afm import colorsrc

    monkeypatch.setattr(colorsrc, "ColorSource", _FakeSource)
    mesh = _box((40.0, 40.0, 40.0))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        parts = CP.color_parts(None, mesh)
    assert sum(len(m.faces) for _n, m, _c in parts) > 20 * len(mesh.faces)


def test_subdivision_failure_warns_and_still_draws(monkeypatch):
    import trimesh.remesh

    from afm import colorsrc

    def boom(*_a, **_k):
        raise ValueError("max_iter exceeded!")

    monkeypatch.setattr(colorsrc, "ColorSource", _FakeSource)
    monkeypatch.setattr(trimesh.remesh, "subdivide_to_size", boom)
    mesh = _box((10.0, 10.0, 10.0))
    with pytest.warns(UserWarning, match="no se pudo subdividir"):
        parts = CP.color_parts(None, mesh)
    assert sum(len(m.faces) for _n, m, _c in parts) == len(mesh.faces)


def test_faces_without_origin_take_neighbour_color(monkeypatch):
    """Las caras sin origen en la fuente (x ≥ 0 en la fuente falsa) toman el color de la cara con origen más cercana, no el de su «muestra más cercana»."""
    from afm import colorsrc

    monkeypatch.setattr(colorsrc, "ColorSource", _FakeSource)
    parts = CP.color_parts(None, _box((10.0, 10.0, 10.0)))
    cols = _split(parts)
    assert list(cols) == [(200, 0, 0)]                            # todo rojo: nada quedó azul


def test_no_origin_anywhere_falls_back_to_nearest_sample(monkeypatch):
    from types import SimpleNamespace

    from afm import colorsrc

    class _Far(_FakeSource):
        def query(self, points, normals=None, eps=0.15, min_dot=0.3, workers=1):
            n = len(np.atleast_2d(points))
            return SimpleNamespace(rgb=np.tile(np.uint8([0, 200, 0]), (n, 1)), has_origin=np.zeros(n, bool) if eps < 1e6 else np.ones(n, bool))

    monkeypatch.setattr(colorsrc, "ColorSource", _Far)
    parts = CP.color_parts(None, _box((10.0, 10.0, 10.0)))
    assert list(_split(parts)) == [(0, 200, 0)]
