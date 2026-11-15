"""Selector de idioma de la cabecera: la ventana se arma de nuevo en el idioma elegido con el mismo controlador."""
import os

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, GLib, Gtk
except (ImportError, ValueError):
    pytest.skip("sin GTK4", allow_module_level=True)
if Gdk.Display.get_default() is None:
    pytest.skip("sin pantalla (usar xvfb-run)", allow_module_level=True)

from afm import i18n  # noqa: E402
from afm.gui import settings  # noqa: E402
from afm.gui.controller import Controller  # noqa: E402
from afm.gui.window import Application, MainWindow  # noqa: E402


def _labels(widget, out):
    for attr in ("get_label", "get_text"):
        f = getattr(widget, attr, None)
        if f:
            try:
                s = f()
            except Exception:  # noqa: BLE001
                s = None
            if isinstance(s, str) and s.strip():
                out.append(s)
    c = widget.get_first_child()
    while c is not None:
        _labels(c, out)
        c = c.get_next_sibling()
    return out


def _pump(n=20):
    ctx = GLib.MainContext.default()
    for _ in range(n):
        ctx.iteration(False)


def test_switch_language_rebuilds_window_with_same_controller(tmp_path, monkeypatch):
    monkeypatch.setenv("AFM_GUI_CONFIG", str(tmp_path / "gui.json"))
    i18n.set_language("es")
    app = Application()
    app.register(None)
    ctrl = Controller()
    app.win = MainWindow(app, ctrl)
    app.win.present()
    _pump()
    old = app.win
    assert "Guardar" in _labels(old, [])
    app.switch_language("en")
    _pump()
    assert app.win is not old and app.win.ctrl is ctrl
    labels = _labels(app.win, [])
    assert "Save" in labels and "Guardar" not in labels
    assert settings.language() == "en"
    app.switch_language("pt")
    _pump()
    assert "Salvar" in _labels(app.win, [])
    i18n.set_language("es")


def test_language_menu_lists_three_languages(tmp_path, monkeypatch):
    monkeypatch.setenv("AFM_GUI_CONFIG", str(tmp_path / "gui.json"))
    i18n.set_language("es")
    app = Application()
    app.register(None)
    win = MainWindow(app, Controller())
    assert set(win.btn_lang) == {"es", "en", "pt"}
