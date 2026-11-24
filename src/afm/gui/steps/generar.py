"""Pestaña «Generar» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations
from ...i18n import tr

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..controller import PARAM_FIELDS
from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Generar» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page(tr("common.generar"))
    sec = _collapsible(box, tr("common.generar"))
    win.btn_generate = win._button(tr("generar.generar_figura"), lambda *_: win._on_generate())
    win.btn_generate.add_css_class("suggested-action")
    sec.append(win.btn_generate)
    _help(sec, tr("generar.generar_puede_tardar_varios_minutos_si"))

    sec = _collapsible(box, tr("generar.opciones_de_exportacion"))
    win.chk_orient = Gtk.CheckButton(label=tr("generar.apoyar_las_piezas_en_la_cama"), active=True)
    sec.append(win.chk_orient)
    sub = _collapsible(sec, tr("generar.impresion_sin_soportes"), sub=True)
    win.chk_flat = Gtk.CheckButton(label=tr("generar.modificar_para_imprimir_sin_soportes_aplan"))
    win.chk_flat.set_tooltip_text(tr("generar.corta_con_un_plano_horizontal_sobre"))
    win.chk_flat.connect("toggled", lambda b: None if win._updating else win._on_flat_toggled(b.get_active()))
    sub.append(win.chk_flat)
    spec = next(f for f in PARAM_FIELDS if f[0] == "flat_depth")
    sub.append(win._param_row(*spec))               # la altura del corte vive acá, junto a la casilla que la usa
    win.chk_fill_hip = Gtk.CheckButton(label=tr("generar.rellenar_los_muslos_hasta_la_pelvis"))
    win.chk_fill_hip.set_tooltip_text(tr("generar.el_corte_plano_le_saca_a"))
    win.chk_fill_hip.connect("toggled", lambda b: None if win._updating else win._on_fill_hip_toggled(b))
    sub.append(win.chk_fill_hip)
    win.btn_flat_plane = win._button(tr("generar.ajustar_la_altura_del_corte_vista"), lambda *_: win._on_flat_plane())
    win.btn_flat_plane.set_tooltip_text(tr("generar.muestra_la_figura_generada_sentada_de"))
    sub.append(win.btn_flat_plane)
    win.btn_flat_click = win._button(tr("generar.ajustar_el_clic_del_brazo_para"),
                                       lambda *_: win._on_flat_click())
    win.btn_flat_click.set_tooltip_text(tr("generar.el_brazo_con_clic_no_se"))
    sub.append(win.btn_flat_click)
    _help(sec, tr("generar.estas_opciones_se_aplican_al_exportar"))
    _build_bambu(win, sec)

    win.sidebar_box = win.tabs                  # lo que se desactiva mientras se genera


def _build_bambu(win, parent) -> None:
    """«Salida para Bambu Studio»: además de los STL, `<nombre>_bambu.3mf` y `.obj` con UN color plano por triángulo (sin textura). Sin catálogo: los
    colores salen de las piezas que dejó el último «Exportar» (con color). Los manejadores viven en `window.py` (`_on_bambu_*`)."""
    sub = _collapsible(parent, tr("generar.bambu_titulo"), sub=True)
    win.chk_bambu = Gtk.CheckButton(label=tr("generar.bambu_activar"))
    win.chk_bambu.set_tooltip_text(tr("generar.bambu_activar_tip"))
    win.chk_bambu.connect("toggled", lambda b: None if win._updating else win._on_bambu_settings())
    sub.append(win.chk_bambu)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("generar.bambu_colores"), xalign=0, hexpand=True, wrap=True))
    win.sp_bambu_n = Gtk.SpinButton.new_with_range(1, 16, 1)
    win.sp_bambu_n.set_tooltip_text(tr("generar.bambu_colores_tip"))
    win.sp_bambu_n.connect("value-changed", lambda b: None if win._updating else win._on_bambu_settings())
    row.append(win.sp_bambu_n)
    sub.append(row)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("generar.bambu_min"), xalign=0, hexpand=True, wrap=True))
    win.sp_bambu_min = Gtk.SpinButton.new_with_range(0.0, 20.0, 0.1)
    win.sp_bambu_min.set_digits(1)
    win.sp_bambu_min.set_tooltip_text(tr("generar.bambu_min_tip"))
    win.sp_bambu_min.connect("value-changed", lambda b: None if win._updating else win._on_bambu_settings())
    row.append(win.sp_bambu_min)
    sub.append(row)
    _help(sub, tr("generar.bambu_nota"))
    title = Gtk.Label(label=tr("generar.bambu_sel_titulo"), xalign=0, margin_top=6)
    title.add_css_class("heading")
    sub.append(title)
    _help(sub, tr("generar.bambu_sel_ayuda"))
    win.box_bambu_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    sub.append(win.box_bambu_rows)
    win.lbl_bambu_info = Gtk.Label(xalign=0, wrap=True)
    win.lbl_bambu_info.add_css_class("dim-label")
    sub.append(win.lbl_bambu_info)
    win.chk_bambu_view = Gtk.CheckButton(label=tr("generar.bambu_vista"))
    win.chk_bambu_view.set_tooltip_text(tr("generar.bambu_vista_tip"))
    win.chk_bambu_view.connect("toggled", lambda b: None if win._updating else win._on_bambu_view(b.get_active()))
    sub.append(win.chk_bambu_view)
    btns = Gtk.Box(spacing=6)
    win.btn_bambu_auto = win._button(tr("generar.bambu_sel_auto"), lambda *_: win._on_bambu_auto())
    win.btn_bambu_auto.set_tooltip_text(tr("generar.bambu_sel_auto_tip"))
    btns.append(win.btn_bambu_auto)
    win.btn_bambu_export = win._button(tr("generar.bambu_exportar_ahora"), lambda *_: win._on_bambu_export())
    win.btn_bambu_export.set_tooltip_text(tr("generar.bambu_exportar_ahora_tip"))
    btns.append(win.btn_bambu_export)
    sub.append(btns)
    win._bambu_w = {}                           # {HEX: {chk, btn, lbl, reset}} de las filas del selector (ver `window._refresh_bambu`)
    win._bambu_sig = None
    win._bambu_timer = 0                        # retardo de GLib: agrupa varios clics seguidos en el selector antes de recalcular la vista
