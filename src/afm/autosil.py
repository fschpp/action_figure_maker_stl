"""Propuesta automática de siluetas de brazo y pierna (plan §8.3, «Automatización propuesta»).

Se trabaja sobre la malla del lado de trabajo (x >= 0, frente hacia +Y, Z arriba) con **cortes
horizontales**: en cada altura se separan los componentes conectados del corte (recortado a x >= 0).
Brazos y piernas son los componentes que quedan separados del tronco; donde un brazo se funde con el
torso (hombro) el borde se extrapola desde el tramo separado más cercano. La silueta propuesta es un
polígono en el plano frontal (x, z) que se puede subir a cualquier plano de miembro no de canto
(`lift_to_plane`) y que después se corrige con el editor de polígonos.

Las constantes de la cadera (`hip_x`, `hip_up`) salen de los tres personajes ya hechos a mano
(Güemes, Belgrano, Azurduy: cara de contacto en x = 7 mm y recorte 10 mm por encima de la entrepierna,
con la figura normalizada a 100 mm pies→coronilla).

Validación (volumen de la pieza recortada, diferencia simétrica contra la silueta hecha a mano): brazo de
Güemes 4,2 %, Belgrano 3,5 %, Azurduy 0,6 %, Mickey 5,3 %; pierna de Güemes 1,3 %. En Belgrano y Azurduy
(abrigo / faldón largo) la entrepierna detectada es el borde de la prenda y la pierna da ~30 % de diferencia;
con `crotch_z` (38 y 42) coincide al 0,0 %. La pierna de Mickey (27,8 %) no se corrigió: la cola y las botas
hacen que haya recortes propios del personaje (`Trim`). `propose_arm` devuelve None si no hay ningún tramo
donde el brazo quede separado del tronco.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from shapely.geometry import Polygon, box

from .planes import Plane

_HALF = box(0.0, -1e4, 1e4, 1e4)


@dataclass
class Slice:
    z: float
    inner: tuple[float, float] | None        # (x_min, x_max) del componente más interno (tronco o pierna)
    outer: list[tuple[float, float, float]]  # (x_min, x_max, área) de los componentes separados hacia afuera
    x_all: float = 0.0                       # x máxima de todo el corte (incluye componentes pegados al interno)


def slice_at(mesh, z: float, min_area: float = 0.3) -> Slice:
    """Componentes del corte horizontal de ``mesh`` a la altura ``z``, recortado a x >= 0."""
    sec = mesh.section(plane_origin=(0, 0, z), plane_normal=(0, 0, 1))
    if sec is None:
        return Slice(z, None, [], 0.0)
    u = Polygon()
    for loop in sec.discrete:
        if len(loop) >= 4:
            u = u.symmetric_difference(Polygon(loop[:, :2]).buffer(0))   # par-impar: respeta los huecos
    u = u.intersection(_HALF)
    gs = [g for g in (u.geoms if hasattr(u, "geoms") else [u]) if g.geom_type == "Polygon" and g.area >= min_area]
    if not gs:
        return Slice(z, None, [], 0.0)
    gs.sort(key=lambda g: g.bounds[0])
    inner = gs[0]
    outer = [(g.bounds[0], g.bounds[2], g.area) for g in gs[1:] if g.bounds[0] > inner.bounds[2] + 0.05]
    return Slice(z, (inner.bounds[0], inner.bounds[2]), outer, max(g.bounds[2] for g in gs))


def scan(mesh, step: float = 0.5, z_max: float | None = None) -> list[Slice]:
    top = float(mesh.bounds[1, 2]) if z_max is None else z_max
    return [slice_at(mesh, float(z)) for z in np.arange(step, top, step)]


def _median_smooth(a: np.ndarray, w: int = 5) -> np.ndarray:
    pad = w // 2
    p = np.pad(a, pad, mode="edge")
    return np.array([np.median(p[i:i + w]) for i in range(len(a))])


def _runs(flags: np.ndarray, max_gap: int) -> list[tuple[int, int]]:
    """Tramos consecutivos de True, uniendo los que distan <= max_gap muestras."""
    idx = np.where(flags)[0]
    if len(idx) == 0:
        return []
    out, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - p > max_gap + 1:
            out.append((s, p))
            s = i
        p = i
    out.append((s, p))
    return out


def _close(left: np.ndarray, zs: np.ndarray, x_far: float, tol: float = 0.25) -> np.ndarray:
    """Polígono (x, z) con borde izquierdo ``left`` (x por cada z), borde derecho vertical en ``x_far``."""
    poly = Polygon(np.vstack([np.column_stack([left, zs]), [[x_far, zs[-1]], [x_far, zs[0]]]]))
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.geom_type != "Polygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    poly = poly.simplify(tol)
    return np.asarray(poly.exterior.coords)[:-1]


def propose_arm(mesh, slices: list[Slice] | None = None, margin: float = 1.0, fuse_offset: float = 0.5,
                ) -> np.ndarray | None:
    """Silueta (Nx2, x–z) del brazo: todo lo que está más afuera que el tronco entre la mano y el hombro.

    Devuelve None si no hay un tramo donde el brazo quede separado del tronco (p. ej. Mickey).
    """
    sl = slices if slices is not None else scan(mesh)
    zs = np.array([s.z for s in sl])
    has = np.array([bool(s.outer) for s in sl])
    runs = _runs(has, max_gap=4)
    if not runs:
        return None
    area = lambda r: sum(a for s in sl[r[0]:r[1] + 1] for (_, _, a) in s.outer)
    r0, r1 = max(runs, key=area)
    if zs[r1] - zs[r0] < 4.0:
        return None
    inner_max = np.array([s.inner[1] if s.inner else np.nan for s in sl])
    # borde del tronco en el tramo separado y corte en el medio del hueco
    cut = np.full(len(sl), np.nan)
    for i in range(r0, r1 + 1):
        s = sl[i]
        if s.outer:
            amin = min(o[0] for o in s.outer)
            cut[i] = inner_max[i] + 0.5 * (amin - inner_max[i])
    ok = ~np.isnan(cut[r0:r1 + 1])
    seg = cut[r0:r1 + 1]
    seg[~ok] = np.interp(np.where(~ok)[0], np.where(ok)[0], seg[ok])
    seg = np.maximum(_median_smooth(seg), inner_max[r0:r1 + 1] + 0.15)
    cut[r0:r1 + 1] = seg
    # hombro fundido: el borde del tronco sigue como en los últimos 5 mm del tramo separado
    lastn = max(1, int(5.0 / (zs[1] - zs[0])))
    edge = float(np.median(inner_max[max(r0, r1 - lastn):r1 + 1])) + fuse_offset
    top = r1
    for i in range(r1 + 1, len(sl)):
        s = sl[i]
        if s.inner is None or s.x_all <= edge + 1.5:
            break
        cut[i] = edge
        top = i
    zb, zt = max(zs[r0] - margin, 0.0), zs[top] + margin
    sel = np.arange(len(sl))[(zs >= zb) & (zs <= zt)]
    left = np.where(np.isnan(cut[sel]), edge, cut[sel])
    # por debajo del primer corte (margen) se mantiene el primer valor; por encima del último, el del hombro
    zz = zs[sel]
    x_far = float(mesh.bounds[1, 0]) + 2.0
    return _close(left, zz, x_far)


def propose_leg(mesh, slices: list[Slice] | None = None, hip_x: float = 7.0, hip_up: float = 10.0,
                crotch_z: float | None = None) -> np.ndarray | None:
    """Silueta (Nx2, x–z) de la pierna: columna hasta la entrepierna + trozo de cadera (x > hip_x).

    La entrepierna es la primera altura donde el componente interno toca x = 0 (las piernas dejan de estar
    separadas); con abrigo o faldón largo eso da el borde de la prenda, más abajo que la cadera real: en ese
    caso se pasa ``crotch_z`` a mano (Belgrano y Azurduy: unos 8 mm más arriba). El borde exterior queda en el medio del hueco con la mano si hay, o 1,5 mm afuera del tronco.
    """
    sl = slices if slices is not None else scan(mesh)
    zs = np.array([s.z for s in sl])
    crotch = crotch_z
    for s in ([] if crotch is not None else sl):
        if s.z > 3.0 and s.inner is not None and s.inner[0] <= 0.05:
            crotch = s.z
            break
    if crotch is None:
        return None
    z_top = crotch + hip_up
    sel = [i for i, s in enumerate(sl) if s.z <= z_top and s.inner is not None]
    out = np.empty(len(sel))
    for k, i in enumerate(sel):
        s = sl[i]
        out[k] = 0.5 * (s.inner[1] + min(o[0] for o in s.outer)) if s.outer else s.inner[1] + 1.5
    out = np.maximum(_median_smooth(out), hip_x + 0.5)
    zz = zs[sel]
    # recorrido: (0, -1) → borde exterior ↑ → (hip_x, z_top) → (hip_x, crotch) → (0, crotch)
    pts = np.vstack([[0.0, -1.0], [out[0], -1.0], np.column_stack([out, zz]), [out[-1], z_top],
                     [hip_x, z_top], [hip_x, crotch], [0.0, crotch]])
    poly = Polygon(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return np.asarray(poly.simplify(0.25).exterior.coords)[:-1]


def lift_to_plane(poly_xz: np.ndarray, plane: Plane, min_ny: float = 0.2) -> np.ndarray:
    """Sube un polígono (x, z) a un plano de miembro: cada punto se lleva al plano siguiendo el eje Y.

    Un plano casi de canto (|n·Y| < ``min_ny``) no se puede usar: se avisa con ValueError.
    """
    n = np.asarray(plane.normal, float)
    o = np.asarray(plane.origin, float)
    if abs(n[1]) < min_ny:
        raise ValueError("El plano está de canto respecto del frente: no se puede proponer la silueta; "
                         "elegí un plano frontal o dibujala a mano.")
    x, z = poly_xz[:, 0], poly_xz[:, 1]
    y = o[1] - (n[0] * (x - o[0]) + n[2] * (z - o[2])) / n[1]
    return np.column_stack([x, y, z])
