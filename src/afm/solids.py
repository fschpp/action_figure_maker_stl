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


def _bad_edges(tm: trimesh.Trimesh) -> int:
    """Aristas que no tienen exactamente 2 caras."""
    _, counts = np.unique(tm.edges_sorted, axis=0, return_counts=True)
    return int((counts != 2).sum())


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
    if _bad_edges(tm):
        # un tallado que roza una superficie existente deja vértices a menos de 1e-4 mm sin soldar: pinchan la malla en
        # aristas de 4 caras (el «non-manifold edge» del laminador). Se prueba soldar también hasta esa resolución y se
        # conserva solo si mejora (en una malla sana, soldar de más puede crear pinches nuevos).
        alt = tm.copy()
        alt.merge_vertices(digits_vertex=4)
        f = alt.faces
        keep = (f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])
        if not keep.all():
            alt.update_faces(keep)
            alt.remove_unreferenced_vertices()
        if _bad_edges(alt) < _bad_edges(tm):
            tm = alt
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


def swept_offset(parts: list, center, axis, angle_deg: float, radius: float, pitch: float = 0.2,
                 step_deg: float = 3.0) -> Manifold:
    """Volumen barrido por `parts` al girar de 0° a `angle_deg` alrededor de (center, axis), ensanchado `radius` mm.

    Se calcula con un campo de distancia voxelizado (distancia euclídea al sólido en su pose inicial, evaluada en cada
    pose girada) y marching cubes, no con Minkowski ni uniones de copias: esas dos escalan con la cantidad de caras y
    se vuelven inviables (o dejan cientos de miles de microcaras) con piezas de ~200k triángulos. El error del borde es
    del orden de `pitch`/4; la malla resultante es lisa y liviana. Devuelve un Manifold estanco."""
    from scipy import ndimage
    from skimage import measure

    tm = trimesh.util.concatenate([to_trimesh(m) for m in parts])
    c = np.asarray(center, float)
    ax = np.asarray(axis, float)
    ax = ax / np.linalg.norm(ax)
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])

    def rot(a_deg):
        a = np.radians(a_deg)
        return np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * (K @ K)

    n = max(1, int(np.ceil(abs(angle_deg) / step_deg)))
    angles = np.linspace(0.0, angle_deg, n + 1)
    pad = radius + 3 * pitch
    # campo de distancia local (pose inicial)
    vox = tm.voxelized(pitch).fill()
    occ = np.pad(vox.matrix, int(np.ceil(pad / pitch)) + 1)
    origin = np.asarray(vox.transform)[:3, 3] - (int(np.ceil(pad / pitch)) + 1) * pitch
    dist = (ndimage.distance_transform_edt(~occ) * pitch).astype(np.float32)
    # rejilla del mundo: caja de las poses extremas (el giro es continuo: se usan todas las poses)
    pts_all = np.vstack([(tm.vertices - c) @ rot(a).T + c for a in angles])
    lo = pts_all.min(0) - pad
    hi = pts_all.max(0) + pad
    shape = np.ceil((hi - lo) / pitch).astype(int) + 1
    field = np.full(shape, 1e3, np.float32)
    xs = lo[0] + np.arange(shape[0]) * pitch
    ys = lo[1] + np.arange(shape[1]) * pitch
    zs = lo[2] + np.arange(shape[2]) * pitch
    slab = max(1, int(2_000_000 // (shape[1] * shape[2])))
    for i0 in range(0, shape[0], slab):
        xi = xs[i0:i0 + slab]
        G = np.stack(np.meshgrid(xi, ys, zs, indexing="ij"), -1).reshape(-1, 3) - c
        best = np.full(len(G), 1e3, np.float32)
        for a in angles:
            loc = (G @ rot(a)) + c                      # rot(a)^-1 = rot(a).T → G @ R
            idx = ((loc - origin) / pitch).T
            d = ndimage.map_coordinates(dist, idx, order=1, mode="constant", cval=1e3).astype(np.float32)
            np.minimum(best, d, out=best)
        field[i0:i0 + slab] = best.reshape(len(xi), shape[1], shape[2])
    vol = np.pad(radius - field, 1, constant_values=-1.0)
    verts, faces, _n, _v = measure.marching_cubes(vol, 0.0, spacing=(pitch, pitch, pitch))
    verts = verts + lo - pitch
    mesh = trimesh.Trimesh(verts, faces, process=False)
    mesh.merge_vertices()
    if mesh.volume < 0:
        mesh.invert()
    return to_manifold(mesh)


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
