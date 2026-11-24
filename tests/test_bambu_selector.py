"""Selector de colores y vista previa de la salida para Bambu Studio (`afm.bambu`, `Colorizer`, sin GTK)."""
import hashlib
import json
import os
import zipfile

import numpy as np
import pytest
import trimesh

from afm.bambu import (BambuOptions, Collected, build_from, candidates, default_selection, follow_roles, norm_hex, preview_parts)
from afm.colorexport import Colorizer
from afm.colorsrc import ColorSource
from color_helpers import doll_params, doll_pieces, doll_textured, piece_trimeshes

FIG = "muneco"
RED, BLUE, PALE = "#C81E1E", "#1E1EC8", "#2832BE"
ROLES = {"rojo": RED, "azul": BLUE}


def synthetic(role_hex=ROLES) -> Collected:
    """Una esfera de 80 caras: 50 rojas, 20 azules y 10 de un azul casi igual (el candidato que se funde al sacarlo)."""
    s = trimesh.creation.icosphere(subdivisions=1)
    raw = np.array([[200, 30, 30]] * 50 + [[30, 30, 200]] * 20 + [[40, 50, 190]] * 10, np.uint8)
    return Collected(["bola"], [raw], [np.ones(len(raw))], [(np.asarray(s.vertices, float), np.asarray(s.faces, np.int64))], True, dict(role_hex or {}))


def hexes(res):
    return ["#%02X%02X%02X" % tuple(int(x) for x in c) for c in res.colors]


# ------------------------------------------------------------------ opciones
def test_norm_hex():
    assert norm_hex("#c81e1e") == RED
    for bad in ("C81E1E", "#C81E1", "#GGGGGG", None):
        with pytest.raises(ValueError):
            norm_hex(bad)


def test_options_chosen_and_remap_are_normalized():
    o = BambuOptions.coerce({"chosen": ["#c81e1e", RED, "#1e1ec8"], "remap": {"#c81e1e": "#00ff00"}})
    assert o.chosen == (RED, BLUE) and o.remap == {RED: "#00FF00"}
    assert BambuOptions.coerce(True).chosen is None and BambuOptions.coerce(True).remap == {}
    for bad in ({"chosen": []}, {"chosen": ["rojo"]}, {"remap": {RED: "verde"}}):
        with pytest.raises(ValueError):
            BambuOptions.coerce(bad)


# ------------------------------------------------------------------ candidatos
def test_candidates_are_sorted_with_area_and_roles():
    c = candidates(synthetic())
    assert [x["hex"] for x in c] == [RED, BLUE, PALE]                       # del más al menos usado
    assert [x["area_pct"] for x in c] == [62.5, 25.0, 12.5] and [x["caras"] for x in c] == [50, 20, 10]
    assert c[0]["roles"] == ["rojo"] and c[1]["roles"] == ["azul"] and c[2]["roles"] == []
    assert candidates(synthetic(role_hex=None))[0]["roles"] == []
    assert candidates(Collected([], [], [], [], True)) == []


def test_default_selection_follows_cap_and_min_area():
    c = candidates(synthetic())
    assert default_selection(c) == [RED, BLUE, PALE]
    assert default_selection(c, max_colors=2) == [RED, BLUE]
    assert default_selection(c, min_area_pct=20.0) == [RED, BLUE]
    assert default_selection(c, min_area_pct=99.0) == [RED]                 # siempre queda al menos uno
    assert default_selection([]) == []


# ------------------------------------------------------------------ selección manual
def test_auto_is_what_it_was_before():
    res = build_from(synthetic(), None, BambuOptions(despeckle=0))
    assert hexes(res) == [RED, BLUE, PALE] and res.area_pct == [62.5, 25.0, 12.5] and res.merged == 0


def test_chosen_merges_removed_colors_into_the_closest():
    res = build_from(synthetic(), None, BambuOptions(despeckle=0, chosen=(RED, BLUE)))        # sin el azul pálido: sus 10 caras pasan al azul
    assert hexes(res) == [RED, BLUE] and res.area_pct == [62.5, 37.5] and res.merged == 1 and res.faces == [50, 30]
    one = build_from(synthetic(), None, BambuOptions(despeckle=0, chosen=(PALE,)))             # un solo color: todo pasa a él
    assert hexes(one) == [PALE] and one.area_pct == [100.0]


def test_chosen_ignores_colors_that_are_not_candidates():
    auto = build_from(synthetic(), None, BambuOptions(despeckle=0))
    stale = build_from(synthetic(), None, BambuOptions(despeckle=0, chosen=("#010203",)))     # un HEX viejo (p. ej. tras recolorear): vuelve al automático
    assert hexes(stale) == hexes(auto)
    mixed = build_from(synthetic(), None, BambuOptions(despeckle=0, chosen=("#010203", RED)))
    assert hexes(mixed) == [RED]


def test_remap_changes_only_the_written_color():
    res = build_from(synthetic(), None, BambuOptions(despeckle=0, chosen=(RED, BLUE, PALE), remap={RED: "#00FF00"}))
    assert hexes(res) == ["#00FF00", BLUE, PALE] and res.area_pct == [62.5, 25.0, 12.5]
    auto = build_from(synthetic(), None, BambuOptions(despeckle=0, max_colors=2, remap={RED: "#00FF00"}))     # sin selección: sale de la automática
    assert hexes(auto)[0] == "#00FF00" and len(auto.colors) == 2


def test_preview_parts_one_flat_mesh_per_piece_and_color():
    res = build_from(synthetic(), None, BambuOptions(despeckle=0))
    parts = preview_parts(res)
    assert len(parts) == 3 and sum(len(m.faces) for _n, m, _c in parts) == 80
    assert sorted(tuple(round(x * 255) for x in c[:3]) for _n, _m, c in parts) == sorted([(200, 30, 30), (30, 30, 200), (40, 50, 190)])
    assert all(c[3] == 1.0 for _n, _m, c in parts)


def test_follow_roles_moves_chosen_and_remap_to_the_new_role_color():
    chosen, remap = follow_roles({"rojo": RED, "azul": BLUE}, {"rojo": "#112233", "azul": BLUE}, (RED, BLUE), {RED: "#00FF00"})
    assert chosen == ("#112233", BLUE) and remap == {"#112233": "#00FF00"}
    assert follow_roles({"rojo": RED}, {"rojo": RED}, None, {}) == (None, {})                  # nada cambió
    assert follow_roles({"rojo": RED}, {"verde": "#00FF00"}, [RED], {}) == ([RED], {})         # un rol que ya no está: no se toca


# ------------------------------------------------------------------ Colorizer sobre el muñeco de pruebas (color por textura RGB)
def sha(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def materials(d) -> list:
    with zipfile.ZipFile(os.path.join(d, f"{FIG}_bambu.3mf")) as z:
        xml = z.read("3D/3dmodel.model").decode()
    return [c[:7] for c in xml.split('displaycolor="')[1:]]


@pytest.fixture(scope="module")
def baked(tmp_path_factory):
    params = doll_params()
    res = doll_pieces(params)
    cz = Colorizer(ColorSource(doll_textured(), spacing_mm=0.1), params, texel_mm=0.3, bambu=3)
    for n, m in piece_trimeshes(res).items():
        cz(n, m, res)
    d = tmp_path_factory.mktemp("bambu_sel")
    return d, cz.write(str(d), FIG), cz


def test_colorizer_candidates_are_cached_and_sorted(baked):
    _d, _out, cz = baked
    c = cz.bambu_candidates()
    assert len(c) >= 2 and [x["area_pct"] for x in c] == sorted((x["area_pct"] for x in c), reverse=True)
    assert abs(sum(x["area_pct"] for x in c) - 100.0) < 0.5 and all(x["roles"] == [] for x in c)      # fuente RGB: sin roles
    assert cz.bambu_collected() is cz.bambu_collected()


def test_colorizer_preview_writes_nothing_and_follows_the_options(baked):
    d, out, cz = baked
    before = sorted(os.listdir(d))
    c = cz.bambu_candidates()
    res = cz.bambu_preview({"chosen": [c[0]["hex"]]})
    assert len(res.colors) == 1 and res.area_pct == [100.0]
    assert len(cz.bambu_preview({"max_colors": 2}).colors) <= 2
    assert sorted(os.listdir(d)) == before and sha(d / out["bambu"]["3mf"]) == sha(d / out["bambu"]["3mf"])


def test_write_bambu_only_touches_the_bambu_files(baked):
    d, out, cz = baked
    glb, rep_before = sha(d / out["glb"]), json.load(open(d / out["informe"]))
    cands = cz.bambu_candidates()
    new = "#00FF00"
    rep = cz.write_bambu({"chosen": [cands[0]["hex"], cands[1]["hex"]], "remap": {cands[0]["hex"]: new}})
    assert new in [c["hex"] for c in rep["colores"]] and new in materials(d) and len(rep["colores"]) == 2
    assert rep["opciones"]["colores_elegidos"] == [cands[0]["hex"], cands[1]["hex"]] and rep["opciones"]["cambios"] == {cands[0]["hex"]: new}
    assert sha(d / out["glb"]) == glb and json.load(open(d / out["informe"])) == rep_before      # nada más cambió
    assert cz.bambu.chosen is None                                                                # y la opción de la salida quedó como estaba


def test_write_bambu_needs_a_written_figure():
    params = doll_params()
    res = doll_pieces(params)
    cz = Colorizer(ColorSource(doll_textured(), spacing_mm=0.1), params, texel_mm=0.3)
    with pytest.raises(ValueError):
        cz.write_bambu()
    for n, m in piece_trimeshes(res).items():
        cz(n, m, res)
    with pytest.raises(ValueError):                                                              # hay piezas pero todavía no se escribió nada
        cz.write_bambu()
