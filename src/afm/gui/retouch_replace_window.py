"""Diálogo «Reemplazar color de la pieza» del modo «Retocar color…».

Dos selectores de color de la paleta (grillas de muestras, no desplegables): «color a buscar:» y «reemplazar por:». «Previsualizar» muestra el
reemplazo en la figura sin guardarlo; «Aceptar» lo suma al retoque (un solo paso de deshacer) y cierra; «Cancelar» (o cerrar la ventana) cierra sin
cambiar nada y, si había una previsualización, la ventana de catálogo vuelve al color que se veía antes.

Solo arma widgets: la lógica (validar, calcular la vista previa, sumar la pincelada) la hace `CatalogWindow` con `RetouchSession.preview_replace` /
`add_replace`.
"""
from __future__ import annotations

import itertools

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..i18n import tr  # noqa: E402
from .retouch_toolbox import PALETTE_COLUMNS, _load_css, _tint  # noqa: E402

_uid = itertools.count()


class SwatchPicker(Gtk.Box):
    """Grilla de muestras de la paleta; una está elegida. `on_changed(rol_id)` avisa cuando el usuario toca otra."""

    def __init__(self, roles: list[tuple[str, str, str]], selected: int = 0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._roles = list(roles)
        self._buttons: list[Gtk.Button] = []
        self.index = min(max(selected, 0), max(len(self._roles) - 1, 0))
        self.on_changed = None
        grid = Gtk.Grid(row_spacing=2, column_spacing=2, halign=Gtk.Align.START)
        uid = next(_uid)
        for i, (rid, name, hx) in enumerate(self._roles):
            b = Gtk.Button()
            b.add_css_class("rt-swatch")
            _tint(b, hx, f"rt-rp-{uid}-{i}")
            b.set_tooltip_text(name if name == rid else f"{name} ({rid})")
            b.connect("clicked", self._on_click, i)
            grid.attach(b, i % PALETTE_COLUMNS, i // PALETTE_COLUMNS, 1, 1)
            self._buttons.append(b)
        self.append(grid)
        self.lbl = Gtk.Label(xalign=0)
        self.lbl.add_css_class("dim-label")
        self.append(self.lbl)
        self._sync()

    @property
    def role(self) -> str | None:
        return self._roles[self.index][0] if self._roles else None

    def _on_click(self, _btn, i: int) -> None:
        self.index = i
        self._sync()
        if self.on_changed is not None:
            self.on_changed(self.role)

    def _sync(self) -> None:
        for i, b in enumerate(self._buttons):
            (b.add_css_class if i == self.index else b.remove_css_class)("rt-selected")
        if self._roles:
            self.lbl.set_text(self._roles[self.index][1])


class ReplaceColorDialog(Gtk.Window):
    """Ventana modal. Callbacks: `on_preview(desde, a)`, `on_accept(desde, a)`, `on_cancel()` (una sola vez, también al cerrar con la X)."""

    def __init__(self, parent: Gtk.Window, roles: list[tuple[str, str, str]], on_preview, on_accept, on_cancel):
        super().__init__(title=tr("catalog_window.reemplazar_titulo"), transient_for=parent, modal=True, resizable=False, destroy_with_parent=True)
        _load_css()
        self._on_preview, self._on_accept, self._on_cancel = on_preview, on_accept, on_cancel
        self._finished = False
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_child(root)
        self.pick_from = SwatchPicker(roles, 0)
        self.pick_to = SwatchPicker(roles, 1 if len(roles) > 1 else 0)
        root.append(Gtk.Label(label=tr("catalog_window.reemplazar_buscar"), xalign=0))
        root.append(self.pick_from)
        root.append(Gtk.Label(label=tr("catalog_window.reemplazar_por"), xalign=0))
        root.append(self.pick_to)
        self.lbl_msg = Gtk.Label(xalign=0, wrap=True)
        self.lbl_msg.add_css_class("dim-label")
        root.append(self.lbl_msg)
        bar = Gtk.Box(spacing=6)
        self.btn_preview = Gtk.Button(label=tr("catalog_window.reemplazar_previsualizar"))
        self.btn_preview.set_tooltip_text(tr("catalog_window.reemplazar_previsualizar_tip"))
        self.btn_cancel = Gtk.Button(label=tr("catalog_window.reemplazar_cancelar"))
        self.btn_accept = Gtk.Button(label=tr("catalog_window.reemplazar_aceptar"))
        self.btn_accept.add_css_class("suggested-action")
        bar.append(self.btn_preview)
        bar.append(Gtk.Box(hexpand=True))
        bar.append(self.btn_cancel)
        bar.append(self.btn_accept)
        root.append(bar)
        self.btn_preview.connect("clicked", lambda *_: self._on_preview(*self.choice()))
        self.btn_accept.connect("clicked", self._accept)
        self.btn_cancel.connect("clicked", lambda *_: self._cancel())
        self.connect("close-request", self._on_close)
        self.pick_from.on_changed = self.pick_to.on_changed = lambda *_: self._sync()
        self._sync()

    def choice(self) -> tuple[str | None, str | None]:
        return self.pick_from.role, self.pick_to.role

    def set_message(self, text: str) -> None:
        self.lbl_msg.set_text(text)

    def _sync(self) -> None:
        a, b = self.choice()
        ok = a is not None and b is not None and a != b
        self.btn_preview.set_sensitive(ok)
        self.btn_accept.set_sensitive(ok)
        self.lbl_msg.set_text("" if ok else tr("catalog_window.reemplazar_iguales"))

    def _accept(self, *_) -> None:
        if self._finished:
            return
        self._finished = True
        a, b = self.choice()
        self._on_accept(a, b)
        self.close()

    def _cancel(self) -> None:
        if not self._finished:
            self._finished = True
            self._on_cancel()
        self.close()

    def _on_close(self, *_) -> bool:
        if not self._finished:
            self._finished = True
            self._on_cancel()
        return False
