"""Números dibujados con líneas (sin texto de GL): para rotular los cortes en el visor 3D.

Cada dígito es un trazo continuo en una caja de 1 × 2; `number_paths` los pone en 3D mirando a la cámara.
"""
from __future__ import annotations

import numpy as np

_D = {
    "0": [(0, 0), (1, 0), (1, 2), (0, 2), (0, 0)],
    "1": [(0.15, 1.6), (0.5, 2), (0.5, 0)],
    "2": [(0, 2), (1, 2), (1, 1), (0, 1), (0, 0), (1, 0)],
    "3": [(0, 2), (1, 2), (1, 1), (0.3, 1), (1, 1), (1, 0), (0, 0)],
    "4": [(0, 2), (0, 1), (1, 1), (1, 2), (1, 1), (1, 0)],
    "5": [(1, 2), (0, 2), (0, 1), (1, 1), (1, 0), (0, 0)],
    "6": [(1, 2), (0, 2), (0, 0), (1, 0), (1, 1), (0, 1)],
    "7": [(0, 2), (1, 2), (0.4, 0)],
    "8": [(0, 0), (1, 0), (1, 2), (0, 2), (0, 0), (0, 1), (1, 1)],
    "9": [(1, 1), (0, 1), (0, 2), (1, 2), (1, 0), (0, 0)],
}
ADVANCE = 1.5           # separación entre dígitos (en cajas de ancho 1)


def number_paths(n: int, origin, right, up, height: float) -> list[np.ndarray]:
    """Un trazo (N×3) por dígito de `n`, con su esquina inferior izquierda en `origin`, escrito a lo largo de `right`
    y hacia arriba por `up` (vectores unitarios) y de `height` mm de alto."""
    k = height / 2.0
    origin, right, up = (np.asarray(v, float) for v in (origin, right, up))
    out = []
    for i, ch in enumerate(str(int(n))):
        pts = np.array(_D[ch], float)
        out.append(origin + (pts[:, :1] + i * ADVANCE) * k * right + pts[:, 1:] * k * up)
    return out


def camera_axes(eye, target) -> tuple[np.ndarray, np.ndarray]:
    """(derecha, arriba) de la cámara que mira de `eye` a `target`, con Z arriba (como `camera.look_at`)."""
    d = np.asarray(target, float) - np.asarray(eye, float)
    d = d / max(np.linalg.norm(d), 1e-12)
    right = np.cross(d, (0.0, 0.0, 1.0))
    if np.linalg.norm(right) < 1e-6:           # mirando justo desde arriba o abajo
        right = np.array([1.0, 0.0, 0.0])
    right = right / np.linalg.norm(right)
    up = np.cross(right, d)
    return right, up / np.linalg.norm(up)
