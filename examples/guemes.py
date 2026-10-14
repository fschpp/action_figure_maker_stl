"""Proyecto de ejemplo para guemes_segmentado_lq_fixed_resized.stl (114 mm, mira hacia -Y)."""
import sys

import numpy as np

from afm.cli import run
from afm.limbs import LimbSpec
from afm.params import Params
from afm.planes import Plane
from afm.project import Project

fp = Plane.from_normal((0, 0, 0), (0, 1, 0), "corte al medio")
# Silueta del brazo (vista frontal XZ): todo lo que está a x > 12 entre z 44 y 95.
arm = LimbSpec("brazo", "arm", fp, np.array([[12, 0, 44], [32, 0, 44], [32, 0, 95], [12, 0, 95]], float), 40.0)
# Silueta de la pierna: columna completa + pedazo de cadera (x > 7) hasta z = 58.
leg = LimbSpec("pierna", "leg", fp,
               np.array([[0, 0, -1], [18, 0, -1], [18, 0, 58], [7, 0, 58], [7, 0, 47], [0, 0, 47]], float), 40.0)
proj = Project(params=Params(), symmetry=Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría"),
               crown_z=100.0,   # supuesto: escala 1:1 (el STL ya está en mm)
               front="-Y", limbs=[arm, leg])

if __name__ == "__main__":
    stl, out = sys.argv[1], sys.argv[2]
    import os
    os.makedirs(out, exist_ok=True)
    info = run(stl, proj, out, "guemes")
    v = info["validacion"]
    print("OK" if v["ok"] else "PROBLEMAS", v["problems"])
    print("holguras", v["gaps"])
    print({k: x["componentes"] for k, x in v["pieces"].items()})
