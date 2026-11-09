"""Dónde se tocan el brazo y el torso en una figura armada desde el catálogo.

Uso:  python scripts/diagnostico_brazo_torso.py RECETA.receta.json RUTA_DEL_CATALOGO
      python scripts/diagnostico_brazo_torso.py PROYECTO.proyecto RUTA_DEL_CATALOGO [FIGURA.stl]
Arma la figura de la receta o del proyecto (como «Generar»; con proyecto, el STL sale del propio proyecto si no se pasa) y, para cada brazo, imprime el volumen que se superpone con el torso, dónde está
(caja y centro), la holgura mínima y la cara por donde se tocan. Sirve para ver de dónde sale «brazo: interpenetra el torso»
o una holgura de 0 mm en el informe."""
import sys

import numpy as np

from afm.recipe import Recipe, assemble


def main(archivo, catalogo, stl=None):
    if archivo.endswith(".proyecto"):
        from afm.gui.session import Session
        sess = Session.open_project(archivo, stl, catalogo)
        if not sess.catalog_root:
            sess.open_catalog(catalogo)          # las piezas de catálogo del proyecto se resuelven en este catálogo
        res = sess.build(progress=print).result
    else:
        res = assemble(Recipe.load(archivo), catalogo, progress=print).res
    torso = res.pieces["torso"]
    for name in ("brazo_izq", "brazo_der"):
        arm = res.pieces[name]
        inter = arm ^ torso
        vol = float(inter.volume())
        gap = float(arm.min_gap(torso, 5.0))
        print(f"{name}: se superpone con el torso {vol:.5f} mm³; holgura mínima {gap:.3f} mm")
        if vol > 1e-9:
            lo, hi = np.asarray(inter.bounding_box()[:3]), np.asarray(inter.bounding_box()[3:])
            print(f"   zona de la superposición: x {lo[0]:.2f}…{hi[0]:.2f}  y {lo[1]:.2f}…{hi[1]:.2f}  z {lo[2]:.2f}…{hi[2]:.2f} mm")
        a0, a1 = np.asarray(arm.bounding_box()[:3]), np.asarray(arm.bounding_box()[3:])
        t0, t1 = np.asarray(torso.bounding_box()[:3]), np.asarray(torso.bounding_box()[3:])
        print(f"   brazo: x {a0[0]:.2f}…{a1[0]:.2f}   torso: x {t0[0]:.2f}…{t1[0]:.2f}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
