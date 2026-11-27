"""Ventana flotante de herramientas del modo «Retocar color…», al estilo de Paintbrush de Windows.

Herramientas con ícono a la izquierda (pintar, borrar, rellenar, polígono entero, deshacer, rehacer), grosores del pincel como líneas
de distinto espesor y la paleta de la línea como una grilla de muestras de color abajo. Lo poco usado (ver resultado, descartar,
exportar / importar / traer de otra pieza, espejar) queda en el menú «Más». Aplicar y Cancelar siempre a la vista.

Esta clase solo arma widgets y mantiene sincronizado lo visual (muestra elegida, grosor marcado); la lógica del retoque sigue en
`CatalogManagerWindow`, que conecta las señales de los widgets públicos. Los widgets `tgl_*`, `spin_*`, `dd_role` y `dd_source` son los
mismos de siempre: `dd_role` ya no se muestra, pero guarda el rol elegido (las muestras lo mueven y él mueve las muestras).
"""
from __future__ import annotations

from ..i18n import tr

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from .widgets import no_scroll_change  # noqa: E402

BRUSH_PRESETS_MM = (0.25, 0.75, 1.5, 3.0, 6.0)      # radios de los 5 grosores (la línea dibujada crece con el radio)
LINE_PX = (1, 2, 4, 6, 9)
SWATCH_PX = 24
PALETTE_COLUMNS = 8
_CSS = f"""
button.rt-tool {{ padding: 4px 6px; min-width: 80px; }}
button.rt-tool label {{ font-size: 0.8em; }}
button.rt-swatch {{ padding: 0; min-width: {SWATCH_PX}px; min-height: {SWATCH_PX}px; border-radius: 0; background-image: none; border: 1px solid alpha(black, 0.45); box-shadow: none; }}
button.rt-swatch.rt-selected {{ border: 2px solid @accent_bg_color; outline: 2px solid alpha(white, 0.9); outline-offset: -4px; }}
.rt-current {{ min-width: 44px; min-height: 44px; border: 1px solid alpha(black, 0.6); }}
.rt-line {{ background-color: currentColor; border-radius: 2px; }}
button.rt-size {{ padding: 3px 6px; }}
"""
_css_loaded = False


def _load_css() -> None:
    global _css_loaded
    if _css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    prov = Gtk.CssProvider()
    prov.load_from_data(_CSS.encode())
    Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


def _icon_name(*names: str) -> str | None:
    """El primer ícono de `names` que el tema tiene (o `None`: el botón queda solo con texto)."""
    display = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
    if theme is None:
        return None
    return next((n for n in names if theme.has_icon(n)), None)


def _tool_content(icons: tuple[str, ...], text: str) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, halign=Gtk.Align.CENTER)
    name = _icon_name(*icons)
    if name:
        box.append(Gtk.Image.new_from_icon_name(name))
    lab = Gtk.Label(label=text, justify=Gtk.Justification.CENTER, wrap=True, width_chars=9, max_width_chars=12)
    lab.set_wrap_mode(Pango.WrapMode.WORD)
    box.append(lab)
    return box


def _style_tool(btn: Gtk.Button, icons: tuple[str, ...], text: str, tip: str) -> None:
    btn.set_child(_tool_content(icons, text))
    btn.add_css_class("rt-tool")
    if tip:
        btn.set_tooltip_text(tip)


def _tint(widget: Gtk.Widget, hex_color: str, name: str) -> None:
    """Pinta `widget` con `hex_color` mediante una regla CSS propia (`name` único)."""
    prov = Gtk.CssProvider()
    prov.load_from_data(f".{name} {{ background-color: {hex_color}; background-image: none; }}".encode())
    widget.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    widget.add_css_class(name)


class RetouchToolbox(Gtk.Window):
    """Paleta de herramientas flotante (no modal) del retoque de color."""

    def __init__(self, parent: Gtk.Window | None = None):
        super().__init__(title=tr("catalog_window.retocar_herramientas"), transient_for=parent, resizable=False, hide_on_close=True,
                         destroy_with_parent=True)
        _load_css()
        self._swatches: list[Gtk.Button] = []
        self._names: list[tuple[str, str, str]] = []
        self._syncing = False
        self.on_role_picked = None             # callback(índice) cuando el usuario toca una muestra
        self._build()

    # ------------------------------------------------------------------ armado
    def _build(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        self.set_child(root)

        # --- herramientas (2 columnas) + grosores
        top = Gtk.Box(spacing=8)
        grid = Gtk.Grid(row_spacing=4, column_spacing=4, valign=Gtk.Align.START)
        self.tgl_paint = Gtk.ToggleButton()
        self.tgl_erase = Gtk.ToggleButton(group=self.tgl_paint)
        self.tgl_fill = Gtk.ToggleButton(group=self.tgl_paint)
        self.tgl_poly = Gtk.ToggleButton()
        self.btn_undo = Gtk.Button()
        self.btn_redo = Gtk.Button()
        self.tgl_line = Gtk.ToggleButton(group=self.tgl_paint)           # formas: se arrastran sobre la pieza; borde y relleno llevan el color elegido
        self.tgl_circle = Gtk.ToggleButton(group=self.tgl_paint)
        self.tgl_square = Gtk.ToggleButton(group=self.tgl_paint)
        _style_tool(self.tgl_paint, ("document-edit-symbolic", "applications-graphics-symbolic"), tr("catalog_window.retocar_pintar"),
                    tr("catalog_window.retocar_pintar_tip"))
        _style_tool(self.tgl_erase, ("edit-clear-all-symbolic", "edit-clear-symbolic", "edit-delete-symbolic"), tr("catalog_window.retocar_borrar"),
                    tr("catalog_window.retocar_borrar_tip"))
        _style_tool(self.tgl_fill, ("color-select-symbolic", "color-fill-symbolic", "format-fill-symbolic"), tr("catalog_window.retocar_rellenar"),
                    tr("catalog_window.retocar_rellenar_tip"))
        _style_tool(self.tgl_poly, ("view-grid-symbolic", "draw-polygon-symbolic"), tr("catalog_window.retocar_poligono"),
                    tr("catalog_window.retocar_poligono_tip"))
        _style_tool(self.btn_undo, ("edit-undo-symbolic",), tr("catalog_window.retocar_deshacer"), tr("catalog_window.retocar_deshacer_tip"))
        _style_tool(self.btn_redo, ("edit-redo-symbolic",), tr("catalog_window.retocar_rehacer"), tr("catalog_window.retocar_rehacer_tip"))
        _style_tool(self.tgl_line, ("draw-line-symbolic", "list-remove-symbolic"), tr("catalog_window.retocar_linea"), tr("catalog_window.retocar_linea_tip"))
        _style_tool(self.tgl_circle, ("media-record-symbolic", "draw-ellipse-symbolic"), tr("catalog_window.retocar_circulo"),
                    tr("catalog_window.retocar_circulo_tip"))
        _style_tool(self.tgl_square, ("media-playback-stop-symbolic", "draw-rectangle-symbolic"), tr("catalog_window.retocar_cuadrado"),
                    tr("catalog_window.retocar_cuadrado_tip"))
        for i, b in enumerate((self.tgl_paint, self.tgl_erase, self.tgl_fill, self.tgl_poly, self.tgl_line, self.tgl_circle, self.tgl_square,
                               self.btn_undo, self.btn_redo)):
            grid.attach(b, i % 2, i // 2, 1, 1)
        top.append(grid)

        # grosores: 5 líneas de espesor creciente (como el selector de líneas de Paintbrush) + radio exacto
        size = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.START)
        self.size_box = size
        self.tgl_sizes: list[Gtk.ToggleButton] = []
        for mm, px in zip(BRUSH_PRESETS_MM, LINE_PX):
            b = Gtk.ToggleButton(group=self.tgl_sizes[0] if self.tgl_sizes else None)
            b.add_css_class("rt-size")
            line = Gtk.Box(hexpand=True, valign=Gtk.Align.CENTER)
            line.set_size_request(56, px)
            line.add_css_class("rt-line")
            b.set_child(line)
            b.set_tooltip_text(tr("catalog_window.retocar_grosor_tip", mm=f"{mm:g}"))
            b.connect("toggled", self._on_size_preset, mm)
            self.tgl_sizes.append(b)
            size.append(b)
        self.spin_radius = Gtk.SpinButton.new_with_range(0.05, 15.0, 0.25)
        self.spin_radius.set_digits(2)
        self.spin_radius.set_tooltip_text(tr("catalog_window.retocar_radio_tip"))
        self.spin_radius.connect("value-changed", lambda *_: self.sync_size_presets())
        no_scroll_change(self.spin_radius)
        size.append(Gtk.Label(label=tr("catalog_window.retocar_radio"), xalign=0))
        size.append(self.spin_radius)
        # alcance de «Rellenar»: solo se muestra con esa herramienta
        self.reach_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, visible=False)
        self.spin_reach = Gtk.SpinButton.new_with_range(0.05, 15.0, 0.5)
        self.spin_reach.set_digits(1)
        self.spin_reach.set_tooltip_text(tr("catalog_window.retocar_alcance_tip"))
        no_scroll_change(self.spin_reach)
        self.reach_box.append(Gtk.Label(label=tr("catalog_window.retocar_alcance"), xalign=0))
        self.reach_box.append(self.spin_reach)
        size.append(self.reach_box)
        top.append(size)
        root.append(top)
        self.tgl_fill.connect("toggled", lambda *_: self.reach_box.set_visible(self.tgl_fill.get_active()))
        self.tgl_poly.connect("toggled", lambda *_: self._sync_size_enabled())
        self.tgl_circle.connect("toggled", lambda *_: self._sync_size_enabled())

        # --- color actual + paleta
        cur = Gtk.Box(spacing=8)
        self.current = Gtk.Box()
        self.current.add_css_class("rt-current")
        cur.append(self.current)
        self.lbl_role = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.lbl_role.add_css_class("heading")
        cur.append(self.lbl_role)
        root.append(cur)
        self.palette_grid = Gtk.Grid(row_spacing=2, column_spacing=2, halign=Gtk.Align.START)
        root.append(self.palette_grid)
        self.dd_role = Gtk.DropDown.new_from_strings([tr("common.ninguna")])           # no se muestra: guarda el rol elegido
        self.dd_role.connect("notify::selected", lambda *_: self._sync_swatches())

        # --- estado
        self.lbl_count = Gtk.Label(xalign=0, wrap=True)
        self.lbl_count.add_css_class("dim-label")
        self.lbl_count.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        root.append(self.lbl_count)

        # --- menú «Más» + Cancelar / Aplicar
        bar = Gtk.Box(spacing=6)
        self.btn_more = Gtk.MenuButton(label=tr("catalog_window.retocar_mas"))
        self.btn_more.set_popover(self._build_more())
        bar.append(self.btn_more)
        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)
        self.btn_cancel = Gtk.Button(label=tr("catalog_window.cancelar_esc"))
        self.btn_apply = Gtk.Button(label=tr("catalog_window.retocar_aplicar"))
        self.btn_apply.add_css_class("suggested-action")
        bar.append(self.btn_cancel)
        bar.append(self.btn_apply)
        root.append(bar)

    def _build_more(self) -> Gtk.Popover:
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        self.btn_view = Gtk.Button(label=tr("catalog_window.retocar_ver_resultado"))
        self.btn_view.set_tooltip_text(tr("catalog_window.retocar_ver_resultado_tip"))
        self.btn_discard = Gtk.Button(label=tr("catalog_window.retocar_descartar"))
        self.btn_discard.set_tooltip_text(tr("catalog_window.retocar_descartar_tip"))
        self.btn_export = Gtk.Button(label=tr("catalog_window.retocar_exportar"))
        self.btn_export.set_tooltip_text(tr("catalog_window.retocar_exportar_tip"))
        self.btn_import = Gtk.Button(label=tr("catalog_window.retocar_importar"))
        self.btn_import.set_tooltip_text(tr("catalog_window.retocar_importar_tip"))
        self.btn_replace = Gtk.Button(label=tr("catalog_window.reemplazar_boton"))
        self.btn_replace.set_tooltip_text(tr("catalog_window.reemplazar_boton_tip"))
        for b in (self.btn_replace, self.btn_view, self.btn_discard, self.btn_export, self.btn_import):
            b.set_halign(Gtk.Align.FILL)
            box.append(b)
        box.append(Gtk.Separator())
        self.dd_source = Gtk.DropDown.new_from_strings([tr("common.ninguna")])
        self.dd_source.set_tooltip_text(tr("catalog_window.retocar_traer_tip"))
        self.btn_bring = Gtk.Button(label=tr("catalog_window.retocar_traer_boton"))
        self.btn_bring.set_tooltip_text(tr("catalog_window.retocar_traer_tip"))
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=tr("catalog_window.retocar_traer")))
        row.append(self.dd_source)
        row.append(self.btn_bring)
        box.append(row)
        self.chk_mirror = Gtk.CheckButton(label=tr("catalog_window.retocar_espejar"))
        self.chk_mirror.set_tooltip_text(tr("catalog_window.retocar_espejar_tip"))
        box.append(self.chk_mirror)
        pop.set_child(box)
        return pop

    # ------------------------------------------------------------------ paleta
    def set_palette(self, roles: list[tuple[str, str, str]]) -> None:
        """Arma la grilla con `roles` = [(id, nombre, '#RRGGBB'), …] y el `dd_role` oculto con los ids."""
        for b in self._swatches:
            self.palette_grid.remove(b)
        self._swatches = []
        self._names = [(rid, name, hx) for rid, name, hx in roles]
        for i, (rid, name, hx) in enumerate(self._names):
            b = Gtk.Button()
            b.add_css_class("rt-swatch")
            _tint(b, hx, f"rt-sw-{i}")
            b.set_tooltip_text(name if name == rid else f"{name} ({rid})")
            b.connect("clicked", self._on_swatch, i)
            self.palette_grid.attach(b, i % PALETTE_COLUMNS, i // PALETTE_COLUMNS, 1, 1)
            self._swatches.append(b)
        self._syncing = True
        try:
            self.dd_role.set_model(Gtk.StringList.new([r[0] for r in self._names] or [tr("common.ninguna")]))
            self.dd_role.set_selected(0)
        finally:
            self._syncing = False
        self._sync_swatches()

    def _on_swatch(self, _btn, i: int) -> None:
        self.dd_role.set_selected(i)               # lo que ya escuchaba la ventana (`_on_retouch_role`)
        if self.on_role_picked is not None:
            self.on_role_picked(i)

    def _sync_swatches(self) -> None:
        sel = self.dd_role.get_selected()
        for i, b in enumerate(self._swatches):
            (b.add_css_class if i == sel else b.remove_css_class)("rt-selected")
        if 0 <= sel < len(self._names):
            rid, name, hx = self._names[sel]
            for c in [c for c in self.current.get_css_classes() if c.startswith("rt-cur-")]:
                self.current.remove_css_class(c)
            _tint(self.current, hx, f"rt-cur-{sel}")
            self.lbl_role.set_text(name)

    # ------------------------------------------------------------------ grosores
    def _on_size_preset(self, btn: Gtk.ToggleButton, mm: float) -> None:
        if self._syncing or not btn.get_active():
            return
        self.spin_radius.set_value(mm)

    def sync_size_presets(self) -> None:
        """Marca el grosor que coincide con el radio actual (ninguno si es un valor intermedio)."""
        v = self.spin_radius.get_value()
        self._syncing = True
        try:
            hit = next((b for b, mm in zip(self.tgl_sizes, BRUSH_PRESETS_MM) if abs(mm - v) < 1e-6), None)
            for b in self.tgl_sizes:
                b.set_active(b is hit)
        finally:
            self._syncing = False

    def _sync_size_enabled(self) -> None:
        # con el polígono entero el radio no cuenta (y con una forma el polígono tampoco); el círculo fija su radio arrastrando
        self.size_box.set_sensitive(not self.tgl_poly.get_active() and not self.tgl_circle.get_active())
