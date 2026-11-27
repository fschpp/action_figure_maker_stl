"""Formas del retoque de color (línea, círculo, cuadrado), sin GTK ni GL.

Se arrastra sobre la pieza: el primer punto es el origen `a` y el punto actual es `b` (ambos sobre la superficie). Borde y relleno llevan el mismo
color (el rol elegido), así que cada forma sale **rellena**:

* **línea**: de `a` a `b`, con el grosor del pincel;
* **círculo**: centro en `a`, radio `|b − a|` (una sola pincelada grande, tope `MAX_RADIUS_MM`);
* **cuadrado**: centro en `a`, alineado con la pantalla; `b` fija la mitad del lado (`max(|Δu|, |Δv|)`, como un cuadrado que se estira desde el centro).

Las formas planas (línea, cuadrado) se definen en el plano tangente en `a` (base `u`, `v`: «derecha» de la cámara y su perpendicular) y cada punto se
**proyecta sobre la pieza** con `project(P, n, tol) -> (impacto, normal) | None`, así que la forma sigue la superficie; lo que no cae sobre ella se omite.
"""
from __future__ import annotations

import numpy as np

from ..retouch import MAX_RADIUS_MM, MIN_RADIUS_MM

LINE, CIRCLE, SQUARE = "linea", "circulo", "cuadrado"
SHAPES = (LINE, CIRCLE, SQUARE)
STEP_FACTOR = 0.5            # separación entre pinceladas del borde, en radios (igual que el pincel)
MAX_RING_POINTS = 150        # tope de rayos por lado de una forma grande (cada punto es un rayo contra la malla)
MIN_PROJECT_TOL_MM = 4.0     # cuánto puede alejarse la superficie del plano tangente y seguir contando
CIRCLE_SEGMENTS = 48


def tangent_basis(n, right) -> tuple[np.ndarray, np.ndarray]:
    """`(u, v)` ortonormales en el plano de normal `n`: `u` es la «derecha» de la cámara proyectada en el plano; `v = n × u`."""
    n = np.asarray(n, float)
    n = n / np.linalg.norm(n)
    u = np.asarray(right, float) - (np.asarray(right, float) @ n) * n
    if np.linalg.norm(u) < 1e-6:                                  # se mira la superficie de canto: cualquier eje sirve
        u = np.cross(n, (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0))
    u = u / np.linalg.norm(u)
    return u, np.cross(n, u)


def clip_radius(r: float) -> float:
    return float(min(max(r, MIN_RADIUS_MM), MAX_RADIUS_MM))


def half_side(a, b, u, v) -> float:
    d = np.asarray(b, float) - np.asarray(a, float)
    return float(max(abs(d @ u), abs(d @ v)))


def radius_of(a, b) -> float:
    return float(np.linalg.norm(np.asarray(b, float) - np.asarray(a, float)))


def outline(kind: str, a, b, n, u, v) -> np.ndarray:
    """Contorno de la forma como segmentos `(E, 2, 3)` en el plano tangente (para mostrarlo mientras se arrastra)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if kind == LINE:
        return np.array([[a, b]])
    if kind == CIRCLE:
        r = radius_of(a, b)
        t = np.linspace(0.0, 2 * np.pi, CIRCLE_SEGMENTS + 1)
        ring = a + r * (np.cos(t)[:, None] * u + np.sin(t)[:, None] * v)
        return np.stack([ring[:-1], ring[1:]], axis=1)
    if kind == SQUARE:
        h = half_side(a, b, u, v)
        c = [a + h * (sx * u + sy * v) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        return np.array([[c[i], c[(i + 1) % 4]] for i in range(4)])
    raise ValueError(f"forma desconocida: {kind!r}")


def _edge_points(p, q, step: float, cap: int = MAX_RING_POINTS) -> list[np.ndarray]:
    p, q = np.asarray(p, float), np.asarray(q, float)
    length = float(np.linalg.norm(q - p))
    k = max(1, min(int(np.ceil(length / max(step, 1e-6))), cap))
    return [p + (q - p) * (i / k) for i in range(k + 1)]


def samples(kind: str, a, b, n, u, v, r: float, project) -> list[tuple]:
    """Las pinceladas `(centro, radio, normal)` que dibujan la forma, ya sobre la superficie. `r` es el grosor del pincel (mm).
    `project(P, n, tol)` devuelve `(impacto, normal)` o `None` si ahí no hay pieza."""
    a, b, n = np.asarray(a, float), np.asarray(b, float), np.asarray(n, float)
    r = clip_radius(r)
    if kind == CIRCLE:
        return [(tuple(a), clip_radius(radius_of(a, b)), tuple(n))]
    if kind == LINE:
        tol = max(MIN_PROJECT_TOL_MM, radius_of(a, b))
        pts = _edge_points(a, b, r * STEP_FACTOR)
    elif kind == SQUARE:
        h = half_side(a, b, u, v)
        tol = max(MIN_PROJECT_TOL_MM, 2 * h)
        pts = []
        ring = max(h - r, 0.0)                                    # el anillo del borde va metido `r`: así la forma termina justo en `h`
        corners = [a + ring * (sx * u + sy * v) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        step = max(r * STEP_FACTOR, 8.0 * h / MAX_RING_POINTS)
        for i in range(4):
            pts += _edge_points(corners[i], corners[(i + 1) % 4], step)
        rf = clip_radius(max(r, h / 3.0))                         # el relleno usa bolas más grandes (pocos rayos); tocan el borde sin pasarse
        inner = max(h - rf, 0.0)
        k = max(1, int(np.ceil(2 * inner / (rf * 1.4)))) if inner > 0 else 0
        axis = np.linspace(-inner, inner, k + 1) if k else np.array([0.0])
        fill = [a + x * u + y * v for x in axis for y in axis]
    else:
        raise ValueError(f"forma desconocida: {kind!r}")
    out = []
    for p in pts:
        hit = project(p, n, tol)
        if hit is not None:
            out.append((tuple(hit[0]), r, tuple(hit[1])))
    if kind == SQUARE:
        for p in fill:
            hit = project(p, n, tol)
            if hit is not None:
                out.append((tuple(hit[0]), rf, tuple(hit[1])))
    return out
