"""Lista de planos siempre visible, fija sobre las pestañas de la barra lateral (UX etapa 4b).

Antes estaba dentro de la pestaña «Planos» y «Miembros» obligaba a ir y volver para elegir el lápiz o el pasador de un
plano. Los widgets conservan sus nombres (`win.planes_box`, `win.chk_show`, `win.chk_cut_numbers`).
"""
from __future__ import annotations
from ...i18n import tr

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..widgets import collapsible as _collapsible

LIST_MAX_HEIGHT_PX = 170      # alto máximo de la lista antes de que aparezca su propio scroll


def build(win) -> Gtk.Widget:
    """Arma el panel «Planos» (visibilidad + lista) y lo devuelve; la ventana lo pone encima del `Gtk.Notebook`."""
    panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    for side in ("start", "end", "top"):
        getattr(panel, f"set_margin_{side}")(10)
    sec = _collapsible(panel, tr("plane_panel.planos_del_proyecto"))
    win.chk_show: dict[str, Gtk.CheckButton] = {}
    vis = _collapsible(sec, tr("plane_panel.que_se_muestra"), expanded=False, sub=True)
    for group, label, tip in (
            ("planes", tr("plane_panel.mostrar_todos_los_planos"), tr("plane_panel.interruptor_general_se_combina_con_el")),
            ("pins", tr("plane_panel.mostrar_todos_los_pasadores_de_articulacio"),
             tr("plane_panel.marcas_de_los_pasadores_hombro_cadera")),
            ("contours", tr("plane_panel.mostrar_todos_los_contornos"), tr("plane_panel.siluetas_de_los_miembros_1_o"))):
        chk = Gtk.CheckButton(label=label, active=False, tooltip_text=tip)
        chk.connect("toggled", lambda b, g=group: None if win._updating else win.ctrl.set_group_visible(g, b.get_active()))
        vis.append(chk)
        win.chk_show[group] = chk
    win.chk_cut_numbers = Gtk.CheckButton(label=tr("plane_panel.numerar_los_cortes_en_el_visor"), active=win.ctrl.show_cut_numbers)
    win.chk_cut_numbers.connect("toggled", lambda b: None if win._updating else win.ctrl.set_show_cut_numbers(b.get_active()))
    vis.append(win.chk_cut_numbers)
    win.planes_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    win.planes_box.add_css_class("boxed-list")
    win.lbl_planes_empty = Gtk.Label(label=tr("plane_panel.todavia_no_hay_planos_agregalos_en"),
                                     wrap=True, xalign=0, css_classes=["afm-help-text"])
    sec.append(win.lbl_planes_empty)                  # `_rebuild_plane_list` lo muestra solo con la lista vacía
    win.planes_scroller = scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                  max_content_height=LIST_MAX_HEIGHT_PX)
    scroller.set_child(win.planes_box)
    sec.append(scroller)
    sec.append(Gtk.Label(label=tr("plane_panel.durante_la_seleccion_retroceso_deshace_el"),
                         xalign=0, wrap=True, css_classes=["afm-help-text"]))
    return panel
