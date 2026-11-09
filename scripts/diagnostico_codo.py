"""Dónde hace tope cada articulación (codo / rodilla) de una figura: primer ángulo en que la pieza distal choca con la proximal.

Uso:  python scripts/diagnostico_codo.py PROYECTO.proyecto FIGURA.stl [paso_grados]
Para cada articulación imprime el rango de flexión, el ángulo de choque en extensión y en flexión, y lo que exige la validación
(`Params.stop_margin` + 5° más allá del límite). Sirve para ver si «sin tope de extensión» es un tope que cae unos grados más allá
(ajustable con `stop_margin`) o que no existe (hay que mover el plano de la articulación)."""
import sys

import numpy as np

from afm import pipeline as P
from afm.extract import load_body
from afm.project import Project

JOINT_TOL_MM3 = P.JOINT_TOL_MM3


def first_contact(jb, start, direction, step, limit=90.0):
    """Primer ángulo (desde `start` en la dirección ±1) en que distal y proximal se tocan, o None dentro de `limit` grados."""
    f = start
    while abs(f - start) <= limit:
        if float((jb.rotate_distal(f) ^ jb.proximal).volume()) > JOINT_TOL_MM3:
            return f
        f += direction * step
    return None


def main(proj_path, stl, step=2.0):
    pr = Project.load(proj_path)
    body, _ = load_body(stl, pr, progress=print)
    res = P.build(body, pr.limbs, pr.params, pr.symmetry, trims=pr.trims, head=pr.head, joints=pr.joints, waist=pr.waist, hand=pr.hand)
    reach = pr.params.stop_margin + 5.0
    if not res.joints:
        print("la figura no tiene articulaciones (codo / rodilla) definidas")
        return 1
    for name, jb in res.joints.items():
        lo, hi = jb.flex
        ext = first_contact(jb, lo, -1.0, step)
        fle = first_contact(jb, hi, +1.0, step)
        fmt = lambda a, lim: "no choca en 90°" if a is None else f"{a:+.0f}° ({abs(a - lim):.0f}° más allá del límite)"
        print(f"{name}: rango {lo:+.0f}°…{hi:+.0f}°; exige tope a ≤ {reach:.0f}° del límite")
        print(f"   extensión: {fmt(ext, lo)}")
        print(f"   flexión:   {fmt(fle, hi)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 2.0))
