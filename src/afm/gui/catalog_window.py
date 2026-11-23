"""Ventana «Administrar catálogo»: lista de piezas con vista previa 3/4 y eliminación de una o varias.

La lógica (filtro, textos, cámara, borrado) está en `catalog_manager.py` y se prueba sin GTK; acá solo está la ventana.
La vista previa se dibuja fuera de pantalla (`PreviewRenderer`, contexto EGL propio) y se muestra como imagen: así no
compite con el contexto OpenGL del visor de la ventana principal. Se abre desde la pestaña «Catálogo» de la ventana principal."""
from __future__ import annotations
from ..i18n import tr, loc

import glob
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import catalog_ops as OPS  # noqa: E402
from .catalog_manager import (ALL_STATES, ALL_TAGS, ALL_TYPES, COLUMNS, PIN_LEGEND, CatalogManager, JointEditor, PreviewRenderer,  # noqa: E402
                              WristCutter, can_cut_wrist, can_retouch, can_retune_joint, can_turn_color, detail_text, matches, next_row, row_cells, sort_pieces)
from . import catalog_import as CI  # noqa: E402
from .catalog_manager import ZOOM_STEP, zoom_about  # noqa: E402
from .catalog_manager import view_bounds as CM_view_bounds  # noqa: E402
from .progress_panel import ProgressPanel  # noqa: E402
from .retouch_editor import RetouchEditor  # noqa: E402
from .retouch_session import ERASE, FILL, ORPHAN, PAINT  # noqa: E402
from .retouch_toolbox import RetouchToolbox  # noqa: E402
from ..parts import STATES  # noqa: E402
from .widgets import WrapRow, no_scroll_change as _no_scroll_change  # noqa: E402

JOINT_DELAY_MS = 250        # espera tras el último cambio de un valor antes de recalcular el hueco (se mueve un spin varias veces seguidas)
CLICK_MAX_PX = 4.0          # un arrastre más corto que esto es un clic (marca un punto); más largo gira la vista


class CatalogManagerWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, ctrl):
        super().__init__(title=tr("catalog_window.administrar_catalogo_de_piezas"), transient_for=parent, default_width=1240, default_height=780)
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
        self._reditor: RetouchEditor | None = None   # modo «Retocar color…» (None = no está activo)
        self._rpaint = False                         # hay un trazo del pincel en curso (arrastre con el botón izquierdo)
        self._rupdating = False
        self._rbusy = 0                              # «Ver resultado» en curso (hilo)
        self._hover_idle = 0                         # GLib source del redibujado pendiente del resaltado bajo el pincel
        self._rview_pending = False                  # se soltó el pincel con un «Ver resultado» en curso: recalcular al terminar
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
        self.search = Gtk.SearchEntry(placeholder_text=tr("catalog_window.buscar_nombre_tipo_figura_de_origen"))
        self.search.connect("search-changed", lambda *_: self._reload())
        box.append(self.search)
        row = Gtk.Box(spacing=6)
        self.dd_type = Gtk.DropDown.new_from_strings([ALL_TYPES])
        self.dd_type.set_hexpand(True)
        self.dd_type.connect("notify::selected", lambda *_: None if self._updating else self._reload())
        row.append(self.dd_type)
        btn_all = Gtk.Button(label=tr("catalog_window.elegir_todas_las_que_se_ven"))
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
        self.chk_latest = Gtk.CheckButton(label=tr("catalog_window.solo_la_ultima_version"))
        self.chk_latest.set_tooltip_text(tr("catalog_window.como_afm_catalogo_listar_una_fila"))
        self.chk_latest.set_active(True)                          # por defecto solo la última versión (antes de conectar «toggled»: no recarga)
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
        hint = Gtk.Label(label=tr("catalog_window.clic_elige_una_sola_pieza_mayus"),
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        box.append(hint)
        return box

    def _build_header(self) -> Gtk.Widget:
        """Cabecera de la tabla: un clic en una columna ordena por ella (otro clic invierte); «Tipo» agrupa por cabeza, torso, pierna…"""
        head = Gtk.Box(spacing=8, margin_start=8, margin_end=8)
        for key, title, width in COLUMNS:
            lab = Gtk.Label(xalign=0, width_chars=width, max_width_chars=width, ellipsize=Pango.EllipsizeMode.END,
                            tooltip_text=tr("catalog_window.ordenar_por_title_otro_clic_invierte", title=title))
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
        self.picture.set_size_request(200, 300)                # ancho mínimo bajo: en una ventana angosta el panel de detalle no se recorta
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
        motion.connect("motion", self._on_picture_motion)
        motion.connect("leave", self._on_picture_leave)
        self.picture.add_controller(motion)
        frame = Gtk.Frame()
        frame.set_child(self.picture)
        box.append(frame)
        legend = Gtk.Label(label=PIN_LEGEND, xalign=0, wrap=True)
        legend.add_css_class("dim-label")
        box.append(legend)

        bar = WrapRow(spacing=6)                                   # (si la ventana es angosta, los botones pasan a otra línea; el texto largo se acorta)
        reset = Gtk.Button(label=tr("common.vista_3_4"))
        reset.set_tooltip_text(tr("catalog_window.vuelve_a_la_vista_3_4"))
        reset.connect("clicked", self._on_reset_view)
        bar.append(reset)
        for txt, tip, fac in (("−", tr("catalog_window.alejar_tambien_la_rueda_del_raton"), 1 / ZOOM_STEP), ("+", tr("catalog_window.acercar_tambien_la_rueda_del_raton"), ZOOM_STEP)):
            b = Gtk.Button(label=txt)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, f=fac: self._zoom_by(f))
            bar.append(b)
        bar.append(Gtk.Label(label=tr("catalog_window.arrastra_para_girar_rueda_o_pellizco"), xalign=0, hexpand=True,
                             ellipsize=Pango.EllipsizeMode.END, width_chars=8))
        self.btn_wrist = Gtk.Button(label=tr("catalog_window.cortar_la_mano"))
        self.btn_wrist.set_sensitive(False)
        self.btn_wrist.connect("clicked", self._on_wrist_start)
        bar.append(self.btn_wrist)
        self.btn_grip = Gtk.Button(label=tr("catalog_window.taladrar_agarre"))
        self.btn_grip.set_sensitive(False)
        self.btn_grip.connect("clicked", self._on_grip_start)
        bar.append(self.btn_grip)
        self.btn_joint = Gtk.Button(label=tr("catalog_window.reajustar_articulacion"))
        self.btn_joint.set_sensitive(False)
        self.btn_joint.connect("clicked", self._on_joint_start)
        bar.append(self.btn_joint)
        self.btn_retouch = Gtk.Button(label=tr("catalog_window.retocar_color"))
        self.btn_retouch.set_sensitive(False)
        self.btn_retouch.connect("clicked", self._on_retouch_start)
        bar.append(self.btn_retouch)
        self.btn_turn = Gtk.Button(label=tr("catalog_window.girar_color"))
        self.btn_turn.set_sensitive(False)
        self.btn_turn.connect("clicked", self._on_turn_start)
        bar.append(self.btn_turn)
        self.btn_rename = Gtk.Button(label=tr("catalog_window.renombrar_f2"))
        self.btn_rename.set_sensitive(False)
        self.btn_rename.set_tooltip_text(tr("catalog_window.cambia_el_nombre_con_que_se"))
        self.btn_rename.connect("clicked", self._on_rename)
        bar.append(self.btn_rename)
        self.btn_delete = Gtk.Button(label=tr("catalog_window.eliminar"))
        self.btn_delete.add_css_class("destructive-action")
        self.btn_delete.connect("clicked", self._on_delete)
        bar.append(self.btn_delete)
        self.btn_undo = Gtk.Button(label=tr("catalog_window.recuperar_lo_eliminado"))
        self.btn_undo.set_sensitive(False)
        self.btn_undo.connect("clicked", self._on_undo_delete)
        bar.append(self.btn_undo)
        box.append(bar)
        box.append(self._build_state_row())
        box.append(self._build_ops_row())
        box.append(self._build_flex_row())
        box.append(self._build_wrist_panel())
        box.append(self._build_joint_panel())
        box.append(self._build_retouch_panel())
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
        # Con scroll vertical y sin scroll horizontal: las filas `WrapRow` se acomodan al ancho de la ventana y, si no entra todo en el alto,
        # el panel se desplaza. Sin esto la altura mínima del panel (todos los botones en una sola columna) impide maximizar la ventana.
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      min_content_width=240, min_content_height=200, hexpand=True, vexpand=True)
        scroller.set_child(box)
        return scroller

    # ------------------------------------------------------------ estado de las piezas elegidas
    def _build_state_row(self) -> Gtk.Widget:
        """«Estado: [borrador ▾] [Aplicar a las elegidas]»: cambia el estado de una o varias versiones a la vez."""
        row = WrapRow(spacing=8)
        row.append(Gtk.Label(label=tr("catalog_window.estado"), xalign=0))
        self.dd_state = Gtk.DropDown.new_from_strings(list(STATES))
        self.dd_state.set_tooltip_text(tr("catalog_window.borrador_validada_impresa_es_lo_unico"))
        row.append(self.dd_state)
        self.btn_state = Gtk.Button(label=tr("catalog_window.aplicar_a_las_elegidas"))
        self.btn_state.set_sensitive(False)
        self.btn_state.connect("clicked", self._on_apply_state)
        row.append(self.btn_state)
        return row

    def _sync_state_row(self, refs: list[str]) -> None:
        ok = bool(refs) and not self.ctrl.busy and self._cutter is None and self._jeditor is None
        self.btn_state.set_sensitive(ok)
        self.btn_state.set_label(tr("catalog_window.aplicar_a_las_elegidas") if len(refs) <= 1 else tr("catalog_window.aplicar_a_n_piezas", n=len(refs)))
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
        self.btn_undo.set_label(tr("catalog_window.recuperar_lo_eliminado_n", n=n) if n else tr("catalog_window.recuperar_lo_eliminado"))
        self.btn_undo.set_tooltip_text(tr("catalog_window.devuelve_al_catalogo_el_ultimo_grupo") if n
                                       else tr("catalog_window.no_hay_nada_eliminado_para_recuperar"))

    def _on_undo_delete(self, *_) -> None:
        if self.ctrl.busy or self._cutter is not None or self._jeditor is not None:
            return
        self.mgr.undo_delete()
        self._reload()
        self.lbl_status.set_text(self.ctrl.message or "")

    # ------------------------------------------------------------ comandos de `afm catalogo` (catalog_ops)
    def _build_catalog_menu(self) -> Gtk.Widget:
        """Menú «Catálogo ▾»: los comandos que valen para todo el catálogo (verificar, contrato, certificar todo, compatibilidad, matriz)."""
        self.menu_cat = Gtk.MenuButton(label=tr("common.catalogo"))
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self._menu_buttons: list[Gtk.Button] = []
        items = [
            (tr("catalog_window.verificar_el_catalogo"), self._op_verify, tr("catalog_window.afm_catalogo_verificar_formato_hash_mallas")),
            (tr("catalog_window.ver_el_contrato_de_la_linea"), self._op_contract, tr("catalog_window.afm_catalogo_contrato_ver_talles_de")),
            (tr("palette_panel.abrir"), self._open_palette, tr("palette_panel.abrir_tip")),
            (tr("roles_wizard.abrir"), self._open_roles_wizard, tr("roles_wizard.abrir_tip")),
            None,
            (tr("catalog_window.estado_de_la_certificacion_todas"), lambda: self._op_certify(None, status_only=True), tr("catalog_window.afm_catalogo_certificar_estado_no_arma")),
            (tr("catalog_window.certificar_todas_las_piezas"), lambda: self._op_certify(None), tr("catalog_window.afm_catalogo_certificar_arma_de_verdad")),
            (tr("catalog_window.certificar_todas_de_nuevo_forzar"), lambda: self._op_certify(None, force=True), tr("catalog_window.afm_catalogo_certificar_forzar_vuelve_a")),
            None,
            (tr("common.compatibilidad_de_las_recetas"), lambda: self._op_compat(False), tr("catalog_window.afm_catalogo_compat_build_validate_de")),
            (tr("catalog_window.compatibilidad_de_las_recetas_forzar"), lambda: self._op_compat(True), tr("catalog_window.afm_catalogo_compat_forzar")),
            (tr("catalog_window.escribir_compatibilidad_md"), lambda: self._op_matrix(False), tr("catalog_window.afm_catalogo_matriz_matriz_barata_del")),
            (tr("catalog_window.escribir_compatibilidad_md_validando_antes"), lambda: self._op_matrix(True), tr("catalog_window.afm_catalogo_matriz_completa")),
            None,
            (tr("catalog_window.medir_la_capacidad_de_las_tomas"), lambda: self._op_capacity(None), tr("catalog_window.afm_catalogo_capacidad_hasta_donde_llega")),
            (tr("catalog_window.medir_y_guardar_la_capacidad"), lambda: self._op_capacity(None, write=True), tr("catalog_window.afm_catalogo_capacidad_escribir_ademas_esc")),
            None,
            (tr("catalog_window.calibrar_el_contrato_propuesta"), self._op_contract_propose, tr("catalog_window.afm_catalogo_contrato_calibrar_tolerancia")),
            (tr("catalog_window.guardar_la_propuesta_del_contrato"), self._op_contract_save, tr("catalog_window.afm_catalogo_contrato_calibrar_guardar_esc")),
            (tr("catalog_window.borrar_el_contrato_de_la_linea_2"), self._op_contract_clear, tr("catalog_window.afm_catalogo_contrato_quitar_pide_confirma")),
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
            if label == tr("catalog_window.guardar_la_propuesta_del_contrato"):
                self._btn_save_proposal = b
        pop.set_child(box)
        self.menu_cat.set_popover(pop)
        return self.menu_cat

    def _build_import_menu(self) -> Gtk.Widget:
        """Menú «Importar ▾»: agregar al catálogo una mano, un accesorio, un arma o una pieza suelta desde un STL."""
        self.menu_import = Gtk.MenuButton(label=tr("catalog_window.importar"))
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self._import_buttons: list[Gtk.Button] = []
        for kind, label in CI.LABELS.items():
            b = Gtk.Button(label=label, has_frame=False, halign=Gtk.Align.START,
                           tooltip_text=tr("catalog_window.afm_catalogo_comando_ayuda", comando=CI.FORMS[kind]['comando'], ayuda=CI.FORMS[kind]['ayuda']))
            b.connect("clicked", lambda _b, k=kind: (pop.popdown(), self._open_import(k)))
            box.append(b)
            self._import_buttons.append(b)
        pop.set_child(box)
        self.menu_import.set_popover(pop)
        return self.menu_import

    def _open_palette(self) -> None:
        """«Paleta de la línea…»: abre la ventana de roles de color (una sola; si ya está abierta, la trae al frente)."""
        from .palette_window import PaletteWindow

        win = getattr(self, "_palette_win", None)
        if win is not None and win.get_visible():
            win.present()
            return
        # En GTK4 «destroy» solo se emite al liberar el widget y esta referencia lo mantiene vivo: una ventana ya
        # cerrada quedaba guardada y presentarla daba «A window is shown after it has been destroyed».
        # Por eso se suelta al cerrar (connect_after: si la paleta frena el cierre por cambios sin aplicar, no se suelta)
        # y, por si se cerró con destroy() directo, arriba se descarta la que ya no está visible.
        win = PaletteWindow(self, self.ctrl)
        self._palette_win = win
        win.connect_after("close-request", lambda w, *_: self._forget_window("_palette_win", w) or False)
        win.connect("destroy", lambda w, *_: self._forget_window("_palette_win", w))
        win.present()

    def _forget_window(self, attr: str, win) -> None:
        """Suelta la referencia a una ventana auxiliar cerrada, solo si sigue siendo la guardada."""
        if getattr(self, attr, None) is win:
            setattr(self, attr, None)

    def _open_roles_wizard(self) -> None:
        """«Roles de color desde un FBX…»: abre el asistente (una sola ventana; si ya está abierta, la trae al frente)."""
        from .roles_wizard_window import RolesWizardWindow

        win = getattr(self, "_roles_win", None)
        if win is not None and win.get_visible():
            win.present()
            return
        win = RolesWizardWindow(self, self.ctrl, on_saved=self.refresh_if_changed)
        self._roles_win = win
        win.connect_after("close-request", lambda w, *_: self._forget_window("_roles_win", w) or False)
        win.connect("destroy", lambda w, *_: self._forget_window("_roles_win", w))
        win.present()

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
            return self.ctrl.message or tr("catalog_window.no_se_pudo_empezar_la_importacion")
        self._report_set(tr("catalog_window.importar_titulo", titulo=CI.FORMS[kind]['titulo']), [])
        self._run_ops_task(tr("common.importando_titulo", titulo=CI.FORMS[kind]['titulo']), job, self._after_import, cancellable=False)
        return ""

    def _after_import(self, res, err) -> None:
        if err is not None:
            self._report_append(tr("catalog_window.error_err", err=err))
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
        row = WrapRow(spacing=6)
        self.btn_certify = Gtk.Button(label=tr("catalog_window.certificar"))
        self.btn_certify.connect("clicked", lambda *_: self._op_certify(self._selected_refs()))
        self.chk_force = Gtk.CheckButton(label=tr("catalog_window.forzar"))
        self.chk_force.set_tooltip_text(tr("catalog_window.volver_a_armar_aunque_el_resultado"))
        self.btn_cover = Gtk.Button(label=tr("catalog_window.cobertura"))
        self.btn_cover.connect("clicked", lambda *_: self._op_piece_report("cobertura"))
        self.btn_explain = Gtk.Button(label=tr("catalog_window.explicar_acoples"))
        self.btn_explain.connect("clicked", lambda *_: self._op_piece_report("explicar"))
        self.btn_capacity = Gtk.Button(label=tr("catalog_window.capacidad"))
        self.btn_capacity.connect("clicked", lambda *_: self._op_capacity(self._selected_refs()))
        for w, tip in ((self.btn_certify, tr("catalog_window.afm_catalogo_certificar_id_arma_la")),
                       (self.btn_cover, tr("catalog_window.afm_catalogo_cobertura_id_cuantas_piezas")),
                       (self.btn_capacity, tr("catalog_window.afm_catalogo_capacidad_id_hasta_donde")),
                       (self.btn_explain, tr("catalog_window.afm_catalogo_explicar_id_por_que"))):
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
        self.btn_task_cancel = Gtk.Button(label=tr("common.cancelar"), valign=Gtk.Align.CENTER)
        self.btn_task_cancel.set_tooltip_text(tr("catalog_window.corta_en_cuanto_el_motor_informa"))
        self.btn_task_cancel.connect("clicked", self._on_task_cancel)
        self.task_row.append(self.btn_task_cancel)
        return self.task_row

    def _build_report(self) -> Gtk.Widget:
        """Panel «Informe»: el texto de cualquier comando (lo mismo que imprimiría la consola), con copiar y guardar."""
        self.report_exp = Gtk.Expander(label=tr("catalog_window.informe"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.report_buf = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self.report_buf, editable=False, cursor_visible=False, monospace=True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        sw = Gtk.ScrolledWindow(min_content_height=140, vexpand=False)
        sw.set_child(view)
        box.append(sw)
        bar = Gtk.Box(spacing=6)
        for label, cb in ((tr("catalog_window.copiar"), self._on_report_copy), (tr("catalog_window.guardar"), self._on_report_save), (tr("catalog_window.limpiar"), lambda *_: self.report_buf.set_text(""))):
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
        dlg = Gtk.FileDialog(title=tr("catalog_window.guardar_el_informe"), initial_name="informe_catalogo.txt")

        def finished(d, res) -> None:
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                with open(f.get_path(), "w", encoding="utf-8") as fh:
                    fh.write(self._report_text())
                self.lbl_status.set_text(tr("catalog_window.informe_guardado_en_get_path", get_path=f.get_path()))
            except OSError as exc:
                self.lbl_status.set_text(tr("catalog_window.no_se_pudo_guardar_el_informe", exc=exc))

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
        self.btn_certify.set_label(tr("catalog_window.certificar") if len(refs) <= 1 else tr("catalog_window.certificar_n", n=len(self.mgr.certify_refs(refs))))

    def _op_error(self, title: str, exc: Exception) -> None:
        self._report_set(title, [tr("catalog_window.error_exc", exc=exc)])
        self.lbl_status.set_text(f"{title}: {exc}")

    def _op_verify(self) -> None:
        try:
            lines, ok = self.mgr.report_verify()
        except ValueError as exc:
            self._op_error(tr("catalog_window.verificar"), exc)
            return
        self._report_set(tr("catalog_window.verificar_el_catalogo"), lines)
        self.lbl_status.set_text(lines[-1] if lines else "")

    def _op_contract(self) -> None:
        try:
            lines = self.mgr.report_contract()
        except ValueError as exc:
            self._op_error(tr("catalog_window.contrato"), exc)
            return
        self._report_set(tr("catalog_window.contrato_de_la_linea"), lines)

    def _op_piece_report(self, which: str) -> None:
        refs = self._selected_refs()
        if len(refs) != 1:
            return
        try:
            lines = self.mgr.report_coverage(refs[0]) if which == "cobertura" else self.mgr.report_explain(refs[0])
        except (ValueError, KeyError) as exc:
            self._op_error(tr("catalog_window.cobertura") if which == "cobertura" else tr("catalog_window.explicar"), exc)
            return
        self._report_set(tr("catalog_window.cobertura_de_ref", ref=refs[0]) if which == "cobertura"
                         else tr("catalog_window.explicar_de_ref", ref=refs[0]), lines or [tr("catalog_window.sin_datos")])

    def _op_certify(self, refs: list[str] | None, force: bool = False, status_only: bool = False) -> None:
        if not self._ops_free():
            return
        force = force or self.chk_force.get_active()
        job = self.mgr.certify_job(refs, force, status_only, emit=lambda line: GLib.idle_add(self._report_append, line))
        if job is None:
            self.lbl_status.set_text(self.ctrl.message or "")
            return
        todo = self.mgr.certify_refs(refs)
        self._report_set((tr("catalog_window.estado_de_la_certificacion") if status_only else tr("catalog_window.certificar") + ((' ' + tr("catalog_window.forzado")) if force else "")) + (' ' + tr("catalog_window.n_pieza_s", n=len(todo))), [])
        label = tr("common.revisando_la_certificacion") if status_only else tr("common.certificando")
        self._run_ops_task(label, job, lambda res, err: self._after_certify(res, err))

    def _after_certify(self, res, err) -> None:
        if err is not None and not isinstance(err, OPS.Cancelled):
            self._report_append(tr("catalog_window.error_err", err=err))
        elif res is not None:
            self._report_append(((tr("common.cancelado_2") + ' ') if res.cancelled else "") + (tr("catalog_window.done_pieza_s_procesada_s", done=res.done) + ' ')
                                + (tr("catalog_window.bad_sin_certificar_o_con_problemas", bad=res.bad) if res.bad else tr("catalog_window.todas_certificadas")))

    def _op_compat(self, force: bool) -> None:
        if not self._ops_free():
            return
        job = self.mgr.compat_job(force)
        if job is None:
            self.lbl_status.set_text(self.ctrl.message or "")
            return
        self._report_set(tr("common.compatibilidad_de_las_recetas") + ((' ' + tr("catalog_window.forzada")) if force else ""), [])
        self._run_ops_task(tr("common.compatibilidad_de_las_recetas"), job, self._after_compat)

    def _after_compat(self, res, err) -> None:
        if err is not None:
            if not isinstance(err, OPS.Cancelled):
                self._report_append(tr("catalog_window.error_err", err=err))
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
        self._report_set(tr("catalog_window.informe_compatibilidad_md"), [])
        self._run_ops_task(tr("common.informe_de_compatibilidad"), job, self._after_matrix)

    def _after_matrix(self, res, err) -> None:
        if err is not None:
            if not isinstance(err, OPS.Cancelled):
                self._report_append(tr("catalog_window.error_err", err=err))
            return
        self._report_append(tr("catalog_window.escrito_res", res=res))

    def _confirm(self, message: str, detail: str, action: str, on_yes) -> None:
        """Pregunta antes de escribir en el catálogo; `on_yes()` corre solo si se elige `action`."""
        dlg = Gtk.AlertDialog(message=message, detail=detail, buttons=[tr("common.cancelar"), action], cancel_button=0, default_button=0)

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
                self.lbl_status.set_text(tr("common.ninguna_de_las_piezas_tiene_tomas"))
                return
            self._confirm(tr("catalog_window.guardar_la_capacidad_medida_de_n", n=n),
                          tr("catalog_window.se_escribe_en_el_pieza_json"),
                          tr("catalog_window.medir_y_guardar"), lambda: self._start_capacity(refs, True))
        else:
            self._start_capacity(refs, False)

    def _start_capacity(self, refs: list[str] | None, write: bool) -> None:
        if not self._ops_free():
            return
        job = self.mgr.capacity_job(refs, write, emit=lambda line: GLib.idle_add(self._report_append, line))
        if job is None:
            self.lbl_status.set_text(self.ctrl.message or "")
            return
        self._report_set(tr("catalog_window.capacidad_de_las_tomas") + ((' ' + tr("catalog_window.se_guarda")) if write else (' ' + tr("catalog_window.sin_guardar"))), [])
        self._run_ops_task(tr("common.midiendo_y_guardando_la_capacidad") if write else tr("common.midiendo_la_capacidad"), job, self._after_capacity)

    def _after_capacity(self, res, err) -> None:
        if err is not None and not isinstance(err, OPS.Cancelled):
            self._report_append(tr("catalog_window.error_err", err=err))

    def _op_contract_propose(self) -> None:
        try:
            prop = self.mgr.propose_contract()
        except (ValueError, KeyError) as exc:
            self._op_error(tr("catalog_window.calibrar_el_contrato"), exc)
            return
        self._report_set(tr("catalog_window.contrato_de_la_linea_propuesta"), prop.lines)
        self.lbl_status.set_text(tr("catalog_window.la_propuesta_tiene_problemas_no_se") if prop.problems else
                                 tr("catalog_window.propuesta_lista_si_te_sirve_catalogo"))
        self._sync_ops_buttons()

    def _op_contract_save(self) -> None:
        if not self._ops_free() or not self.mgr.can_save_proposal():
            return

        def save() -> None:
            try:
                digest = self.mgr.save_proposal()
            except (ValueError, OSError) as exc:
                self._op_error(tr("catalog_window.guardar_el_contrato"), exc)
                return
            self._report_append(tr("catalog_window.contrato_guardado_en_linea_json_huella", digest=digest))
            self.lbl_status.set_text(tr("catalog_window.contrato_guardado"))
            self._reload()
            self._sync_ops_buttons()

        self._confirm(tr("catalog_window.guardar_la_propuesta_como_contrato_de"), tr("catalog_window.reemplaza_el_contrato_actual_de_linea"),
                      tr("catalog_window.guardar_contrato"), save)

    def _op_contract_clear(self) -> None:
        if not self._ops_free():
            return

        def clear() -> None:
            try:
                had = self.mgr.clear_contract()
            except (ValueError, OSError) as exc:
                self._op_error(tr("catalog_window.borrar_el_contrato"), exc)
                return
            self._report_set(tr("catalog_window.contrato_de_la_linea"), [tr("catalog_window.contrato_borrado_de_linea_json") if had else tr("catalog_window.la_linea_no_tenia_contrato")])
            self._reload()
            self._sync_ops_buttons()

        self._confirm(tr("catalog_window.borrar_el_contrato_de_la_linea"), tr("catalog_window.se_quita_el_contrato_de_linea"), tr("catalog_window.borrar_contrato"), clear)

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
        self.progress.finish(err is None or cancelled, tr("common.cancelado") if cancelled else label)
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
        self.lbl_status.set_text(tr("catalog_window.cancelando_se_corta_en_cuanto_el"))

    # ------------------------------------------------------------ cortar la mano de un brazo
    def _build_wrist_panel(self) -> Gtk.Widget:
        """Panel del modo «Cortar la mano…» (oculto hasta que se activa)."""
        self.wrist_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        self.lbl_wrist = Gtk.Label(xalign=0, wrap=True)
        self.lbl_wrist.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.wrist_panel.append(self.lbl_wrist)
        row = WrapRow(spacing=6)
        self.btn_wrist_undo = Gtk.Button(label=tr("catalog_window.deshacer_el_ultimo_punto"))
        self.btn_wrist_undo.connect("clicked", self._on_wrist_undo)
        self.btn_wrist_invert = Gtk.Button(label=tr("catalog_window.invertir_lado"))
        self.btn_wrist_invert.set_tooltip_text(tr("catalog_window.si_lo_verde_no_es_la"))
        self.btn_wrist_invert.connect("clicked", self._on_wrist_invert)
        self.btn_wrist_cancel = Gtk.Button(label=tr("catalog_window.cancelar_esc"))
        self.btn_wrist_cancel.connect("clicked", lambda *_: self._wrist_cancel())
        for b in (self.btn_wrist_undo, self.btn_wrist_invert, self.btn_wrist_cancel):
            row.append(b)
        self.wrist_panel.append(row)
        row2 = WrapRow(spacing=6)
        row2.append(Gtk.Label(label=tr("catalog_window.id_de_la_mano")))
        self.ent_hand_id = Gtk.Entry(hexpand=True)
        self.ent_hand_id.set_tooltip_text(tr("catalog_window.pieza_mano_que_se_guarda_aparte"))
        row2.append(self.ent_hand_id)
        self.chk_hand_new = Gtk.CheckButton(label=tr("catalog_window.version_nueva_si_ya_existe"))
        row2.append(self.chk_hand_new)
        self.btn_wrist_save = Gtk.Button(label=tr("common.guardar"))
        self.btn_wrist_save.add_css_class("suggested-action")
        self.btn_wrist_save.connect("clicked", self._on_wrist_save)
        row2.append(self.btn_wrist_save)
        self.wrist_panel.append(row2)
        return self.wrist_panel

    def _sync_wrist_button(self, refs: list[str]) -> None:
        """«Cortar la mano…» se puede usar con UN brazo elegido que todavía tiene la mano pegada."""
        ok, why = False, tr("common.elegi_un_brazo_de_la_lista")
        if len(refs) == 1:
            meta = next((m for _row, m in self._rows if m.ref == refs[0]), None)
            ok, why = can_cut_wrist(meta)
        elif len(refs) > 1:
            why = tr("catalog_window.elegi_un_solo_brazo")
        if ok and self.ctrl.busy:
            ok, why = False, tr("common.espera_a_que_termine_la_tarea")
        if ok and self._shown is None:
            ok, why = False, tr("catalog_window.no_hay_vista_previa_de_la")
        self.btn_wrist.set_sensitive(ok and self._cutter is None and self._jeditor is None and self._reditor is None)
        self._sync_grip_button(refs)
        self._sync_turn_button(refs)
        self._sync_rename_button(refs)
        self.btn_wrist.set_tooltip_text(tr("catalog_window.le_corta_la_mano_al_brazo") if ok else why)

    def _sync_grip_button(self, refs: list[str]) -> None:
        """«Taladrar agarre…» se puede usar con UNA mano elegida y sin otra tarea en curso."""
        ok, why = False, tr("catalog_window.elegi_una_sola_mano")
        if len(refs) == 1:
            meta = next((m for _row, m in self._rows if m.ref == refs[0]), None)
            ok = meta is not None and meta.tipo == "mano"
        if ok and self.ctrl.busy:
            ok, why = False, tr("common.espera_a_que_termine_la_tarea")
        self.btn_grip.set_sensitive(ok and self._cutter is None and self._jeditor is None and self._reditor is None)
        self.btn_grip.set_tooltip_text(tr("catalog_window.taladrar_agarre_ayuda") if ok else why)

    def _sync_turn_button(self, refs: list[str]) -> None:
        """O08 · «Girar color 180°…» se puede usar con UNA cabeza con color por roles y sin otra tarea en curso."""
        meta = next((m for _row, m in self._rows if m.ref == refs[0]), None) if len(refs) == 1 else None
        ok, why = can_turn_color(meta)
        if ok and self.ctrl.busy:
            ok, why = False, tr("common.espera_a_que_termine_la_tarea")
        self.btn_turn.set_sensitive(ok and self._cutter is None and self._jeditor is None and self._reditor is None)
        self.btn_turn.set_tooltip_text(tr("catalog_window.girar_color_tip") if ok else why)

    def _on_turn_start(self, *_) -> None:
        refs = self._selected_refs()
        if len(refs) == 1 and self._ops_free():
            ImportDialog(self, "girar_color", self._start_import, initial={"ref": refs[0]}).present()

    def _on_grip_start(self, *_) -> None:
        refs = self._selected_refs()
        if len(refs) == 1 and self._ops_free():
            ImportDialog(self, "agarre", self._start_import, initial={"mano": refs[0]}).present()

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
            self.lbl_wrist.set_text(self.ctrl.message or tr("catalog_window.no_se_pudo_guardar"))
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
        tip = tr("catalog_window.solo_recoloca_la_pieza_distal_en")
        self.flex_row = Gtk.Box(spacing=8, visible=False)
        self.flex_row.append(Gtk.Label(label=tr("common.flexion_mostrada"), xalign=0, hexpand=True, tooltip_text=tip))
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
        self.dd_joint = Gtk.DropDown.new_from_strings([tr("common.ninguna")])
        self.dd_joint.connect("notify::selected", self._on_joint_choice)
        self.dd_joint.set_visible(False)                        # solo si la pieza trae más de una articulación
        self.joint_panel.append(self.dd_joint)
        grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        self._jspin: dict[str, Gtk.SpinButton] = {}
        specs = (("h", tr("common.altura_del_eje_sobre_el_plano"), 0.5, 15.0, 0.1, 2,
                  tr("catalog_window.sube_o_baja_el_eje_de")),
                 ("front", tr("catalog_window.inclinacion_del_eje_de_frente"), -45.0, 45.0, 0.5, 1,
                  tr("catalog_window.angulo_del_eje_visto_de_frente")),
                 ("side", tr("catalog_window.inclinacion_del_plano_de_costado"), -60.0, 60.0, 1.0, 1,
                  tr("catalog_window.angulo_del_plano_visto_de_costado")),
                 ("lo", tr("catalog_window.rango_minimo_negativo_hiperextension"), -45.0, 149.0, 1.0, 0, tr("catalog_window.flexion_minima_del_rango_de_la")),
                 ("hi", tr("catalog_window.rango_maximo"), -44.0, 150.0, 1.0, 0, tr("catalog_window.flexion_maxima_del_rango_de_la")))
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
        row = WrapRow(spacing=6)
        self.btn_joint_pick = Gtk.Button(label=tr("catalog_window.mover_el_eje_con_un_clic"))
        self.btn_joint_pick.set_tooltip_text(tr("catalog_window.muestra_la_pieza_entera_un_clic"))
        self.btn_joint_pick.connect("clicked", self._on_joint_pick)
        self.btn_joint_straight = Gtk.Button(label=tr("catalog_window.enderezar_el_plano"))
        self.btn_joint_straight.set_tooltip_text(tr("catalog_window.inclinacion_de_costado_0_el_eje"))
        self.btn_joint_straight.connect("clicked", lambda *_: self._on_joint_value("side", 0.0))
        self.btn_joint_reset = Gtk.Button(label=tr("catalog_window.restablecer"))
        self.btn_joint_reset.set_tooltip_text(tr("catalog_window.vuelve_a_la_articulacion_de_la"))
        self.btn_joint_reset.connect("clicked", self._on_joint_reset)
        for b in (self.btn_joint_pick, self.btn_joint_straight, self.btn_joint_reset):
            row.append(b)
        self.joint_panel.append(row)
        row = WrapRow(spacing=6)
        row.append(Gtk.Label(label=tr("catalog_window.se_guarda_como_version_nueva_borrador"), xalign=0, hexpand=True, wrap=True))
        self.btn_joint_cancel = Gtk.Button(label=tr("catalog_window.cancelar_esc"))
        self.btn_joint_cancel.connect("clicked", lambda *_: self._joint_cancel())
        self.btn_joint_save = Gtk.Button(label=tr("catalog_window.guardar_version_nueva"))
        self.btn_joint_save.add_css_class("suggested-action")
        self.btn_joint_save.connect("clicked", self._on_joint_save)
        row.append(self.btn_joint_cancel)
        row.append(self.btn_joint_save)
        self.joint_panel.append(row)
        return self.joint_panel

    def _sync_joint_button(self, refs: list[str]) -> None:
        """«Reajustar articulación…» se puede usar con UN brazo / pierna elegido que trae articulación."""
        ok, why = False, tr("common.elegi_un_brazo_o_una_pierna")
        if len(refs) == 1:
            meta = next((m for _row, m in self._rows if m.ref == refs[0]), None)
            ok, why = can_retune_joint(meta)
        elif len(refs) > 1:
            why = tr("catalog_window.elegi_una_sola_pieza")
        if ok and self.ctrl.busy:
            ok, why = False, tr("common.espera_a_que_termine_la_tarea")
        if ok and self._shown is None:
            ok, why = False, tr("catalog_window.no_hay_vista_previa_de_la_2")
        self.btn_joint.set_sensitive(ok and self._jeditor is None and self._cutter is None and self._reditor is None)
        self.flex_row.set_visible(ok or self._jeditor is not None)          # con articulación: se ve aunque no se esté reajustando
        self.flex_row.set_sensitive(self._cutter is None and (self._jeditor is not None or (ok and not self.ctrl.busy)))
        self.btn_joint.set_tooltip_text(tr("catalog_window.move_el_eje_de_la_bisagra") if ok else why)

    def edit_joint(self, ref: str) -> None:
        """Elige la pieza `ref` (`id@n`, o `id` = la última versión) y abre «Reajustar articulación…» (lo llama la ventana principal)."""
        self._reload()
        pid, _, ver = ref.partition("@")
        found = [(row, m) for row, m in self._rows if m.id == pid and (not ver or m.version == int(ver))]
        if not found:
            self.lbl_status.set_text(tr("catalog_window.ref_no_esta_en_la_lista", ref=ref))
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
            self.lbl_joint.set_text(self.ctrl.message or tr("catalog_window.no_se_pudo_guardar"))
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
        if keyval == Gdk.KEY_Escape and self._reditor is not None:
            self._retouch_cancel()
            return True
        if self._reditor is not None and (_state & Gdk.ModifierType.CONTROL_MASK) and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self._on_retouch_undo() if not (_state & Gdk.ModifierType.SHIFT_MASK) else self._on_retouch_redo()
            return True
        if keyval == Gdk.KEY_Escape and self._cutter is not None:
            self._wrist_cancel()
            return True
        return False

    # ------------------------------------------------------------ modo «Retocar color…» (R07)
    def _build_retouch_panel(self) -> Gtk.Widget:
        """Franja del modo «Retocar color…» (oculta hasta que se activa): estado y ayuda. Las herramientas viven en la ventana flotante
        `RetouchToolbox` (estilo Paintbrush); los widgets se publican con los mismos nombres de siempre."""
        tb = self._rtoolbox = RetouchToolbox(self)
        self.dd_rrole, self.spin_rradius, self.spin_rreach = tb.dd_role, tb.spin_radius, tb.spin_reach
        self.tgl_rpaint, self.tgl_rerase, self.tgl_rfill, self.chk_rpoly = tb.tgl_paint, tb.tgl_erase, tb.tgl_fill, tb.tgl_poly
        self.btn_rundo, self.btn_rredo, self.btn_rview, self.btn_rdiscard = tb.btn_undo, tb.btn_redo, tb.btn_view, tb.btn_discard
        self.btn_rexport, self.btn_rimport, self.btn_rbring = tb.btn_export, tb.btn_import, tb.btn_bring
        self.dd_rsource, self.chk_rmirror = tb.dd_source, tb.chk_mirror
        self.btn_rcancel, self.btn_rapply = tb.btn_cancel, tb.btn_apply
        self._rsources: list[str] = []
        self.dd_rrole.connect("notify::selected", self._on_retouch_role)
        self.spin_rradius.connect("value-changed", self._on_retouch_radius)
        self.spin_rreach.connect("value-changed", self._on_retouch_reach)
        for t in (self.tgl_rpaint, self.tgl_rerase, self.tgl_rfill):
            t.connect("toggled", self._on_retouch_mode)
        self.chk_rpoly.connect("toggled", self._on_retouch_polygon)
        self.btn_rundo.connect("clicked", lambda *_: self._on_retouch_undo())
        self.btn_rredo.connect("clicked", lambda *_: self._on_retouch_redo())
        self.btn_rview.connect("clicked", self._on_retouch_view)
        self.btn_rdiscard.connect("clicked", self._on_retouch_discard)
        self.btn_rexport.connect("clicked", self._on_retouch_export)
        self.btn_rimport.connect("clicked", self._on_retouch_import)
        self.btn_rbring.connect("clicked", self._on_retouch_bring)
        self.btn_rcancel.connect("clicked", lambda *_: self._retouch_cancel())
        self.btn_rapply.connect("clicked", self._on_retouch_apply)
        keys = Gtk.EventControllerKey()                              # Esc y Ctrl+Z también con el foco en la ventana de herramientas
        keys.connect("key-pressed", self._on_window_key)
        tb.add_controller(keys)

        self.retouch_panel = Gtk.Box(spacing=8, visible=False)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self.lbl_retouch = Gtk.Label(xalign=0, wrap=True)
        self.lbl_retouch.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        help_ = Gtk.Label(label=tr("catalog_window.retocar_ayuda"), xalign=0, wrap=True)
        help_.add_css_class("dim-label")
        text.append(self.lbl_retouch)
        text.append(help_)
        self.btn_rtools = Gtk.Button(label=tr("catalog_window.retocar_herramientas"), valign=Gtk.Align.START)
        self.btn_rtools.set_tooltip_text(tr("catalog_window.retocar_herramientas_tip"))
        self.btn_rtools.connect("clicked", lambda *_: self._rtoolbox.present())
        self.retouch_panel.append(text)
        self.retouch_panel.append(self.btn_rtools)
        return self.retouch_panel

    def _sync_retouch_button(self, refs: list[str]) -> None:
        """«Retocar color…» se puede usar con UNA pieza elegida que lleva color por roles."""
        ok, why = False, tr("catalog_window.retocar_elegi_una_pieza")
        if len(refs) == 1:
            meta = next((m for _row, m in self._rows if m.ref == refs[0]), None)
            ok, why = can_retouch(meta)
        elif len(refs) > 1:
            why = tr("catalog_window.elegi_una_sola_pieza")
        if ok and self.ctrl.busy:
            ok, why = False, tr("common.espera_a_que_termine_la_tarea")
        if ok and self._shown is None:
            ok, why = False, tr("catalog_window.no_hay_vista_previa_de_la_2")
        self.btn_retouch.set_sensitive(ok and self._reditor is None and self._cutter is None and self._jeditor is None)
        self.btn_retouch.set_tooltip_text(tr("catalog_window.retocar_tip") if ok else why)

    def _on_retouch_start(self, *_) -> None:
        if self._shown is None or self._reditor is not None or self._cutter is not None or self._jeditor is not None:
            return
        meta, mesh = self._shown
        ok, why = can_retouch(meta)
        cat = self.ctrl.catalog_handle()
        if not ok or cat is None:
            self.lbl_status.set_text(why or tr("catalog_window.retocar_elegi_una_pieza"))
            return
        try:
            ed = RetouchEditor(cat, meta, mesh)
        except Exception as exc:                                    # noqa: BLE001  (retoque ilegible / paleta ausente: se muestra y no se entra)
            self.lbl_status.set_text(tr("catalog_window.retocar_error", exc=exc))
            return
        if not ed.session.palette_ids:
            self.lbl_status.set_text(tr("catalog_window.retocar_sin_paleta"))
            return
        ed.session.set_paint_orphan(True)                           # «Pintar» pinta también lo que no tiene origen
        ed.session.set_polygon(True)                                # «Polígono entero» activado por defecto
        self._reditor = ed
        self._dyaw = self._dpitch = 0.0
        self._zoom, self._pan = 1.0, (0.0, 0.0)
        self._rupdating = True
        try:
            self._rtoolbox.set_palette([(r.id, r.nombre, r.hex) for r in ed.palette.roles if r.id in ed.session.palette_ids])
            self.spin_rradius.set_value(ed.session.radius)
            self._rtoolbox.sync_size_presets()
            self.spin_rreach.set_value(ed.session.reach)
            self.tgl_rpaint.set_active(True)
            self.chk_rmirror.set_active(False)
            self.chk_rpoly.set_active(ed.session.polygon)
            self._rsources = [m.ref for m in self.mgr.pieces() if m.ref != meta.ref and (getattr(m, "color", None) or {}).get("retoque")]
            self.dd_rsource.set_model(Gtk.StringList.new(self._rsources or [tr("common.ninguna")]))
            self.btn_rbring.set_sensitive(bool(self._rsources))
        finally:
            self._rupdating = False
        self.retouch_panel.set_visible(True)
        self._rtoolbox.present()
        for b in (self.btn_retouch, self.btn_turn, self.btn_wrist, self.btn_joint, self.btn_delete):
            b.set_sensitive(False)
        self._sync_ops_buttons()
        self._retouch_refresh()

    def _retouch_cancel(self, redraw: bool = True) -> None:
        ed = self._reditor
        if ed is None:
            return
        n = len(ed.session) if ed.dirty else 0
        self._reditor = None
        self._rpaint = False
        self._rview_pending = False
        if self._hover_idle:
            GLib.source_remove(self._hover_idle)
            self._hover_idle = 0
        self.retouch_panel.set_visible(False)
        self._rtoolbox.set_visible(False)
        self._color_shown = None
        self._sync_wrist_button(self._selected_refs())
        self._sync_joint_button(self._selected_refs())
        self._sync_retouch_button(self._selected_refs())
        self._sync_ops_buttons()
        self.btn_delete.set_sensitive(bool(self._selected_refs()) and not self.ctrl.busy)
        if n:
            self.lbl_status.set_text(tr("catalog_window.retocar_descartado", n=n))
        if redraw:
            self._redraw()
            if self._shown is not None and getattr(self._shown[0], "color", None):
                self._load_color_preview(self._shown[0].ref)         # vuelve el color guardado

    def _retouch_refresh(self) -> None:
        """Texto y botones según el estado de la sesión; redibuja."""
        ed = self._reditor
        if ed is None:
            return
        s = ed.session
        self.lbl_retouch.set_text(tr("catalog_window.retocar_estado", n=len(s), roles=", ".join(s.roles_used()) or "—") + ("  " + ed.message if ed.message else ""))
        self._rtoolbox.lbl_count.set_text(tr("catalog_window.retocar_estado", n=len(s), roles=", ".join(s.roles_used()) or "—"))
        self.btn_rundo.set_sensitive(s.can_undo)
        self.btn_rredo.set_sensitive(s.can_redo)
        self.btn_rdiscard.set_sensitive(ed.dirty)
        self.btn_rapply.set_sensitive(ed.dirty)
        self.btn_rview.set_sensitive(self._rbusy == 0)
        self._redraw()

    def _on_retouch_role(self, *_) -> None:
        ed = self._reditor
        if ed is None or self._rupdating:
            return
        i = self.dd_rrole.get_selected()
        ids = ed.session.palette_ids
        if 0 <= i < len(ids):
            ed.session.set_role(ids[i])
            if ed.session.mode == PAINT:                      # elegir un rol vuelve a pintar (si se estaba rellenando, se sigue rellenando)
                self._rupdating = True
                try:
                    self.tgl_rpaint.set_active(True)
                finally:
                    self._rupdating = False

    def _on_retouch_radius(self, *_) -> None:
        if self._reditor is not None and not self._rupdating:
            self._reditor.session.set_radius(self.spin_rradius.get_value())
            self._redraw()

    def _on_retouch_mode(self, *_) -> None:
        if self._reditor is not None and not self._rupdating:
            self._reditor.session.set_mode(FILL if self.tgl_rfill.get_active() else PAINT if self.tgl_rpaint.get_active() else ERASE)

    def _on_retouch_polygon(self, *_) -> None:
        if self._reditor is not None and not self._rupdating:
            self._reditor.session.set_polygon(self.chk_rpoly.get_active())
            self._reditor.clear_hover()
            self._redraw()

    def _on_retouch_reach(self, *_) -> None:
        if self._reditor is not None and not self._rupdating:
            self._reditor.session.set_reach(self.spin_rreach.get_value())

    # --- traer pinceladas de un archivo u otra pieza (como un solo trazo deshacible)
    def _retouch_add_strokes(self, strokes, source: str = "") -> None:
        """Suma `strokes` al retoque en curso (espejadas si la casilla está marcada) y recalcula «Ver resultado»."""
        ed = self._reditor
        if ed is None:
            return
        mirror = self.chk_rmirror.get_active()
        try:
            if mirror:
                from ..retouch import Retouch
                strokes = Retouch(strokes).mirrored().strokes
            n = ed.session.import_strokes(strokes)
        except Exception as exc:                                    # noqa: BLE001  (no entran / rol fuera de la paleta: no cambia nada)
            self.lbl_status.set_text(tr("catalog_window.retocar_no_entra", exc=exc))
            return
        ed.result_shown = False
        self.lbl_status.set_text(tr("catalog_window.retocar_importado", n=n, espejo=tr("catalog_window.retocar_espejado") if mirror else ""))
        self._retouch_refresh()
        if n:
            self._on_retouch_view()

    def _on_retouch_export(self, *_) -> None:
        ed = self._reditor
        if ed is None:
            return
        rt = ed.session.retouch()
        if len(rt) == 0:
            self.lbl_status.set_text(tr("catalog_window.retocar_sin_nada"))
            return
        dlg = Gtk.FileDialog(title=tr("catalog_window.retocar_exportar"), initial_name=f"{ed.meta.id}.retoque.json")

        def finished(d, res) -> None:
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                self.lbl_status.set_text(tr("catalog_window.retocar_exportado", path=rt.save(f.get_path())))
            except Exception as exc:                                # noqa: BLE001
                self.lbl_status.set_text(tr("catalog_window.retocar_no_entra", exc=exc))

        dlg.save(self, None, finished)

    def _on_retouch_import(self, *_) -> None:
        if self._reditor is None:
            return
        dlg = Gtk.FileDialog(title=tr("catalog_window.retocar_importar"))

        def finished(d, res) -> None:
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            try:
                from ..retouch import Retouch
                strokes = Retouch.load(f.get_path()).strokes
            except Exception as exc:                                # noqa: BLE001  (archivo ilegible o de otro formato)
                self.lbl_status.set_text(tr("catalog_window.retocar_no_entra", exc=exc))
                return
            self._retouch_add_strokes(strokes)

        dlg.open(self, None, finished)

    def _on_retouch_bring(self, *_) -> None:
        ed = self._reditor
        if ed is None or not self._rsources:
            self.lbl_status.set_text(tr("catalog_window.retocar_sin_otras"))
            return
        i = self.dd_rsource.get_selected()
        if not 0 <= i < len(self._rsources):
            return
        try:
            rt = self.ctrl.catalog_handle(required=True).retouch(self._rsources[i])
        except Exception as exc:                                    # noqa: BLE001  (archivo faltante / hash alterado)
            self.lbl_status.set_text(tr("catalog_window.retocar_no_entra", exc=exc))
            return
        if rt is None:
            self.lbl_status.set_text(tr("catalog_window.retocar_sin_otras"))
            return
        self._retouch_add_strokes(rt.strokes, self._rsources[i])

    def _on_retouch_undo(self) -> None:
        if self._reditor is not None and self._reditor.session.undo():
            self._reditor.result_shown = False
            self._retouch_refresh()
            self._on_retouch_view()                              # como al soltar el pincel: se muestra el color real del retoque que quedó

    def _on_retouch_redo(self) -> None:
        if self._reditor is not None and self._reditor.session.redo():
            self._reditor.result_shown = False
            self._retouch_refresh()
            self._on_retouch_view()

    def _on_retouch_discard(self, *_) -> None:
        ed = self._reditor
        if ed is not None and ed.session.discard():
            self._color_shown = None
            self._retouch_refresh()

    def _on_retouch_view(self, *_) -> None:
        """«Ver resultado»: calcula en un hilo la pieza con el color real del retoque (sin guardarlo) y la dibuja."""
        ed = self._reditor
        if ed is None:
            return
        if self._rbusy:                                               # ya se está calculando: al terminar se recalcula con los trazos nuevos
            self._rview_pending = True
            return
        ref, rt = ed.meta.ref, ed.session.retouch()
        self._rbusy += 1
        self.btn_rview.set_sensitive(False)
        self.lbl_status.set_text(tr("catalog_window.color_calculando"))

        def work():
            try:
                parts, err = self.mgr.color_parts_with(ref, rt), None
            except Exception as exc:                                  # noqa: BLE001  (se muestra; el modo sigue)
                parts, err = None, exc
            GLib.idle_add(self._retouch_view_done, ref, parts, err)

        threading.Thread(target=work, daemon=True).start()

    def _retouch_view_done(self, ref: str, parts, err) -> bool:
        self._rbusy = max(0, self._rbusy - 1)
        ed = self._reditor
        if ed is None or ed.meta.ref != ref:
            self._rview_pending = False
            return False
        if self._rview_pending and not self._rbusy:                  # llegaron trazos mientras se calculaba: este resultado ya quedó viejo
            self._rview_pending = False
            self._on_retouch_view()
            return False
        if err is not None:
            self.lbl_status.set_text(tr("catalog_window.color_error", ref=ref, exc=err))
        else:
            self._color_shown = (ref, parts) if parts else None
            ed.result_shown = bool(parts)
            self.lbl_status.set_text(tr("catalog_window.color_listo") if parts else "")
        self._retouch_refresh()
        return False

    def _on_retouch_apply(self, *_) -> None:
        ed = self._reditor
        if ed is None or not ed.dirty:
            return
        try:
            meta = ed.apply(self.ctrl.catalog_handle(required=True))
        except Exception as exc:                                      # noqa: BLE001  (CatalogError: falta un rol en la paleta, etc.)
            self.lbl_status.set_text(tr("catalog_window.retocar_error", exc=exc))
            return
        n = len(ed.session)
        mesh = self._shown[1] if self._shown else None
        self._retouch_cancel(redraw=False)
        self._reload()                                                # la ficha cambió: la lista y el detalle se rehacen
        if mesh is not None:
            self._shown = (meta, mesh)
            self._redraw()
            self._load_color_preview(meta.ref)                        # la caché de color incluye el hash del retoque: sale el nuevo
        self._flash = tr("catalog_window.retocar_guardado", n=n)
        self.lbl_status.set_text(self._flash)

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
        self.lbl_title.set_text(tr("common.no_hay_un_catalogo_abierto") if not root else
                                tr("catalog_window.catalogo_root_total_version_es_de", root=root, total=total) + ("" if len(metas) == total else (' ' + tr("catalog_window.se_ven_n", n=len(metas))))
                                + (('\n' + tr("catalog_window.certificacion_summary", summary=summary)) if summary else ""))
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
        if self._reditor is not None and refs != [self._reditor.meta.ref]:
            self._retouch_cancel(redraw=False)
        self.btn_delete.set_label(tr("catalog_window.eliminar") if n == 0 else tr("catalog_window.eliminar_n_pieza_s", n=n))
        self.btn_delete.set_sensitive(bool(n) and not self.ctrl.busy and self._cutter is None and self._jeditor is None and self._reditor is None)
        self._sync_rename_button(refs)
        self._sync_state_row(refs)
        self._sync_undo_button()
        if n == 0:
            self.lbl_detail.set_text(tr("catalog_window.elegi_una_pieza_de_la_lista") if self._rows else tr("catalog_window.el_catalogo_no_tiene_piezas_todavia"))
            self._show(None)
            self._sync_wrist_button(refs)
            self._sync_joint_button(refs)
            self._sync_retouch_button(refs)
            self._sync_ops_buttons(refs)
            return
        meta = next(m for _row, m in self._rows if m.ref == refs[0])
        text = detail_text(meta, self.mgr.uses(meta.ref), self.mgr.cert_text(meta.ref))
        if n > 1:
            text = (tr("catalog_window.n_piezas_elegidas_se_muestra_la", n=n) + '\n\n') + text
        self.lbl_detail.set_text(text)
        if self._shown is None or self._shown[0].ref != meta.ref:      # agregar una a la selección no redibuja la misma
            self._show(meta)
        self._sync_wrist_button(refs)
        self._sync_joint_button(refs)
        self._sync_retouch_button(refs)
        self._sync_ops_buttons(refs)

    def _show(self, meta) -> None:
        """Pone `meta` (o nada) en la vista previa, en vista 3/4."""
        self._dyaw = self._dpitch = 0.0
        self._zoom, self._pan = 1.0, (0.0, 0.0)
        self._shown = None
        self._color_shown = None                                         # (ref, partes de color) de la pieza que se ve; None = gris
        if meta is None:
            self.preview.clear()
            self.picture.set_paintable(None)
            self.lbl_status.set_text("")
            return
        try:
            self._shown = (meta, self.mgr.mesh(meta.ref))
        except (ValueError, OSError) as exc:
            self.picture.set_paintable(None)
            self.lbl_status.set_text(tr("catalog_window.no_se_pudo_leer_la_malla", ref=meta.ref, exc=exc))
            return
        self._redraw()
        if getattr(meta, "color", None):
            self._load_color_preview(meta.ref)

    def _load_color_preview(self, ref: str) -> None:
        """Pieza con color: se calcula en un hilo (puede tardar unos segundos) y al terminar se redibuja a color; mientras tanto se ve en gris."""
        self.lbl_status.set_text(tr("catalog_window.color_calculando"))

        def work():
            try:
                parts, err = self.mgr.color_parts(ref), None
            except Exception as exc:                                     # noqa: BLE001  (se muestra en la barra de estado; la vista gris sigue)
                parts, err = None, exc
            GLib.idle_add(self._color_preview_done, ref, parts, err)

        threading.Thread(target=work, daemon=True).start()

    def _color_preview_done(self, ref: str, parts, err) -> bool:
        if self._shown is None or self._shown[0].ref != ref:             # mientras tanto se eligió otra pieza
            return False
        if err is not None:
            self.lbl_status.set_text(tr("catalog_window.color_error", ref=ref, exc=err))
            return False
        self._color_shown = (ref, parts) if parts else None
        flash, self._flash = getattr(self, "_flash", ""), ""         # el aviso de «Retoque guardado» no se pierde bajo el de la vista a color
        self.lbl_status.set_text((flash + "  " if flash else "") + tr("catalog_window.color_listo") if parts else flash)
        self._redraw()
        return False

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
            elif self._reditor is not None:                                  # retocando: la pieza (con el color de «Ver resultado» si se pidió) y el pincel encima
                re_ = self._reditor
                cs = getattr(self, "_color_shown", None)
                cparts = cs[1] if cs is not None and cs[0] == meta.ref else None
                img = self.preview.render(mesh, meta, self._dyaw, self._dpitch, None, zoom=self._zoom, pan=self._pan, color_parts=cparts, guides=False,      # al retocar el color no se dibujan los pasadores ni el plano de la articulación
                                          hover=re_.hover_segments(),
                                          edges=True)             # los lados de los polígonos siempre a la vista (claros sobre un color oscuro, oscuros sobre uno claro)            # solo se iluminan las aristas que el pincel pintaría bajo el mouse (sin manchas de color)
            elif c is not None and c.preview_parts() is not None:          # corte calculado: brazo sin mano en gris y la mano en verde
                img = self.preview.render_parts(c.preview_parts(), self._dyaw, self._dpitch, c.marks(),
                                                zoom=self._zoom, pan=self._pan,
                                                bounds=CM_view_bounds(mesh, meta))      # mismo encuadre que antes del corte: la vista no salta
            else:
                cs = getattr(self, "_color_shown", None)
                cparts = cs[1] if cs is not None and cs[0] == meta.ref else None
                img = self.preview.render(mesh, meta, self._dyaw, self._dpitch, c.marks() if c is not None else None,
                                          zoom=self._zoom, pan=self._pan, color_parts=cparts)
        except Exception as exc:                               # noqa: BLE001  (sin EGL: el resto de la ventana sigue andando)
            self.picture.set_paintable(None)
            self.lbl_status.set_text(tr("common.la_vista_previa_3d_no_esta", exc=exc))
            return
        if not (getattr(self, "_color_shown", None) and self.lbl_status.get_text().endswith(tr("catalog_window.color_listo"))):
            self.lbl_status.set_text("")                                 # el aviso de «vista a color» se queda mientras se gira la pieza
        h, w = img.shape[:2]
        self._img_size = (w, h)
        data = GLib.Bytes.new(img.tobytes())
        self.picture.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, data, w * 4))

    def _widget_size(self) -> tuple[float, float]:
        return float(self.picture.get_width()), float(self.picture.get_height())

    def _on_drag_begin(self, g, x: float, y: float) -> None:
        self._drag0 = (self._dyaw, self._dpitch)
        self._press = (x, y)
        re_ = self._reditor
        self._rpaint = False
        if re_ is not None and not (g.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK) and self._shown is not None:
            self._rpaint = re_.press(x, y, self._widget_size(), self._img_size, self._dyaw, self._dpitch, self._zoom, self._pan)
            if self._rpaint:
                self._retouch_refresh()

    def _on_drag_end(self, _g, dx: float, dy: float) -> None:
        """Un arrastre casi sin movimiento es un clic: con el modo «Cortar la mano…» activo, marca un punto de la muñeca."""
        if self._rpaint and self._reditor is not None:
            self._rpaint = False
            n = self._reditor.release()
            self._reditor.result_shown = False
            self._retouch_refresh()
            if n or self._reditor.session.mode == FILL:              # al soltar el botón se muestra el color real (como «Ver resultado»), sea pintar, borrar o rellenar
                self._on_retouch_view()
            return
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

    def _on_picture_motion(self, _c, x: float, y: float) -> None:
        """El mouse se mueve sobre la vista previa: guarda el cursor y, retocando, ilumina las aristas que el pincel pintaría ahí."""
        self._cursor = (x, y)
        re_ = self._reditor
        if re_ is None or self._shown is None:
            return
        if re_.hover(x, y, self._widget_size(), self._img_size, self._dyaw, self._dpitch, self._zoom, self._pan):
            self._schedule_hover_redraw()

    def _on_picture_leave(self, *_) -> None:
        if self._reditor is not None and self._reditor.clear_hover():
            self._schedule_hover_redraw()

    def _schedule_hover_redraw(self) -> None:
        """Un solo redibujado por vuelta del bucle principal: si el mouse se mueve rápido solo se dibuja la última posición."""
        if self._hover_idle:
            return

        def run():
            self._hover_idle = 0
            if self._reditor is not None:
                self._redraw()
            return False

        self._hover_idle = GLib.idle_add(run)

    def _on_drag(self, _g, dx: float, dy: float) -> None:
        if self._rpaint and self._reditor is not None:                # pintando: el arrastre no gira la vista
            x, y = self._press[0] + dx, self._press[1] + dy
            self._reditor.move(x, y, self._widget_size(), self._img_size, self._dyaw, self._dpitch, self._zoom, self._pan)
            self._reditor.hover(x, y, self._widget_size(), self._img_size, self._dyaw, self._dpitch, self._zoom, self._pan)   # el resaltado sigue al pincel
            self._redraw()
            return
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
        dlg = Gtk.Window(title=tr("catalog_window.renombrar_pieza"), transient_for=self, modal=True, default_width=440, resizable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(Gtk.Label(label=tr("catalog_window.nombre_para_id_se_aplica_a", id=meta.id),
                             xalign=0, wrap=True))
        entry = Gtk.Entry(text=meta.nombre, placeholder_text=meta.id, max_length=60, activates_default=True)
        box.append(entry)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=tr("common.cancelar"))
        cancel.connect("clicked", lambda *_: dlg.close())
        ok = Gtk.Button(label=tr("catalog_window.renombrar"))
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
        dlg = Gtk.AlertDialog(message=msg, detail=detail, buttons=[tr("common.cancelar"), tr("catalog_window.eliminar_2")], cancel_button=0, default_button=0)

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

    def __init__(self, parent: Gtk.Window, kind: str, start, initial: dict | None = None):
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
        for key, val in (initial or {}).items():                  # valores de partida (p. ej. la mano elegida en la lista)
            w = self._w.get(key)
            if isinstance(w, Gtk.Entry):
                w.set_text(str(val))
        self.lbl_error = Gtk.Label(label="", xalign=0, wrap=True)
        self.lbl_error.add_css_class("error")
        outer.append(self.lbl_error)
        bar = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=tr("common.cancelar"))
        cancel.connect("clicked", lambda *_: self.close())
        go = Gtk.Button(label=tr("catalog_window.taladrar_agarre").rstrip("…") if kind == "agarre" else tr("catalog_window.importar_2"))
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
            ent = Gtk.Entry(hexpand=True, placeholder_text=tr("catalog_window.ruta_del_archivo_stl"))
            btn = Gtk.Button(label=tr("common.elegir"))
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
        dlg = Gtk.FileDialog(title=tr("catalog_window.elegir_el_stl"))
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
