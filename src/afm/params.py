"""Parámetros y perfil de línea (plan §5.2 y §8.12). Todo en mm finales (figura de 100 mm)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict


@dataclass
class PinSpec:
    shaft_d: float
    shaft_len: float
    head_d: float
    head_h: float


@dataclass
class Params:
    ref_height: float = 100.0
    clearance: float = 0.50          # holgura cavidad vs. miembro (0,495 medido en el STL de Mickey)
    burr: float = 0.66               # rebaba a rebajar en la cara de contacto
    shoulder_pin: PinSpec = field(default_factory=lambda: PinSpec(4.1, 2.5, 6.6, 1.65))
    hip_pin: PinSpec = field(default_factory=lambda: PinSpec(5.0, 4.1, 6.6, 2.5))
    sit_angle: float = 90.0          # pose sentada (hacia adelante)
    hip_margin: float = 5.0          # recorte del límite de cadera hasta sit_angle + margen
    sweep_step: float = 5.0          # paso angular del volumen barrido
    min_wall: float = 1.2            # espesor mínimo de pared (validación)
    min_pin_d: float = 3.0           # diámetro mínimo de pasador (validación)
    nozzle: float = 0.4
    decimate: float = 0.6            # reducción de triángulos de las piezas al exportar (0 = desactivar)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        d = dict(d)
        for k in ("shoulder_pin", "hip_pin"):
            if k in d and isinstance(d[k], dict):
                d[k] = PinSpec(**d[k])
        return cls(**d)

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Params":
        with open(path) as f:
            return cls.from_dict(json.load(f))
