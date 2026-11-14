"""Pestaña «Planos» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ...head import REPLACEMENT_UPS
from ..controller import GRIP_FIELDS
from ..ui_consts import HEAD_ADJUST_DEFAULTS
from ..ui_consts import HEAD_ADJUST_FIELDS
from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Planos» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page("Planos")
    sec = _collapsible(box, "Lista de planos")
    _help(sec, "Durante la selección: Retroceso deshace el último punto, Esc cancela.")
    win.chk_show: dict[str, Gtk.CheckButton] = {}
    for group, label, tip in (
            ("planes", "Mostrar todos los planos", "Interruptor general: se combina con el de cada plano de la lista"),
            ("pins", "Mostrar todos los pines de articulación",
             "Marcas de los pasadores (hombro, cadera, cuello) y ejes de las bisagras (rodilla, codo)"),
            ("contours", "Mostrar todos los contornos", "Siluetas de los miembros (1.º y 2.º corte) y recortes manuales")):
        chk = Gtk.CheckButton(label=label, active=False, tooltip_text=tip)
        chk.connect("toggled", lambda b, g=group: None if win._updating else win.ctrl.set_group_visible(g, b.get_active()))
        sec.append(chk)
        win.chk_show[group] = chk
    win.planes_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    win.planes_box.add_css_class("boxed-list")
    sec.append(win.planes_box)
    win.chk_cut_numbers = Gtk.CheckButton(label="Numerar los cortes en el visor", active=win.ctrl.show_cut_numbers)
    win.chk_cut_numbers.connect("toggled", lambda b: None if win._updating else win.ctrl.set_show_cut_numbers(b.get_active()))
    sec.append(win.chk_cut_numbers)

    sec = _collapsible(box, "Plano de simetría")
    win.btn_sym = win._button("Plano de simetría (3 puntos)", lambda *_: win.ctrl.start_symmetry())
    win.btn_sym_auto = win._button("Simetría automática (x = 0)", lambda *_: win.ctrl.auto_symmetry())
    for b in (win.btn_sym, win.btn_sym_auto):
        sec.append(b)

    sec = _collapsible(box, "Brazos y piernas")
    win.btn_arm = win._button("Agregar plano de brazo (3 puntos)", lambda *_: win.ctrl.start_limb_plane("arm"))
    win.btn_leg = win._button("Agregar plano de pierna (3 puntos)", lambda *_: win.ctrl.start_limb_plane("leg"))
    for b in (win.btn_arm, win.btn_leg):
        sec.append(b)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Corte a (mm del plano de simetría)", xalign=0, hexpand=True))
    win.spin_cut = Gtk.SpinButton.new_with_range(0.5, 200.0, 0.5)
    win.spin_cut.set_digits(2)
    win.spin_cut.connect("value-changed", win._on_cut_offset)
    row.append(win.spin_cut)
    sec.append(row)
    # Referencia del corte: a cuántos mm del plano de simetría corta una figura ya cortada del catálogo (p. ej. Güemes)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Referencia (figura ya cortada)", xalign=0))
    win.dd_cut_ref = Gtk.DropDown.new_from_strings(["(sin catálogo)"])
    win.dd_cut_ref.set_hexpand(True)
    win.dd_cut_ref.set_tooltip_text("Figura del catálogo cuyo corte de brazo y pierna se toma de referencia: abajo dice a cuántos mm del plano \n"
                                     "de simetría corta, y en el visor una guía celeste marca esa distancia.")
    win.dd_cut_ref.connect("notify::selected", win._on_cut_ref_figure)
    row.append(win.dd_cut_ref)
    win.chk_cut_ref = Gtk.CheckButton(label="guía", active=True, tooltip_text="Mostrar / ocultar el plano celeste de la referencia en el visor")
    win.chk_cut_ref.connect("toggled", lambda b: None if win._updating else win.ctrl.toggle_cut_ref())
    row.append(win.chk_cut_ref)
    win.chk_cut_ghost = Gtk.CheckButton(label="fantasma", active=False,
                                         tooltip_text="Dibuja translúcidas las piezas de la figura de referencia (torso o pelvis y el miembro) sobre la tuya, "
                                                      "con el corte en su lugar. No se escala: usa los mm de la referencia.")
    win.chk_cut_ghost.connect("toggled", lambda b: None if win._updating else win.ctrl.toggle_cut_ghost())
    row.append(win.chk_cut_ghost)
    sec.append(row)
    win.lbl_cut_ref = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_cut_ref)
    win.btn_cut_match = win._button("Igualar a la referencia", lambda *_: win.ctrl.match_cut_to_ref())
    win.btn_cut_match.set_tooltip_text("Mueve el corte del brazo o la pierna activos a la distancia a la que corta la figura de referencia. "
                                        "Es el mismo movimiento que «Corte a»: después hay que revisar el pasador.")
    sec.append(win.btn_cut_match)
    win._cut_ref_figs: list[str] = []
    win.chk_cut_aim = Gtk.CheckButton(label="Mira del corte (cinta de color y sección)")
    win.chk_cut_aim.set_tooltip_text("Con un catálogo abierto: pinta la sección del corte con el color de cuánto del catálogo lo acepta "
                                      "(verde = casi todo, amarillo = una parte, rojo = nada) y deja una cinta de puntos del mismo color "
                                      "a lo largo de la distancia al plano de simetría, ±3 mm cada 0,25 mm; el punto blanco es la mejor "
                                      "posición de la cinta. Es informativo (no mira el choque en pose sentada).")
    win.chk_cut_aim.connect("toggled", lambda b: None if win._updating else win.ctrl.toggle_cut_aim())
    sec.append(win.chk_cut_aim)
    win.lbl_cut_aim = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_cut_aim)
    row = Gtk.Box(spacing=6)
    win.btn_cut_snap = win._button("Ir a la mejor posición del corte (M)", lambda *_: win.ctrl.snap_cut())
    win.btn_cut_snap.set_tooltip_text("El imán del corte: mueve el plano a la mejor posición de la cinta (±3 mm). Solo cambia la distancia "
                                       "al plano de simetría; después hay que revisar el pasador.")
    win.btn_cut_seated = win._button("Medir choque en pose sentada", win._on_cut_seated)
    win._cut_seated_tip = ("Arma el brazo y la pierna de esta figura sobre cada torso del catálogo, sienta la pierna y mide si "
                            "chocan. Tarda (carga las mallas): se mide solo en la posición elegida. Si choca, aparece un punto "
                            "violeta en la cinta.")
    win.btn_cut_seated.set_tooltip_text(win._cut_seated_tip)
    row.append(win.btn_cut_snap)
    row.append(win.btn_cut_seated)
    sec.append(row)
    # Recuadro de la sección: la cara de este corte y, encima, las caras del catálogo con las que se acopla (como el del pasador)
    win.cut_area = Gtk.DrawingArea(content_width=300, content_height=220, visible=False)
    win.cut_area.set_draw_func(win._draw_cut_aim)
    sec.append(win.cut_area)
    sub = _collapsible(sec, "Rodillas y codos", sub=True)
    win.btn_knee = win._button("Agregar rodilla a una pierna (3 puntos)",
                                 lambda *_: win.ctrl.start_joint_plane_for_kind("leg"))
    win.btn_elbow = win._button("Agregar codo a un brazo (3 puntos)",
                                  lambda *_: win.ctrl.start_joint_plane_for_kind("arm"))
    win.btn_elbow_lines = win._button("Agregar codo siguiendo el brazo (2 líneas, de frente)",
                                        lambda *_: win.ctrl.start_joint_lines_for_kind("arm"))
    for b in (win.btn_knee, win.btn_elbow, win.btn_elbow_lines):
        sub.append(b)

    sec = _collapsible(box, "Cintura")
    win.btn_waist = win._button("Agregar cintura: cortar el torso y girar a los costados (3 puntos)",
                                  lambda *_: win.ctrl.start_waist_plane())
    sec.append(win.btn_waist)
    win.btn_waist_pin = win._button("Elegir dónde va el pasador de la cintura (vista de costado)", lambda *_: win.ctrl.start_waist_pin())
    win.btn_waist_pin.set_tooltip_text("Con un pasador elegido, el puerto «cintura» que se cataloga va en él (como el cuello y los miembros); "
                                        "sin elegir, queda en el centro de la cara, como siempre. Clic = mover; flechas = 0,1 mm; M = imán; H = mapa de calor.")
    win.btn_waist_pin_reset = win._button("Pasador de la cintura: volver a la posición automática", lambda *_: win.ctrl.reset_waist_pin())
    win.btn_waist_snap = win._button("Imán del pasador (M)", lambda *_: win.ctrl.snap_pin())
    win.btn_waist_heat = win._button("Mapa de calor del pasador (H)", lambda *_: win.ctrl.toggle_heat())
    for b in (win.btn_waist_pin, win.btn_waist_pin_reset, win.btn_waist_snap, win.btn_waist_heat):
        sec.append(b)
    win._build_joint_aim(sec, "cintura")

    sec = _collapsible(box, "Mano estándar")
    win.btn_hand = win._button("Extraer mano estándar de esta figura (3 puntos en la muñeca izquierda)",
                                 lambda *_: win.ctrl.start_hand_extract())
    win.btn_hand_invert = win._button("Invertir lado de la mano", lambda *_: win.ctrl.invert_hand_extract())
    win.btn_hand_save = win._button("Guardar mano estándar…", win._on_save_hand)
    win.btn_hand_drop = win._button("Descartar la mano extraída", lambda *_: win.ctrl.discard_hand_extract())
    for b in (win.btn_hand, win.btn_hand_invert, win.btn_hand_save, win.btn_hand_drop):
        sec.append(b)
    sec.append(Gtk.Label(label="Pegar la mano estándar en este modelo", xalign=0, margin_top=8))
    win.btn_hand_stl = win._button("Mano de reemplazo (STL)… (3 puntos en la muñeca)", win._on_hand_replacement)
    win.btn_hand_flip = win._button("Invertir lado de la mano del modelo", lambda *_: win.ctrl.invert_hand_place())
    win.btn_hand_orig = win._button("Volver a la mano original", lambda *_: win.ctrl.clear_hand())
    for b in (win.btn_hand_stl, win.btn_hand_flip):
        sec.append(b)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Giro de la mano (°)", xalign=0, hexpand=True))
    win.spin_hand_yaw = Gtk.SpinButton.new_with_range(-180.0, 180.0, 1.0)
    win.spin_hand_yaw.set_digits(0)
    win.spin_hand_yaw.connect("value-changed", win._on_hand_yaw)
    row.append(win.spin_hand_yaw)
    sec.append(row)
    win.chk_hand_swivel = Gtk.CheckButton(label="Mano articulada (pieza aparte, gira libre dentro del brazo)")
    win.chk_hand_swivel.connect("toggled", lambda b: None if win._updating else win.ctrl.set_hand_swivel(b.get_active()))
    sec.append(win.chk_hand_swivel)
    sec.append(win.btn_hand_orig)
    win.lbl_hand_fit = Gtk.Label(label="", xalign=0, wrap=True)
    sec.append(win.lbl_hand_fit)
    _help(sec, "Elegí el STL de la mano estándar y marcá 3 puntos en la muñeca de este modelo (cualquiera de los dos brazos). "
               "El punto 1 va en el mismo lugar que al extraer la mano (p. ej. el lado del pulgar): así queda orientada sin "
               "tocar el giro. La mano original del modelo se descarta y la estándar se pega sin escalarla (se ve en turquesa); "
               "el otro brazo sale por espejo. La silueta del brazo tiene que incluir la mano nueva: «Generar figura» avisa si no. "
               "«Mano articulada»: en vez de soldarla, la mano sale como pieza aparte con un pasador que entra en el brazo y "
               "gira libre (se achica solo si la muñeca es fina; ver Parámetros → Mano articulada).")

    sec = _collapsible(box, "Agarre para accesorios", expanded=False)
    win.btn_grip = win._button("Taladrar una mano estándar (STL)…", win._on_grip_pick)
    sec.append(win.btn_grip)
    win.grip_spins: dict[str, Gtk.SpinButton] = {}
    for key, label, lo, hi, step, digits in GRIP_FIELDS:
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        spin = Gtk.SpinButton.new_with_range(lo, hi, step)
        spin.set_digits(digits)
        spin.set_value(getattr(win.ctrl.grip_spec, key))
        spin.connect("value-changed", lambda b, k=key: None if win._updating else win.ctrl.set_grip_param(k, b.get_value()))
        win.grip_spins[key] = spin
        row.append(spin)
        sec.append(row)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Ø de los dentados a probar (mm)", xalign=0, hexpand=True))
    win.ent_grip_barbs = Gtk.Entry(text=", ".join(f"{b:g}" for b in win.ctrl.grip_barbs), width_chars=12)
    win.ent_grip_barbs.connect("changed", lambda e: None if win._updating else win.ctrl.set_grip_barbs(e.get_text()))
    row.append(win.ent_grip_barbs)
    sec.append(row)
    win.btn_grip_hand = win._button("Guardar mano con agarre…", win._on_grip_save_hand)
    win.btn_grip_plate = win._button("Guardar placa de espadas de prueba…", win._on_grip_save_plate)
    sec.append(win.btn_grip_hand)
    sec.append(win.btn_grip_plate)
    win.chk_grip_show = Gtk.CheckButton(label="Ver una espada de prueba en la mano pegada de la figura")
    win.chk_grip_show.connect("toggled", lambda b: None if win._updating else win.ctrl.set_grip_show(b.get_active()))
    sec.append(win.chk_grip_show)
    win.lbl_grip = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_grip)
    _help(sec, "Para armas y otros accesorios que se sujetan en el puño sin pegamento. Elegí el STL de la mano estándar: el "
               "programa busca el canal que dejan los dedos con la palma y lo taladra al Ø pedido (con la mano de Güemes, 2,4 mm "
               "es casi el máximo; más abre el anillo). Guardá la mano con agarre y pegala con «Mano de reemplazo (STL)…». "
               "La placa de pruebas trae una espada por cada Ø de dentado, acostadas (las lengüetas flexionan en el plano "
               "de la cama): imprimila, de preferencia en PETG, y fijate cuál sujeta sin romper la lengüeta. El taladro se "
               "hace una vez sobre la mano estándar: toda la línea de figuras la comparte.")

    sec = _collapsible(box, "Arma (accesorio)", expanded=False)
    win.btn_weapon_open = win._button("Importar un arma (STL)…", win._on_weapon_open)
    sec.append(win.btn_weapon_open)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Largo del arma (mm)", xalign=0, hexpand=True))
    win.spin_weapon_len = Gtk.SpinButton.new_with_range(5.0, 400.0, 0.5)
    win.spin_weapon_len.set_digits(1)
    win.spin_weapon_len.connect("value-changed", lambda b: None if win._updating else win.ctrl.set_weapon_scale(length_mm=b.get_value()))
    row.append(win.spin_weapon_len)
    sec.append(row)
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Escala sobre las unidades del STL", xalign=0, hexpand=True))
    win.spin_weapon_scale = Gtk.SpinButton.new_with_range(0.0001, 10000.0, 0.01)
    win.spin_weapon_scale.set_digits(4)
    win.spin_weapon_scale.connect("value-changed", lambda b: None if win._updating else win.ctrl.set_weapon_scale(factor=b.get_value()))
    row.append(win.spin_weapon_scale)
    sec.append(row)
    win.btn_weapon_cut = win._button("Elegir plano de corte del mango (3 clics)", lambda *_: win.ctrl.start_weapon_cut_plane())
    win.btn_weapon_flip = win._button("Invertir el lado que se descarta", lambda *_: win.ctrl.flip_weapon_cut())
    sec.append(win.btn_weapon_cut)
    sec.append(win.btn_weapon_flip)
    win.chk_weapon_roll = Gtk.CheckButton(label="Giro automático sobre la empuñadura (arma acostada)", active=True)
    win.spin_weapon_roll = Gtk.SpinButton.new_with_range(0.0, 179.0, 5.0)
    win.chk_weapon_roll.connect("toggled", lambda *_: None if win._updating else win._on_weapon_roll())
    win.spin_weapon_roll.connect("value-changed", lambda *_: None if win._updating else win._on_weapon_roll())
    row = Gtk.Box(spacing=6)
    row.append(win.chk_weapon_roll)
    row.append(win.spin_weapon_roll)
    sec.append(row)
    win.ent_weapon_id = Gtk.Entry(placeholder_text="id en el catálogo (p. ej. excalibur-01)", hexpand=True)
    sec.append(win.ent_weapon_id)
    win.chk_weapon_ver = Gtk.CheckButton(label="Si el id ya existe, guardar una versión nueva")
    sec.append(win.chk_weapon_ver)
    win.btn_weapon_save = win._button("Guardar el arma en el catálogo", win._on_weapon_save)
    win.btn_weapon_close = win._button("Volver a la figura", lambda *_: win.ctrl.close_weapon())
    sec.append(win.btn_weapon_save)
    sec.append(win.btn_weapon_close)
    win.lbl_weapon = Gtk.Label(label="", xalign=0, wrap=True, selectable=True)
    sec.append(win.lbl_weapon)
    _help(sec, "Importa cualquier STL como arma de la línea: se escala (por defecto, 0,40 × la altura de la línea), le elegís con 3 clics el "
               "plano que corta el mango original y el programa le pone la empuñadura dentada que encaja en la mano con agarre del catálogo "
               "abierto. El resultado se ve encima del arma (translúcida). Con «Guardar» queda en el catálogo como pieza «accesorio» "
               "y se puede elegir para cualquier figura de la línea. Si el mango queda más grueso que el vástago, imprimila con soporte bajo el vástago.")

    sec = _collapsible(box, "Accesorios que viajan con la figura", expanded=False)
    win.box_proj_acc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    sec.append(win.box_proj_acc)
    win._acc_sig = None
    _help(sec, "Armas y accesorios del catálogo que acompañan a esta figura: al exportar salen como STL aparte en piezas/ (listos para imprimir) y "
               "dentro del ensamblado, al lado de la figura. Quedan guardados en el .proyecto. No se pegan en el puño. Cada uno dice si se sujeta "
               "en la mano pegada de esta figura.")

    sec = _collapsible(box, "Cabeza")
    win.btn_neck = win._button("Agregar plano del cuello / cabeza (3 puntos)", lambda *_: win.ctrl.start_neck_plane())
    win.btn_head_stl = win._button("Cabeza de reemplazo (STL)…", win._on_head_replacement)
    win.btn_head_orig = win._button("Volver a la cabeza original", lambda *_: win.ctrl.clear_head_replacement())
    win.btn_head_cut = win._button("Cortar la base de la cabeza de reemplazo (3 puntos)", lambda *_: win.ctrl.start_head_cut_plane())
    win.btn_head_uncut = win._button("Quitar el corte de la base", lambda *_: win.ctrl.clear_head_cut_plane())
    win.btn_head_pin = win._button("Elegir dónde va el pasador del cuello (vista de costado)", lambda *_: win.ctrl.start_head_pin())
    win.btn_head_pin_reset = win._button("Pasador del cuello: volver a la posición automática", lambda *_: win.ctrl.reset_head_pin())
    for b in (win.btn_neck, win.btn_head_stl, win.btn_head_orig, win.btn_head_cut, win.btn_head_uncut,
              win.btn_head_pin, win.btn_head_pin_reset):
        sec.append(b)
    win.btn_head_snap = win._button("Imán del pasador (M)", lambda *_: win.ctrl.snap_pin())
    win.btn_head_heat = win._button("Mapa de calor del pasador (H)", lambda *_: win.ctrl.toggle_heat())
    sec.append(win.btn_head_snap)
    sec.append(win.btn_head_heat)
    win._build_joint_aim(sec, "cuello")
    row = Gtk.Box(spacing=6)
    row.append(Gtk.Label(label="Cabeza de reemplazo: eje «arriba» del STL", xalign=0, hexpand=True))
    win.dd_head_up = Gtk.DropDown.new_from_strings(list(REPLACEMENT_UPS))
    win.dd_head_up.connect("notify::selected", win._on_head_up)
    row.append(win.dd_head_up)
    sec.append(row)
    # (clave de HeadSpec/set_head_adjust, rótulo, mínimo, máximo, paso, decimales)
    win.head_spins: dict[str, Gtk.SpinButton] = {}
    for key, label, lo, hi, step, digits in HEAD_ADJUST_FIELDS:
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
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
    _help(sec, "Pasador del cuello: «Elegir dónde va…» pone la vista de costado (el cuello se ve de canto). Un clic lo mueve \
adelante o atrás sobre la cara del cuello (siempre centrado en x = 0); con flechas se ajusta de a 0,1 mm (Mayús, 1 mm). \
En naranja se ve el contorno del pasador (vástago y cabeza); en verde, la posición automática.")
    _help(sec, "La cabeza de reemplazo se ve en naranja sobre el cuello (la original se oculta). Orientación: eje «arriba» "
               "y giros X / Y / Z (los giros se aplican en ese orden). Escala: 1,00 deja la base igual a la del cuello; "
               "más o menos agranda o achica la cabeza entera. Corte de la base: elegí 3 puntos sobre la cabeza naranja; el plano "
               "que forman pasa a ser la base (lo de abajo se descarta) y acompaña a la malla si después la girás. "
               "Si la base coincide poco con la del cuello, probá otro giro, escala o corte: el ajuste se recalcula al instante.")

    # --------------------------------------------------------- Catálogo
