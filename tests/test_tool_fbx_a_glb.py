"""T05 · tools/fbx_a_glb.py: FBX texturizado → GLB con color, de pie y a escala."""
import os
import sys

import numpy as np
import pytest
import trimesh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fbx_a_glb  # noqa: E402
from afm.texmesh import sample_image  # noqa: E402
from color_helpers import doll_textured  # noqa: E402

FBX = os.path.join(ROOT, "examples", "guemes_segmentado_lq_texturas.fbx")
real = pytest.mark.skipif(not os.path.isfile(FBX), reason="falta examples/guemes_segmentado_lq_texturas.fbx")


def _load(path):
    scene = trimesh.load(path, process=False)
    geoms = list(scene.geometry.values())
    assert len(geoms) == 1
    return geoms[0]


@pytest.fixture(scope="module")
def guemes_glb(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("glb") / "guemes.glb")
    return out, fbx_a_glb.convert(FBX, out, front="-Y", crown_z=101.769)


@real
def test_glb_exists_and_loads(guemes_glb):
    path, info = guemes_glb
    assert os.path.getsize(path) > 1000
    m = _load(path)
    assert len(m.vertices) == 32058 == info["vertices_glb"] == 10686 * 3
    assert len(m.faces) == 10686


@real
def test_glb_height_and_upright(guemes_glb, tmp_path):
    path, info = guemes_glb
    b = _load(path).bounds
    # con --crown-z la coronilla queda a 100 mm y todo (sombrero) llega a 114,0 como el STL de Güemes
    assert b[0, 2] == pytest.approx(0.0, abs=1e-6) and b[1, 2] == pytest.approx(114.0, abs=0.1)
    assert info["escala"] == pytest.approx(114.0 / 115.962, abs=1e-4)
    ext = b[1] - b[0]
    assert ext[2] > ext[0] and ext[2] > ext[1]                          # de pie
    assert (b[0, :2] + b[1, :2]) / 2 == pytest.approx([0, 0], abs=1e-3)  # centrada en X e Y
    # sin --crown-z: 100 mm hasta el punto más alto
    out = str(tmp_path / "g100.glb")
    info2 = fbx_a_glb.convert(FBX, out)
    assert info2["alto_mm"] == pytest.approx(100.0, abs=0.01)
    assert _load(out).bounds[1, 2] == pytest.approx(100.0, abs=0.01)


@real
def test_glb_has_texture(guemes_glb):
    m = _load(guemes_glb[0])
    assert m.visual.material.baseColorTexture is not None
    assert m.visual.material.baseColorTexture.size == (4096, 4096)
    assert m.visual.uv.shape == (32058, 2)


@real
def test_front_minus_y_rotates_180_about_z(tmp_path):
    a, b = str(tmp_path / "a.glb"), str(tmp_path / "b.glb")
    fbx_a_glb.convert(FBX, a, front="+Y")
    fbx_a_glb.convert(FBX, b, front="-Y")
    va, vb = _load(a).vertices, _load(b).vertices
    assert np.allclose(vb[:, :2], -va[:, :2], atol=1e-4) and np.allclose(vb[:, 2], va[:, 2], atol=1e-4)


def test_colors_survive_the_glb_roundtrip(tmp_path):
    """Muñeco sintético: el color que se lee del GLB (UV por vértice + textura) es el de la fuente en cada cara."""
    tm = doll_textured()
    out = str(tmp_path / "doll.glb")
    info = fbx_a_glb.convert(tm, out)
    assert info["alto_mm"] == pytest.approx(100.0, abs=0.01)
    m = _load(out)
    assert len(m.vertices) == 3 * len(tm.faces)
    img = np.asarray(m.visual.material.baseColorTexture.convert("RGB"))
    got = sample_image(img, np.asarray(m.visual.uv, float).reshape(-1, 3, 2).mean(axis=1))
    want = tm.sample(np.arange(len(tm.faces)), np.full((len(tm.faces), 3), 1 / 3), "base_color")
    assert np.abs(got.astype(int) - want.astype(int)).max() <= 2


def test_cli_main_and_errors(tmp_path, capsys):
    assert fbx_a_glb.main([os.path.join(tmp_path, "no_existe.fbx"), str(tmp_path / "x.glb")]) == 1
    assert "error" in capsys.readouterr().err
    tm = doll_textured()
    tm.images.clear()
    with pytest.raises(ValueError, match="textura"):
        fbx_a_glb.convert(tm, str(tmp_path / "y.glb"))
    with pytest.raises(ValueError, match="front"):
        fbx_a_glb.convert(doll_textured(), str(tmp_path / "z.glb"), front="+X")


def test_cli_accepts_front_minus_y_as_two_words(tmp_path):
    """`--front -Y` (como en el plan) no debe tomarse por una opción de argparse."""
    import subprocess

    assert fbx_a_glb._join_front(["a.fbx", "b.glb", "--front", "-Y", "--altura", "90"]) == ["a.fbx", "b.glb", "--front=-Y", "--altura", "90"]
    assert fbx_a_glb._join_front(["--front", "+Y"]) == ["--front=+Y"]
    assert subprocess.run([sys.executable, os.path.join(ROOT, "tools", "fbx_a_glb.py"), "--help"], capture_output=True).returncode == 0


@real
def test_glb_raw_uv_follow_the_gltf_convention(guemes_glb):
    """Lo ESCRITO en el GLB es v_glb = 1 − v_fbx (origen arriba a la izquierda): el roundtrip por trimesh no lo detecta."""
    import json
    import struct

    from afm import fbxio

    b = open(guemes_glb[0], "rb").read()
    jl, _ = struct.unpack("<II", b[12:20])
    js = json.loads(b[20:20 + jl])
    off = 20 + jl + 8
    a = js["accessors"][js["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]]
    bv = js["bufferViews"][a["bufferView"]]
    uv = np.frombuffer(b[off + bv["byteOffset"]: off + bv["byteOffset"] + a["count"] * 8], np.float32).reshape(-1, 2)
    src = fbxio.load_textured(FBX).corner_uv.reshape(-1, 2)
    assert np.allclose(uv[:, 0], src[:, 0], atol=1e-6) and np.allclose(uv[:, 1], 1 - src[:, 1], atol=1e-6)
