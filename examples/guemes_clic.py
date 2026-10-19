"""Güemes de 13 cm con mecanismo de clic en hombros y caderas (PLAN_mecanismo_clic_hombro_cadera.md).

Las siluetas de `guemes.py` están en mm de una figura de 100 mm; aquí se escalan por k = 129/100 para la figura de 13 cm
(`Params.reference_figure()`: 129 mm de los pies a la coronilla marcada). Los parámetros del clic son mm absolutos.

Uso:  python examples/guemes_clic.py examples/guemes_segmentado_lq_fixed_resized.stl out/guemes_clic
"""
import dataclasses
import os
import sys

import numpy as np

from afm.cli import run
from afm.limbs import LimbSpec
from afm.params import Params
from afm.planes import Plane
from afm.project import Project

K = 1.29
fp = Plane.from_normal((0, 0, 0), (0, 1, 0), "corte al medio")
arm = LimbSpec("brazo", "arm", fp, K * np.array([[12, 0, 44], [32, 0, 44], [32, 0, 95], [12, 0, 95]], float), K * 40.0)
leg = LimbSpec("pierna", "leg", fp,
               K * np.array([[0, 0, -1], [18, 0, -1], [18, 0, 58], [7, 0, 58], [7, 0, 47], [0, 0, 47]], float), K * 40.0,
               cut_offset=K * 7.0)
params = dataclasses.replace(Params.reference_figure(), shoulder_detent=True, hip_detent=True)
proj = Project(params=params, symmetry=Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría"), crown_z=100.0,
               front="-Y", limbs=[arm, leg])

if __name__ == "__main__":
    stl, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    info = run(stl, proj, out, "guemes_clic", orient=True)
    v = info["validacion"]
    print("OK" if v["ok"] else "PROBLEMAS", v["problems"])
    print("avisos", v["avisos"])
    print("holguras", v["gaps"])
    print({k: x["componentes"] for k, x in v["pieces"].items()})
