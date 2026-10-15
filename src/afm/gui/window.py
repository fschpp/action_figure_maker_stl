"""Ventana principal GTK4: visor 3D (Gtk.GLArea + moderngl) y barra lateral de planos.

Capa fina sobre `Controller` (toda la lógica está ahí y se prueba sin pantalla). Esta capa solo
traduce eventos de GTK (ratón, teclas, botones, diálogos) y dibuja con el contexto GL del GLArea.
"""
from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .controller import Controller, Mode  # noqa: E402
from .render import SceneRenderer  # noqa: E402

DRAG_THRESHOLD_PX = 4.0
_MODE_TITLES = {Mode.VIEW: "", Mode.CROWN: "  ·  marcando coronilla", Mode.SYMMETRY: "  ·  plano de simetría",
                Mode.LIMB_PLANE: "  ·  plano de un miembro"}


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

        box.append(_section("Planos"))
        self.btn_sym = self._button("Plano de simetría (3 puntos)", lambda *_: self.ctrl.start_symmetry())
        self.btn_sym_auto = self._button("Simetría automática (x = 0)", lambda *_: self.ctrl.auto_symmetry())
        self.btn_arm = self._button("Agregar plano de brazo (3 puntos)", lambda *_: self.ctrl.start_limb_plane("arm"))
        self.btn_leg = self._button("Agregar plano de pierna (3 puntos)", lambda *_: self.ctrl.start_limb_plane("leg"))
        for b in (self.btn_sym, self.btn_sym_auto, self.btn_arm, self.btn_leg):
            box.append(b)
        hint = Gtk.Label(label="Durante la selección: Retroceso deshace el último punto, Esc cancela.",
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        box.append(hint)
        self.planes_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.planes_box.add_css_class("boxed-list")
        box.append(self.planes_box)

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
            probs = ctrl.problems()
            self.lbl_problems.set_text("Pendiente: " + "; ".join(probs) if probs else "Listo para dibujar siluetas.")
            title = "Action Figure Maker"
            if ctrl.session.stl_path:
                title += " — " + os.path.basename(ctrl.session.stl_path)
            self.set_title(title + _MODE_TITLES[ctrl.mode])
            loaded = ctrl.loaded
            for b in (self.btn_crown, self.btn_sym, self.btn_sym_auto, self.btn_save, self.dd_front, self.chk_side):
                b.set_sensitive(loaded)
            has_sym = ctrl.session.project.symmetry is not None
            self.btn_arm.set_sensitive(loaded and has_sym)
            self.btn_leg.set_sensitive(loaded and has_sym)
            self.chk_side.set_sensitive(loaded and has_sym)
            self.chk_side.set_active(ctrl.one_side)
            self.dd_front.set_selected(0 if ctrl.session.project.front == "+Y" else 1)
            self._rebuild_plane_list()
        finally:
            self._updating = False
        self.gl.queue_render()

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

    def _on_key(self, _c, keyval: int, _code: int, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.ctrl.cancel()
            return True
        if keyval == Gdk.KEY_BackSpace:
            self.ctrl.undo_point()
            return True
        return False

    # ------------------------------------------------------------------ acciones
    def _set_view(self, name: str) -> None:
        self.ctrl.camera.set_view(name)
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
