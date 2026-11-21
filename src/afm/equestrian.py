"""Caballos estáticos compatibles con figuras articuladas (`PLAN_caballos_compatibles.md`).

E01: modelos de datos del caballo (`HorseSpec`, `HorseMeta`) con ida y vuelta a JSON. Solo datos y validaciones: no
carga mallas (eso es E02) ni toca la figura. Este módulo no puede importar `manifold3d`: toda geometría pasa por
`solids.py` (prueba de portabilidad). No es una `montura` (`mount.py` es el soporte de armas).

Marco canónico (D3): Z arriba, Y hacia la cabeza del caballo, X lateral. `yaw` es el guiño en grados, sobre Z, que
lleva el STL a ese marco (`horse_baseless.stl` mira a −Y: guiño de 180°). La escala es un factor único: el caballo no
se normaliza a `ref_height` (D2) y no hay escalado no uniforme.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass, field

import numpy as np
import trimesh

from . import solids as S

HORSE_FORMAT = "afm-caballo-1"
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
REF_KINDS = ("alto_dorso", "largo", "alto_total")      # medida física de referencia que fija la escala


class HorseError(ValueError):
    """La ficha o la especificación del caballo no cumple el formato."""


def _num(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HorseError(f"«{name}» tiene que ser un número")
    v = float(value)
    if not math.isfinite(v):
        raise HorseError(f"«{name}» tiene que ser finito")
    return v


@dataclass(frozen=True)
class HorseSpec:
    """Cómo se coloca el STL de un caballo: archivo (con su SHA-256), escala, guiño y medida física de referencia."""

    archivo: str
    sha256: str
    escala: float = 1.0
    yaw: float = 0.0
    ref_tipo: str | None = None          # una de REF_KINDS
    ref_mm: float | None = None          # medida real en mm de esa referencia (tras escalar)

    def __post_init__(self):
        if not isinstance(self.archivo, str) or not self.archivo.strip():
            raise HorseError("falta «archivo»")
        if not isinstance(self.sha256, str) or not SHA_RE.match(self.sha256):
            raise HorseError("«sha256» tiene que ser un hash SHA-256 en hexadecimal minúscula (64 caracteres)")
        if not _num(self.escala, "escala") > 0:
            raise HorseError("«escala» tiene que ser mayor que 0")
        _num(self.yaw, "yaw")
        if (self.ref_tipo is None) != (self.ref_mm is None):
            raise HorseError("«ref_tipo» y «ref_mm» van juntos")
        if self.ref_tipo is not None:
            if self.ref_tipo not in REF_KINDS:
                raise HorseError(f"«ref_tipo» desconocido «{self.ref_tipo}» (hay {', '.join(REF_KINDS)})")
            if not _num(self.ref_mm, "ref_mm") > 0:
                raise HorseError("«ref_mm» tiene que ser mayor que 0")

    def to_dict(self) -> dict:
        d = {"archivo": self.archivo, "sha256": self.sha256, "escala": float(self.escala), "yaw": float(self.yaw)}
        if self.ref_tipo is not None:
            d["referencia"] = {"tipo": self.ref_tipo, "mm": float(self.ref_mm)}
        return d

    @classmethod
    def from_dict(cls, d) -> "HorseSpec":
        if not isinstance(d, dict):
            raise HorseError("la especificación del caballo tiene que ser un diccionario")
        ref = d.get("referencia")
        if ref is not None and (not isinstance(ref, dict) or set(ref) != {"tipo", "mm"}):
            raise HorseError("«referencia» tiene que ser {tipo, mm}")
        try:
            return cls(archivo=d["archivo"], sha256=d["sha256"], escala=d.get("escala", 1.0), yaw=d.get("yaw", 0.0),
                       ref_tipo=ref["tipo"] if ref else None, ref_mm=ref["mm"] if ref else None)
        except KeyError as exc:
            raise HorseError(f"falta «{exc.args[0]}»") from None


@dataclass(frozen=True)
class HorseMeta:
    """Ficha de un caballo de la biblioteca (`caballos/<id>/caballo.json`; no es una `PieceMeta`)."""

    id: str
    version: int
    spec: HorseSpec
    nombre: str = ""

    def __post_init__(self):
        if not isinstance(self.id, str) or not ID_RE.match(self.id):
            raise HorseError("«id» tiene que ser minúsculas, dígitos y guiones (p. ej. «caballo-pardo-01»)")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise HorseError("«version» tiene que ser un entero ≥ 1")
        if not isinstance(self.spec, HorseSpec):
            raise HorseError("«spec» tiene que ser un HorseSpec")
        if not isinstance(self.nombre, str) or any(ord(c) < 32 for c in self.nombre) or len(self.nombre) > 60:
            raise HorseError("«nombre» tiene que ser texto de hasta 60 caracteres sin caracteres de control")

    @property
    def ref(self) -> str:
        """Referencia `id@versión` que usa la receta."""
        return f"{self.id}@{self.version}"

    def to_dict(self) -> dict:
        d = {"formato": HORSE_FORMAT, "id": self.id, "version": self.version}
        if self.nombre:
            d["nombre"] = self.nombre
        d.update(self.spec.to_dict())
        return d

    @classmethod
    def from_dict(cls, d) -> "HorseMeta":
        if not isinstance(d, dict) or d.get("formato") != HORSE_FORMAT:
            raise HorseError(f"el formato tiene que ser «{HORSE_FORMAT}»")
        try:
            return cls(id=d["id"], version=d["version"], nombre=" ".join(str(d.get("nombre", "")).split()),
                       spec=HorseSpec.from_dict({k: v for k, v in d.items() if k not in ("formato", "id", "version", "nombre")}))
        except KeyError as exc:
            raise HorseError(f"falta «{exc.args[0]}»") from None


# ---------------------------------------------------------------------------------------------------------------- E02
DECIMATE_ABOVE = 60_000       # triángulos: por encima se arma también la malla liviana para booleanas y vista previa
LIGHT_TARGET = 30_000         # triángulos aproximados de esa malla liviana


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class HorseReport:
    """Qué se encontró al cargar el STL del caballo (mm, ya con escala y guiño)."""

    triangles: int = 0
    components: int = 0
    extents: tuple = (0.0, 0.0, 0.0)
    volume: float = 0.0
    was_watertight: bool = False
    repaired: bool = False
    warnings: list = field(default_factory=list)


@dataclass
class HorseModel:
    """Caballo cargado: malla escalada y girada al marco canónico (D3), su `Manifold` y, si es pesada, una versión liviana."""

    spec: HorseSpec
    mesh: trimesh.Trimesh
    manifold: object
    light: object                  # Manifold decimado (o el mismo si no hizo falta)
    report: HorseReport


def load_horse_mesh(spec: HorseSpec, base_dir: str = ".", repair: bool = True, check_hash: bool = True) -> HorseModel:
    """Carga el STL de `spec` (ruta relativa a `base_dir`), verifica el SHA-256, gira `yaw` grados sobre Z y escala por `escala`.

    No normaliza a `ref_height` ni recentra (D2): el caballo conserva su posición, sólo girada y escalada respecto del origen.
    Si la malla no es una variedad estanca y `repair` es True se intenta reparar (`meshio.repair`) y queda anotado en el
    informe; si no se puede (o `repair` es False) levanta `HorseError` con el motivo.
    """
    path = spec.archivo if os.path.isabs(spec.archivo) else os.path.join(base_dir, spec.archivo)
    if not os.path.isfile(path):
        raise HorseError(f"no existe el archivo del caballo: {path}")
    if check_hash and sha256_file(path) != spec.sha256:
        raise HorseError("el SHA-256 del STL no coincide con el de la ficha: el archivo cambió; revalidar el caballo")
    try:
        mesh = trimesh.load(path, file_type="stl", process=False, force="mesh")
    except Exception as exc:                                   # noqa: BLE001  (trimesh levanta tipos variados)
        raise HorseError(f"no se pudo leer el STL: {exc}") from None
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise HorseError("el STL no tiene triángulos")
    rep = HorseReport()
    mesh.merge_vertices()
    rep.was_watertight = bool(mesh.is_watertight)
    try:
        mf = S.to_manifold(mesh)
    except S.MeshNotManifold as exc:
        if not repair:
            raise HorseError(f"el caballo no es una malla estanca ({exc}); no se reparó") from None
        from . import meshio

        try:
            mesh = meshio.repair(mesh)
            mf = S.to_manifold(mesh)
        except Exception as exc2:                              # noqa: BLE001
            raise HorseError(f"el caballo no es una malla estanca y no se pudo reparar: {exc2}") from None
        rep.repaired = True
        rep.warnings.append("el STL del caballo no era estanco: se reparó")
    mesh = mesh.copy()
    mesh.apply_transform(trimesh.transformations.rotation_matrix(math.radians(spec.yaw), [0, 0, 1]))
    mesh.apply_scale(spec.escala)
    mf = S.to_manifold(mesh)
    rep.triangles = int(len(mesh.faces))
    rep.components = int(len(mesh.split(only_watertight=False)))
    rep.extents = tuple(float(x) for x in mesh.extents)
    rep.volume = float(abs(mf.volume()))
    if rep.components > 1:
        rep.warnings.append(f"el caballo tiene {rep.components} cáscaras")
    light = mf
    if rep.triangles > DECIMATE_ABOVE and S.can_decimate():
        light = S.decimate(mf, reduction=1.0 - LIGHT_TARGET / rep.triangles)
    return HorseModel(spec=spec, mesh=mesh, manifold=mf, light=light, report=rep)
