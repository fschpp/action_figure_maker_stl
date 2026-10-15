"""Proyecto de ejemplo para belgrano_lq_segmentado.stl (sin recortar; frente hacia -Y, sin sombrero).

El STL viene en unidades de ~1,16 de alto: se normaliza a 100 mm pies->coronilla.
crown_z va en unidades del ARCHIVO (no en mm): sin sombrero es el punto más alto, 1,1595.
Medidas de las siluetas tomadas de cortes horizontales de la figura ya a 100 mm.
"""
import sys

import numpy as np

from afm.cli import run
from afm.limbs import LimbSpec
from afm.params import Params
from afm.planes import Plane
from afm.project import Project

fp = Plane.from_normal((0, 0, 0), (0, 1, 0), "corte al medio")
# Brazo: por debajo de z=55 el brazo está separado del abrigo (x >= 13.1 sobre el abrigo, que llega a 11,6);
# por encima, la manga se funde con el torso entre z=66 y 80 y el corte vertical queda en x = 11 (torso <= 10,7).
arm = LimbSpec("brazo", "arm", fp,
               np.array([[11, 0, 85], [24, 0, 85], [24, 0, 36], [13.1, 0, 36], [13.1, 0, 55], [11, 0, 55]], float),
               40.0, auto=True)
# Pierna: columna (botas) hasta z=38 más un trozo de cadera bajo el abrigo (x > 7, z 38-48).
# x <= 13,0 deja afuera la mano (empieza en x = 13,3 a z = 44).
leg = LimbSpec("pierna", "leg", fp,
               np.array([[0, 0, -1], [13.0, 0, -1], [13.0, 0, 48], [7, 0, 48], [7, 0, 38], [0, 0, 38]], float),
               40.0, auto=True)
proj = Project(params=Params(), symmetry=Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría"),
               crown_z=1.1595070362091064, front="-Y", limbs=[arm, leg])

if __name__ == "__main__":
    stl, out = sys.argv[1], sys.argv[2]
    import os
    os.makedirs(out, exist_ok=True)
    info = run(stl, proj, out, "belgrano")
    v = info["validacion"]
    print("OK" if v["ok"] else "PROBLEMAS", v["problems"])
    print("holguras", v["gaps"])
    print({k: x["componentes"] for k, x in v["pieces"].items()})
    print(info["construccion"]["limbs"])
