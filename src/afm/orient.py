"""Orientación de impresión y zonas voladizas (plan §8.7)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh


@dataclass
class OrientResult:
    rotation: np.ndarray          # 4x4: lleva la figura a su orientación de impresión (apoyada en z = 0)
    overhang_area: float          # mm² de caras con inclinación > ángulo límite
    base_area: float              # mm² de apoyo sobre la cama
    label: str


def overhang_mask(mesh: trimesh.Trimesh, max_angle: float = 45.0, floor_tol: float = 0.05,
                  zmin: float | None = None) -> np.ndarray:
    """Máscara booleana por cara: True si la cara es voladizo.

    `max_angle` es el ángulo tolerado de la pared **respecto de la vertical**: una pared vertical es
    0° (se imprime sin soporte) y un techo horizontal es 90° (el peor caso). Una cara mirando hacia
    abajo con normal n forma con la vertical un ángulo φ tal que -n_z = sen φ; es voladizo si
    φ > max_angle. Las caras apoyadas en la cama (z ≈ mínimo) no cuentan. `zmin`: altura de la cama si la
    malla es solo una parte de un conjunto que se apoya junto (por defecto, el mínimo de la propia malla).
    """
    n = mesh.face_normals
    zmin = mesh.bounds[0][2] if zmin is None else zmin
    steep = -n[:, 2] > np.sin(np.radians(max_angle))
    on_bed = mesh.triangles[:, :, 2].max(axis=1) < zmin + floor_tol
    return steep & ~on_bed


def overhang_area(mesh: trimesh.Trimesh, max_angle: float = 45.0, floor_tol: float = 0.05) -> float:
    """Área (mm²) de las caras voladizas (ver `overhang_mask`)."""
    return float(mesh.area_faces[overhang_mask(mesh, max_angle, floor_tol)].sum())


def base_area(mesh: trimesh.Trimesh, tol: float = 0.05) -> float:
    zmin = mesh.bounds[0][2]
    on_bed = (mesh.triangles[:, :, 2].max(axis=1) < zmin + tol) & (mesh.face_normals[:, 2] < -0.99)
    return float(mesh.area_faces[on_bed].sum())


def _axis_rotations():
    """Las 6 orientaciones que ponen cada eje ±X, ±Y, ±Z hacia arriba."""
    out = []
    for name, v in (("+Z", (0, 0, 1)), ("-Z", (0, 0, -1)), ("+X", (1, 0, 0)),
                    ("-X", (-1, 0, 0)), ("+Y", (0, 1, 0)), ("-Y", (0, -1, 0))):
        R = trimesh.geometry.align_vectors(v, (0, 0, 1))
        out.append((f"{name} arriba", R))
    return out


def best_orientation(mesh: trimesh.Trimesh, max_angle: float = 45.0, extra_tilts=(0.0,)) -> OrientResult:
    """Elige la orientación con menos voladizo (a igualdad, más base). `extra_tilts`: grados de
    inclinación adicional alrededor de X a probar (el cliente puede afinar con un control)."""
    best = None
    for label, R in _axis_rotations():
        for tilt in extra_tilts:
            T = R.copy()
            if tilt:
                T = trimesh.transformations.rotation_matrix(np.radians(tilt), (1, 0, 0)) @ T
            m = mesh.copy()
            m.apply_transform(T)
            lift = np.eye(4)
            lift[2, 3] = -m.bounds[0][2]
            m.apply_transform(lift)
            ov, ba = overhang_area(m, max_angle), base_area(m)
            score = (round(ov, 1), -ba)
            if best is None or score < best[0]:
                best = (score, OrientResult(lift @ T, ov, ba, label + (f" +{tilt}°" if tilt else "")))
    return best[1]


def pose_orientation(mesh: trimesh.Trimesh, max_angle: float = 45.0) -> OrientResult:
    """Orientación «como se ve»: la de la figura generada en la pose de cadera (cabeza hacia +Z, sentada), sin girarla,
    solo bajada hasta apoyar en la cama (z = 0). Es la que usa la vista previa de la pose; a diferencia de
    `best_orientation` nunca da vuelta la figura (patas / asiento para arriba) para ahorrar voladizo."""
    lift = np.eye(4)
    lift[2, 3] = -mesh.bounds[0][2]
    m = mesh.copy()
    m.apply_transform(lift)
    return OrientResult(lift, overhang_area(m, max_angle), base_area(m), "pose de cadera, cabeza arriba")
