"""Despliegue UV base «una celda por triángulo» (plan §6, §7.5, T10, decisión D9).

Cada triángulo de la pieza se aplana de forma isométrica y recibe una celda propia del atlas, de modo que NO hay solapes por
construcción y no hace falta ningún algoritmo de cartas ni `xatlas`. A cambio el atlas aprovecha menos el espacio (se mide en T16
y recién ahí se decide T20, el despliegue por cartas).

Convenciones (plan §3.1 y §6): coordenadas continuas de píxel con x hacia la derecha e y hacia abajo; el centro del píxel
(fila r, columna c) es (c + 0,5, r + 0,5); `uv = (x / ancho, 1 − y / alto)`.

`trimesh` se importa de forma perezosa (D10); la malla se usa por sus arreglos `vertices` y `faces`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Giro cíclico de las esquinas para que la arista más larga (índice e) quede como base del aplanado.
_ROT = np.array([[0, 1, 2], [1, 2, 0], [2, 0, 1]], np.int64)
FILL_TARGET = 0.7          # aprovechamiento supuesto del atlas al elegir el ancho
MAX_RETRIES = 6


@dataclass
class Unwrapped:
    vertices: np.ndarray       # (n, 3) float64: la pieza (posiblemente subdividida; misma geometría)
    faces: np.ndarray          # (m, 3) int32
    src_face: np.ndarray       # (m,) int32: cara de la pieza original de cada cara
    tri_px: np.ndarray         # (m, 3, 2) float32: coordenadas continuas de píxel (x der, y abajo)
    uv: np.ndarray             # (m, 3, 2) float32 = (x / ancho, 1 − y / alto)
    size: tuple                # (ancho, alto)
    texel_mm: float            # efectivo (puede ser mayor que el pedido si el atlas no entraba)
    cells: np.ndarray | None = None    # (m, 4) int32: (x, y, ancho, alto) de la celda de cada cara, en píxeles


# ------------------------------------------------------------------ subdivisión
def _subdivide(mesh, max_edge_mm: float):
    """Devuelve (vertices, faces, src_face). Sin nada que subdividir, `src_face = arange`."""
    V = np.asarray(mesh.vertices, float)
    F = np.asarray(mesh.faces, np.int64)
    e = np.stack([np.linalg.norm(V[F[:, 1]] - V[F[:, 0]], axis=1),
                  np.linalg.norm(V[F[:, 2]] - V[F[:, 1]], axis=1),
                  np.linalg.norm(V[F[:, 0]] - V[F[:, 2]], axis=1)], axis=1)
    if len(F) == 0 or e.max() <= max_edge_mm:
        return V, F.astype(np.int32), np.arange(len(F), dtype=np.int32)
    from trimesh.remesh import subdivide_to_size

    v2, f2, idx = subdivide_to_size(V, F, max_edge_mm, return_index=True)
    return np.asarray(v2, float), np.asarray(f2, np.int32), np.asarray(idx, np.int32)


# ------------------------------------------------------------------ aplanado isométrico
def _flatten(V: np.ndarray, F: np.ndarray):
    """Aplana cada triángulo con su arista MÁS LARGA como base (sobre +x): el tercer vértice cae entre 0 y L, así la caja
    mide L × h con h ≤ L·√3/2 y ninguna celda se pasa del tamaño que se esperó al subdividir.

    Devuelve `flat` (m, 3, 2) en mm, en el orden de las esquinas de `F` (x a lo largo de la base, y ≥ 0), y `w`, `h` (mm).
    """
    T = V[F]                                                        # (m, 3, 3)
    ed = np.stack([np.linalg.norm(T[:, 1] - T[:, 0], axis=1),
                   np.linalg.norm(T[:, 2] - T[:, 1], axis=1),
                   np.linalg.norm(T[:, 0] - T[:, 2], axis=1)], axis=1)
    e = np.argmax(ed, axis=1)                                       # la base es la arista e: (e, e+1)
    order = _ROT[e]                                                 # (m, 3): esquina que hace de p, q, r
    P = np.take_along_axis(T, order[:, :, None], axis=1)
    p, q, r = P[:, 0], P[:, 1], P[:, 2]
    e01, e02 = q - p, r - p
    L = np.linalg.norm(e01, axis=1)
    cr = np.linalg.norm(np.cross(e01, e02), axis=1)                 # 2·área
    Ls = np.where(L > 1e-12, L, 1.0)
    x2 = np.where(L > 1e-12, np.einsum("ij,ij->i", e02, e01) / Ls, 0.0)
    y2 = np.where(L > 1e-12, cr / Ls, np.linalg.norm(e02, axis=1))
    x2 = np.clip(x2, 0.0, np.maximum(L, 1e-12))
    flat_pqr = np.stack([np.zeros((len(F), 2)), np.column_stack([L, np.zeros(len(F))]), np.column_stack([x2, y2])], axis=1)
    flat = np.empty_like(flat_pqr)
    np.put_along_axis(flat, order[:, :, None].repeat(2, axis=2), flat_pqr, axis=1)   # de vuelta al orden de las esquinas
    return flat, L, y2


# ------------------------------------------------------------------ empaquetado por estantes
def _shelf_pack(w: np.ndarray, h: np.ndarray, width: int):
    """Estantes de izquierda a derecha, celdas por alto decreciente. Devuelve (x, y, alto_total)."""
    m = len(w)
    order = np.lexsort((np.arange(m), -h))          # alto decreciente; empate: el índice (determinista)
    xs = np.zeros(m, np.int64)
    ys = np.zeros(m, np.int64)
    cx = cy = 0
    shelf_h = 0
    wl, hl = w[order].tolist(), h[order].tolist()
    for k, i in enumerate(order.tolist()):
        wi, hi = wl[k], hl[k]
        if cx + wi > width:                          # no entra en el estante: abrir uno nuevo
            cy += shelf_h
            cx, shelf_h = 0, 0
        xs[i], ys[i] = cx, cy
        cx += wi
        if hi > shelf_h:
            shelf_h = hi
    return xs, ys, cy + shelf_h


def _pow2_at_least(n: float) -> int:
    k = 1
    while k < n:
        k *= 2
    return k


def unwrap_triangles(mesh, texel_mm: float = 0.2, margin_px: int = 2, max_cell_px: int = 64, max_atlas: int = 4096,
                     max_edge_mm: float | None = None) -> Unwrapped:
    """Despliega `mesh` con una celda por triángulo.

    1. Subdivide las aristas mayores que `max_edge_mm` (por defecto `0,9 · max_cell_px · texel_mm`); la geometría no cambia.
    2. Aplana cada triángulo de forma isométrica y le da una celda `ceil(lado_mm / texel) · s + 2·margin_px`, con
       `s = min(1, max_cell_px / lado_mayor_px)`.
    3. Empaqueta por estantes en un atlas de ancho potencia de 2 (≥ √(Σ áreas / 0,7), tope `max_atlas`) y alto múltiplo de 4. Si no
       entra en `max_atlas`, agranda `texel_mm` por `1,15 · √(área_usada / área_máx)` y reintenta (hasta 6 veces; luego `ValueError`).
    """
    if texel_mm <= 0:
        raise ValueError("texel_mm debe ser positivo")
    if max_edge_mm is None:
        max_edge_mm = 0.9 * max_cell_px * texel_mm
    V, F, src_face = _subdivide(mesh, float(max_edge_mm))
    if len(F) == 0:
        raise ValueError("la pieza no tiene caras")
    flat, w_mm, h_mm = _flatten(V, F)
    m = len(F)
    m_px = int(margin_px)
    texel = float(texel_mm)
    for _intento in range(MAX_RETRIES + 1):
        wpx0 = np.maximum(1, np.ceil(w_mm / texel - 1e-9)).astype(np.int64)
        hpx0 = np.maximum(1, np.ceil(h_mm / texel - 1e-9)).astype(np.int64)
        s = np.minimum(1.0, max_cell_px / np.maximum(wpx0, hpx0))            # (m,)
        cw = np.maximum(1, np.ceil(wpx0 * s - 1e-9)).astype(np.int64) + 2 * m_px
        ch = np.maximum(1, np.ceil(hpx0 * s - 1e-9)).astype(np.int64) + 2 * m_px
        total = float((cw * ch).sum())
        width = min(int(max_atlas), _pow2_at_least(max(np.sqrt(total / FILL_TARGET), int(cw.max()))))
        if cw.max() > width:                                                   # ni una celda cabe a lo ancho
            texel *= 1.15 * float(np.sqrt(cw.max() / width))
            continue
        xs, ys, hh = _shelf_pack(cw, ch, width)
        height = int(-(-hh // 4) * 4)
        if height <= max_atlas:
            break
        texel *= 1.15 * float(np.sqrt(total / (width * max_atlas)))
    else:
        raise ValueError(f"la pieza no entra en un atlas de {max_atlas}×{max_atlas} ni con texel de {texel:.3f} mm")
    k = (s / texel)[:, None, None]                                             # píxeles por mm de cada celda
    px = np.empty((m, 3, 2), np.float64)
    px[:, :, 0] = xs[:, None] + m_px + flat[:, :, 0] * k[:, :, 0]
    px[:, :, 1] = ys[:, None] + m_px + flat[:, :, 1] * k[:, :, 0]
    uv = np.empty_like(px)
    uv[:, :, 0] = px[:, :, 0] / width
    uv[:, :, 1] = 1.0 - px[:, :, 1] / height
    cells = np.column_stack([xs, ys, cw, ch]).astype(np.int32)
    return Unwrapped(V, F, src_face, px.astype(np.float32), uv.astype(np.float32), (int(width), int(height)), float(texel), cells)


# ------------------------------------------------------------------ verificación
def check_no_overlap(unw: Unwrapped) -> int:
    """Cantidad de píxeles cubiertos por más de una celda (0 = bien). Sin `cells`, usa la caja de `tri_px`."""
    W, H = unw.size
    if unw.cells is not None:
        c = np.asarray(unw.cells, np.int64)
        x0, y0, x1, y1 = c[:, 0], c[:, 1], c[:, 0] + c[:, 2], c[:, 1] + c[:, 3]
    else:
        t = np.asarray(unw.tri_px, np.float64)
        x0 = np.floor(t[:, :, 0].min(axis=1)).astype(np.int64)
        x1 = np.ceil(t[:, :, 0].max(axis=1)).astype(np.int64)
        y0 = np.floor(t[:, :, 1].min(axis=1)).astype(np.int64)
        y1 = np.ceil(t[:, :, 1].max(axis=1)).astype(np.int64)
    if (x0 < 0).any() or (y0 < 0).any() or (x1 > W).any() or (y1 > H).any():
        raise ValueError("hay celdas fuera del atlas")
    # arreglo de diferencias 2D: una suma acumulada en cada eje da la cobertura de cada píxel
    stride = W + 1
    n = (H + 1) * stride
    d = (np.bincount(y0 * stride + x0, minlength=n) - np.bincount(y0 * stride + x1, minlength=n)
         - np.bincount(y1 * stride + x0, minlength=n) + np.bincount(y1 * stride + x1, minlength=n)).astype(np.int32)
    cov = d.reshape(H + 1, stride).cumsum(axis=0).cumsum(axis=1)[:H, :W]
    return int((cov > 1).sum())
