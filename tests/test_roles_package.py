"""T41 · Paquete de roles (`.color.npz`) y `TexturedMesh` con etiquetas como fuente de color. Sin FBX reales ni GUI."""
import hashlib
import os

import numpy as np
import pytest

from afm.colorroles import FORMAT, RolesError, RolesPackage, build_package, propose
from afm.colorsrc import ColorSource
from afm.palette import NO_ROLE, Palette, PaletteError, Role
from afm.texmesh import TexturedMesh, nearest_label, sample_image
from color_helpers import doll_textured, write_textured_fbx
from fbx_synth import png_bytes


def palette_for(n, shift=0):
    return Palette([Role(f"r{i}", f"Rol {i}", "#%02X%02X%02X" % ((40 * i + shift) % 256, (90 + 17 * i) % 256, (200 - 20 * i) % 256)) for i in range(n)])


@pytest.fixture(scope="module")
def doll_pkg():
    tm = doll_textured()
    p = propose(tm, None, k=10)
    return build_package(tm, p, {i: f"r{i}" for i in range(p.k)}, origen={"nombre": "muneco.fbx", "sha256": "ab" * 32}), tm


def test_package_roundtrip_exact(doll_pkg, tmp_path):
    pkg, _ = doll_pkg
    path = str(tmp_path / "pieza@1.color.npz")
    assert pkg.save(path) == path and os.path.isfile(path) and not os.path.exists(path + ".tmp")
    back = RolesPackage.load(path)
    for a in ("vertices", "faces", "corner_uv", "labels"):
        assert np.array_equal(getattr(back, a), getattr(pkg, a)) and getattr(back, a).dtype == getattr(pkg, a).dtype, a
    assert back.roles == pkg.roles and back.origen == pkg.origen
    assert back.vertices.dtype == np.float32 and back.faces.dtype == np.int32 and back.labels.dtype == np.uint8


def test_load_rejects_pickle_bad_shapes_unknown_labels(doll_pkg, tmp_path):
    pkg, _ = doll_pkg
    good = dict(vertices=pkg.vertices, faces=pkg.faces, corner_uv=pkg.corner_uv, labels=pkg.labels)
    meta = lambda **kw: np.frombuffer(__import__("json").dumps({"formato": FORMAT, "roles": pkg.roles, **kw}).encode(), np.uint8)

    def write(name, **arrays):
        path = str(tmp_path / name)
        with open(path, "wb") as fh:
            np.savez(fh, **arrays)
        return path

    with pytest.raises(RolesError, match="legible"):                                   # array de objetos (pickle): no se carga
        RolesPackage.load(write("pickle.npz", meta=np.array([{"a": 1}], dtype=object), **good))
    with pytest.raises(RolesError, match="legible"):                                   # ni siquiera es un npz
        bad = tmp_path / "x.npz"
        bad.write_bytes(b"no soy un zip")
        RolesPackage.load(str(bad))
    with pytest.raises(RolesError, match="formato desconocido"):
        RolesPackage.load(write("fmt.npz", **{**good, "meta": np.frombuffer(b'{"formato": "otro", "roles": []}', np.uint8)}))
    with pytest.raises(RolesError, match="faltan arrays"):
        RolesPackage.load(write("falta.npz", meta=meta(), vertices=pkg.vertices))
    with pytest.raises(RolesError, match="corner_uv"):                                 # formas que no cierran
        RolesPackage.load(write("uv.npz", **{**good, "meta": meta(), "corner_uv": pkg.corner_uv[:-1]}))
    with pytest.raises(RolesError, match="fuera de los vértices"):
        RolesPackage.load(write("idx.npz", **{**good, "meta": meta(), "faces": pkg.faces + 10_000}))
    over = pkg.labels.copy()
    over[0, 0] = len(pkg.roles) + 3                                                      # etiqueta que no es un rol ni 255
    with pytest.raises(RolesError, match="etiquetas que no son un rol"):
        RolesPackage.load(write("lab.npz", **{**good, "meta": meta(), "labels": over}))
    with pytest.raises(RolesError, match="id inválido"):
        RolesPackage.load(write("id.npz", **{**good, "meta": np.frombuffer(__import__("json").dumps({"formato": FORMAT, "roles": ["Malo"] * 1}).encode(), np.uint8)}))
    with pytest.raises(RolesError, match="no es válido"):                              # y `save` tampoco escribe un paquete inválido
        RolesPackage(pkg.vertices, pkg.faces, pkg.corner_uv, over, pkg.roles).save(str(tmp_path / "no.npz"))
    assert not (tmp_path / "no.npz").exists()


def test_nearest_sampling_returns_only_palette_colors():
    atlas = np.array([[0, 1, 2, 0], [1, 2, 0, 1], [2, 0, 1, 2], [0, NO_ROLE, 2, 1]], np.uint8)       # 4×4, 3 roles (+ un «sin rol»)
    pkg = RolesPackage(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32), np.array([[0, 1, 2]], np.int32),
                       np.zeros((1, 3, 2), np.float32), atlas, ["a", "b", "c"])
    pal = Palette([Role("a", "A", "#FF0000"), Role("b", "B", "#00FF00"), Role("c", "C", "#0000FF")])
    tm = pkg.to_textured(pal)
    rng = np.random.default_rng(0)
    for _ in range(3):                                                                    # UV al azar, incluso sobre los bordes de texel
        uv = np.vstack([rng.random((4000, 2)), [[0, 0], [1, 1], [0.5, 0.5], [0.25, 0.75]]])
        n = len(uv)
        # un triángulo cuyos tres vértices tienen el mismo UV: la baricéntrica no importa, el color sale de ese UV
        for k in range(0, n, 1000):
            blk = uv[k:k + 1000]
            tm2 = TexturedMesh(tm.vertices, np.tile(tm.faces, (len(blk), 1)), np.repeat(blk[:, None, :], 3, axis=1).astype(np.float32),
                               tm.images, tm.info, labels=tm.labels, lut=tm.lut)
            got = tm2.sample(np.arange(len(blk)), np.tile([1 / 3, 1 / 3, 1 / 3], (len(blk), 1)))
            assert {tuple(int(x) for x in c) for c in got} <= {(255, 0, 0), (0, 255, 0), (0, 0, 255), (200, 200, 200)}   # el último, «sin rol»
    # nada de colores intermedios aunque el UV caiga justo en el borde entre dos texels de colores distintos
    edge = np.array([[0.25, 0.9], [0.5, 0.5], [0.75, 0.25]])
    assert set(map(tuple, pal_lut_rows(pkg, pal)[nearest_label(atlas, edge)].tolist())) <= {(255, 0, 0), (0, 255, 0), (0, 0, 255), (200, 200, 200)}


def pal_lut_rows(pkg, pal):
    return pal.lut(pkg.roles)


def test_uv_convention_matches_bilinear_at_texel_centers():
    rng = np.random.default_rng(2)
    H, W = 6, 9
    atlas = rng.integers(0, 4, (H, W)).astype(np.uint8)
    lut = palette_for(4).lut([f"r{i}" for i in range(4)])
    i, j = np.meshgrid(np.arange(H), np.arange(W), indexing="ij")
    uv = np.column_stack([((j + 0.5) / W).ravel(), (1 - (i + 0.5) / H).ravel()])
    nearest = lut[nearest_label(atlas, uv)]
    assert np.array_equal(nearest, sample_image(lut[atlas], uv))                          # en el centro del texel, igual que el bilineal
    assert np.array_equal(nearest, lut[atlas].reshape(-1, 3))                             # y es exactamente el texel (fila i, columna j)
    # fuera de rango no revienta (tope en los bordes)
    assert nearest_label(atlas, np.array([[-0.2, 1.3], [1.4, -0.3]])).shape == (2,)


def test_missing_role_raises_with_list(doll_pkg):
    pkg, _ = doll_pkg
    short = palette_for(len(pkg.roles) - 2)
    with pytest.raises(PaletteError) as e:
        pkg.to_textured(short)
    assert all(r in str(e.value) for r in pkg.roles[-2:]) and pkg.roles[0] not in str(e.value).split(":")[1].split(", ")
    with pytest.raises(PaletteError, match="no existen"):
        pkg.to_textured(palette_for(len(pkg.roles)), {"fantasma": "#000000"})


def test_colorsource_from_roles_has_only_palette_colors(doll_pkg):
    pkg, _ = doll_pkg
    pal = palette_for(len(pkg.roles))
    tm = pkg.to_textured(pal)
    assert tm.info["roles"] == pkg.roles and "base_color" in tm.images and tm.labels is not None
    src = ColorSource(tm, spacing_mm=1.0)
    allowed = {tuple(int(x) for x in pal.lut(pkg.roles)[i]) for i in range(len(pkg.roles))}
    got = {tuple(int(x) for x in c) for c in np.unique(src.sample_rgb, axis=0)}
    assert got <= allowed and len(got) >= len(pkg.roles) - 1
    moved = src.query(src.sample_xyz[::50] + 0.01)                                         # una consulta también devuelve solo colores de paleta
    assert {tuple(int(x) for x in c) for c in np.unique(moved.rgb, axis=0)} <= allowed
    assert tm.transformed(np.eye(4)).labels is tm.labels and tm.transformed(np.eye(4)).lut is tm.lut      # transformed conserva etiquetas y LUT


def test_recolor_changes_rgb_without_rebuilding_tree(doll_pkg):
    pkg, _ = doll_pkg
    pal = palette_for(len(pkg.roles))
    src = ColorSource(pkg.to_textured(pal), spacing_mm=1.0)
    tree, coarse, xyz, before = src._tree, src._coarse, src.sample_xyz, src.sample_rgb.copy()
    new = pal.with_overrides({"r0": "#123456"})
    src.recolor(new.lut(pkg.roles))
    assert src._tree is tree and src._coarse is coarse and src.sample_xyz is xyz           # nada de geometría se tocó
    assert np.array_equal(src.sample_rgb[src.sample_label == 0], np.tile([0x12, 0x34, 0x56], ((src.sample_label == 0).sum(), 1)))
    assert np.array_equal(src.sample_rgb[src.sample_label != 0], before[src.sample_label != 0])      # los demás roles no cambian
    fresh = ColorSource(pkg.to_textured(new), spacing_mm=1.0)                              # igual que armar la fuente de cero con la paleta nueva
    assert np.array_equal(fresh.sample_rgb, src.sample_rgb)
    with pytest.raises(ValueError, match="256, 3"):
        src.recolor(np.zeros((3, 3), np.uint8))
    with pytest.raises(ValueError, match="etiquetas de rol"):
        ColorSource(doll_textured(), spacing_mm=1.0).recolor(new.lut(pkg.roles))


def test_legacy_sampling_unchanged():
    """Sin etiquetas el camino viejo da los mismos bytes que antes de T41 (huellas tomadas sobre `main` @ T40)."""
    tm = doll_textured()
    assert tm.labels is None and tm.lut is None
    rng = np.random.default_rng(0)
    face = rng.integers(0, len(tm.faces), 5000)
    b = rng.random((5000, 3))
    b /= b.sum(1, keepdims=True)
    assert hashlib.sha256(tm.sample(face, b, "base_color").tobytes()).hexdigest() == "26fbc63230e4f01dd2cda49e8b9044b9658c86d5d652fbec831f13696b88a91d"
    src = ColorSource(doll_textured(), spacing_mm=1.0)
    assert src.sample_label is None and len(src.sample_rgb) == 12424
    assert hashlib.sha256(src.sample_rgb.tobytes()).hexdigest() == "21e44274d003c9ca6af0e0b82e6d7a44d801e842abcdfb278d4ffc03ee2c04a7"


def test_package_is_much_smaller_than_fbx(doll_pkg, tmp_path):
    """El FBX real pesa 26 MB por las texturas; el paquete lleva geometría + UV + etiquetas. Acá, el muñeco con cuatro mapas sombreados
    de 1024² (color, normal, rugosidad, metálico) como los del FBX real; con la textura chica de `doll_textured` (256², casi plana)
    el FBX pesa 33 KB y el paquete NO es 5 % de eso (la geometría pesa más que la textura)."""
    pkg, tm = doll_pkg
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:1024, 0:1024]
    shaded = lambda: np.clip(120 + 60 * np.sin(xx / 90.0)[..., None] + 40 * np.cos(yy / 70.0)[..., None] + rng.normal(0, 6, (1024, 1024, 3)), 0, 255).astype(np.uint8)
    rich = TexturedMesh(tm.vertices, tm.faces, tm.corner_uv, {"base_color": png_bytes(shaded())}, {})
    from fbx_synth import write_fbx
    fbx = str(tmp_path / "rico.fbx")
    write_fbx(fbx, rich.vertices, rich.faces.tolist(), np.arange(3 * len(rich.faces)), np.asarray(rich.corner_uv, float).reshape(-1, 2),
              texture=rich.image("base_color"), extra_textures={"NormalMap": shaded(), "ShininessExponent": shaded(), "ReflectionFactor": shaded()})
    npz = pkg.save(str(tmp_path / "p.color.npz"))
    ratio = os.path.getsize(npz) / os.path.getsize(fbx)
    print(f"\nFBX {os.path.getsize(fbx) / 1e6:.2f} MB, paquete {os.path.getsize(npz) / 1e3:.1f} KB, {100 * ratio:.2f} %")
    assert ratio < 0.05
