#!/usr/bin/env python3
"""Escribe la similitud 4×4 FBX → marco de la figura, para el asistente «Roles de color desde un FBX…» (o `afm catalogo color --similitud`).

Hace falta cuando la pieza del catálogo es un trozo recortado de una figura (brazo, pierna, torso…): el FBX es el de la figura ENTERA y no
coincide con el STL de la pieza («el FBX no coincide con el STL de la pieza: distancia media …»). Es la misma matriz que usa `afm build --color-desde`.

    python tools/similitud_figura.py figura.stl proyecto.proyecto figura.fbx similitud.json
"""
import json
import sys

from afm import meshio
from afm.frame import SIMILARITY_OK_MM, FigureFrame
from afm.project import Project
from afm.fbxio import load_textured


def main(argv) -> int:
    if len(argv) != 5:
        print(__doc__)
        return 2
    stl, proj, fbx, out = argv[1:]
    project = Project.load(proj)
    mesh, _rep = meshio.load_figure(stl, project.params.ref_height, project.crown_z)   # la figura tal como se carga, antes de girar/alinear
    tm = load_textured(fbx)
    frame = FigureFrame.from_similarity(tm, mesh).then(FigureFrame.engine_post(project.front, project.symmetry))
    media = float(frame.report.get("media_mm", 0.0))
    print(f"distancia media FBX → figura: {media:.3f} mm (máximo aceptado {SIMILARITY_OK_MM:g} mm)")
    if not frame.report.get("ok", True):
        print("AVISO: el FBX no coincide con esa figura; revisá que el STL y el FBX sean de la misma figura.", file=sys.stderr)
        return 1
    with open(out, "w") as fh:
        json.dump({"similitud": frame.matrix.tolist()}, fh, indent=1)
    print(f"escrito {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
