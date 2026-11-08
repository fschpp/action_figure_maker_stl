"""Proyecto de un héroe nuevo a partir de una figura patrón (la que ya funciona, p. ej. Güemes).

Todas las figuras se normalizan al mismo tamaño (`meshio.normalize`: pies en z = 0, `ref_height` mm hasta la coronilla,
caja centrada en X e Y), así que los planos de un proyecto ya armado caen más o menos donde corresponden en otra figura
del mismo estilo. `desde_patron` copia del patrón lo que no depende de la silueta —simetría, frente, cintura, cuello,
articulaciones (rodilla, codo), muñeca, pasadores y parámetros— y le pide a `autosil` (la misma propuesta de la ventana)
que vuelva a dibujar la silueta de cada miembro sobre la malla nueva. Lo que no se puede proponer se deja como estaba en el
patrón y se avisa. No copia las piezas de catálogo elegidas, las escalas ni los recortes: son de la figura de origen.

`review_image` dibuja la figura nueva de frente y de costado con los planos copiados encima, para ver de un vistazo si caen
donde deben antes de abrir el proyecto en la ventana.
"""
from __future__ import annotations

import copy
import json

import numpy as np

from .planes import Plane
from .project import Project


def desde_patron(ref_path: str, stl: str, crown_z: float | None = None, front: str | None = None,
                 crotch_z: float | None = None, progress=None):
    """(`Session`, avisos) con la figura `stl` y el proyecto del patrón `ref_path` adaptado. No guarda nada."""
    from .gui.session import Session

    with open(ref_path) as f:
        ref = Project.from_dict(json.load(f))
    proj = copy.deepcopy(ref)
    notes: list[str] = []
    if proj.trims:
        notes.append(f"se descartaron {len(proj.trims)} recorte(s) del patrón: son de su malla (hay que rehacerlos si hacen falta)")
    if proj.parts or proj.part_scales:
        notes.append("se descartaron las piezas de catálogo y las escalas del patrón")
    proj.trims, proj.parts, proj.part_scales, proj.accesorios = [], {}, {}, []
    proj.front = front or ref.front
    proj.symmetry = Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría")        # la normalización centra la figura en X
    proj.crown_z = crown_z
    sess = Session(proj)
    sess.load(stl, crown_z, progress=progress)
    for spec in list(proj.limbs):
        try:
            sess.propose_silhouette(spec.name, crotch_z if spec.kind == "leg" else None)
        except (ValueError, KeyError) as exc:
            notes.append(f"«{spec.name}»: se dejó la silueta del patrón ({exc})")
        if spec.cross is not None:
            try:
                sess.propose_silhouette(spec.name, crotch_z if spec.kind == "leg" else None, cross=True)
            except (ValueError, KeyError) as exc:
                notes.append(f"«{spec.name}»: se dejó el segundo corte del patrón ({exc})")
    for what, obj in (("cintura", proj.waist), ("cuello", proj.head), ("mano", proj.hand)):
        if obj is not None:
            notes.append(f"{what}: se copió del patrón; revisá que caiga bien en esta figura")
    if proj.joints:
        notes.append(f"{len(proj.joints)} articulación(es) copiadas del patrón; revisá la altura de rodilla y codo")
    return sess, notes


# ---------------------------------------------------------------------------------------------------- imagen de revisión
def _trace(ax, plane: Plane, view: str, lim, color: str, label: str | None = None, lw: float = 1.6) -> None:
    """Dibuja la traza del plano en la vista (`"front"` = x–z, `"side"` = y–z), o nada si el plano es paralelo a la vista."""
    i = 0 if view == "front" else 1
    n = np.asarray(plane.normal, float)
    o = np.asarray(plane.origin, float)
    a, b = n[i], n[2]
    if abs(a) < 1e-6 and abs(b) < 1e-6:
        return
    d = np.array([-b, a])
    d /= np.linalg.norm(d)
    c = np.array([o[i], o[2]])
    t = np.linspace(-1e3, 1e3, 2)
    pts = c[None] + t[:, None] * d[None]
    ax.plot(pts[:, 0], pts[:, 1], color=color, lw=lw, label=label)
    ax.set_xlim(*lim[0])
    ax.set_ylim(*lim[1])


def review_image(sess, path: str) -> str:
    """PNG con la figura de frente y de costado y los planos del proyecto encima. Devuelve `path`."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    mesh, proj = sess.work, sess.project
    tris = np.asarray(mesh.triangles, float)
    b = mesh.bounds
    pad = 6.0
    fig, axes = plt.subplots(1, 2, figsize=(10, 7.5), dpi=130)
    for ax, view in zip(axes, ("front", "side")):
        i = 0 if view == "front" else 1
        j = 1 if view == "front" else 0                                         # eje de profundidad
        order = np.argsort(tris[:, :, j].mean(axis=1))[::-1] if view == "front" else np.argsort(tris[:, :, 0].mean(axis=1))
        poly = tris[order][:, :, [i, 2]]
        ax.add_collection(PolyCollection(poly, facecolors="#d9d4cc", edgecolors="#b5aea3", linewidths=0.15))
        lim = ((b[0, i] - pad, b[1, i] + pad), (b[0, 2] - pad, b[1, 2] + pad))
        ax.set_xlim(*lim[0])
        ax.set_ylim(*lim[1])
        ax.set_aspect("equal")
        ax.set_title("De frente" if view == "front" else "De costado")
        ax.axis("off")
        if proj.waist is not None:
            _trace(ax, proj.waist.plane, view, lim, "#1f77b4", "cintura")
        if proj.head is not None:
            _trace(ax, proj.head.plane, view, lim, "#2ca02c", "cuello")
        for k, jt in enumerate(proj.joints):
            _trace(ax, jt.plane, view, lim, "#ff7f0e", jt.name if k == 0 else None, lw=1.2)
        for spec in proj.limbs:
            poly3 = np.asarray(spec.polygon, float)
            if len(poly3) >= 3:
                pts = poly3[:, [i, 2]]
                ax.plot(*np.vstack([pts, pts[:1]]).T, color="#d62728", lw=1.2, label="silueta de miembro" if spec is proj.limbs[0] else None)
            _trace(ax, spec.plane, view, lim, "#d62728", None, lw=0.8)
        if proj.hand is not None and len(proj.hand.points):
            hp = np.asarray(proj.hand.points, float)
            ax.plot(hp[:, i], hp[:, 2], "o", color="#9467bd", ms=4, label="muñeca")
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path)
    plt.close(fig)
    return path
