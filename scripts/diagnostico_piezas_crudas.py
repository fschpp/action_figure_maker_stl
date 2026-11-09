"""Superposición entre dos piezas del catálogo tal como están guardadas (sin armar la figura).

Uso:  python scripts/diagnostico_piezas_crudas.py RUTA_DEL_CATALOGO TORSO@VERSION BRAZO@VERSION
Sirve para saber si «brazo: interpenetra el torso» viene de las piezas catalogadas o de algo que se agrega al armar (clic, cavidades)."""
import sys

import numpy as np

from afm import solids as S
from afm.catalog import Catalog


def main(catalogo, ref_torso, ref_brazo):
    cat = Catalog.open(catalogo)
    _, t = cat.load(ref_torso)
    _, a = cat.load(ref_brazo)
    torso, arm = S.to_manifold(t), S.to_manifold(a)
    inter = arm ^ torso
    vol = float(inter.volume())
    print(f"piezas crudas: se superponen {vol:.5f} mm³; holgura mínima {float(arm.min_gap(torso, 5.0)):.3f} mm")
    if vol > 1e-9:
        b = np.asarray(inter.bounding_box())
        print(f"   zona: x {b[0]:.2f}…{b[3]:.2f}  y {b[1]:.2f}…{b[4]:.2f}  z {b[2]:.2f}…{b[5]:.2f} mm")
    tb, ab = np.asarray(torso.bounding_box()), np.asarray(arm.bounding_box())
    print(f"   torso: x {tb[0]:.2f}…{tb[3]:.2f}   brazo: x {ab[0]:.2f}…{ab[3]:.2f}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
