"""Herramienta de calibración (Fase 0): partes puras, sin armar figuras (armar una cuesta minutos)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import calibrar_compat as C  # noqa: E402
from afm import guide as G  # noqa: E402
from afm.catalog import Catalog  # noqa: E402
from afm.recipe import Recipe  # noqa: E402

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


def row(h, d, place, nivel, ok, **kw):
    return {"anfitrion": h, "donante": d, "lugar": place, "clase": C.CLASS_OF_PLACE[place], "pieza": f"{place}-{d}-01@1",
            "nivel": nivel, "iou": kw.get("iou", 0.7), "escalon_mm": 0.5, "armado": True, "pasa": ok,
            "problemas_nuevos": [] if ok else ["pared de 0.8 mm tras la cabeza del pasador"], "avisos": [], "segundos": 1.0, "cache": False}


def test_confusion_and_calibration_json_feed_the_guide(tmp_path):
    rows = [row("a", "b", "brazo", "ok", True), row("a", "c", "brazo", "ok", False), row("b", "a", "brazo", "aviso", True),
            row("b", "c", "brazo", "no", False), row("a", "b", "pierna", "aviso", True), row("a", "c", "pierna", "nivel-raro", True)]
    conf = C.confusion(rows)
    assert conf == {"hombro": {"ok": [1, 2], "aviso": [1, 1], "no": [0, 1]}, "cadera": {"aviso": [1, 1]}}     # el nivel raro se ignora
    cat = Catalog.open(EJEMPLO)
    js = C.calibration_json({"filas": rows, "base": {}, "figuras": ["a", "b", "c"]}, cat)
    assert js["formato"] == G.CALIB_FORMAT and js["clases"] == conf and js["n_combinaciones"] == 6
    (tmp_path / G.CALIB_FILE).write_text(json.dumps(js))
    assert G.load_calibration(str(tmp_path)) == conf                                  # lo que escribe es lo que la guía lee


def test_figures_pick_and_recipes():
    cat = Catalog.open(EJEMPLO)
    figs = C.figures_of(cat)
    assert list(figs) == ["muneco.stl"] and {"cabeza", "torso", "brazo", "pierna", "mano"} <= set(figs["muneco.stl"])
    assert C.pick(["muneco"], figs) == ["muneco.stl"] and C.pick(["mun"], figs) == ["muneco.stl"] and C.pick(["otra"], figs) == []
    assert C.pick(None, figs) == ["muneco.stl"]
    base = C.base_recipe(cat, "muneco.stl", figs["muneco.stl"])
    assert base.lugares["torso"] == "torso-muneco-01@1" and base.lugares["pelvis"] is None
    assert base.to_dict()["lugares"] == Recipe.load(os.path.join(EJEMPLO, "recetas", "muneco.receta.json")).to_dict()["lugares"]
    # el donante cambia solo el lugar pedido; sin puerto «muneca» la mano va pegada al brazo
    swapped = C.swap_recipe(cat, "muneco.stl", figs["muneco.stl"], "otra.stl", {"brazo": cat.meta("brazo-muneco-01")}, "brazo")
    assert swapped.lugares["brazo"] == "brazo-muneco-01@1" and swapped.personaje == "cal-muneco-brazo-otra"


def test_report_markdown_reads_the_confusion():
    cat = Catalog.open(EJEMPLO)
    rows = [row("a", "b", "brazo", "ok", True), row("a", "c", "brazo", "ok", False), row("b", "a", "pierna", "no", True)]
    md = C.report_markdown({"filas": rows, "base": {"a": {"armado": True, "problemas": [], "avisos": ["x"]}}, "figuras": ["a", "b", "c"]}, cat)
    assert "| hombro | ok | 1 | 2 |" in md and "| cadera | no | 1 | 1 |" in md
    assert "De 2 combinaciones «ok» por la barata, 1 fallaron" in md and "De 1 combinaciones «no», 1 pasaron" in md
    assert "pared de N mm tras la cabeza del pasador" in md                            # los números se reemplazan para agrupar
    assert "| a | brazo | c | ok | 0,70 | 0,50 | **falla** |" in md
