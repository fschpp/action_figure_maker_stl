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
from .params import Params, PinSpec
from .pins import mushroom_pin
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

    def twisted(self, deg: float) -> "WristFrame":
        """Mismo marco con la referencia girada `deg` grados alrededor del eje (+ = antihorario mirando desde la mano)."""
        if not deg:
            return WristFrame(self.center.copy(), self.axis.copy(), self.ref.copy())
        a = np.radians(float(deg))
        ref = self.ref * np.cos(a) + np.cross(self.axis, self.ref) * np.sin(a)      # Rodrigues (ref ⟂ axis)
        return WristFrame(self.center.copy(), self.axis.copy(), ref / np.linalg.norm(ref))

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


def _face_triangles(mf: S.Manifold, frame: WristFrame) -> np.ndarray:
    tri = S.to_trimesh(mf).triangles
    d = np.abs((tri.reshape(-1, 3) - frame.center) @ frame.axis).reshape(-1, 3)
    return tri[(d < FACE_TOL).all(axis=1)]


def _dist_to_face(tri: np.ndarray, p: np.ndarray) -> float:
    """Distancia de `p` (sobre el plano) a los triángulos de la cara del corte: 0 si cae dentro de alguno."""
    if len(tri) == 0:
        return float("inf")
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    v0, v1, v2 = c - a, b - a, p - a
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    ok = np.abs(den) > 1e-18
    safe = np.where(ok, den, 1.0)
    u = np.where(ok, (d11 * d20 - d01 * d21) / safe, -1.0)
    v = np.where(ok, (d00 * d21 - d01 * d20) / safe, -1.0)
    if (ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9)).any():
        return 0.0
    return float(np.linalg.norm(tri.mean(axis=1) - p, axis=1).min())


def _component_at_wrist(half: S.Manifold, frame: WristFrame):
    """(pieza conectada de `half` cuya cara del corte contiene —o queda más cerca de— el centro de los 3 puntos, el resto de
    los componentes de `half`); (None, []) si ninguno toca el plano.

    El plano es infinito: corta TODA la figura (con los brazos colgando, también piernas y pelvis). Lo que importa de
    cada lado es solo la pieza que toca la cara del corte en la muñeca."""
    best, parts = None, half.decompose()
    for i, part in enumerate(parts):
        dist = _dist_to_face(_face_triangles(part, frame), frame.center)
        if np.isfinite(dist) and (best is None or dist < best[0]):
            best = (dist, i)
    if best is None:
        return None, []
    return parts[best[1]], [q for j, q in enumerate(parts) if j != best[1]]


DEGENERATE_MM3 = 1e-6          # un componente con menos volumen que esto es una lámina sin espesor, no material


def _drop_degenerate(mf: S.Manifold) -> S.Manifold:
    """Quita los componentes sin volumen: restar la mano del cuerpo deja, donde las caras coinciden (la teselación del
    cilindro del brazo), láminas de espesor cero pegadas al corte que no son material y tocarían la mano."""
    parts = mf.decompose()
    keep = [c for c in parts if c.volume() > DEGENERATE_MM3]
    return mf if len(keep) == len(parts) else S.union(*keep)


def split_at_wrist(body: S.Manifold, frame: WristFrame, invert: bool = False) -> WristSplit:
    """Corta `body` por el plano de muñeca y separa la mano del resto.

    De cada lado del plano se toma solo la pieza conectada que toca la cara del corte en la muñeca (el plano es
    infinito y también corta lo que no es del brazo). La mano es la de menor volumen (`invert=True`: la de mayor).
    La mano y el resto quedan cerrados (la cara del corte es plana). Devuelve el marco con `axis` apuntando a la mano
    y `center` en el centroide de la cara.

    Limitación: si la mano está fundida con otra parte del cuerpo (p. ej. apoyada sobre el muslo y soldada), esa parte
    viaja con ella: el aviso de «casi por la mitad» o el tamaño de la mano lo delatan."""
    pos = S.intersection(body, S.halfspace(Plane.from_normal(frame.center, frame.axis)))
    neg = S.intersection(body, S.halfspace(Plane.from_normal(frame.center, -frame.axis)))
    cp, others_p = (None, []) if pos.is_empty() else _component_at_wrist(pos, frame)
    cn, others_n = (None, []) if neg.is_empty() else _component_at_wrist(neg, frame)
    if cp is None or cn is None or min(cp.volume(), cn.volume()) < MIN_HAND_VOLUME:
        raise HandError("el plano de muñeca no atraviesa la figura (¿quedó fuera de la malla?)")
    vp, vn = float(cp.volume()), float(cn.volume())
    vs, vb = min(vp, vn), max(vp, vn)
    if not invert and vs / vb > MAX_SIDE_RATIO:
        raise HandError(f"el plano de muñeca parte la pieza casi por la mitad ({vs / vb:.0%}): no separa una mano; "
                        "elegí 3 puntos en la muñeca (o usá «invertir» si la mano es el lado grande)")
    hand_is_pos = (vp <= vn) != invert
    hand = cp if hand_is_pos else cn
    oriented = frame.with_axis_sign(1.0 if hand_is_pos else -1.0)
    face = _cut_face(hand, oriented)
    if face is None:
        raise HandError("el plano de muñeca no deja una cara de corte en la mano (¿pasa por una cavidad?)")
    centroid, area = face
    # El resto = la mitad completa del lado del brazo + lo que quedó del lado de la mano sin ser la mano (piernas, etc.).
    # No se resta la mano del cuerpo: donde la pared del brazo y la de la mano coinciden, la resta deja una «falda» de
    # espesor cero que después se cuela en las piezas (y con la mano articulada tocaría la mano).
    arm_side, others = (neg, others_p) if hand_is_pos else (pos, others_n)
    rest = _drop_degenerate(S.union(arm_side, *others) if others else arm_side)
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


# ------------------------------------------------------------------ pegar la mano estándar en otro modelo
HAND_NAME = "mano"             # pieza de la mano articulada (`mano_izq` / `mano_der`)
JOINS = ("solape", "giro")     # solape: la mano queda soldada al brazo; giro: pieza aparte con pasador de hongo, gira libre
DEFAULT_OVERLAP_MM = 0.5       # cuánto se hunde la base de la mano en el brazo: la unión tiene volumen, no solo una cara
STEP_WARN_MM = 1.0             # diferencia de radio de muñeca (modelo vs mano estándar) a partir de la cual se avisa del escalón
PIN_SHRINK_STEP = 0.05         # el pasador de la mano se achica de a 5 % hasta que entra en el brazo
PIN_SHRINK_FLOOR = 0.4         # ... y no por debajo de 40 % del pedido
HAND_SKIN_TOL_MM3 = 0.2        # lo que sobresale la cavidad engrosada `min_wall` fuera de la piel del brazo (teselación) sin ser pared fina
HAND_MIN_SHAFT_D = 2.0         # mm: por debajo, el vástago del pasador de la mano no se imprime con una boquilla de 0,4–0,8
SIZE_WARN_RATIO = 3.0          # la mano estándar es tantas veces más grande / chica (en volumen) que la original del modelo


@dataclass
class HandSpec:
    """Mano de reemplazo de un modelo: STL de la mano estándar + plano de muñeca de 3 puntos sobre el modelo.

    `points` están en el marco de trabajo de la figura (el mismo en que se eligen en la GUI y en que `afm extraer-mano`
    recibe los suyos) y pueden estar en cualquiera de los dos brazos: se espejan al lado +X (el otro lado sale por
    espejo, como el resto de las piezas). El punto 1 es la referencia de giro: marcarlo en el mismo lugar anatómico que
    al extraer la mano deja la mano orientada igual sin tocar `yaw`. La mano no se reescala (mm reales)."""

    path: str
    points: tuple
    yaw: float = 0.0                 # giro extra (°) alrededor del eje de la muñeca
    invert: bool = False             # la mano del modelo es el lado grande del corte
    join: str = "solape"
    overlap: float = DEFAULT_OVERLAP_MM

    def to_dict(self) -> dict:
        return {"path": self.path, "points": [list(map(float, q)) for q in self.points], "yaw": float(self.yaw),
                "invert": bool(self.invert), "join": self.join, "overlap": float(self.overlap)}

    @classmethod
    def from_dict(cls, d: dict) -> "HandSpec":
        return cls(path=d["path"], points=tuple(tuple(map(float, q)) for q in d["points"]),
                   yaw=float(d.get("yaw", 0.0)), invert=bool(d.get("invert", False)),
                   join=d.get("join", "solape"), overlap=float(d.get("overlap", DEFAULT_OVERLAP_MM)))


@dataclass
class HandBuild:
    body: S.Manifold            # el cuerpo (lado +X) sin la mano original; con «solape», con la estándar ya soldada
    hand: S.Manifold            # la mano estándar ya puesta en el modelo (mundo): con solape o, con «giro», con su pasador
    original: S.Manifold        # la mano original del modelo, la que se descarta (mundo)
    frame: WristFrame           # marco de muñeca en el modelo (eje hacia la mano, con el giro aplicado)
    info: dict
    pocket: S.Manifold | None = None   # solo «giro»: cavidad (pasador dilatado por la holgura) que se resta del brazo
    pin: PinSpec | None = None         # solo «giro»: el pasador usado (ya ajustado a la muñeca)
    pin_origin: np.ndarray | None = None   # solo «giro»: donde arranca el pasador (cara de la mano) y se orienta hacia el brazo (−eje)


def _local_face(hand_local: S.Manifold):
    """(área, distancia del centroide al origen) de la cara plana de la mano estándar (z = 0); None si no tiene."""
    tri = S.to_trimesh(hand_local).triangles
    on = tri[(np.abs(tri[:, :, 2]) < FACE_TOL).all(axis=1)]
    if len(on) == 0:
        return None
    area = 0.5 * np.linalg.norm(np.cross(on[:, 1] - on[:, 0], on[:, 2] - on[:, 0]), axis=1)
    if area.sum() <= 0:
        return None
    c = (on.mean(axis=1) * area[:, None]).sum(axis=0) / area.sum()
    return float(area.sum()), float(np.linalg.norm(c))


def fit_hand_pin(rest: S.Manifold, frame: WristFrame, p: Params, base: PinSpec | None = None, cache: dict | None = None):
    """Pasador de hongo de la mano, achicado (diámetros, misma relación) hasta que entra en el brazo.

    El pasador arranca en la cara de la mano (a `clearance` del corte) y entra en el brazo hacia −eje. «Entra» significa que
    la cavidad (pasador dilatado por `clearance`) más `min_wall` de pared queda dentro de la piel de `rest`: se mide contra la
    malla real, así aprovecha que el antebrazo se ensancha hacia el codo (vástago fino en la muñeca, cabeza más adentro).
    El tamaño que entra solo crece al achicar, así que se busca por bisección sobre una grilla de `PIN_SHRINK_STEP`.
    `cache` (dict, opcional) guarda el resultado mientras no cambien el plano de muñeca, el brazo ni las medidas: el giro de la
    mano no lo cambia. Devuelve (PinSpec, escala, entra)."""
    base = base or p.hand_pin
    key = None
    if cache is not None:
        key = (tuple(np.round(frame.center, 5)), tuple(np.round(frame.axis, 5)), float(p.clearance), float(p.min_wall),
               tuple(base.__dict__.values()), round(float(rest.volume()), 3), int(rest.num_tri()))
        if key in cache:
            return cache[key]
    arm_side = S.halfspace(Plane.from_normal(frame.center, -frame.axis))
    origin = frame.center + frame.axis * p.clearance
    n = int(round((1.0 - PIN_SHRINK_FLOOR) / PIN_SHRINK_STEP))
    ks = [round(1.0 - i * PIN_SHRINK_STEP, 6) for i in range(n + 1)]          # de 1,00 a PIN_SHRINK_FLOOR
    seen: dict[int, tuple] = {}

    def fits(i: int) -> bool:
        if i not in seen:
            spec = PinSpec(base.shaft_d * ks[i], base.shaft_len, base.head_d * ks[i], base.head_h)
            zone = S.dilate(mushroom_pin(origin, -frame.axis, spec), p.clearance + p.min_wall)
            leak = float(S.difference(S.intersection(zone, arm_side), rest).volume())
            seen[i] = (spec, ks[i], leak <= HAND_SKIN_TOL_MM3)
        return seen[i][2]

    if fits(0):
        best = 0
    elif not fits(n):
        best = n                                              # ni el más chico entra: se devuelve ese y `entra = False`
    else:
        lo, hi = 0, n                                         # lo no entra, hi entra
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (lo, mid) if fits(mid) else (mid, hi)
        best = hi
    result = seen[best]
    if cache is not None:
        cache[key] = result
    return result


def apply_hand(body: S.Manifold, spec: HandSpec, ref_height: float | None = None,
               params: Params | None = None, pin_cache: dict | None = None) -> HandBuild:
    """Cambia la mano de `body` (la mitad de trabajo, lado +X) por la mano estándar de `spec`.

    1. Corta por el plano de muñeca de `spec.points` y descarta la mano original (`split_at_wrist`).
    2. Pone la mano estándar con su cara plana sobre la del corte (centroide con centroide, sin escalar).
       * `join = "solape"`: la hunde `spec.overlap` mm en el brazo, así la unión es un volumen y la mano queda soldada.
       * `join = "giro"`: la deja a `clearance` del corte como PIEZA APARTE con un pasador de hongo que entra en el brazo
         (`fit_hand_pin`); el brazo recibe la cavidad (`HandBuild.pocket`, la resta el pipeline) y la mano gira libre.
    El informe trae los radios de muñeca (el escalón que queda si no coinciden) y avisos."""
    if spec.join not in JOINS:
        raise HandError(f"unión «{spec.join}» desconocida (las que hay: {', '.join(JOINS)})")
    pts = np.asarray(spec.points, float)
    if pts.shape != (3, 3):
        raise HandError("el plano de muñeca necesita 3 puntos (x, y, z)")
    mirrored = bool(pts[:, 0].mean() < 0)
    if mirrored:
        pts = pts * np.array([-1.0, 1.0, 1.0])               # la mano se pega en +X; el otro brazo sale por espejo
    hand_local, meta = load_standard_hand(spec.path)
    face = _local_face(hand_local)
    if face is None:
        raise HandError("la mano estándar no tiene una cara plana de unión en z = 0: usá un STL guardado con "
                        "«Guardar mano estándar…» o `afm extraer-mano`")
    face_area, face_off = face
    if face_off > 0.5:
        raise HandError(f"la cara de unión de la mano estándar no está centrada en el origen (a {face_off:.2f} mm): "
                        "no es una mano estándar de esta herramienta")
    split = split_at_wrist(body, WristFrame.from_points(pts), invert=spec.invert)
    frame = split.frame.twisted(spec.yaw)
    placed = place_hand(hand_local, frame)
    swivel = spec.join == "giro"
    p = params or Params()
    overlap = 0.0 if swivel else max(0.0, float(spec.overlap))
    if overlap:
        placed = placed.translate(tuple(float(x) for x in -frame.axis * overlap))
    pocket = pin = pin_origin = None
    pin_info: dict = {}
    pin_avisos: list[str] = []
    if swivel:
        gap = float(p.clearance)
        placed = placed.translate(tuple(float(x) for x in frame.axis * gap))          # la cara de la mano a `gap` del corte
        pin, k, fits = fit_hand_pin(split.rest, frame, p, cache=pin_cache)
        pin_origin = frame.center + frame.axis * gap
        pin_mf = mushroom_pin(pin_origin, -frame.axis, pin)
        placed = S.union(placed, pin_mf)
        pocket = S.dilate(pin_mf, p.clearance)
        new_body = split.rest                                    # la cavidad la resta el pipeline en la pieza que lleva la mano
        lip = pin.shaft_len - 2 * p.clearance
        pin_info = {"vastago_mm": round(pin.shaft_d, 3), "cabeza_mm": round(pin.head_d, 3), "largo_mm": pin.shaft_len,
                    "escala": round(k, 3), "entra": bool(fits), "labio_tras_cabeza_mm": round(lip, 3), "holgura_mm": gap}
        if not fits:
            pin_avisos.append(f"el pasador de la mano no entra en el brazo con {p.min_wall:g} mm de pared ni achicado a ×{k:.2f} "
                              "(vástago Ø%.1f, cabeza Ø%.1f): la muñeca es muy fina; bajá la holgura o la pared mínima, o usá la unión «solape»"
                              % (pin.shaft_d, pin.head_d))
        elif k < 1.0 - 1e-9:
            pin_avisos.append(f"el pasador de la mano se achicó a ×{k:.2f} (vástago Ø{pin.shaft_d:.1f}, cabeza Ø{pin.head_d:.1f}) "
                              "para que entre en el brazo")
        if pin.shaft_d < HAND_MIN_SHAFT_D:
            pin_avisos.append(f"el vástago del pasador de la mano (Ø{pin.shaft_d:.1f} mm) es más fino que {HAND_MIN_SHAFT_D:g} mm: no se imprime")
        elif pin.shaft_d < p.min_pin_d:
            pin_avisos.append(f"el vástago del pasador de la mano (Ø{pin.shaft_d:.1f} mm) es más fino que el mínimo de pasador "
                              f"({p.min_pin_d:g} mm): imprimí una probeta antes de confiar en él")
        if lip < p.min_wall - 1e-9:
            pin_avisos.append(f"el labio del brazo tras la cabeza del pasador mide {lip:.2f} mm (< {p.min_wall:g} mm): subí el largo del vástago")
    else:
        new_body = S.union(split.rest, placed)
    r_arm, r_hand = split.face_radius, float(np.sqrt(face_area / np.pi))
    avisos: list[str] = []
    if abs(r_arm - r_hand) > STEP_WARN_MM:
        avisos.append(f"la muñeca del modelo (Ø{2 * r_arm:.1f} mm) y la de la mano estándar (Ø{2 * r_hand:.1f} mm) difieren "
                      f"{abs(r_arm - r_hand):.1f} mm de radio: queda un escalón en la unión")
    ratio = float(hand_local.volume()) / max(split.hand_volume, 1e-9)
    if ratio > SIZE_WARN_RATIO or ratio < 1.0 / SIZE_WARN_RATIO:
        avisos.append(f"la mano estándar tiene {ratio:.1f} veces el volumen de la mano original del modelo "
                      "(¿el plano de muñeca está bien puesto?)")
    src_h = None if meta is None else meta.get("altura_figura_mm")
    if src_h and ref_height and abs(float(src_h) - float(ref_height)) > 0.5:
        avisos.append(f"la mano estándar se midió en una figura de {float(src_h):g} mm y esta mide {float(ref_height):g} mm: "
                      "la mano no se reescala, puede verse grande o chica")
    if swivel:      # con giro libre el escalón es solo estético (no hay soldadura a la muñeca): se avisa igual pero más suave
        avisos = [a.replace("queda un escalón en la unión", "queda un escalón visible entre mano y brazo") for a in avisos]
    avisos += pin_avisos
    info = {"archivo": spec.path, "puntos_espejados": mirrored, "radio_brazo_mm": round(r_arm, 3),
            "radio_mano_mm": round(r_hand, 3), "escalon_mm": round(abs(r_arm - r_hand), 3), "solape_mm": overlap,
            "giro_grados": float(spec.yaw), "union": spec.join,
            "volumen_mano_mm3": round(float(placed.volume()), 2), "volumen_original_mm3": round(split.hand_volume, 2),
            "centro": frame.center.tolist(), "eje": frame.axis.tolist(), "avisos": avisos}
    if swivel:
        info["pasador"] = pin_info
    return HandBuild(body=new_body, hand=placed, original=split.hand, frame=frame, info=info, pocket=pocket, pin=pin,
                     pin_origin=pin_origin)


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
