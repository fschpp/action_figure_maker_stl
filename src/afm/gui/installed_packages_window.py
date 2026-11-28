"""Ventana «Paquetes instalados» (D20): lista con versión y origen, Actualizar, Versiones, Quitar y los problemas de cada paquete.

Solo dibuja: todo lo que decide (qué se lista, qué estado tiene cada paquete, qué se quitaría, qué se conserva, a quién se le atribuye cada error)
sale de `installed_packages.InstalledState`, que se prueba sin GTK. Lo que tarda (leer índices, revisar el catálogo, quitar) corre en un hilo.

«Actualizar» e «Instalar esta versión» abren el diálogo de confirmación de D19; «Quitar» pide confirmación mostrando lo que se va, lo que se conserva y
quién requiere el paquete. Nada se escribe sin esas confirmaciones. Se abre desde «Explorar catálogos en línea…».
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..i18n import tr  # noqa: E402
from . import catalog_online as CO  # noqa: E402
from . import installed_packages as IP  # noqa: E402

STATE_CSS = {IP.BLOQUEADO: "error", IP.RETIRADA: "warning", IP.ACTUALIZACION: "accent", IP.AL_DIA: "success", IP.SIN_INDICE: "dim-label"}


def _label(text: str = "", *, xalign: float = 0.0, wrap: bool = True, css: tuple = (), selectable: bool = False) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=xalign, wrap=wrap, selectable=selectable)
    lb.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for c in css:
        lb.add_css_class(c)
    return lb


class InstalledPackagesWindow(Gtk.Window):
    """`online` es el `OnlineState` de «Explorar» (mismos índices y catálogos). `on_changed()` se llama cuando se instaló o quitó algo."""

    def __init__(self, parent: Gtk.Window | None, online: CO.OnlineState, *, on_changed=None, refresh_on_open: bool = False):
        super().__init__(title=tr("installed_packages_window.titulo"), transient_for=parent, default_width=1000, default_height=660)
        self.online = online
        self.st = IP.InstalledState(online)
        self.on_changed = on_changed
        self._rows: list[IP.Row] = []
        self._task: CO.Task | None = None
        self._on_done = None
        self._tick = 0
        self._updating = False
        self._diagnoses: dict[str, IP.Diagnosis] = {}
        self._selected: tuple[str, str] | None = None          # (catálogo, id) del paquete elegido: sobrevive a redibujar la lista
        self._build()
        self.connect("close-request", self._on_close)
        if refresh_on_open:
            self._refresh_indexes()
        else:
            self._populate()

    # ------------------------------------------------------------------------------------------------------------------ armado
    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("start", "end", "top", "bottom"):
            getattr(outer, f"set_margin_{side}")(8)
        self.set_child(outer)

        bar = Gtk.Box(spacing=6)
        self.lbl_count = _label("", css=("dim-label",), wrap=False)
        self.lbl_count.set_hexpand(True)
        bar.append(self.lbl_count)
        self.btn_indexes = Gtk.Button(label=tr("installed_packages_window.actualizar_indices"))
        self.btn_indexes.connect("clicked", lambda *_: self._refresh_indexes())
        bar.append(self.btn_indexes)
        self.btn_check = Gtk.Button(label=tr("installed_packages_window.revisar"), tooltip_text=tr("installed_packages_window.revisar_tip"))
        self.btn_check.connect("clicked", self._on_check)
        bar.append(self.btn_check)
        outer.append(bar)

        self.lbl_notices = _label("", css=("dim-label",), selectable=True)
        outer.append(self.lbl_notices)

        prow = Gtk.Box(spacing=6, visible=False)
        self.progress_row = prow
        self.bar = Gtk.ProgressBar(hexpand=True, show_text=True)
        self.btn_cancel = Gtk.Button(label=tr("installed_packages_window.cancelar"))
        self.btn_cancel.connect("clicked", lambda *_: self._task and self._task.cancel())
        prow.append(self.bar)
        prow.append(self.btn_cancel)
        outer.append(prow)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, position=380, shrink_start_child=False)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", self._on_row_selected)
        paned.set_start_child(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.listbox))
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("start", "end", "top", "bottom"):
            getattr(self.detail_box, f"set_margin_{side}")(8)
        paned.set_end_child(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.detail_box))
        outer.append(paned)
        self._placeholder()

    # ------------------------------------------------------------------------------------------------------------------ tareas
    def _start(self, task: CO.Task, on_done) -> None:
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

    def _set_busy(self, busy: bool) -> None:
        for w in (self.btn_indexes, self.btn_check):
            w.set_sensitive(not busy)

    def _refresh_indexes(self) -> None:
        self._set_busy(True)
        self._start(self.online.task_refresh(notify=self._notify), self._after_indexes)

    def _after_indexes(self, task: CO.Task) -> None:
        self._set_busy(False)
        if task.status == CO.FAILED:
            self._error(task.error_text)
        self._populate()

    # ------------------------------------------------------------------------------------------------------------------ lista
    def _populate(self) -> None:
        self._updating = True
        while (r := self.listbox.get_first_child()) is not None:
            self.listbox.remove(r)
        self._rows = self.st.rows()
        notices = list(self.st.errors)
        for r in self._rows:
            row = Gtk.ListBoxRow()
            row.item = r
            row.set_child(self._row_widget(r))
            self.listbox.append(row)
        self.lbl_count.set_text(tr("installed_packages_window.cuenta", n=len(self._rows)))
        self.lbl_notices.set_text("\n".join(notices))
        self._updating = False
        keep = next((i for i, r in enumerate(self._rows) if (r.root, r.id) == self._selected), None)
        if keep is not None:
            self.listbox.select_row(self.listbox.get_row_at_index(keep))
        else:
            self._selected = None
            self._placeholder(tr("installed_packages_window.sin_paquetes") if not self._rows else None)

    def _row_widget(self, r: IP.Row) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(6)
        head = Gtk.Box(spacing=6)
        title = _label(r.titulo, wrap=False, css=("heading",))
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)
        head.append(title)
        head.append(_label(r.estado_texto, wrap=False, css=(STATE_CSS[r.estado],)))
        box.append(head)
        box.append(_label(r.subtitulo, css=("dim-label",)))
        return box

    def _on_row_selected(self, _lb, row) -> None:
        if self._updating:
            return
        if row is None:
            self._selected = None
            self._placeholder()
            return
        self._selected = (row.item.root, row.item.id)
        self._show_detail(row.item)

    # ------------------------------------------------------------------------------------------------------------------ ficha
    def _clear_detail(self) -> None:
        while (c := self.detail_box.get_first_child()) is not None:
            self.detail_box.remove(c)

    def _placeholder(self, text: str | None = None) -> None:
        self._clear_detail()
        self.detail_box.append(_label(tr("installed_packages_window.elegi") if text is None else text, css=("dim-label",)))

    def _show_detail(self, r: IP.Row) -> None:
        self._clear_detail()
        box = self.detail_box
        box.append(_label(r.titulo, css=("title-2",), selectable=True))
        box.append(_label(r.estado_texto, css=(STATE_CSS[r.estado],)))
        for a in r.alertas:
            box.append(_label(a, css=("warning",), selectable=True))
        grid = Gtk.Grid(column_spacing=12, row_spacing=2)
        for i, (k, v) in enumerate(self.st.detail_rows(r)):
            grid.attach(_label(k, css=("dim-label",), wrap=False), 0, i, 1, 1)
            val = _label(v, selectable=True)
            val.set_hexpand(True)
            grid.attach(val, 1, i, 1, 1)
        box.append(grid)

        diag = self._diagnoses.get(r.root)
        if diag is not None:
            lines = diag.of(r.id)
            box.append(_label(tr("installed_packages_window.problemas_titulo"), css=("heading",)))
            if lines:
                for t in lines:
                    box.append(_label(t, css=("error",), selectable=True))
            else:
                box.append(_label(tr("installed_packages_window.sin_problemas"), css=("success",)))

        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.START)
        upd = Gtk.Button(label=tr("installed_packages_window.actualizar_a", version=r.actualizar_a) if r.puede_actualizar else tr("installed_packages_window.actualizar"))
        upd.add_css_class("suggested-action")
        upd.set_sensitive(r.puede_actualizar)
        upd.connect("clicked", lambda *_: self._open_install(self.st.update_target(r)))
        buttons.append(upd)
        ver = Gtk.Button(label=tr("installed_packages_window.versiones"))
        ver.set_sensitive(r.tiene_versiones)
        ver.connect("clicked", lambda *_: self._show_versions(r))
        buttons.append(ver)
        rem = Gtk.Button(label=tr("installed_packages_window.quitar"))
        rem.add_css_class("destructive-action")
        rem.connect("clicked", lambda *_: self._on_remove(r))
        buttons.append(rem)
        box.append(buttons)
        missing = [q for q in self.st.required_files(r) if not q.importado]
        if missing:                                              # D26: piezas por referencia: el STL lo aporta la persona
            box.append(_label(tr("installed_packages_window.requeridos_titulo"), css=("heading",)))
            box.append(_label(tr("installed_packages_window.requeridos_ayuda"), css=("dim-label",)))
            for q in missing:
                line = Gtk.Box(spacing=6)
                lb = _label(q.texto, wrap=True, css=("warning",))
                lb.set_hexpand(True)
                line.append(lb)
                btn = Gtk.Button(label=tr("installed_packages_window.importar_requerido"))
                btn.connect("clicked", lambda *_, req=q: self._on_import_required(r, req))
                line.append(btn)
                box.append(line)
        if r.entry is None:
            box.append(_label(tr("installed_packages_window.sin_indice"), css=("dim-label",)))
        self.versions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(self.versions_box)

    def _show_versions(self, r: IP.Row) -> None:
        while (c := self.versions_box.get_first_child()) is not None:
            self.versions_box.remove(c)
        versions = self.st.versions(r)
        self.versions_box.append(_label(tr("installed_packages_window.versiones_titulo"), css=("heading",)))
        if not versions:
            self.versions_box.append(_label(tr("installed_packages_window.sin_versiones"), css=("dim-label",)))
        for v in versions:
            line = Gtk.Box(spacing=6)
            lb = _label(v.texto, wrap=False, css=("warning",) if v.retirada else ())
            lb.set_hexpand(True)
            line.append(lb)
            btn = Gtk.Button(label=tr("installed_packages_window.instalar_version"))
            btn.set_sensitive(v.instalable and not v.instalada)
            btn.connect("clicked", lambda *_, ver=v.version: self._open_install(self.st.version_target(r, ver)))
            line.append(btn)
            self.versions_box.append(line)

    # ------------------------------------------------------------------------------------------------------------------ acciones
    def _message(self, title: str, text: str) -> None:
        dlg = Gtk.AlertDialog(message=title, detail=text, buttons=[tr("installed_packages_window.cerrar")])
        dlg.choose(self, None, lambda *_: None)

    def _error(self, text: str) -> None:
        self._message(tr("installed_packages_window.error_titulo"), text)

    def _changed(self) -> None:
        self._diagnoses.clear()
        self._populate()
        if self.on_changed:
            self.on_changed()

    def _open_install(self, target) -> None:
        """«Actualizar» / «Instalar esta versión»: el diálogo de confirmación (D19). Al terminar bien se redibuja la lista."""
        if target is None:
            return
        from .install_confirm_window import InstallConfirmDialog

        entry, version = target
        self.install_dialog = InstallConfirmDialog(self, self.online, entry, version, on_installed=lambda results: self._changed())
        self.install_dialog.present()

    def _on_check(self, *_) -> None:
        roots = self.online.roots()
        if not roots:
            return
        self._set_busy(True)
        pending = list(roots)

        def step(task=None):
            if task is not None:
                if task.status == CO.FAILED:
                    self._error(task.error_text)
                elif task.status == CO.DONE:
                    self._diagnoses[task.result.root] = task.result
            if pending:
                self._start(self.st.task_diagnose(pending.pop(0), notify=self._notify), step)
                return
            self._set_busy(False)
            n = sum(len(v) for d in self._diagnoses.values() for v in d.por_paquete.values()) + sum(len(d.propios) for d in self._diagnoses.values())
            self.lbl_notices.set_text(tr("installed_packages_window.revision_lista", n=n))
            if self._selected:
                row = next((r for r in self._rows if (r.root, r.id) == self._selected), None)
                if row is not None:
                    self._show_detail(row)
        step()

    def _on_import_required(self, r: IP.Row, req: IP.RequiredRow) -> None:
        """«Importar archivo requerido…»: elegir el STL; se verifica por sha256 y recién ahí se guarda (si no es el pedido se dice por qué)."""
        dlg = Gtk.FileDialog(title=tr("installed_packages_window.elegir_archivo", nombre=req.nombre))

        def chosen(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return                                              # cancelado
            path = f.get_path() if f is not None else None
            if not path:
                return
            try:
                lines = self.st.import_required(r, req.id, path)
            except Exception as exc:                                # noqa: BLE001 — el motivo (otro archivo, pesa de más, catálogo ilegible) se dice, no se cierra
                self._error(str(exc))
                return
            self._changed()
            self._message(tr("installed_packages_window.importado_titulo"), "\n".join(lines))

        dlg.open(self, None, chosen)

    def _on_remove(self, r: IP.Row) -> None:
        try:
            plan = self.st.plan_remove(r)
        except Exception as exc:                                   # noqa: BLE001 — el catálogo o el registro no se pudo leer: se dice, no se cierra
            self._error(str(exc))
            return
        title, lines, needs_dep = self.st.remove_confirmation(plan)
        if not plan.ok:
            self._message(title, "\n".join(lines))
            return
        btn = tr("installed_packages_window.quitar_confirmar_dep") if needs_dep else tr("installed_packages_window.quitar_confirmar")
        dlg = Gtk.AlertDialog(message=title, detail="\n".join(lines), buttons=[tr("installed_packages_window.cancelar"), btn], cancel_button=0, default_button=0)

        def answered(d, res):
            try:
                choice = d.choose_finish(res)
            except GLib.Error:
                return                                              # cancelado
            if choice == 1:
                self._set_busy(True)
                self._start(self.st.task_remove(plan, acepto=True, confirmar_dependientes=needs_dep, notify=self._notify), self._after_remove)

        dlg.choose(self, None, answered)

    def _after_remove(self, task: CO.Task) -> None:
        self._set_busy(False)
        if task.status == CO.FAILED:
            self._error(task.error_text)
            return
        self._selected = None
        self._changed()
        if task.status == CO.DONE:
            self._message(tr("installed_packages_window.quitado_titulo"), "\n".join(self.st.result_lines(task.result)))

    def _on_close(self, *_) -> bool:
        if self._task is not None:
            self._task.cancel()
        return False


__all__ = ["InstalledPackagesWindow"]
