"""Pestaña «Parámetros» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations
from ...i18n import tr, loc

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .. import param_meta as PM
from ..controller import Controller, JOINT_PREVIEW_FLEX_MAX, JOINT_PREVIEW_FLEX_MIN, PARAM_FIELDS
from ..param_diagram_view import ParamDiagramView
from ..session import Session
from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Parámetros» a `win.tabs` (win = la `MainWindow`)."""
    win.param_size_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
    win.param_diagram = ParamDiagramView(win.ctrl.get_param)             # cabecera fija con el dibujo acotado del parámetro activo
    box = win._tab_page(tr("common.parametros"), header=win.param_diagram)
    sec = _collapsible(box, tr("parametros.altura_de_la_figura"))
    row = Gtk.Box(spacing=6)
    lbl = Gtk.Label(label=tr("parametros.pies_coronilla_mm"), xalign=0, hexpand=True)
    lbl.set_tooltip_text(tr("parametros.reescala_la_figura_y_los_planos"))
    row.append(lbl)
    lo, hi = Session.REF_HEIGHT_RANGE
    win.spin_height = Gtk.SpinButton.new_with_range(lo, hi, 1.0)
    win.spin_height.set_digits(1)
    win._shown_height = win.ctrl.session.project.params.ref_height
    win.spin_height.set_value(win._shown_height)
    win.spin_height.connect("activate", lambda *_: win._on_apply_height())
    row.append(win.spin_height)
    row.append(win._button(tr("common.aplicar"), lambda *_: win._on_apply_height()))
    sec.append(row)

    sec = _collapsible(box, tr("parametros.perfil_de_linea"))
    _help(sec, tr("parametros.un_perfil_guarda_estos_parametros_para"))
    row = Gtk.Box(spacing=6)
    win.dd_profile = Gtk.DropDown.new_from_strings(win.ctrl.profile_names() or [tr("parametros.sin_perfiles")])
    win.dd_profile.set_hexpand(True)
    row.append(win.dd_profile)
    row.append(win._button(tr("parametros.cargar"), lambda *_: win._on_load_profile()))
    sec.append(row)
    row = Gtk.Box(spacing=6)
    win.ent_profile = Gtk.Entry(placeholder_text=tr("parametros.nombre_del_perfil"), hexpand=True)
    row.append(win.ent_profile)
    row.append(win._button(tr("common.guardar"), lambda *_: win._on_save_profile()))
    sec.append(row)

    sec = _collapsible(box, tr("parametros.parametros_de_articulacion"))
    bar = Gtk.Box(spacing=6)
    win.ent_param_search = Gtk.SearchEntry(placeholder_text=tr("parametros.buscar_parametro"), hexpand=True)
    win.ent_param_search.connect("search-changed", lambda e: win._on_param_search(e.get_text()))
    bar.append(win.ent_param_search)
    win.btn_param_reset_all = win._button(tr("parametros.restaurar_todos"), lambda *_: win.ctrl.reset_params())
    bar.append(win.btn_param_reset_all)
    sec.append(bar)
    win.lbl_param_modified = Gtk.Label(label="", xalign=0)
    win.lbl_param_modified.add_css_class("dim-label")
    sec.append(win.lbl_param_modified)
    win.param_group_exp: dict[str, tuple[Gtk.Expander, bool]] = {}
    groups = {}
    for name in PM.PARAM_GROUPS:
        expanded = name == PM.PARAM_GROUPS[0]
        groups[name] = _collapsible(sec, loc(name), expanded=expanded, sub=True, css_class="afm-param-group")
        win.param_group_exp[name] = (groups[name].get_parent(), expanded)
    win.param_spins: dict[str, object] = {}
    win.param_rows: dict[str, dict] = {}
    for key, label, lo, hi, step, digits in PARAM_FIELDS:
        if PM.group_of(key) in groups:                  # «Sin soportes» vive en la pestaña Generar
            groups[PM.group_of(key)].append(win._param_row(key, label, lo, hi, step, digits))
    win.lbl_param_none = Gtk.Label(label=tr("parametros.ningun_parametro_coincide_con_la_busqueda"), xalign=0, visible=False)
    sec.append(win.lbl_param_none)

    sec = _collapsible(box, tr("common.color"), expanded=False)
    _help(sec, tr("color.panel_ayuda"))
    win.lbl_color = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_color)
    row = Gtk.Box(spacing=6)
    win.btn_color_pick = win._button(tr("color.panel_elegir_fbx"), lambda *_: win._on_color_pick())
    win.btn_color_relocate = win._button(tr("color.panel_relocalizar"), lambda *_: win._on_color_relocate())
    win.btn_color_remove = win._button(tr("color.panel_quitar"), lambda *_: win.ctrl.remove_color())
    for b in (win.btn_color_pick, win.btn_color_relocate, win.btn_color_remove):
        row.append(b)
    sec.append(row)
    win.chk_color_tex = Gtk.CheckButton(label=tr("color.panel_ver_textura"))
    win.chk_color_tex.set_tooltip_text(tr("color.panel_ver_textura_tooltip"))
    win.chk_color_tex.connect("toggled", lambda b: None if win._updating else win.ctrl.set_show_texture(b.get_active()))
    sec.append(win.chk_color_tex)
    win.spin_color_texel = win._color_spin(sec, tr("color.panel_texel"), Controller.COLOR_TEXEL_RANGE, 0.05, 2, "texel_mm")
    win.spin_color_eps = win._color_spin(sec, tr("color.panel_tolerancia"), Controller.COLOR_EPS_RANGE, 0.01, 2, "eps")
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("color.panel_relleno"), xalign=0, hexpand=True, wrap=True))
    win.dd_color_fill = Gtk.DropDown.new_from_strings([tr("color.panel_relleno_extender"), tr("color.panel_relleno_plano")])
    win.dd_color_fill.connect("notify::selected", lambda *_: win._on_color_fill())
    row.append(win.dd_color_fill)
    win.ent_color_fill = Gtk.Entry(text="#C8C8C8", width_chars=9, max_width_chars=9)
    win.ent_color_fill.connect("activate", lambda *_: win._on_color_fill())
    row.append(win.ent_color_fill)
    sec.append(row)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("color.panel_lado"), xalign=0, hexpand=True, wrap=True))
    win.dd_color_side = Gtk.DropDown.new_from_strings([tr("color.panel_lado_original"), tr("color.panel_lado_simetrico")])
    win.dd_color_side.connect("notify::selected", lambda *_: None if win._updating else win.ctrl.update_color(symmetric=win.dd_color_side.get_selected() == 1))
    row.append(win.dd_color_side)
    sec.append(row)
    sec.append(Gtk.Label(label=tr("color.panel_tabla"), xalign=0, wrap=True))
    win.lbl_color_report = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    win.lbl_color_report.add_css_class("monospace")
    sec.append(win.lbl_color_report)

    sec = _collapsible(box, tr("parametros.vista_previa_de_la_articulacion"), expanded=False)
    _help(sec, tr("parametros.arma_solo_la_rodilla_el_codo"))
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("common.articulacion"), xalign=0, hexpand=True))
    win.dd_jprev = Gtk.DropDown.new_from_strings([tr("common.ninguna")])
    win.dd_jprev.connect("notify::selected", win._on_jprev_choice)
    row.append(win.dd_jprev)
    sec.append(row)
    win.chk_jprev = Gtk.CheckButton(label=tr("parametros.ver_solo_la_articulacion_sin_generar"))
    win.chk_jprev.connect("toggled", lambda b: None if win._updating else win.ctrl.set_joint_preview(win._jprev_name(), b.get_active()))
    sec.append(win.chk_jprev)
    win.spin_jh = win._joint_spin(sec, tr("common.altura_del_eje_sobre_el_plano"), 0.5, 15.0, 0.1, 2,
                                    lambda n, v: win.ctrl.set_joint_height(n, v))
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("parametros.inclinacion_del_plano_de_costado"), xalign=0, hexpand=True, wrap=True,
                         tooltip_text=tr("parametros.angulo_del_plano_visto_de_costado")))
    win.spin_jtilt = Gtk.SpinButton.new_with_range(-60.0, 60.0, 1.0)
    win.spin_jtilt.set_digits(1)
    win.spin_jtilt.connect("value-changed", lambda b: None if win._updating or win._jprev_name() is None
                            else win.ctrl.set_joint_plane_side_tilt(win._jprev_name(), b.get_value()))
    row.append(win.spin_jtilt)
    win.btn_jstraight = win._button(tr("parametros.enderezar"), lambda *_: win._jprev_name() is not None
                                      and win.ctrl.set_joint_plane_side_tilt(win._jprev_name(), 0.0))
    row.append(win.btn_jstraight)
    sec.append(row)
    win.spin_jflex = win._joint_spin(sec, tr("common.flexion_mostrada"), JOINT_PREVIEW_FLEX_MIN, JOINT_PREVIEW_FLEX_MAX, 5.0, 0,
                                       lambda n, v: win.ctrl.set_joint_preview_flex(v))
    win.lbl_jprev = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_jprev)

    sec = _collapsible(box, tr("parametros.probetas_de_articulacion"), expanded=False)
    win.dd_probe = Gtk.DropDown.new_from_strings([tr("common.pasador_de_cadera"), tr("common.pasador_de_hombro"), tr("parametros.bisagra_de_rodilla_codo")])
    sec.append(win.dd_probe)
    _help(sec, tr("parametros.probetas_ayuda"))
    sec.append(win._button(tr("parametros.generar_probetas_de_articulacion"), lambda *_: win._on_probes()))
    # tabla número -> valores y paso de retroalimentación: elegir el número ganador y pasarlo a los parámetros
    win.lbl_probe_table = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    win.lbl_probe_table.add_css_class("monospace")
    sec.append(win.lbl_probe_table)
    sec.append(Gtk.Label(label=tr("parametros.probetas_resultado"), xalign=0, wrap=True))
    row = Gtk.Box(spacing=6)
    win.dd_probe_cell = Gtk.DropDown.new_from_strings([tr("parametros.sin_tirada_de_probetas")])
    win.dd_probe_cell.set_hexpand(True)
    row.append(win.dd_probe_cell)
    win.btn_probe_use = win._button(tr("parametros.usar_esta_celda"), lambda *_: win._on_probe_use())
    win.btn_probe_use.set_sensitive(False)
    row.append(win.btn_probe_use)
    sec.append(row)
    sec.append(win._button(tr("parametros.cargar_tirada_anterior"), lambda *_: win._on_probe_load()))

    # ---------------------------------------------------------- Generar
