"""T40 · Clusters de color y asignación a roles a partir de un `TexturedMesh` (`colorroles.propose` / `build_package`). Sin disco ni GUI."""
import os
import time

import numpy as np
import pytest

from afm.colorroles import MAX_ATLAS, Proposal, RolesError, build_package, mode_filter, propose
from afm.palette import NO_ROLE, Palette, Role
from afm.texmesh import TexturedMesh
from color_helpers import REGIONS, _ORDER, doll_textured, region_of
from fbx_synth import png_bytes

SLOW = pytest.mark.skipif(os.environ.get("AFM_SLOW") != "1", reason="AFM_SLOW=1: figura real de Güemes (≈ 30 s)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lab(rgb):
    from skimage.color import rgb2lab
    return rgb2lab(np.asarray(rgb, float).reshape(-1, 1, 3) / 255.0).reshape(-1, 3)


def doll_true_colors(tm, min_share=0.002):
    """Colores reales del muñeco (por región, con su % de área ≥ `min_share`)."""
    V, F = tm.vertices, tm.faces
    t = V[F]
    area = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
    by = {}
    for c, a in zip(t.mean(axis=1), area):
        by[region_of(c)] = by.get(region_of(c), 0.0) + a
    tot = sum(by.values())
    return {n: REGIONS[n] for n, a in by.items() if a / tot >= min_share}


def repaint(tm, **colors):
    """Copia del muñeco con otro color en los bloques de las regiones indicadas (`pierna_izq=(150,150,150)`)."""
    from PIL import Image
    import io
    img = np.array(Image.open(io.BytesIO(tm.images["base_color"])).convert("RGB"))
    for name, col in colors.items():
        r, c = divmod(_ORDER.index(name), 32)
        img[r * 8:(r + 1) * 8, c * 8:(c + 1) * 8] = col
    return TexturedMesh(tm.vertices, tm.faces, tm.corner_uv, {"base_color": png_bytes(img)}, dict(tm.info))


def quad(img):
    """Dos triángulos que cubren todo el UV: la textura entera es la superficie."""
    V = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float)
    F = np.array([[0, 1, 2], [0, 2, 3]], np.int32)
    uv = np.array([[[0, 0], [1, 0], [1, 1]], [[0, 0], [1, 1], [0, 1]]], np.float32)
    return TexturedMesh(V, F, uv, {"base_color": png_bytes(img)}, {})


def test_clusters_of_flat_regions_recovered():
    tm = doll_textured()
    truth = doll_true_colors(tm)
    assert 6 <= len(truth) <= 8
    p = propose(tm, None, k=10)
    assert isinstance(p, Proposal) and p.k <= 10 and p.k >= len(truth)
    assert p.share.sum() == pytest.approx(100.0) and (np.diff(p.share) <= 1e-9).all()          # del más grande al más chico
    d = np.linalg.norm(lab(list(truth.values()))[:, None] - lab(p.centers_rgb)[None], axis=2)
    assert d.min(axis=1).max() < 3.0                              # cada color real tiene un centro a ΔE < 3
    assert d.min(axis=0).max() < 3.0                              # y cada centro es un color real (no inventa tonos)
    assert p.labels_atlas.shape == (256, 256) and p.labels_atlas.dtype == np.uint8 and p.labels_atlas.max() < p.k


def test_shades_of_one_role_can_share_label():
    tm = repaint(doll_textured(), pierna_izq=(150, 150, 150), pierna_der=(190, 190, 190), brazo_izq=(110, 110, 110))
    p = propose(tm, None, k=10)
    greys = [i for i, c in enumerate(p.centers_rgb) if abs(int(c[0]) - int(c[1])) < 3 and abs(int(c[1]) - int(c[2])) < 3 and 100 < c[0] < 200]
    assert len(greys) >= 3                                        # tres tonos de gris = tres clusters
    assign = {i: ("uniforme" if i in greys else f"otro{i}") for i in range(p.k)}
    pkg = build_package(tm, p, assign, filter_px=1)
    assert pkg.roles.count("uniforme") == 1 and len(pkg.roles) == p.k - len(greys) + 1
    u = pkg.roles.index("uniforme")
    for g in greys:                                               # los píxeles de los tres tonos quedaron con la misma etiqueta
        assert (pkg.labels[p.raw_atlas == g] == u).all()
    assert pkg.labels.dtype == np.uint8 and len(pkg.vertices) == len(tm.vertices)


def test_suggestion_uses_anchors_and_none_when_far():
    tm = doll_textured()
    pal = Palette([Role("rojo", "Rojo", "#FF0000", ["#DC1E1E"]),                 # = brazo_izq (220, 30, 30)
                   Role("azul", "Azul", "#0000FF", ["#1E1EDC", "#2020D0"]),       # ≈ brazo_der (30, 30, 220)
                   Role("sin_anclas", "Sin anclas", "#00FF00")])
    p = propose(tm, pal, k=10)
    by_rgb = {tuple(int(x) for x in c): s for c, s in zip(p.centers_rgb, p.suggested)}
    assert by_rgb[REGIONS["brazo_izq"]] == "rojo" and by_rgb[REGIONS["brazo_der"]] == "azul"
    assert by_rgb[REGIONS["cabeza"]] is None                      # el amarillo está lejos de toda ancla
    assert all(s in (None, "rojo", "azul") for s in p.suggested)  # un rol sin anclas nunca se sugiere
    assert propose(tm, Palette([]), k=10).suggested == [None] * p.k and propose(tm, None, k=10).suggested == [None] * p.k


def test_build_requires_complete_assignment():
    tm = doll_textured()
    p = propose(tm, None, k=10)
    with pytest.raises(RolesError, match="faltan clusters"):
        build_package(tm, p, {0: "piel"})
    full = {i: None for i in range(p.k)}
    with pytest.raises(RolesError, match="no existen"):
        build_package(tm, p, {**full, 99: "piel"})
    with pytest.raises(RolesError, match="id de rol inválido"):
        build_package(tm, p, {**full, 0: "Piel Roja"})
    pkg = build_package(tm, p, full)                              # todo «sin rol» es una decisión válida
    assert pkg.roles == [] and (pkg.labels == NO_ROLE).all()


def test_mode_filter_removes_isolated_pixels_and_keeps_edges():
    a = np.zeros((40, 60), np.uint8)
    a[:, 30:] = 1                                                 # borde recto vertical entre 0 y 1
    clean = a.copy()
    rng = np.random.default_rng(1)
    ys, xs = rng.integers(3, 37, 25), rng.integers(3, 27, 25)
    a[ys, xs] = 2                                                 # «sal y pimienta» dentro de la zona 0 (a ≥ 3 px del borde)
    a[ys[:5], xs[:5] + 31] = 2
    out = mode_filter(a, 3, 5)
    assert (out == clean).all()                                   # el ruido desaparece y el borde no se mueve
    assert ((out == 2).sum() == 0)
    blob = clean.copy()
    blob[10:20, 5:15] = 2                                         # una isla de 10×10 NO es ruido: se queda
    assert (mode_filter(blob, 3, 5)[12:18, 7:13] == 2).all()
    assert mode_filter(blob, 3, 5, block=7).tolist() == mode_filter(blob, 3, 5, block=256).tolist()   # por bloques da lo mismo


def test_atlas_never_exceeds_2048():
    for (h, w) in ((4096, 4096), (4100, 3000), (3000, 5000), (100, 120)):
        img = np.zeros((h, w, 3), np.uint8)
        img[:, : w // 2] = (200, 30, 30)
        img[:, w // 2:] = (30, 30, 200)
        p = propose(quad(img), None, k=3, n_samples=20_000)
        assert max(p.labels_atlas.shape) <= MAX_ATLAS and max(p.raw_atlas.shape) <= MAX_ATLAS, (h, w)
        assert p.labels_atlas.shape == p.raw_atlas.shape
        pkg = build_package(quad(img), p, {i: f"r{i}" for i in range(p.k)}, max_atlas=512)
        assert max(pkg.labels.shape) <= 512
    p = propose(quad(img), None, k=3, n_samples=20_000)
    assert p.k <= 3 and p.share[:2].sum() > 99.0                  # dos colores reales; un tercer cluster, si sale, es el mezclado del borde (bilineal)
    assert {tuple(int(x) for x in c) for c in p.centers_rgb[:2]} == {(200, 30, 30), (30, 30, 200)}


def test_deterministic_with_seed():
    tm = repaint(doll_textured(), pierna_izq=(150, 150, 150), pierna_der=(190, 190, 190))
    a, b = propose(tm, None, k=6, seed=3), propose(tm, None, k=6, seed=3)
    assert (a.centers_rgb == b.centers_rgb).all() and (a.labels_atlas == b.labels_atlas).all() and (a.share == b.share).all()


@SLOW
def test_real_guemes_roles_in_under_30s():
    from afm.fbxio import load_textured

    tm = load_textured(os.path.join(ROOT, "examples", "guemes_segmentado_lq_texturas.fbx"))
    t0 = time.time()
    p = propose(tm, None, k=10)
    dt = time.time() - t0
    changed = float((p.raw_atlas != p.labels_atlas).mean())
    print(f"\npropose Güemes K=10: {dt:.1f} s; atlas {p.labels_atlas.shape}; filtro cambia {100 * changed:.2f} % de los píxeles")
    print("clusters (rgb, % área):", [(tuple(int(x) for x in c), round(float(s), 1)) for c, s in zip(p.centers_rgb, p.share)])
    assert dt < 30 and max(p.labels_atlas.shape) <= MAX_ATLAS
    assert changed < 0.02
