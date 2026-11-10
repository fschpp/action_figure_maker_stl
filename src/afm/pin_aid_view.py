"""Recuadro de la mira del pasador (el «bullseye»): la cara de ESTE corte con el círculo del pasador y, encima, el contorno de la cara
de cada pieza del catálogo con la que se acoplaría, para ver el desajuste de un vistazo.

Sin GTK: `aim_box_data` arma los datos (anillos en mm, en (u, v) del puerto, con el pasador en el origen) y `draw_aim_box` los
dibuja sobre cualquier contexto de cairo (la ventana le pasa el de un `Gtk.DrawingArea`; las pruebas, el de una `ImageSurface`).
"""
from __future__ import annotations

import math

MAX_REFS = 12                                   # tantas caras del catálogo se superponen como máximo (las que no dan «ok» primero)
LEVEL_RGB = {"ok": (0.20, 0.75, 0.30), "aviso": (0.95, 0.75, 0.10), "no": (0.90, 0.25, 0.25)}
_ORDER = {"no": 0, "aviso": 1, "ok": 2}


def _rings(shape) -> list:
    """Los anillos (exterior e interiores) de una figura de shapely como listas de (u, v)."""
    geoms = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
    out = []
    for g in geoms:
        if g.is_empty or g.geom_type != "Polygon":
            continue
        out.append([list(map(float, p)) for p in g.exterior.coords])
        out.extend([list(map(float, p)) for p in h.coords] for h in g.interiors)
    return out


def aim_box_data(aid, reading) -> dict:
    """Lo que dibuja el recuadro para la lectura `reading`: la cara propia, las del catálogo y el pasador.

    `{"propia": [anillo], "refs": [(ref, nivel, [anillo])], "vastago": r, "cabeza": r, "texto": str, "nivel": str}`. Fuera de la cara
    (o sin lectura) no hay caras que superponer: queda solo el texto."""
    data = {"propia": [], "refs": [], "vastago": aid.pin.shaft_d / 2, "cabeza": aid.pin.head_d / 2,
            "texto": reading.texto(), "nivel": reading.nivel}
    if not reading.dentro:
        return data
    try:
        data["propia"] = _rings(aid.own_face(reading))
    except ValueError:                           # una cara degenerada en ese punto
        return data
    refs = sorted(aid.reference_faces(reading), key=lambda r: _ORDER.get(r[1], 3))[:MAX_REFS]
    data["refs"] = [(ref, nivel, _rings(shape)) for ref, nivel, shape in refs]
    return data


def _extent(data: dict) -> tuple:
    """Medio lado (mm) del cuadro que muestra la cara propia con aire, y su centro: las caras del catálogo que se pasan se recortan."""
    pts = [p for ring in data["propia"] for p in ring]
    if not pts:
        r = max(data["cabeza"], 1.0) * 2
        return 0.0, 0.0, r
    us, vs = [p[0] for p in pts], [p[1] for p in pts]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    half = max(u1 - u0, v1 - v0, 2 * data["cabeza"]) / 2 * 1.25
    return (u0 + u1) / 2, (v0 + v1) / 2, half


def draw_aim_box(cr, w: float, h: float, data: dict) -> None:
    """Dibuja el recuadro en un lienzo de `w` × `h` px (fondo, cara propia, caras del catálogo, pasador y texto)."""
    cr.set_source_rgba(0.12, 0.13, 0.15, 1.0)
    cr.rectangle(0, 0, w, h)
    cr.fill()
    cu, cv, half = _extent(data)
    top = 18.0                                    # lugar para el texto de arriba
    k = min(w, h - top) / (2 * half)
    ox, oy = w / 2, top + (h - top) / 2

    def xy(p):
        return ox + (p[0] - cu) * k, oy - (p[1] - cv) * k

    def path(rings):
        for ring in rings:
            if not ring:
                continue
            cr.move_to(*xy(ring[0]))
            for q in ring[1:]:
                cr.line_to(*xy(q))
            cr.close_path()

    cr.save()
    cr.rectangle(0, top, w, h - top)
    cr.clip()
    path(data["propia"])                         # la cara de este corte
    cr.set_source_rgba(0.35, 0.65, 0.95, 0.35)
    cr.fill_preserve()
    cr.set_source_rgba(0.55, 0.78, 1.0, 1.0)
    cr.set_line_width(2.0)
    cr.stroke()
    cr.set_line_width(1.5)
    for _ref, nivel, rings in reversed(data["refs"]):         # las que fallan quedan arriba
        path(rings)
        cr.set_source_rgba(*LEVEL_RGB.get(nivel, (0.7, 0.7, 0.7)), 0.9)
        cr.stroke()
    px, py = xy((0.0, 0.0))                       # el pasador está en el origen del puerto
    for r, rgb in ((data["cabeza"], (1.0, 0.85, 0.3)), (data["vastago"], (1.0, 0.55, 0.1))):
        cr.new_sub_path()
        cr.arc(px, py, r * k, 0, 2 * math.pi)
        cr.set_source_rgb(*rgb)
        cr.set_line_width(1.5)
        cr.stroke()
    cr.arc(px, py, 2.0, 0, 2 * math.pi)
    cr.set_source_rgb(1.0, 0.25, 0.25)
    cr.fill()
    cr.restore()
    cr.set_source_rgb(*LEVEL_RGB.get(data["nivel"], (0.75, 0.75, 0.78)))
    cr.set_font_size(11)
    cr.move_to(6, 13)
    cr.show_text(data["texto"][:60])
