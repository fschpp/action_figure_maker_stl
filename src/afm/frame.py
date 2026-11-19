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

    @staticmethod
    def engine_post(front: str = "+Y", symmetry=None) -> np.ndarray:
        """4×4 de lo que el motor hace DESPUÉS de cargar la figura: giro de 180° sobre Z si el frente es −Y y `align_to_symmetry`
        (`A · F` de `from_load`). Sirve para llevar a la fuente al marco del motor cuando se alineó contra el STL tal como se cargó."""
        F = np.diag([-1.0, -1.0, 1.0, 1.0]) if front == "-Y" else np.eye(4)
        return _symmetry_matrix(symmetry) @ F

    def then(self, post: np.ndarray) -> "FigureFrame":
        """Marco compuesto: primero este, después `post` (4×4 rígida, sin escala). Conserva `scale` y `report`."""
        return FigureFrame(np.asarray(post, float) @ self.matrix, self.scale, self.report)

    @classmethod
    def from_similarity(cls, source_vertices, target_mesh, refine: bool | None = None, source_faces=None) -> "FigureFrame":
        """Modo B (plan §7.2, T04): lleva los vértices de la fuente (FBX) al marco de la geometría `target_mesh` (el STL ya trabajado).

        (1) rotación de `orient_z_up_matrix`; (2) escala = alto del destino / alto de la fuente, cajas centradas en X e Y y z mínimo
        apoyado; (3) ICP de similitud solo si `refine` es True, o si es None y la media sin ICP supera `ICP_AUTO_MEAN_MM` (D11);
        (4) se mide la distancia de los vértices del destino a la superficie de la fuente y se carga `report`.
        `source_faces` (opcional) permite medir contra la superficie; sin ellas se mide contra los vértices (cota superior).
        Solo rotaciones propias: det(R) = +1.
        """
        import trimesh

        from .meshio import orient_z_up_matrix

        V = np.asarray(source_vertices.vertices if hasattr(source_vertices, "vertices") else source_vertices, float)
        if source_faces is None and hasattr(source_vertices, "faces"):
            source_faces = source_vertices.faces
        Q = np.asarray(target_mesh.vertices, float)
        R0 = orient_z_up_matrix(trimesh.PointCloud(V))
        Vo = V @ R0.T
        lo, hi = Vo.min(0), Vo.max(0)
        tlo, thi = Q.min(0), Q.max(0)
        s = float(thi[2] - tlo[2]) / float(hi[2] - lo[2])
        t = np.array([(tlo[0] + thi[0]) / 2 - s * (lo[0] + hi[0]) / 2, (tlo[1] + thi[1]) / 2 - s * (lo[1] + hi[1]) / 2, tlo[2] - s * lo[2]])
        M = _similarity_matrix(s * R0, t)
        surf = _SourceSurface(V, source_faces)
        stats = _target_distances(surf, M, Q)
        iters = 0
        if refine is True or (refine is None and stats[0] > ICP_AUTO_MEAN_MM):
            M2, stats2, iters = _icp_similarity(surf, M, Q, stats)
            if stats2[0] < stats[0] or refine is True:
                M, stats = M2, stats2
            else:
                iters = 0
        scale = float(np.cbrt(abs(np.linalg.det(M[:3, :3]))))
        mean, p95, mx = stats
        rep = {"media_mm": mean, "p95_mm": p95, "max_mm": mx, "ok": bool(mean <= SIMILARITY_OK_MM), "iteraciones": int(iters)}
        return cls(M, scale, rep)


ICP_AUTO_MEAN_MM = 0.05          # D11: el ICP es respaldo; sobra si la media sin ICP no pasa de esto
SIMILARITY_OK_MM = 0.1           # §7.2: el FBX es la misma figura si la media ≤ 0,1 mm
ICP_MAX_ITER = 20
ICP_STOP_DELTA = 1e-4


def _similarity_matrix(A: np.ndarray, t: np.ndarray) -> np.ndarray:
    M = np.eye(4)
    M[:3, :3] = A
    M[:3, 3] = t
    return M


class _SourceSurface:
    """Muestras densas de la superficie de la fuente (en SU marco) para medir distancias punto-superficie sin `rtree`."""

    def __init__(self, V: np.ndarray, F=None, samples: int = 400_000):
        from scipy.spatial import cKDTree

        self.V = V
        if F is None:                                           # solo vértices: distancia punto a punto (cota superior)
            self.F, self.xyz, self.face = None, V, None
        else:
            self.F = np.asarray(F, np.int64)
            tri = V[self.F]
            area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
            counts = np.maximum(1, np.ceil(area / (area.sum() / samples)).astype(np.int64))
            face = np.repeat(np.arange(len(self.F)), counts)
            r = np.random.default_rng(0).random((len(face), 2))
            flip = r.sum(1) > 1
            r[flip] = 1 - r[flip]
            b = np.column_stack([1 - r.sum(1), r])
            self.xyz, self.face = np.einsum("nk,nkc->nc", b, tri[face]), face
            self._tri = tri
        self.tree = cKDTree(self.xyz)

    def nearest(self, P: np.ndarray) -> np.ndarray:
        """Punto de la fuente (muestra) más cercano a cada punto de `P`."""
        return self.xyz[self.tree.query(P)[1]]

    def distance(self, P: np.ndarray, k: int = 3) -> np.ndarray:
        """Distancia al triángulo más cercano entre los de las `k` muestras vecinas (exacta punto-triángulo)."""
        if self.F is None:
            return self.tree.query(P)[0]
        import trimesh

        idx = self.tree.query(P, k=k)[1]
        best = np.full(len(P), np.inf)
        for j in range(idx.shape[1]):
            f = self.face[idx[:, j]]
            cp = trimesh.triangles.closest_point(self._tri[f], P)
            best = np.minimum(best, np.linalg.norm(cp - P, axis=1))
        return best


def _target_distances(surf: _SourceSurface, M: np.ndarray, Q: np.ndarray):
    """(media, p95, máx) en el marco del destino, de los vértices `Q` del destino a la superficie de la fuente transformada con `M`."""
    s = float(np.cbrt(abs(np.linalg.det(M[:3, :3]))))
    Qs = (Q - M[:3, 3]) @ np.linalg.inv(M[:3, :3]).T           # destino → marco de la fuente
    d = surf.distance(Qs) * s
    return float(d.mean()), float(np.percentile(d, 95)), float(d.max())


def _umeyama(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Similitud (escala · R + t, con det(R) = +1) que lleva `A` sobre `B` por mínimos cuadrados."""
    ca, cb = A.mean(0), B.mean(0)
    Ac, Bc = A - ca, B - cb
    U, S, Vt = np.linalg.svd(Bc.T @ Ac / len(A))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U) * np.linalg.det(Vt)) or 1.0
    R = U @ D @ Vt
    s = float(np.trace(np.diag(S) @ D) / Ac.var(0).sum())
    return _similarity_matrix(s * R, cb - s * R @ ca)


def _icp_similarity(surf: _SourceSurface, M: np.ndarray, Q: np.ndarray, stats0):
    """ICP de similitud: cada vértice del destino se lleva al marco de la fuente, se busca su muestra más cercana y Umeyama da la
    transformación fuente→destino completa (el árbol de la fuente se arma una sola vez). Devuelve (matriz, stats, iteraciones)."""
    best, best_stats, it_done = M, stats0, 0
    cur = M
    for it in range(1, ICP_MAX_ITER + 1):
        Qs = (Q - cur[:3, 3]) @ np.linalg.inv(cur[:3, :3]).T
        cur = _umeyama(surf.nearest(Qs), Q)
        stats = _target_distances(surf, cur, Q)
        it_done = it
        improved = best_stats[0] - stats[0]
        if stats[0] < best_stats[0]:
            best, best_stats = cur, stats
        if improved < ICP_STOP_DELTA:
            break
    return best, best_stats, it_done


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
