"""Pestaña «Recortes» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..controller import TRIM_OP_LABELS
from ..controller import TRIM_SHAPES
from ..controller import TRIM_SHAPE_LABELS
from ..controller import trim_size_label
from ..ui_consts import TRIM_OPS as _TRIM_OPS
from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Recortes» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page("Recortes")
    sec = _collapsible(box, "Recortes manuales")
    _help(sec, "Para los ajustes propios de cada figura (p. ej. la «esfera panza»): se dibuja un polígono sobre el plano de "
               "simetría (visto de costado) o se coloca una esfera / caja / cilindro con un clic en la figura; se extruye y "
               "se resta o se une a una pieza. Se hace sobre el lado +X y se espeja. Tiene que llegar a la superficie: un "
               "hueco cerrado por dentro se descarta.")
    for text, attr, items in (("Pieza", "dd_trim_target", ["torso"]),
                              ("Forma", "dd_trim_shape", [TRIM_SHAPE_LABELS[k].capitalize() for k in TRIM_SHAPES]),
                              ("Operación", "dd_trim_op", [TRIM_OP_LABELS[k].capitalize() for k in _TRIM_OPS])):
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=text, xalign=0, hexpand=True))
        dd = Gtk.DropDown.new_from_strings(items)
        setattr(win, attr, dd)
        row.append(dd)
        sec.append(row)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Extrusión (mm)", xalign=0, hexpand=True))
    win.spin_trim_len = Gtk.SpinButton.new_with_range(0.1, 500.0, 1.0)
    win.spin_trim_len.set_value(25.0)
    row.append(win.spin_trim_len)
    sec.append(row)
    win.row_trim_size = Gtk.Box(spacing=6)
    win.lbl_trim_size = Gtk.Label(label="Radio de la esfera (mm)", xalign=0, hexpand=True)
    win.row_trim_size.append(win.lbl_trim_size)
    win.spin_trim_size = Gtk.SpinButton.new_with_range(0.5, 100.0, 0.5)
    win.spin_trim_size.set_value(5.0)
    win.row_trim_size.append(win.spin_trim_size)
    sec.append(win.row_trim_size)

    def shape_changed(*_) -> None:                  # el tamaño solo aplica a esfera / caja / cilindro (R10)
        label = trim_size_label(TRIM_SHAPES[win.dd_trim_shape.get_selected()])
        win.row_trim_size.set_visible(label is not None)
        if label is not None:
            win.lbl_trim_size.set_text(label)

    win.dd_trim_shape.connect("notify::selected", shape_changed)
    shape_changed()
    win.btn_trim_add = win._button("Dibujar polígono / colocar con clic", lambda *_: win._on_trim_add())
    sec.append(win.btn_trim_add)
    sec = _collapsible(box, "Recortes de la figura")
    win.trims_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    win.trims_box.add_css_class("boxed-list")
    sec.append(win.trims_box)

    # ------------------------------------------------------- Parámetros
