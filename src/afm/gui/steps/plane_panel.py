"""Lista de planos siempre visible, fija sobre las pestañas de la barra lateral (UX etapa 4b).

Antes estaba dentro de la pestaña «Planos» y «Miembros» obligaba a ir y volver para elegir el lápiz o el pasador de un
plano. Los widgets conservan sus nombres (`win.planes_box`, `win.chk_show`, `win.chk_cut_numbers`).
"""
from __future__ import annotations

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
    sec = _collapsible(panel, "Planos del proyecto")
    win.chk_show: dict[str, Gtk.CheckButton] = {}
    vis = _collapsible(sec, "Qué se muestra", expanded=False, sub=True)
    for group, label, tip in (
            ("planes", "Mostrar todos los planos", "Interruptor general: se combina con el de cada plano de la lista"),
            ("pins", "Mostrar todos los pines de articulación",
             "Marcas de los pasadores (hombro, cadera, cuello) y ejes de las bisagras (rodilla, codo)"),
            ("contours", "Mostrar todos los contornos", "Siluetas de los miembros (1.º y 2.º corte) y recortes manuales")):
        chk = Gtk.CheckButton(label=label, active=False, tooltip_text=tip)
        chk.connect("toggled", lambda b, g=group: None if win._updating else win.ctrl.set_group_visible(g, b.get_active()))
        vis.append(chk)
        win.chk_show[group] = chk
    win.chk_cut_numbers = Gtk.CheckButton(label="Numerar los cortes en el visor", active=win.ctrl.show_cut_numbers)
    win.chk_cut_numbers.connect("toggled", lambda b: None if win._updating else win.ctrl.set_show_cut_numbers(b.get_active()))
    vis.append(win.chk_cut_numbers)
    win.planes_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    win.planes_box.add_css_class("boxed-list")
    win.lbl_planes_empty = Gtk.Label(label="Todavía no hay planos. Agregalos en la pestaña «Planos» (simetría, brazo, pierna).",
                                     wrap=True, xalign=0, css_classes=["afm-help-text"])
    sec.append(win.lbl_planes_empty)                  # `_rebuild_plane_list` lo muestra solo con la lista vacía
    win.planes_scroller = scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                  max_content_height=LIST_MAX_HEIGHT_PX)
    scroller.set_child(win.planes_box)
    sec.append(scroller)
    sec.append(Gtk.Label(label="Durante la selección: Retroceso deshace el último punto, Esc cancela.",
                         xalign=0, wrap=True, css_classes=["afm-help-text"]))
    return panel
