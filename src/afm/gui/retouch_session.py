"""Controlador del retoque de color de una pieza (`PLAN_retoque_color_pieza.md`, R06).

Lógica del modo «Retocar» de la ventana de Catálogo, **sin GTK ni GL**: qué rol se pinta, el radio, pintar o borrar, cómo un arrastre del
mouse se convierte en pinceladas separadas ≈ radio/2, la pila de deshacer/rehacer (un trazo = una entrada) y escribir el resultado en el
catálogo. La ventana (R07) solo traduce el mouse a `begin_stroke` / `add_point` / `end_stroke` con el punto y la normal que da el *picking*.

Todo está en el marco del STL de la pieza (mm), el mismo de `Stroke`. Sin PIL ni scipy al importar (R3).
"""
from __future__ import annotations

from ..retouch import MAX_RADIUS_MM, MAX_STROKES, MIN_RADIUS_MM, Retouch, RetouchError, Stroke

PAINT, ERASE = "pintar", "borrar"
DEFAULT_RADIUS_MM = 1.5
SPACING_FACTOR = 0.5            # separación entre pinceladas de un mismo trazo, en radios (plan §4)


class RetouchSession:
    def __init__(self, retouch: Retouch | None, palette_ids, *, radius: float = DEFAULT_RADIUS_MM):
        self._palette = list(palette_ids)
        self._saved = list((retouch.strokes if retouch is not None else []))      # lo que hay en disco (para «descartar» y «sin cambios»)
        self._strokes: list[Stroke] = list(self._saved)
        self._undo: list[int] = []         # cantidad de pinceladas de cada trazo hecho (el último, arriba)
        self._redo: list[list[Stroke]] = []
        self._open: list[Stroke] | None = None        # trazo en curso
        self._last_c = None
        self.mode = PAINT
        self.role: str | None = self._palette[0] if self._palette else None
        self.radius = float(radius)
        self.truncated = False            # el último trazo se cortó porque la pieza llegó al máximo de pinceladas

    # ------------------------------------------------------------------ herramienta
    @property
    def palette_ids(self) -> list[str]:
        return list(self._palette)

    def set_role(self, role: str) -> None:
        if role not in self._palette:
            raise RetouchError(f"el rol «{role}» no está en la paleta de la línea")
        self.role = role
        self.mode = PAINT

    def set_mode(self, mode: str) -> None:
        if mode not in (PAINT, ERASE):
            raise RetouchError(f"modo desconocido: {mode!r}")
        self.mode = mode

    def set_radius(self, r: float) -> float:
        """Fija el radio recortado al rango válido y lo devuelve."""
        r = float(r)
        if r != r:          # NaN
            raise RetouchError("el radio no es un número")
        self.radius = min(max(r, MIN_RADIUS_MM), MAX_RADIUS_MM)
        return self.radius

    @property
    def can_paint(self) -> bool:
        return self.mode == ERASE or self.role is not None

    # ------------------------------------------------------------------ trazos
    @property
    def stroking(self) -> bool:
        return self._open is not None

    def begin_stroke(self, c, n=None) -> int:
        """Empieza un trazo con una primera pincelada en `c` (marco del STL; `n` = normal de la superficie o `None`). Devuelve las pinceladas agregadas."""
        if self._open is not None:
            self.end_stroke()
        if not self.can_paint:
            raise RetouchError("no hay rol elegido para pintar")
        self._open, self._last_c, self.truncated = [], None, False
        return self.add_point(c, n)

    def add_point(self, c, n=None) -> int:
        """Suma un punto al trazo en curso: agrega pinceladas solo si se alejó ≥ radio·0,5 de la última (rellena el hueco si el mouse saltó)."""
        if self._open is None:
            raise RetouchError("no hay un trazo en curso")
        rol = self.role if self.mode == PAINT else None
        new = Stroke(c, self.radius, rol, n)             # valida c, radio y normal
        step = self.radius * SPACING_FACTOR
        pts = [new]
        if self._last_c is not None:
            d = [new.c[i] - self._last_c[i] for i in range(3)]
            dist = sum(a * a for a in d) ** 0.5
            if dist < step:
                return 0
            k = int(dist // step)
            # intermedios a lo largo del segmento, cada `step` mm (la normal se repite: son puntos del mismo arrastre)
            pts = [Stroke(tuple(self._last_c[i] + d[i] * (j * step / dist) for i in range(3)), self.radius, rol, n)
                   for j in range(1, k)] + [new]
        added = 0
        for s in pts:
            if len(self._strokes) >= MAX_STROKES:
                self.truncated = True
                break
            self._strokes.append(s)
            self._open.append(s)
            added += 1
        if added:
            self._last_c = new.c
        return added

    def end_stroke(self) -> int:
        """Cierra el trazo: queda como una entrada de la pila de deshacer. Devuelve cuántas pinceladas tenía."""
        if self._open is None:
            return 0
        n = len(self._open)
        self._open, self._last_c = None, None
        if n:
            self._undo.append(n)
            self._redo.clear()
        return n

    # ------------------------------------------------------------------ deshacer / rehacer
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if self._open is not None:
            self.end_stroke()
        if not self._undo:
            return False
        n = self._undo.pop()
        self._redo.append(self._strokes[-n:])
        del self._strokes[-n:]
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        block = self._redo.pop()
        self._strokes.extend(block)
        self._undo.append(len(block))
        return True

    def discard(self) -> bool:
        """«Descartar retoques»: vuelve a lo que hay guardado en el catálogo y vacía la pila de deshacer/rehacer. `True` si había algo que descartar."""
        changed = self.dirty
        if self._open is not None:
            self._open, self._last_c = None, None
        self._strokes = list(self._saved)
        self._undo.clear()
        self._redo.clear()
        return changed

    # ------------------------------------------------------------------ estado
    def __len__(self) -> int:
        return len(self._strokes)

    @property
    def dirty(self) -> bool:
        return self._strokes != self._saved

    def retouch(self) -> Retouch:
        """El retoque tal como está ahora (para la vista previa: `colorpreview.color_parts(..., retouch=)`)."""
        return Retouch(self._strokes)

    def roles_used(self) -> list[str]:
        return self.retouch().roles_used()

    def apply(self, catalog, ref: str):
        """«Aplicar»: escribe el retoque en el catálogo (`Catalog.set_retouch`; vacío = quitar el retoque). Si falla no cambia nada ni acá ni allá."""
        meta = catalog.set_retouch(ref, self.retouch())
        self._saved = list(self._strokes)
        return meta
