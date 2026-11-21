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


# ---------------------------------------------------------------------------------------------------------------- E03
LEG_PREFIXES = ("pierna", "muslo", "rodilla", "pantorrilla", "bota")     # piezas que forman la «pierna completa» (muslo + rodilla + bota)
MIN_FACING_MM = 1.0       # distancia mínima (XY) entre el centroide de las piernas y el de la pelvis para fiarse del frente


class RiderError(ValueError):
    """La figura armada no sirve como jinete (sin piernas ni cadera, sin frente claro…)."""


@dataclass
class RiderProfile:
    """Lo que el caballo necesita saber del jinete, leído de una figura ya armada en pose sentada (marco del motor, mm).

    Función pura sobre `recipe.Assembled`: no recalcula nada ni modifica la figura (D4). Las mallas son los `Manifold` de
    `BuildResult.pieces` (no los STL exportados: ver plan 1.3-8).
    """

    pelvis: object                    # Manifold de la pelvis; si la figura no tiene cintura, el torso (que lleva las caderas)
    pelvis_name: str                  # «pelvis» o «torso»
    legs: dict                        # nombre -> Manifold (muslo_izq, pantorrilla_izq, … o pierna_izq)
    hip_left: object                  # ports.PortFrame (centro, eje de giro de la cadera, referencia)
    hip_right: object                 # el mismo, espejado en X
    hip_separation: float             # distancia entre los dos centros de cadera
    lower_min: tuple                  # caja envolvente de pelvis + piernas
    lower_max: tuple
    facing: np.ndarray                # vector unitario en XY hacia donde mira el jinete (z = 0)
    sit_angle: float
    knee_print_angle: float
    other_pieces: tuple               # piezas que no entran en el perfil (torso, brazos, cabeza…), para moverlas igual
    warnings: list = field(default_factory=list)


def _mf_bounds(mf) -> np.ndarray:
    return np.asarray(mf.bounding_box(), float).reshape(2, 3)


def _mf_centroid_xy(mfs) -> np.ndarray:
    """Centroide XY ponderado por volumen (el de la caja envolvente si el volumen es nulo)."""
    pts, ws = [], []
    for mf in mfs:
        v = abs(float(mf.volume()))
        b = _mf_bounds(mf)
        tm = S.to_trimesh(mf)
        c = np.asarray(tm.center_mass, float)[:2] if tm.is_watertight and v > 0 else b.mean(axis=0)[:2]
        pts.append(c)
        ws.append(v if v > 0 else 1.0)
    return np.average(np.asarray(pts), axis=0, weights=np.asarray(ws))


def rider_profile(asm) -> RiderProfile:
    """Perfil del jinete de una figura armada (`recipe.Assembled`, con `sit=True`). Levanta `RiderError` con un mensaje útil."""
    from .ports import PortFrame

    res = asm.res
    pieces = res.pieces
    legs = {k: v for k, v in pieces.items() if k.startswith(LEG_PREFIXES)}
    if not legs:
        raise RiderError("la figura no tiene piernas (piezas pierna/muslo/pantorrilla): no hay con qué montar")
    motion = (res.motion or {}).get("pierna")
    if motion is None:
        raise RiderError("la figura no tiene articulación de cadera (sin recorrido «pierna»): faltan los puertos de cadera")
    center = np.asarray(motion[1], float)
    axis = np.asarray(motion[2], float)
    if center.shape != (3,) or axis.shape != (3,) or not np.linalg.norm(axis) > 0:
        raise RiderError("la cadera de la figura no tiene centro y eje válidos")
    axis = axis / np.linalg.norm(axis)
    warns = []
    if "pelvis" in pieces:
        pname = "pelvis"
    elif "torso" in pieces:
        pname = "torso"
        warns.append("la figura no tiene pelvis separada (sin cintura): se usa el torso entero como pelvis")
    else:
        raise RiderError("la figura no tiene pelvis ni torso")
    placed = (res.placed or {}).get("pierna")
    frame = getattr(getattr(placed, "port", None), "frame", None)
    ref = np.asarray(frame.ref, float) if frame is not None else None
    if ref is None or not np.linalg.norm(ref) > 0:
        ref = np.cross(axis, [0.0, 0.0, 1.0])
        if not np.linalg.norm(ref) > 1e-6:
            ref = np.cross(axis, [0.0, 1.0, 0.0])
    ref = ref / np.linalg.norm(ref)
    mir = np.array([-1.0, 1.0, 1.0])
    left, right = (PortFrame(center, axis, ref), PortFrame(center * mir, axis * mir, ref * mir))
    if center[0] < 0:                                         # el motor trabaja en +X; por si acaso, «izq» es el de +X
        left, right = right, left
    if getattr(res, "right", None) is not None:
        warns.append("figura asimétrica: la cadera derecha se toma por espejo de la izquierda")
    pel = pieces[pname]
    b = np.vstack([_mf_bounds(pel)] + [_mf_bounds(m) for m in legs.values()])
    d = _mf_centroid_xy(list(legs.values())) - _mf_centroid_xy([pel])
    if np.linalg.norm(d) < MIN_FACING_MM:
        raise RiderError("no se puede saber hacia dónde mira el jinete: las piernas quedan a menos de "
                         f"{MIN_FACING_MM:g} mm del centro de la pelvis (¿figura sin pose sentada?)")
    facing = np.array([d[0], d[1], 0.0]) / np.linalg.norm(d)
    others = tuple(k for k in pieces if k not in legs and k != pname)
    return RiderProfile(pelvis=pel, pelvis_name=pname, legs=legs, hip_left=left, hip_right=right,
                        hip_separation=float(np.linalg.norm(left.center - right.center)),
                        lower_min=tuple(float(x) for x in b[:, :].min(axis=0)), lower_max=tuple(float(x) for x in b.max(axis=0)),
                        facing=facing, sit_angle=float(asm.params.sit_angle), knee_print_angle=float(asm.params.knee_print_angle),
                        other_pieces=others, warnings=warns)


# ---------------------------------------------------------------------------------------------------------------- E04
MAX_PITCH_DEG = 60.0


@dataclass
class RiderFrame:
    """Marco de asiento (D4): una sola matriz rígida que lleva TODO el jinete al marco del caballo, sin deformarlo.

    El ancla del jinete (`anchor`, marco del motor) es el punto de la base de la pelvis y los muslos bajo el centro de las
    caderas; `seat_origin` es el punto elegido sobre el lomo (marco canónico del caballo, D3). El jinete gira sobre Z hasta
    mirar hacia `direction` (hacia la cabeza del caballo) y, solo si se pide, se inclina `pitch_deg` en torno a su eje lateral
    que pasa por el ancla (la normal del lomo no acuesta al jinete, 5.2). `+Z` queda arriba.
    """

    anchor: np.ndarray
    seat_origin: np.ndarray
    direction: np.ndarray          # unitario en XY (z = 0)
    pitch_deg: float
    matrix: np.ndarray             # 4×4 rígida: marco del motor → marco del caballo

    def apply_points(self, pts) -> np.ndarray:
        p = np.atleast_2d(np.asarray(pts, float))
        return p @ self.matrix[:3, :3].T + self.matrix[:3, 3]

    def apply_vectors(self, vecs) -> np.ndarray:
        return np.atleast_2d(np.asarray(vecs, float)) @ self.matrix[:3, :3].T

    def apply(self, mf):
        """`Manifold` movido con la matriz (copia nueva; el original no se toca)."""
        return mf.transform(self.matrix[:3, :])

    def inverse(self) -> np.ndarray:
        R, t = self.matrix[:3, :3], self.matrix[:3, 3]
        M = np.eye(4)
        M[:3, :3] = R.T
        M[:3, 3] = -R.T @ t
        return M

    def check(self, tol: float = 1e-9) -> None:
        """RᵀR = I y det(R) = +1 (sin escala, sin espejo). Levanta `RiderError` si no."""
        R = self.matrix[:3, :3]
        if not np.allclose(R.T @ R, np.eye(3), atol=tol) or abs(np.linalg.det(R) - 1.0) > tol:
            raise RiderError("la matriz del jinete no es una rotación propia")


def seat_anchor(rp: RiderProfile) -> np.ndarray:
    """Ancla del jinete: bajo el centro de las caderas, a la altura de la base de los muslos (si la figura los tiene
    separados) o de la pelvis."""
    mid = (np.asarray(rp.hip_left.center, float) + np.asarray(rp.hip_right.center, float)) / 2.0
    base = [k for k in rp.legs if k.startswith("muslo")]
    zs = [_mf_bounds(rp.legs[k])[0][2] for k in base] or [_mf_bounds(rp.pelvis)[0][2]]
    return np.array([mid[0], mid[1], float(min(zs))])


def seat_frame(rp: RiderProfile, seat_origin, direction=(0.0, 1.0, 0.0), pitch_deg: float = 0.0) -> RiderFrame:
    """Marco de asiento: `seat_origin` sobre el lomo y `direction` hacia la cabeza del caballo (solo cuenta su parte XY)."""
    o = np.asarray(seat_origin, float)
    d = np.asarray(direction, float)
    if o.shape != (3,) or not np.all(np.isfinite(o)):
        raise RiderError("«seat_origin» tiene que ser un punto [x, y, z] finito")
    if d.shape != (3,) or not np.all(np.isfinite(d)) or np.linalg.norm(d[:2]) < 1e-6:
        raise RiderError("«direction» tiene que tener una parte horizontal (XY) no nula")
    if isinstance(pitch_deg, bool) or not isinstance(pitch_deg, (int, float)) or not abs(pitch_deg) <= MAX_PITCH_DEG:
        raise RiderError(f"«pitch_deg» tiene que estar entre −{MAX_PITCH_DEG:g}° y {MAX_PITCH_DEG:g}°")
    d = np.array([d[0], d[1], 0.0]) / np.linalg.norm(d[:2])
    f = np.asarray(rp.facing, float)
    yaw = math.atan2(d[1], d[0]) - math.atan2(f[1], f[0])
    anchor = seat_anchor(rp)
    c, s = math.cos(yaw), math.sin(yaw)
    Rz = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    R = Rz
    if pitch_deg:
        lat = Rz @ np.array([1.0, 0.0, 0.0])                    # eje lateral del jinete, ya girado
        # inclinación positiva = el jinete se inclina hacia adelante (el frente baja): rotación en torno a «lat» que lleva +Y hacia −Z
        R = trimesh.transformations.rotation_matrix(math.radians(-pitch_deg), lat)[:3, :3] @ Rz
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = o - R @ anchor
    fr = RiderFrame(anchor=anchor, seat_origin=o, direction=d, pitch_deg=float(pitch_deg), matrix=M)
    fr.check()
    return fr


def place_rider(asm, frame: RiderFrame) -> dict:
    """Todas las piezas del jinete (`BuildResult.pieces`, incluidas las que traen monturas de armas) movidas con la MISMA matriz.
    No recalcula pasadores ni holguras, no escala; los accesorios sueltos (`Recipe.accesorios`) no entran (D5)."""
    return {k: frame.apply(v) for k, v in asm.res.pieces.items()}


# ---------------------------------------------------------------------------------------------------------------- E06
SADDLE_TOUCH_TOL = 0.1       # mm: más allá de la separación prevista, la silla «flota»
SADDLE_PAD_MARGIN = 2.0      # mm: margen de la ventana del caballo alrededor de la silla
SADDLE_TALL = 50.0           # mm sobre la cara superior hasta donde se mira si el lomo sobresale de la silla
SADDLE_SEARCH = 5.0          # mm: hasta esta distancia se mide el hueco bajo una silla que flota


class SaddleError(ValueError):
    """Parámetros de silla inválidos."""


@dataclass(frozen=True)
class SaddleSpec:
    """Silla mínima (almohadilla) en el marco de asiento (plano 5.4, paso 1). Medidas en mm.

    `ancho` va en X (a través del lomo), `largo` a lo largo de `direction` (hacia la cabeza). La cara superior queda
    `alto` mm sobre el punto de asiento y los costados bajan `bajada` mm por debajo de él: la almohadilla abraza el lomo.
    `encaje` > 0 hunde la cara inferior esa cantidad en el caballo (para fusionarlas); < 0 deja esa holgura; 0 apoya.
    """

    ancho: float = 40.0
    largo: float = 45.0
    alto: float = 5.0
    bajada: float = 8.0
    encaje: float = 0.0
    desplazamiento: float = 0.0       # mm a lo largo de la dirección (+ hacia la cabeza)

    def __post_init__(self):
        for name in ("ancho", "largo", "alto"):
            if not _num(getattr(self, name), name) > 0:
                raise SaddleError(f"«{name}» tiene que ser mayor que 0")
        if not _num(self.bajada, "bajada") >= 0:
            raise SaddleError("«bajada» no puede ser negativa")
        _num(self.encaje, "encaje")
        _num(self.desplazamiento, "desplazamiento")
        if abs(self.encaje) > self.alto + self.bajada:
            raise SaddleError("«encaje» no puede superar el alto total de la almohadilla")

    def to_dict(self) -> dict:
        return {k: float(getattr(self, k)) for k in ("ancho", "largo", "alto", "bajada", "encaje", "desplazamiento")}

    @classmethod
    def from_dict(cls, d) -> "SaddleSpec":
        if not isinstance(d, dict):
            raise SaddleError("la silla tiene que ser un diccionario")
        extra = set(d) - set(cls.__dataclass_fields__)
        if extra:
            raise SaddleError(f"campos desconocidos en la silla: {', '.join(sorted(extra))}")
        return cls(**d)


def suggest_saddle_spec(rp: RiderProfile, margin: float = 4.0) -> SaddleSpec:
    """Medidas iniciales desde el jinete (flujo 4.5): ancho y largo de los muslos (o de las piernas) más un margen por lado."""
    base = [m for k, m in rp.legs.items() if k.startswith("muslo")] or list(rp.legs.values())
    b = np.vstack([_mf_bounds(m) for m in base])
    ext = b.max(axis=0) - b.min(axis=0)
    return SaddleSpec(ancho=float(ext[0] + 2 * margin), largo=float(ext[1] + 2 * margin))


@dataclass
class SaddleBuild:
    """Silla generada y lo que se midió de ella. `problems` vacío = silla válida (sola; falta el despeje de la pelvis, E07)."""

    spec: SaddleSpec
    manifold: object
    seat_origin: np.ndarray
    direction: np.ndarray
    z_top: float                      # cara superior: ahí se apoya el jinete (el ancla del jinete va a esta altura)
    volume: float = 0.0
    components: int = 0
    fragments: list = field(default_factory=list)      # volúmenes de las componentes que no son la mayor
    voids: int = 0                    # cavidades cerradas dentro de la silla
    contact_volume: float = 0.0       # volumen de la silla que penetra en el caballo (mm³)
    gap: float = 0.0                  # distancia mínima silla–caballo (0 si se tocan o penetran)
    floating: bool = False
    top_thickness: float | None = None    # espesor de la silla sobre el punto más alto del lomo bajo su huella
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def lift(self) -> float:
        """Cuánto sube el jinete respecto del lomo: la cara superior sobre el punto de asiento."""
        return float(self.spec.alto)

    @property
    def ok(self) -> bool:
        return not self.problems


def _box_mf(extents, center, yaw_rad: float = 0.0):
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(center)
    if yaw_rad:
        m.apply_transform(trimesh.transformations.rotation_matrix(yaw_rad, [0, 0, 1]))
    return S.to_manifold(m)


def shell_stats(mf) -> dict:
    """Componentes (cáscaras exteriores), volúmenes de las menores y cavidades cerradas (cáscaras de volumen negativo).

    `Manifold.decompose` trata la cavidad interior como una componente más, por eso se cuenta por cáscaras con signo."""
    if mf.is_empty():
        return {"components": 0, "fragments": [], "voids": 0}
    tm = S.to_trimesh(mf)
    tm.merge_vertices()
    vols = [float(s.volume) for s in tm.split(only_watertight=False)]
    outer = sorted((v for v in vols if v > 0), reverse=True)
    return {"components": len(outer), "fragments": outer[1:], "voids": sum(1 for v in vols if v < 0)}


def build_saddle(horse: HorseModel, spec: SaddleSpec, seat_origin, direction=(0.0, 1.0, 0.0), min_wall: float = 1.2) -> SaddleBuild:
    """Silla mínima sobre el lomo: almohadilla `spec` cuya cara inferior copia el caballo (plano 5.4, pasos 1 y 2).

    `silla = almohadilla − caballo` (el caballo bajado `encaje` mm). Trabaja sobre una ventana del caballo y no lo toca.
    Detecta silla flotante, fragmentos, cavidades, lomo que sobresale de la cara superior y vacío bajo la huella.
    """
    o = np.asarray(seat_origin, float)
    d = np.asarray(direction, float)
    if o.shape != (3,) or not np.all(np.isfinite(o)):
        raise SaddleError("«seat_origin» tiene que ser un punto [x, y, z] finito")
    if d.shape != (3,) or not np.all(np.isfinite(d)) or np.linalg.norm(d[:2]) < 1e-6:
        raise SaddleError("«direction» tiene que tener una parte horizontal (XY) no nula")
    d = np.array([d[0], d[1], 0.0]) / np.linalg.norm(d[:2])
    yaw = math.atan2(d[1], d[0]) - math.pi / 2.0
    c_xy = o[:2] + spec.desplazamiento * d[:2]
    z_top = float(o[2] + spec.alto)
    zc = float(o[2] + (spec.alto - spec.bajada) / 2.0)
    h = spec.alto + spec.bajada

    def at(extents, zlo, zhi):                                 # caja con la misma huella, rotada y centrada como la silla
        m = trimesh.creation.box(extents=(extents[0], extents[1], zhi - zlo))
        m.apply_translation((0, 0, (zlo + zhi) / 2.0))
        m.apply_transform(trimesh.transformations.rotation_matrix(yaw, [0, 0, 1]))
        m.apply_translation((c_xy[0], c_xy[1], 0))
        return S.to_manifold(m)

    pad = at((spec.ancho, spec.largo), zc - h / 2.0, zc + h / 2.0)
    reach = abs(spec.encaje) + SADDLE_PAD_MARGIN + SADDLE_SEARCH
    win = np.asarray(pad.bounding_box(), float).reshape(2, 3)
    lo, hi = win[0] - reach, win[1] + reach
    hi[2] = z_top + SADDLE_TALL + reach
    box = _box_mf(hi - lo, (lo + hi) / 2.0)
    horse_win = S.intersection(horse.manifold, box)
    build = SaddleBuild(spec=spec, manifold=pad, seat_origin=o, direction=d, z_top=z_top)
    if horse_win.is_empty():
        build.floating = True
        build.gap = float("inf")
        build.problems.append(f"no hay caballo a menos de {SADDLE_SEARCH:g} mm de la silla (asiento fuera del lomo o muy alto)")
        return build
    p = float(spec.encaje)
    horse_shift = horse_win.translate((0.0, 0.0, -p)) if p else horse_win
    saddle = S.difference(pad, horse_shift)
    build.manifold = saddle
    if saddle.is_empty():
        build.problems.append("la silla desapareció al recortarla contra el caballo")
        return build
    build.volume = abs(float(saddle.volume()))
    st = shell_stats(saddle)
    build.components, build.fragments, build.voids = st["components"], st["fragments"], st["voids"]
    build.contact_volume = abs(float(S.intersection(saddle, horse_win).volume()))
    expected = max(0.0, -p)
    if build.contact_volume > 1e-3:
        build.gap = 0.0
    else:
        search = expected + SADDLE_SEARCH
        build.gap = float(saddle.min_gap(horse_win, search))
    build.floating = build.gap > expected + SADDLE_TOUCH_TOL
    # espesor sobre el punto más alto del lomo bajo la huella (el lomo que sobresale de la cara superior rompe la silla)
    under = S.intersection(horse_shift, at((spec.ancho, spec.largo), float(win[0][2]) - reach, z_top + SADDLE_TALL))
    if not under.is_empty():
        build.top_thickness = float(z_top - np.asarray(under.bounding_box(), float).reshape(2, 3)[1][2])
    if build.floating:
        build.problems.append(f"la silla flota: {build.gap:.2f} mm sobre el caballo (esperado {expected:.2f} mm)")
    if build.components > 1:
        vols = ", ".join(f"{v:.1f}" for v in build.fragments)
        build.problems.append(f"la silla tiene {build.components} componentes (fragmentos de {vols} mm³): el caballo la parte")
    if build.voids:
        build.problems.append(f"la silla tiene {build.voids} cavidad(es) cerrada(s)")
    if build.top_thickness is not None and build.top_thickness < min_wall:
        build.problems.append(f"el lomo deja solo {build.top_thickness:.2f} mm de silla sobre él (mínimo {min_wall:g}): subir «alto» o mover el asiento")
    if build.contact_volume > 1e-3 and p <= 0:
        build.warnings.append("la silla penetra en el caballo más de lo previsto")
    return build


# ---------------------------------------------------------------------------------------------------------------- E05
CARVE_SAMPLE_SPACING = 0.15    # mm: separación de los puntos con que se mide la profundidad (error ≤ ~1 spacing)
CARVE_MAX_RAYS = 600           # caras de la cavidad que se miden para la pared residual
CARVE_GAP_TOL = 0.05           # mm: lo que puede faltar a la holgura medida por la decimación de la herramienta


@dataclass(frozen=True)
class CarveLimits:
    """Límites del tallado (plano 5.3): si no se cumplen, la combinación se declara incompatible y no se recorta más."""

    max_depth: float = 6.0                 # mm: hasta dónde puede entrar el corte por debajo de la superficie original del caballo
    min_wall: float = 1.2                  # mm: pared residual mínima (≈ 3 líneas de 0,4 mm)
    max_removed_frac: float = 0.03         # fracción del volumen del caballo que se puede retirar
    zone: tuple | None = None              # (xmin, ymin, zmin, xmax, ymax, zmax) en el marco del caballo: el corte no puede salirse

    def __post_init__(self):
        for name in ("max_depth", "min_wall"):
            if not _num(getattr(self, name), name) > 0:
                raise HorseError(f"«{name}» tiene que ser mayor que 0")
        if not 0 < _num(self.max_removed_frac, "max_removed_frac") <= 1:
            raise HorseError("«max_removed_frac» tiene que estar entre 0 y 1")
        if self.zone is not None:
            z = tuple(float(_num(v, "zone")) for v in self.zone)
            if len(z) != 6 or not (z[0] < z[3] and z[1] < z[4] and z[2] < z[5]):
                raise HorseError("«zone» tiene que ser (xmin, ymin, zmin, xmax, ymax, zmax) con mín < máx")
            object.__setattr__(self, "zone", z)


@dataclass
class CarveResult:
    """Qué pasó al tallar el caballo. `carved` es None si el tallado no cumple los límites (`attempt` guarda el intento)."""

    ok: bool
    carved: object | None
    attempt: object
    tool: object
    clearance: float
    removed_volume: float = 0.0
    removed_frac: float = 0.0
    max_depth: float = 0.0
    wall_min: float | None = None          # pared residual mínima estimada con rayos (None: no se talló nada)
    components: int = 1
    genus_delta: int = 0                   # túneles abiertos (> 0)
    voids: int = 0
    gap: float | None = None               # distancia mínima piernas–caballo tallado (medida con las piernas sin decimar)
    legs_overlap: float = 0.0              # volumen de las piernas que sigue dentro del caballo tallado
    cut_bounds: tuple | None = None
    zones: dict = field(default_factory=dict)        # pierna -> volumen retirado por su herramienta
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _mesh_arrays(mf):
    mm = mf.to_mesh()
    return np.asarray(mm.vert_properties, float)[:, :3], np.asarray(mm.tri_verts, np.int64), mm


def _first_hit_distances(origins, dirs, tris, tmin: float = 1e-6) -> np.ndarray:
    """Distancia al primer triángulo que cada rayo cruza hacia adelante (Möller–Trumbore con numpy; inf si no cruza)."""
    v0, e1, e2 = tris[:, 0], tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    out = np.full(len(origins), np.inf)
    for i, (o, d) in enumerate(zip(origins, dirs)):
        p = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, p)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = o - v0
        u = np.einsum("ij,ij->i", s, p) * inv
        q = np.cross(s, e1)
        v = (q @ d) * inv
        t = np.einsum("ij,ij->i", e2, q) * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > tmin)
        if hit.any():
            out[i] = t[hit].min()
    return out


def _surface_distance(points, verts, faces, seed: int = 0) -> np.ndarray:
    """Distancia de cada punto a la superficie de la malla, con puntos muestreados cada `CARVE_SAMPLE_SPACING` (scipy, sin rtree)."""
    from scipy.spatial import cKDTree

    tm = trimesh.Trimesh(verts, faces, process=False)
    count = int(min(600_000, max(2000, tm.area / CARVE_SAMPLE_SPACING ** 2)))
    try:
        pts, _ = trimesh.sample.sample_surface(tm, count, seed=seed)
    except TypeError:                                          # trimesh viejo, sin «seed»
        np.random.seed(seed)
        pts, _ = trimesh.sample.sample_surface(tm, count)
    return cKDTree(np.vstack([pts, verts])).query(points)[0]


def _wall_behind_cavity(solid, tool, lo, hi, rim: float):
    """Pared mínima detrás de la cavidad que `tool` abrió en `solid` (rayos desde las caras de la cavidad hacia el material).

    `tool` tiene que ser el sólido restado (con `as_original()`), para reconocer sus caras en `solid`. Se miden solo triángulos
    dentro de la caja `lo`–`hi` y se ignora el borde de entrada (a menos de `rim` mm de donde la cavidad se une a la superficie
    original). `None` si no hay cavidad medible."""
    cv, cf, cmesh = _mesh_arrays(solid)
    ids = list(cmesh.run_original_id)
    ridx = np.asarray(cmesh.run_index, np.int64) // 3
    tid = tool.original_id()
    cav = np.concatenate([np.arange(ridx[i], ridx[i + 1]) for i, r in enumerate(ids) if r == tid] or [np.array([], np.int64)])
    if not len(cav):
        return None
    tri = cv[cf]
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    iscav = np.zeros(len(cf), bool)
    iscav[cav] = True
    rim_v = np.intersect1d(np.unique(cf[iscav]), np.unique(cf[~iscav]))          # vértices donde la cavidad se une a la superficie original
    cen = tri.mean(axis=1)
    pick = cav
    if len(rim_v):
        from scipy.spatial import cKDTree
        pick = cav[cKDTree(cv[rim_v]).query(cen[cav])[0] > rim]
    if not len(pick):
        return None
    pick = pick[np.linspace(0, len(pick) - 1, min(CARVE_MAX_RAYS, len(pick))).astype(int)]
    near = ((tri.max(axis=1) >= lo).all(axis=1)) & ((tri.min(axis=1) <= hi).all(axis=1))
    t = _first_hit_distances(cen[pick] - nrm[pick] * 1e-3, -nrm[pick], tri[near])
    t = t[np.isfinite(t)] + 1e-3
    return float(t.min()) if len(t) else None


def verify_carved(carved, legs: dict, clearance: float, tol: float = CARVE_GAP_TOL) -> tuple:
    """Comprueba el tallado contra las piernas ORIGINALES (sin decimar): `(overlap, gap, problems)`.

    Una herramienta sin la holgura, o con la decimación demasiado agresiva, se detecta acá: la distancia piernas–caballo
    tiene que ser ≥ `clearance − tol` y ningún volumen de pierna puede quedar dentro."""
    allegs = S.union(*legs.values()) if len(legs) > 1 else next(iter(legs.values()))
    overlap = abs(float(S.intersection(carved, allegs).volume()))
    lb = np.asarray(allegs.bounding_box(), float).reshape(2, 3)
    reach = clearance + 1.0
    box = _box_mf(lb[1] - lb[0] + 2 * reach, (lb[0] + lb[1]) / 2.0)
    win = S.intersection(carved, box)
    gap = float(allegs.min_gap(win, reach)) if not win.is_empty() else float("inf")
    problems = []
    if overlap > 1e-3:
        problems.append(f"las piernas siguen {overlap:.2f} mm³ dentro del caballo tallado")
    if gap < clearance - tol:
        problems.append(f"holgura piernas–caballo {gap:.3f} mm < {clearance:g} mm previstos")
    return overlap, gap, problems


def carve_horse(horse: HorseModel, legs: dict, clearance: float = 0.5, limits: CarveLimits | None = None,
                tool_max_tris: int = 20_000, rim_exclude: float | None = None) -> CarveResult:
    """Tallado del caballo (plano 5.3): `caballo − (piernas completas dilatadas por la holgura)`, sobre una copia.

    `legs`: `{nombre: Manifold}` de las piernas completas YA colocadas en el marco del caballo (`place_rider`). La herramienta
    se dilata sobre una copia decimada (`tool_max_tris`); la holgura se comprueba después con las piernas sin decimar. Se mide
    volumen y zonas retirados, profundidad máxima, pared residual (rayos desde la cavidad; se ignora el borde de entrada, a menos
    de `rim_exclude` mm de donde la herramienta sale del caballo), componentes, túneles y cavidades cerradas. Si algo pasa los
    límites no se recorta más: `ok` False, `carved` None. El caballo original no se modifica.
    """
    lim = limits or CarveLimits()
    if not legs:
        raise HorseError("faltan las piernas del jinete para tallar")
    if isinstance(clearance, bool) or not isinstance(clearance, (int, float)) or not clearance >= 0:
        raise HorseError("«clearance» tiene que ser un número ≥ 0")
    rim = lim.min_wall if rim_exclude is None else float(rim_exclude)
    hmf = horse.manifold
    dil = {}
    for name, mf in legs.items():
        src = mf
        n = int(mf.num_tri())
        if n > tool_max_tris and S.can_decimate():
            src = S.decimate(mf, reduction=1.0 - tool_max_tris / n)
        dil[name] = S.dilate(src, clearance)
    tool = S.union(*dil.values()).as_original()
    h0 = hmf.as_original()
    cut = S.intersection(h0, tool)
    base_genus, base_vol = int(hmf.genus()), abs(float(hmf.volume()))
    base_comp = shell_stats(hmf)["components"]
    res = CarveResult(ok=True, carved=hmf, attempt=hmf, tool=tool, clearance=float(clearance))
    if cut.is_empty() or abs(float(cut.volume())) < 1e-6:
        res.warnings.append("las piernas no tocan el caballo: no hay nada que tallar")
        return res
    carved = S.difference(h0, tool)
    res.attempt = carved
    res.removed_volume = abs(float(cut.volume()))
    res.removed_frac = res.removed_volume / base_vol
    cb = np.asarray(cut.bounding_box(), float).reshape(2, 3)
    res.cut_bounds = (tuple(cb[0]), tuple(cb[1]))
    for name, d in dil.items():
        res.zones[name] = abs(float(S.intersection(cut, d).volume()))
    st = shell_stats(carved)
    res.components, res.voids = st["components"], st["voids"]
    res.genus_delta = int(carved.genus()) - base_genus
    # profundidad: distancia de los puntos del corte a la superficie ORIGINAL del caballo
    hv, hf, _ = _mesh_arrays(hmf)
    R = lim.max_depth * 1.5 + 3.0
    lo, hi = cb[0] - R, cb[1] + R
    fmax, fmin = hv[hf].max(axis=1), hv[hf].min(axis=1)
    keep = (fmax >= lo).all(axis=1) & (fmin <= hi).all(axis=1)
    sub_v, sub_f = hv, hf[keep]
    ev, ef, _ = _mesh_arrays(cut)
    pts = np.vstack([ev, ev[ef].mean(axis=1)])
    res.max_depth = float(_surface_distance(pts, sub_v, sub_f).max())
    # pared residual: rayos desde las caras de la cavidad hacia el material
    W = lim.max_depth * 2 + lim.min_wall + 10.0
    res.wall_min = _wall_behind_cavity(carved, tool, cb[0] - W, cb[1] + W, rim)
    _, res.gap, vprob = verify_carved(carved, legs, clearance)
    res.legs_overlap = abs(float(S.intersection(carved, S.union(*legs.values()) if len(legs) > 1 else next(iter(legs.values()))).volume()))
    pr = res.problems
    pr.extend(vprob)
    if res.max_depth > lim.max_depth:
        pr.append(f"el corte entra {res.max_depth:.2f} mm bajo la superficie del caballo (máximo {lim.max_depth:g})")
    if res.wall_min is not None and res.wall_min < lim.min_wall:
        pr.append(f"la pared que queda detrás del corte mide {res.wall_min:.2f} mm (mínimo {lim.min_wall:g})")
    if res.removed_frac > lim.max_removed_frac:
        pr.append(f"se retira el {100 * res.removed_frac:.1f} % del caballo (máximo {100 * lim.max_removed_frac:g} %)")
    if res.components > base_comp:
        pr.append(f"el corte parte el caballo en {res.components} componentes")
    if res.genus_delta > 0:
        pr.append(f"el corte abre {res.genus_delta} túnel(es) a través del caballo")
    if res.voids:
        pr.append(f"el corte deja {res.voids} cavidad(es) cerrada(s) dentro del caballo")
    if lim.zone is not None:
        z = np.asarray(lim.zone, float)
        if np.any(cb[0] < z[:3] - 1e-6) or np.any(cb[1] > z[3:] + 1e-6):
            pr.append("el corte se sale de la zona permitida")
    if pr:
        res.ok = False
        res.carved = None
        pr.append("este caballo no admite esta figura en la pose estándar con estos límites (no se recorta más)")
    else:
        res.carved = carved
    return res


# ---------------------------------------------------------------------------------------------------------------- E07
SKIRT_OVERLAP = 2.0          # mm que el faldón se mete en la almohadilla para quedar unido a ella
RELIEF_GAP_TOL = 0.05        # mm: lo que puede faltar a la holgura medida por la decimación de la herramienta


@dataclass(frozen=True)
class ReliefSpec:
    """Despeje del jinete en la silla y faldones (plano 5.4, pasos 3 y 4). Medidas en mm.

    `clearance` es la holgura jinete–silla. Los faldones son dos placas laterales de espesor `skirt_thick` en los costados de la
    almohadilla que bajan `skirt_drop` mm por debajo de su borde inferior y cubren los canales de las piernas; `skirt_drop` = 0
    los quita. El faldón no entra en el caballo ni en el despeje (se le resta el caballo y el jinete dilatado).
    """

    clearance: float = 0.5
    skirt_drop: float = 8.0
    skirt_thick: float = 1.6

    def __post_init__(self):
        if not _num(self.clearance, "clearance") >= 0:
            raise SaddleError("«clearance» no puede ser negativa")
        if not _num(self.skirt_drop, "skirt_drop") >= 0:
            raise SaddleError("«skirt_drop» no puede ser negativo")
        if not _num(self.skirt_thick, "skirt_thick") > 0:
            raise SaddleError("«skirt_thick» tiene que ser mayor que 0")

    def to_dict(self) -> dict:
        return {k: float(getattr(self, k)) for k in ("clearance", "skirt_drop", "skirt_thick")}

    @classmethod
    def from_dict(cls, d) -> "ReliefSpec":
        if not isinstance(d, dict):
            raise SaddleError("el despeje tiene que ser un diccionario")
        extra = set(d) - set(cls.__dataclass_fields__)
        if extra:
            raise SaddleError(f"campos desconocidos en el despeje: {', '.join(sorted(extra))}")
        return cls(**d)


@dataclass
class ReliefBuild:
    """Silla con el despeje del jinete y los faldones. `ok` False deja `manifold` en None (`attempt` guarda el intento)."""

    ok: bool
    manifold: object | None
    attempt: object
    tool: object
    base: SaddleBuild
    spec: ReliefSpec
    removed_volume: float = 0.0            # volumen de la silla base que se llevó el despeje
    skirt_volume: float = 0.0              # volumen que aportan los faldones (ya recortados)
    volume: float = 0.0
    components: int = 0
    fragments: list = field(default_factory=list)
    voids: int = 0
    wall_min: float | None = None          # pared mínima detrás del despeje (rayos; None si no se pudo medir)
    gaps: dict = field(default_factory=dict)         # pieza del jinete -> distancia a la silla
    overlaps: dict = field(default_factory=dict)     # pieza del jinete -> volumen que sigue dentro de la silla
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def gap(self) -> float | None:
        return min(self.gaps.values()) if self.gaps else None


def rider_lower(rp: RiderProfile, placed: dict) -> dict:
    """Pelvis y piernas completas, ya colocadas (`place_rider`), que son lo que el asiento tiene que despejar."""
    names = [rp.pelvis_name, *rp.legs]
    missing = [n for n in names if n not in placed]
    if missing:
        raise RiderError(f"faltan piezas del jinete colocado: {', '.join(missing)}")
    return {n: placed[n] for n in names}


def rider_pairs(solid, rider: dict, clearance: float, tol: float = RELIEF_GAP_TOL, overlap_tol: float = 1e-3,
                label: str = "silla") -> tuple:
    """Pareja por pareja, contra las piezas ORIGINALES (sin decimar): `(overlaps, gaps, problems)` de `solid` con cada pieza.

    Ninguna pieza puede quedar dentro (más de `overlap_tol` mm³) y la distancia tiene que ser ≥ `clearance − tol`. `label` nombra
    el otro lado de la pareja en los mensajes («pierna_izq ↔ caballo»)."""
    reach = clearance + 1.0
    overlaps, gaps, problems = {}, {}, []
    for name, mf in rider.items():
        ov = abs(float(S.intersection(solid, mf).volume()))
        b = np.asarray(mf.bounding_box(), float).reshape(2, 3)
        win = S.intersection(solid, _box_mf(b[1] - b[0] + 2 * reach, (b[0] + b[1]) / 2.0))
        gap = float(mf.min_gap(win, reach)) if not win.is_empty() else float("inf")
        overlaps[name], gaps[name] = ov, gap
        if ov > overlap_tol:
            problems.append(f"{name} ↔ {label}: {ov:.2f} mm³ del jinete siguen dentro de la {label}")
        elif gap < clearance - tol:
            problems.append(f"{name} ↔ {label}: holgura {gap:.3f} mm < {clearance:g} mm previstos")
    return overlaps, gaps, problems


def relieve_saddle(horse: HorseModel, saddle: SaddleBuild, rider: dict, spec: ReliefSpec | None = None,
                   min_wall: float = 1.2, tool_max_tris: int = 20_000, rim_exclude: float | None = None) -> ReliefBuild:
    """Despeje y faldones de la silla (plano 5.4, pasos 3 y 4): `(silla + faldones) − (pelvis y piernas dilatadas por la holgura)`.

    `rider`: `{nombre: Manifold}` de la pelvis y las piernas completas YA colocadas (`rider_lower`). No modifica al jinete, a la
    silla base ni al caballo. Mide volumen retirado y aportado, componentes, cavidades cerradas, pared detrás del despeje y, pieza
    por pieza, cuánto del jinete sigue dentro de la silla y a qué distancia queda. Si algo no cumple, `ok` False y `manifold` None.
    """
    sp = spec or ReliefSpec(clearance=0.5)
    if not rider:
        raise SaddleError("faltan la pelvis y las piernas del jinete para despejar la silla")
    if saddle.manifold is None or saddle.manifold.is_empty():
        raise SaddleError("la silla base está vacía: no hay nada que despejar")
    if isinstance(min_wall, bool) or not isinstance(min_wall, (int, float)) or not min_wall > 0:
        raise SaddleError("«min_wall» tiene que ser mayor que 0")
    rim = min_wall if rim_exclude is None else float(rim_exclude)
    dil = []
    for mf in rider.values():
        n = int(mf.num_tri())
        src = S.decimate(mf, reduction=1.0 - tool_max_tris / n) if n > tool_max_tris and S.can_decimate() else mf
        dil.append(S.dilate(src, sp.clearance))
    tool = S.union(*dil).as_original()
    base = saddle.manifold
    sb = np.asarray(base.bounding_box(), float).reshape(2, 3)
    horse_shift = None
    shape = base
    skirt_vol = 0.0
    warns = []
    if sp.skirt_drop > 0:
        ss = saddle.spec
        d = saddle.direction
        yaw = math.atan2(d[1], d[0]) - math.pi / 2.0
        c_xy = saddle.seat_origin[:2] + ss.desplazamiento * d[:2]
        zb = float(saddle.seat_origin[2] - ss.bajada)                    # borde inferior de la almohadilla
        zlo, zhi = zb - sp.skirt_drop, zb + min(SKIRT_OVERLAP, ss.alto + ss.bajada)
        plates = []
        for sign in (1.0, -1.0):
            m = trimesh.creation.box(extents=(sp.skirt_thick, ss.largo, zhi - zlo))
            m.apply_translation((sign * (ss.ancho - sp.skirt_thick) / 2.0, 0.0, (zlo + zhi) / 2.0))
            m.apply_transform(trimesh.transformations.rotation_matrix(yaw, [0, 0, 1]))
            m.apply_translation((c_xy[0], c_xy[1], 0.0))
            plates.append(S.to_manifold(m))
        skirts = S.union(*plates)
        reach = abs(ss.encaje) + SADDLE_PAD_MARGIN
        sk = np.asarray(skirts.bounding_box(), float).reshape(2, 3)
        lo, hi = sk[0] - reach, sk[1] + reach
        hwin = S.intersection(horse.manifold, _box_mf(hi - lo, (lo + hi) / 2.0))
        if not hwin.is_empty():
            p = float(ss.encaje)
            horse_shift = hwin.translate((0.0, 0.0, -p)) if p else hwin
            skirts = S.difference(skirts, horse_shift)
        skirts = S.difference(skirts, tool)
        skirt_vol = abs(float(skirts.volume())) if not skirts.is_empty() else 0.0
        shape = S.union(base, skirts) if not skirts.is_empty() else base
    cut = S.intersection(base, tool)
    removed = abs(float(cut.volume())) if not cut.is_empty() else 0.0
    out = S.difference(shape, tool)
    res = ReliefBuild(ok=True, manifold=out, attempt=out, tool=tool, base=saddle, spec=sp, removed_volume=removed, skirt_volume=skirt_vol)
    pr = res.problems
    pr.extend(f"silla base: {p}" for p in saddle.problems)
    if out.is_empty():
        res.ok, res.manifold = False, None
        pr.append("la silla desapareció al despejarla")
        return res
    res.volume = abs(float(out.volume()))
    st = shell_stats(out)
    res.components, res.fragments, res.voids = st["components"], st["fragments"], st["voids"]
    ob = np.asarray(out.bounding_box(), float).reshape(2, 3)
    W = min_wall * 2 + 10.0
    res.wall_min = _wall_behind_cavity(out, tool, ob[0] - W, ob[1] + W, rim)
    res.overlaps, res.gaps, vprob = rider_pairs(out, rider, sp.clearance)
    pr.extend(vprob)
    if removed < 1e-6:
        warns.append("el jinete no toca la silla: no hay nada que despejar (¿asiento demasiado alto?)")
    if sp.skirt_drop > 0 and sp.skirt_thick < min_wall:
        pr.append(f"los faldones miden {sp.skirt_thick:g} mm de espesor (mínimo {min_wall:g})")
    if sp.skirt_drop > 0 and skirt_vol < 1e-6:
        warns.append("los faldones quedaron vacíos (el caballo o el despeje los comen enteros)")
    if res.components > 1:
        vols = ", ".join(f"{v:.1f}" for v in res.fragments)
        pr.append(f"la silla despejada tiene {res.components} componentes (fragmentos de {vols} mm³): el despeje la parte")
    if res.voids:
        pr.append(f"la silla despejada tiene {res.voids} cavidad(es) cerrada(s)")
    if res.wall_min is not None and res.wall_min < min_wall:
        pr.append(f"la pared que queda detrás del despeje mide {res.wall_min:.2f} mm (mínimo {min_wall:g})")
    res.warnings.extend(warns)
    if pr:
        res.ok, res.manifold = False, None
    return res


# ---------------------------------------------------------------------------------------------------------------- E08
RIDING_OVERLAP_TOL = 1e-6        # mm³: la misma tolerancia de interpenetración que `pipeline.validate`
RIDING_GAP_REL_TOL = 0.1         # fracción de la holgura que puede faltar (igual que `pipeline.validate`)
RIDING_FLOAT_MM = 1.0            # mm sobre la holgura prevista a partir de los cuales la pelvis «flota»


@dataclass
class RidingAssembly:
    """El conjunto jinete + caballo (+ silla) ya calculado, listo para validar y exportar (D1).

    `rider`: TODAS las piezas del jinete ya colocadas (`place_rider`); `lower` es la pelvis y las piernas completas (`rider_lower`).
    `carve`: resultado de `carve_horse` (si hay tallado; `carve.carved` es el caballo tallado, o None si se rechazó). `saddle` y
    `relief`: la silla base y su despeje (si hay silla). `limits`: los límites con que se talló (para revisar profundidad y zona).
    """

    horse: HorseModel
    rider: dict
    lower: dict
    pelvis_name: str = "pelvis"
    carve: CarveResult | None = None
    saddle: SaddleBuild | None = None
    relief: ReliefBuild | None = None
    limits: CarveLimits | None = None

    @property
    def horse_mf(self):
        """El caballo contra el que se valida: el tallado si existe y se aceptó; si no, el original."""
        return self.carve.carved if self.carve is not None and self.carve.carved is not None else self.horse.manifold

    @property
    def saddle_mf(self):
        """La silla final (con despeje y faldones) si se pudo hacer; si no, la base; None sin silla."""
        if self.relief is not None and self.relief.manifold is not None:
            return self.relief.manifold
        return self.saddle.manifold if self.saddle is not None else None


def _overlap(a, b) -> float:
    ba, bb = np.asarray(a.bounding_box(), float).reshape(2, 3), np.asarray(b.bounding_box(), float).reshape(2, 3)
    if np.any(ba[1] < bb[0]) or np.any(bb[1] < ba[0]):
        return 0.0
    return abs(float(S.intersection(a, b).volume()))


def validate_riding(ra: RidingAssembly, params=None) -> dict:
    """Validación ecuestre (plano 6): separada de `pipeline.validate`, que no se toca. Certifica la geometría de la pose, no la
    durabilidad de las articulaciones. Cada problema nombra la pareja exacta (`pierna_izq ↔ caballo`, `pelvis ↔ silla`).

    Devuelve `{"ok", "problems", "avisos", "info", "gaps"}`. Las uniones intencionales se declaran en `info["uniones"]`: una silla
    con `encaje` > 0 se hunde en el caballo a propósito y no cuenta como choque.
    """
    clearance = float(getattr(params, "clearance", 0.5)) if params is not None else 0.5
    min_wall = float(getattr(params, "min_wall", 1.2)) if params is not None else 1.2
    out = {"ok": True, "problems": [], "avisos": [], "info": {}, "gaps": {}}
    pr, av, info = out["problems"], out["avisos"], out["info"]
    gtol = RIDING_GAP_REL_TOL * clearance + 1e-6
    horse = ra.horse_mf
    # 1. caballo convertible a Manifold
    if ra.horse.manifold is None or ra.horse.manifold.status() != S.m3.Error.NoError:
        pr.append("caballo: no es una variedad estanca (no sirve para booleanas)")
        out["ok"] = False
        return out
    # 2. tallado: límites y mediciones propias del motor
    c = ra.carve
    if c is not None:
        info["tallado"] = {"retirado_mm3": round(c.removed_volume, 3), "profundidad_mm": round(c.max_depth, 3),
                           "pared_mm": None if c.wall_min is None else round(c.wall_min, 3), "fraccion": round(c.removed_frac, 5)}
        if not c.ok:
            pr.extend(f"caballo tallado: {x}" for x in c.problems)
        lim = ra.limits
        if lim is not None and c.ok:
            if c.max_depth > lim.max_depth:
                pr.append(f"caballo tallado: el rebaje entra {c.max_depth:.2f} mm (máximo {lim.max_depth:g})")
            if c.removed_frac > lim.max_removed_frac:
                pr.append(f"caballo tallado: se retira el {100 * c.removed_frac:.1f} % del caballo (máximo {100 * lim.max_removed_frac:g} %)")
            if lim.zone is not None and c.cut_bounds is not None:
                z, cb = np.asarray(lim.zone, float), np.asarray(c.cut_bounds, float)
                if np.any(cb[0] < z[:3] - 1e-6) or np.any(cb[1] > z[3:] + 1e-6):
                    pr.append("caballo tallado: el rebaje se sale de la zona permitida")
        if c.ok and c.carved is not None and c.tool is not None and c.removed_volume > 0:
            cb = np.asarray(c.carved.bounding_box(), float).reshape(2, 3)
            wm = _wall_behind_cavity(c.carved, c.tool, cb[0] - 10.0, cb[1] + 10.0, min_wall)       # se vuelve a medir
            info["tallado"]["pared_medida_mm"] = None if wm is None else round(wm, 3)
            if wm is not None and wm < min_wall:
                pr.append(f"caballo tallado: la pared detrás del rebaje mide {wm:.2f} mm (mínimo {min_wall:g})")
    # 3. silla: una componente, sin cavidades, pared, apoyo sobre el caballo
    sad = ra.saddle_mf
    if ra.saddle is not None and sad is None:
        pr.append("silla: no se pudo generar")
    if ra.relief is not None and ra.relief.manifold is None:
        pr.extend(f"silla: {x}" for x in ra.relief.problems)
    if sad is not None:
        st = shell_stats(sad)
        if st["components"] != 1:
            pr.append(f"silla: {st['components']} componentes (debería ser 1)")
        if st["voids"]:
            pr.append(f"silla: {st['voids']} cavidad(es) cerrada(s)")
        if ra.relief is not None and ra.relief.manifold is not None and ra.relief.tool is not None:
            sb = np.asarray(sad.bounding_box(), float).reshape(2, 3)
            wm = _wall_behind_cavity(sad, ra.relief.tool, sb[0] - 10.0, sb[1] + 10.0, min_wall)
            info["silla_pared_mm"] = None if wm is None else round(wm, 3)
            if wm is not None and wm < min_wall:
                pr.append(f"silla: la pared detrás del despeje mide {wm:.2f} mm (mínimo {min_wall:g})")
        enc = float(ra.saddle.spec.encaje) if ra.saddle is not None else 0.0
        ov = _overlap(sad, horse)
        expected = max(0.0, -enc)
        if enc > 0:
            info["uniones"] = [f"silla ↔ caballo: encaje de {enc:g} mm (intencional, {ov:.1f} mm³)"]
        elif ov > RIDING_OVERLAP_TOL:
            pr.append(f"silla ↔ caballo: se interpenetran {ov:.3f} mm³ sin que el encaje lo pida")
        else:
            sbb = np.asarray(sad.bounding_box(), float).reshape(2, 3)
            reach = expected + SADDLE_SEARCH
            win = S.intersection(horse, _box_mf(sbb[1] - sbb[0] + 2 * reach, (sbb[0] + sbb[1]) / 2.0))
            gap = float(sad.min_gap(win, reach)) if not win.is_empty() else float("inf")
            out["gaps"]["silla ↔ caballo"] = None if math.isinf(gap) else round(gap, 4)
            if gap > expected + SADDLE_TOUCH_TOL:
                pr.append(f"silla ↔ caballo: la silla flota {gap:.2f} mm sobre el caballo (esperado {expected:.2f} mm)")
    elif ra.saddle is None and ra.relief is None:
        info["silla"] = "sin silla (solo tallado)"
    # 4. piernas completas y pelvis contra silla y caballo tallado (si el despeje falló ya se informó: no se repite contra la base)
    sad_pairs = None if (ra.relief is not None and ra.relief.manifold is None) else sad
    for label, solid in (("caballo", horse), ("silla", sad_pairs)):
        if solid is None:
            continue
        ov, gp, prob = rider_pairs(solid, ra.lower, clearance, tol=gtol, overlap_tol=RIDING_OVERLAP_TOL, label=label)
        pr.extend(prob)
        for n, g in gp.items():
            out["gaps"][f"{n} ↔ {label}"] = None if math.isinf(g) else round(g, 4)
    # 5. el resto del jinete (torso, brazos, cabeza, armas) no puede entrar en el caballo ni en la silla
    for n, mf in ra.rider.items():
        if n in ra.lower:
            continue
        for label, solid in (("caballo", horse), ("silla", sad_pairs)):
            if solid is None:
                continue
            ov = _overlap(mf, solid)
            if ov > RIDING_OVERLAP_TOL:
                pr.append(f"{n} ↔ {label}: se interpenetran {ov:.3f} mm³")
    # 6. pelvis flotando
    pel = ra.lower.get(ra.pelvis_name)
    if pel is not None:
        other = "silla" if sad_pairs is not None else "caballo"
        g = out["gaps"].get(f"{ra.pelvis_name} ↔ {other}")
        if g is None or g > clearance + RIDING_FLOAT_MM:
            av.append(f"{ra.pelvis_name} ↔ {other}: la pelvis no apoya "
                      f"({'a más de ' + format(clearance + RIDING_FLOAT_MM, 'g') + ' mm' if g is None else format(g, '.2f') + ' mm'})")
    # 7. informativo: altura del jinete y escala del caballo
    rb = np.vstack([np.asarray(m.bounding_box(), float).reshape(2, 3) for m in ra.rider.values()])
    hb = np.asarray(ra.horse.manifold.bounding_box(), float).reshape(2, 3)
    info["altura_jinete_mm"] = round(float(rb[:, 2].max() - rb[:, 2].min()), 2)
    info["caballo_mm"] = [round(float(v), 2) for v in (hb[1] - hb[0])]
    info["escala_caballo"] = float(ra.horse.spec.escala)
    if ra.relief is not None:
        av.extend(f"silla: {w}" for w in ra.relief.warnings)
    if c is not None:
        av.extend(f"caballo tallado: {w}" for w in c.warnings)
    out["ok"] = not pr
    return out


# ---------------------------------------------------------------------------------------------------------------- E09
SAFE_MAX_DEPTH = 10.0            # mm: tope duro de profundidad del tallado; ningún ajuste puede pedir más
FIT_MODES = ("tallado", "silla", "ambos")


@dataclass
class FitResult:
    """Un intento de montar la figura en un asiento. `ok` solo es True si pasó `validate_riding` sin problemas."""

    ok: bool
    mode: str
    seat_origin: np.ndarray
    direction: np.ndarray
    pitch_deg: float
    assembly: RidingAssembly | None = None
    validation: dict | None = None
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    stage: str = ""                  # dónde se detuvo: «silla», «tallado», «despeje», «validación» o «ok»
    seconds: float = 0.0


class RidingFitter:
    """Ajuste iterativo del asiento (E09): se construye una vez con la figura armada y el caballo y se recalcula tallado,
    silla y validación cada vez que cambian el punto, la dirección, la inclinación o el tamaño de la silla.

    La figura NO se vuelve a armar: el perfil del jinete se lee una sola vez y cada intento aplica una matriz rígida nueva a las
    piezas ya armadas (D4: pasadores, holguras y articulaciones quedan como salieron del motor). Un intento solo se acepta si
    pasa la validación ecuestre; si no, `current` sigue siendo el último intento aceptado. La profundidad del tallado nunca puede
    pasar de `SAFE_MAX_DEPTH`.
    """

    def __init__(self, horse: HorseModel, asm, params=None, limits: CarveLimits | None = None):
        self.horse = horse
        self.asm = asm
        self.params = params if params is not None else asm.params
        self.rp = rider_profile(asm)
        mw = float(getattr(self.params, "min_wall", 1.2))
        self.limits = limits or CarveLimits(min_wall=mw)
        if self.limits.max_depth > SAFE_MAX_DEPTH:
            raise HorseError(f"«max_depth» {self.limits.max_depth:g} mm pasa el máximo seguro de {SAFE_MAX_DEPTH:g} mm")
        self.clearance = float(getattr(self.params, "clearance", 0.5))
        self.min_wall = mw
        self.current: FitResult | None = None
        self.history: list = []

    def back_z(self, x: float, y: float) -> float | None:
        """Altura del lomo (punto más alto del caballo) bajo `(x, y)`; None si no hay caballo ahí."""
        col = S.intersection(self.horse.manifold, _box_mf((0.5, 0.5, 4000.0), (x, y, 0.0)))
        return None if col.is_empty() else float(np.asarray(col.bounding_box(), float).reshape(2, 3)[1][2])

    def fit(self, seat_origin, direction=(0.0, 1.0, 0.0), pitch_deg: float = 0.0, mode: str = "tallado",
            saddle_spec: SaddleSpec | None = None, relief_spec: ReliefSpec | None = None) -> FitResult:
        """Un intento: marco de asiento → tallado y/o silla con despeje → validación. Se detiene en la primera etapa que falla."""
        import time

        if mode not in FIT_MODES:
            raise HorseError(f"«mode» tiene que ser uno de {', '.join(FIT_MODES)}")
        t0 = time.time()
        o = np.asarray(seat_origin, float)
        res = FitResult(ok=False, mode=mode, seat_origin=o, direction=np.asarray(direction, float), pitch_deg=float(pitch_deg))
        use_saddle, use_carve = mode in ("silla", "ambos"), mode in ("tallado", "ambos")
        sad = rel = carve = None
        anchor_z = float(o[2])
        if use_saddle:
            sad = build_saddle(self.horse, saddle_spec or suggest_saddle_spec(self.rp), o, direction, self.min_wall)
            anchor_z = o[2] + sad.lift
            if not sad.ok:
                res.problems.extend(f"silla: {p}" for p in sad.problems)
                res.stage = "silla"
        if not res.problems:
            frame = seat_frame(self.rp, (o[0], o[1], anchor_z), direction, pitch_deg)
            placed = place_rider(self.asm, frame)
            low = rider_lower(self.rp, placed)
            legs = {k: v for k, v in low.items() if k != self.rp.pelvis_name}
            if use_carve:
                carve = carve_horse(self.horse, legs, self.clearance, self.limits)
                res.warnings.extend(carve.warnings)
                if not carve.ok:
                    res.problems.extend(f"tallado: {p}" for p in carve.problems)
                    res.stage = "tallado"
            if not res.problems and use_saddle:
                rel = relieve_saddle(self.horse, sad, low, relief_spec or ReliefSpec(clearance=self.clearance), self.min_wall)
                res.warnings.extend(rel.warnings)
                if not rel.ok:
                    res.problems.extend(f"despeje: {p}" for p in rel.problems)
                    res.stage = "despeje"
            res.assembly = RidingAssembly(horse=self.horse, rider=placed, lower=low, pelvis_name=self.rp.pelvis_name,
                                          carve=carve, saddle=sad, relief=rel, limits=self.limits)
            if not res.problems:
                res.validation = validate_riding(res.assembly, self.params)
                res.warnings.extend(res.validation["avisos"])
                if not res.validation["ok"]:
                    res.problems.extend(res.validation["problems"])
                    res.stage = "validación"
        res.ok = not res.problems
        if res.ok:
            res.stage = "ok"
            self.current = res
        res.seconds = time.time() - t0
        self.history.append(res)
        return res

    def sweep(self, ys, x: float = 0.0, direction=(0.0, 1.0, 0.0), **kw) -> list:
        """Prueba un asiento por cada `y` a lo largo del lomo (el punto es el más alto del caballo bajo `(x, y)`). Devuelve los intentos."""
        out = []
        for y in ys:
            z = self.back_z(x, float(y))
            if z is None:
                continue
            out.append(self.fit((x, float(y), z), direction, **kw))
        return out
