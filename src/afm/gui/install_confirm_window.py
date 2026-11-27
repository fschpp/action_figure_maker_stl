"""Diálogo de confirmación e instalación (D19): lo que se instala, de quién es, con qué licencia, qué toca y qué conflictos hay, **antes** de escribir.

Solo dibuja: todo lo que decide sale de `install_confirm.InstallFlow`, que se prueba sin GTK. Lo lento (bajar y verificar los zips, instalar, verificar el
catálogo y medir la cobertura) corre en un hilo (`Task`) y el diálogo solo recibe el aviso por `GLib.idle_add`. **Sin apretar «Instalar» no se escribe nada**:
cerrar o cancelar deja el catálogo como estaba, y un fallo al instalar lo deshace todo. Se abre desde «Instalar» / «Actualizar» de la ficha de
«Explorar catálogos en línea…» (`catalog_online_window`).
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..i18n import tr  # noqa: E402
from . import catalog_online as CO  # noqa: E402
from . import install_confirm as IC  # noqa: E402


def _label(text: str = "", *, css: tuple = (), selectable: bool = True, wrap: bool = True) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=0.0, wrap=wrap, selectable=selectable)
    lb.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for c in css:
        lb.add_css_class(c)
    return lb


class InstallConfirmDialog(Gtk.Window):
    """`flow` es un `InstallFlow` (si no se pasa, se arma con `state` y `entry`). `on_installed(results)` se llama cuando la instalación terminó bien
    (antes del informe), para que quien abrió el diálogo refresque su lista."""

    def __init__(self, parent: Gtk.Window | None, state: CO.OnlineState | None = None, entry=None, version: str | None = None, *, flow: IC.InstallFlow | None = None,
                 on_installed=None, start: bool = True):
        super().__init__(title=tr("install_confirm_window.titulo"), transient_for=parent, modal=True, default_width=760, default_height=680)
        self.flow = flow or IC.InstallFlow(state, entry, version)
        self.on_installed = on_installed
        self._task: CO.Task | None = None
        self._on_done = None
        self._tick = 0
        self._closed = False
        self._updating = False
        self._build()
        self.connect("close-request", self._on_close)
        if start:
            self.propose()

    # ------------------------------------------------------------------------------------------------------------------ armado
    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("start", "end", "top", "bottom"):
            getattr(outer, f"set_margin_{side}")(12)
        self.set_child(outer)
        self.lbl_title = _label(self.flow.entry.nombre or self.flow.entry.id, css=("title-2",))
        outer.append(self.lbl_title)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.append(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.body))

        self.progress_row = Gtk.Box(spacing=6, visible=False)
        self.bar = Gtk.ProgressBar(hexpand=True, show_text=True)
        self.btn_cancel_task = Gtk.Button(label=tr("install_confirm_window.cancelar"))
        self.btn_cancel_task.connect("clicked", lambda *_: self._task and self._task.cancel())
        self.progress_row.append(self.bar)
        self.progress_row.append(self.btn_cancel_task)
        outer.append(self.progress_row)

        row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.btn_close = Gtk.Button(label=tr("install_confirm_window.cancelar"))
        self.btn_close.connect("clicked", lambda *_: self.close())
        self.btn_install = Gtk.Button(label=tr("install_confirm.boton_instalar"), sensitive=False)
        self.btn_install.add_css_class("suggested-action")
        self.btn_install.connect("clicked", self._on_install)
        row.append(self.btn_close)
        row.append(self.btn_install)
        outer.append(row)

    def _clear(self) -> None:
        child = self.body.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.body.remove(child)
            child = nxt

    # ------------------------------------------------------------------------------------------------------------------ tareas
    def _start(self, task: CO.Task, on_done) -> None:
        self._task, self._on_done = task, on_done
        self.progress_row.set_visible(True)
        self.btn_install.set_sensitive(False)
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
        self.btn_cancel_task.set_sensitive(t.cancelable)
        if t.finished:
            self.progress_row.set_visible(False)
            self._task = None
            done, self._on_done = self._on_done, None
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

    # ------------------------------------------------------------------------------------------------------------------ proponer
    def propose(self) -> None:
        """Baja y verifica los zips y arma el plan (no escribe). Al terminar muestra la confirmación."""
        self._clear()
        self.body.append(_label(tr("install_confirm_window.preparando"), css=("dim-label",)))
        self._start(self.flow.task_propose(notify=self._notify), self._after_propose)

    def _after_propose(self, t: CO.Task) -> None:
        if t.status == CO.FAILED:
            self._show_failure(tr("install_confirm_window.propuesta_error", error=t.error_text))
        elif t.status == CO.CANCELLED:
            self.close()
        else:
            self._show_confirmation()

    def _show_failure(self, text: str) -> None:
        self._clear()
        self.body.append(_label(text, css=("error",)))
        self.btn_install.set_visible(False)
        self.btn_close.set_label(tr("install_confirm_window.cerrar"))

    def _show_confirmation(self) -> None:
        c = self.flow.confirmation
        if c is None:
            return
        self._updating = True
        self._clear()
        self.lbl_title.set_text(c.titulo)
        grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        for n, (k, v) in enumerate(c.rows):
            grid.attach(_label(k, css=("dim-label",), selectable=False), 0, n, 1, 1)
            grid.attach(_label(v), 1, n, 1, 1)
        self.body.append(grid)
        if c.trae:
            self.body.append(_label(c.trae, css=("heading",)))
        if c.nada_que_hacer:
            self.body.append(_label(tr("install_confirm_window.nada_que_hacer"), css=("success",)))
        if c.errores:
            self.body.append(_label(tr("install_confirm_window.errores"), css=("heading", "error"), selectable=False))
            self.body.append(_label("\n".join(f"• {t}" for t in c.errores), css=("error",)))
        if c.avisos:
            self.body.append(_label(tr("install_confirm_window.avisos"), css=("heading", "warning"), selectable=False))
            self.body.append(_label("\n".join(f"• {t}" for t in c.avisos), css=("warning",)))
        for n, s in enumerate(c.secciones):
            ex = Gtk.Expander(label=s.titulo, expanded=n < 2)
            ex.set_child(_label("\n".join(s.lineas)))
            self.body.append(ex)
        # opciones que obligan a pensar o a proponer de nuevo
        self.chk_origen = self.chk_linea = self.chk_fbx = None
        if c.pide_origen:
            self.chk_origen = Gtk.CheckButton(label=tr("install_confirm_window.chk_origen"), active=self.flow.confirmar_origen)
            self.chk_origen.connect("toggled", self._on_origin_toggled)
            self.body.append(self.chk_origen)
        if c.ofrece_su_linea or self.flow.como_linea:
            self.chk_linea = Gtk.CheckButton(label=tr("install_confirm_window.chk_linea"), active=self.flow.como_linea)
            self.chk_linea.connect("toggled", self._on_option_toggled)
            self.body.append(self.chk_linea)
        if c.ofrece_fbx or self.flow.allow_fbx:
            self.chk_fbx = Gtk.CheckButton(label=tr("install_confirm_window.chk_fbx"), active=self.flow.allow_fbx)
            self.chk_fbx.connect("toggled", self._on_option_toggled)
            self.body.append(self.chk_fbx)
        self.body.append(_label(c.no_verificado, css=("dim-label",)))
        self.btn_install.set_label(c.boton)
        self.btn_install.set_visible(True)
        self.btn_install.set_sensitive(c.puede_instalar)
        self.btn_close.set_label(tr("install_confirm_window.cancelar"))
        self._updating = False

    # ------------------------------------------------------------------------------------------------------------------ opciones
    def _on_origin_toggled(self, btn: Gtk.CheckButton) -> None:
        if self._updating:
            return
        self.flow.confirmar_origen = btn.get_active()
        c = self.flow.confirmation
        self.btn_install.set_sensitive(bool(c and c.puede_instalar))

    def _on_option_toggled(self, *_) -> None:
        """Cambiar la línea destino o el FBX cambia el plan: hay que proponer de nuevo (la confirmación que se vio era de otro plan)."""
        if self._updating:
            return
        if self.chk_linea is not None:
            self.flow.como_linea = self.chk_linea.get_active()
        if self.chk_fbx is not None:
            self.flow.allow_fbx = self.chk_fbx.get_active()
        self.propose()

    # ------------------------------------------------------------------------------------------------------------------ instalar
    def _on_install(self, *_) -> None:
        """Recién acá se escribe: la persona apretó «Instalar» con la confirmación a la vista."""
        c = self.flow.confirmation
        if c is None or not c.puede_instalar:
            return
        self.btn_close.set_sensitive(False)
        self._start(self.flow.task_install(acepto=True, notify=self._notify), self._after_install)

    def _after_install(self, t: CO.Task) -> None:
        self.btn_close.set_sensitive(True)
        if t.status != CO.DONE:
            self._show_failure(tr("install_confirm_window.instalar_error", error=t.error_text))
            return
        rep = t.result
        if self.on_installed is not None:
            try:
                self.on_installed(rep.results)
            except Exception:                                  # noqa: BLE001 — refrescar la lista de quien abrió no puede esconder el informe
                pass
        self._show_report(rep)

    def _show_report(self, rep: IC.Report) -> None:
        self._clear()
        self.lbl_title.set_text(tr("install_confirm_window.resultado_titulo", id=self.flow.entry.id))
        self.body.append(_label("\n".join(rep.resumen), css=("success",)))
        self.body.append(_label(tr("install_confirm_window.carpeta", carpeta=rep.carpeta), css=("dim-label",)))
        self.body.append(_label(tr("install_confirm_window.verificar_ok" if rep.verificar_ok else "install_confirm_window.verificar_mal"),
                                css=("heading", "success" if rep.verificar_ok else "error"), selectable=False))
        ex = Gtk.Expander(label=tr("install_confirm_window.detalle_verificar"), expanded=not rep.verificar_ok)
        ex.set_child(_label("\n".join(rep.verificar)))
        self.body.append(ex)
        if rep.cobertura:
            ex = Gtk.Expander(label=tr("install_confirm_window.cobertura_titulo"))
            ex.set_child(_label("\n".join(rep.cobertura)))
            self.body.append(ex)
        self.btn_install.set_visible(False)
        self.btn_close.set_label(tr("install_confirm_window.cerrar"))

    # ------------------------------------------------------------------------------------------------------------------ cierre
    def _on_close(self, *_) -> bool:
        """Cerrar o cancelar antes de «Instalar» no escribe nada. Mientras se escribe el catálogo no se cierra (la instalación no se corta a la mitad)."""
        if self.flow.stage == IC.INSTALANDO and self._task is not None and not self._task.finished:
            return True
        self._closed = True
        if self._task is not None:
            self._task.cancel()
        return False


__all__ = ["InstallConfirmDialog"]
