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
    """Nota de ayuda: texto fijo (no se pliega ni se puede accionar), con ícono de información, fondo tenue y
    borde lateral para distinguirla de los controles que configuran algo."""
    box = Gtk.Box(spacing=8)
    box.add_css_class("afm-help")
    icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
    icon.set_valign(Gtk.Align.START)
    icon.add_css_class("afm-help-icon")
    lab = Gtk.Label(label=text, xalign=0, wrap=True, hexpand=True)
    lab.add_css_class("afm-help-text")
    box.append(icon)
    box.append(lab)
    parent.append(box)
