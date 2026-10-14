"""Proyecto de ejemplo para mickey_reescalado.stl (Mickey original, 75 mm pies->sombrero, Z arriba, mira hacia -Y).

Las siluetas están dibujadas en unidades del STL original y se escalan a mm finales (coronilla = 100 mm).
Las caras de contacto salen del borde vertical (x = const) de cada silueta:
hombro en x = 5,1 y cadera en x = 5,45 (deducido de las piezas finales del cliente).
"""
import sys

import numpy as np

from afm.cli import run
from afm.limbs import LimbSpec
from afm.params import Params
from afm.planes import Plane
from afm.project import Project

CROWN_Z = 60.6                 # coronilla en unidades del STL (100 mm finales)
K = 100.0 / CROWN_Z            # factor a mm finales


def poly(pts_xz):
    return np.array([[x * K, 0.0, z * K] for x, z in pts_xz], float)


fp = Plane.from_normal((0, 0, 0), (0, 1, 0), "corte al medio")
ARM = [(5.1, 40.3), (22, 40.3), (22, 17), (9.6, 17), (9.5, 24), (8.8, 30), (7.2, 34), (5.1, 36.5)]
LEG = [(3.0, -1), (16, -1), (16, 26), (5.45, 26), (5.45, 16.5), (3.0, 16.5)]
arm = LimbSpec("brazo", "arm", fp, poly(ARM), 100.0)
leg = LimbSpec("pierna", "leg", fp, poly(LEG), 100.0)
proj = Project(params=Params(), symmetry=Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría"),
               crown_z=CROWN_Z, front="-Y", limbs=[arm, leg])

if __name__ == "__main__":
    import os
    stl, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    info = run(stl, proj, out, "mickey")
    v = info["validacion"]
    print("OK" if v["ok"] else "PROBLEMAS", v["problems"])
    print("avisos", v["avisos"])
    print("holguras", v["gaps"])
    print({k: (x["componentes"], x["triangulos"]) for k, x in v["pieces"].items()})
