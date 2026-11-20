"""Mide `ColorSource.paint` con una pieza sintética densa (~1,6 M de muestras, el supuesto del plan de retoque, riesgo 1).

Uso: python scripts/medir_retoque_paint.py [--muestras 1600000]
Genera una esfera de 40 mm de radio, la muestrea a `spacing` suficiente para llegar a las muestras pedidas y pinta
100 / 1 000 / 5 000 / 20 000 pinceladas de 1,5 mm con normal. Es solo una medición (no es una prueba)."""
import argparse
import time

import numpy as np
import trimesh

from afm.colorroles import RolesPackage
from afm.colorsrc import ColorSource
from afm.palette import Palette, Role
from afm.retouch import Stroke


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--muestras", type=int, default=1_600_000)
    a = ap.parse_args()
    ball = trimesh.creation.icosphere(subdivisions=5, radius=40.0)
    V, F = np.asarray(ball.vertices, np.float32), np.asarray(ball.faces, np.int32)
    uv = np.full((len(F), 3, 2), 0.5, np.float32)
    pal = Palette([Role("piel", "Piel", "#EABF97"), Role("rojo", "Rojo", "#CC0000")])
    pkg = RolesPackage(V, F, uv, np.zeros((4, 4), np.uint8), ["piel"], {})
    spacing = float(np.sqrt(ball.area / a.muestras))
    t = time.perf_counter()
    src = ColorSource(pkg.with_roles(("rojo",)).to_textured(pal), spacing_mm=spacing)
    print(f"ColorSource: {len(src.sample_xyz):,} muestras (spacing {spacing:.3f} mm) en {time.perf_counter() - t:.2f} s")
    rng = np.random.default_rng(0)
    for n in (100, 1000, 5000, 20000):
        i = rng.integers(0, len(src.sample_xyz), n)
        strokes = [Stroke(tuple(src.sample_xyz[k]), 1.5, "rojo", tuple(src.face_normals[src.sample_face[k]])) for k in i]
        t = time.perf_counter()
        rep = src.paint(strokes)
        print(f"{n:>6} pinceladas: {time.perf_counter() - t:6.2f} s, {rep['muestras']:,} muestras cambiadas (acumulado: cada fila parte de la anterior)")


if __name__ == "__main__":
    main()
