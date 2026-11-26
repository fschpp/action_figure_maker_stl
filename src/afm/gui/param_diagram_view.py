"""Vista GTK del diagrama de parámetros (cabecera fija de la pestaña «Parámetros»). El dibujo vive en `param_diagram` (sin GTK)."""
from __future__ import annotations

import math
import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..i18n import tr
from . import param_diagram as D
from . import param_meta as PM
from .controller import PARAM_FIELDS

DIAGRAM_H = 190
_RANGES = {k: (lo, hi) for k, _l, lo, hi, _s, _d in PARAM_FIELDS}


def _rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


class CairoCanvas:
    """`Canvas` de `param_diagram` sobre un contexto de cairo."""

    def __init__(self, cr) -> None:
        self.cr = cr

    def _stroke(self, color: str, width: float, dash) -> None:
        cr = self.cr
        cr.set_source_rgb(*_rgb(color))
        cr.set_line_width(width)
        cr.set_dash(list(dash) if dash else [])
        cr.stroke()

    def line(self, p0, p1, color, width=1.0, dash=None):
        self.cr.move_to(*p0)
        self.cr.line_to(*p1)
        self._stroke(color, width, dash)

    def poly(self, pts, fill, stroke, width=1.0, dash=None, closed=True):
        if not pts:
            return
        cr = self.cr
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        if closed:
            cr.close_path()
        if fill:
            cr.set_source_rgb(*_rgb(fill))
            cr.fill_preserve()
        if stroke:
            self._stroke(stroke, width, dash)
        else:
            cr.new_path()

    def circle(self, c, r, fill, stroke, width=1.0, dash=None):
        cr = self.cr
        cr.new_sub_path()
        cr.arc(c[0], c[1], max(r, 0.1), 0, 2 * math.pi)
        if fill:
            cr.set_source_rgb(*_rgb(fill))
            cr.fill_preserve()
        if stroke:
            self._stroke(stroke, width, dash)
        else:
            cr.new_path()

    def text_width(self, s, size):
        self.cr.set_font_size(size)
        return self.cr.text_extents(s).x_advance

    def text(self, p, s, color, size=11.0, anchor="middle", bg=None):
        if not s:
            return
        cr = self.cr
        cr.set_font_size(size)
        wd = cr.text_extents(s).x_advance
        x = p[0] - wd / 2 if anchor == "middle" else (p[0] if anchor == "start" else p[0] - wd)
        if bg:
            cr.set_source_rgb(*_rgb(bg))
            cr.rectangle(x - 2, p[1] - size, wd + 4, size + 3)
            cr.fill()
        cr.set_source_rgb(*_rgb(color))
        cr.move_to(x, p[1])
        cr.show_text(s)


class ParamDiagramView(Gtk.Box):
    """Título de la escena + dibujo con cotas del parámetro activo. `set_active(key)` cambia de escena y resalta; `animate(key)`
    hace el vaivén; `refresh()` redibuja con los valores actuales (se llama al cambiar cualquier parámetro)."""

    def __init__(self, get_value: Callable[[str], float]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._get = get_value
        self._key = "clearance"
        self._anim = D.Animator()
        self._timer = 0
        self.add_css_class("afm-diagram")
        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("afm-sub-title")
        self.append(self.title)
        self.area = Gtk.DrawingArea(hexpand=True, content_height=DIAGRAM_H)
        self.area.set_draw_func(self._draw)
        self.append(self.area)
        self._update_title()

    @property
    def active_key(self) -> str:
        return self._key

    def _update_title(self) -> None:
        self.title.set_text(D.scene_title(D.scene_id_for_key(self._key)))

    def set_active(self, key: str) -> None:
        if key == self._key:
            return
        self._anim.stop()
        self._key = key
        self._update_title()
        self.area.queue_draw()

    def refresh(self) -> None:
        self._update_title()
        self.area.queue_draw()

    def animate(self, key: str) -> None:
        """Vaivén del parámetro `key` alrededor de su valor actual (clic en su etiqueta)."""
        self.set_active(key)
        lo, hi = _RANGES.get(key, (0.0, 1.0))
        self._anim.start(key, self._get(key), lo, hi, time.monotonic(), boolean=PM.is_boolean(key))
        if not self._timer:
            self._timer = GLib.timeout_add(33, self._tick)

    def _tick(self) -> bool:
        self.area.queue_draw()
        if self._anim.active(time.monotonic()):
            return True
        self._anim.stop()
        self._timer = 0
        self.area.queue_draw()
        return False

    def _dark(self) -> bool:
        try:
            c = self.get_color()
            return (c.red + c.green + c.blue) / 3 > 0.5            # texto claro = tema oscuro
        except Exception:                                          # noqa: BLE001
            return False

    def _draw(self, _area, cr, w, h) -> None:
        anim = self._anim.value(time.monotonic())
        cur = self._anim.key

        def v(k: str) -> float:
            return anim if (anim is not None and k == cur) else self._get(k)

        draw_scene(cr, w, h, self._key, v, self._get, self._dark())


def draw_scene(cr, w: float, h: float, key: str, value: Callable[[str], float], fixed: Callable[[str], float],
               dark: bool) -> None:
    """Dibuja la escena del parámetro `key` con sus cotas (la de la cabecera de «Parámetros» y la del cuadro flotante).

    `value` da los valores del dibujo; `fixed` los que fijan el encuadre (así el dibujo no se reescala al animar o editar)."""
    pal = D.DARK if dark else D.LIGHT
    cv = CairoCanvas(cr)
    cr.select_font_face("sans-serif")
    cr.set_source_rgb(*_rgb(pal["bg"]))
    cr.paint()
    sid = D.scene_id_for_key(key)
    frame = D.build_scene(sid, D.reference_values(_RANGES, fixed)).bbox()
    D.render(cv, D.build_scene(sid, value), key, w, h, pal, frame=frame)
