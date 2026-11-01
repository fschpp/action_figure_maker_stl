"""Ventana «Administrar catálogo»: lista de piezas con vista previa 3/4 y eliminación de una o varias.

La lógica (filtro, textos, cámara, borrado) está en `catalog_manager.py` y se prueba sin GTK; acá solo está la ventana.
La vista previa se dibuja fuera de pantalla (`PreviewRenderer`, contexto EGL propio) y se muestra como imagen: así no
compite con el contexto OpenGL del visor de la ventana principal. Se abre desde la pestaña «Catálogo» de la ventana principal."""
from __future__ import annotations

import glob
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .catalog_manager import (ALL_TYPES, CatalogManager, PreviewRenderer, WristCutter, can_cut_wrist, detail_text,  # noqa: E402
                              matches, row_text)
from .catalog_manager import ZOOM_STEP, zoom_about  # noqa: E402
from .catalog_manager import view_bounds as CM_view_bounds  # noqa: E402

CLICK_MAX_PX = 4.0          # un arrastre más corto que esto es un clic (marca un punto); más largo gira la vista


class CatalogManagerWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, ctrl):
        super().__init__(title="Administrar catálogo de piezas", transient_for=parent, default_width=1020, default_height=640)
        self.ctrl = ctrl
        self.mgr = CatalogManager(ctrl)
        self.preview = PreviewRenderer()
        self._rows: list = []                 # [(Gtk.ListBoxRow, PieceMeta)] de lo que muestra la lista
        self._updating = False
        self._signature = None
        self._shown = None                    # (meta, malla) de la pieza que se está viendo
        self._dyaw = 0.0
        self._dpitch = 0.0
        self._drag0 = (0.0, 0.0)
        self._zoom = 1.0
        self._zoom0 = 1.0
        self._cursor = (0.0, 0.0)
        self._pan = (0.0, 0.0)
        self._pan0 = (0.0, 0.0)
        self._press = (0.0, 0.0)              # dónde empezó el último arrastre (widget): si fue un clic, ahí se marca un punto
        self._img_size = (self.preview.width, self.preview.height)
        self._cutter: WristCutter | None = None      # modo «Cortar la mano…» (None = no está activo)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
        paned.set_start_child(self._build_list())
        paned.set_end_child(self._build_detail())
        paned.set_position(430)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        self.set_child(paned)
        self.connect("destroy", lambda *_: self.preview.close())
        win_keys = Gtk.EventControllerKey()
        win_keys.connect("key-pressed", self._on_window_key)
        self.add_controller(win_keys)
        self._reload()

    # ------------------------------------------------------------ construcción
    def _build_list(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        self.lbl_title = Gtk.Label(xalign=0, wrap=True)
        self.lbl_title.add_css_class("heading")
        box.append(self.lbl_title)
        self.search = Gtk.SearchEntry(placeholder_text="Buscar: nombre, tipo, figura de origen, fecha…")
        self.search.connect("search-changed", lambda *_: self._reload())
        box.append(self.search)
        row = Gtk.Box(spacing=6)
        self.dd_type = Gtk.DropDown.new_from_strings([ALL_TYPES])
        self.dd_type.set_hexpand(True)
        self.dd_type.connect("notify::selected", lambda *_: None if self._updating else self._reload())
        row.append(self.dd_type)
        btn_all = Gtk.Button(label="Elegir todas las que se ven")
        btn_all.connect("clicked", lambda *_: self.listbox.select_all())
        row.append(btn_all)
        box.append(row)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("selected-rows-changed", self._on_selection)
        # Clic simple = elegir SOLO esa pieza; Mayús o Ctrl + clic = agregarla / quitarla de lo elegido (GTK acumularía con cada clic)
        click = Gtk.GestureClick(button=1)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_list_click)
        self.listbox.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.listbox.add_controller(keys)
        sw = Gtk.ScrolledWindow(vexpand=True, hexpand=True, min_content_width=380)
        sw.set_child(self.listbox)
        box.append(sw)
        hint = Gtk.Label(label="Clic elige una sola pieza; Mayús + clic (o Ctrl + clic) agrega o quita otra. Filtrá por texto y usá «Elegir todas las que se ven» "
                               "para borrar de una vez, por ejemplo, todo lo catalogado desde un proyecto equivocado.",
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        box.append(hint)
        return box

    def _build_detail(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.picture.set_size_request(360, 300)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag)
        drag.connect("drag-end", self._on_drag_end)
        self.picture.add_controller(drag)
        pan = Gtk.GestureDrag(button=0)                       # botón derecho o central: mover la vista
        pan.connect("drag-begin", self._on_pan_begin)
        pan.connect("drag-update", self._on_pan)
        self.picture.add_controller(pan)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self._on_scroll)
        self.picture.add_controller(wheel)
        pinch = Gtk.GestureZoom()                             # pellizco con dos dedos en el touchpad o la pantalla táctil
        pinch.connect("begin", lambda *_: setattr(self, "_zoom0", self._zoom))
        pinch.connect("scale-changed", self._on_pinch)
        self.picture.add_controller(pinch)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: setattr(self, "_cursor", (x, y)))
        self.picture.add_controller(motion)
        frame = Gtk.Frame()
        frame.set_child(self.picture)
        box.append(frame)

        bar = Gtk.Box(spacing=6)
        reset = Gtk.Button(label="Vista 3/4")
        reset.set_tooltip_text("Vuelve a la vista 3/4 y sin zoom. Arrastrar con el ratón gira la pieza, la rueda acerca o aleja y el botón derecho la mueve; los puntos naranjas son los puertos. "
                               "Con «Cortar la mano…» activo, un clic marca un punto de la muñeca.")
        reset.connect("clicked", self._on_reset_view)
        bar.append(reset)
        for txt, tip, fac in (("−", "Alejar (también la rueda del ratón)", 1 / ZOOM_STEP), ("+", "Acercar (también la rueda del ratón)", ZOOM_STEP)):
            b = Gtk.Button(label=txt)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, f=fac: self._zoom_by(f))
            bar.append(b)
        bar.append(Gtk.Label(label="Arrastrá para girar · rueda o pellizco = zoom · botón derecho = mover · naranja = puertos", xalign=0, hexpand=True))
        self.btn_wrist = Gtk.Button(label="Cortar la mano…")
        self.btn_wrist.set_sensitive(False)
        self.btn_wrist.connect("clicked", self._on_wrist_start)
        bar.append(self.btn_wrist)
        self.btn_delete = Gtk.Button(label="Eliminar…")
        self.btn_delete.add_css_class("destructive-action")
        self.btn_delete.connect("clicked", self._on_delete)
        bar.append(self.btn_delete)
        box.append(bar)
        box.append(self._build_wrist_panel())

        self.lbl_detail = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True)
        self.lbl_detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        sw = Gtk.ScrolledWindow(min_content_height=130, vexpand=False)
        sw.set_child(self.lbl_detail)
        box.append(sw)
        self.lbl_status = Gtk.Label(xalign=0, wrap=True)
        self.lbl_status.add_css_class("dim-label")
        box.append(self.lbl_status)
        return box

    # ------------------------------------------------------------ cortar la mano de un brazo
    def _build_wrist_panel(self) -> Gtk.Widget:
        """Panel del modo «Cortar la mano…» (oculto hasta que se activa)."""
        self.wrist_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        self.lbl_wrist = Gtk.Label(xalign=0, wrap=True)
        self.lbl_wrist.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.wrist_panel.append(self.lbl_wrist)
        row = Gtk.Box(spacing=6)
        self.btn_wrist_undo = Gtk.Button(label="Deshacer el último punto")
        self.btn_wrist_undo.connect("clicked", self._on_wrist_undo)
        self.btn_wrist_invert = Gtk.Button(label="Invertir lado")
        self.btn_wrist_invert.set_tooltip_text("Si lo verde no es la mano sino el resto del brazo, toma el otro lado del plano como mano.")
        self.btn_wrist_invert.connect("clicked", self._on_wrist_invert)
        self.btn_wrist_cancel = Gtk.Button(label="Cancelar (Esc)")
        self.btn_wrist_cancel.connect("clicked", lambda *_: self._wrist_cancel())
        for b in (self.btn_wrist_undo, self.btn_wrist_invert, self.btn_wrist_cancel):
            row.append(b)
        self.wrist_panel.append(row)
        row2 = Gtk.Box(spacing=6)
        row2.append(Gtk.Label(label="Id de la mano:"))
        self.ent_hand_id = Gtk.Entry(hexpand=True)
        self.ent_hand_id.set_tooltip_text("Pieza «mano» que se guarda aparte. Si ya existe, tildá «versión nueva» o elegí otro id.")
        row2.append(self.ent_hand_id)
        self.chk_hand_new = Gtk.CheckButton(label="versión nueva si ya existe")
        row2.append(self.chk_hand_new)
        self.btn_wrist_save = Gtk.Button(label="Guardar")
        self.btn_wrist_save.add_css_class("suggested-action")
        self.btn_wrist_save.connect("clicked", self._on_wrist_save)
        row2.append(self.btn_wrist_save)
        self.wrist_panel.append(row2)
        return self.wrist_panel

    def _sync_wrist_button(self, refs: list[str]) -> None:
        """«Cortar la mano…» se puede usar con UN brazo elegido que todavía tiene la mano pegada."""
        ok, why = False, "Elegí un brazo de la lista."
        if len(refs) == 1:
            meta = next((m for _row, m in self._rows if m.ref == refs[0]), None)
            ok, why = can_cut_wrist(meta)
        elif len(refs) > 1:
            why = "Elegí un solo brazo."
        if ok and self.ctrl.busy:
            ok, why = False, "Esperá a que termine la tarea en curso."
        if ok and self._shown is None:
            ok, why = False, "No hay vista previa de la pieza para marcar la muñeca."
        self.btn_wrist.set_sensitive(ok and self._cutter is None)
        self.btn_wrist.set_tooltip_text("Le corta la mano al brazo: tocás 3 puntos de la muñeca sobre la vista previa y se guarda una versión "
                                        "nueva del brazo sin mano más la mano como pieza aparte (el brazo original no se toca)." if ok else why)

    def _on_wrist_start(self, *_) -> None:
        if self._shown is None or self._cutter is not None:
            return
        meta, mesh = self._shown
        ok, why = can_cut_wrist(meta)
        if not ok:
            self.lbl_status.set_text(why)
            return
        self._cutter = WristCutter(self.ctrl, meta, mesh)
        self._dyaw = self._dpitch = 0.0
        self._zoom, self._pan = 1.0, (0.0, 0.0)
        self.ent_hand_id.set_text("")
        self.ent_hand_id.set_placeholder_text(self._cutter.default_hand_id())
        self.chk_hand_new.set_active(False)
        self.wrist_panel.set_visible(True)
        self.btn_wrist.set_sensitive(False)
        self.btn_delete.set_sensitive(False)
        self._wrist_refresh()

    def _wrist_cancel(self, redraw: bool = True) -> None:
        if self._cutter is None:
            return
        self._cutter = None
        self.wrist_panel.set_visible(False)
        self._sync_wrist_button(self._selected_refs())
        self.btn_delete.set_sensitive(bool(self._selected_refs()) and not self.ctrl.busy)
        if redraw:
            self._redraw()

    def _wrist_refresh(self) -> None:
        """Texto, botones y dibujo según cuántos puntos hay y si el corte salió."""
        c = self._cutter
        if c is None:
            return
        self.lbl_wrist.set_text(c.message())
        self.btn_wrist_undo.set_sensitive(bool(c.points))
        self.btn_wrist_invert.set_sensitive(c.complete)
        self.btn_wrist_save.set_sensitive(c.plan is not None)
        self.ent_hand_id.set_sensitive(c.complete)
        self.chk_hand_new.set_sensitive(c.complete)
        self._redraw()

    def _on_wrist_undo(self, *_) -> None:
        if self._cutter is not None:
            self._cutter.undo()
            self._wrist_refresh()

    def _on_wrist_invert(self, *_) -> None:
        if self._cutter is not None:
            self._cutter.toggle_invert()
            self._wrist_refresh()

    def _on_wrist_save(self, *_) -> None:
        c = self._cutter
        if c is None or self.ctrl.busy:
            return
        c.set_hand_id_and_recompute(self.ent_hand_id.get_text())         # el id puede haber cambiado desde que se vio el corte
        if c.plan is None:
            self._wrist_refresh()
            return
        res = c.save(self.chk_hand_new.get_active())
        if res is None:                                                  # p. ej. el id de la mano ya existe: el motivo está en el mensaje
            self.lbl_wrist.set_text(self.ctrl.message or "No se pudo guardar.")
            return
        self._wrist_cancel(redraw=False)
        self._reload()
        for row, m in self._rows:                                        # deja elegido SOLO el brazo nuevo para verlo
            if m.ref == res.arm.ref:
                self.listbox.unselect_all()
                self.listbox.select_row(row)
                break
        self.lbl_status.set_text(self.ctrl.message or "")                # después: elegir otra pieza limpia el texto de estado

    def _on_window_key(self, _ctl, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape and self._cutter is not None:
            self._wrist_cancel()
            return True
        return False

    # ------------------------------------------------------------ lista
    def _type_filter(self) -> str | None:
        item = self.dd_type.get_selected_item()
        txt = item.get_string() if item is not None else ALL_TYPES
        return None if txt == ALL_TYPES else txt

    def _type_names(self) -> list[str]:
        model = self.dd_type.get_model()
        names = [model.get_string(i) for i in range(model.get_n_items())] if model is not None else []
        return sorted(n for n in names if n != ALL_TYPES)

    def _fill_types(self, present: list[str]) -> None:
        current = self._type_filter()
        names = [ALL_TYPES] + sorted(set(present))
        self._updating = True
        try:
            self.dd_type.set_model(Gtk.StringList.new(names))
            self.dd_type.set_selected(names.index(current) if current in names else 0)
        finally:
            self._updating = False

    def _on_list_click(self, gesture: Gtk.GestureClick, _n: int, _x: float, y: float) -> None:
        row = self.listbox.get_row_at_y(int(y))
        if row is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)          # el comportamiento por defecto de GTK no corre
        extend = bool(gesture.get_current_event_state() & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK))
        self._updating = True                                      # un solo redibujado al final, no uno por cada cambio
        try:
            if extend:
                (self.listbox.unselect_row if row.is_selected() else self.listbox.select_row)(row)
            else:
                self.listbox.unselect_all()
                self.listbox.select_row(row)
        finally:
            self._updating = False
        self.listbox.grab_focus()
        self._on_selection()

    def _selected_refs(self) -> list[str]:
        sel = {id(r) for r in self.listbox.get_selected_rows()}
        return [m.ref for row, m in self._rows if id(row) in sel]

    def _disk_signature(self) -> tuple:
        """Qué hay en el disco (archivo y fecha de cada ficha): barato, para no releer todo el catálogo en cada cambio."""
        root = self.ctrl.session.catalog_root
        if not root:
            return ()
        files = glob.glob(os.path.join(root, "piezas", "*", "*.pieza.json"))
        try:
            return (root, tuple(sorted((p, os.stat(p).st_mtime_ns) for p in files)))
        except OSError:
            return (root, None)

    def refresh_if_changed(self) -> None:
        """La ventana principal avisa cuando algo cambió (p. ej. se catalogó otra figura con esta ventana abierta)."""
        if self._disk_signature() != self._signature:
            self._reload()

    def _reload(self) -> None:
        keep = set(self._selected_refs())
        self._signature = self._disk_signature()
        everything = self.ctrl.catalog_pieces()              # una sola lectura del catálogo por recarga
        if sorted({m.tipo for m in everything}) != self._type_names():
            self._fill_types([m.tipo for m in everything])
        text, tipo = self.search.get_text(), self._type_filter()
        metas = [m for m in everything if matches(m, text, tipo)]
        root = self.ctrl.session.catalog_root
        total = len(everything)
        self.lbl_title.set_text("No hay un catálogo abierto." if not root else
                                f"Catálogo: {root}\n{total} versión(es) de pieza" + ("" if len(metas) == total else f" · se ven {len(metas)}"))
        self._updating = True
        try:
            while (child := self.listbox.get_first_child()) is not None:
                self.listbox.remove(child)
            self._rows = []
            for m in metas:
                row = Gtk.ListBoxRow()
                row.set_child(Gtk.Label(label=row_text(m), xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                        margin_top=4, margin_bottom=4, margin_start=8, margin_end=8))
                self.listbox.append(row)
                self._rows.append((row, m))
                if m.ref in keep:
                    self.listbox.select_row(row)
        finally:
            self._updating = False
        self._on_selection()

    # ------------------------------------------------------------ selección y vista previa
    def _on_selection(self, *_) -> None:
        if self._updating:
            return
        refs = self._selected_refs()
        n = len(refs)
        if self._cutter is not None and refs != [self._cutter.meta.ref]:        # eligió otra pieza: se sale del modo sin guardar nada
            self._wrist_cancel(redraw=False)
        self.btn_delete.set_label("Eliminar…" if n == 0 else f"Eliminar {n} pieza(s)…")
        self.btn_delete.set_sensitive(bool(n) and not self.ctrl.busy and self._cutter is None)
        if n == 0:
            self.lbl_detail.set_text("Elegí una pieza de la lista para verla." if self._rows else "El catálogo no tiene piezas (todavía).")
            self._show(None)
            self._sync_wrist_button(refs)
            return
        meta = next(m for _row, m in self._rows if m.ref == refs[0])
        text = detail_text(meta, self.mgr.uses(meta.ref))
        if n > 1:
            text = f"{n} piezas elegidas (se muestra la primera).\n\n" + text
        self.lbl_detail.set_text(text)
        if self._shown is None or self._shown[0].ref != meta.ref:      # agregar una a la selección no redibuja la misma
            self._show(meta)
        self._sync_wrist_button(refs)

    def _show(self, meta) -> None:
        """Pone `meta` (o nada) en la vista previa, en vista 3/4."""
        self._dyaw = self._dpitch = 0.0
        self._zoom, self._pan = 1.0, (0.0, 0.0)
        self._shown = None
        if meta is None:
            self.preview.clear()
            self.picture.set_paintable(None)
            self.lbl_status.set_text("")
            return
        try:
            self._shown = (meta, self.mgr.mesh(meta.ref))
        except (ValueError, OSError) as exc:
            self.picture.set_paintable(None)
            self.lbl_status.set_text(f"No se pudo leer la malla de {meta.ref}: {exc}")
            return
        self._redraw()

    def _redraw(self) -> None:
        if self._shown is None:
            return
        meta, mesh = self._shown
        c = self._cutter
        try:
            if c is not None and c.preview_parts() is not None:          # corte calculado: brazo sin mano en gris y la mano en verde
                img = self.preview.render_parts(c.preview_parts(), self._dyaw, self._dpitch, c.marks(),
                                                zoom=self._zoom, pan=self._pan,
                                                bounds=CM_view_bounds(mesh, meta))      # mismo encuadre que antes del corte: la vista no salta
            else:
                img = self.preview.render(mesh, meta, self._dyaw, self._dpitch, c.marks() if c is not None else None,
                                          zoom=self._zoom, pan=self._pan)
        except Exception as exc:                               # noqa: BLE001  (sin EGL: el resto de la ventana sigue andando)
            self.picture.set_paintable(None)
            self.lbl_status.set_text(f"La vista previa 3D no está disponible: {exc}")
            return
        self.lbl_status.set_text("")
        h, w = img.shape[:2]
        self._img_size = (w, h)
        data = GLib.Bytes.new(img.tobytes())
        self.picture.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, data, w * 4))

    def _on_drag_begin(self, _g, x: float, y: float) -> None:
        self._drag0 = (self._dyaw, self._dpitch)
        self._press = (x, y)

    def _on_drag_end(self, _g, dx: float, dy: float) -> None:
        """Un arrastre casi sin movimiento es un clic: con el modo «Cortar la mano…» activo, marca un punto de la muñeca."""
        c = self._cutter
        if c is None or (dx * dx + dy * dy) ** 0.5 > CLICK_MAX_PX or self._shown is None:
            return
        x, y = self._press[0] + dx, self._press[1] + dy
        c.pick_at(x, y, (float(self.picture.get_width()), float(self.picture.get_height())), self._img_size, self._dyaw, self._dpitch,
                    self._zoom, self._pan)
        self._wrist_refresh()

    def _on_drag(self, _g, dx: float, dy: float) -> None:
        y0, p0 = self._drag0
        self._dyaw = y0 - dx * 0.4
        self._dpitch = p0 + dy * 0.4
        self._redraw()

    def _on_pan_begin(self, _g, _x: float, _y: float) -> None:
        self._pan0 = self._pan

    def _on_pan(self, g, dx: float, dy: float) -> None:
        if g.get_current_button() == 1:                        # el izquierdo ya gira
            return
        h = self._image_height_px()
        if h:
            z = self._zoom
            self._pan = (self._pan0[0] - dx / h / z, self._pan0[1] + dy / h / z)
            self._redraw()

    def _image_height_px(self) -> float:
        """Alto (en píxeles del widget) con que se ve la imagen (CONTAIN)."""
        ww, wh = float(self.picture.get_width()), float(self.picture.get_height())
        iw, ih = self._img_size
        return ih * min(ww / iw, wh / ih) if iw and ih and ww and wh else 0.0

    def _on_pinch(self, g, scale: float) -> None:
        ok, x, y = g.get_bounding_box_center()
        if ok:
            self._cursor = (x, y)
        self._zoom_by(self._zoom0 * scale / self._zoom, at_cursor=True)

    def _on_scroll(self, _c, _dx: float, dy: float) -> bool:
        if dy == 0:
            return False
        self._zoom_by(ZOOM_STEP ** (-1 if dy > 0 else 1), at_cursor=True)
        return True

    def _zoom_by(self, factor: float, at_cursor: bool = False) -> None:
        """Acerca / aleja; con la rueda, el punto bajo el cursor se queda donde está."""
        u = v = 0.0
        h = self._image_height_px()
        if at_cursor and h:
            x, y = self._cursor
            u = (x - self.picture.get_width() / 2) / h
            v = (self.picture.get_height() / 2 - y) / h
        self._zoom, self._pan = zoom_about(self._zoom, self._pan, factor, u, v)
        self._redraw()

    def _on_reset_view(self, *_) -> None:
        self._dyaw = self._dpitch = 0.0
        self._zoom, self._pan = 1.0, (0.0, 0.0)
        self._redraw()

    def _on_key(self, _ctl, keyval, _code, _state) -> bool:
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self._on_delete()
            return True
        return False

    # ------------------------------------------------------------ eliminar
    def _on_delete(self, *_) -> None:
        refs = self._selected_refs()
        if not refs or self.ctrl.busy or self._cutter is not None:
            return
        msg, detail = self.mgr.confirm_for(refs)
        dlg = Gtk.AlertDialog(message=msg, detail=detail, buttons=["Cancelar", "Eliminar"], cancel_button=0, default_button=0)

        def answered(d, res) -> None:
            try:
                i = d.choose_finish(res)
            except GLib.Error:
                return
            if i != 1:
                return
            self.mgr.delete(refs)
            self.lbl_status.set_text(self.ctrl.message or "")
            self._reload()

        dlg.choose(self, None, answered)
