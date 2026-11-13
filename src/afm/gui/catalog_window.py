"""Ventana «Administrar catálogo»: lista de piezas con vista previa 3/4 y eliminación de una o varias.

La lógica (filtro, textos, cámara, borrado) está en `catalog_manager.py` y se prueba sin GTK; acá solo está la ventana.
La vista previa se dibuja fuera de pantalla (`PreviewRenderer`, contexto EGL propio) y se muestra como imagen: así no
compite con el contexto OpenGL del visor de la ventana principal. Se abre desde la pestaña «Catálogo» de la ventana principal."""
from __future__ import annotations

import glob
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import catalog_ops as OPS  # noqa: E402
from .catalog_manager import (ALL_STATES, ALL_TAGS, ALL_TYPES, COLUMNS, PIN_LEGEND, CatalogManager, JointEditor, PreviewRenderer,  # noqa: E402
                              WristCutter, can_cut_wrist, can_retune_joint, detail_text, matches, next_row, row_cells, sort_pieces)
from . import catalog_import as CI  # noqa: E402
from .catalog_manager import ZOOM_STEP, zoom_about  # noqa: E402
from .catalog_manager import view_bounds as CM_view_bounds  # noqa: E402
from .progress_panel import ProgressPanel  # noqa: E402
from ..parts import STATES  # noqa: E402
from .window import _no_scroll_change  # noqa: E402

JOINT_DELAY_MS = 250        # espera tras el último cambio de un valor antes de recalcular el hueco (se mueve un spin varias veces seguidas)
CLICK_MAX_PX = 4.0          # un arrastre más corto que esto es un clic (marca un punto); más largo gira la vista


class CatalogManagerWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, ctrl):
        super().__init__(title="Administrar catálogo de piezas", transient_for=parent, default_width=1240, default_height=780)
        self.ctrl = ctrl
        self.mgr = CatalogManager(ctrl)
        self.preview = PreviewRenderer()
        self._rows: list = []                 # [(Gtk.ListBoxRow, PieceMeta)] de lo que muestra la lista
        self._updating = False
        self._signature = None
        self._kb_index: int | None = None      # fila de la última selección con el teclado o el ratón (de ahí siguen las flechas)
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
        self._pending_select = ""                    # pieza recién importada que hay que dejar elegida
        self._task_label = ""                        # tarea larga de catálogo en curso (certificar, compatibilidad…); "" = ninguna

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
        paned.set_start_child(self._build_list())
        paned.set_end_child(self._build_detail())
        paned.set_position(640)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(True)           # ninguno de los dos lados fija el tamaño mínimo de la ventana: se puede achicar y maximizar
        paned.set_shrink_end_child(True)
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
        row.append(self._build_catalog_menu())
        row.append(self._build_import_menu())
        box.append(row)
        row2 = Gtk.Box(spacing=6)
        self.dd_fstate = Gtk.DropDown.new_from_strings([ALL_STATES])
        self.dd_fstate.set_hexpand(True)
        self.dd_tag = Gtk.DropDown.new_from_strings([ALL_TAGS])
        self.dd_tag.set_hexpand(True)
        for dd in (self.dd_fstate, self.dd_tag):
            dd.connect("notify::selected", lambda *_: None if self._updating else self._reload())
            row2.append(dd)
        self.chk_latest = Gtk.CheckButton(label="solo la última versión")
        self.chk_latest.set_tooltip_text("Como `afm catalogo listar`: una fila por pieza (su última versión). Sin tildar se ven todas las versiones.")
        self.chk_latest.connect("toggled", lambda *_: None if self._updating else self._reload())
        row2.append(self.chk_latest)
        box.append(row2)

        head_sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL, vscrollbar_policy=Gtk.PolicyType.NEVER)
        head_sw.set_child(self._build_header())        # EXTERNAL: no pide su ancho completo como mínimo (si no, la ventana no se podría achicar)
        box.append(head_sw)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("selected-rows-changed", self._on_selection)
        # Clic simple = elegir SOLO esa pieza; Mayús o Ctrl + clic = agregarla / quitarla de lo elegido (GTK acumularía con cada clic)
        click = Gtk.GestureClick(button=1)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_list_click)
        self.listbox.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)   # antes que GTK: en una lista de selección múltiple sus flechas solo mueven el foco
        keys.connect("key-pressed", self._on_key)
        self.listbox.add_controller(keys)
        search_keys = Gtk.EventControllerKey()                     # desde el buscador, ↓ / ↑ ya mueven la selección de la lista
        search_keys.connect("key-pressed", lambda _c, kv, _kc, st: self._on_arrow(kv, st, focus=False) if kv in (Gdk.KEY_Up, Gdk.KEY_Down) else False)
        self.search.add_controller(search_keys)
        sw = Gtk.ScrolledWindow(vexpand=True, hexpand=True, min_content_width=240)
        sw.set_child(self.listbox)
        self.listbox.set_adjustment(sw.get_vadjustment())          # enfocar una fila la trae a la vista (flechas, Inicio / Fin)
        sw.get_hadjustment().connect("value-changed", lambda a: head_sw.get_hadjustment().set_value(a.get_value()))   # la cabecera acompaña
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

    def _row_widget(self, meta, cert: str = "") -> Gtk.Widget:
        """Una fila de la tabla: una etiqueta por columna; la articulación va en verde si trae pin y atenuada si no
        (la certificación, en verde si está certificada y en naranja si venció o quedó parcial)."""
        box = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4, margin_start=8, margin_end=8)
        cells = row_cells(meta, cert)
        for key, _title, width in COLUMNS:
            lab = Gtk.Label(label=cells[key], xalign=0, width_chars=width, max_width_chars=width, ellipsize=Pango.EllipsizeMode.END,
                            tooltip_text=cells[key])
            if key == "articulacion":
                lab.add_css_class("success" if cells[key].startswith("●") else "dim-label")
            elif key == "certificacion" and cert:
                lab.add_css_class("success" if cert.startswith("certificada") and "falla" not in cert else "warning")
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

        bar = Gtk.Box(spacing=6)                                   # (los botones se recortan si la ventana es muy angosta; el texto largo se acorta)
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
        bar.append(Gtk.Label(label="Arrastrá para girar · rueda o pellizco = zoom · botón derecho = mover · naranja = puertos", xalign=0, hexpand=True,
                             ellipsize=Pango.EllipsizeMode.END, width_chars=8))
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
        self.btn_undo = Gtk.Button(label="Recuperar lo eliminado")
        self.btn_undo.set_sensitive(False)
        self.btn_undo.connect("clicked", self._on_undo_delete)
        bar.append(self.btn_undo)
        box.append(bar)
        box.append(self._build_state_row())
        box.append(self._build_ops_row())
        box.append(self._build_flex_row())
        box.append(self._build_wrist_panel())
        box.append(self._build_joint_panel())
        box.append(self._build_task_row())
        box.append(self._build_report())

        self.lbl_detail = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True)
        self.lbl_detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        sw = Gtk.ScrolledWindow(min_content_height=130, vexpand=False)
        sw.set_child(self.lbl_detail)
        box.append(sw)
        self.lbl_status = Gtk.Label(xalign=0, wrap=True)
        self.lbl_status.add_css_class("dim-label")
        box.append(self.lbl_status)
        return box

    # ------------------------------------------------------------ estado de las piezas elegidas
    def _build_state_row(self) -> Gtk.Widget:
        """«Estado: [borrador ▾] [Aplicar a las elegidas]»: cambia el estado de una o varias versiones a la vez."""
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Label(label="Estado:", xalign=0))
        self.dd_state = Gtk.DropDown.new_from_strings(list(STATES))
        self.dd_state.set_tooltip_text("borrador → validada → impresa. Es lo único que se puede cambiar de una versión sin crear otra.")
        row.append(self.dd_state)
        self.btn_state = Gtk.Button(label="Aplicar a las elegidas")
        self.btn_state.set_sensitive(False)
        self.btn_state.connect("clicked", self._on_apply_state)
        row.append(self.btn_state)
        return row

    def _sync_state_row(self, refs: list[str]) -> None:
        ok = bool(refs) and not self.ctrl.busy and self._cutter is None and self._jeditor is None
        self.btn_state.set_sensitive(ok)
        self.btn_state.set_label("Aplicar a las elegidas" if len(refs) <= 1 else f"Aplicar a {len(refs)} piezas")
        if refs:                                                   # muestra el estado actual de la primera elegida
            meta = next((m for _row, m in self._rows if m.ref == refs[0]), None)
            if meta is not None and meta.estado in STATES:
                self.dd_state.set_selected(STATES.index(meta.estado))

    def _on_apply_state(self, *_) -> None:
        refs = self._selected_refs()
        if not refs or self.ctrl.busy or self._cutter is not None or self._jeditor is not None:
            return
        state = STATES[self.dd_state.get_selected()]
        if self.mgr.set_state(refs, state):
            self._reload()
        self.lbl_status.set_text(self.ctrl.message or "")

    def _sync_undo_button(self) -> None:
        n = self.ctrl.catalog_trash_count()
        self.btn_undo.set_sensitive(n > 0 and not self.ctrl.busy and self._cutter is None and self._jeditor is None)
        self.btn_undo.set_label(f"Recuperar lo eliminado ({n})" if n else "Recuperar lo eliminado")
        self.btn_undo.set_tooltip_text("Devuelve al catálogo el último grupo de piezas eliminadas (están en la carpeta .papelera)." if n
                                       else "No hay nada eliminado para recuperar.")

    def _on_undo_delete(self, *_) -> None:
        if self.ctrl.busy or self._cutter is not None or self._jeditor is not None:
            return
        self.mgr.undo_delete()
        self._reload()
        self.lbl_status.set_text(self.ctrl.message or "")

    # ------------------------------------------------------------ comandos de `afm catalogo` (catalog_ops)
    def _build_catalog_menu(self) -> Gtk.Widget:
        """Menú «Catálogo ▾»: los comandos que valen para todo el catálogo (verificar, contrato, certificar todo, compatibilidad, matriz)."""
        self.menu_cat = Gtk.MenuButton(label="Catálogo ▾")
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self._menu_buttons: list[Gtk.Button] = []
        items = [
            ("Verificar el catálogo", self._op_verify, "afm catalogo verificar: formato, hash, mallas cerradas, puertos, caras y altura de cada pieza."),
            ("Ver el contrato de la línea", self._op_contract, "afm catalogo contrato --ver: talles de cara y posición por clase de puerto, y el talle de cada pieza."),
            None,
            ("Estado de la certificación (todas)", lambda: self._op_certify(None, status_only=True), "afm catalogo certificar --estado: no arma nada."),
            ("Certificar todas las piezas", lambda: self._op_certify(None), "afm catalogo certificar: arma de verdad cada pieza en su panel de torsos. Reutiliza lo ya armado si no cambió nada; tarda minutos por anfitrión."),
            ("Certificar todas de nuevo (forzar)", lambda: self._op_certify(None, force=True), "afm catalogo certificar --forzar: vuelve a armar aunque compat.json ya tenga el resultado."),
            None,
            ("Compatibilidad de las recetas", lambda: self._op_compat(False), "afm catalogo compat: build + validate de las recetas de figuras/ y recetas/ (con caché)."),
            ("Compatibilidad de las recetas (forzar)", lambda: self._op_compat(True), "afm catalogo compat --forzar."),
            ("Escribir COMPATIBILIDAD.md", lambda: self._op_matrix(False), "afm catalogo matriz: matriz barata del catálogo y los resultados completos guardados."),
            ("Escribir COMPATIBILIDAD.md validando antes las recetas", lambda: self._op_matrix(True), "afm catalogo matriz --completa."),
            None,
            ("Medir la capacidad de las tomas (todas)", lambda: self._op_capacity(None), "afm catalogo capacidad: hasta dónde llega el hueco de cada toma con pasador. No guarda nada."),
            ("Medir y guardar la capacidad…", lambda: self._op_capacity(None, write=True), "afm catalogo capacidad --escribir: además escribe la medida en los .pieza.json (pide confirmación)."),
            None,
            ("Calibrar el contrato (propuesta)", self._op_contract_propose, "afm catalogo contrato --calibrar (tolerancia 0,4 mm, radio equivalente; otra tolerancia o métrica, en consola). No guarda nada."),
            ("Guardar la propuesta del contrato…", self._op_contract_save, "afm catalogo contrato --calibrar --guardar: escribe la última propuesta en linea.json (pide confirmación). Solo si no tiene problemas."),
            ("Borrar el contrato de la línea…", self._op_contract_clear, "afm catalogo contrato --quitar (pide confirmación)."),
        ]
        for it in items:
            if it is None:
                box.append(Gtk.Separator())
                continue
            label, cb, tip = it
            b = Gtk.Button(label=label, has_frame=False, halign=Gtk.Align.START, tooltip_text=tip)
            b.connect("clicked", lambda _b, cb=cb: (pop.popdown(), cb()))
            box.append(b)
            self._menu_buttons.append(b)
            if label.startswith("Guardar la propuesta"):
                self._btn_save_proposal = b
        pop.set_child(box)
        self.menu_cat.set_popover(pop)
        return self.menu_cat

    def _build_import_menu(self) -> Gtk.Widget:
        """Menú «Importar ▾»: agregar al catálogo una mano, un accesorio, un arma o una pieza suelta desde un STL."""
        self.menu_import = Gtk.MenuButton(label="Importar ▾")
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self._import_buttons: list[Gtk.Button] = []
        for kind, label in CI.LABELS.items():
            b = Gtk.Button(label=label, has_frame=False, halign=Gtk.Align.START,
                           tooltip_text=f"afm catalogo {CI.FORMS[kind]['comando']}: {CI.FORMS[kind]['ayuda']}")
            b.connect("clicked", lambda _b, k=kind: (pop.popdown(), self._open_import(k)))
            box.append(b)
            self._import_buttons.append(b)
        pop.set_child(box)
        self.menu_import.set_popover(pop)
        return self.menu_import

    def _open_import(self, kind: str) -> None:
        if self._ops_free():
            ImportDialog(self, kind, self._start_import).present()

    def _start_import(self, kind: str, values: dict) -> str:
        """Lanza la importación del formulario. Devuelve "" si empezó, o el motivo por el que el formulario no sirve (el diálogo lo muestra)."""
        try:
            job = self.mgr.import_job(kind, values)
        except ValueError as exc:
            return str(exc)
        if job is None:
            return self.ctrl.message or "No se pudo empezar la importación."
        self._report_set(f"Importar: {CI.FORMS[kind]['titulo']}", [])
        self._run_ops_task(f"Importando · {CI.FORMS[kind]['titulo']}", job, self._after_import, cancellable=False)
        return ""

    def _after_import(self, res, err) -> None:
        if err is not None:
            self._report_append(f"ERROR: {err}")
            return
        for line in CI.result_lines(res):
            self._report_append(line)
        self._pending_select = res.ref

    def _select_ref(self, ref: str) -> None:
        for row, m in self._rows:
            if m.ref == ref:
                self.listbox.unselect_all()
                self.listbox.select_row(row)
                return

    def _build_ops_row(self) -> Gtk.Widget:
        """Comandos de la pieza elegida: certificar, cobertura, explicar."""
        row = Gtk.Box(spacing=6)
        self.btn_certify = Gtk.Button(label="Certificar")
        self.btn_certify.connect("clicked", lambda *_: self._op_certify(self._selected_refs()))
        self.chk_force = Gtk.CheckButton(label="forzar")
        self.chk_force.set_tooltip_text("Volver a armar aunque el resultado guardado siga valiendo (certificar --forzar).")
        self.btn_cover = Gtk.Button(label="Cobertura")
        self.btn_cover.connect("clicked", lambda *_: self._op_piece_report("cobertura"))
        self.btn_explain = Gtk.Button(label="Explicar acoples")
        self.btn_explain.connect("clicked", lambda *_: self._op_piece_report("explicar"))
        self.btn_capacity = Gtk.Button(label="Capacidad")
        self.btn_capacity.connect("clicked", lambda *_: self._op_capacity(self._selected_refs()))
        for w, tip in ((self.btn_certify, "afm catalogo certificar ID: arma la pieza elegida en un panel de torsos del catálogo (la propia figura y los de menor, mediana y mayor capacidad)."),
                       (self.btn_cover, "afm catalogo cobertura ID: cuántas piezas del catálogo aceptan cada puerto de esta pieza (por metadatos, instantáneo)."),
                       (self.btn_capacity, "afm catalogo capacidad ID: hasta dónde llega el hueco de las tomas con pasador de la pieza (no guarda; para guardar, Catálogo ▾ → Medir y guardar la capacidad…)."),
                       (self.btn_explain, "afm catalogo explicar ID: por qué se acopla (o no) con cada otra pieza: radios, IoU, escalón y motivos.")):
            w.set_tooltip_text(tip)
            row.append(w)
            if w is self.btn_certify:
                row.append(self.chk_force)
        return row

    def _build_task_row(self) -> Gtk.Widget:
        """Progreso de una tarea larga de catálogo (oculto hasta que corre una) y su botón para cancelarla."""
        self.task_row = Gtk.Box(spacing=6, visible=False)
        self.progress = ProgressPanel()
        self.progress.set_hexpand(True)
        self.task_row.append(self.progress)
        self.btn_task_cancel = Gtk.Button(label="Cancelar", valign=Gtk.Align.CENTER)
        self.btn_task_cancel.set_tooltip_text("Corta en cuanto el motor informa progreso. Lo ya certificado queda guardado.")
        self.btn_task_cancel.connect("clicked", self._on_task_cancel)
        self.task_row.append(self.btn_task_cancel)
        return self.task_row

    def _build_report(self) -> Gtk.Widget:
        """Panel «Informe»: el texto de cualquier comando (lo mismo que imprimiría la consola), con copiar y guardar."""
        self.report_exp = Gtk.Expander(label="Informe")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.report_buf = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self.report_buf, editable=False, cursor_visible=False, monospace=True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        sw = Gtk.ScrolledWindow(min_content_height=140, vexpand=False)
        sw.set_child(view)
        box.append(sw)
        bar = Gtk.Box(spacing=6)
        for label, cb in (("Copiar", self._on_report_copy), ("Guardar…", self._on_report_save), ("Limpiar", lambda *_: self.report_buf.set_text(""))):
            b = Gtk.Button(label=label)
            b.connect("clicked", cb)
            bar.append(b)
        box.append(bar)
        self.report_exp.set_child(box)
        return self.report_exp

    def _report_set(self, title: str, lines: list[str]) -> None:
        self.report_buf.set_text(f"== {title} ==\n" + "\n".join(lines) + "\n")
        self.report_exp.set_expanded(True)

    def _report_append(self, line: str) -> bool:
        self.report_buf.insert(self.report_buf.get_end_iter(), line + "\n")
        self.report_exp.set_expanded(True)
        return False

    def _report_text(self) -> str:
        return self.report_buf.get_text(self.report_buf.get_start_iter(), self.report_buf.get_end_iter(), False)

    def _on_report_copy(self, *_) -> None:
        self.get_clipboard().set(self._report_text())

    def _on_report_save(self, *_) -> None:
        dlg = Gtk.FileDialog(title="Guardar el informe", initial_name="informe_catalogo.txt")

        def finished(d, res) -> None:
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                with open(f.get_path(), "w", encoding="utf-8") as fh:
                    fh.write(self._report_text())
                self.lbl_status.set_text(f"Informe guardado en {f.get_path()}")
            except OSError as exc:
                self.lbl_status.set_text(f"No se pudo guardar el informe: {exc}")

        dlg.save(self, None, finished)

    def _ops_free(self) -> bool:
        """¿Se pueden lanzar comandos de catálogo? (hay catálogo, ninguna tarea en curso, ningún modo de edición abierto)."""
        return bool(self.ctrl.session.catalog_root) and not self.ctrl.busy and self._cutter is None and self._jeditor is None

    def _sync_ops_buttons(self, refs: list[str] | None = None) -> None:
        refs = self._selected_refs() if refs is None else refs
        free = self._ops_free()
        for b in self._menu_buttons:
            b.set_sensitive(free)
        self.menu_cat.set_sensitive(bool(self.ctrl.session.catalog_root))
        for b in self._import_buttons:
            b.set_sensitive(free)
        self.menu_import.set_sensitive(bool(self.ctrl.session.catalog_root))
        self.btn_certify.set_sensitive(free and bool(self.mgr.certify_refs(refs)))
        self.chk_force.set_sensitive(free)
        self.btn_capacity.set_sensitive(free and bool(self.mgr.capacity_refs(refs)))
        self._btn_save_proposal.set_sensitive(free and self.mgr.can_save_proposal())
        self.btn_cover.set_sensitive(free and len(refs) == 1)
        self.btn_explain.set_sensitive(free and len(refs) == 1)
        self.btn_certify.set_label("Certificar" if len(refs) <= 1 else f"Certificar {len(self.mgr.certify_refs(refs))}")

    def _op_error(self, title: str, exc: Exception) -> None:
        self._report_set(title, [f"ERROR: {exc}"])
        self.lbl_status.set_text(f"{title}: {exc}")

    def _op_verify(self) -> None:
        try:
            lines, ok = self.mgr.report_verify()
        except ValueError as exc:
            self._op_error("Verificar", exc)
            return
        self._report_set("Verificar el catálogo", lines)
        self.lbl_status.set_text(lines[-1] if lines else "")

    def _op_contract(self) -> None:
        try:
            lines = self.mgr.report_contract()
        except ValueError as exc:
            self._op_error("Contrato", exc)
            return
        self._report_set("Contrato de la línea", lines)

    def _op_piece_report(self, which: str) -> None:
        refs = self._selected_refs()
        if len(refs) != 1:
            return
        try:
            lines = self.mgr.report_coverage(refs[0]) if which == "cobertura" else self.mgr.report_explain(refs[0])
        except (ValueError, KeyError) as exc:
            self._op_error(which.capitalize(), exc)
            return
        self._report_set(f"{which.capitalize()} de {refs[0]}", lines or ["(sin datos)"])

    def _op_certify(self, refs: list[str] | None, force: bool = False, status_only: bool = False) -> None:
        if not self._ops_free():
            return
        force = force or self.chk_force.get_active()
        job = self.mgr.certify_job(refs, force, status_only, emit=lambda line: GLib.idle_add(self._report_append, line))
        if job is None:
            self.lbl_status.set_text(self.ctrl.message or "")
            return
        todo = self.mgr.certify_refs(refs)
        self._report_set(("Estado de la certificación" if status_only else "Certificar" + (" (forzado)" if force else "")) + f" · {len(todo)} pieza(s)", [])
        label = "Revisando la certificación" if status_only else "Certificando"
        self._run_ops_task(label, job, lambda res, err: self._after_certify(res, err))

    def _after_certify(self, res, err) -> None:
        if err is not None and not isinstance(err, OPS.Cancelled):
            self._report_append(f"ERROR: {err}")
        elif res is not None:
            self._report_append(("Cancelado. " if res.cancelled else "") + f"{res.done} pieza(s) procesada(s); "
                                + (f"{res.bad} sin certificar o con problemas." if res.bad else "todas certificadas."))

    def _op_compat(self, force: bool) -> None:
        if not self._ops_free():
            return
        job = self.mgr.compat_job(force)
        if job is None:
            self.lbl_status.set_text(self.ctrl.message or "")
            return
        self._report_set("Compatibilidad de las recetas" + (" (forzada)" if force else ""), [])
        self._run_ops_task("Compatibilidad de las recetas", job, self._after_compat)

    def _after_compat(self, res, err) -> None:
        if err is not None:
            if not isinstance(err, OPS.Cancelled):
                self._report_append(f"ERROR: {err}")
            return
        for line in res.lines:
            self._report_append(line)

    def _op_matrix(self, complete: bool) -> None:
        if not self._ops_free():
            return
        job = self.mgr.matrix_job(complete)
        if job is None:
            self.lbl_status.set_text(self.ctrl.message or "")
            return
        self._report_set("Informe COMPATIBILIDAD.md", [])
        self._run_ops_task("Informe de compatibilidad", job, self._after_matrix)

    def _after_matrix(self, res, err) -> None:
        if err is not None:
            if not isinstance(err, OPS.Cancelled):
                self._report_append(f"ERROR: {err}")
            return
        self._report_append(f"Escrito {res}")

    def _confirm(self, message: str, detail: str, action: str, on_yes) -> None:
        """Pregunta antes de escribir en el catálogo; `on_yes()` corre solo si se elige `action`."""
        dlg = Gtk.AlertDialog(message=message, detail=detail, buttons=["Cancelar", action], cancel_button=0, default_button=0)

        def answered(d, res) -> None:
            try:
                i = d.choose_finish(res)
            except GLib.Error:
                return
            if i == 1:
                on_yes()

        dlg.choose(self, None, answered)

    def _op_capacity(self, refs: list[str] | None, write: bool = False) -> None:
        if not self._ops_free():
            return
        if write:
            n = len(self.mgr.capacity_refs([m.ref for m in self.mgr.pieces(latest_only=True)] if refs is None else refs))
            if n == 0:
                self.lbl_status.set_text("Ninguna de las piezas tiene tomas de pasador para medir.")
                return
            self._confirm(f"¿Guardar la capacidad medida de {n} pieza(s)?",
                          "Se escribe en el .pieza.json de cada pieza (el campo de capacidad de sus tomas). Es lo que `afm catalogo capacidad --escribir` hace.",
                          "Medir y guardar", lambda: self._start_capacity(refs, True))
        else:
            self._start_capacity(refs, False)

    def _start_capacity(self, refs: list[str] | None, write: bool) -> None:
        if not self._ops_free():
            return
        job = self.mgr.capacity_job(refs, write, emit=lambda line: GLib.idle_add(self._report_append, line))
        if job is None:
            self.lbl_status.set_text(self.ctrl.message or "")
            return
        self._report_set("Capacidad de las tomas" + (" (se guarda)" if write else " (sin guardar)"), [])
        self._run_ops_task("Midiendo y guardando la capacidad" if write else "Midiendo la capacidad", job, self._after_capacity)

    def _after_capacity(self, res, err) -> None:
        if err is not None and not isinstance(err, OPS.Cancelled):
            self._report_append(f"ERROR: {err}")

    def _op_contract_propose(self) -> None:
        try:
            prop = self.mgr.propose_contract()
        except (ValueError, KeyError) as exc:
            self._op_error("Calibrar el contrato", exc)
            return
        self._report_set("Contrato de la línea: propuesta", prop.lines)
        self.lbl_status.set_text("La propuesta tiene problemas: no se puede guardar." if prop.problems else
                                 "Propuesta lista. Si te sirve: Catálogo ▾ → Guardar la propuesta del contrato…")
        self._sync_ops_buttons()

    def _op_contract_save(self) -> None:
        if not self._ops_free() or not self.mgr.can_save_proposal():
            return

        def save() -> None:
            try:
                digest = self.mgr.save_proposal()
            except (ValueError, OSError) as exc:
                self._op_error("Guardar el contrato", exc)
                return
            self._report_append(f"Contrato guardado en linea.json (huella {digest}). La certificación se revisa con el contrato nuevo.")
            self.lbl_status.set_text("Contrato guardado.")
            self._reload()
            self._sync_ops_buttons()

        self._confirm("¿Guardar la propuesta como contrato de la línea?", "Reemplaza el contrato actual de linea.json, si lo hay. Las certificaciones guardadas pueden quedar vencidas.",
                      "Guardar contrato", save)

    def _op_contract_clear(self) -> None:
        if not self._ops_free():
            return

        def clear() -> None:
            try:
                had = self.mgr.clear_contract()
            except (ValueError, OSError) as exc:
                self._op_error("Borrar el contrato", exc)
                return
            self._report_set("Contrato de la línea", ["Contrato borrado de linea.json" if had else "La línea no tenía contrato"])
            self._reload()
            self._sync_ops_buttons()

        self._confirm("¿Borrar el contrato de la línea?", "Se quita el contrato de linea.json; las piezas dejan de tener talle. Se puede volver a calibrar.", "Borrar contrato", clear)

    def _run_ops_task(self, label: str, job, done, cancellable: bool = True) -> None:
        """Corre `job(progress)` en un hilo (la ventana sigue viva) y llama `done(resultado, error)` en el hilo principal."""
        self._task_label = label
        self.progress.start(None, label)
        self.task_row.set_visible(True)
        self.btn_task_cancel.set_sensitive(cancellable)
        self._sync_ops_buttons()
        self._sync_wrist_button(self._selected_refs())
        self._sync_joint_button(self._selected_refs())
        self.btn_delete.set_sensitive(False)

        def work() -> None:
            try:
                res = job(lambda text: GLib.idle_add(self._ops_progress, text))
            except Exception as exc:        # noqa: BLE001 — se informa en el panel
                GLib.idle_add(self._ops_done, label, done, None, exc)
            else:
                GLib.idle_add(self._ops_done, label, done, res, None)

        threading.Thread(target=work, daemon=True, name="afm-catalog-task").start()

    def _ops_progress(self, text: str) -> bool:
        self.progress.feed(text)
        return False

    def _ops_done(self, label: str, done, res, err) -> bool:
        cancelled = isinstance(err, OPS.Cancelled) or bool(getattr(res, "cancelled", False))
        self._task_label = ""
        self.progress.finish(err is None or cancelled, "Cancelado" if cancelled else label)
        self.task_row.set_visible(False)
        self.mgr.finish_task(label, OPS.Cancelled() if cancelled else err)
        self._pending_select = ""
        done(res, err)
        self._reload()                                  # las insignias (certificada / vencida) cambian
        if self._pending_select:                        # una importación deja elegida la pieza nueva
            self._select_ref(self._pending_select)
        self.lbl_status.set_text(self.ctrl.message or "")
        return False

    def _on_task_cancel(self, *_) -> None:
        self.mgr.request_cancel()
        self.btn_task_cancel.set_sensitive(False)
        self.lbl_status.set_text("Cancelando… se corta en cuanto el motor informa progreso.")

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
        self._sync_ops_buttons()
        self._wrist_refresh()

    def _wrist_cancel(self, redraw: bool = True) -> None:
        if self._cutter is None:
            return
        self._cutter = None
        self.wrist_panel.set_visible(False)
        self._sync_wrist_button(self._selected_refs())
        self._sync_ops_buttons()
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
        self._sync_ops_buttons()
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
        self._sync_ops_buttons()
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

    @staticmethod
    def _dd_value(dd: Gtk.DropDown, all_label: str) -> str | None:
        item = dd.get_selected_item()
        txt = item.get_string() if item is not None else all_label
        return None if txt == all_label else txt

    def _fill_dd(self, dd: Gtk.DropDown, all_label: str, present: list[str]) -> None:
        """Lista del filtro con lo que hay en el catálogo (conserva la elección si sigue existiendo)."""
        model = dd.get_model()
        have = [model.get_string(i) for i in range(model.get_n_items())] if model is not None else []
        names = [all_label] + sorted(set(present))
        if have == names:
            return
        current = self._dd_value(dd, all_label)
        self._updating = True
        try:
            dd.set_model(Gtk.StringList.new(names))
            dd.set_selected(names.index(current) if current in names else 0)
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
        self._kb_index = next((i for i, (r, _m) in enumerate(self._rows) if r is row), None)
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
        self._kb_index = None                                # las filas se rehacen: las flechas siguen desde lo elegido
        self._signature = self._disk_signature()
        everything = self.ctrl.catalog_pieces()              # una sola lectura del catálogo por recarga
        if sorted({m.tipo for m in everything}) != self._type_names():
            self._fill_types([m.tipo for m in everything])
        for dd, label, vals in ((self.dd_fstate, ALL_STATES, [m.estado for m in everything]),
                                (self.dd_tag, ALL_TAGS, [t for m in everything for t in m.etiquetas])):
            self._fill_dd(dd, label, vals)
        text, tipo = self.search.get_text(), self._type_filter()
        estado, etiqueta = self._dd_value(self.dd_fstate, ALL_STATES), self._dd_value(self.dd_tag, ALL_TAGS)
        last: dict[str, int] = {}
        for m in everything:
            last[m.id] = max(last.get(m.id, 0), m.version)
        latest = self.chk_latest.get_active()

        def cert_of(m) -> str:                                  # la certificación se muestra solo en la última versión de cada pieza
            return self.mgr.cert_text(m.ref) if m.version == last[m.id] else ""

        metas = sort_pieces([m for m in everything if matches(m, text, tipo, estado, etiqueta) and (not latest or m.version == last[m.id])],
                            *self._sort, cert_of=cert_of)
        root = self.ctrl.session.catalog_root
        total = len(everything)
        summary = self.mgr.cert_summary() if root else ""
        self.lbl_title.set_text("No hay un catálogo abierto." if not root else
                                f"Catálogo: {root}\n{total} versión(es) de pieza" + ("" if len(metas) == total else f" · se ven {len(metas)}")
                                + (f"\nCertificación: {summary}" if summary else ""))
        self._updating = True
        try:
            while (child := self.listbox.get_first_child()) is not None:
                self.listbox.remove(child)
            self._rows = []
            for m in metas:
                row = Gtk.ListBoxRow()
                row.set_child(self._row_widget(m, cert_of(m)))
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
        self._sync_state_row(refs)
        self._sync_undo_button()
        if n == 0:
            self.lbl_detail.set_text("Elegí una pieza de la lista para verla." if self._rows else "El catálogo no tiene piezas (todavía).")
            self._show(None)
            self._sync_wrist_button(refs)
            self._sync_joint_button(refs)
            self._sync_ops_buttons(refs)
            return
        meta = next(m for _row, m in self._rows if m.ref == refs[0])
        text = detail_text(meta, self.mgr.uses(meta.ref), self.mgr.cert_text(meta.ref))
        if n > 1:
            text = f"{n} piezas elegidas (se muestra la primera).\n\n" + text
        self.lbl_detail.set_text(text)
        if self._shown is None or self._shown[0].ref != meta.ref:      # agregar una a la selección no redibuja la misma
            self._show(meta)
        self._sync_wrist_button(refs)
        self._sync_joint_button(refs)
        self._sync_ops_buttons(refs)

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

    ARROW_KEYS = (Gdk.KEY_Up, Gdk.KEY_KP_Up, Gdk.KEY_Down, Gdk.KEY_KP_Down, Gdk.KEY_Home, Gdk.KEY_End, Gdk.KEY_Page_Up, Gdk.KEY_Page_Down)

    def _on_arrow(self, keyval, state, focus: bool = True) -> bool:
        """↑ / ↓ eligen la pieza anterior / siguiente (Mayús las suma a lo elegido); Inicio / Fin van a la primera / última y
        Re Pág / Av Pág saltan 10. Elige solo esa pieza, como un clic."""
        rows = [row for row, _m in self._rows]
        if not rows:
            return True
        key = {Gdk.KEY_Up: "up", Gdk.KEY_KP_Up: "up", Gdk.KEY_Down: "down", Gdk.KEY_KP_Down: "down", Gdk.KEY_Home: "home",
               Gdk.KEY_End: "end", Gdk.KEY_Page_Up: "pgup", Gdk.KEY_Page_Down: "pgdn"}[keyval]
        new = next_row(len(rows), key, self._kb_index, [i for i, r in enumerate(rows) if r.is_selected()])
        extend = bool(state & Gdk.ModifierType.SHIFT_MASK)
        self._updating = True                                      # un solo redibujado al final
        try:
            if not extend:
                self.listbox.unselect_all()
            self.listbox.select_row(rows[new])
        finally:
            self._updating = False
        self._kb_index = new
        if focus:
            rows[new].grab_focus()                                 # también la trae a la vista
        self._on_selection()
        return True

    def _on_key(self, _ctl, keyval, _code, state) -> bool:
        if keyval in self.ARROW_KEYS:
            return self._on_arrow(keyval, state)
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


class ImportDialog(Gtk.Window):
    """Formulario de una importación (`catalog_import.FORMS[kind]`): archivo + parámetros. «Importar» valida y lanza la tarea."""

    def __init__(self, parent: Gtk.Window, kind: str, start):
        form = CI.FORMS[kind]
        super().__init__(title=form["titulo"], transient_for=parent, modal=True, default_width=620)
        self.kind, self._start, self._form = kind, start, form
        self._w: dict[str, Gtk.Widget] = {}
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=14, margin_end=14)
        hint = Gtk.Label(label=form["ayuda"], xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        outer.append(hint)
        grid = Gtk.Grid(row_spacing=6, column_spacing=10)
        for r, f in enumerate(form["campos"]):
            lab = Gtk.Label(label=f.label + (" *" if f.required else ""), xalign=0, wrap=True, valign=Gtk.Align.START)
            grid.attach(lab, 0, r, 1, 1)
            w = self._make_field(f)
            if f.help:
                w.set_tooltip_text(f.help)
                lab.set_tooltip_text(f.help)
            grid.attach(w, 1, r, 1, 1)
        outer.append(grid)
        self.lbl_error = Gtk.Label(label="", xalign=0, wrap=True)
        self.lbl_error.add_css_class("error")
        outer.append(self.lbl_error)
        bar = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancelar")
        cancel.connect("clicked", lambda *_: self.close())
        go = Gtk.Button(label="Importar")
        go.add_css_class("suggested-action")
        go.connect("clicked", self._on_go)
        bar.append(cancel)
        bar.append(go)
        outer.append(bar)
        sw = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=640)
        sw.set_child(outer)
        self.set_child(sw)

    def _make_field(self, f: CI.Field) -> Gtk.Widget:
        if f.kind == CI.FILE:
            box = Gtk.Box(spacing=6)
            ent = Gtk.Entry(hexpand=True, placeholder_text="ruta del archivo .stl")
            btn = Gtk.Button(label="Elegir…")
            btn.connect("clicked", lambda *_: self._choose_file(ent))
            box.append(ent)
            box.append(btn)
            self._w[f.key] = ent
            return box
        if f.kind == CI.BOOL:
            w = Gtk.CheckButton(active=bool(f.default))
        elif f.kind == CI.CHOICE:
            w = Gtk.DropDown.new_from_strings(list(f.choices))
            w.set_selected(max(0, list(f.choices).index(f.default)) if f.default in f.choices else 0)
        elif f.kind == CI.MULTILINE:
            tv = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.NONE)
            tv.get_buffer().set_text(str(f.default))
            w = Gtk.ScrolledWindow(min_content_height=90, hexpand=True)
            w.set_child(tv)
            self._w[f.key] = tv
            return w
        else:
            w = Gtk.Entry(hexpand=True, text=str(f.default))
        self._w[f.key] = w
        return w

    def _choose_file(self, entry: Gtk.Entry) -> None:
        dlg = Gtk.FileDialog(title="Elegir el STL")
        flt = Gtk.FileFilter(name="STL")
        flt.add_pattern("*.stl")
        flt.add_pattern("*.STL")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)

        def done(d, res) -> None:
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            entry.set_text(f.get_path() or "")

        dlg.open(self, None, done)

    def values(self) -> dict:
        out: dict = {}
        for f in self._form["campos"]:
            w = self._w[f.key]
            if f.kind == CI.BOOL:
                out[f.key] = w.get_active()
            elif f.kind == CI.CHOICE:
                out[f.key] = w.get_selected_item().get_string()
            elif f.kind == CI.MULTILINE:
                b = w.get_buffer()
                out[f.key] = b.get_text(b.get_start_iter(), b.get_end_iter(), False)
            else:
                out[f.key] = w.get_text()
        return out

    def _on_go(self, *_) -> None:
        err = self._start(self.kind, self.values())
        if err:
            self.lbl_error.set_text(err)
        else:
            self.close()
