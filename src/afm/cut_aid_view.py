"""Cinta de calor del corte (ver `cut_aid`): una franja con una celda por desplazamiento del plano, coloreada por el nivel del corte.

Sin GTK: `ribbon_data` arma los datos y `draw_ribbon` los dibuja sobre cualquier contexto de cairo (la ventana le pasa el de un
`Gtk.DrawingArea`; las pruebas, el de una `ImageSurface`). `offset_at` traduce un clic en la franja al desplazamiento."""
from __future__ import annotations

import math

from .pin_aid_view import LEVEL_RGB

GREY = (0.75, 0.75, 0.78)
RED = (0.90, 0.25, 0.25)
MARGIN = 10.0


def ribbon_data(aid, actual: float | None = None) -> dict:
    """`{"celdas": [(offset, nivel)], "actual": off, "mejor": off | None, "paso": mm, "texto": str, "nivel": str}`.

    Sin cinta (el corte no aplica) queda solo el motivo en `texto`."""
    rib = aid.ribbon()
    cur = aid.base if actual is None else float(actual)
    data = {"celdas": [(off, r.nivel) for off, r in rib], "actual": cur, "mejor": None, "paso": 0.0, "texto": "", "nivel": "sin_catalogo"}
    if not rib:
        data["texto"] = "El corte no aplica: " + (aid.motivo or "no hay desplazamientos válidos")
        return data
    if len(rib) > 1:
        data["paso"] = rib[1][0] - rib[0][0]
    best = aid.best()
    data["mejor"] = None if best is None else best.uv[0]
    here = aid.evaluate(cur)
    data["nivel"] = here.nivel
    data["texto"] = f"Corte a {cur:.2f} mm: " + here.texto()
    return data


def _x_of(data: dict, w: float, off: float) -> float:
    cells = data["celdas"]
    lo, hi = cells[0][0], cells[-1][0]
    return MARGIN + (off - lo) / ((hi - lo) or 1.0) * (w - 2 * MARGIN)


def offset_at(data: dict, w: float, x: float) -> float | None:
    """El desplazamiento de la celda más cercana al clic en `x` (px) en una franja de `w` px; None sin celdas."""
    cells = data["celdas"]
    if not cells:
        return None
    return min((off for off, _n in cells), key=lambda off: abs(_x_of(data, w, off) - x))


def draw_ribbon(cr, w: float, h: float, data: dict) -> None:
    """Dibuja la franja en un lienzo de `w` × `h` px: celdas por nivel, marca del corte actual (triángulo) y del mejor (barra)."""
    cr.set_source_rgba(0.12, 0.13, 0.15, 1.0)
    cr.rectangle(0, 0, w, h)
    cr.fill()
    cells = data["celdas"]
    cr.set_font_size(11)
    cr.set_source_rgb(*LEVEL_RGB.get(data["nivel"], RED if data["nivel"] == "fuera" else GREY))
    cr.move_to(6, 13)
    cr.show_text(data["texto"][:62])
    if not cells:
        return
    top, bot = 22.0, h - 18.0
    step = data["paso"] or 1.0
    half = step / 2 * (w - 2 * MARGIN) / ((cells[-1][0] - cells[0][0]) or 1.0)
    for off, nivel in cells:
        x = _x_of(data, w, off)
        cr.set_source_rgb(*(RED if nivel == "fuera" else LEVEL_RGB.get(nivel, GREY)))
        cr.rectangle(x - half + 0.5, top, max(2 * half - 1.0, 1.0), bot - top)
        cr.fill()
    cr.set_source_rgb(0.85, 0.85, 0.88)                      # los extremos de la cinta, en mm
    for off in (cells[0][0], cells[-1][0]):
        cr.move_to(_x_of(data, w, off) - 8, h - 5)
        cr.show_text(f"{off:g}")
    if data["mejor"] is not None:                            # el mejor: una barra blanca sobre la celda
        x = _x_of(data, w, data["mejor"])
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.set_line_width(2.0)
        cr.move_to(x, top - 3)
        cr.line_to(x, bot + 3)
        cr.stroke()
    if cells[0][0] - step <= data["actual"] <= cells[-1][0] + step:   # el corte actual: un triángulo debajo
        x = _x_of(data, w, data["actual"])
        cr.set_source_rgb(1.0, 0.85, 0.3)
        cr.move_to(x, bot + 1)
        cr.line_to(x - 5, bot + 9)
        cr.line_to(x + 5, bot + 9)
        cr.close_path()
        cr.fill()
