"""Pestaña «Figura» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations
from ...i18n import tr

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Figura» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page(tr("common.figura"))
    sec = _collapsible(box, tr("common.figura"))
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("figura.frente_hacia"), xalign=0, hexpand=True))
    win.dd_front = Gtk.DropDown.new_from_strings(["+Y", "-Y"])
    win.dd_front.connect("notify::selected", win._on_front)
    row.append(win.dd_front)
    sec.append(row)
    win.btn_crown = win._button(tr("figura.marcar_coronilla_clic"), lambda *_: win.ctrl.start_crown())
    sec.append(win.btn_crown)
    win.chk_side = Gtk.CheckButton(label=tr("figura.trabajar_un_solo_lado_oculta_x"))
    win.chk_side.connect("toggled", lambda b: None if win._updating else win.ctrl.set_one_side(b.get_active()))
    sec.append(win.chk_side)
    sub = _collapsible(sec, tr("figura.voladizos_de_impresion"), expanded=False, sub=True)
    win.chk_over = Gtk.CheckButton(label=tr("figura.mostrar_voladizos_de_impresion_rojo"))
    win.chk_over.connect("toggled", lambda b: None if win._updating else win.ctrl.set_show_overhangs(b.get_active()))
    sub.append(win.chk_over)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("figura.angulo_maximo_desde_la_vertical"), xalign=0, hexpand=True))
    win.spin_over = Gtk.SpinButton.new_with_range(10, 80, 5)
    win.spin_over.set_value(win.ctrl.overhang_angle)
    win.spin_over.connect("value-changed", lambda b: None if win._updating else win.ctrl.set_overhang_angle(b.get_value()))
    row.append(win.spin_over)
    sub.append(row)

    sec = _collapsible(box, tr("figura.espejo_y_pose_sentada"))
    win.chk_mirror = Gtk.CheckButton(label=tr("figura.espejo_en_vivo_trabaja_un_solo"))
    win.chk_mirror.connect("toggled", lambda b: None if win._updating else win.ctrl.set_mirror_live(b.get_active()))
    sec.append(win.chk_mirror)
    _help(sec, tr("figura.muestra_en_el_lado_oculto_x"))
    win.chk_pose = Gtk.CheckButton(label=tr("figura.ver_la_figura_generada_en_la"))
    win.chk_pose.connect("toggled", lambda b: None if win._updating else win.ctrl.set_pose_view(b.get_active()))
    sec.append(win.chk_pose)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("figura.angulo_de_cadera"), xalign=0, hexpand=True))
    win.spin_pose = Gtk.SpinButton.new_with_range(0, 180, 5)
    win.spin_pose.connect("value-changed", lambda b: None if win._updating else win.ctrl.set_pose_angle(b.get_value()))
    row.append(win.spin_pose)
    sec.append(row)
    _help(sec, tr("figura.hace_falta_haber_generado_la_figura"))

    # ------------------------------------------------------------ Planos
