"""Importar al catálogo desde la ventana (`afm catalogo importar-mano / -accesorio / -arma / agregar-stl`): formularios, validación y tarea.

Sin GTK: `catalog_import` valida y ejecuta; el diálogo y el menú de la ventana se prueban en `test_catalog_window_real.py`."""
import os

import pytest

from afm import catalog_ops as OPS
from afm.catalog import Catalog
from afm.cli import main
from afm.gui import catalog_import as CI
from afm.gui import catalog_manager as CM
from afm.gui.controller import Controller
from test_catalog import BASE, _standard_hand, cyl
from test_catalog_grip import _sword
from test_gui_hand_place import std_hand  # noqa: F401  (fixture)
from test_weapon import cat_hand, sword_stl  # noqa: F401  (fixtures)


def _cut(pts) -> str:
    return ";".join(",".join(f"{x:.6f}" for x in p) for p in pts)


def _ports(*names) -> str:
    return "\n".join(f"{n} " + " ".join(str(v) for p in BASE for v in p) for n in names)


# ---------------------------------------------------------------------------------------------------- validación (sin catálogo)
def test_every_import_command_has_a_form():
    assert {f["comando"] for f in CI.FORMS.values()} == {c for c in OPS.COMMANDS if c.startswith("importar-") or c == "agregar-stl"}
    assert set(CI.LABELS) == set(CI.FORMS)


@pytest.mark.parametrize("kind, values, msg", [
    ("mano", {}, "Falta «STL de la mano»"),
    ("accesorio", {"stl": "a.stl"}, "Falta «Largo del canal"),
    ("accesorio", {"stl": "a.stl", "largo_canal": "0"}, "mayor que 0"),
    ("accesorio", {"stl": "a.stl", "largo_canal": "diez"}, "no es un número"),
    ("arma", {"stl": "a.stl"}, "Falta «Plano de corte"),
    ("arma", {"stl": "a.stl", "corte": "0,0;1,0,0;0,1,0"}, "tres puntos"),
    ("arma", {"stl": "a.stl", "corte": "0,0,0;1,0,0;0,1,0", "largo": "50", "escala": "2"}, "no los dos"),
    ("arma", {"stl": "a.stl", "corte": "0,0,0;1,0,0;0,1,0", "largo": "-3"}, "mayor que 0"),
    ("pieza", {"stl": "a.stl", "tipo": "brazo"}, "Falta «Puertos"),
    ("pieza", {"stl": "a.stl", "tipo": "brazo", "puertos": "hombro 1 2 3"}, "hacen falta 9 números"),
    ("pieza", {"stl": "a.stl", "tipo": "brazo", "puertos": "hombro 1 2 3 4 5 6 7 8 x"}, "no es un número"),
    ("pieza", {"stl": "a.stl", "tipo": "brazo", "puertos": _ports("hombro", "hombro")}, "repetido"),
    ("pieza", {"stl": "a.stl", "tipo": "brazo", "puertos": _ports("hombro"), "etiquetas": "rara"}, "Etiqueta desconocida"),
])
def test_parse_rejects_what_does_not_make_sense(kind, values, msg):
    with pytest.raises(ValueError, match=msg):
        CI.parse(kind, values)


def test_parse_builds_the_arguments_of_catalog_ops():
    p = CI.parse("arma", {"stl": "a.stl", "corte": "0,0,0; 1,0,0 ;0,1,0", "largo": "54,5", "invertir": True, "giro": "30", "id": " espada-01 "})
    assert p["args"] == (((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),)
    assert p["kwargs"] == {"scale": None, "length": 54.5, "flip": True, "roll": 30.0, "hand": None, "piece_id": "espada-01", "description": "", "new_version": False}
    p = CI.parse("accesorio", {"stl": "a.stl", "largo_canal": "10", "juego": "0,5"})
    assert p["args"] == (10.0,) and p["kwargs"]["play"] == 0.5 and p["kwargs"]["bore_d"] == 2.4
    p = CI.parse("pieza", {"stl": "a.stl", "tipo": "brazo", "puertos": _ports("hombro") + "\n\n", "etiquetas": "estandar, propia", "espejable": False})
    assert p["args"][0] == "brazo" and list(p["args"][1]) == ["hombro"] and p["kwargs"]["tags"] == ["estandar", "propia"] and p["kwargs"]["mirrorable"] is False
    assert CI.parse_ports("a 0,0,0, 1,0,0, 0,1,0") == {"a": [[0, 0, 0], [1, 0, 0], [0, 1, 0]]}


# ---------------------------------------------------------------------------------------------------- las cuatro importaciones
def test_import_hand_then_loose_piece_then_new_version(tmp_path):
    root = str(tmp_path / "linea")
    Catalog.create(root, "linea-prueba", 130.0)
    res = CI.run("mano", root, CI.parse("mano", {"stl": _standard_hand(tmp_path)}))
    assert res.ref == "mano-estandar-01@1" and res.lines[0].startswith("Mano guardada como mano-estandar-01@1")
    with pytest.raises(Exception, match="ya existe|existe"):
        CI.run("mano", root, CI.parse("mano", {"stl": _standard_hand(tmp_path)}))                  # mismo id sin «nueva versión»
    assert CI.run("mano", root, CI.parse("mano", {"stl": _standard_hand(tmp_path), "nueva_version": True})).ref == "mano-estandar-01@2"

    stl = str(tmp_path / "brazo.stl")
    cyl(5, 30).export(stl)
    top = " ".join(str(v) for a, b, _ in BASE for v in (a, b, 30.0))
    res = CI.run("pieza", root, CI.parse("pieza", {"stl": stl, "tipo": "brazo", "id": "brazo-form-01", "etiquetas": "estandar",
                                                   "puertos": _ports("hombro") + f"\nmuneca {top}"}))
    assert res.ref == "brazo-form-01@1" and "puertos: hombro, muneca" in res.lines[0]
    meta = Catalog.open(root).meta("brazo-form-01@1")
    assert meta.etiquetas == ["estandar"] and meta.altura_figura_mm == 130.0 and meta.espejable


def test_import_weapon_and_accessory_on_a_line_with_a_grip_hand(cat_hand, sword_stl, tmp_path):  # noqa: F811
    path, pts = sword_stl
    res = CI.run("arma", cat_hand.root, CI.parse("arma", {"stl": path, "corte": _cut(pts), "largo": "54", "id": "excalibur-02"}))
    assert res.ref == "excalibur-02@1" and res.lines[0].startswith("Arma guardada como excalibur-02@1")
    assert any("se sujeta en mano-agarre-01@1" in ln for ln in res.lines)
    stl = str(tmp_path / "espada.stl")
    _sword().export(stl)
    res = CI.run("accesorio", cat_hand.root, CI.parse("accesorio", {"stl": stl, "id": "espada-uno-01", "largo_canal": "10"}))
    assert res.ref == "espada-uno-01@1" and any("sirve en mano-agarre-01@1: ok" in ln for ln in res.lines)
    assert Catalog.open(cat_hand.root).verify().ok


def test_form_and_console_leave_the_same_piece(cat_hand, sword_stl, capsys):  # noqa: F811
    """El formulario y `afm catalogo importar-arma` pasan por el mismo código: misma salida para los mismos datos."""
    path, pts = sword_stl
    assert main(["catalogo", "importar-arma", cat_hand.root, path, "--corte=" + _cut(pts), "--largo", "54", "--id", "excalibur-cli-01"]) == 0
    cli_lines = capsys.readouterr().out.splitlines()
    res = CI.run("arma", cat_hand.root, CI.parse("arma", {"stl": path, "corte": _cut(pts), "largo": "54", "id": "excalibur-gui-01"}))
    assert [ln.replace("excalibur-cli-01", "X") for ln in cli_lines[:2]] == [ln.replace("excalibur-gui-01", "X") for ln in CI.result_lines(res)[:2]]


# ---------------------------------------------------------------------------------------------------- el gestor
def _mgr(root):
    c = Controller()
    assert c.open_catalog(root)
    return CM.CatalogManager(c)


def test_manager_import_job_validates_before_taking_busy_and_runs(cat_hand, sword_stl):  # noqa: F811
    mgr = _mgr(cat_hand.root)
    with pytest.raises(ValueError, match="Falta"):
        mgr.import_job("arma", {"stl": ""})
    assert not mgr.ctrl.busy                                                            # un formulario mal llenado no deja la ventana ocupada
    path, pts = sword_stl
    job = mgr.import_job("arma", {"stl": path, "corte": _cut(pts), "largo": "54", "id": "excalibur-job-01"})
    assert callable(job) and mgr.ctrl.busy
    assert mgr.import_job("mano", {"stl": "x.stl"}) is None and "Esperá" in mgr.ctrl.message
    res = job(None)
    mgr.finish_task("Importando", None)
    assert res.ref == "excalibur-job-01@1" and not mgr.ctrl.busy
    assert any(m.ref == "excalibur-job-01@1" for m in mgr.ctrl.catalog_pieces())


def test_manager_import_without_catalog_does_not_take_busy():
    c = Controller()
    m = CM.CatalogManager(c)
    assert m.import_job("mano", {"stl": "x.stl"}) is None and "No hay un catálogo abierto" in c.message and not c.busy
