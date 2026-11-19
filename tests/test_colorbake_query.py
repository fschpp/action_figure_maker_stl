"""T12 · Horneado: consulta de color por texel."""
import dataclasses
import time

import numpy as np
import pytest

from afm import colorbake as CB, pipeline as P, solids as S
from afm.colorreg import register_piece
from afm.colorsrc import ColorSource
from afm.project import demo_project
from afm.testfigure import make_doll
from afm.unwrap import unwrap_triangles
from color_helpers import REGIONS, doll_textured

RED, BLUE, GREEN = REGIONS["brazo_izq"], REGIONS["brazo_der"], REGIONS["pierna_izq"]


@pytest.fixture(scope="module")
def built():
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0)
    res = P.build(S.to_manifold(make_doll()), proj.limbs, p, proj.symmetry)
    return res, p


@pytest.fixture(scope="module")
def src():
    return ColorSource(doll_textured(), spacing_mm=0.1)


@pytest.fixture(scope="module")
def meshes(built):
    res, _p = built
    return {n: S.to_printable_trimesh(m) for n, m in res.pieces.items()}


@pytest.fixture(scope="module")
def regs(built, meshes, src):
    res, p = built
    return {n: register_piece(n, m, src, res, p) for n, m in meshes.items()}


@pytest.fixture(scope="module")
def unws(meshes):
    return {n: unwrap_triangles(m, texel_mm=0.3) for n, m in meshes.items()}


def _share(bk, color, tol=30):
    """Fracción de los texeles cubiertos cuyo color está a ≤ `tol` (por canal) de `color`."""
    px = bk.atlas[bk.mask].astype(int)
    return float((np.abs(px - np.array(color)).max(axis=1) <= tol).mean())


def test_arm_piece_colors_match_region(meshes, unws, regs, src):
    left = CB.bake_piece(meshes["brazo_izq"], unws["brazo_izq"], src, regs["brazo_izq"])
    right = CB.bake_piece(meshes["brazo_der"], unws["brazo_der"], src, regs["brazo_der"])
    print("izq rojo", _share(left, RED), "der azul", _share(right, BLUE))
    assert _share(left, RED) > 0.6 and _share(left, BLUE) < 0.1       # izquierdo: mayormente rojo
    assert _share(right, BLUE) > 0.6 and _share(right, RED) < 0.1     # derecho: mayormente azul


def test_rotated_piece_uses_registration(meshes, unws, regs, src):
    """La pierna sale girada 90° (sentada): con el registro recupera el verde; sin él (identidad) no."""
    reg = regs["pierna_izq"]
    assert reg.status in ("semilla", "barrido")
    ok = CB.bake_piece(meshes["pierna_izq"], unws["pierna_izq"], src, reg)
    bad = CB.bake_piece(meshes["pierna_izq"], unws["pierna_izq"], src, dataclasses.replace(reg, matrix=np.eye(4)))
    print("pierna verde con registro", _share(ok, GREEN), "origen", ok.stats["pct_origen"], "/ sin registro", _share(bad, GREEN), bad.stats["pct_origen"])
    # medido (muñeco, texel 0,3): con registro 58 % verde y 73,5 % con origen; sin registro 36 % y 58,8 % (la pierna girada aún roza la fuente)
    assert _share(ok, GREEN) > 0.5 and _share(ok, GREEN) > _share(bad, GREEN) + 0.15
    assert ok.stats["pct_origen"] > bad.stats["pct_origen"] + 10.0


def test_symmetric_option_copies_left_color_to_right(meshes, unws, regs, src):
    own = CB.bake_piece(meshes["brazo_der"], unws["brazo_der"], src, regs["brazo_der"], symmetric=False)
    sym = CB.bake_piece(meshes["brazo_der"], unws["brazo_der"], src, regs["brazo_der"], symmetric=True)
    assert _share(own, BLUE) > 0.6
    assert _share(sym, RED) > 0.6 and _share(sym, BLUE) < 0.1          # el derecho toma el rojo del izquierdo
    left = CB.bake_piece(meshes["brazo_izq"], unws["brazo_izq"], src, regs["brazo_izq"], symmetric=True)
    ref = CB.bake_piece(meshes["brazo_izq"], unws["brazo_izq"], src, regs["brazo_izq"], symmetric=False)
    assert np.array_equal(left.atlas, ref.atlas)                        # el lado izquierdo no cambia


def test_stats_pct_origen_in_0_100(meshes, unws, regs, src):
    for name in ("torso", "brazo_izq", "pierna_izq"):
        bk = CB.bake_piece(meshes[name], unws[name], src, regs[name])
        st = bk.stats
        assert 0.0 <= st["pct_origen"] <= 100.0
        assert 0 < st["n_texeles"] <= int(bk.mask.sum())            # la máscara incluye la dilatación (T13)
        assert st["area_mm2"] == pytest.approx(meshes[name].area, rel=1e-6)
        assert st["segundos"] >= 0.0
        assert bk.atlas.shape == (unws[name].size[1], unws[name].size[0], 3) and bk.atlas.dtype == np.uint8
        assert bk.origin.shape == bk.mask.shape and not (bk.origin & ~bk.mask).any()
    # medido: el torso del muñeco da 47,7 % con origen (muchas superficies nuevas: cavidades, pasadores, cortes de miembros)
    assert CB.bake_piece(meshes["torso"], unws["torso"], src, regs["torso"]).stats["pct_origen"] > 30.0


def test_chunking_gives_same_result(meshes, unws, regs, src, monkeypatch):
    a = CB.bake_piece(meshes["brazo_izq"], unws["brazo_izq"], src, regs["brazo_izq"])
    monkeypatch.setattr(CB, "CHUNK", 777)
    b = CB.bake_piece(meshes["brazo_izq"], unws["brazo_izq"], src, regs["brazo_izq"])
    assert np.array_equal(a.atlas, b.atlas) and np.array_equal(a.mask, b.mask) and np.array_equal(a.origin, b.origin)
    assert a.stats["pct_origen"] == b.stats["pct_origen"]


def test_bake_time_scales(meshes, regs, src):
    unw = unwrap_triangles(meshes["torso"], texel_mm=0.4)
    t0 = time.time()
    CB.bake_piece(meshes["torso"], unw, src, regs["torso"])
    dt = time.time() - t0
    assert dt < 15.0, dt
