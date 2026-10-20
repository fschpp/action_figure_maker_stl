"""Mano de reemplazo: plano de muñeca por 3 puntos, corte y mano estándar compartida por una línea de figuras.

Idea: una línea de figuras (p. ej. Güemes, Belgrano…) comparte UNA mano estándar (un STL). Esa mano se extrae
una sola vez de una figura (`extract_standard_hand`) cortándola por un plano de muñeca de 3 puntos; después se
pega en otros modelos (`place_hand`) sobre su propio plano de muñeca. Todo el trabajo se hace en mm reales: la
mano NO se reescala para igualar la muñeca de otro modelo (así todas las manos de la línea son idénticas y
comparten accesorios); lo que no coincida lo resuelve la unión (primero una espiga cilíndrica, luego, más
adelante, una articulación de giro print-in-place sobre el mismo plano).

Convenciones del plano de muñeca (`WristFrame`):

* `center`: centroide de la cara del corte (en la mano estándar y en el brazo donde se pega, así que al pegar
  se alinean centroide con centroide, como en la cabeza de reemplazo).
* `axis`: normal unitaria del plano, apuntando HACIA LA MANO.
* `ref`: dirección en el plano que va del centro de los 3 puntos al PUNTO 1. Es la referencia de giro: si el
  punto 1 se marca siempre en el mismo lugar anatómico (p. ej. el lado del pulgar), la mano queda orientada
  igual en todos los modelos sin ajustar grados a mano.
* Marco local de la mano estándar: origen en `center`, +Z = `axis`, +X = `ref`, +Y = Z × X. La mano estándar
  se guarda en ese marco, con la cara plana del corte sobre z = 0 y los dedos hacia +Z.

Qué lado del plano es la mano: por defecto el de MENOR volumen (en una figura entera la mano es una fracción
mínima; en un brazo suelto, también la parte más chica). `invert=True` toma el otro lado.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import numpy as np

from . import solids as S
from .planes import Plane

log = logging.getLogger(__name__)

HAND_FORMAT = "afm-mano-1"
LINE_HEIGHT_MM = 130.0         # altura de figura de la línea por defecto (Güemes a 13 cm): la mano estándar se mide a esta escala
MIN_HAND_VOLUME = 1.0          # mm³: por debajo, el plano no corta ninguna mano
MAX_SIDE_RATIO = 0.5           # volumen menor / volumen mayor: por encima, el plano no separa «una mano» del resto
FACE_TOL = 1e-3                # mm: distancia al plano para considerar un triángulo parte de la cara del corte


class HandError(ValueError):
    """El plano de muñeca no sirve (puntos alineados, plano fuera de la figura, no separa una mano…)."""


@dataclass
class WristFrame:
    """Plano de muñeca: centro, eje hacia la mano y referencia de giro (ver docstring del módulo)."""

    center: np.ndarray
    axis: np.ndarray
    ref: np.ndarray

    @classmethod
    def from_points(cls, points) -> "WristFrame":
        """Marco a partir de los 3 puntos elegidos. El sentido de `axis` queda sin decidir (lo fija `split_at_wrist`)."""
        pts = np.asarray(points, float)
        if pts.shape != (3, 3):
            raise HandError("el plano de muñeca necesita 3 puntos (x, y, z)")
        p0, p1, p2 = pts
        n = np.cross(p1 - p0, p2 - p0)
        norm = float(np.linalg.norm(n))
        if norm < 1e-9 * max(1.0, float(np.linalg.norm(p1 - p0) * np.linalg.norm(p2 - p0))):
            raise HandError("los 3 puntos de la muñeca están alineados: elegí 3 puntos que formen un triángulo")
        n = n / norm
        center = pts.mean(axis=0)
        r = p0 - center
        r = r - (r @ n) * n
        rn = float(np.linalg.norm(r))
        if rn < 1e-9:
            raise HandError("el punto 1 coincide con el centro de los 3 puntos: no define la referencia de giro")
        return cls(center=center, axis=n, ref=r / rn)

    def with_axis_sign(self, sign: float) -> "WristFrame":
        return WristFrame(self.center.copy(), self.axis * (1.0 if sign >= 0 else -1.0), self.ref.copy())

    def with_center(self, center) -> "WristFrame":
        c = np.asarray(center, float)
        return WristFrame(c.copy(), self.axis.copy(), self.ref.copy())

    @property
    def y(self) -> np.ndarray:
        return np.cross(self.axis, self.ref)

    def matrix(self) -> np.ndarray:
        """4×4 local (marco de la mano estándar) → mundo."""
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = self.ref, self.y, self.axis, self.center
        return m

    def plane(self) -> Plane:
        return Plane.from_normal(self.center, self.axis, "muñeca")

    def to_dict(self) -> dict:
        return {"center": self.center.tolist(), "axis": self.axis.tolist(), "ref": self.ref.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> "WristFrame":
        return cls(np.asarray(d["center"], float), np.asarray(d["axis"], float), np.asarray(d["ref"], float))


@dataclass
class WristSplit:
    hand: S.Manifold             # lado de la mano, con la cara del corte cerrada (coordenadas del mundo)
    rest: S.Manifold             # el resto (brazo + cuerpo), con su cara del corte cerrada
    frame: WristFrame            # eje hacia la mano; centro = centroide de la cara del corte
    face_area: float             # mm² de la cara del corte
    face_radius: float           # radio equivalente (√(área/π)) de la cara del corte, mm
    hand_volume: float
    rest_volume: float


def _cut_face(mf: S.Manifold, frame: WristFrame):
    """(centroide, área) de los triángulos de `mf` que yacen sobre el plano de `frame`; None si no hay."""
    tri = S.to_trimesh(mf).triangles
    d = np.abs((tri.reshape(-1, 3) - frame.center) @ frame.axis).reshape(-1, 3)
    tri = tri[(d < FACE_TOL).all(axis=1)]
    if len(tri) == 0:
        return None
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    if area.sum() <= 0:
        return None
    return (tri.mean(axis=1) * area[:, None]).sum(axis=0) / area.sum(), float(area.sum())


def split_at_wrist(body: S.Manifold, frame: WristFrame, invert: bool = False) -> WristSplit:
    """Corta `body` por el plano de muñeca y separa la mano del resto.

    La mano es el lado de menor volumen (`invert=True`: el de mayor). Ambas mitades quedan cerradas (la cara del
    corte es plana). Devuelve el marco con `axis` apuntando a la mano y `center` en el centroide de la cara."""
    pos = S.intersection(body, S.halfspace(Plane.from_normal(frame.center, frame.axis)))
    neg = S.intersection(body, S.halfspace(Plane.from_normal(frame.center, -frame.axis)))
    vp = 0.0 if pos.is_empty() else float(pos.volume())
    vn = 0.0 if neg.is_empty() else float(neg.volume())
    if min(vp, vn) < MIN_HAND_VOLUME:
        raise HandError("el plano de muñeca no atraviesa la figura (¿quedó fuera de la malla?)")
    small, big = (pos, neg) if vp <= vn else (neg, pos)
    vs, vb = min(vp, vn), max(vp, vn)
    if not invert and vs / vb > MAX_SIDE_RATIO:
        raise HandError(f"el plano de muñeca parte la figura casi por la mitad ({vs / vb:.0%}): no separa una mano; "
                        "elegí 3 puntos en la muñeca (o usá «invertir» si la mano es el lado grande)")
    hand, rest = (big, small) if invert else (small, big)
    sign = 1.0 if (hand is pos) else -1.0
    oriented = frame.with_axis_sign(sign)
    hand = S.drop_small_components(hand, 0.01)
    face = _cut_face(hand, oriented)
    if face is None:
        raise HandError("el plano de muñeca no deja una cara de corte en la mano (¿pasa por una cavidad?)")
    centroid, area = face
    return WristSplit(hand=hand, rest=rest, frame=oriented.with_center(centroid), face_area=area,
                      face_radius=float(np.sqrt(area / np.pi)), hand_volume=float(hand.volume()),
                      rest_volume=float(rest.volume()))


# ------------------------------------------------------------------ mano estándar
def to_local(hand: S.Manifold, frame: WristFrame) -> S.Manifold:
    """Lleva la mano (mundo) al marco local de la mano estándar (cara del corte en z = 0, dedos hacia +Z)."""
    return hand.transform(np.linalg.inv(frame.matrix())[:3, :])


def place_hand(hand_local: S.Manifold, frame: WristFrame) -> S.Manifold:
    """Pone la mano estándar (marco local) sobre `frame`, sin escalar (mm reales). Para el lado opuesto se
    aplica `solids.mirror_x` al resultado, como el resto de las piezas del repo."""
    return hand_local.transform(frame.matrix()[:3, :])


def extract_standard_hand(body: S.Manifold, points, invert: bool = False) -> tuple[S.Manifold, WristSplit]:
    """Corta la mano de `body` por los 3 puntos y la devuelve en el marco local (lista para guardar como estándar)."""
    split = split_at_wrist(body, WristFrame.from_points(points), invert=invert)
    return to_local(split.hand, split.frame), split


def hand_meta(split: WristSplit, height_mm: float | None = None, source: str = "") -> dict:
    side = "izquierda" if split.frame.center[0] > 1e-6 else ("derecha" if split.frame.center[0] < -1e-6 else "centro")
    b = split.hand.bounding_box()
    length = float(S.to_trimesh(to_local(split.hand, split.frame)).bounds[1, 2])
    return {"formato": HAND_FORMAT, "lado_origen": side, "altura_figura_mm": height_mm,
            "radio_muneca_mm": split.face_radius, "area_corte_mm2": split.face_area,
            "volumen_mm3": split.hand_volume, "origen_figura": source,
            "marco": "origen en el centroide del corte, +Z hacia la mano, +X = referencia (punto 1)",
            "largo_mm": length,
            "frame_en_figura": split.frame.to_dict(), "bbox_mundo": [list(map(float, b[:3])), list(map(float, b[3:]))]}


def meta_path(stl_path: str) -> str:
    return os.path.splitext(stl_path)[0] + ".mano.json"


def save_standard_hand(path: str, hand_local: S.Manifold, meta: dict | None = None) -> None:
    """Guarda el STL de la mano estándar y, al lado, `<nombre>.mano.json` con sus medidas."""
    from . import meshio

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    meshio.save_stl(S.to_printable_trimesh(hand_local), path)
    if meta is not None:
        with open(meta_path(path), "w") as f:
            json.dump(meta, f, indent=2, default=float, ensure_ascii=False)


def load_standard_hand(path: str) -> tuple[S.Manifold, dict | None]:
    """Carga la mano estándar (marco local) y su JSON de medidas si existe."""
    import trimesh

    mesh = trimesh.load(path, force="mesh")
    if len(mesh.faces) == 0:
        raise HandError(f"«{path}» no tiene triángulos")
    mesh = mesh.copy()
    mesh.merge_vertices()
    if mesh.volume < 0:
        mesh.invert()
    meta = None
    mp = meta_path(path)
    if os.path.exists(mp):
        with open(mp) as f:
            meta = json.load(f)
        if meta.get("formato") != HAND_FORMAT:
            raise HandError(f"«{mp}» no es una mano estándar de esta versión (formato {meta.get('formato')!r})")
    return S.to_manifold(mesh), meta
