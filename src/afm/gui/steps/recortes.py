"""Pestaña «Recortes» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations
from ...i18n import tr, loc

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
    box = win._tab_page(tr("common.recortes"))
    sec = _collapsible(box, tr("recortes.recortes_manuales"))
    _help(sec, tr("recortes.para_los_ajustes_propios_de_cada"))
    for text, attr, items in ((tr("recortes.pieza"), "dd_trim_target", ["torso"]),
                              (tr("recortes.forma"), "dd_trim_shape", [loc(TRIM_SHAPE_LABELS[k]).capitalize() for k in TRIM_SHAPES]),
                              (tr("recortes.operacion"), "dd_trim_op", [loc(TRIM_OP_LABELS[k]).capitalize() for k in _TRIM_OPS])):
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=text, xalign=0, hexpand=True))
        dd = Gtk.DropDown.new_from_strings(items)
        setattr(win, attr, dd)
        row.append(dd)
        sec.append(row)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("recortes.extrusion_mm"), xalign=0, hexpand=True))
    win.spin_trim_len = Gtk.SpinButton.new_with_range(0.1, 500.0, 1.0)
    win.spin_trim_len.set_value(25.0)
    row.append(win.spin_trim_len)
    sec.append(row)
    win.row_trim_size = Gtk.Box(spacing=6)
    win.lbl_trim_size = Gtk.Label(label=tr("common.radio_de_la_esfera_mm"), xalign=0, hexpand=True)
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
    win.btn_trim_add = win._button(tr("recortes.dibujar_poligono_colocar_con_clic"), lambda *_: win._on_trim_add())
    sec.append(win.btn_trim_add)
    sec = _collapsible(box, tr("recortes.recortes_de_la_figura"))
    win.trims_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    win.trims_box.add_css_class("boxed-list")
    sec.append(win.trims_box)

    # ------------------------------------------------------- Parámetros
