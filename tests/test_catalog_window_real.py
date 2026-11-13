"""La ventana «Administrar catálogo» con GTK4 real: comandos de `afm catalogo` (certificar, cancelar, verificar, filtros).

Necesita GTK 4 y una pantalla (`xvfb-run -a pytest tests/test_catalog_window_real.py`); si no, se saltea."""
import os
import shutil
import time

import pytest

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    HAS_GTK = True
except (ImportError, ValueError):
    HAS_GTK = False

pytestmark = pytest.mark.skipif(not HAS_GTK or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                                reason="hace falta GTK 4 y una pantalla (xvfb-run -a pytest …)")

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


def pump(cond, timeout=600.0):
    """Deja correr el bucle principal de GTK hasta que `cond()` sea verdadera."""
    ctx = GLib.MainContext.default()
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("se agotó el tiempo esperando")
        ctx.iteration(False)
        time.sleep(0.01)


@pytest.fixture()
def win(tmp_path):
    from afm.gui.catalog_window import CatalogManagerWindow
    from afm.gui.controller import Controller

    root = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, root, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    ctrl = Controller()
    assert ctrl.open_catalog(root)
    parent = Gtk.Window()
    w = CatalogManagerWindow(parent, ctrl)
    w.present()
    pump(lambda: True)
    yield w
    w.destroy()
    parent.destroy()


def select(w, ref):
    for row, m in w._rows:
        if m.ref == ref:
            w.listbox.unselect_all()
            w.listbox.select_row(row)
            return
    raise AssertionError(f"{ref} no está en la lista")


def label_of(row):
    """Texto de la fila (la tabla trae una etiqueta por columna): las celdas unidas con « · »."""
    cells, child = [], row.get_child().get_first_child()
    while child is not None:
        cells.append(child.get_label())
        child = child.get_next_sibling()
    return " · ".join(c for c in cells if c)


def cells_of(row):
    from afm.gui.catalog_manager import COLUMNS

    labs, child = [], row.get_child().get_first_child()
    while child is not None:
        labs.append(child.get_label())
        child = child.get_next_sibling()
    return {k: v for (k, _t, _w), v in zip(COLUMNS, labs)}


def test_list_shows_the_certification_and_the_new_filters(win):
    by_ref = {m.ref: cells_of(row) for row, m in win._rows}
    assert by_ref["brazo-muneco-01@1"]["certificacion"] == "sin certificar"
    assert by_ref["torso-muneco-01@1"]["certificacion"] == ""                          # el torso no se certifica
    assert "certificacion" in [k for k, _t, _w in __import__("afm.gui.catalog_manager", fromlist=["COLUMNS"]).COLUMNS]
    assert "sin certificar 4" in win.lbl_title.get_text()
    # filtros: estado y etiqueta se llenan con lo que hay; «solo la última versión» no oculta nada con una versión por pieza
    assert win.dd_fstate.get_model().get_n_items() >= 2
    n = len(win._rows)
    win.chk_latest.set_active(True)
    assert len(win._rows) == n
    win.search.set_text("brazo")
    pump(lambda: len(win._rows) < n, 5)
    assert all("brazo" in m.display_name or m.tipo == "brazo" for _r, m in win._rows)


def test_menu_verify_and_contract_fill_the_report(win):
    win._op_verify()
    assert "limpio" in win._report_text() and win.report_exp.get_expanded()
    win._op_contract()
    assert "contrato" in win._report_text().lower()


def test_piece_buttons_follow_the_selection(win):
    assert not win.btn_certify.get_sensitive() and not win.btn_cover.get_sensitive()
    select(win, "torso-muneco-01@1")
    assert not win.btn_certify.get_sensitive() and win.btn_cover.get_sensitive()      # el torso no se certifica, sí tiene cobertura
    select(win, "brazo-muneco-01@1")
    assert win.btn_certify.get_sensitive() and win.btn_explain.get_sensitive()
    win._op_piece_report("cobertura")
    assert "hombro" in win._report_text()
    win._op_piece_report("explicar")
    assert win._report_text().startswith("== Explicar de brazo-muneco-01@1")


def test_certify_runs_in_a_thread_updates_the_row_and_releases_busy(win):
    select(win, "brazo-muneco-01@1")
    win._op_certify(win._selected_refs())
    assert win.ctrl.busy and win._task_label == "Certificando" and win.task_row.get_visible()
    assert not win.btn_certify.get_sensitive() and not win.btn_delete.get_sensitive()        # una sola tarea y nada de borrar en medio
    pump(lambda: not win._task_label)
    assert not win.ctrl.busy and not win.task_row.get_visible()
    assert "brazo-muneco-01@1: certificada" in win._report_text() and "1 pieza(s) procesada(s)" in win._report_text()
    badge = next(cells_of(row)["certificacion"] for row, m in win._rows if m.ref == "brazo-muneco-01@1")
    assert badge.startswith("certificada")
    assert "Certificación: certificada" in win.lbl_detail.get_text()
    assert "certificadas 1" in win.lbl_title.get_text()
    assert win.btn_certify.get_sensitive()


def test_cancel_stops_the_task_and_keeps_the_window_usable(win):
    select(win, "pierna-muneco-01@1")
    win._op_certify(win._selected_refs(), force=True)
    win._on_task_cancel()
    assert not win.btn_task_cancel.get_sensitive()
    pump(lambda: not win._task_label)
    assert not win.ctrl.busy and "cancelado" in win.ctrl.message.lower()
    assert "Cancelado" in win._report_text()
    badge = next(cells_of(row)["certificacion"] for row, m in win._rows if m.ref == "pierna-muneco-01@1")
    assert badge == "sin certificar"
    win._op_verify()                                                                        # y se puede seguir usando
    assert "limpio" in win._report_text()


def test_matrix_from_the_menu_writes_the_report(win):
    win._op_matrix(False)
    pump(lambda: not win._task_label)
    assert "COMPATIBILIDAD.md" in win._report_text()
    assert os.path.isfile(os.path.join(win.ctrl.session.catalog_root, "COMPATIBILIDAD.md"))


def test_filter_dropdowns_do_not_clash_with_the_apply_state_row(win):
    """`dd_state` es el selector de «Aplicar a las elegidas» (estado en lote); los filtros son `dd_fstate` y `dd_tag`."""
    from afm.parts import STATES

    assert win.dd_state.get_model().get_n_items() == len(STATES)
    assert win.dd_fstate.get_model().get_item(0).get_string().startswith("(todos")
    select(win, "brazo-muneco-01@1")
    win.dd_state.set_selected(STATES.index("validada"))
    win._on_apply_state()
    assert next(cells_of(row)["estado"] for row, m in win._rows if m.ref == "brazo-muneco-01@1") == "validada"
    assert "validada" in [win.dd_fstate.get_model().get_string(i) for i in range(win.dd_fstate.get_model().get_n_items())]


def test_capacity_runs_from_the_window_and_saves_only_when_asked(win):
    select(win, "torso-muneco-01@1")
    assert win.btn_capacity.get_sensitive()
    win._start_capacity(win._selected_refs(), False)
    pump(lambda: not win._task_label)
    assert "5 toma(s) medida(s)" in win._report_text() and "no se guardó nada" in win._report_text() and not win.ctrl.busy
    from afm.catalog import Catalog

    assert not Catalog.open(win.ctrl.session.catalog_root).meta("torso-muneco-01@1").puertos["cuello"].capacity
    win._start_capacity(None, True)
    pump(lambda: not win._task_label)
    assert "guardado en los .pieza.json" in win._report_text()
    assert Catalog.open(win.ctrl.session.catalog_root).meta("torso-muneco-01@1").puertos["cuello"].capacity
    select(win, "brazo-muneco-01@1")
    assert not win.btn_capacity.get_sensitive()                                           # el brazo no tiene tomas con pasador


def test_contract_proposal_enables_the_save_item(win):
    assert not win._btn_save_proposal.get_sensitive()
    win._op_contract_propose()
    assert "Propuesta (a confirmar)" in win._report_text() and win._btn_save_proposal.get_sensitive()
    digest = win.mgr.save_proposal()                                                      # (el diálogo de confirmación no se maneja desde el test)
    win._sync_ops_buttons()
    assert not win._btn_save_proposal.get_sensitive() and win.ctrl.catalog_handle().contract().hash() == digest
    win._op_contract()
    assert "afm-interfaz-1" in win._report_text()


# ---------------------------------------------------------------------------------------------------- Importar ▾
def _fill(dlg, **vals):
    """Llena el formulario como lo haría la persona: texto en los campos, casillas, lista de tipos y cuadro de puertos."""
    from afm.gui import catalog_import as CI

    for f in dlg._form["campos"]:
        if f.key not in vals:
            continue
        w, v = dlg._w[f.key], vals[f.key]
        if f.kind == CI.BOOL:
            w.set_active(v)
        elif f.kind == CI.CHOICE:
            w.set_selected(list(f.choices).index(v))
        elif f.kind == CI.MULTILINE:
            w.get_buffer().set_text(v)
        else:
            w.set_text(str(v))


def test_import_menu_lists_the_four_forms_and_follows_the_busy_state(win):
    from afm.gui import catalog_import as CI

    assert [b.get_label() for b in win._import_buttons] == list(CI.LABELS.values())
    assert all(b.get_sensitive() for b in win._import_buttons)
    select(win, "brazo-muneco-01@1")
    win._op_certify(win._selected_refs())
    assert win._task_label and not any(b.get_sensitive() for b in win._import_buttons)         # una sola tarea a la vez
    win._open_import("mano")                                                                    # y no abre formularios en medio de una tarea
    pump(lambda: not win._task_label)
    assert all(b.get_sensitive() for b in win._import_buttons)


def test_import_dialog_shows_the_validation_error_and_stays_open(win):
    from afm.gui.catalog_window import ImportDialog

    dlg = ImportDialog(win, "accesorio", win._start_import)
    dlg.present()
    dlg._on_go()
    assert "Falta «STL del accesorio»" in dlg.lbl_error.get_text() and dlg.get_visible() and not win.ctrl.busy
    _fill(dlg, stl="x.stl", largo_canal="abc")
    dlg._on_go()
    assert "no es un número" in dlg.lbl_error.get_text() and not win.ctrl.busy
    dlg.close()


def test_import_loose_piece_from_the_dialog_adds_and_selects_it(win, tmp_path):
    from afm.catalog import Catalog
    from afm.gui.catalog_window import ImportDialog
    from test_catalog import BASE, cyl

    stl = str(tmp_path / "brazo_nuevo.stl")
    cyl(5, 30).export(stl)
    top = " ".join(str(v) for a, b, _ in BASE for v in (a, b, 30.0))
    n = len(win._rows)
    dlg = ImportDialog(win, "pieza", win._start_import)
    dlg.present()
    _fill(dlg, stl=stl, tipo="brazo", id="brazo-dialogo-01", etiquetas="propia",
          puertos="hombro " + " ".join(str(v) for p in BASE for v in p) + f"\nmuneca {top}")
    dlg._on_go()
    assert win.ctrl.busy and win._task_label.startswith("Importando") and not win.btn_task_cancel.get_sensitive()     # no se cancela una importación
    pump(lambda: not win._task_label)
    assert not win.ctrl.busy and not dlg.get_visible()
    assert "Pieza guardada como brazo-dialogo-01@1" in win._report_text()
    assert len(win._rows) == n + 1 and win._selected_refs() == ["brazo-dialogo-01@1"]
    assert Catalog.open(win.ctrl.session.catalog_root).meta("brazo-dialogo-01@1").etiquetas == ["propia"]


def test_import_that_fails_reports_the_error_and_releases_busy(win, tmp_path):
    from afm.gui.catalog_window import ImportDialog

    dlg = ImportDialog(win, "mano", win._start_import)
    dlg.present()
    _fill(dlg, stl=str(tmp_path / "no_existe.stl"))
    dlg._on_go()
    pump(lambda: not win._task_label)
    assert not win.ctrl.busy and "ERROR:" in win._report_text() and "falló" in win.ctrl.message
