"""T37 · Cabeza real de Güemes con su propio FBX (`examples/guemes_cabeza_textura.fbx`) sobre la figura de Güemes. Lento: AFM_SLOW=1.

El STL de la cabeza no está en el repo: se arma con la misma malla del FBX (la cabeza «real» es la del FBX). La base de esa cabeza no es plana
(termina en el mentón), así que se corta con un plano de 3 puntos a la altura de la mandíbula (y = −28 en el FBX, Y arriba); sin ese corte la
base que mide `fit_replacement` es la punta del mentón y la cabeza sale del tamaño del FBX (≈ 105 mm)."""
import importlib.util
import os

import numpy as np
import pytest
import trimesh

from afm.cli import run
from afm.colorbake import texel_points
from afm.fbxio import load_textured
from afm.head import HeadSpec
from afm.planes import Plane

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STL = os.path.join(ROOT, "examples", "guemes_segmentado_lq_fixed_resized.stl")
FBX_FIG = os.path.join(ROOT, "examples", "guemes_segmentado_lq_texturas.fbx")
FBX_HEAD = os.path.join(ROOT, "examples", "guemes_cabeza_textura.fbx")
COLOR = {"fbx": FBX_FIG, "texel_mm": 0.2, "eps": 0.15, "fill": "extend", "symmetric": False}
NECK_Z = 94.0          # plano del cuello en la figura (mm, Z arriba)
CUT_Y = -28.0          # plano que corta la base de la cabeza en el FBX (Y arriba)
MIN_PCT_ORIGEN = 85.0  # umbral de T32/T34 (plan §5), sin relajar (R8)

pytestmark = [
    pytest.mark.skipif(os.environ.get("AFM_SLOW") != "1", reason="AFM_SLOW=1 para correr Güemes con la cabeza real (≈ 45 s)"),
    pytest.mark.skipif(not all(os.path.isfile(p) for p in (STL, FBX_FIG, FBX_HEAD)), reason="faltan los archivos de ejemplo de Güemes"),
]


def _project(head_stl: str, yaw: float):
    spec = importlib.util.spec_from_file_location("guemes_example", os.path.join(ROOT, "examples", "guemes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    proj = mod.proj
    proj.head = HeadSpec(Plane.from_normal((0, 0, NECK_Z), (0, 0, 1), "cabeza cuerpo"), replacement_path=head_stl, replacement_up="+Y",
                         replacement_yaw=yaw, replacement_color_path=FBX_HEAD,
                         replacement_cut_points=((0, CUT_Y, 0), (10, CUT_Y, 0), (0, CUT_Y, 10)))
    return proj


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("cabeza_real")
    tm = load_textured(FBX_HEAD)
    head_stl = str(d / "cabeza_guemes.stl")
    trimesh.Trimesh(tm.vertices, tm.faces, process=False).export(head_stl)
    keep: dict = {}
    info = run(STL, _project(head_stl, 180.0), str(d / "out"), "guemes", progress=lambda *_: None, keep=keep, color=COLOR)
    return {"info": info, "keep": keep, "out": d / "out"}


def test_head_comes_from_its_own_fbx_with_enough_origin(built):
    piezas = built["info"]["color"]["salida"]["piezas"]
    cab = piezas["cabeza"]
    print(f"\ncabeza: fuente={cab['fuente']} estado={cab['estado']} pct_origen={cab['pct_origen']} atlas={cab['atlas']} {cab['segundos']} s")
    assert cab["fuente"] == "pieza" and cab["estado"] != "sin_origen"
    assert cab["pct_origen"] >= MIN_PCT_ORIGEN, cab
    assert all(p["fuente"] == "figura" for k, p in piezas.items() if k != "cabeza")      # el resto sigue con el FBX de la figura
    assert built["info"]["color"]["cabeza"]["alineacion"]["ok"]
    assert built["info"]["validacion"]["ok"]


def test_head_has_the_size_of_a_head_not_of_the_fbx(built):
    h = trimesh.load(str(built["out"] / "piezas" / "guemes_cabeza.stl"))
    alto = float(np.ptp(h.vertices[:, 2]))
    assert 15 < alto < 40, alto              # sin el corte de la base salían ≈ 105 mm


def test_head_looks_forward_like_the_body(built):
    """La cara (piel) cae del lado +Y, donde está el frente del cuerpo, y el pelo (oscuro) detrás."""
    unw, bk, reg, _ = built["keep"]["colorizer"].pieces["cabeza"]
    grid = texel_points(unw)
    rgb = bk.atlas[grid.rows, grid.cols].astype(int)
    P = np.asarray(grid.points, float)
    alto = P[:, 2] > P[:, 2].min() + 8          # sin el pasador ni la base
    skin = alto & (rgb[:, 0] > 130) & (rgb[:, 0] - rgb[:, 2] > 40)
    hair = alto & (rgb.max(1) < 90)
    assert skin.sum() > 500 and hair.sum() > 500, (skin.sum(), hair.sum())
    assert P[skin, 1].mean() > P[hair, 1].mean() + 1.0, (P[skin, 1].mean(), P[hair, 1].mean())


def test_head_orientation_is_stable_against_a_wrong_yaw(built, tmp_path):
    """Control negativo: con yaw 0 la cara cae atrás. La prueba anterior detecta ese error."""
    tm = load_textured(FBX_HEAD)
    head_stl = str(tmp_path / "cabeza.stl")
    trimesh.Trimesh(tm.vertices, tm.faces, process=False).export(head_stl)
    keep: dict = {}
    run(STL, _project(head_stl, 0.0), str(tmp_path / "out"), "guemes", progress=lambda *_: None, keep=keep, color=COLOR)
    unw, bk, _, _ = keep["colorizer"].pieces["cabeza"]
    grid = texel_points(unw)
    rgb = bk.atlas[grid.rows, grid.cols].astype(int)
    P = np.asarray(grid.points, float)
    alto = P[:, 2] > P[:, 2].min() + 8
    skin = alto & (rgb[:, 0] > 130) & (rgb[:, 0] - rgb[:, 2] > 40)
    hair = alto & (rgb.max(1) < 90)
    assert P[skin, 1].mean() < P[hair, 1].mean()
