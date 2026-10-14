"""Figura sintética (muñeco) para pruebas y demo sin STL real."""
from __future__ import annotations

import numpy as np
import trimesh

from . import solids as S
from .solids import Manifold

# Puntos (mm) de las siluetas y planos del muñeco, para el proyecto de ejemplo.
ARM_X0 = 15.0   # cara de contacto del brazo (plano x = ARM_X0)
LEG_X0 = 14.0   # cara de contacto de la pierna


def make_doll() -> trimesh.Trimesh:
    """Muñeco de 100 mm (pies→coronilla): pelvis ancha, torso, cabeza, 2 brazos, 2 piernas."""
    def ell(rx, ry, h, z0, cx=0.0):
        return (Manifold.cylinder(h, 1, 1, 64).scale((rx, ry, 1)).translate((cx, 0, z0)))

    pelvis = ell(23, 12, 8, 34)                       # z 34..42
    torso = ell(15, 10, 33, 42)                       # z 42..75
    head = Manifold.sphere(11, 48).translate((0, 0, 89))   # z 78..100
    neck = ell(4, 4, 10, 72)
    parts = [pelvis, torso, head, neck]
    for sx in (+1, -1):
        parts.append(ell(7, 7, 38, -0.0, cx=sx * 9))                        # pierna z 0..38 (x 2..16)
        arm = Manifold.cylinder(30, 4, 4, 32).translate((sx * 18, 0, 45))   # brazo z 45..75
        parts.append(arm)
    body = S.union(*parts)
    return S.to_trimesh(body)
