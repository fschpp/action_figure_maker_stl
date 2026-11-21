"""E12: la «cabalgadura» de una receta: qué caballo, dónde se sienta la figura y cómo se adapta el caballo (plano 7.2).

`RideSpec` es solo datos (ida y vuelta a JSON, validaciones, conversión a los objetos del motor). Va en `Recipe.cabalgadura`,
fuera de `ajustes` (mismo patrón que `accesorios` y `paleta_override`) y NUNCA entra en `compat.recipe_key` (D7): el armado de
la figura no depende del caballo. El conjunto caballo + jinete tiene su propia clave (`riding_key`).
Este módulo no importa `manifold3d` (solo `solids` a través de `equestrian`).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

import numpy as np

from . import solids as S
from .equestrian import (FIT_MODES, SAFE_MAX_DEPTH, CarveLimits, HorseError, HorseMeta, ReliefSpec, RidingAssembly, SaddleError,
                         SaddleSpec)
from .horse_library import HorseLibrary, parse_horse_ref

RIDE_KEYS = ("caballo", "sha256", "asiento", "direccion", "inclinacion", "modo", "silla", "relieve", "holgura", "limites",
             "hash_geometria")
_LIMIT_KEYS = ("max_depth", "min_wall", "max_removed_frac", "zone")


def _vec3(v, name: str) -> tuple:
    if isinstance(v, (str, bytes, dict)) or not hasattr(v, "__len__") or len(v) != 3:
        raise HorseError(f"«{name}» tiene que ser [x, y, z]")
    out = []
    for x in v:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise HorseError(f"«{name}» tiene que ser tres números finitos")
        out.append(float(x))
    return tuple(out)


@dataclass(frozen=True)
class RideSpec:
    """Cabalgadura de una receta. `caballo` es `id@versión` (la versión es obligatoria: la receta fija exactamente un caballo) y
    `sha256` el hash de su STL. `asiento` y `direccion` están en el marco del caballo (D3, mm); `inclinacion` en grados.
    `silla`/`relieve` solo con modo `silla` o `ambos`, `limites` solo con `tallado` o `ambos`. `holgura` None = `Params.clearance`.
    `hash_geometria` es el de la geometría derivada (caballo tallado y/o silla) de la última vez que se validó (`geometry_hash`)."""

    caballo: str
    sha256: str
    asiento: tuple
    direccion: tuple = (0.0, 1.0, 0.0)
    inclinacion: float = 0.0
    modo: str = "tallado"
    silla: SaddleSpec | None = None
    relieve: ReliefSpec | None = None
    holgura: float | None = None
    limites: CarveLimits | None = None
    hash_geometria: str | None = None

    def __post_init__(self):
        hid, v = parse_horse_ref(self.caballo)
        if v is None:
            raise HorseError("«caballo» tiene que traer la versión (id@versión)")
        if not isinstance(self.sha256, str) or len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise HorseError("«sha256» tiene que ser un hash SHA-256 en hexadecimal minúscula (64 caracteres)")
        object.__setattr__(self, "asiento", _vec3(self.asiento, "asiento"))
        d = _vec3(self.direccion, "direccion")
        if math.hypot(d[0], d[1]) < 1e-6:
            raise HorseError("«direccion» tiene que tener una parte horizontal (XY) no nula")
        object.__setattr__(self, "direccion", d)
        if isinstance(self.inclinacion, bool) or not isinstance(self.inclinacion, (int, float)) or not math.isfinite(self.inclinacion):
            raise HorseError("«inclinacion» tiene que ser un número")
        object.__setattr__(self, "inclinacion", float(self.inclinacion))
        if self.modo not in FIT_MODES:
            raise HorseError(f"«modo» tiene que ser uno de {', '.join(FIT_MODES)}")
        saddle, carve = self.modo in ("silla", "ambos"), self.modo in ("tallado", "ambos")
        if (self.silla is not None or self.relieve is not None) and not saddle:
            raise HorseError("«silla» y «relieve» solo van con el modo «silla» o «ambos»")
        if self.limites is not None and not carve:
            raise HorseError("«limites» solo va con el modo «tallado» o «ambos»")
        if self.silla is not None and not isinstance(self.silla, SaddleSpec):
            raise HorseError("«silla» tiene que ser un SaddleSpec")
        if self.relieve is not None and not isinstance(self.relieve, ReliefSpec):
            raise HorseError("«relieve» tiene que ser un ReliefSpec")
        if self.limites is not None:
            if not isinstance(self.limites, CarveLimits):
                raise HorseError("«limites» tiene que ser un CarveLimits")
            if self.limites.max_depth > SAFE_MAX_DEPTH:
                raise HorseError(f"«max_depth» {self.limites.max_depth:g} mm pasa el máximo seguro de {SAFE_MAX_DEPTH:g} mm")
        if self.holgura is not None:
            if isinstance(self.holgura, bool) or not isinstance(self.holgura, (int, float)) or not math.isfinite(self.holgura) or self.holgura < 0:
                raise HorseError("«holgura» tiene que ser un número ≥ 0 (mm)")
            object.__setattr__(self, "holgura", float(self.holgura))
        if self.hash_geometria is not None and (not isinstance(self.hash_geometria, str) or len(self.hash_geometria) != 64):
            raise HorseError("«hash_geometria» tiene que ser un SHA-256 en hexadecimal (64 caracteres)")

    # ------------------------------------------------------------------ formato
    def to_dict(self) -> dict:
        d = {"caballo": self.caballo, "sha256": self.sha256, "asiento": list(self.asiento), "direccion": list(self.direccion),
             "inclinacion": self.inclinacion, "modo": self.modo}
        if self.silla is not None:                       # solo si hay: la receta no lleva claves vacías
            d["silla"] = self.silla.to_dict()
        if self.relieve is not None:
            d["relieve"] = self.relieve.to_dict()
        if self.holgura is not None:
            d["holgura"] = self.holgura
        if self.limites is not None:
            lim = {k: getattr(self.limites, k) for k in _LIMIT_KEYS}
            lim["zone"] = list(lim["zone"]) if lim["zone"] is not None else None
            d["limites"] = {k: v for k, v in lim.items() if v is not None}
        if self.hash_geometria is not None:
            d["hash_geometria"] = self.hash_geometria
        return d

    @classmethod
    def from_dict(cls, d) -> "RideSpec":
        if not isinstance(d, dict):
            raise HorseError("la cabalgadura tiene que ser un diccionario")
        extra = set(d) - set(RIDE_KEYS)
        if extra:
            raise HorseError(f"campos desconocidos en la cabalgadura: {', '.join(sorted(extra))}")
        for k in ("caballo", "sha256", "asiento"):
            if k not in d:
                raise HorseError(f"a la cabalgadura le falta «{k}»")
        lim = d.get("limites")
        if lim is not None:
            if not isinstance(lim, dict) or set(lim) - set(_LIMIT_KEYS):
                raise HorseError(f"«limites» tiene que ser un diccionario con {', '.join(_LIMIT_KEYS)}")
            lim = CarveLimits(**lim)
        try:
            return cls(caballo=d["caballo"], sha256=d["sha256"], asiento=d["asiento"], direccion=d.get("direccion", (0.0, 1.0, 0.0)),
                       inclinacion=d.get("inclinacion", 0.0), modo=d.get("modo", "tallado"),
                       silla=SaddleSpec.from_dict(d["silla"]) if d.get("silla") is not None else None,
                       relieve=ReliefSpec.from_dict(d["relieve"]) if d.get("relieve") is not None else None,
                       holgura=d.get("holgura"), limites=lim, hash_geometria=d.get("hash_geometria"))
        except (SaddleError, TypeError) as exc:
            raise HorseError(str(exc)) from None

    # ------------------------------------------------------------------ hacia el motor
    def carve_limits(self, min_wall: float = 1.2) -> CarveLimits:
        """Los límites del tallado (los de la receta o los de siempre con la pared mínima de la línea)."""
        return self.limites if self.limites is not None else CarveLimits(min_wall=min_wall)

    def fit_kwargs(self) -> dict:
        """Argumentos de `RidingFitter.fit` (`seat_origin`, `direction`, `pitch_deg`, `mode`, `saddle_spec`, `relief_spec`)."""
        return {"seat_origin": self.asiento, "direction": self.direccion, "pitch_deg": self.inclinacion, "mode": self.modo,
                "saddle_spec": self.silla, "relief_spec": self.relieve}

    def with_geometry_hash(self, h: str | None) -> "RideSpec":
        import dataclasses

        return dataclasses.replace(self, hash_geometria=h)


def riding_key(base_key: str, ride: RideSpec) -> str:
    """Clave propia del armado ecuestre (D7): `sha256(clave de la receta + caballo con su hash + todo lo que fija el asiento y la
    adaptación)`. `base_key` es `compat.recipe_key(recipe, cat)[0]`, que no cambia con el caballo. No incluye `hash_geometria`
    (es un resultado, no una entrada)."""
    d = ride.to_dict()
    d.pop("hash_geometria", None)
    blob = json.dumps({"receta": base_key, "cabalgadura": d}, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def resolve_horse(ride: RideSpec, lib: HorseLibrary) -> tuple[HorseMeta, list]:
    """Ficha del caballo de la receta en la biblioteca y los avisos: si el hash de la ficha no es el de la receta, el caballo
    cambió y hay que revalidar (plano 7.2). Levanta `HorseError` si el caballo o su versión no están."""
    meta = lib.meta(ride.caballo)
    warns = []
    if meta.spec.sha256 != ride.sha256:
        warns.append(f"el caballo «{meta.ref}» no tiene el SHA-256 que guardó la receta: cambió; revalidar el conjunto")
    return meta, warns


def _digest(mf) -> "hashlib._Hash":
    tm = S.to_trimesh(mf)
    q = np.round(np.asarray(tm.vertices, float) * 1000.0).astype(np.int64)          # µm: por debajo de cualquier resolución de impresión
    uniq, inv = np.unique(q, axis=0, return_inverse=True)
    f = np.sort(np.asarray(inv).reshape(-1)[np.asarray(tm.faces)], axis=1)           # sin orden de vértices ni de caras
    f = f[np.lexsort(f.T[::-1])]
    h = hashlib.sha256()
    h.update(uniq.tobytes())
    h.update(f.astype(np.int64).tobytes())
    return h


def geometry_hash(ra: RidingAssembly) -> str:
    """SHA-256 de la geometría derivada del conjunto: el caballo tallado (si se talló) y la silla final (si hay). No depende del
    orden de vértices ni de caras. Lo produce el motor: otra versión del motor puede dar otro hash con la misma figura, así que
    una diferencia es un aviso para revalidar, no un error."""
    parts = []
    if ra.carve is not None and ra.carve.carved is not None:
        parts.append(("caballo", ra.carve.carved))
    if ra.saddle is not None and ra.saddle_mf is not None:
        parts.append(("silla", ra.saddle_mf))
    if not parts:
        raise HorseError("el conjunto no tiene geometría derivada (ni tallado ni silla)")
    h = hashlib.sha256()
    for name, mf in parts:
        h.update(name.encode())
        h.update(_digest(mf).digest())
    return h.hexdigest()


def geometry_warnings(ride: RideSpec, ra: RidingAssembly) -> list:
    """Avisos si la geometría recalculada no es la que se validó (`hash_geometria` de la receta)."""
    if ride.hash_geometria is None:
        return []
    if geometry_hash(ra) != ride.hash_geometria:
        return ["la geometría derivada (caballo tallado / silla) no coincide con la última validada: revalidar el conjunto"]
    return []
