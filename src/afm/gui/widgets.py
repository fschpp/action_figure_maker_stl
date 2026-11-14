"""Ayudas de construcción de widgets GTK4 compartidas por la ventana principal y los módulos de `gui/steps`."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


def collapsible(parent: Gtk.Box, title: str, expanded: bool = True, sub: bool = False) -> Gtk.Box:
    """Sección (o subsección, `sub=True`) plegable: agrega un `Gtk.Expander` a `parent` y devuelve su caja interior."""
    exp = Gtk.Expander(expanded=expanded)
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
        btn = Gtk.Button(label="Más ▾", halign=Gtk.Align.START)
        btn.add_css_class("flat")

        def toggle(*_):
            on = not revealer.get_reveal_child()
            revealer.set_reveal_child(on)
            btn.set_label("Menos ▴" if on else "Más ▾")

        btn.connect("clicked", toggle)
        col.append(revealer)
        col.append(btn)
    box.append(icon)
    box.append(col)
    parent.append(box)
