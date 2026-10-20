"""Selección de puntos sobre la malla por ray-casting, con ajuste al vértice más cercano (plan §6, §7)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh


@dataclass
class PickResult:
    point: np.ndarray          # punto elegido (el vértice si se ajustó)
    hit: np.ndarray            # punto exacto de impacto del rayo
    face: int
    normal: np.ndarray
    snapped: bool


def ray_first_hit(tris: np.ndarray, origin, direction, eps: float = 1e-12):
    """Möller–Trumbore vectorizado sobre (n,3,3) triángulos. Devuelve (índice, t) del primer impacto
    hacia delante, o None. Un rayo por llamada: basta para clics y evita depender de rtree/embree."""
    o = np.asarray(origin, float)
    d = np.asarray(direction, float)
    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0
    p = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > eps
    inv = np.zeros_like(det)
    inv[ok] = 1.0 / det[ok]
    s = o - v0
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ d) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 1e-9)
    if not hit.any():
        return None
    idx = np.where(hit)[0]
    k = idx[np.argmin(t[idx])]
    return int(k), float(t[k])


class Picker:
    def __init__(self, mesh: trimesh.Trimesh):
        self.mesh = mesh
        self._tris = np.asarray(mesh.triangles, float)

    def pick(self, origin, direction, snap_radius: float = 1.5) -> PickResult | None:
        """Primer impacto del rayo. Si algún vértice del triángulo golpeado queda a menos de
        `snap_radius` mm del impacto, se devuelve ese vértice (como al elegir vértices en Onshape)."""
        r = ray_first_hit(self._tris, origin, direction)
        if r is None:
            return None
        face, t = r
        hit = np.asarray(origin, float) + t * np.asarray(direction, float)
        verts = self.mesh.vertices[self.mesh.faces[face]]
        dist = np.linalg.norm(verts - hit, axis=1)
        k = int(np.argmin(dist))
        snapped = bool(snap_radius > 0 and dist[k] <= snap_radius)
        return PickResult(verts[k].copy() if snapped else hit.copy(), hit.copy(), face,
                          self.mesh.face_normals[face].copy(), snapped)

    def nearest_vertex(self, camera, px: float, py: float, width: float, height: float,
                       radius_px: float = 12.0) -> tuple[int, np.ndarray] | None:
        """Vértice de la malla más cercano al puntero (píxeles, y hacia abajo) si queda a `radius_px` o menos en pantalla.
        Solo cuenta lo visible: se parte del primer impacto del rayo y se buscan vértices a su alrededor. Devuelve
        (índice, posición) o None si el puntero no está sobre la figura o no hay ningún vértice cerca."""
        o, d = camera.ray(px, py, width, height)
        r = ray_first_hit(self._tris, o, d)
        if r is None:
            return None
        hit = o + r[1] * d
        base = camera.project(hit, width, height)[0, :2]
        ppm = max(float(np.linalg.norm(camera.project(hit + e, width, height)[0, :2] - base)) for e in np.eye(3))
        if ppm < 1e-9:
            return None
        if getattr(self, "_tree", None) is None:
            from scipy.spatial import cKDTree
            self._tree = cKDTree(np.asarray(self.mesh.vertices, float))
        idx = np.asarray(self._tree.query_ball_point(hit, 1.6 * radius_px / ppm), dtype=int)
        if idx.size == 0:
            return None
        pix = camera.project(self.mesh.vertices[idx], width, height)[:, :2]
        dpx = np.linalg.norm(pix - np.array([px, py]), axis=1)
        ok = dpx <= radius_px
        if not ok.any():
            return None
        k = int(idx[ok][np.argmin(dpx[ok])])
        return k, np.asarray(self.mesh.vertices[k], float).copy()

    def pick_pixel(self, camera, px: float, py: float, width: float, height: float,
                   snap_radius: float = 1.5) -> PickResult | None:
        o, d = camera.ray(px, py, width, height)
        return self.pick(o, d, snap_radius)
