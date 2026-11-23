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


EDGE_LIGHT = (1.0, 1.0, 1.0, 1.0)      # arista clara: contrasta con un color oscuro
EDGE_DARK = (0.03, 0.03, 0.05, 1.0)    # arista oscura: contrasta con un color claro
EDGE_MAX = 200_000                      # tope de aristas que se dibujan (más que esto la malla ya es una mancha: no se dibujan)
EDGE_SHADE = 0.75                       # el visor oscurece el color plano (luz ambiente + 2 luces): el contraste se mide sobre el color ya sombreado
EDGE_LIGHT_BELOW = 0.179                # luminancia lineal por debajo de la cual el blanco contrasta más que el negro (cruce de las razones de contraste WCAG)


def _linear_luminance(rgb) -> np.ndarray:
    """Luminancia relativa (0-1, WCAG) de colores sRGB `(..., 3)` con canales 0-1."""
    c = np.clip(np.asarray(rgb, float), 0.0, 1.0)
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return lin @ np.array([0.2126, 0.7152, 0.0722])


def face_rgb_from_parts(mesh, color_parts, default_rgba=(0.80, 0.82, 0.86, 1.0)) -> np.ndarray:
    """Color `(F, 3)` (0-1) de cada cara de `mesh` según `color_parts` (`color_parts`: `[(nombre, malla, rgba)]`, la pieza subdividida y partida por
    color). Cada cara toma el color de la cara de las partes cuyo centro queda más cerca de su centro. Sin partes, todas toman `default_rgba`."""
    F = len(mesh.faces)
    if not color_parts:
        return np.tile(np.asarray(default_rgba, float)[:3], (F, 1))
    from scipy.spatial import cKDTree

    centers, colors = [], []
    for _name, m, rgba in color_parts:
        if len(m.faces) == 0:
            continue
        centers.append(np.asarray(m.triangles_center, float))
        colors.append(np.tile(np.asarray(rgba, float)[:3], (len(m.faces), 1)))
    if not centers:
        return np.tile(np.asarray(default_rgba, float)[:3], (F, 1))
    _d, near = cKDTree(np.vstack(centers)).query(np.asarray(mesh.triangles_center, float))
    return np.vstack(colors)[near]


def edge_segments_by_contrast(mesh, face_rgb=None) -> tuple[np.ndarray, np.ndarray]:
    """Aristas de `mesh` (sin repetir, las de los polígonos de la pieza; no las de la subdivisión de la vista previa) como segmentos `(E, 2, 3)`,
    repartidas en `(claras, oscuras)`: cada arista va clara si el color de las caras que toca es oscuro y oscura si es claro, así que se ve
    sobre CUALQUIER color con el que se pinte (negro, rojo oscuro, blanco…) y la forma no se pierde. `face_rgb` `(F, 3)`: el color de cada cara
    (`face_rgb_from_parts`); sin él se supone el gris claro de la pieza. Una arista entre dos colores usa el promedio de los dos lados.
    Con más de `EDGE_MAX` aristas devuelve dos arreglos vacíos."""
    empty = np.zeros((0, 2, 3))
    if len(mesh.faces) == 0:
        return empty, empty
    edges = np.asarray(mesh.edges_unique, np.int64)
    if len(edges) > EDGE_MAX:
        return empty, empty
    rgb = face_rgb_from_parts(mesh, None) if face_rgb is None else np.asarray(face_rgb, float)
    if rgb.shape != (len(mesh.faces), 3):
        raise ValueError(f"face_rgb debe ser (F, 3) = {(len(mesh.faces), 3)}, no {rgb.shape}")
    lum = _linear_luminance(rgb * EDGE_SHADE)
    fue = np.asarray(mesh.faces_unique_edges, np.int64)             # (F, 3): índice de arista de cada lado de cada cara
    total = np.zeros(len(edges))
    count = np.zeros(len(edges))
    np.add.at(total, fue.ravel(), np.repeat(lum, fue.shape[1]))
    np.add.at(count, fue.ravel(), 1.0)
    dark_color = total / np.maximum(count, 1.0) < EDGE_LIGHT_BELOW
    seg = np.asarray(mesh.vertices, float)[edges]
    return seg[dark_color & (count > 0)], seg[~dark_color & (count > 0)]


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
