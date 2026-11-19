"""T21 · Modo A: el FBX es la entrada única (geometría + fuente de color)."""
import json
import os

import numpy as np
import pytest

from afm import cli, meshio
from afm.colorexport import COLOR_DIR
from afm.project import demo_project
from afm.testfigure import make_doll
from color_helpers import doll_params, doll_textured, piece_trimeshes, write_doll_fbx
from fbx_synth import write_fbx

FIG = "muneco"


def _project():
    pr = demo_project()
    pr.params = doll_params()
    return pr


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("modo_a")
    stl = str(d / "muneco.stl")
    make_doll().export(stl)
    return {"fbx": write_doll_fbx(d / "muneco.fbx"), "stl": stl, "dir": d}


@pytest.fixture(scope="module")
def run_a(files):
    keep: dict = {}
    info = cli.run(files["fbx"], _project(), str(files["dir"] / "out_a"), FIG, progress=lambda *_: None, keep=keep,
                   color={"texel_mm": 0.4})
    return info, keep, files["dir"] / "out_a"


@pytest.fixture(scope="module")
def run_b(files):
    keep: dict = {}
    info = cli.run(files["stl"], _project(), str(files["dir"] / "out_b"), FIG, progress=lambda *_: None, keep=keep)
    return info, keep, files["dir"] / "out_b"


def test_load_figure_accepts_fbx(files):
    mesh, rep = meshio.load_figure(files["fbx"], 127.0)
    assert rep.color_source_path == os.path.abspath(files["fbx"])
    assert mesh.is_watertight and abs(mesh.bounds[0, 2]) < 1e-6
    assert mesh.extents[2] == pytest.approx(127.0, rel=1e-3)
    assert meshio.load_figure(files["stl"], 127.0)[1].color_source_path == ""        # el STL no deja fuente


def test_mode_a_matches_mode_b_geometry(run_a, run_b):
    va = {n: m.volume for n, m in piece_trimeshes(run_a[1]["res"]).items()}
    vb = {n: m.volume for n, m in piece_trimeshes(run_b[1]["res"]).items()}
    assert sorted(va) == sorted(vb)
    for n in va:
        assert va[n] == pytest.approx(vb[n], rel=0.005), (n, va[n], vb[n])


def test_mode_a_colors_the_pieces(run_a):
    info, keep, out = run_a
    col = info["color"]
    assert col["alineacion"] == {"modo": "A", "ok": True}
    assert sorted(col["salida"]["piezas"]) == sorted(keep["res"].pieces)
    assert col["salida"]["sin_origen"] == []
    assert os.path.isdir(out / COLOR_DIR) and os.path.isfile(out / f"{FIG}_ensamblada.glb")


def test_mode_a_without_texture_runs_without_color(files):
    tm = doll_textured()
    path = str(files["dir"] / "sin_textura.fbx")
    n = len(tm.faces)
    write_fbx(path, tm.vertices, tm.faces.tolist(), np.arange(3 * n), np.asarray(tm.corner_uv, float).reshape(-1, 2), texture=None)
    msgs = []
    out = files["dir"] / "out_sin_textura"
    info = cli.run(path, _project(), str(out), FIG, progress=msgs.append)
    assert info["color"]["omitido"] == "sin_textura" and "salida" not in info["color"]
    assert not (out / COLOR_DIR).exists() and info["validacion"]["ok"]


def test_sin_color_uses_fbx_only_as_geometry(files):
    out = files["dir"] / "out_sin_color"
    info = cli.run(files["fbx"], _project(), str(out), FIG, progress=lambda *_: None, sin_color=True)
    assert "color" not in info and not (out / COLOR_DIR).exists()


def test_remeshed_raises_eps(files, monkeypatch):
    real = meshio.load_figure
    seen: dict = {}

    def fake_load(*a, **k):
        mesh, rep = real(*a, **k)
        rep.remeshed = True
        return mesh, rep

    def fake_make(color, mesh, params, progress=print, post=None, frame=None):
        seen["eps"] = color["eps"]
        return None, {"omitido": "prueba"}
    monkeypatch.setattr(meshio, "load_figure", fake_load)
    monkeypatch.setattr(cli, "make_colorizer", fake_make)
    msgs = []
    cli.run(files["fbx"], _project(), str(files["dir"] / "out_remalla"), FIG, progress=msgs.append, color={"eps": 0.15})
    assert seen["eps"] == 0.3 and any("0.3" in str(m) for m in msgs)


def test_stl_flow_unchanged(run_b, files):
    info, _keep, out = run_b
    assert "color" not in info and not (out / COLOR_DIR).exists()
    assert info["carga"]["color_source_path"] == ""
    out2 = files["dir"] / "out_b_color"                                                   # modo B sigue andando
    info2 = cli.run(files["stl"], _project(), str(out2), FIG, progress=lambda *_: None,
                    color={"fbx": files["fbx"], "texel_mm": 0.4})
    assert info2["color"]["alineacion"]["ok"] and "modo" not in info2["color"]["alineacion"]


def test_report_with_fbx_is_json_serializable(run_a):
    info, _keep, out = run_a
    json.dumps(info, default=float)
    with open(out / f"{FIG}_informe.json", encoding="utf-8") as fh:
        assert json.load(fh)["carga"]["color_source_path"].endswith("muneco.fbx")
