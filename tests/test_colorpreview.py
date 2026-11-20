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
