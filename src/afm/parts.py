"""Piezas crudas con puertos (plan de piezas intercambiables, §3.2 y Apéndice A).

Una pieza es una malla CRUDA (sin pasadores, cavidades, clic ni lengüetas: eso se genera al armar con los parámetros de
la línea) más un JSON `afm-pieza-1` con sus puertos. Este módulo solo define el formato y su lectura/escritura; el
orden en disco y las versiones los maneja `catalog.py`.

Convenciones:

* Mm reales, sin reescalar. `altura_figura_mm` es la altura de la figura de origen (pies → coronilla).
* Lado de trabajo +X (izquierda); la derecha sale por espejo. Las piezas asimétricas llevan `espejable: false`.
* La malla se guarda en el marco de la figura de origen (Z arriba, +Y frente, x = 0 simetría); la mano estándar
  (`afm-mano-1`) conserva su marco local con el origen en la muñeca.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from dataclasses import dataclass, field

import numpy as np

from . import __version__
from . import solids as S
from .ports import PORT_CLASSES, Port, PortError, PortFrame, face_from_mesh, port_class

PIECE_FORMAT = "afm-pieza-1"
TYPES = ("cabeza", "torso", "torso_sup", "pelvis", "brazo", "pierna", "mano",
         "antebrazo", "pantorrilla", "bota", "accesorio")
STATES = ("borrador", "validada", "impresa")
TAGS = ("estandar", "propia")
SIDES = ("izq", "der", "centro")
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Puertos por tipo de pieza: nombre -> (rol, requerido). El nombre empieza con la clase del puerto (`hombro_izq`).
PORT_SPEC: dict[str, dict[str, tuple[str, bool]]] = {
    "cabeza": {"cuello": ("enchufe", True)},
    "torso": {"cuello": ("toma", False), "hombro_izq": ("toma", True), "hombro_der": ("toma", True),
              "cadera_izq": ("toma", True), "cadera_der": ("toma", True)},
    "torso_sup": {"cuello": ("toma", False), "hombro_izq": ("toma", True), "hombro_der": ("toma", True),
                  "cintura": ("toma", True)},
    "pelvis": {"cintura": ("enchufe", True), "cadera_izq": ("toma", True), "cadera_der": ("toma", True)},
    "brazo": {"hombro": ("enchufe", True), "muneca": ("toma", False), "codo": ("enchufe", False)},
    "pierna": {"cadera": ("enchufe", True), "rodilla": ("enchufe", False)},
    "mano": {"muneca": ("enchufe", True)},
    "antebrazo": {"codo": ("toma", True), "muneca": ("toma", False)},
    "pantorrilla": {"rodilla": ("toma", True)},
    "bota": {"rodilla": ("enchufe", True)},
    "accesorio": {},
}


class PieceError(ValueError):
    """La pieza no cumple el formato (tipo desconocido, puertos que faltan, hash que no coincide…)."""


def stl_name(piece_id: str, version: int) -> str:
    return f"{piece_id}@{version}.stl"


def meta_name(piece_id: str, version: int) -> str:
    return f"{piece_id}@{version}.pieza.json"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class PieceMeta:
    """Metadatos `afm-pieza-1` de una pieza (ver Apéndice A del plan)."""

    id: str
    version: int
    tipo: str
    puertos: dict                       # nombre -> Port
    descripcion: str = ""
    origen: dict = field(default_factory=dict)
    altura_figura_mm: float | None = None
    lado: str = "izq"
    espejable: bool = True
    stl: str = ""
    sha256: str = ""
    volumen_mm3: float = 0.0
    bbox: list = field(default_factory=lambda: [[0.0] * 3, [0.0] * 3])
    articulaciones: list = field(default_factory=list)    # JointSpec internos, en el marco de la pieza (más adelante)
    mecanismo: dict | None = None
    estado: str = "borrador"
    etiquetas: list = field(default_factory=list)

    @property
    def ref(self) -> str:
        """Referencia inmutable `id@version`."""
        return f"{self.id}@{self.version}"

    def problems(self) -> list[str]:
        """Problemas de formato (no abre la malla)."""
        out: list[str] = []
        if not ID_RE.match(self.id or ""):
            out.append(f"id inválido «{self.id}»: minúsculas, números y guiones (p. ej. pierna-botas-01)")
        if self.version < 1:
            out.append(f"versión inválida: {self.version}")
        if self.tipo not in TYPES:
            out.append(f"tipo desconocido «{self.tipo}» (hay {', '.join(TYPES)})")
            return out
        if self.estado not in STATES:
            out.append(f"estado desconocido «{self.estado}» (hay {', '.join(STATES)})")
        if self.lado not in SIDES:
            out.append(f"lado desconocido «{self.lado}» (hay {', '.join(SIDES)})")
        for t in self.etiquetas:
            if t not in TAGS:
                out.append(f"etiqueta desconocida «{t}» (hay {', '.join(TAGS)})")
        spec = PORT_SPEC[self.tipo]
        for name, (rol, required) in spec.items():
            if required and name not in self.puertos:
                out.append(f"falta el puerto «{name}» ({rol}) de una pieza «{self.tipo}»")
        for name, p in self.puertos.items():
            if name.split("_")[0] not in PORT_CLASSES:
                out.append(f"puerto «{name}»: clase desconocida")
                continue
            if p.clase != port_class(name):
                out.append(f"puerto «{name}»: la clase «{p.clase}» no coincide con el nombre")
            if name in spec and p.rol != spec[name][0]:
                out.append(f"puerto «{name}»: el rol es «{p.rol}» y una pieza «{self.tipo}» lleva «{spec[name][0]}»")
            if name not in spec:
                out.append(f"puerto «{name}» no previsto en una pieza «{self.tipo}» (válidos: {', '.join(spec) or 'ninguno'})")
            if abs(np.linalg.norm(p.frame.axis) - 1) > 1e-3 or abs(np.linalg.norm(p.frame.ref) - 1) > 1e-3 \
                    or abs(float(p.frame.axis @ p.frame.ref)) > 1e-3:
                out.append(f"puerto «{name}»: el marco (eje, ref) no es ortonormal")
            if p.face is not None and p.face.area <= 0:
                out.append(f"puerto «{name}»: la cara no tiene área")
        return out

    def to_dict(self) -> dict:
        return {"formato": PIECE_FORMAT, "id": self.id, "version": int(self.version), "tipo": self.tipo,
                "descripcion": self.descripcion, "origen": self.origen,
                "altura_figura_mm": self.altura_figura_mm, "lado": self.lado, "espejable": bool(self.espejable),
                "stl": self.stl, "sha256": self.sha256, "volumen_mm3": round(float(self.volumen_mm3), 4),
                "bbox": self.bbox, "puertos": {k: p.to_dict() for k, p in self.puertos.items()},
                "articulaciones": self.articulaciones, "mecanismo": self.mecanismo,
                "estado": self.estado, "etiquetas": list(self.etiquetas)}

    @classmethod
    def from_dict(cls, d: dict) -> "PieceMeta":
        if d.get("formato") != PIECE_FORMAT:
            raise PieceError(f"no es una pieza de esta versión (formato {d.get('formato')!r}, se esperaba {PIECE_FORMAT!r})")
        return cls(id=d["id"], version=int(d["version"]), tipo=d["tipo"],
                   puertos={k: Port.from_dict(v) for k, v in d.get("puertos", {}).items()},
                   descripcion=d.get("descripcion", ""), origen=d.get("origen", {}),
                   altura_figura_mm=d.get("altura_figura_mm"), lado=d.get("lado", "izq"),
                   espejable=bool(d.get("espejable", True)), stl=d.get("stl", ""), sha256=d.get("sha256", ""),
                   volumen_mm3=float(d.get("volumen_mm3", 0.0)), bbox=d.get("bbox", [[0.0] * 3, [0.0] * 3]),
                   articulaciones=d.get("articulaciones", []), mecanismo=d.get("mecanismo"),
                   estado=d.get("estado", "borrador"), etiquetas=list(d.get("etiquetas", [])))


def _as_trimesh(mesh):
    import trimesh

    if isinstance(mesh, trimesh.Trimesh):
        return mesh
    return S.to_printable_trimesh(mesh)


def make_origin(figure: str = "", project: str = "", note: str = "") -> dict:
    o = {"figura": figure, "proyecto": project, "motor": __version__, "fecha": datetime.date.today().isoformat()}
    if note:
        o["nota"] = note
    return o


def write_piece(directory: str, meta: PieceMeta, mesh) -> PieceMeta:
    """Escribe `<id>@<version>.stl` y `<id>@<version>.pieza.json` en `directory` y completa el hash, el volumen y la caja.

    `mesh` puede ser un `trimesh.Trimesh` o un `Manifold`. No sobrescribe: si el archivo ya existe es un error (las
    piezas son inmutables; un cambio es una versión nueva)."""
    from . import meshio

    bad = meta.problems()
    if bad:
        raise PieceError("; ".join(bad))
    tm = _as_trimesh(mesh)
    if not tm.is_watertight:
        raise PieceError(f"la malla de «{meta.id}» no es estanca: no se guarda una pieza cruda con huecos")
    stl, js = os.path.join(directory, stl_name(meta.id, meta.version)), os.path.join(directory, meta_name(meta.id, meta.version))
    if os.path.exists(stl) or os.path.exists(js):
        raise PieceError(f"«{meta.ref}» ya existe: las piezas son inmutables, guardá una versión nueva")
    os.makedirs(directory, exist_ok=True)
    meshio.save_stl(tm, stl)
    saved = load_mesh(stl)
    meta.stl = stl_name(meta.id, meta.version)
    meta.sha256 = sha256_file(stl)
    meta.volumen_mm3 = abs(float(saved.volume))
    meta.bbox = [[round(float(x), 4) for x in saved.bounds[0]], [round(float(x), 4) for x in saved.bounds[1]]]
    with open(js, "w") as f:
        json.dump(meta.to_dict(), f, indent=2, ensure_ascii=False)
    return meta


def read_meta(path: str) -> PieceMeta:
    with open(path) as f:
        return PieceMeta.from_dict(json.load(f))


def load_mesh(path: str):
    """Malla (trimesh) de un STL de pieza: vértices soldados y orientada hacia afuera."""
    import trimesh

    mesh = trimesh.load(path, force="mesh")
    if len(mesh.faces) == 0:
        raise PieceError(f"«{path}» no tiene triángulos")
    mesh = mesh.copy()
    mesh.merge_vertices()
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def read_piece(directory: str, piece_id: str, version: int, check_hash: bool = True):
    """(meta, malla trimesh) de una pieza guardada. Verifica el hash del STL salvo `check_hash=False`."""
    meta = read_meta(os.path.join(directory, meta_name(piece_id, version)))
    stl = os.path.join(directory, meta.stl)
    if check_hash and meta.sha256 and sha256_file(stl) != meta.sha256:
        raise PieceError(f"«{meta.ref}»: el STL no coincide con el hash guardado (¿se modificó el archivo?)")
    return meta, load_mesh(stl)


def piece_from_standard_hand(stl_path: str, piece_id: str | None = None, version: int = 1, estado: str = "borrador",
                             etiquetas=("estandar",)) -> tuple[PieceMeta, object]:
    """Convierte una mano estándar `afm-mano-1` (STL + `.mano.json`) en pieza `mano` con su puerto `muneca`.

    La malla sigue en el marco local de la mano (origen en la muñeca, dedos hacia +Z); el puerto sale hacia −Z."""
    from .hand import HAND_FORMAT, HandError, load_standard_hand

    _mf, hand_meta = load_standard_hand(stl_path)
    if hand_meta is None:
        raise PieceError(f"«{stl_path}» no trae su `.mano.json` ({HAND_FORMAT}): no se sabe de qué figura salió ni a qué altura")
    mesh = load_mesh(stl_path)
    frame = PortFrame.hand_standard()
    try:
        face = face_from_mesh(mesh, frame)
    except PortError as exc:
        raise HandError(f"«{stl_path}»: {exc}") from exc
    stem = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(os.path.basename(stl_path))[0].lower()).strip("-") or "mano"
    pid = piece_id or (stem if stem.startswith("mano") else f"mano-{stem}")
    if not re.search(r"-\d+$", pid):
        pid += "-01"
    side = {"izquierda": "izq", "derecha": "der"}.get(hand_meta.get("lado_origen"), "izq")
    meta = PieceMeta(id=pid, version=version, tipo="mano", puertos={"muneca": Port("muneca", "enchufe", frame, face)},
                     descripcion="Mano estándar importada de afm-mano-1",
                     origen=make_origin(hand_meta.get("origen_figura", ""), note="afm-mano-1"),
                     altura_figura_mm=hand_meta.get("altura_figura_mm"), lado=side, estado=estado, etiquetas=list(etiquetas))
    if hand_meta.get("agarre"):                          # mano taladrada con `afm agarre`: el catálogo sabe qué accesorios admite
        from .props import grip_problems
        bad = grip_problems(hand_meta["agarre"], "mano")
        if bad:
            raise PieceError(f"«{stl_path}»: {'; '.join(bad)}")
        meta.mecanismo = {"agarre": hand_meta["agarre"]}
    return meta, mesh


def piece_accessory(mesh, piece_id: str, agarre: dict, descripcion: str = "", figura: str = "", version: int = 1,
                    estado: str = "borrador", etiquetas=("propia",), extra: dict | None = None) -> tuple[PieceMeta, object]:
    """Pieza `accesorio` (arma, herramienta…) con empuñadura de agarre. La malla va en el marco del accesorio (ver
    `props.GRIP_FRAME_NOTE`); `agarre` es `props.accessory_grip(...)`. No lleva puertos: se sujeta por el taladro de la mano."""
    from .props import grip_problems

    bad = grip_problems(agarre, "accesorio")
    if bad:
        raise PieceError("; ".join(bad))
    meta = PieceMeta(id=piece_id, version=version, tipo="accesorio", puertos={}, descripcion=descripcion or "Accesorio con empuñadura dentada",
                     origen=make_origin(figura, note="afm agarre"), altura_figura_mm=None, lado="centro", espejable=False,
                     estado=estado, etiquetas=list(etiquetas), mecanismo={**(extra or {}), "agarre": agarre})
    return meta, mesh
