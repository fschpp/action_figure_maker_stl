"""T19 · Egon (Ghostbusters, `examples/kenner-real_ghostbusters-egon.proyecto`) con color: figura real con `limbs_right` y cabeza.

Es la prueba real de la asimetría: el proyecto trae cortes propios del lado derecho. Lento (dos corridas de ≈ 70–85 s): `AFM_SLOW=1`.
Medidas al escribirla (1 núcleo, texel 0,2 mm): sin color 70,9 s · con color 86,2 s (extra 15 s) · alineación FBX→STL media 0,025 mm,
p95 0,052, máx. 0,120, sin ICP · 6 piezas, ninguna `sin_origen` (torso 56,9 %, brazo izq. 74,6 %, brazo der. 88,5 %, cabeza 87,1 %,
piernas 86–88 %) · STL idénticos byte a byte.
"""
import hashlib
import os
import time

import numpy as np
import pytest

from afm.cli import run
from afm.colorexport import COLOR_DIR, Colorizer
from afm.project import Project
from color_helpers import piece_trimeshes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples")
STL = os.path.join(EX, "egon_fixed.stl")
FBX = os.path.join(EX, "egon_lq_textura.fbx")
PROYECTO = os.path.join(EX, "kenner-real_ghostbusters-egon.proyecto")
COLOR = {"fbx": FBX, "texel_mm": 0.2, "eps": 0.15, "fill": "extend", "symmetric": False}
PIEZAS = ["brazo_der", "brazo_izq", "cabeza", "pierna_der", "pierna_izq", "torso"]

pytestmark = [
    pytest.mark.skipif(os.environ.get("AFM_SLOW") != "1", reason="AFM_SLOW=1 para correr Egon con color (≈ 3 min)"),
    pytest.mark.skipif(not all(os.path.isfile(f) for f in (STL, FBX, PROYECTO)), reason="faltan los archivos de ejemplo de Egon"),
]


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16]


def _stl_hashes(outdir):
    out = {f: _sha(os.path.join(outdir, "piezas", f)) for f in sorted(os.listdir(os.path.join(outdir, "piezas")))}
    out["ensamblada"] = _sha(os.path.join(outdir, "egon_ensamblada.stl"))
    return out


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    plain, colored = tmp_path_factory.mktemp("egon_sin_color"), tmp_path_factory.mktemp("egon_con_color")
    t0 = time.perf_counter()
    run(STL, Project.load(PROYECTO), str(plain), "egon", progress=lambda *_: None)
    t_plain = time.perf_counter() - t0
    keep: dict = {}
    t0 = time.perf_counter()
    info = run(STL, Project.load(PROYECTO), str(colored), "egon", progress=lambda *_: None, keep=keep, color=COLOR)
    t_color = time.perf_counter() - t0
    return {"plain": plain, "colored": colored, "info": info, "keep": keep, "t_plain": t_plain, "t_color": t_color}


def test_egon_is_asymmetric_and_has_six_pieces(runs):
    res = runs["keep"]["res"]
    assert res.right is not None                                              # el proyecto trae `limbs_right`
    assert sorted(runs["keep"]["colorizer"].pieces) == PIEZAS


def test_egon_alignment_with_the_stl_is_exact_without_icp(runs):
    al = runs["info"]["color"]["alineacion"]
    assert al["ok"] and al["iteraciones"] == 0, al
    assert al["media_mm"] <= 0.05 and al["max_mm"] <= 0.3, al                 # medido 0,025 / 0,120


def test_egon_every_piece_registers_with_origin(runs):
    cz = runs["keep"]["colorizer"]
    for n, (_u, _b, reg, info) in cz.pieces.items():
        assert reg.status != "sin_origen", (n, reg.status)
        assert info["pct_origen"] >= 50.0, (n, info["pct_origen"])           # medido: torso 56,9 % (el mínimo), el resto ≥ 74 %
    assert runs["info"]["color"]["sin_origen"] == []
    legs = {n: cz.pieces[n][3] for n in ("pierna_izq", "pierna_der")}
    assert legs["pierna_izq"]["angulo"] == -90.0 and legs["pierna_der"]["angulo"] == 90.0     # el lado derecho refleja el giro
    assert all(i["estado"] == "semilla" for i in legs.values())


def test_egon_stl_identical_to_run_without_color(runs):
    assert _stl_hashes(str(runs["plain"])) == _stl_hashes(str(runs["colored"]))
    assert os.path.isdir(runs["colored"] / COLOR_DIR) and not (runs["plain"] / COLOR_DIR).exists()
    assert os.path.isfile(runs["colored"] / "egon_ensamblada.glb")


def test_egon_color_time_under_60s(runs):
    extra = runs["t_color"] - runs["t_plain"]
    print(f"\nEgon sin color {runs['t_plain']:.1f} s · con color {runs['t_color']:.1f} s · extra {extra:.1f} s")
    assert extra <= 60.0, extra


def test_egon_right_side_keeps_its_own_color(runs):
    """Egon es asimétrico también en color: con `symmetric=True` el brazo derecho cambia en muchos texeles; el izquierdo no."""
    keep = runs["keep"]
    cz, res = keep["colorizer"], keep["res"]
    meshes = piece_trimeshes(res)
    sym = Colorizer(cz.src, cz.params, texel_mm=cz.texel_mm, symmetric=True)
    for n in ("brazo_izq", "brazo_der"):
        sym(n, meshes[n], res)
    diff = {}
    for n in ("brazo_izq", "brazo_der"):
        a, b = cz.pieces[n][1], sym.pieces[n][1]
        assert a.atlas.shape == b.atlas.shape                                   # mismo despliegue: se pueden comparar texel a texel
        both = a.mask & b.mask
        diff[n] = float(np.mean(np.abs(a.atlas.astype(int) - b.atlas.astype(int)).max(axis=2)[both] > 40))
    assert diff["brazo_izq"] == 0.0, diff
    assert diff["brazo_der"] >= 0.10, diff                                      # medido 24,6 % de los texeles difiere más de 40/255
