"""Color de la fuente (malla original con textura) para puntos 3D (plan §6–§7.3, T07).

Camino por defecto (D4): muestras densas sobre la superficie (una cada ~0,08 mm) con su cara, baricéntricas y color, en un
`cKDTree`. No usa `rtree`. La malla original no se modifica; se la lleva al marco de la figura con un `FigureFrame`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .texmesh import TexturedMesh

MAX_SAMPLES = 6_000_000          # RG5: tope de memoria de la fuente densa; si se pasa, sube el espaciado


@dataclass
class ColorQuery:
    rgb: np.ndarray              # (n, 3) uint8
    dist: np.ndarray             # (n,) float32: distancia (mm) al triángulo de la muestra más cercana
    has_origin: np.ndarray       # (n,) bool: dist ≤ eps y normal compatible
    face: np.ndarray             # (n,) int32


class ColorSource:
    def __init__(self, tm: TexturedMesh, frame=None, spacing_mm: float = 0.08, seed: int = 0):
        from scipy.spatial import cKDTree

        V = np.asarray(tm.vertices, float)
        V = V if frame is None else frame.apply(V)
        self.faces = np.asarray(tm.faces, np.int32)
        self.vertices = V
        self._tris = V[self.faces]
        cr = np.cross(self._tris[:, 1] - self._tris[:, 0], self._tris[:, 2] - self._tris[:, 0])
        self.area = 0.5 * np.linalg.norm(cr, axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            n = cr / np.linalg.norm(cr, axis=1, keepdims=True)
        self.face_normals = np.nan_to_num(n)
        self._tm = tm
        self._mesh = None
        self.spacing_mm = float(spacing_mm)
        counts = self._counts(self.spacing_mm)
        while counts.sum() > MAX_SAMPLES:                       # tope de memoria: espaciado mayor
            self.spacing_mm *= 1.1 * float(np.sqrt(counts.sum() / MAX_SAMPLES))
            counts = self._counts(self.spacing_mm)
        rng = np.random.default_rng(seed)
        face = np.repeat(np.arange(len(self.faces), dtype=np.int32), counts)
        r = rng.random((len(face), 2))
        flip = r.sum(axis=1) > 1.0
        r[flip] = 1.0 - r[flip]
        bary = np.column_stack([1.0 - r.sum(axis=1), r]).astype(np.float32)
        self.sample_face, self.sample_bary = face, bary
        self.sample_xyz = np.einsum("nk,nkc->nc", bary.astype(np.float64), self._tris[face])
        self.sample_rgb = tm.sample(face, bary, "base_color")
        tm.release_image("base_color")                          # RG5: se libera la imagen decodificada
        self._tree = cKDTree(self.sample_xyz)
        self._coarse = cKDTree(self.sample_xyz[::4])

    def _counts(self, spacing: float) -> np.ndarray:
        return np.maximum(1, np.ceil(self.area / (spacing * spacing)).astype(np.int64))

    def to_trimesh(self):
        import trimesh

        if self._mesh is None:
            self._mesh = trimesh.Trimesh(self.vertices, self.faces, process=False)
        return self._mesh

    @property
    def mesh(self):
        """Geometría de la fuente en el marco de la figura (`trimesh.Trimesh`, perezosa)."""
        return self.to_trimesh()

    # ------------------------------------------------------------------ consulta
    def query(self, points, normals=None, eps: float = 0.15, min_dot: float = 0.3, workers: int = 1) -> ColorQuery:
        import trimesh

        P = np.atleast_2d(np.asarray(points, float))
        _, idx = self._tree.query(P, workers=workers)
        face = self.sample_face[idx]
        # distancia exacta al triángulo de la muestra más cercana (no sobreestima por el espaciado)
        cp = trimesh.triangles.closest_point(self._tris[face], P)
        dist = np.linalg.norm(cp - P, axis=1)
        ok = dist <= eps
        if normals is not None:
            ok &= np.einsum("nc,nc->n", self.face_normals[face], np.asarray(normals, float)) > min_dot
        return ColorQuery(self.sample_rgb[idx], dist.astype(np.float32), ok, face.astype(np.int32))

    def distance(self, points, workers: int = 1) -> np.ndarray:
        """Distancia gruesa (una muestra de cada 4), para puntuar registros."""
        P = np.atleast_2d(np.asarray(points, float))
        return self._coarse.query(P, workers=workers)[0].astype(np.float32)
