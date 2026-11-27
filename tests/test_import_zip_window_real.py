"""D22 · importar un `.zip` con GTK 4 real: el mismo diálogo de confirmación que «Instalar» de «Explorar», para un paquete y para un zip heredado.

Necesita GTK 4 y una pantalla (`xvfb-run -a pytest tests/test_import_zip_window_real.py`); si no, se saltea. La lógica (qué se muestra, qué se escribe,
qué pasa si falla) ya se prueba sin GTK en `test_import_zip.py`: acá solo se comprueba que la ventana la dibuja, que el formulario de un zip heredado
frena hasta tener los datos, que no se escribe nada hasta «Instalar» y que cerrar limpia la carpeta de trabajo."""
import os

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GObject, Gtk
except (ImportError, ValueError):
    pytest.skip("sin GTK4", allow_module_level=True)
if Gdk.Display.get_default() is None:
    pytest.skip("sin pantalla (usar xvfb-run)", allow_module_level=True)

from afm import instalar as I  # noqa: E402
from afm import paquete as Q  # noqa: E402
from afm.catalog import Catalog  # noqa: E402
from afm.gui import import_zip_window as IW  # noqa: E402
from afm.gui import install_confirm as IC  # noqa: E402
from afm.i18n import set_language, tr  # noqa: E402
from test_catalog_online_window_real import pump, texts, walk  # noqa: E402,F401
from test_dependencias import entorno  # noqa: E402,F401  (autouse: aísla ~/.local/share, la caché y la configuración)
from test_exportar import add_arm, cat  # noqa: E402,F401
from test_instalar import dest, export  # noqa: E402,F401
from test_instalar_heredado import EJEMPLO, JUNK, files_of, zip_dir  # noqa: E402


@pytest.fixture(autouse=True)
def _idioma():
    set_language("es")
    yield
    set_language("es")


@pytest.fixture
def alerts(monkeypatch):
    """Los avisos (`Gtk.AlertDialog`) se anotan en vez de mostrarse."""
    seen: list = []
    monkeypatch.setattr(IW, "_alert", lambda parent, message, detail="": seen.append((message, detail)))
    return seen


def ready(d):
    pump(lambda: d._task is None)
    return d


def pick(dd, values, value):
    dd.set_selected(values.index(value) + 1)


# ------------------------------------------------------------------------------------------------------------------------ un paquete

def test_un_paquete_muestra_la_confirmacion_no_escribe_hasta_instalar_y_deja_el_informe(cat, dest, tmp_path, alerts):
    add_arm(cat)
    z = export(cat, tmp_path)
    before = I.tree_digest(dest.root)
    done = []
    d = IW.open_import(None, dest.root, z, on_installed=lambda results: done.append(len(results)))
    try:
        ready(d)
        t = texts(d)
        assert d.flow.stage == IC.CONFIRMANDO and d.lbl_title.get_text().startswith("Instalar «")
        assert {"Autor", "Licencia", "Origen", "Línea de destino", "Carpeta", "Archivo", "Tamaño"} <= set(t) and os.path.abspath(z) in t and "Descarga" not in t
        assert any(x.startswith("Importado desde un archivo") for x in t)                          # no pasó por ningún índice: lo dice
        assert d.btn_install.get_visible() and d.btn_install.get_sensitive() and I.tree_digest(dest.root) == before
        assert not any(isinstance(x, Gtk.Button) and x.get_label() == tr("import_zip_window.cambiar_datos") for x in walk(d))     # un paquete no tiene formulario
        d.btn_install.emit("clicked")
        pump(lambda: d.flow.stage == IC.LISTO and d._task is None)
        assert done == [1] and tr("install_confirm_window.verificar_ok") in texts(d) and d.lbl_title.get_text() == f"Instalado: {d.flow.name}"
        assert Catalog.open(dest.root).verify().ok and not alerts
    finally:
        d.destroy()


def test_cancelar_antes_de_instalar_no_escribe_nada(cat, dest, tmp_path, alerts):
    add_arm(cat)
    z = export(cat, tmp_path)
    before = I.tree_digest(dest.root)
    closed = []
    d = IW.open_import(None, dest.root, z, on_closed=lambda: closed.append(1))
    ready(d)
    d.btn_close.emit("clicked")                                                                      # «Cancelar»
    pump(lambda: closed)
    assert I.tree_digest(dest.root) == before


# ------------------------------------------------------------------------------------------------------------------------ zip heredado (D07)

def test_un_zip_heredado_pide_los_datos_antes_de_proponer_y_se_instala_sin_perder_nada(tmp_path, alerts):
    h = zip_dir(EJEMPLO, tmp_path / "heredado.zip", "proceres-127/", extra={**JUNK})
    destino = tmp_path / "destino"
    d = IW.open_import(None, str(destino), str(h))
    try:
        assert d.flow.pide_datos and d._task is None and d.flow.stage == IC.INICIO                  # el formulario va primero: todavía no se propuso nada
        assert not d.btn_install.get_visible() and tr("import_zip_window.datos_titulo") in texts(d)
        d.txt_autores.get_buffer().set_text("")
        d._on_form_go()                                                                              # sin autor, licencia ni origen: no avanza
        assert d.flow.pide_datos and d._task is None and any("Falta" in x for x in texts(d))
        d.txt_autores.get_buffer().set_text("Ana Pérez:owner")
        pick(d.dd_lic, Q.LICENSES, "CC-BY-4.0")
        pick(d.dd_org, Q.ORIGINS, "propia")
        assert d.lbl_lic.get_text() and d.lbl_org.get_text()                                         # cada opción se explica
        d._on_form_go()
        ready(d)
        t = texts(d)
        assert d.flow.stage == IC.CONFIRMANDO and "Tipo" in t and "CC-BY-4.0" in t and any("no trae ficha de paquete" in x for x in t)
        assert d.btn_install.get_sensitive() and not os.path.exists(destino)                         # proponer no escribe
        work = d.flow._work
        assert os.path.isdir(work)
        d._on_change_data()                                                                          # «Cambiar los datos»: vuelve al formulario sin escribir
        assert d.flow.pide_datos and d.flow.stage == IC.INICIO and not os.path.exists(destino)
        d._on_form_go()                                                                              # los datos quedaron cargados en el formulario
        ready(d)
        d.btn_install.emit("clicked")
        pump(lambda: d.flow.stage == IC.LISTO and d._task is None)
        assert Catalog.open(str(destino)).verify().ok and files_of(str(destino)) == files_of(EJEMPLO)
        d.close()
        pump()
        assert not os.path.exists(work)                                                              # cerrar borra la carpeta de trabajo
    finally:
        d.destroy()


# ------------------------------------------------------------------------------------------------------------------------ lo que no se puede importar y la cola

def test_un_archivo_que_no_se_puede_importar_avisa_el_motivo_y_no_abre_nada(tmp_path, alerts):
    roto = tmp_path / "roto.zip"
    roto.write_bytes(b"no soy un zip")
    closed = []
    assert IW.open_import(None, str(tmp_path / "c"), str(roto), on_closed=lambda: closed.append(1)) is None
    assert closed == [1] and len(alerts) == 1 and "roto.zip" in alerts[0][0] and alerts[0][1]


def test_varios_archivos_soltados_se_abren_de_a_uno_y_lo_que_no_es_zip_se_informa(cat, dest, tmp_path, alerts):
    add_arm(cat)
    z1, z2 = export(cat, tmp_path, "uno.zip"), export(cat, tmp_path, "dos.zip", version="1.0.1")
    stl = tmp_path / "figura.stl"
    stl.write_bytes(b"solid")
    q = IW.ImportQueue(None, lambda: dest.root)
    zips, otros = q.add([z1, str(stl), z2])
    assert zips == [z1, z2] and otros == [str(stl)]
    assert alerts[0][0] == tr("import_zip_window.soltado_ignorado_titulo") and "figura.stl" in alerts[0][1]
    assert alerts[1][0] == tr("import_zip_window.varios", n=2)
    first = q.current
    try:
        assert first is not None and first.flow.zip_path == os.path.abspath(z1) and q.pending == [z2]    # el segundo espera
        ready(first)
        first.btn_close.emit("clicked")
        pump(lambda: q.current is not None and q.current is not first)
        second = q.current
        assert second.flow.zip_path == os.path.abspath(z2) and q.pending == []
        ready(second)
        second.btn_close.emit("clicked")
        pump(lambda: not q.busy)
    finally:
        for dlg in (first, q.current):
            if dlg is not None:
                dlg.destroy()


def test_soltar_archivos_sobre_una_ventana_llega_a_la_cola(cat, dest, tmp_path, alerts):
    add_arm(cat)
    z = export(cat, tmp_path)
    win = Gtk.Window()
    q = IW.ImportQueue(win, lambda: dest.root)
    target = IW.attach_drop(win, q)
    assert target in [c for c in win.observe_controllers()]                                          # la ventana acepta soltar archivos
    value = GObject.Value()
    value.init(Gdk.FileList)
    value.set_boxed(Gdk.FileList.new_from_list([Gio.File.new_for_path(z)]))
    try:
        assert target.emit("drop", value, 0.0, 0.0) is True                                               # el destino dice que aceptó lo soltado
        pump(lambda: q.current is not None)
        assert q.current.flow.zip_path == os.path.abspath(z)
    finally:
        if q.current is not None:
            q.current.destroy()
        win.destroy()


# ------------------------------------------------------------------------------------------------------------------------ enchufado en «Administrar catálogo»

def test_el_menu_compartir_ofrece_importar_acepta_soltar_y_publicar_abre(tmp_path, alerts):
    import shutil

    from afm.gui.catalog_window import CatalogManagerWindow
    from afm.gui.controller import Controller

    root = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, root, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    ctrl = Controller()
    assert ctrl.open_catalog(root)
    parent = Gtk.Window()
    w = CatalogManagerWindow(parent, ctrl)
    w.present()
    pump()
    try:
        labels = {b.get_label(): b for b in walk(w.menu_share.get_popover()) if isinstance(b, Gtk.Button)}
        assert tr("import_zip_window.menu_importar") in labels and tr("publish_window.menu_publicar") in labels
        assert labels[tr("import_zip_window.menu_importar")].get_sensitive()                        # importar no depende de lo que esté ocupado
        assert w._zip_drop in list(w.observe_controllers()) and w._zip_queue.parent is w
        w._open_publish()                                                                           # D21 usaba `ctrl.catalog_root`, que no existe
        assert w._publish_win is not None
        w._publish_win.destroy()
    finally:
        w.destroy()
        parent.destroy()
