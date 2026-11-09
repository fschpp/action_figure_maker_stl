"""Dibuja la cara del cuello de la cabeza de reemplazo tal como la ve la interfaz y tal como queda catalogada.

Uso:  python scripts/diagnostico_cuello.py PROYECTO.proyecto FIGURA.stl SALIDA.png
Imprime áreas, círculo inscrito y dónde cae el pasador en cada una, y guarda un dibujo con las dos siluetas."""
import dataclasses
import sys

import numpy as np

from afm.extract import extract_all, load_body
from afm.head import _face_from_head, _face_shape, fit_replacement, neck_plane, pin_center_of, split_head
from afm.ports import pin_diagnosis
from afm.project import Project


def rings(shape):
    out = []
    for g in (list(shape.geoms) if hasattr(shape, "geoms") else [shape]):
        out.append(np.asarray(g.exterior.coords))
        out += [np.asarray(r.coords) for r in g.interiors]
    return out


def main(proj_path, stl, png):
    pr = Project.load(proj_path)
    body, _ = load_body(stl, pr, progress=print)
    p = pr.params
    neck = neck_plane(pr.head)
    split = split_head(body, dataclasses.replace(pr.head, replacement_path=None), p)
    placed, info = fit_replacement(pr.head, neck, split.face)
    gui = _face_from_head(placed, neck)
    plane, gshape = _face_shape(gui)
    c_gui = pin_center_of(dataclasses.replace(split, face=gui), pr.head)
    res = extract_all(body, pr, "diag", ["cabeza"])
    port = res.pieces[0].meta.puertos["cuello"]
    fr, pshape = port.frame, port.face.shape()
    print(f"interfaz: área {gui.area:.1f} mm², centro de la cara {np.round(gui.center, 2)}, pasador en {np.round(c_gui, 2)}")
    print(f"catálogo: área {port.face.area:.1f} mm², puerto en {np.round(fr.center, 2)}")
    print("catálogo, pasador vs cara:", {k: round(v, 2) if isinstance(v, float) else v for k, v in pin_diagnosis(port.face, p.neck_pin.shaft_d).items()})
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 6))
    for r in rings(gshape):
        uv = fr.to_uv(plane.to_world(r))
        ax.plot(uv[:, 0], uv[:, 1], "b-", label="cara en la interfaz")
    for r in rings(pshape):
        ax.plot(r[:, 0], r[:, 1], "r--", label="cara catalogada")
    ax.add_patch(plt.Circle((0, 0), p.neck_pin.shaft_d / 2, fill=False, color="g", lw=2, label="pasador (puerto)"))
    ax.plot([0], [0], "g+")
    ax.set_aspect("equal"); ax.legend(); ax.set_title("cuello: azul = interfaz, rojo = catálogo")
    fig.savefig(png, dpi=110)
    print("dibujo en", png)


if __name__ == "__main__":
    main(*sys.argv[1:4])
