"""Proyecto de ejemplo para azurduy_lq_segmentado.stl (frente hacia -Y, sin sombrero).

El STL no es estanco (4 aristas abiertas) y viene en unidades de ~1,14 de alto. `crown_z` va en unidades
del ARCHIVO: sin sombrero es el punto más alto (1,13554). Siluetas tomadas de cortes horizontales a 100 mm.
"""
import sys

import numpy as np

from afm.cli import run
from afm.limbs import LimbSpec
from afm.params import Params
from afm.planes import Plane
from afm.project import Project
from afm.trims import Trim

fp = Plane.from_normal((0, 0, 0), (0, 1, 0), "corte al medio")
# Brazo: bajo z=52 el borde interno del brazo está en x >= 12,2 (el faldón llega a 11,9); entre 52 y 66 el brazo se
# acerca al torso (de 12,2 a 9,3 contra 9,5-8,7), y el hombro se funde con el torso entre z 68 y 80:
# corte vertical en x = 9 (ahí queda la cara de contacto).
arm = LimbSpec("brazo", "arm", fp,
               np.array([[9.0, 0, 85], [26, 0, 85], [26, 0, 36], [12.1, 0, 36], [12.1, 0, 52],
                         [10.5, 0, 58], [9.0, 0, 66.5]], float),
               40.0, auto=True)
# Pierna: botas hasta z=42 más un trozo de cadera bajo el faldón (x > 7, z 42-52). x <= 12,0 deja afuera el brazo.
leg = LimbSpec("pierna", "leg", fp,
               np.array([[0, 0, -1], [12.0, 0, -1], [12.0, 0, 52], [7, 0, 52], [7, 0, 42], [0, 0, 42]], float),
               40.0, auto=True)
# El STL trae láminas finísimas (~0,4 mm, z 0,6-1,1) que sobresalen de las suelas hasta x = 14,9 e y = -11,9.
# La bota real a z >= 1,1 ocupa x 4,1-10,8, y -8,7..7,0: se recorta todo lo que cae afuera por debajo de z = 1,1;
# y lo que quedó del lado del torso (x > 12) se quita entero bajo z = 2.
def _box(target, lo, hi, note):
    return Trim(target, fp, np.array([lo, hi], float), shape="box", note=note)


trims = [
    _box("pierna", (10.9, -30, -5), (30, 30, 1.1), "lámina de la suela, afuera de la bota (x)"),
    _box("pierna", (-30, -30, -5), (30, -8.8, 1.1), "lámina de la suela, afuera de la punta (y)"),
    _box("torso", (-30, -30, -5), (30, 30, 2.0), "resto de la lámina de las suelas que quedó en el torso"),
]
proj = Project(params=Params(), symmetry=Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría"),
               crown_z=1.1355350017547607, front="-Y", limbs=[arm, leg], trims=trims)

if __name__ == "__main__":
    stl, out = sys.argv[1], sys.argv[2]
    import os
    os.makedirs(out, exist_ok=True)
    info = run(stl, proj, out, "azurduy")
    v = info["validacion"]
    print("OK" if v["ok"] else "PROBLEMAS", v["problems"])
    print("holguras", v["gaps"])
    print({k: x["componentes"] for k, x in v["pieces"].items()})
    print(info["construccion"]["limbs"])
