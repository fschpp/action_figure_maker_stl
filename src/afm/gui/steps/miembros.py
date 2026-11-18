"""Pestaña «Miembros» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations
from ...i18n import tr

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ...i18n import loc
from .. import param_meta as PM
from ..controller import PARAM_FIELDS
from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Miembros» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page(tr("common.miembros"))
    sec = _collapsible(box, tr("miembros.silueta_del_miembro"))
    _help(sec, tr("miembros.en_la_fila_de_cada_miembro_de"))
    win.btn_close_poly = win._button(tr("miembros.cerrar_poligono_intro"), lambda *_: win.ctrl.close_polygon())
    win.btn_propose = win._button(tr("miembros.proponer_silueta_automatica"), lambda *_: win.ctrl.propose_silhouette())
    win.btn_outline = win._button(tr("miembros.empezar_desde_el_contorno_de_la"), lambda *_: win.ctrl.use_outline_as_base())
    win.btn_raise = win._button(tr("miembros.subir_el_borde_superior_2_a"),
                                  lambda *_: win.ctrl.raise_top())
    win.btn_clear_poly = win._button(tr("miembros.vaciar_y_redibujar"), lambda *_: win.ctrl.clear_polygon())
    win.btn_end_poly = win._button(tr("miembros.terminar_edicion_de_silueta_esc"), lambda *_: win.ctrl.cancel())
    for b in (win.btn_close_poly, win.btn_propose, win.btn_outline, win.btn_raise, win.btn_clear_poly,
              win.btn_end_poly):
        sec.append(b)

    sec = _collapsible(box, tr("miembros.pasador_de_brazo_cadera"))
    _help(sec, tr("miembros.elegi_colocar_el_pasador_marcador_en"))
    win.btn_pin_snap = win._button(tr("miembros.iman_mejor_posicion_cercana_m"), lambda *_: win.ctrl.snap_pin())
    win.btn_pin_heat = win._button(tr("miembros.mapa_de_calor_mostrar_ocultar_h"), lambda *_: win.ctrl.toggle_heat())
    win.btn_pin_reset = win._button(tr("miembros.volver_a_la_posicion_automatica"), lambda *_: win.ctrl.reset_pin())
    win.btn_pin_face = win._button(tr("miembros.mirar_la_cara_de_frente"), lambda *_: win._pin_face_view())
    win.btn_pin_end = win._button(tr("miembros.terminar_colocacion_del_pasador_esc"), lambda *_: win.ctrl.cancel())
    win.btn_pin_widest = win._button(tr("miembros.pin_mas_ancho"), lambda *_: win.ctrl.pin_widest())
    win.btn_pin_shrink = win._button(tr("miembros.pin_achicar"), lambda *_: win.ctrl.pin_shrink_to_fit())
    for b in (win.btn_pin_snap, win.btn_pin_heat, win.btn_pin_widest, win.btn_pin_shrink, win.btn_pin_reset, win.btn_pin_face,
              win.btn_pin_end):
        sec.append(b)
    # Simulación del giro del hombro / cadera sin salir de la colocación (se ve al costado de la figura, como la cadera al dibujar la silueta)
    win.chk_pin_sim = Gtk.CheckButton(label=tr("miembros.pin_simular"))
    win.chk_pin_sim.connect("toggled", lambda b: None if win._updating else win.ctrl.pin_simulate_pivot(b.get_active()))
    sec.append(win.chk_pin_sim)
    # Tamaño del pasador y del hueco (vástago, cabeza, largo, alto de cabeza y holgura): el fantasma del visor cambia al instante
    win.pin_size_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, visible=False)
    win.pin_size_box.append(Gtk.Label(label=tr("miembros.pin_tamanos"), xalign=0, css_classes=["heading"]))
    win.pin_size_rows = {}
    for key in ("shoulder_pin.shaft_d", "shoulder_pin.head_d", "shoulder_pin.shaft_len", "shoulder_pin.head_h",
                "hip_pin.shaft_d", "hip_pin.head_d", "hip_pin.shaft_len", "hip_pin.head_h", "clearance"):
        _k, label, lo, hi, step, digits = next(f for f in PARAM_FIELDS if f[0] == key)
        short, hint = PM.split_label(loc(label))
        row = Gtk.Box(spacing=6)
        lbl = Gtk.Label(label=short, xalign=0, hexpand=True, wrap=True)
        if hint:
            lbl.set_tooltip_text(hint)
        row.append(lbl)
        spin = Gtk.SpinButton.new_with_range(lo, hi, step)
        spin.set_digits(digits)
        spin.connect("value-changed", lambda b, k=key: None if win._updating else win.ctrl.set_pin_size(k, b.get_value()))
        row.append(spin)
        win.pin_size_box.append(row)
        win.pin_size_rows[key] = {"row": row, "spin": spin}
    sec.append(win.pin_size_box)
    # Recuadro de la mira: la cara de este corte con el pasador y, encima, las caras del catálogo (solo con catálogo abierto)
    win.aim_area = Gtk.DrawingArea(content_width=300, content_height=220, visible=False)
    win.aim_area.set_draw_func(win._draw_aim)
    sec.append(win.aim_area)

    # --------------------------------------------------------- Recortes
