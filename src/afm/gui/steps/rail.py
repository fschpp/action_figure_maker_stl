"""Riel de pasos de la barra lateral (UX etapa 5b): una lista vertical con un ícono de estado y una línea de detalle por paso.

Reemplaza a las pestañas visibles del `Gtk.Notebook`, que sigue siendo el contenedor de las páginas (con las pestañas ocultas): así el
resto de la ventana (`tabs.set_current_page`, `_tab_for_mode`, los tests) no cambia. El riel y el Notebook se mantienen sincronizados en los
dos sentidos. El estado de cada paso sale de `gui/step_state.py`."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from ..step_state import GLYPHS, OPTIONAL, ORDER, TITLES, next_pending, step_states  # noqa: E402


def _page_of(key: str) -> int:
    """Índice de la página del Notebook para un paso: el orden de `STEPS` (el módulo se llama como la clave del paso)."""
    from . import STEPS

    return [s.__name__.rsplit(".", 1)[-1] for s in STEPS].index(key)


def build(win) -> Gtk.Widget:
    """Arma el riel y lo deja en `win.rail` / `win.rail_box`; devuelve la caja que va a la izquierda de las páginas."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.set_size_request(160, -1)
    win.rail_box = box
    win.rail = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
    win.rail.add_css_class("navigation-sidebar")
    win._rail_rows, win._rail_labels, win._rail_cache, win._rail_sync = {}, {}, {}, False
    for key in ORDER:
        if key == OPTIONAL[0]:
            sep = Gtk.ListBoxRow(selectable=False, activatable=False)
            sep.set_child(Gtk.Label(label="Opcional", xalign=0, margin_top=8, margin_start=8, css_classes=["afm-sub-title"]))
            win.rail.append(sep)
        row = Gtk.ListBoxRow()
        line = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
        glyph = Gtk.Label(label=GLYPHS["todo"], css_classes=["afm-step-glyph"], valign=Gtk.Align.START)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label=TITLES[key], xalign=0, css_classes=["afm-step-title"])
        detail = Gtk.Label(label="", xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=22,
                           css_classes=["afm-step-detail"], visible=False)
        text.append(title)
        text.append(detail)
        line.append(glyph)
        line.append(text)
        row.set_child(line)
        win.rail.append(row)
        win._rail_rows[key] = row
        win._rail_labels[key] = (glyph, detail, row)
    win.rail.connect("row-selected", lambda _lb, row: _on_row(win, row))
    win.tabs.connect("switch-page", lambda _nb, _page, num: _on_page(win, num))
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    scroller.set_child(win.rail)
    box.append(scroller)
    win.btn_next_step = Gtk.Button(label="Siguiente pendiente ▸", margin_start=4, margin_end=4, margin_bottom=4)
    win.btn_next_step.set_tooltip_text("Salta al primer paso que todavía pide algo (○ falta · ⚠ revisar · ⟳ desactualizado · ▶ listo para generar).")
    win.btn_next_step.connect("clicked", lambda *_: go_next_pending(win))
    box.append(win.btn_next_step)
    _select(win, "figura")
    return box


def _select(win, key: str) -> None:
    win._rail_sync = True
    try:
        win.rail.select_row(win._rail_rows[key])
    finally:
        win._rail_sync = False


def _key_of_row(win, row) -> str | None:
    return next((k for k, r in win._rail_rows.items() if r is row), None)


def _on_row(win, row) -> None:
    """Se eligió un paso en el riel: se muestra su página."""
    if win._rail_sync or row is None:
        return
    key = _key_of_row(win, row)
    if key is not None and win.tabs.get_current_page() != _page_of(key):
        win.tabs.set_current_page(_page_of(key))


def _on_page(win, num: int) -> None:
    """Cambió la página del Notebook (por un modo activo o por el riel): el riel marca el paso que corresponde."""
    for key in ORDER:
        if _page_of(key) == num:
            if _key_of_row(win, win.rail.get_selected_row()) != key:
                _select(win, key)
            return


def current_key(win) -> str | None:
    return _key_of_row(win, win.rail.get_selected_row())


def go_next_pending(win) -> None:
    key = next_pending(step_states(win.ctrl), current_key(win))
    if key is not None:
        _select(win, key)
        win.tabs.set_current_page(_page_of(key))


def refresh(win) -> None:
    """Actualiza el ícono y el detalle de cada paso (solo si cambiaron: se llama en cada `_refresh` de la ventana)."""
    states = step_states(win.ctrl)
    for key, st in states.items():
        cached = win._rail_cache.get(key)
        if cached == (st.state, st.detail):
            continue
        win._rail_cache[key] = (st.state, st.detail)
        glyph, detail, row = win._rail_labels[key]
        for s in GLYPHS:
            glyph.remove_css_class(f"afm-step-{s}")
        glyph.add_css_class(f"afm-step-{st.state}")
        glyph.set_text(st.glyph)
        detail.set_text(st.detail)
        detail.set_visible(bool(st.detail))
        row.set_tooltip_text(f"{st.title}: {st.detail}" if st.detail else st.title)
    win.btn_next_step.set_sensitive(next_pending(states, current_key(win)) is not None)
