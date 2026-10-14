"""Herramienta genérica de recorte manual (plan §8.8).

Un recorte = polígono sobre un plano (por defecto el de simetría) -> extrusión -> booleano
(restar o unir) sobre una pieza (torso o miembro). Se aplica sobre el lado de trabajo, así
el espejado lo replica.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import solids as S
from .planes import Plane


@dataclass
class Trim:
    target: str                 # "torso" o nombre de un miembro (p. ej. "pierna")
    plane: Plane
    polygon: np.ndarray         # Nx3 sobre el plano
    distance: float = 25.0      # espesor de la extrusión
    symmetric: bool = True      # simétrica respecto del plano; si no, hacia +normal
    op: str = "subtract"        # "subtract" | "union"
    shape: str = "polygon"      # "polygon" | "sphere" | "box" | "cylinder" (formas predefinidas)
    note: str = ""

    def tool(self) -> S.Manifold:
        if self.shape == "polygon":
            return S.prism_from_polygon(self.plane, self.polygon, self.distance, self.symmetric)
        pts = np.asarray(self.polygon, float)
        c = pts.mean(axis=0)
        r = float(np.linalg.norm(pts - c, axis=1).max())
        if self.shape == "sphere":
            return S.Manifold.sphere(r, 48).translate(tuple(c))
        if self.shape == "box":
            lo, hi = pts.min(axis=0), pts.max(axis=0)
            return S.Manifold.cube(tuple(hi - lo), center=False).translate(tuple(lo))
        if self.shape == "cylinder":
            return S.cylinder_along(c - self.plane.normal * self.distance / 2, self.plane.normal,
                                    r, self.distance)
        raise ValueError(f"Forma desconocida: {self.shape}")

    def apply(self, mf: S.Manifold) -> S.Manifold:
        tool = self.tool()
        if self.op == "subtract":
            return S.difference(mf, tool)
        if self.op == "union":
            return S.union(mf, tool)
        raise ValueError(f"Operación desconocida: {self.op}")

    def to_dict(self) -> dict:
        return {"target": self.target, "plane": self.plane.to_dict(),
                "polygon": np.asarray(self.polygon).tolist(), "distance": self.distance,
                "symmetric": self.symmetric, "op": self.op, "shape": self.shape, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict) -> "Trim":
        return cls(d["target"], Plane.from_dict(d["plane"]), np.asarray(d["polygon"], float),
                   d.get("distance", 25.0), d.get("symmetric", True), d.get("op", "subtract"),
                   d.get("shape", "polygon"), d.get("note", ""))


def apply_trims(mf: S.Manifold, trims: list[Trim], target: str) -> S.Manifold:
    for t in trims:
        if t.target == target:
            mf = t.apply(mf)
    return mf
