"""Cintura articulada: corte del torso por un plano de 3 puntos + pasador de hongo robusto (giro «a los costados»).

Misma tecnología que el hombro y el cuello (`head.py`): un pasador de hongo (vástago + cabeza achaflanada) sobre
la cara del corte, una cavidad con la holgura de impresión (`Params.clearance`) y giro libre alrededor del eje del
pasador. Lo que cambia es la escala: el pasador de la cintura (`Params.waist_pin`) es bastante más grueso que el de
hombro (vástago Ø6 contra Ø4,1, cabeza Ø10 con relación 1,67) porque sostiene todo el torso y trabaja a flexión
cuando la figura se agarra del pecho; el vástago largo deja una pared de ≥ 2 mm detrás de la cabeza.

Convenciones (marco de trabajo: plano de simetría en x = 0, Z arriba):

* El plano se simetriza como el del cuello: la normal se proyecta sobre x = 0 y apunta hacia arriba, el origen se
  lleva a x = 0. El pasador queda centrado en x = 0 y el eje de giro es la normal del plano (el plano puede estar
  inclinado: la figura gira sobre ese eje, no sobre la vertical).
* El torso (ya con todas sus cavidades) se parte en **torso** (arriba: pecho, hombros, cuello) y **pelvis** (abajo:
  cadera). Entre las dos caras queda `clearance` mm de aire medidos sobre la normal, así que ningún giro alrededor
  del eje las acerca: las dos mitades del torso no pueden chocar entre sí, solo los miembros que cuelgan de la mitad
  de arriba (brazos) contra la de abajo, y eso lo mide `pipeline.validate` (`val["cintura"]`).
* El pasador sale de la **pelvis hacia arriba** (`pin_on="lower"`, por defecto): al imprimir la figura sentada y con la
  cabeza arriba, el vástago crece desde la pelvis y la cavidad del pecho queda «techada» (puente corto) en lugar de
  imprimir una cabeza flotando en el aire. `pin_on="upper"` lo invierte.
* Con `auto` (y sin `pin_center`) el pasador va en el centro del círculo inscrito de la cara del corte; si la cabeza
  no entra con `min_wall` de pared a cada lado, se achican los diámetros (misma relación) y se informa.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import numpy as np

from . import solids as S
from .head import _plane_faces, propose_pin_center
from .limbs import ContactFace, _face_shape, pin_margin
from .params import Params, PinSpec
from .pins import mushroom_pin
from .planes import Plane

WAIST_NAME = "cintura"
PELVIS_NAME = "pelvis"
MIN_UP = 0.3             # |n·Z| mínimo: un plano casi vertical no separa pecho de pelvis
MAX_SIDE = 0.5           # |n·X| máximo antes de simetrizar
FIT_SLACK = 0.1          # mm de más de pared al achicar el pasador (la malla poligonal de la piel queda un poco adentro del círculo)
MIN_PIN_SCALE = 0.4      # no se achica el pasador por debajo de esta fracción del original (la cintura es demasiado fina)
PIN_ON = ("lower", "upper")


@dataclass
class WaistSpec:
    """Cintura: plano de corte (3 puntos) y, opcionalmente, centro y medidas del pasador."""

    plane: Plane
    name: str = WAIST_NAME
    pin_center: tuple | None = None       # centro del pasador (mundo); None = círculo inscrito mayor de la cara
    auto: bool = True                     # True: propone el centro y achica el pasador si no entra en la cara
    pin: PinSpec | None = None            # None = `Params.waist_pin`
    pin_on: str = "lower"                 # "lower": el pasador sale de la pelvis hacia arriba; "upper": del pecho hacia abajo
    twist_deg: float | None = None        # giro a cada lado que se valida contra los miembros; None = `Params.waist_twist`

    def to_dict(self) -> dict:
        return {"name": self.name, "plane": self.plane.to_dict(),
                "pin_center": None if self.pin_center is None else list(self.pin_center), "auto": self.auto,
                "pin": None if self.pin is None else dataclasses.asdict(self.pin),
                "pin_on": self.pin_on, "twist_deg": self.twist_deg}

    @classmethod
    def from_dict(cls, d: dict) -> "WaistSpec":
        return cls(plane=Plane.from_dict(d["plane"]), name=d.get("name", WAIST_NAME),
                   pin_center=None if d.get("pin_center") is None else tuple(d["pin_center"]),
                   auto=d.get("auto", True), pin=None if d.get("pin") is None else PinSpec(**d["pin"]),
                   pin_on=d.get("pin_on", "lower"), twist_deg=d.get("twist_deg"))


@dataclass
class WaistBuild:
    upper: S.Manifold            # torso de arriba (pecho) con la cavidad del pasador (o con el pasador si pin_on="upper")
    lower: S.Manifold            # pelvis, con el pasador (o con la cavidad si pin_on="upper")
    pin: S.Manifold              # el pasador solo (para validar y dibujar)
    pocket: S.Manifold           # pasador dilatado por la holgura (lo que se restó al huésped)
    plane: Plane                 # plano simetrizado, normal hacia arriba, sobre la cara de la pelvis
    face: ContactFace            # cara donde nace el pasador; center = centro del pasador
    axis: np.ndarray             # eje de giro (normal del plano, hacia arriba)
    pin_spec: PinSpec            # medidas efectivas del pasador (tras el ajuste a la cara)
    pin_on: str
    info: dict = field(default_factory=dict)


def waist_plane(spec: WaistSpec) -> Plane:
    """Plano de la cintura simetrizado: normal en YZ apuntando hacia arriba, origen sobre x = 0."""
    n = np.asarray(spec.plane.normal, float)
    n = n / np.linalg.norm(n)
    if n[2] < 0:
        n = -n
    if n[2] < MIN_UP:
        raise ValueError(f"el plano de la cintura está casi vertical (|n·Z| = {abs(n[2]):.2f} < {MIN_UP}): "
                         "elegí 3 puntos a una misma altura alrededor de la cintura")
    if abs(n[0]) > MAX_SIDE:
        raise ValueError("el plano de la cintura está girado de costado: tiene que contener la dirección "
                         "izquierda–derecha (2 puntos a cada lado de la cintura y 1 adelante o atrás)")
    n = np.array([0.0, n[1], n[2]])
    n /= np.linalg.norm(n)
    o = np.asarray(spec.plane.origin, float).copy()
    o[0] = 0.0
    return Plane.from_normal(o, n, spec.name)


def waist_pin_spec(spec: WaistSpec, p: Params) -> PinSpec:
    return spec.pin or p.waist_pin


def twist_range(spec: WaistSpec | None, p: Params) -> float:
    return float(spec.twist_deg if spec is not None and spec.twist_deg is not None else p.waist_twist)


def _face_on(mf: S.Manifold, plane: Plane, outward: np.ndarray) -> ContactFace:
    """Cara de `mf` que yace sobre `plane` (centro = centroide con x = 0, `outward` = normal que sale de `mf`)."""
    tri = _plane_faces(mf, plane)
    if len(tri) == 0:
        raise ValueError("el plano de la cintura no atraviesa el torso (la cara del corte quedó vacía)")
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area_f = 0.5 * np.linalg.norm(cross, axis=1)
    centroid = (tri.mean(axis=1) * area_f[:, None]).sum(axis=0) / area_f.sum()
    centroid[0] = 0.0
    return ContactFace(center=centroid, outward=np.asarray(outward, float).copy(), area=float(area_f.sum()), triangles=tri)


def _inscribed_radius(face: ContactFace) -> float:
    """Distancia del centro de `face` al borde de su huella (positiva si el centro cae dentro)."""
    from shapely.geometry import Point

    _plane, shape = _face_shape(face)
    pt = Point(0.0, 0.0)
    d = float(shape.boundary.distance(pt))
    return d if shape.contains(pt) else -d


def fit_pin(pin: PinSpec, face: ContactFace, min_wall: float) -> tuple[PinSpec, float]:
    """Achica los diámetros (misma relación cabeza/vástago, mismos largos) hasta que la cabeza entre en la cara con
    `min_wall` de pared a cada lado. Devuelve (pasador, factor); factor 1 = no hizo falta."""
    room = 2.0 * (_inscribed_radius(face) - min_wall - FIT_SLACK)
    if room + 2.0 * FIT_SLACK >= pin.head_d - 1e-9:
        return pin, 1.0          # ya entra con `min_wall` de pared
    k = max(room, 0.0) / pin.head_d
    if k < MIN_PIN_SCALE:
        raise ValueError(f"la cara del corte es demasiado chica para el pasador de la cintura: entra una cabeza de "
                         f"Ø{max(room, 0.0):.1f} mm (con {min_wall:g} mm de pared) y el pasador pide Ø{pin.head_d:g}; "
                         "elegí el plano en una parte más ancha de la cintura o usá un pasador más chico")
    return dataclasses.replace(pin, shaft_d=pin.shaft_d * k, head_d=pin.head_d * k), float(k)


def lip_mm(pin: PinSpec, clearance: float) -> float:
    """Pared de material entre la cavidad de la cabeza y la de la cara del corte: `shaft_len − 2·clearance`
    (la cara del huésped queda a `clearance` del plano y la cavidad dilata la cabeza otra `clearance`)."""
    return pin.shaft_len - 2.0 * clearance


def build_waist(torso: S.Manifold, spec: WaistSpec, p: Params, skin: S.Manifold | None = None) -> WaistBuild:
    """Parte el torso ENTERO (simétrico, con todas sus cavidades) por el plano de la cintura y arma el pasador.

    `skin`: el cuerpo original, para medir cuánto sobresale el pasador de la piel (`info["pasador_fuera_mm3"]`)."""
    if spec.pin_on not in PIN_ON:
        raise ValueError(f"pin_on debe ser uno de {PIN_ON}, no {spec.pin_on!r}")
    plane = waist_plane(spec)
    n = plane.normal
    up = Plane.from_normal(plane.origin + n * p.clearance, n)           # cara de la mitad de arriba
    upper = S.intersection(torso, S.halfspace(up))
    lower = S.intersection(torso, S.halfspace(Plane.from_normal(plane.origin, -n)))
    if upper.is_empty() or upper.volume() < 1.0:
        raise ValueError("el plano de la cintura deja el torso sin parte de arriba (¿está por encima de la figura?)")
    if lower.is_empty() or lower.volume() < 1.0:
        raise ValueError("el plano de la cintura deja el torso sin pelvis (¿está por debajo de la figura?)")
    on_lower = spec.pin_on == "lower"
    host, host_plane = (lower, plane) if on_lower else (upper, up)
    d = n if on_lower else -n                                           # dirección del pasador: hacia el huésped opuesto
    face = _face_on(host, host_plane, d)          # como en limbs: outward = hacia donde sale el pasador
    if spec.pin_center is not None:
        c = np.asarray(spec.pin_center, float)
        c = c - ((c - host_plane.origin) @ n) * n                       # sobre la cara
        c[0] = 0.0
    elif spec.auto:
        c = propose_pin_center(face)
    else:
        c = face.center
    face = dataclasses.replace(face, center=c)
    pin_spec = waist_pin_spec(spec, p)
    k = 1.0
    if spec.auto or spec.pin is None:
        pin_spec, k = fit_pin(pin_spec, face, p.min_wall)
    pin = mushroom_pin(c, d, pin_spec)
    pocket = S.dilate(pin, p.clearance)
    if on_lower:
        lower, upper = S.union(lower, pin), S.difference(upper, pocket)
    else:
        upper, lower = S.union(upper, pin), S.difference(lower, pocket)
    info = {"pin_center": c.tolist(), "axis": n.tolist(), "pin_on": spec.pin_on,
            "pin": dataclasses.asdict(pin_spec), "pin_reducido": None if k == 1.0 else round(k, 3),
            "pared_tras_cabeza_mm": round(lip_mm(pin_spec, p.clearance), 3),
            "relacion_cabeza_vastago": round(pin_spec.head_d / pin_spec.shaft_d, 3),
            "margen_vastago_mm": _round(pin_margin(face, pin_spec.shaft_d)),
            "margen_cabeza_mm": _round(pin_margin(face, pin_spec.head_d)),
            "area_cara_mm2": round(float(face.area), 2), "giro_pedido_grados": twist_range(spec, p)}
    if skin is not None:
        # cuánto de la cavidad/pasador engrosado `min_wall` queda fuera del cuerpo original = pared que falta
        info["pasador_fuera_mm3"] = round(float(S.difference(S.dilate(pin, p.min_wall), skin).volume()), 4)
    return WaistBuild(upper=upper, lower=lower, pin=pin, pocket=pocket, plane=plane, face=face, axis=n.copy(),
                      pin_spec=pin_spec, pin_on=spec.pin_on, info=info)


def _round(x) -> float | None:
    return None if x is None else round(float(x), 3)
