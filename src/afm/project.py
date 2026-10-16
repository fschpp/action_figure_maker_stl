"""Archivo de proyecto (.proyecto / JSON): planos, siluetas y parámetros (plan §4)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .head import HeadSpec
from .limbs import LimbSpec
from .params import Params, PinSpec
from .planes import Plane
from .trims import Trim


@dataclass
class Project:
    params: Params = field(default_factory=Params)
    symmetry: Plane | None = None
    crown_z: float | None = None          # cota de la coronilla en unidades del STL original
    front: str = "+Y"                     # hacia dónde mira la figura en el STL ("+Y" o "-Y")
    limbs: list[LimbSpec] = field(default_factory=list)
    trims: list[Trim] = field(default_factory=list)
    head: HeadSpec | None = None          # cabeza articulada (plano del cuello + pasador); None = cabeza fija al torso

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "params": self.params.to_dict(),
            "symmetry": None if self.symmetry is None else self.symmetry.to_dict(),
            "crown_z": self.crown_z,
            "front": self.front,
            "limbs": [l.to_dict() for l in self.limbs],
            "trims": [t.to_dict() for t in self.trims],
            "head": None if self.head is None else self.head.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(
            params=Params.from_dict(d.get("params", {})),
            symmetry=None if d.get("symmetry") is None else Plane.from_dict(d["symmetry"]),
            crown_z=d.get("crown_z"),
            front=d.get("front", "+Y"),
            limbs=[LimbSpec.from_dict(x) for x in d.get("limbs", [])],
            trims=[Trim.from_dict(x) for x in d.get("trims", [])],
            head=None if d.get("head") is None else HeadSpec.from_dict(d["head"]),
        )

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path) as f:
            return cls.from_dict(json.load(f))


def demo_project() -> Project:
    """Proyecto de ejemplo para la figura sintética (afm.testfigure)."""
    import numpy as np
    fp = Plane.from_normal((0, 0, 0), (0, 1, 0), "corte al medio")
    arm = LimbSpec("brazo", "arm", fp,
                   np.array([[15, 0, 43], [26, 0, 43], [26, 0, 76], [15, 0, 76]], float), 25.0)
    leg = LimbSpec("pierna", "leg", fp,
                   np.array([[0, 0, -1], [17, 0, -1], [17, 0, 34], [14, 0, 34], [14, 0, 42], [0, 0, 42]], float),
                   25.0)
    neck = HeadSpec(Plane.from_points((-5, -5, 80), (5, -5, 80), (0, 5, 80), "cabeza cuerpo"))
    # cuello del muñeco: Ø8 mm; pasador fino para dejar pared (vástago 3,2 + holgura)
    params = Params(neck_pin=PinSpec(3.2, 2.0, 5.2, 1.2))
    return Project(params=params, symmetry=Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría"), limbs=[arm, leg],
                   head=neck)
