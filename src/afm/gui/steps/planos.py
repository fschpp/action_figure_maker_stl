"""Pestaña «Planos» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations
from ...i18n import tr, loc

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ...head import REPLACEMENT_UPS
from ..controller import GRIP_FIELDS, GRIP_SPIN_MAX
from ..ui_consts import HEAD_ADJUST_DEFAULTS
from ..ui_consts import HEAD_ADJUST_FIELDS
from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help



def _floor_cut_controls(win, sec, place: str, button_label: str, tip: str) -> None:
    """Botón «… paralelo al piso» + «Altura sobre el piso (mm)»: el plano horizontal se traslada en paralelo, como «Corte a»
    traslada el plano de simetría para brazos y piernas. `place`: «head» (cuello) o «waist» (cintura)."""
    btn = win._button(button_label, lambda *_: win.ctrl.start_floor_cut(place))
    btn.set_tooltip_text(tip)
    setattr(win, f"btn_{'neck' if place == 'head' else 'waist'}_floor", btn)
    sec.append(btn)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("planos.altura_sobre_el_piso_mm"), xalign=0, hexpand=True))
    spin = Gtk.SpinButton.new_with_range(0.5, 400.0, 0.5)
    spin.set_digits(2)
    spin.set_tooltip_text(tr("planos.traslada_el_plano_de_corte_en_paralelo"))
    spin.connect("value-changed", lambda b, pl=place: win._on_floor_cut(pl, b))
    row.append(spin)
    sec.append(row)
    win.spin_floor[place] = spin

def build(win) -> None:
    """Agrega la pestaña «Planos» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page(tr("common.planos"))
    win.spin_floor: dict[str, Gtk.SpinButton] = {}      # «Altura sobre el piso» de cada corte paralelo al piso (clave: head / waist)
    sec = _collapsible(box, tr("planos.plano_de_simetria"))
    win.btn_sym = win._button(tr("planos.plano_de_simetria_3_puntos"), lambda *_: win.ctrl.start_symmetry())
    win.btn_sym_auto = win._button(tr("planos.simetria_automatica_x_0"), lambda *_: win.ctrl.auto_symmetry())
    for b in (win.btn_sym, win.btn_sym_auto):
        sec.append(b)

    sec = _collapsible(box, tr("planos.brazos_y_piernas"))
    win.btn_arm = win._button(tr("planos.agregar_plano_de_brazo_3_puntos"), lambda *_: win.ctrl.start_limb_plane("arm"))
    win.btn_leg = win._button(tr("planos.agregar_plano_de_pierna_3_puntos"), lambda *_: win.ctrl.start_limb_plane("leg"))
    for b in (win.btn_arm, win.btn_leg):
        sec.append(b)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("planos.corte_a_mm_del_plano_de"), xalign=0, hexpand=True))
    win.spin_cut = Gtk.SpinButton.new_with_range(0.5, 200.0, 0.5)
    win.spin_cut.set_digits(2)
    win.spin_cut.connect("value-changed", win._on_cut_offset)
    row.append(win.spin_cut)
    sec.append(row)
    # Referencia del corte: a cuántos mm del plano de simetría corta una figura ya cortada del catálogo (p. ej. Güemes)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("planos.referencia_figura_ya_cortada"), xalign=0))
    win.dd_cut_ref = Gtk.DropDown.new_from_strings([tr("planos.sin_catalogo")])
    win.dd_cut_ref.set_hexpand(True)
    win.dd_cut_ref.set_tooltip_text(tr("planos.figura_del_catalogo_cuyo_corte_de"))
    win.dd_cut_ref.connect("notify::selected", win._on_cut_ref_figure)
    row.append(win.dd_cut_ref)
    win.chk_cut_ref = Gtk.CheckButton(label=tr("planos.guia"), active=True, tooltip_text=tr("planos.mostrar_ocultar_el_plano_celeste_de"))
    win.chk_cut_ref.connect("toggled", lambda b: None if win._updating else win.ctrl.toggle_cut_ref())
    row.append(win.chk_cut_ref)
    win.chk_cut_ghost = Gtk.CheckButton(label=tr("planos.fantasma"), active=False,
                                         tooltip_text=tr("planos.dibuja_translucidas_las_piezas_de_la"))
    win.chk_cut_ghost.connect("toggled", lambda b: None if win._updating else win.ctrl.toggle_cut_ghost())
    row.append(win.chk_cut_ghost)
    sec.append(row)
    win.lbl_cut_ref = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_cut_ref)
    win.btn_cut_match = win._button(tr("planos.igualar_a_la_referencia"), lambda *_: win.ctrl.match_cut_to_ref())
    win.btn_cut_match.set_tooltip_text(tr("planos.mueve_el_corte_del_brazo_o"))
    sec.append(win.btn_cut_match)
    win._cut_ref_figs: list[str] = []
    win.chk_cut_aim = Gtk.CheckButton(label=tr("planos.mira_del_corte_cinta_de_color"))
    win.chk_cut_aim.set_tooltip_text(tr("planos.con_un_catalogo_abierto_pinta_la"))
    win.chk_cut_aim.connect("toggled", lambda b: None if win._updating else win.ctrl.toggle_cut_aim())
    sec.append(win.chk_cut_aim)
    win.lbl_cut_aim = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_cut_aim)
    row = Gtk.Box(spacing=6)
    win.btn_cut_snap = win._button(tr("planos.ir_a_la_mejor_posicion_del"), lambda *_: win.ctrl.snap_cut())
    win.btn_cut_snap.set_tooltip_text(tr("planos.el_iman_del_corte_mueve_el"))
    win.btn_cut_seated = win._button(tr("planos.medir_choque_en_pose_sentada"), win._on_cut_seated)
    win._cut_seated_tip = (tr("planos.arma_el_brazo_y_la_pierna"))
    win.btn_cut_seated.set_tooltip_text(win._cut_seated_tip)
    row.append(win.btn_cut_snap)
    row.append(win.btn_cut_seated)
    sec.append(row)
    # Recuadro de la sección: la cara de este corte y, encima, las caras del catálogo con las que se acopla (como el del pasador)
    win.cut_area = Gtk.DrawingArea(content_width=300, content_height=220, visible=False)
    win.cut_area.set_draw_func(win._draw_cut_aim)
    sec.append(win.cut_area)
    sub = _collapsible(sec, tr("planos.rodillas_y_codos"), sub=True)
    win.btn_knee = win._button(tr("planos.agregar_rodilla_a_una_pierna_3"),
                                 lambda *_: win.ctrl.start_joint_plane_for_kind("leg"))
    win.btn_elbow = win._button(tr("planos.agregar_codo_a_un_brazo_3"),
                                  lambda *_: win.ctrl.start_joint_plane_for_kind("arm"))
    win.btn_elbow_lines = win._button(tr("planos.agregar_codo_siguiendo_el_brazo_2"),
                                        lambda *_: win.ctrl.start_joint_lines_for_kind("arm"))
    for b in (win.btn_knee, win.btn_elbow, win.btn_elbow_lines):
        sub.append(b)

    sec = _collapsible(box, tr("common.cintura"))
    win.btn_waist = win._button(tr("planos.agregar_cintura_cortar_el_torso_y"),
                                  lambda *_: win.ctrl.start_waist_plane())
    sec.append(win.btn_waist)
    _floor_cut_controls(win, sec, "waist", tr("planos.agregar_cintura_paralela_al_piso_una"),
                        tr("planos.corta_el_torso_con_un_plano"))
    win.btn_waist_pin = win._button(tr("planos.elegir_donde_va_el_pasador_de"), lambda *_: win.ctrl.start_waist_pin())
    win.btn_waist_pin.set_tooltip_text(tr("planos.con_un_pasador_elegido_el_puerto"))
    win.btn_waist_pin_reset = win._button(tr("planos.pasador_de_la_cintura_volver_a"), lambda *_: win.ctrl.reset_waist_pin())
    win.btn_waist_snap = win._button(tr("planos.iman_del_pasador_m"), lambda *_: win.ctrl.snap_pin())
    win.btn_waist_heat = win._button(tr("planos.mapa_de_calor_del_pasador_h"), lambda *_: win.ctrl.toggle_heat())
    for b in (win.btn_waist_pin, win.btn_waist_pin_reset, win.btn_waist_snap, win.btn_waist_heat):
        sec.append(b)
    win._build_joint_aim(sec, "cintura")

    sec = _collapsible(box, tr("planos.mano_estandar"))
    win.btn_hand = win._button(tr("planos.extraer_mano_estandar_de_esta_figura"),
                                 lambda *_: win.ctrl.start_hand_extract())
    win.btn_hand_invert = win._button(tr("planos.invertir_lado_de_la_mano"), lambda *_: win.ctrl.invert_hand_extract())
    win.btn_hand_save = win._button(tr("planos.guardar_mano_estandar"), win._on_save_hand)
    win.btn_hand_drop = win._button(tr("planos.descartar_la_mano_extraida"), lambda *_: win.ctrl.discard_hand_extract())
    for b in (win.btn_hand, win.btn_hand_invert, win.btn_hand_save, win.btn_hand_drop):
        sec.append(b)
    sec.append(Gtk.Label(label=tr("planos.pegar_la_mano_estandar_en_este"), xalign=0, margin_top=8))
    win.btn_hand_stl = win._button(tr("planos.mano_de_reemplazo_stl_3_puntos"), win._on_hand_replacement)
    win.btn_hand_flip = win._button(tr("planos.invertir_lado_de_la_mano_del"), lambda *_: win.ctrl.invert_hand_place())
    win.btn_hand_orig = win._button(tr("planos.volver_a_la_mano_original"), lambda *_: win.ctrl.clear_hand())
    for b in (win.btn_hand_stl, win.btn_hand_flip):
        sec.append(b)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("common.giro_de_la_mano"), xalign=0, hexpand=True))
    win.spin_hand_yaw = Gtk.SpinButton.new_with_range(-180.0, 180.0, 1.0)
    win.spin_hand_yaw.set_digits(0)
    win.spin_hand_yaw.connect("value-changed", win._on_hand_yaw)
    row.append(win.spin_hand_yaw)
    sec.append(row)
    win.chk_hand_swivel = Gtk.CheckButton(label=tr("common.mano_articulada_pieza_aparte_gira_libre"))
    win.chk_hand_swivel.connect("toggled", lambda b: None if win._updating else win.ctrl.set_hand_swivel(b.get_active()))
    sec.append(win.chk_hand_swivel)
    sec.append(win.btn_hand_orig)
    win.lbl_hand_fit = Gtk.Label(label="", xalign=0, wrap=True)
    sec.append(win.lbl_hand_fit)
    _help(sec, tr("planos.elegi_el_stl_de_la_mano"))

    sec = _collapsible(box, tr("planos.agarre_para_accesorios"), expanded=False)
    win.chk_grip_preview = Gtk.CheckButton(label=tr("planos.previsualizar_el_agarre_sin_taladrar"))
    win.chk_grip_preview.connect("toggled", win._on_grip_preview_toggled)
    sec.append(win.chk_grip_preview)
    _help(sec, tr("planos.agarre_ayuda_manual"))
    win.btn_grip_plane = win._button(tr("planos.agarre_elegir_plano"), win._on_grip_plane)
    win.btn_grip_center = win._button(tr("planos.agarre_elegir_centro"), win._on_grip_center)
    win.btn_grip_end = win._button(tr("planos.agarre_elegir_final"), win._on_grip_end)
    win.btn_grip_end_clear = win._button(tr("planos.agarre_quitar_final"), win._on_grip_end_clear)
    win.btn_grip_clear = win._button(tr("planos.agarre_borrar"), win._on_grip_clear)
    for b in (win.btn_grip_plane, win.btn_grip_center, win.btn_grip_end, win.btn_grip_end_clear, win.btn_grip_clear):
        sec.append(b)
    win.btn_grip = win._button(tr("planos.taladrar_una_mano_estandar_stl"), win._on_grip_pick)
    sec.append(win.btn_grip)
    win.grip_spins: dict[str, Gtk.SpinButton] = {}
    for key, label, lo, hi, step, digits in GRIP_FIELDS:
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        spin = Gtk.SpinButton.new_with_range(lo, GRIP_SPIN_MAX if hi is None else hi, step)
        spin.set_digits(digits)
        spin.set_width_chars(6)
        spin.set_value(getattr(win.ctrl.grip_spec, key))
        spin.connect("value-changed", lambda b, k=key: None if win._updating else win.ctrl.set_grip_param(k, b.get_value()))
        win.grip_spins[key] = spin
        row.append(spin)
        sec.append(row)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("planos.de_los_dentados_a_probar_mm"), xalign=0, hexpand=True))
    win.ent_grip_barbs = Gtk.Entry(text=", ".join(f"{b:g}" for b in win.ctrl.grip_barbs), width_chars=12)
    win.ent_grip_barbs.connect("changed", lambda e: None if win._updating else win.ctrl.set_grip_barbs(e.get_text()))
    row.append(win.ent_grip_barbs)
    sec.append(row)
    win.btn_grip_hand = win._button(tr("planos.guardar_mano_con_agarre"), win._on_grip_save_hand)
    win.btn_grip_catalog = win._button(tr("planos.guardar_la_mano_en_el_catalogo"), win._on_grip_save_catalog)
    win.btn_grip_plate = win._button(tr("planos.guardar_placa_de_espadas_de_prueba"), win._on_grip_save_plate)
    sec.append(win.btn_grip_hand)
    sec.append(win.btn_grip_catalog)
    sec.append(win.btn_grip_plate)
    win.chk_grip_show = Gtk.CheckButton(label=tr("planos.ver_una_espada_de_prueba_en"))
    win.chk_grip_show.connect("toggled", lambda b: None if win._updating else win.ctrl.set_grip_show(b.get_active()))
    sec.append(win.chk_grip_show)
    win.lbl_grip = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_grip)
    _help(sec, tr("planos.para_armas_y_otros_accesorios_que"))

    sec = _collapsible(box, tr("planos.arma_accesorio"), expanded=False)
    win.btn_weapon_open = win._button(tr("planos.importar_un_arma_stl"), win._on_weapon_open)
    sec.append(win.btn_weapon_open)
    win.chk_weapon_hand = Gtk.CheckButton(label=tr("planos.ver_la_mano_estandar_sobre_el_mango"), active=True)
    win.chk_weapon_hand.connect("toggled", lambda b: None if win._updating else win.ctrl.set_weapon_show_hand(b.get_active()))
    sec.append(win.chk_weapon_hand)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("planos.largo_del_arma_mm"), xalign=0, hexpand=True))
    win.spin_weapon_len = Gtk.SpinButton.new_with_range(5.0, 400.0, 0.5)
    win.spin_weapon_len.set_digits(1)
    win.spin_weapon_len.connect("value-changed", lambda b: None if win._updating else win.ctrl.set_weapon_scale(length_mm=b.get_value()))
    row.append(win.spin_weapon_len)
    sec.append(row)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("planos.escala_sobre_las_unidades_del_stl"), xalign=0, hexpand=True))
    win.spin_weapon_scale = Gtk.SpinButton.new_with_range(0.0001, 10000.0, 0.01)
    win.spin_weapon_scale.set_digits(4)
    win.spin_weapon_scale.connect("value-changed", lambda b: None if win._updating else win.ctrl.set_weapon_scale(factor=b.get_value()))
    row.append(win.spin_weapon_scale)
    sec.append(row)
    win.btn_weapon_cut = win._button(tr("planos.elegir_plano_de_corte_del_mango"), lambda *_: win.ctrl.start_weapon_cut_plane())
    win.btn_weapon_flip = win._button(tr("planos.invertir_el_lado_que_se_descarta"), lambda *_: win.ctrl.flip_weapon_cut())
    sec.append(win.btn_weapon_cut)
    sec.append(win.btn_weapon_flip)
    win.chk_weapon_roll = Gtk.CheckButton(label=tr("planos.giro_automatico_sobre_la_empunadura_arma"), active=True)
    win.spin_weapon_roll = Gtk.SpinButton.new_with_range(0.0, 179.0, 5.0)
    win.chk_weapon_roll.connect("toggled", lambda *_: None if win._updating else win._on_weapon_roll())
    win.spin_weapon_roll.connect("value-changed", lambda *_: None if win._updating else win._on_weapon_roll())
    row = Gtk.Box(spacing=6)
    row.append(win.chk_weapon_roll)
    row.append(win.spin_weapon_roll)
    sec.append(row)
    win.ent_weapon_id = Gtk.Entry(placeholder_text=tr("planos.id_en_el_catalogo_p_ej"), hexpand=True)
    sec.append(win.ent_weapon_id)
    win.chk_weapon_ver = Gtk.CheckButton(label=tr("planos.si_el_id_ya_existe_guardar"))
    sec.append(win.chk_weapon_ver)
    win.btn_weapon_save = win._button(tr("planos.guardar_el_arma_en_el_catalogo"), win._on_weapon_save)
    win.btn_weapon_close = win._button(tr("planos.volver_a_la_figura"), lambda *_: win.ctrl.close_weapon())
    sec.append(win.btn_weapon_save)
    sec.append(win.btn_weapon_close)
    win.lbl_weapon = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_weapon)
    _help(sec, tr("planos.importa_cualquier_stl_como_arma_de"))

    sec = _collapsible(box, tr("planos.accesorios_que_viajan_con_la_figura"), expanded=False)
    win.box_proj_acc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    sec.append(win.box_proj_acc)
    win._acc_sig = None
    _help(sec, tr("planos.armas_y_accesorios_del_catalogo_que"))

    sec = _collapsible(box, tr("common.cabeza"))
    win.btn_neck = win._button(tr("planos.agregar_plano_del_cuello_cabeza_3"), lambda *_: win.ctrl.start_neck_plane())
    win.btn_head_stl = win._button(tr("planos.cabeza_de_reemplazo_stl"), win._on_head_replacement)
    win.btn_head_orig = win._button(tr("planos.volver_a_la_cabeza_original"), lambda *_: win.ctrl.clear_head_replacement())
    win.btn_head_cut = win._button(tr("planos.cortar_la_base_de_la_cabeza"), lambda *_: win.ctrl.start_head_cut_plane())
    win.btn_head_uncut = win._button(tr("planos.quitar_el_corte_de_la_base"), lambda *_: win.ctrl.clear_head_cut_plane())
    win.btn_head_pin = win._button(tr("planos.elegir_donde_va_el_pasador_del"), lambda *_: win.ctrl.start_head_pin())
    win.btn_head_pin_reset = win._button(tr("planos.pasador_del_cuello_volver_a_la"), lambda *_: win.ctrl.reset_head_pin())
    for b in (win.btn_neck, win.btn_head_stl, win.btn_head_orig, win.btn_head_cut, win.btn_head_uncut,
              win.btn_head_pin, win.btn_head_pin_reset):
        sec.append(b)
    _floor_cut_controls(win, sec, "head", tr("planos.agregar_corte_de_la_cabeza_paralelo_al"),
                        tr("planos.corta_la_cabeza_con_un_plano"))
    win.btn_head_snap = win._button(tr("planos.iman_del_pasador_m"), lambda *_: win.ctrl.snap_pin())
    win.btn_head_heat = win._button(tr("planos.mapa_de_calor_del_pasador_h"), lambda *_: win.ctrl.toggle_heat())
    sec.append(win.btn_head_snap)
    sec.append(win.btn_head_heat)
    win._build_joint_aim(sec, "cuello")
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label=tr("planos.cabeza_de_reemplazo_eje_arriba_del"), xalign=0, hexpand=True))
    win.dd_head_up = Gtk.DropDown.new_from_strings(list(REPLACEMENT_UPS))
    win.dd_head_up.connect("notify::selected", win._on_head_up)
    row.append(win.dd_head_up)
    sec.append(row)
    # (clave de HeadSpec/set_head_adjust, rótulo, mínimo, máximo, paso, decimales)
    win.head_spins: dict[str, Gtk.SpinButton] = {}
    for key, label, lo, hi, step, digits in HEAD_ADJUST_FIELDS:
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=loc(label), xalign=0, hexpand=True))
        spin = Gtk.SpinButton.new_with_range(lo, hi, step)
        spin.set_digits(digits)
        spin.set_value(HEAD_ADJUST_DEFAULTS[key])
        spin.connect("value-changed", lambda b, k=key: win._on_head_adjust(k, b))
        win.head_spins[key] = spin
        row.append(spin)
        sec.append(row)
    win.spin_head_yaw = win.head_spins["yaw"]
    win.lbl_head_fit = Gtk.Label(label="", xalign=0, wrap=True)
    sec.append(win.lbl_head_fit)
    _help(sec, tr("planos.pasador_del_cuello_elegir_donde_va"))
    _help(sec, tr("planos.la_cabeza_de_reemplazo_se_ve"))

    # --------------------------------------------------------- Catálogo
