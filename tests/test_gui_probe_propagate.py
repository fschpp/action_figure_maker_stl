"""El diálogo que se abre tras «Usar esta celda» (prueba de humo con la ventana real; necesita GTK4 y pantalla)."""
import json
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

from afm import profile
from afm.catalog import Catalog
from afm.gui.controller import Controller
from afm.gui.window import MainWindow

HONGO = {"formato": "afm-probetas-1", "tipo": "hongo", "pasador": "hombro",
         "celdas": [{"n": 1, "holgura": 0.3, "vastago": 3.9}, {"n": 2, "holgura": 0.4, "vastago": 4.3}]}


def _walk(w):
    yield w
    c = w.get_first_child()
    while c is not None:
        yield from _walk(c)
        c = c.get_next_sibling()


def test_use_cell_opens_the_dialog_and_apply_propagates(tmp_path):
    cat = Catalog.create(str(tmp_path / "linea"), "linea", 100.0, 0.4)
    man = tmp_path / "probetas.json"
    man.write_text(json.dumps(HONGO), encoding="utf-8")
    res = {}
    app = Gtk.Application(application_id="ar.afm.test.propagar")

    def act(a):
        try:
            ctrl = Controller()
            win = MainWindow(a, ctrl)
            assert ctrl.open_catalog(cat.root)
            ctrl.load_probe_manifest(str(man))
            win._probe_refresh()
            win.dd_probe_cell.set_selected(1)
            win.ent_profile.set_text("probada")
            win._on_probe_use()                                     # «Usar esta celda»
            dlg = next(w for w in Gtk.Window.list_toplevels() if (w.get_title() or "").startswith("Celda 2"))
            res["checks"] = [b.get_label() for b in _walk(dlg) if isinstance(b, Gtk.CheckButton)]
            ok = next(b for b in _walk(dlg) if isinstance(b, Gtk.Button) and b.get_label() == "Aplicar")
            chk_def = next(b for b in _walk(dlg) if isinstance(b, Gtk.CheckButton) and "por defecto" in (b.get_label() or ""))
            chk_def.set_active(True)
            ok.emit("clicked")
            res["message"] = ctrl.message
        except Exception as e:      # noqa: BLE001
            res["error"] = e
        GLib.idle_add(a.quit)

    app.connect("activate", act)
    app.run([])
    assert "error" not in res, res.get("error")
    assert len(res["checks"]) == 3                                   # perfil, el catálogo abierto y por defecto
    assert "probada" in res["message"] and cat.name in res["message"]
    assert Catalog.open(cat.root).params.shoulder_pin.shaft_d == 4.3
    assert profile.app_defaults().clearance == 0.4
