"""La lista de planos se reconstruye entera con cada cambio (al tocar «Dibujar silueta», por ejemplo) y el `ScrolledWindow` volvía
arriba de todo: la fila del brazo desaparecía y quedaba a la vista la de la pierna, que confundía. Ahora se conserva el scroll."""
import dataclasses
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

from afm.gui.controller import Controller, Mode
from afm.gui.window import MainWindow
from afm.project import demo_project
from afm.testfigure import make_doll


def _run_steps(app_id, build, steps, settle_ms=300):
    """Abre la ventana real, corre `build(win, ctrl)` y después cada paso de `steps` dejando que GTK haga el layout entre uno y otro."""
    res = {}
    app = Gtk.Application(application_id=app_id)

    def act(a):
        try:
            ctrl = Controller()
            win = MainWindow(a, ctrl)
            win.present()
            build(win, ctrl)
            queue = list(steps)

            def tick():
                if not queue:
                    a.quit()
                    return GLib.SOURCE_REMOVE
                try:
                    queue.pop(0)(win, ctrl, res)
                except Exception as e:      # noqa: BLE001
                    res["error"] = e
                    a.quit()
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE

            GLib.timeout_add(settle_ms, tick)
        except Exception as e:      # noqa: BLE001
            res["error"] = e
            GLib.idle_add(a.quit)

    app.connect("activate", act)
    app.run([])
    assert "error" not in res, res.get("error")
    return res


@pytest.fixture()
def stl(tmp_path):
    path = str(tmp_path / "muneco.stl")
    make_doll().export(path)
    return path


def _load_with_limbs(stl):
    def build(win, ctrl):
        ctrl.open_stl(stl)
        ctrl.auto_symmetry()
        ctrl.session.project.limbs = demo_project().limbs
        ctrl.session.project.params = dataclasses.replace(ctrl.session.project.params, decimate=0)
        win._refresh()
    return build


def _limb(ctrl, kind):
    return next(l.name for l in ctrl.session.project.limbs if l.kind == kind)


def _silhouette_buttons(win):
    """Botones «Dibujar/Editar silueta», «2.ª» y «3.ª» de la lista, en orden de aparición (brazo antes que pierna si es el último par)."""
    found = []

    def walk(w):
        c = w.get_first_child()
        while c is not None:
            if isinstance(c, Gtk.Label) and "ilueta" in (c.get_label() or ""):
                btn = c.get_ancestor(Gtk.Button)
                if btn is not None and btn not in found:
                    found.append(btn)
            walk(c)
            c = c.get_next_sibling()
    walk(win.planes_box)
    return found


def _click_like_a_mouse(btn):
    """El clic real le da el foco al botón antes de activarlo; ahí está el salto (al reconstruir la lista el foco se pierde)."""
    btn.grab_focus()
    btn.emit("clicked")


def _bottom(adj):
    return adj.get_upper() - adj.get_page_size()


def test_drawing_a_silhouette_keeps_the_plane_list_where_it_was(stl):
    """Con la lista desplazada hasta abajo (a la vista la fila del brazo), tocar «Dibujar silueta» no la manda arriba de todo."""
    def scroll_down(win, ctrl, res):
        adj = win.planes_scroller.get_vadjustment()
        res["rango"] = (adj.get_upper(), adj.get_page_size())
        adj.set_value(_bottom(adj))
        res["antes"] = adj.get_value()

    def click_arm_silhouette(win, ctrl, res):
        btns = _silhouette_buttons(win)
        assert btns, "no se encontró ningún botón de silueta"
        _click_like_a_mouse(btns[-1])
        assert ctrl.mode == Mode.POLYGON

    def after(win, ctrl, res):
        res["despues"] = win.planes_scroller.get_vadjustment().get_value()

    res = _run_steps("ar.afm.test.scroll1", _load_with_limbs(stl), [scroll_down, click_arm_silhouette, after])
    assert res["rango"][0] > res["rango"][1], "la lista tiene que desbordar para que el scroll exista"
    assert res["antes"] > 0
    assert res["despues"] == pytest.approx(res["antes"], abs=1.0)


def test_each_silhouette_button_of_the_arm_keeps_the_scroll(stl):
    """Lo mismo con la 2.ª y la 3.ª silueta, y al terminar la edición (Esc), que también reconstruye la lista."""
    def scroll_down(win, ctrl, res):
        adj = win.planes_scroller.get_vadjustment()
        adj.set_value(_bottom(adj))
        res["antes"] = adj.get_value()
        res["valores"] = []

    def make_step(**kw):
        def step(win, ctrl, res):
            ctrl.start_polygon(_limb(ctrl, "arm"), **kw)
            win.planes_scroller.get_vadjustment()       # (el controlador ya refrescó la lista)
        return step

    def sample(win, ctrl, res):
        res["valores"].append(win.planes_scroller.get_vadjustment().get_value())

    def end_edit(win, ctrl, res):
        ctrl.cancel()

    steps = [scroll_down,
             make_step(cross=True), sample,
             make_step(cross2=True), sample,
             end_edit, sample]
    res = _run_steps("ar.afm.test.scroll2", _load_with_limbs(stl), steps)
    assert res["antes"] > 0
    assert res["valores"] == [pytest.approx(res["antes"], abs=1.0)] * 3


def test_scroll_of_a_short_list_does_not_break(stl):
    """Sin desborde (poca lista) el valor queda en 0 y reconstruir no tira nada."""
    def rebuild(win, ctrl, res):
        win._rebuild_plane_list()
        win._rebuild_plane_list()

    def after(win, ctrl, res):
        res["despues"] = win.planes_scroller.get_vadjustment().get_value()

    def build(win, ctrl):
        ctrl.open_stl(stl)
        ctrl.auto_symmetry()
        win._refresh()

    res = _run_steps("ar.afm.test.scroll3", build, [rebuild, after])
    assert res["despues"] == 0
