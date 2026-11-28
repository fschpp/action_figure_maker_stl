"""Asistente «Publicar…» (D21): de piezas o recetas del catálogo a un paquete para compartir, sin terminal.

Solo dibuja: todo lo que decide sale de `publish.PublishFlow`, que se prueba sin GTK. Cuatro pasos: **contenido** (qué piezas y recetas) → **datos**
(nombre, descripción, autores, repo) → **licencia y origen** (cada opción se explica en español; la app no las verifica) → **revisar** (se arma el
paquete en una carpeta temporal y se valida con las reglas estrictas; los errores salen legibles, con qué corregir). Recién con la validación sin
errores se habilitan las salidas: guardar el `.zip`, preparar la carpeta del repo de GitHub y preparar el alta en el índice. **Nada se escribe en el
catálogo**; lo lento corre en un hilo (`Task`) y la ventana solo recibe el aviso por `GLib.idle_add`. Se abre desde «Compartir ▾ → Publicar…» de
«Administrar catálogo».
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from ..i18n import tr  # noqa: E402
from . import catalog_online as CO  # noqa: E402
from . import publish as PB  # noqa: E402
from .. import paquete as Q  # noqa: E402

PAGES = ("contenido", "datos", "licencia", "revisar")


def _label(text: str = "", *, css: tuple = (), selectable: bool = True, wrap: bool = True) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=0.0, wrap=wrap, selectable=selectable)
    lb.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for c in css:
        lb.add_css_class(c)
    return lb


def _text(view: Gtk.TextView) -> str:
    buf = view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def _scrolled(child: Gtk.Widget, height: int = 120) -> Gtk.ScrolledWindow:
    return Gtk.ScrolledWindow(child=child, min_content_height=height, hscrollbar_policy=Gtk.PolicyType.NEVER, has_frame=True)


class PublishWindow(Gtk.Window):
    """`flow` es un `PublishFlow` (si no se pasa, se arma con `root`, la carpeta del catálogo abierto)."""

    def __init__(self, parent: Gtk.Window | None, root: str | None = None, *, flow: PB.PublishFlow | None = None):
        super().__init__(title=tr("publish_window.titulo"), transient_for=parent, modal=False, default_width=820, default_height=720)
        self.flow = flow or PB.PublishFlow(root or "")
        self._task: CO.Task | None = None
        self._on_done = None
        self._tick = 0
        self._closed = False
        self._page = 0
        self._piece_checks: list = []
        self._recipe_checks: list = []
        self._build()
        self.connect("close-request", self._on_close)
        self._show_page(0)

    # ------------------------------------------------------------------------------------------------------------------ armado
    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("start", "end", "top", "bottom"):
            getattr(outer, f"set_margin_{side}")(12)
        self.set_child(outer)
        self.lbl_step = _label("", css=("title-2",), selectable=False)
        outer.append(self.lbl_step)
        self.stack = Gtk.Stack(vexpand=True, hhomogeneous=True)
        for name, build in zip(PAGES, (self._page_content, self._page_data, self._page_license, self._page_review)):
            self.stack.add_named(Gtk.ScrolledWindow(child=build(), hscrollbar_policy=Gtk.PolicyType.NEVER), name)
        outer.append(self.stack)

        self.progress_row = Gtk.Box(spacing=6, visible=False)
        self.bar = Gtk.ProgressBar(hexpand=True, show_text=True)
        self.btn_cancel_task = Gtk.Button(label=tr("publish_window.cancelar_tarea"))
        self.btn_cancel_task.connect("clicked", lambda *_: self._task and self._task.cancel())
        self.progress_row.append(self.bar)
        self.progress_row.append(self.btn_cancel_task)
        outer.append(self.progress_row)

        self.lbl_msg = _label("", css=("dim-label",))
        outer.append(self.lbl_msg)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.btn_close = Gtk.Button(label=tr("publish_window.cerrar"))
        self.btn_close.connect("clicked", lambda *_: self.close())
        self.btn_back = Gtk.Button(label=tr("publish_window.atras"))
        self.btn_back.connect("clicked", lambda *_: self._show_page(self._page - 1))
        self.btn_next = Gtk.Button(label=tr("publish_window.siguiente"))
        self.btn_next.add_css_class("suggested-action")
        self.btn_next.connect("clicked", self._on_next)
        for b in (self.btn_close, self.btn_back, self.btn_next):
            row.append(b)
        outer.append(row)

    def _box(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(4)
        return box

    def _field(self, box: Gtk.Box, label: str, tip: str = "") -> Gtk.Entry:
        box.append(_label(label, css=("heading",), selectable=False))
        e = Gtk.Entry(hexpand=True, tooltip_text=tip or None)
        box.append(e)
        return e

    # ---- 1 · contenido
    def _page_content(self) -> Gtk.Widget:
        box = self._box()
        line, height = self.flow.line()
        box.append(_label(tr("publish_window.contenido_ayuda", linea=line or "—", altura=f"{height:g}" if isinstance(height, (int, float)) else "—"), css=("dim-label",)))
        box.append(_label(tr("publish_window.tipo"), css=("heading",), selectable=False))
        self.dd_tipo = Gtk.DropDown.new_from_strings([tr("publish_window.tipo_piezas"), tr("publish_window.tipo_heroe")])
        self.dd_tipo.set_tooltip_text(tr("publish_window.tipo_tip"))
        box.append(self.dd_tipo)
        self.chk_line = Gtk.CheckButton(label=tr("publish_window.linea_completa"))
        self.chk_line.set_tooltip_text(tr("publish_window.linea_completa_tip"))
        self.chk_line.connect("toggled", self._on_line_toggled)
        box.append(self.chk_line)
        self.box_pieces = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for pid, versions, tipo in self.flow.available_pieces():
            chk = Gtk.CheckButton(label=tr("publish_window.pieza_fila", id=pid, tipo=tipo, versiones=", ".join(f"@{v}" for v in versions)))
            chk._ref = pid
            self._piece_checks.append(chk)
            self.box_pieces.append(chk)
        box.append(_label(tr("publish_window.piezas"), css=("heading",), selectable=False))
        box.append(_scrolled(self.box_pieces, 200) if self._piece_checks else _label(tr("publish_window.sin_piezas"), css=("dim-label",)))
        self.box_recipes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for name in self.flow.available_recipes():
            chk = Gtk.CheckButton(label=name)
            chk._ref = name
            self._recipe_checks.append(chk)
            self.box_recipes.append(chk)
        box.append(_label(tr("publish_window.recetas"), css=("heading",), selectable=False))
        box.append(_scrolled(self.box_recipes, 120) if self._recipe_checks else _label(tr("publish_window.sin_recetas"), css=("dim-label",)))
        self.chk_color = Gtk.CheckButton(label=tr("publish_window.incluir_color"))
        self.chk_color.set_tooltip_text(tr("publish_window.incluir_color_tip"))
        self.chk_color.set_active(self.flow.form.incluir_color)
        box.append(self.chk_color)
        return box

    def _on_line_toggled(self, btn: Gtk.CheckButton) -> None:
        for chk in self._piece_checks + self._recipe_checks:
            chk.set_sensitive(not btn.get_active())

    # ---- 2 · datos
    def _page_data(self) -> Gtk.Widget:
        box = self._box()
        self.e_nombre = self._field(box, tr("publish_window.nombre"))
        self.e_id = self._field(box, tr("publish_window.id"), tr("publish_window.id_tip"))
        self.e_nombre.connect("changed", self._on_name_changed)
        self.e_version = self._field(box, tr("publish_window.version"), tr("publish_window.version_tip"))
        self.e_version.set_text("1.0.0")
        self.e_desc = self._field(box, tr("publish_window.descripcion", max=Q.DESCRIPTION_MAX))
        box.append(_label(tr("publish_window.autores"), css=("heading",), selectable=False))
        box.append(_label(tr("publish_window.autores_ayuda"), css=("dim-label",)))
        self.tv_autores = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        box.append(_scrolled(self.tv_autores, 70))
        self.e_repo = self._field(box, tr("publish_window.repo"), tr("publish_window.repo_tip"))
        self.e_cats = self._field(box, tr("publish_window.categorias"))
        self.e_tags = self._field(box, tr("publish_window.etiquetas"))
        box.append(_label(tr("publish_window.portada"), css=("heading",), selectable=False))
        box.append(_label(tr("publish_window.portada_ayuda"), css=("dim-label",)))
        row = Gtk.Box(spacing=6)
        self.e_portada = Gtk.Entry(hexpand=True, placeholder_text=tr("publish_window.portada_sin"))
        pick = Gtk.Button(label=tr("publish_window.portada_elegir"))
        pick.connect("clicked", self._on_pick_cover)
        row.append(self.e_portada)
        row.append(pick)
        box.append(row)
        return box

    def _on_pick_cover(self, *_) -> None:
        """Elige la imagen de portada (PNG o JPG); queda en el campo y se copia a `previews/portada.png` al armar el paquete."""
        dlg = Gtk.FileDialog(title=tr("publish_window.portada_elegir_titulo"))
        flt = Gtk.FileFilter()
        flt.set_name(tr("publish_window.portada_filtro"))
        for pat in ("*.png", "*.jpg", "*.jpeg"):
            flt.add_pattern(pat)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)
        dlg.set_default_filter(flt)

        def finished(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return                                            # cancelado
            path = f.get_path() if isinstance(f, Gio.File) else None
            if path:
                self.e_portada.set_text(path)

        dlg.open(self, None, finished)

    def _on_name_changed(self, *_) -> None:
        self.e_id.set_placeholder_text(PB.suggest_id(self.e_nombre.get_text()))

    # ---- 3 · licencia y origen
    def _page_license(self) -> Gtk.Widget:
        box = self._box()
        box.append(_label(tr("publish_window.licencia"), css=("heading",), selectable=False))
        self.dd_lic = Gtk.DropDown.new_from_strings([tr("publish_window.elegir"), *Q.LICENSES])
        self.dd_lic.connect("notify::selected", self._on_license_changed)
        box.append(self.dd_lic)
        self.lbl_lic = _label(PB.license_help(""), css=("dim-label",))
        box.append(self.lbl_lic)
        box.append(_label(tr("publish_window.origen"), css=("heading",), selectable=False))
        self.dd_org = Gtk.DropDown.new_from_strings([tr("publish_window.elegir"), *Q.ORIGINS])
        self.dd_org.connect("notify::selected", self._on_license_changed)
        box.append(self.dd_org)
        self.lbl_org = _label("", css=("dim-label",))
        box.append(self.lbl_org)
        self.lbl_warn = _label("", css=("warning",))
        box.append(self.lbl_warn)
        box.append(_label(tr("publish_window.creditos"), css=("heading",), selectable=False))
        box.append(_label(tr("publish_window.creditos_ayuda"), css=("dim-label",)))
        self.tv_creditos = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        box.append(_scrolled(self.tv_creditos, 90))
        box.append(_label(tr("publish_window.responsabilidad"), css=("dim-label",)))
        return box

    def _selected(self, dd: Gtk.DropDown, values: tuple) -> str:
        n = dd.get_selected()
        return values[n - 1] if 0 < n <= len(values) else ""

    def _on_license_changed(self, *_) -> None:
        self.flow.form.licencia = self._selected(self.dd_lic, Q.LICENSES)
        self.flow.form.origen = self._selected(self.dd_org, Q.ORIGINS)
        self.lbl_lic.set_text(PB.license_help(self.flow.form.licencia))
        self.lbl_org.set_text(PB.origin_help(self.flow.form.origen) if self.flow.form.origen else "")
        self.lbl_warn.set_text("\n".join(self.flow.warnings_before()))

    # ---- 4 · revisar
    def _page_review(self) -> Gtk.Widget:
        box = self._box()
        self.review_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(self.review_box)
        self.out_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        self.btn_zip = Gtk.Button(label=tr("publish_window.guardar_zip"), tooltip_text=tr("publish_window.guardar_zip_tip"), halign=Gtk.Align.START)
        self.btn_zip.connect("clicked", self._on_save_zip)
        self.btn_repo = Gtk.Button(label=tr("publish_window.preparar_repo"), tooltip_text=tr("publish_window.preparar_repo_tip"), halign=Gtk.Align.START)
        self.btn_repo.connect("clicked", self._on_prepare_repo)
        self.btn_alta = Gtk.Button(label=tr("publish_window.preparar_alta"), tooltip_text=tr("publish_window.preparar_alta_tip"), halign=Gtk.Align.START)
        self.btn_alta.connect("clicked", self._on_prepare_alta)
        for b in (self.btn_zip, self.btn_repo, self.btn_alta):
            self.out_row.append(b)
        box.append(self.out_row)
        self.out_msg = _label("", css=("success",))
        box.append(self.out_msg)
        return box

    # ------------------------------------------------------------------------------------------------------------------ pasos
    def _collect(self) -> None:
        """Pasa lo que hay en los controles al formulario del flujo."""
        f = self.flow.form
        f.tipo = "heroe" if self.dd_tipo.get_selected() == 1 else "piezas"
        f.linea_completa = self.chk_line.get_active()
        f.incluir_color = self.chk_color.get_active()
        f.piezas = [] if f.linea_completa else [c._ref for c in self._piece_checks if c.get_active()]
        f.recetas = [] if f.linea_completa else [c._ref for c in self._recipe_checks if c.get_active()]
        f.nombre, f.id = self.e_nombre.get_text().strip(), self.e_id.get_text().strip()
        f.version, f.descripcion = self.e_version.get_text().strip(), self.e_desc.get_text().strip()
        f.autores = PB.parse_authors(_text(self.tv_autores))
        f.github = self.e_repo.get_text().strip()
        f.categorias, f.etiquetas = PB._csv(self.e_cats.get_text()), PB._csv(self.e_tags.get_text())
        f.portada = self.e_portada.get_text().strip()
        f.licencia = self._selected(self.dd_lic, Q.LICENSES)
        f.origen = self._selected(self.dd_org, Q.ORIGINS)
        f.creditos = _text(self.tv_creditos).strip()

    def _show_page(self, n: int) -> None:
        self._page = max(0, min(n, len(PAGES) - 1))
        self.stack.set_visible_child_name(PAGES[self._page])
        self.lbl_step.set_text(tr("publish_window.paso", n=self._page + 1, total=len(PAGES), titulo=tr(f"publish_window.paso_{PAGES[self._page]}")))
        self.btn_back.set_sensitive(self._page > 0 and self._task is None)
        last = self._page == len(PAGES) - 1
        self.btn_next.set_visible(not last)
        self.lbl_msg.set_text("")
        if last:
            self._review()

    def _on_next(self, *_) -> None:
        self._collect()
        wanted = {"contenido": ("contenido",), "datos": ("nombre", "id", "version", "descripcion", "autores", "github"),
                  "licencia": ("licencia", "origen")}[PAGES[self._page]]
        probs = [p for p in self.flow.problems() if p.campo in wanted]
        if probs:
            self.lbl_msg.set_text("\n".join(p.texto for p in probs))
            return
        self._show_page(self._page + 1)

    # ------------------------------------------------------------------------------------------------------------------ tareas
    def _start(self, task: CO.Task, on_done) -> None:
        self._task, self._on_done = task, on_done
        self.progress_row.set_visible(True)
        self.btn_back.set_sensitive(False)
        self.bar.set_text(task.text)
        task.start()
        if not self._tick:
            self._tick = GLib.timeout_add(120, self._on_tick)

    def _notify(self) -> None:
        GLib.idle_add(self._poll)

    def _poll(self) -> bool:
        t = self._task
        if t is None or self._closed:
            return False
        self.bar.set_text(t.text)
        if t.finished:
            self.progress_row.set_visible(False)
            self._task = None
            done, self._on_done = self._on_done, None
            self.btn_back.set_sensitive(self._page > 0)
            if done:
                done(t)
        return False

    def _on_tick(self) -> bool:
        t = self._task
        if t is None or t.finished or self._closed:
            self._tick = 0
            return False
        frac = t.fraction
        self.bar.set_fraction(frac) if frac is not None else self.bar.pulse()
        return True

    # ------------------------------------------------------------------------------------------------------------------ revisar
    def _clear(self, box: Gtk.Box) -> None:
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _review(self) -> None:
        self._clear(self.review_box)
        self.out_row.set_visible(False)
        self.out_msg.set_text("")
        self.review_box.append(_label(tr("publish_window.validando"), css=("dim-label",)))
        self._start(self.flow.task_validate(notify=self._notify), self._after_review)

    def _after_review(self, t: CO.Task) -> None:
        self._clear(self.review_box)
        if t.status == CO.CANCELLED:
            self._show_page(self._page - 1)
            return
        rep = self.flow.report
        if rep is None:                                           # falló antes de validar (falta un dato, el catálogo no se pudo leer…)
            self.review_box.append(_label(tr("publish_window.no_se_pudo", error=self.flow.error or t.error_text), css=("error",)))
            return
        self.review_box.append(_label(rep.resumen, css=("heading", "success" if rep.ok else "error"), selectable=False))
        if rep.errores:
            self.review_box.append(_label(tr("publish_window.errores"), css=("heading", "error"), selectable=False))
            self.review_box.append(_label("\n".join(self._issue_text(i) for i in rep.errores), css=("error",)))
            self.review_box.append(_label(tr("publish_window.errores_ayuda"), css=("dim-label",)))
        if rep.avisos or rep.avisos_exportacion:
            self.review_box.append(_label(tr("publish_window.avisos"), css=("heading", "warning"), selectable=False))
            self.review_box.append(_label("\n".join([self._issue_text(i) for i in rep.avisos] + [f"• {a}" for a in rep.avisos_exportacion]), css=("warning",)))
        if rep.ok:
            self.review_box.append(_label(tr("publish_window.listo"), css=("success",)))
            self.review_box.append(_label(tr("publish_window.no_verificado"), css=("dim-label",)))
        self.out_row.set_visible(rep.ok)

    @staticmethod
    def _issue_text(i: PB.Issue) -> str:
        where = f" ({i.donde})" if i.donde else ""
        return f"• {i.texto}{where}" + (f" → {i.arreglo}" if i.arreglo else "")

    # ------------------------------------------------------------------------------------------------------------------ salidas
    def _pick(self, title: str, cb, *, save_name: str = "", folder: bool = False) -> None:
        dlg = Gtk.FileDialog(title=title)
        if save_name:
            dlg.set_initial_name(save_name)

        def finished(d, res):
            try:
                f = d.select_folder_finish(res) if folder else d.save_finish(res)
            except GLib.Error:
                return                                            # cancelado
            path = f.get_path() if isinstance(f, Gio.File) else None
            if path:
                self._run_output(cb, path)

        (dlg.select_folder if folder else dlg.save)(self, None, finished)

    def _run_output(self, cb, path: str) -> None:
        try:
            self.out_msg.remove_css_class("error")
            self.out_msg.set_text(cb(path))
        except (PB.PublishError, OSError) as exc:
            self.out_msg.add_css_class("error")
            self.out_msg.set_text(str(exc))

    def _on_save_zip(self, *_) -> None:
        f = self.flow.form
        self._pick(tr("publish_window.guardar_zip"), lambda p: tr("publish_window.zip_guardado", ruta=self.flow.export_zip(p)),
                   save_name=f"{f.effective_id()}-{f.version}.zip")

    def _on_prepare_repo(self, *_) -> None:
        def make(path: str) -> str:
            import os
            repo = self.flow.form.github
            if not repo:
                raise PB.PublishError(tr("publish.github_invalido"))
            steps = self.flow.prepare_repo(os.path.join(path, self.flow.form.effective_id()), repo)
            return tr("publish_window.repo_listo") + "\n" + "\n".join(steps)
        self._pick(tr("publish_window.preparar_repo"), make, folder=True)

    def _on_prepare_alta(self, *_) -> None:
        def make(path: str) -> str:
            target, steps = self.flow.prepare_alta(path)
            return tr("publish_window.alta_lista", ruta=target) + "\n" + "\n".join(steps)
        self._pick(tr("publish_window.preparar_alta"), make, folder=True)

    # ------------------------------------------------------------------------------------------------------------------ cierre
    def _on_close(self, *_) -> bool:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
        self.flow.close()                                         # borra la carpeta temporal con el paquete armado
        return False


__all__ = ["PublishWindow"]
