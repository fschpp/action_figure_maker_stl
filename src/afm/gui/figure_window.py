"""Ventana «Armar figura desde catálogo»: una figura nueva, sin STL ni proyecto, eligiendo una pieza del catálogo por lugar.

La lógica está en `figure_builder.py` (se prueba sin GTK). Acá: un desplegable por lugar (cada pieza marcada con cómo
acopla a la ya elegida), vista previa 3/4 de la pieza elegida y, al armar, de la figura entera, y el armado en un hilo."""
from __future__ import annotations
from ..i18n import tr, loc

import os
import re
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .catalog_manager import PreviewRenderer, view_bounds  # noqa: E402,F401  (view_bounds: el encuadre lo hace PreviewRenderer)
from ..recipe import SCALE_PLACES  # noqa: E402
from ..mount import MOUNT_TYPES  # noqa: E402
from .progress_panel import ProgressPanel  # noqa: E402
from .figure_builder import NONE_LABEL, PLACE_LABELS, PLACE_ORDER, FigureBuilder, summary  # noqa: E402
from . import mount_pick as MP  # noqa: E402
from . import mount_preview as PV  # noqa: E402

YAW_MAX = 1.0e6      # GTK pide un tope numérico: este es técnico, el giro no tiene límite superior en la práctica
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _rgba(hex_text: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse(hex_text)
    return c


def _hex_of(rgba: Gdk.RGBA) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(round(max(0.0, min(1.0, v)) * 255)) for v in (rgba.red, rgba.green, rgba.blue)))


class FigureBuilderWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, catalog_root: str, remember_dir: str | None = None):
        super().__init__(title=tr("figure_window.armar_figura_desde_catalogo"), transient_for=parent, default_width=980, default_height=640)
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
        self._cp_token = 0                      # vista previa a color en curso (un cálculo más nuevo invalida al anterior)
        self._color_one = None                  # (ref, partes de color) de la pieza sola que se ve; None = gris
        self._tex_parts = None                  # [TexturedPart] de la figura armada con color (GLB); None = se dibuja el STL
        self._color_w: dict[str, dict] = {}     # T48: rol -> {"btn", "ent", "reset"} del bloque «Colores»
        self._dyaw = self._dpitch = 0.0
        self._drag0 = (0.0, 0.0)
        self._pick_group = None                 # M14: las piezas de la vista conjunta SIN partir por color (de ahí sale el clic)
        self._pick_mode = None                  # M14: None | "ancla" | "dir" (qué hace el ratón sobre la vista previa)
        self._pick_guard = False                # M14: los botones se cambian por código sin disparar sus avisos
        self._mount_extra: list = []            # M15: arma y puente de la montura elegida, para sumar a la vista conjunta
        self._mount_overlay = None              # M15: {nombre: (puntos, rgba)} del mapa de zonas
        self._mount_token = 0                   # M15: un cálculo más nuevo invalida al anterior
        self._combo = (None, None, [])          # M15: (id del grupo, id de lo extra, grupo + extra): se sube a la GPU solo si cambia
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
        title = Gtk.Label(label=tr("figure_window.linea_name_figuras_de_height_mm", name=cat.name, height_mm=f"{cat.height_mm:g}", root=self.builder.root), xalign=0, wrap=True)
        title.add_css_class("heading")
        box.append(title)
        box.append(Gtk.Label(label=tr("figure_window.nombre_de_la_figura"), xalign=0))
        name_row = Gtk.Box(spacing=6)
        self.ent_name = Gtk.Entry(text=self.builder.name, hexpand=True)
        self.ent_name.connect("changed", self._on_name)
        name_row.append(self.ent_name)
        self.btn_open = Gtk.Button(label=tr("figure_window.abrir_receta"))
        self.btn_open.set_tooltip_text(tr("figure_window.carga_una_receta_guardada_piezas_opciones"))
        self.btn_open.connect("clicked", self._on_open_recipe)
        name_row.append(self.btn_open)
        box.append(name_row)
        box.append(Gtk.Label(label=tr("figure_window.pieza_de_cada_lugar_empeza_por"), xalign=0, margin_top=6))
        for place in PLACE_ORDER:
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Label(label=loc(PLACE_LABELS[place]), xalign=0, width_chars=7))
            dd = Gtk.DropDown.new_from_strings([loc(NONE_LABEL)])
            dd.set_hexpand(True)
            dd.connect("notify::selected", self._on_pick, place)
            self._dd[place] = dd
            row.append(dd)
            box.append(row)
        self.chk_shoulder = Gtk.CheckButton(label=tr("figure_window.clic_en_los_hombros"))
        self.chk_hip = Gtk.CheckButton(label=tr("figure_window.clic_en_las_caderas"))
        for chk, key in ((self.chk_shoulder, "clic_hombro"), (self.chk_hip, "clic_cadera")):
            chk.connect("toggled", lambda b, k=key: self.builder.options.__setitem__(k, b.get_active()))
            box.append(chk)
        box.append(self._build_hand_section())
        box.append(self._build_scale_section())
        box.append(self._build_accessory_section())
        box.append(self._build_mount_section())
        box.append(self._build_colors_section())
        box.append(self._build_flat_section())
        self.lbl_problems = Gtk.Label(xalign=0, wrap=True)
        self.lbl_problems.add_css_class("dim-label")
        box.append(self.lbl_problems)

        bar = Gtk.Box(spacing=6, margin_top=4)
        self.btn_recipe = Gtk.Button(label=tr("figure_window.guardar_receta_en_el_catalogo"))
        self.btn_recipe.set_tooltip_text(tr("figure_window.guarda_las_piezas_elegidas_en_catalogo"))
        self.btn_recipe.connect("clicked", self._on_save_recipe)
        self.btn_build = Gtk.Button(label=tr("figure_window.armar_y_exportar"))
        self.btn_build.add_css_class("suggested-action")
        self.btn_build.connect("clicked", self._on_build)
        bar.append(self.btn_recipe)
        bar.append(self.btn_build)
        box.append(bar)
        self._last_project: str | None = None
        self.progress_panel = ProgressPanel()                # porcentaje, tarea en curso, pendientes y tiempo estimado
        box.append(self.progress_panel)
        self.btn_open_main = Gtk.Button(label=tr("figure_window.abrir_la_figura_armada_en_la"))
        self.btn_open_main.set_tooltip_text(tr("figure_window.abre_el_proyecto_recien_exportado_sin"))
        self.btn_open_main.set_visible(False)
        self.btn_open_main.connect("clicked", self._on_open_main)
        box.append(self.btn_open_main)
        hint = Gtk.Label(label=tr("figure_window.el_torso_arma_el_cuerpo_uno"),
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        box.append(hint)
        return box

    def _build_hand_section(self) -> Gtk.Widget:
        """Mismos controles de mano que la ventana principal: giro (−180°…180°, de a 1°) y «Mano articulada»."""
        exp = Gtk.Expander(label=tr("figure_window.mano_giro_y_articulacion"))
        self.exp_hand = exp
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=tr("common.giro_de_la_mano"), xalign=0, hexpand=True))
        self.sp_yaw = Gtk.SpinButton.new_with_range(-180.0, YAW_MAX, 1.0)      # sin tope superior (solo el mínimo de −180°)
        self.sp_yaw.set_digits(0)
        self.sp_yaw.set_tooltip_text(tr("figure_window.gira_la_mano_alrededor_de_la"))
        self.sp_yaw.connect("value-changed", self._on_hand)
        row.append(self.sp_yaw)
        box.append(row)
        self.chk_swivel = Gtk.CheckButton(label=tr("common.mano_articulada_pieza_aparte_gira_libre"))
        self.chk_swivel.set_tooltip_text(tr("figure_window.sin_marcar_la_base_de_la"))
        self.chk_swivel.connect("toggled", self._on_hand)
        box.append(self.chk_swivel)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=tr("figure_window.solape_mm"), xalign=0, hexpand=True))
        self.sp_overlap = Gtk.SpinButton.new_with_range(0.0, 5.0, 0.1)
        self.sp_overlap.set_digits(1)
        self.sp_overlap.set_tooltip_text(tr("figure_window.cuanto_se_hunde_la_base_de"))
        self.sp_overlap.connect("value-changed", self._on_hand)
        row.append(self.sp_overlap)
        self.row_overlap = row
        box.append(row)
        exp.set_child(box)
        return exp

    def _build_accessory_section(self) -> Gtk.Widget:
        """Accesorios del catálogo que acompañan a la figura: se exportan aparte (STL en piezas/ y en la lista de piezas)."""
        self.exp_acc = Gtk.Expander(label=tr("figure_window.accesorios_que_acompanan_a_la_figura"), expanded=False)
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
            lbl = Gtk.Label(label=tr("figure_window.el_catalogo_no_tiene_accesorios_afm"),
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

    # ------------------------------------------------------------ armas en el cuerpo (M13)
    def _build_mount_section(self) -> Gtk.Widget:
        """Monturas de armas al cuerpo (`FigureBuilder.mounts`): una lista, y los campos de la que está elegida. Ancla y dirección son
        números por ahora (el clic sobre la figura es M14)."""
        self.exp_mount = Gtk.Expander(label=tr("figure_window.armas_en_el_cuerpo"), expanded=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=12, margin_top=4)
        top = Gtk.Box(spacing=6)
        self.dd_mount = Gtk.DropDown.new_from_strings([""])
        self.dd_mount.set_hexpand(True)
        self.dd_mount.connect("notify::selected", self._on_mount_pick)
        self.btn_mount_add = Gtk.Button(label=tr("figure_window.montura_agregar"))
        self.btn_mount_add.connect("clicked", self._on_mount_add)
        self.btn_mount_del = Gtk.Button(label=tr("figure_window.montura_quitar"))
        self.btn_mount_del.connect("clicked", self._on_mount_del)
        for w in (self.dd_mount, self.btn_mount_add, self.btn_mount_del):
            top.append(w)
        box.append(top)
        self.box_mount_form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        def row(label: str, *widgets) -> None:
            r = Gtk.Box(spacing=6)
            r.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            for w in widgets:
                r.append(w)
            self.box_mount_form.append(r)

        def spins(lo: float, hi: float, step: float, digits: int, n: int = 3) -> list:
            out = []
            for _ in range(n):
                sp = Gtk.SpinButton.new_with_range(lo, hi, step)
                sp.set_digits(digits)
                sp.set_width_chars(6)
                sp.connect("value-changed", self._on_mount_field)
                out.append(sp)
            return out

        self.dd_m_weapon = Gtk.DropDown.new_from_strings([""])
        self.dd_m_place = Gtk.DropDown.new_from_strings([""])
        self.dd_m_side = Gtk.DropDown.new_from_strings([tr("figure_window.montura_lado_izq"), tr("figure_window.montura_lado_der")])
        self.dd_m_type = Gtk.DropDown.new_from_strings([tr("figure_window.montura_tipo_lazo"), tr("figure_window.montura_tipo_clip"), tr("figure_window.montura_tipo_cuna")])
        for dd in (self.dd_m_weapon, self.dd_m_place, self.dd_m_side, self.dd_m_type):
            dd.connect("notify::selected", self._on_mount_field)
        row(tr("figure_window.montura_arma"), self.dd_m_weapon)
        row(tr("figure_window.montura_lugar"), self.dd_m_place)
        row(tr("figure_window.montura_lado"), self.dd_m_side)
        row(tr("figure_window.montura_tipo"), self.dd_m_type)
        self._m_anchor = spins(-500.0, 500.0, 0.1, 2)
        self._m_dir = spins(-1.0, 1.0, 0.05, 2)
        row(tr("figure_window.montura_ancla_mm"), *self._m_anchor)
        row(tr("figure_window.montura_direccion"), *self._m_dir)
        pick = Gtk.Box(spacing=6)
        self.tgl_m_pick = Gtk.ToggleButton(label=tr("figure_window.montura_pick_ancla"))
        self.tgl_m_dir = Gtk.ToggleButton(label=tr("figure_window.montura_pick_dir"))
        self.tgl_m_pick.connect("toggled", self._on_pick_toggle, "ancla")
        self.tgl_m_dir.connect("toggled", self._on_pick_toggle, "dir")
        pick.append(self.tgl_m_pick)
        pick.append(self.tgl_m_dir)
        self.box_mount_form.append(pick)
        view = Gtk.Box(spacing=6)
        self.tgl_m_view = Gtk.ToggleButton(label=tr("figure_window.montura_ver"))
        self.tgl_m_map = Gtk.ToggleButton(label=tr("figure_window.montura_mapa"))
        self.tgl_m_view.connect("toggled", self._on_mount_view_toggle)
        self.tgl_m_map.connect("toggled", self._on_mount_view_toggle)
        view.append(self.tgl_m_view)
        view.append(self.tgl_m_map)
        self.box_mount_form.append(view)
        self.lbl_mount_preview = Gtk.Label(xalign=0, wrap=True)
        self.box_mount_form.append(self.lbl_mount_preview)
        self.lbl_mount_pick = Gtk.Label(xalign=0, wrap=True)
        self.lbl_mount_pick.add_css_class("dim-label")
        self.box_mount_form.append(self.lbl_mount_pick)
        (self.sp_m_gap,) = spins(0.0, 2.0, 0.05, 2, 1)
        (self.sp_m_wall,) = spins(0.0, 5.0, 0.1, 1, 1)
        row(tr("figure_window.montura_holgura_mm"), self.sp_m_gap)
        row(tr("figure_window.montura_pared_mm"), self.sp_m_wall)
        self.lbl_mount_status = Gtk.Label(xalign=0, wrap=True)
        self.box_mount_form.append(self.lbl_mount_status)
        note = Gtk.Label(label=tr("figure_window.montura_nota"), xalign=0, wrap=True)
        note.add_css_class("dim-label")
        self.box_mount_form.append(note)
        box.append(self.box_mount_form)
        self.exp_mount.set_child(box)
        self._mount_refill_pending = False
        return self.exp_mount

    def _mount_index(self) -> int | None:
        """Índice de la montura elegida en la lista, o None si no hay (GTK da un valor enorme sin selección)."""
        i = self.dd_mount.get_selected()
        return i if i < len(self.builder.mounts) else None

    def _refill_mounts(self, select: int | None = None) -> None:
        """Pone lista y campos como están en el constructor. Se llama con `_updating` activo (o lo activa); nunca desde un `notify` del
        desplegable cuyo modelo cambia (ver `_schedule_mount_refill`)."""
        b = self.builder
        was, self._updating = self._updating, True
        try:
            n = len(b.mounts)
            cur = select if select is not None else (self._mount_index() or 0)
            cur = min(cur, n - 1) if n else None
            self.dd_mount.set_model(Gtk.StringList.new([b.mount_label(i) for i in range(n)] or [""]))
            weapons = b.mount_weapons()
            self.dd_m_weapon.set_model(Gtk.StringList.new(weapons or [""]))
            places = list(b.mount_places())
            self.dd_m_place.set_model(Gtk.StringList.new([loc(PLACE_LABELS[p]) for p in places]))
            if cur is not None:
                self.dd_mount.set_selected(cur)
                m = b.mounts[cur]
                self.dd_m_weapon.set_selected(weapons.index(m.arma) if m.arma in weapons else 0)
                self.dd_m_place.set_selected(places.index(m.lugar) if m.lugar in places else 0)
                self.dd_m_side.set_selected(0 if m.lado == "izq" else 1)
                self.dd_m_type.set_selected(MOUNT_TYPES.index(m.tipo))
                for sp, v in zip(self._m_anchor, m.ancla):
                    sp.set_value(v)
                for sp, v in zip(self._m_dir, m.direccion):
                    sp.set_value(v)
                self.sp_m_gap.set_value(m.holgura)
                self.sp_m_wall.set_value(0.0 if m.pared is None else m.pared)
                self.lbl_mount_status.set_text(self._mount_status_text(cur))
        finally:
            self._updating = was
        ok = not self._busy
        self.box_mount_form.set_visible(bool(b.mounts))
        self.btn_mount_add.set_sensitive(ok and bool(b.mount_weapons()))
        self.btn_mount_del.set_sensitive(ok and bool(b.mounts))
        self.box_mount_form.set_sensitive(ok)
        self.exp_mount.set_sensitive(bool(b.accessories) or bool(b.mounts))

    def _mount_status_text(self, i: int) -> str:
        st = self.builder.mount_status(i)
        if st is None:
            return tr("figure_window.montura_sin_armar")
        head = tr({"ok": "figure_window.montura_estado_ok", "aviso": "figure_window.montura_estado_aviso",
                   "no": "figure_window.montura_estado_no"}.get(st["nivel"], "figure_window.montura_estado_ok"))
        return "\n".join([head] + [f"- {t}" for t in st["problemas"] + st["avisos"]])

    def _schedule_mount_refill(self, select: int | None = None) -> None:
        if not self._mount_refill_pending:
            self._mount_refill_pending = True
            GLib.idle_add(self._mount_refill_idle, select)

    def _mount_refill_idle(self, select) -> bool:
        self._mount_refill_pending = False
        self._refill_mounts(select)
        if self.tgl_all.get_active():
            self._redraw()              # las marcas de ancla y dirección siguen a la montura
            self._start_mount_preview()
        return False

    # ------------------------------------------------------------ M15: arma y puente, mapa de zonas
    def _on_mount_view_toggle(self, *_args) -> None:
        if self._pick_guard:
            return
        if (self.tgl_m_view.get_active() or self.tgl_m_map.get_active()) and not self.tgl_all.get_active():
            self.tgl_all.set_active(True)         # necesita la vista conjunta: al prenderla se vuelve a calcular
        self._start_mount_preview()

    def _set_view_toggles(self, on: bool) -> None:
        """Prende o apaga «Ver arma y montura» y «Mapa de zonas» por código, sin que sus avisos vuelvan a entrar, y borra lo dibujado."""
        was, self._pick_guard = self._pick_guard, True
        try:
            self.tgl_m_view.set_active(on)
            self.tgl_m_map.set_active(on)
        finally:
            self._pick_guard = was
        if not on:
            self._mount_token += 1
            self._mount_extra, self._mount_overlay = [], None
            self.lbl_mount_preview.set_text("")
            self.lbl_mount_preview.remove_css_class("error")

    def _clear_mount_preview(self, text: str = "") -> None:
        self._mount_extra, self._mount_overlay = [], None
        self.lbl_mount_preview.set_text(text)
        self.lbl_mount_preview.remove_css_class("error")
        self._redraw()

    def _start_mount_preview(self) -> None:
        """Calcula en un hilo el arma, el puente y el mapa de zonas de la montura elegida (lo que esté prendido) y los dibuja al terminar."""
        self._mount_token += 1
        token = self._mount_token
        i, group = self._mount_index(), self._pick_group
        want_view, want_map = self.tgl_m_view.get_active(), self.tgl_m_map.get_active()
        if i is None or group is None or not (want_view or want_map):
            if self._mount_extra or self._mount_overlay or self.lbl_mount_preview.get_text():
                self._clear_mount_preview()
            return
        b = self.builder

        def work() -> None:
            try:
                pv = PV.mount_preview(b, i, group) if want_view else None
                zm = PV.zone_map(b, i, group) if want_map else None
                GLib.idle_add(self._mount_preview_done, token, pv, zm, None)
            except Exception as exc:                              # noqa: BLE001  (se dice en la ventana; el resto sigue andando)
                GLib.idle_add(self._mount_preview_done, token, None, None, exc)

        threading.Thread(target=work, daemon=True, name="afm-vista-montura").start()

    def _mount_preview_done(self, token: int, pv, zm, error) -> bool:
        if token != self._mount_token:                            # mientras tanto cambió la montura
            return False
        lines, bad = [], False
        self._mount_extra = pv.parts if pv is not None else []
        self._mount_overlay = zm.overlay if zm is not None else None
        if error is not None:
            lines.append(str(error))
            bad = True
        if pv is not None:
            lines.append(pv.text)
            bad = bad or not pv.ok or bool(pv.avisos)
        if zm is not None:
            lines.append(zm.text)
        self.lbl_mount_preview.set_text("\n".join(lines))
        (self.lbl_mount_preview.add_css_class if bad else self.lbl_mount_preview.remove_css_class)("error")      # rojo: no sale o con avisos
        self._redraw()
        return False

    def _draw_group(self):
        """Lo que dibuja la vista conjunta: las piezas (con color si ya se calculó) y, encima, el arma y el puente. Misma lista mientras
        nada cambie, para que girar la vista no vuelva a subir todo a la GPU."""
        if not self._mount_extra:
            return self._group
        key = (id(self._group), id(self._mount_extra))
        if self._combo[:2] != key:
            self._combo = (*key, list(self._group) + list(self._mount_extra))
        return self._combo[2]

    # ------------------------------------------------------------ M14: ancla y dirección con el ratón
    def _set_pick_mode(self, mode: str | None) -> None:
        """Pone el modo del ratón y deja los dos botones como corresponde, sin que sus avisos vuelvan a entrar."""
        self._pick_mode = mode
        was, self._pick_guard = self._pick_guard, True
        try:
            self.tgl_m_pick.set_active(mode == "ancla")
            self.tgl_m_dir.set_active(mode == "dir")
        finally:
            self._pick_guard = was
        self.lbl_mount_pick.set_text({"ancla": tr("figure_window.montura_pick_ayuda_ancla"), "dir": tr("figure_window.montura_pick_ayuda_dir")}.get(mode, ""))

    def _on_pick_toggle(self, btn: Gtk.ToggleButton, mode: str) -> None:
        if self._pick_guard:
            return
        if not btn.get_active():
            self._set_pick_mode(None if self._pick_mode == mode else self._pick_mode)
            return
        if self._mount_index() is None:
            self._set_pick_mode(None)
            return
        if not self.tgl_all.get_active():
            self.tgl_all.set_active(True)         # elegir con el ratón necesita la vista conjunta (lo apaga _refresh_group si no se puede)
        if self._pick_group is None:
            self._set_pick_mode(None)
            self.lbl_mount_pick.set_text(tr("figure_window.montura_pick_sin_vista"))
            return
        self._set_pick_mode(mode)

    def _image_size(self) -> tuple[int, int]:
        return self.preview.width, self.preview.height

    def _apply_pick_anchor(self, x: float, y: float) -> None:
        i = self._mount_index()
        if i is None or self._pick_group is None:
            return
        b = self.builder
        try:
            a = MP.pick_from_widget(x, y, (self.picture.get_width(), self.picture.get_height()), self._image_size(), self._pick_group,
                                    b.mounts[i].lugar, self._dyaw, self._dpitch)
            b.set_mount(i, ancla=a.ancla, lado=a.lado)
        except ValueError as exc:                               # MountPickError es un ValueError
            self.lbl_mount_pick.set_text(str(exc))
            return
        self.lbl_mount_pick.set_text(tr("figure_window.montura_pick_ancla_puesta", x=f"{a.ancla[0]:g}", y=f"{a.ancla[1]:g}", z=f"{a.ancla[2]:g}", lado=a.lado))
        self._schedule_mount_refill(i)
        self._sync()

    def _apply_pick_direction(self, x0: float, y0: float, x1: float, y1: float) -> None:
        i = self._mount_index()
        if i is None or self._pick_group is None:
            return
        b = self.builder
        try:
            d = MP.direction_from_widget(x0, y0, x1, y1, (self.picture.get_width(), self.picture.get_height()), self._image_size(),
                                         self._pick_group, b.mounts[i].ancla, self._dyaw, self._dpitch)
            b.set_mount(i, direccion=d)
        except ValueError as exc:
            self.lbl_mount_pick.set_text(str(exc))
            return
        self.lbl_mount_pick.set_text(tr("figure_window.montura_pick_dir_puesta", x=f"{d[0]:g}", y=f"{d[1]:g}", z=f"{d[2]:g}"))
        self._schedule_mount_refill(i)
        self._sync()

    def _on_mount_pick(self, *_args) -> None:
        if self._updating or self._mount_index() is None:
            return
        self._schedule_mount_refill(self._mount_index())

    def _on_mount_add(self, *_args) -> None:
        weapons = self.builder.mount_weapons()
        if not weapons:
            self.lbl_result.set_text(tr("figure_window.montura_marca_un_accesorio"))
            return
        try:
            i = self.builder.add_mount(weapons[0])
        except ValueError as exc:
            self.lbl_result.set_text(str(exc))
            return
        self._schedule_mount_refill(i)
        self._sync()

    def _on_mount_del(self, *_args) -> None:
        i = self._mount_index()
        if i is None:
            return
        try:
            self.builder.remove_mount(i)
        except ValueError as exc:
            self.lbl_result.set_text(str(exc))
        self._schedule_mount_refill(max(0, i - 1))
        self._sync()

    def _on_mount_field(self, *_args) -> None:
        """Un campo del formulario cambió: va a la montura elegida. Si el valor no vale, se avisa y el formulario vuelve a lo que había."""
        i = self._mount_index()
        if self._updating or i is None:
            return
        b = self.builder
        weapons, places = b.mount_weapons(), list(b.mount_places())
        w, p = self.dd_m_weapon.get_selected(), self.dd_m_place.get_selected()
        try:
            b.set_mount(i, arma=weapons[w] if w < len(weapons) else b.mounts[i].arma, lugar=places[p] if p < len(places) else b.mounts[i].lugar,
                        lado="izq" if self.dd_m_side.get_selected() == 0 else "der", tipo=MOUNT_TYPES[self.dd_m_type.get_selected()],
                        ancla=[s.get_value() for s in self._m_anchor],
                        direccion=[s.get_value() for s in self._m_dir], holgura=self.sp_m_gap.get_value(),
                        pared=self.sp_m_wall.get_value() or None)
        except ValueError as exc:
            self.lbl_result.set_text(str(exc))
        self._schedule_mount_refill(i)
        self._sync()

    def _build_scale_section(self) -> Gtk.Widget:
        exp = Gtk.Expander(label=tr("figure_window.escala_de_cada_pieza_x_ancho"))
        self.exp_scale = exp
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6)
        for place in SCALE_PLACES:
            row = Gtk.Box(spacing=4)
            row.append(Gtk.Label(label=loc(PLACE_LABELS[place]), xalign=0, width_chars=7))
            sps = []
            for axis in "XYZ":
                sp = Gtk.SpinButton.new_with_range(0.5, 2.0, 0.01)
                sp.set_digits(2)
                sp.set_value(1.0)
                sp.set_width_chars(5)
                sp.set_tooltip_text(tr("figure_window.factor_en_axis_1_original_1", axis=axis))
                sp.connect("value-changed", self._on_scale, place, axis)
                sps.append(sp)
                row.append(sp)
            lock = Gtk.CheckButton(label=tr("common.igual"), active=True)
            lock.set_tooltip_text(tr("figure_window.los_tres_ejes_se_mueven_juntos"))
            row.append(lock)
            self._scale_sp[place], self._scale_lock[place], self._scale_row[place] = sps, lock, row
            box.append(row)
        note = Gtk.Label(label=tr("figure_window.la_pieza_se_escala_alrededor_de"),
                         xalign=0, wrap=True)
        note.add_css_class("dim-label")
        box.append(note)
        exp.set_child(box)
        return exp

    def _build_colors_section(self) -> Gtk.Widget:
        """T48: los roles de color de la paleta de la línea. Cambiar uno guarda un cambio SOLO de esta figura (`FigureBuilder.set_override`);
        «Recolorear» lo aplica a la figura ya armada sin volver a armarla. La lógica está en `FigureBuilder`; acá solo los controles."""
        exp = Gtk.Expander(label=tr("figure_window.colores_titulo"))
        exp.set_tooltip_text(tr("figure_window.colores_tip"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        self._color_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(self._color_rows)
        self.lbl_colors = Gtk.Label(xalign=0, wrap=True)
        self.lbl_colors.add_css_class("dim-label")
        box.append(self.lbl_colors)
        bar = Gtk.Box(spacing=6)
        self.btn_reset_colors = Gtk.Button(label=tr("figure_window.colores_restablecer_todos"))
        self.btn_reset_colors.connect("clicked", self._on_reset_colors)
        self.btn_recolor = Gtk.Button(label=tr("figure_window.colores_recolorear"))
        self.btn_recolor.set_tooltip_text(tr("figure_window.colores_recolorear_tip"))
        self.btn_recolor.connect("clicked", self._on_recolor)
        bar.append(self.btn_reset_colors)
        bar.append(self.btn_recolor)
        box.append(bar)
        exp.set_child(box)
        self.exp_colors = exp
        return exp

    def _build_flat_section(self) -> Gtk.Widget:
        """«Imprimir sin soportes» (lo mismo que la ventana principal): aplana piernas y cola con un corte horizontal y gira los brazos hacia
        adelante. Es una opción de exportación: no cambia la receta. La lógica está en `FigureBuilder` (`flat`, `flat_depth`, `fill_hip`)."""
        exp = Gtk.Expander(label=tr("generar.impresion_sin_soportes"))
        self.exp_flat = exp
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        self.chk_flat = Gtk.CheckButton(label=tr("generar.modificar_para_imprimir_sin_soportes_aplan"))
        self.chk_flat.set_tooltip_text(tr("generar.corta_con_un_plano_horizontal_sobre"))
        self.chk_flat.connect("toggled", self._on_flat)
        box.append(self.chk_flat)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=tr("figure_window.sin_soportes_altura_del_corte"), xalign=0, hexpand=True, wrap=True))
        self.sp_flat = Gtk.SpinButton.new_with_range(0.2, 6.0, 0.1)
        self.sp_flat.set_digits(1)
        self.sp_flat.set_value(float(self.builder.catalog().params.flat_depth))
        self.sp_flat.connect("value-changed", self._on_flat)
        row.append(self.sp_flat)
        self.row_flat = row
        box.append(row)
        self.chk_fill_hip = Gtk.CheckButton(label=tr("generar.rellenar_los_muslos_hasta_la_pelvis"))
        self.chk_fill_hip.set_tooltip_text(tr("generar.el_corte_plano_le_saca_a"))
        self.chk_fill_hip.connect("toggled", self._on_flat)
        box.append(self.chk_fill_hip)
        note = Gtk.Label(label=tr("figure_window.sin_soportes_nota"), xalign=0, wrap=True)
        note.add_css_class("dim-label")
        box.append(note)
        exp.set_child(box)
        return exp

    def _on_flat(self, *_args) -> None:
        b = self.builder
        b.flat = self.chk_flat.get_active()
        b.flat_depth = round(self.sp_flat.get_value(), 3)
        b.fill_hip = self.chk_fill_hip.get_active()
        self.row_flat.set_sensitive(b.flat and not self._busy)
        self.chk_fill_hip.set_sensitive(b.flat and not self._busy)
        self._update_color_status()

    def _sync_colors(self) -> None:
        """Vuelve a armar las filas (una por rol de la línea) con los colores de ahora y los textos de estado."""
        b = self.builder
        try:
            rows = b.palette_rows()
        except Exception:                                      # noqa: BLE001 — paleta.json ilegible: el bloque queda vacío y el armado lo avisa
            rows = []
        child = self._color_rows.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._color_rows.remove(child)
            child = nxt
        self._color_w = {}
        self._updating = True
        try:
            for r in rows:
                line = Gtk.Box(spacing=6)
                btn = None
                if hasattr(Gtk, "ColorDialogButton"):         # GTK ≥ 4.10; en una versión anterior queda solo el campo HEX
                    btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
                    btn.set_rgba(_rgba(r["hex"]))
                    btn.connect("notify::rgba", self._on_color_picked, r["id"])
                    line.append(btn)
                name = Gtk.Label(label=r["nombre"] or r["id"], xalign=0, width_chars=10)
                line.append(name)
                ent = Gtk.Entry(text=r["hex"], width_chars=8, max_length=7)
                ent.connect("changed", self._on_color_hex, r["id"])
                line.append(ent)
                reset = Gtk.Button(label="↺", visible=r["cambiado"])
                reset.set_tooltip_text(tr("figure_window.color_restablecer"))
                reset.connect("clicked", self._on_reset_role, r["id"])
                line.append(reset)
                use = Gtk.Label(label=tr("figure_window.color_usado_por", n=len(r["piezas"])) if r["piezas"] else tr("figure_window.color_sin_uso"),
                                xalign=0, hexpand=True)
                use.add_css_class("dim-label")
                if r["piezas"]:
                    use.set_tooltip_text(", ".join(r["piezas"]))
                line.append(use)
                self._color_rows.append(line)
                self._color_w[r["id"]] = {"btn": btn, "ent": ent, "reset": reset}
        finally:
            self._updating = False
        self._update_color_status(has_rows=bool(rows))

    def _update_color_status(self, has_rows: bool | None = None) -> None:
        """Textos de estado y botones del bloque «Colores» (sin tocar las filas: se llama mientras se escribe un HEX)."""
        b = self.builder
        if has_rows is None:
            has_rows = bool(self._color_w)
        lines: list[str] = []
        try:
            lines = b.color_report_lines()
        except Exception:                                      # noqa: BLE001
            pass
        if not has_rows and lines:                            # hay roles en uso o piezas sin color pero la línea no tiene paleta
            lines.insert(0, tr("figure_window.colores_sin_paleta"))
        if b.palette_override:
            if b.can_recolor():
                lines.append(tr("figure_window.colores_pendientes") if b.overrides_pending() else tr("figure_window.colores_al_dia"))
            elif not b._built:
                lines.append(tr("figure_window.colores_sin_figura"))
        self.lbl_colors.set_text("\n".join(lines))
        for rid, w in self._color_w.items():
            w["reset"].set_visible(rid in b.palette_override)
        self.btn_reset_colors.set_sensitive(bool(b.palette_override) and not self._busy)
        self.btn_recolor.set_sensitive(b.can_recolor() and b.overrides_pending() and not self._busy)
        self.exp_colors.set_visible(has_rows or bool(lines))

    def _apply_role_color(self, role: str, hex_color: str, source: str) -> None:
        """`source`: «btn» o «ent» (el control que originó el cambio; el otro se pone al día sin disparar nada)."""
        try:
            self.builder.set_override(role, hex_color)
        except ValueError as exc:
            self.lbl_colors.set_text(str(exc))
            return
        w = self._color_w.get(role)
        if w:
            self._updating = True
            try:
                if source != "ent":
                    w["ent"].set_text(hex_color.upper())
                if source != "btn" and w["btn"] is not None:
                    w["btn"].set_rgba(_rgba(hex_color))
            finally:
                self._updating = False
        self._update_color_status()
        self._start_color_preview()

    def _on_color_picked(self, btn, _p, role: str) -> None:
        if not self._updating:
            self._apply_role_color(role, _hex_of(btn.get_rgba()), "btn")

    def _on_color_hex(self, ent: Gtk.Entry, role: str) -> None:
        if self._updating:
            return
        text = ent.get_text().strip()
        if HEX_RE.match(text):                                 # solo un «#RRGGBB» completo; mientras se escribe no se toca nada
            self._apply_role_color(role, text, "ent")
        elif len(text) >= 7:
            self.lbl_colors.set_text(tr("figure_window.colores_hex_invalido"))

    def _on_reset_role(self, _btn, role: str) -> None:
        self.builder.clear_override(role)
        self._sync_colors()
        self._start_color_preview()

    def _on_reset_colors(self, *_) -> None:
        self.builder.clear_override()
        self._sync_colors()
        self._start_color_preview()

    def _on_recolor(self, *_) -> None:
        if self._busy:
            return
        self._t0 = time.monotonic()
        self._set_busy(True)
        self.lbl_result.set_text(tr("figure_window.colores_recoloreando"))

        def work() -> None:
            try:
                out = self.builder.recolor()
            except Exception as exc:                           # noqa: BLE001 — se informa en la ventana
                GLib.idle_add(self._recolor_done, None, exc)
            else:
                GLib.idle_add(self._recolor_done, out, None)

        threading.Thread(target=work, daemon=True, name="afm-recolorear").start()

    def _recolor_done(self, out, error) -> bool:
        self._set_busy(False)
        if error is not None:
            self.lbl_result.set_text(tr("figure_window.colores_no_se_pudo", exc=error))
            self._update_color_status()
            return False
        self.lbl_result.set_text(tr("figure_window.colores_recoloreada", seg=f"{out.get('segundos', time.monotonic() - self._t0):g}"))
        if out.get("glb") and os.path.exists(out["glb"]):
            try:
                self._show_glb(out["glb"])
            except Exception as exc:                           # noqa: BLE001
                self.lbl_result.set_text(self.lbl_result.get_text() + ('\n' + tr("figure_window.sin_vista_previa_exc", exc=exc)))
        self._update_color_status()
        return False

    def _build_preview(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.picture.set_size_request(340, 300)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag)
        drag.connect("drag-end", self._on_drag_end)
        self.picture.add_controller(drag)
        frame = Gtk.Frame()
        frame.set_child(self.picture)
        box.append(frame)
        row = Gtk.Box(spacing=6)
        reset = Gtk.Button(label=tr("common.vista_3_4"))
        reset.connect("clicked", self._on_reset_view)
        row.append(reset)
        self.tgl_all = Gtk.ToggleButton(label=tr("figure_window.ver_todas_las_piezas_juntas"))
        self.tgl_all.set_tooltip_text(tr("figure_window.pone_las_piezas_elegidas_en_su"))
        self.tgl_all.connect("toggled", self._on_toggle_all)
        row.append(self.tgl_all)
        self.tgl_tex = Gtk.ToggleButton(label=tr("figure_window.textura"), active=True, visible=False)
        self.tgl_tex.set_tooltip_text(tr("figure_window.textura_tooltip"))
        self.tgl_tex.connect("toggled", lambda *_: self._redraw())
        row.append(self.tgl_tex)                # solo aparece cuando la figura armada trae color
        row.append(Gtk.Label(label=tr("figure_window.arrastra_para_girar"), xalign=0, hexpand=True))
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
                dd.set_model(Gtk.StringList.new([loc(NONE_LABEL)] + [b.label(m, fit) for m in metas]))
                cur = b.choices.get(place)
                dd.set_selected(self._refs[place].index(cur) + 1 if cur in self._refs[place] else 0)
                enabled = not self._busy and (place != "pelvis" or b.waist) and bool(metas)
                dd.set_sensitive(enabled)
                dd.set_tooltip_text(None if metas else tr("figure_window.el_catalogo_no_tiene_piezas_de"))
        finally:
            self._updating = False
        self._updating = True
        try:
            self._refill_accessories(cat)
            self._refill_mounts()
        finally:
            self._updating = False
        self._sync_controls()
        self._sync_colors()
        self._sync()

    def _sync(self) -> None:
        probs = self.builder.problems()
        self.lbl_problems.set_text((tr("common.falta") + ' ') + "; ".join(probs) if probs else tr("figure_window.listo_para_armar"))
        self.btn_build.set_sensitive(not probs and not self._busy)
        self.btn_recipe.set_sensitive(not probs and not self._busy)
        self.ent_name.set_sensitive(not self._busy)
        self.btn_open.set_sensitive(not self._busy)
        if hasattr(self, "exp_colors"):                      # el bloque «Colores» ya se construyó
            self._update_color_status()

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
            yaw = (b.hand["yaw"] + 180.0) % 360.0 - 180.0 if b.hand["yaw"] < -180.0 else b.hand["yaw"]       # el mínimo del cuadro es −180°
            self.sp_yaw.set_value(yaw)
            self.chk_swivel.set_active(b.hand["union"] == "giro")
            self.sp_overlap.set_value(b.hand["solape"])
            for place in SCALE_PLACES:
                k = b.scale_of(place)
                for sp, v in zip(self._scale_sp[place], k):
                    sp.set_value(v)
                self._scale_lock[place].set_active(max(k) - min(k) < 1e-9)
        finally:
            self._updating = False
        has_hand = bool(b.choices.get("mano")) and not self._busy
        if hasattr(self, "exp_flat"):
            self.exp_flat.set_sensitive(not self._busy)
            self.row_flat.set_sensitive(b.flat and not self._busy)
            self.chk_fill_hip.set_sensitive(b.flat and not self._busy)
        self.exp_hand.set_sensitive(has_hand)
        self.row_overlap.set_sensitive(b.hand["union"] == "solape")
        for place in SCALE_PLACES:
            self._scale_row[place].set_sensitive(bool(b.choices.get(place)) and not self._busy)

    def _on_hand(self, *_args) -> None:
        if self._updating:
            return
        try:
            self.builder.set_hand(yaw=self.sp_yaw.get_value(), union="giro" if self.chk_swivel.get_active() else "solape",
                                  solape=self.sp_overlap.get_value())
        except ValueError as exc:
            self.lbl_result.set_text(str(exc))
        self.row_overlap.set_sensitive(self.builder.hand["union"] == "solape")
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
        dlg = Gtk.FileDialog(title=tr("figure_window.abrir_receta_para_retocarla"))
        flt = Gtk.FileFilter()
        flt.set_name(tr("figure_window.recetas_receta_json"))
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
        msg = tr("figure_window.receta_abierta_path_retocala_y_guardala", path=path)
        if warns:
            msg += ('\n' + tr("figure_window.no_se_pudo_cargar_todo") + '\n') + "\n".join(f"• {w}" for w in warns)
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
            self._group = self._pick_group = None
            if hasattr(self, "tgl_m_pick"):
                self._set_pick_mode(None)           # sin vista conjunta no hay dónde elegir
                self._set_view_toggles(False)
            self._redraw()
            return
        try:
            self._group, notes = self.builder.preview()
            self._pick_group = self._group           # el color parte las piezas por color: el clic usa estas, de una sola pieza por nombre
        except Exception as exc:                              # noqa: BLE001  (sin torso, pieza rota…): se dice y se vuelve a la pieza
            self._group = self._pick_group = None
            if hasattr(self, "tgl_m_pick"):
                self._set_pick_mode(None)
                self._set_view_toggles(False)
            self.lbl_result.set_text(tr("figure_window.no_se_puede_ver_la_figura", exc=exc))
            self._redraw()
            return
        if notes:
            self.lbl_result.set_text((tr("figure_window.vista_conjunta_sin_pasadores_ni_holguras") + '\n') + "\n".join(f"• {n}" for n in notes))
        self._redraw()
        self._start_color_preview()
        if hasattr(self, "tgl_m_view"):
            self._start_mount_preview()             # las piezas cambiaron: el arma, el puente y el mapa se vuelven a calcular

    def _show_piece(self, ref: str) -> None:
        if self.tgl_all.get_active():
            self._refresh_group()
            return
        try:
            meta, mesh = self.builder.catalog().load(ref, check_hash=False)
        except Exception as exc:                              # noqa: BLE001
            self.lbl_result.set_text(tr("figure_window.no_se_pudo_leer_ref_exc", ref=ref, exc=exc))
            return
        self._set_shown(mesh, meta)

    # ------------------------------------------------------------ vista previa a color
    def _start_color_preview(self) -> None:
        """Calcula en un hilo la vista previa a color de lo que se ve (la pieza sola o todas juntas) y la redibuja al terminar; mientras, se ve la
        versión de siempre (gris, o un color plano por tipo de pieza). No hace nada si ninguna pieza lleva color."""
        self._cp_token += 1
        token = self._cp_token
        b = self.builder
        group = self.tgl_all.get_active()
        shown = self._shown
        self._color_one = None
        if self._tex_parts is not None:                           # figura armada con textura de verdad: no hay nada que calcular
            return
        if group:
            if not b.has_colored_choice():
                return
        elif shown is None or shown[1] is None or not getattr(shown[1], "color", None):
            return

        def work() -> None:
            try:
                if group:
                    parts, notes = b.preview_color()
                else:
                    parts, notes = b.piece_color_parts(shown[1], shown[0]), []
                GLib.idle_add(self._color_preview_done, token, parts, notes, None)
            except Exception as exc:                              # noqa: BLE001  (se dice en la ventana; la vista de siempre sigue)
                GLib.idle_add(self._color_preview_done, token, None, [], exc)

        threading.Thread(target=work, daemon=True, name="afm-vista-color").start()

    def _color_preview_done(self, token: int, parts, notes, error) -> bool:
        if token != self._cp_token:                               # mientras tanto se cambió lo que se ve
            return False
        if error is not None:
            self.lbl_result.set_text(tr("figure_window.color_vista_error", exc=error))
            return False
        if not parts:
            return False
        if self.tgl_all.get_active():
            self._group = parts
            if notes:
                self.lbl_result.set_text(tr("figure_window.color_vista_avisos") + '\n' + "\n".join(f"• {n}" for n in notes))
        elif self._shown is not None and self._shown[1] is not None:
            self._color_one = (self._shown[1].ref, parts)
        self._redraw()
        return False

    def _set_shown(self, mesh, meta) -> None:
        self._shown = (mesh, meta)
        self._color_one = None
        self._tex_parts = None
        self.tgl_tex.set_visible(False)
        self._dyaw = self._dpitch = 0.0
        self._redraw()
        self._start_color_preview()

    def _redraw(self) -> None:
        group = self._draw_group() if self.tgl_all.get_active() else None
        if group is None and self._shown is None and self._tex_parts is None:
            return
        try:
            if group is not None:
                bounds = MP.parts_bounds(self._pick_group) if self._pick_group else None      # el mismo encuadre que usa el clic
                img = self.preview.render_parts(group, self._dyaw, self._dpitch, marks=MP.mount_marks(self.builder.mounts), bounds=bounds,
                                               overlay=self._mount_overlay)
            elif self._tex_parts is not None:
                img = self.preview.render_textured(self._tex_parts, self.tgl_tex.get_active(), self._dyaw, self._dpitch)
            else:
                mesh, meta = self._shown
                co = self._color_one
                cparts = co[1] if co is not None and meta is not None and co[0] == meta.ref else None
                img = self.preview.render(mesh, meta, self._dyaw, self._dpitch, color_parts=cparts)
        except Exception as exc:                              # noqa: BLE001  (sin EGL: el resto sigue andando)
            self.picture.set_paintable(None)
            self.lbl_result.set_text(tr("common.la_vista_previa_3d_no_esta", exc=exc))
            return
        h, w = img.shape[:2]
        data = GLib.Bytes.new(img.tobytes())
        self.picture.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, data, w * 4))

    def _on_drag_begin(self, _g, x: float, y: float) -> None:
        self._drag0 = (self._dyaw, self._dpitch)
        self._drag_start = (x, y)

    def _on_drag_end(self, _g, dx: float, dy: float) -> None:
        """M14: con el modo «Ancla» un clic (sin arrastrar) elige el punto; con «Dirección» el arrastre es la dirección."""
        x0, y0 = getattr(self, "_drag_start", (0.0, 0.0))
        if self._pick_mode == "ancla" and MP.is_click(dx, dy):
            self._apply_pick_anchor(x0, y0)
        elif self._pick_mode == "dir":
            self._apply_pick_direction(x0, y0, x0 + dx, y0 + dy)

    def _on_drag(self, _g, dx: float, dy: float) -> None:
        if self._pick_mode == "dir":
            return                  # el arrastre marca la dirección: no gira la vista
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
            self.lbl_result.set_text(tr("figure_window.no_se_guardo_la_receta_exc", exc=exc))
            return
        self.lbl_result.set_text(tr("figure_window.receta_guardada_en_path", path=path))

    def _on_build(self, *_) -> None:
        dlg = Gtk.FileDialog(title=tr("figure_window.carpeta_donde_exportar_la_figura"))

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
        self.lbl_result.set_text(tr("figure_window.armando_la_figura_tarda_unos_minutos"))
        try:
            stages = self.builder.progress_stages()
        except Exception:                                     # noqa: BLE001 — sin plan la barra solo late
            stages = None
        self.progress_panel.start(stages, tr("figure_window.armando_la_figura"))
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
            self.lbl_result.set_text(tr("figure_window.no_se_pudo_abrir_en_la", exc=exc))

    def _build_done(self, info, error, folder: str) -> bool:
        self._set_busy(False)
        self.progress_panel.finish(error is None, tr("figure_window.figura_armada_y_exportada"))
        if error is not None:
            self.lbl_result.set_text(tr("figure_window.no_se_pudo_armar_error", error=error))
            return False
        self._last_project = info.get("proyecto")
        self.btn_open_main.set_visible(bool(self._last_project))
        self.lbl_result.set_text(summary(info) + ('\n\n' + tr("figure_window.exportado_en_folder", folder=folder)))
        if self.tgl_all.get_active():
            self.tgl_all.set_active(False)       # se muestra la figura armada de verdad
        self._update_color_status()
        try:
            glb = os.path.splitext(info["ensamblada"])[0] + ".glb"      # `Colorizer.write` lo deja junto al STL ensamblado
            if not (os.path.exists(glb) and self._show_glb(glb)):
                import trimesh

                self._tex_parts = None
                self.tgl_tex.set_visible(False)
                self._set_shown(trimesh.load(info["ensamblada"], force="mesh"), None)
        except Exception as exc:                              # noqa: BLE001
            self.lbl_result.set_text(self.lbl_result.get_text() + ('\n' + tr("figure_window.sin_vista_previa_exc", exc=exc)))
        return False

    def _show_glb(self, glb: str) -> bool:
        """Muestra el GLB con color de la figura armada (o recoloreada). `False` si no trae textura: el que llama dibuja el STL."""
        from .render import load_glb_parts

        parts = [p for p in load_glb_parts(glb, y_up=True)]           # el GLB de `Colorizer.write` va en Y arriba
        if not parts or not any(p.image is not None for p in parts):
            return False
        self._set_shown(parts[0].mesh, None)                         # deja `_shown` coherente y luego pasa a la vista con textura
        self._tex_parts = parts
        self.tgl_tex.set_visible(True)
        self._redraw()
        return True
