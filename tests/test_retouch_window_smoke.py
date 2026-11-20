"""R07 · Prueba de humo de la ventana «Administrar catálogo» con el modo «Retocar color…».

Necesita GTK4 y GL (por software sirve): `LIBGL_ALWAYS_SOFTWARE=1 xvfb-run -a pytest tests/test_retouch_window_smoke.py`.
Sin GTK, sin pantalla o sin GL se saltea sola (no bloquea el resto de la suite)."""
import time

import numpy as np
import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
except (ValueError, ImportError, RuntimeError):          # RuntimeError: GTK no pudo abrir pantalla
    pytest.skip("sin GTK4 o sin pantalla (usá xvfb-run)", allow_module_level=True)
pytest.importorskip("moderngl")

from afm.catalog import Catalog  # noqa: E402
from test_catalog_retouch import REF, base_line, line  # noqa: E402,F401


def pump(n=20):
    for _ in range(n):
        while GLib.MainContext.default().iteration(False):
            pass
        time.sleep(0.01)


class FakeGesture:
    def get_current_event_state(self):
        return 0


@pytest.fixture()
def win(line):
    try:
        ok = Gtk.init_check()
        ok = ok[0] if isinstance(ok, tuple) else ok
        parent = Gtk.Window()
    except RuntimeError:
        ok = False
    if not ok:
        pytest.skip("sin pantalla (usá xvfb-run)")
    from afm.gui.catalog_window import CatalogManagerWindow
    from afm.gui.controller import Controller

    ctrl = Controller()
    ctrl.session.catalog_root = line
    w = CatalogManagerWindow(parent, ctrl)
    w.present()
    pump(20)
    if not w.preview.available:
        pytest.skip(f"sin GL: {w.preview.error}")
    yield w
    w.destroy()


def test_retouch_mode_paint_apply(win, line):
    win._select_ref(REF)
    pump(30)
    assert win.btn_retouch.get_sensitive()
    win._on_retouch_start()
    ed = win._reditor
    assert ed is not None and win.retouch_panel.get_visible() and not win.btn_delete.get_sensitive()
    cam = ed.camera(win._img_size, 0, 0, 1.0, (0, 0))
    px, py = cam.project(np.asarray(cam.target, float), *win._img_size)[0, :2]
    w, h = win._widget_size()
    k = min(w / win._img_size[0], h / win._img_size[1])
    x, y = (w - win._img_size[0] * k) / 2 + px * k, (h - win._img_size[1] * k) / 2 + py * k
    win.dd_rrole.set_selected(len(ed.session.palette_ids) - 1)
    win._on_drag_begin(FakeGesture(), x, y)
    assert win._rpaint
    win._on_drag(None, 15, 0)
    win._on_drag_end(None, 15, 0)
    assert ed.dirty and win.btn_rapply.get_sensitive()
    win._on_retouch_apply()
    pump(20)
    assert win._reditor is None and not win.retouch_panel.get_visible()
    assert Catalog.open(line).meta(REF).color["retoque"]["n"] >= 1


def test_retouch_cancel_discards(win, line):
    win._select_ref(REF)
    pump(30)
    win._on_retouch_start()
    win._reditor.session.begin_stroke((0, 0, 0))
    win._reditor.session.end_stroke()
    win._retouch_cancel()
    assert win._reditor is None and Catalog.open(line).retouch(REF) is None
