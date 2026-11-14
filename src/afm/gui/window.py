"""Ventana principal GTK4: visor 3D (Gtk.GLArea + moderngl) y barra lateral de planos.

Capa fina sobre `Controller` (toda la lógica está ahí y se prueba sin pantalla). Esta capa solo
traduce eventos de GTK (ratón, teclas, botones, diálogos) y dibuja con el contexto GL del GLArea.
"""
from __future__ import annotations

import os
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ..head import MIN_BASE_IOU, REPLACEMENT_UPS  # noqa: E402
from .catalog_manager import can_retune_joint, find_piece, piece_label  # noqa: E402
from .controller import EXPORT_ONLY_PARAMS, JOINT_PREFIX, TRIM_OP_LABELS, TRIM_SHAPES, Controller, Mode  # noqa: E402
from ..progress import CancelToken, TaskCancelled
from . import param_meta as PM
from .progress_panel import ProgressPanel  # noqa: E402
from .render import SceneRenderer  # noqa: E402
from .session import CatalogNeeded  # noqa: E402
from .steps import STEPS, plane_panel, rail  # noqa: E402
from .ui_consts import HEAD_ADJUST_ATTR, TRIM_OPS as _TRIM_OPS  # noqa: E402
from .widgets import no_scroll_change as _no_scroll_change  # noqa: E402
from .viewcube import ViewCube, interpolate_view, view_angles  # noqa: E402

DRAG_THRESHOLD_PX = 4.0
# flechas -> (derecha, arriba) sobre la cara de contacto vista de frente
_ARROWS = {Gdk.KEY_Left: (-1, 0), Gdk.KEY_Right: (1, 0), Gdk.KEY_Up: (0, 1), Gdk.KEY_Down: (0, -1)}
_MODE_TITLES = {Mode.VIEW: "", Mode.CROWN: "  ·  marcando coronilla", Mode.SYMMETRY: "  ·  plano de simetría",
                Mode.LIMB_PLANE: "  ·  plano de un miembro", Mode.NECK_PLANE: "  ·  plano del cuello", Mode.HEAD_CUT_PLANE: "  ·  corte de la base de la cabeza", Mode.WEAPON_CUT_PLANE: "  ·  corte del mango del arma", Mode.WAIST_PLANE: "  ·  plano de la cintura", Mode.HAND_EXTRACT: "  ·  muñeca: extraer mano", Mode.HAND_PLACE: "  ·  muñeca: pegar mano estándar", Mode.POLYGON: "  ·  dibujando silueta",
                Mode.PIN: "  ·  colocando pasador", Mode.TRIM_PICK: "  ·  colocando recorte",
                Mode.JOINT_PLANE: "  ·  plano de articulación", Mode.JOINT_AXIS: "  ·  colocando eje de bisagra",
                Mode.JOINT_LINES: "  ·  codo con 2 líneas (de frente)", Mode.JOINT_SIDE: "  ·  guías de la articulación (de costado)",
                Mode.FLAT_PLANE: "  ·  plano de corte sin soportes",
                Mode.HEAD_PIN: "  ·  pasador del cuello (de costado)", Mode.WAIST_PIN: "  ·  pasador de la cintura (de costado)"}


TAB_FIGURA, TAB_PLANOS, TAB_CATALOGO, TAB_MIEMBROS, TAB_RECORTES, TAB_PARAMS, TAB_GENERAR = range(7)
CUBE_PX = 120
VIEW_ANIM_S = 0.25            # duración de la animación al elegir una vista en el cubo
_CSS = b"""
.afm-section-title { font-weight: 700; padding: 4px 0; border-bottom: 1px solid alpha(currentColor, 0.2); }
.afm-sub-title { font-weight: 600; opacity: 0.85; padding: 2px 0; }
.afm-help { background: alpha(currentColor, 0.08); border-left: 3px solid alpha(currentColor, 0.45);
            border-radius: 4px; padding: 6px 8px; }
.afm-banner { background: alpha(#f5c211, 0.30); padding: 6px 10px; border-bottom: 1px solid alpha(currentColor, 0.2); }
.afm-modified { font-weight: 700; }
.afm-help-icon { opacity: 0.75; }
.afm-help-text { font-size: 0.9em; opacity: 0.9; }
.afm-task { font-weight: 600; }
.afm-cube-btn { color: #e6e8ec; background: alpha(#000, 0.35); border-radius: 6px; padding: 2px 10px; min-height: 0; }
.afm-cube-btn:hover { background: alpha(#00ae42, 0.8); }
.afm-step-glyph { font-weight: 700; min-width: 1.4em; }
.afm-step-title { font-weight: 600; }
.afm-step-detail { font-size: 0.85em; opacity: 0.85; }
.afm-step-ok { color: #2ec27e; }
.afm-step-warn { color: #e5a50a; }
.afm-step-stale { color: #e66100; }
.afm-step-ready { color: #3584e4; }
.afm-step-bad { color: #e01b24; }
.afm-step-todo, .afm-step-info { opacity: 0.7; }
button:disabled, spinbutton:disabled, checkbutton:disabled, dropdown:disabled, switch:disabled { opacity: 0.65; }
"""


class _SwitchValue:
    """Hace que un `Gtk.Switch` se lea como un spin (`get_value` / `set_value` en 0 o 1): el resto de la ventana sincroniza ambos igual."""

    def __init__(self, switch: Gtk.Switch):
        self.switch = switch

    def get_value(self) -> float:
        return 1.0 if self.switch.get_active() else 0.0

    def set_value(self, v: float) -> None:
        self.switch.set_active(v >= 0.5)




class MainWindow(Gtk.ApplicationWindow):
    _catalog_win = None                                # ventana «Administrar catálogo» abierta (una sola)
    _prop_win = None                                   # ventana «Comparar proporciones con un patrón» abierta (una sola)
    _build_win = None                                  # ventana «Armar figura desde catálogo» abierta (una sola)

    def __init__(self, app: Gtk.Application, ctrl: Controller):
        super().__init__(application=app, title="Action Figure Maker", default_width=1240, default_height=780)
        self.ctrl = ctrl
        self.ctrl.on_change = self._refresh
        self.ctrl.defer_history = True                   # el historial de deshacer se registra tras una pausa (agrupa arrastres y giros de spin)
        self._hist_timer = 0
        self._task_serial = 0                            # cuenta de tareas: el plegado del progreso no pisa una tarea nueva
        # los cálculos largos del controlador (vista previa de la articulación) corren en un hilo y vuelven por acá
        self.ctrl.call_in_main = lambda f, *a: GLib.idle_add(lambda: (f(*a), False)[1])
        self._joint_names: list[str] = []
        self.mgl = None
        self.scene: SceneRenderer | None = None
        self._updating = False
        self._base_tips: dict = {}              # tooltip original de cada control que `_gate` apaga con un motivo
        self._trim_names: list[str] = []
        self._drag = {"button": 0, "last": (0.0, 0.0), "moved": False, "start": (0.0, 0.0), "pan": False}

        self._install_css()
        self._last_mode = self.ctrl.mode
        self.set_titlebar(self._build_header())
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
        paned.set_start_child(self._build_viewer())
        paned.set_end_child(self._build_sidebar())
        paned.set_resize_start_child(True)
        paned.set_shrink_start_child(False)
        paned.set_resize_end_child(False)
        paned.set_shrink_end_child(False)
        paned.set_vexpand(True)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self._build_stale_banner())
        root.append(paned)
        root.append(Gtk.Separator())
        root.append(self._build_status())
        self.set_child(root)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self._refresh()

    # ------------------------------------------------------------ construcción
    @staticmethod
    def _install_css() -> None:
        prov = Gtk.CssProvider()
        prov.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    @staticmethod
    def _icon_button(icon: str, label: str, tip: str, cb) -> Gtk.Button:
        """Botón de la cabecera con ícono y texto."""
        content = Gtk.Box(spacing=6)
        content.append(Gtk.Image.new_from_icon_name(icon))
        content.append(Gtk.Label(label=label))
        b = Gtk.Button(child=content)
        b.set_tooltip_text(tip)
        b.connect("clicked", cb)
        return b

    def _build_header(self) -> Gtk.HeaderBar:
        bar = Gtk.HeaderBar()
        # «Abrir ▾»: STL, proyecto o figura armada solo con piezas de catálogo (antes eran tres botones sueltos)
        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover = Gtk.Popover(child=pop_box)
        self.btn_open = []

        def entry(label: str, tip: str, cb) -> Gtk.Button:
            b = Gtk.Button(label=label, halign=Gtk.Align.FILL)
            b.add_css_class("flat")
            b.get_child().set_halign(Gtk.Align.START)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda *_: (popover.popdown(), cb()))
            pop_box.append(b)
            return b

        self.btn_open.append(entry("Abrir STL…", "Carga el cuerpo de la figura desde un archivo STL.", lambda: self._on_open_stl()))
        self.btn_open.append(entry("Abrir proyecto…", "Retoma un archivo .proyecto guardado.", lambda: self._on_open_project()))
        self.btn_build_cat = entry("Armar desde catálogo…",
                                   "Figura nueva solo con piezas del catálogo, sin abrir un STL ni un proyecto.",
                                   lambda: self._on_catalog_build())
        self.menu_open = Gtk.MenuButton(popover=popover)
        self.menu_open.set_child(self._menu_content("document-open-symbolic", "Abrir"))
        self.menu_open.set_tooltip_text("Abrir un STL, un proyecto o armar una figura desde el catálogo")
        bar.pack_start(self.menu_open)
        self.btn_undo = Gtk.Button(icon_name="edit-undo-symbolic")
        self.btn_undo.connect("clicked", lambda *_: self.ctrl.undo())
        self.btn_redo = Gtk.Button(icon_name="edit-redo-symbolic")
        self.btn_redo.connect("clicked", lambda *_: self.ctrl.redo())
        for b in (self.btn_undo, self.btn_redo):
            bar.pack_start(b)
        self.btn_save = self._icon_button("document-save-symbolic", "Guardar", "Guarda el proyecto en un archivo .proyecto.",
                                          self._on_save_project)
        bar.pack_end(self.btn_save)
        self.btn_export = self._icon_button("document-send-symbolic", "Exportar",
                                            "Genera y guarda las piezas para imprimir.", lambda *_: self._on_export())
        bar.pack_end(self.btn_export)                 # queda a la izquierda de «Guardar»
        return bar

    @staticmethod
    def _menu_content(icon: str, label: str) -> Gtk.Widget:
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Image.new_from_icon_name(icon))
        box.append(Gtk.Label(label=label))
        return box

    def _build_viewer(self) -> Gtk.Widget:
        self.gl = Gtk.GLArea()
        self.gl.set_required_version(3, 3)
        self.gl.set_has_depth_buffer(True)
        self.gl.set_hexpand(True)
        self.gl.set_vexpand(True)
        self.gl.set_size_request(480, 360)
        self.gl.set_focusable(True)
        self.gl.connect("realize", self._on_realize)
        self.gl.connect("unrealize", self._on_unrealize)
        self.gl.connect("render", self._on_render)

        drag = Gtk.GestureDrag()
        drag.set_button(0)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.gl.add_controller(drag)

        hover = Gtk.EventControllerMotion()
        hover.connect("motion", self._on_gl_motion)
        hover.connect("leave", self._on_gl_leave)
        self.gl.add_controller(hover)
        self._hover_pos: tuple[float, float] | None = None
        self._hover_timer = 0

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.gl.add_controller(scroll)

        overlay = Gtk.Overlay()
        overlay.set_child(self.gl)
        overlay.add_overlay(self._build_view_cube())
        return overlay

    def _build_view_cube(self) -> Gtk.Widget:
        """Cubo de vista (arriba a la derecha del visor): clic = ir a esa vista, arrastrar = orbitar."""
        self.view_cube = ViewCube(CUBE_PX)
        self._cube_pointer: tuple[float, float] | None = None
        self._cube_drag = {"last": (0.0, 0.0), "moved": False, "start": (0.0, 0.0)}
        self._anim_id = 0
        self.cube_area = Gtk.DrawingArea(content_width=CUBE_PX, content_height=CUBE_PX,
                                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.cube_area.set_draw_func(self._draw_cube)
        self.cube_area.set_tooltip_text("Cubo de vista: clic en una cara, arista o vértice para mirar desde ahí; arrastrar para orbitar")
        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-begin", self._on_cube_drag_begin)
        drag.connect("drag-update", self._on_cube_drag_update)
        drag.connect("drag-end", self._on_cube_drag_end)
        self.cube_area.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_cube_motion)
        motion.connect("leave", self._on_cube_leave)
        self.cube_area.add_controller(motion)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, halign=Gtk.Align.END, valign=Gtk.Align.START)
        for side in ("end", "top"):
            getattr(box, f"set_margin_{side}")(10)
        box.append(self.cube_area)
        self.btn_frame = Gtk.Button(label="Encuadrar", halign=Gtk.Align.CENTER, tooltip_text="Encuadrar la figura en el visor")
        self.btn_frame.add_css_class("afm-cube-btn")
        self.btn_frame.connect("clicked", lambda *_: self._frame())
        box.append(self.btn_frame)
        return box

    def _build_stale_banner(self) -> Gtk.Widget:
        """Franja superior: la figura generada ya no vale porque cambió el proyecto (aparece sola, sin abrir otra pestaña)."""
        row = Gtk.Box(spacing=8)
        row.add_css_class("afm-banner")
        lbl = Gtk.Label(label="La figura generada quedó desactualizada: cambiaste el proyecto desde la última generación.",
                        xalign=0, hexpand=True, wrap=True)
        self.btn_stale_gen = Gtk.Button(label="Generar de nuevo")
        self.btn_stale_gen.add_css_class("suggested-action")
        self.btn_stale_gen.connect("clicked", lambda *_: self._on_generate())
        row.append(lbl)
        row.append(self.btn_stale_gen)
        self.stale_banner = Gtk.Revealer(child=row, reveal_child=False, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        return self.stale_banner

    def _build_status(self) -> Gtk.Widget:
        """Panel «Estado» al pie de la ventana: alto fijo de 2 líneas de texto (con scroll y tooltip si el mensaje es más largo)."""
        self.status_view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                        left_margin=8, right_margin=8, top_margin=4, bottom_margin=4)
        self._status_buf = self.status_view.get_buffer()
        click = Gtk.GestureClick()                            # clic en el estado = ir al próximo paso pendiente (R1)
        click.connect("released", lambda *_: rail.go_next_pending(self))
        self.status_view.add_controller(click)
        self.status_view.set_cursor_from_name("pointer")
        self._status_buf.create_tag("dim", foreground="gray", style=Pango.Style.ITALIC)
        self._status_text: tuple[str, str] | None = None
        line_h = self.status_view.create_pango_layout("Ag").get_pixel_size()[1]
        height = 2 * line_h + 8 + 2                              # 2 líneas: el mensaje y los pendientes (el texto completo va en el tooltip)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      vexpand=False, propagate_natural_height=False)
        scroller.set_min_content_height(height)
        scroller.set_max_content_height(height)
        scroller.set_size_request(-1, height)
        scroller.set_child(self.status_view)
        self.progress_panel = ProgressPanel()                # porcentaje, tarea en curso, pendientes y tiempo estimado
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.progress_panel)
        body.append(scroller)
        self._task: dict | None = None                       # tarea larga en curso (ver `_run_task`)
        self.spinner = Gtk.Spinner(visible=False)
        self.lbl_task = Gtk.Label(xalign=0)
        self.lbl_task.add_css_class("afm-task")
        head = Gtk.Box(spacing=6)
        head.append(Gtk.Label(label="Estado"))
        head.append(self.spinner)
        head.append(self.lbl_task)
        self.btn_task_cancel = Gtk.Button(label="Cancelar", visible=False)
        self.btn_task_cancel.set_tooltip_text("Corta la tarea cuando termine el paso en curso (un paso largo no se puede interrumpir a medias).")
        self.btn_task_cancel.connect("clicked", lambda *_: self._on_task_cancel())
        head.append(self.btn_task_cancel)
        frame = Gtk.Frame()
        frame.set_label_widget(head)
        for side in ("start", "end", "bottom"):
            getattr(frame, f"set_margin_{side}")(6)
        frame.set_child(body)
        return frame

    def _set_status(self, message: str, problems: str) -> None:
        if self._status_text == (message, problems):          # no se pisa el scroll si no cambió nada
            return
        self._status_text = (message, problems)
        self.status_view.set_tooltip_text(message + (("\n" + problems + "\n(clic: ir al próximo paso pendiente)") if problems else ""))
        buf = self._status_buf
        buf.set_text(message)
        if problems:
            buf.insert_with_tags_by_name(buf.get_end_iter(), "\n" + problems, "dim")

    def _build_sidebar(self) -> Gtk.Widget:
        """Pestañas por tema (como Bambu Studio); cada pestaña tiene secciones y subsecciones plegables (ver `gui/steps`)."""
        self.tabs = Gtk.Notebook(scrollable=True, tab_pos=Gtk.PositionType.TOP)
        self.tabs.set_size_request(400, -1)
        self.tabs.set_show_tabs(False)                # las páginas se eligen en el riel de pasos (etapa 5b); el Notebook sigue siendo el contenedor
        for step in STEPS:
            step.build(self)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side.append(plane_panel.build(self))        # lista de planos fija, siempre a la vista (etapa 4b)
        body = Gtk.Box(spacing=4)
        body.set_vexpand(True)
        body.append(rail.build(self))                # riel de pasos con su estado (etapa 5b)
        self.tabs.set_vexpand(True)
        self.tabs.set_hexpand(True)
        body.append(self.tabs)
        side.append(body)
        return side

    def _joint_spin(self, sec: Gtk.Box, label: str, lo: float, hi: float, step: float, digits: int, on_change) -> Gtk.SpinButton:
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=label, xalign=0, hexpand=True, wrap=True))
        spin = Gtk.SpinButton.new_with_range(lo, hi, step)
        spin.set_digits(digits)
        spin.connect("value-changed", lambda b: None if self._updating or self._jprev_name() is None
                     else on_change(self._jprev_name(), b.get_value()))
        row.append(spin)
        sec.append(row)
        return spin

    def _jprev_name(self) -> str | None:
        i = self.dd_jprev.get_selected()
        return self._joint_names[i] if 0 <= i < len(self._joint_names) else None

    def _on_jprev_choice(self, *_a) -> None:
        if self._updating:
            return
        if self.ctrl.show_joint_preview and self._jprev_name() is not None:
            self.ctrl.set_joint_preview(self._jprev_name(), True)     # cambió de articulación: se recalcula
        else:
            self._refresh()

    def _refresh_joint_preview(self) -> None:
        """Lista de articulaciones, controles de la vista previa y su texto (se llama dentro de `_refresh`)."""
        ctrl = self.ctrl
        names = ctrl.session.preview_names() if ctrl.loaded else []
        if names != self._joint_names:
            keep = self._jprev_name()
            self._joint_names = names
            self.dd_jprev.set_model(Gtk.StringList.new([n.capitalize() for n in names] or ["(ninguna)"]))
            self.dd_jprev.set_selected(names.index(keep) if keep in names else 0)
        if ctrl.show_joint_preview and ctrl._jp["name"] in names:
            self.dd_jprev.set_selected(names.index(ctrl._jp["name"]))
        name = self._jprev_name()
        has = name is not None
        hip = has and ctrl.is_hip_preview(name)               # la cadera se ve girar, no se ajusta acá
        editable = has and ctrl.joint_editable(name)          # la de una pieza de catálogo solo se ve
        for w in (self.chk_jprev, self.spin_jflex):
            w.set_sensitive(has)
        for w in (self.spin_jh, self.spin_jtilt, self.btn_jstraight):
            w.set_sensitive(editable)
        self.chk_jprev.set_active(ctrl.show_joint_preview)
        if has:
            vals = [(self.spin_jflex, ctrl._jp["flex"])]
            if not hip:
                vals += [(self.spin_jh, ctrl.joint_height(name)), (self.spin_jtilt, ctrl.joint_plane_side_tilt(name))]
            for spin, val in vals:
                if abs(spin.get_value() - val) > 1e-9:
                    spin.set_value(val)
        if has:
            note = ("" if editable or hip else
                    "Articulación de la pieza de catálogo: el eje, el plano y el rango son los de la pieza.\n")
            self.lbl_jprev.set_text(note + ctrl.joint_preview_status())
        else:
            self.lbl_jprev.set_text(ctrl.no_joint_text() if ctrl.loaded and ctrl.session.catalog_only()
                                    else "Agregá la rodilla o el codo de un miembro, o una pierna (cadera), en la pestaña Planos.")

    def _tab_page(self, title: str) -> Gtk.Box:
        """Agrega una pestaña con scroll propio y devuelve la caja donde van sus secciones."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(10)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(box)
        self.tabs.append_page(scroller, Gtk.Label(label=title))
        return box

    def _tab_for_mode(self) -> int | None:
        """Pestaña donde están los controles del modo activo (None = no cambiar de pestaña)."""
        mode = self.ctrl.mode
        if mode == Mode.VIEW:
            return None
        if mode == Mode.POLYGON:
            return TAB_RECORTES if self.ctrl.trim_cfg else TAB_MIEMBROS
        return {Mode.CROWN: TAB_FIGURA, Mode.PIN: TAB_MIEMBROS, Mode.TRIM_PICK: TAB_RECORTES,
                Mode.FLAT_PLANE: TAB_GENERAR}.get(mode, TAB_PLANOS)

    @staticmethod
    def _button(label: str, cb) -> Gtk.Button:
        b = Gtk.Button(label=label)
        b.connect("clicked", cb)
        return b

    def _record_history(self) -> bool:
        self._hist_timer = 0
        self.ctrl.record_history()
        self._sync_undo_buttons()
        self._sync_title()                                 # el «*» de cambios sin guardar sigue al historial
        return False

    def _sync_title(self) -> None:
        ctrl = self.ctrl
        title = "Action Figure Maker"
        if ctrl.session.stl_path:
            title += " — " + os.path.basename(ctrl.session.stl_path)
        if ctrl.dirty:
            title = "* " + title                           # cambios sin guardar en el proyecto
        mode_title = "  ·  dibujando recorte" if ctrl.mode == Mode.POLYGON and ctrl.trim_cfg else _MODE_TITLES[ctrl.mode]
        self.set_title(title + mode_title)

    def _sync_undo_buttons(self) -> None:
        ctrl, h = self.ctrl, self.ctrl.history
        why = ctrl.undo_blocked() or (None if h.can_undo else "No hay nada para deshacer.")
        self.btn_undo.set_sensitive(why is None)
        self.btn_undo.set_tooltip_text(why or ("Deshacer: " + h.undo_label if h.undo_label else "Deshacer (Ctrl+Z)"))
        why = ctrl.undo_blocked() or (None if h.can_redo else "No hay nada para rehacer.")
        self.btn_redo.set_sensitive(why is None)
        self.btn_redo.set_tooltip_text(why or ("Rehacer: " + h.redo_label if h.redo_label else "Rehacer (Ctrl+Mayús+Z)"))

    def _param_row(self, key: str, label: str, lo: float, hi: float, step: float, digits: int) -> Gtk.Box:
        """Fila de un parámetro: etiqueta corta (la explicación va al tooltip), spin o interruptor y «restaurar» si se modificó."""
        short, hint = PM.split_label(label)
        if key in EXPORT_ONLY_PARAMS:
            hint = (hint + " " if hint else "") + "Se aplica al exportar: no obliga a generar de nuevo."
        row = Gtk.Box(spacing=6)
        lbl = Gtk.Label(label=short, xalign=0, hexpand=True, wrap=True)
        if hint:
            lbl.set_tooltip_text(hint)
        row.append(lbl)
        reset = Gtk.Button(icon_name="document-revert-symbolic", visible=False, valign=Gtk.Align.CENTER)
        reset.add_css_class("flat")
        reset.connect("clicked", lambda *_, k=key: self.ctrl.reset_param(k))
        row.append(reset)
        if PM.is_boolean(key):
            sw = Gtk.Switch(valign=Gtk.Align.CENTER)
            sw.set_active(self.ctrl.get_param(key) >= 0.5)
            sw.connect("notify::active", lambda w, _p, k=key: None if self._updating else self.ctrl.set_param(k, 1.0 if w.get_active() else 0.0))
            row.append(sw)
            self.param_spins[key] = _SwitchValue(sw)
        else:
            spin = Gtk.SpinButton.new_with_range(lo, hi, step)
            spin.set_digits(digits)
            spin.set_value(self.ctrl.get_param(key))
            spin.connect("value-changed", lambda b, k=key: None if self._updating else self.ctrl.set_param(k, b.get_value()))
            row.append(spin)
            self.param_spins[key] = spin
        self.param_rows[key] = {"row": row, "label": lbl, "reset": reset, "text": label}
        return row

    def _sync_param_marks(self) -> None:
        """Marca en negrita los parámetros modificados, muestra su «restaurar» (con el valor por defecto) y el resumen."""
        ctrl = self.ctrl
        mods = 0
        for key, info in self.param_rows.items():
            mod = ctrl.param_modified(key)
            mods += mod
            info["reset"].set_visible(mod)
            if mod:
                d = ctrl.param_default(key)
                info["reset"].set_tooltip_text("Restaurar el valor por defecto (" + ("sí" if d >= 0.5 else "no") + ")" if PM.is_boolean(key)
                                               else f"Restaurar el valor por defecto ({d:g})")
                info["label"].add_css_class("afm-modified")
            else:
                info["label"].remove_css_class("afm-modified")
        self.lbl_param_modified.set_text(f"{mods} modificado(s) respecto de los valores por defecto." if mods else "Todos con su valor por defecto.")
        self.btn_param_reset_all.set_sensitive(bool(mods) and not ctrl.busy)

    def _on_param_search(self, text: str) -> None:
        """Filtra las filas por palabras (sin mayúsculas ni acentos); mientras se busca, los grupos con resultados se abren."""
        any_shown = False
        for key, info in self.param_rows.items():
            if PM.group_of(key) not in self.param_group_exp:       # la fila de «Sin soportes» (en Generar) no se filtra
                continue
            vis = PM.matches(text, key, info["text"])
            info["row"].set_visible(vis)
            any_shown = any_shown or vis
        searching = bool(text.strip())
        for name, (exp, default) in self.param_group_exp.items():
            has = any(PM.group_of(k) == name and i["row"].get_visible() for k, i in self.param_rows.items())
            exp.set_visible(has)
            exp.set_expanded(has if searching else default)
        self.lbl_param_none.set_visible(searching and not any_shown)

    def _gate(self, widgets, why: str | None) -> None:
        """Habilita los controles si `why` es None; si no, los apaga y deja el motivo en el tooltip.

        Al habilitarse vuelven a su tooltip original (el primero que tuvieron), así el motivo no queda pegado.
        """
        if not isinstance(widgets, (list, tuple)):
            widgets = (widgets,)
        for w in widgets:
            if w not in self._base_tips:
                self._base_tips[w] = w.get_tooltip_text()
            w.set_sensitive(why is None)
            w.set_tooltip_text(why if why is not None else self._base_tips[w])

    # --------------------------------------------------------------- refresco
    def _refresh(self) -> None:
        ctrl = self.ctrl
        self._updating = True
        try:
            for key, spin in self.param_spins.items():
                if abs(spin.get_value() - ctrl.get_param(key)) > 1e-9:
                    spin.set_value(ctrl.get_param(key))
            self._sync_param_marks()
            # la altura se sincroniza solo cuando cambia en el proyecto (abrir proyecto, aplicar): lo tipeado no se pisa
            ref = ctrl.session.project.params.ref_height
            if abs(ref - self._shown_height) > 1e-9:
                self._shown_height = ref
                self.spin_height.set_value(ref)
            probs = ctrl.problems()
            self._set_status(ctrl.message, "Pendiente: " + "; ".join(probs) if probs else "Siluetas completas.")
            if ctrl.mode != self._last_mode:                 # al empezar una tarea, mostrar la pestaña con sus controles
                self._last_mode = ctrl.mode
                tab = self._tab_for_mode()
                if tab is not None:
                    self.tabs.set_current_page(tab)
            self._sync_title()
            loaded = ctrl.loaded
            gate = self._gate
            self.stale_banner.set_reveal_child(ctrl.outcome_stale)
            self.btn_stale_gen.set_sensitive(ctrl.blocked("generate") is None)
            self._sync_undo_buttons()
            if not self._hist_timer:                       # registra el estado una vez que pasó la ráfaga de cambios
                self._hist_timer = GLib.timeout_add(300, self._record_history)
            gate((self.btn_crown, self.btn_sym, self.btn_sym_auto, self.dd_front, self.chk_side,
                  self.chk_over, self.spin_over), ctrl.blocked("stl"))
            gate(self.btn_save, ctrl.blocked("save"))
            gate(self.btn_grip, ctrl.blocked("grip"))
            gate(self.btn_grip_hand, ctrl.blocked("grip_hand"))
            gate(self.btn_grip_plate, ctrl.blocked("grip_plate"))
            txt = ctrl.grip_summary()
            if self.lbl_grip.get_text() != txt:           # no pisar la selección del texto en cada refresco
                self.lbl_grip.set_text(txt)
            ws = ctrl.weapon
            has_w = ws is not None and ws.loaded
            free = has_w and not ctrl.busy
            gate((self.spin_weapon_len, self.spin_weapon_scale, self.btn_weapon_cut, self.chk_weapon_roll, self.ent_weapon_id,
                  self.chk_weapon_ver), ctrl.blocked("weapon"))
            self.btn_weapon_open.set_sensitive(not ctrl.busy)
            self.btn_weapon_flip.set_sensitive(bool(free and ws.spec.cut_points))
            self.spin_weapon_roll.set_sensitive(bool(free and not self.chk_weapon_roll.get_active()))
            self.btn_weapon_save.set_sensitive(bool(free and ws.build is not None))
            self.btn_weapon_close.set_sensitive(bool(ctrl.weapon_active))
            if has_w:
                if abs(self.spin_weapon_len.get_value() - ws.length_mm) > 0.05:
                    self.spin_weapon_len.set_value(ws.length_mm)
                if abs(self.spin_weapon_scale.get_value() - ws.scale) > 1e-4 * max(1.0, ws.scale):
                    self.spin_weapon_scale.set_value(ws.scale)
            txt = ctrl.weapon_summary()
            if self.lbl_weapon.get_text() != txt:
                self.lbl_weapon.set_text(txt)
            self._refill_project_accessories()
            if self.chk_grip_show.get_active() != ctrl.grip_show:
                self.chk_grip_show.set_active(ctrl.grip_show)
            for key, spin in self.grip_spins.items():
                if abs(spin.get_value() - getattr(ctrl.grip_spec, key)) > 1e-9:
                    spin.set_value(getattr(ctrl.grip_spec, key))
            gate(self.btn_open, ctrl.blocked("open"))      # no se cambia de archivo en medio de una tarea
            has_sym = ctrl.session.project.symmetry is not None
            gate((self.btn_arm, self.btn_leg, self.btn_neck, self.btn_waist, self.btn_hand, self.btn_hand_stl),
                 ctrl.blocked("sym"))
            gate((self.btn_hand_invert, self.btn_hand_save, self.btn_hand_drop), ctrl.blocked("hand_extract"))
            placed = ctrl.session.project.hand if loaded else None
            gate((self.btn_hand_flip, self.btn_hand_orig, self.spin_hand_yaw, self.chk_hand_swivel), ctrl.blocked("hand_placed"))
            self.chk_hand_swivel.set_active(placed is not None and placed.join == "giro")
            if placed is not None and abs(self.spin_hand_yaw.get_value() - placed.yaw) > 1e-9:
                self.spin_hand_yaw.set_value(placed.yaw)
            fit = ctrl.hand_fit() if placed is not None else None
            self.lbl_hand_fit.set_text("" if fit is None else
                                       f"Muñeca del modelo Ø{2 * fit['info']['radio_brazo_mm']:.1f} mm · de la mano "
                                       f"Ø{2 * fit['info']['radio_mano_mm']:.1f} mm (escalón {fit['info']['escalon_mm']:.1f} mm)")
            self._sync_catalog(loaded)
            gate((self.btn_knee, self.btn_elbow, self.btn_elbow_lines), ctrl.blocked("limbs"))
            head = ctrl.session.project.head
            gate((self.btn_head_stl, self.btn_head_pin), ctrl.blocked("head"))
            gate(self.btn_head_pin_reset, ctrl.blocked("head_pin_reset"))
            waist = ctrl.session.project.waist if loaded else None
            gate(self.btn_waist_pin, ctrl.blocked("waist"))
            gate(self.btn_waist_pin_reset, ctrl.blocked("waist_pin_reset"))
            joint_pin = ctrl.mode in (Mode.HEAD_PIN, Mode.WAIST_PIN)
            gate((self.btn_head_snap, self.btn_head_heat, self.btn_waist_snap, self.btn_waist_heat),
                 None if joint_pin else "Se activa mientras colocás el pasador del cuello o de la cintura.")
            self._sync_joint_aim_ui(loaded)
            rep = loaded and head is not None and head.replacement_path is not None
            gate((self.btn_head_orig, self.dd_head_up, self.btn_head_cut, *self.head_spins.values()), ctrl.blocked("head_replacement"))
            gate(self.btn_head_uncut, ctrl.blocked("head_replacement_cut"))
            if rep:
                self.dd_head_up.set_selected(REPLACEMENT_UPS.index(head.replacement_up))
                for key, spin in self.head_spins.items():
                    val = getattr(head, HEAD_ADJUST_ATTR[key])
                    if abs(spin.get_value() - val) > 1e-9:
                        spin.set_value(val)
                prev = ctrl.head_preview()
                self.lbl_head_fit.set_text("" if prev is None else
                                           f"Base coincide {100 * prev[1]['coincidencia_base']:.0f} % "
                                           f"(conviene ≥ {100 * MIN_BASE_IOU:.0f} %) · alto {prev[1]['alto_mm']:.1f} mm")
            else:
                self.lbl_head_fit.set_text("")
            off_limb = ctrl.offset_limb() if loaded else None
            self.spin_cut.set_sensitive(off_limb is not None)
            if off_limb is not None:
                self.spin_cut.set_value(ctrl.session.limb_offset(off_limb))
            self.chk_cut_aim.set_sensitive(off_limb is not None)
            if self.chk_cut_aim.get_active() != ctrl.cut_aim_visible:
                self.chk_cut_aim.set_active(ctrl.cut_aim_visible)
            self.lbl_cut_aim.set_text(ctrl.cut_aim_text() if off_limb is not None else "")
            self._sync_cut_ref_widgets(off_limb)
            box = ctrl.cut_aim_box() if off_limb is not None else None
            self.cut_area.set_visible(box is not None)
            self.cut_area.queue_draw()
            self.btn_cut_snap.set_sensitive(box is not None and not ctrl.busy)
            why = ctrl.cut_seated_blocked() if box is not None else None        # el motivo va al tooltip en vez de avisar al apretar
            self.btn_cut_seated.set_sensitive(box is not None and not ctrl.busy and why is None)
            self.btn_cut_seated.set_tooltip_text(why or self._cut_seated_tip)
            self.chk_side.set_sensitive(loaded and has_sym)
            self.chk_side.set_active(ctrl.one_side)
            for group, chk in self.chk_show.items():
                chk.set_active(ctrl.group_visible(group))
            self.chk_over.set_active(ctrl.show_overhangs)
            self.chk_mirror.set_sensitive(loaded and has_sym)
            self.chk_mirror.set_active(ctrl.mirror_live)
            self.chk_pose.set_sensitive(ctrl.session.outcome is not None)
            self.chk_pose.set_active(ctrl.pose_active())
            self.spin_pose.set_sensitive(ctrl.pose_active())
            self.spin_pose.set_value(ctrl._pose_angle())
            gate(self.btn_trim_add, ctrl.blocked("sym"))
            self.sidebar_box.set_sensitive(not ctrl.busy)
            self.rail_box.set_sensitive(not ctrl.busy)
            rail.refresh(self)
            gate(self.btn_generate, ctrl.blocked("generate"))
            gate((self.btn_export, self.btn_flat_plane, self.btn_flat_click), ctrl.blocked("outcome"))
            gate((self.btn_close_poly, self.btn_propose, self.btn_outline, self.btn_clear_poly, self.btn_end_poly),
                 ctrl.blocked("drawing"))
            gate(self.btn_raise, ctrl.blocked("raise"))
            gate((self.btn_pin_reset, self.btn_pin_face), ctrl.blocked("pin"))
            gate((self.btn_pin_snap, self.btn_pin_heat, self.btn_pin_end), ctrl.blocked("pin_any"))     # también en el cuello / la cintura
            self.aim_area.set_visible(ctrl.aim_active())
            self.aim_area.queue_draw()
            self.spin_over.set_value(ctrl.overhang_angle)
            self.chk_fill_hip.set_active(ctrl.session.project.params.flat_fill_hip)
            self.chk_fill_hip.set_sensitive(self.chk_flat.get_active())
            self.dd_front.set_selected(0 if ctrl.session.project.front == "+Y" else 1)
            self._rebuild_plane_list()
            self._rebuild_trim_ui()
            self._refresh_joint_preview()
        finally:
            self._updating = False
        self.gl.queue_render()

    def _rebuild_trim_ui(self) -> None:
        """Pieza elegible (torso + miembros) y lista de recortes con editar / cambiar operación / quitar."""
        names = self.ctrl.trim_targets() if self.ctrl.loaded else ["torso"]
        if names != self._trim_names:
            keep = self._trim_names[self.dd_trim_target.get_selected()] if self.dd_trim_target.get_selected() < len(self._trim_names) else None
            self._trim_names = names
            self.dd_trim_target.set_model(Gtk.StringList.new([n.capitalize() for n in names]))
            self.dd_trim_target.set_selected(names.index(keep) if keep in names else 0)
        while (child := self.trims_box.get_first_child()) is not None:
            self.trims_box.remove(child)
        for e in self.ctrl.trim_entries():
            row = Gtk.Box(spacing=6)
            for side in ("start", "end", "top", "bottom"):
                getattr(row, f"set_margin_{side}")(4)
            row.append(Gtk.Label(label=e["label"], xalign=0, hexpand=True, wrap=True))
            i = e["index"]
            if e["shape"] == "polygon":
                ed = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Editar los vértices del polígono")
                ed.add_css_class("flat")
                ed.connect("clicked", lambda _b, k=i: self.ctrl.start_trim(index=k))
                row.append(ed)
            other = "union" if e["op"] == "subtract" else "subtract"
            tg = Gtk.Button(label="→ " + TRIM_OP_LABELS[other], tooltip_text="Cambiar entre restar y unir")
            tg.add_css_class("flat")
            tg.connect("clicked", lambda _b, k=i, o=other: self.ctrl.set_trim_op(k, o))
            row.append(tg)
            rm = Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text="Quitar el recorte")
            rm.add_css_class("flat")
            rm.connect("clicked", lambda _b, k=i: self.ctrl.remove_trim(k))
            row.append(rm)
            self.trims_box.append(row)

    def _on_trim_add(self) -> None:
        names = self.ctrl.trim_targets()
        sel = self.dd_trim_target.get_selected()
        target = names[sel] if sel < len(names) else "torso"
        shape = TRIM_SHAPES[self.dd_trim_shape.get_selected()]
        op = _TRIM_OPS[self.dd_trim_op.get_selected()]
        length = self.spin_trim_len.get_value()
        if shape == "polygon":
            self.ctrl.start_trim(target, op, length)
        else:
            self.ctrl.start_trim_pick(target, shape, self.spin_trim_size.get_value(), op, length)

    def _on_cut_ref_figure(self, dd, _pspec=None) -> None:
        if self._updating or not self._cut_ref_figs:
            return
        i = dd.get_selected()
        if 0 <= i < len(self._cut_ref_figs):
            self.ctrl.set_cut_ref_figure(self._cut_ref_figs[i])

    def _sync_cut_ref_widgets(self, off_limb) -> None:
        """Desplegable de figuras de referencia, casilla de la guía, lectura «tu corte / el de la referencia» y el botón de igualar."""
        ctrl = self.ctrl
        figs = ctrl.cut_ref_figures()
        if figs != self._cut_ref_figs:
            self._cut_ref_figs = figs
            self.dd_cut_ref.set_model(Gtk.StringList.new(figs or ["(sin referencia)"]))
        cur = ctrl.cut_ref_figure()
        if cur in figs and self.dd_cut_ref.get_selected() != figs.index(cur):
            self.dd_cut_ref.set_selected(figs.index(cur))
        self.dd_cut_ref.set_sensitive(bool(figs))
        if self.chk_cut_ref.get_active() != ctrl.cut_ref_visible:
            self.chk_cut_ref.set_active(ctrl.cut_ref_visible)
        self.chk_cut_ref.set_sensitive(bool(figs))
        if self.chk_cut_ghost.get_active() != ctrl.cut_ghost_visible:
            self.chk_cut_ghost.set_active(ctrl.cut_ghost_visible)
        self.chk_cut_ghost.set_sensitive(bool(figs))
        self.lbl_cut_ref.set_text(ctrl.cut_ref_text())
        self.btn_cut_match.set_sensitive(off_limb is not None and bool(ctrl.cut_ref_rows()))

    def _on_cut_offset(self, spin) -> None:
        if self._updating:
            return
        name = self.ctrl.offset_limb()
        if name is not None and abs(spin.get_value() - (self.ctrl.session.limb_offset(name) or 0.0)) > 1e-9:
            self.ctrl.set_limb_offset(name, spin.get_value())

    def _rebuild_plane_list(self) -> None:
        while (child := self.planes_box.get_first_child()) is not None:
            self.planes_box.remove(child)
        cuts = {r["id"]: r for r in self.ctrl.cut_rows()}
        entries = self.ctrl.plane_entries()
        self.lbl_planes_empty.set_visible(not entries)
        self.planes_scroller.set_visible(bool(entries))
        for e in entries:
            row = Gtk.Box(spacing=6)
            for side in ("start", "end", "top", "bottom"):
                getattr(row, f"set_margin_{side}")(4)
            vis = Gtk.CheckButton(active=e.visible, tooltip_text="Mostrar / ocultar")
            vis.connect("toggled", lambda _b, pid=e.id: None if self._updating else self.ctrl.toggle_plane(pid))
            row.append(vis)
            cut = cuts.get(e.id)
            lab = Gtk.Label(label=self.ctrl.cut_row_text(cut) if cut else e.label, xalign=0, hexpand=True, wrap=True)
            row.append(lab)
            if cut and cut["dif_mm"] is not None and abs(cut["dif_mm"]) >= 0.05:
                al = Gtk.Button(label="Alinear", tooltip_text="Mover este corte a la misma distancia del plano de simetría "
                                "que el de la pieza de catálogo elegida")
                al.add_css_class("flat")
                al.connect("clicked", lambda _b, pid=e.id: self.ctrl.align_cut_to_line(pid))
                row.append(al)
            if e.kind in ("arm", "leg"):
                ed = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Dibujar / editar la silueta")
                ed.add_css_class("flat")
                ed.connect("clicked", lambda _b, pid=e.id: self.ctrl.start_polygon(pid))
                row.append(ed)
                cr = Gtk.Button(label="2.º", tooltip_text="Dibujar / editar el 2.º corte (silueta sobre el plano "
                                "perpendicular, normalmente de frente). El miembro es lo que queda dentro de las "
                                "dos siluetas")
                cr.add_css_class("flat")
                cr.connect("clicked", lambda _b, pid=e.id: self.ctrl.start_polygon(pid, cross=True))
                row.append(cr)
                if self.ctrl.session.limb(e.id) is not None and self.ctrl.session.limb(e.id).cross is not None:
                    cx = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text="Quitar el 2.º corte")
                    cx.add_css_class("flat")
                    cx.connect("clicked", lambda _b, pid=e.id: self.ctrl.remove_cross(pid))
                    row.append(cx)
                pn = Gtk.Button(icon_name="find-location-symbolic",
                                tooltip_text="Colocar el pasador sobre la cara de corte")
                pn.add_css_class("flat")
                pn.connect("clicked", lambda _b, pid=e.id: self.ctrl.start_pin(pid))
                row.append(pn)
            if e.kind == "joint":
                jn = e.id[len(JOINT_PREFIX):]
                ax = Gtk.Button(icon_name="find-location-symbolic", tooltip_text="Colocar el eje de la bisagra "
                                "(clic sobre el costado de la figura)")
                ax.add_css_class("flat")
                ax.connect("clicked", lambda _b, n=jn: self.ctrl.start_joint_axis(n))
                row.append(ax)
                if jn in self.ctrl.joint_guides:
                    sd = Gtk.Button(icon_name="object-flip-horizontal-symbolic",
                                    tooltip_text="Ajustar las guías de las 2 líneas desde el costado (4 clics en la vista "
                                                 "«Costado»); recalcula el corte y el eje")
                    sd.add_css_class("flat")
                    sd.connect("clicked", lambda _b, n=jn: self.ctrl.start_joint_side(n))
                    row.append(sd)
                lo, hi = self.ctrl.joint_limits(jn)
                for tip, val, lo_b, hi_b, cb in (
                        ("Flexión mínima (° ; negativa = hiperextensión)", lo, -45.0, 149.0,
                         lambda b, n=jn: self.ctrl.set_joint_limit(n, "lo", b.get_value())),
                        ("Flexión máxima (°)", hi, -44.0, 150.0,
                         lambda b, n=jn: self.ctrl.set_joint_limit(n, "hi", b.get_value())),
                        ("Inclinación del eje, vista de frente (° ; positiva = sube hacia +X)", self.ctrl.joint_tilt(jn), -45.0, 45.0,
                         lambda b, n=jn: self.ctrl.set_joint_tilt(n, b.get_value()))):
                    sp = Gtk.SpinButton.new_with_range(lo_b, hi_b, 1.0)
                    sp.set_value(val)
                    sp.set_tooltip_text(tip)
                    sp.connect("value-changed", lambda b, cb=cb: None if self._updating else cb(b))
                    row.append(sp)
            rm = Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text="Quitar plano")
            rm.add_css_class("flat")
            rm.connect("clicked", lambda _b, pid=e.id: self.ctrl.remove_plane(pid))
            row.append(rm)
            self.planes_box.append(row)

    # --------------------------------------------------------------------- GL
    def _on_realize(self, area: Gtk.GLArea) -> None:
        area.make_current()
        if area.get_error() is not None:
            return
        import moderngl

        self.mgl = moderngl.create_context()
        self.scene = SceneRenderer(self.mgl)
        self.ctrl.scene_dirty = True

    def _on_unrealize(self, area: Gtk.GLArea) -> None:
        area.make_current()
        if self.scene is not None:
            self.scene.clear()
        self.scene, self.mgl = None, None

    def _on_render(self, area: Gtk.GLArea, _gl_context) -> bool:
        if self.scene is None:
            return False
        scale = area.get_scale_factor()
        w, h = area.get_width() * scale, area.get_height() * scale
        if w <= 0 or h <= 0:
            return True
        if self.ctrl.scene_dirty:
            self.ctrl.sync(self.scene)
            self.ctrl.scene_dirty = False
        self.scene.draw(self.ctrl.camera, w, h, self.mgl.detect_framebuffer())
        self.cube_area.queue_draw()          # el cubo sigue a la cámara
        return True

    # ------------------------------------------------------------------ ratón
    def _on_drag_begin(self, g: Gtk.GestureDrag, x: float, y: float) -> None:
        self.gl.grab_focus()
        shift = bool(g.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        self._drag = {"button": g.get_current_button(), "last": (0.0, 0.0), "moved": False,
                      "start": (x, y), "pan": shift}

    def _on_drag_update(self, _g, dx: float, dy: float) -> None:
        d = self._drag
        if not d["moved"] and (dx * dx + dy * dy) ** 0.5 < DRAG_THRESHOLD_PX:
            return
        d["moved"] = True
        self.ctrl.clear_hover()
        ddx, ddy = dx - d["last"][0], dy - d["last"][1]
        d["last"] = (dx, dy)
        cam = self.ctrl.camera
        if d["button"] == 2 or d["pan"] or d["button"] == 3:
            cam.pan(ddx, ddy, max(self.gl.get_height(), 1))
        else:
            cam.orbit(ddx, ddy)
        self.gl.queue_render()

    def _build_joint_aim(self, sec: Gtk.Box, lugar: str) -> None:
        """Mira del plano del cuello / de la cintura: casilla, texto, botones para mirarlo, correrlo (±0,5 mm) y el imán, y el recuadro
        de la sección. La cinta se pinta en el visor mientras esta sección es la que se mira (`Controller.joint_focus`)."""
        w = {}
        name = "del cuello" if lugar == "cuello" else "de la cintura"

        def focus(fn):
            def go(*_):
                self.ctrl.set_joint_focus(lugar)
                fn()
                self._refresh()
            return go

        w["chk"] = Gtk.CheckButton(label=f"Mira del plano {name} (cinta de color y sección)")
        w["chk"].set_tooltip_text("Con un catálogo abierto: pinta la sección del plano con el color de cuánto del catálogo la acepta (verde = casi "
                                  "todo, amarillo = una parte, rojo = nada) y una cinta de puntos a lo largo de la normal del plano, ±4 mm cada "
                                  "0,5 mm; el punto blanco es la mejor posición. Informativo, no una garantía.")
        w["chk"].connect("toggled", lambda b: None if self._updating else self.ctrl.toggle_cut_aim())
        sec.append(w["chk"])
        w["lbl"] = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
        sec.append(w["lbl"])
        row = Gtk.Box(spacing=6)
        w["look"] = self._button(f"Mirar este plano", focus(lambda: None))
        w["minus"] = self._button("−0,5 mm", focus(lambda: self.ctrl.shift_joint_plane(lugar, -0.5)))
        w["plus"] = self._button("+0,5 mm", focus(lambda: self.ctrl.shift_joint_plane(lugar, 0.5)))
        w["snap"] = self._button("Imán del plano", focus(lambda: self.ctrl.snap_joint()))
        w["snap"].set_tooltip_text("Desliza el plano a la mejor posición de la cinta (±4 mm sobre su normal). Descarta el pasador elegido: hay "
                                   "que volver a revisarlo. (La tecla M sigue moviendo solo el corte lateral de un miembro.)")
        for b in (w["look"], w["minus"], w["plus"], w["snap"]):
            row.append(b)
        sec.append(row)
        w["area"] = Gtk.DrawingArea(content_width=300, content_height=220, visible=False)
        w["area"].set_draw_func(lambda _a, cr, wd, ht: self._draw_joint_aim(cr, wd, ht, lugar))
        sec.append(w["area"])
        exp = sec.get_parent()
        if isinstance(exp, Gtk.Expander):                      # abrir la sección elige el plano que se mira
            exp.connect("notify::expanded", lambda e, _p: (self.ctrl.set_joint_focus(lugar) if e.get_expanded()
                                                           else self.ctrl.joint_focus == lugar and self.ctrl.set_joint_focus(None),
                                                           self._refresh()))
        self.joint_aim_w = getattr(self, "joint_aim_w", {})
        self.joint_aim_w[lugar] = w

    def _sync_joint_aim_ui(self, loaded: bool) -> None:
        ctrl = self.ctrl
        for lugar, w in getattr(self, "joint_aim_w", {}).items():
            spec = (ctrl.session.project.head if lugar == "cuello" else ctrl.session.project.waist) if loaded else None
            ok = spec is not None and not ctrl.busy
            for k in ("look", "minus", "plus", "snap", "chk"):
                w[k].set_sensitive(ok)
            if w["chk"].get_active() != ctrl.cut_aim_visible:
                w["chk"].set_active(ctrl.cut_aim_visible)
            mine = ok and ctrl.joint_focus == lugar
            w["lbl"].set_text(ctrl.joint_aim_text() if mine else "")
            box = ctrl.joint_aim_box() if mine else None
            w["area"].set_visible(box is not None)
            w["area"].queue_draw()
            w["snap"].set_sensitive(box is not None and ok)

    def _draw_joint_aim(self, cr, w: int, h: int, lugar: str) -> None:
        from ..pin_aid_view import draw_aim_box

        if self.ctrl.joint_focus != lugar:
            return
        data = self.ctrl.joint_aim_box()
        if data is not None:
            draw_aim_box(cr, w, h, data)

    def _draw_cut_aim(self, _area, cr, w: int, h: int) -> None:
        """El recuadro de la sección del corte actual con las caras del catálogo encima."""
        from ..pin_aid_view import draw_aim_box

        data = self.ctrl.cut_aim_box()
        if data is not None:
            draw_aim_box(cr, w, h, data)

    def _on_cut_seated(self, *_) -> None:
        job = self.ctrl.cut_seated_job()
        if job is None:
            return
        key, run = job
        self._run_task("Midiendo el choque en pose sentada…", run, lambda res, err: self.ctrl.finish_cut_seated(key, res, err))

    def _draw_aim(self, _area, cr, w: int, h: int) -> None:
        """El recuadro de la mira: lo que lee bajo el puntero o, si no hay lectura, en el centro actual del pasador."""
        from ..pin_aid_view import draw_aim_box

        ctrl = self.ctrl
        if not ctrl.aim_active():
            return
        data = ctrl.aim_box()
        if data is not None:
            draw_aim_box(cr, w, h, data)

    def _on_gl_motion(self, _c, x: float, y: float) -> None:
        """Marca el vértice cercano al puntero (modos de elegir puntos). Se agrupan los eventos: la búsqueda traza un rayo."""
        self._hover_pos = (x, y)
        if (self.ctrl.hover_active() or self.ctrl.aim_active()) and not self._hover_timer:
            self._hover_timer = GLib.timeout_add(30, self._hover_tick)

    def _hover_tick(self) -> bool:
        self._hover_timer = 0
        pos = self._hover_pos
        if pos is not None and not self._drag["moved"] and self.ctrl.update_hover(
                pos[0], pos[1], max(self.gl.get_width(), 1), max(self.gl.get_height(), 1)):
            self.gl.queue_render()
            if self.ctrl.aim_active():                  # la mira: solo la barra de estado y el recuadro, no toda la interfaz
                probs = self.ctrl.problems()
                self._set_status(self.ctrl.message, "Pendiente: " + "; ".join(probs) if probs else "Siluetas completas.")
                self.aim_area.queue_draw()
        return False

    def _on_gl_leave(self, _c) -> None:
        self._hover_pos = None
        if self.ctrl.clear_hover():
            self.gl.queue_render()

    def _on_drag_end(self, _g, _dx: float, _dy: float) -> None:
        d = self._drag
        moved, d["moved"] = d["moved"], False          # el hover vuelve a andar al terminar el arrastre
        if moved or d["button"] != 1:
            return
        x, y = d["start"]
        self.ctrl.click(x, y, max(self.gl.get_width(), 1), max(self.gl.get_height(), 1))

    def _on_scroll(self, _c, _dx: float, dy: float) -> bool:
        self.ctrl.camera.zoom(1.1 ** dy)
        self.gl.queue_render()
        return True

    def _on_key(self, _c, keyval: int, _code: int, state) -> bool:
        if state & Gdk.ModifierType.CONTROL_MASK:        # Ctrl+Z deshace; Ctrl+Mayús+Z o Ctrl+Y rehacen
            shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
            if keyval in (Gdk.KEY_z, Gdk.KEY_Z) and not shift:
                self.ctrl.undo()
                return True
            if keyval in (Gdk.KEY_y, Gdk.KEY_Y) or (keyval in (Gdk.KEY_z, Gdk.KEY_Z) and shift):
                self.ctrl.redo()
                return True
        if keyval == Gdk.KEY_Escape:
            self.ctrl.cancel()
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete):
            self.ctrl.undo_point()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.ctrl.mode == Mode.POLYGON:
            self.ctrl.close_polygon()
            return True
        if self.ctrl.mode == Mode.FLAT_PLANE and keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            self.ctrl.nudge_flat(1 if keyval == Gdk.KEY_Up else -1, big=bool(state & Gdk.ModifierType.SHIFT_MASK))
            return True
        if self.ctrl.mode == Mode.WAIST_PIN and keyval in _ARROWS:
            du, dv = _ARROWS[keyval]
            self.ctrl.nudge_waist_pin(du, dv, big=bool(state & Gdk.ModifierType.SHIFT_MASK))
            return True
        if self.ctrl.mode == Mode.HEAD_PIN and keyval in _ARROWS:
            du, dv = _ARROWS[keyval]
            self.ctrl.nudge_head_pin(du, dv, big=bool(state & Gdk.ModifierType.SHIFT_MASK))
            return True
        pin_mode = self.ctrl.mode in (Mode.PIN, Mode.HEAD_PIN, Mode.WAIST_PIN)
        if pin_mode and keyval in (Gdk.KEY_m, Gdk.KEY_M):
            self.ctrl.snap_pin()
            return True
        if not pin_mode and keyval in (Gdk.KEY_m, Gdk.KEY_M) and self.ctrl.snap_cut(quiet=True):
            return True
        if pin_mode and keyval in (Gdk.KEY_h, Gdk.KEY_H):
            self.ctrl.toggle_heat()
            return True
        if self.ctrl.mode == Mode.PIN and keyval in _ARROWS:
            du, dv = _ARROWS[keyval]
            self.ctrl.nudge_pin(du, dv, big=bool(state & Gdk.ModifierType.SHIFT_MASK))
            return True
        return False

    # ------------------------------------------------------------------ acciones
    # ------------------------------------------------------------ cubo de vista
    def _cube_rot(self):
        return self.ctrl.camera.view()[:3, :3]

    def _draw_cube(self, _area, cr, _w, _h) -> None:
        hover = self.view_cube.hit(self._cube_rot(), *self._cube_pointer) if self._cube_pointer else None
        self.view_cube.draw(cr, self._cube_rot(), hover)

    def _on_cube_motion(self, _c, x: float, y: float) -> None:
        self._cube_pointer = (x, y)
        self.cube_area.queue_draw()

    def _on_cube_leave(self, _c) -> None:
        self._cube_pointer = None
        self.cube_area.queue_draw()

    def _on_cube_drag_begin(self, _g, x: float, y: float) -> None:
        self._cancel_view_anim()
        self._cube_drag = {"last": (0.0, 0.0), "moved": False, "start": (x, y)}

    def _on_cube_drag_update(self, _g, dx: float, dy: float) -> None:
        d = self._cube_drag
        if not d["moved"] and (dx * dx + dy * dy) ** 0.5 < DRAG_THRESHOLD_PX:
            return
        d["moved"] = True
        ddx, ddy = dx - d["last"][0], dy - d["last"][1]
        d["last"] = (dx, dy)
        self.ctrl.camera.orbit(ddx, ddy)
        self.gl.queue_render()
        self.cube_area.queue_draw()

    def _on_cube_drag_end(self, _g, _dx: float, _dy: float) -> None:
        if self._cube_drag["moved"]:
            return
        hit = self.view_cube.hit(self._cube_rot(), *self._cube_drag["start"])
        if hit is not None:
            self._animate_view(view_angles(hit.direction()))

    def _cancel_view_anim(self) -> None:
        if self._anim_id:
            self.cube_area.remove_tick_callback(self._anim_id)
            self._anim_id = 0

    def _animate_view(self, target: tuple[float, float]) -> None:
        """Lleva la cámara a (yaw, pitch) con una transición corta."""
        self._cancel_view_anim()
        cam = self.ctrl.camera
        start = (cam.yaw, cam.pitch)
        t0 = GLib.get_monotonic_time()

        def tick(widget, _clock) -> bool:
            t = (GLib.get_monotonic_time() - t0) / 1e6 / VIEW_ANIM_S
            cam.yaw, cam.pitch = interpolate_view(start, target, t)
            self.gl.queue_render()
            widget.queue_draw()
            if t >= 1.0:
                self._anim_id = 0
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self._anim_id = self.cube_area.add_tick_callback(tick)

    # ------------------------------------------------------------------ acciones
    def _on_fill_hip_toggled(self, btn) -> None:
        """Con el clic de cadera encendido el relleno puede cortar las lengüetas: se pide confirmación antes de activarlo."""
        if not btn.get_active() or not self.ctrl.session.project.params.hip_detent:
            self.ctrl.set_flat_fill_hip(btn.get_active())
            return

        def answered(dlg, result) -> None:
            try:
                ok = dlg.choose_finish(result) == 1
            except Exception:                       # cerrado sin elegir
                ok = False
            if ok:
                self.ctrl.set_flat_fill_hip(True)
            else:
                self._updating = True
                try:
                    btn.set_active(False)
                finally:
                    self._updating = False

        dlg = Gtk.AlertDialog(message="La cadera tiene mecanismo de clic",
                              detail="Rellenar los muslos talla la pelvis alrededor del muslo alargado y puede cortar las lengüetas del clic: "
                                     "el clic de la cadera podría dejar de funcionar. Al exportar se informa cuánto se pierde. ¿Rellenar igual?",
                              buttons=["Cancelar", "Rellenar igual"], cancel_button=0, default_button=0)
        dlg.choose(self, None, answered)

    def _on_flat_toggled(self, on: bool) -> None:
        self.chk_fill_hip.set_sensitive(on)
        self.ctrl.set_flat_print(on)
        self.gl.queue_render()

    def _on_flat_click(self) -> None:
        self.ctrl.apply_flat_click_angle()
        self.gl.queue_render()

    def _on_flat_plane(self) -> None:
        self.ctrl.start_flat_plane()
        self.gl.queue_render()

    def _pin_face_view(self) -> None:
        self.ctrl.face_view()
        self.gl.queue_render()

    def _frame(self) -> None:
        if self.ctrl.loaded:
            self.ctrl.camera.frame(self.ctrl.session.work.bounds)
            self.gl.queue_render()

    def _on_head_up(self, dd: Gtk.DropDown, _p) -> None:
        head = self.ctrl.session.project.head
        if self._updating or head is None or not head.replacement_path:
            return
        up = REPLACEMENT_UPS[dd.get_selected()]
        if up != head.replacement_up:
            self.ctrl.set_head_orientation(up=up)

    def _on_head_adjust(self, key: str, spin: Gtk.SpinButton) -> None:
        head = self.ctrl.session.project.head
        if self._updating or head is None or not head.replacement_path:
            return
        if abs(spin.get_value() - getattr(head, HEAD_ADJUST_ATTR[key])) > 1e-9:
            self.ctrl.set_head_adjust(**{key: spin.get_value()})

    # ------------------------------------------------------- piezas de catálogo
    def _sync_catalog(self, loaded: bool) -> None:
        ctrl = self.ctrl
        root = ctrl.session.catalog_root
        self.btn_cat_new.set_sensitive(not ctrl.busy)
        self.btn_cat_open.set_sensitive(not ctrl.busy)
        self.btn_cat_hero.set_sensitive(not ctrl.busy)
        self.btn_cat_add.set_sensitive(bool(loaded and root and not ctrl.busy))
        self.btn_cat_guide.set_sensitive(bool(loaded and not ctrl.busy))
        self.btn_cat_fitcm.set_sensitive(bool(loaded and root and not ctrl.busy))
        self.btn_proportions.set_sensitive(bool(loaded and root and not ctrl.busy))
        self.btn_cat_manage.set_sensitive(not ctrl.busy)       # sin proyecto ni catálogo abierto: al apretar abre el recordado o pide la carpeta
        self.btn_cat_build.set_sensitive(not ctrl.busy)
        self.btn_build_cat.set_sensitive(not ctrl.busy)
        if self._catalog_win is not None:
            self._catalog_win.refresh_if_changed()
        self.lbl_catalog.set_text("Sin catálogo abierto" if not root else f"Catálogo: {root}")
        self.btn_cat_forget.set_sensitive(bool(root and not ctrl.busy))
        self.btn_cat_review.set_sensitive(bool(loaded and (ctrl.session.project.parts or ctrl.session.project.part_scales) and not ctrl.busy))
        self.btn_divide_model.set_sensitive(bool(loaded and not ctrl.busy and not ctrl.session.catalog_only()))
        self.btn_reset_piece_scales.set_sensitive(bool(loaded and ctrl.session.project.part_scales and not ctrl.busy))
        self.btn_cat_scale.set_sensitive(bool(loaded and root and not ctrl.busy))
        self.btn_cat_align.set_sensitive(bool(loaded and root and ctrl.session.project.parts and not ctrl.busy))
        scale = ctrl.line_scale() if root else None
        self.btn_cat_scale.set_label("Escalar la figura a la altura de la línea" if scale is None else
                                     f"Escalar la figura a la altura de la línea (×{scale:.3f})".replace(".", ","))
        rows = ctrl.catalog_rows() if loaded else []
        choices = {r["lugar"]: [m.ref for m in ctrl.catalog_choices(r["lugar"])] if root else [] for r in rows}
        pieces = ctrl.catalog_pieces() if root else []
        shown = {m.ref: piece_label(m) for m in pieces}                    # «Nombre (id@n)» si la pieza tiene nombre
        row_labels = {r["pieza"]: piece_label(pc) for r in rows if r["pieza"] and (pc := find_piece(pieces, r["pieza"])) is not None}
        sig = (root, ctrl.busy, tuple(sorted(shown.items())), tuple(sorted(ctrl.session.project.part_scales.items())),
               tuple(ctrl.session.scale_places()), tuple((r["lugar"], r["pieza"], ctrl.catalog_row_text(r, row_labels), tuple(choices[r["lugar"]]), r.get("nivel"),
                           tuple(r.get("conflictos") or ()), tuple(r.get("avisos") or ())) for r in rows))
        if sig == self._parts_sig:
            return
        self._parts_sig = sig
        while (child := self.parts_box.get_first_child()) is not None:
            self.parts_box.remove(child)
        for r in rows:
            lugar, refs = r["lugar"], choices[r["lugar"]]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            line = Gtk.Box(spacing=6)
            state, glyph = ctrl.catalog_row_state(r)
            icon = Gtk.Label(label=glyph, tooltip_text={"modelo": "Usa la pieza del modelo", "pendiente": "Pieza elegida, sin revisar",
                                                         "ok": "Acopla bien", "aviso": "Acopla con avisos", "no": "No acopla"}[state])
            icon.add_css_class("afm-step-glyph")
            icon.add_css_class({"modelo": "afm-step-info", "pendiente": "afm-step-todo", "ok": "afm-step-ok",
                                "aviso": "afm-step-warn", "no": "afm-step-bad"}[state])
            line.append(icon)
            line.append(Gtk.Label(label=lugar.capitalize(), xalign=0, hexpand=True))
            dd = Gtk.DropDown.new_from_strings(["(la del modelo)"] + [shown.get(ref, ref) for ref in refs])
            cur = r["pieza"]
            # la pieza guardada puede traer versión fija («id@2»): se muestra la fila de esa pieza si está en la lista
            idx = 0 if cur is None else next((i + 1 for i, ref in enumerate(refs) if ref == cur or ref.split("@")[0] == cur.split("@")[0]), 0)
            dd.set_selected(idx)
            _no_scroll_change(dd)
            dd.connect("notify::selected", self._on_part_selected, lugar, refs)
            dd.set_sensitive(bool(root and not ctrl.busy))
            line.append(dd)
            box.append(line)
            if lugar in ctrl.session.scale_places():
                box.append(self._scale_line(lugar))
            if r["pieza"] is not None:
                lbl = Gtk.Label(label=ctrl.catalog_row_text(r, row_labels), xalign=0, wrap=True)
                lbl.add_css_class("dim-label")
                box.append(lbl)
                if r.get("previa"):
                    box.append(self._part_actions(lugar, r))
                piece = find_piece(pieces, r["pieza"]) if lugar in ("brazo", "pierna") else None
                if piece is not None and can_retune_joint(piece)[0]:
                    btn = Gtk.Button(label="Reajustar la articulación de esta pieza…", halign=Gtk.Align.START,
                                     tooltip_text="Abre «Administrar catálogo» con esta pieza para mover el eje de la bisagra, inclinar el plano o cambiar el rango "
                                                  "viendo el hueco; se guarda como versión nueva de la pieza (después elegila en este lugar).")
                    btn.connect("clicked", self._on_retune_joint, piece.ref)
                    box.append(btn)
            self.parts_box.append(box)

    def _scale_line(self, lugar: str) -> Gtk.Box:
        """Fila de escala de la pieza de un lugar: X (ancho), Y (profundidad) y Z (alto), con «igual» para mantener la forma."""
        ctrl = self.ctrl
        k = ctrl.session.part_scale(lugar)
        line = Gtk.Box(spacing=6)
        line.append(Gtk.Label(label="Escala de la pieza ×", xalign=0))
        lock = Gtk.CheckButton(label="igual", tooltip_text="Los tres ejes juntos (escala uniforme). Sin marcar, cada eje es independiente.")
        lock.set_active(k[0] == k[1] == k[2])
        spins = []
        guard = {"on": False}

        def linked(src, *_):
            if guard["on"] or not lock.get_active():
                return
            guard["on"] = True
            for sp in spins:
                if sp is not src:
                    sp.set_value(src.get_value())
            guard["on"] = False

        for name, tip, val in (("X", "lateral: ancho de la pieza", k[0]), ("Y", "adelante-atrás: profundidad", k[1]),
                               ("Z", "vertical: alto", k[2])):
            line.append(Gtk.Label(label=name, xalign=1, tooltip_text=tip))
            sp = Gtk.SpinButton.new_with_range(0.05, 10.0, 0.01)
            sp.set_digits(3)
            sp.set_value(val)
            sp.set_tooltip_text(tip)
            sp.connect("value-changed", linked)
            _no_scroll_change(sp)
            spins.append(sp)
            line.append(sp)
        line.append(lock)
        apply = Gtk.Button(label="Aplicar")
        apply.connect("clicked", lambda *_: ctrl.set_part_scale(lugar, tuple(sp.get_value() for sp in spins)))
        line.append(apply)
        ok = bool(not ctrl.busy and not ctrl.session.catalog_only())
        for w in (*spins, lock, apply):
            w.set_sensitive(ok)
        return line

    def _on_part_selected(self, dd: Gtk.DropDown, _p, lugar: str, refs: list) -> None:
        if self._updating:
            return
        i = dd.get_selected()
        ref = None if i == 0 else refs[i - 1]
        cur = self.ctrl.session.project.parts.get(lugar)
        if (cur.ref if cur else None) == ref or (cur is not None and ref is not None and cur.id == ref.split("@")[0]
                                                  and cur.version == int(ref.split("@")[1])):
            return                                    # ya estaba (la lista se redibujó)
        self.ctrl.catalog_set_part(lugar, ref)

    def _part_actions(self, lugar: str, r: dict) -> Gtk.Box:
        """Botones de una fila revisada: «Ver por qué», escalar para este lugar y resolver el conflicto de rodilla/codo."""
        ctrl = self.ctrl
        bar = Gtk.Box(spacing=4)
        why = Gtk.Button(label="Ver por qué", tooltip_text="Superpone la cara de la pieza y la del modelo y explica el acople")
        why.connect("clicked", lambda *_: self._show_fit_diagnosis(lugar))
        bar.append(why)
        pv = ctrl.part_preview(lugar)
        if pv is not None and pv.scale_hint is not None and abs(pv.scale_hint - 1.0) > 0.03:
            sc = Gtk.Button(label=f"Escalar la figura ×{pv.scale_hint:.3f}".replace(".", ","),
                            tooltip_text="Escala TODA la figura para que la cara de este lugar tenga el tamaño de la de la pieza")
            sc.connect("clicked", lambda *_: ctrl.scale_to_fit_part(lugar))
            bar.append(sc)
            if not ctrl.session.catalog_only():
                local = Gtk.Button(label="Ajustar sólo torso / pelvis", tooltip_text="Iguala el radio de las caras de unión. Revisá luego IoU, escalón y pared.")
                local.connect("clicked", lambda *_: ctrl.scale_host_for_part(lugar))
                bar.append(local)
        if (pv is not None and pv.scale_xyz is not None and not ctrl.session.catalog_only()
                and max(abs(v - 1.0) for v in pv.scale_xyz) > 0.03):
            sx, sy, sz = pv.scale_xyz
            xyz = Gtk.Button(label=f"Ajustar ejes (X {sx:.2f}, Y {sy:.2f}, Z {sz:.2f})".replace(".", ","),
                             tooltip_text="Escala por separado ancho, profundidad y alto de la pieza del modelo que lleva esta unión "
                                          "para que su cara tenga el ancho y el alto de la de la pieza elegida; no toca el espesor "
                                          "ni las demás piezas. Revisá luego IoU, escalón y pared.")
            xyz.connect("clicked", lambda *_: ctrl.fit_host_xyz(lugar))
            bar.append(xyz)
        if r.get("conflictos"):
            k1 = Gtk.Button(label="Usar la articulación de la pieza",
                            tooltip_text="Quita la rodilla/codo del modelo en este miembro")
            k1.connect("clicked", lambda *_: ctrl.resolve_joint_conflict(lugar, "part"))
            k2 = Gtk.Button(label="Usar la del modelo", tooltip_text="Vuelve a la pieza del modelo en este lugar")
            k2.connect("clicked", lambda *_: ctrl.resolve_joint_conflict(lugar, "model"))
            bar.append(k1)
            bar.append(k2)
        return bar

    def _show_fit_diagnosis(self, lugar: str) -> None:
        """Ventana con las dos caras superpuestas (u, v en mm) y el porqué, en lenguaje llano."""
        d = self.ctrl.fit_diagnosis(lugar)
        if d is None:
            self.ctrl.report_progress("Primero revisá las piezas.")
            return
        win = Gtk.Window(title=f"Por qué «{d['pieza']}» en {lugar}", transient_for=self, modal=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        box.append(Gtk.Label(label=d["resumen"], xalign=0, wrap=True, max_width_chars=60))
        area = Gtk.DrawingArea(content_width=420, content_height=320)
        area.set_draw_func(lambda _a, cr, w, h: self._draw_faces(cr, w, h, d["caras"]))
        box.append(area)
        box.append(Gtk.Label(label="Verde: lo que se toca · rojo: sobra de la pieza · ámbar: sobra del modelo · "
                                   "azul: contorno del modelo · naranja: contorno de la pieza", xalign=0, wrap=True, max_width_chars=60))
        for t in d["lineas"] or ["Todo bien: las caras coinciden."]:
            box.append(Gtk.Label(label="• " + t, xalign=0, wrap=True, max_width_chars=60))
        close = Gtk.Button(label="Cerrar", halign=Gtk.Align.END)
        close.connect("clicked", lambda *_: win.close())
        box.append(close)
        win.set_child(box)
        win.present()

    @staticmethod
    def _draw_faces(cr, w: int, h: int, caras: dict) -> None:
        pts = [pt for key in ("pieza", "modelo") for poly in caras.get(key, []) for pt in poly["exterior"]]
        if not pts:
            return
        us, vs = [p[0] for p in pts], [p[1] for p in pts]
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        k = 0.9 * min(w / max(u1 - u0, 1e-6), h / max(v1 - v0, 1e-6))
        cu, cv = (u0 + u1) / 2, (v0 + v1) / 2

        def xy(p):
            return w / 2 + (p[0] - cu) * k, h / 2 - (p[1] - cv) * k

        def path(poly):
            for ring in [poly["exterior"]] + list(poly["interiors"]):
                if not ring:
                    continue
                cr.move_to(*xy(ring[0]))
                for q in ring[1:]:
                    cr.line_to(*xy(q))
                cr.close_path()

        for key, rgba in (("comun", (0.30, 0.78, 0.45, 0.55)), ("solo_pieza", (0.90, 0.30, 0.30, 0.60)),
                          ("solo_modelo", (0.95, 0.75, 0.20, 0.60))):
            for poly in caras.get(key, []):
                path(poly)
                cr.set_source_rgba(*rgba)
                cr.fill()
        cr.set_line_width(2.0)
        for key, rgb in (("modelo", (0.25, 0.50, 0.95)), ("pieza", (0.95, 0.50, 0.15))):
            for poly in caras.get(key, []):
                path(poly)
                cr.set_source_rgb(*rgb)
                cr.stroke()

    def _on_divide_model(self, *_):
        if self.ctrl.busy:
            return
        self.ctrl.busy = True
        self.ctrl._changed("Dividiendo la figura en piezas crudas…", scene=False)
        self._run_task("Dividiendo la figura…", lambda progress: self.ctrl.session.divide_model(),
                       lambda res, err: self.ctrl.finish_preview_parts(res, err), "Figura dividida")

    def _on_proportions(self, *_):
        """«Comparar proporciones con un patrón…»: ventana (no modal) con tu figura y el patrón del catálogo, en tres vistas."""
        if self._prop_win is not None:
            self._prop_win.present()
            return
        if not self.ctrl.proportion_figures():
            self.ctrl._changed("Abrí un catálogo con una figura patrón completa (cabeza, torso, brazo y pierna del mismo origen).", scene=False)
            return
        from .proportions_window import ProportionsWindow

        win = ProportionsWindow(self, self.ctrl)
        self._prop_win = win
        win.connect("close-request", self._on_proportions_closed)
        win.present()
        win.reload()

    def _on_proportions_closed(self, *_) -> bool:
        self._prop_win = None
        return False

    def _on_cut_guide(self, *_):
        """«Guía de cortes»: ventana (no modal) con la medición de cada corte y su cobertura del catálogo."""
        lines = self.ctrl.cut_guide_lines()
        if lines is None:
            return
        win = Gtk.Window(title="Guía de cortes", transient_for=self, modal=False, default_width=760, default_height=520)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        label = Gtk.Label(label="\n".join(lines), xalign=0, yalign=0, selectable=True, wrap=False)
        label.add_css_class("monospace")
        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(label)
        box.append(Gtk.Label(label="Cada corte se mide sobre la figura abierta (sin generar). «Como enchufe» es la pieza que saldría de esta "
                                   "figura puesta en otros torsos del catálogo; «como toma», el torso de esta figura con piezas de otras.",
                             xalign=0, wrap=True, max_width_chars=90))
        box.append(scroller)
        bar = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        again = Gtk.Button(label="Volver a medir")
        close = Gtk.Button(label="Cerrar")

        def refresh(*_):
            new = self.ctrl.cut_guide_lines()
            if new is not None:
                label.set_text("\n".join(new))

        again.connect("clicked", refresh)
        close.connect("clicked", lambda *_: win.close())
        bar.append(again)
        bar.append(close)
        box.append(bar)
        win.set_child(box)
        win.present()

    def _on_new_from_pattern(self, *_):
        """«Nuevo héroe desde el patrón»: pide el proyecto del patrón, el STL del héroe y dónde guardar; lo crea en segundo plano,
        lo abre y muestra la imagen de revisión."""
        if self.ctrl.busy:
            return
        win = Gtk.Window(title="Nuevo héroe desde el patrón", transient_for=self, modal=True, default_width=640)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(Gtk.Label(label="Copia los planos del proyecto del patrón y vuelve a dibujar las siluetas sobre la malla del héroe.",
                             xalign=0, wrap=True, max_width_chars=80))
        entries = {}

        def file_row(key, title, placeholder, patterns, save=False):
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=title, xalign=0, width_chars=22))
            ent = Gtk.Entry(placeholder_text=placeholder, hexpand=True)
            btn = Gtk.Button(label="Elegir…")

            def pick(*_):
                dlg = Gtk.FileDialog(title=title)
                flt = Gtk.FileFilter()
                flt.set_name(", ".join(patterns))
                for pt in patterns:
                    flt.add_pattern(pt)
                filters = Gio.ListStore.new(Gtk.FileFilter)
                filters.append(flt)
                dlg.set_filters(filters)

                def finished(d, res):
                    try:
                        f = (d.save_finish if save else d.open_finish)(res)
                    except GLib.Error:
                        return
                    ent.set_text(f.get_path())
                    if key == "stl" and not entries["out"].get_text():
                        entries["out"].set_text(os.path.splitext(f.get_path())[0] + ".proyecto")

                (dlg.save if save else dlg.open)(win, None, finished)

            btn.connect("clicked", pick)
            row.append(ent)
            row.append(btn)
            box.append(row)
            entries[key] = ent

        file_row("patron", "Proyecto del patrón:", "p. ej. guemes.proyecto", ["*.proyecto", "*.json"])
        file_row("stl", "STL del héroe:", "p. ej. sanmartin.stl", ["*.stl", "*.STL"])
        file_row("out", "Guardar proyecto como:", "por defecto, junto al STL", ["*.proyecto"], save=True)
        opt = Gtk.Box(spacing=6)
        opt.append(Gtk.Label(label="Coronilla (opcional):", xalign=0, width_chars=22))
        ent_crown = Gtk.Entry(placeholder_text="altura en unidades del STL; si lleva sombrero o plumas", hexpand=True)
        opt.append(ent_crown)
        box.append(opt)
        opt2 = Gtk.Box(spacing=6)
        opt2.append(Gtk.Label(label="Entrepierna (opcional):", xalign=0, width_chars=22))
        ent_crotch = Gtk.Entry(placeholder_text="altura en mm; si lleva abrigo largo", hexpand=True)
        opt2.append(ent_crotch)
        box.append(opt2)
        status = Gtk.Label(label="", xalign=0, wrap=True, max_width_chars=80)
        box.append(status)
        bar = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        go = Gtk.Button(label="Crear y abrir")
        cancel = Gtk.Button(label="Cancelar")
        bar.append(go)
        bar.append(cancel)
        box.append(bar)

        def number(ent, what):
            txt = ent.get_text().strip().replace(",", ".")
            if not txt:
                return None
            try:
                return float(txt)
            except ValueError:
                raise ValueError(f"{what}: «{txt}» no es un número") from None

        def run(*_):
            try:
                patron, stl = entries["patron"].get_text().strip(), entries["stl"].get_text().strip()
                out = entries["out"].get_text().strip() or (os.path.splitext(stl)[0] + ".proyecto" if stl else "")
                job = self.ctrl.new_from_pattern_job(patron, stl, out, number(ent_crown, "Coronilla"), number(ent_crotch, "Entrepierna"))
            except ValueError as e:
                status.set_text(str(e))
                return
            win.close()
            self.ctrl.busy = True
            self._run_task("Creando el héroe desde el patrón…", job, lambda res, err: self._new_from_pattern_done(res, err, stl),
                           "Héroe creado")

        go.connect("clicked", run)
        cancel.connect("clicked", lambda *_: win.close())
        win.set_child(box)
        win.present()

    def _new_from_pattern_done(self, res, err, stl: str) -> None:
        image = self.ctrl.finish_new_from_pattern(res, err, stl)
        if image is None or not os.path.isfile(image):
            return
        self._review_on_open()
        win = Gtk.Window(title="Revisión de los planos (frente y costado)", transient_for=self, modal=False, default_width=900, default_height=640)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        pic = Gtk.Picture.new_for_filename(image)
        pic.set_can_shrink(True)
        pic.set_vexpand(True)
        box.append(pic)
        box.append(Gtk.Label(label=f"{image}\nSi un plano no cae donde debe, corregilo en Planos o en el editor de siluetas.", xalign=0, wrap=True))
        win.set_child(box)
        win.present()

    def _on_fit_waist_wrist(self, *_):
        """«Ajustar cintura y muñeca al patrón»: ventana con lo que se movería y botón para aplicarlo (con el catálogo abierto)."""
        opts = self.ctrl.waist_wrist_options()
        win = Gtk.Window(title="Ajustar cintura y muñeca al patrón", transient_for=self, modal=False, default_width=760, default_height=480)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(Gtk.Label(label="Compara el tamaño de la cara de la cintura y de la muñeca de esta figura con las de la pelvis y la mano "
                                   "del patrón del catálogo y propone cuánto mover cada plano. «Aplicar» solo mueve los planos; si sobra "
                                   "escala, el texto lo dice.", xalign=0, wrap=True, max_width_chars=90))
        drops = {}
        for key, title in (("pelvis", "Pelvis del patrón"), ("mano", "Mano del patrón")):
            refs = opts.get(key) or []
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=title + ":", xalign=0))
            dd = Gtk.DropDown.new_from_strings(refs or ["(ninguna en el catálogo)"])
            dd.set_sensitive(len(refs) > 1)
            row.append(dd)
            box.append(row)
            drops[key] = (dd, refs)

        def chosen(key):
            dd, refs = drops[key]
            return refs[dd.get_selected()] if refs else None

        scale_chk = Gtk.CheckButton(label="Al aplicar, escalar también la pelvis del patrón con la escala que sobra")
        scale_chk.set_tooltip_text("Escribe la escala de la pieza «pelvis» (la inversa de la escala restante del héroe) cuando entra en el tope de ±10 %. "
                                   "La mano no se reescala.")
        box.append(scale_chk)
        label = Gtk.Label(label="", xalign=0, yalign=0, selectable=True, wrap=False)
        label.add_css_class("monospace")
        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(label)
        box.append(scroller)
        bar = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        measure = Gtk.Button(label="Medir")
        apply_btn = Gtk.Button(label="Aplicar")
        close = Gtk.Button(label="Cerrar")

        def refresh(*_):
            new = self.ctrl.waist_wrist_fit_lines(chosen("pelvis"), chosen("mano"))
            if new is not None:
                label.set_text("\n".join(new))

        def apply(*_):
            if self.ctrl.apply_waist_wrist_fit(chosen("pelvis"), chosen("mano"), scale_chk.get_active()):
                refresh()

        measure.connect("clicked", refresh)
        apply_btn.connect("clicked", apply)
        close.connect("clicked", lambda *_: win.close())
        for b in (measure, apply_btn, close):
            bar.append(b)
        box.append(bar)
        win.set_child(box)
        win.present()
        refresh()

    def _on_catalog_review(self, *_):
        job = self.ctrl.begin_preview_parts()
        if job is not None:
            self._run_task("Revisando las piezas", job, lambda items, err: self.ctrl.finish_preview_parts(items, err),
                           "Piezas revisadas")

    def _review_on_open(self) -> None:
        """Al abrir un proyecto (o su catálogo) con piezas de otra figura: las revisa en segundo plano para dibujarlas."""
        if self.ctrl.needs_review_on_open():
            self._on_catalog_review()

    def _open_project_path(self, path: str, catalog_root: str | None = None) -> None:
        try:
            self.ctrl.open_project(path, catalog_root=catalog_root)
        except CatalogNeeded as exc:
            if catalog_root is not None:            # la carpeta elegida tampoco es el catálogo: no insistir en bucle
                raise
            self._ask_catalog_for_project(path, exc)
            return
        self._review_on_open()

    def _ask_catalog_for_project(self, path: str, exc: "CatalogNeeded") -> None:
        """El proyecto es de una figura armada solo con piezas de catálogo (sin STL) y no está el catálogo de su línea:
        se pide la carpeta y se vuelve a abrir."""
        dlg = Gtk.FileDialog(title=f"Catálogo de la línea «{exc.line}» (carpeta)")

        def finished(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                self._error(exc)                    # cancelado: se explica por qué no se abrió
                return
            try:
                self._open_project_path(path, f.get_path())
            except Exception as e:          # noqa: BLE001
                self._error(e)

        dlg.select_folder(self, None, finished)


    def _on_align_all(self, *_):
        self.ctrl.align_cuts_to_line()

    def _on_catalog_new(self, *_):
        """«Crear catálogo…»: elegir la carpeta nueva, después nombre de la línea y altura de figura."""
        name0, height0 = self.ctrl.default_catalog_line()
        dlg = Gtk.FileDialog(title="Carpeta nueva para el catálogo de la línea", initial_name=name0)

        def picked(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return                      # cancelado
            self._catalog_line_dialog(f.get_path(), height0)

        dlg.save(self, None, picked)

    def _catalog_line_dialog(self, path: str, height0: float) -> None:
        win = Gtk.Window(title="Crear catálogo de la línea", transient_for=self, modal=True, resizable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        box.append(Gtk.Label(label=f"Carpeta: {path}", xalign=0, wrap=True, max_width_chars=50))
        box.append(Gtk.Label(label="Nombre de la línea (letras, números, guiones y puntos)", xalign=0))
        ent = Gtk.Entry(text=os.path.basename(path.rstrip(os.sep)), hexpand=True)
        box.append(ent)
        box.append(Gtk.Label(label="Altura de la figura de la línea, de los pies a la coronilla (mm)", xalign=0))
        spin = Gtk.SpinButton.new_with_range(10.0, 500.0, 1.0)
        spin.set_value(height0)
        box.append(spin)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=6)
        cancel = Gtk.Button(label="Cancelar")
        ok = Gtk.Button(label="Crear")
        ok.add_css_class("suggested-action")
        row.append(cancel)
        row.append(ok)
        box.append(row)
        win.set_child(box)
        cancel.connect("clicked", lambda *_: win.close())

        def create(*_):
            if self.ctrl.create_catalog(path, ent.get_text(), spin.get_value()):
                win.close()
            else:
                self._error(ValueError(self.ctrl.message))   # deja abierto el diálogo para corregir nombre o altura

        ok.connect("clicked", create)
        win.present()

    def _on_catalog_open(self, *_):
        dlg = Gtk.FileDialog(title="Catálogo de la línea (carpeta)")

        def finished(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return                      # cancelado
            try:
                if self.ctrl.open_catalog(f.get_path()):
                    self._review_on_open()          # el proyecto ya traía piezas y faltaba el catálogo para verlas
            except Exception as e:          # noqa: BLE001
                self._error(e)

        dlg.select_folder(self, None, finished)

    def _on_catalog_build(self, *_):
        """«Armar figura desde catálogo»: no hace falta un STL. Usa el catálogo abierto, el último que se usó o pide la carpeta."""
        if self._build_win is not None:
            self._build_win.present()
            return
        if not self.ctrl.session.catalog_root:
            from . import settings as GUI_SETTINGS

            last = GUI_SETTINGS.last_catalog()
            if last and self.ctrl.open_catalog(last):
                self._open_builder()
                return
            dlg = Gtk.FileDialog(title="Catálogo de la línea (carpeta)")

            def finished(d, res):
                try:
                    f = d.select_folder_finish(res)
                except GLib.Error:
                    return                      # cancelado
                try:
                    if self.ctrl.open_catalog(f.get_path()):
                        self._open_builder()
                except Exception as e:          # noqa: BLE001
                    self._error(e)

            dlg.select_folder(self, None, finished)
            return
        self._open_builder()

    def _open_builder(self) -> None:
        from .figure_window import FigureBuilderWindow

        try:
            win = FigureBuilderWindow(self, self.ctrl.session.catalog_root)
        except Exception as e:                  # noqa: BLE001
            self._error(e)
            return
        win.connect("close-request", self._on_builder_closed)
        self._build_win = win
        win.present()

    def _on_builder_closed(self, *_) -> bool:
        self._build_win = None
        return False

    def _on_catalog_manage(self, *_):
        """«Administrar catálogo…»: ventana con la lista de piezas, vista previa 3/4 y eliminación (una sola ventana)."""
        if self._catalog_win is not None:
            self._catalog_win.refresh_if_changed()
            self._catalog_win.present()
            return
        if not self.ctrl.session.catalog_root:           # no hace falta proyecto ni STL: usa el último catálogo o pide la carpeta
            from . import settings as GUI_SETTINGS

            last = GUI_SETTINGS.last_catalog()
            if last and self.ctrl.open_catalog(last):
                self._open_catalog_manager()
                return
            dlg = Gtk.FileDialog(title="Catálogo de la línea (carpeta)")

            def finished(d, res):
                try:
                    f = d.select_folder_finish(res)
                except GLib.Error:
                    return                                # cancelado
                try:
                    if self.ctrl.open_catalog(f.get_path()):
                        self._open_catalog_manager()
                except Exception as e:                    # noqa: BLE001
                    self._error(e)

            dlg.select_folder(self, None, finished)
            return
        self._open_catalog_manager()

    def _open_catalog_manager(self) -> None:
        from .catalog_window import CatalogManagerWindow

        win = CatalogManagerWindow(self, self.ctrl)
        win.connect("close-request", self._on_catalog_manager_closed)
        self._catalog_win = win
        win.present()

    def _on_retune_joint(self, _btn, ref: str) -> None:
        """«Reajustar la articulación de esta pieza…»: abre el gestor del catálogo con `ref` elegida y el modo de reajuste activo."""
        self._on_catalog_manage()
        if self._catalog_win is not None:
            self._catalog_win.edit_joint(ref)

    def _on_catalog_manager_closed(self, *_) -> bool:
        self._catalog_win = None
        return False                                  # deja que GTK cierre la ventana

    def _on_catalog_add(self, *_):
        """«Catalogar esta figura…»: diálogo para elegir qué lugares guardar y si va como versión nueva."""
        opts = self.ctrl.catalog_add_options()
        if opts is None:
            return
        win = Gtk.Window(title="Catalogar esta figura", transient_for=self, modal=True, resizable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(Gtk.Label(label=f"Piezas que se guardan en el catálogo (ids «…-{opts['prefijo']}-01»)", xalign=0, wrap=True,
                             max_width_chars=60))
        checks: dict[str, Gtk.CheckButton] = {}
        for row in opts["lugares"]:
            lugar = row["lugar"]
            text = lugar.capitalize() + (f" — {row['nota']}" if row["nota"] else "")
            if row["existentes"]:
                text += f"  (ya está: {', '.join(row['existentes'])})"
            chk = Gtk.CheckButton(label=text)
            chk.set_sensitive(row["disponible"])
            chk.set_active(bool(row["disponible"] and (not row["existentes"] or (lugar == "cabeza" and opts["cabeza_reemplazo"]))))
            checks[lugar] = chk
            box.append(chk)
        newv = Gtk.CheckButton(label="Guardar como versión nueva si la pieza ya existe")
        box.append(newv)

        def sync(*_):
            used = any(chk.get_active() and row["existentes"] for chk, row in zip(checks.values(), opts["lugares"]))
            if used:
                newv.set_active(True)

        for chk in checks.values():
            chk.connect("toggled", sync)
        sync()
        hint = ("La cabeza que se guarda es la de reemplazo que importaste (ya escalada a la base del cuello), no la del modelo. "
                if opts["cabeza_reemplazo"] else "")
        hint += ("Para subir solo una cabeza nueva dejá tildada únicamente «Cabeza» y «versión nueva»: el resto del catálogo no se toca "
                 "y las figuras que ya usan la versión anterior siguen usándola.")
        lab = Gtk.Label(label=hint, xalign=0, wrap=True, max_width_chars=60)
        lab.add_css_class("dim-label")
        box.append(lab)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=6)
        cancel, ok = Gtk.Button(label="Cancelar"), Gtk.Button(label="Catalogar")
        ok.add_css_class("suggested-action")
        row.append(cancel)
        row.append(ok)
        box.append(row)
        win.set_child(box)
        cancel.connect("clicked", lambda *_: win.close())

        def go(*_):
            places = [lugar for lugar, chk in checks.items() if chk.get_active()]
            if not places:
                return
            nv, prefix = newv.get_active(), opts["prefijo"]
            win.close()
            self._run_task("Catalogando la figura…",
                           lambda progress: self.ctrl.session.catalog_add(prefix, places=places, new_version=nv),
                           lambda res, err: self.ctrl.catalog_add_done(res, err), "Figura catalogada")

        ok.connect("clicked", go)
        win.present()

    def _on_hand_replacement(self, *_):
        self._pick_file("Mano estándar (STL)", ["*.stl"], "Archivos STL", self.ctrl.start_hand_place)

    def _on_hand_yaw(self, spin: Gtk.SpinButton) -> None:
        hand = self.ctrl.session.project.hand
        if self._updating or hand is None:
            return
        if abs(spin.get_value() - hand.yaw) > 1e-9:
            self.ctrl.set_hand_adjust(yaw=spin.get_value())

    def _on_front(self, dd: Gtk.DropDown, _p) -> None:
        if self._updating:
            return
        front = "+Y" if dd.get_selected() == 0 else "-Y"
        if front == self.ctrl.session.project.front:
            return
        loss = self.ctrl.front_change_loss()
        if not loss:
            self.ctrl.set_front(front)
            return
        dlg = Gtk.AlertDialog(
            message="Cambiar hacia dónde mira la figura",
            detail="Esto borra " + ", ".join(loss) + " y no se puede deshacer. ¿Cambiar igual?",
            buttons=["Cancelar", "Cambiar y borrar"], cancel_button=0, default_button=0)

        def answered(d, res) -> None:
            try:
                ok = d.choose_finish(res) == 1
            except GLib.Error:
                ok = False
            if ok:
                self.ctrl.set_front(front)
            else:                                   # vuelve el desplegable al valor vigente
                self._updating = True
                try:
                    self.dd_front.set_selected(0 if self.ctrl.session.project.front == "+Y" else 1)
                finally:
                    self._updating = False

        dlg.choose(self, None, answered)

    # --------------------------------------------------------------- diálogos
    def _error(self, e: Exception) -> None:
        Gtk.AlertDialog(message="No se pudo completar la acción", detail=str(e)).show(self)

    @staticmethod
    def _filters(name: str, patterns: list[str]) -> Gio.ListStore:
        f = Gtk.FileFilter()
        f.set_name(name)
        for p in patterns:
            f.add_pattern(p)
            f.add_pattern(p.upper())
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        return store

    def _pick_file(self, title: str, patterns: list[str], label: str, done) -> None:
        dlg = Gtk.FileDialog(title=title, filters=self._filters(label, patterns))

        def finished(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return                      # cancelado
            try:
                done(f.get_path())
            except Exception as e:          # noqa: BLE001 — se informa al usuario
                self._error(e)

        dlg.open(self, None, finished)

    # ------------------------------------------------------- tareas largas
    @staticmethod
    def _fmt_elapsed(sec: float) -> str:
        sec = int(sec)
        h, rem = divmod(sec, 3600)
        m, sc = divmod(rem, 60)
        return f"{h}:{m:02d}:{sc:02d}" if h else f"{m}:{sc:02d}"

    def _run_task(self, label: str, job, done, done_label: str = "Terminado") -> None:
        """Corre `job(progress)` en un hilo (la interfaz sigue viva) y llama `done(resultado, error)` en el hilo principal.

        Mientras tanto el panel Estado muestra un spinner y un cronómetro con el tiempo transcurrido, para que se vea
        que el programa trabaja y no está colgado."""
        token = CancelToken()
        self._task = {"label": label, "done": done_label, "t0": time.monotonic(), "token": token}
        self._task_serial += 1
        stages, self.ctrl.progress_stages = self.ctrl.progress_stages, None      # el plan lo deja `begin_*` del controlador
        self.progress_panel.start(stages, label)
        self.spinner.set_visible(True)
        self.spinner.start()
        self.btn_task_cancel.set_label("Cancelar")
        self.btn_task_cancel.set_sensitive(True)
        self.btn_task_cancel.set_visible(True)
        self._task_tick()
        GLib.timeout_add(200, self._task_tick)

        def work() -> None:
            try:
                result = job(token.wrap(lambda text: GLib.idle_add(self._progress_idle, text)))
            except Exception as e:          # noqa: BLE001 — se informa en el estado
                GLib.idle_add(self._task_done_idle, done, None, e)
            else:
                GLib.idle_add(self._task_done_idle, done, result, None)

        threading.Thread(target=work, daemon=True, name="afm-task").start()

    def _on_task_cancel(self) -> None:
        t = self._task
        if t is None:
            return
        t["token"].cancel()
        self.btn_task_cancel.set_sensitive(False)
        self.btn_task_cancel.set_label("Cancelando…")

    def _collapse_progress(self, serial: int) -> bool:
        if serial == self._task_serial and self._task is None:      # no si arrancó otra tarea en el medio
            self.progress_panel.hide_panel()
        return False

    def _task_tick(self) -> bool:
        t = self._task
        if t is None:
            return False                    # terminó: se corta el temporizador
        extra = " · cancelando: termina el paso en curso" if t["token"].cancelled else ""
        self.lbl_task.set_text(f"{t['label']} · {self._fmt_elapsed(time.monotonic() - t['t0'])}{extra}")
        return True

    def _task_done_idle(self, done, result, error) -> bool:
        t, self._task = self._task, None
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.btn_task_cancel.set_visible(False)
        cancelled = isinstance(error, TaskCancelled)
        took = self._fmt_elapsed(time.monotonic() - t["t0"]) if t else ""
        self.progress_panel.finish(error is None, (t or {}).get("done", ""))
        if cancelled and self.progress_panel.get_visible():
            self.progress_panel.bar.set_text("Cancelado")
        self._task_serial += 1
        if error is None or cancelled:          # si salió bien (o se canceló) se pliega solo; si falló queda visible
            GLib.timeout_add_seconds(4, self._collapse_progress, self._task_serial)
        self.lbl_task.set_text(("Cancelado" if cancelled else f"✗ Falló a los {took}" if error else f"✓ {t['done']} en {took}") if t else "")
        done(result, error)
        return False

    def _progress_idle(self, text: str) -> bool:
        self.progress_panel.feed(text)
        self.ctrl.report_progress(text)
        return False                        # no repetir

    def _on_generate(self) -> None:
        """«Generar figura»: si hay piezas de catálogo sin revisar las revisa primero (rápido) y avisa antes de generar."""
        ctrl = self.ctrl
        if ctrl.loaded and not ctrl.busy and ctrl.session.project.parts and not ctrl.session.part_preview_fresh():
            job = ctrl.begin_preview_parts()
            if job is not None:
                def reviewed(items, err) -> None:
                    ctrl.finish_preview_parts(items, err)
                    if err is None:
                        self._confirm_generate()

                self._run_task("Revisando las piezas", job, reviewed, "Piezas revisadas")
                return
        self._confirm_generate()

    def _confirm_generate(self) -> None:
        issues = self.ctrl.pregenerate_issues()
        bad, warn = issues["bloqueantes"], issues["avisos"]
        if not bad and not warn:
            self._start_generate()
            return
        lines = [("✗ " + t) for t in bad] + [("• " + t) for t in warn]
        detail = "\n".join(lines[:12]) + (f"\n… y {len(lines) - 12} más" if len(lines) > 12 else "")
        if bad:
            detail += "\n\nGenerar con esto probablemente falle o deje la unión mal."
        dlg = Gtk.AlertDialog(message="Antes de generar", detail=detail, buttons=["Cancelar", "Generar igual"],
                              cancel_button=0, default_button=0)

        def answered(d, res) -> None:
            try:
                i = d.choose_finish(res)
            except GLib.Error:
                return
            if i == 1:
                self._start_generate()

        dlg.choose(self, None, answered)

    def _start_generate(self) -> None:
        job = self.ctrl.begin_generate()
        if job is not None:
            self._run_task("Generando la figura", job, lambda outcome, err: self.ctrl.finish_generate(outcome, err), "Figura generada")

    def _on_export(self) -> None:
        dlg = Gtk.FileDialog(title="Carpeta donde exportar las piezas")

        def finished(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            outdir = f.get_path()
            job = self.ctrl.begin_export(outdir, orient=self.chk_orient.get_active(), flat=self.chk_flat.get_active())
            if job is None:
                return

            def done(info, err) -> None:
                self.ctrl.finish_export(info, outdir, err)
                if err is not None:
                    self._error(err)

            self._run_task("Exportando las piezas", job, done, "Piezas exportadas")

        dlg.select_folder(self, None, finished)

    def _on_apply_height(self) -> None:
        self.spin_height.update()            # toma lo tipeado aunque no se haya confirmado
        if not self.ctrl.set_ref_height(self.spin_height.get_value()):
            self._shown_height = self.ctrl.session.project.params.ref_height
            self.spin_height.set_value(self._shown_height)

    def _on_load_profile(self) -> None:
        names = self.ctrl.profile_names()
        if not names:
            return
        if self.ctrl.load_profile(names[self.dd_profile.get_selected()]):
            self._updating = True
            try:
                for key, spin in self.param_spins.items():
                    spin.set_value(self.ctrl.get_param(key))
            finally:
                self._updating = False

    def _on_save_profile(self) -> None:
        name = self.ent_profile.get_text().strip()
        if self.ctrl.save_profile(name):
            names = self.ctrl.profile_names()
            self.dd_profile.set_model(Gtk.StringList.new(names))
            self.dd_profile.set_selected(names.index(name) if name in names else 0)

    def _on_probes(self) -> None:
        dlg = Gtk.FileDialog(title="Carpeta donde guardar las probetas")
        which = ("hip", "shoulder", "hinge")[self.dd_probe.get_selected()]

        def finished(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            outdir = f.get_path()
            job = self.ctrl.begin_export_probes(outdir, which=which)
            if job is None:
                return

            def done(info, err) -> None:
                self.ctrl.finish_export_probes(info, outdir, which, err)
                if err is not None:
                    self._error(err)

            self._run_task("Generando las probetas", job, done, "Probetas generadas")

        dlg.select_folder(self, None, finished)

    def _on_head_replacement(self, *_):
        self._pick_file("Cabeza de reemplazo (STL)", ["*.stl"], "Archivos STL", self.ctrl.set_head_replacement)

    def _on_open_stl(self, *_):
        self._pick_file("Abrir STL", ["*.stl"], "Archivos STL", self._open_stl_task)

    def _open_stl_task(self, path: str) -> None:
        """Abre el STL en un hilo: leer y reparar la malla puede tardar (y la interfaz no debe congelarse)."""
        job = self.ctrl.begin_open_stl(path)
        if job is None:
            return

        def done(session, err) -> None:
            self.ctrl.finish_open_stl(path, session, err)
            if err is not None:
                self._error(err)

        self._run_task("Abriendo el STL", job, done, "STL abierto")

    def _on_open_project(self, *_):
        self._pick_file("Abrir proyecto", ["*.proyecto", "*.json"], "Proyectos", self._open_project_path)

    def _on_weapon_open(self, *_):
        self._pick_file("Arma (STL)", ["*.stl"], "Archivos STL", lambda path: self.ctrl.open_weapon(path))

    def _on_weapon_roll(self) -> None:
        self.ctrl.set_weapon_roll(None if self.chk_weapon_roll.get_active() else self.spin_weapon_roll.get_value())

    def _on_weapon_save(self, *_):
        self.ctrl.save_weapon(self.ent_weapon_id.get_text().strip() or None, self.chk_weapon_ver.get_active())

    def _refill_project_accessories(self) -> None:
        """Una casilla por accesorio del catálogo, marcada con cómo se lleva con la mano pegada; solo se rehace si algo cambió."""
        ctrl = self.ctrl
        metas = ctrl.accessory_pieces()
        labels = ctrl.project_accessory_labels() if metas else {}
        chosen = {a.pieza for a in ctrl.session.project.accesorios}
        sig = (tuple((m.ref, labels.get(m.ref, ""), m.ref in chosen) for m in metas), bool(ctrl.busy))
        if sig == self._acc_sig:
            return
        self._acc_sig = sig
        while (child := self.box_proj_acc.get_first_child()) is not None:
            self.box_proj_acc.remove(child)
        if not metas:
            lbl = Gtk.Label(label="El catálogo abierto no tiene accesorios: importá un arma arriba.", xalign=0, wrap=True)
            lbl.add_css_class("dim-label")
            self.box_proj_acc.append(lbl)
        for m in metas:
            text = m.ref + (f" — {labels[m.ref]}" if labels.get(m.ref) else "")
            chk = Gtk.CheckButton(label=text)
            chk.set_active(m.ref in chosen)
            chk.set_sensitive(not ctrl.busy)
            chk.connect("toggled", lambda b, r=m.ref: None if self._updating else self.ctrl.set_project_accessory(r, b.get_active()))
            self.box_proj_acc.append(chk)

    def _on_grip_pick(self, *_):
        self._pick_file("Mano estándar (STL)", ["*.stl"], "Archivos STL", self._grip_task)

    def _grip_task(self, path: str) -> None:
        """Busca el canal y taladra la mano en un hilo (tarda hasta un minuto y la interfaz no debe congelarse)."""
        job = self.ctrl.begin_grip(path)
        if job is None:
            return

        def done(res, err) -> None:
            self.ctrl.finish_grip(res, err)
            if err is not None:
                self._error(err)

        self._run_task("Taladrando la mano estándar", job, done, "Mano taladrada")

    def _save_as(self, title: str, name: str, done) -> None:
        dlg = Gtk.FileDialog(title=title)
        dlg.set_initial_name(name)

        def finished(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return                      # cancelado
            try:
                done(f.get_path())
            except Exception as e:          # noqa: BLE001 — se informa al usuario
                self._error(e)

        dlg.save(self, None, finished)

    def _grip_stem(self) -> str:
        g = self.ctrl.grip
        return os.path.splitext(os.path.basename(g.source))[0] if g is not None else "mano"

    def _on_grip_save_hand(self, *_):
        self._save_as("Guardar mano con agarre", f"{self._grip_stem()}_agarre.stl", self.ctrl.save_grip_hand)

    def _on_grip_save_plate(self, *_):
        self._save_as("Guardar placa de espadas de prueba", f"{self._grip_stem()}_espadas_prueba.stl", self.ctrl.save_grip_plate)

    def _on_save_hand(self, *_):
        dlg = Gtk.FileDialog(title="Guardar mano estándar")
        dlg.set_initial_name("mano_estandar.stl")

        def finished(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                self.ctrl.save_hand_extract(f.get_path())
            except Exception as e:          # noqa: BLE001
                self._error(e)

        dlg.save(self, None, finished)

    def _on_save_project(self, *_):
        dlg = Gtk.FileDialog(title="Guardar proyecto")
        stl = self.ctrl.session.stl_path
        dlg.set_initial_name((os.path.splitext(os.path.basename(stl))[0] if stl else "figura") + ".proyecto")

        def finished(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                self.ctrl.save_project(f.get_path())
            except Exception as e:          # noqa: BLE001
                self._error(e)

        dlg.save(self, None, finished)


class Application(Gtk.Application):
    def __init__(self, stl_path: str | None = None, project_path: str | None = None):
        super().__init__(application_id="ar.afm.ActionFigureMaker", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self._stl, self._project = stl_path, project_path
        self.win: MainWindow | None = None

    def do_activate(self) -> None:
        ctrl = Controller()
        self.win = MainWindow(self, ctrl)
        self.win.present()
        try:
            if self._project:
                try:
                    ctrl.open_project(self._project, self._stl)
                except CatalogNeeded as exc:                      # figura de catálogo sin STL: pedir la carpeta del catálogo
                    self.win._ask_catalog_for_project(self._project, exc)
                else:
                    self.win._review_on_open()
            elif self._stl:
                ctrl.open_stl(self._stl)
        except Exception as e:              # noqa: BLE001
            self.win._error(e)
