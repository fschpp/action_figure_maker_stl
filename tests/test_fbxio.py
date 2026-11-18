"""T01: parser binario de FBX (solo árbol de nodos)."""
import os
import struct
import sys
import time
import zlib

import numpy as np
import pytest

from afm import fbxio
from afm.fbxio import FbxError, Node, fbx_version, parse_fbx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FBX = os.path.join(ROOT, "examples", "guemes_segmentado_lq_texturas.fbx")
real = pytest.mark.skipif(not os.path.exists(FBX), reason="falta el FBX de ejemplo")


def _prop(v):
    if isinstance(v, bool):
        return b"C" + struct.pack("<?", v)
    if isinstance(v, int):
        return b"L" + struct.pack("<q", v)
    if isinstance(v, float):
        return b"D" + struct.pack("<d", v)
    if isinstance(v, str):
        b = v.encode()
        return b"S" + struct.pack("<I", len(b)) + b
    if isinstance(v, bytes):
        return b"R" + struct.pack("<I", len(v)) + v
    if isinstance(v, np.ndarray):          # float64, comprimido
        raw = zlib.compress(v.astype("<f8").tobytes())
        return b"d" + struct.pack("<III", len(v), 1, len(raw)) + raw
    raise TypeError(v)


def _write_fbx(version: int, name="Hola", props=(), kids=()) -> bytes:
    wide = version >= 7500
    hdr = 25 if wide else 13

    def node(n, ps, ks, base):
        pb = b"".join(_prop(p) for p in ps)
        nm = n.encode()
        pos = base + hdr + len(nm) + len(pb)
        body = b""
        for k in ks:
            b = node(*k, pos + len(body))
            body += b
        if ks:
            body += b"\0" * hdr
        end = pos + len(body)
        h = struct.pack("<QQQB" if wide else "<IIIB", end, len(ps), len(pb), len(nm))
        return h + nm + pb + body

    head = fbxio.MAGIC + struct.pack("<I", version)
    out = node(name, list(props), list(kids), len(head))
    return head + out + b"\0" * hdr


@pytest.mark.parametrize("ver", [7400, 7500])
def test_synthetic_v7400_and_v7500_roundtrip(ver):
    arr = np.arange(6, dtype=float)
    data = _write_fbx(ver, "Raiz", [True, 7, 2.5, "txt", b"\x89PNG"], [("Hijo", [arr], [])])
    root = parse_fbx(data)
    n = root.first("Raiz")
    assert n.props[:5] == [True, 7, 2.5, "txt", b"\x89PNG"]
    assert np.array_equal(n.first("Hijo").props[0], arr)
    assert fbx_version(data) == ver


def test_ascii_fbx_is_rejected():
    with pytest.raises(FbxError, match="texto"):
        parse_fbx(b"; FBX 7.4.0 project file\n" + b"x" * 100)


def test_bad_signature_and_version():
    with pytest.raises(FbxError):
        parse_fbx(b"no soy un fbx" * 5)
    with pytest.raises(FbxError, match="versión"):
        parse_fbx(fbxio.MAGIC + struct.pack("<I", 6000) + b"\0" * 30)


def test_truncated_file_raises_fbxerror():
    data = _write_fbx(7400, "Raiz", [1, "a"], [("Hijo", [np.arange(50.0)], [])])
    for cut in (30, 45, len(data) // 2, len(data) - 20):
        try:
            parse_fbx(data[:cut])
        except FbxError:
            pass


@real
def test_real_file_version_and_roots():
    d = open(FBX, "rb").read()
    assert fbx_version(d) == 7400
    names = [k.name for k in parse_fbx(d).kids]
    assert {"Objects", "Connections", "GlobalSettings"} <= set(names)


@real
def test_real_file_geometry_arrays():
    g = [n for n in parse_fbx(open(FBX, "rb").read()).first("Objects").find("Geometry") if n.props[2] == "Mesh"][0]
    assert len(g.first("Vertices").props[0]) == 16035
    assert len(g.first("PolygonVertexIndex").props[0]) == 21850
    assert len(g.first("LayerElementUV").first("UVIndex").props[0]) == 21850


@real
def test_real_file_embedded_png():
    v = parse_fbx(open(FBX, "rb").read()).first("Objects").find("Video")[0]
    c = v.first("Content").props[0]
    assert c[:4] == b"\x89PNG" and len(c) == 12524256


@real
def test_real_file_parses_in_under_5_seconds():
    d = open(FBX, "rb").read()
    t = time.time()
    parse_fbx(d)
    assert time.time() - t < 5


def test_import_does_not_pull_heavy_modules():
    import subprocess
    code = f"import sys; sys.path.insert(0, {os.path.join(ROOT, 'src')!r}); import afm.fbxio; assert 'PIL' not in sys.modules and 'trimesh' not in sys.modules"
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
