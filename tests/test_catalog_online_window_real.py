"""D18 · la ventana «Explorar catálogos en línea…» con GTK 4 real: lista con filtros, ficha con portada, estados, modo sin red y alta de un índice.

Necesita GTK 4 y una pantalla (`xvfb-run -a pytest tests/test_catalog_online_window_real.py`); si no, se saltea. La lógica (qué se lista, qué dice
cada estado, la compatibilidad…) ya se prueba sin GTK en `test_catalog_online.py`: acá solo se comprueba que la ventana la dibuja y no se congela.
Los índices, las portadas y las fichas salen de un `http.server` local."""
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

from afm import remoto as R  # noqa: E402
from afm.gui import catalog_online as C  # noqa: E402
from afm.gui.catalog_online_window import OnlineCatalogWindow  # noqa: E402
from afm.i18n import set_language, tr  # noqa: E402
from test_catalog_online import COVER, net, png_bytes, serve  # noqa: E402,F401
from test_dependencias import ent, entorno  # noqa: E402,F401  (entorno es autouse)
from test_instalar import dest  # noqa: E402,F401
from test_remoto import POLICY, srv  # noqa: E402,F401


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


def titles(win):
    return [r.card.titulo for r in win._rows]


def open_win(srv, tmp_path, **kw):
    st = C.OnlineState([srv.base], kw.pop("root", ""), **net(tmp_path))
    win = OnlineCatalogWindow(None, state=st, **kw)
    win.present()
    pump(lambda: win._task is None and win._cards is not None and (win._rows or st.errors or st.indexes))
    return win


@pytest.fixture()
def win(srv, tmp_path):
    serve(srv, ent("guemes", nombre="Güemes", tipo="heroe", licencia="CC-BY-4.0", categorias=["heroes"], descargas={"total": 40}),
          ent("espada", nombre="Espada", tipo="piezas", licencia="CC0-1.0", categorias=["armas"], descargas={"total": 5}),
          ent("vieja", nombre="Vieja", tipo="piezas", licencia="CC0-1.0", categorias=["armas"], descargas=None, motor=">=99"))
    srv.routes["/data/paquetes/owner@guemes/portada.png"] = (200, {}, png_bytes(16, 9))
    w = open_win(srv, tmp_path)
    yield w
    w.destroy()


def select(win, title):
    for row in win._rows:
        if row.card.titulo == title:
            win.listbox.select_row(row)
            pump()
            return row
    raise AssertionError(f"{title} no está en la lista")


def test_lista_los_paquetes_ordenados_por_descargas_con_el_aviso_de_no_verificado(win):
    assert titles(win) == ["Güemes", "Espada", "Vieja"]                       # los sin contador, al final
    assert win.lbl_count.get_text() == tr("catalog_online_window.n_paquetes", n=3)
    assert tr("catalog_online.aviso_no_verificado") in win.lbl_notices.get_text()
    assert win.progress_row.get_visible() is False and win.btn_refresh.get_sensitive()


def test_filtros_de_la_ventana_llegan_a_la_lista(win):
    win.entry.set_text("espada")
    pump(lambda: titles(win) == ["Espada"])
    win.entry.set_text("")
    pump(lambda: len(titles(win)) == 3)
    dd = win._dropdowns["tipo"]
    dd.set_selected(win._dd_values["tipo"].index("piezas"))
    pump(lambda: set(titles(win)) == {"Espada", "Vieja"})
    dd.set_selected(0)
    win.dd_sort.set_selected(R.SORTS.index("nombre"))
    pump(lambda: titles(win) == ["Espada", "Güemes", "Vieja"])
    win.chk_compat.set_active(True)                                          # «Vieja» pide un motor que esta versión no tiene
    pump(lambda: "Vieja" not in titles(win))
    assert win.lbl_count.get_text() == tr("catalog_online_window.n_paquetes", n=2)


def test_los_desplegables_ofrecen_solo_lo_que_hay(win):
    assert win._dd_values["tipo"] == ["", "heroe", "piezas"] and win._dd_values["licencia"] == ["", "CC-BY-4.0", "CC0-1.0"]
    assert win._dropdowns["categoria"].get_model().get_n_items() == 3        # Todos + heroes + armas


def test_la_ficha_muestra_estado_filas_y_la_portada(win):
    assert "Elegí" in " ".join(texts(win.detail_box))
    select(win, "Güemes")
    shown = texts(win.detail_box)
    assert "Güemes" in shown and any("1.0.0" in t and "No instalado" in t for t in shown) and "CC-BY-4.0" in shown
    assert tr("catalog_online.aviso_no_verificado") in shown
    pump(lambda: win.cover.get_visible())                                    # la portada baja en un hilo y aparece sola
    assert win.cover.get_paintable().get_intrinsic_width() == 16
    assert win.btn_install.get_sensitive() and win.btn_install.get_label() == tr("catalog_online_window.instalar")


def test_incompatible_se_marca_en_la_tarjeta_y_la_ficha(win):
    row = next(r for r in win._rows if r.card.titulo == "Vieja")
    assert any(tr("catalog_online_window.no_encaja") in t for t in texts(row.get_child()))
    select(win, "Vieja")
    assert any(">=99" in t for t in texts(win.detail_box))


def test_instalar_llama_al_gancho_con_la_entrada(srv, tmp_path):
    serve(srv, ent("guemes", nombre="Güemes"))
    got = []
    w = open_win(srv, tmp_path, on_install=lambda e, c: got.append((e.id, c.estado)))
    try:
        select(w, "Güemes")
        w.btn_install.emit("clicked")
        assert got == [("guemes", C.NO_INSTALADO)]
    finally:
        w.destroy()


def test_sin_gancho_abre_el_dialogo_de_confirmacion_y_no_escribe_nada(srv, tmp_path, dest):
    serve(srv, ent("guemes", nombre="Güemes"))
    w = open_win(srv, tmp_path, root=dest.root)
    try:
        select(w, "Güemes")
        w.btn_install.emit("clicked")                                          # D19: abre el diálogo (acá el zip de la entrada no existe: falla sin escribir)
        d = w.install_dialog
        pump(lambda: d._task is None)
        assert d.flow.stage in ("fallo", "confirmando") and not d.btn_install.get_sensitive()
        d.destroy()
    finally:
        w.destroy()


def test_bloqueado_e_inaccesible_se_ocultan_y_con_la_casilla_se_ven_sin_poder_instalarse(srv, tmp_path):
    serve(srv, ent("roto", nombre="Roto", update_check="inaccesible"), ent("malo", nombre="Malo"), ent("bueno", nombre="Bueno"),
          bloqueados=[{"id": "malo", "motivo": "retirado por sus autores", "fecha": "2026-11-01"}])
    w = open_win(srv, tmp_path)
    try:
        assert titles(w) == ["Bueno"]
        w.chk_hidden.set_active(True)
        pump(lambda: len(titles(w)) == 3)
        for title in ("Roto", "Malo"):
            select(w, title)
            assert not w.btn_install.get_sensitive() and not w.btn_compat.get_sensitive(), title
        select(w, "Roto")
        assert any("Inaccesible" in t for t in texts(w.detail_box)) and any("inaccesible" in t for t in texts(w.detail_box))
        select(w, "Malo")
        assert any("retirado por sus autores" in t for t in texts(w.detail_box))
        select(w, "Bueno")
        assert w.btn_install.get_sensitive()
    finally:
        w.destroy()


def test_sin_red_usa_el_indice_guardado_y_lo_dice(srv, tmp_path):
    serve(srv, ent("guemes", nombre="Güemes"))
    w = open_win(srv, tmp_path)                                               # primera vez, con red: queda guardado
    w.destroy()
    srv.routes["/data/index.json"] = (503, {}, b"")                             # el servidor ya no responde
    w2 = open_win(srv, tmp_path)
    try:
        assert titles(w2) == ["Güemes"] and "Sin red" in w2.lbl_notices.get_text()
        w2.chk_offline.set_active(True)                                       # y el modo «Sin red» ni lo intenta
        pump(lambda: w2._task is None and "Sin red" in w2.lbl_notices.get_text())
        n = len(srv.log)
        w2.btn_refresh.emit("clicked")
        pump(lambda: w2._task is None)
        assert len(srv.log) == n and titles(w2) == ["Güemes"]
    finally:
        w2.destroy()


def test_un_indice_caido_se_avisa_sin_romper_la_ventana(srv, tmp_path):
    w = open_win(srv, tmp_path)                                               # el servidor no tiene index.json
    try:
        assert w._rows == [] and "index.json" in w.lbl_notices.get_text()
    finally:
        w.destroy()


def test_la_compatibilidad_se_mide_en_un_hilo_y_se_muestra(srv, tmp_path, monkeypatch):
    serve(srv, ent("guemes", nombre="Güemes"))
    w = open_win(srv, tmp_path)
    try:
        monkeypatch.setattr(C.OnlineState, "compat_lines", lambda self, e, *, progress=None: ["Compatibilidad con tu catálogo: 1 de 1 pieza encajan"])
        select(w, "Güemes")
        w.btn_compat.emit("clicked")
        pump(lambda: "1 de 1" in w.lbl_compat.get_text())
        assert w.btn_compat.get_sensitive()
    finally:
        w.destroy()


def test_agregar_indice_valida_y_recarga(srv, tmp_path):
    serve(srv, ent("guemes", nombre="Güemes"))
    w = open_win(srv, tmp_path)
    try:
        w.btn_add_index.emit("clicked")
        pump()
        dlg = next(t for t in Gtk.Window.list_toplevels() if isinstance(t, Gtk.Window) and t.get_title() == tr("catalog_online_window.indice_titulo"))
        entry = next(x for x in walk(dlg) if isinstance(x, Gtk.Entry))
        ok = next(x for x in walk(dlg) if isinstance(x, Gtk.Button) and x.get_label() == tr("catalog_online_window.agregar"))
        entry.set_text("owner/repo")
        ok.emit("clicked")
        assert any(tr("catalog_online.indice_owner_repo") == t for t in texts(dlg))          # el error se ve en el diálogo y no se cierra
        assert dlg.get_visible()
        entry.set_text(srv.base + "/")
        ok.emit("clicked")                                                    # ya está en la lista: otro error, no se duplica
        assert any("ya está" in t for t in texts(dlg))
        dlg.destroy()
        assert R.load_config() == []                                          # lo rechazado no se guardó
    finally:
        w.destroy()


def test_cerrar_mientras_trabaja_cancela_la_tarea(srv, tmp_path):
    started = []

    def slow(self):
        started.append(1)
        time.sleep(0.3)

    serve(srv, ent("guemes"))
    st = C.OnlineState([srv.base], "", **net(tmp_path))
    w = OnlineCatalogWindow(None, state=st, refresh_on_open=False)
    w.present()
    pump()
    t = C.Task(lambda task: [task.progress(i, 100, "x") or time.sleep(0.02) for i in range(100)], notify=w._notify)
    w._start(t, None)
    w.close()
    assert t.join(5) and t.status == C.CANCELLED
    w.destroy()


def test_el_boton_de_la_pestana_catalogo_abre_una_sola_ventana(tmp_path, monkeypatch):
    from afm.gui.controller import Controller
    from afm.gui.window import Application, MainWindow

    monkeypatch.setenv("AFM_GUI_CONFIG", str(tmp_path / "gui.json"))
    app = Application()
    app.register(None)
    main = MainWindow(app, Controller())
    main.present()
    pump()
    try:
        assert main.btn_cat_online.get_label() == tr("catalogo.explorar_catalogos_en_linea")
        monkeypatch.setattr(OnlineCatalogWindow, "refresh", lambda self: None)             # sin red: no se leen índices
        main.btn_cat_online.emit("clicked")
        first = main._online_win
        assert first is not None and isinstance(first, OnlineCatalogWindow)
        main.btn_cat_online.emit("clicked")
        assert main._online_win is first
        first.close()
        assert main._online_win is None
    finally:
        main.destroy()
