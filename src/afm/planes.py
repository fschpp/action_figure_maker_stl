"""Planos definidos por 3 puntos y utilidades de proyección."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Plane:
    """Plano con origen, normal unitaria y base (u, v) en el plano."""

    origin: np.ndarray
    normal: np.ndarray
    u: np.ndarray
    v: np.ndarray
    name: str = ""

    @classmethod
    def from_points(cls, p0, p1, p2, name: str = "") -> "Plane":
        p0, p1, p2 = (np.asarray(p, float) for p in (p0, p1, p2))
        n = np.cross(p1 - p0, p2 - p0)
        norm = np.linalg.norm(n)
        if norm < 1e-9:
            raise ValueError("Los 3 puntos son colineales: no definen un plano")
        n /= norm
        return cls.from_normal(p0, n, name)

    @classmethod
    def from_normal(cls, origin, normal, name: str = "") -> "Plane":
        n = np.asarray(normal, float)
        n = n / np.linalg.norm(n)
        hint = np.array([0.0, 0.0, 1.0])
        if abs(n @ hint) > 0.95:
            hint = np.array([0.0, 1.0, 0.0])
        v = hint - (hint @ n) * n
        v /= np.linalg.norm(v)
        u = np.cross(v, n)
        return cls(np.asarray(origin, float), n, u, v, name)

    def to_uv(self, pts) -> np.ndarray:
        d = np.atleast_2d(np.asarray(pts, float)) - self.origin
        return np.stack([d @ self.u, d @ self.v], axis=1)

    def to_world(self, uv, w: float = 0.0) -> np.ndarray:
        uv = np.atleast_2d(np.asarray(uv, float))
        return self.origin + uv[:, :1] * self.u + uv[:, 1:2] * self.v + w * self.normal

    def matrix(self) -> np.ndarray:
        """Matriz 4x4 local(u,v,w) -> mundo."""
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = self.u, self.v, self.normal, self.origin
        return m

    def signed_distance(self, pts) -> np.ndarray:
        return (np.atleast_2d(np.asarray(pts, float)) - self.origin) @ self.normal

    def to_dict(self) -> dict:
        return {"name": self.name, "origin": self.origin.tolist(), "normal": self.normal.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> "Plane":
        return cls.from_normal(d["origin"], d["normal"], d.get("name", ""))
