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


def click_center(win, ed):
    """Clic y soltar sobre el centro de la pieza en pantalla (sin arrastrar)."""
    cam = ed.camera(win._img_size, 0, 0, 1.0, (0, 0))
    px, py = cam.project(np.asarray(cam.target, float), *win._img_size)[0, :2]
    w, h = win._widget_size()
    k = min(w / win._img_size[0], h / win._img_size[1])
    x, y = (w - win._img_size[0] * k) / 2 + px * k, (h - win._img_size[1] * k) / 2 + py * k
    win._on_drag_begin(FakeGesture(), x, y)
    assert win._rpaint
    win._on_drag_end(None, 0, 0)


def wait_for(cond, seconds=30):
    end = time.time() + seconds
    while time.time() < end and not cond():
        pump(5)
    return cond()


def test_fill_mode_one_click_shows_the_result_and_applies(win, line):
    import json
    import os

    from afm.gui.retouch_session import FILL

    win._select_ref(REF)
    pump(30)
    win._on_retouch_start()
    ed = win._reditor
    win.dd_rrole.set_selected(len(ed.session.palette_ids) - 1)
    win.tgl_rfill.set_active(True)
    assert ed.session.mode == FILL
    win.spin_rreach.set_value(12.0)
    assert ed.session.reach == 12.0
    click_center(win, ed)
    assert len(ed.session) == 1 and ed.session.retouch().strokes[0].relleno and ed.session.retouch().strokes[0].r == 12.0
    assert wait_for(lambda: ed.result_shown), "el relleno no mostró el color real tras el clic"
    win.dd_rrole.set_selected(0)                                           # cambiar de rol no saca del modo Rellenar
    assert ed.session.mode == FILL and win.tgl_rfill.get_active()
    win._on_retouch_apply()
    pump(20)
    f = Catalog.open(line).meta(REF).color["retoque"]
    assert f["n"] == 1
    assert json.load(open(os.path.join(line, "piezas", "cabeza", f["fuente"])))["formato"] == "afm-retoque-2"


@pytest.fixture()
def arm_with_retouch(line):
    """Otra pieza con color por roles y un retoque (pincel + relleno), ya guardado antes de abrir la ventana."""
    from afm.retouch import Retouch, Stroke
    from test_color_catalog_palette import ARM_ID, with_roles

    cat = Catalog.open(line)
    ref = with_roles(cat, ARM_ID).ref
    cat.set_retouch(ref, Retouch([Stroke((4.0, 1.0, 2.0), 1.5, "oro", (1, 0, 0)), Stroke((4.0, 1.0, 2.0), 6.0, "oro", (1, 0, 0), relleno=True)]))
    return ref


def test_bring_from_another_piece_mirrored_is_one_undo_step(arm_with_retouch, win, line):
    win._select_ref(REF)
    pump(30)
    win._on_retouch_start()
    ed = win._reditor
    assert arm_with_retouch in win._rsources and win.btn_rbring.get_sensitive()
    win.dd_rsource.set_selected(win._rsources.index(arm_with_retouch))
    win.chk_rmirror.set_active(True)
    win._on_retouch_bring()
    got = ed.session.retouch().strokes
    assert len(got) == 2 and got[0].c[0] == pytest.approx(-4.0) and got[1].relleno and got[1].n[0] == pytest.approx(-1.0)
    win._on_retouch_undo()
    assert len(ed.session) == 0                                            # las dos pinceladas, de un solo paso
    win._on_retouch_redo()
    assert len(ed.session) == 2
    win.chk_rmirror.set_active(False)
    win._on_retouch_bring()                                                # sin espejar se suma tal cual (a lo ya traído)
    assert len(ed.session) == 4 and ed.session.retouch().strokes[2].c[0] == pytest.approx(4.0)


def _paint_stroke(win, ed):
    cam = ed.camera(win._img_size, 0, 0, 1.0, (0, 0))
    px, py = cam.project(np.asarray(cam.target, float), *win._img_size)[0, :2]
    w, h = win._widget_size()
    k = min(w / win._img_size[0], h / win._img_size[1])
    x, y = (w - win._img_size[0] * k) / 2 + px * k, (h - win._img_size[1] * k) / 2 + py * k
    win._on_drag_begin(FakeGesture(), x, y)
    assert win._rpaint
    win._on_drag(None, 15, 0)
    win._on_drag_end(None, 15, 0)


def test_retouch_release_shows_result_automatically(win, line):
    """Al soltar el botón del pincel (pintar) se calcula solo «Ver resultado», sin apretar el botón."""
    win._select_ref(REF)
    pump(30)
    win._on_retouch_start()
    ed = win._reditor
    win.dd_rrole.set_selected(len(ed.session.palette_ids) - 1)
    _paint_stroke(win, ed)
    assert win._rbusy == 1 or ed.result_shown          # arrancó solo el cálculo en el hilo
    for _ in range(100):
        pump(5)
        if ed.result_shown and not win._rbusy:
            break
    assert ed.result_shown and win._color_shown is not None and win._color_shown[0] == REF


def test_retouch_release_while_busy_recalculates(win, line):
    """Si se suelta el pincel con un cálculo en curso, al terminar se recalcula con los trazos nuevos."""
    win._select_ref(REF)
    pump(30)
    win._on_retouch_start()
    ed = win._reditor
    win.dd_rrole.set_selected(len(ed.session.palette_ids) - 1)
    win._rbusy = 1                                      # simula un cálculo en curso
    _paint_stroke(win, ed)
    assert win._rview_pending and not ed.result_shown
    win._rbusy = 0
    win._retouch_view_done(ed.meta.ref, None, None)      # termina el cálculo viejo → lanza el nuevo
    assert not win._rview_pending and win._rbusy == 1
    for _ in range(100):
        pump(5)
        if not win._rbusy:
            break
    assert ed.result_shown


def test_detail_rows_wrap_when_window_is_narrow(win, line):
    """Los botones del detalle pasan a otra línea en vez de recortarse o ensanchar la ventana (WrapRow)."""
    from afm.gui.widgets import WrapRow
    assert isinstance(win.btn_retouch.get_parent(), WrapRow)
    win.set_default_size(900, 700)
    pump(20)
    assert win.get_width() <= 900 or win.is_maximized()


def test_window_minimum_size_allows_maximizing(win, line):
    """La altura mínima de la ventana no depende de los botones apilados del detalle: se puede maximizar y achicar."""
    win._select_ref(REF)
    pump(30)
    min_h = win.get_child().measure(Gtk.Orientation.VERTICAL, -1)[0]
    assert min_h <= 600, f"altura mínima {min_h}px: la ventana no entraría en una pantalla chica ni se podría maximizar"
