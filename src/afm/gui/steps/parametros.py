"""Pestaña «Parámetros» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .. import param_meta as PM
from ..controller import PARAM_FIELDS
from ..session import Session
from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Parámetros» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page("Parámetros")
    sec = _collapsible(box, "Altura de la figura")
    row = Gtk.Box(spacing=6)
    lbl = Gtk.Label(label="Pies → coronilla (mm)", xalign=0, hexpand=True)
    lbl.set_tooltip_text("Reescala la figura (y los planos, siluetas y recortes ya hechos) a esta altura. "
                         "Los pasadores, la holgura y el clic son medidas absolutas y no se escalan.")
    row.append(lbl)
    lo, hi = Session.REF_HEIGHT_RANGE
    win.spin_height = Gtk.SpinButton.new_with_range(lo, hi, 1.0)
    win.spin_height.set_digits(1)
    win._shown_height = win.ctrl.session.project.params.ref_height
    win.spin_height.set_value(win._shown_height)
    win.spin_height.connect("activate", lambda *_: win._on_apply_height())
    row.append(win.spin_height)
    row.append(win._button("Aplicar", lambda *_: win._on_apply_height()))
    sec.append(row)

    sec = _collapsible(box, "Parámetros de articulación")
    bar = Gtk.Box(spacing=6)
    win.ent_param_search = Gtk.SearchEntry(placeholder_text="Buscar parámetro…", hexpand=True)
    win.ent_param_search.connect("search-changed", lambda e: win._on_param_search(e.get_text()))
    bar.append(win.ent_param_search)
    win.btn_param_reset_all = win._button("Restaurar todos", lambda *_: win.ctrl.reset_params())
    bar.append(win.btn_param_reset_all)
    sec.append(bar)
    win.lbl_param_modified = Gtk.Label(label="", xalign=0)
    win.lbl_param_modified.add_css_class("dim-label")
    sec.append(win.lbl_param_modified)
    win.param_group_exp: dict[str, tuple[Gtk.Expander, bool]] = {}
    groups = {}
    for name in PM.PARAM_GROUPS:
        expanded = name == PM.PARAM_GROUPS[0]
        groups[name] = _collapsible(sec, name, expanded=expanded, sub=True)
        win.param_group_exp[name] = (groups[name].get_parent(), expanded)
    win.param_spins: dict[str, object] = {}
    win.param_rows: dict[str, dict] = {}
    for key, label, lo, hi, step, digits in PARAM_FIELDS:
        if PM.group_of(key) in groups:                  # «Sin soportes» vive en la pestaña Generar
            groups[PM.group_of(key)].append(win._param_row(key, label, lo, hi, step, digits))
    win.lbl_param_none = Gtk.Label(label="Ningún parámetro coincide con la búsqueda.", xalign=0, visible=False)
    sec.append(win.lbl_param_none)

    sec = _collapsible(box, "Vista previa de la articulación", expanded=False)
    _help(sec, "Arma SOLO la rodilla, el codo o la cadera (recortada alrededor del eje) para ver cuánto material se lleva el hueco "
               "mientras movés el eje, el plano o el rango, sin esperar a «Generar figura». Se recalcula sola (unos "
               "segundos) y muestra el último resultado hasta que llega el nuevo. Con la vista previa encendida se oculta "
               "la figura. En una figura armada desde el catálogo se ve la articulación de la pieza tal como se catalogó "
               "(el eje y el plano no se mueven acá). La cadera muestra el pasador y el hueco del barrido en la pelvis: «Flexión "
               "mostrada» gira el muslo (0° de pie, hasta la pose sentada).")
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Articulación", xalign=0, hexpand=True))
    win.dd_jprev = Gtk.DropDown.new_from_strings(["(ninguna)"])
    win.dd_jprev.connect("notify::selected", win._on_jprev_choice)
    row.append(win.dd_jprev)
    sec.append(row)
    win.chk_jprev = Gtk.CheckButton(label="Ver solo la articulación (sin generar la figura)")
    win.chk_jprev.connect("toggled", lambda b: None if win._updating else win.ctrl.set_joint_preview(win._jprev_name(), b.get_active()))
    sec.append(win.chk_jprev)
    win.spin_jh = win._joint_spin(sec, "Altura del eje sobre el plano (mm)", 0.5, 15.0, 0.1, 2,
                                    lambda n, v: win.ctrl.set_joint_height(n, v))
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Inclinación del plano de costado (°)", xalign=0, hexpand=True, wrap=True,
                         tooltip_text="Ángulo del plano visto de costado (+ = sube hacia +Y). 0 = plano recto: el eje no se "
                                      "mueve y conserva su altura; lo que se corre es el plano."))
    win.spin_jtilt = Gtk.SpinButton.new_with_range(-60.0, 60.0, 1.0)
    win.spin_jtilt.set_digits(1)
    win.spin_jtilt.connect("value-changed", lambda b: None if win._updating or win._jprev_name() is None
                            else win.ctrl.set_joint_plane_side_tilt(win._jprev_name(), b.get_value()))
    row.append(win.spin_jtilt)
    win.btn_jstraight = win._button("Enderezar", lambda *_: win._jprev_name() is not None
                                      and win.ctrl.set_joint_plane_side_tilt(win._jprev_name(), 0.0))
    row.append(win.btn_jstraight)
    sec.append(row)
    win.spin_jflex = win._joint_spin(sec, "Flexión mostrada (°)", -45.0, 150.0, 5.0, 0,
                                       lambda n, v: win.ctrl.set_joint_preview_flex(v))
    win.lbl_jprev = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_jprev)

    sec = _collapsible(box, "Perfil de línea", expanded=False)
    _help(sec, "Un perfil guarda estos parámetros para reusarlos en la próxima figura de la línea.")
    row = Gtk.Box(spacing=6)
    win.dd_profile = Gtk.DropDown.new_from_strings(win.ctrl.profile_names() or ["(sin perfiles)"])
    win.dd_profile.set_hexpand(True)
    row.append(win.dd_profile)
    row.append(win._button("Cargar", lambda *_: win._on_load_profile()))
    sec.append(row)
    row = Gtk.Box(spacing=6)
    win.ent_profile = Gtk.Entry(placeholder_text="Nombre del perfil", hexpand=True)
    row.append(win.ent_profile)
    row.append(win._button("Guardar", lambda *_: win._on_save_profile()))
    sec.append(row)

    sec = _collapsible(box, "Probetas de articulación", expanded=False)
    win.dd_probe = Gtk.DropDown.new_from_strings(["Pasador de cadera", "Pasador de hombro", "Bisagra de rodilla / codo"])
    sec.append(win.dd_probe)
    sec.append(win._button("Generar probetas de articulación…", lambda *_: win._on_probes()))

    # ---------------------------------------------------------- Generar
