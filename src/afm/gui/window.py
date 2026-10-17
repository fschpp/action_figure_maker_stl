"""Ventana principal GTK4: visor 3D (Gtk.GLArea + moderngl) y barra lateral de planos.

Capa fina sobre `Controller` (toda la lógica está ahí y se prueba sin pantalla). Esta capa solo
traduce eventos de GTK (ratón, teclas, botones, diálogos) y dibuja con el contexto GL del GLArea.
"""
from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .controller import JOINT_PREFIX, PARAM_FIELDS, TRIM_OP_LABELS, TRIM_SHAPE_LABELS, TRIM_SHAPES, Controller, Mode  # noqa: E402
from .render import SceneRenderer  # noqa: E402

DRAG_THRESHOLD_PX = 4.0
# flechas -> (derecha, arriba) sobre la cara de contacto vista de frente
_ARROWS = {Gdk.KEY_Left: (-1, 0), Gdk.KEY_Right: (1, 0), Gdk.KEY_Up: (0, 1), Gdk.KEY_Down: (0, -1)}
_MODE_TITLES = {Mode.VIEW: "", Mode.CROWN: "  ·  marcando coronilla", Mode.SYMMETRY: "  ·  plano de simetría",
                Mode.LIMB_PLANE: "  ·  plano de un miembro", Mode.NECK_PLANE: "  ·  plano del cuello", Mode.POLYGON: "  ·  dibujando silueta",
                Mode.PIN: "  ·  colocando pasador", Mode.TRIM_PICK: "  ·  colocando recorte",
                Mode.JOINT_PLANE: "  ·  plano de articulación", Mode.JOINT_AXIS: "  ·  colocando eje de bisagra"}
_TRIM_OPS = ("subtract", "union")


def _section(title: str) -> Gtk.Label:
    lab = Gtk.Label(label=title, xalign=0)
    lab.add_css_class("heading")
    lab.set_margin_top(10)
    return lab


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

        self.set_titlebar(self._build_header())
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
        paned.set_start_child(self._build_viewer())
        paned.set_end_child(self._build_sidebar())
        paned.set_resize_start_child(True)
        paned.set_shrink_start_child(False)
        paned.set_resize_end_child(False)
        paned.set_shrink_end_child(False)
        self.set_child(paned)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self._refresh()

    # ------------------------------------------------------------ construcción
    def _build_header(self) -> Gtk.HeaderBar:
        bar = Gtk.HeaderBar()
        for label, cb in (("Abrir STL…", self._on_open_stl), ("Abrir proyecto…", self._on_open_project)):
            b = Gtk.Button(label=label)
            b.connect("clicked", cb)
            bar.pack_start(b)
        self.btn_save = Gtk.Button(label="Guardar proyecto…")
        self.btn_save.connect("clicked", self._on_save_project)
        bar.pack_end(self.btn_save)
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

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.gl.add_controller(scroll)
        return self.gl

    def _build_sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(10)

        box.append(_section("Figura"))
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Frente hacia"))
        self.dd_front = Gtk.DropDown.new_from_strings(["+Y", "-Y"])
        self.dd_front.connect("notify::selected", self._on_front)
        row.append(self.dd_front)
        box.append(row)
        self.btn_crown = self._button("Marcar coronilla (clic)", lambda *_: self.ctrl.start_crown())
        box.append(self.btn_crown)
        self.chk_side = Gtk.CheckButton(label="Trabajar un solo lado (oculta x < 0)")
        self.chk_side.connect("toggled", lambda b: None if self._updating else self.ctrl.set_one_side(b.get_active()))
        box.append(self.chk_side)
        self.chk_over = Gtk.CheckButton(label="Mostrar voladizos de impresión (rojo)")
        self.chk_over.connect("toggled", lambda b: None if self._updating else self.ctrl.set_show_overhangs(b.get_active()))
        box.append(self.chk_over)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Ángulo máximo desde la vertical (°)"))
        self.spin_over = Gtk.SpinButton.new_with_range(10, 80, 5)
        self.spin_over.set_value(self.ctrl.overhang_angle)
        self.spin_over.connect("value-changed", lambda b: None if self._updating else self.ctrl.set_overhang_angle(b.get_value()))
        row.append(self.spin_over)
        box.append(row)

        box.append(_section("Planos"))
        self.btn_sym = self._button("Plano de simetría (3 puntos)", lambda *_: self.ctrl.start_symmetry())
        self.btn_sym_auto = self._button("Simetría automática (x = 0)", lambda *_: self.ctrl.auto_symmetry())
        self.btn_arm = self._button("Agregar plano de brazo (3 puntos)", lambda *_: self.ctrl.start_limb_plane("arm"))
        self.btn_leg = self._button("Agregar plano de pierna (3 puntos)", lambda *_: self.ctrl.start_limb_plane("leg"))
        self.btn_neck = self._button("Agregar plano del cuello / cabeza (3 puntos)", lambda *_: self.ctrl.start_neck_plane())
        self.btn_knee = self._button("Agregar rodilla a una pierna (3 puntos)",
                                     lambda *_: self.ctrl.start_joint_plane_for_kind("leg"))
        self.btn_elbow = self._button("Agregar codo a un brazo (3 puntos)",
                                      lambda *_: self.ctrl.start_joint_plane_for_kind("arm"))
        self.btn_head_stl = self._button("Cabeza de reemplazo (STL)…", self._on_head_replacement)
        self.btn_head_orig = self._button("Volver a la cabeza original", lambda *_: self.ctrl.clear_head_replacement())
        for b in (self.btn_sym, self.btn_sym_auto, self.btn_arm, self.btn_leg, self.btn_neck,
                  self.btn_knee, self.btn_elbow, self.btn_head_stl, self.btn_head_orig):
            box.append(b)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Corte a (mm del plano de simetría)"))
        self.spin_cut = Gtk.SpinButton.new_with_range(0.5, 200.0, 0.5)
        self.spin_cut.set_digits(2)
        self.spin_cut.connect("value-changed", self._on_cut_offset)
        row.append(self.spin_cut)
        box.append(row)
        hint = Gtk.Label(label="Durante la selección: Retroceso deshace el último punto, Esc cancela.",
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        box.append(hint)
        self.planes_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.planes_box.add_css_class("boxed-list")
        box.append(self.planes_box)

        box.append(_section("Silueta del miembro"))
        hint2 = Gtk.Label(label="Elegí «Dibujar silueta» (lápiz) en un plano de la lista; «2.º» dibuja el segundo corte, de "
                                "frente (el miembro es lo que queda dentro de las dos siluetas: p. ej. en una pierna, la "
                                "columna del muslo + el escalón de la cadera). Clic = vértice sobre el plano; "
                                "cerrá con un clic en el primero o con Intro. Ya cerrado: clic en un vértice y luego "
                                "clic para moverlo; clic sobre un borde agrega uno; Retroceso borra el elegido.",
                          xalign=0, wrap=True)
        hint2.add_css_class("dim-label")
        box.append(hint2)
        self.btn_close_poly = self._button("Cerrar polígono (Intro)", lambda *_: self.ctrl.close_polygon())
        self.btn_propose = self._button("Proponer silueta automática", lambda *_: self.ctrl.propose_silhouette())
        self.btn_outline = self._button("Empezar desde el contorno de la figura", lambda *_: self.ctrl.use_outline_as_base())
        self.btn_raise = self._button("Subir el borde superior (2.º corte) hasta la otra silueta",
                                      lambda *_: self.ctrl.raise_top())
        self.btn_clear_poly = self._button("Vaciar y redibujar", lambda *_: self.ctrl.clear_polygon())
        self.btn_end_poly = self._button("Terminar edición de silueta (Esc)", lambda *_: self.ctrl.cancel())
        for b in (self.btn_close_poly, self.btn_propose, self.btn_outline, self.btn_raise, self.btn_clear_poly,
                  self.btn_end_poly):
            box.append(b)

        box.append(_section("Pasador de brazo / cadera"))
        hint_pin = Gtk.Label(label="Elegí «Colocar el pasador» (marcador) en un miembro de la lista de planos. Se muestra la cara de "
                                   "corte de frente, con el torso recortado: el círculo naranja es el vástago y el amarillo la "
                                   "cabeza del hongo; el punto verde es dónde lo pondría el programa. Clic sobre la cara = "
                                   "mover el pasador; flechas = ajustar 0,1 mm (Mayús: 1 mm). Si no lo tocás, se usa la "
                                   "posición automática.", xalign=0, wrap=True)
        hint_pin.add_css_class("dim-label")
        box.append(hint_pin)
        self.btn_pin_reset = self._button("Volver a la posición automática", lambda *_: self.ctrl.reset_pin())
        self.btn_pin_face = self._button("Mirar la cara de frente", lambda *_: self._pin_face_view())
        self.btn_pin_end = self._button("Terminar colocación del pasador (Esc)", lambda *_: self.ctrl.cancel())
        for b in (self.btn_pin_reset, self.btn_pin_face, self.btn_pin_end):
            box.append(b)

        box.append(_section("Espejo y pose sentada"))
        self.chk_mirror = Gtk.CheckButton(label="Espejo en vivo (trabaja un solo lado)")
        self.chk_mirror.connect("toggled", lambda b: None if self._updating else self.ctrl.set_mirror_live(b.get_active()))
        box.append(self.chk_mirror)
        hint_mirror = Gtk.Label(label="Muestra, en el lado oculto (x < 0), el reflejo de lo que hacés en el lado +X: la figura, "
                                      "las siluetas (también la que estás dibujando), los pasadores y los recortes. Es lo que "
                                      "sale al generar: un lado y su espejo.", xalign=0, wrap=True)
        hint_mirror.add_css_class("dim-label")
        box.append(hint_mirror)
        self.chk_pose = Gtk.CheckButton(label="Ver la figura generada en la pose de cadera")
        self.chk_pose.connect("toggled", lambda b: None if self._updating else self.ctrl.set_pose_view(b.get_active()))
        box.append(self.chk_pose)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Ángulo de cadera (°)", xalign=0, hexpand=True))
        self.spin_pose = Gtk.SpinButton.new_with_range(0, 180, 5)
        self.spin_pose.connect("value-changed", lambda b: None if self._updating else self.ctrl.set_pose_angle(b.get_value()))
        row.append(self.spin_pose)
        box.append(row)
        hint_pose = Gtk.Label(label="Hace falta haber generado la figura. Las piernas giran sobre el pasador de cadera (0° = de "
                                    "pie, 90° = sentada) y las piezas que chocan con el torso o entre sí se pintan de rojo; el "
                                    "resultado, con el límite de cadera tallado, queda en el Estado.", xalign=0, wrap=True)
        hint_pose.add_css_class("dim-label")
        box.append(hint_pose)

        box.append(_section("Recortes manuales"))
        hint_trim = Gtk.Label(label="Para los ajustes propios de cada figura (p. ej. la «esfera panza»): se dibuja un polígono "
                                    "sobre el plano de simetría (visto de costado) o se coloca una esfera / caja / cilindro con "
                                    "un clic en la figura; se extruye y se resta o se une a una pieza. Se hace sobre el lado +X y "
                                    "se espeja. Tiene que llegar a la superficie: un hueco cerrado por dentro se descarta.",
                              xalign=0, wrap=True)
        hint_trim.add_css_class("dim-label")
        box.append(hint_trim)
        for text, attr, items in (("Pieza", "dd_trim_target", ["torso"]),
                                  ("Forma", "dd_trim_shape", [TRIM_SHAPE_LABELS[k].capitalize() for k in TRIM_SHAPES]),
                                  ("Operación", "dd_trim_op", [TRIM_OP_LABELS[k].capitalize() for k in _TRIM_OPS])):
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=text, xalign=0, hexpand=True))
            dd = Gtk.DropDown.new_from_strings(items)
            setattr(self, attr, dd)
            row.append(dd)
            box.append(row)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Extrusión (mm)", xalign=0, hexpand=True))
        self.spin_trim_len = Gtk.SpinButton.new_with_range(0.1, 500.0, 1.0)
        self.spin_trim_len.set_value(25.0)
        row.append(self.spin_trim_len)
        box.append(row)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label="Radio / arista de la forma (mm)", xalign=0, hexpand=True))
        self.spin_trim_size = Gtk.SpinButton.new_with_range(0.5, 100.0, 0.5)
        self.spin_trim_size.set_value(5.0)
        row.append(self.spin_trim_size)
        box.append(row)
        self.btn_trim_add = self._button("Dibujar polígono / colocar con clic", lambda *_: self._on_trim_add())
        box.append(self.btn_trim_add)
        self.trims_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.trims_box.add_css_class("boxed-list")
        box.append(self.trims_box)

        box.append(_section("Parámetros"))
        self.param_spins: dict[str, Gtk.SpinButton] = {}
        for key, label, lo, hi, step, digits in PARAM_FIELDS:
            row = Gtk.Box(spacing=6)
            lbl = Gtk.Label(label=label, xalign=0, hexpand=True)
            row.append(lbl)
            spin = Gtk.SpinButton.new_with_range(lo, hi, step)
            spin.set_digits(digits)
            spin.set_value(self.ctrl.get_param(key))
            spin.connect("value-changed", lambda b, k=key: None if self._updating else self.ctrl.set_param(k, b.get_value()))
            row.append(spin)
            box.append(row)
            self.param_spins[key] = spin
        box.append(_section("Perfil de línea"))
        hint_prof = Gtk.Label(label="Un perfil guarda estos parámetros para reusarlos en la próxima figura de la línea.",
                              xalign=0, wrap=True)
        hint_prof.add_css_class("dim-label")
        box.append(hint_prof)
        row = Gtk.Box(spacing=6)
        self.dd_profile = Gtk.DropDown.new_from_strings(self.ctrl.profile_names() or ["(sin perfiles)"])
        self.dd_profile.set_hexpand(True)
        row.append(self.dd_profile)
        row.append(self._button("Cargar", lambda *_: self._on_load_profile()))
        box.append(row)
        row = Gtk.Box(spacing=6)
        self.ent_profile = Gtk.Entry(placeholder_text="Nombre del perfil", hexpand=True)
        row.append(self.ent_profile)
        row.append(self._button("Guardar", lambda *_: self._on_save_profile()))
        box.append(row)
        self.dd_probe = Gtk.DropDown.new_from_strings(["Pasador de cadera", "Pasador de hombro", "Bisagra de rodilla / codo"])
        box.append(self.dd_probe)
        box.append(self._button("Generar probetas de articulación…", lambda *_: self._on_probes()))

        box.append(_section("Generar"))
        self.btn_generate = self._button("Generar figura", lambda *_: self._on_generate())
        self.btn_generate.add_css_class("suggested-action")
        box.append(self.btn_generate)
        self.chk_orient = Gtk.CheckButton(label="Orientar las piezas para imprimir al exportar", active=True)
        box.append(self.chk_orient)
        self.btn_export = self._button("Exportar piezas…", lambda *_: self._on_export())
        box.append(self.btn_export)
        hint3 = Gtk.Label(label="Generar puede tardar varios minutos. Si cambiás algo después, hay que generar de nuevo "
                                "antes de exportar.", xalign=0, wrap=True)
        hint3.add_css_class("dim-label")
        box.append(hint3)

        box.append(_section("Vista"))
        views = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, column_spacing=4,
                            row_spacing=4)
        for label, name in (("Frente", "frente"), ("Espalda", "espalda"), ("Izquierda", "izquierda"),
                            ("Derecha", "derecha"), ("Arriba", "arriba"), ("Iso", "iso")):
            views.append(self._button(label, lambda _b, n=name: self._set_view(n)))
        box.append(views)
        reframe = self._button("Encuadrar figura", lambda *_: self._frame())
        box.append(reframe)

        box.append(_section("Estado"))
        self.lbl_status = Gtk.Label(xalign=0, wrap=True, selectable=True)
        box.append(self.lbl_status)
        self.lbl_problems = Gtk.Label(xalign=0, wrap=True)
        self.lbl_problems.add_css_class("dim-label")
        box.append(self.lbl_problems)

        self.sidebar_box = box
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(box)
        scroller.set_size_request(320, -1)
        return scroller

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
            self.lbl_status.set_text(ctrl.message)
            for key, spin in self.param_spins.items():
                if abs(spin.get_value() - ctrl.get_param(key)) > 1e-9:
                    spin.set_value(ctrl.get_param(key))
            probs = ctrl.problems()
            self.lbl_problems.set_text("Pendiente: " + "; ".join(probs) if probs else "Siluetas completas.")
            title = "Action Figure Maker"
            if ctrl.session.stl_path:
                title += " — " + os.path.basename(ctrl.session.stl_path)
            mode_title = "  ·  dibujando recorte" if ctrl.mode == Mode.POLYGON and ctrl.trim_cfg else _MODE_TITLES[ctrl.mode]
            self.set_title(title + mode_title)
            loaded = ctrl.loaded
            for b in (self.btn_crown, self.btn_sym, self.btn_sym_auto, self.btn_save, self.dd_front, self.chk_side,
                      self.chk_over, self.spin_over):
                b.set_sensitive(loaded)
            has_sym = ctrl.session.project.symmetry is not None
            self.btn_arm.set_sensitive(loaded and has_sym)
            self.btn_leg.set_sensitive(loaded and has_sym)
            self.btn_neck.set_sensitive(loaded and has_sym)
            self.btn_knee.set_sensitive(loaded and has_sym and bool(ctrl.session.project.limbs))
            self.btn_elbow.set_sensitive(loaded and has_sym and bool(ctrl.session.project.limbs))
            head = ctrl.session.project.head
            self.btn_head_stl.set_sensitive(loaded and head is not None)
            self.btn_head_orig.set_sensitive(loaded and head is not None and head.replacement_path is not None)
            off_limb = ctrl.offset_limb() if loaded else None
            self.spin_cut.set_sensitive(off_limb is not None)
            if off_limb is not None:
                self.spin_cut.set_value(ctrl.session.limb_offset(off_limb))
            self.chk_side.set_sensitive(loaded and has_sym)
            self.chk_side.set_active(ctrl.one_side)
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
            editing = ctrl.mode == Mode.POLYGON
            for b in (self.btn_close_poly, self.btn_propose, self.btn_outline, self.btn_clear_poly, self.btn_end_poly):
                b.set_sensitive(editing)
            self.btn_raise.set_sensitive(editing and ctrl.edit_cross)
            placing = ctrl.mode == Mode.PIN
            for b in (self.btn_pin_reset, self.btn_pin_face, self.btn_pin_end):
                b.set_sensitive(placing)
            self.spin_over.set_value(ctrl.overhang_angle)
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
        for e in self.ctrl.plane_entries():
            row = Gtk.Box(spacing=6)
            for side in ("start", "end", "top", "bottom"):
                getattr(row, f"set_margin_{side}")(4)
            vis = Gtk.CheckButton(active=e.visible, tooltip_text="Mostrar / ocultar")
            vis.connect("toggled", lambda _b, pid=e.id: None if self._updating else self.ctrl.toggle_plane(pid))
            row.append(vis)
            lab = Gtk.Label(label=e.label, xalign=0, hexpand=True)
            row.append(lab)
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
        ddx, ddy = dx - d["last"][0], dy - d["last"][1]
        d["last"] = (dx, dy)
        cam = self.ctrl.camera
        if d["button"] == 2 or d["pan"] or d["button"] == 3:
            cam.pan(ddx, ddy, max(self.gl.get_height(), 1))
        else:
            cam.orbit(ddx, ddy)
        self.gl.queue_render()

    def _on_drag_end(self, _g, _dx: float, _dy: float) -> None:
        d = self._drag
        if d["moved"] or d["button"] != 1:
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
        if self.ctrl.mode == Mode.PIN and keyval in _ARROWS:
            du, dv = _ARROWS[keyval]
            self.ctrl.nudge_pin(du, dv, big=bool(state & Gdk.ModifierType.SHIFT_MASK))
            return True
        return False

    # ------------------------------------------------------------------ acciones
    def _set_view(self, name: str) -> None:
        self.ctrl.camera.set_view(name)
        self.gl.queue_render()

    def _pin_face_view(self) -> None:
        self.ctrl.face_view()
        self.gl.queue_render()

    def _frame(self) -> None:
        if self.ctrl.loaded:
            self.ctrl.camera.frame(self.ctrl.session.work.bounds)
            self.gl.queue_render()

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

    def _on_generate(self) -> None:
        """Corre la generación en un hilo (la interfaz sigue viva) y vuelve al hilo principal para informar."""
        job = self.ctrl.begin_generate()
        if job is None:
            return

        def work() -> None:
            try:
                outcome = job(lambda text: GLib.idle_add(self._progress_idle, text))
            except Exception as e:          # noqa: BLE001 — se informa en el estado
                GLib.idle_add(self._finish_idle, None, e)
            else:
                GLib.idle_add(self._finish_idle, outcome, None)

        threading.Thread(target=work, daemon=True, name="afm-generate").start()

    def _progress_idle(self, text: str) -> bool:
        self.ctrl.report_progress(text)
        return False                        # no repetir

    def _finish_idle(self, outcome, error) -> bool:
        self.ctrl.finish_generate(outcome, error)
        return False

    def _on_export(self) -> None:
        dlg = Gtk.FileDialog(title="Carpeta donde exportar las piezas")

        def finished(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            try:
                self.ctrl.export(f.get_path(), orient=self.chk_orient.get_active())
            except Exception as e:          # noqa: BLE001
                self._error(e)

        dlg.select_folder(self, None, finished)

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
            try:
                self.ctrl.export_probes(f.get_path(), which=which)
            except Exception as e:          # noqa: BLE001
                self._error(e)

        dlg.select_folder(self, None, finished)

    def _on_head_replacement(self, *_):
        self._pick_file("Cabeza de reemplazo (STL)", ["*.stl"], "Archivos STL", self.ctrl.set_head_replacement)

    def _on_open_stl(self, *_):
        self._pick_file("Abrir STL", ["*.stl"], "Archivos STL", self.ctrl.open_stl)

    def _on_open_project(self, *_):
        self._pick_file("Abrir proyecto", ["*.proyecto", "*.json"], "Proyectos", self.ctrl.open_project)

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
