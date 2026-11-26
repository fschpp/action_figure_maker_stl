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

from dataclasses import dataclass, field
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


@runtime_checkable
class Motion(Protocol):
    """Cómo se mueve la pieza hija. Hoy solo `RevoluteMotion`; la rótula (`ConeMotion`, Fase 4) la generaliza."""

    def poses(self, step_deg: float = 5.0) -> Iterable[np.ndarray]: ...
    def revolute(self) -> "RevoluteMotion | None": ...


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
    motion: RevoluteMotion                                # §3.3
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
    def build(self, site: JointSite) -> JointResult: ...
