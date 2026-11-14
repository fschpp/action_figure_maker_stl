"""Prueba de humo: la ventana real se construye con todas sus pestañas (protege el armado de `gui/steps`)."""
import importlib.util
import os

import pytest

pytest.importorskip("gi")
try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
except (ImportError, ValueError):
    pytest.skip("sin GTK4", allow_module_level=True)

if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    pytest.skip("sin pantalla (probar con xvfb-run)", allow_module_level=True)

from afm.gui.controller import Controller
from afm.gui.steps import STEPS
from afm.gui.window import MainWindow

PAGES = ["Figura", "Planos", "Catálogo", "Miembros", "Recortes", "Parámetros", "Generar"]


def test_main_window_builds_every_step():
    res = {}
    app = Gtk.Application(application_id="ar.afm.test")

    def act(a):
        try:
            win = MainWindow(a, Controller())
            res["pages"] = [win.tabs.get_tab_label_text(win.tabs.get_nth_page(i)) for i in range(win.tabs.get_n_pages())]
            res["attrs"] = {k for k in vars(win)}
        except Exception as e:      # noqa: BLE001
            res["error"] = e
        GLib.idle_add(a.quit)

    app.connect("activate", act)
    app.run([])
    assert "error" not in res, res.get("error")
    assert res["pages"] == PAGES
    assert len(STEPS) == len(PAGES)
    # controles que otras partes de la ventana (y los tests) esperan encontrar
    for attr in ("dd_front", "btn_crown", "tabs"):
        assert attr in res["attrs"]


def test_plane_list_is_fixed_above_tabs_and_follows_planes(tmp_path):
    """La lista de planos está fuera del Notebook (siempre visible); vacía muestra el aviso y con planos, la lista."""
    from afm.testfigure import make_doll

    m = make_doll()
    m.apply_scale(0.05)
    stl = str(tmp_path / "muneco.stl")
    m.export(stl)
    res = {}
    app = Gtk.Application(application_id="ar.afm.test2")

    def act(a):
        try:
            ctrl = Controller()
            win = MainWindow(a, ctrl)
            res["fuera_del_notebook"] = win.planes_box.get_ancestor(Gtk.Notebook) is None
            res["vacia"] = (win.lbl_planes_empty.get_visible(), win.planes_scroller.get_visible())
            ctrl.open_stl(stl)
            ctrl.auto_symmetry()
            win._rebuild_plane_list()
            res["llena"] = (win.lbl_planes_empty.get_visible(), win.planes_scroller.get_visible())
            res["filas"] = len(ctrl.plane_entries())
        except Exception as e:      # noqa: BLE001
            res["error"] = e
        GLib.idle_add(a.quit)

    app.connect("activate", act)
    app.run([])
    assert "error" not in res, res.get("error")
    assert res["fuera_del_notebook"]
    assert res["vacia"] == (True, False)
    assert res["filas"] >= 1 and res["llena"] == (False, True)
