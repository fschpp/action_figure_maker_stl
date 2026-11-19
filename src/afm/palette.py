"""Paleta de color de una línea de figuras: roles («piel», «uniforme», «botas») con un color actual y anclas (T38).

Un **rol** es un identificador estable con un color editable (`hex`) y una lista de **anclas**: colores de referencia en que se vio ese rol
(sirven para sugerir el rol en la próxima figura y no cambian cuando se recolorea). Las piezas guardan ids de rol, nunca RGB; recolorear es
cambiar el `hex` de un rol (`PLAN_paleta_color_linea.md` §5.1, §6, §7).

Módulo puro: no toca el disco y no importa PIL, trimesh ni skimage al cargarse (`nearest` importa `skimage.color` solo cuando se llama).
Los errores son `PaletteError` (un `ValueError`) con el motivo legible.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

import numpy as np

FORMAT = "afm-paleta-1"
MAX_ROLES = 64            # una etiqueta cabe en 8 bits y queda libre el 255 para «sin rol»
MAX_ANCHORS = 8
NO_ROLE = 255             # etiqueta de «sin rol»: sale del color plano
DEFAULT_FLAT = "#C8C8C8"  # el mismo valor que `colorbake.DEFAULT_FLAT` (no se importa para no cargar scipy; una prueba los compara)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class PaletteError(ValueError):
    """Paleta inválida o rol inexistente; el mensaje dice cuál es el problema."""


def normalize_hex(value, what: str = "color") -> str:
    """`#RRGGBB` en mayúscula. Acepta minúscula; todo lo demás es `PaletteError`."""
    if not isinstance(value, str) or not _HEX_RE.match(value):
        raise PaletteError(f"{what}: {value!r} no es un color «#RRGGBB»")
    return value.upper()


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = normalize_hex(value)
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


@dataclass
class Role:
    id: str
    nombre: str
    hex: str
    anclas: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.id, str) or not _ID_RE.match(self.id):
            raise PaletteError(f"id de rol inválido: {self.id!r} (minúsculas, dígitos y «_»; empieza con letra; hasta 32 caracteres)")
        if not isinstance(self.nombre, str) or not self.nombre.strip():
            raise PaletteError(f"rol «{self.id}»: el nombre no puede estar vacío")
        self.hex = normalize_hex(self.hex, f"rol «{self.id}»")
        if not isinstance(self.anclas, (list, tuple)):
            raise PaletteError(f"rol «{self.id}»: las anclas deben ser una lista")
        if len(self.anclas) > MAX_ANCHORS:
            raise PaletteError(f"rol «{self.id}»: {len(self.anclas)} anclas (máximo {MAX_ANCHORS})")
        self.anclas = [normalize_hex(a, f"ancla del rol «{self.id}»") for a in self.anclas]

    def to_dict(self) -> dict:
        return {"id": self.id, "nombre": self.nombre, "hex": self.hex, "anclas": list(self.anclas)}


@dataclass
class Palette:
    roles: list[Role] = field(default_factory=list)

    def __post_init__(self):
        self.roles = list(self.roles)
        if len(self.roles) > MAX_ROLES:
            raise PaletteError(f"{len(self.roles)} roles (máximo {MAX_ROLES})")
        seen: set[str] = set()
        for r in self.roles:
            if r.id in seen:
                raise PaletteError(f"el rol «{r.id}» está repetido")
            seen.add(r.id)

    # ------------------------------------------------------------------ JSON
    def to_dict(self) -> dict:
        return {"formato": FORMAT, "roles": [r.to_dict() for r in self.roles]}

    @classmethod
    def from_dict(cls, d) -> "Palette":
        """Valida y normaliza (hex en mayúscula). `nombre` y `anclas` son opcionales (por defecto, el id y ninguna)."""
        if not isinstance(d, dict):
            raise PaletteError("la paleta debe ser un objeto JSON")
        if d.get("formato") != FORMAT:
            raise PaletteError(f"formato de paleta desconocido: {d.get('formato')!r} (se esperaba «{FORMAT}»)")
        roles = d.get("roles")
        if not isinstance(roles, list):
            raise PaletteError("la paleta no trae la lista «roles»")
        out = []
        for i, r in enumerate(roles):
            if not isinstance(r, dict) or "id" not in r or "hex" not in r:
                raise PaletteError(f"rol #{i + 1}: debe ser un objeto con «id» y «hex»")
            out.append(Role(id=r["id"], nombre=r.get("nombre", r["id"]), hex=r["hex"], anclas=r.get("anclas", [])))
        return cls(out)

    # ------------------------------------------------------------------ consulta
    def ids(self) -> list[str]:
        return [r.id for r in self.roles]

    def get(self, role_id: str) -> Role:
        for r in self.roles:
            if r.id == role_id:
                return r
        raise PaletteError(f"la paleta no tiene el rol «{role_id}»")

    def hex_of(self, role_id: str) -> str:
        return self.get(role_id).hex

    def lut(self, role_ids: list[str], flat: str = DEFAULT_FLAT) -> np.ndarray:
        """Tabla (256, 3) uint8: la fila `i` es el color del rol `role_ids[i]`; las demás filas y la 255 («sin rol») son `flat`.

        Un rol que la paleta no tiene → `PaletteError` con la lista de los que faltan (no se inventa color)."""
        role_ids = list(role_ids)
        if len(role_ids) > NO_ROLE:
            raise PaletteError(f"{len(role_ids)} roles: una etiqueta de 8 bits admite hasta {NO_ROLE}")
        have = {r.id: r.hex for r in self.roles}
        missing = [r for r in dict.fromkeys(role_ids) if r not in have]
        if missing:
            raise PaletteError("faltan roles en la paleta: " + ", ".join(missing))
        table = np.empty((256, 3), np.uint8)
        table[:] = hex_to_rgb(flat)
        for i, rid in enumerate(role_ids):
            table[i] = hex_to_rgb(have[rid])
        return table

    def with_overrides(self, overrides: dict) -> "Palette":
        """Copia con otros `hex` (`{rol: "#RRGGBB"}`); la paleta original no cambia. Un rol que no existe es `PaletteError`."""
        overrides = dict(overrides or {})
        missing = [r for r in overrides if r not in self.ids()]
        if missing:
            raise PaletteError("no se puede cambiar el color de roles que no existen: " + ", ".join(missing))
        return Palette([Role(r.id, r.nombre, overrides.get(r.id, r.hex), list(r.anclas)) for r in self.roles])

    def digest(self) -> str:
        """SHA-256 de los colores de la paleta: JSON normalizado con los roles ordenados por id y cada `hex` en mayúscula.

        Solo entran `id` y `hex`: renombrar un rol, agregarle anclas o reordenar la lista no cambia el color de nada, así que no cambia el hash
        (es el que va al informe de color para poder repetir una exportación, §5.6)."""
        norm = [{"id": r.id, "hex": r.hex} for r in sorted(self.roles, key=lambda r: r.id)]
        return hashlib.sha256(json.dumps(norm, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def nearest(self, rgb, max_delta_e: float | None = None) -> tuple[str, float] | None:
        """`(id, ΔE)` del rol cuya **ancla** está más cerca de `rgb` (Lab, distancia euclídea), o `None` si no hay anclas o ninguna llega
        a `max_delta_e`. `rgb`: tres valores 0–255 o «#RRGGBB». Es solo una sugerencia: nunca asigna nada (§4, R-1)."""
        pairs = [(r.id, a) for r in self.roles for a in r.anclas]
        if not pairs:
            return None
        from skimage.color import rgb2lab                    # pesado: solo al llamar

        q = hex_to_rgb(rgb) if isinstance(rgb, str) else tuple(float(c) for c in rgb)
        if len(q) != 3:
            raise PaletteError(f"color inválido: {rgb!r}")
        pts = np.array([q] + [hex_to_rgb(a) for _, a in pairs], float).reshape(1, -1, 3) / 255.0
        lab = rgb2lab(pts)[0]
        d = np.linalg.norm(lab[1:] - lab[0], axis=1)
        k = int(np.argmin(d))                                # empate: la primera ancla (orden de la paleta)
        if max_delta_e is not None and d[k] > max_delta_e:
            return None
        return pairs[k][0], float(d[k])
