"""Color de una pieza extraído de su FBX y publicado **sin el FBX** (`colorrgb`, `exportar(incluir_color=True)`)."""
import json
import os
import zipfile

import numpy as np

from afm import colorrgb as CR
from afm import exportar as X
from afm import parts as P
from afm import validar as V
from afm.catalog import Catalog
from afm.fbxio import load_textured
from color_helpers import doll_textured, write_textured_fbx
from test_exportar import OWNER, add_colored, cat, names, read, unzip  # noqa: F401  (cat es una fixture)


def _fbx(tmp_path, tm=None, name="x.fbx"):
    return write_textured_fbx(tmp_path / name, tm or doll_textured())


def test_el_color_extraido_da_la_misma_malla_uv_y_colores(tmp_path):
    fbx = _fbx(tmp_path)
    ref = load_textured(fbx)
    comp = load_textured(CR.compact_bytes(ref))                 # lo lee la misma función que lee un FBX
    assert comp.faces.shape == ref.faces.shape and np.array_equal(comp.faces, ref.faces)
    assert np.allclose(comp.vertices, ref.vertices, atol=1e-4) and np.allclose(comp.corner_uv, ref.corner_uv, atol=1e-6)
    face = np.arange(len(ref.faces))
    bary = np.full((len(face), 3), 1 / 3)
    a, b = ref.sample(face, bary).astype(int), comp.sample(face, bary).astype(int)
    assert np.abs(a - b).mean() < 6                             # JPEG: el color baja unos pocos niveles, no cambia de región


def test_se_lee_desde_un_archivo_y_no_nombra_el_fbx(tmp_path):
    fbx = _fbx(tmp_path, name="cabeza-secreta.fbx")
    out = tmp_path / "c.colorrgb.npz"
    out.write_bytes(CR.compact_file(fbx))
    assert len(load_textured(str(out)).faces) == len(load_textured(fbx).faces)
    raw = out.read_bytes()
    assert b"cabeza-secreta" not in raw and b"Kaydara" not in raw
    with zipfile.ZipFile(out) as z:
        meta = json.loads(bytes(np.load(z.open("meta.npy"))).decode())
    assert meta["formato"] == CR.FORMAT and "origen" not in meta and "sha256" not in meta


def test_una_textura_grande_se_reduce_y_pesa_menos_que_el_fbx(tmp_path):
    tm = doll_textured()
    rng = np.random.default_rng(1)
    big = np.clip(np.asarray(tm.image("base_color")).repeat(8, 0).repeat(8, 1).astype(int) + rng.integers(-6, 7, (2048, 2048, 3)), 0, 255).astype(np.uint8)
    from fbx_synth import png_bytes
    tm.images["base_color"], tm._cache = png_bytes(big), {}
    fbx = _fbx(tmp_path, tm, "grande.fbx")
    comp = CR.compact_file(fbx, max_side=512)
    assert load_textured(comp).image("base_color").shape[:2] == (512, 512)
    assert len(comp) < os.path.getsize(fbx) / 3


def test_sin_textura_o_roto_se_dice(tmp_path):
    tm = doll_textured()
    tm.images.pop("base_color")
    try:
        CR.compact_bytes(tm)
    except CR.ColorRgbError as exc:
        assert "textura" in str(exc)
    else:
        raise AssertionError("tenía que fallar")
    bad = tmp_path / "roto.colorrgb.npz"
    bad.write_bytes(b"PK basura")
    try:
        load_textured(str(bad))
    except ValueError as exc:
        assert "legible" in str(exc) or "formato" in str(exc)
    else:
        raise AssertionError("tenía que fallar")


def test_exportar_con_incluir_color_lleva_el_color_y_no_el_fbx(cat, tmp_path):
    payload = open(_fbx(tmp_path), "rb").read()
    add_colored(cat, "brazo-rgb-01", "rgb", payload)
    res = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-rgb-01"], incluir_color=True, **OWNER)
    ns = names(res.path)
    assert "piezas/brazo/brazo-rgb-01@1.colorrgb.npz" in ns and not any(n.endswith(".fbx") for n in ns)
    assert not any("se exporta sin color" in a for a in res.avisos)
    ficha = json.loads(read(res.path, "piezas/brazo/brazo-rgb-01@1.pieza.json"))
    col = ficha["color"]
    assert col["fuente"] == "brazo-rgb-01@1.colorrgb.npz" and col["similitud"] and col.get("modo", "rgb") == "rgb"
    import hashlib
    assert col["sha256"] == hashlib.sha256(zipfile.ZipFile(res.path).read("piezas/brazo/brazo-rgb-01@1.colorrgb.npz")).hexdigest()
    assert "fbx" not in json.dumps(ficha).lower()
    assert cat.meta("brazo-rgb-01").color["fuente"].endswith(".fbx")        # el catálogo de origen no se toca
    dest = Catalog.open(unzip(res.path, tmp_path / "x"))
    assert dest.verify().ok
    assert len(load_textured(dest.color_file("brazo-rgb-01")).faces) > 0     # lo que instala quien lo recibe se lee como color de la pieza
    assert not [e for e in V.validate_package(res.path, estricto=False).errors if e.code == "CK307"]


def test_con_incluir_color_un_fbx_ilegible_sale_sin_color_con_aviso(cat, tmp_path):
    add_colored(cat, "brazo-rgb-01", "rgb", b"no es un fbx")
    res = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-rgb-01"], incluir_color=True, **OWNER)
    assert not any(n.endswith((".fbx", ".colorrgb.npz")) for n in names(res.path))
    assert any("no se pudo extraer el color" in a for a in res.avisos)
    assert json.loads(read(res.path, "piezas/brazo/brazo-rgb-01@1.pieza.json")).get("color") in (None, {})


def test_incluir_fbx_manda_sobre_incluir_color(cat, tmp_path):
    add_colored(cat, "brazo-rgb-01", "rgb", b"FBX" * 10)
    res = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-rgb-01"], incluir_fbx=True, incluir_color=True, **OWNER)
    ns = names(res.path)
    assert "piezas/brazo/brazo-rgb-01@1.fbx" in ns and not any(n.endswith(".colorrgb.npz") for n in ns)
