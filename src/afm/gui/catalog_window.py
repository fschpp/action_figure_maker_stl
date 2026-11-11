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

from .catalog_manager import (ALL_TYPES, COLUMNS, PIN_LEGEND, CatalogManager, JointEditor, PreviewRenderer, WristCutter,  # noqa: E402
                              can_cut_wrist, can_retune_joint, detail_text, matches, row_cells, sort_pieces)
from .catalog_manager import ZOOM_STEP, zoom_about  # noqa: E402
from .catalog_manager import view_bounds as CM_view_bounds  # noqa: E402
from .window import _no_scroll_change  # noqa: E402

JOINT_DELAY_MS = 250        # espera tras el último cambio de un valor antes de recalcular el hueco (se mueve un spin varias veces seguidas)
CLICK_MAX_PX = 4.0          # un arrastre más corto que esto es un clic (marca un punto); más largo gira la vista


class CatalogManagerWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, ctrl):
        super().__init__(title="Administrar catálogo de piezas", transient_for=parent, default_width=1240, default_height=680)
        self.ctrl = ctrl
        self.mgr = CatalogManager(ctrl)
        self.preview = PreviewRenderer()
        self._rows: list = []                 # [(Gtk.ListBoxRow, PieceMeta)] de lo que muestra la lista
        self._updating = False
        self._signature = None
        self._sort = ("tipo", False)           # (columna, descendente): ordenar por «tipo» agrupa cabezas, torsos, piernas…
        self._head_labels: dict[str, Gtk.Label] = {}
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
        self._jeditor: JointEditor | None = None     # modo «Reajustar articulación…» (None = no está activo)
        self._jupdating = False                      # se están poniendo valores en los spins: no recalcular
        self._jtimer = 0                             # GLib source del recálculo pendiente

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
        paned.set_start_child(self._build_list())
        paned.set_end_child(self._build_detail())
        paned.set_position(640)
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

        box.append(self._build_header())
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
                               "para borrar de una vez, por ejemplo, todo lo catalogado desde un proyecto equivocado. Clic en el título de una columna "
                               "ordena (por «Tipo» se agrupan cabezas, torsos, piernas…); F2 renombra la pieza elegida.",
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        box.append(hint)
        return box

    def _build_header(self) -> Gtk.Widget:
        """Cabecera de la tabla: un clic en una columna ordena por ella (otro clic invierte); «Tipo» agrupa por cabeza, torso, pierna…"""
        head = Gtk.Box(spacing=8, margin_start=8, margin_end=8)
        for key, title, width in COLUMNS:
            lab = Gtk.Label(xalign=0, width_chars=width, max_width_chars=width, ellipsize=Pango.EllipsizeMode.END,
                            tooltip_text=f"Ordenar por «{title}» (otro clic invierte el orden)")
            lab.add_css_class("heading")
            click = Gtk.GestureClick(button=1)
            click.connect("pressed", lambda *_a, k=key: self._on_sort(k))
            lab.add_controller(click)
            self._head_labels[key] = lab
            head.append(lab)
        self._sync_header()
        return head

    def _sync_header(self) -> None:
        key, desc = self._sort
        for k, title, _w in COLUMNS:
            self._head_labels[k].set_text(title + ((" ▼" if desc else " ▲") if k == key else ""))

    def _on_sort(self, key: str) -> None:
        self._sort = (key, not self._sort[1] if self._sort[0] == key else False)
        self._sync_header()
        self._reload()

    def _row_widget(self, meta) -> Gtk.Widget:
        """Una fila de la tabla: una etiqueta por columna; la articulación va en verde si trae pin y atenuada si no."""
        box = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4, margin_start=8, margin_end=8)
        cells = row_cells(meta)
        for key, _title, width in COLUMNS:
            lab = Gtk.Label(label=cells[key], xalign=0, width_chars=width, max_width_chars=width, ellipsize=Pango.EllipsizeMode.END,
                            tooltip_text=cells[key])
            if key == "articulacion":
                lab.add_css_class("success" if cells[key].startswith("●") else "dim-label")
            box.append(lab)
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
        legend = Gtk.Label(label=PIN_LEGEND, xalign=0, wrap=True)
        legend.add_css_class("dim-label")
        box.append(legend)

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
        self.btn_joint = Gtk.Button(label="Reajustar articulación…")
        self.btn_joint.set_sensitive(False)
        self.btn_joint.connect("clicked", self._on_joint_start)
        bar.append(self.btn_joint)
        self.btn_rename = Gtk.Button(label="Renombrar… (F2)")
        self.btn_rename.set_sensitive(False)
        self.btn_rename.set_tooltip_text("Cambia el nombre con que se ve la pieza (todas sus versiones). El id y la referencia no cambian: "
                                         "las recetas y figuras que la usan siguen armando igual.")
        self.btn_rename.connect("clicked", self._on_rename)
        bar.append(self.btn_rename)
        self.btn_delete = Gtk.Button(label="Eliminar…")
        self.btn_delete.add_css_class("destructive-action")
        self.btn_delete.connect("clicked", self._on_delete)
        bar.append(self.btn_delete)
        box.append(bar)
        box.append(self._build_flex_row())
        box.append(self._build_wrist_panel())
        box.append(self._build_joint_panel())

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
        self.btn_wrist.set_sensitive(ok and self._cutter is None and self._jeditor is None)
        self._sync_rename_button(refs)
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

    # ------------------------------------------------------------ reajustar la articulación de una pieza
    def _build_flex_row(self) -> Gtk.Widget:
        """«Flexión mostrada (°)»: visible siempre que la pieza elegida (brazo / pierna) traiga articulación, sin entrar en
        «Reajustar articulación…». Moverla abre la vista de la articulación y dobla la pieza distal (no cambia lo que se guarda)."""
        tip = "Solo recoloca la pieza distal en la vista previa: no cambia lo que se guarda."
        self.flex_row = Gtk.Box(spacing=8, visible=False)
        self.flex_row.append(Gtk.Label(label="Flexión mostrada (°)", xalign=0, hexpand=True, tooltip_text=tip))
        sp = Gtk.SpinButton.new_with_range(-45.0, 150.0, 5.0)
        sp.set_digits(0)
        sp.set_tooltip_text(tip)
        sp.connect("value-changed", lambda b: None if self._jupdating else self._on_flex_value(b.get_value()))
        _no_scroll_change(sp)
        self.spin_flex = sp
        self.flex_row.append(sp)
        return self.flex_row

    def _on_flex_value(self, value: float) -> None:
        """Cambió la flexión mostrada: si todavía no se está en el modo de articulación, se abre (calcula el hueco una vez)."""
        if self._jeditor is None:
            if not self.btn_joint.get_sensitive():
                return
            self._on_joint_start()
            if self._jeditor is None:
                return
        self._on_joint_value("flex", value)
        self._joint_fill()                                  # al abrir el modo los spins se llenaron con 0: vuelve a verse lo pedido

    def _build_joint_panel(self) -> Gtk.Widget:
        """Panel del modo «Reajustar articulación…» (oculto hasta que se activa)."""
        self.joint_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        self.lbl_joint = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.lbl_joint.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.joint_panel.append(self.lbl_joint)
        self.dd_joint = Gtk.DropDown.new_from_strings(["(ninguna)"])
        self.dd_joint.connect("notify::selected", self._on_joint_choice)
        self.dd_joint.set_visible(False)                        # solo si la pieza trae más de una articulación
        self.joint_panel.append(self.dd_joint)
        grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        self._jspin: dict[str, Gtk.SpinButton] = {}
        specs = (("h", "Altura del eje sobre el plano (mm)", 0.5, 15.0, 0.1, 2,
                  "Sube o baja el eje de la bisagra en vertical. Más alto = el hueco se lleva más material del miembro."),
                 ("front", "Inclinación del eje, de frente (°)", -45.0, 45.0, 0.5, 1,
                  "Ángulo del eje visto de frente (0 = a lo largo de X; + = z sube con x)."),
                 ("side", "Inclinación del plano, de costado (°)", -60.0, 60.0, 1.0, 1,
                  "Ángulo del plano visto de costado (+ = sube hacia +Y). 0 = plano recto: el eje no se mueve; lo que se corre es el plano."),
                 ("lo", "Rango: mínimo (°, negativo = hiperextensión)", -45.0, 149.0, 1.0, 0, "Flexión mínima del rango de la articulación."),
                 ("hi", "Rango: máximo (°)", -44.0, 150.0, 1.0, 0, "Flexión máxima del rango de la articulación."))
        for i, (key, label, lo, hi, step, digits, tip) in enumerate(specs):
            grid.attach(Gtk.Label(label=label, xalign=0, hexpand=True, wrap=True, tooltip_text=tip), 0, i, 1, 1)
            sp = Gtk.SpinButton.new_with_range(lo, hi, step)
            sp.set_digits(digits)
            sp.set_tooltip_text(tip)
            sp.connect("value-changed", lambda b, k=key: None if self._jupdating else self._on_joint_value(k, b.get_value()))
            _no_scroll_change(sp)
            self._jspin[key] = sp
            grid.attach(sp, 1, i, 1, 1)
        self.joint_panel.append(grid)
        row = Gtk.Box(spacing=6)
        self.btn_joint_pick = Gtk.Button(label="Mover el eje con un clic")
        self.btn_joint_pick.set_tooltip_text("Muestra la pieza entera: un clic sobre su costado pone el eje ahí (se toman la altura y la profundidad del clic).")
        self.btn_joint_pick.connect("clicked", self._on_joint_pick)
        self.btn_joint_straight = Gtk.Button(label="Enderezar el plano")
        self.btn_joint_straight.set_tooltip_text("Inclinación de costado 0°: el eje no se mueve y conserva su altura; se corre el plano.")
        self.btn_joint_straight.connect("clicked", lambda *_: self._on_joint_value("side", 0.0))
        self.btn_joint_reset = Gtk.Button(label="Restablecer")
        self.btn_joint_reset.set_tooltip_text("Vuelve a la articulación de la versión guardada.")
        self.btn_joint_reset.connect("clicked", self._on_joint_reset)
        for b in (self.btn_joint_pick, self.btn_joint_straight, self.btn_joint_reset):
            row.append(b)
        self.joint_panel.append(row)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Se guarda como versión nueva (borrador); la actual no se toca.", xalign=0, hexpand=True, wrap=True))
        self.btn_joint_cancel = Gtk.Button(label="Cancelar (Esc)")
        self.btn_joint_cancel.connect("clicked", lambda *_: self._joint_cancel())
        self.btn_joint_save = Gtk.Button(label="Guardar versión nueva")
        self.btn_joint_save.add_css_class("suggested-action")
        self.btn_joint_save.connect("clicked", self._on_joint_save)
        row.append(self.btn_joint_cancel)
        row.append(self.btn_joint_save)
        self.joint_panel.append(row)
        return self.joint_panel

    def _sync_joint_button(self, refs: list[str]) -> None:
        """«Reajustar articulación…» se puede usar con UN brazo / pierna elegido que trae articulación."""
        ok, why = False, "Elegí un brazo o una pierna de la lista."
        if len(refs) == 1:
            meta = next((m for _row, m in self._rows if m.ref == refs[0]), None)
            ok, why = can_retune_joint(meta)
        elif len(refs) > 1:
            why = "Elegí una sola pieza."
        if ok and self.ctrl.busy:
            ok, why = False, "Esperá a que termine la tarea en curso."
        if ok and self._shown is None:
            ok, why = False, "No hay vista previa de la pieza."
        self.btn_joint.set_sensitive(ok and self._jeditor is None and self._cutter is None)
        self.flex_row.set_visible(ok or self._jeditor is not None)          # con articulación: se ve aunque no se esté reajustando
        self.flex_row.set_sensitive(self._cutter is None and (self._jeditor is not None or (ok and not self.ctrl.busy)))
        self.btn_joint.set_tooltip_text("Mové el eje de la bisagra, inclinás el plano o cambiá el rango viendo cuánto material se lleva el hueco; "
                                        "se guarda como una versión nueva de la pieza (la actual no se toca)." if ok else why)

    def edit_joint(self, ref: str) -> None:
        """Elige la pieza `ref` (`id@n`, o `id` = la última versión) y abre «Reajustar articulación…» (lo llama la ventana principal)."""
        self._reload()
        pid, _, ver = ref.partition("@")
        found = [(row, m) for row, m in self._rows if m.id == pid and (not ver or m.version == int(ver))]
        if not found:
            self.lbl_status.set_text(f"«{ref}» no está en la lista (¿hay un filtro puesto?).")
            return
        row, _m = max(found, key=lambda t: t[1].version)
        self.listbox.unselect_all()
        self.listbox.select_row(row)
        self._on_joint_start()

    def _on_joint_start(self, *_) -> None:
        if self._shown is None or self._jeditor is not None or self._cutter is not None:
            return
        meta, mesh = self._shown
        ok, why = can_retune_joint(meta)
        if not ok:
            self.lbl_status.set_text(why)
            return
        ed = self._jeditor = JointEditor(self.ctrl, meta, mesh)
        self._dyaw = self._dpitch = 0.0
        self._zoom, self._pan = 1.0, (0.0, 0.0)
        model = Gtk.StringList.new(ed.names)
        self._jupdating = True
        try:
            self.dd_joint.set_model(model)
            self.dd_joint.set_selected(0)
        finally:
            self._jupdating = False
        self.dd_joint.set_visible(len(ed.names) > 1)
        self.joint_panel.set_visible(True)
        self.btn_joint.set_sensitive(False)
        self.btn_wrist.set_sensitive(False)
        self.btn_delete.set_sensitive(False)
        self._joint_fill()
        self._joint_schedule(0)

    def _joint_cancel(self, redraw: bool = True) -> None:
        if self._jeditor is None:
            return
        if self._jtimer:
            GLib.source_remove(self._jtimer)
            self._jtimer = 0
        self._jeditor = None
        self.joint_panel.set_visible(False)
        self._jupdating = True
        try:
            self.spin_flex.set_value(0.0)
        finally:
            self._jupdating = False
        self._sync_joint_button(self._selected_refs())
        self._sync_wrist_button(self._selected_refs())
        self.btn_delete.set_sensitive(bool(self._selected_refs()) and not self.ctrl.busy)
        if redraw:
            self._redraw()

    def _joint_fill(self) -> None:
        """Pone en los spins los valores de la articulación que se edita (sin disparar recálculos)."""
        ed = self._jeditor
        if ed is None:
            return
        lo, hi = ed.limits()
        self._jupdating = True
        try:
            for key, val in (("h", ed.height()), ("front", ed.front_tilt()), ("side", ed.side_tilt()), ("lo", lo), ("hi", hi)):
                self._jspin[key].set_value(val)
            self.spin_flex.set_value(ed.flex)
        finally:
            self._jupdating = False

    def _on_joint_choice(self, *_) -> None:
        ed = self._jeditor
        if ed is None or self._jupdating:
            return
        i = self.dd_joint.get_selected()
        if 0 <= i < len(ed.names):
            ed.select(ed.names[i])
            self._joint_fill()
            self._joint_schedule(0)

    def _on_joint_value(self, key: str, value: float) -> None:
        ed = self._jeditor
        if ed is None:
            return
        if key == "flex":                                      # solo recoloca el distal: no recalcula el hueco
            ed.set_flex(value)
            self._joint_refresh()
            return
        if key == "h":
            ed.set_height(value)
        elif key == "front":
            ed.set_front_tilt(value)
        elif key == "side":
            ed.set_side_tilt(value)
        else:                                                  # "lo" / "hi": los dos van juntos
            ed.set_range(self._jspin["lo"].get_value(), self._jspin["hi"].get_value())
        if ed.error:                                           # no sirvió: los spins vuelven a lo que vale
            self._joint_fill()
        elif key == "side":
            self._joint_fill()                                 # enderezar / inclinar el plano no cambia la altura, pero la redondea
        self._joint_refresh()
        self._joint_schedule()

    def _on_joint_reset(self, *_) -> None:
        if self._jeditor is not None:
            self._jeditor.reset()
            self._joint_fill()
            self._joint_refresh()
            self._joint_schedule(0)

    def _on_joint_pick(self, *_) -> None:
        ed = self._jeditor
        if ed is not None:
            self.lbl_joint.set_text(ed.start_pick())
            self._zoom, self._pan = 1.0, (0.0, 0.0)
            self._redraw()

    def _joint_refresh(self) -> None:
        """Texto, botones y dibujo según el estado del editor."""
        ed = self._jeditor
        if ed is None:
            return
        self.lbl_joint.set_text(ed.message())
        self.btn_joint_save.set_sensitive(ed.changed and not ed.busy and not ed.error)
        self.btn_joint_reset.set_sensitive(ed.changed)
        self.btn_joint_pick.set_sensitive(not ed.pick_axis)
        self._redraw()

    def _joint_schedule(self, delay_ms: int = JOINT_DELAY_MS) -> None:
        """Pide recalcular el hueco dentro de `delay_ms` (cada cambio nuevo reinicia la espera)."""
        if self._jtimer:
            GLib.source_remove(self._jtimer)
        self._jtimer = GLib.timeout_add(max(int(delay_ms), 1), self._joint_run)

    def _joint_run(self) -> bool:
        self._jtimer = 0
        ed = self._jeditor
        if ed is None or not ed.needs_compute():               # (si sigue calculando el anterior, `_joint_done` vuelve a pedirlo)
            return False
        try:
            key, run = ed.job()
        except ValueError as exc:                              # la pieza no se puede preparar (sin puerto, etc.)
            ed.error = str(exc)
            ed.busy = False
            ed._pv_key = ed.key()
            self._joint_refresh()
            return False
        self._joint_refresh()                                  # «Calculando…»

        def work() -> None:
            try:
                res, err = run(), None
            except Exception as exc:                           # noqa: BLE001  (cualquier falla del motor va al texto, no tira la ventana)
                res, err = None, exc
            GLib.idle_add(self._joint_done, key, res, err)

        import threading
        threading.Thread(target=work, daemon=True, name="afm-joint-retune").start()
        return False

    def _joint_done(self, key: str, pv, err) -> bool:
        ed = self._jeditor
        if ed is None:
            return False
        first = ed.pv is None
        ed.done(key, pv, err)
        if first and ed.pv is not None and not ed.pick_axis:
            self._zoom, self._pan = 1.0, (0.0, 0.0)            # el encuadre pasa de la pieza entera a la articulación
        if ed.needs_compute():                                 # cambió algo mientras se calculaba
            self._joint_schedule()
        self._joint_refresh()
        return False

    def _on_joint_save(self, *_) -> None:
        ed = self._jeditor
        if ed is None or self.ctrl.busy or ed.busy:
            return
        meta = ed.save()
        if meta is None:                                       # el motivo está en el mensaje del controlador
            self.lbl_joint.set_text(self.ctrl.message or "No se pudo guardar.")
            return
        self._joint_cancel(redraw=False)
        self._reload()
        for row, m in self._rows:                              # deja elegida SOLO la versión nueva para verla
            if m.ref == meta.ref:
                self.listbox.unselect_all()
                self.listbox.select_row(row)
                break
        self.lbl_status.set_text(self.ctrl.message or "")

    def _on_window_key(self, _ctl, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape and self._jeditor is not None:
            if self._jeditor.pick_axis:
                self._jeditor.cancel_pick()
                self._joint_refresh()
            else:
                self._joint_cancel()
            return True
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
        metas = sort_pieces([m for m in everything if matches(m, text, tipo)], *self._sort)
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
                row.set_child(self._row_widget(m))
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
        if self._jeditor is not None and refs != [self._jeditor.meta.ref]:
            self._joint_cancel(redraw=False)
        self.btn_delete.set_label("Eliminar…" if n == 0 else f"Eliminar {n} pieza(s)…")
        self.btn_delete.set_sensitive(bool(n) and not self.ctrl.busy and self._cutter is None and self._jeditor is None)
        self._sync_rename_button(refs)
        if n == 0:
            self.lbl_detail.set_text("Elegí una pieza de la lista para verla." if self._rows else "El catálogo no tiene piezas (todavía).")
            self._show(None)
            self._sync_wrist_button(refs)
            self._sync_joint_button(refs)
            return
        meta = next(m for _row, m in self._rows if m.ref == refs[0])
        text = detail_text(meta, self.mgr.uses(meta.ref))
        if n > 1:
            text = f"{n} piezas elegidas (se muestra la primera).\n\n" + text
        self.lbl_detail.set_text(text)
        if self._shown is None or self._shown[0].ref != meta.ref:      # agregar una a la selección no redibuja la misma
            self._show(meta)
        self._sync_wrist_button(refs)
        self._sync_joint_button(refs)

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
        ed = self._jeditor
        try:
            if ed is not None:                                           # reajustando la articulación: el hueco, o la pieza entera para tocar el eje
                parts, bounds, marks = ed.view()
                img = self.preview.render_parts(parts, self._dyaw, self._dpitch, marks, bounds=bounds, zoom=self._zoom, pan=self._pan)
            elif c is not None and c.preview_parts() is not None:          # corte calculado: brazo sin mano en gris y la mano en verde
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
        ed = self._jeditor
        if ed is not None and ed.pick_axis and (dx * dx + dy * dy) ** 0.5 <= CLICK_MAX_PX and self._shown is not None:
            x, y = self._press[0] + dx, self._press[1] + dy
            ed.pick_at(x, y, (float(self.picture.get_width()), float(self.picture.get_height())), self._img_size, self._dyaw, self._dpitch,
                       self._zoom, self._pan)
            if not ed.pick_axis and not ed.error:               # el eje se movió: se recalcula y se vuelve a ver el hueco
                self._zoom, self._pan = 1.0, (0.0, 0.0)
                self._joint_fill()
                self._joint_schedule(0)
            self._joint_refresh()
            return
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
        if keyval == Gdk.KEY_F2:
            self._on_rename()
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self._on_delete()
            return True
        return False

    # ------------------------------------------------------------ renombrar
    def _sync_rename_button(self, refs: list[str]) -> None:
        ok = len(refs) == 1 and not self.ctrl.busy and self._cutter is None and self._jeditor is None
        self.btn_rename.set_sensitive(ok)

    def _on_rename(self, *_) -> None:
        refs = self._selected_refs()
        if len(refs) != 1 or self.ctrl.busy or self._cutter is not None or self._jeditor is not None:
            return
        meta = next(m for _row, m in self._rows if m.ref == refs[0])
        dlg = Gtk.Window(title="Renombrar pieza", transient_for=self, modal=True, default_width=440, resizable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(Gtk.Label(label=f"Nombre para «{meta.id}» (se aplica a todas sus versiones). Vacío = volver a mostrar el id.",
                             xalign=0, wrap=True))
        entry = Gtk.Entry(text=meta.nombre, placeholder_text=meta.id, max_length=60, activates_default=True)
        box.append(entry)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancelar")
        cancel.connect("clicked", lambda *_: dlg.close())
        ok = Gtk.Button(label="Renombrar")
        ok.add_css_class("suggested-action")

        def accept(*_) -> None:
            if self.ctrl.catalog_rename(meta.ref, entry.get_text()):
                dlg.close()
                self._reload()
                self.lbl_status.set_text(self.ctrl.message or "")
            else:
                self.lbl_status.set_text(self.ctrl.message or "")
                dlg.close()

        ok.connect("clicked", accept)
        entry.connect("activate", accept)
        row.append(cancel)
        row.append(ok)
        box.append(row)
        dlg.set_child(box)
        dlg.set_default_widget(ok)
        dlg.present()
        entry.grab_focus()

    # ------------------------------------------------------------ eliminar
    def _on_delete(self, *_) -> None:
        refs = self._selected_refs()
        if not refs or self.ctrl.busy or self._cutter is not None or self._jeditor is not None:
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
