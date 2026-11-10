"""Edición de una articulación (rodilla / codo) como dato: `JointSpec` entra, `JointSpec` nuevo sale.

Es la misma geometría que usan `Session.set_joint_height`, `set_joint_plane_side_tilt`, `set_joint_axis` y
`set_joint_range` sobre las articulaciones del proyecto, pero sin sesión ni figura: sirve para reajustar la articulación
que una pieza del catálogo trae en su `meta.articulaciones` (marco de la pieza). Ninguna función modifica su argumento.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from .joints import JointSpec
from .planes import Plane

MIN_H_MM = 0.5            # el eje queda al menos esto sobre el plano (como `build_joint`)
MAX_SIDE_TILT = 60.0      # límites de los controles de la ventana
MAX_FRONT_TILT = 45.0
RANGE_MIN, RANGE_MAX = -45.0, 150.0


def normal(j: JointSpec) -> np.ndarray:
    """Normal del plano apuntando al lado proximal (arriba: figura de pie, Z arriba)."""
    n = np.asarray(j.plane.normal, float)
    n = n / np.linalg.norm(n)
    return -n if n[2] < 0 else n


def height(j: JointSpec) -> float:
    """Distancia (mm) del eje de la bisagra al plano de articulación, a lo largo de la normal."""
    return float((np.asarray(j.axis_center, float) - np.asarray(j.plane.origin, float)) @ normal(j))


def front_tilt(j: JointSpec) -> float:
    """Inclinación del eje vista de frente (° alrededor de Y; 0 = a lo largo de X; positivo = z sube con x)."""
    d = j.axis_dir
    return float(np.degrees(np.arctan2(d[2], d[0])))


def side_tilt(j: JointSpec) -> float:
    """Inclinación de costado del plano (vista lateral; positivo si z sube con y). Un plano horizontal da 0."""
    n = normal(j)
    return float(np.degrees(np.arctan2(-n[1] * n[2], 1.0 - n[1] ** 2))) + 0.0


def with_height(j: JointSpec, h: float) -> JointSpec:
    """Sube o baja el eje en vertical (x e y no cambian) hasta que quede a `h` mm del plano."""
    h = float(h)
    if h < MIN_H_MM:
        raise ValueError(f"el eje tiene que quedar al menos {MIN_H_MM:g} mm arriba del plano de articulación (pedido: {h:.2f} mm)")
    n = normal(j)
    c = np.asarray(j.axis_center, float).copy()
    c[2] += (h - height(j)) / float(n[2])
    return dataclasses.replace(j, axis_center=tuple(float(x) for x in c))


def with_axis_point(j: JointSpec, point) -> JointSpec:
    """Lleva el eje a (y, z) de `point` (la x se conserva: el eje atraviesa el miembro). El plano no se mueve."""
    pt = np.asarray(point, float)
    c = np.asarray(j.axis_center, float)
    c = np.array([c[0], pt[1], pt[2]])
    h = float((c - np.asarray(j.plane.origin, float)) @ normal(j))
    if h < MIN_H_MM:
        raise ValueError(f"el eje tiene que quedar al menos {MIN_H_MM:g} mm arriba del plano de articulación (queda a {h:.2f} mm)")
    return dataclasses.replace(j, axis_center=tuple(float(x) for x in c))


def with_front_tilt(j: JointSpec, tilt_deg: float) -> JointSpec:
    """Inclina el eje visto de frente. No mueve el plano ni el centro del eje."""
    t = float(tilt_deg)
    if abs(t) > MAX_FRONT_TILT:
        raise ValueError(f"la inclinación tiene que estar entre −{MAX_FRONT_TILT:g}° y {MAX_FRONT_TILT:g}°")
    r = np.radians(t)
    return dataclasses.replace(j, axis_dir=(float(np.cos(r)), 0.0, float(np.sin(r))))


def with_side_tilt(j: JointSpec, tilt_deg: float) -> JointSpec:
    """Pone la inclinación de costado del plano en `tilt_deg` (0 = «enderezarlo»). El eje no se mueve y conserva su altura:
    lo que se corre es el plano (que sigue pasando a esa altura bajo el eje)."""
    t = float(tilt_deg)
    if abs(t) > MAX_SIDE_TILT:
        raise ValueError(f"la inclinación de costado tiene que estar entre −{MAX_SIDE_TILT:g}° y {MAX_SIDE_TILT:g}°")
    n = normal(j)
    h = height(j)
    new = np.array([n[0] / n[2], -np.tan(np.radians(t)), 1.0])
    new = new / np.linalg.norm(new)
    if new[2] < 0.5:
        raise ValueError("con esa inclinación el plano deja de cortar el miembro a lo largo (tiene que ser casi horizontal)")
    c = np.asarray(j.axis_center, float)
    return dataclasses.replace(j, plane=Plane.from_normal(c - new * h, new, j.plane.name))


def with_range(j: JointSpec, lo: float | None, hi: float | None) -> JointSpec:
    """Rango de flexión (°, hiperextensión negativa). `None, None` vuelve al de los parámetros."""
    if lo is None and hi is None:
        return dataclasses.replace(j, flex_range=None)
    lo, hi = float(lo), float(hi)
    if not (RANGE_MIN <= lo < hi <= RANGE_MAX):
        raise ValueError(f"el rango tiene que cumplir {RANGE_MIN:g}° ≤ mínimo < máximo ≤ {RANGE_MAX:g}°")
    return dataclasses.replace(j, flex_range=(lo, hi))


def differs(a: JointSpec, b: JointSpec, tol: float = 1e-6) -> bool:
    """¿Cambió algo que importa (eje, dirección, plano, rango, bisagra)?"""
    da, db = a.to_dict(), b.to_dict()
    for k in ("axis_center", "axis_dir"):
        if not np.allclose(da[k], db[k], atol=tol):
            return True
    pa, pb = da["plane"], db["plane"]
    for k in pa:
        va, vb = pa[k], pb[k]
        if isinstance(va, (list, tuple)):
            if not np.allclose(va, vb, atol=tol):
                return True
        elif va != vb:
            return True
    return any(da[k] != db[k] for k in ("flex_range", "hinge", "flex_sign"))
