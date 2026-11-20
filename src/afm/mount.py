"""Monturas: geometría generada que engancha un arma (accesorio) a una parte del cuerpo de la figura (M01 de
`docs/PLAN_enganche_armas_cuerpo.md`).

Este módulo empieza por los **datos**: `MountSpec` (qué arma, dónde, hacia dónde apunta, qué tipo de enganche y con qué juego)
y sus constantes de defecto. La geometría (M02–M04) y la integración en `pipeline.build` (M05) vienen después.

Los defectos de holgura y pared viven acá y **no** en `Params` (plan §6.3): `recipe_key` hashea `Params`, y un campo nuevo
dejaría «vencida» la certificación de cada figura que no usa monturas.

Marcos: `ancla` y `direccion` están en el marco de la pieza huésped (la misma que se ve en `BuildResult.pieces`). `direccion`
es el eje del arma de la empuñadura hacia la punta; el arma, en su marco de accesorio, tiene la empuñadura en +X y la hoja
en x < 0 (ver `weapon.py`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MOUNT_FORMAT = "afm-montura-1"
MOUNT_PLACES = ("pelvis", "torso", "brazo", "pierna", "cabeza")
MOUNT_SIDES = ("izq", "der")                       # izq = +X (lado de trabajo), der = −X (por espejo)
MOUNT_TYPES = ("lazo", "clip", "cuna")
MOUNT_FIELDS = ("arma", "lugar", "lado", "ancla", "direccion", "tipo", "holgura", "pared", "estaciones")

DEFAULT_CLEARANCE = 0.25                           # mm de juego entre la hoja y el hueco del puente
DEFAULT_STATIONS = 1
MAX_CLEARANCE = 2.0                                # por encima ya no retiene nada: se considera un error de tipeo
MAX_STATIONS = 2


class MountError(ValueError):
    """La montura está mal formada o pide algo que no se puede generar."""


def _vec3(value, name: str) -> tuple:
    try:
        v = tuple(float(c) for c in value)
    except (TypeError, ValueError) as exc:
        raise MountError(f"«{name}» tiene que ser una lista de 3 números") from exc
    if len(v) != 3 or not all(math.isfinite(c) for c in v):
        raise MountError(f"«{name}» tiene que ser una lista de 3 números finitos")
    return v


@dataclass(frozen=True)
class MountSpec:
    """Una montura: dónde y cómo se engancha un arma al cuerpo. Inmutable: cambia la receta, no el objeto."""

    arma: str                                      # `id@versión` de un accesorio que figura en `Recipe.accesorios`
    lugar: str                                     # una de MOUNT_PLACES
    ancla: tuple                                   # (x, y, z) sobre la superficie de la pieza huésped
    direccion: tuple                               # eje del arma (empuñadura → punta) en el marco de la pieza
    lado: str = "izq"
    tipo: str = "lazo"
    holgura: float = DEFAULT_CLEARANCE             # mm, juego hoja–hueco
    pared: float | None = None                     # mm; None = `Params.min_wall` (se resuelve con `wall`)
    estaciones: int = DEFAULT_STATIONS             # puentes a lo largo de la hoja

    def __post_init__(self):
        # normaliza a tuplas de floats (también si llegan como listas del JSON) y valida
        object.__setattr__(self, "ancla", _vec3(self.ancla, "ancla"))
        object.__setattr__(self, "direccion", _vec3(self.direccion, "direccion"))
        bad = self.problems()
        if bad:
            raise MountError("; ".join(bad))

    # ------------------------------------------------------------------ validación
    def problems(self) -> list:
        from .catalog import CatalogError, parse_ref

        out = []
        try:
            parse_ref(self.arma)
        except (CatalogError, TypeError, AttributeError) as exc:
            out.append(f"arma «{self.arma}»: {exc}")
        if self.lugar not in MOUNT_PLACES:
            out.append(f"lugar «{self.lugar}» desconocido (hay {', '.join(MOUNT_PLACES)})")
        if self.lado not in MOUNT_SIDES:
            out.append(f"lado «{self.lado}» desconocido (hay {', '.join(MOUNT_SIDES)})")
        if self.tipo not in MOUNT_TYPES:
            out.append(f"tipo «{self.tipo}» desconocido (hay {', '.join(MOUNT_TYPES)})")
        if math.sqrt(sum(c * c for c in self.direccion)) < 1e-9:
            out.append("«direccion» no puede ser el vector nulo")
        if isinstance(self.holgura, bool) or not isinstance(self.holgura, (int, float)) or \
                not (0.0 <= self.holgura <= MAX_CLEARANCE):
            out.append(f"holgura {self.holgura!r}: tiene que ser un número entre 0 y {MAX_CLEARANCE} mm")
        if self.pared is not None and (isinstance(self.pared, bool) or not isinstance(self.pared, (int, float))
                                       or not (self.pared > 0 and math.isfinite(self.pared))):
            out.append(f"pared {self.pared!r}: tiene que ser un número mayor que 0 (o ausente para usar min_wall)")
        if isinstance(self.estaciones, bool) or not isinstance(self.estaciones, int) or \
                not (1 <= self.estaciones <= MAX_STATIONS):
            out.append(f"estaciones {self.estaciones!r}: tiene que ser 1 o {MAX_STATIONS}")
        return out

    # ------------------------------------------------------------------ derivados
    def wall(self, min_wall: float) -> float:
        """Espesor del puente: el pedido o, si no se pidió, la pared mínima de la línea."""
        return float(self.pared) if self.pared is not None else float(min_wall)

    def unit_direction(self) -> tuple:
        n = math.sqrt(sum(c * c for c in self.direccion))
        return tuple(c / n for c in self.direccion)

    # ------------------------------------------------------------------ formato
    def to_dict(self) -> dict:
        """Diccionario JSON. Siempre completo (`pared` nula si usa `min_wall`) para que `recipe_key` sea estable."""
        return {"arma": self.arma, "lugar": self.lugar, "lado": self.lado, "ancla": list(self.ancla),
                "direccion": list(self.direccion), "tipo": self.tipo, "holgura": float(self.holgura),
                "pared": None if self.pared is None else float(self.pared), "estaciones": int(self.estaciones)}

    @classmethod
    def from_dict(cls, d: dict) -> "MountSpec":
        if not isinstance(d, dict):
            raise MountError("una montura tiene que ser un diccionario")
        unknown = sorted(k for k in d if k not in MOUNT_FIELDS)
        if unknown:
            raise MountError(f"campo desconocido en la montura: {', '.join(unknown)} (hay {', '.join(MOUNT_FIELDS)})")
        missing = [k for k in ("arma", "lugar", "ancla", "direccion") if k not in d]
        if missing:
            raise MountError(f"a la montura le falta: {', '.join(missing)}")
        return cls(arma=d["arma"], lugar=d["lugar"], ancla=d["ancla"], direccion=d["direccion"],
                   lado=d.get("lado", "izq"), tipo=d.get("tipo", "lazo"),
                   holgura=d.get("holgura", DEFAULT_CLEARANCE), pared=d.get("pared"),
                   estaciones=d.get("estaciones", DEFAULT_STATIONS))
