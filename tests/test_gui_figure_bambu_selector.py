"""Selector de colores y vista previa del 3MF en «Armar figura desde catálogo»: lo que decide está en `FigureBuilder` (sin GTK, R9)."""
import hashlib
import json
import os
import shutil
import zipfile

import pytest

from test_color_catalog_palette import base_line  # noqa: F401  (fixture)
from test_gui_figure_palette import NEW, make_builder

NAME = "muneco-color"


def sha(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def materials(out) -> list:
    with zipfile.ZipFile(os.path.join(out, f"{NAME}_bambu.3mf")) as z:
        xml = z.read("3D/3dmodel.model").decode()
    return [c[:7] for c in xml.split('displaycolor="')[1:]]


@pytest.fixture(scope="module")
def built(tmp_path_factory, base_line):
    d = str(tmp_path_factory.mktemp("linea") / "linea")
    shutil.copytree(base_line, d)
    b = make_builder(d)
    out = str(tmp_path_factory.mktemp("salida") / "fig")
    b.generate(out, progress=lambda *_: None)
    return b, out


@pytest.fixture(autouse=True)
def _auto(request):
    """Cada prueba parte del modo automático (la figura armada se comparte entre ellas)."""
    if "built" in request.fixturenames:
        request.getfixturevalue("built")[0].bambu_reset()
        request.getfixturevalue("built")[0].bambu_colors, request.getfixturevalue("built")[0].bambu_min_pct = 8, 0.5
    yield


def test_nothing_to_choose_before_building(tmp_path, base_line):
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    b = make_builder(d)
    assert b.has_bambu_colors() is False and b.bambu_candidates() == []
    assert b.bambu_options() is None and "chosen" not in b.bambu_options(force=True)
    for call in (b.bambu_preview, b.bambu_export):
        with pytest.raises(ValueError):
            call()
    with pytest.raises(ValueError):
        b.bambu_set_enabled("#C81E1E", True)


def test_candidates_name_the_palette_roles(built):
    b, _out = built
    c = b.bambu_candidates()
    assert b.has_bambu_colors() and len(c) >= 2
    assert {r for x in c for r in x["roles"]} >= {"rojo", "azul"}            # la paleta de la línea del muñeco
    assert {n for x in c for n in x["nombres"]} >= {"Rojo", "Azul"}          # y la fila lleva el nombre del rol, no solo el HEX
    assert all(x["out_hex"] == x["hex"] and not x["cambiado"] for x in c)                   # sin tocar nada: se escriben con su color
    assert {x["hex"] for x in c if x["enabled"]} == {x["hex"] for x in c if x["area_pct"] >= b.bambu_min_pct}      # marcados: los que pasan el % mínimo
    assert not b.bambu_is_custom()


def test_automatic_selection_follows_the_cap(built):
    b, _out = built
    n = len(b.bambu_candidates())
    b.bambu_colors = 1
    on = [x for x in b.bambu_candidates() if x["enabled"]]
    assert len(on) == 1 and on[0] is not None and on[0]["hex"] == b.bambu_candidates()[0]["hex"]      # el más usado
    assert b.bambu_options(force=True)["chosen"] == [on[0]["hex"]] and not b.bambu_is_custom()
    b.bambu_colors = 16
    assert len([x for x in b.bambu_candidates() if x["enabled"]]) <= n


def test_disable_a_color_updates_the_preview_and_never_leaves_zero(built):
    b, _out = built
    cands = b.bambu_candidates()
    n_on = len([c for c in cands if c["enabled"]])
    gone = [c for c in cands if c["enabled"]][-1]["hex"]
    b.bambu_set_enabled(gone, False)
    assert b.bambu_is_custom() and gone not in b.bambu_selection
    _parts, summary = b.bambu_preview()
    assert len(summary["colores"]) == n_on - 1 and summary["fundidos"] >= 1 and _parts
    assert sum(summary["area_pct"]) == pytest.approx(100.0, abs=0.1)
    for c in b.bambu_candidates():                                               # dejar solo uno: bien; sacar el último que queda: no
        if c["enabled"] and c["hex"] != cands[0]["hex"]:
            b.bambu_set_enabled(c["hex"], False)
    assert [c["hex"] for c in b.bambu_candidates() if c["enabled"]] == [cands[0]["hex"]]
    with pytest.raises(ValueError):
        b.bambu_set_enabled(cands[0]["hex"], False)
    with pytest.raises(ValueError):
        b.bambu_set_enabled("#010203", True)
    b.bambu_set_enabled(gone, True)
    assert gone in b.bambu_effective_selection()


def test_output_color_changes_only_the_3mf(built):
    b, out = built
    cands = b.bambu_candidates()
    top = cands[0]["hex"]
    glb, rep = sha(os.path.join(out, f"{NAME}_ensamblada.glb")), sha(os.path.join(out, f"{NAME}_informe.json"))
    b.bambu_set_output_color(top, "#00ff00")
    row = b.bambu_candidates()[0]
    assert row["out_hex"] == "#00FF00" and row["cambiado"] and b.bambu_is_custom()
    assert b.bambu_options(force=True)["remap"] == {top: "#00FF00"}
    parts, summary = b.bambu_preview()
    assert "#00FF00" in summary["colores"] and any(abs(c[1] - 1.0) < 1e-6 and c[0] == 0.0 for _n, _m, c in parts)
    info = b.bambu_export()
    assert "#00FF00" in materials(out) and "#00FF00" in [c["hex"] for c in info["colores"]]
    assert sha(os.path.join(out, f"{NAME}_ensamblada.glb")) == glb and sha(os.path.join(out, f"{NAME}_informe.json")) == rep      # solo los de Bambu
    b.bambu_set_output_color(top, top)                                           # volver al mismo color lo quita
    assert b.bambu_remap == {} and not b.bambu_is_custom()
    with pytest.raises(ValueError):
        b.bambu_set_output_color("#010203", "#00FF00")
    with pytest.raises(ValueError):
        b.bambu_set_output_color(top, "verde")


def test_export_now_without_the_checkbox_still_writes_the_chosen_colors(built):
    b, out = built
    assert b.bambu is False
    for f in [f for f in os.listdir(out) if "_bambu" in f]:
        os.remove(os.path.join(out, f))
    cands = b.bambu_candidates()
    last = [c for c in cands if c["enabled"]][-1]["hex"]
    b.bambu_set_enabled(last, False)
    info = b.bambu_export()
    assert os.path.isfile(os.path.join(out, info["3mf"])) and last not in materials(out)
    assert len(materials(out)) == len(info["colores"]) == len([c for c in cands if c["enabled"]]) - 1


def test_reset_goes_back_to_automatic(built):
    b, _out = built
    cands = b.bambu_candidates()
    auto = [c["enabled"] for c in cands]
    b.bambu_set_enabled([c for c in cands if c["enabled"]][-1]["hex"], False)
    b.bambu_set_output_color(cands[0]["hex"], "#00FF00")
    b.bambu_reset()
    assert not b.bambu_is_custom() and b.bambu_selection is None and b.bambu_remap == {}
    assert [c["enabled"] for c in b.bambu_candidates()] == auto and not any(c["cambiado"] for c in b.bambu_candidates())


def test_recolor_makes_the_selection_follow_the_role(tmp_path, base_line):
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    b = make_builder(d)
    b.bambu = True
    out = str(tmp_path / "fig")
    b.generate(out, progress=lambda *_: None)
    red = next(c for c in b.bambu_candidates() if "rojo" in c["roles"])
    b.bambu_set_output_color(red["hex"], "#00FF00")
    b.set_override("rojo", NEW)
    b.recolor()
    sel = {c["hex"]: c for c in b.bambu_candidates()}
    assert NEW in sel and red["hex"] not in sel                                  # el candidato pasó al color nuevo del rol
    assert sel[NEW]["out_hex"] == "#00FF00" and list(b.bambu_remap) == [NEW]     # y el cambio de salida lo siguió
    assert "#00FF00" in materials(out) and NEW not in materials(out)             # el 3MF se reescribió con eso


def test_building_again_drops_the_old_selection(tmp_path, base_line):
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    b = make_builder(d)
    b.generate(str(tmp_path / "uno"), progress=lambda *_: None)
    b.bambu_set_enabled([c for c in b.bambu_candidates() if c["enabled"]][-1]["hex"], False)
    b.bambu_set_output_color(b.bambu_candidates()[0]["hex"], "#00FF00")
    assert b.bambu_is_custom()
    b.generate(str(tmp_path / "dos"), progress=lambda *_: None)       # figura nueva = candidatos nuevos: se vuelve al automático
    assert not b.bambu_is_custom()

