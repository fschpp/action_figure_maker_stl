"""Ventana «Paleta de la línea» (T46): roles de color, su color, las piezas que los usan y una vista previa pintada con la paleta.

Toda la lógica (borrador, validación, uso, importar/exportar, vista previa) está en `palette_editor.py` y se prueba sin GTK; acá solo
está la ventana. **No se probó en pantalla** (no hay GTK en el entorno de desarrollo, R9). Se abre desde el menú «Catálogo ▾» de la
ventana «Administrar catálogo». Cambiar un color solo toca el borrador y la vista previa; `paleta.json` se escribe con «Aplicar».
"""
from __future__ import annotations

from ..i18n import tr

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .catalog_manager import PreviewRenderer  # noqa: E402
from .palette_editor import PaletteEditError, PaletteEditor  # noqa: E402
from ..palette import hex_to_rgb  # noqa: E402


def _rgba(hex_text: str) -> Gdk.RGBA:
    r, g, b = hex_to_rgb(hex_text)
    return Gdk.RGBA(red=r / 255.0, green=g / 255.0, blue=b / 255.0, alpha=1.0)


def _hex_of(rgba: Gdk.RGBA) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(round(max(0.0, min(1.0, c)) * 255)) for c in (rgba.red, rgba.green, rgba.blue)))


class PaletteWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, ctrl, mgr=None):
        super().__init__(title=tr("palette_panel.titulo"), transient_for=parent, default_width=1000, default_height=640)
        self.ctrl = ctrl
        self.editor = PaletteEditor(ctrl)
        self.preview = PreviewRenderer()
        self._updating = False                 # se están poniendo valores en los campos: no tomarlos como un cambio del usuario
        self._closed = False                   # ya se destruyó la ventana: ningún callback tardío debe tocarla
        self._asking = False                   # hay un «cambios sin aplicar» abierto: un segundo cierre no apila otro
        self._rows: list = []                  # [(Gtk.ListBoxRow, id de rol)]
        self._refs: list[str] = []             # piezas con roles que ofrece la vista previa
        self._dyaw = 0.0
        self._dpitch = 0.0
        self._drag0 = (0.0, 0.0)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True, vexpand=True)
        paned.set_start_child(self._build_roles())
        paned.set_end_child(self._build_preview())
        paned.set_position(460)
        paned.set_shrink_start_child(True)
        paned.set_shrink_end_child(True)
        outer.append(paned)
        outer.append(self._build_bar())
        self.set_child(outer)
        self.connect("destroy", self._on_destroy)
        self.connect("close-request", self._on_close_request)
        self._reload(initial=True)

    # ------------------------------------------------------------ construcción
    def _build_roles(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(Gtk.Label(label=tr("palette_panel.roles"), xalign=0.0, css_classes=["heading"]))
        self.lbl_empty = Gtk.Label(label=tr("palette_panel.sin_paleta"), xalign=0.0, wrap=True, visible=False)
        box.append(self.lbl_empty)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, min_content_height=200)
        scroll.set_child(self.listbox)
        box.append(scroll)

        grid = Gtk.Grid(row_spacing=6, column_spacing=8)
        grid.attach(Gtk.Label(label=tr("palette_panel.nombre"), xalign=1.0), 0, 0, 1, 1)
        self.ent_name = Gtk.Entry(hexpand=True)
        self.ent_name.connect("activate", self._on_name_activate)
        grid.attach(self.ent_name, 1, 0, 2, 1)
        grid.attach(Gtk.Label(label=tr("palette_panel.id"), xalign=1.0), 0, 1, 1, 1)
        self.lbl_id = Gtk.Label(xalign=0.0, selectable=True)
        grid.attach(self.lbl_id, 1, 1, 2, 1)
        grid.attach(Gtk.Label(label=tr("palette_panel.color_hex"), xalign=1.0), 0, 2, 1, 1)
        self.ent_hex = Gtk.Entry(hexpand=True, max_length=7, width_chars=9)
        self.ent_hex.connect("changed", self._on_hex_changed)
        grid.attach(self.ent_hex, 1, 2, 1, 1)
        self.btn_color = None
        if hasattr(Gtk, "ColorDialogButton"):             # GTK ≥ 4.10; en una versión anterior queda solo el campo HEX
            self.btn_color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
            self.btn_color.connect("notify::rgba", self._on_color_picked)
            grid.attach(self.btn_color, 2, 2, 1, 1)
        box.append(grid)
        self.lbl_notice = Gtk.Label(xalign=0.0, wrap=True)            # cuántas piezas recolorea el rol elegido
        box.append(self.lbl_notice)

        bar = Gtk.Box(spacing=6)
        self.btn_add = Gtk.Button(label=tr("palette_panel.agregar"))
        self.btn_add.connect("clicked", lambda *_: self._ask_new_role())
        self.btn_remove = Gtk.Button(label=tr("palette_panel.quitar"))
        self.btn_remove.connect("clicked", lambda *_: self._remove_selected())
        self.btn_import = Gtk.Button(label=tr("palette_panel.importar"))
        self.btn_import.connect("clicked", lambda *_: self._import())
        self.btn_export = Gtk.Button(label=tr("palette_panel.exportar"))
        self.btn_export.connect("clicked", lambda *_: self._export())
        for b in (self.btn_add, self.btn_remove, self.btn_import, self.btn_export):
            bar.append(b)
        box.append(bar)
        return box

    def _build_preview(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(Gtk.Label(label=tr("palette_panel.vista_previa"), xalign=0.0, css_classes=["heading"]))
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=tr("palette_panel.pieza_con_roles")))
        self.drop = Gtk.DropDown(model=Gtk.StringList.new([]), hexpand=True)
        self.drop.connect("notify::selected", lambda *_: self._render())
        row.append(self.drop)
        box.append(row)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.picture.set_size_request(320, 300)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_: setattr(self, "_drag0", (self._dyaw, self._dpitch)))
        drag.connect("drag-update", self._on_drag)
        self.picture.add_controller(drag)
        box.append(self.picture)
        self.lbl_preview = Gtk.Label(xalign=0.0, wrap=True)
        box.append(self.lbl_preview)
        return box

    def _build_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=8)
        self.lbl_status = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
        bar.append(self.lbl_status)
        self.btn_revert = Gtk.Button(label=tr("palette_panel.descartar"))
        self.btn_revert.connect("clicked", lambda *_: self._revert())
        self.btn_apply = Gtk.Button(label=tr("palette_panel.aplicar"), css_classes=["suggested-action"])
        self.btn_apply.connect("clicked", lambda *_: self._apply())
        bar.append(self.btn_revert)
        bar.append(self.btn_apply)
        return bar

    # ------------------------------------------------------------ estado de la pantalla
    def _selected_id(self) -> str | None:
        row = self.listbox.get_selected_row()
        return next((rid for r, rid in self._rows if r is row), None) if row is not None else None

    def _on_destroy(self, *_a) -> None:
        self._closed = True
        self.preview.close()

    def _say(self, text: str) -> None:
        if self._closed:
            return
        self.lbl_status.set_text(text)

    def _error(self, exc: Exception) -> None:
        if self._closed:
            return
        dlg = Gtk.AlertDialog(message=str(exc), buttons=[tr("common.cancelar")], cancel_button=0, default_button=0)
        dlg.show(self)
        self._say(str(exc))

    def _reload(self, initial: bool = False, keep: str | None = None) -> None:
        """Vuelve a dibujar la lista desde el borrador (conserva la selección si el rol sigue)."""
        if initial:
            try:
                self.editor.reload()
            except PaletteEditError as exc:
                self._say(str(exc))
        keep = keep or self._selected_id()
        self._updating = True
        try:
            while (child := self.listbox.get_first_child()) is not None:
                self.listbox.remove(child)
            self._rows = []
            for info in self.editor.rows():
                row = Gtk.ListBoxRow()
                line = Gtk.Box(spacing=8, margin_top=3, margin_bottom=3, margin_start=6, margin_end=6)
                sw = Gtk.DrawingArea(content_width=26, content_height=26)
                r, g, b = hex_to_rgb(info["hex"])
                sw.set_draw_func(lambda _a, cr, _w, _h, r=r, g=g, b=b: (cr.set_source_rgb(r / 255.0, g / 255.0, b / 255.0), cr.paint()))
                line.append(sw)
                mark = "• " if info["cambiado"] else ""
                line.append(Gtk.Label(label=f"{mark}{info['nombre']}", xalign=0.0, hexpand=True))
                line.append(Gtk.Label(label=info["id"], css_classes=["dim-label"]))
                line.append(Gtk.Label(label=tr("palette_panel.piezas_n", n=info["n_piezas"]), css_classes=["dim-label"]))
                row.set_child(line)
                self.listbox.append(row)
                self._rows.append((row, info["id"]))
            target = next((r for r, rid in self._rows if rid == keep), self._rows[0][0] if self._rows else None)
            if target is not None:
                self.listbox.select_row(target)
        finally:
            self._updating = False
        self.lbl_empty.set_visible(not self.editor.draft.roles)
        self._fill_fields()
        self._refresh_buttons()
        self._refresh_previews(initial)

    def _fill_fields(self) -> None:
        rid = self._selected_id()
        self._updating = True
        try:
            info = next((i for i in self.editor.rows() if i["id"] == rid), None)
            self.ent_name.set_text(info["nombre"] if info else "")
            self.lbl_id.set_text(info["id"] if info else "")
            self.ent_hex.set_text(info["hex"] if info else "")
            if self.btn_color is not None and info:
                self.btn_color.set_rgba(_rgba(info["hex"]))
            self.lbl_notice.set_text(self.editor.usage_notice(rid) if rid else "")
        finally:
            self._updating = False
        for w in (self.ent_name, self.ent_hex):
            w.set_sensitive(rid is not None)
        if self.btn_color is not None:
            self.btn_color.set_sensitive(rid is not None)

    def _refresh_buttons(self) -> None:
        has_cat = self.editor.has_catalog
        self.btn_apply.set_sensitive(has_cat and self.editor.dirty)
        self.btn_revert.set_sensitive(self.editor.dirty)
        self.btn_add.set_sensitive(has_cat)
        self.btn_import.set_sensitive(has_cat)
        self.btn_remove.set_sensitive(self._selected_id() is not None)
        self.btn_export.set_sensitive(bool(self.editor.draft.roles))
        if self.editor.dirty:
            self._say(tr("palette_panel.cambios_sin_aplicar"))

    def _refresh_previews(self, rebuild_list: bool) -> None:
        if rebuild_list:
            self._refs = self.editor.preview_refs() if self.editor.has_catalog else []
            self._updating = True
            try:
                self.drop.set_model(Gtk.StringList.new(self._refs))
            finally:
                self._updating = False
            self.lbl_preview.set_text("" if self._refs else tr("palette_panel.sin_vista_previa"))
        self._render()

    def _render(self) -> None:
        """Repinta la pieza elegida con el borrador (LUT sobre el atlas; no se guarda nada)."""
        if self._closed:                                     # no recrear el contexto EGL de la vista previa después de cerrarlo
            return
        if self._updating or not self._refs:
            self.picture.set_paintable(None) if not self._refs else None
            return
        idx = self.drop.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(self._refs):
            return
        try:
            parts = self.editor.preview_parts(self._refs[idx])
            img = self.preview.render_textured(parts, True, self._dyaw, self._dpitch)
        except PaletteEditError as exc:                    # p. ej. al borrador le falta un rol que usa la pieza
            self.picture.set_paintable(None)
            self.lbl_preview.set_text(str(exc))
            return
        except Exception as exc:                           # noqa: BLE001  (sin EGL: el resto de la ventana sigue andando)
            self.picture.set_paintable(None)
            self.lbl_preview.set_text(tr("palette_panel.sin_gl") + f" ({exc})")
            return
        self.lbl_preview.set_text("")
        h, w = img.shape[:2]
        self.picture.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(img.tobytes()), w * 4))

    def _on_drag(self, _g, dx: float, dy: float) -> None:
        y0, p0 = self._drag0
        self._dyaw = y0 - dx * 0.4
        self._dpitch = p0 + dy * 0.4
        self._render()

    # ------------------------------------------------------------ eventos
    def _on_row_selected(self, _lb, _row) -> None:
        if not self._updating:
            self._fill_fields()
            self._refresh_buttons()

    def _on_hex_changed(self, entry) -> None:
        rid = self._selected_id()
        text = entry.get_text().strip()
        if self._updating or rid is None or len(text) != 7:        # se espera a que el color esté completo
            return
        try:
            hx = self.editor.set_hex(rid, text)
        except PaletteEditError as exc:
            self._say(str(exc))
            return
        if self.btn_color is not None:
            self._updating = True
            try:
                self.btn_color.set_rgba(_rgba(hx))
            finally:
                self._updating = False
        self._reload(keep=rid)

    def _on_color_picked(self, btn, _pspec) -> None:
        rid = self._selected_id()
        if self._updating or rid is None:
            return
        try:
            self.editor.set_hex(rid, _hex_of(btn.get_rgba()))
        except PaletteEditError as exc:
            self._say(str(exc))
            return
        self._reload(keep=rid)

    def _on_name_activate(self, entry) -> None:
        rid = self._selected_id()
        if rid is None:
            return
        try:
            self.editor.rename(rid, entry.get_text())
        except PaletteEditError as exc:
            self._error(exc)
            return
        self._reload(keep=rid)

    def _ask_new_role(self) -> None:
        win = Gtk.Window(title=tr("palette_panel.agregar"), transient_for=self, modal=True, default_width=360)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(Gtk.Label(label=tr("palette_panel.id_nuevo"), xalign=0.0))
        ent = Gtk.Entry(activates_default=True)
        box.append(ent)
        bar = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=tr("common.cancelar"))
        ok = Gtk.Button(label=tr("palette_panel.agregar"), css_classes=["suggested-action"])
        bar.append(cancel)
        bar.append(ok)
        box.append(bar)
        win.set_child(box)
        win.set_default_widget(ok)

        def accept(*_a):
            sel = next((i for i in self.editor.rows() if i["id"] == self._selected_id()), None)
            try:
                self.editor.add(ent.get_text(), sel["hex"] if sel else "#808080")      # arranca con el color del rol elegido
            except PaletteEditError as exc:
                self._error(exc)
                return
            rid = ent.get_text().strip()
            win.close()
            self._reload(keep=rid)

        ok.connect("clicked", accept)
        ent.connect("activate", accept)
        cancel.connect("clicked", lambda *_: win.close())
        win.present()

    def _remove_selected(self) -> None:
        rid = self._selected_id()
        if rid is None:
            return
        try:
            self.editor.remove(rid)
        except PaletteEditError as exc:                       # rol en uso: el mensaje lista las piezas
            self._error(exc)
            return
        self._reload()

    def _apply(self) -> None:
        try:
            done = self.editor.apply()
        except PaletteEditError as exc:
            self._error(exc)
            return
        self._reload(initial=False)
        self._say(tr("palette_panel.aplicada", ruta=self.editor.ctrl.catalog_handle().palette_path) if done else tr("palette_panel.sin_cambios"))

    def _revert(self) -> None:
        self.editor.revert()
        self._reload()
        self._say(tr("palette_panel.descartada"))

    def _import(self) -> None:
        dlg = Gtk.FileDialog(title=tr("palette_panel.elegir_json"))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return                                         # cancelado
            try:
                self.editor.import_json(f.get_path())
            except PaletteEditError as exc:
                self._error(exc)
                return
            self._reload(initial=True)
            self._say(tr("palette_panel.importada"))

        dlg.open(self, None, done)

    def _export(self) -> None:
        dlg = Gtk.FileDialog(title=tr("palette_panel.guardar_json"), initial_name="paleta.json")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                path = self.editor.export_json(f.get_path())
            except PaletteEditError as exc:
                self._error(exc)
                return
            self._say(tr("palette_panel.exportada", ruta=path))

        dlg.save(self, None, done)

    def _on_close_request(self, *_a) -> bool:
        """Con cambios sin aplicar pregunta antes de cerrar; devuelve True para frenar el cierre."""
        if self._closed:
            return False
        if not self.editor.dirty:
            return False
        if self._asking:                                     # ya se está preguntando: no apilar otro diálogo
            return True
        self._asking = True
        dlg = Gtk.AlertDialog(message=tr("palette_panel.cambios_sin_aplicar"), buttons=[tr("common.cancelar"), tr("palette_panel.descartar")],
                              cancel_button=0, default_button=0)

        def chosen(d, res):
            self._asking = False
            if self._closed:
                return
            try:
                if d.choose_finish(res) == 1:
                    self.editor.revert()
                    if not self._closed:
                        self.destroy()
            except GLib.Error:
                pass

        dlg.choose(self, None, chosen)
        return True
