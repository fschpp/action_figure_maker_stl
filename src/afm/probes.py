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
