"""Barra contextual sobre el visor (UX R2): dice qué herramienta está activa, qué hace cada clic y da los botones del modo.

La tabla (`MODE_HELP`) no usa GTK y se prueba aparte; `build` arma la barra (un `Gtk.Revealer` dentro del `Gtk.Overlay` del visor)
y `update` la muestra u oculta según el modo del controlador. Cada ayuda queda en una línea (≤ 200 caracteres).
"""
from __future__ import annotations
from ..i18n import tr, lz, loc

from .controller import JOINT_PREVIEW_FLEX_MAX, JOINT_PREVIEW_FLEX_MIN, Mode

_PLANE3 = lz("mode_bar.clic_punto_sobre_la_figura_3")
_PIN = lz("mode_bar.clic_poner_el_pasador_flechas_mover")

# modo -> (ayuda de una línea, acciones); acciones: undo_point, close, cancel, snap, heat, done, preview, fix_ok / fix_no
# (estas dos solo se muestran mientras el controlador tiene una propuesta de pasador a la espera del OK: ver `PROPOSAL_ACTIONS`)
MODE_HELP: dict[Mode, tuple[str, tuple[str, ...]]] = {
    Mode.CROWN: (lz("mode_bar.clic_sobre_la_coronilla_de_la"), ("cancel",)),
    Mode.SYMMETRY: (_PLANE3, ("undo_point", "cancel")),
    Mode.LIMB_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.NECK_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.WEAPON_CUT_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.HEAD_CUT_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.HAND_EXTRACT: (_PLANE3, ("undo_point", "cancel")),
    Mode.HAND_PLACE: (_PLANE3, ("undo_point", "cancel")),
    Mode.WAIST_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.JOINT_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.JOINT_LINES: (lz("mode_bar.clic_4_puntos_de_frente_arribacodo"),
                       ("undo_point", "cancel")),
    Mode.JOINT_SIDE: (lz("mode_bar.clic_4_puntos_de_costado_para"),
                      ("undo_point", "cancel", "preview")),
    Mode.JOINT_AXIS: (lz("mode_bar.clic_sobre_la_figura_eje_de"), ("preview", "done")),
    Mode.POLYGON: (lz("mode_bar.clic_agregar_vertice_intro_cerrar_el"),
                   ("undo_point", "close", "cancel", "preview")),
    Mode.PIN: (_PIN, ("fix_ok", "fix_no", "snap", "heat", "preview", "done")),
    Mode.HEAD_PIN: (_PIN, ("snap", "heat", "done")),
    Mode.WAIST_PIN: (_PIN, ("snap", "heat", "done")),
    Mode.TRIM_PICK: (lz("mode_bar.clic_sobre_la_figura_centro_del"), ("cancel",)),
    Mode.FLOOR_CUT: (lz("mode_bar.clic_sobre_la_figura_o_altura_del"), ("done",)),
    Mode.FLAT_PLANE: (lz("mode_bar.clic_o_altura_del_corte_con"), ("done",)),
    Mode.GRIP_PLANE: (lz("mode_bar.agarre_plano"), ("undo_point", "cancel")),
    Mode.GRIP_CENTER: (lz("mode_bar.agarre_centro"), ("cancel",)),
    Mode.GRIP_END: (lz("mode_bar.agarre_final"), ("cancel",)),
}

ACTION_LABELS = {"undo_point": (lz("mode_bar.quitar_ultimo"), lz("mode_bar.quita_el_ultimo_punto_o_vertice")),
                 "close": (lz("mode_bar.cerrar_poligono"), lz("mode_bar.cierra_el_poligono_con_los_vertices")),
                 "cancel": (lz("common.cancelar"), lz("mode_bar.sale_de_la_herramienta_sin_guardar")),
                 "done": (lz("mode_bar.terminar"), lz("mode_bar.sale_de_la_herramienta_esc")),
                 "snap": (lz("common.iman"), lz("mode_bar.lleva_el_pasador_al_punto_mas")),
                 "heat": (lz("mode_bar.calor"), lz("mode_bar.alterna_la_vista_de_mapa_de")),
                 "preview": (lz("mode_bar.simulacion"), lz("mode_bar.muestra_u_oculta_la_simulacion_de")),
                 "fix_ok": (lz("mode_bar.aceptar_pasador_propuesto"), lz("mode_bar.aplica_el_pasador_mas_chico_propuesto")),
                 "fix_no": (lz("mode_bar.dejar_el_pasador_como_esta"), lz("mode_bar.no_cambia_el_pasador_ni_su_medida"))}
PROPOSAL_ACTIONS = ("fix_ok", "fix_no")           # la pregunta «¿achicar el pasador?»: solo con una propuesta pendiente


def help_for(mode: Mode) -> tuple[str, tuple[str, ...]] | None:
    """(ayuda, acciones) del modo, o None si el modo no lleva barra (ver la figura)."""
    return MODE_HELP.get(mode)


def build(win):
    """Barra oculta; la ventana la agrega al `Gtk.Overlay` del visor y llama a `update` en cada refresco."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    ctrl = win.ctrl
    revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, halign=Gtk.Align.CENTER,
                            valign=Gtk.Align.START, margin_top=10, reveal_child=False)
    bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    bar.add_css_class("afm-mode-bar")
    win.mode_title = Gtk.Label(xalign=0)
    win.mode_title.add_css_class("afm-mode-title")
    win.mode_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=64)
    row = Gtk.Box(spacing=6)
    actions = {
        "undo_point": ctrl.undo_point, "close": ctrl.close_polygon, "cancel": ctrl.cancel, "done": ctrl.cancel,
        "snap": ctrl.snap_pin, "heat": ctrl.toggle_heat, "preview": ctrl.toggle_joint_preview_here,
        "fix_ok": ctrl.accept_pin_fix, "fix_no": ctrl.reject_pin_fix,
    }
    buttons = {}
    for key, (label, tip) in ACTION_LABELS.items():
        b = Gtk.Button(label=loc(label), tooltip_text=loc(tip), visible=False)
        b.add_css_class("afm-cube-btn")
        b.connect("clicked", lambda _b, fn=actions[key]: fn())
        row.append(b)
        buttons[key] = b
    win.mode_flex_box = Gtk.Box(spacing=6, visible=False)          # flexión de la simulación, a mano de la herramienta
    win.mode_flex_box.append(Gtk.Label(label=tr("common.flexion_mostrada")))
    win.mode_flex = Gtk.SpinButton.new_with_range(JOINT_PREVIEW_FLEX_MIN, JOINT_PREVIEW_FLEX_MAX, 5.0)
    win.mode_flex.connect("value-changed", lambda b: None if getattr(win, "_mode_flex_sync", False) else ctrl.set_joint_preview_flex(b.get_value()))
    win.mode_flex_box.append(win.mode_flex)
    for w in (win.mode_title, win.mode_hint, row, win.mode_flex_box):
        bar.append(w)
    revealer.set_child(bar)
    win.mode_revealer, win.mode_buttons = revealer, buttons
    return revealer


def update(win, title: str) -> None:
    """Muestra la barra con el título y la ayuda del modo actual, o la oculta en el modo «ver»."""
    if getattr(win, "mode_revealer", None) is None:          # el título se refresca antes de armar el visor
        return
    info = help_for(win.ctrl.mode)
    if info is None:
        win.mode_revealer.set_reveal_child(False)
        return
    hint, acts = info
    ctrl = win.ctrl
    if "preview" in acts and not ctrl.preview_here_available():
        acts = tuple(a for a in acts if a != "preview")     # p. ej. la silueta con torso de catálogo: no hay cadera ni hombro del modelo que simular
    ask = ctrl.pin_proposal_text() if ctrl.pin_proposal is not None else ""
    if not ask:
        acts = tuple(a for a in acts if a not in PROPOSAL_ACTIONS)     # sin propuesta pendiente no hay nada que aceptar ni rechazar
    else:
        hint += "\n" + ask
    if ctrl.show_joint_preview and ctrl._jp["error"] and "preview" in acts:
        hint += "\n" + tr("mode_bar.simulacion_estado", status=ctrl.joint_preview_status())
    win.mode_title.set_text(title)
    win.mode_hint.set_text(loc(hint))
    for key, b in win.mode_buttons.items():
        b.set_visible(key in acts)
    if "preview" in acts:
        on = ctrl.show_joint_preview
        win.mode_buttons["preview"].set_label(tr("mode_bar.ocultar_simulacion") if on else tr("mode_bar.ver_simulacion"))
        win.mode_flex_box.set_visible(on)
        if on and abs(win.mode_flex.get_value() - ctrl._jp["flex"]) > 1e-9:
            win._mode_flex_sync = True                  # no devolver al controlador lo que él mismo acaba de dar
            win.mode_flex.set_value(ctrl._jp["flex"])
            win._mode_flex_sync = False
    else:
        win.mode_flex_box.set_visible(False)
    win.mode_revealer.set_reveal_child(True)
