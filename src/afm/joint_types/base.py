"""Contrato de un tipo de articulación (plan de plugins de articulación, §3.2–§3.3, Fase 0).

Un **corte** es un plano: de un lado queda la pieza padre, del otro la hija, y en el medio no hay nada salvo la holgura. Lo que
se pone en ese medio es el **tipo de articulación**. El núcleo (`pipeline.build`) entrega el corte como un `JointSite` y recibe
un `JointResult`: la pieza hija con su pasador, lo que hay que RESTAR del padre y del anfitrión (cavidad con holgura), lo que
hay que SUMAR al padre, cómo SE MUEVE (`RevoluteMotion`) y qué EXIGE a la cara y al anfitrión (`FaceRequirements`).

Reglas que el contrato le impone a un tipo (para no romper el port a Pyodide): usa solo `afm.solids` (única puerta a
`manifold3d`), `afm.pins`, `afm.params`, `numpy` y `shapely`; es determinista; no modifica sus entradas; trabaja en mm absolutos de
impresión y en la pose «de pie» (ángulo 0).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, NamedTuple, Protocol, runtime_checkable

import numpy as np

from .. import solids as S
from ..limbs import ContactFace
from ..params import Params
from ..planes import Plane

API_VERSION = 1


def rotation_axis(face: ContactFace) -> np.ndarray:
    """Eje de giro de un pasador sobre la cara: su normal, orientada de modo que +ángulo = hacia adelante (+Y) para −Z."""
    ax = face.outward / np.linalg.norm(face.outward)
    return ax if ax[0] >= 0 else -ax


def sweep_angles(rng: tuple[float, float], step: float) -> list[float]:
    """Ángulos del barrido de la cavidad: de `rng[0]` a `rng[1]` en pasos de `step`, cerrando siempre en `rng[1]`."""
    a0, a1 = rng
    angles = np.arange(a0, a1 + 1e-9, step) if a1 > a0 else np.array([a0])
    if len(angles) > 1 and angles[-1] < a1 - 1e-9:
        angles = np.append(angles, a1)
    return [float(a) for a in angles]


class RevoluteMotion(NamedTuple):
    """Movimiento de un solo eje. Es IDÉNTICA a la tupla `(limb, centro, eje, (a0, a1))` que `BuildResult.motion` guarda desde
    siempre: se desempaqueta igual (`_l, c, ax, (a0, a1) = res.motion[n]`), así que ningún consumidor existente cambia."""
    limb: S.Manifold          # la pieza móvil de pie (ángulo 0)
    center: np.ndarray        # un punto del eje
    axis: np.ndarray          # eje unitario (+ángulo = hacia adelante)
    range: tuple              # (a0, a1) en grados: el recorrido que se valida

    def revolute(self) -> "RevoluteMotion":
        return self

    def poses(self, step_deg: float = 5.0) -> Iterable[np.ndarray]:
        """Matrices 4×4 de la pieza móvil respecto de su padre, de `a0` a `a1` en pasos de `step_deg`."""
        for a in sweep_angles(tuple(self.range), step_deg):
            yield rotation_matrix(self.center, self.axis, a)

    def print_pose(self, angle_deg: float = 0.0) -> np.ndarray:
        """4×4 de la pose impresa. Un eje no sabe a qué ángulo se imprime (lo fijan `Params.sit_angle`, `print_angle`…): lo da quien llama."""
        return rotation_matrix(self.center, self.axis, angle_deg)

    def with_limb(self, limb: S.Manifold) -> "RevoluteMotion":
        """La misma articulación con otra malla de la pieza móvil (p. ej. la compactada)."""
        return self._replace(limb=limb)


def rotation_matrix(origin, axis, angle_deg: float) -> np.ndarray:
    """4×4 de la rotación `angle_deg` alrededor de la recta (origen, eje): la misma cuenta que `S.rotate_about_axis`."""
    o = np.asarray(origin, float)
    ax = np.asarray(axis, float)
    ax = ax / np.linalg.norm(ax)
    a = np.radians(angle_deg)
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    R = np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * (K @ K)
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = o - R @ o
    return T

def _perpendicular_basis(axis) -> tuple[np.ndarray, np.ndarray]:
    """Dos vectores unitarios perpendiculares entre sí y a `axis` (determinista)."""
    ax = np.asarray(axis, float)
    ax = ax / np.linalg.norm(ax)
    seed = np.array([0.0, 0.0, 1.0]) if abs(ax[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(ax, seed)
    u /= np.linalg.norm(u)
    return u, np.cross(ax, u)


@dataclass(frozen=True)
class ConeMotion:
    """Movimiento de una rótula: la pieza móvil inclina su eje de reposo `axis` hasta `half_angle` grados en cualquier dirección
    (un cono) y además puede girar sobre sí misma (`twist`). No es de un solo eje: `revolute()` devuelve `None`, y los consumidores
    que solo saben de un eje (color, clic, caballos) lo saltean. `print_tilt` = `(inclinación°, azimut°, giro propio°)` de la pose impresa.

    `poses(step)` recorre una grilla: inclinaciones de 0 a `half_angle` y, para cada una, tantos azimuts como hagan falta para que el
    paso sobre el arco no pase de `step` (en el eje, un solo azimut); cada pose se repite por los giros de `twist`."""
    limb: S.Manifold                       # la pieza móvil de pie (pose de reposo)
    center: np.ndarray                     # centro de la esfera
    axis: np.ndarray                       # eje de reposo del cuello (unitario o no: se normaliza)
    half_angle: float                      # inclinación máxima, grados
    twist: tuple = (0.0, 0.0)              # giro propio (a0, a1), grados
    print_tilt: tuple = (0.0, 0.0, 0.0)    # (inclinación, azimut, giro propio) de la pose impresa, grados

    def __post_init__(self):
        if not 0.0 <= float(self.half_angle) <= 90.0:
            raise ValueError(f"ConeMotion: half_angle {self.half_angle}° fuera de 0–90°")
        if float(self.twist[1]) < float(self.twist[0]):
            raise ValueError(f"ConeMotion: el giro propio {tuple(self.twist)} va de menor a mayor")

    def revolute(self) -> None:
        return None

    def with_limb(self, limb: S.Manifold) -> "ConeMotion":
        return replace(self, limb=limb)

    def pose(self, tilt_deg: float, azimuth_deg: float = 0.0, twist_deg: float = 0.0) -> np.ndarray:
        """4×4 de inclinar `tilt_deg` hacia el azimut `azimuth_deg` (0° = primer perpendicular del eje) tras girar `twist_deg` sobre el eje."""
        c = np.asarray(self.center, float)
        u, v = _perpendicular_basis(self.axis)
        a = np.radians(azimuth_deg)
        swing = rotation_matrix(c, np.cos(a) * u + np.sin(a) * v, tilt_deg)
        return swing @ rotation_matrix(c, self.axis, twist_deg)

    def poses(self, step_deg: float = 5.0) -> Iterable[np.ndarray]:
        step = max(float(step_deg), 1e-6)
        twists = sweep_angles(tuple(self.twist), step)
        yield from (self.pose(0.0, 0.0, tw) for tw in twists)
        for tilt in (t for t in sweep_angles((0.0, float(self.half_angle)), step) if t > 1e-9):
            n_az = max(4, int(np.ceil(360.0 * np.sin(np.radians(tilt)) / step)))
            for k in range(n_az):
                for tw in twists:
                    yield self.pose(tilt, 360.0 * k / n_az, tw)

    def print_pose(self, angle_deg: float | None = None) -> np.ndarray:
        """Pose impresa (`print_tilt`). `angle_deg` no se usa: está en la firma para que `RevoluteMotion` y esta se llamen igual."""
        tilt, az, tw = (float(x) for x in self.print_tilt)
        return self.pose(tilt, az, tw)

    def contains(self, tilt_deg: float) -> bool:
        """¿Una inclinación cae dentro del cono declarado?"""
        return 0.0 <= float(tilt_deg) <= float(self.half_angle) + 1e-9


def as_revolute(motion) -> "RevoluteMotion | None":
    """Vista de un solo eje de un movimiento, o `None` si no la tiene. Acepta un `Motion` y también la tupla `(limb, centro, eje, (a0, a1))`
    de siempre (proyectos y pruebas que la arman a mano)."""
    if motion is None:
        return None
    if isinstance(motion, RevoluteMotion):
        return motion
    rv = getattr(motion, "revolute", None)
    if callable(rv):
        return rv()
    if isinstance(motion, (tuple, list)) and len(motion) == 4:
        return RevoluteMotion(*motion)
    return None


def revolute_motions(motions: dict | None) -> dict:
    """`{nombre: RevoluteMotion}` de los movimientos de un solo eje de `BuildResult.motion`, en el mismo orden. Los de rótula quedan
    afuera: quien recorre ángulos con esto (validación, color, caballos) los saltea y no se rompe."""
    out = {}
    for name, m in (motions or {}).items():
        r = as_revolute(m)
        if r is not None:
            out[name] = r
    return out


@runtime_checkable
class Motion(Protocol):
    """Cómo se mueve la pieza hija: `RevoluteMotion` (un eje) o `ConeMotion` (rótula). Quien solo sabe de un eje llama `revolute()` y
    salta lo que devuelva `None` (`as_revolute`, `revolute_motions`)."""

    limb: S.Manifold

    def poses(self, step_deg: float = 5.0) -> Iterable[np.ndarray]: ...
    def print_pose(self, angle_deg: float | None = None) -> np.ndarray: ...
    def revolute(self) -> "RevoluteMotion | None": ...
    def with_limb(self, limb: S.Manifold) -> "Motion": ...


@dataclass(frozen=True)
class FaceRequirements:
    """Lo que el tipo le pide a la cara y al anfitrión. Mismas claves que `capacity.demand` (que esta clase reemplazará en la
    Fase 1): `profundidad_mm` (cuerpo necesario detrás de la cara), `pared_min_mm` (piel que debe quedar), `huella_radio_mm`
    (radio de la cabeza sobre la cara) y, si el tipo se enfrenta a su espejo (cadera), `separacion_min_mm`."""
    profundidad_mm: float
    pared_min_mm: float
    huella_radio_mm: float
    separacion_min_mm: float | None = None

    def to_dict(self) -> dict:
        d = {"profundidad_mm": self.profundidad_mm, "pared_min_mm": self.pared_min_mm, "huella_radio_mm": self.huella_radio_mm}
        if self.separacion_min_mm is not None:
            d["separacion_min_mm"] = self.separacion_min_mm
        return d


@dataclass(frozen=True)
class JointSite:
    """El corte tal como lo ve el tipo de articulación (todo en la pose «de pie», ángulo 0)."""
    slot: str                                 # nombre del miembro/lugar («pierna», «brazo», …)
    side: str                                 # «izq» | «der»: el motor trabaja en +X y espeja con `S.mirror_x`
    child: S.Manifold                         # la hija ya cortada (con la rebaba rebajada)
    face: ContactFace                         # centro, outward, área, triángulos
    params: Params                            # globales: clearance, min_wall, sweep_step, ref_height…
    jp: object                                # parámetros propios del tipo (instancia de `params_cls`)
    plane: Plane | None = None                # solo en articulaciones DENTRO de un miembro (Fase 2)
    parent: S.Manifold | None = None          # solo lectura: mide espacio; None en la vista previa
    host: S.Manifold | None = None            # el torso/anfitrión, solo lectura
    flex: tuple | None = None                 # rango pedido (a0, a1) en grados, o None = el del tipo
    print_hint: dict = field(default_factory=dict)   # pose impresa: «print_angle» (° del miembro al imprimir)
    opts: dict = field(default_factory=dict)          # opciones propias de cada tipo: «pin» (PinSpec del hongo del clic), «rod_step» (bisagra)
    segments: int = 12                        # resolución de la cavidad; la vista previa de la GUI baja este valor
    say: Callable[[str], None] = lambda _msg: None   # avance («Cavidad de pierna…»)


@dataclass
class JointResult:
    """Lo que el tipo devuelve al núcleo."""
    pieces: dict[str, S.Manifold]                         # {«child»: …} (o {«prox»: …, «dist»: …}); el motor los nombra
    cutters: dict[str, list[S.Manifold]]                  # se RESTAN. Destinos: «parent» y «host»
    motion: Motion                                        # §3.3: `RevoluteMotion` (un eje) o `ConeMotion` (rótula)
    parent_adds: list[S.Manifold] = field(default_factory=list)   # se SUMAN al padre al final (lengüetas del clic)
    report: dict = field(default_factory=dict)            # lo que el tipo quiere dejar en el informe de armado
    warnings: list[str] = field(default_factory=list)
    extras: dict[str, S.Manifold] = field(default_factory=dict)   # piezas SUELTAS (un pasador aparte)
    assembly: str = "print_in_place"                      # «post_assembly» si `extras` no está vacío
    native: object = None                                 # resultado propio del tipo (`DetentLimb`, `JointBuild`) mientras sus consumidores
                                                          # (lengüetas del clic, validación de cadena) sigan leyéndolo; se achica en fases siguientes


@dataclass(frozen=True)
class Finding:
    """Un hallazgo de la validación propia del tipo."""
    level: str          # «ok» | «aviso» | «error»
    code: str
    message: str


@runtime_checkable
class JointType(Protocol):
    id: str
    labels: dict[str, str]            # nombre por idioma (es/en/pt)
    api_version: int
    params_cls: type                  # dataclass de los parámetros propios del tipo
    slots: frozenset[str]             # en qué lugares tiene sentido

    def default_params(self, params: Params, slot: str) -> object: ...
    def requires(self, jp, params: Params, slot: str | None = None) -> FaceRequirements | None: ...   # None = no pide nada al anfitrión
    def pin_shift(self, jp, params: Params, slot: str | None = None) -> float: ...   # mm que corre el hongo hacia adentro (el clic: disc_h)
    def build(self, site: JointSite) -> JointResult: ...
