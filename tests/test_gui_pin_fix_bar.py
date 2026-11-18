"""La barra del visor pregunta «¿achicar el pasador?» con botones reales (necesita GTK4 y pantalla: xvfb-run)."""
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
from afm.gui.window import MainWindow
from afm.project import demo_project
from afm.testfigure import make_doll


def _run(stl, scenario, app_id):
    res = {}
    app = Gtk.Application(application_id=app_id)

    def act(a):
        try:
            ctrl = Controller()
            win = MainWindow(a, ctrl)
            ctrl.open_stl(stl)
            ctrl.auto_symmetry()
            for l in demo_project().limbs:
                ctrl.session.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
            ctrl.session.set_limb_polygon("brazo", next(l for l in demo_project().limbs if l.name == "brazo").polygon)
            ctrl.session.limb("brazo").cut_offset = -3.0           # cara de Ø5,2 mm
            ctrl.set_param("shoulder_pin.shaft_d", 6.0)
            ctrl.set_param("shoulder_pin.head_d", 8.0)
            scenario(ctrl, win, res)
        except Exception as e:      # noqa: BLE001
            res["error"] = e
        GLib.idle_add(a.quit)

    app.connect("activate", act)
    app.run([])
    assert "error" not in res, res.get("error")
    return res


@pytest.fixture(scope="module")
def stl(tmp_path_factory):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path_factory.mktemp("bar") / "muneco.stl")
    m.export(path)
    return path


def _vis(win):
    return {k: b.get_visible() for k, b in win.mode_buttons.items() if k in ("fix_ok", "fix_no")}


def test_buttons_appear_with_the_question_and_accept_applies(stl):
    def scenario(ctrl, win, res):
        res["before"] = _vis(win)
        ctrl.start_pin("brazo")
        win._refresh()
        res["during"] = _vis(win)
        res["hint"] = win.mode_hint.get_text()
        res["shaft_pending"] = ctrl.get_param("shoulder_pin.shaft_d")
        win.mode_buttons["fix_ok"].emit("clicked")
        win._refresh()
        res["after"] = _vis(win)
        res["shaft_after"] = ctrl.get_param("shoulder_pin.shaft_d")

    r = _run(stl, scenario, "ar.afm.pinfix1")
    assert r["before"] == {"fix_ok": False, "fix_no": False}
    assert r["during"] == {"fix_ok": True, "fix_no": True}
    assert "¿Achicarlo" in r["hint"] and "catálogo" in r["hint"]
    assert r["shaft_pending"] == 6.0                                   # preguntar no cambia nada
    assert r["after"] == {"fix_ok": False, "fix_no": False} and r["shaft_after"] < 6.0


def test_reject_button_keeps_the_pin_and_hides_the_question(stl):
    def scenario(ctrl, win, res):
        ctrl.start_pin("brazo")
        win._refresh()
        win.mode_buttons["fix_no"].emit("clicked")
        win._refresh()
        res["after"] = _vis(win)
        res["shaft"] = ctrl.get_param("shoulder_pin.shaft_d")
        res["hint"] = win.mode_hint.get_text()

    r = _run(stl, scenario, "ar.afm.pinfix2")
    assert r["after"] == {"fix_ok": False, "fix_no": False} and r["shaft"] == 6.0 and "¿Achicarlo" not in r["hint"]
