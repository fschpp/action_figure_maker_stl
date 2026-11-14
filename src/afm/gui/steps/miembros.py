"""Pestaña «Miembros» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Miembros» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page("Miembros")
    sec = _collapsible(box, "Silueta del miembro")
    _help(sec, "Elegí «Dibujar silueta» (lápiz) en un plano de la lista de arriba; «2.º» dibuja el segundo corte, de frente (el "
               "miembro es lo que queda dentro de las dos siluetas: p. ej. en una pierna, la columna del muslo + el escalón "
               "de la cadera). Clic = vértice sobre el plano; cerrá con un clic en el primero o con Intro. Ya cerrado: clic "
               "en un vértice y luego clic para moverlo; clic sobre un borde agrega uno; Retroceso borra el elegido.")
    win.btn_close_poly = win._button("Cerrar polígono (Intro)", lambda *_: win.ctrl.close_polygon())
    win.btn_propose = win._button("Proponer silueta automática", lambda *_: win.ctrl.propose_silhouette())
    win.btn_outline = win._button("Empezar desde el contorno de la figura", lambda *_: win.ctrl.use_outline_as_base())
    win.btn_raise = win._button("Subir el borde superior (2.º corte) hasta la otra silueta",
                                  lambda *_: win.ctrl.raise_top())
    win.btn_clear_poly = win._button("Vaciar y redibujar", lambda *_: win.ctrl.clear_polygon())
    win.btn_end_poly = win._button("Terminar edición de silueta (Esc)", lambda *_: win.ctrl.cancel())
    for b in (win.btn_close_poly, win.btn_propose, win.btn_outline, win.btn_raise, win.btn_clear_poly,
              win.btn_end_poly):
        sec.append(b)

    sec = _collapsible(box, "Pasador de brazo / cadera")
    _help(sec, "Elegí «Colocar el pasador» (marcador) en un miembro de la lista de planos. Se muestra la cara de corte de "
               "frente, con el torso recortado: el círculo naranja es el vástago y el amarillo la cabeza del hongo; el punto "
               "verde es dónde lo pondría el programa. Clic sobre la cara = mover el pasador; flechas = ajustar 0,1 mm "
               "(Mayús: 1 mm). Con un catálogo abierto, el círculo que sigue al puntero es la mira: verde si lo aceptan casi "
               "todas las piezas del catálogo, amarillo si solo una parte, rojo si ninguna (informativo, no una garantía); "
               "«Imán» (M) busca la mejor posición a ±2 mm. Los puntos verde / amarillo / rojo sobre la cara son el mapa de calor (se barre una vez al entrar; H lo oculta). Si no lo tocás, se usa la posición automática.")
    win.btn_pin_snap = win._button("Imán: mejor posición cercana (M)", lambda *_: win.ctrl.snap_pin())
    win.btn_pin_heat = win._button("Mapa de calor: mostrar / ocultar (H)", lambda *_: win.ctrl.toggle_heat())
    win.btn_pin_reset = win._button("Volver a la posición automática", lambda *_: win.ctrl.reset_pin())
    win.btn_pin_face = win._button("Mirar la cara de frente", lambda *_: win._pin_face_view())
    win.btn_pin_end = win._button("Terminar colocación del pasador (Esc)", lambda *_: win.ctrl.cancel())
    for b in (win.btn_pin_snap, win.btn_pin_heat, win.btn_pin_reset, win.btn_pin_face, win.btn_pin_end):
        sec.append(b)
    # Recuadro de la mira: la cara de este corte con el pasador y, encima, las caras del catálogo (solo con catálogo abierto)
    win.aim_area = Gtk.DrawingArea(content_width=300, content_height=220, visible=False)
    win.aim_area.set_draw_func(win._draw_aim)
    sec.append(win.aim_area)

    # --------------------------------------------------------- Recortes
