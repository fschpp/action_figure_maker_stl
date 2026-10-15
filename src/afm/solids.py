"""Envoltorio sobre manifold3d: conversión, booleanos, primitivas y offset."""
from __future__ import annotations

import numpy as np
import trimesh
import manifold3d as m3

from .planes import Plane

Manifold = m3.Manifold


class MeshNotManifold(RuntimeError):
    pass


def to_manifold(mesh: trimesh.Trimesh) -> Manifold:
    mesh = mesh.copy()
    mesh.merge_vertices()
    mf = Manifold(
        m3.Mesh(
            vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
            tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
        )
    )
    if mf.status() != m3.Error.NoError:
        raise MeshNotManifold(f"La malla no es una variedad estanca: {mf.status()}")
    return mf


def to_trimesh(mf: Manifold) -> trimesh.Trimesh:
    mm = mf.to_mesh()
    return trimesh.Trimesh(
        vertices=np.asarray(mm.vert_properties)[:, :3].astype(float),
        faces=np.asarray(mm.tri_verts).astype(np.int64),
        process=False,
    )


def to_printable_trimesh(mf: Manifold, tol: float = 1e-3) -> trimesh.Trimesh:
    """Malla lista para escribir a STL: lo que un slicer ve al soldar vértices por posición.

    manifold3d puede dejar vértices duplicados en una misma posición y triángulos de área cero
    (colapsados sobre una arista). Al escribir el STL y soldar por posición, esos triángulos quedan
    con un índice repetido y generan aristas con más de 2 caras («non-manifold edge» en el slicer).
    Acá se simplifica con una tolerancia muy por debajo de la resolución de impresión (`tol`, mm),
    se sueldan los vértices coincidentes y se descartan las caras con un índice repetido.
    No se borran las caras de área cero «colineales»: sostienen la topología (borrarlas abre agujeros).
    """
    tm = to_trimesh(mf.simplify(tol) if tol and tol > 0 else mf)
    tm.merge_vertices()
    f = tm.faces
    keep = (f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])
    if not keep.all():
        tm.update_faces(keep)
        tm.remove_unreferenced_vertices()
    return tm


def union(*parts: Manifold) -> Manifold:
    return Manifold.batch_boolean(list(parts), m3.OpType.Add)


def difference(a: Manifold, *others: Manifold) -> Manifold:
    return Manifold.batch_boolean([a, *others], m3.OpType.Subtract)


def intersection(a: Manifold, b: Manifold) -> Manifold:
    return a ^ b


def largest_component(mf: Manifold) -> Manifold:
    parts = mf.decompose()
    if len(parts) <= 1:
        return mf
    return max(parts, key=lambda p: p.volume())


def drop_small_components(mf: Manifold, min_ratio: float = 0.01) -> Manifold:
    parts = mf.decompose()
    if len(parts) <= 1:
        return mf
    vmax = max(p.volume() for p in parts)
    keep = [p for p in parts if p.volume() >= min_ratio * vmax]
    return union(*keep)


# ---------------------------------------------------------------- primitivas
def cylinder_along(axis_origin, axis_dir, radius: float, length: float, segments: int = 48,
                   start: float = 0.0) -> Manifold:
    """Cilindro con eje (origen, dirección) que va de `start` a `start+length` sobre el eje."""
    d = np.asarray(axis_dir, float)
    d = d / np.linalg.norm(d)
    cyl = Manifold.cylinder(length, radius, radius, segments)  # eje +Z, z in [0, length]
    plane = Plane.from_normal(np.asarray(axis_origin, float) + d * start, d)
    return cyl.transform(plane.matrix()[:3, :])


def prism_from_polygon(plane: Plane, polygon_world, thickness: float, symmetric: bool = True) -> Manifold:
    """Extruye un polígono (puntos 3D sobre el plano) perpendicularmente al plano."""
    uv = plane.to_uv(polygon_world).astype(float)
    x, y = uv[:, 0], uv[:, 1]
    if 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) < 0:  # forzar sentido antihorario
        uv = uv[::-1]
    cs = m3.CrossSection([uv])
    prism = Manifold.extrude(cs, thickness)  # z in [0, thickness]
    if symmetric:
        prism = prism.translate((0, 0, -thickness / 2))
    return prism.transform(plane.matrix()[:3, :])


def halfspace(plane: Plane, size: float = 1e4) -> Manifold:
    """Semiespacio (caja grande) del lado de la normal del plano."""
    box = Manifold.cube((size, size, size), center=True).translate((0, 0, size / 2))
    return box.transform(plane.matrix()[:3, :])


def rotate_about_axis(mf: Manifold, origin, axis, angle_deg: float) -> Manifold:
    """Rota `angle_deg` alrededor de la recta (origen, eje)."""
    o = np.asarray(origin, float)
    ax = np.asarray(axis, float)
    ax = ax / np.linalg.norm(ax)
    a = np.radians(angle_deg)
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    R = np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * (K @ K)
    t = o - R @ o
    return mf.transform(np.hstack([R, t[:, None]]))


# -------------------------------------------------------------------- offset
def dilate(mf: Manifold, r: float, segments: int = 12) -> Manifold:
    """Dilata un sólido `r` mm (suma de Minkowski con una esfera de baja resolución).

    La esfera se circunscribe (radio r / cos(pi/segments)) para no quedar corta,
    de modo que la holgura real sea >= r.
    """
    if r <= 0:
        return mf
    rr = r / np.cos(np.pi / segments)
    return mf.minkowski_sum(Manifold.sphere(rr, segments))


def mirror_x(mf: Manifold) -> Manifold:
    return mf.mirror((1, 0, 0))


# ------------------------------------------------------------------ decimación
def decimate(mf: Manifold, reduction: float = 0.6, max_vol_change: float = 0.002) -> Manifold:
    """Reduce triángulos con decimación cuadrática (fast_simplification), conservando la forma.

    Devuelve el original si la librería no está, si el resultado no es variedad o si el volumen
    cambia más de `max_vol_change` (fracción). Descarta cáscaras de volumen despreciable.
    """
    if reduction <= 0:
        return mf
    try:
        import fast_simplification as fs
    except ImportError:
        return mf
    tm = to_trimesh(mf)
    tm.merge_vertices()
    v, f = fs.simplify(tm.vertices.astype(np.float32), tm.faces.astype(np.int32),
                       target_reduction=reduction, agg=5)
    try:
        out = to_manifold(trimesh.Trimesh(v, f, process=False))
    except MeshNotManifold:
        return mf
    out = drop_small_components(out, 1e-4)
    if abs(out.volume() - mf.volume()) > max_vol_change * abs(mf.volume()):
        return mf
    return out
