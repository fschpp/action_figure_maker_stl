"""Comparador de proporciones: la figura abierta (en piezas, con sus escalas por pieza) contra el cuerpo de una figura patrón del
catálogo (p. ej. Güemes), en tres vistas, para ver si el escalado por pieza (`part_scales`) va logrando el cuerpo del patrón.

Núcleo sin GTK: junta las mallas de cada lado (`model_meshes`, `reference_meshes`), las lleva a un mismo marco (`normalize`: pies en
z = 0, ancho centrado en x = 0, torso alineado en Y), mide cada pieza (`compare`: alto, ancho y fondo, la razón patrón / modelo y
la escala sugerida) y dibuja siluetas por pieza (`render_view`: imagen RGBA lista para cairo). La ventana (`gui/proportions_window`)
solo muestra y llama a `Controller.set_part_scale`.

Las medidas son las de las cajas de cada pieza ya colocada: sirven para igualar largos y anchos, no la forma (ver «Igualar medidas
no garantiza igual forma» en docs/ajustes_piezas.md)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PLACES = ("cabeza", "torso", "brazo", "pelvis", "pierna")          # de arriba a abajo, el orden de la tabla
LABELS = {"cabeza": "Cabeza", "torso": "Torso", "brazo": "Brazo", "pelvis": "Pelvis", "pierna": "Pierna"}
PLACE_OF_TIPO = {"torso": "torso", "torso_sup": "torso", "pelvis": "pelvis", "cabeza": "cabeza", "brazo": "brazo", "pierna": "pierna"}
ONE_SIDED = ("brazo", "pierna")                                    # sale un solo lado (+X) y se espeja para dibujar la figura entera
VIEWS = {"frente": (0, 2), "costado": (1, 2), "arriba": (0, 1)}    # ejes (horizontal, vertical) de cada vista
AXES = ("X", "Y", "Z")                                             # ancho, fondo, alto
OK_TOL, WARN_TOL = 0.03, 0.08                                      # diferencia relativa: ≤3 % bien, ≤8 % aviso, más: no
PALETTE = {"cabeza": (240, 160, 60), "torso": (70, 130, 200), "brazo": (60, 170, 110), "pelvis": (150, 110, 190), "pierna": (210, 90, 90)}


# --------------------------------------------------------------------------------------------- reunir las mallas
def catalog_figures(cat) -> dict[str, dict[str, str]]:
    """Figuras del catálogo con su cuerpo: `{figura: {lugar: ref}}` (última versión de cada pieza de ese origen)."""
    out: dict[str, dict[str, str]] = {}
    for meta in cat.list():
        place = PLACE_OF_TIPO.get(meta.tipo)
        fig = (meta.origen or {}).get("figura") or ""
        if place is None or not fig:
            continue
        out.setdefault(fig, {})[place] = meta.ref
    return out


def reference_meshes(cat, figura: str) -> dict:
    """Las mallas de la figura patrón, tal cual están en el catálogo: cada pieza conserva el marco de su figura de origen, así que
    juntas ya forman el cuerpo armado. Un `torso` entero cuenta como torso (y sin pelvis aparte)."""
    refs = catalog_figures(cat).get(figura)
    if not refs:
        raise ValueError(f"el catálogo no tiene piezas de la figura «{figura}»")
    return {place: cat.load(ref)[1] for place, ref in refs.items()}


def model_meshes(items: dict, project) -> dict:
    """Las piezas de la figura abierta (ya escaladas y colocadas, `PartPreview.mesh`) con el nombre de lugar del comparador:
    los miembros del modelo (`brazo`, `pierna`, o como se llamen) pasan a «brazo» / «pierna»."""
    kind_place = {"arm": "brazo", "leg": "pierna"}
    names = {l.name: kind_place[l.kind] for l in project.limbs}
    out = {}
    for name, pv in items.items():
        place = names.get(name, name)
        if place in PLACES and getattr(pv, "mesh", None) is not None:
            out[place] = pv.mesh
    return out


def _is_one_sided(place: str, mesh) -> bool:
    return place in ONE_SIDED and float(mesh.bounds[0][0]) > -1e-3


def full_points(place: str, mesh, n: int, seed: int = 0) -> np.ndarray:
    """Puntos sobre la superficie de la pieza; los miembros de un solo lado suman su espejo en X."""
    tri, area = mesh.triangles, mesh.area_faces
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(tri), n, p=area / area.sum())
    u = rng.random((n, 2))
    flip = u.sum(1) > 1
    u[flip] = 1 - u[flip]
    a, b, c = tri[idx][:, 0], tri[idx][:, 1], tri[idx][:, 2]
    pts = a + u[:, :1] * (b - a) + u[:, 1:] * (c - a)
    if _is_one_sided(place, mesh):
        mir = pts.copy()
        mir[:, 0] *= -1.0
        pts = np.vstack([pts, mir])
    return pts


def harmonize(model: dict, ref: dict) -> tuple[dict, dict, str | None]:
    """Si un lado trae el torso entero y el otro lo trae partido en torso + pelvis, suma la pelvis al torso del que la tiene, para
    comparar lo mismo con lo mismo. Devuelve (modelo, patrón, nota) con copias de los diccionarios (nota None si no hizo falta)."""
    import trimesh

    model, ref = dict(model), dict(ref)
    if ("pelvis" in model) == ("pelvis" in ref) or "torso" not in model or "torso" not in ref:
        return model, ref, None
    side, name = (model, "tu figura") if "pelvis" in model else (ref, "el patrón")
    side["torso"] = trimesh.util.concatenate([side["torso"], side.pop("pelvis")])
    other = "el patrón" if name == "tu figura" else "tu figura"
    return model, ref, f"{other.capitalize()} trae el torso entero: la pelvis de {name} se suma a su torso para comparar."


# --------------------------------------------------------------------------------------------- un marco común
def normalize(meshes: dict) -> dict:
    """Copia de las mallas en un marco común: los pies en z = 0 y el torso con el centro de su caja en y = 0 (x queda: la simetría
    es x = 0 en las dos figuras)."""
    if not meshes:
        return {}
    zmin = min(float(m.bounds[0][2]) for m in meshes.values())
    ref = meshes.get("torso")
    y0 = 0.5 * float(ref.bounds[0][1] + ref.bounds[1][1]) if ref is not None else 0.0
    out = {}
    for place, mesh in meshes.items():
        m = mesh.copy()
        m.apply_translation((0.0, -y0, -zmin))
        out[place] = m
    return out


# --------------------------------------------------------------------------------------------- medir y comparar
@dataclass
class Dims:
    size: tuple[float, float, float]          # ancho X, fondo Y, alto Z (mm); el ancho de un miembro es el de UN lado
    zmin: float
    zmax: float


def dims(meshes: dict) -> dict[str, Dims]:
    out = {}
    for place, m in meshes.items():
        lo, hi = m.bounds
        out[place] = Dims(tuple(float(v) for v in (hi - lo)), float(lo[2]), float(hi[2]))
    return out


@dataclass
class Row:
    place: str
    model: Dims | None
    ref: Dims | None
    current: tuple[float, float, float]       # escala actual de la pieza del modelo (X, Y, Z)
    ratio: tuple[float, float, float] | None  # patrón / modelo en cada eje (None si falta una de las dos piezas)
    suggested: tuple[float, float, float] | None   # escala que igualaría las medidas: actual × razón
    nivel: str                                # "ok" | "aviso" | "no" | "sin_dato"

    @property
    def label(self) -> str:
        return LABELS.get(self.place, self.place)

    def text(self) -> str:
        if self.ratio is None:
            return "sin pieza para comparar"
        parts = []
        for ax, r in zip(AXES, self.ratio):
            d = 100.0 * (1.0 - r)                # + = el modelo es más grande que el patrón
            parts.append(f"{ax} {'+' if d > 0 else ''}{d:.0f} %" if abs(d) >= 0.5 else f"{ax} igual")
        return "modelo vs patrón: " + ", ".join(parts)


def _level(ratio) -> str:
    worst = max(abs(r - 1.0) for r in ratio)
    return "ok" if worst <= OK_TOL else "aviso" if worst <= WARN_TOL else "no"


def compare(model: dict, ref: dict, scales: dict | None = None) -> list[Row]:
    """La tabla del comparador (una fila por pieza, de la cabeza a la pierna) con la escala sugerida: la actual × (patrón / modelo)."""
    scales = scales or {}
    dm, dr = dims(model), dims(ref)
    rows = []
    for place in PLACES:
        cur = _vec(scales.get(place, 1.0))
        m, r = dm.get(place), dr.get(place)
        if m is None or r is None:
            rows.append(Row(place, m, r, cur, None, None, "sin_dato"))
            continue
        ratio = tuple(r.size[i] / m.size[i] if m.size[i] > 1e-6 else 1.0 for i in range(3))
        sug = tuple(round(cur[i] * ratio[i], 4) for i in range(3))
        rows.append(Row(place, m, r, cur, ratio, sug, _level(ratio)))
    return rows


def _vec(k) -> tuple[float, float, float]:
    if isinstance(k, (int, float)):
        return (float(k),) * 3
    a = [float(x) for x in k]
    return tuple(a) if len(a) == 3 else (a[0],) * 3


def landmarks(ref_dims: dict) -> list[tuple[str, float]]:
    """Alturas de referencia del patrón para las guías horizontales: coronilla, hombro, cintura, entrepierna y pies."""
    out = []
    if "cabeza" in ref_dims:
        out.append(("coronilla", ref_dims["cabeza"].zmax))
    elif "torso" in ref_dims:
        out.append(("coronilla", ref_dims["torso"].zmax))
    if "brazo" in ref_dims:
        out.append(("hombro", ref_dims["brazo"].zmax))
    if "torso" in ref_dims:
        out.append(("cintura", ref_dims["torso"].zmin))
    if "pelvis" in ref_dims:
        out.append(("entrepierna", ref_dims["pelvis"].zmin))
    out.append(("pies", 0.0))
    seen, uniq = set(), []
    for name, z in out:
        key = round(z, 1)
        if key not in seen:
            seen.add(key)
            uniq.append((name, float(z)))
    return uniq


# --------------------------------------------------------------------------------------------- dibujar
def view_bounds(a: dict, b: dict, view: str, margin: float = 4.0) -> tuple[float, float, float, float]:
    """Caja (h0, v0, h1, v1) en mm que cubre las dos figuras en esa vista, para que los dos lados se dibujen a la misma escala."""
    ih, iv = VIEWS[view]
    lo, hi = [np.inf, np.inf], [-np.inf, -np.inf]
    for meshes in (a, b):
        for place, m in meshes.items():
            for j, i in enumerate((ih, iv)):
                l, h = float(m.bounds[0][i]), float(m.bounds[1][i])
                if i == 0 and _is_one_sided(place, m):
                    l = -h
                lo[j], hi[j] = min(lo[j], l), max(hi[j], h)
    if not np.isfinite(lo[0]):
        return (0.0, 0.0, 1.0, 1.0)
    return (lo[0] - margin, lo[1] - margin, hi[0] + margin, hi[1] + margin)


def render_view(meshes: dict, view: str, bounds, px: float = 0.5, alpha: int = 255, only: set | None = None, seed: int = 0) -> np.ndarray:
    """Silueta de las piezas en una vista: imagen RGBA (alto × ancho × 4, fila 0 = arriba), un color por pieza; las de abajo de la lista
    se dibujan primero. Cubre `bounds` (h0, v0, h1, v1) en mm a `px` mm por píxel."""
    ih, iv = VIEWS[view]
    h0, v0, h1, v1 = bounds
    w, h = max(int(np.ceil((h1 - h0) / px)), 1), max(int(np.ceil((v1 - v0) / px)), 1)
    img = np.zeros((h, w, 4), np.uint8)
    for place in reversed(PLACES):
        m = meshes.get(place)
        if m is None or (only is not None and place not in only):
            continue
        n = int(np.clip(m.area / (px * px) * 5.0, 3000, 120000))
        pts = full_points(place, m, n, seed)
        ix = np.floor((pts[:, ih] - h0) / px).astype(int)
        iy = h - 1 - np.floor((pts[:, iv] - v0) / px).astype(int)
        ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        mask = np.zeros((h, w), bool)
        mask[iy[ok], ix[ok]] = True
        mask = _close(mask)
        img[mask] = (*PALETTE[place], alpha)
    return img


def _close(mask: np.ndarray) -> np.ndarray:
    """Cierra los huecos entre muestras (dilatar 1 px y erosionar 1 px): la silueta queda llena."""
    def dil(m):
        o = m.copy()
        o[1:] |= m[:-1]; o[:-1] |= m[1:]; o[:, 1:] |= m[:, :-1]; o[:, :-1] |= m[:, 1:]
        return o
    d = dil(mask)
    e = ~dil(~d)
    return e | mask


def render_overlay(model: dict, ref: dict, view: str, bounds, px: float = 0.5) -> np.ndarray:
    """Las dos figuras superpuestas: el patrón en gris y el modelo encima, con sus colores y algo de transparencia, para ver
    dónde sobra y dónde falta."""
    base = render_view(ref, "frente" if view not in VIEWS else view, bounds, px)
    mask = base[..., 3] > 0
    out = np.zeros_like(base)
    out[mask] = (110, 110, 118, 255)
    top = render_view(model, view, bounds, px, alpha=170)
    a = top[..., 3:4].astype(np.float32) / 255.0
    blend = (top[..., :3] * a + out[..., :3] * (1.0 - a)).astype(np.uint8)
    out[..., :3] = np.where(mask[..., None], blend, top[..., :3])         # sin patrón debajo, el color propio de la pieza
    out[..., 3] = np.maximum(out[..., 3], top[..., 3])
    return out


def bgra_premultiplied(img: np.ndarray) -> bytearray:
    """RGBA (sin premultiplicar) -> los bytes B, G, R, A premultiplicados que pide `cairo.FORMAT_ARGB32` (little-endian)."""
    a = img[..., 3:4].astype(np.uint16)
    rgb = (img[..., :3].astype(np.uint16) * a + 127) // 255
    out = np.empty(img.shape, np.uint8)
    out[..., 0], out[..., 1], out[..., 2], out[..., 3] = rgb[..., 2], rgb[..., 1], rgb[..., 0], img[..., 3]
    return bytearray(np.ascontiguousarray(out).tobytes())


def guide_rows(bounds, px: float, marks: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Posición vertical (en píxeles de la imagen, desde arriba) de cada guía horizontal `(nombre, z)` en una vista de frente o de costado."""
    _h0, v0, _h1, v1 = bounds
    h = max(int(np.ceil((v1 - v0) / px)), 1)
    return [(name, h - (z - v0) / px) for name, z in marks]
