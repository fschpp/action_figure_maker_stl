"""Regresión: «Paleta de la línea…» y el asistente de roles se pueden volver a abrir después de cerrarlos.

Necesita GTK4 y una pantalla (p. ej. `xvfb-run -a pytest tests/test_gui_window_reopen.py`); sin eso se salta.
Antes del arreglo la ventana cerrada quedaba guardada y `present()` daba «A window is shown after it has been destroyed»."""
import os
import shutil
import time

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
except (ValueError, ImportError):
    pytest.skip("sin GTK4", allow_module_level=True)
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    pytest.skip("sin pantalla (usar xvfb-run)", allow_module_level=True)

from afm.gui.catalog_window import CatalogManagerWindow
from afm.gui.controller import Controller
from test_catalog_roles import ROOT


def spin(n: int = 40) -> None:
    ctx = GLib.MainContext.default()
    for _ in range(n):
        while ctx.iteration(False):
            pass
        time.sleep(0.01)


@pytest.fixture()
def manager(tmp_path):
    d = str(tmp_path / "linea")
    shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    ctrl = Controller()
    assert ctrl.open_catalog(d)
    parent = Gtk.Window()
    parent.present()
    win = CatalogManagerWindow(parent, ctrl)
    win.present()
    spin()
    yield win
    win.destroy()
    parent.destroy()


@pytest.mark.parametrize("opener,attr", [("_open_palette", "_palette_win"), ("_open_roles_wizard", "_roles_win")])
def test_reopen_after_close(manager, opener, attr):
    getattr(manager, opener)()
    spin()
    first = getattr(manager, attr)
    assert first.get_visible()
    first.close()
    spin()
    assert getattr(manager, attr) is None
    getattr(manager, opener)()
    spin()
    second = getattr(manager, attr)
    assert second is not first and second.get_visible()


def test_second_click_while_open_reuses_window(manager):
    manager._open_palette()
    spin()
    first = manager._palette_win
    manager._open_palette()
    spin()
    assert manager._palette_win is first
