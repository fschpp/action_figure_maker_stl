"""Prueba «dejar uno afuera» (Fase 7): el informe y los errores; la corrida completa con figuras reales la hace el cliente."""
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import dejar_uno_afuera as D  # noqa: E402

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


def fila(ok, lugar="brazo"):
    return {"anfitrion": "a.stl", "donante": "b.stl", "lugar": lugar, "talle_donante": "M", "pasa": ok,
            "problemas_nuevos": [] if ok else ["pared de 0.8 mm"]}


def test_report_counts_only_same_talle_and_checks_goal():
    good = {"figura": "x.stl", "errores": [], "ajuste": ["brazo (hombro): sin cambios"], "filas": [fila(True)] * 10,
            "mismo_talle": [fila(True)] * 9 + [fila(False)], "pasan": 9}
    txt = D.report([good], "equivalente")
    assert "del mismo talle: 10; pasan: 9 (90 %)" in txt and "**cumple**" in txt and "FALLA a.stl ← b.stl" in txt
    bad = dict(good, mismo_talle=[fila(True)] * 8 + [fila(False)] * 2, pasan=8)
    assert "**no cumple**" in D.report([bad], "inscrito")
    err = {"figura": "y.stl", "errores": ["no se pudo extraer la figura: x"]}
    txt = D.report([err], "equivalente")
    assert "**Error:** no se pudo extraer" in txt and "sin combinaciones del mismo talle" in txt


def test_leave_one_out_reports_unknown_figure(tmp_path):
    out = D.leave_one_out(EJEMPLO, "no-existe.stl", "x.stl", "x.proyecto", "equivalente", 0.4, str(tmp_path))
    assert out["errores"] and "no tiene piezas" in out["errores"][0]


def test_leave_one_out_end_to_end_with_a_second_figure(tmp_path):
    """El muñeco como figura nueva frente a una «otra figura» hecha con sus mismas piezas: queda en el talle, no se toca el
    corte y las 4 combinaciones cruzadas (brazo y pierna, en los dos sentidos) pasan. Arma figuras: ~1,5 min."""
    sys.path.insert(0, os.path.dirname(__file__))
    from test_guide import clone

    from afm.catalog import Catalog
    from afm.project import demo_project
    from afm.testfigure import make_doll

    root = str(tmp_path / "cat")
    shutil.copytree(EJEMPLO, root, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    cat = Catalog.open(root)
    for ref, nid in (("torso-muneco-01", "torso-otra-a"), ("brazo-muneco-01", "brazo-otra-a"), ("pierna-muneco-01", "pierna-otra-a")):
        clone(cat, ref, nid, 1.0)
    figs = tmp_path / "figs"
    figs.mkdir()
    make_doll().export(str(figs / "muneco.stl"))
    demo_project().save(str(figs / "muneco.proyecto"))
    out = D.leave_one_out(root, "muneco.stl", str(figs / "muneco.stl"), str(figs / "muneco.proyecto"), "equivalente", 0.4,
                          str(tmp_path / "work"), log=lambda *_: None)
    assert out["errores"] == [] and out["ajustados"] == 0 and all("sin cambios" in l for l in out["ajuste"] if l.startswith("  →"))
    assert len(out["filas"]) == 4 and len(out["mismo_talle"]) == 4 and out["pasan"] == 4
    assert "**cumple**" in D.report([out], "equivalente")
