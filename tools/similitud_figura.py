#!/usr/bin/env python3
"""Escribe la similitud 4×4 FBX → marco de la figura, para el asistente «Roles de color desde un FBX…» (o `afm catalogo color --similitud`).

Desde la GUI no hace falta este script: el asistente tiene el botón «Calcular similitud…». Queda para usarlo por línea de comandos.

    python tools/similitud_figura.py figura.stl proyecto.proyecto figura.fbx similitud.json
"""
import json
import sys

from afm.figsim import FigureSimilarityError, compute_figure_similarity
from afm.frame import SIMILARITY_OK_MM


def main(argv) -> int:
    if len(argv) != 5:
        print(__doc__)
        return 2
    stl, proj, fbx, out = argv[1:]
    try:
        sim = compute_figure_similarity(stl, proj, fbx)
    except FigureSimilarityError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"distancia media FBX → figura: {sim.media_mm:.3f} mm (máximo aceptado {SIMILARITY_OK_MM:g} mm)")
    if not sim.ok:
        print("AVISO: el FBX no coincide con esa figura; revisá que el STL y el FBX sean de la misma figura.", file=sys.stderr)
        return 1
    with open(out, "w") as fh:
        json.dump({"similitud": sim.matrix}, fh, indent=1)
    print(f"escrito {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
