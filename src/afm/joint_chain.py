"""Cadena de articulaciones de un miembro (plan de plugins de articulación, Fase 3, corte 1).

Hoy `pipeline.build` admite UNA articulación por miembro. Para pasar a varias (bíceps + codo + muñeca, o el tobillo
sobre la rodilla) hace falta primero un criterio único y probado de tres cosas, que este módulo aísla sin tocar geometría:

1. **Agrupar** las articulaciones por miembro, rechazando miembros inexistentes y nombres repetidos.
2. **Ordenar** cada cadena del torso a la punta (distancia firmada del plano de cada articulación a lo largo del miembro).
3. **Encadenar** los nombres de pieza: la pieza distal de una articulación es la proximal de la siguiente
   (`rodilla.dist_name == tobillo.prox_name`); si no coinciden, la pieza del medio tendría dos nombres.

Puro datos: sin `manifold3d`, sin mallas. Con una sola articulación por miembro el resultado es el de siempre.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

from .joints import JointSpec


def group_by_limb(joints: Iterable[JointSpec], limb_names: Iterable[str]) -> dict[str, list[JointSpec]]:
    """`{miembro: [articulaciones en el orden dado]}`. Error claro si apuntan a un miembro que no existe o si dos
    articulaciones se llaman igual (el nombre identifica la articulación en el informe, la GUI y los colores)."""
    known = set(limb_names)
    out: dict[str, list[JointSpec]] = {}
    seen: set[str] = set()
    for j in joints:
        if j.limb not in known:
            raise ValueError(f"la articulación «{j.name}» apunta al miembro «{j.limb}», que no existe")
        if j.name in seen:
            raise ValueError(f"hay dos articulaciones llamadas «{j.name}»: el nombre debe ser único")
        seen.add(j.name)
        out.setdefault(j.limb, []).append(j)
    return out


def order_along(joints: Sequence[JointSpec], root: Sequence[float], direction: Sequence[float]) -> list[JointSpec]:
    """Las articulaciones del torso a la punta: orden creciente de la distancia firmada del origen de su plano a
    `root` (el centro de la cara de contacto del miembro) medida sobre `direction` (de la cara hacia la punta).
    Dos planos a la misma distancia (± 1e-6 mm) no definen un orden: error, no se adivina."""
    d = np.asarray(direction, float)
    n = float(np.linalg.norm(d))
    if n < 1e-12:
        raise ValueError("la dirección del miembro es nula")
    d = d / n
    r = np.asarray(root, float)
    keyed = sorted(((float((np.asarray(j.plane.origin, float) - r) @ d), j) for j in joints), key=lambda kj: kj[0])
    for (a, ja), (b, jb) in zip(keyed, keyed[1:]):
        if b - a < 1e-6:
            raise ValueError(f"«{ja.name}» y «{jb.name}» cortan el miembro «{ja.limb}» en el mismo lugar")
    return [j for _, j in keyed]


def check_names(chain: Sequence[JointSpec]) -> list[str]:
    """Nombres de pieza de la cadena, del torso a la punta (`len(chain) + 1`). Exige que la distal de cada
    articulación sea la proximal de la siguiente y que ninguna pieza se repita."""
    if not chain:
        return []
    pieces = [chain[0].names()[0]]
    for j, nxt in zip(chain, chain[1:]):
        dist, prox_next = j.names()[1], nxt.names()[0]
        if dist != prox_next:
            raise ValueError(f"«{j.name}» deja la pieza «{dist}» y «{nxt.name}» espera «{prox_next}»: "
                             f"en una cadena la distal de una es la proximal de la siguiente (usar prox_name/dist_name)")
    for j in chain:
        pieces.append(j.names()[1])
    if len(set(pieces)) != len(pieces):
        raise ValueError(f"la cadena repite nombres de pieza: {pieces}")
    return pieces
