"""Horneado del color de una pieza (plan §6, §7.5; T11–T13).

T11: `texel_points` rasteriza el despliegue (`Unwrapped`, T10) y lista, para cada píxel cuyo centro cae dentro de un triángulo,
la cara, las baricéntricas, el punto 3D y la normal. Todo vectorizado (sin bucle por píxel).
T12 y T13 agregan `bake_piece` (consulta de color, dilatación y políticas de relleno) en este mismo módulo.

`PIL` y `trimesh` no se importan aquí (D10).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

NO_LABEL = 255           # T45: etiqueta de «sin rol» / color plano (la misma que `palette.NO_ROLE`; no se importa para no cargar la paleta)
BARY_TOL = 1e-6          # un centro de píxel cuenta como «dentro» si min(bary) ≥ −BARY_TOL


@dataclass
class TexelGrid:
    rows: np.ndarray         # (n,) int32
    cols: np.ndarray         # (n,) int32
    face: np.ndarray         # (n,) int32: cara de `Unwrapped.faces` (la pieza subdividida)
    bary: np.ndarray         # (n, 3) float32
    points: np.ndarray       # (n, 3) float64: punto 3D del texel (en el marco de la pieza)
    normals: np.ndarray      # (n, 3) float64: normal plana de la cara (unitaria; cara degenerada: ceros)
    size: tuple              # (ancho, alto) del atlas


def _face_normals(T: np.ndarray) -> np.ndarray:
    cr = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(cr, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        n = cr / ln
    return np.where(ln > 1e-18, n, 0.0)


def texel_points(unw) -> TexelGrid:
    """Texeles cubiertos por el despliegue, con su cara, baricéntricas, punto 3D y normal.

    (1) por triángulo, los centros de píxel de la caja de `tri_px` (con `np.repeat`/`arange`); (2) baricéntricas contra `tri_px`
    y se conserva `min(bary) ≥ −1e-6`; (3) un triángulo sin ningún texel recibe el píxel que contiene su baricentro (con
    baricéntricas 1/3); (4) `points = Σ bary·vértices` y `normals` = normal plana de la cara.
    """
    W, H = unw.size
    tri = np.asarray(unw.tri_px, np.float64)                       # (m, 3, 2)
    m = len(tri)
    x, y = tri[:, :, 0], tri[:, :, 1]
    x0 = np.clip(np.floor(x.min(axis=1)).astype(np.int64), 0, W - 1)
    x1 = np.clip(np.ceil(x.max(axis=1)).astype(np.int64), x0 + 1, W)
    y0 = np.clip(np.floor(y.min(axis=1)).astype(np.int64), 0, H - 1)
    y1 = np.clip(np.ceil(y.max(axis=1)).astype(np.int64), y0 + 1, H)
    nx, ny = x1 - x0, y1 - y0
    cnt = nx * ny
    total = int(cnt.sum())
    t = np.repeat(np.arange(m, dtype=np.int64), cnt)
    start = np.cumsum(cnt) - cnt
    k = np.arange(total, dtype=np.int64) - np.repeat(start, cnt)
    col = x0[t] + k % nx[t]
    row = y0[t] + k // nx[t]
    px, py = col + 0.5, row + 0.5
    ax, ay, bx, by, cx, cy = x[:, 0], y[:, 0], x[:, 1], y[:, 1], x[:, 2], y[:, 2]
    den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)            # 2 × área con signo
    ok_tri = np.abs(den) > 1e-12
    dd = np.where(ok_tri, den, 1.0)
    w0 = ((by - cy)[t] * (px - cx[t]) + (cx - bx)[t] * (py - cy[t])) / dd[t]
    w1 = ((cy - ay)[t] * (px - cx[t]) + (ax - cx)[t] * (py - cy[t])) / dd[t]
    w2 = 1.0 - w0 - w1
    keep = ok_tri[t] & (np.minimum(np.minimum(w0, w1), w2) >= -BARY_TOL)
    face = t[keep]
    bary = np.column_stack([w0[keep], w1[keep], w2[keep]])
    rows, cols = row[keep], col[keep]
    # (3) triángulos sin ningún texel: el píxel que contiene su baricentro
    have = np.zeros(m, bool)
    have[face] = True
    miss = np.flatnonzero(~have)
    if len(miss):
        gx, gy = x[miss].mean(axis=1), y[miss].mean(axis=1)
        cols = np.concatenate([cols, np.clip(np.floor(gx).astype(np.int64), 0, W - 1)])
        rows = np.concatenate([rows, np.clip(np.floor(gy).astype(np.int64), 0, H - 1)])
        face = np.concatenate([face, miss])
        bary = np.vstack([bary, np.full((len(miss), 3), 1.0 / 3.0)])
    bary = np.clip(bary, 0.0, 1.0)
    bary /= bary.sum(axis=1, keepdims=True)                         # recorta los −1e-6 y deja la suma en 1
    T = np.asarray(unw.vertices, float)[np.asarray(unw.faces, np.int64)]      # (m, 3, 3)
    points = np.einsum("nk,nkc->nc", bary, T[face])
    normals = _face_normals(T)[face]
    return TexelGrid(rows.astype(np.int32), cols.astype(np.int32), face.astype(np.int32), bary.astype(np.float32), points,
                     normals, (int(W), int(H)))


# ------------------------------------------------------------------ T12: horneado (consulta de color por texel)
CHUNK = 200_000          # puntos por consulta a la fuente (acota la memoria de `ColorSource.query`)
DEFAULT_FLAT = "#C8C8C8"   # color plano de las superficies sin origen (§7.7) y de las piezas `sin_origen` (§7.8)
EDGE_PX = 1.0              # RG12: los texeles a menos de tantos texeles de una arista se corren hacia el interior


@dataclass
class BakeResult:
    atlas: np.ndarray        # (alto, ancho, 3) uint8
    mask: np.ndarray         # (alto, ancho) bool: texeles con color propio (consultados)
    stats: dict              # pct_origen (0–100), area_mm2, n_texeles, segundos
    origin: np.ndarray | None = None     # (alto, ancho) bool: texeles con origen en la fuente (lo usa la política de relleno, T13)
    # T45 (solo si la fuente trae etiquetas de rol; si no, los tres quedan en None y todo es como antes):
    labels: np.ndarray | None = None     # (alto, ancho) uint8: etiqueta de cada texel; 255 = color plano / sin rol (no depende de la paleta); la dilatación copia la del vecino
    label_roles: list | None = None      # ids de rol de la fuente: el valor `i` de `labels` es `label_roles[i]`


def _to_rest(matrix: np.ndarray, points: np.ndarray, normals: np.ndarray):
    """Lleva puntos (con traslado) y normales (solo giro) de la pieza en pose a la pose de reposo de la fuente."""
    R, t = matrix[:3, :3], matrix[:3, 3]
    return points @ R.T + t, normals @ R.T


def parse_fill(fill) -> tuple | None:
    """`"extend"` → None; `"#RRGGBB"` → (r, g, b). Cualquier otra cosa: `ValueError`."""
    if fill == "extend":
        return None
    if isinstance(fill, str) and len(fill) == 7 and fill[0] == "#":
        try:
            return tuple(int(fill[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            pass
    raise ValueError(f"fill inválido: {fill!r} (use 'extend' o '#RRGGBB')")


def dilate(atlas: np.ndarray, mask: np.ndarray, px: int):
    """Copia el color del texel cubierto más cercano a los píxeles sin cubrir a ≤ `px` píxeles. No toca los cubiertos.

    Devuelve `(atlas, mask)` nuevos; `mask` pasa a incluir la dilatación.
    """
    if px <= 0 or mask.all() or not mask.any():
        return atlas.copy(), mask.copy()
    from scipy.ndimage import distance_transform_edt

    dist, (ri, ci) = distance_transform_edt(~mask, return_indices=True)
    grow = (~mask) & (dist <= px)
    out = atlas.copy()
    out[grow] = atlas[ri[grow], ci[grow]]
    return out, mask | grow


def shrink_to_interior(grid: TexelGrid, unw, edge_px: float = EDGE_PX) -> TexelGrid:
    """RG12: los texeles a menos de `edge_px` texeles de una arista de su triángulo en el atlas se llevan hacia el baricentro
    hasta quedar a `edge_px` de la arista (o al baricentro, si el triángulo es más angosto). Los demás no cambian.

    La distancia de un texel a la arista opuesta al vértice k es `bary_k · altura_k` (en píxeles del atlas).
    """
    if edge_px <= 0 or len(grid.face) == 0:
        return grid
    tri = np.asarray(unw.tri_px, np.float64)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    area2 = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    opp = np.stack([np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1), np.linalg.norm(b - a, axis=1)], axis=1)
    h = area2[:, None] / np.maximum(opp, 1e-12)                       # (m, 3) altura sobre la arista opuesta a cada vértice
    B = grid.bary.astype(np.float64)
    hf = h[grid.face]
    d = B * hf                                                         # (n, 3) distancia a cada arista
    need = d.min(axis=1) < edge_px - 1e-9
    if not need.any():
        return grid
    Bn, hn, dn = B[need], hf[need], d[need]
    # t en [0, 1]: (1 − t)·d_k + t·h_k/3 ≥ edge_px para todo k con d_k < edge_px
    with np.errstate(divide="ignore", invalid="ignore"):
        tk = (edge_px - dn) / (hn / 3.0 - dn)
    tk = np.where((dn < edge_px) & (hn / 3.0 > dn), tk, 0.0)
    tk = np.where((dn < edge_px) & ~(hn / 3.0 > dn), 1.0, tk)
    tk = np.where(~np.isfinite(tk), 1.0, tk)
    tt = np.clip(tk.max(axis=1), 0.0, 1.0)[:, None]
    Bnew = (1.0 - tt) * Bn + tt * (1.0 / 3.0)
    # en un triángulo muy angosto el baricentro puede quedar más cerca de una arista que el propio texel: ahí no se mueve
    better = (Bnew * hn).min(axis=1) > dn.min(axis=1) + 1e-9
    idx = np.flatnonzero(need)[better]
    Bnew = Bnew[better]
    bary = grid.bary.copy()
    bary[idx] = Bnew.astype(np.float32)
    T = np.asarray(unw.vertices, float)[np.asarray(unw.faces, np.int64)]
    points = grid.points.copy()                                        # los que no se mueven quedan idénticos
    points[idx] = np.einsum("nk,nkc->nc", Bnew, T[grid.face[idx]])
    return TexelGrid(grid.rows, grid.cols, grid.face, bary, points, grid.normals, grid.size)


def bake_piece(mesh, unw, src, reg, *, eps: float = 0.15, fill="extend", symmetric: bool = False, dilate_px: int = 6,
               workers: int = 1, edge_px: float = EDGE_PX) -> BakeResult:
    """Color de cada texel de `unw` a partir de la fuente `src` (`ColorSource`) y del registro `reg` (`Registration`).

    (1) `texel_points`; (2) puntos y normales a la pose de reposo con `reg.matrix` (las normales solo giran); (3) con `symmetric`,
    una pieza cuyo x medio (en reposo) es < 0 refleja x antes de consultar, de modo que copia el color del lado izquierdo;
    (4) `src.query` en bloques de `CHUNK`; (5) el atlas toma el color de los texeles consultados; `stats["pct_origen"]` cuenta los
    que tienen origen (`dist ≤ eps` y normal compatible).

    T13. `fill="extend"` (defecto): los texeles sin origen quedan con el color de la muestra más cercana de la fuente, sin importar
    la distancia; `fill="#RRGGBB"`: quedan de ese color (si no es ninguna de las dos formas, `ValueError`). Una pieza `sin_origen`
    (el registro no halló dónde está en la fuente) sale toda del color plano `#C8C8C8` con `extend`, o del de `fill`. Antes de
    consultar, los texeles a < `edge_px` texeles de una arista se corren hacia el interior de la cara (RG12: evita el borde
    borroso de las islas). Al final se dilata `dilate_px` píxeles el color de los bordes de cada celda (para el filtrado
    bilineal del visor) y `BakeResult.mask` = islas + dilatación.
    """
    t0 = time.perf_counter()
    flat = parse_fill(fill)
    unregistered = getattr(reg, "status", "") == "sin_origen"
    if unregistered and flat is None:
        flat = parse_fill(DEFAULT_FLAT)
    grid = shrink_to_interior(texel_points(unw), unw, edge_px)
    P, N = _to_rest(np.asarray(reg.matrix, float), grid.points, grid.normals)
    if symmetric:
        V = np.asarray(unw.vertices, float)
        x_mean = float(_to_rest(np.asarray(reg.matrix, float), V, V)[0][:, 0].mean())
        if x_mean < 0:
            P = P * np.array([-1.0, 1.0, 1.0])
            N = N * np.array([-1.0, 1.0, 1.0])
    n = len(P)
    rgb = np.empty((n, 3), np.uint8)
    has = np.zeros(n, bool)
    painted = np.zeros(n, bool)                       # R11: texeles que una pincelada «sin origen» pintó: no son origen, pero tampoco se pisan con el relleno
    label_roles = getattr(src, "label_roles", None) if getattr(src, "sample_label", None) is not None else None
    lab = None if label_roles is None else np.full(n, NO_LABEL, np.uint8)       # T45: etiqueta de cada texel (255 = plano)
    if unregistered:                                  # no hay dónde consultar: todo sin origen
        rgb[:] = flat
    else:
        for a in range(0, n, CHUNK):
            q = src.query(P[a:a + CHUNK], N[a:a + CHUNK], eps=eps, workers=workers)
            rgb[a:a + CHUNK], has[a:a + CHUNK] = q.rgb, q.has_origin
            assigned = getattr(q, "assigned", None)         # una consulta sin el campo (fuente vieja o doble de prueba): ninguna pintada
            if assigned is not None:
                painted[a:a + CHUNK] = assigned
            if lab is not None:
                lab[a:a + CHUNK] = q.label
        if flat is not None:
            plain = ~(has | painted)
            rgb[plain] = flat
            if lab is not None:
                lab[plain] = NO_LABEL                 # lo pintado con el relleno plano no tiene rol
    W, H = grid.size
    atlas = np.zeros((H, W, 3), np.uint8)
    mask = np.zeros((H, W), bool)
    origin = np.zeros((H, W), bool)
    atlas[grid.rows, grid.cols] = rgb
    mask[grid.rows, grid.cols] = True
    origin[grid.rows, grid.cols] = has
    latlas = None
    if lab is not None:
        latlas = np.full((H, W), NO_LABEL, np.uint8)
        latlas[grid.rows, grid.cols] = lab
        latlas, _ = dilate(latlas, mask, int(dilate_px))      # `dilate` copia el valor del texel cubierto más cercano: la etiqueta, no un promedio
    atlas, mask = dilate(atlas, mask, int(dilate_px))
    T = np.asarray(unw.vertices, float)[np.asarray(unw.faces, np.int64)]
    area = float(0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1).sum())
    stats = {"pct_origen": float(100.0 * has.mean()) if n else 0.0, "area_mm2": area, "n_texeles": int(n),
             "segundos": float(time.perf_counter() - t0)}
    if latlas is None:
        return BakeResult(atlas, mask, stats, origin)
    return BakeResult(atlas, mask, stats, origin, latlas, list(label_roles))
