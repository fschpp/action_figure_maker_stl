"""Pestaña «Generar» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..controller import PARAM_FIELDS
from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Generar» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page("Generar")
    sec = _collapsible(box, "Generar")
    win.btn_generate = win._button("Generar figura", lambda *_: win._on_generate())
    win.btn_generate.add_css_class("suggested-action")
    sec.append(win.btn_generate)
    _help(sec, "Generar puede tardar varios minutos. Si cambiás algo después, hay que generar de nuevo antes de exportar.")

    sec = _collapsible(box, "Opciones de exportación")
    win.chk_orient = Gtk.CheckButton(label="Apoyar las piezas en la cama al exportar (en la pose de cadera, cabeza arriba)", active=True)
    sec.append(win.chk_orient)
    sub = _collapsible(sec, "Impresión sin soportes", sub=True)
    win.chk_flat = Gtk.CheckButton(label="Modificar para imprimir sin soportes (aplana piernas y cola; los brazos giran hacia adelante)")
    win.chk_flat.set_tooltip_text("Corta con un plano horizontal sobre la cola (altura en la fila de abajo) y "
                                   "gira los brazos hacia adelante lo justo para que apoyen en ese plano. Las piernas quedan estiradas (rodilla a 0°): muslo y "
                                   "pantorrilla horizontales sobre el plano. Los brazos no se modifican.")
    win.chk_flat.connect("toggled", lambda b: None if win._updating else win._on_flat_toggled(b.get_active()))
    sub.append(win.chk_flat)
    spec = next(f for f in PARAM_FIELDS if f[0] == "flat_depth")
    sub.append(win._param_row(*spec))               # la altura del corte vive acá, junto a la casilla que la usa
    win.chk_fill_hip = Gtk.CheckButton(label="Rellenar los muslos hasta la pelvis recortada")
    win.chk_fill_hip.set_tooltip_text("El corte plano le saca a la pelvis una loncha y de pie queda un hueco contra el muslo. Con esto el muslo se \
queda con esa loncha y la pelvis se talla de nuevo alrededor (el muslo gira libre hasta el límite de cadera). Se nota al \
exportar; tarda un poco más.")
    win.chk_fill_hip.connect("toggled", lambda b: None if win._updating else win._on_fill_hip_toggled(b))
    sub.append(win.chk_fill_hip)
    win.btn_flat_plane = win._button("Ajustar la altura del corte (vista Derecha, figura sentada)", lambda *_: win._on_flat_plane())
    win.btn_flat_plane.set_tooltip_text("Muestra la figura generada sentada, de costado, con el plano horizontal del corte «sin soportes». "
                                         "Clic a la altura que querés, o ↑ ↓ (Mayús = 1 mm). Esc termina.")
    sub.append(win.btn_flat_plane)
    win.btn_flat_click = win._button("Ajustar el clic del brazo para que apoye sin soportes (hay que generar de nuevo)",
                                       lambda *_: win._on_flat_click())
    win.btn_flat_click.set_tooltip_text("El brazo con clic no se puede girar después de generar: su pose la fija la fase de los clics. "
                                         "Pone «Clic: ángulo del brazo al imprimir» en el ángulo en que el brazo apoya en el plano del corte.")
    sub.append(win.btn_flat_click)
    _help(sec, "Estas opciones se aplican al exportar con el botón «Exportar» de la barra superior (sale de la figura ya generada).")

    win.sidebar_box = win.tabs                  # lo que se desactiva mientras se genera
