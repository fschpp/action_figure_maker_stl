"""Ventana «Explorar catálogos en línea…» (D18): lista con filtros, ficha con portada y compatibilidad, estados y modo sin red.

Solo dibuja: todo lo que decide (qué se lista, qué dice cada estado, qué se puede instalar, la compatibilidad, la portada, el alta de un índice)
sale de `catalog_online.OnlineState`, que se prueba sin GTK. Lo que tarda o toca la red (leer índices, medir compatibilidad, bajar la portada)
corre en un hilo (`Task`) y la ventana solo recibe el aviso por `GLib.idle_add`, así que no se congela y se puede cancelar.

«Instalar» no instala por su cuenta: llama a `on_install(entry, card)`, que por defecto abre el diálogo de confirmación (D19,
`install_confirm_window`); sin confirmar no se escribe nada. Se abre desde la pestaña «Catálogo» de la ventana principal.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from ..i18n import tr  # noqa: E402
from .. import remoto as R  # noqa: E402
from . import catalog_online as CO  # noqa: E402

STATE_CSS = {CO.BLOQUEADO: "error", CO.INACCESIBLE: "error", CO.SIN_VERSION: "dim-label", CO.RETIRADA: "warning", CO.ACTUALIZACION: "accent",
             CO.INSTALADO: "success", CO.NO_INSTALADO: "dim-label"}
FILTERS = (("tipo", "f_tipo"), ("categoria", "f_categoria"), ("linea", "f_linea"), ("licencia", "f_licencia"))
COVER_HEIGHT = 200


def _label(text: str = "", *, xalign: float = 0.0, wrap: bool = True, css: tuple = (), selectable: bool = False) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=xalign, wrap=wrap, selectable=selectable)
    lb.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for c in css:
        lb.add_css_class(c)
    return lb


class OnlineCatalogWindow(Gtk.Window):
    """`state` es un `OnlineState` (si no se pasa, se arma con el catálogo abierto en `ctrl`); `on_install(entry, card)` es el gancho de D19."""

    def __init__(self, parent: Gtk.Window | None = None, ctrl=None, *, state: CO.OnlineState | None = None, on_install=None, refresh_on_open: bool = True):
        super().__init__(title=tr("catalog_online_window.titulo"), transient_for=parent, default_width=1100, default_height=720)
        root = ""
        if ctrl is not None and getattr(ctrl, "session", None) is not None:
            root = ctrl.session.catalog_root or ""
        self.st = state or CO.OnlineState(root=root)
        self.on_install = on_install or self._open_install
        self._cards: list[CO.Card] = []
        self._rows: list[Gtk.ListBoxRow] = []
        self._task: CO.Task | None = None
        self._on_done = None
        self._tick = 0
        self._updating = False
        self._detail_gen = 0                       # cada ficha que se dibuja sube el contador: lo que llega tarde de una anterior se descarta
        self._dropdowns: dict[str, Gtk.DropDown] = {}
        self._dd_values: dict[str, list[str]] = {}
        self._build()
        self.connect("close-request", self._on_close)
        if refresh_on_open:
            self.refresh()
        else:
            self._rebuild_filters()
            self._populate()

    # ------------------------------------------------------------------------------------------------------------------ armado
    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("start", "end", "top", "bottom"):
            getattr(outer, f"set_margin_{side}")(8)
        self.set_child(outer)

        bar = Gtk.Box(spacing=6)
        self.entry = Gtk.SearchEntry(placeholder_text=tr("catalog_online_window.buscar"), hexpand=True)
        self.entry.connect("search-changed", self._on_filter_changed)
        bar.append(self.entry)
        self.chk_compat = Gtk.CheckButton(label=tr("catalog_online_window.solo_compatibles"))
        self.chk_compat.connect("toggled", self._on_filter_changed)
        bar.append(self.chk_compat)
        self.chk_hidden = Gtk.CheckButton(label=tr("catalog_online_window.mostrar_ocultos"), tooltip_text=tr("catalog_online_window.mostrar_ocultos_tip"))
        self.chk_hidden.connect("toggled", self._on_filter_changed)
        bar.append(self.chk_hidden)
        self.chk_offline = Gtk.CheckButton(label=tr("catalog_online_window.sin_red"), active=self.st.offline)
        self.chk_offline.connect("toggled", self._on_offline_toggled)
        bar.append(self.chk_offline)
        self.btn_refresh = Gtk.Button(label=tr("catalog_online_window.actualizar"))
        self.btn_refresh.connect("clicked", lambda *_: self.refresh())
        bar.append(self.btn_refresh)
        self.btn_add_index = Gtk.Button(label=tr("catalog_online_window.agregar_indice"))
        self.btn_add_index.connect("clicked", self._on_add_index)
        bar.append(self.btn_add_index)
        outer.append(bar)

        frow = Gtk.Box(spacing=6)
        for key, label_key in FILTERS:
            frow.append(_label(tr(f"catalog_online_window.{label_key}"), wrap=False))
            dd = Gtk.DropDown(model=Gtk.StringList.new([CO.ALL]))
            dd.connect("notify::selected", self._on_filter_changed)
            self._dropdowns[key] = dd
            self._dd_values[key] = [""]
            frow.append(dd)
        frow.append(_label(tr("catalog_online_window.f_orden"), wrap=False))
        self.dd_sort = Gtk.DropDown(model=Gtk.StringList.new([str(CO.SORT_LABELS[k]) for k in R.SORTS]))
        self.dd_sort.connect("notify::selected", self._on_filter_changed)
        frow.append(self.dd_sort)
        self.lbl_count = _label("", css=("dim-label",), wrap=False)
        self.lbl_count.set_hexpand(True)
        self.lbl_count.set_halign(Gtk.Align.END)
        frow.append(self.lbl_count)
        outer.append(frow)

        self.lbl_notices = _label("", css=("dim-label",), selectable=True)
        outer.append(self.lbl_notices)

        prow = Gtk.Box(spacing=6, visible=False)
        self.progress_row = prow
        self.bar = Gtk.ProgressBar(hexpand=True, show_text=True)
        self.btn_cancel = Gtk.Button(label=tr("catalog_online_window.cancelar"))
        self.btn_cancel.connect("clicked", lambda *_: self._task and self._task.cancel())
        prow.append(self.bar)
        prow.append(self.btn_cancel)
        outer.append(prow)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, position=430, shrink_start_child=False)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", self._on_row_selected)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.listbox)
        paned.set_start_child(sw)
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("start", "end", "top", "bottom"):
            getattr(self.detail_box, f"set_margin_{side}")(8)
        paned.set_end_child(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.detail_box))
        outer.append(paned)
        self._detail_placeholder()

    # ------------------------------------------------------------------------------------------------------------------ tareas
    def _start(self, task: CO.Task, on_done) -> None:
        """Una tarea a la vez; la barra late (o avanza si se sabe el total) mientras corre."""
        if self._task is not None and not self._task.finished:
            self._task.cancel()
        self._task = task
        self._on_done = on_done
        self.progress_row.set_visible(True)
        self.bar.set_text(task.text)
        task.start()
        if not self._tick:
            self._tick = GLib.timeout_add(120, self._on_tick)

    def _notify(self) -> None:
        GLib.idle_add(self._poll)

    def _poll(self) -> bool:
        t = self._task
        if t is None:
            return False
        self.bar.set_text(t.text)
        self.btn_cancel.set_sensitive(t.cancelable)
        if t.finished:
            self.progress_row.set_visible(False)
            self._task = None
            done, self._on_done = self._on_done, None
            if done:
                done(t)
        return False

    def _on_tick(self) -> bool:
        t = self._task
        if t is None or t.finished:
            self._tick = 0
            return False
        frac = t.fraction
        self.bar.set_fraction(frac) if frac is not None else self.bar.pulse()
        return True

    def refresh(self) -> None:
        self._set_busy(True)
        self._start(self.st.task_refresh(offline=self.chk_offline.get_active(), notify=self._notify), self._after_refresh)

    def _after_refresh(self, task: CO.Task) -> None:
        self._set_busy(False)
        if task.status == CO.FAILED:
            self._error(task.error_text)
        self._rebuild_filters()
        self._populate()

    def _set_busy(self, busy: bool) -> None:
        for w in (self.btn_refresh, self.btn_add_index, self.chk_offline):
            w.set_sensitive(not busy)

    # ------------------------------------------------------------------------------------------------------------------ lista
    def _rebuild_filters(self) -> None:
        """Los desplegables ofrecen lo que hay en la lista visible; se conserva la elección si todavía existe."""
        opts = self.st.options()
        self._updating = True
        for key, _ in FILTERS:
            keep = self._current(key)
            values = [""] + list(opts[key])
            self._dd_values[key] = values
            dd = self._dropdowns[key]
            dd.set_model(Gtk.StringList.new([CO.ALL] + list(opts[key])))
            dd.set_selected(values.index(keep) if keep in values else 0)
        self._updating = False

    def _current(self, key: str) -> str:
        dd, vals = self._dropdowns[key], self._dd_values[key]
        i = dd.get_selected()
        return vals[i] if 0 <= i < len(vals) else ""

    def _on_filter_changed(self, *_) -> None:
        if self._updating:
            return
        f = self.st.filters
        f.text = self.entry.get_text().strip()
        for key, _ in FILTERS:
            setattr(f, key, self._current(key))
        f.solo_compatibles = self.chk_compat.get_active()
        f.incluir_ocultas = self.chk_hidden.get_active()
        i = self.dd_sort.get_selected()
        f.orden = R.SORTS[i] if 0 <= i < len(R.SORTS) else "descargas"
        self._populate()

    def _on_offline_toggled(self, *_) -> None:
        if not self._updating:
            self.refresh()

    def _populate(self) -> None:
        """Vuelve a dibujar la lista con los filtros de `self.st.filters` y mantiene la selección si el paquete sigue ahí."""
        keep = self.st.selected
        self._cards = self.st.cards()
        self._updating = True
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)
        self._rows = []
        for card in self._cards:
            row = Gtk.ListBoxRow(child=self._card_widget(card))
            row.card = card
            self.listbox.append(row)
            self._rows.append(row)
        self._updating = False
        notices = self.st.notices()
        self.lbl_notices.set_text("\n".join(notices))
        self.lbl_count.set_text(tr("catalog_online_window.n_paquetes", n=len(self._cards)))
        if not self._cards:
            self._detail_placeholder(tr("catalog_online_window.sin_resultados") if self.st.entries() else "")
            return
        target = next((r for r in self._rows if keep is not None and (r.card.entry.indice, r.card.entry.folder) == (keep.indice, keep.folder)), None)
        if target is not None:
            self.listbox.select_row(target)
        else:
            self.st.select(None)
            self._detail_placeholder()

    def _card_widget(self, card: CO.Card) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(6)
        head = Gtk.Box(spacing=6)
        title = _label(card.titulo, wrap=False, css=("heading",))
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)
        head.append(title)
        head.append(_label(card.texto_estado, wrap=False, css=(STATE_CSS[card.estado],)))
        box.append(head)
        box.append(_label(card.subtitulo, css=("dim-label",)))
        extra = [CO.downloads_text(card.entry)]
        if card.entry.categorias:
            extra.append(", ".join(card.entry.categorias))
        if card.experimental:
            extra.append(tr("catalog_online.aviso_experimental"))
        box.append(_label(" · ".join(extra), css=("dim-label",)))
        if card.incompatible:
            box.append(_label(f"{tr('catalog_online_window.no_encaja')}: {card.incompatible}", css=("warning",)))
        return box

    def _on_row_selected(self, _lb, row) -> None:
        if self._updating:
            return
        if row is None:
            self.st.select(None)
            self._detail_placeholder()
            return
        self.st.select(row.card.entry)
        self._show_detail(row.card)

    # ------------------------------------------------------------------------------------------------------------------ ficha
    def _clear_detail(self) -> None:
        while (c := self.detail_box.get_first_child()) is not None:
            self.detail_box.remove(c)

    def _detail_placeholder(self, text: str | None = None) -> None:
        self._detail_gen += 1
        self._clear_detail()
        self.detail_box.append(_label(tr("catalog_online_window.elegi") if text is None else text, css=("dim-label",)))

    def _show_detail(self, card: CO.Card) -> None:
        d = self.st.detail(card.entry)
        if d is None:
            self._detail_placeholder()
            return
        self._detail_gen += 1
        gen = self._detail_gen
        self._clear_detail()
        box = self.detail_box
        self.cover = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, height_request=COVER_HEIGHT, visible=False)
        box.append(self.cover)
        box.append(_label(d.titulo, css=("title-2",), selectable=True))
        box.append(_label(tr("catalog_online_window.version_estado", version=d.version or "—", estado=d.estado), css=(STATE_CSS[card.estado],)))
        for aviso in d.avisos:
            box.append(_label(aviso, css=("warning",)))
        if d.descripcion:
            box.append(_label(d.descripcion, selectable=True))
        grid = Gtk.Grid(column_spacing=12, row_spacing=2)
        for i, (k, v) in enumerate(d.rows):
            grid.attach(_label(k, css=("dim-label",), wrap=False), 0, i, 1, 1)
            val = _label(v, selectable=True)
            val.set_hexpand(True)
            grid.attach(val, 1, i, 1, 1)
        box.append(grid)

        self.btn_compat = Gtk.Button(label=tr("catalog_online_window.medir_compat"), halign=Gtk.Align.START)
        self.btn_compat.set_sensitive(d.puede_instalar)
        self.btn_compat.connect("clicked", self._on_compat, card, gen)
        box.append(self.btn_compat)
        self.lbl_compat = _label("", selectable=True)
        self.lbl_compat.add_css_class("monospace")
        box.append(self.lbl_compat)

        label = (tr("catalog_online_window.actualizar_a", version=d.version) if card.estado == CO.ACTUALIZACION else
                 tr("catalog_online_window.instalado") if card.estado == CO.INSTALADO else tr("catalog_online_window.instalar"))
        self.btn_install = Gtk.Button(label=label, halign=Gtk.Align.START)
        self.btn_install.add_css_class("suggested-action")
        self.btn_install.set_sensitive(d.puede_instalar and card.estado != CO.INSTALADO)
        self.btn_install.connect("clicked", lambda *_: self.on_install(card.entry, card))
        box.append(self.btn_install)
        if d.razon:
            box.append(_label(d.razon, css=("error",)))

        if d.portada:
            self._start(self.st.task_cover(card.entry, notify=self._notify), lambda t, g=gen: self._after_cover(t, g))

    def _after_cover(self, task: CO.Task, gen: int) -> None:
        if gen != self._detail_gen or not task.result:
            return
        try:
            tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(task.result))
        except GLib.Error:
            return                                  # no es una imagen que GTK entienda: la ficha queda sin portada
        self.cover.set_paintable(tex)
        self.cover.set_visible(True)

    def _on_compat(self, btn, card: CO.Card, gen: int) -> None:
        btn.set_sensitive(False)
        self.lbl_compat.set_text(tr("catalog_online.midiendo"))
        self._start(self.st.task_compat(card.entry, notify=self._notify), lambda t, g=gen: self._after_compat(t, g, btn))

    def _after_compat(self, task: CO.Task, gen: int, btn: Gtk.Button) -> None:
        if gen != self._detail_gen:
            return
        btn.set_sensitive(True)
        self.lbl_compat.set_text("\n".join(task.result) if task.status == CO.DONE and task.result else (task.error_text or ""))

    # ------------------------------------------------------------------------------------------------------------------ diálogos
    def _message(self, title: str, text: str) -> None:
        dlg = Gtk.AlertDialog(message=title, detail=text, buttons=[tr("catalog_online_window.cerrar")])
        dlg.choose(self, None, lambda *_: None)

    def _error(self, text: str) -> None:
        self._message(tr("catalog_online_window.error_titulo"), text)

    def _open_install(self, entry: R.Entry, card: CO.Card) -> None:
        """«Instalar» / «Actualizar»: el diálogo de confirmación (D19). Al terminar bien se vuelven a dibujar los estados de la lista."""
        from .install_confirm_window import InstallConfirmDialog

        self.install_dialog = InstallConfirmDialog(self, self.st, entry, on_installed=lambda results: self._populate())
        self.install_dialog.present()

    def _on_add_index(self, *_) -> None:
        win = Gtk.Window(title=tr("catalog_online_window.indice_titulo"), transient_for=self, modal=True, default_width=520)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(12)
        box.append(_label(tr("catalog_online_window.indice_aviso"), css=("warning",)))
        box.append(_label(tr("catalog_online_window.indice_url")))
        entry = Gtk.Entry(hexpand=True, activates_default=True)
        box.append(entry)
        err = _label("", css=("error",))
        box.append(err)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=tr("catalog_online_window.cancelar"))
        ok = Gtk.Button(label=tr("catalog_online_window.agregar"))
        ok.add_css_class("suggested-action")
        row.append(cancel)
        row.append(ok)
        box.append(row)
        win.set_child(box)
        win.set_default_widget(ok)
        cancel.connect("clicked", lambda *_: win.close())

        def accept(*_):
            try:
                url = self.st.add_index(entry.get_text())
            except R.RemoteError as exc:
                err.set_text(str(exc))
                return
            win.close()
            self.lbl_notices.set_text(tr("catalog_online_window.indice_agregado", url=url))
            self.refresh()

        ok.connect("clicked", accept)
        win.present()

    def _on_close(self, *_) -> bool:
        if self._task is not None:
            self._task.cancel()
        return False


__all__ = ["OnlineCatalogWindow"]
