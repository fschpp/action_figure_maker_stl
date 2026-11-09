"""Dónde se tocan el brazo y el torso en una figura armada desde el catálogo.

Uso:  python scripts/diagnostico_brazo_torso.py RECETA.receta.json RUTA_DEL_CATALOGO
      python scripts/diagnostico_brazo_torso.py PROYECTO.proyecto RUTA_DEL_CATALOGO [FIGURA.stl]
Arma la figura de la receta o del proyecto (como «Generar»; con proyecto, el STL sale del propio proyecto si no se pasa) y, para cada brazo, imprime el volumen que se superpone con el torso, dónde está
(caja y centro), la holgura mínima y la cara por donde se tocan. Sirve para ver de dónde sale «brazo: interpenetra el torso»
o una holgura de 0 mm en el informe."""
import sys

import numpy as np

from afm.recipe import Recipe, assemble


def main(archivo, catalogo, stl=None, salida=None):
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
        if salida and name == "brazo_izq":
            recortes(res, arm, torso, inter if vol > 1e-9 else None, salida)


def recortes(res, arm, torso, inter, salida):
    """Guarda en `salida` recortes chicos (unos KB) del torso, el brazo y el clic alrededor de la zona de contacto, para mandarlos."""
    import os

    import trimesh
    from afm import solids as S
    os.makedirs(salida, exist_ok=True)
    b = np.asarray((inter if inter is not None else arm).bounding_box())
    c = (b[:3] + b[3:]) / 2 if inter is not None else np.array([b[0] + 3.0, (b[1] + b[4]) / 2, b[5] - 12.0])
    box = S.to_manifold(trimesh.creation.box(extents=(16.0, 16.0, 16.0), transform=trimesh.transformations.translation_matrix(c)))
    items = {"torso": torso, "brazo": arm}
    dl = (getattr(res, "detent", None) or {}).get("brazo")
    if dl is not None:
        items.update({"clic_miembro": dl.limb, "clic_talla_torso": dl.sweep})
    for nom, mf in items.items():
        piece = mf ^ box
        if piece.volume() > 0:
            S.to_trimesh(piece).export(os.path.join(salida, f"recorte_{nom}.stl"))
    print(f"recortes guardados en {salida} (centro {np.round(c, 2).tolist()})")


if __name__ == "__main__":
    main(*sys.argv[1:5])
