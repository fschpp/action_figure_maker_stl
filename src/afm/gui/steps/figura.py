"""Pestaña «Figura» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Figura» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page("Figura")
    sec = _collapsible(box, "Figura")
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Frente hacia", xalign=0, hexpand=True))
    win.dd_front = Gtk.DropDown.new_from_strings(["+Y", "-Y"])
    win.dd_front.connect("notify::selected", win._on_front)
    row.append(win.dd_front)
    sec.append(row)
    win.btn_crown = win._button("Marcar coronilla (clic)", lambda *_: win.ctrl.start_crown())
    sec.append(win.btn_crown)
    win.chk_side = Gtk.CheckButton(label="Trabajar un solo lado (oculta x < 0)")
    win.chk_side.connect("toggled", lambda b: None if win._updating else win.ctrl.set_one_side(b.get_active()))
    sec.append(win.chk_side)
    sub = _collapsible(sec, "Voladizos de impresión", expanded=False, sub=True)
    win.chk_over = Gtk.CheckButton(label="Mostrar voladizos de impresión (rojo)")
    win.chk_over.connect("toggled", lambda b: None if win._updating else win.ctrl.set_show_overhangs(b.get_active()))
    sub.append(win.chk_over)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Ángulo máximo desde la vertical (°)", xalign=0, hexpand=True))
    win.spin_over = Gtk.SpinButton.new_with_range(10, 80, 5)
    win.spin_over.set_value(win.ctrl.overhang_angle)
    win.spin_over.connect("value-changed", lambda b: None if win._updating else win.ctrl.set_overhang_angle(b.get_value()))
    row.append(win.spin_over)
    sub.append(row)

    sec = _collapsible(box, "Espejo y pose sentada")
    win.chk_mirror = Gtk.CheckButton(label="Espejo en vivo (trabaja un solo lado)")
    win.chk_mirror.connect("toggled", lambda b: None if win._updating else win.ctrl.set_mirror_live(b.get_active()))
    sec.append(win.chk_mirror)
    _help(sec, "Muestra, en el lado oculto (x < 0), el reflejo de lo que hacés en el lado +X: la figura, las siluetas "
               "(también la que estás dibujando), los pasadores y los recortes. Es lo que sale al generar: un lado y su espejo.")
    win.chk_pose = Gtk.CheckButton(label="Ver la figura generada en la pose de cadera")
    win.chk_pose.connect("toggled", lambda b: None if win._updating else win.ctrl.set_pose_view(b.get_active()))
    sec.append(win.chk_pose)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Ángulo de cadera (°)", xalign=0, hexpand=True))
    win.spin_pose = Gtk.SpinButton.new_with_range(0, 180, 5)
    win.spin_pose.connect("value-changed", lambda b: None if win._updating else win.ctrl.set_pose_angle(b.get_value()))
    row.append(win.spin_pose)
    sec.append(row)
    _help(sec, "Hace falta haber generado la figura. Las piernas giran sobre el pasador de cadera (0° = de pie, 90° = "
               "sentada) y las piezas que chocan con el torso o entre sí se pintan de rojo; el resultado, con el límite de "
               "cadera tallado, queda en el Estado.")

    # ------------------------------------------------------------ Planos
