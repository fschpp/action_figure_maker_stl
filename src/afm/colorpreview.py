"""Vista previa a color de una pieza del catálogo que lleva color (`PieceMeta.color`), sin GTK ni OpenGL.

La pieza guarda su fuente de color en el marco de su propio STL: el FBX entero (modo `rgb`) o el paquete de roles + la paleta de la línea
(modo `roles`), y la similitud `A` FBX → STL. Para mostrarla se lleva la fuente a ese marco (`A`), se subdivide un poco la malla de la pieza
para que un triángulo grande no tenga un solo color, se consulta el color en el centro de cada cara y se agrupan las caras por color. El
resultado es una lista `[(nombre, malla, rgba)]` que `PreviewRenderer.render_parts`/`render` dibujan como mallas de color plano.
Es solo una vista previa (el color real se hornea al armar la figura): no se escribe nada en el catálogo.
"""
from __future__ import annotations

import warnings

import numpy as np

SPACING_MM = 0.25            # espaciado de las muestras de color de la fuente (más grueso que al armar: es una vista previa)
MAX_EDGE_MM = 1.2            # se subdividen las aristas más largas que esto
MAX_FACES = 120_000          # tope de caras tras subdividir
MAX_COLORS = 24              # cantidad máxima de colores distintos (una malla por color)
SUBDIV_MAX_ITER = 8          # `subdivide_to_size` levanta ValueError si no llega en tantas pasadas; con aristas de ~19 mm hacen falta 5
ORIGIN_EPS_MM = 0.15         # como el horneado (`bake_piece`): una cara «tiene origen» si la fuente está a esta distancia...
ORIGIN_MIN_DOT = 0.3         # ...y su normal es compatible con la de la cara


def _quantize(rgb: np.ndarray, max_colors: int) -> tuple[np.ndarray, np.ndarray]:
    """(índice de grupo por cara, colores medios (g, 3) uint8): agrupa colores parecidos hasta que no pasen de `max_colors`."""
    rgb = np.asarray(rgb, np.uint8)
    for step in (8, 16, 24, 32, 48, 64, 96, 128):
        key = (rgb // step).astype(np.int64)
        flat = key[:, 0] * 1_000_000 + key[:, 1] * 1_000 + key[:, 2]
        uniq, inv = np.unique(flat, return_inverse=True)
        if len(uniq) <= max_colors:
            break
    means = np.stack([np.bincount(inv, weights=rgb[:, c], minlength=len(uniq)) / np.bincount(inv, minlength=len(uniq)) for c in range(3)], axis=1)
    return inv, np.rint(means).astype(np.uint8)


SAVED = "guardado"           # `retouch=SAVED`: el retoque que la pieza tiene guardado en el catálogo (None = ignorarlo, un `Retouch` = ese, sin guardar)


def _retouch_for(catalog, meta, retouch):
    if retouch is SAVED or (isinstance(retouch, str) and retouch == SAVED):
        return catalog.retouch(meta.ref)
    return retouch


def piece_color_source_tm(catalog, meta, overrides: dict | None = None, retouch=SAVED):
    """`TexturedMesh` de color de la pieza en el marco de su STL, o `None` si no lleva color. `overrides` = `{rol: "#RRGGBB"}`: cambios de la paleta solo
    para esta figura (modo `roles`; en `rgb` no se usan). Levanta `ValueError` (`CatalogError`, `PaletteError`,
    `FbxError`…) si el archivo de color falta, cambió o la paleta no tiene sus roles.

    `retouch` (modo `roles`, R05): por defecto el retoque guardado de la pieza; `None` lo ignora; un `Retouch` (p. ej. el que se está pintando en la
    ventana, sin guardar) se usa tal cual. Los roles que pinta y el paquete no trae se agregan a la LUT; las pinceladas las aplica `color_parts`."""
    from .colorpiece import _as_4x4, _load_roles
    from .parts import color_mode

    if not getattr(meta, "color", None):
        return None
    path = catalog.color_file(meta.ref)
    if path is None:
        return None
    if color_mode(meta.color) == "roles":
        palette = catalog.palette()
        if palette is None:
            raise ValueError(f"la línea no tiene paleta (falta paleta.json): no se puede mostrar el color de {meta.ref}")
        pkg = _load_roles(path)
        rt = _retouch_for(catalog, meta, retouch)
        if rt is not None and len(rt):
            pkg = pkg.with_roles(rt.roles_used())
        tm = pkg.to_textured(palette, overrides or None)
    else:
        from .fbxio import load_textured

        tm = load_textured(path)
    return tm.transformed(_as_4x4(meta.color["similitud"], "similitud FBX → STL"))


def color_parts(tm, mesh, *, spacing_mm: float = SPACING_MM, max_colors: int = MAX_COLORS, prefix: str = "pieza_color", retouch=None) -> list:
    """`[(nombre, malla, rgba)]` con la malla de la pieza partida por color. `tm`: la fuente de color ya en el marco de `mesh`.
    `retouch` (R05): `Retouch` cuyas pinceladas (en el marco de `mesh`) se aplican a la fuente antes de consultar el color; sus roles tienen que
    estar en la LUT de `tm` (los agrega `piece_color_source_tm`)."""
    import trimesh
    from trimesh.remesh import subdivide_to_size

    from .colorsrc import ColorSource

    V, F = np.asarray(mesh.vertices, float), np.asarray(mesh.faces, np.int64)
    try:
        V2, F2 = subdivide_to_size(V, F, max_edge=MAX_EDGE_MM, max_iter=SUBDIV_MAX_ITER)
        if len(F2) <= MAX_FACES:
            V, F = V2, F2
        else:
            warnings.warn(f"vista previa de color: la malla subdividida tendría {len(F2)} caras (tope {MAX_FACES}); se dibuja sin subdividir", stacklevel=2)
    except Exception as exc:                            # noqa: BLE001  (sin subdividir: la vista es más tosca, pero sirve)
        warnings.warn(f"vista previa de color: no se pudo subdividir la malla ({exc}); se dibuja sin subdividir", stacklevel=2)
    tri = V[F]
    centers = tri.mean(axis=1)
    cr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    with np.errstate(invalid="ignore", divide="ignore"):
        normals = np.nan_to_num(cr / np.linalg.norm(cr, axis=1, keepdims=True))
    src = ColorSource(tm, spacing_mm=spacing_mm)
    if retouch is not None and len(retouch):
        src.paint(retouch.strokes)
    q = src.query(centers, normals, eps=ORIGIN_EPS_MM, min_dot=ORIGIN_MIN_DOT)
    rgb = np.array(q.rgb, copy=True)
    ok = np.asarray(q.has_origin, bool)
    assigned = getattr(q, "assigned", None)               # consultas de fuentes sin el campo (p. ej. dobles de prueba): ninguna pintada
    painted = np.zeros(len(ok), bool) if assigned is None else np.asarray(assigned, bool)      # R11: pintadas con «sin origen»: ya tienen color
    todo = ~(ok | painted)
    if ok.any() and todo.any():
        # Misma regla que el horneado: lo que no tiene origen en la fuente (cortes, paredes internas, la cara de un botón que el STL no
        # tiene) no toma el color de «la muestra más cercana, la que sea» (eso pintaba triángulos sueltos de otra capa) sino el de la
        # cara con origen más cercana de la propia pieza. Lo que el retoque «sin origen» pintó se respeta (no se pisa ni sirve de fuente).
        from scipy.spatial import cKDTree

        _, near = cKDTree(centers[ok]).query(centers[todo])
        rgb[todo] = rgb[ok][near]
    elif not ok.any() and not painted.any():            # nada coincide (similitud mala): la muestra más cercana, como antes
        rgb = src.query(centers, eps=1e9).rgb
    inv, means = _quantize(rgb, max_colors)
    parts = []
    for g in range(len(means)):
        sel = F[inv == g]
        if len(sel):
            parts.append((f"{prefix}_{g}", trimesh.Trimesh(V, sel, process=False), tuple(float(x) / 255.0 for x in means[g]) + (1.0,)))
    return parts


def piece_color_parts(catalog, meta, mesh, retouch=SAVED) -> list | None:
    """Partes de color de la vista previa de `meta` (con su `mesh`), o `None` si la pieza no lleva color. `retouch`: ver `piece_color_source_tm`."""
    rt = _retouch_for(catalog, meta, retouch) if getattr(meta, "color", None) else None
    tm = piece_color_source_tm(catalog, meta, retouch=rt)
    return None if tm is None else color_parts(tm, mesh, retouch=rt)


def fit_affine(src, dst, tol_mm: float = 1e-3):
    """4×4 afín (giro, traslado, escala o espejo) que lleva los vértices `src` sobre `dst` (mismo orden), o `None` si no existe: otra cantidad de
    vértices o un residuo mayor que `tol_mm` (la pieza se deformó, p. ej. por una operación que rehízo la malla)."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    if src.shape != dst.shape or src.ndim != 2 or len(src) < 4:
        return None
    X, *_ = np.linalg.lstsq(np.column_stack([src, np.ones(len(src))]), dst, rcond=None)
    M = np.eye(4)
    M[:3, :] = X.T
    res = np.abs(np.column_stack([src, np.ones(len(src))]) @ X - dst).max()
    return M if res <= tol_mm and abs(np.linalg.det(M[:3, :3])) > 1e-9 else None
