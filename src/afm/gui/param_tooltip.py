"""Cuadro flotante de cada parámetro (como el de Bambu Studio): qué hace, rango / valor por defecto / actual y el dibujo
acotado de su escena con el parámetro resaltado. Se muestra al posar el puntero sobre el nombre del parámetro."""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..i18n import tr
from . import param_diagram as D
from . import param_meta as PM
from .param_diagram_view import draw_scene

TIP_W, TIP_H = 320, 170
WRAP_CHARS = 42


def _fmt(v: float, digits: int) -> str:
    return f"{v:.{digits}f}".rstrip("0").rstrip(".") if digits > 0 else f"{v:.0f}"


class ParamTip(Gtk.Box):
    """Contenido del cuadro flotante de un parámetro; `refresh()` lo actualiza con los valores de ese momento."""

    def __init__(self, key: str, short: str, hint: str, get: Callable[[str], float], default: Callable[[str], float],
                 lo: float, hi: float, digits: int) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._key, self._get, self._default = key, get, default
        self._lo, self._hi, self._digits = lo, hi, digits
        self._bool = PM.is_boolean(key)
        title = Gtk.Label(label=short, xalign=0, wrap=True, max_width_chars=WRAP_CHARS)
        title.add_css_class("heading")
        self.append(title)
        body = PM.help_text(key)
        if body:
            self.append(Gtk.Label(label=body, xalign=0, wrap=True, max_width_chars=WRAP_CHARS))
        if hint:
            note = Gtk.Label(label=hint, xalign=0, wrap=True, max_width_chars=WRAP_CHARS)
            note.add_css_class("dim-label")
            self.append(note)
        self.area = Gtk.DrawingArea(content_width=TIP_W, content_height=TIP_H)
        self.area.set_draw_func(self._draw)
        self.append(self.area)
        self.foot = Gtk.Label(xalign=0, wrap=True, max_width_chars=WRAP_CHARS)
        self.foot.add_css_class("dim-label")
        self.append(self.foot)
        self.set_size_request(TIP_W, -1)
        self.refresh()

    def _show(self, v: float) -> str:
        return (tr("param_help.si") if v >= 0.5 else tr("param_help.no")) if self._bool else _fmt(v, self._digits)

    def refresh(self) -> None:
        parts = []
        if not self._bool:
            parts.append(tr("param_help.rango", lo=_fmt(self._lo, self._digits), hi=_fmt(self._hi, self._digits)))
        parts.append(tr("param_help.por_defecto", d=self._show(self._default(self._key))))
        parts.append(tr("param_help.actual", v=self._show(self._get(self._key))))
        self.foot.set_text("  ·  ".join(parts))
        self.area.queue_draw()

    def _dark(self) -> bool:
        try:
            c = self.get_color()
            return (c.red + c.green + c.blue) / 3 > 0.5            # texto claro = fondo oscuro
        except Exception:                                          # noqa: BLE001
            return False

    def _draw(self, _area, cr, w, h) -> None:
        draw_scene(cr, w, h, self._key, self._get, self._get, self._dark())


def attach(label: Gtk.Widget, key: str, short: str, hint: str, get: Callable[[str], float],
           default: Callable[[str], float], lo: float, hi: float, digits: int) -> None:
    """Hace que `label` muestre el cuadro flotante del parámetro `key` al posar el puntero (se arma una sola vez)."""
    cache: dict[str, ParamTip] = {}

    def on_query(_w, _x, _y, _kbd, tooltip) -> bool:
        tip = cache.get(key)
        if tip is None:
            tip = cache[key] = ParamTip(key, short, hint, get, default, lo, hi, digits)
        tip.refresh()
        tooltip.set_custom(tip)
        return True

    label.set_has_tooltip(True)
    label.connect("query-tooltip", on_query)
