"""T45 · Recolor rápido de lo ya horneado (`BakeResult.labels` + `Colorizer.recolor`).

Sobre el muñeco con FBX sintético y color por roles (rojo / azul): se hornea una vez, se cambia un color de la paleta con `recolor` y se
compara, píxel por píxel, contra volver a correr todo el color con esa paleta. Los STL no cambian nunca por el color: se comparan por bytes.
"""
import dataclasses
import hashlib
import json
import os
import shutil
import time

import numpy as np
import pytest

from afm import cli
from afm.catalog import Catalog
from afm.colorbake import dilate
from afm.colorroles import build_package, propose
from afm.palette import Palette, PaletteError, Role
from afm.project import demo_project
from afm.testfigure import make_doll
from color_helpers import doll_params, doll_textured

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "..", "catalogo", "ejemplo")
FIG = "muneco"
CHANGE = {"rojo": "#00FF00"}                                   # el cambio que se prueba: rojo -> verde
FILL = "#112233"
FLAT_RGB = (0x11, 0x22, 0x33)
GREEN = (0, 255, 0)


def palette() -> Palette:
    return Palette([Role("rojo", "Rojo", "#C81E1E", ["#C81E1E"]), Role("azul", "Azul", "#1E3CC8", ["#1E3CC8"])])


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def stl_hashes(out) -> dict:
    res = {}
    for base, _dirs, names in os.walk(out):
        for n in names:
            if n.endswith(".stl"):
                res[os.path.relpath(os.path.join(base, n), out)] = _sha(os.path.join(base, n))
    return res


def png_hashes(out, ext: tuple = (".png",)) -> dict:
    """Hash de cada archivo de `piezas_color/` con una de las extensiones `ext` (por defecto, solo los PNG)."""
    d = os.path.join(out, "piezas_color")
    return {n: _sha(os.path.join(d, n)) for n in sorted(os.listdir(d)) if n.endswith(ext)}


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("recolor")
    stl = str(d / "muneco.stl")
    make_doll().export(stl)
    pal = palette()
    tm = doll_textured()
    prop = propose(tm, pal, k=10)
    assign = {i: ("rojo" if c[0] > c[2] else "azul") for i, c in enumerate(prop.centers_rgb)}
    npz = build_package(tm, prop, assign, origen={"nombre": "muneco.fbx", "sha256": "ab" * 32}).save(str(d / "muneco.color.npz"))
    line = str(d / "linea")
    shutil.copytree(EXAMPLES, line, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    Catalog.open(line).set_palette(pal)
    return {"dir": d, "stl": stl, "npz": npz, "line": line}


def _run(files, name, **color):
    keep: dict = {}
    out = str(files["dir"] / name)
    pr = demo_project()
    pr.params = doll_params()
    pr.color = None
    info = cli.run(files["stl"], pr, out, FIG, progress=lambda *_: None, keep=keep, catalog_root=files["line"],
                   color={"roles": files["npz"], "texel_mm": 0.4, **color})
    return info, keep["colorizer"], out


def _snapshot(cz) -> dict:
    return {n: (bk.atlas.copy(), bk.mask.copy(), bk.labels.copy()) for n, (_u, bk, _r, _i) in cz.pieces.items()}


@pytest.fixture(scope="module", params=["extend", FILL], ids=["relleno_extend", "relleno_plano"])
def case(request, files):
    """Una corrida horneada, su recolor y, aparte, la misma corrida con el cambio desde cero (rehorneo completo)."""
    fill = request.param
    tag = "ext" if fill == "extend" else "fill"
    info, cz, out = _run(files, f"out_{tag}", fill=fill)
    before = _snapshot(cz)
    stl_before = stl_hashes(out)
    png_before = png_hashes(out)
    geom_before = png_hashes(out, (".obj", ".mtl"))
    t0 = time.perf_counter()
    res = cz.recolor(palette(), CHANGE)
    seconds = time.perf_counter() - t0
    f_info, f_cz, f_out = _run(files, f"out_{tag}_full", fill=fill, paleta_override=CHANGE)
    return {"fill": fill, "info": info, "cz": cz, "out": out, "before": before, "stl_before": stl_before, "png_before": png_before, "geom_before": geom_before,
            "res": res, "seconds": seconds, "full_info": f_info, "full": f_cz, "full_out": f_out}


# ------------------------------------------------------------------------------------------ igualdad con el rehorneo
def test_recolor_equals_full_rebake_pixel_for_pixel(case):
    cz, full = case["cz"], case["full"]
    assert sorted(cz.pieces) == sorted(full.pieces) and cz.pieces
    assert case["res"]["piezas"] == sorted(cz.pieces) and case["res"]["sin_etiquetas"] == []
    changed = 0
    for name in cz.pieces:
        bk, fbk = cz.pieces[name][1], full.pieces[name][1]
        assert np.array_equal(bk.atlas, fbk.atlas), name
        assert np.array_equal(bk.mask, fbk.mask) and np.array_equal(bk.labels, fbk.labels), name
        changed += int(not np.array_equal(bk.atlas, case["before"][name][0]))
    assert changed > 0                                                          # el cambio se vio en alguna pieza
    assert png_hashes(case["out"]) == png_hashes(case["full_out"])             # y los PNG reescritos son los del rehorneo, byte a byte
    assert png_hashes(case["out"]) != case["png_before"]


def test_recolor_only_changes_the_role_that_changed(case):
    for name, (_u, bk, _r, _i) in case["cz"].pieces.items():
        a0, m0, l0 = case["before"][name]
        roles = bk.label_roles
        assert sorted(roles) == ["azul", "rojo"]
        sel = bk.mask & (bk.labels != 255)
        red = sel & (bk.labels == roles.index("rojo"))
        blue = sel & (bk.labels == roles.index("azul"))
        assert np.array_equal(bk.labels, l0) and np.array_equal(bk.mask, m0)    # las etiquetas no se tocan
        assert (bk.atlas[red] == GREEN).all() and (bk.atlas[blue] == a0[blue]).all()
        assert (bk.atlas[~sel] == a0[~sel]).all()                               # sin rol (plano) y fuera del atlas: iguales


def test_flat_fill_stays_flat_after_recolor(case):
    if case["fill"] == "extend":
        pytest.skip("solo con relleno plano")
    flat_seen = 0
    for name, (_u, bk, _r, _i) in case["cz"].pieces.items():
        flat = bk.mask & (bk.labels == 255)
        flat_seen += int(flat.sum())
        assert (bk.atlas[flat] == FLAT_RGB).all(), name
        assert (case["before"][name][0][flat] == FLAT_RGB).all(), name         # y ya era así antes del recolor
    assert flat_seen > 0                                                        # la prueba ejercita texeles planos de verdad


def test_recolor_keeps_obj_and_mtl_byte_identical(case):
    """La geometría y los nombres de los PNG no cambian con el color: los OBJ/MTL no se reescriben y son los del rehorneo."""
    after = png_hashes(case["out"], (".obj", ".mtl"))
    assert after == case["geom_before"] and len(after) == 2 * (len(case["cz"].pieces) + 1)      # un OBJ y un MTL por pieza + el ensamblado
    assert after == png_hashes(case["full_out"], (".obj", ".mtl"))


def test_recolor_under_one_second_with_files_rewritten(case):
    assert case["seconds"] < 1.0, case["seconds"]                               # incluye reescribir PNG, GLB e informe
    assert case["res"]["segundos"] <= case["seconds"] + 0.01


# ------------------------------------------------------------------------------------------ salidas
def test_recolor_leaves_stl_untouched_and_rewrites_report(case):
    assert stl_hashes(case["out"]) == case["stl_before"] and case["stl_before"]
    assert stl_hashes(case["out"]) == stl_hashes(case["full_out"])
    rep = json.load(open(os.path.join(case["out"], f"{FIG}_color_informe.json")))
    full = json.load(open(os.path.join(case["full_out"], f"{FIG}_color_informe.json")))
    assert rep["paleta"] == full["paleta"] and rep["paleta"]["cambios"] == CHANGE
    assert rep["paleta"]["hash"] == palette().with_overrides(CHANGE).digest() != case["info"]["color"]["paleta"]["hash"]
    assert os.path.getsize(os.path.join(case["out"], f"{FIG}_ensamblada.glb")) > 0
    json.dumps(case["res"], default=float)                                      # R5


# ------------------------------------------------------------------------------------------ sin etiquetas / errores
def test_dilate_copies_label_not_blend():
    labels = np.full((1, 9), 255, np.uint8)
    mask = np.zeros((1, 9), bool)
    labels[0, 1], labels[0, 7] = 1, 3
    mask[0, 1] = mask[0, 7] = True
    out, grown = dilate(labels, mask, 4)
    assert set(np.unique(out[0, grown[0]])) <= {1, 3}                           # ningún 2 (promedio) ni 255 dentro de lo dilatado
    assert out[0, 0] == 1 and out[0, 2] == 1 and out[0, 6] == 3 and out[0, 8] == 3
    assert out[0, 1] == 1 and out[0, 7] == 3 and out.dtype == np.uint8


def test_legacy_bake_has_no_labels_and_recolor_leaves_it_alone(case):
    """Una pieza cuya fuente es RGB (sin etiquetas) sale como antes (`labels` None) y `recolor` no la toca."""
    cz = case["cz"]
    name = sorted(cz.pieces)[0]
    unw, bk, reg, info = cz.pieces[name]
    legacy = dataclasses.replace(bk, labels=None, label_roles=None)
    assert legacy.labels is None and legacy.label_roles is None
    saved = cz.pieces[name]
    try:
        cz.pieces[name] = (unw, legacy, reg, info)
        atlas = legacy.atlas.copy()
        res = cz.recolor(palette(), CHANGE, rewrite=False)
        assert res["sin_etiquetas"] == [name] and name not in res["piezas"]
        assert np.array_equal(cz.pieces[name][1].atlas, atlas)
    finally:
        cz.pieces[name] = saved


def test_missing_role_changes_nothing(case):
    cz = case["cz"]
    before = _snapshot(cz)
    report = dict(cz.palette_report)
    with pytest.raises(PaletteError, match="azul"):
        cz.recolor(Palette([palette().roles[0]]), rewrite=False)                # a la paleta le falta «azul»
    with pytest.raises(PaletteError, match="no existen"):
        cz.recolor(palette(), {"verde": "#00FF00"}, rewrite=False)              # un cambio sobre un rol que no existe
    after = _snapshot(cz)
    assert before.keys() == after.keys()
    for n in before:
        assert all(np.array_equal(x, y) for x, y in zip(before[n], after[n])), n
    assert cz.palette_report == report


def test_recolor_without_a_previous_write_does_not_write():
    from afm.colorexport import Colorizer

    cz = Colorizer(None, doll_params())
    res = cz.recolor(palette(), CHANGE)
    assert res["piezas"] == [] and "salida" not in res and cz._last_write is None
    assert cz.palette_report["cambios"] == CHANGE


def test_resolved_sources_are_recolored_too(case):
    """La fuente de la figura sigue en memoria: una pieza que se hornee después sale con la paleta nueva."""
    src = case["cz"].src
    assert src is not None and src.sample_label is not None
    used = {tuple(int(x) for x in c) for c in np.unique(src.sample_rgb, axis=0)}
    assert GREEN in used and (200, 30, 30) not in used and (30, 60, 200) in used
