"""Ventana «Armar figura desde catálogo»: una figura nueva, sin STL ni proyecto, eligiendo una pieza del catálogo por lugar.

La lógica está en `figure_builder.py` (se prueba sin GTK). Acá: un desplegable por lugar (cada pieza marcada con cómo
acopla a la ya elegida), vista previa 3/4 de la pieza elegida y, al armar, de la figura entera, y el armado en un hilo."""
from __future__ import annotations

import os
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .catalog_manager import PreviewRenderer, view_bounds  # noqa: E402,F401  (view_bounds: el encuadre lo hace PreviewRenderer)
from ..recipe import SCALE_PLACES  # noqa: E402
from .progress_panel import ProgressPanel  # noqa: E402
from .figure_builder import HAND_JOINS, NONE_LABEL, PLACE_LABELS, PLACE_ORDER, FigureBuilder, summary  # noqa: E402


class FigureBuilderWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, catalog_root: str, remember_dir: str | None = None):
        super().__init__(title="Armar figura desde catálogo", transient_for=parent, default_width=980, default_height=640)
        self.builder = FigureBuilder(catalog_root)
        self.preview = PreviewRenderer()
        self._refs: dict[str, list] = {p: [] for p in PLACE_ORDER}      # lugar -> refs en el orden del desplegable
        self._dd: dict[str, Gtk.DropDown] = {}
        self._scale_sp: dict[str, list[Gtk.SpinButton]] = {}      # lugar -> [X, Y, Z]
        self._scale_lock: dict[str, Gtk.CheckButton] = {}
        self._scale_row: dict[str, Gtk.Widget] = {}
        self._updating = False
        self._busy = False
        self._refill_pending = False
        self._t0 = 0.0
        self._shown = None                      # (malla, meta | None) en la vista previa
        self._group = None                      # [(nombre, malla, color)] de «todas las piezas juntas»
        self._dyaw = self._dpitch = 0.0
        self._drag0 = (0.0, 0.0)
        self._last_dir = remember_dir

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
        paned.set_start_child(self._build_form())
        paned.set_end_child(self._build_preview())
        paned.set_position(400)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        self.set_child(paned)
        self.connect("destroy", lambda *_: self.preview.close())
        self._refill()

    # ------------------------------------------------------------ construcción
    def _build_form(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)
        cat = self.builder.catalog()
        title = Gtk.Label(label=f"Línea «{cat.name}» · figuras de {cat.height_mm:g} mm\n{self.builder.root}", xalign=0, wrap=True)
        title.add_css_class("heading")
        box.append(title)
        box.append(Gtk.Label(label="Nombre de la figura", xalign=0))
        name_row = Gtk.Box(spacing=6)
        self.ent_name = Gtk.Entry(text=self.builder.name, hexpand=True)
        self.ent_name.connect("changed", self._on_name)
        name_row.append(self.ent_name)
        self.btn_open = Gtk.Button(label="Abrir receta…")
        self.btn_open.set_tooltip_text("Carga una receta guardada (piezas, opciones, mano y escalas) para retocarla. "
                                       "Guardala con otro nombre para no pisar la original.")
        self.btn_open.connect("clicked", self._on_open_recipe)
        name_row.append(self.btn_open)
        box.append(name_row)
        box.append(Gtk.Label(label="Pieza de cada lugar (empezá por el torso)", xalign=0, margin_top=6))
        for place in PLACE_ORDER:
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Label(label=PLACE_LABELS[place], xalign=0, width_chars=7))
            dd = Gtk.DropDown.new_from_strings([NONE_LABEL])
            dd.set_hexpand(True)
            dd.connect("notify::selected", self._on_pick, place)
            self._dd[place] = dd
            row.append(dd)
            box.append(row)
        self.chk_shoulder = Gtk.CheckButton(label="Clic en los hombros")
        self.chk_hip = Gtk.CheckButton(label="Clic en las caderas")
        for chk, key in ((self.chk_shoulder, "clic_hombro"), (self.chk_hip, "clic_cadera")):
            chk.connect("toggled", lambda b, k=key: self.builder.options.__setitem__(k, b.get_active()))
            box.append(chk)
        box.append(self._build_hand_section())
        box.append(self._build_scale_section())
        box.append(self._build_accessory_section())
        self.lbl_problems = Gtk.Label(xalign=0, wrap=True)
        self.lbl_problems.add_css_class("dim-label")
        box.append(self.lbl_problems)

        bar = Gtk.Box(spacing=6, margin_top=4)
        self.btn_recipe = Gtk.Button(label="Guardar receta en el catálogo")
        self.btn_recipe.set_tooltip_text("Guarda las piezas elegidas en <catálogo>/figuras/<nombre>.receta.json "
                                         "(se puede volver a armar con «afm armar» y cuenta como uso de cada pieza).")
        self.btn_recipe.connect("clicked", self._on_save_recipe)
        self.btn_build = Gtk.Button(label="Armar y exportar…")
        self.btn_build.add_css_class("suggested-action")
        self.btn_build.connect("clicked", self._on_build)
        bar.append(self.btn_recipe)
        bar.append(self.btn_build)
        box.append(bar)
        self._last_project: str | None = None
        self.progress_panel = ProgressPanel()                # porcentaje, tarea en curso, pendientes y tiempo estimado
        box.append(self.progress_panel)
        self.btn_open_main = Gtk.Button(label="Abrir la figura armada en la ventana principal")
        self.btn_open_main.set_tooltip_text("Abre el .proyecto recién exportado (sin STL: el cuerpo sale del catálogo) y la figura "\
                                            "se ve por partes. Reemplaza lo que esté abierto en la ventana principal.")
        self.btn_open_main.set_visible(False)
        self.btn_open_main.connect("clicked", self._on_open_main)
        box.append(self.btn_open_main)
        hint = Gtk.Label(label="El torso arma el cuerpo: uno entero, o «torso_sup» + una pelvis (la figura lleva cintura). "
                               "Cada pieza de la lista dice cómo acopla a la ya elegida (ok / aviso / no acopla). Armar tarda "
                               "unos minutos; exporta las piezas, la figura ensamblada, el informe, la receta y un .proyecto (abrible en la ventana "
                               "principal sin el STL) en la carpeta que elijas.",
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        box.append(hint)
        return box

    def _build_hand_section(self) -> Gtk.Widget:
        exp = Gtk.Expander(label="Mano: giro y unión")
        self.exp_hand = exp
        grid = Gtk.Grid(row_spacing=6, column_spacing=8, margin_top=6)
        grid.attach(Gtk.Label(label="Giro extra (°)", xalign=0), 0, 0, 1, 1)
        self.sp_yaw = Gtk.SpinButton.new_with_range(-360.0, 360.0, 5.0)
        self.sp_yaw.set_digits(1)
        self.sp_yaw.set_tooltip_text("Gira la mano alrededor de la muñeca. 0 = como la guarda el catálogo.")
        self.sp_yaw.connect("value-changed", self._on_hand)
        grid.attach(self.sp_yaw, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Unión", xalign=0), 0, 1, 1, 1)
        self.dd_join = Gtk.DropDown.new_from_strings(["solape (mano soldada al brazo)", "giro (mano aparte, gira libre)"])
        self.dd_join.set_tooltip_text("«solape»: la base de la mano se hunde en el brazo y queda soldada. «giro»: la mano es otra "
                                      "pieza con pasador y gira sobre la muñeca.")
        self.dd_join.connect("notify::selected", self._on_hand)
        grid.attach(self.dd_join, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Solape (mm)", xalign=0), 0, 2, 1, 1)
        self.sp_overlap = Gtk.SpinButton.new_with_range(0.0, 5.0, 0.1)
        self.sp_overlap.set_digits(1)
        self.sp_overlap.set_tooltip_text("Cuánto se hunde la base de la mano en el brazo (solo con unión «solape»).")
        self.sp_overlap.connect("value-changed", self._on_hand)
        grid.attach(self.sp_overlap, 1, 2, 1, 1)
        exp.set_child(grid)
        return exp

    def _build_accessory_section(self) -> Gtk.Widget:
        """Accesorios del catálogo que acompañan a la figura: se exportan aparte (STL en piezas/ y en la lista de piezas)."""
        self.exp_acc = Gtk.Expander(label="Accesorios que acompañan a la figura", expanded=False)
        self.box_acc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=12, margin_top=4)
        self.exp_acc.set_child(self.box_acc)
        self._acc_checks: dict[str, Gtk.CheckButton] = {}
        return self.exp_acc

    def _refill_accessories(self, cat) -> None:
        """Una casilla por accesorio del catálogo, marcada con cómo se lleva con la mano elegida (ok / aviso / no se sujeta)."""
        b = self.builder
        while (child := self.box_acc.get_first_child()) is not None:
            self.box_acc.remove(child)
        self._acc_checks = {}
        metas = b.accessory_pieces(cat)
        fits = b.accessory_fits(cat)
        if not metas:
            lbl = Gtk.Label(label="El catálogo no tiene accesorios (afm agarre --catalogo, o afm catalogo importar-accesorio).",
                            xalign=0, wrap=True)
            lbl.add_css_class("dim-label")
            self.box_acc.append(lbl)
        for m in metas:
            chk = Gtk.CheckButton(label=b.accessory_label(m, fits))
            chk.set_active(m.ref in b.accessories)
            chk.set_sensitive(not self._busy)
            chk.connect("toggled", self._on_accessory, m.ref)
            self._acc_checks[m.ref] = chk
            self.box_acc.append(chk)
        self.exp_acc.set_sensitive(bool(metas))

    def _on_accessory(self, chk: Gtk.CheckButton, ref: str) -> None:
        if self._updating:
            return
        try:
            self.builder.set_accessory(ref, chk.get_active())
        except ValueError as exc:
            self.lbl_result.set_text(str(exc))
        self._sync()

    def _build_scale_section(self) -> Gtk.Widget:
        exp = Gtk.Expander(label="Escala de cada pieza (X ancho · Y profundidad · Z alto)")
        self.exp_scale = exp
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6)
        for place in SCALE_PLACES:
            row = Gtk.Box(spacing=4)
            row.append(Gtk.Label(label=PLACE_LABELS[place], xalign=0, width_chars=7))
            sps = []
            for axis in "XYZ":
                sp = Gtk.SpinButton.new_with_range(0.5, 2.0, 0.01)
                sp.set_digits(2)
                sp.set_value(1.0)
                sp.set_width_chars(5)
                sp.set_tooltip_text(f"Factor en {axis}: 1 = original, 1,10 = 10 % mayor, 0,95 = 5 % menor.")
                sp.connect("value-changed", self._on_scale, place, axis)
                sps.append(sp)
                row.append(sp)
            lock = Gtk.CheckButton(label="igual", active=True)
            lock.set_tooltip_text("Los tres ejes se mueven juntos (escala uniforme).")
            row.append(lock)
            self._scale_sp[place], self._scale_lock[place], self._scale_row[place] = sps, lock, row
            box.append(row)
        note = Gtk.Label(label="La pieza se escala alrededor de su unión principal y las demás se vuelven a acoplar. Los pasadores y las "
                               "holguras no se escalan (son mm de impresión). La mano va siempre en mm reales. Revisá la validación al armar.",
                         xalign=0, wrap=True)
        note.add_css_class("dim-label")
        box.append(note)
        exp.set_child(box)
        return exp

    def _build_preview(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.picture.set_size_request(340, 300)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_: setattr(self, "_drag0", (self._dyaw, self._dpitch)))
        drag.connect("drag-update", self._on_drag)
        self.picture.add_controller(drag)
        frame = Gtk.Frame()
        frame.set_child(self.picture)
        box.append(frame)
        row = Gtk.Box(spacing=6)
        reset = Gtk.Button(label="Vista 3/4")
        reset.connect("clicked", self._on_reset_view)
        row.append(reset)
        self.tgl_all = Gtk.ToggleButton(label="Ver todas las piezas juntas")
        self.tgl_all.set_tooltip_text("Pone las piezas elegidas en su lugar (con sus escalas y la mano) y las dibuja juntas, sin generar: "
                                      "sirve para ver proporciones y uniones antes de esperar el armado. No incluye pasadores ni holguras.")
        self.tgl_all.connect("toggled", self._on_toggle_all)
        row.append(self.tgl_all)
        row.append(Gtk.Label(label="Arrastrá para girar", xalign=0, hexpand=True))
        self.spinner = Gtk.Spinner()
        row.append(self.spinner)
        box.append(row)
        self.lbl_result = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True)
        self.lbl_result.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        sw = Gtk.ScrolledWindow(min_content_height=120, vexpand=False)
        sw.set_child(self.lbl_result)
        box.append(sw)
        return box

    # ------------------------------------------------------------ elecciones
    def _refill(self) -> None:
        """Vuelve a cargar los desplegables: cada pieza lleva la marca de cómo acopla a la ya elegida."""
        b = self.builder
        cat = b.catalog()
        self._updating = True
        try:
            for place in PLACE_ORDER:
                metas = b.pieces_for(place, cat)
                fit = b.fit_labels(place, cat)
                self._refs[place] = [m.ref for m in metas]
                dd = self._dd[place]
                dd.set_model(Gtk.StringList.new([NONE_LABEL] + [b.label(m, fit) for m in metas]))
                cur = b.choices.get(place)
                dd.set_selected(self._refs[place].index(cur) + 1 if cur in self._refs[place] else 0)
                enabled = not self._busy and (place != "pelvis" or b.waist) and bool(metas)
                dd.set_sensitive(enabled)
                dd.set_tooltip_text(None if metas else "El catálogo no tiene piezas de este tipo.")
        finally:
            self._updating = False
        self._updating = True
        try:
            self._refill_accessories(cat)
        finally:
            self._updating = False
        self._sync_controls()
        self._sync()

    def _sync(self) -> None:
        probs = self.builder.problems()
        self.lbl_problems.set_text("Falta: " + "; ".join(probs) if probs else "Listo para armar.")
        self.btn_build.set_sensitive(not probs and not self._busy)
        self.btn_recipe.set_sensitive(not probs and not self._busy)
        self.ent_name.set_sensitive(not self._busy)
        self.btn_open.set_sensitive(not self._busy)

    def _on_name(self, entry: Gtk.Entry) -> None:
        self.builder.name = entry.get_text().strip()
        self._sync()

    def _on_pick(self, dd: Gtk.DropDown, _p, place: str) -> None:
        if self._updating:
            return
        i = dd.get_selected()
        refs = self._refs[place]
        ref = refs[i - 1] if 0 < i <= len(refs) else None
        if ref == self.builder.choices.get(place):
            return          # GTK avisa el cambio de selección de un `set_model` / `set_selected` nuestro DESPUÉS de la guarda
        try:
            self.builder.set_choice(place, ref)
        except ValueError as exc:
            self.lbl_result.set_text(str(exc))
        self._sync()
        self._schedule_refill()          # NO ahora: GTK se cae si se cambia el modelo del desplegable que está avisando el cambio
        if ref:
            self._show_piece(ref)
        else:
            self._refresh_group()

    def _schedule_refill(self) -> None:
        if not self._refill_pending:
            self._refill_pending = True
            GLib.idle_add(self._refill_idle)

    def _refill_idle(self) -> bool:
        self._refill_pending = False
        self._refill()
        return False

    # ------------------------------------------------------------ mano, escalas y receta abierta
    def _sync_controls(self) -> None:
        """Pone los controles de mano y escalas como están en el constructor y habilita solo los que tienen pieza."""
        b = self.builder
        self._updating = True
        try:
            self.sp_yaw.set_value(b.hand["yaw"])
            self.dd_join.set_selected(HAND_JOINS.index(b.hand["union"]))
            self.sp_overlap.set_value(b.hand["solape"])
            for place in SCALE_PLACES:
                k = b.scale_of(place)
                for sp, v in zip(self._scale_sp[place], k):
                    sp.set_value(v)
                self._scale_lock[place].set_active(max(k) - min(k) < 1e-9)
        finally:
            self._updating = False
        has_hand = bool(b.choices.get("mano")) and not self._busy
        self.exp_hand.set_sensitive(has_hand)
        self.sp_overlap.set_sensitive(b.hand["union"] == "solape")
        for place in SCALE_PLACES:
            self._scale_row[place].set_sensitive(bool(b.choices.get(place)) and not self._busy)

    def _on_hand(self, *_args) -> None:
        if self._updating:
            return
        try:
            self.builder.set_hand(yaw=self.sp_yaw.get_value(), union=HAND_JOINS[self.dd_join.get_selected()],
                                  solape=self.sp_overlap.get_value())
        except ValueError as exc:
            self.lbl_result.set_text(str(exc))
        self.sp_overlap.set_sensitive(self.builder.hand["union"] == "solape")
        self._refresh_group()

    def _on_scale(self, sp: Gtk.SpinButton, place: str, axis: str) -> None:
        if self._updating:
            return
        sps = self._scale_sp[place]
        if self._scale_lock[place].get_active():          # «igual»: el valor que se tocó pasa a los otros dos ejes
            v = sp.get_value()
            self._updating = True
            try:
                for other in sps:
                    other.set_value(v)
            finally:
                self._updating = False
        try:
            self.builder.set_scale(place, [x.get_value() for x in sps])
        except ValueError as exc:
            self.lbl_result.set_text(str(exc))
        self._refresh_group()

    def _on_open_recipe(self, *_) -> None:
        dlg = Gtk.FileDialog(title="Abrir receta para retocarla")
        flt = Gtk.FileFilter()
        flt.set_name("Recetas (*.receta.json)")
        flt.add_pattern("*.receta.json")
        flt.add_pattern("*.json")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)
        start = os.path.join(self.builder.root, "figuras")
        folder = start if os.path.isdir(start) else (self._last_dir if self._last_dir and os.path.isdir(self._last_dir) else self.builder.root)
        dlg.set_initial_folder(Gio.File.new_for_path(folder))

        def picked(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return                                        # cancelado
            self._open_recipe(path)

        dlg.open(self, None, picked)

    def _open_recipe(self, path: str) -> None:
        try:
            warns = self.builder.load_recipe(path)
        except ValueError as exc:
            self.lbl_result.set_text(str(exc))
            return
        self.ent_name.set_text(self.builder.name)
        self._sync_controls()
        self.chk_shoulder.set_active(self.builder.options["clic_hombro"])
        self.chk_hip.set_active(self.builder.options["clic_cadera"])
        self._refill()
        msg = f"Receta abierta: {path}\nRetocala y guardala con otro nombre, o armala."
        if warns:
            msg += "\nNo se pudo cargar todo:\n" + "\n".join(f"• {w}" for w in warns)
        self.lbl_result.set_text(msg)
        shown = self.builder.choices.get("torso")
        if shown:
            self._show_piece(shown)             # con «todas juntas» activo esto vuelve a dibujar el conjunto

    # ------------------------------------------------------------ vista previa
    def _on_toggle_all(self, *_) -> None:
        if self.tgl_all.get_active():
            self._dyaw = self._dpitch = 0.0
        self._refresh_group()

    def _refresh_group(self) -> None:
        """Con «Ver todas las piezas juntas» activo, vuelve a colocar y dibujar el conjunto (instantáneo)."""
        if not self.tgl_all.get_active():
            self._group = None
            self._redraw()
            return
        try:
            self._group, notes = self.builder.preview()
        except Exception as exc:                              # noqa: BLE001  (sin torso, pieza rota…): se dice y se vuelve a la pieza
            self._group = None
            self.lbl_result.set_text(f"No se puede ver la figura: {exc}")
            self._redraw()
            return
        if notes:
            self.lbl_result.set_text("Vista conjunta (sin pasadores ni holguras):\n" + "\n".join(f"• {n}" for n in notes))
        self._redraw()

    def _show_piece(self, ref: str) -> None:
        if self.tgl_all.get_active():
            self._refresh_group()
            return
        try:
            meta, mesh = self.builder.catalog().load(ref, check_hash=False)
        except Exception as exc:                              # noqa: BLE001
            self.lbl_result.set_text(f"No se pudo leer {ref}: {exc}")
            return
        self._set_shown(mesh, meta)

    def _set_shown(self, mesh, meta) -> None:
        self._shown = (mesh, meta)
        self._dyaw = self._dpitch = 0.0
        self._redraw()

    def _redraw(self) -> None:
        group = self._group if self.tgl_all.get_active() else None
        if group is None and self._shown is None:
            return
        try:
            if group is not None:
                img = self.preview.render_parts(group, self._dyaw, self._dpitch)
            else:
                mesh, meta = self._shown
                img = self.preview.render(mesh, meta, self._dyaw, self._dpitch)
        except Exception as exc:                              # noqa: BLE001  (sin EGL: el resto sigue andando)
            self.picture.set_paintable(None)
            self.lbl_result.set_text(f"La vista previa 3D no está disponible: {exc}")
            return
        h, w = img.shape[:2]
        data = GLib.Bytes.new(img.tobytes())
        self.picture.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, data, w * 4))

    def _on_drag(self, _g, dx: float, dy: float) -> None:
        y0, p0 = self._drag0
        self._dyaw, self._dpitch = y0 - dx * 0.4, p0 + dy * 0.4
        self._redraw()

    def _on_reset_view(self, *_) -> None:
        self._dyaw = self._dpitch = 0.0
        self._redraw()

    # ------------------------------------------------------------ guardar receta y armar
    def _on_save_recipe(self, *_) -> None:
        try:
            path = self.builder.save_recipe()
        except Exception as exc:                              # noqa: BLE001
            self.lbl_result.set_text(f"No se guardó la receta: {exc}")
            return
        self.lbl_result.set_text(f"Receta guardada en {path}")

    def _on_build(self, *_) -> None:
        dlg = Gtk.FileDialog(title="Carpeta donde exportar la figura")

        def picked(d, res):
            try:
                folder = d.select_folder_finish(res).get_path()
            except GLib.Error:
                return                                        # cancelado
            self._start_build(folder)

        dlg.select_folder(self, None, picked)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self._schedule_refill()
        self._sync()

    def _start_build(self, folder: str) -> None:
        if self._busy:
            return
        self._t0 = time.monotonic()
        self._set_busy(True)
        self.lbl_result.set_text("Armando la figura… (tarda unos minutos)")
        try:
            stages = self.builder.progress_stages()
        except Exception:                                     # noqa: BLE001 — sin plan la barra solo late
            stages = None
        self.progress_panel.start(stages, "Armando la figura…")
        progress = self.progress_panel.feed

        def work() -> None:
            try:
                info = self.builder.generate(folder, progress)
            except Exception as exc:                          # noqa: BLE001 — se informa en la ventana
                GLib.idle_add(self._build_done, None, exc, folder)
            else:
                GLib.idle_add(self._build_done, info, None, folder)

        threading.Thread(target=work, daemon=True, name="afm-armar").start()

    def _on_open_main(self, *_) -> None:
        parent = self.get_transient_for()
        if self._last_project is None or parent is None:
            return
        try:
            parent._open_project_path(self._last_project, self.builder.root)
        except Exception as exc:                              # noqa: BLE001
            self.lbl_result.set_text(f"No se pudo abrir en la ventana principal: {exc}")

    def _build_done(self, info, error, folder: str) -> bool:
        self._set_busy(False)
        self.progress_panel.finish(error is None, "Figura armada y exportada")
        if error is not None:
            self.lbl_result.set_text(f"No se pudo armar: {error}")
            return False
        self._last_project = info.get("proyecto")
        self.btn_open_main.set_visible(bool(self._last_project))
        self.lbl_result.set_text(summary(info) + f"\n\nExportado en {folder}")
        if self.tgl_all.get_active():
            self.tgl_all.set_active(False)       # se muestra la figura armada de verdad
        try:
            import trimesh

            mesh = trimesh.load(info["ensamblada"], force="mesh")
            self._set_shown(mesh, None)
        except Exception as exc:                              # noqa: BLE001
            self.lbl_result.set_text(self.lbl_result.get_text() + f"\n(sin vista previa: {exc})")
        return False
