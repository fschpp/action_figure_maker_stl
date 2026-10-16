"""Cabeza articulada: corte por el plano del cuello + pasador tipo hongo (como los hombros).

Tutorial de Onshape, pasos 001–004 (plano «cabeza cuerpo» por 3 puntos, intersección) para el corte, y
pasos 030–033 (vástago + cabeza del hongo, cavidad con holgura) para la articulación. Aquí el plano del
cuello hace de herramienta única: lo que queda del lado de la cabeza es la cabeza; el resto, el cuerpo.

Convenciones (marco de trabajo: plano de simetría en x = 0, Z arriba):

* El plano del cuello se simetriza: su normal se proyecta sobre x = 0 (la cabeza es simétrica) y apunta
  hacia arriba. Así la cara del corte es simétrica y el pasador queda centrado en x = 0.
* La cabeza es UNA sola pieza (a diferencia de brazos y piernas no hay «izquierda/derecha»): se corta del
  cuerpo ENTERO (así la cara del cuello es un único polígono y no hay costura en x = 0). Solo si hay
  recortes manuales sobre la cabeza se trabaja la mitad de trabajo y se espeja. El pasador se agrega a la entera.
* Entre la cara del corte y el torso queda `clearance` mm de aire; el pasador entra en una cavidad con
  la misma holgura (dilatación del pasador), igual que en los hombros.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from . import solids as S
from .limbs import ContactFace, _face_shape, pin_fit
from .params import Params
from .pins import mushroom_pin
from .planes import Plane

HEAD_NAME = "cabeza"
MIN_UP = 0.3          # |n·Z| mínimo del plano del cuello (un plano casi vertical no separa cabeza de cuerpo)
MAX_SIDE = 0.5        # |n·X| máximo antes de simetrizar (el plano tiene que contener el eje izquierda-derecha)


@dataclass
class HeadSpec:
    """Cabeza: plano del cuello (3 puntos) y, opcionalmente, centro del pasador.

    La cabeza gira libre sobre el cuello (giro «no»): el pasador es un cilindro cuyo eje pasa por el centro de
    una cara plana perpendicular a él, así que ningún ángulo de giro la acerca al cuerpo; no hace falta barrido."""

    plane: Plane
    name: str = HEAD_NAME
    pin_center: tuple | None = None       # centro del pasador (mundo); None = círculo inscrito mayor de la cara
    auto: bool = True

    def to_dict(self) -> dict:
        return {"name": self.name, "plane": self.plane.to_dict(),
                "pin_center": None if self.pin_center is None else list(self.pin_center),
                "auto": self.auto}

    @classmethod
    def from_dict(cls, d: dict) -> "HeadSpec":
        return cls(plane=Plane.from_dict(d["plane"]), name=d.get("name", HEAD_NAME),
                   pin_center=None if d.get("pin_center") is None else tuple(d["pin_center"]),
                   auto=d.get("auto", True))


@dataclass
class HeadSplit:
    head: S.Manifold             # cabeza entera, sin pasador
    body_half: S.Manifold        # mitad de trabajo (x >= 0) del cuerpo, ya con la holgura del cuello
    plane: Plane                 # plano del cuello simetrizado, normal hacia la cabeza
    face: ContactFace            # cara del corte, normal hacia el cuerpo


@dataclass
class HeadBuild:
    head: S.Manifold             # cabeza entera con su pasador (pieza final)
    pocket: S.Manifold           # cavidad (pasador dilatado por la holgura) que se resta del cuerpo
    face: ContactFace            # center = centro del pasador
    axis: np.ndarray             # eje del cuello (hacia la cabeza): eje de giro
    pin_margin: float | None


def neck_plane(spec: HeadSpec) -> Plane:
    """Plano del cuello simetrizado: normal en el plano YZ y apuntando hacia arriba, origen en x = 0."""
    n = np.asarray(spec.plane.normal, float)
    n = n / np.linalg.norm(n)
    if n[2] < 0:
        n = -n
    if n[2] < MIN_UP:
        raise ValueError(f"el plano del cuello está casi vertical (|n·Z| = {abs(n[2]):.2f} < {MIN_UP}): "
                         "elegí 2 puntos en la nuca y 1 bajo el mentón")
    if abs(n[0]) > MAX_SIDE:
        raise ValueError("el plano del cuello está girado de costado: tiene que contener la dirección "
                         "izquierda–derecha (2 puntos a cada lado de la nuca, 1 bajo el mentón)")
    n = np.array([0.0, n[1], n[2]])
    n /= np.linalg.norm(n)
    o = np.asarray(spec.plane.origin, float).copy()
    o[0] = 0.0
    return Plane.from_normal(o, n, spec.name)


def _plane_faces(mf: S.Manifold, plane: Plane, tol: float = 1e-3) -> np.ndarray:
    """Triángulos (n,3,3) de `mf` que yacen sobre `plane` (los 3 vértices a menos de `tol`)."""
    tm = S.to_trimesh(mf)
    tri = tm.triangles
    d = np.abs((tri.reshape(-1, 3) - plane.origin) @ plane.normal).reshape(-1, 3)
    return tri[(d < tol).all(axis=1)]


def split_head(body: S.Manifold, spec: HeadSpec, p: Params) -> HeadSplit:
    """Corta el cuerpo entero por el plano del cuello. El cuerpo se baja `clearance` mm (holgura)."""
    plane = neck_plane(spec)
    head = S.largest_component(S.intersection(body, S.halfspace(plane)))
    if head.is_empty() or head.volume() < 1.0:
        raise ValueError("el plano del cuello no corta ninguna cabeza (¿está por encima de la figura?)")
    below = Plane.from_normal(plane.origin - plane.normal * p.clearance, -plane.normal)
    body_half = S.intersection(body, S.halfspace(below)).trim_by_plane((1.0, 0.0, 0.0), 0.0)
    if body_half.is_empty():
        raise ValueError("el plano del cuello deja la figura sin cuerpo (¿está por debajo de los pies?)")
    tri = _plane_faces(head, plane)
    if len(tri) == 0:
        raise ValueError("el plano del cuello no atraviesa el cuello (la cara del corte quedó vacía)")
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area_f = 0.5 * np.linalg.norm(cross, axis=1)
    centroid = (tri.mean(axis=1) * area_f[:, None]).sum(axis=0) / area_f.sum()
    centroid[0] = 0.0
    face = ContactFace(center=centroid, outward=-plane.normal.copy(), area=float(area_f.sum()), triangles=tri)
    return HeadSplit(head, body_half, plane, face)


def propose_pin_center(face: ContactFace) -> np.ndarray:
    """Centro del mayor círculo inscrito en la cara del corte, sobre x = 0."""
    from shapely.ops import polylabel

    plane, shape = _face_shape(face)
    geoms = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
    big = max(geoms, key=lambda g: g.area)
    pole = polylabel(big, tolerance=0.01)
    c = plane.to_world(np.array([[pole.x, pole.y]]))[0]
    c[0] = 0.0          # el plano contiene al eje X: seguir en x = 0 no lo saca de la cara
    return c


def build_head(split: HeadSplit, spec: HeadSpec, p: Params, trims=None) -> HeadBuild:
    """Cabeza entera con pasador + cavidad (el pasador dilatado por la holgura, como en los hombros)."""
    from .limbs import pin_margin
    from .trims import apply_trims

    mine = [t for t in (trims or []) if t.target == spec.name]
    whole = split.head
    if mine:       # los recortes se dibujan en el lado de trabajo: se aplican a la mitad y se espeja
        half = apply_trims(whole.trim_by_plane((1.0, 0.0, 0.0), 0.0), mine, spec.name)
        whole = S.union(half, S.mirror_x(half))
    if spec.pin_center is not None:
        c = np.asarray(spec.pin_center, float)
        c = c - ((c - split.plane.origin) @ split.plane.normal) * split.plane.normal   # sobre el plano
        c[0] = 0.0
    elif spec.auto:
        c = propose_pin_center(split.face)
    else:
        c = split.face.center
    face = dataclasses.replace(split.face, center=c)
    pin = mushroom_pin(c, face.outward, p.neck_pin)
    return HeadBuild(head=S.union(whole, pin), pocket=S.dilate(pin, p.clearance), face=face,
                     axis=split.plane.normal.copy(), pin_margin=pin_margin(face, p.neck_pin.shaft_d))


def head_fit(split: HeadSplit, uv, shaft_d: float) -> tuple[bool, float]:
    """(centro dentro de la cara, margen vástago–borde) para un centro (u, v) sobre el plano de la cara."""
    _plane, shape = _face_shape(split.face)
    return pin_fit(shape, uv, shaft_d)
