"""Cabeza articulada: corte por el plano del cuello + pasador tipo hongo (como los hombros).

Tutorial de Onshape, pasos 001–004 (plano «cabeza cuerpo» por 3 puntos, intersección) para el corte, y
pasos 030–033 (vástago + cabeza del hongo, cavidad con holgura) para la articulación. Aquí el plano del
cuello hace de herramienta única: lo que queda del lado de la cabeza es la cabeza; el resto, el cuerpo.

Convenciones (marco de trabajo: plano de simetría en x = 0, Z arriba):

* El plano del cuello se simetriza: su normal se proyecta sobre x = 0 (la cabeza es simétrica) y apunta
  hacia arriba. Así la cara del corte es simétrica y el pasador queda centrado en x = 0.
* La cabeza es UNA sola pieza (a diferencia de brazos y piernas no hay «izquierda/derecha»): se corta del
  cuerpo ENTERO (así la cara del cuello es un único polígono y no hay costura en x = 0). Solo si hay
  recortes manuales sobre la cabeza se trabaja la mitad de trabajo y se espeja. El pasador se agrega a la entera.
* Entre la cara del corte y el torso queda `clearance` mm de aire; el pasador entra en una cavidad con
  la misma holgura (dilatación del pasador), igual que en los hombros.

Cabeza de reemplazo (`HeadSpec.replacement_path`): se descarta la cabeza original y se pone otra malla (STL).
La original solo sirve para medir la BASE (la cara del corte en el cuello). La nueva se escala UNIFORMEMENTE
(sin deformarla) hasta que su base tenga el mismo área que la original, se apoya sobre el plano con el centroide
de su base sobre el de la original y se usa tal cual: no se simetriza ni se espeja nada. El pasador se calcula
sobre la base de la cabeza nueva.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass

import numpy as np

from . import solids as S
from .limbs import ContactFace, _face_shape, pin_fit
from .params import Params
from .pins import mushroom_pin
from .planes import Plane

log = logging.getLogger(__name__)

HEAD_NAME = "cabeza"
REPLACEMENT_UPS = ("+Z", "-Z", "+Y", "-Y", "+X", "-X")   # eje de la malla importada que apunta hacia arriba
BASE_SLICE_FRAC = 0.03    # si la cabeza nueva no trae base plana, se corta a esta fracción de su alto
MIN_BASE_IOU = 0.80       # por debajo, la base nueva se parece poco a la del cuello: aviso
MIN_UP = 0.3          # |n·Z| mínimo del plano del cuello (un plano casi vertical no separa cabeza de cuerpo)
MAX_SIDE = 0.5        # |n·X| máximo antes de simetrizar (el plano tiene que contener el eje izquierda-derecha)
REPLACEMENT_SCALE_RANGE = (0.2, 5.0)   # factor manual sobre la escala automática
REPLACEMENT_CUT_RANGE = (0.0, 60.0)    # % del alto de la malla que se puede cortar desde la base


@dataclass
class HeadSpec:
    """Cabeza: plano del cuello (3 puntos) y, opcionalmente, centro del pasador.

    La cabeza gira libre sobre el cuello (giro «no»): el pasador es un cilindro cuyo eje pasa por el centro de
    una cara plana perpendicular a él, así que ningún ángulo de giro la acerca al cuerpo; no hace falta barrido."""

    plane: Plane
    name: str = HEAD_NAME
    pin_center: tuple | None = None       # centro del pasador (mundo); None = círculo inscrito mayor de la cara
    auto: bool = True
    replacement_path: str | None = None   # STL de la cabeza de reemplazo; None = la cabeza original del modelo
    replacement_yaw: float = 0.0          # giro (°) sobre el eje vertical de la cabeza importada (+ = antihorario)
    replacement_up: str = "+Z"            # eje de la malla importada que apunta hacia arriba
    replacement_rx: float = 0.0           # giro fino (°) de la malla importada sobre X, tras el eje «arriba»
    replacement_ry: float = 0.0           # idem sobre Y
    replacement_scale: float = 1.0        # factor manual sobre la escala automática (1 = base igual a la del cuello)
    replacement_cut: float = 0.0          # % del alto que se corta desde abajo con un plano horizontal (0 = automático)

    def to_dict(self) -> dict:
        return {"name": self.name, "plane": self.plane.to_dict(),
                "pin_center": None if self.pin_center is None else list(self.pin_center),
                "auto": self.auto, "replacement_path": self.replacement_path,
                "replacement_yaw": self.replacement_yaw, "replacement_up": self.replacement_up,
                "replacement_rx": self.replacement_rx, "replacement_ry": self.replacement_ry,
                "replacement_scale": self.replacement_scale, "replacement_cut": self.replacement_cut}

    @classmethod
    def from_dict(cls, d: dict) -> "HeadSpec":
        return cls(plane=Plane.from_dict(d["plane"]), name=d.get("name", HEAD_NAME),
                   pin_center=None if d.get("pin_center") is None else tuple(d["pin_center"]),
                   auto=d.get("auto", True), replacement_path=d.get("replacement_path"),
                   replacement_yaw=float(d.get("replacement_yaw", 0.0)),
                   replacement_up=d.get("replacement_up", "+Z"),
                   replacement_rx=float(d.get("replacement_rx", 0.0)),
                   replacement_ry=float(d.get("replacement_ry", 0.0)),
                   replacement_scale=float(d.get("replacement_scale", 1.0)),
                   replacement_cut=float(d.get("replacement_cut", 0.0)))


@dataclass
class HeadSplit:
    head: S.Manifold             # cabeza entera, sin pasador
    body_half: S.Manifold        # mitad de trabajo (x >= 0) del cuerpo, ya con la holgura del cuello
    plane: Plane                 # plano del cuello simetrizado, normal hacia la cabeza
    face: ContactFace            # cara del corte (la de la cabeza que se usa), normal hacia el cuerpo
    replacement: dict | None = None   # datos del ajuste de la cabeza de reemplazo (escala, coincidencia de base…)


@dataclass
class HeadBuild:
    head: S.Manifold             # cabeza entera con su pasador (pieza final)
    pocket: S.Manifold           # cavidad (pasador dilatado por la holgura) que se resta del cuerpo
    face: ContactFace            # center = centro del pasador
    axis: np.ndarray             # eje del cuello (hacia la cabeza): eje de giro
    pin_margin: float | None


def neck_plane(spec: HeadSpec) -> Plane:
    """Plano del cuello simetrizado: normal en el plano YZ y apuntando hacia arriba, origen en x = 0."""
    n = np.asarray(spec.plane.normal, float)
    n = n / np.linalg.norm(n)
    if n[2] < 0:
        n = -n
    if n[2] < MIN_UP:
        raise ValueError(f"el plano del cuello está casi vertical (|n·Z| = {abs(n[2]):.2f} < {MIN_UP}): "
                         "elegí 2 puntos en la nuca y 1 bajo el mentón")
    if abs(n[0]) > MAX_SIDE:
        raise ValueError("el plano del cuello está girado de costado: tiene que contener la dirección "
                         "izquierda–derecha (2 puntos a cada lado de la nuca, 1 bajo el mentón)")
    n = np.array([0.0, n[1], n[2]])
    n /= np.linalg.norm(n)
    o = np.asarray(spec.plane.origin, float).copy()
    o[0] = 0.0
    return Plane.from_normal(o, n, spec.name)


def _plane_faces(mf: S.Manifold, plane: Plane, tol: float = 1e-3) -> np.ndarray:
    """Triángulos (n,3,3) de `mf` que yacen sobre `plane` (los 3 vértices a menos de `tol`)."""
    tm = S.to_trimesh(mf)
    tri = tm.triangles
    d = np.abs((tri.reshape(-1, 3) - plane.origin) @ plane.normal).reshape(-1, 3)
    return tri[(d < tol).all(axis=1)]


def _face_from_head(head: S.Manifold, plane: Plane) -> ContactFace:
    """Cara del corte de `head` sobre `plane` (normal hacia el cuerpo, centro = centroide con x = 0)."""
    tri = _plane_faces(head, plane)
    if len(tri) == 0:
        raise ValueError("el plano del cuello no atraviesa el cuello (la cara del corte quedó vacía)")
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area_f = 0.5 * np.linalg.norm(cross, axis=1)
    centroid = (tri.mean(axis=1) * area_f[:, None]).sum(axis=0) / area_f.sum()
    centroid[0] = 0.0
    return ContactFace(center=centroid, outward=-plane.normal.copy(), area=float(area_f.sum()), triangles=tri)


def split_head(body: S.Manifold, spec: HeadSpec, p: Params) -> HeadSplit:
    """Corta el cuerpo entero por el plano del cuello. El cuerpo se baja `clearance` mm (holgura).

    Con `spec.replacement_path` la cabeza original se descarta y se devuelve la importada, ya escalada y
    colocada sobre el plano (la original solo aporta la base a igualar)."""
    plane = neck_plane(spec)
    head = S.largest_component(S.intersection(body, S.halfspace(plane)))
    if head.is_empty() or head.volume() < 1.0:
        raise ValueError("el plano del cuello no corta ninguna cabeza (¿está por encima de la figura?)")
    below = Plane.from_normal(plane.origin - plane.normal * p.clearance, -plane.normal)
    body_half = S.intersection(body, S.halfspace(below)).trim_by_plane((1.0, 0.0, 0.0), 0.0)
    if body_half.is_empty():
        raise ValueError("el plano del cuello deja la figura sin cuerpo (¿está por debajo de los pies?)")
    face = _face_from_head(head, plane)
    info = None
    if spec.replacement_path:
        head, info = fit_replacement(spec, plane, face)      # descarta la cabeza original
        face = _face_from_head(head, plane)
    return HeadSplit(head, body_half, plane, face, info)


# ------------------------------------------------------------- cabeza de reemplazo
_UP_ROTATIONS = {            # rotación que lleva el eje «arriba» de la malla importada a +Z
    "+Z": np.eye(3),
    "-Z": np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], float),
    "+Y": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float),
    "-Y": np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float),
    "+X": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], float),
    "-X": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], float),
}


_REPAIR_CACHE: dict = {}
REPLACEMENT_VOXEL_RES = 160     # remallado por voxelización si la cabeza importada no es estanca (el cuerpo usa 700: pide GB de RAM)


def _repaired(path: str, pre: float, mesh):
    """Malla reparada de la cabeza importada, en caché por (archivo, fecha, tamaño).

    Reparar puede remallar por voxelización (lento y con mucha RAM); girar o cambiar el eje «arriba» no cambia la
    reparación, así que se hace una sola vez por archivo. Una cabeza no necesita la resolución de 700 del cuerpo."""
    import os

    from . import meshio

    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    hit = _REPAIR_CACHE.get(key)
    if hit is None:
        _REPAIR_CACHE.clear()                      # una sola cabeza en memoria
        hit = _REPAIR_CACHE[key] = meshio.repair(mesh, voxel_res=REPLACEMENT_VOXEL_RES)
    return hit.copy()


def load_replacement(path: str, up: str = "+Z", yaw_deg: float = 0.0, rx_deg: float = 0.0, ry_deg: float = 0.0):
    """Carga y repara el STL de la cabeza importada y la deja con Z arriba (según `up`) y girada.

    Orden de giros: primero el eje «arriba» (`up`), luego `rx_deg` sobre X, `ry_deg` sobre Y y por último
    `yaw_deg` sobre Z (con rx = ry = 0 es exactamente el comportamiento anterior). No se centra ni se escala
    (eso lo hace `fit_replacement`). Se pre-escala a ~30 mm solo para que las tolerancias absolutas de la
    reparación sean razonables. Devuelve (malla, pre_escala): la malla ya multiplicada por `pre_escala`
    respecto de las unidades del archivo."""
    import trimesh

    from . import meshio

    if up not in _UP_ROTATIONS:
        raise ValueError(f"replacement_up debe ser uno de {REPLACEMENT_UPS}, no {up!r}")
    mesh = trimesh.load(path, force="mesh")
    if len(mesh.faces) == 0:
        raise ValueError(f"«{path}» no tiene triángulos")
    mesh = mesh.copy()
    ext = float(mesh.extents.max())
    if ext <= 0:
        raise ValueError(f"«{path}» tiene tamaño cero")
    pre = 30.0 / ext
    mesh.apply_scale(pre)
    mesh = _repaired(path, pre, mesh)
    a, b, c = np.radians(yaw_deg), np.radians(ry_deg), np.radians(rx_deg)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    Ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(c), -np.sin(c)], [0, np.sin(c), np.cos(c)]])
    T = np.eye(4)
    T[:3, :3] = Rz @ Ry @ Rx @ _UP_ROTATIONS[up]
    mesh = mesh.copy()
    mesh.apply_transform(T)
    return mesh, float(pre)


def _section_shape(mf: S.Manifold, z: float):
    """Sección horizontal de `mf` a la cota z como geometría shapely (relleno par-impar: respeta agujeros)."""
    from functools import reduce

    from shapely.geometry import Polygon

    rings = [Polygon(r).buffer(0) for r in mf.slice(z).to_polygons() if len(r) >= 3]
    if not rings:
        return Polygon()
    return reduce(lambda a, b: a.symmetric_difference(b), rings)


def _flat_bottom_area(mesh, z0: float, tol: float) -> float:
    """Área de las caras que miran hacia abajo y yacen sobre el plano z = z0 (base plana de la malla)."""
    tri = mesh.triangles
    flat = (mesh.face_normals[:, 2] < -0.999) & (np.abs(tri[:, :, 2] - z0).max(axis=1) < tol)
    return float(mesh.area_faces[flat].sum())


def fit_replacement(spec: HeadSpec, plane: Plane, face: ContactFace):
    """Escala y coloca la cabeza importada sobre el plano del cuello.

    Devuelve (cabeza como Manifold con su base sobre `plane`, info). Escala uniforme k tal que el área de su
    base == área de `face`; el centroide de la base nueva va al de la original; la «cara» de la cabeza
    importada mira hacia +v del plano (hacia adelante, +Y en un plano horizontal), como el frente de la figura.
    No se espeja ni simetriza nada. Si la malla no trae base plana se corta una fracción baja de su alto
    (`BASE_SLICE_FRAC`) para tener una cara plana donde apoyar y poner el pasador."""
    lo_s, hi_s = REPLACEMENT_SCALE_RANGE
    lo_c, hi_c = REPLACEMENT_CUT_RANGE
    if not (lo_s <= spec.replacement_scale <= hi_s):
        raise ValueError(f"la escala de la cabeza tiene que estar entre ×{lo_s:g} y ×{hi_s:g}")
    if not (lo_c <= spec.replacement_cut <= hi_c):
        raise ValueError(f"el corte de la base tiene que estar entre {lo_c:g} y {hi_c:g} % del alto")
    mesh, pre = load_replacement(spec.replacement_path, spec.replacement_up, spec.replacement_yaw,
                                 spec.replacement_rx, spec.replacement_ry)
    mf = S.to_manifold(mesh)
    z0 = float(mesh.bounds[0, 2])
    height = float(mesh.extents[2])
    tol = 1e-3 * height
    if spec.replacement_cut > 0:                              # corte pedido: plano horizontal sobre la base
        z_base, cut = z0 + spec.replacement_cut / 100.0 * height, True
        base = _section_shape(mf, z_base + 2 * tol)
    else:
        sec_f = _section_shape(mf, z0 + BASE_SLICE_FRAC * height)
        flat_area = _flat_bottom_area(mesh, z0, tol)
        if flat_area >= 0.85 * sec_f.area and flat_area > 0:
            z_base, cut = z0, False                          # ya trae una base plana: se usa entera
            base = _section_shape(mf, z0 + 2 * tol)
        else:
            z_base, cut = z0 + BASE_SLICE_FRAC * height, True
            base = sec_f
    if base.is_empty or base.area <= 1e-9:
        raise ValueError("no se pudo medir la base de la cabeza importada (¿está vacía o al revés? probá otro «arriba»)")
    orig = _face_shape_in(plane, face)
    k_auto = float(np.sqrt(orig.area / base.area))
    k = k_auto * float(spec.replacement_scale)          # la escala manual multiplica el ajuste automático
    c_new, c_old = np.array(base.centroid.coords[0]), np.array(orig.centroid.coords[0])
    # local (malla) -> plano: centroide de la base al de la original, base sobre el plano, escala k
    A = np.eye(4)
    A[:3, :3] *= k
    A[:3, 3] = [-c_new[0] * k + c_old[0], -c_new[1] * k + c_old[1], -z_base * k]
    M = plane.matrix() @ A
    placed = mf.transform(M[:3, :])
    if cut:
        placed = S.intersection(placed, S.halfspace(plane))   # quita lo que queda bajo la base
    parts = placed.decompose()
    placed = S.union(*[q for q in parts if q.volume() > 1e-3 * max(r.volume() for r in parts)]) \
        if len(parts) > 1 else placed
    if placed.is_empty() or placed.volume() < 1.0:
        raise ValueError("la cabeza importada quedó vacía al apoyarla sobre el cuello")
    from shapely import affinity
    moved = affinity.translate(affinity.scale(base, k, k, origin=(0, 0)),
                               c_old[0] - c_new[0] * k, c_old[1] - c_new[1] * k)
    iou = float(moved.intersection(orig).area / max(moved.union(orig).area, 1e-12))
    bounds = placed.bounding_box()
    # "escala" es respecto de las unidades del archivo importado (k es respecto de la malla ya pre-escalada)
    info = {"escala": k * pre, "base_igualada_mm2": float(orig.area), "coincidencia_base": iou,
            "base_cortada": bool(cut), "alto_mm": float((height - (z_base - z0)) * k),
            "escala_manual": float(spec.replacement_scale), "corte_pct": float(spec.replacement_cut),
            "volumen_mm3": float(placed.volume()), "bbox": [list(map(float, b)) for b in
                                                           (bounds[:3], bounds[3:])]}
    if iou < MIN_BASE_IOU:
        log.warning("La base de la cabeza importada coincide solo %.0f %% con la del cuello", 100 * iou)
    return placed, info


def _face_shape_in(plane: Plane, face: ContactFace):
    """Huella de `face` como geometría shapely en las coordenadas (u, v) de `plane` (la del cuello)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polys = [Polygon(plane.to_uv(t)).buffer(0) for t in face.triangles]
    return unary_union([q for q in polys if q.area > 1e-12])


def propose_pin_center(face: ContactFace) -> np.ndarray:
    """Centro del mayor círculo inscrito en la cara del corte, sobre x = 0."""
    from shapely.ops import polylabel

    plane, shape = _face_shape(face)
    geoms = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
    big = max(geoms, key=lambda g: g.area)
    pole = polylabel(big, tolerance=0.01)
    c = plane.to_world(np.array([[pole.x, pole.y]]))[0]
    c[0] = 0.0          # el plano contiene al eje X: seguir en x = 0 no lo saca de la cara
    return c


def build_head(split: HeadSplit, spec: HeadSpec, p: Params, trims=None) -> HeadBuild:
    """Cabeza entera con pasador + cavidad (el pasador dilatado por la holgura, como en los hombros)."""
    from .limbs import pin_margin
    from .trims import apply_trims

    mine = [t for t in (trims or []) if t.target == spec.name]
    whole = split.head
    if mine and spec.replacement_path:   # cabeza importada: sin simetría, los recortes se aplican tal cual
        from .trims import apply_trims as _apply
        whole = _apply(whole, mine, spec.name)
    elif mine:     # los recortes se dibujan en el lado de trabajo: se aplican a la mitad y se espeja
        half = apply_trims(whole.trim_by_plane((1.0, 0.0, 0.0), 0.0), mine, spec.name)
        whole = S.union(half, S.mirror_x(half))
    if spec.pin_center is not None:
        c = np.asarray(spec.pin_center, float)
        c = c - ((c - split.plane.origin) @ split.plane.normal) * split.plane.normal   # sobre el plano
        c[0] = 0.0
    elif spec.auto:
        c = propose_pin_center(split.face)
    else:
        c = split.face.center
    face = dataclasses.replace(split.face, center=c)
    pin = mushroom_pin(c, face.outward, p.neck_pin)
    return HeadBuild(head=S.union(whole, pin), pocket=S.dilate(pin, p.clearance), face=face,
                     axis=split.plane.normal.copy(), pin_margin=pin_margin(face, p.neck_pin.shaft_d))


def head_fit(split: HeadSplit, uv, shaft_d: float) -> tuple[bool, float]:
    """(centro dentro de la cara, margen vástago–borde) para un centro (u, v) sobre el plano de la cara."""
    _plane, shape = _face_shape(split.face)
    return pin_fit(shape, uv, shaft_d)
