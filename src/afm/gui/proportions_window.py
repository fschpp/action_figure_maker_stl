"""Ventana «Comparar proporciones con un patrón»: tu figura (en piezas, con sus escalas) y el cuerpo de una figura patrón del
catálogo, lado a lado y superpuestos, en tres vistas (frente, costado, arriba), con guías horizontales a las alturas del patrón
(coronilla, hombro, cintura, entrepierna, pies) y la escala X / Y / Z de cada pieza para ir igualándola viendo el resultado.

Todo el cálculo (mallas, medidas, siluetas, escalas sugeridas) está en `afm.proportions` y se prueba sin GTK; acá solo hay
widgets. Se abre desde la pestaña «Catálogo» de la ventana principal."""
from __future__ import annotations
from ..i18n import tr, lz, loc

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .. import proportions as PR  # noqa: E402

PX_MM = 0.5                  # milímetros por píxel de las imágenes
ZOOM = 3.0                   # píxeles de pantalla por milímetro
VIEW_TITLES = {"frente": lz("common.frente"), "costado": lz("proportions_window.costado"), "arriba": lz("common.arriba")}
KIND_TITLES = (("model", lz("proportions_window.tu_figura_en_piezas")), ("ref", lz("proportions_window.patron")), ("over", lz("proportions_window.superpuestas_patron_en_gris")))
LEVEL_COLOR = {"ok": "#2FB36B", "aviso": "#D9A400", "no": "#E5484D", "sin_dato": "#9CA3AF"}
LEVEL_TEXT = {"ok": lz("proportions_window.coincide"), "aviso": lz("proportions_window.casi"), "no": lz("proportions_window.no_coincide"), "sin_dato": lz("proportions_window.sin_dato")}


class ProportionsWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, ctrl):
        super().__init__(title=tr("proportions_window.comparar_proporciones_con_un_patron"), transient_for=parent, default_width=1180, default_height=860)
        self.parent_win, self.ctrl = parent, ctrl
        self.figures: list[str] = []
        self.snapshot: dict | None = None
        self._surfaces: dict = {}          # (vista, lado) -> (ImageSurface, ancho, alto) en píxeles de imagen
        self._keep: list = []              # los bytes que cairo usa por dentro: no se pueden soltar
        self._guides: dict = {}            # vista -> [(nombre, y en píxeles de imagen)]
        self._areas: dict = {}
        self._spins: dict = {}             # lugar -> (spin X, spin Y, spin Z)
        self._rows: list = []

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)
        self.set_child(outer)
        bar = Gtk.Box(spacing=8)
        bar.append(Gtk.Label(label=tr("proportions_window.figura_patron")))
        self.dd_fig = Gtk.DropDown.new_from_strings([tr("common.ninguna")])
        bar.append(self.dd_fig)
        self.btn_reload = Gtk.Button(label=tr("proportions_window.actualizar"))
        self.btn_reload.connect("clicked", lambda *_: self.reload())
        bar.append(self.btn_reload)
        self.chk_guides = Gtk.CheckButton(label=tr("proportions_window.guias_a_las_alturas_del_patron"), active=True)
        self.chk_guides.connect("toggled", lambda *_: self._redraw())
        bar.append(self.chk_guides)
        self.lbl_status = Gtk.Label(label="", xalign=0, hexpand=True, wrap=True)
        bar.append(self.lbl_status)
        outer.append(bar)
        outer.append(Gtk.Label(
            label=tr("proportions_window.cada_pieza_de_tu_figura_se"), xalign=0, wrap=True))

        body = Gtk.Box(spacing=12, vexpand=True)
        outer.append(body)
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        for c, (_kind, title) in enumerate(KIND_TITLES):
            grid.attach(Gtk.Label(label=loc(title), xalign=0), c + 1, 0, 1, 1)
        for r, view in enumerate(PR.VIEWS):
            grid.attach(Gtk.Label(label=loc(VIEW_TITLES[view]), xalign=1, valign=Gtk.Align.CENTER), 0, r + 1, 1, 1)
            for c, (kind, _t) in enumerate(KIND_TITLES):
                area = Gtk.DrawingArea(content_width=200, content_height=200)
                area.set_draw_func(self._make_draw(view, kind))
                self._areas[(view, kind)] = area
                grid.attach(area, c + 1, r + 1, 1, 1)
        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_child(grid)
        body.append(scroll)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=430)
        self.table = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.append(Gtk.Label(label=tr("proportions_window.escala_por_pieza_1_original"), xalign=0, css_classes=["heading"]))
        side.append(self.table)
        self.btn_all = Gtk.Button(label=tr("proportions_window.aplicar_las_escalas_sugeridas_a_todas"))
        self.btn_all.connect("clicked", self._on_apply_all)
        side.append(self.btn_all)
        self.btn_fit = Gtk.Button(label=tr("proportions_window.ajustar_los_cortes_al_talle_estandar"))
        self.btn_fit.set_tooltip_text(tr("proportions_window.lleva_la_cara_de_cada_miembro"))
        self.btn_fit.connect("clicked", self._on_fit)
        side.append(self.btn_fit)
        close = Gtk.Button(label=tr("common.cerrar"), halign=Gtk.Align.END)
        close.connect("clicked", lambda *_: self.close())
        side.append(close)
        body.append(side)

    # ------------------------------------------------------------------ dibujo
    def _make_draw(self, view: str, kind: str):
        def draw(_area, cr, w, h):
            cr.set_source_rgb(0.12, 0.13, 0.15)
            cr.paint()
            entry = self._surfaces.get((view, kind))
            if entry is None:
                return
            surf, _iw, _ih = entry
            k = ZOOM * PX_MM
            cr.save()
            cr.scale(k, k)
            cr.set_source_surface(surf, 0, 0)
            cr.paint()
            cr.restore()
            if view != "arriba" and self.chk_guides.get_active():
                cr.set_source_rgba(1.0, 0.85, 0.3, 0.85)
                cr.set_line_width(1.0)
                cr.set_font_size(10)
                for name, y in self._guides.get(view, []):
                    cr.set_dash([4.0, 3.0])
                    cr.move_to(0, y * k)
                    cr.line_to(w, y * k)
                    cr.stroke()
                    if kind == "ref":
                        cr.set_dash([])
                        cr.move_to(4, y * k - 3)
                        cr.show_text(name)
                cr.set_dash([])
        return draw

    def _redraw(self) -> None:
        for area in self._areas.values():
            area.queue_draw()

    # ------------------------------------------------------------------ cargar
    def reload(self) -> None:
        """Arma de nuevo la comparación (en un hilo): divide la figura si hace falta y dibuja las tres vistas."""
        self.figures = self.ctrl.proportion_figures()
        if not self.figures:
            self.lbl_status.set_text(tr("proportions_window.el_catalogo_abierto_no_tiene_una"))
            return
        current = self.dd_fig.get_selected()
        if self.dd_fig.get_model().get_n_items() != len(self.figures) or current >= len(self.figures):
            self.dd_fig.set_model(Gtk.StringList.new(self.figures))
            self.dd_fig.set_selected(0)
        figura = self.figures[self.dd_fig.get_selected()]
        job = self.ctrl.proportions_job(figura)
        if job is None:
            return
        self.lbl_status.set_text(tr("proportions_window.comparando_con_figura", figura=figura))
        self.parent_win._run_task(tr("proportions_window.comparando_proporciones"), lambda progress: self._render(job(progress)),
                                  self._loaded, tr("proportions_window.comparacion_lista"))

    @staticmethod
    def _render(snap: dict) -> dict:
        """Hilo de trabajo: normaliza las mallas, mide y dibuja las siluetas (imágenes numpy; los widgets se arman después)."""
        model, ref = PR.normalize(snap["model"]), PR.normalize(snap["ref"])
        images, bounds = {}, {}
        for view in PR.VIEWS:
            b = PR.view_bounds(model, ref, view)
            bounds[view] = b
            images[(view, "model")] = PR.render_view(model, view, b, PX_MM)
            images[(view, "ref")] = PR.render_view(ref, view, b, PX_MM)
            images[(view, "over")] = PR.render_overlay(model, ref, view, b, PX_MM)
        marks = PR.landmarks(PR.dims(ref))
        guides = {v: PR.guide_rows(bounds[v], PX_MM, marks) for v in ("frente", "costado")}
        rows = PR.compare(model, ref, snap["scales"])
        return {**snap, "images": images, "guides": guides, "rows": rows}

    def _loaded(self, res, err) -> None:
        self.ctrl.finish_proportions(err)
        if err is not None or res is None:
            self.lbl_status.set_text(tr("proportions_window.no_se_pudo_comparar_err", err=err))
            return
        import cairo

        self.snapshot = res
        self._surfaces.clear()
        self._keep.clear()
        for key, img in res["images"].items():
            h, w = img.shape[:2]
            data = PR.bgra_premultiplied(img)
            self._keep.append(data)
            self._surfaces[key] = (cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, w, h, w * 4), w, h)
            self._areas[key].set_content_width(max(int(w * PX_MM * ZOOM), 60))
            self._areas[key].set_content_height(max(int(h * PX_MM * ZOOM), 60))
        self._guides = res["guides"]
        self._fill_table(res["rows"])
        self._redraw()
        self.lbl_status.set_text(tr("proportions_window.comparando_con_figura_las_lineas_amarillas", figura=res['figura'])
                                 + (f" {res['nota']}" if res.get("nota") else ""))

    # ------------------------------------------------------------------ tabla de piezas
    def _fill_table(self, rows: list) -> None:
        child = self.table.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.table.remove(child)
            child = nxt
        self._spins.clear()
        self._rows = rows
        for row in rows:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            head = Gtk.Label(xalign=0, use_markup=True)
            head.set_markup(tr("proportions_window.b_markup_escape_text_b_span", markup_escape_text=GLib.markup_escape_text(row.label), level_color=LEVEL_COLOR[row.nivel], level_text=LEVEL_TEXT[row.nivel]))
            box.append(head)
            if row.model is not None and row.ref is not None:
                m, r = row.model.size, row.ref.size
                box.append(Gtk.Label(label=tr("proportions_window.tu_figura_m_m2_m3_mm", m=f"{m[0]:.1f}", m2=f"{m[1]:.1f}", m3=f"{m[2]:.1f}", r=f"{r[0]:.1f}", r2=f"{r[1]:.1f}", r3=f"{r[2]:.1f}"),
                                     xalign=0, wrap=True))
            box.append(Gtk.Label(label=row.text(), xalign=0, wrap=True))
            if row.place in ("cabeza", "torso", "brazo", "pelvis", "pierna") and self._place_name(row.place) in self.ctrl.session.scale_places():
                line = Gtk.Box(spacing=4)
                spins = []
                for ax, val in zip(PR.AXES, row.current):
                    line.append(Gtk.Label(label=ax))
                    sp = Gtk.SpinButton.new_with_range(0.2, 5.0, 0.01)
                    sp.set_digits(3)
                    sp.set_value(val)
                    spins.append(sp)
                    line.append(sp)
                self._spins[row.place] = tuple(spins)
                sug = Gtk.Button(label=tr("proportions_window.sugerida"), sensitive=row.suggested is not None)
                sug.connect("clicked", lambda _b, rw=row: self._set_spins(rw.place, rw.suggested))
                line.append(sug)
                ap = Gtk.Button(label=tr("common.aplicar"))
                ap.connect("clicked", lambda _b, rw=row: self._apply(rw.place))
                line.append(ap)
                box.append(line)
            self.table.append(box)
            self.table.append(Gtk.Separator())
        self.btn_all.set_sensitive(any(r.suggested is not None for r in rows))

    def _place_name(self, place: str) -> str:
        """Nombre del lugar en el proyecto: los miembros se llaman como el plano («brazo», «brazo 2»…)."""
        kind = {"brazo": "arm", "pierna": "leg"}.get(place)
        if kind is None:
            return place
        return next((l.name for l in self.ctrl.session.project.limbs if l.kind == kind), place)

    def _set_spins(self, place: str, values) -> None:
        if values is None or place not in self._spins:
            return
        for sp, v in zip(self._spins[place], values):
            sp.set_value(float(v))

    def _apply(self, place: str, reload: bool = True) -> bool:
        vals = tuple(sp.get_value() for sp in self._spins[place])
        ok = self.ctrl.set_part_scale(self._place_name(place), vals)
        if ok and reload:
            self.reload()
        return ok

    def _on_apply_all(self, *_):
        for row in self._rows:
            if row.suggested is not None and row.place in self._spins:
                self._set_spins(row.place, row.suggested)
                if not self._apply(row.place, reload=False):
                    break
        self.reload()

    def _on_fit(self, *_):
        if self.ctrl.apply_cut_fit():
            self.reload()
