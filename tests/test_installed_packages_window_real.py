"""D20 · la ventana «Paquetes instalados» con GTK 4 real: lista con estados, ficha con Actualizar / Versiones / Quitar y revisión del catálogo.

Necesita GTK 4 y una pantalla (`xvfb-run -a pytest tests/test_installed_packages_window_real.py`); si no, se saltea. La lógica (estados, plan de quitar,
errores atribuidos, piezas que faltan) ya se prueba sin GTK en `test_installed_packages.py`: acá solo se comprueba que la ventana la dibuja."""
import json
import os
import time

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk
except (ImportError, ValueError):
    pytest.skip("sin GTK4", allow_module_level=True)
if Gdk.Display.get_default() is None:
    pytest.skip("sin pantalla (usar xvfb-run)", allow_module_level=True)

from afm import exportar as X  # noqa: E402
from afm import instalar as I  # noqa: E402
from afm.gui import catalog_online as CO  # noqa: E402
from afm.gui.installed_packages_window import InstalledPackagesWindow  # noqa: E402
from afm.i18n import set_language, tr  # noqa: E402
from test_dependencias import ent, entorno, idx  # noqa: E402,F401  (entorno es autouse)
from test_exportar import OWNER, add_arm, add_recipe, cat  # noqa: E402,F401
from test_instalar import dest  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _idioma():
    set_language("es")
    yield
    set_language("es")


def pump(cond=lambda: True, timeout=20.0):
    ctx = GLib.MainContext.default()
    t0 = time.monotonic()
    while True:
        while ctx.iteration(False):
            pass
        if cond():
            return
        if time.monotonic() - t0 > timeout:
            raise AssertionError("se agotó el tiempo esperando")
        time.sleep(0.01)


def walk(w):
    yield w
    c = w.get_first_child()
    while c is not None:
        yield from walk(c)
        c = c.get_next_sibling()


def texts(widget):
    return [x.get_label() for x in walk(widget) if isinstance(x, Gtk.Label) and x.get_label()]


def button(widget, label):
    return next(x for x in walk(widget) if isinstance(x, Gtk.Button) and x.get_label() == label)


@pytest.fixture
def heroe(cat, dest, tmp_path):
    ref = add_arm(cat).ref
    add_recipe(cat, "heroe", {"brazo": ref})
    z = X.export_package(cat, str(tmp_path / "heroe.zip"), recetas=["heroe"], id="heroe", github="owner/heroe", **OWNER).path
    I.apply(I.plan_install(z, dest.root))
    return dest


def open_win(root, *entries):
    st = CO.OnlineState([], root)
    st.indexes = [idx(*entries)] if entries else []
    win = InstalledPackagesWindow(None, st, on_changed=lambda: None)
    win.present()
    pump()
    return win


def test_lista_el_paquete_con_su_estado_y_su_ficha(heroe):
    win = open_win(heroe.root, ent("heroe", "1.1.0", github="owner/heroe", versions=[("1.0.0", False)]))
    assert [r.id for r in win._rows] == ["heroe"] and any("1.1.0" in t for t in texts(win.listbox))
    win.listbox.select_row(win.listbox.get_row_at_index(0))
    pump()
    t = texts(win.detail_box)
    assert "owner/heroe" in t
    assert button(win.detail_box, tr("installed_packages_window.actualizar_a", version="1.1.0")).get_sensitive()
    button(win.detail_box, tr("installed_packages_window.versiones")).emit("clicked")
    pump()
    assert any("1.0.0" in x and "instalada" in x for x in texts(win.versions_box))
    win.destroy()


def test_sin_indice_no_ofrece_actualizar_y_sin_paquetes_lo_dice(heroe, tmp_path):
    win = open_win(heroe.root)
    win.listbox.select_row(win.listbox.get_row_at_index(0))
    pump()
    assert not button(win.detail_box, tr("installed_packages_window.actualizar")).get_sensitive()
    assert not button(win.detail_box, tr("installed_packages_window.versiones")).get_sensitive()
    win.destroy()
    from afm.catalog import Catalog
    empty = Catalog.create(str(tmp_path / "vacio"), "proceres-130", 130.0, 0.4)
    win = open_win(empty.root)
    assert win._rows == [] and tr("installed_packages_window.sin_paquetes") in texts(win.detail_box)
    win.destroy()


def test_revisar_atribuye_el_problema_al_paquete(heroe):
    os.remove(os.path.join(heroe.root, "figuras", "heroe.receta.json"))
    win = open_win(heroe.root)
    win.listbox.select_row(win.listbox.get_row_at_index(0))
    pump()
    button(win, tr("installed_packages_window.revisar")).emit("clicked")
    pump(lambda: win._task is None and heroe.root in win._diagnoses)
    assert any("heroe" in x and "falta" in x for x in texts(win.detail_box))
    win.destroy()
