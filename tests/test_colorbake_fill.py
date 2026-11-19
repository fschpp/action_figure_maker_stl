"""T13 · Dilatación y políticas de superficies sin origen."""
import dataclasses

import numpy as np
import pytest

from afm import colorbake as CB
from afm.colorreg import Registration
from test_colorbake_query import GREEN, RED, built, meshes, regs, src, unws  # noqa: F401  (fixtures compartidas)

GRAY = np.array([200, 200, 200])


def test_dilation_fills_up_to_px():
    atlas = np.zeros((21, 21, 3), np.uint8)
    mask = np.zeros((21, 21), bool)
    atlas[10, 10] = (10, 20, 30)
    mask[10, 10] = True
    out, m2 = CB.dilate(atlas, mask, 3)
    rr, cc = np.mgrid[0:21, 0:21]
    dist = np.hypot(rr - 10, cc - 10)
    assert np.array_equal(m2, dist <= 3)
    assert (out[m2] == (10, 20, 30)).all()
    assert (out[~m2] == 0).all()
    assert CB.dilate(atlas, mask, 0)[1].sum() == 1                       # px = 0: no cambia nada


def test_dilation_never_overwrites_covered_texels():
    rng = np.random.default_rng(0)
    atlas = rng.integers(0, 255, (32, 32, 3)).astype(np.uint8)
    mask = rng.random((32, 32)) < 0.3
    out, m2 = CB.dilate(atlas, mask, 6)
    assert np.array_equal(out[mask], atlas[mask])
    assert (m2 | ~mask).all()                                             # la máscara solo crece
    assert (m2.sum() > mask.sum())


def test_fill_hex_color_applies_to_no_origin_only(meshes, unws, regs, src):  # noqa: F811
    name = "brazo_izq"
    ext = CB.bake_piece(meshes[name], unws[name], src, regs[name], fill="extend", dilate_px=0)
    hexa = CB.bake_piece(meshes[name], unws[name], src, regs[name], fill="#123456", dilate_px=0)
    no = ext.mask & ~ext.origin
    assert no.any(), "el brazo del muñeco tiene superficies nuevas (cortes, pasador)"
    assert (hexa.atlas[no] == (0x12, 0x34, 0x56)).all()
    assert np.array_equal(hexa.atlas[ext.origin], ext.atlas[ext.origin])  # los texeles con origen no cambian
    assert np.array_equal(hexa.origin, ext.origin) and hexa.stats["pct_origen"] == ext.stats["pct_origen"]


def test_extend_policy_has_no_gray(meshes, unws, regs, src):  # noqa: F811
    for name in ("torso", "brazo_der"):
        bk = CB.bake_piece(meshes[name], unws[name], src, regs[name], fill="extend")
        px = bk.atlas[bk.mask].astype(int)
        assert not (px == GRAY).all(axis=1).any()                         # ni un texel del gris plano #C8C8C8


def test_invalid_fill_raises(meshes, unws, regs, src):  # noqa: F811
    for bad in ("rojo", "#12345", "#GGGGGG", "123456", 5, None):
        with pytest.raises(ValueError):
            CB.parse_fill(bad)
    with pytest.raises(ValueError):
        CB.bake_piece(meshes["brazo_izq"], unws["brazo_izq"], src, regs["brazo_izq"], fill="verde")
    assert CB.parse_fill("extend") is None and CB.parse_fill("#c8C8c8") == (200, 200, 200)


def test_sin_origen_piece_is_flat_gray(meshes, unws, src):  # noqa: F811
    reg = Registration("sin_origen", np.eye(4), 0.0, 0.0, None)
    bk = CB.bake_piece(meshes["brazo_izq"], unws["brazo_izq"], src, reg, dilate_px=0)
    assert (bk.atlas[bk.mask] == GRAY).all()
    assert bk.stats["pct_origen"] == 0.0
    own = CB.bake_piece(meshes["brazo_izq"], unws["brazo_izq"], src, reg, fill="#00FF00", dilate_px=0)
    assert (own.atlas[own.mask] == (0, 255, 0)).all()


def test_dilated_mask_covers_cell_margins(meshes, unws, regs, src):  # noqa: F811
    name = "brazo_izq"
    raw = CB.bake_piece(meshes[name], unws[name], src, regs[name], dilate_px=0)
    dil = CB.bake_piece(meshes[name], unws[name], src, regs[name], dilate_px=4)
    assert dil.mask.sum() > raw.mask.sum() and (dil.mask | ~raw.mask).all()
    assert np.array_equal(dil.atlas[raw.mask], raw.atlas[raw.mask])
    assert dil.stats["n_texeles"] == raw.stats["n_texeles"]


def test_edge_shrink_changes_edge_texels_only(meshes, unws):  # noqa: F811
    unw = unws["brazo_izq"]
    g0 = CB.texel_points(unw)
    g1 = CB.shrink_to_interior(g0, unw, 1.0)
    tri = unw.tri_px.astype(np.float64)[g0.face]
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    area2 = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    opp = np.stack([np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1), np.linalg.norm(b - a, axis=1)], axis=1)
    d0 = (g0.bary * (area2[:, None] / np.maximum(opp, 1e-12))).min(axis=1)
    changed = ~np.all(g0.bary == g1.bary, axis=1)
    assert changed.any() and (d0[changed] < 1.0).all()                   # solo cambian los de borde
    assert np.array_equal(g1.bary[~changed], g0.bary[~changed])
    assert np.array_equal(g1.points[~changed], g0.points[~changed])
    # el movimiento es hacia el interior: ningún texel queda más cerca de una arista que antes
    d1 = (g1.bary * (area2[:, None] / np.maximum(opp, 1e-12))).min(axis=1)
    assert (d1[changed] >= d0[changed] - 1e-6).all()
    assert np.allclose(g1.bary.sum(axis=1), 1.0, atol=1e-5) and g1.bary.min() >= 0
    same = CB.shrink_to_interior(g0, unw, 0.0)                           # edge_px = 0: no hace nada
    assert same is g0


def test_edge_shrink_only_alters_atlas_at_edges(meshes, unws, regs, src):  # noqa: F811
    name = "brazo_izq"
    a = CB.bake_piece(meshes[name], unws[name], src, regs[name], edge_px=0.0, dilate_px=0)
    b = CB.bake_piece(meshes[name], unws[name], src, regs[name], edge_px=1.0, dilate_px=0)
    assert np.array_equal(a.mask, b.mask)
    diff = (a.atlas != b.atlas).any(axis=2)
    assert diff.mean() < 0.25                                             # la gran mayoría del atlas queda igual
