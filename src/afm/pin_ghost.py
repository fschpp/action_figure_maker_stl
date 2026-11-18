"""Fantasma del pasador / hueco sobre la cara de contacto (modo «Colocar el pasador»).

Sirve para ver y entender, ANTES de generar, si el pasador entra en la cara de corte:

* `evaluate_pin`: dónde cae el vástago respecto de la cara (margen, qué parte sobresale, el mayor vástago que cabe en ese
  punto) y un nivel «ok» / «aviso» / «no» con el mismo criterio que la mira (`pin_aid`): margen < 0 no entra; margen menor
  que la pared mínima entra justo.
* `widest_point`: el punto de la cara donde más ancho cabe un vástago (centro del mayor círculo inscripto).
* `fit_scale`: cuánto hay que achicar el pasador para que entre en la cara.
* `propose_smaller_pin`: qué pasador más chico entraría y dónde (para PREGUNTARLE al usuario antes de cambiarlo: el Ø del
  vástago es parte de la interfaz con el catálogo, así que nunca se achica en silencio).
* `ghost_meshes`: la malla del pasador (vástago + cabeza) y la de su hueco (con la holgura) para dibujarlas translúcidas.

Es geometría pura (numpy / shapely / trimesh): no depende de la GUI, así que se prueba sin ventana.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import trimesh

from .limbs import pin_fit
from .params import PinSpec

LEVEL_OK, LEVEL_WARN, LEVEL_NO = "ok", "aviso", "no"
MIN_SHAFT_D = 0.2          # por debajo de esto no se sugiere nada (la cara no admite ni un vástago finito)
MAX_MOVE_MM = 8.0          # lo más que la propuesta corre el pasador del punto pedido para ganar vástago (si no, achica donde está)


@dataclass
class PinFit:
    """Cómo cae un pasador en un punto (u, v) de la cara de contacto."""

    uv: tuple
    inside: bool                 # el centro cae dentro de la cara
    margin_mm: float             # entre el vástago y el borde de la cara (negativo: sobresale)
    hole_margin_mm: float        # lo mismo para el hueco (vástago + holgura): lo que queda de pared en el torso
    level: str                   # «ok» | «aviso» | «no»
    max_shaft_d: float           # mayor Ø de vástago que cabe centrado en este punto (0 si el centro está fuera)
    overflow: object = None      # geometría shapely: la parte del vástago que sobresale de la cara (None si entra)
    face_area: float = 0.0
    shaft_d: float = 0.0

    @property
    def fits(self) -> bool:
        return self.level != LEVEL_NO

    def text(self, min_wall: float) -> str:
        """Frase corta para la barra de estado."""
        if not self.inside:
            return (f"el centro cae fuera de la cara · en el punto más ancho cabe Ø {self.max_shaft_d:.1f} mm"
                    if self.max_shaft_d > 0 else "el centro cae fuera de la cara")
        if self.margin_mm < 0:
            return (f"no entra: el vástago de Ø{self.shaft_d:g} sobresale {-self.margin_mm:.1f} mm de la cara "
                    f"(acá cabe Ø {self.max_shaft_d:.1f} mm como máximo)")
        if self.margin_mm < min_wall:
            return f"entra justo: queda {self.margin_mm:.1f} mm de borde (mínimo {min_wall:g} mm)"
        return f"entra: {self.margin_mm:.1f} mm de borde"


def evaluate_pin(shape, uv, pin: PinSpec, clearance: float, min_wall: float, face_area: float | None = None) -> PinFit:
    """Evalúa un pasador de `pin` centrado en `uv` sobre la huella `shape` (shapely, coordenadas (u, v) de la cara)."""
    from shapely.geometry import Point

    uv = (float(uv[0]), float(uv[1]))
    inside, margin = pin_fit(shape, uv, pin.shaft_d)
    _in_h, hole_margin = pin_fit(shape, uv, pin.shaft_d + 2.0 * clearance)
    pt = Point(uv)
    dist = float(shape.boundary.distance(pt))
    max_d = 2.0 * dist if inside else 0.0
    if not inside or margin < 0:
        level = LEVEL_NO
    elif margin < min_wall:
        level = LEVEL_WARN
    else:
        level = LEVEL_OK
    overflow = None
    if level == LEVEL_NO:
        disc = pt.buffer(pin.shaft_d / 2.0, 32)
        over = disc.difference(shape)
        overflow = None if over.is_empty else over
    area = float(shape.area) if face_area is None else float(face_area)
    return PinFit(uv, bool(inside), float(margin), float(hole_margin), level, max_d, overflow, area, float(pin.shaft_d))


def widest_point(shape) -> tuple[np.ndarray, float]:
    """(uv, Ø) del mayor círculo que cabe dentro de la huella: el mejor lugar para un vástago grueso."""
    from shapely.ops import polylabel

    geoms = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
    geoms = [g for g in geoms if g.geom_type == "Polygon" and not g.is_empty]
    if not geoms:
        return np.zeros(2), 0.0
    best_uv, best_d = None, -1.0
    for g in geoms:
        c = polylabel(g, tolerance=0.02)
        d = 2.0 * float(g.boundary.distance(c))
        if d > best_d:
            best_uv, best_d = np.array([c.x, c.y], float), d
    return best_uv, best_d


def fit_scale(max_shaft_d: float, pin: PinSpec, min_wall: float = 0.0) -> float:
    """Factor (≤ 1) con que hay que escalar el pasador para que su vástago deje `min_wall` de borde en un círculo de Ø `max_shaft_d`.
    1.0 si ya entra. 0.0 si no hay lugar."""
    room = float(max_shaft_d) - 2.0 * float(min_wall)
    if room < MIN_SHAFT_D:
        return 0.0
    return min(1.0, room / float(pin.shaft_d))


def shrunk_dims(pin: PinSpec, room: float, min_wall: float) -> tuple[float, float] | None:
    """(Ø vástago, Ø cabeza) del pasador achicado en proporción para que entre en un círculo de Ø `room` con `min_wall` de borde.

    El vástago se redondea hacia abajo al 0,1 mm (que sobre borde, no que falte). El pasador intacto si ya entra; None si no hay lugar.
    """
    k = fit_scale(room, pin, min_wall)
    if k <= 0.0:
        return None
    if k >= 1.0:
        return float(pin.shaft_d), float(pin.head_d)
    shaft = math.floor(pin.shaft_d * k * 10.0 + 1e-9) / 10.0
    if shaft <= 0.0:
        return None
    return shaft, round(pin.head_d * shaft / pin.shaft_d, 1)


@dataclass
class PinProposal:
    """Un pasador más chico que SÍ entra y dónde ponerlo. Es solo una propuesta: no cambia nada hasta que el usuario acepta."""

    uv: tuple                    # centro propuesto en (u, v) de la cara (el mismo punto pedido o el más ancho de su trozo de cara)
    shaft_d: float
    head_d: float
    room: float                  # mayor Ø de vástago que cabe en `uv`
    moved_mm: float              # cuánto se corre del punto pedido (0 si se achica donde está)
    old_shaft_d: float
    old_head_d: float


@dataclass
class PinFixResult:
    """Resultado de `propose_smaller_pin`: la propuesta o, si no hay, por qué (`reason`) y cuánto cabe (`room`, 0 si no se sabe).

    `reason`: «» (hay propuesta) · «no_toca» (el vástago no toca ninguna cara: achicar no arregla nada) · «sin_lugar» (ni el
    vástago mínimo con borde) · «bajo_minimo» (entraría, pero con un vástago menor que el mínimo que admite la herramienta).
    """

    proposal: PinProposal | None
    reason: str = ""
    room: float = 0.0


def _pieces(shape) -> list:
    geoms = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
    return [g for g in geoms if g.geom_type == "Polygon" and not g.is_empty]


def propose_smaller_pin(shape, uv, pin: PinSpec, min_wall: float, lo_shaft: float = 0.0, lo_head: float = 0.0,
                        max_move: float = MAX_MOVE_MM) -> PinFixResult:
    """Qué pasador más chico entraría cerca de `uv` (la cara puede tener varios trozos: se trabaja SOLO en el más cercano).

    * Si el trozo más cercano no toca el vástago en `uv`, no hay propuesta (`no_toca`): achicar no inventa cara donde no la hay.
    * Nunca salta a otro trozo: si la cara tiene uno en el hombro y otro en la mano, un clic en el hombro solo se resuelve ahí
      (el «punto más ancho» de TODA la cara podría estar en la mano).
    * Si el punto pedido está dentro del trozo se achica ahí; si moviéndolo hasta el punto más ancho del trozo (a lo sumo
      `max_move` mm) queda un vástago más grueso, se propone ese. Un pasador más grueso es más compatible con el catálogo.
    * `lo_shaft` / `lo_head` son los mínimos de la herramienta (rango de los parámetros): por debajo no se propone (`bajo_minimo`).
    """
    from shapely.geometry import Point
    from shapely.ops import polylabel

    pieces = _pieces(shape)
    if not pieces:
        return PinFixResult(None, "sin_lugar")
    pt = Point(float(uv[0]), float(uv[1]))
    piece = min(pieces, key=lambda g: g.distance(pt))
    if piece.distance(pt) > pin.shaft_d / 2.0:
        return PinFixResult(None, "no_toca")
    inside = bool(piece.contains(pt))
    room_here = 2.0 * float(piece.boundary.distance(pt)) if inside else 0.0
    wc = polylabel(piece, tolerance=0.02)
    room_best = 2.0 * float(piece.boundary.distance(wc))
    best_uv = (float(wc.x), float(wc.y))
    moved = math.hypot(best_uv[0] - pt.x, best_uv[1] - pt.y)
    here = shrunk_dims(pin, room_here, min_wall) if room_here > 0.0 else None
    there = shrunk_dims(pin, room_best, min_wall)
    if there is not None and (here is None or (there[0] > here[0] and moved <= max_move)):
        (shaft, head), room, where, dist = there, room_best, best_uv, moved
    elif here is not None:
        (shaft, head), room, where, dist = here, room_here, (pt.x, pt.y), 0.0
    else:
        return PinFixResult(None, "sin_lugar", room_best)
    if shaft < lo_shaft - 1e-9:
        return PinFixResult(None, "bajo_minimo", room)
    return PinFixResult(PinProposal(where, shaft, max(head, lo_head), room, dist, float(pin.shaft_d), float(pin.head_d)), "", room)


def _along(direction, z_from: float, z_to: float, radius: float, origin, sections: int = 40) -> trimesh.Trimesh:
    cyl = trimesh.creation.cylinder(radius=radius, height=max(z_to - z_from, 1e-3), sections=sections)
    cyl.apply_translation((0.0, 0.0, (z_from + z_to) / 2.0))
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    rot = trimesh.geometry.align_vectors([0.0, 0.0, 1.0], d)
    cyl.apply_transform(rot)
    cyl.apply_translation(np.asarray(origin, float))
    return cyl


def ghost_meshes(origin, outward, pin: PinSpec, clearance: float) -> dict[str, trimesh.Trimesh]:
    """Mallas fantasma del pasador sobre la cara: `pasador` (vástago + cabeza, avanza hacia `outward` como `mushroom_pin`)
    y `hueco` (lo mismo dilatado por la holgura: lo que se vacía del torso)."""
    c = float(clearance)
    pasador = trimesh.util.concatenate([
        _along(outward, 0.0, pin.shaft_len, pin.shaft_d / 2.0, origin),
        _along(outward, pin.shaft_len, pin.shaft_len + pin.head_h, pin.head_d / 2.0, origin),
    ])
    hueco = trimesh.util.concatenate([
        _along(outward, -c, pin.shaft_len + c, pin.shaft_d / 2.0 + c, origin),
        _along(outward, pin.shaft_len - c, pin.shaft_len + pin.head_h + c, pin.head_d / 2.0 + c, origin),
    ])
    return {"pasador": pasador, "hueco": hueco}
