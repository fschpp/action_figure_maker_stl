"""D19 · el diálogo de confirmación con GTK 4 real: muestra lo que se instala, no escribe hasta «Instalar» y deja el informe.

Necesita GTK 4 y una pantalla (`xvfb-run -a pytest tests/test_install_confirm_window_real.py`); si no, se saltea. La lógica (qué se muestra, qué se
escribe, qué pasa si falla) ya se prueba sin GTK en `test_install_confirm.py`: acá solo se comprueba que la ventana la dibuja, no se congela y
solo escribe después de apretar «Instalar». El índice y los zips salen de un `http.server` local."""
import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk
except (ImportError, ValueError):
    pytest.skip("sin GTK4", allow_module_level=True)
if Gdk.Display.get_default() is None:
    pytest.skip("sin pantalla (usar xvfb-run)", allow_module_level=True)

from afm import dependencias as D  # noqa: E402
from afm import instalar as I  # noqa: E402
from afm.catalog import Catalog  # noqa: E402
from afm.gui import install_confirm as IC  # noqa: E402
from afm.gui.install_confirm_window import InstallConfirmDialog  # noqa: E402
from afm.i18n import set_language, tr  # noqa: E402
from test_catalog_online import state, world  # noqa: E402,F401
from test_catalog_online_window_real import pump, texts, walk  # noqa: E402,F401
from test_dependencias import entorno, set_registry  # noqa: E402,F401  (entorno es autouse)
from test_exportar import cat  # noqa: E402,F401
from test_instalar import dest  # noqa: E402,F401
from test_remoto import srv  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _idioma():
    set_language("es")
    yield
    set_language("es")


def open_dialog(srv, cat, dest, tmp_path, pid="dep-pk", **kw):
    st = world(srv, cat, tmp_path, dest)
    d = InstallConfirmDialog(None, st, st.indexes[0].find(pid), **kw)
    d.present()
    pump(lambda: d._task is None)
    return d


def checks(d):
    return [x for x in walk(d) if isinstance(x, Gtk.CheckButton)]


def test_muestra_la_confirmacion_y_no_escribe_hasta_apretar_instalar(srv, cat, dest, tmp_path):
    before = I.tree_digest(dest.root)
    d = open_dialog(srv, cat, dest, tmp_path)
    try:
        t = texts(d)
        assert d.lbl_title.get_text() == "Instalar «dep-pk» 1.0.0" and d.flow.stage == IC.CONFIRMANDO
        assert {"Autor", "Licencia", "Origen", "Línea de destino", "Carpeta", "Descarga"} <= set(t) and "Ana Pérez (@owner)" in t and "CC-BY-4.0" in t
        assert d.flow.confirmation.trae in t and any(x.startswith("Listado no verificado") for x in t)
        assert d.btn_install.get_visible() and d.btn_install.get_sensitive() and d.btn_install.get_label() == "Instalar"
        assert not d.progress_row.get_visible() and I.tree_digest(dest.root) == before
        d.btn_close.emit("clicked")                                              # «Cancelar»: el catálogo queda como estaba
        pump()
        assert I.tree_digest(dest.root) == before
    finally:
        d.destroy()


def test_instalar_escribe_y_muestra_verificar_y_cobertura(srv, cat, dest, tmp_path):
    done = []
    d = open_dialog(srv, cat, dest, tmp_path, on_installed=lambda results: done.append([r.paquete.id for r in results]))
    try:
        d.btn_install.emit("clicked")
        pump(lambda: d.flow.stage == IC.LISTO and d._task is None)
        t = texts(d)
        assert done == [["base-pk", "dep-pk"]] and d.lbl_title.get_text() == "Instalado: dep-pk" and tr("install_confirm_window.verificar_ok") in t
        assert any("Instalado «dep-pk» 1.0.0" in x for x in t)
        assert not d.btn_install.get_visible() and d.btn_close.get_label() == "Cerrar"
        assert Catalog.open(dest.root).verify().ok and set(I.load_registry(dest.root)["paquetes"]) == {"base-pk", "dep-pk"}
    finally:
        d.destroy()


def test_un_fallo_al_instalar_muestra_el_motivo_y_deja_el_catalogo_como_estaba(srv, cat, dest, tmp_path, monkeypatch):
    d = open_dialog(srv, cat, dest, tmp_path)
    before = I.tree_digest(dest.root)

    def boom(chain, **kw):
        raise OSError("disco lleno")

    monkeypatch.setattr(D, "install_chain", boom)
    try:
        d.btn_install.emit("clicked")
        pump(lambda: d._task is None and d.flow.stage == IC.FALLO)
        assert any("disco lleno" in x for x in texts(d)) and I.tree_digest(dest.root) == before and not d.btn_install.get_visible()
    finally:
        d.destroy()


def test_un_cambio_de_origen_pide_tildar_la_casilla(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    set_registry(dest.root, **{"dep-pk": ("0.5.0", {"github": "otro/repo"})})
    d = InstallConfirmDialog(None, st, st.indexes[0].find("dep-pk"))
    d.present()
    pump(lambda: d._task is None)
    try:
        assert not d.btn_install.get_sensitive() and d.chk_origen is not None
        d.chk_origen.set_active(True)
        assert d.flow.confirmar_origen and d.btn_install.get_sensitive()
        d.btn_install.emit("clicked")
        pump(lambda: d.flow.stage == IC.LISTO and d._task is None)
        assert set(I.load_registry(dest.root)["paquetes"]) >= {"dep-pk"}
    finally:
        d.destroy()


def test_la_propuesta_que_falla_se_muestra_sin_el_boton_de_instalar(srv, cat, dest, tmp_path, monkeypatch):
    st = world(srv, cat, tmp_path, dest)
    monkeypatch.setattr(st, "propose", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin red")))
    d = InstallConfirmDialog(None, st, st.indexes[0].find("dep-pk"))
    d.present()
    pump(lambda: d._task is None)
    try:
        assert any("sin red" in x for x in texts(d)) and not d.btn_install.get_visible() and d.flow.stage == IC.FALLO
    finally:
        d.destroy()


def test_no_se_cierra_mientras_se_escribe_el_catalogo(srv, cat, dest, tmp_path):
    d = open_dialog(srv, cat, dest, tmp_path)
    try:
        d.flow.stage = IC.INSTALANDO
        d._task = type("T", (), {"finished": False, "cancel": lambda self: False})()
        assert d._on_close() is True                                             # la instalación no se corta a la mitad
        d._task = None
        d.flow.stage = IC.CONFIRMANDO
        assert d._on_close() is False
    finally:
        d.destroy()
