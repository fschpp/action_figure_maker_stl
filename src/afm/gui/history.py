"""Historial de deshacer / rehacer del proyecto (UX etapa 1c).

Guarda instantáneas de texto (el JSON del proyecto, el mismo que se guarda en disco), sin depender de GTK ni del
controlador, así que se prueba sola. La firma y la instantánea son la misma cadena: comparar dos estados es comparar
dos textos.

Qué entra: solo lo que está en `Project` (planos, siluetas, recortes, parámetros, piezas de catálogo…). Lo que no se
guarda en el proyecto (la selección, los planos ocultos, la cámara) no se deshace.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Snapshot:
    text: str            # JSON del proyecto
    label: str           # qué se hizo para llegar a este estado (mensaje del controlador)


class History:
    def __init__(self, limit: int = 50, max_bytes: int = 64 * 1024 * 1024):
        self.limit = limit
        self.max_bytes = max_bytes          # tope de memoria: con proyectos grandes quedan menos pasos, no más RAM
        self._undo: list[Snapshot] = []     # estados anteriores, el más viejo primero
        self._redo: list[Snapshot] = []
        self.current: Snapshot | None = None

    # ------------------------------------------------------------------ estado
    def reset(self, text: str | None, label: str = "") -> None:
        """Empieza de cero (se abrió otra figura / proyecto)."""
        self._undo.clear()
        self._redo.clear()
        self.current = None if text is None else Snapshot(text, label)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str | None:
        return self.current.label if self._undo and self.current is not None else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def _trim(self) -> None:
        while len(self._undo) > self.limit:
            self._undo.pop(0)
        total = sum(len(s.text) for s in self._undo)
        while self._undo and total > self.max_bytes:
            total -= len(self._undo.pop(0).text)

    # ---------------------------------------------------------------- registro
    def observe(self, text: str, label: str = "") -> bool:
        """Registra el estado actual si cambió respecto del último. Devuelve True si hubo un paso nuevo."""
        if self.current is None:
            self.current = Snapshot(text, label)
            return False
        if text == self.current.text:
            return False
        self._undo.append(self.current)
        self._redo.clear()                      # un cambio nuevo cancela lo que se podía rehacer
        self.current = Snapshot(text, label)
        self._trim()
        return True

    # ------------------------------------------------------- deshacer / rehacer
    def undo(self, now: str) -> Snapshot | None:
        """Estado anterior (None si no hay). `now` es el estado real de hoy: si tiene cambios sin registrar, se registran antes."""
        self.observe(now)
        if not self._undo or self.current is None:
            return None
        self._redo.append(self.current)
        self.current = self._undo.pop()
        return self.current

    def redo(self, now: str) -> Snapshot | None:
        if self.current is not None and now != self.current.text:
            self.observe(now)                   # hay un cambio sin registrar: ya no hay nada que rehacer
        if not self._redo:
            return None
        if self.current is not None:
            self._undo.append(self.current)
        self.current = self._redo.pop()
        self._trim()
        return self.current

    def settle(self, text: str) -> None:
        """Tras restaurar: el estado real (re-serializado) pasa a ser el actual sin tocar las pilas."""
        if self.current is not None:
            self.current = Snapshot(text, self.current.label)
