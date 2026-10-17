"""Generador de probetas de articulación (plan §8.11).

Produce un STL de prueba con varias articulaciones (pasador hongo + cavidad con holgura),
cada una con una combinación distinta de holgura y de diámetro de vástago/cabeza, para que
el cliente elija con su impresora los valores de mínima fricción.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from . import solids as S
from .params import Params, PinSpec
from .pins import mushroom_pin

DEFAULT_CLEARANCES = (0.3, 0.4, 0.5, 0.6)
DEFAULT_SHAFT_DELTAS = (-0.2, 0.0, 0.2)


@dataclass
class ProbeCell:
    label: str
    clearance: float
    pin: PinSpec


def probe_cells(p: Params, base: PinSpec | None = None,
                clearances=DEFAULT_CLEARANCES, shaft_deltas=DEFAULT_SHAFT_DELTAS) -> list[ProbeCell]:
    """Todas las combinaciones holgura x diámetro de vástago (la cabeza se mantiene proporcional)."""
    base = base or p.shoulder_pin
    cells = []
    for c in clearances:
        for dd in shaft_deltas:
            pin = replace(base, shaft_d=round(base.shaft_d + dd, 3))
            cells.append(ProbeCell(f"{c:.1f}/{pin.shaft_d:.1f}", c, pin))
    return cells


def _cell(cell: ProbeCell, wall: float = 2.0, pitch: float = 16.0) -> S.Manifold:
    """Una articulación: bloque con cavidad + pasador atrapado (que es parte del 'miembro')."""
    pin = cell.pin
    z0 = 0.0
    block_w = pitch - 2.0
    depth = pin.shaft_len + pin.head_h + 2 * wall
    block = S.Manifold.cube((block_w, block_w, depth), center=False).translate((-block_w / 2, -block_w / 2, 0))
    # miembro de prueba: un disco con su pasador que sale hacia +Z dentro del bloque
    disc = S.cylinder_along((0, 0, depth), (0, 0, 1), pin.head_d / 2 + 1.5, wall, 48, start=0)
    pn = mushroom_pin((0, 0, depth), (0, 0, -1), pin)
    limb = S.union(disc, pn)
    cavity = S.dilate(limb, cell.clearance)
    body = S.difference(block, cavity)
    return S.union(body, limb)


def build_probe(p: Params | None = None, cells: list[ProbeCell] | None = None,
                columns: int = 3, pitch: float = 16.0) -> S.Manifold:
    """Placa de probetas en una grilla. Devuelve el sólido completo (piezas separadas por holgura)."""
    p = p or Params()
    cells = cells or probe_cells(p)
    parts = []
    for i, cell in enumerate(cells):
        r, c = divmod(i, columns)
        parts.append(_cell(cell, pitch=pitch).translate((c * pitch, -r * pitch, 0)))
    return S.union(*parts)


def labels_text(cells: list[ProbeCell]) -> str:
    """Tabla de referencia (holgura/vástago) en el orden de la grilla, para anotar el resultado."""
    return "\n".join(f"#{i + 1}: holgura {c.clearance:.1f} mm, vástago Ø{c.pin.shaft_d:.1f} mm"
                     for i, c in enumerate(cells))


# --------------------------------------------------------------------------------------------------
# Probeta de bisagra (rodilla / codo): plan de articulaciones §4, Fase 6.
# --------------------------------------------------------------------------------------------------
DEFAULT_HINGE_CLEARANCES = (0.3, 0.4, 0.5)
DEFAULT_HINGE_PIN_DELTAS = (-0.2, 0.0, 0.2)
HINGE_PROBE_RANGE = (0.0, 90.0)     # barrido tallado en la ranura de cada celda (grados, alrededor de +X)
HINGE_PROBE_STEP = 15.0


@dataclass
class HingeProbeCell:
    label: str
    clearance: float          # holgura radial
    axial_clearance: float    # holgura a lo largo del eje (lengüeta vs. paredes de la ranura)
    hinge: object             # HingeSpec


def hinge_probe_cells(p: Params, base=None, clearances=DEFAULT_HINGE_CLEARANCES,
                      pin_deltas=DEFAULT_HINGE_PIN_DELTAS, axial_clearances=None) -> list[HingeProbeCell]:
    """Combinaciones holgura radial × Ø de bulón × holgura axial (por defecto solo la de `Params`)."""
    base = base or p.hinge_pin
    axials = tuple(axial_clearances) if axial_clearances else (p.axial_clearance,)
    cells = []
    for c in clearances:
        for dd in pin_deltas:
            hs = replace(base, pin_d=round(base.pin_d + dd, 3))
            for ax in axials:
                cells.append(HingeProbeCell(f"r{c:.2f}/d{hs.pin_d:.1f}/a{ax:.2f}", float(c), float(ax), hs))
    return cells


def _hinge_cell(cell: HingeProbeCell, wall: float = 1.5, stem_len: float = 6.0) -> S.Manifold:
    """Una bisagra de prueba: yugo (bloque con ranura y agujeros ciegos) + pieza distal (disco, vástago y 2 bulones).

    Eje de la bisagra = +X por el origen. La pieza distal queda retenida: el disco es más ancho que su vástago y
    los bulones están en agujeros cerrados. La ranura se talla con el barrido del distal dilatado (como `joints`)."""
    from .joints import _bolt, dilate_anisotropic
    h = cell.hinge
    axis = np.array([1.0, 0.0, 0.0])
    r = h.end_r
    disc = S.cylinder_along((0, 0, 0), axis, r, h.tongue_t, 64, start=-h.tongue_t / 2)
    stem_w = 2 * max(r - 1.0, 1.0)
    stem = S.Manifold.cube((h.tongue_t, stem_w, stem_len + 0.5), center=False).translate(
        (-h.tongue_t / 2, -stem_w / 2, -stem_len))
    bolts = [_bolt((0, 0, 0), axis * s, h, 32) for s in (1.0, -1.0)]
    distal = S.largest_component(S.union(disc, stem, *bolts))
    half_x = h.tongue_t / 2 + h.pin_len + cell.axial_clearance + wall
    half_y = stem_len
    top = r + cell.clearance + wall
    block = S.Manifold.cube((2 * half_x, 2 * half_y, top + 2.0), center=False).translate((-half_x, -half_y, -2.0))
    grown = dilate_anisotropic(distal, cell.clearance, cell.axial_clearance, axis)
    lo, hi = HINGE_PROBE_RANGE
    angles = list(np.arange(lo, hi + 1e-9, HINGE_PROBE_STEP))
    sweep = S.union(*[S.rotate_about_axis(grown, (0, 0, 0), axis, a) for a in angles])
    return S.union(S.difference(block, sweep), distal)


def build_hinge_probe(p: Params | None = None, cells: list[HingeProbeCell] | None = None,
                      columns: int = 3, pitch_x: float = 14.0, pitch_y: float = 16.0) -> S.Manifold:
    """Placa de probetas de bisagra en una grilla (piezas distales sueltas dentro de su yugo)."""
    p = p or Params()
    cells = cells or hinge_probe_cells(p)
    parts = []
    for i, cell in enumerate(cells):
        r, c = divmod(i, columns)
        parts.append(_hinge_cell(cell).translate((c * pitch_x, -r * pitch_y, 0)))
    return S.union(*parts)


def hinge_labels_text(cells: list[HingeProbeCell]) -> str:
    """Tabla de referencia en el orden de la grilla, para anotar cuál gira suave y sostiene la pose."""
    return "\n".join(f"#{i + 1}: holgura radial {c.clearance:.2f} mm, axial {c.axial_clearance:.2f} mm, "
                     f"bulón Ø{c.hinge.pin_d:.1f} mm (lengüeta {c.hinge.tongue_t:.1f} mm)" for i, c in enumerate(cells))
