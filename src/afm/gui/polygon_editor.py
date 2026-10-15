"""Edición de la silueta (polígono) de un miembro sobre su plano. No depende de GTK ni de OpenGL.

El polígono vive en coordenadas (u, v) del plano del miembro (`Plane.to_uv` / `to_world`). Los clics
del visor se convierten en puntos del plano intersectando el rayo de la cámara con él; para elegir un
vértice o un borde se compara en **píxeles** (el controlador proyecta los vértices con la cámara y
pasa aquí las posiciones de pantalla).

Flujo: se agregan vértices con clics hasta cerrar (clic sobre el primero o «Cerrar»). Ya cerrado, un
clic cerca de un vértice lo elige y el siguiente clic lo mueve; un clic sobre un borde inserta un
vértice; Retroceso borra el elegido.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..planes import Plane

VERTEX_PX = 12.0        # radio de captura de un vértice, en píxeles
EDGE_PX = 8.0           # distancia máxima a un borde para insertar un vértice
MIN_AREA = 1e-3         # mm²


def ray_plane_uv(plane: Plane, origin, direction, min_cos: float = 0.02) -> np.ndarray | None:
    """Punto (u, v) donde el rayo corta el plano, o None si es casi paralelo (plano de canto) o queda
    detrás de la cámara."""
    o, d = np.asarray(origin, float), np.asarray(direction, float)
    denom = float(d @ plane.normal)
    if abs(denom) < min_cos:
        return None
    t = float((plane.origin - o) @ plane.normal) / denom
    if t <= 0:
        return None
    return plane.to_uv(o + t * d)[0]


def _dist_point_segment(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    n2 = float(ab @ ab)
    t = 0.0 if n2 < 1e-12 else float(np.clip((p - a) @ ab / n2, 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab)))


@dataclass
class PolygonDraft:
    plane: Plane
    pts: list[np.ndarray] = field(default_factory=list)      # vértices (u, v)
    closed: bool = False
    selected: int | None = None

    # ------------------------------------------------------------ conversión
    @classmethod
    def from_world(cls, plane: Plane, polygon_world) -> "PolygonDraft":
        uv = plane.to_uv(polygon_world)
        return cls(plane, [p.copy() for p in uv], closed=len(uv) >= 3)

    def world(self) -> np.ndarray:
        if not self.pts:
            return np.zeros((0, 3))
        return self.plane.to_world(np.array(self.pts))

    # -------------------------------------------------------------- validez
    def validity_error(self) -> str | None:
        """None si el polígono sirve para extruir; si no, el motivo."""
        from shapely.geometry import Polygon

        if len(self.pts) < 3:
            return "necesita al menos 3 vértices"
        pg = Polygon(np.array(self.pts))
        if not pg.is_valid:
            return "se cruza consigo mismo"
        if pg.area < MIN_AREA:
            return "tiene área casi nula"
        return None

    # ---------------------------------------------------------- edición
    def add(self, uv) -> None:
        self.pts.append(np.asarray(uv, float).copy())

    def close(self) -> bool:
        if len(self.pts) < 3:
            return False
        self.closed, self.selected = True, None
        return True

    def move(self, i: int, uv) -> None:
        self.pts[i] = np.asarray(uv, float).copy()

    def insert(self, edge: int, uv) -> None:
        """Inserta un vértice en el borde `edge` (entre el vértice `edge` y el siguiente)."""
        self.pts.insert(edge + 1, np.asarray(uv, float).copy())
        self.selected = None

    def delete(self, i: int) -> None:
        del self.pts[i]
        self.selected = None

    def clear(self) -> None:
        self.pts, self.closed, self.selected = [], False, None

    # ------------------------------------------------------- selección (px)
    def nearest_vertex(self, screen: np.ndarray, click, radius: float = VERTEX_PX) -> int | None:
        if len(screen) == 0:
            return None
        d = np.linalg.norm(np.asarray(screen)[:, :2] - np.asarray(click, float), axis=1)
        k = int(np.argmin(d))
        return k if d[k] <= radius else None

    def nearest_edge(self, screen: np.ndarray, click, radius: float = EDGE_PX) -> int | None:
        n = len(screen)
        if n < 2:
            return None
        s, c = np.asarray(screen, float)[:, :2], np.asarray(click, float)
        count = n if self.closed else n - 1
        best, best_d = None, radius
        for i in range(count):
            dist = _dist_point_segment(c, s[i], s[(i + 1) % n])
            if dist <= best_d:
                best, best_d = i, dist
        return best
