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
from afm.trims import Trim

CROWN_Z = 60.6                 # coronilla en unidades del STL (100 mm finales)
K = 100.0 / CROWN_Z            # factor a mm finales


def poly(pts_xz):
    return np.array([[x * K, 0.0, z * K] for x, z in pts_xz], float)


fp = Plane.from_normal((0, 0, 0), (0, 1, 0), "corte al medio")
ARM = [(5.1, 40.3), (22, 40.3), (22, 17), (9.6, 17), (9.5, 24), (8.8, 30), (7.2, 34), (5.1, 36.5)]
# borde interno del pie: superficie natural del STL hasta z = 7,2 (zapato; el cliente conserva x >= 2,7 mm);
# por encima, corte plano en x = 3,0 (pelvis).
LEG = [(1.2, -1), (16, -1), (16, 26), (5.45, 26), (5.45, 16.5), (3.0, 16.5), (3.0, 7.2), (1.2, 7.2)]
arm = LimbSpec("brazo", "arm", fp, poly(ARM), 100.0)
# Medido contra la pierna del cliente (alineando por el pie, ajuste de círculo rms 0,03 mm):
# respecto del centroide de la cara de contacto (-5,88; 36,35) el pasador sube 1,6 mm y se corre 0,6 mm hacia atrás;
# el extremo de cadera es un cilindro R = 7,6 con centro 3,75 mm por debajo del pasador (coordenadas de pie, mm).
HIP_PIN = (9.65, -6.50, 37.98)
# En la pierna el cliente no rebaja la cara de contacto (la punta del pasador queda a 6,6 mm de la cara original).
leg = LimbSpec("pierna", "leg", fp, poly(LEG), 100.0, pin_center=HIP_PIN, burr=0.0,
               cap_radius=7.6, cap_center=(9.65, -6.46, 34.23))
# La cola de Mickey queda pegada a la pierna (tutorial pasos 023-026): se recorta con dos cortes.
sp = Plane.from_normal((0, 0, 0), (1, 0, 0), "plano lateral")
TAIL_ROOT = Trim("pierna", sp, np.array([(0, -9.2, 23.5), (0, -12.7, 14.9), (0, -14.7, 13.0), (0, -20, 13.0), (0, -20, 23.5)], float),
                 distance=60.0, note="raíz de la cola (corte inclinado, medido contra la pierna del cliente)")
TAIL_TIP = Trim("pierna", sp, np.array([(13.6, -17.0, 10.3), (22.5, -10.6, 15.3)], float), shape="box",
                note="punta de la cola")
proj = Project(params=Params(), symmetry=Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría"),
               crown_z=CROWN_Z, front="-Y", limbs=[arm, leg], trims=[TAIL_ROOT, TAIL_TIP])

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
