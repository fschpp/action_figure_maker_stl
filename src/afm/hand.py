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
    """Pieza conectada de `half` cuya cara del corte contiene (o queda más cerca de) el centro de los 3 puntos.

    El plano es infinito: corta TODA la figura (con los brazos colgando, también piernas y pelvis). Lo que importa de
    cada lado es solo la pieza que toca la cara del corte en la muñeca."""
    best = None
    for part in half.decompose():
        dist = _dist_to_face(_face_triangles(part, frame), frame.center)
        if np.isfinite(dist) and (best is None or dist < best[0]):
            best = (dist, part)
    return None if best is None else best[1]


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
    cp = None if pos.is_empty() else _component_at_wrist(pos, frame)
    cn = None if neg.is_empty() else _component_at_wrist(neg, frame)
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
    rest = S.difference(body, hand)
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
JOINS = ("solape",)            # «giro» (articulación print-in-place sobre el mismo plano) queda para más adelante
DEFAULT_OVERLAP_MM = 0.5       # cuánto se hunde la base de la mano en el brazo: la unión tiene volumen, no solo una cara
STEP_WARN_MM = 1.0             # diferencia de radio de muñeca (modelo vs mano estándar) a partir de la cual se avisa del escalón
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
    body: S.Manifold            # el cuerpo (lado +X) con la mano original cambiada por la estándar
    hand: S.Manifold            # la mano estándar ya puesta en el modelo (mundo), con el solape
    original: S.Manifold        # la mano original del modelo, la que se descarta (mundo)
    frame: WristFrame           # marco de muñeca en el modelo (eje hacia la mano, con el giro aplicado)
    info: dict


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


def apply_hand(body: S.Manifold, spec: HandSpec, ref_height: float | None = None) -> HandBuild:
    """Cambia la mano de `body` (la mitad de trabajo, lado +X) por la mano estándar de `spec`.

    1. Corta por el plano de muñeca de `spec.points` y descarta la mano original (`split_at_wrist`).
    2. Pone la mano estándar con su cara plana sobre la del corte (centroide con centroide, sin escalar) y la hunde
       `spec.overlap` mm en el brazo, así la unión es un volumen y no un simple contacto de caras.
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
    overlap = max(0.0, float(spec.overlap))
    if overlap:
        placed = placed.translate(tuple(float(x) for x in -frame.axis * overlap))
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
    info = {"archivo": spec.path, "puntos_espejados": mirrored, "radio_brazo_mm": round(r_arm, 3),
            "radio_mano_mm": round(r_hand, 3), "escalon_mm": round(abs(r_arm - r_hand), 3), "solape_mm": overlap,
            "giro_grados": float(spec.yaw), "union": spec.join,
            "volumen_mano_mm3": round(float(placed.volume()), 2), "volumen_original_mm3": round(split.hand_volume, 2),
            "centro": frame.center.tolist(), "eje": frame.axis.tolist(), "avisos": avisos}
    return HandBuild(body=new_body, hand=placed, original=split.hand, frame=frame, info=info)


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
