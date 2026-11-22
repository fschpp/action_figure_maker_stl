"""Ventana «Roles de color desde un FBX» (T47): clusters de color del FBX, un rol por cluster y vista previa pintada.

Toda la lógica (clusters, decisiones, sugerencias a confirmar, anclas, guardar) está en `roles_wizard.py` y se prueba sin GTK; acá solo está
la ventana. **No se probó en pantalla** (no hay GTK en el entorno de desarrollo, R9). Se abre desde el menú «Catálogo ▾» de la ventana
«Administrar catálogo». El análisis (k-means sobre la superficie) corre en un hilo; guardar es corto y corre en la ventana."""
from __future__ import annotations

from ..i18n import tr

import json
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..palette import hex_to_rgb  # noqa: E402
from .catalog_manager import PreviewRenderer  # noqa: E402
from .roles_wizard import RolesWizard, RolesWizardError  # noqa: E402

DEFAULT_K = 10
VIEWS = ("clusters", "roles")                      # vista previa: colores encontrados / roles elegidos


class RolesWizardWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, ctrl, on_saved=None):
        super().__init__(title=tr("roles_wizard.titulo"), transient_for=parent, default_width=1100, default_height=720)
        self.ctrl = ctrl
        self.wiz = RolesWizard(ctrl)
        self.preview = PreviewRenderer()
        self._on_saved = on_saved
        self._fbx = ""
        self._sim_path = ""
        self._updating = False                  # se están poniendo valores en los widgets: no tomarlos como una decisión
        self._busy = False                      # analizando
        self._closed = False                    # la ventana ya se destruyó: ningún callback tardío (hilo, idle) debe tocarla
        self._hl: int | None = None             # cluster resaltado (la fila elegida)
        self._rows: list = []                   # [(Gtk.ListBoxRow, n de cluster)]
        self._parts_cache: tuple | None = None  # (clave, partes): girar la vista no vuelve a subir la malla a la GPU
        self._dyaw = 0.0
        self._dpitch = 0.0
        self._drag0 = (0.0, 0.0)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        outer.append(self._build_source())
        outer.append(self._build_target())
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True, vexpand=True)
        paned.set_start_child(self._build_clusters())
        paned.set_end_child(self._build_preview())
        paned.set_position(520)
        paned.set_shrink_start_child(True)
        paned.set_shrink_end_child(True)
        outer.append(paned)
        outer.append(self._build_bar())
        self.set_child(outer)
        self.connect("destroy", self._on_destroy)
        self._refresh()

    def _on_destroy(self, *_a) -> None:
        self._closed = True
        self.preview.close()

    # ------------------------------------------------------------ construcción
    def _build_source(self) -> Gtk.Widget:
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Label(label=tr("roles_wizard.fbx")))
        self.lbl_fbx = Gtk.Label(label=tr("roles_wizard.sin_fbx"), xalign=0.0, hexpand=True, ellipsize=3)
        row.append(self.lbl_fbx)
        self.btn_fbx = Gtk.Button(label=tr("roles_wizard.elegir_fbx"))
        self.btn_fbx.connect("clicked", lambda *_: self._choose_fbx())
        row.append(self.btn_fbx)
        row.append(Gtk.Label(label=tr("roles_wizard.colores_k")))
        self.spin_k = Gtk.SpinButton.new_with_range(2, 24, 1)
        self.spin_k.set_value(DEFAULT_K)
        row.append(self.spin_k)
        self.btn_analyze = Gtk.Button(label=tr("roles_wizard.analizar"), css_classes=["suggested-action"])
        self.btn_analyze.connect("clicked", lambda *_: self._analyze())
        row.append(self.btn_analyze)
        return row

    def _build_target(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Label(label=tr("roles_wizard.destino")))
        self.rb_piece = Gtk.CheckButton(label=tr("roles_wizard.destino_pieza"), active=True)
        self.rb_figure = Gtk.CheckButton(label=tr("roles_wizard.destino_figura"))
        self.rb_figure.set_group(self.rb_piece)
        self.rb_figure.set_sensitive(bool(getattr(self.ctrl, "loaded", False)))      # sin figura abierta no hay dónde anotarlo
        self.rb_piece.connect("toggled", lambda *_: self._refresh())
        row.append(self.rb_piece)
        row.append(self.rb_figure)
        box.append(row)
        self.piece_row = Gtk.Box(spacing=8)
        self.piece_row.append(Gtk.Label(label=tr("roles_wizard.pieza")))
        self._refs = self.wiz.piece_refs()
        self.drop_piece = Gtk.DropDown(model=Gtk.StringList.new(self._refs), hexpand=False)
        self.piece_row.append(self.drop_piece)
        self.piece_row.append(Gtk.Label(label=tr("roles_wizard.similitud")))
        self.lbl_sim = Gtk.Label(label="", xalign=0.0, hexpand=True, ellipsize=3)
        self.piece_row.append(self.lbl_sim)
        self.btn_calc = Gtk.Button(label=tr("roles_wizard.calcular_similitud"), tooltip_text=tr("roles_wizard.calcular_similitud_ayuda"))
        self.btn_calc.connect("clicked", lambda *_: self._calc_similarity())
        self.piece_row.append(self.btn_calc)
        btn = Gtk.Button(label=tr("roles_wizard.elegir_similitud"))
        btn.connect("clicked", lambda *_: self._choose_similarity())
        self.piece_row.append(btn)
        box.append(self.piece_row)
        return box

    def _build_clusters(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(Gtk.Label(label=tr("roles_wizard.colores"), xalign=0.0, css_classes=["heading"]))
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, min_content_height=220)
        scroll.set_child(self.listbox)
        box.append(scroll)
        self.btn_confirm = Gtk.Button(label=tr("roles_wizard.confirmar_sugerencias"), halign=Gtk.Align.START)
        self.btn_confirm.connect("clicked", lambda *_: self._confirm_all())
        box.append(self.btn_confirm)
        return box

    def _build_preview(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=tr("roles_wizard.vista_clusters") + " / " + tr("roles_wizard.vista_roles"), xalign=0.0, css_classes=["heading"]))
        self.drop_view = Gtk.DropDown(model=Gtk.StringList.new([tr("roles_wizard.vista_clusters"), tr("roles_wizard.vista_roles")]))
        self.drop_view.connect("notify::selected", lambda *_: self._render())
        row.append(self.drop_view)
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
        self.btn_save = Gtk.Button(label=tr("roles_wizard.guardar"), css_classes=["suggested-action"])
        self.btn_save.connect("clicked", lambda *_: self._save())
        bar.append(self.btn_save)
        return bar

    # ------------------------------------------------------------ estado de la pantalla
    def _say(self, text: str) -> None:
        if self._closed:
            return
        self.lbl_status.set_text(text)

    def _error(self, exc: Exception) -> None:
        if self._closed:                                     # p. ej. el análisis terminó con la ventana ya cerrada: no abrir un diálogo sobre una ventana destruida
            return
        dlg = Gtk.AlertDialog(message=str(exc), buttons=[tr("common.cancelar")], cancel_button=0, default_button=0)
        dlg.show(self)
        self._say(str(exc))

    def _refresh(self) -> None:
        """Pone a punto los botones y el mensaje de lo que falta."""
        to_piece = self.rb_piece.get_active()
        self.piece_row.set_visible(to_piece)
        ready = self.wiz.loaded and not self._busy
        self.btn_analyze.set_sensitive(bool(self._fbx) and not self._busy)
        self.btn_confirm.set_sensitive(ready and bool(self.wiz.unconfirmed()))
        ok_target = bool(self._refs) if to_piece else True
        self.btn_save.set_sensitive(ready and self.wiz.can_save and ok_target)
        if not self._busy:
            self._say(self.wiz.blockers() if self.wiz.loaded else tr("roles_wizard.sin_analisis"))

    def _rebuild(self) -> None:
        """Vuelve a dibujar las fichas de cluster desde el asistente."""
        if self._closed:
            return
        keep = self._hl
        self._updating = True
        try:
            while (child := self.listbox.get_first_child()) is not None:
                self.listbox.remove(child)
            self._rows = []
            if self.wiz.loaded:
                opts = self.wiz.role_options()
                names = [tr("roles_wizard.elegir_rol")] + opts + [tr("roles_wizard.rol_nuevo"), tr("roles_wizard.sin_rol")]
                for info in self.wiz.clusters():
                    self._rows.append((self._add_row(info, opts, names), info["n"]))
                target = next((r for r, n in self._rows if n == keep), self._rows[0][0] if self._rows else None)
                if target is not None:
                    self.listbox.select_row(target)
                    self._hl = next(n for r, n in self._rows if r is target)
        finally:
            self._updating = False
        self._refresh()
        self._render()

    def _add_row(self, info: dict, opts: list, names: list) -> Gtk.ListBoxRow:
        c = info["n"]
        row = Gtk.ListBoxRow()
        line = Gtk.Box(spacing=8, margin_top=3, margin_bottom=3, margin_start=6, margin_end=6)
        sw = Gtk.DrawingArea(content_width=30, content_height=30)
        r, g, b = hex_to_rgb(info["hex"])
        sw.set_draw_func(lambda _a, cr, _w, _h, r=r, g=g, b=b: (cr.set_source_rgb(r / 255.0, g / 255.0, b / 255.0), cr.paint()))
        line.append(sw)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        col.append(Gtk.Label(label=tr("roles_wizard.color_texto", n=c + 1, hex=info["hex"], pct=f"{info['pct']:.1f} %"), xalign=0.0))
        if info["estado"] == "sugerido":
            col.append(Gtk.Label(label=tr("roles_wizard.sugerencia"), xalign=0.0, css_classes=["warning", "caption"]))
        elif info["estado"] == "sin_decidir":
            col.append(Gtk.Label(label=tr("roles_wizard.sin_decidir"), xalign=0.0, css_classes=["dim-label", "caption"]))
        line.append(col)
        dd = Gtk.DropDown(model=Gtk.StringList.new(names))
        if info["tipo"] is None:
            sel = 0
        elif info["tipo"] == "none":
            sel = len(names) - 1
        else:
            sel = 1 + opts.index(info["rol"]) if info["rol"] in opts else 0
        dd.set_selected(sel)
        dd.connect("notify::selected", lambda d, _p, c=c, opts=opts: self._on_pick(c, d, opts))
        line.append(dd)
        if info["estado"] == "sugerido":
            ok = Gtk.Button(label="✓", tooltip_text=tr("roles_wizard.confirmar_sugerencias"))
            ok.connect("clicked", lambda *_b, c=c: self._confirm_one(c))
            line.append(ok)
        row.set_child(line)
        self.listbox.append(row)
        return row

    # ------------------------------------------------------------ eventos
    def _on_row_selected(self, _lb, row) -> None:
        if self._updating or row is None:
            return
        self._hl = next((n for r, n in self._rows if r is row), None)
        self._render()

    def _on_pick(self, c: int, dd, opts: list) -> None:
        if self._updating:
            return
        idx = dd.get_selected()
        cur = self.wiz.choices.get(c)
        try:
            if idx == 0:
                return                                              # «Elegí un rol…»: no es una decisión
            if idx <= len(opts):
                rid = opts[idx - 1]
                if cur is not None and cur.kind == "new" and cur.role == rid:
                    self.wiz.confirm(c)                             # es el rol que este mismo cluster crea
                else:
                    self.wiz.set_role(c, rid)
            elif idx == len(opts) + 1:
                self._ask_new_role(c)
                return
            else:
                self.wiz.set_none(c)
        except RolesWizardError as exc:
            self._error(exc)
        self._rebuild()

    def _confirm_one(self, c: int) -> None:
        self.wiz.confirm(c)
        self._rebuild()

    def _confirm_all(self) -> None:
        self.wiz.confirm_suggestions()
        self._rebuild()

    def _ask_new_role(self, c: int) -> None:
        win = Gtk.Window(title=tr("roles_wizard.rol_nuevo"), transient_for=self, modal=True, default_width=380)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(Gtk.Label(label=tr("roles_wizard.nuevo_id"), xalign=0.0))
        ent_id = Gtk.Entry(activates_default=True)
        box.append(ent_id)
        box.append(Gtk.Label(label=tr("roles_wizard.nuevo_nombre"), xalign=0.0))
        ent_name = Gtk.Entry(activates_default=True)
        box.append(ent_name)
        bar = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=tr("common.cancelar"))
        ok = Gtk.Button(label=tr("roles_wizard.rol_nuevo"), css_classes=["suggested-action"])
        bar.append(cancel)
        bar.append(ok)
        box.append(bar)
        win.set_child(box)
        win.set_default_widget(ok)

        def accept(*_a):
            try:
                self.wiz.set_new(c, ent_id.get_text(), ent_name.get_text())
            except RolesWizardError as exc:
                self._error(exc)
                return
            win.close()
            self._rebuild()

        ok.connect("clicked", accept)
        ent_id.connect("activate", accept)
        cancel.connect("clicked", lambda *_: (win.close(), self._rebuild()))             # volver a mostrar la decisión que había
        win.connect("close-request", lambda *_: (GLib.idle_add(self._rebuild), False)[1])
        win.present()

    # ------------------------------------------------------------ FBX y análisis
    def _choose_fbx(self) -> None:
        dlg = Gtk.FileDialog(title=tr("roles_wizard.elegir_fbx_titulo"))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return                                              # cancelado
            self._fbx = f.get_path()
            self.lbl_fbx.set_text(os.path.basename(self._fbx))
            self._refresh()

        dlg.open(self, None, done)

    def _choose_similarity(self) -> None:
        dlg = Gtk.FileDialog(title=tr("roles_wizard.elegir_similitud_titulo"))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            self._sim_path = f.get_path()
            self.wiz.clear_similarity()                             # el JSON elegido reemplaza a la similitud calculada
            self.lbl_sim.set_text(os.path.basename(self._sim_path))

        dlg.open(self, None, done)

    # ------------------------------------------------------------ similitud calculada desde la GUI
    def _calc_similarity(self) -> None:
        """Calcula la 4×4 FBX → figura sin salir de la ventana: con la figura abierta (si hay) o con un STL y un `.proyecto` a elegir."""
        if not self._fbx:
            self._say(tr("roles_wizard.sim_sin_fbx"))
            return
        sess = getattr(self.ctrl, "session", None)
        stl, proj = getattr(sess, "stl_path", None), getattr(sess, "project_path", None)
        if stl and proj and os.path.isfile(stl) and os.path.isfile(proj):
            dlg = Gtk.AlertDialog(message=tr("roles_wizard.sim_usar_abierta", stl=os.path.basename(stl), proyecto=os.path.basename(proj)),
                                  buttons=[tr("roles_wizard.sim_btn_abierta"), tr("roles_wizard.sim_btn_elegir"), tr("common.cancelar")],
                                  cancel_button=2, default_button=0)

            def answered(d, res):
                try:
                    i = d.choose_finish(res)
                except GLib.Error:
                    return
                if i == 0:
                    self._run_similarity(stl, proj)
                elif i == 1:
                    self._pick_similarity_files()

            dlg.choose(self, None, answered)
        else:
            self._pick_similarity_files()

    def _pick_similarity_files(self) -> None:
        def pick_stl():
            d = Gtk.FileDialog(title=tr("roles_wizard.sim_elegir_stl"))

            def done(dd, res):
                try:
                    stl = dd.open_finish(res).get_path()
                except GLib.Error:
                    return
                pick_proj(stl)

            d.open(self, None, done)

        def pick_proj(stl):
            d = Gtk.FileDialog(title=tr("roles_wizard.sim_elegir_proyecto"))

            def done(dd, res):
                try:
                    proj = dd.open_finish(res).get_path()
                except GLib.Error:
                    return
                self._run_similarity(stl, proj)

            d.open(self, None, done)

        pick_stl()

    def _run_similarity(self, stl: str, proj: str) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_calc.set_sensitive(False)
        self.btn_save.set_sensitive(False)
        self._say(tr("roles_wizard.sim_calculando"))

        def work():
            try:
                info, err = self.wiz.compute_similarity(stl, proj), None
            except Exception as exc:                                 # noqa: BLE001  (se muestra en la ventana)
                info, err = None, exc
            GLib.idle_add(self._similarity_done, info, err)

        threading.Thread(target=work, daemon=True).start()

    def _similarity_done(self, info, err) -> bool:
        self._busy = False
        if self._closed:
            return False
        self.btn_calc.set_sensitive(True)
        if err is not None:
            self.wiz.clear_similarity()
            self._sim_path = ""
            self.lbl_sim.set_text("")
            self._refresh()
            self._error(err)
            return False
        self._sim_path = ""                                          # la calculada reemplaza a un JSON elegido antes
        self.lbl_sim.set_text(tr("roles_wizard.sim_calculada", media=f"{info['media_mm']:.3f}", stl=self.wiz.similarity_info.get("stl", "")))
        self._refresh()
        self._say(tr("roles_wizard.sim_lista", media=f"{info['media_mm']:.3f}"))
        return False

    def _analyze(self) -> None:
        if not self._fbx or self._busy:
            return
        self._busy = True
        self.btn_analyze.set_sensitive(False)
        self.btn_save.set_sensitive(False)
        self._say(tr("roles_wizard.analizando"))
        k, path = int(self.spin_k.get_value()), self._fbx

        def work():
            try:
                self.wiz.load(path, k)
                err = None
            except Exception as exc:                                 # noqa: BLE001  (se muestra en la ventana)
                err = exc
            GLib.idle_add(self._analysis_done, err)

        threading.Thread(target=work, daemon=True).start()

    def _analysis_done(self, err) -> bool:
        self._busy = False
        if self._closed:                                     # se cerró la ventana mientras analizaba
            return False
        self._parts_cache = None
        self._hl = None
        if err is not None:
            self._error(err)
        self._rebuild()
        return False                                                  # una sola vez (idle_add)

    # ------------------------------------------------------------ vista previa
    def _render(self) -> None:
        if self._closed:                                     # no recrear el contexto EGL de la vista previa después de cerrarlo
            return
        if not self.wiz.loaded:
            self.picture.set_paintable(None)
            return
        mode = VIEWS[min(self.drop_view.get_selected(), 1)] if self.drop_view.get_selected() != Gtk.INVALID_LIST_POSITION else VIEWS[0]
        key = (self._hl, mode, tuple((c, ch.kind, ch.role) for c, ch in sorted(self.wiz.choices.items())), id(self.wiz.proposal))
        try:
            if self._parts_cache is None or self._parts_cache[0] != key:
                self._parts_cache = (key, self.wiz.preview_parts(self._hl, mode))
            img = self.preview.render_textured(self._parts_cache[1], True, self._dyaw, self._dpitch)
        except RolesWizardError as exc:
            self.picture.set_paintable(None)
            self.lbl_preview.set_text(str(exc))
            return
        except Exception as exc:                                     # noqa: BLE001  (sin EGL: el resto de la ventana sigue andando)
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

    # ------------------------------------------------------------ guardar
    def _save(self) -> None:
        try:
            if self.rb_piece.get_active():
                self._save_piece()
            else:
                self._save_figure()
        except RolesWizardError as exc:
            self._error(exc)

    def _save_piece(self) -> None:
        idx = self.drop_piece.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(self._refs):
            return
        sim = self.wiz.similarity
        if sim is None and self._sim_path:
            try:
                with open(self._sim_path, encoding="utf-8") as fh:
                    sim = json.load(fh)
                sim = sim.get("similitud", sim) if isinstance(sim, dict) else sim
            except (OSError, ValueError) as exc:
                raise RolesWizardError(f"{self._sim_path}: {exc}") from exc
        if self._busy:
            return
        self._busy = True
        self.btn_save.set_sensitive(False)
        self._say(tr("roles_wizard.guardando_pieza"))
        ref = self._refs[idx]

        def work():
            try:
                new, err = self.wiz.save_piece(ref, sim), None
            except Exception as exc:                                 # noqa: BLE001  (se muestra en la ventana)
                new, err = None, exc
            GLib.idle_add(self._piece_saved, new, err, sim is None)

        threading.Thread(target=work, daemon=True).start()          # alinear el FBX con la pieza puede tardar: no se congela la ventana

    def _piece_saved(self, new, err, no_sim: bool) -> bool:
        self._busy = False
        if self._closed:
            return False
        if err is not None:
            if no_sim and isinstance(err, RolesWizardError) and "similarity" in str(err):    # pieza recortada de una figura: el error pedía la similitud
                err = RolesWizardError(f"{err}\n\n{tr('roles_wizard.sim_pista')}")
            self._refresh()
            self._error(err)
            return False
        self._refresh()
        self._after_saved(tr("roles_wizard.guardada_pieza", ref=new.ref, roles=", ".join(new.color.get("roles", []))))
        return False

    def _save_figure(self) -> None:
        dlg = Gtk.FileDialog(title=tr("roles_wizard.guardar_paquete"), initial_name="figura.color.npz")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                self.wiz.save_figure(f.get_path())
            except RolesWizardError as exc:
                self._error(exc)
                return
            self._after_saved(self.ctrl.message or "")

        dlg.save(self, None, done)

    def _after_saved(self, text: str) -> None:
        self._refs = self.wiz.piece_refs()                         # la versión nueva ya está en el catálogo
        self.drop_piece.set_model(Gtk.StringList.new(self._refs))
        self._rebuild()
        self._say(text)
        if self._on_saved is not None:
            self._on_saved()
