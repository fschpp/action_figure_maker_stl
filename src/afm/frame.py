"""Marco de la figura: UNA matriz 4×4 archivo → marco de trabajo (plan §6–§7.2, T03/T04).

El motor aplica a la geometría: `orient_z_up` → escala → `repair` → `normalize` (pies en z=0, centrada) → giro de 180° sobre Z si
`front == "-Y"` → `align_to_symmetry`. `FigureFrame` compone lo mismo en 4×4 para llevar la fuente de color al mismo marco.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class FigureFrame:
    matrix: np.ndarray                      # (4, 4): archivo -> marco de la figura (mm, pies en z=0, centrada, simetría en x=0)
    scale: float = 1.0
    report: dict = field(default_factory=dict)

    def apply(self, points) -> np.ndarray:
        p = np.atleast_2d(np.asarray(points, float))
        return p @ self.matrix[:3, :3].T + self.matrix[:3, 3]

    def det(self) -> float:
        """Determinante de la parte 3×3 dividido por `scale³`: +1 = solo rotación (sin espejo)."""
        return float(np.linalg.det(self.matrix[:3, :3]) / self.scale ** 3)

    @classmethod
    def from_load(cls, report, front: str = "+Y", symmetry=None) -> "FigureFrame":
        """`M = A · F · N`: `N` = orientar + escalar + apoyar + centrar (igual que `LoadReport.to_figure`), `F` = giro de 180° sobre Z si
        el frente es −Y, `A` = lo que hace `align_to_symmetry` (rotación sobre Z + traslado en x)."""
        R = np.eye(3) if not report.orient_matrix else np.asarray(report.orient_matrix, float).reshape(3, 3)
        N = np.eye(4)
        N[:3, :3] = report.scale * R
        N[:3, 3] = [-report.center_xy[0], -report.center_xy[1], -report.scale * report.zmin_file]
        # `to_figure` trabaja sobre puntos ya orientados: el traslado de z es -scale·zmin_file en el marco orientado
        F = np.diag([-1.0, -1.0, 1.0, 1.0]) if front == "-Y" else np.eye(4)
        A = _symmetry_matrix(symmetry)
        return cls(A @ F @ N, float(report.scale))


def _symmetry_matrix(symmetry) -> np.ndarray:
    """La transformación rígida de `align_to_symmetry`, obtenida aplicándola a cuatro puntos (Kabsch)."""
    if symmetry is None:
        return np.eye(4)
    import trimesh

    from . import pipeline as P

    pts = np.array([[0.0, 0, 0], [10.0, 0, 0], [0, 10.0, 0], [0, 0, 10.0]])
    tet = trimesh.Trimesh(pts, [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]], process=False)
    out = np.asarray(P.align_to_symmetry(tet, symmetry).vertices)
    a, b = pts, out
    ca, cb = a.mean(0), b.mean(0)
    U, _, Vt = np.linalg.svd((a - ca).T @ (b - cb))
    d = np.sign(np.linalg.det(Vt.T @ U.T)) or 1.0
    Rm = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    M = np.eye(4)
    M[:3, :3] = Rm
    M[:3, 3] = cb - Rm @ ca
    return M
