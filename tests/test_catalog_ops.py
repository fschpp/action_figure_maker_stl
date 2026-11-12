"""`afm catalogo …` dentro de la ventana «Administrar catálogo»: la lógica compartida (`catalog_ops`) y su reparto en el gestor.

Sin GTK: la ventana solo muestra lo que devuelven estas funciones (ver `tests/test_catalog_window_real.py` para la ventana)."""
import argparse
import os
import shutil

import pytest

from afm import catalog_ops as OPS
from afm import certify, compat
from afm.catalog import Catalog
from afm.catalog_cli import add_parsers
from afm.cli import main
from afm.gui import catalog_manager as CM
from afm.gui.controller import Controller

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture()
def cat_dir(tmp_path):
    dst = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, dst, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    return dst


@pytest.fixture()
def mgr(cat_dir):
    c = Controller()
    assert c.open_catalog(cat_dir)
    return CM.CatalogManager(c)


# ---------------------------------------------------------------------------------------------------- registro consola → ventana
def test_every_catalog_subcommand_is_registered_for_the_window():
    """Un subcomando nuevo de `afm catalogo` obliga a decidir: ¿va en la ventana o es «solo consola»?"""
    p = argparse.ArgumentParser()
    add_parsers(p.add_subparsers(dest="cmd"))
    sub = next(a for a in p._subparsers._group_actions[0].choices["catalogo"]._subparsers._group_actions)
    in_cli = set(sub.choices)
    assert in_cli == set(OPS.COMMANDS), f"sin registrar: {in_cli - set(OPS.COMMANDS)}; sobran: {set(OPS.COMMANDS) - in_cli}"
    assert {v[0] for v in OPS.COMMANDS.values()} <= {OPS.GUI, OPS.CONSOLE}


# ---------------------------------------------------------------------------------------------------- la consola sigue igual
def test_cli_outputs_unchanged_after_the_refactor(cat_dir, capsys):
    assert main(["catalogo", "verificar", cat_dir]) == 0
    assert "limpio" in capsys.readouterr().out
    assert main(["catalogo", "certificar", cat_dir, "--estado"]) == 0
    out = capsys.readouterr().out
    assert "brazo-muneco-01@1: sin certificar" in out and "mano-muneco-01@1: sin certificar" in out
    assert main(["catalogo", "cobertura", cat_dir, "brazo-muneco-01"]) == 0
    assert "hombro (enchufe)" in capsys.readouterr().out
    assert main(["catalogo", "explicar", cat_dir, "no-existe-01@1"]) == 2
    assert main(["catalogo", "contrato", cat_dir, "--ver"]) == 0


def test_listar_shows_the_certification_of_hands_and_pelvis_too(cat_dir, capsys):
    """Antes `listar` solo mostraba la certificación de brazo, pierna y cabeza aunque se certifican también pelvis y mano."""
    assert main(["catalogo", "listar", cat_dir]) == 0
    lines = capsys.readouterr().out.splitlines()
    i = next(i for i, ln in enumerate(lines) if ln.startswith("mano-muneco-01@1"))
    assert lines[i + 1].strip() == "sin certificar"


# ---------------------------------------------------------------------------------------------------- informes
def test_report_functions_return_lines_and_do_not_print(cat_dir, capsys):
    cat = Catalog.open(cat_dir)
    v = OPS.verify(cat)
    assert v.ok and v.lines[-1].endswith("limpio")
    assert any("hombro" in ln for ln in OPS.coverage_lines(cat, "brazo-muneco-01"))
    assert OPS.explain_lines(cat, "brazo-muneco-01")[0].startswith("brazo-muneco-01@1")
    lines, probs = OPS.contract_view(cat)
    assert lines and probs == []
    with pytest.raises(Exception, match="no-existe"):
        OPS.explain_lines(cat, "no-existe-01@1")
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------------------------------- certificar
def test_certify_status_only_arms_nothing_and_cancel_before_start(cat_dir):
    rep = OPS.certify_pieces(cat_dir, status_only=True)
    assert rep.bad == 0 and rep.done == len(OPS.certifiable(Catalog.open(cat_dir)))
    assert all(ln.endswith("sin certificar") for ln in rep.lines)
    assert not os.path.exists(os.path.join(cat_dir, "compat.json"))

    rep = OPS.certify_pieces(cat_dir, ["brazo-muneco-01@1"], cancel=lambda: True)
    assert rep.cancelled and rep.done == 0 and not os.path.exists(os.path.join(cat_dir, "compat.json"))


def test_cancel_inside_an_assembly_stops_it_and_saves_nothing(cat_dir):
    """El motor avisa su progreso: en cuanto `cancel()` da True se corta el armado (sin confundirlo con una falla de la receta)."""
    seen = []
    rep = OPS.certify_pieces(cat_dir, ["brazo-muneco-01@1"], progress=seen.append, cancel=lambda: bool(seen))
    # `cancel` es falso al empezar (seen vacío) y verdadero apenas llega el primer mensaje de progreso
    assert rep.cancelled and any("cancelado" in ln for ln in rep.lines)
    assert certify.status(Catalog.open(cat_dir), "brazo-muneco-01@1")["estado"] == "sin certificar"


def test_certify_emits_lines_live_and_then_status_is_known(cat_dir):
    live = []
    rep = OPS.certify_pieces(cat_dir, ["brazo-muneco-01@1"], emit=live.append)
    assert live == rep.lines and rep.done == 1 and not rep.cancelled
    assert rep.lines[-1].startswith("brazo-muneco-01@1: certificada")
    assert compat.CompatCache(cat_dir).get_cert("brazo-muneco-01") is not None


# ---------------------------------------------------------------------------------------------------- gestor
def test_manager_filters_by_state_tag_and_latest_version(mgr):
    todas = mgr.pieces()
    assert {m.estado for m in todas} == set(mgr.states())
    assert [m.ref for m in mgr.pieces(estado=CM.ALL_STATES)] == [m.ref for m in todas]
    assert mgr.pieces(estado="impresa") == [] and mgr.pieces(etiqueta="no-existe") == []
    mgr.ctrl.session.catalog_open().set_state("brazo-muneco-01", "validada")
    assert [m.id for m in mgr.pieces(estado="validada")] == ["brazo-muneco-01"]
    assert len(mgr.pieces(latest_only=True)) == len({m.id for m in todas})


def test_cert_badge_is_cached_until_the_disk_changes(mgr):
    ref = "brazo-muneco-01@1"
    assert mgr.cert_text(ref) == "sin certificar" and mgr.cert_text("torso-muneco-01@1") == ""
    assert "sin certificar 4" in mgr.cert_summary()
    mgr._badges[1][ref] = "valor viejo"                   # mientras el disco no cambie, se reutiliza
    assert mgr.cert_text(ref) == "valor viejo"
    OPS.certify_pieces(mgr.ctrl.session.catalog_root, [ref])
    assert mgr.cert_text(ref).startswith("certificada")   # compat.json cambió: se vuelve a calcular
    assert "certificadas 1" in mgr.cert_summary()


def test_row_and_detail_text_show_the_certification(mgr):
    meta = mgr.pieces()[0]
    assert "certificada 1/1" in CM.row_text(meta, "certificada 1/1") and "certificada" not in CM.row_text(meta)
    assert "Certificación: vencida" in CM.detail_text(meta, None, "vencida") and "Certificación" not in CM.detail_text(meta, None)


def test_long_task_flow_busy_cancel_and_finish(mgr):
    ctrl = mgr.ctrl
    lines = []
    job = mgr.certify_job(["brazo-muneco-01@1"], emit=lines.append)
    assert callable(job) and ctrl.busy
    assert mgr.certify_job(None) is None and "Esperá" in ctrl.message           # una sola tarea a la vez
    assert mgr.compat_job() is None and mgr.matrix_job() is None
    res = job(None)
    assert res.done == 1 and lines and lines[-1].startswith("brazo-muneco-01@1:")
    mgr.finish_task("Certificando", None)
    assert not ctrl.busy and "terminado" in ctrl.message

    mgr.request_cancel()                                                         # `certify_job` rearma la señal al empezar
    job = mgr.certify_job(["brazo-muneco-01@1"], force=True)
    assert job is not None and not mgr.cancelled()
    mgr.request_cancel()
    assert job(None).cancelled
    mgr.finish_task("Certificando", OPS.Cancelled())
    assert not ctrl.busy and "cancelado" in ctrl.message


def test_long_task_without_catalog_or_without_certifiable_pieces(mgr):
    c = Controller()
    m = CM.CatalogManager(c)
    assert m.matrix_job() is None and "No hay un catálogo abierto" in c.message
    assert not c.busy
    assert mgr.certify_job(["torso-muneco-01@1"]) is None and "No hay piezas para certificar" in mgr.ctrl.message
    assert not mgr.ctrl.busy


def test_compat_and_matrix_jobs(mgr, cat_dir):
    job = mgr.compat_job()
    res = job(None)
    assert isinstance(res, OPS.CompatReport) and res.lines
    mgr.finish_task("Compatibilidad", None)
    job = mgr.matrix_job(False)
    path = job(None)
    mgr.finish_task("Informe", None)
    assert path.endswith("COMPATIBILIDAD.md") and os.path.isfile(path)


def test_certification_is_a_sortable_column_of_the_table(mgr):
    metas = mgr.pieces()
    cert = {m.ref: ("certificada 1/1" if m.tipo == "brazo" else "sin certificar" if m.tipo in certify.PLACE_OF_TIPO else "") for m in metas}
    cells = CM.row_cells(metas[0], cert[metas[0].ref])
    assert {k for k, _t, _w in CM.COLUMNS} == set(cells) and "certificacion" in cells
    ordered = CM.sort_pieces(metas, "certificacion", cert_of=lambda m: cert[m.ref])
    assert [cert[m.ref] for m in ordered] == sorted(cert.values())
    desc = CM.sort_pieces(metas, "certificacion", True, lambda m: cert[m.ref])
    assert [cert[m.ref] for m in desc] == sorted(cert.values(), reverse=True)


# ---------------------------------------------------------------------------------------------------- fase 3: capacidad y contrato
def test_capacity_report_and_cli_print_the_same_lines(cat_dir, capsys):
    rep = OPS.measure_capacity(cat_dir)
    assert rep.done == 5 and rep.skipped == 0 and not rep.wrote
    assert rep.lines[0].startswith("  torso-muneco-01@1") and "pide 5.85" in rep.lines[0]
    assert rep.lines[-1].endswith("no se guardó nada (usar --escribir)")
    assert main(["catalogo", "capacidad", cat_dir]) == 0
    assert capsys.readouterr().out.splitlines() == rep.lines


def test_capacity_write_saves_into_the_piece_and_cancel_stops_between_pieces(cat_dir):
    assert OPS.measure_capacity(cat_dir, cancel=lambda: True).cancelled
    assert OPS.measure_capacity(cat_dir, ["torso-muneco-01@1"], cancel=lambda: True).done == 0
    rep = OPS.measure_capacity(cat_dir, ["torso-muneco-01@1"], write=True)
    assert rep.wrote and rep.lines[-1].endswith("guardado en los .pieza.json")
    caps = Catalog.open(cat_dir).meta("torso-muneco-01@1").puertos["cuello"].capacity
    assert caps and caps["profundidad_mm"] == pytest.approx(38.0, abs=0.01)
    assert OPS.measurable(Catalog.open(cat_dir), Catalog.open(cat_dir).meta("brazo-muneco-01@1")) == []     # el brazo no tiene tomas con pasador


def test_contract_propose_save_clear_and_cli_agree(cat_dir, capsys):
    cat = Catalog.open(cat_dir)
    assert cat.contract() is None
    prop = OPS.propose_contract(cat)
    assert prop.lines[0] == "Propuesta (a confirmar):" and prop.problems == [] and prop.contract is not None
    assert cat.contract() is None                                                    # proponer no escribe nada
    assert main(["catalogo", "contrato", cat_dir, "--calibrar"]) == 0
    assert capsys.readouterr().out.splitlines()[:len(prop.lines)] == prop.lines
    digest = OPS.save_contract(cat, prop.contract)
    assert Catalog.open(cat_dir).contract().hash() == digest
    assert OPS.clear_contract(Catalog.open(cat_dir)) and not OPS.clear_contract(Catalog.open(cat_dir))
    assert main(["catalogo", "contrato", cat_dir, "--calibrar", "--guardar"]) == 0
    assert Catalog.open(cat_dir).contract() is not None
    assert main(["catalogo", "contrato", cat_dir, "--quitar"]) == 0


def test_manager_capacity_job_and_contract_flow(mgr):
    assert mgr.capacity_refs(["brazo-muneco-01@1", "torso-muneco-01@1"]) == ["torso-muneco-01@1"]
    assert mgr.capacity_job(["brazo-muneco-01@1"]) is None and "Ninguna de las piezas" in mgr.ctrl.message and not mgr.ctrl.busy
    lines = []
    job = mgr.capacity_job(None, write=True, emit=lines.append)
    assert callable(job) and mgr.ctrl.busy
    res = job(None)
    assert res.wrote and res.done == 5 and lines == res.lines
    mgr.finish_task("Midiendo y guardando la capacidad", None)
    assert not mgr.ctrl.busy

    assert not mgr.can_save_proposal() and pytest.raises(ValueError, mgr.save_proposal)
    prop = mgr.propose_contract()
    assert mgr.can_save_proposal() and mgr.proposal is prop
    digest = mgr.save_proposal()
    assert mgr.proposal is None and mgr.ctrl.catalog_handle().contract().hash() == digest
    assert mgr.clear_contract() and mgr.ctrl.catalog_handle().contract() is None
