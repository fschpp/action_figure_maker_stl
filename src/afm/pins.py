"""Pasadores tipo hongo (plan §8.5): vástago + cabeza con borde achaflanado."""
from __future__ import annotations

import numpy as np

from . import solids as S
from .params import PinSpec


def mushroom_pin(origin, direction, spec: PinSpec, overlap: float = 0.3, segments: int = 48):
    """Pasador que arranca en `origin` (sobre la cara de contacto) y avanza hacia `direction`.

    El vástago entra `overlap` mm en el miembro para que la unión sea sólida.
    """
    d = np.asarray(direction, float)
    shaft = S.cylinder_along(origin, d, spec.shaft_d / 2, spec.shaft_len + overlap, segments, start=-overlap)
    ch = min(0.5, spec.head_h / 2)
    r = spec.head_d / 2
    ov = min(0.3, spec.head_h / 3)   # solape para que vástago y cabeza se fundan (no solo contacto coplanar)
    head_base = S.cylinder_along(origin, d, r, spec.head_h - ch + ov, segments, start=spec.shaft_len - ov)
    # tope achaflanado: cono truncado de r a r-ch
    cone = S.Manifold.cylinder(ch + 0.1, r, r - ch, segments)   # arranca 0,1 mm dentro de la cabeza
    from .planes import Plane
    plane = Plane.from_normal(np.asarray(origin, float) + d * (spec.shaft_len + spec.head_h - ch - 0.1), d)
    cone = cone.transform(plane.matrix()[:3, :])
    return S.union(shaft, head_base, cone)
