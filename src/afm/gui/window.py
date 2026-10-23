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

from ..head import MIN_BASE_IOU, REPLACEMENT_SCALE_RANGE, REPLACEMENT_UPS  # noqa: E402
from .controller import JOINT_PREFIX, PARAM_FIELDS, TRIM_OP_LABELS, TRIM_SHAPE_LABELS, TRIM_SHAPES, Controller, Mode  # noqa: E402
from .render import SceneRenderer  # noqa: E402
from .session import Session  # noqa: E402
from .viewcube import ViewCube, interpolate_view, view_angles  # noqa: E402

DRAG_THRESHOLD_PX = 4.0
# flechas -> (derecha, arriba) sobre la cara de contacto vista de frente
_ARROWS = {Gdk.KEY_Left: (-1, 0), Gdk.KEY_Right: (1, 0), Gdk.KEY_Up: (0, 1), Gdk.KEY_Down: (0, -1)}
_MODE_TITLES = {Mode.VIEW: "", Mode.CROWN: "  ·  marcando coronilla", Mode.SYMMETRY: "  ·  plano de simetría",
                Mode.LIMB_PLANE: "  ·  plano de un miembro", Mode.NECK_PLANE: "  ·  plano del cuello", Mode.HEAD_CUT_PLANE: "  ·  corte de la base de la cabeza", Mode.WAIST_PLANE: "  ·  plano de la cintura", Mode.HAND_EXTRACT: "  ·  muñeca: extraer mano", Mode.HAND_PLACE: "  ·  muñeca: pegar mano estándar", Mode.POLYGON: "  ·  dibujando silueta",
                Mode.PIN: "  ·  colocando pasador", Mode.TRIM_PICK: "  ·  colocando recorte",
                Mode.JOINT_PLANE: "  ·  plano de articulación", Mode.JOINT_AXIS: "  ·  colocando eje de bisagra",
                Mode.JOINT_LINES: "  ·  codo con 2 líneas (de frente)", Mode.JOINT_SIDE: "  ·  guías de la articulación (de costado)",
                Mode.FLAT_PLANE: "  ·  plano de corte sin soportes",
                Mode.HEAD_PIN: "  ·  pasador del cuello (de costado)"}
_TRIM_OPS = ("subtract", "union")


TAB_FIGURA, TAB_PLANOS, TAB_MIEMBROS, TAB_RECORTES, TAB_PARAMS, TAB_GENERAR = range(6)
CUBE_PX = 120
VIEW_ANIM_S = 0.25            # duración de la animación al elegir una vista en el cubo
SCALE_SPIN_MIN = 1e-6         # el control no admite 0 (la escala debe ser > 0); 1e-6 es el menor valor que muestra con 6 decimales
# Controles de la cabeza de reemplazo: (clave de Controller.set_head_adjust, rótulo, mín, máx, paso, decimales)
HEAD_ADJUST_FIELDS = (
    ("rx", "Cabeza de reemplazo: giro X (°)", -180.0, 180.0, 5.0, 1),
    ("ry", "Cabeza de reemplazo: giro Y (°)", -180.0, 180.0, 5.0, 1),
    ("yaw", "Cabeza de reemplazo: giro Z, sobre el cuello (°)", -360.0, 360.0, 15.0, 1),
    ("scale", "Cabeza de reemplazo: escala (× base del cuello)", SCALE_SPIN_MIN, REPLACEMENT_SCALE_RANGE[1], 0.05, 6),
)
HEAD_ADJUST_ATTR = {"rx": "replacement_rx", "ry": "replacement_ry", "yaw": "replacement_yaw",
                    "scale": "replacement_scale"}
HEAD_ADJUST_DEFAULTS = {"rx": 0.0, "ry": 0.0, "yaw": 0.0, "scale": 1.0}
PARAM_GROUPS = ("Generales", "Pasador de hombro", "Pasador de cadera", "Mecanismo de clic", "Rodilla / codo", "Cintura")
_CSS = b"""
.afm-section-title { font-weight: 700; padding: 4px 0; border-bottom: 1px solid alpha(currentColor, 0.2); }
.afm-sub-title { font-weight: 600; opacity: 0.85; padding: 2px 0; }
.afm-help { background: alpha(currentColor, 0.06); border-left: 3px solid alpha(currentColor, 0.30);
            border-radius: 4px; padding: 6px 8px; }
.afm-help-icon { opacity: 0.55; }
.afm-help-text { font-size: 0.9em; font-style: italic; opacity: 0.75; }
.afm-task { font-weight: 600; }
.afm-cube-btn { color: #e6e8ec; background: alpha(#000, 0.35); border-radius: 6px; padding: 2px 10px; min-height: 0; }
.afm-cube-btn:hover { background: alpha(#00ae42, 0.8); }
"""


def _param_group(key: str) -> str:
    """Subsección de la pestaña Parámetros a la que pertenece un campo de `PARAM_FIELDS`."""
    if key.startswith("shoulder_pin."):
        return PARAM_GROUPS[1]
    if key.startswith("hip_pin."):
        return PARAM_GROUPS[2]
    if key.startswith("detent.") or key.endswith("_detent"):
        return PARAM_GROUPS[3]
    if key.startswith("hinge_pin."):
        return PARAM_GROUPS[4]
    if key.startswith("waist_"):
        return PARAM_GROUPS[5]
    return PARAM_GROUPS[0]


def _collapsible(parent: Gtk.Box, title: str, expanded: bool = True, sub: bool = False) -> Gtk.Box:
    """Sección (o subsección, `sub=True`) plegable: agrega un `Gtk.Expander` a `parent` y devuelve su caja interior."""
    exp = Gtk.Expander(expanded=expanded)
    lab = Gtk.Label(label=title, xalign=0, hexpand=True)
    lab.add_css_class("afm-sub-title" if sub else "afm-section-title")
    exp.set_label_widget(lab)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    body.set_margin_top(6)
    body.set_margin_bottom(8)
    body.set_margin_start(12 if sub else 6)
    exp.set_child(body)
    exp.set_margin_top(4 if sub else 8)
    parent.append(exp)
    return body


def _help(parent: Gtk.Box, text: str) -> None:
    """Nota de ayuda: texto fijo (no se pliega ni se puede accionar), con ícono de información, fondo tenue y
    borde lateral para distinguirla de los controles que configuran algo."""
    box = Gtk.Box(spacing=8)
    box.add_css_class("afm-help")
    icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
    icon.set_valign(Gtk.Align.START)
    icon.add_css_class("afm-help-icon")
    lab = Gtk.Label(label=text, xalign=0, wrap=True, hexpand=True)
    lab.add_css_class("afm-help-text")
    box.append(icon)
    box.append(lab)
    parent.append(box)


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, ctrl: Controller):
        super().__init__(application=app, title="Action Figure Maker", default_width=1240, default_height=780)
        self.ctrl = ctrl
        self.ctrl.on_change = self._refresh
        self.mgl = None
        self.scene: SceneRenderer | None = None
        self._updating = False
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

    def _build_header(self) -> Gtk.HeaderBar:
        bar = Gtk.HeaderBar()
        self.btn_open = []
        for label, cb in (("Abrir STL…", self._on_open_stl), ("Abrir proyecto…", self._on_open_project)):
            b = Gtk.Button(label=label)
            b.connect("clicked", cb)
            bar.pack_start(b)
            self.btn_open.append(b)
        self.btn_save = Gtk.Button(label="Guardar proyecto…")
        self.btn_save.connect("clicked", self._on_save_project)
        bar.pack_end(self.btn_save)
        self.btn_export = Gtk.Button(label="Exportar piezas…")
        self.btn_export.connect("clicked", lambda *_: self._on_export())
        bar.pack_end(self.btn_export)                 # queda a la izquierda de «Guardar proyecto…»
        return bar

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

    def _build_status(self) -> Gtk.Widget:
        """Panel «Estado» al pie de la ventana: alto fijo de 3 líneas de texto (con scroll si el mensaje es más largo)."""
        self.status_view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                        left_margin=8, right_margin=8, top_margin=4, bottom_margin=4)
        self._status_buf = self.status_view.get_buffer()
        self._status_buf.create_tag("dim", foreground="gray", style=Pango.Style.ITALIC)
        self._status_text: tuple[str, str] | None = None
        line_h = self.status_view.create_pango_layout("Ag").get_pixel_size()[1]
        height = 3 * line_h + 8 + 2
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      vexpand=False, propagate_natural_height=False)
        scroller.set_min_content_height(height)
        scroller.set_max_content_height(height)
        scroller.set_size_request(-1, height)
        scroller.set_child(self.status_view)
        self._task: dict | None = None                       # tarea larga en curso (ver `_run_task`)
        self.spinner = Gtk.Spinner(visible=False)
        self.lbl_task = Gtk.Label(xalign=0)
        self.lbl_task.add_css_class("afm-task")
        head = Gtk.Box(spacing=6)
        head.append(Gtk.Label(label="Estado"))
        head.append(self.spinner)
        head.append(self.lbl_task)
        frame = Gtk.Frame()
        frame.set_label_widget(head)
        for side in ("start", "end", "bottom"):
            getattr(frame, f"set_margin_{side}")(6)
        frame.set_child(scroller)
        return frame

    def _set_status(self, message: str, problems: str) -> None:
        if self._status_text == (message, problems):          # no se pisa el scroll si no cambió nada
            return
        self._status_text = (message, problems)
        buf = self._status_buf
        buf.set_text(message)
        if problems:
            buf.insert_with_tags_by_name(buf.get_end_iter(), "\n" + problems, "dim")

    def _build_sidebar(self) -> Gtk.Widget:
        """Pestañas por tema (como Bambu Studio); cada pestaña tiene secciones y subsecciones plegables."""
        self.tabs = Gtk.Notebook(scrollable=True, tab_pos=Gtk.PositionType.TOP)
        self.tabs.set_size_request(400, -1)

        # ------------------------------------------------------------ Figura
        box = self._tab_page("Figura")
        sec = _collapsible(box, "Figura")
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Frente hacia", xalign=0, hexpand=True))
        self.dd_front = Gtk.DropDown.new_from_strings(["+Y", "-Y"])
        self.dd_front.connect("notify::selected", self._on_front)
        row.append(self.dd_front)
        sec.append(row)
        self.btn_crown = self._button("Marcar coronilla (clic)", lambda *_: self.ctrl.start_crown())
        sec.append(self.btn_crown)
        self.chk_side = Gtk.CheckButton(label="Trabajar un solo lado (oculta x < 0)")
        self.chk_side.connect("toggled", lambda b: None if self._updating else self.ctrl.set_one_side(b.get_active()))
        sec.append(self.chk_side)
        sub = _collapsible(sec, "Voladizos de impresión", expanded=False, sub=True)
        self.chk_over = Gtk.CheckButton(label="Mostrar voladizos de impresión (rojo)")
        self.chk_over.connect("toggled", lambda b: None if self._updating else self.ctrl.set_show_overhangs(b.get_active()))
        sub.append(self.chk_over)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Ángulo máximo desde la vertical (°)", xalign=0, hexpand=True))
        self.spin_over = Gtk.SpinButton.new_with_range(10, 80, 5)
        self.spin_over.set_value(self.ctrl.overhang_angle)
        self.spin_over.connect("value-changed", lambda b: None if self._updating else self.ctrl.set_overhang_angle(b.get_value()))
        row.append(self.spin_over)
        sub.append(row)

        sec = _collapsible(box, "Espejo y pose sentada")
        self.chk_mirror = Gtk.CheckButton(label="Espejo en vivo (trabaja un solo lado)")
        self.chk_mirror.connect("toggled", lambda b: None if self._updating else self.ctrl.set_mirror_live(b.get_active()))
        sec.append(self.chk_mirror)
        _help(sec, "Muestra, en el lado oculto (x < 0), el reflejo de lo que hacés en el lado +X: la figura, las siluetas "
                   "(también la que estás dibujando), los pasadores y los recortes. Es lo que sale al generar: un lado y su espejo.")
        self.chk_pose = Gtk.CheckButton(label="Ver la figura generada en la pose de cadera")
        self.chk_pose.connect("toggled", lambda b: None if self._updating else self.ctrl.set_pose_view(b.get_active()))
        sec.append(self.chk_pose)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Ángulo de cadera (°)", xalign=0, hexpand=True))
        self.spin_pose = Gtk.SpinButton.new_with_range(0, 180, 5)
        self.spin_pose.connect("value-changed", lambda b: None if self._updating else self.ctrl.set_pose_angle(b.get_value()))
        row.append(self.spin_pose)
        sec.append(row)
        _help(sec, "Hace falta haber generado la figura. Las piernas giran sobre el pasador de cadera (0° = de pie, 90° = "
                   "sentada) y las piezas que chocan con el torso o entre sí se pintan de rojo; el resultado, con el límite de "
                   "cadera tallado, queda en el Estado.")

        # ------------------------------------------------------------ Planos
        box = self._tab_page("Planos")
        sec = _collapsible(box, "Plano de simetría")
        self.btn_sym = self._button("Plano de simetría (3 puntos)", lambda *_: self.ctrl.start_symmetry())
        self.btn_sym_auto = self._button("Simetría automática (x = 0)", lambda *_: self.ctrl.auto_symmetry())
        for b in (self.btn_sym, self.btn_sym_auto):
            sec.append(b)

        sec = _collapsible(box, "Brazos y piernas")
        self.btn_arm = self._button("Agregar plano de brazo (3 puntos)", lambda *_: self.ctrl.start_limb_plane("arm"))
        self.btn_leg = self._button("Agregar plano de pierna (3 puntos)", lambda *_: self.ctrl.start_limb_plane("leg"))
        for b in (self.btn_arm, self.btn_leg):
            sec.append(b)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Corte a (mm del plano de simetría)", xalign=0, hexpand=True))
        self.spin_cut = Gtk.SpinButton.new_with_range(0.5, 200.0, 0.5)
        self.spin_cut.set_digits(2)
        self.spin_cut.connect("value-changed", self._on_cut_offset)
        row.append(self.spin_cut)
        sec.append(row)
        sub = _collapsible(sec, "Rodillas y codos", sub=True)
        self.btn_knee = self._button("Agregar rodilla a una pierna (3 puntos)",
                                     lambda *_: self.ctrl.start_joint_plane_for_kind("leg"))
        self.btn_elbow = self._button("Agregar codo a un brazo (3 puntos)",
                                      lambda *_: self.ctrl.start_joint_plane_for_kind("arm"))
        self.btn_elbow_lines = self._button("Agregar codo siguiendo el brazo (2 líneas, de frente)",
                                            lambda *_: self.ctrl.start_joint_lines_for_kind("arm"))
        for b in (self.btn_knee, self.btn_elbow, self.btn_elbow_lines):
            sub.append(b)

        sec = _collapsible(box, "Cintura")
        self.btn_waist = self._button("Agregar cintura: cortar el torso y girar a los costados (3 puntos)",
                                      lambda *_: self.ctrl.start_waist_plane())
        sec.append(self.btn_waist)

        sec = _collapsible(box, "Mano estándar")
        self.btn_hand = self._button("Extraer mano estándar de esta figura (3 puntos en la muñeca izquierda)",
                                     lambda *_: self.ctrl.start_hand_extract())
        self.btn_hand_invert = self._button("Invertir lado de la mano", lambda *_: self.ctrl.invert_hand_extract())
        self.btn_hand_save = self._button("Guardar mano estándar…", self._on_save_hand)
        self.btn_hand_drop = self._button("Descartar la mano extraída", lambda *_: self.ctrl.discard_hand_extract())
        for b in (self.btn_hand, self.btn_hand_invert, self.btn_hand_save, self.btn_hand_drop):
            sec.append(b)
        sec.append(Gtk.Label(label="Pegar la mano estándar en este modelo", xalign=0, margin_top=8))
        self.btn_hand_stl = self._button("Mano de reemplazo (STL)… (3 puntos en la muñeca)", self._on_hand_replacement)
        self.btn_hand_flip = self._button("Invertir lado de la mano del modelo", lambda *_: self.ctrl.invert_hand_place())
        self.btn_hand_orig = self._button("Volver a la mano original", lambda *_: self.ctrl.clear_hand())
        for b in (self.btn_hand_stl, self.btn_hand_flip):
            sec.append(b)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Giro de la mano (°)", xalign=0, hexpand=True))
        self.spin_hand_yaw = Gtk.SpinButton.new_with_range(-180.0, 180.0, 1.0)
        self.spin_hand_yaw.set_digits(0)
        self.spin_hand_yaw.connect("value-changed", self._on_hand_yaw)
        row.append(self.spin_hand_yaw)
        sec.append(row)
        self.chk_hand_swivel = Gtk.CheckButton(label="Mano articulada (pieza aparte, gira libre dentro del brazo)")
        self.chk_hand_swivel.connect("toggled", lambda b: None if self._updating else self.ctrl.set_hand_swivel(b.get_active()))
        sec.append(self.chk_hand_swivel)
        sec.append(self.btn_hand_orig)
        self.lbl_hand_fit = Gtk.Label(label="", xalign=0, wrap=True)
        sec.append(self.lbl_hand_fit)
        _help(sec, "Elegí el STL de la mano estándar y marcá 3 puntos en la muñeca de este modelo (cualquiera de los dos brazos). "
                   "El punto 1 va en el mismo lugar que al extraer la mano (p. ej. el lado del pulgar): así queda orientada sin "
                   "tocar el giro. La mano original del modelo se descarta y la estándar se pega sin escalarla (se ve en turquesa); "
                   "el otro brazo sale por espejo. La silueta del brazo tiene que incluir la mano nueva: «Generar figura» avisa si no. "
                   "«Mano articulada»: en vez de soldarla, la mano sale como pieza aparte con un pasador que entra en el brazo y "
                   "gira libre (se achica solo si la muñeca es fina; ver Parámetros → Mano articulada).")

        sec = _collapsible(box, "Piezas de catálogo")
        self.btn_cat_new = self._button("Crear catálogo de la línea (carpeta nueva)…", self._on_catalog_new)
        self.btn_cat_open = self._button("Abrir catálogo de la línea (carpeta)…", self._on_catalog_open)
        self.lbl_catalog = Gtk.Label(label="Sin catálogo abierto", xalign=0, wrap=True)
        self.btn_cat_add = self._button("Catalogar esta figura…", self._on_catalog_add)
        for w in (self.btn_cat_new, self.btn_cat_open, self.lbl_catalog, self.btn_cat_add):
            sec.append(w)
        self.btn_cat_forget = self._button("Olvidar el catálogo recordado", lambda *_: self.ctrl.forget_catalog())
        self.btn_cat_forget.set_tooltip_text("Cierra el catálogo y borra lo recordado: la próxima vez hay que abrirlo a mano. "
                                             "No toca la carpeta ni el proyecto.")
        self.btn_cat_review = self._button("Revisar las piezas elegidas (sin generar)", self._on_catalog_review)
        self.btn_cat_scale = self._button("Escalar la figura a la altura de la línea", lambda *_: self.ctrl.scale_to_line())
        self.btn_cat_align = self._button("Alinear todos los cortes a la línea", self._on_align_all)
        self.chk_part_preview = Gtk.CheckButton(label="Dibujar las piezas elegidas antes de generar",
                                                active=self.ctrl.show_part_preview)
        self.chk_part_preview.connect("toggled", lambda b: None if self._updating else self.ctrl.set_show_part_preview(b.get_active()))
        for w in (self.btn_cat_forget, self.btn_cat_review, self.btn_cat_scale, self.btn_cat_align, self.chk_part_preview):
            sec.append(w)
        sec.append(Gtk.Label(label="Pieza de cada lugar (al cambiarla hay que volver a generar)", xalign=0, margin_top=8))
        self.parts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sec.append(self.parts_box)
        self._parts_sig = None
        _help(sec, "«Crear catálogo» arma una carpeta nueva para la línea (nombre y altura de figura; es lo mismo que «afm catalogo "
                   "crear») y la deja abierta. Si ya tenés una, abrila con «Abrir catálogo». «Catalogar esta figura» guarda "
                   "sus piezas crudas (cabeza, torso, brazo, pierna, con los planos y siluetas que ya definiste) para usarlas en "
                   "otras figuras. En cada lugar elegí «la del modelo» o una pieza del catálogo: «Generar figura» la pone en el "
                   "puerto que el modelo tiene en ese lugar, sin escalarla, y la fila muestra cómo acopla (ok / aviso / no, "
                   "IoU y escalón entre las caras). La mano de catálogo se acopla en la muñeca del brazo de catálogo. «Revisar» mide el acople de cada pieza SIN "
                   "generar y la dibuja ya colocada (verde = ok, ámbar = aviso, rojo = no); «Ver por qué» superpone las dos "
                   "caras. Con un torso (o pelvis) de catálogo la figura sale entera del catálogo. El catálogo se reabre solo "
                   "al cargar la figura.")

        sec = _collapsible(box, "Cabeza")
        self.btn_neck = self._button("Agregar plano del cuello / cabeza (3 puntos)", lambda *_: self.ctrl.start_neck_plane())
        self.btn_head_stl = self._button("Cabeza de reemplazo (STL)…", self._on_head_replacement)
        self.btn_head_orig = self._button("Volver a la cabeza original", lambda *_: self.ctrl.clear_head_replacement())
        self.btn_head_cut = self._button("Cortar la base de la cabeza de reemplazo (3 puntos)", lambda *_: self.ctrl.start_head_cut_plane())
        self.btn_head_uncut = self._button("Quitar el corte de la base", lambda *_: self.ctrl.clear_head_cut_plane())
        self.btn_head_pin = self._button("Elegir dónde va el pasador del cuello (vista de costado)", lambda *_: self.ctrl.start_head_pin())
        self.btn_head_pin_reset = self._button("Pasador del cuello: volver a la posición automática", lambda *_: self.ctrl.reset_head_pin())
        for b in (self.btn_neck, self.btn_head_stl, self.btn_head_orig, self.btn_head_cut, self.btn_head_uncut,
                  self.btn_head_pin, self.btn_head_pin_reset):
            sec.append(b)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Cabeza de reemplazo: eje «arriba» del STL", xalign=0, hexpand=True))
        self.dd_head_up = Gtk.DropDown.new_from_strings(list(REPLACEMENT_UPS))
        self.dd_head_up.connect("notify::selected", self._on_head_up)
        row.append(self.dd_head_up)
        sec.append(row)
        # (clave de HeadSpec/set_head_adjust, rótulo, mínimo, máximo, paso, decimales)
        self.head_spins: dict[str, Gtk.SpinButton] = {}
        for key, label, lo, hi, step, digits in HEAD_ADJUST_FIELDS:
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            spin = Gtk.SpinButton.new_with_range(lo, hi, step)
            spin.set_digits(digits)
            spin.set_value(HEAD_ADJUST_DEFAULTS[key])
            spin.connect("value-changed", lambda b, k=key: self._on_head_adjust(k, b))
            self.head_spins[key] = spin
            row.append(spin)
            sec.append(row)
        self.spin_head_yaw = self.head_spins["yaw"]
        self.lbl_head_fit = Gtk.Label(label="", xalign=0, wrap=True)
        sec.append(self.lbl_head_fit)
        _help(sec, "Pasador del cuello: «Elegir dónde va…» pone la vista de costado (el cuello se ve de canto). Un clic lo mueve \
adelante o atrás sobre la cara del cuello (siempre centrado en x = 0); con flechas se ajusta de a 0,1 mm (Mayús, 1 mm). \
En naranja se ve el contorno del pasador (vástago y cabeza); en verde, la posición automática.")
        _help(sec, "La cabeza de reemplazo se ve en naranja sobre el cuello (la original se oculta). Orientación: eje «arriba» "
                   "y giros X / Y / Z (los giros se aplican en ese orden). Escala: 1,00 deja la base igual a la del cuello; "
                   "más o menos agranda o achica la cabeza entera. Corte de la base: elegí 3 puntos sobre la cabeza naranja; el plano "
                   "que forman pasa a ser la base (lo de abajo se descarta) y acompaña a la malla si después la girás. "
                   "Si la base coincide poco con la del cuello, probá otro giro, escala o corte: el ajuste se recalcula al instante.")

        sec = _collapsible(box, "Lista de planos")
        _help(sec, "Durante la selección: Retroceso deshace el último punto, Esc cancela.")
        self.chk_show: dict[str, Gtk.CheckButton] = {}
        for group, label, tip in (
                ("planes", "Mostrar todos los planos", "Interruptor general: se combina con el de cada plano de la lista"),
                ("pins", "Mostrar todos los pines de articulación",
                 "Marcas de los pasadores (hombro, cadera, cuello) y ejes de las bisagras (rodilla, codo)"),
                ("contours", "Mostrar todos los contornos", "Siluetas de los miembros (1.º y 2.º corte) y recortes manuales")):
            chk = Gtk.CheckButton(label=label, active=True, tooltip_text=tip)
            chk.connect("toggled", lambda b, g=group: None if self._updating else self.ctrl.set_group_visible(g, b.get_active()))
            sec.append(chk)
            self.chk_show[group] = chk
        self.planes_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.planes_box.add_css_class("boxed-list")
        sec.append(self.planes_box)
        self.chk_cut_numbers = Gtk.CheckButton(label="Numerar los cortes en el visor", active=self.ctrl.show_cut_numbers)
        self.chk_cut_numbers.connect("toggled", lambda b: None if self._updating else self.ctrl.set_show_cut_numbers(b.get_active()))
        sec.append(self.chk_cut_numbers)

        # --------------------------------------------------------- Miembros
        box = self._tab_page("Miembros")
        sec = _collapsible(box, "Silueta del miembro")
        _help(sec, "Elegí «Dibujar silueta» (lápiz) en un plano de la lista; «2.º» dibuja el segundo corte, de frente (el "
                   "miembro es lo que queda dentro de las dos siluetas: p. ej. en una pierna, la columna del muslo + el escalón "
                   "de la cadera). Clic = vértice sobre el plano; cerrá con un clic en el primero o con Intro. Ya cerrado: clic "
                   "en un vértice y luego clic para moverlo; clic sobre un borde agrega uno; Retroceso borra el elegido.")
        self.btn_close_poly = self._button("Cerrar polígono (Intro)", lambda *_: self.ctrl.close_polygon())
        self.btn_propose = self._button("Proponer silueta automática", lambda *_: self.ctrl.propose_silhouette())
        self.btn_outline = self._button("Empezar desde el contorno de la figura", lambda *_: self.ctrl.use_outline_as_base())
        self.btn_raise = self._button("Subir el borde superior (2.º corte) hasta la otra silueta",
                                      lambda *_: self.ctrl.raise_top())
        self.btn_clear_poly = self._button("Vaciar y redibujar", lambda *_: self.ctrl.clear_polygon())
        self.btn_end_poly = self._button("Terminar edición de silueta (Esc)", lambda *_: self.ctrl.cancel())
        for b in (self.btn_close_poly, self.btn_propose, self.btn_outline, self.btn_raise, self.btn_clear_poly,
                  self.btn_end_poly):
            sec.append(b)

        sec = _collapsible(box, "Pasador de brazo / cadera")
        _help(sec, "Elegí «Colocar el pasador» (marcador) en un miembro de la lista de planos. Se muestra la cara de corte de "
                   "frente, con el torso recortado: el círculo naranja es el vástago y el amarillo la cabeza del hongo; el punto "
                   "verde es dónde lo pondría el programa. Clic sobre la cara = mover el pasador; flechas = ajustar 0,1 mm "
                   "(Mayús: 1 mm). Si no lo tocás, se usa la posición automática.")
        self.btn_pin_reset = self._button("Volver a la posición automática", lambda *_: self.ctrl.reset_pin())
        self.btn_pin_face = self._button("Mirar la cara de frente", lambda *_: self._pin_face_view())
        self.btn_pin_end = self._button("Terminar colocación del pasador (Esc)", lambda *_: self.ctrl.cancel())
        for b in (self.btn_pin_reset, self.btn_pin_face, self.btn_pin_end):
            sec.append(b)

        # --------------------------------------------------------- Recortes
        box = self._tab_page("Recortes")
        sec = _collapsible(box, "Recortes manuales")
        _help(sec, "Para los ajustes propios de cada figura (p. ej. la «esfera panza»): se dibuja un polígono sobre el plano de "
                   "simetría (visto de costado) o se coloca una esfera / caja / cilindro con un clic en la figura; se extruye y "
                   "se resta o se une a una pieza. Se hace sobre el lado +X y se espeja. Tiene que llegar a la superficie: un "
                   "hueco cerrado por dentro se descarta.")
        for text, attr, items in (("Pieza", "dd_trim_target", ["torso"]),
                                  ("Forma", "dd_trim_shape", [TRIM_SHAPE_LABELS[k].capitalize() for k in TRIM_SHAPES]),
                                  ("Operación", "dd_trim_op", [TRIM_OP_LABELS[k].capitalize() for k in _TRIM_OPS])):
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=text, xalign=0, hexpand=True))
            dd = Gtk.DropDown.new_from_strings(items)
            setattr(self, attr, dd)
            row.append(dd)
            sec.append(row)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Extrusión (mm)", xalign=0, hexpand=True))
        self.spin_trim_len = Gtk.SpinButton.new_with_range(0.1, 500.0, 1.0)
        self.spin_trim_len.set_value(25.0)
        row.append(self.spin_trim_len)
        sec.append(row)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Radio / arista de la forma (mm)", xalign=0, hexpand=True))
        self.spin_trim_size = Gtk.SpinButton.new_with_range(0.5, 100.0, 0.5)
        self.spin_trim_size.set_value(5.0)
        row.append(self.spin_trim_size)
        sec.append(row)
        self.btn_trim_add = self._button("Dibujar polígono / colocar con clic", lambda *_: self._on_trim_add())
        sec.append(self.btn_trim_add)
        sec = _collapsible(box, "Recortes de la figura")
        self.trims_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.trims_box.add_css_class("boxed-list")
        sec.append(self.trims_box)

        # ------------------------------------------------------- Parámetros
        box = self._tab_page("Parámetros")
        sec = _collapsible(box, "Altura de la figura")
        row = Gtk.Box(spacing=6)
        lbl = Gtk.Label(label="Pies → coronilla (mm)", xalign=0, hexpand=True)
        lbl.set_tooltip_text("Reescala la figura (y los planos, siluetas y recortes ya hechos) a esta altura. "
                             "Los pasadores, la holgura y el clic son medidas absolutas y no se escalan.")
        row.append(lbl)
        lo, hi = Session.REF_HEIGHT_RANGE
        self.spin_height = Gtk.SpinButton.new_with_range(lo, hi, 1.0)
        self.spin_height.set_digits(1)
        self._shown_height = self.ctrl.session.project.params.ref_height
        self.spin_height.set_value(self._shown_height)
        self.spin_height.connect("activate", lambda *_: self._on_apply_height())
        row.append(self.spin_height)
        row.append(self._button("Aplicar", lambda *_: self._on_apply_height()))
        sec.append(row)

        sec = _collapsible(box, "Parámetros de articulación")
        groups = {name: _collapsible(sec, name, expanded=(name == PARAM_GROUPS[0]), sub=True) for name in PARAM_GROUPS}
        self.param_spins: dict[str, Gtk.SpinButton] = {}
        for key, label, lo, hi, step, digits in PARAM_FIELDS:
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=label, xalign=0, hexpand=True, wrap=True))
            spin = Gtk.SpinButton.new_with_range(lo, hi, step)
            spin.set_digits(digits)
            spin.set_value(self.ctrl.get_param(key))
            spin.connect("value-changed", lambda b, k=key: None if self._updating else self.ctrl.set_param(k, b.get_value()))
            row.append(spin)
            groups[_param_group(key)].append(row)
            self.param_spins[key] = spin

        sec = _collapsible(box, "Perfil de línea", expanded=False)
        _help(sec, "Un perfil guarda estos parámetros para reusarlos en la próxima figura de la línea.")
        row = Gtk.Box(spacing=6)
        self.dd_profile = Gtk.DropDown.new_from_strings(self.ctrl.profile_names() or ["(sin perfiles)"])
        self.dd_profile.set_hexpand(True)
        row.append(self.dd_profile)
        row.append(self._button("Cargar", lambda *_: self._on_load_profile()))
        sec.append(row)
        row = Gtk.Box(spacing=6)
        self.ent_profile = Gtk.Entry(placeholder_text="Nombre del perfil", hexpand=True)
        row.append(self.ent_profile)
        row.append(self._button("Guardar", lambda *_: self._on_save_profile()))
        sec.append(row)

        sec = _collapsible(box, "Probetas de articulación", expanded=False)
        self.dd_probe = Gtk.DropDown.new_from_strings(["Pasador de cadera", "Pasador de hombro", "Bisagra de rodilla / codo"])
        sec.append(self.dd_probe)
        sec.append(self._button("Generar probetas de articulación…", lambda *_: self._on_probes()))

        # ---------------------------------------------------------- Generar
        box = self._tab_page("Generar")
        sec = _collapsible(box, "Generar")
        self.btn_generate = self._button("Generar figura", lambda *_: self._on_generate())
        self.btn_generate.add_css_class("suggested-action")
        sec.append(self.btn_generate)
        _help(sec, "Generar puede tardar varios minutos. Si cambiás algo después, hay que generar de nuevo antes de exportar.")

        sec = _collapsible(box, "Exportar")
        self.chk_orient = Gtk.CheckButton(label="Apoyar las piezas en la cama al exportar (en la pose de cadera, cabeza arriba)", active=True)
        sec.append(self.chk_orient)
        sub = _collapsible(sec, "Impresión sin soportes", sub=True)
        self.chk_flat = Gtk.CheckButton(label="Modificar para imprimir sin soportes (aplana piernas y cola; los brazos giran hacia adelante)")
        self.chk_flat.set_tooltip_text("Corta con un plano horizontal sobre la cola (altura en Parámetros: «Sin soportes: corte plano») y "
                                       "gira los brazos hacia adelante lo justo para que apoyen en ese plano. Las piernas quedan estiradas (rodilla a 0°): muslo y "
                                       "pantorrilla horizontales sobre el plano. Los brazos no se modifican.")
        self.chk_flat.connect("toggled", lambda b: None if self._updating else self._on_flat_toggled(b.get_active()))
        sub.append(self.chk_flat)
        self.chk_fill_hip = Gtk.CheckButton(label="Rellenar los muslos hasta la pelvis recortada")
        self.chk_fill_hip.set_tooltip_text("El corte plano le saca a la pelvis una loncha y de pie queda un hueco contra el muslo. Con esto el muslo se \
queda con esa loncha y la pelvis se talla de nuevo alrededor (el muslo gira libre hasta el límite de cadera). Se nota al \
exportar; tarda un poco más.")
        self.chk_fill_hip.connect("toggled", lambda b: None if self._updating else self._on_fill_hip_toggled(b))
        sub.append(self.chk_fill_hip)
        self.btn_flat_plane = self._button("Ajustar la altura del corte (vista Derecha, figura sentada)", lambda *_: self._on_flat_plane())
        self.btn_flat_plane.set_tooltip_text("Muestra la figura generada sentada, de costado, con el plano horizontal del corte «sin soportes». "
                                             "Clic a la altura que querés, o ↑ ↓ (Mayús = 1 mm). Esc termina.")
        sub.append(self.btn_flat_plane)
        self.btn_flat_click = self._button("Ajustar el clic del brazo para que apoye sin soportes (hay que generar de nuevo)",
                                           lambda *_: self._on_flat_click())
        self.btn_flat_click.set_tooltip_text("El brazo con clic no se puede girar después de generar: su pose la fija la fase de los clics. "
                                             "Pone «Clic: ángulo del brazo al imprimir» en el ángulo en que el brazo apoya en el plano del corte.")
        sub.append(self.btn_flat_click)
        _help(sec, "Las piezas se exportan con el botón «Exportar piezas…» de la barra superior, junto a «Guardar proyecto…».")

        self.sidebar_box = self.tabs                  # lo que se desactiva mientras se genera
        return self.tabs

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

    # --------------------------------------------------------------- refresco
    def _refresh(self) -> None:
        ctrl = self.ctrl
        self._updating = True
        try:
            for key, spin in self.param_spins.items():
                if abs(spin.get_value() - ctrl.get_param(key)) > 1e-9:
                    spin.set_value(ctrl.get_param(key))
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
            title = "Action Figure Maker"
            if ctrl.session.stl_path:
                title += " — " + os.path.basename(ctrl.session.stl_path)
            mode_title = "  ·  dibujando recorte" if ctrl.mode == Mode.POLYGON and ctrl.trim_cfg else _MODE_TITLES[ctrl.mode]
            self.set_title(title + mode_title)
            loaded = ctrl.loaded
            for b in (self.btn_crown, self.btn_sym, self.btn_sym_auto, self.dd_front, self.chk_side,
                      self.chk_over, self.spin_over):
                b.set_sensitive(loaded)
            self.btn_save.set_sensitive(loaded and not ctrl.busy)
            for b in self.btn_open:
                b.set_sensitive(not ctrl.busy)          # no se cambia de archivo en medio de una tarea
            has_sym = ctrl.session.project.symmetry is not None
            self.btn_arm.set_sensitive(loaded and has_sym)
            self.btn_leg.set_sensitive(loaded and has_sym)
            self.btn_neck.set_sensitive(loaded and has_sym)
            self.btn_waist.set_sensitive(loaded and has_sym)
            self.btn_hand.set_sensitive(loaded and has_sym)
            has_hand = loaded and ctrl.hand_extract is not None
            for b in (self.btn_hand_invert, self.btn_hand_save, self.btn_hand_drop):
                b.set_sensitive(has_hand)
            self.btn_hand_stl.set_sensitive(loaded and has_sym)
            placed = ctrl.session.project.hand if loaded else None
            for b in (self.btn_hand_flip, self.btn_hand_orig, self.spin_hand_yaw, self.chk_hand_swivel):
                b.set_sensitive(placed is not None)
            self.chk_hand_swivel.set_active(placed is not None and placed.join == "giro")
            if placed is not None and abs(self.spin_hand_yaw.get_value() - placed.yaw) > 1e-9:
                self.spin_hand_yaw.set_value(placed.yaw)
            fit = ctrl.hand_fit() if placed is not None else None
            self.lbl_hand_fit.set_text("" if fit is None else
                                       f"Muñeca del modelo Ø{2 * fit['info']['radio_brazo_mm']:.1f} mm · de la mano "
                                       f"Ø{2 * fit['info']['radio_mano_mm']:.1f} mm (escalón {fit['info']['escalon_mm']:.1f} mm)")
            self._sync_catalog(loaded)
            self.btn_knee.set_sensitive(loaded and has_sym and bool(ctrl.session.project.limbs))
            self.btn_elbow.set_sensitive(loaded and has_sym and bool(ctrl.session.project.limbs))
            self.btn_elbow_lines.set_sensitive(loaded and has_sym and bool(ctrl.session.project.limbs))
            head = ctrl.session.project.head
            self.btn_head_stl.set_sensitive(loaded and head is not None)
            self.btn_head_pin.set_sensitive(loaded and head is not None)
            self.btn_head_pin_reset.set_sensitive(loaded and head is not None and head.pin_center is not None)
            self.btn_head_orig.set_sensitive(loaded and head is not None and head.replacement_path is not None)
            rep = loaded and head is not None and head.replacement_path is not None
            for b in (self.dd_head_up, self.btn_head_cut, *self.head_spins.values()):
                b.set_sensitive(bool(rep))
            self.btn_head_uncut.set_sensitive(bool(rep) and head.replacement_cut_points is not None)
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
            self.btn_trim_add.set_sensitive(loaded and has_sym)
            self.sidebar_box.set_sensitive(not ctrl.busy)
            self.btn_generate.set_sensitive(loaded and not ctrl.busy)
            self.btn_export.set_sensitive(ctrl.session.outcome is not None and not ctrl.busy)
            self.btn_flat_plane.set_sensitive(ctrl.session.outcome is not None and not ctrl.busy)
            self.btn_flat_click.set_sensitive(ctrl.session.outcome is not None and not ctrl.busy)
            editing = ctrl.mode == Mode.POLYGON
            for b in (self.btn_close_poly, self.btn_propose, self.btn_outline, self.btn_clear_poly, self.btn_end_poly):
                b.set_sensitive(editing)
            self.btn_raise.set_sensitive(editing and ctrl.edit_cross)
            placing = ctrl.mode == Mode.PIN
            for b in (self.btn_pin_reset, self.btn_pin_face, self.btn_pin_end):
                b.set_sensitive(placing)
            self.spin_over.set_value(ctrl.overhang_angle)
            self.chk_fill_hip.set_active(ctrl.session.project.params.flat_fill_hip)
            self.chk_fill_hip.set_sensitive(self.chk_flat.get_active())
            self.dd_front.set_selected(0 if ctrl.session.project.front == "+Y" else 1)
            self._rebuild_plane_list()
            self._rebuild_trim_ui()
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
        for e in self.ctrl.plane_entries():
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

    def _on_gl_motion(self, _c, x: float, y: float) -> None:
        """Marca el vértice cercano al puntero (modos de elegir puntos). Se agrupan los eventos: la búsqueda traza un rayo."""
        self._hover_pos = (x, y)
        if self.ctrl.hover_active() and not self._hover_timer:
            self._hover_timer = GLib.timeout_add(30, self._hover_tick)

    def _hover_tick(self) -> bool:
        self._hover_timer = 0
        pos = self._hover_pos
        if pos is not None and not self._drag["moved"] and self.ctrl.update_hover(
                pos[0], pos[1], max(self.gl.get_width(), 1), max(self.gl.get_height(), 1)):
            self.gl.queue_render()
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
        if self.ctrl.mode == Mode.HEAD_PIN and keyval in _ARROWS:
            du, dv = _ARROWS[keyval]
            self.ctrl.nudge_head_pin(du, dv, big=bool(state & Gdk.ModifierType.SHIFT_MASK))
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
        self.btn_cat_add.set_sensitive(bool(loaded and root and not ctrl.busy))
        self.lbl_catalog.set_text("Sin catálogo abierto" if not root else f"Catálogo: {root}")
        self.btn_cat_forget.set_sensitive(bool(root and not ctrl.busy))
        self.btn_cat_review.set_sensitive(bool(loaded and root and ctrl.session.project.parts and not ctrl.busy))
        self.btn_cat_scale.set_sensitive(bool(loaded and root and not ctrl.busy))
        self.btn_cat_align.set_sensitive(bool(loaded and root and ctrl.session.project.parts and not ctrl.busy))
        scale = ctrl.line_scale() if root else None
        self.btn_cat_scale.set_label("Escalar la figura a la altura de la línea" if scale is None else
                                     f"Escalar la figura a la altura de la línea (×{scale:.3f})".replace(".", ","))
        rows = ctrl.catalog_rows() if root else []
        choices = {r["lugar"]: [m.ref for m in ctrl.catalog_choices(r["lugar"])] for r in rows}
        sig = (root, tuple((r["lugar"], r["pieza"], ctrl.catalog_row_text(r), tuple(choices[r["lugar"]]), r.get("nivel"),
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
            line.append(Gtk.Label(label=lugar.capitalize(), xalign=0, hexpand=True))
            dd = Gtk.DropDown.new_from_strings(["(la del modelo)"] + refs)
            cur = r["pieza"]
            # la pieza guardada puede traer versión fija («id@2»): se muestra la fila de esa pieza si está en la lista
            idx = 0 if cur is None else next((i + 1 for i, ref in enumerate(refs) if ref == cur or ref.split("@")[0] == cur.split("@")[0]), 0)
            dd.set_selected(idx)
            dd.connect("notify::selected", self._on_part_selected, lugar, refs)
            line.append(dd)
            box.append(line)
            if r["pieza"] is not None:
                lbl = Gtk.Label(label=ctrl.catalog_row_text(r), xalign=0, wrap=True)
                lbl.add_css_class("dim-label")
                box.append(lbl)
                if r.get("previa"):
                    box.append(self._part_actions(lugar, r))
            self.parts_box.append(box)

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

    def _on_catalog_review(self, *_):
        job = self.ctrl.begin_preview_parts()
        if job is not None:
            self._run_task("Revisando las piezas", job, lambda items, err: self.ctrl.finish_preview_parts(items, err),
                           "Piezas revisadas")

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
                self.ctrl.open_catalog(f.get_path())
            except Exception as e:          # noqa: BLE001
                self._error(e)

        dlg.select_folder(self, None, finished)

    def _on_catalog_add(self, *_):
        import re

        stl = self.ctrl.session.stl_path
        prefix = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(os.path.basename(stl or "figura"))[0].lower()).strip("-") or "figura"
        self._run_task("Catalogando la figura…", lambda progress: self.ctrl.session.catalog_add(prefix),
                       lambda res, err: self.ctrl.catalog_add_done(res, err), "Figura catalogada")

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
        self.ctrl.set_front(front)

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
        self._task = {"label": label, "done": done_label, "t0": time.monotonic()}
        self.spinner.set_visible(True)
        self.spinner.start()
        self._task_tick()
        GLib.timeout_add(200, self._task_tick)

        def work() -> None:
            try:
                result = job(lambda text: GLib.idle_add(self._progress_idle, text))
            except Exception as e:          # noqa: BLE001 — se informa en el estado
                GLib.idle_add(self._task_done_idle, done, None, e)
            else:
                GLib.idle_add(self._task_done_idle, done, result, None)

        threading.Thread(target=work, daemon=True, name="afm-task").start()

    def _task_tick(self) -> bool:
        t = self._task
        if t is None:
            return False                    # terminó: se corta el temporizador
        self.lbl_task.set_text(f"{t['label']} · {self._fmt_elapsed(time.monotonic() - t['t0'])}")
        return True

    def _task_done_idle(self, done, result, error) -> bool:
        t, self._task = self._task, None
        self.spinner.stop()
        self.spinner.set_visible(False)
        took = self._fmt_elapsed(time.monotonic() - t["t0"]) if t else ""
        self.lbl_task.set_text((f"✗ Falló a los {took}" if error else f"✓ {t['done']} en {took}") if t else "")
        done(result, error)
        return False

    def _progress_idle(self, text: str) -> bool:
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
        self._pick_file("Abrir proyecto", ["*.proyecto", "*.json"], "Proyectos", self.ctrl.open_project)

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
                ctrl.open_project(self._project, self._stl)
            elif self._stl:
                ctrl.open_stl(self._stl)
        except Exception as e:              # noqa: BLE001
            self.win._error(e)
