"""Ayudas de construcción de widgets GTK4 compartidas por la ventana principal y los módulos de `gui/steps`."""
from __future__ import annotations
from ..i18n import tr

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402


class _WrapLayout(Gtk.LayoutManager):
    """Acomoda los hijos de izquierda a derecha, cada uno con su ancho natural, y pasa a otra línea cuando no entran.

    A diferencia de `Gtk.FlowBox` no alinea columnas entre líneas: cada línea es independiente (se ve como texto que se ajusta)."""

    def __init__(self, spacing: int = 6, row_spacing: int = 4):
        super().__init__()
        self.spacing, self.row_spacing = spacing, row_spacing

    @staticmethod
    def _children(widget: Gtk.Widget):
        c = widget.get_first_child()
        while c is not None:
            if c.should_layout():
                yield c
            c = c.get_next_sibling()

    def _rows(self, widget: Gtk.Widget, width: int):
        """Reparte los hijos en líneas para `width` píxeles: `[[(hijo, ancho, alto), …], …]`."""
        rows, cur, used = [], [], 0
        for c in self._children(widget):
            nat_w = c.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            min_w = c.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
            w = max(min_w, min(nat_w, width)) if width > 0 else nat_w
            need = w + (self.spacing if cur else 0)
            if cur and used + need > width > 0:
                rows.append(cur)
                cur, used, need = [], 0, w
            h = c.measure(Gtk.Orientation.VERTICAL, w)[1]
            cur.append((c, w, h))
            used += need
        if cur:
            rows.append(cur)
        return rows

    def do_get_request_mode(self, widget):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, widget, orientation, for_size):
        kids = list(self._children(widget))
        if orientation == Gtk.Orientation.HORIZONTAL:
            mins = [c.measure(orientation, -1)[0] for c in kids]
            nats = [c.measure(orientation, -1)[1] for c in kids]
            gap = self.spacing * max(0, len(kids) - 1)
            return (max(mins, default=0), sum(nats) + gap, -1, -1)
        width = for_size if for_size >= 0 else sum(c.measure(Gtk.Orientation.HORIZONTAL, -1)[1] for c in kids) + self.spacing * max(0, len(kids) - 1)
        rows = self._rows(widget, width)
        h = sum(max(ch for _c, _w, ch in r) for r in rows) + self.row_spacing * max(0, len(rows) - 1)
        return (h, h, -1, -1)

    def do_allocate(self, widget, width, height, baseline):
        y = 0
        for row in self._rows(widget, width):
            row_h = max(ch for _c, _w, ch in row)
            extra = max(0, width - (sum(w for _c, w, _h in row) + self.spacing * (len(row) - 1)))
            growers = [c for c, _w, _h in row if c.get_hexpand()]
            x = 0
            for c, w, ch in row:
                if c in growers:                                 # los widgets con hexpand se reparten lo que sobra de la línea
                    w += extra // len(growers)
                rect = Gdk.Rectangle()
                rect.x, rect.y, rect.width, rect.height = x, y, w, row_h
                c.size_allocate(rect, -1)
                x += w + self.spacing
            y += row_h + self.row_spacing


class WrapRow(Gtk.Widget):
    """Fila de widgets que pasa a una segunda línea cuando no entra en el ancho (en vez de recortarse o ensanchar la ventana).

    Se usa como un `Gtk.Box` horizontal: `row.append(widget)`."""

    def __init__(self, spacing: int = 6, **kw):
        super().__init__(**kw)
        self.set_layout_manager(_WrapLayout(spacing))
        self.set_hexpand(True)

    def append(self, child: Gtk.Widget) -> None:
        child.set_parent(self)

    def remove(self, child: Gtk.Widget) -> None:
        if child.get_parent() is self:
            child.unparent()

    def do_dispose(self):
        while (c := self.get_first_child()) is not None:
            c.unparent()


def collapsible(parent: Gtk.Box, title: str, expanded: bool = True, sub: bool = False, css_class: str | None = None) -> Gtk.Box:
    """Sección (o subsección, `sub=True`) plegable: agrega un `Gtk.Expander` a `parent` y devuelve su caja interior."""
    exp = Gtk.Expander(expanded=expanded)
    if css_class:
        exp.add_css_class(css_class)
    lab = Gtk.Label(label=title, xalign=0, hexpand=True)
    lab.add_css_class("afm-sub-title" if sub else "afm-section-title")
    exp.set_label_widget(lab)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    body.set_margin_top(6)
    body.set_margin_bottom(8)
    body.set_margin_start(12 if sub else 6)
    exp.set_child(body)
    exp.set_margin_top(4 if sub else 8)
    parent.append(exp)
    return body




def no_scroll_change(widget: Gtk.Widget) -> None:
    """Que la rueda del ratón sobre un spin / desplegable NO cambie su valor: el scroll lo hace el panel (ScrolledWindow)."""
    ctl = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
    ctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)      # antes de que el control lo consuma

    def on_scroll(_c, _dx, dy):
        sw = widget.get_ancestor(Gtk.ScrolledWindow)
        if sw is not None:
            adj = sw.get_vadjustment()
            new = adj.get_value() + dy * adj.get_page_size() ** (2.0 / 3.0)    # mismo paso que usa GTK
            adj.set_value(max(adj.get_lower(), min(new, adj.get_upper() - adj.get_page_size())))
        return True                                              # consumido: el valor del control no cambia

    ctl.connect("scroll", on_scroll)
    widget.add_controller(ctl)




def help_note(parent: Gtk.Box, text: str) -> None:
    """Nota de ayuda: ícono de información, fondo tenue y borde lateral para distinguirla de los controles que configuran
    algo. Si el texto pasa de ~200 caracteres se ve solo el principio (oraciones completas) y el resto queda tras «Más ▾»."""
    from .help_text import split_help

    short, rest = split_help(text)
    box = Gtk.Box(spacing=8)
    box.add_css_class("afm-help")
    icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
    icon.set_valign(Gtk.Align.START)
    icon.add_css_class("afm-help-icon")
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
    lab = Gtk.Label(label=short, xalign=0, wrap=True, hexpand=True)
    lab.add_css_class("afm-help-text")
    col.append(lab)
    if rest:
        more_lab = Gtk.Label(label=rest, xalign=0, wrap=True, hexpand=True)
        more_lab.add_css_class("afm-help-text")
        revealer = Gtk.Revealer(child=more_lab, reveal_child=False)
        btn = Gtk.Button(label=tr("widgets.mas"), halign=Gtk.Align.START)
        btn.add_css_class("flat")

        def toggle(*_):
            on = not revealer.get_reveal_child()
            revealer.set_reveal_child(on)
            btn.set_label(tr("widgets.menos") if on else tr("widgets.mas"))

        btn.connect("clicked", toggle)
        col.append(revealer)
        col.append(btn)
    box.append(icon)
    box.append(col)
    parent.append(box)
