"""T02 · TexturedMesh y fbxio.load_textured."""
import io
import os
import time

import numpy as np
import pytest

from afm import fbxio
from afm.fbxio import FbxError, load_textured, polygons_from_pvi
from afm.texmesh import TexturedMesh
from fbx_synth import png_bytes, write_fbx

REAL = os.path.join(os.path.dirname(__file__), "..", "examples", "guemes_segmentado_lq_texturas.fbx")
real = pytest.mark.skipif(not os.path.isfile(REAL), reason="falta examples/guemes_segmentado_lq_texturas.fbx")


@pytest.fixture(scope="module")
def guemes():
    t = time.time()
    tm = load_textured(REAL)
    tm.load_seconds = time.time() - t
    return tm


def test_polygons_from_pvi_quads_and_tris():
    assert polygons_from_pvi(np.array([0, 1, 2, ~3, 4, 5, ~6])) == [[0, 1, 2, 3], [4, 5, 6]]


def test_quad_split_uses_shortest_diagonal(tmp_path):
    # el cuadrilátero (0,1,2,3) tiene la diagonal 1–3 más corta que la 0–2
    v = [[0, 0, 0], [2, 0, 0], [3, 1, 0], [2.2, 2, 0]]
    f = str(tmp_path / "q.fbx")
    uv = [[0, 0], [1, 0], [1, 1], [0, 1]]
    write_fbx(f, v, [[0, 1, 2, 3]], [0, 1, 2, 3], uv, texture=np.zeros((2, 2, 3), np.uint8))
    tm = load_textured(f)
    d02, d13 = np.linalg.norm(np.subtract(v[0], v[2])), np.linalg.norm(np.subtract(v[1], v[3]))
    assert d13 < d02
    assert {tuple(sorted(t)) for t in tm.faces.tolist()} == {(0, 1, 3), (1, 2, 3)}
    # y a cada triángulo le tocan los UV de SUS esquinas
    for tri, cuv in zip(tm.faces, tm.corner_uv):
        assert np.allclose(cuv, np.array(uv)[tri])


@real
def test_real_file_counts(guemes):
    assert len(guemes.vertices) == 5345 and len(guemes.faces) == 10686
    assert set(guemes.images) == {"base_color", "normal", "roughness", "metallic"}


@real
def test_real_file_scaling_applied(guemes):
    ext = guemes.vertices.max(0) - guemes.vertices.min(0)
    assert np.allclose(sorted(ext), sorted([48.9, 116.0, 20.3]), atol=0.1)


@real
def test_corner_uv_shape_and_range(guemes):
    assert guemes.corner_uv.shape == (10686, 3, 2) and guemes.corner_uv.dtype == np.float32
    assert guemes.corner_uv.min() >= -0.01 and guemes.corner_uv.max() <= 1.01


@real
def test_base_color_image_is_4096_rgb(guemes):
    im = guemes.image()
    assert im.shape == (4096, 4096, 3) and im.dtype == np.uint8
    assert guemes.image("roughness").shape == (4096, 4096, 3)          # un canal replicado


def test_sample_hits_known_texel():
    img = np.zeros((4, 4, 3), np.uint8)
    img[1, 2] = (10, 200, 30)
    tm = TexturedMesh(np.zeros((3, 3)), np.array([[0, 1, 2]], np.int32),
                      np.full((1, 3, 2), [(2 + 0.5) / 4, 1 - (1 + 0.5) / 4], np.float32), {"base_color": png_bytes(img)})
    out = tm.sample(np.array([0]), np.array([[1 / 3, 1 / 3, 1 / 3]]))
    assert out.tolist() == [[10, 200, 30]]


def test_v_axis_convention():
    img = np.zeros((2, 2, 3), np.uint8)
    img[0] = (255, 0, 0)                                              # mitad superior roja
    uv_hi = np.full((1, 3, 2), [0.5, 0.9], np.float32)
    uv_lo = np.full((1, 3, 2), [0.5, 0.1], np.float32)
    mk = lambda uv: TexturedMesh(np.zeros((3, 3)), np.array([[0, 1, 2]], np.int32), uv, {"base_color": png_bytes(img)})
    b = np.array([[1 / 3, 1 / 3, 1 / 3]])
    assert mk(uv_hi).sample(np.array([0]), b).tolist() == [[255, 0, 0]]
    assert mk(uv_lo).sample(np.array([0]), b).tolist() == [[0, 0, 0]]


def test_missing_texture_warns(tmp_path):
    f = str(tmp_path / "n.fbx")
    write_fbx(f, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], [0, 1, 2], [[0, 0], [1, 0], [0, 1]], texture=None)
    tm = load_textured(f)
    assert any("color base" in a for a in tm.info["avisos"]) and "base_color" not in tm.images


@real
def test_real_file_duplicate_texture_link_is_ignored(guemes):
    assert len(guemes.images) == 4 and guemes.info["avisos"] == []


def test_accepts_bytes_and_both_wire_formats(tmp_path):
    for ver in (7400, 7500):
        f = str(tmp_path / f"v{ver}.fbx")
        write_fbx(f, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], [0, 1, 2], [[0, 0], [1, 0], [0, 1]],
                  texture=np.zeros((2, 2, 3), np.uint8), scaling=(100, 100, 100), version=ver)
        a, b = load_textured(f), load_textured(open(f, "rb").read())
        assert np.allclose(a.vertices, b.vertices) and a.vertices.max() == 100 and a.info["version"] == ver


def test_non_supported_uv_mapping_raises(tmp_path):
    f = str(tmp_path / "m.fbx")
    write_fbx(f, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], [0, 1, 2], [[0, 0], [1, 0], [0, 1]], mapping="ByVertice")
    with pytest.raises(FbxError, match="UV"):
        load_textured(f)


@real
def test_real_file_loads_fast(guemes):
    assert guemes.load_seconds < 10
