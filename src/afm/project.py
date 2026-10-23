"""Archivo de proyecto (.proyecto / JSON): planos, siluetas y parámetros (plan §4)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .hand import HandSpec
from .head import HeadSpec
from .joints import JointSpec
from .limbs import LimbSpec
from .params import Params, PinSpec
from .planes import Plane
from .trims import Trim
from .waist import WaistSpec


@dataclass
class PartRef:
    """Pieza de catálogo elegida para un lugar del modelo (plan de piezas intercambiables, §3.4). Guarda la línea y el id
    (nunca rutas absolutas: el catálogo se resuelve con `--catalogo`, `$AFM_CATALOGO` o la ruta por defecto de la línea)."""

    id: str
    version: int | None = None            # None = la última al armar; el CLI la fija al elegirla
    linea: str | None = None

    @property
    def ref(self) -> str:
        return self.id if self.version is None else f"{self.id}@{self.version}"

    def to_dict(self) -> dict:
        return {"id": self.id, "version": self.version, "linea": self.linea}

    @classmethod
    def from_dict(cls, d: dict) -> "PartRef":
        return cls(id=d["id"], version=d.get("version"), linea=d.get("linea"))


@dataclass
class Project:
    params: Params = field(default_factory=Params)
    symmetry: Plane | None = None
    crown_z: float | None = None          # cota de la coronilla en unidades del STL original
    front: str = "+Y"                     # hacia dónde mira la figura en el STL ("+Y" o "-Y")
    limbs: list[LimbSpec] = field(default_factory=list)
    trims: list[Trim] = field(default_factory=list)
    joints: list[JointSpec] = field(default_factory=list)   # rodillas / codos (bisagras); vacío = miembro rígido
    head: HeadSpec | None = None          # cabeza articulada (plano del cuello + pasador); None = cabeza fija al torso
    waist: WaistSpec | None = None        # cintura articulada (plano de corte + pasador robusto); None = torso de una pieza
    hand: HandSpec | None = None          # mano estándar (STL) pegada en la muñeca; None = la mano original del modelo
    parts: dict[str, PartRef] = field(default_factory=dict)  # pieza de catálogo por nombre de miembro (LimbSpec.name)

    part_scales: dict[str, float] = field(default_factory=dict)  # escala uniforme de cada pieza ORIGINAL

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
            "waist": None if self.waist is None else self.waist.to_dict(),
            "hand": None if self.hand is None else self.hand.to_dict(),
            "joints": [j.to_dict() for j in self.joints],
            "parts": {k: v.to_dict() for k, v in self.parts.items()},
            "part_scales": dict(self.part_scales),
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
            waist=None if d.get("waist") is None else WaistSpec.from_dict(d["waist"]),
            hand=None if d.get("hand") is None else HandSpec.from_dict(d["hand"]),
            joints=[JointSpec.from_dict(x) for x in d.get("joints", [])],
            parts={k: PartRef.from_dict(v) for k, v in (d.get("parts") or {}).items()},
            part_scales={k: float(v) for k, v in (d.get("part_scales") or {}).items()},
        )

    def scale_geometry(self, k: float) -> None:
        """Multiplica por `k` todo lo que está medido sobre la figura (planos, polígonos, centros, espesores de
        las herramientas de corte, desplazamientos). No toca las medidas absolutas de impresión de `params`."""
        import numpy as np

        def pl(plane: Plane | None) -> Plane | None:
            return None if plane is None else Plane.from_normal(plane.origin * k, plane.normal, plane.name)

        def pt(p):
            return None if p is None else tuple(float(x) * k for x in p)

        def opt(x):
            return None if x is None else float(x) * k

        self.symmetry = pl(self.symmetry)
        for l in self.limbs:
            l.plane = pl(l.plane)
            l.polygon = np.asarray(l.polygon, float) * k
            l.thickness = float(l.thickness) * k
            l.pin_center = pt(l.pin_center)
            l.cut_offset = opt(l.cut_offset)
            l.cap_radius = opt(l.cap_radius)
            l.cap_center = pt(l.cap_center)
            if l.cross is not None:
                l.cross.plane = pl(l.cross.plane)
                l.cross.polygon = np.asarray(l.cross.polygon, float) * k
                l.cross.thickness = float(l.cross.thickness) * k
        for t in self.trims:
            t.plane = pl(t.plane)
            t.polygon = np.asarray(t.polygon, float) * k
            t.distance = float(t.distance) * k
        for j in self.joints:
            j.plane = pl(j.plane)
            j.axis_center = pt(j.axis_center)
        if self.head is not None:
            self.head.plane = pl(self.head.plane)
            self.head.pin_center = pt(self.head.pin_center)
        if self.waist is not None:
            self.waist.plane = pl(self.waist.plane)
            self.waist.pin_center = pt(self.waist.pin_center)
        if self.hand is not None:
            self.hand.points = tuple(pt(q) for q in self.hand.points)       # el STL de la mano y el solape son mm reales

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
