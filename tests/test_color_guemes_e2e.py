"""T16 · Güemes con color de punta a punta: tiempo, solapes, STL intactos, colores en puntos conocidos y tamaños de atlas. Lento: AFM_SLOW=1."""
import hashlib
import importlib.util
import json
import os
import time

import numpy as np
import pytest

from afm import meshio
from afm.cli import run
from afm.colorbake import texel_points
from afm.colorexport import COLOR_DIR
from afm.fbxio import load_textured
from afm.frame import FigureFrame
from afm.unwrap import check_no_overlap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STL = os.path.join(ROOT, "examples", "guemes_segmentado_lq_fixed_resized.stl")
FBX = os.path.join(ROOT, "examples", "guemes_segmentado_lq_texturas.fbx")
COLOR = {"fbx": FBX, "texel_mm": 0.2, "eps": 0.15, "fill": "extend", "symmetric": False}

pytestmark = [
    pytest.mark.skipif(os.environ.get("AFM_SLOW") != "1", reason="AFM_SLOW=1 para correr Güemes con color (≈ 40 s)"),
    pytest.mark.skipif(not (os.path.isfile(STL) and os.path.isfile(FBX)), reason="faltan los archivos de ejemplo de Güemes"),
]

# Puntos de la superficie de la fuente, en el marco de la figura (mm; Z arriba, pies en z = 0, centrada en X, frente +Y), con el
# color que tiene la textura en cada uno (medido al preparar la prueba; la prueba lo vuelve a leer de la textura, estos son solo
# referencia). Negro (sombrero, botas), piel (cara), dorado (charretera) y verde (puño).
# nombre: (pieza que lo contiene, (x, y, z), rgb de referencia)
KNOWN_POINTS = {
    "sombrero_negro": ("torso", (0.5, 6.3, 113.3), (52, 51, 46)),
    "cara_piel": ("torso", (0.1, 9.9, 101.4), (170, 133, 109)),
    "charretera_dorada": ("brazo_izq", (16.3, -0.1, 90.5), (157, 130, 76)),
    "puno_verde": ("brazo_izq", (23.2, -5.0, 62.9), (51, 69, 46)),
    "bota_izquierda": ("pierna_izq", (11.3, 9.1, 1.5), (36, 35, 33)),
}
COLOR_TOL = 40          # distancia máxima (por canal, /255) entre el atlas y la textura original
POINT_TOL_MM = 0.3      # distancia máxima del punto elegido a la superficie de la pieza


def _guemes_project():
    spec = importlib.util.spec_from_file_location("guemes_example", os.path.join(ROOT, "examples", "guemes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.proj


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16]


def _stl_hashes(outdir):
    out = {f: _sha(os.path.join(outdir, "piezas", f)) for f in sorted(os.listdir(os.path.join(outdir, "piezas")))}
    out["ensamblada"] = _sha(os.path.join(outdir, "guemes_ensamblada.stl"))
    return out


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    proj = _guemes_project()
    plain, colored = tmp_path_factory.mktemp("sin_color"), tmp_path_factory.mktemp("con_color")
    t0 = time.perf_counter()
    run(STL, proj, str(plain), "guemes", progress=lambda *_: None)
    t_plain = time.perf_counter() - t0
    keep: dict = {}
    t0 = time.perf_counter()
    info = run(STL, _guemes_project(), str(colored), "guemes", progress=lambda *_: None, keep=keep, color=COLOR)
    t_color = time.perf_counter() - t0
    return {"plain": plain, "colored": colored, "info": info, "keep": keep, "t_plain": t_plain, "t_color": t_color}


def test_guemes_color_time_under_60s(runs):
    extra = runs["t_color"] - runs["t_plain"]
    print(f"\nGüemes sin color {runs['t_plain']:.1f} s · con color {runs['t_color']:.1f} s · extra {extra:.1f} s")
    assert extra <= 60.0, extra
    assert runs["info"]["color"]["alineacion"]["ok"]


def test_guemes_no_overlapping_texels(runs):
    cz = runs["keep"]["colorizer"]
    assert sorted(cz.pieces) == ["brazo_der", "brazo_izq", "pierna_der", "pierna_izq", "torso"]
    for name, (unw, _bk, _reg, _info) in cz.pieces.items():
        assert check_no_overlap(unw) == 0, name


def test_guemes_stl_identical_to_run_without_color(runs):
    assert _stl_hashes(str(runs["plain"])) == _stl_hashes(str(runs["colored"]))
    assert os.path.isdir(runs["colored"] / COLOR_DIR) and not (runs["plain"] / COLOR_DIR).exists()
    assert os.path.isfile(runs["colored"] / "guemes_ensamblada.glb")


@pytest.fixture(scope="module")
def source_and_frame(runs):
    proj = _guemes_project()
    mesh, _rep = meshio.load_figure(STL, proj.params.ref_height, proj.crown_z)
    tm = load_textured(FBX)
    frame = FigureFrame.from_similarity(tm, mesh).then(FigureFrame.engine_post(proj.front, proj.symmetry))
    return tm, frame


def _original_color(tm, frame, p):
    """Color de la textura ORIGINAL en el punto de la fuente más cercano a `p` (distancia exacta punto-triángulo, muestreo bilineal)."""
    import trimesh

    V = frame.apply(tm.vertices)
    mesh = trimesh.Trimesh(V, tm.faces, process=False)
    q, d, tri = trimesh.proximity.closest_point(mesh, np.asarray([p], float))
    bary = trimesh.triangles.points_to_barycentric(V[tm.faces[tri]], q)
    return tm.sample(tri, bary)[0].astype(float), float(d[0])


def test_guemes_color_at_known_points(runs, source_and_frame):
    """El color horneado en la pieza, en 5 puntos elegidos por coordenada, coincide con el de la textura original (≤ 40/255 por canal)."""
    tm, frame = source_and_frame
    cz = runs["keep"]["colorizer"]
    for name, (piece, p, ref_rgb) in KNOWN_POINTS.items():
        unw, bk, reg, _info = cz.pieces[piece]
        want, dsrc = _original_color(tm, frame, p)
        assert dsrc < POINT_TOL_MM, (name, "el punto no está sobre la fuente", dsrc)
        assert np.abs(want - np.array(ref_rgb)).max() <= 25, (name, "la textura ya no tiene el color de referencia", want)
        # el punto del marco de reposo → la pose de la pieza (el registro lleva pieza → reposo)
        q = np.append(np.asarray(p, float), 1.0)
        piece_pt = (np.linalg.inv(reg.matrix) @ q)[:3]
        grid = texel_points(unw)
        from scipy.spatial import cKDTree

        d, i = cKDTree(grid.points).query(piece_pt)
        assert d < POINT_TOL_MM, (name, "ningún texel de la pieza cerca del punto", d)
        got = bk.atlas[grid.rows[i], grid.cols[i]].astype(float)
        assert np.abs(got - want).max() <= COLOR_TOL, (name, piece, got, want)


def test_guemes_atlas_sizes_recorded(runs):
    pieces = runs["info"]["color"]["salida"]["piezas"]
    cz = runs["keep"]["colorizer"]
    resumen = {}
    for name, p in pieces.items():
        w, h = p["atlas"]
        assert 0 < w <= 4096 and 0 < h <= 4096, (name, p["atlas"])
        assert abs(p["texel_mm"] - 0.2) < 1e-6, (name, "el atlas tuvo que agrandar el texel", p["texel_mm"])
        unw, bk, _reg, _i = cz.pieces[name]
        util = float(bk.stats["n_texeles"]) / (w * h)             # texeles consultados (islas sin dilatación) / píxeles del atlas
        resumen[name] = {"atlas": [w, h], "pct_origen": p["pct_origen"], "util_pct": round(100 * util, 1), "segundos": p["segundos"]}
    print("\nT16 atlas:", json.dumps(resumen))
    assert set(resumen) == set(pieces)
