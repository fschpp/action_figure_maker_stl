"""Barra contextual sobre el visor (UX R2): dice qué herramienta está activa, qué hace cada clic y da los botones del modo.

La tabla (`MODE_HELP`) no usa GTK y se prueba aparte; `build` arma la barra (un `Gtk.Revealer` dentro del `Gtk.Overlay` del visor)
y `update` la muestra u oculta según el modo del controlador. Cada ayuda queda en una línea (≤ 200 caracteres).
"""
from __future__ import annotations

from .controller import Mode

_PLANE3 = "Clic: punto sobre la figura (3 puntos) · Retroceso: quitar el último · Esc: cancelar"
_PIN = "Clic: poner el pasador · Flechas: mover (Mayús: más) · M: imán · H: calor · Esc: salir"

# modo -> (ayuda de una línea, acciones); acciones: undo_point, close, cancel, snap, heat, done
MODE_HELP: dict[Mode, tuple[str, tuple[str, ...]]] = {
    Mode.CROWN: ("Clic sobre la coronilla de la figura · Esc: cancelar", ("cancel",)),
    Mode.SYMMETRY: (_PLANE3, ("undo_point", "cancel")),
    Mode.LIMB_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.NECK_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.WEAPON_CUT_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.HEAD_CUT_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.HAND_EXTRACT: (_PLANE3, ("undo_point", "cancel")),
    Mode.HAND_PLACE: (_PLANE3, ("undo_point", "cancel")),
    Mode.WAIST_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.JOINT_PLANE: (_PLANE3, ("undo_point", "cancel")),
    Mode.JOINT_LINES: ("Clic: 4 puntos de frente (arriba→codo y codo→abajo) · Retroceso: quitar el último · Esc: cancelar",
                       ("undo_point", "cancel")),
    Mode.JOINT_SIDE: ("Clic: 4 puntos de costado para corregir las guías · Retroceso: quitar el último · Esc: cancelar",
                      ("undo_point", "cancel", "preview")),
    Mode.JOINT_AXIS: ("Clic sobre la figura: eje de la bisagra (con la simulación al costado se puede reajustar varias veces) · "
                      "Esc: terminar", ("preview", "done")),
    Mode.POLYGON: ("Clic: agregar vértice · Intro: cerrar el polígono · Retroceso: quitar el último · Esc: salir",
                   ("undo_point", "close", "cancel")),
    Mode.PIN: (_PIN, ("snap", "heat", "done")),
    Mode.HEAD_PIN: (_PIN, ("snap", "heat", "done")),
    Mode.WAIST_PIN: (_PIN, ("snap", "heat", "done")),
    Mode.TRIM_PICK: ("Clic sobre la figura: centro del recorte · Esc: cancelar", ("cancel",)),
    Mode.FLAT_PLANE: ("Clic o ↑↓: altura del corte con la cama de impresión (Mayús: más) · Esc: terminar", ("done",)),
}

ACTION_LABELS = {"undo_point": ("Quitar último", "Quita el último punto o vértice (Retroceso)"),
                 "close": ("Cerrar polígono", "Cierra el polígono con los vértices puestos (Intro)"),
                 "cancel": ("Cancelar", "Sale de la herramienta sin guardar lo que falta (Esc)"),
                 "done": ("Terminar", "Sale de la herramienta (Esc)"),
                 "snap": ("Imán", "Lleva el pasador al punto más cercano de la malla (M)"),
                 "heat": ("Calor", "Alterna la vista de mapa de calor del pasador (H)"),
                 "preview": ("Simulación", "Muestra u oculta la simulación de la articulación al costado de la figura, para "
                                           "verla doblar mientras se elige el punto")}


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
    }
    buttons = {}
    for key, (label, tip) in ACTION_LABELS.items():
        b = Gtk.Button(label=label, tooltip_text=tip, visible=False)
        b.add_css_class("afm-cube-btn")
        b.connect("clicked", lambda _b, fn=actions[key]: fn())
        row.append(b)
        buttons[key] = b
    win.mode_flex_box = Gtk.Box(spacing=6, visible=False)          # flexión de la simulación, a mano de la herramienta
    win.mode_flex_box.append(Gtk.Label(label="Flexión mostrada (°)"))
    win.mode_flex = Gtk.SpinButton.new_with_range(-45.0, 150.0, 5.0)
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
    win.mode_title.set_text(title)
    win.mode_hint.set_text(hint)
    for key, b in win.mode_buttons.items():
        b.set_visible(key in acts)
    ctrl = win.ctrl
    if "preview" in acts:
        on = ctrl.show_joint_preview
        win.mode_buttons["preview"].set_label("Ocultar simulación" if on else "Ver simulación")
        win.mode_flex_box.set_visible(on)
        if on and abs(win.mode_flex.get_value() - ctrl._jp["flex"]) > 1e-9:
            win._mode_flex_sync = True                  # no devolver al controlador lo que él mismo acaba de dar
            win.mode_flex.set_value(ctrl._jp["flex"])
            win._mode_flex_sync = False
    else:
        win.mode_flex_box.set_visible(False)
    win.mode_revealer.set_reveal_child(True)
