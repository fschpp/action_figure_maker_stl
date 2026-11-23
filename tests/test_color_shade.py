"""Sombreado por rol (`PLAN_color_sombreado.md`): `build_package(shade=True)` guarda, además de las etiquetas, el brillo de cada píxel respecto del
color medio de su rol. Se prueba el atlas, el formato (`afm-color-roles-2` y la compatibilidad con el `-1`), el color que sale al armar, el recoloreo
con otra paleta (la luz se conserva) y el horneado + `recolor` (mismo resultado, píxel por píxel, que volver a hornear)."""
import dataclasses
import json
import os
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from afm import colorbake as CB
from afm.colorroles import (FORMAT, FORMAT_SHADE, RolesError, RolesPackage, build_package, prune_small_roles, propose)
from afm.colorsrc import ColorSource
from afm.palette import NO_ROLE, Palette, Role
from afm.texmesh import SHADE_MAX, SHADE_MIN, SHADE_ONE, TexturedMesh, apply_shade, shade_code, shade_factor
from afm.unwrap import unwrap_triangles
from fbx_synth import png_bytes

SIZE = 64


def textured_quad(img) -> TexturedMesh:
    """Dos triángulos que cubren todo el UV: la textura entera es la superficie (10 × 10 mm, plano z = 0, normal +z)."""
    V = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float)
    F = np.array([[0, 1, 2], [0, 2, 3]], np.int32)
    uv = np.array([[[0, 0], [1, 0], [1, 1]], [[0, 0], [1, 1], [0, 1]]], np.float32)
    return TexturedMesh(V, F, uv, {"base_color": png_bytes(img)}, {})


def lit_texture() -> np.ndarray:
    """Mitad izquierda: «piel» (240, 190, 160) con un degradé de luz de 0,7 a 1,2 de izquierda a derecha de la mitad. Mitad derecha: «azul» plano (30, 60, 200)."""
    img = np.zeros((SIZE, SIZE, 3), np.uint8)
    half = SIZE // 2
    ramp = np.linspace(0.7, 1.2, half)[None, :, None]
    base = np.array([200, 160, 130], float)
    img[:, :half] = np.clip(base[None, None, :] * ramp, 0, 255).astype(np.uint8)
    img[:, half:] = (30, 60, 200)
    return img


def palette(skin="#F0BEA0") -> Palette:
    return Palette([Role("piel", "Piel", skin, [skin]), Role("azul", "Azul", "#1E3CC8", ["#1E3CC8"])])


@pytest.fixture(scope="module")
def built():
    tm = textured_quad(lit_texture())
    prop = propose(tm, None, k=3)
    # cada cluster va al rol de su color medio: los tonos de la mitad izquierda (rojizos) son «piel»; el azul es «azul»
    assign = {c: ("azul" if prop.centers_rgb[c][2] > prop.centers_rgb[c][0] else "piel") for c in range(prop.k)}
    return tm, prop, assign, build_package(tm, prop, assign, filter_px=1, shade=True)


# ------------------------------------------------------------------ atlas
def test_shade_atlas_same_shape_and_clamped(built):
    _tm, _prop, _assign, pkg = built
    assert pkg.shade is not None and pkg.shade.dtype == np.uint8 and pkg.shade.shape == pkg.labels.shape
    assert pkg.problems() == []
    lo, hi = int(round(SHADE_MIN * SHADE_ONE)), int(round(SHADE_MAX * SHADE_ONE))
    assert pkg.shade.min() >= lo and pkg.shade.max() <= hi


def test_shade_follows_the_light_and_averages_one_per_role(built):
    tm, _prop, _assign, pkg = built
    f = shade_factor(pkg.shade)
    skin = pkg.roles.index("piel")
    cols = np.arange(pkg.labels.shape[1])
    left = (pkg.labels == skin)
    assert left[:, : pkg.labels.shape[1] // 2].all()                    # la mitad izquierda es toda piel
    row = f[0, : pkg.labels.shape[1] // 2]
    assert row[0] < 1.0 < row[-1]                                       # lado oscuro < 1 < lado claro
    assert (np.diff(row) >= -0.02).all()                                # sube con la luz
    assert abs(float(f[left].mean()) - 1.0) < 0.05                      # el factor promedia ~1: el hex del rol sigue siendo «el color» del rol
    blue = pkg.labels == pkg.roles.index("azul")
    assert np.allclose(f[blue], 1.0, atol=0.02)                         # un rol plano no se sombrea
    assert cols.size                                                    # (usa `cols` para que el atlas no sea de una columna)


def test_without_shade_package_is_as_before(built):
    tm, prop, assign, with_shade = built
    plain = build_package(tm, prop, assign, filter_px=1)
    assert plain.shade is None
    assert np.array_equal(plain.labels, with_shade.labels) and plain.roles == with_shade.roles


def test_unshaded_roles_and_no_role_stay_at_one():
    img = np.full((SIZE, SIZE, 3), 3, np.uint8)                         # casi negro: luminancia < SHADE_MIN_LUMA
    img[:, SIZE // 2:] = (200, 50, 50)
    tm = textured_quad(img)
    prop = propose(tm, None, k=2)
    dark = int(np.argmin(prop.centers_rgb.sum(axis=1)))
    assign = {c: (None if c == dark else "rojo") for c in range(prop.k)}
    pkg = build_package(tm, prop, assign, filter_px=1, shade=True)
    assert (pkg.shade == SHADE_ONE).all()


# ------------------------------------------------------------------ formato
def test_save_load_roundtrip_is_format_2(built, tmp_path):
    _tm, _prop, _assign, pkg = built
    path = pkg.save(tmp_path / "a.color.npz")
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(bytes(z["meta"]).decode())
        assert meta["formato"] == FORMAT_SHADE and "shade" in z.files
    back = RolesPackage.load(path)
    assert np.array_equal(back.shade, pkg.shade) and np.array_equal(back.labels, pkg.labels) and back.roles == pkg.roles
    assert os.path.getsize(path) < 200_000                              # unas decenas de KB, no los MB del FBX


def test_package_without_shade_keeps_format_1_and_old_files_load(built, tmp_path):
    tm, prop, assign, pkg = built
    plain = dataclasses.replace(pkg, shade=None)
    path = plain.save(tmp_path / "b.color.npz")
    with np.load(path, allow_pickle=False) as z:
        assert json.loads(bytes(z["meta"]).decode())["formato"] == FORMAT and "shade" not in z.files
    assert RolesPackage.load(path).shade is None
    # un archivo escrito por la versión anterior (sin `shade` y con formato -1) se lee igual
    meta = {"formato": FORMAT, "roles": list(pkg.roles), "atlas": [pkg.labels.shape[1], pkg.labels.shape[0]], "origen": {}}
    old = tmp_path / "old.color.npz"
    with open(old, "wb") as fh:
        np.savez_compressed(fh, meta=np.frombuffer(json.dumps(meta).encode(), np.uint8), vertices=pkg.vertices, faces=pkg.faces,
                            corner_uv=pkg.corner_uv, labels=pkg.labels)
    assert RolesPackage.load(old).shade is None


def test_format_2_without_shade_array_is_rejected(built, tmp_path):
    _tm, _prop, _assign, pkg = built
    meta = {"formato": FORMAT_SHADE, "roles": list(pkg.roles), "atlas": [pkg.labels.shape[1], pkg.labels.shape[0]], "origen": {}}
    bad = tmp_path / "bad.color.npz"
    with open(bad, "wb") as fh:
        np.savez_compressed(fh, meta=np.frombuffer(json.dumps(meta).encode(), np.uint8), vertices=pkg.vertices, faces=pkg.faces,
                            corner_uv=pkg.corner_uv, labels=pkg.labels)
    with pytest.raises(RolesError, match="shade"):
        RolesPackage.load(bad)


def test_problems_flags_wrong_shade_shape(built):
    _tm, _prop, _assign, pkg = built
    bad = dataclasses.replace(pkg, shade=pkg.shade[:-1])
    assert any("shade" in p for p in bad.problems())
    with pytest.raises(RolesError):
        bad.save("/nonexistent/never_written.npz")


def test_with_roles_and_prune_keep_shade(built):
    _tm, _prop, _assign, pkg = built
    assert pkg.with_roles(["pelo"]).shade is pkg.shade
    same, dropped = prune_small_roles(pkg, None, 0.0)
    assert dropped == [] and same.shade is pkg.shade
    region = np.ones(len(pkg.faces), bool)
    pruned, dropped = prune_small_roles(pkg, region, 60.0)              # ningún rol llega al 60 %: no se quita nada (se evita dejarla sin roles)
    assert pruned.shade is not None


# ------------------------------------------------------------------ al armar
def test_apply_shade_keeps_hue_and_does_not_burn_light_colors():
    rgb = np.array([[250, 200, 100], [100, 50, 20]], np.uint8)
    out = apply_shade(rgb, np.array([1.5, 1.5], np.float32))
    assert out.max() <= 255
    r0, r1 = rgb[0] / rgb[0].max(), out[0] / out[0].max()
    assert np.allclose(r0, r1, atol=0.01)                               # el matiz no cambia al recortar
    assert tuple(out[1]) == (150, 75, 30)                               # lo que cabe, se multiplica tal cual
    assert np.array_equal(apply_shade(rgb, np.ones(2, np.float32)), rgb)
    assert np.array_equal(shade_code(shade_factor(np.array([64, 128, 192], np.uint8))), [64, 128, 192])


def test_to_textured_samples_role_color_times_light(built):
    _tm, _prop, _assign, pkg = built
    tm = pkg.to_textured(palette())
    assert tm.shade is not None
    face = np.zeros(2, np.int32)                                        # triángulo 0: (0,0)-(10,0)-(10,10)
    dark, light = np.array([[0.9, 0.1, 0.0], [0.55, 0.45, 0.0]])        # u = 0,1 y u = 0,45: los dos en la mitad izquierda (piel), uno oscuro y otro claro
    px = tm.sample(face, np.array([dark, light]))
    assert px[0].sum() < px[1].sum()                                    # el mismo rol, más oscuro donde la textura era oscura
    assert len({tuple(c) for c in px}) == 2
    hex_skin = np.array([0xF0, 0xBE, 0xA0], float)
    assert np.allclose(px[0] / px[0].max(), hex_skin / hex_skin.max(), atol=0.02)      # mismo matiz que el hex del rol


def test_recolor_by_palette_keeps_the_shading(built):
    _tm, _prop, _assign, pkg = built
    face = np.zeros(2, np.int32)
    bary = np.array([[0.9, 0.1, 0.0], [0.55, 0.45, 0.0]])             # u = 0,1 (lado oscuro de la piel) y u = 0,45 (lado claro)
    a = pkg.to_textured(palette("#F0BEA0")).sample(face, bary)
    b = pkg.to_textured(palette("#3C8C3C")).sample(face, bary)          # piel → verde
    assert (b[:, 1] > b[:, 0]).all() and (b[:, 1] > b[:, 2]).all()      # ahora es verde en todo el rol
    assert b[0].sum() < b[1].sum()                                      # y la luz sigue ahí: el lado oscuro sigue más oscuro
    assert a[0].sum() < a[1].sum()
    flat = dataclasses.replace(pkg, shade=None).to_textured(palette("#3C8C3C")).sample(face, bary)
    assert (flat[0] == flat[1]).all()                                   # sin sombreado: color plano (el comportamiento de siempre)


def test_preview_png_is_shaded(built):
    from PIL import Image
    import io

    _tm, _prop, _assign, pkg = built
    shaded = np.asarray(Image.open(io.BytesIO(pkg.to_textured(palette()).images["base_color"])).convert("RGB"))
    flat = np.asarray(Image.open(io.BytesIO(dataclasses.replace(pkg, shade=None).to_textured(palette()).images["base_color"])).convert("RGB"))
    assert len(np.unique(shaded.reshape(-1, 3), axis=0)) > len(np.unique(flat.reshape(-1, 3), axis=0))


# ------------------------------------------------------------------ fuente de color y horneado
def test_color_source_recolor_equals_a_fresh_source(built):
    _tm, _prop, _assign, pkg = built
    src = ColorSource(pkg.to_textured(palette("#F0BEA0")), spacing_mm=0.2)
    assert src.sample_shade is not None and len(np.unique(src.sample_rgb, axis=0)) > 2
    new = palette("#3C8C3C").lut(pkg.roles)
    src.recolor(new)
    fresh = ColorSource(pkg.to_textured(palette("#3C8C3C")), spacing_mm=0.2)
    assert np.array_equal(src.sample_rgb, fresh.sample_rgb)


def test_unshaded_source_is_untouched(built):
    _tm, _prop, _assign, pkg = built
    src = ColorSource(dataclasses.replace(pkg, shade=None).to_textured(palette()), spacing_mm=0.2)
    assert src.sample_shade is None
    q = src.query(np.array([[1.0, 1.0, 0.0]]), np.array([[0.0, 0.0, 1.0]]))
    assert q.shade is None and len(np.unique(src.sample_rgb, axis=0)) <= 2


def test_bake_then_recolor_equals_rebake(built):
    _tm, _prop, _assign, pkg = built
    mesh = trimesh.Trimesh(np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float), np.array([[0, 1, 2], [0, 2, 3]]), process=False)
    unw = unwrap_triangles(mesh, texel_mm=0.25)
    reg = SimpleNamespace(matrix=np.eye(4), status="ok")
    old, new = palette("#F0BEA0"), palette("#3C8C3C")
    bk = CB.bake_piece(mesh, unw, ColorSource(pkg.to_textured(old), spacing_mm=0.1), reg)
    assert bk.shade is not None and bk.shade.shape == bk.labels.shape and bk.shade.min() < 1.0 < bk.shade.max()
    want = CB.bake_piece(mesh, unw, ColorSource(pkg.to_textured(new), spacing_mm=0.1), reg)
    # lo mismo que hace `Colorizer.recolor` (T45): atlas = lut[etiquetas] × factor en los texeles con rol
    lut = new.lut(bk.label_roles)
    atlas = bk.atlas.copy()
    has_role = bk.mask & (bk.labels != NO_ROLE)
    atlas[has_role] = apply_shade(lut[bk.labels[has_role]], bk.shade[has_role])
    assert np.array_equal(atlas[has_role], want.atlas[has_role])
    assert np.array_equal(bk.labels, want.labels)
