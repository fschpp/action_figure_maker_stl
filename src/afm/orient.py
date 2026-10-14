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


def overhang_area(mesh: trimesh.Trimesh, max_angle: float = 45.0, floor_tol: float = 0.05) -> float:
    """Área de caras que miran hacia abajo más allá de `max_angle` (medido desde la vertical),
    sin contar las que apoyan en la cama (z ≈ mínimo)."""
    n = mesh.face_normals
    zmin = mesh.bounds[0][2]
    down = -n[:, 2] > np.cos(np.radians(max_angle))   # cos(angulo desde -Z) mayor que el límite
    steep = (-n[:, 2] > 0) & ~down
    # caras a ras de la cama no son voladizo
    on_bed = mesh.triangles[:, :, 2].max(axis=1) < zmin + floor_tol
    return float(mesh.area_faces[steep & ~on_bed].sum())


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
