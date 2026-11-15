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

    win.sidebar_box = win.tabs                  # lo que se desactiva mientras se genera
