"""Barra de progreso con porcentaje, tarea en curso, tareas pendientes y tiempo estimado (compartida por la ventana
principal y «Armar desde catálogo»). La cuenta está en `afm.progress`; esto solo la dibuja."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from ..progress import ProgressTracker, describe, fmt_duration  # noqa: E402

class ProgressPanel(Gtk.Box):
    """Oculta hasta `start(...)`. Sin plan de pasos (`stages` vacío) la barra solo late y muestra el cronómetro."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, visible=False)
        self.bar = Gtk.ProgressBar(show_text=True)
        self.lbl_now = Gtk.Label(xalign=0, wrap=True)
        self.lbl_now.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.lbl_pending = Gtk.Label(xalign=0, wrap=True)
        self.lbl_pending.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.lbl_pending.add_css_class("dim-label")
        for w in (self.bar, self.lbl_now, self.lbl_pending):
            self.append(w)
        for side in ("start", "end", "top", "bottom"):
            getattr(self, f"set_margin_{side}")(4)
        self._tracker: ProgressTracker | None = None
        self._timer = 0
        self._t0 = 0.0

    # ---------------------------------------------------------------- ciclo
    def start(self, stages=None, text: str = "Preparando…") -> None:
        import time

        self._t0 = time.monotonic()
        self._tracker = ProgressTracker(stages) if stages else None
        self.bar.set_fraction(0.0)
        self.bar.set_text("0 %" if self._tracker else "Trabajando…")
        self.lbl_now.set_text(text)
        self.lbl_pending.set_text("")
        self.set_visible(True)
        if not self._timer:
            self._timer = GLib.timeout_add(250, self._tick)

    def feed(self, text: str) -> None:
        """Mensaje del motor (se puede llamar desde el hilo de trabajo: el cálculo no toca widgets)."""
        if self._tracker is not None:
            self._tracker.feed(text)
        else:
            self._last = str(text)

    def finish(self, ok: bool = True, text: str = "") -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._tracker is not None and ok:
            self._tracker.finish(text or "Listo")
            self._render()
        elif not ok:
            self.bar.set_text(f"Falló · {fmt_duration(self._elapsed())}")
            self.lbl_pending.set_text("")
        else:
            self.bar.set_fraction(1.0)
            self.bar.set_text(f"Terminado en {fmt_duration(self._elapsed())}")
            self.lbl_pending.set_text("")

    def hide_panel(self) -> None:
        self.finish(True)
        self.set_visible(False)

    # ---------------------------------------------------------------- dibujo
    def _elapsed(self) -> float:
        import time

        return time.monotonic() - self._t0

    def _tick(self) -> bool:
        if not self._timer:
            return False
        self._render()
        return True

    def _render(self) -> None:
        if self._tracker is None:
            self.bar.pulse()
            self.bar.set_text(f"Trabajando… {fmt_duration(self._elapsed())}")
            last = getattr(self, "_last", "")
            if last:
                self.lbl_now.set_text(f"Ahora: {last}")
            return
        snap = self._tracker.snapshot()
        text, now, pend = describe(snap)
        self.bar.set_fraction(snap.fraction)
        self.bar.set_text(text)
        self.lbl_now.set_text(now)
        self.lbl_pending.set_text(pend)
