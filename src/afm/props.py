"""Accesorios para la mano estándar: taladro de agarre en el puño y empuñadura con dentado (snap-fit).

Idea: la mano estándar (`hand.py`, marco local: +Z hacia los dedos) se taladra UNA vez con un agujero recto de
diámetro `bore_d`; cualquier accesorio (espada, fusil, lanza…) lleva una empuñadura de ese agujero y es
compatible con todas las figuras de la línea, porque comparten mano.

Empuñadura: eje de `shaft_d` con una ranura de `slit_w` que lo parte en dos lengüetas en voladizo de `finger_len`;
cada una termina en un dentado (rampa de entrada + hombro de salida) de diámetro `barb_d` > `bore_d`. Al entrar
por el puño las lengüetas se cierran; al salir se abren y el hombro apoya contra la cara de salida: el accesorio
no se escapa. La deformación de la lengüeta es ε = 1,5·t·δ/L² (la misma fórmula del clic, `detent.tongue_strain`):
t = (shaft_d − slit_w)/2, δ = (barb_d − bore_d)/2, L = finger_len.

El agujero se busca solo (`find_channel`): los dedos curvados de la mano dejan un canal cerrado; se prueban
muchas direcciones y se toma la que da el círculo libre más grande en la silueta. Después `carve_grip` lo
ensancha hasta `bore_d` y comprueba que el anillo de material que lo rodea no quede con paredes finas.

Marco del accesorio: eje de la empuñadura = +X por (y=0, z=0); origen en la cara de la guarda que mira a la mano;
la hoja y la guarda quedan en x < 0 y la empuñadura en x > 0. Pensado para imprimirse acostado: el fondo (z = −shaft_d/2)
es plano y común a hoja, guarda, eje y dentado, y las lengüetas flexionan en el plano de la cama.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

import numpy as np
import trimesh
from scipy import ndimage

from . import solids as S
from .hand import HandError


_EIGHT = np.ones((3, 3), bool)


class GripError(ValueError):
    """El agarre pedido no cabe en la mano (taladro que abre el anillo, lengüetas más largas que la empuñadura…)."""


@dataclass
class GripSpec:
    bore_d: float = 2.4          # Ø del taladro en el puño (mm); con ≥ 2,6–2,8 el anillo de la mano estándar se abre
    shaft_d: float = 2.0         # Ø de la empuñadura (holgura radial = (bore_d − shaft_d)/2)
    slit_w: float = 0.5          # ancho de la ranura entre las dos lengüetas
    finger_len: float = 8.0      # largo de la lengüeta (mm)
    barb_d: float = 2.9          # Ø del dentado (interferencia = (barb_d − bore_d)/2 por lado)
    entry_len: float = 1.6       # largo de la rampa de entrada
    back_len: float = 0.5        # largo de la cara de salida (más corta = agarra más, cuesta más sacarla)
    play: float = 0.4            # juego axial entre el hombro del dentado y la cara de salida de la mano
    max_strain: float = 0.01     # deformación máxima admitida de la lengüeta (PLA ≈ 1 %)
    min_wall: float = 0.8        # pared mínima del anillo de material alrededor del taladro
    segments: int = 48


def grip_strain(spec: GripSpec) -> float:
    t = (spec.shaft_d - spec.slit_w) / 2.0
    delta = max(0.0, (spec.barb_d - spec.bore_d) / 2.0)
    return 1.5 * t * delta / spec.finger_len ** 2


@dataclass
class HandChannel:
    axis: np.ndarray             # dirección del canal (unitario, marco local de la mano)
    center: np.ndarray           # un punto sobre el eje
    free_d: float                # Ø libre del canal natural (mm)


@dataclass
class GripFit:
    axis: np.ndarray
    center: np.ndarray
    ref: np.ndarray              # ejes del plano perpendicular (para colocar el accesorio)
    up: np.ndarray
    bore_d: float
    t_in: float                  # posición axial (desde `center`) de la cara de entrada del material
    t_out: float                 # ... y de la cara de salida
    wall_min: float              # pared mínima del anillo (mm)
    support_deg: float           # arco (°) en que hay material contra el que apoya el hombro del dentado

    @property
    def length(self) -> float:
        return self.t_out - self.t_in

    @classmethod
    def from_dict(cls, d: dict) -> "GripFit":
        """Reconstruye el ajuste desde la clave «agarre» del `.mano.json` (lo que escribe `to_dict`)."""
        axis = np.asarray(d["eje"], float)
        axis = axis / np.linalg.norm(axis)
        ref, up = _ref_up(axis)
        return cls(axis=axis, center=np.asarray(d["centro"], float), ref=ref, up=up, bore_d=float(d["diametro_taladro_mm"]),
                   t_in=float(d["t_entrada_mm"]), t_out=float(d["t_salida_mm"]), wall_min=float(d.get("pared_min_mm", 0.0)),
                   support_deg=float(d.get("apoyo_dentado_grados", 0.0)))

    def to_dict(self) -> dict:
        return {"diametro_taladro_mm": self.bore_d, "centro": self.center.round(4).tolist(),
                "eje": self.axis.round(5).tolist(), "largo_canal_mm": round(self.length, 3),
                "t_entrada_mm": round(self.t_in, 3), "t_salida_mm": round(self.t_out, 3),
                "pared_min_mm": round(self.wall_min, 3), "apoyo_dentado_grados": round(self.support_deg, 1)}


# ------------------------------------------------------------------ canal de la mano
def _solid_points(mf: S.Manifold, pitch: float, max_voxels: float = 4e6) -> tuple[np.ndarray, float]:
    """Centros de los vóxeles ocupados y el paso realmente usado (se agranda si la caja pasaría de `max_voxels`: memoria)."""
    tm = S.to_trimesh(mf)
    ext = np.maximum(tm.extents, 1e-6)
    pitch = max(pitch, float((np.prod(ext) / max_voxels) ** (1 / 3)))
    vox = tm.voxelized(pitch).fill()
    return np.argwhere(vox.matrix) * pitch + np.asarray(vox.transform)[:3, 3], pitch


def _basis(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = np.cross(d, [1.0, 0, 0] if abs(d[0]) < 0.9 else [0, 1.0, 0])
    a /= np.linalg.norm(a)
    return a, np.cross(d, a)


def _silhouette(pts: np.ndarray, d: np.ndarray, pitch: float):
    a, b = _basis(d)
    uv = np.c_[pts @ a, pts @ b]
    lo = uv.min(0) - 0.5
    ij = np.floor((uv - lo) / pitch).astype(int)
    img = np.zeros(ij.max(0) + 2, bool)
    img[ij[:, 0], ij[:, 1]] = True
    return img, lo, a, b


def _enclosed_hole(img: np.ndarray, pitch: float):
    """(radio, celda) del mayor círculo libre que no toca el borde de la silueta; None si no hay agujero cerrado."""
    lab, _ = ndimage.label(~img, structure=_EIGHT)         # 8 vecinos: un contacto en diagonal NO cierra el agujero
    border = np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    enc = (~img) & ~np.isin(lab, border)
    if not enc.any():
        return None
    dist = ndimage.distance_transform_edt(~img) * pitch
    k = np.unravel_index(np.argmax(np.where(enc, dist, 0.0)), dist.shape)
    return float(dist[k]), np.array(k)


def _hole_at(pts, d, pitch):
    img, lo, a, b = _silhouette(pts, d, pitch)
    h = _enclosed_hole(img, pitch)
    if h is None:
        return None
    r, k = h
    return r, a * (lo[0] + k[0] * pitch) + b * (lo[1] + k[1] * pitch)


def _carved_wall(img: np.ndarray, cell: np.ndarray, pitch: float, bore_d: float) -> float:
    """Pared mínima del anillo de material si se taladra Ø`bore_d` con centro en `cell` (0 si el anillo se abre).

    Un cilindro a lo largo de la dirección de la vista quita exactamente las columnas cuya proyección cae dentro del
    disco, así que se puede evaluar en la silueta 2D sin volver a hacer booleanos."""
    rr = bore_d / 2 / pitch
    n = int(np.ceil(rr)) + 1
    i0, j0 = int(round(cell[0])), int(round(cell[1]))
    lo_i, hi_i = max(0, i0 - n), min(img.shape[0], i0 + n + 1)
    lo_j, hi_j = max(0, j0 - n), min(img.shape[1], j0 + n + 1)
    ii, jj = np.mgrid[lo_i:hi_i, lo_j:hi_j]
    disk = (ii - cell[0]) ** 2 + (jj - cell[1]) ** 2 <= rr ** 2
    img2 = img.copy()
    img2[lo_i:hi_i, lo_j:hi_j] &= ~disk
    return _wall_min(img2, np.asarray(cell, float), pitch)


def _score_dir(pts: np.ndarray, d: np.ndarray, pitch: float, bore_d: float):
    img, lo, a, b = _silhouette(pts, d, pitch)
    h = _enclosed_hole(img, pitch)
    if h is None:
        return None
    r, cell = h
    return _carved_wall(img, cell, pitch, bore_d), r, cell, img, lo, a, b


def find_channel(hand: S.Manifold, bore_d: float = 2.4, pitch: float = 0.12, n_dirs: int = 500,
                 refine: bool = True) -> HandChannel:
    """Dirección y centro del canal pasante donde un taladro de Ø`bore_d` deja el anillo de material más grueso.

    No basta con tomar el círculo libre más grande: puede estar pegado a un borde delgado. Por eso cada dirección se
    puntúa por la pared mínima que queda después de taladrar (`_carved_wall`)."""
    pts, pitch = _solid_points(hand, pitch)
    if len(pts) == 0:
        raise HandError("la mano está vacía")
    i = np.arange(n_dirs) + 0.5
    phi, th = np.arccos(1 - i / n_dirs), np.pi * (1 + 5 ** 0.5) * i
    dirs = np.c_[np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)]
    cands = []
    for d in dirs:
        sc = _score_dir(pts, d, pitch, bore_d)
        if sc is not None:
            cands.append((sc[0], sc[1], d))
    if not cands:
        raise HandError("la mano no tiene ningún canal pasante (los dedos no dejan hueco con la palma)")
    cands.sort(key=lambda t: (-t[0], -t[1]))
    top = cands[:3]
    best = None                                        # (pared, radio libre, d, centro en uv, a, b, lo, pitch)
    if refine:
        fine, fp = _solid_points(hand, pitch / 2)
        for _, _, d0 in top:
            a0, b0 = _basis(d0)
            for da in np.radians(np.linspace(-3, 3, 5)):
                for db in np.radians(np.linspace(-3, 3, 5)):
                    d = d0 + np.tan(da) * a0 + np.tan(db) * b0
                    d = d / np.linalg.norm(d)
                    sc = _score_dir(fine, d, fp, bore_d)
                    if sc is not None and (best is None or (sc[0], sc[1]) > (best[0], best[1])):
                        best = (sc[0], sc[1], d, sc[2], sc[3], sc[4], sc[5], sc[6], fp)
        # centro: pequeña búsqueda local alrededor del círculo inscripto
        wall, r, d, cell, img, lo, a, b, fp = best
        c0 = cell.copy()
        for di in np.arange(-6, 7):
            for dj in np.arange(-6, 7):
                c = c0 + np.array([di, dj])
                if img[int(c[0]), int(c[1])]:
                    continue
                w = _carved_wall(img, c, fp, bore_d)
                if w > wall:
                    wall, cell = w, c
        best = (wall, r, d, cell, img, lo, a, b, fp)
    else:
        sc0, r0, d0 = cands[0]
        sc = _score_dir(pts, d0, pitch, bore_d)
        best = (sc[0], sc[1], d0, sc[2], sc[3], sc[4], sc[5], sc[6], pitch)
    wall, r, d, cell, img, lo, a, b, fp = best
    c = a * (lo[0] + cell[0] * fp) + b * (lo[1] + cell[1] * fp)
    c = c + d * float((pts.mean(0) - c) @ d)           # el punto del eje más cercano al centro de la mano
    return HandChannel(axis=d, center=c, free_d=2 * r)


# ------------------------------------------------------------------ taladro
def _ref_up(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ref = np.cross(d, [0, 0, 1.0])
    if np.linalg.norm(ref) < 1e-6:
        ref = np.cross(d, [1.0, 0, 0])
    ref /= np.linalg.norm(ref)
    return ref, np.cross(d, ref)                    # (d, ref, up) positivo: sin espejo al colocar el accesorio


def _free_component(img: np.ndarray, cell) -> tuple[np.ndarray | None, np.ndarray]:
    """(máscara del hueco que contiene `cell`, máscara del exterior). El hueco es None si toca el borde (anillo abierto)."""
    lab, _ = ndimage.label(~img, structure=_EIGHT)
    border = np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    ext = np.isin(lab, border) & (lab > 0)
    i, j = int(round(cell[0])), int(round(cell[1]))
    here = lab[i, j]
    if here == 0 or here in border:
        return None, ext
    return lab == here, ext


def _wall_min(img: np.ndarray, cell, pitch: float) -> float:
    """Espesor mínimo del material entre el hueco que contiene `cell` y el exterior (0 si el anillo está abierto).

    Distancia del borde del hueco al exterior más cercano: no depende de desde dónde se mida (un contacto en diagonal
    o una membrana de un píxel da ≈ un píxel, no un espesor real)."""
    hole, ext = _free_component(img, cell)
    if hole is None:
        return 0.0
    dist = ndimage.distance_transform_edt(~ext) * pitch
    edge = hole & ~ndimage.binary_erosion(hole, structure=_EIGHT, border_value=1)
    return float(dist[edge].min())


def carve_grip(hand: S.Manifold, spec: GripSpec | None = None, channel: HandChannel | None = None,
               pitch: float = 0.05) -> tuple[S.Manifold, GripFit]:
    """Taladra la mano con un agujero recto de `spec.bore_d` por su canal natural y verifica el anillo restante."""
    spec = spec or GripSpec()
    ch = channel or find_channel(hand, spec.bore_d)
    d, c = np.asarray(ch.axis, float), np.asarray(ch.center, float)
    length = 60.0
    bore = S.cylinder_along(c, d, spec.bore_d / 2, length, spec.segments, start=-length / 2)
    out = S.difference(hand, bore)
    if len(out.decompose()) != 1:
        raise GripError(f"el taladro de Ø{spec.bore_d:g} mm parte la mano en {len(out.decompose())} piezas")
    pts, pitch = _solid_points(out, pitch)
    img, lo, a, b = _silhouette(pts, d, pitch)
    cell = np.array([(c @ a - lo[0]) / pitch, (c @ b - lo[1]) / pitch])
    wall_min = _wall_min(img, cell, pitch)
    if wall_min <= 0.0:
        raise GripError(f"el taladro de Ø{spec.bore_d:g} mm abre el anillo de material de la mano (ya no hay agujero cerrado)")
    if wall_min < spec.min_wall:
        raise GripError(f"con un taladro de Ø{spec.bore_d:g} mm el anillo de la mano queda con una pared de {wall_min:.2f} mm "
                        f"(mínimo {spec.min_wall:g}): probá un diámetro menor")
    rel = pts - c
    t = rel @ d
    q = rel - np.outer(t, d)
    rad = np.linalg.norm(q, axis=1)
    ring = (rad > spec.bore_d / 2) & (rad < spec.bore_d / 2 + 1.0)
    if not ring.any():
        raise GripError("no hay material alrededor del taladro")
    t_in, t_out = float(t[ring].min()), float(t[ring].max())
    ref, up = _ref_up(d)
    near = (rad > spec.bore_d / 2) & (rad < spec.bore_d / 2 + 0.4) & (t > t_out - 1.0)
    ang = np.degrees(np.arctan2(q[near] @ up, q[near] @ ref)) if near.any() else np.array([])
    sectors = np.unique(np.floor((ang + 180.0) / 10.0).astype(int)) if len(ang) else np.array([])
    fit = GripFit(axis=d, center=c, ref=ref, up=up, bore_d=spec.bore_d, t_in=t_in, t_out=t_out, wall_min=wall_min,
                  support_deg=10.0 * len(sectors))
    return out, fit


# ------------------------------------------------------------------ accesorio
def _box(x0, x1, y0, y1, z0, z1) -> S.Manifold:
    return S.Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def _check_grip(spec: GripSpec, hole_len: float) -> float:
    """Valida la empuñadura (largo de lengüeta, dentado, deformación) y devuelve la posición del final de la rampa de salida."""
    x_end = hole_len + spec.play + spec.back_len
    if x_end - spec.finger_len < 0.5:
        raise GripError(f"las lengüetas ({spec.finger_len:g} mm) son más largas que la empuñadura ({x_end:.1f} mm)")
    if spec.barb_d <= spec.bore_d:
        raise GripError(f"el dentado (Ø{spec.barb_d:g}) no supera el taladro (Ø{spec.bore_d:g}): no retiene")
    eps = grip_strain(spec)
    if eps > spec.max_strain:
        raise GripError(f"la lengüeta se deforma {100 * eps:.2f} % > {100 * spec.max_strain:.1f} %: dentado más bajo, "
                        f"lengüeta más larga o ranura más ancha")
    return x_end


def _along_x(mf: S.Manifold, x: float) -> S.Manifold:
    return mf.rotate((0, 90, 0)).translate((x, 0, 0))


def grip_stub(spec: GripSpec, hole_len: float, overlap: float = 1.0) -> S.Manifold:
    """Empuñadura sola, lista para pegar a un arma: eje +X por (y=0, z=0) desde x = −`overlap` (esa parte queda dentro del arma)
    hasta el dentado. Dos lengüetas separadas por una ranura vertical (flexionan en Y: impresa acostada, en el plano de la cama);
    el fondo del dentado se recorta a ras del eje (z = −shaft_d/2) para que no sobresalga por debajo."""
    r = spec.shaft_d / 2
    xs = hole_len + spec.play                                # hombro del dentado
    x_end = _check_grip(spec, hole_len)
    shaft = _along_x(S.Manifold.cylinder(x_end + overlap, r, r, spec.segments), -overlap)
    entry = _along_x(S.Manifold.cylinder(spec.entry_len, r, spec.barb_d / 2, spec.segments), xs - spec.entry_len)
    back = _along_x(S.Manifold.cylinder(spec.back_len, spec.barb_d / 2, r * 0.95, spec.segments), xs)
    slit = _box(x_end - spec.finger_len, x_end + 1.0, -spec.slit_w / 2, spec.slit_w / 2, -2 * r, 2 * r)
    return S.intersection(S.difference(S.union(shaft, entry, back), slit), _box(-1e3, 1e3, -1e3, 1e3, -r, 1e3))


def make_prop(spec: GripSpec, hole_len: float, blade_len: float = 34.0, blade_w: float = 4.0, blade_t: float = 0.8,
              guard_w: float = 10.0, guard_t: float = 2.0) -> S.Manifold:
    """Espada de prueba con la empuñadura dentada de `spec` (marco del accesorio, ver docstring del módulo)."""
    r = spec.shaft_d / 2
    z0 = -r
    pts = np.array([[x, y, z] for x, y in ((-guard_t - blade_len, 0.0), (-guard_t - blade_len + 6, -blade_w / 2),
                                           (-guard_t, -blade_w / 2), (-guard_t, blade_w / 2),
                                           (-guard_t - blade_len + 6, blade_w / 2))
                    for z in (z0, z0 + blade_t)])
    blade = S.to_manifold(trimesh.Trimesh(pts).convex_hull)
    guard = _box(-guard_t, 0.0, -guard_w / 2, guard_w / 2, z0, z0 + max(guard_t, spec.shaft_d))
    return S.union(blade, guard, grip_stub(spec, hole_len, overlap=guard_t))


def prop_to_hand_matrix(fit: GripFit) -> np.ndarray:
    """4×4 marco del accesorio → marco local de la mano: guarda contra la cara de entrada, empuñadura por el taladro."""
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = fit.axis, fit.ref, fit.up
    m[:3, 3] = fit.center + fit.axis * fit.t_in
    return m


def place_in_hand(prop: S.Manifold, fit: GripFit) -> S.Manifold:
    """Coloca el accesorio en el marco de la mano (ver `prop_to_hand_matrix`)."""
    return prop.transform(prop_to_hand_matrix(fit)[:3, :].astype(np.float64))


def probe_plate(spec: GripSpec, hole_len: float, barbs=(2.7, 2.9, 3.1), spacing: float | None = None) -> S.Manifold:
    """Placa con una espada por cada Ø de dentado, acostadas (las lengüetas flexionan en el plano de la cama)."""
    from dataclasses import replace

    sp = spacing or (10.0 + 4.0)
    parts = []
    for i, bd in enumerate(barbs):
        parts.append(make_prop(replace(spec, barb_d=float(bd)), hole_len).translate((0, sp * i, spec.shaft_d / 2)))
    return S.union(*parts)


# ------------------------------------------------------------------ todo junto (CLI y GUI)
DEFAULT_BARBS = (2.7, 2.9, 3.1)


def parse_barbs(text: str) -> tuple[float, ...]:
    """«2.7, 2.9, 3.1» → (2.7, 2.9, 3.1); ValueError si está vacío, no son números o hay más de 6 (cabe una fila de la placa)."""
    try:
        vals = tuple(float(x) for x in text.replace(";", ",").replace(" ", ",").split(",") if x.strip())
    except ValueError:
        raise ValueError("los dentados son números separados por comas, p. ej. 2.7,2.9,3.1") from None
    if not vals:
        raise ValueError("falta al menos un Ø de dentado")
    if len(vals) > 6:
        raise ValueError("como mucho 6 dentados por placa")
    if min(vals) <= 0:
        raise ValueError("los Ø de dentado son positivos")
    return vals


@dataclass
class GripResult:
    """Mano ya taladrada, con el canal y el ajuste que se midieron (lo que `compute_grip` deja listo para guardar)."""
    source: str
    hand: S.Manifold             # mano con el taladro, en su marco local
    meta: dict                   # JSON de la mano estándar de origen (o el mínimo) más la clave «agarre»
    channel: HandChannel
    fit: GripFit
    spec: GripSpec


def compute_grip(hand_path: str, spec: GripSpec | None = None, progress=None) -> GripResult:
    """Carga la mano estándar, busca su canal y la taladra. No escribe nada (ver `write_grip_hand`)."""
    from . import hand as H

    say = progress or (lambda text: None)
    spec = spec or GripSpec()
    hand, meta = H.load_standard_hand(hand_path)
    say("buscando el canal de la mano")
    ch = find_channel(hand, spec.bore_d)
    say(f"taladrando Ø{spec.bore_d:g} mm")
    carved, fit = carve_grip(hand, spec, ch)
    meta = dict(meta or {"formato": H.HAND_FORMAT})
    meta["agarre"] = {**fit.to_dict(), "canal_natural_mm": round(ch.free_d, 3), "especificacion": asdict(spec)}
    return GripResult(source=hand_path, hand=carved, meta=meta, channel=ch, fit=fit, spec=spec)


def grip_variants(spec: GripSpec, barbs=DEFAULT_BARBS) -> list[dict]:
    from dataclasses import replace

    return [{"dentado_mm": float(bd), "interferencia_mm": round((bd - spec.bore_d) / 2, 3),
             "deformacion_pct": round(100 * grip_strain(replace(spec, barb_d=float(bd))), 3)} for bd in barbs]


def write_grip_hand(res: GripResult, path: str) -> str:
    """Guarda la mano con el taladro como mano estándar (STL + `.mano.json` con el eje y el Ø del agujero)."""
    from . import hand as H

    H.save_standard_hand(path, res.hand, res.meta)
    return path


def write_grip_plate(res: GripResult, path: str, spec: GripSpec | None = None, barbs=DEFAULT_BARBS) -> str:
    """Guarda la placa de espadas de prueba. `spec` puede cambiar vástago, ranura y lengüeta sin volver a taladrar;
    el Ø del taladro es siempre el de la mano ya taladrada."""
    from dataclasses import replace

    from . import meshio

    sp = replace(spec or res.spec, bore_d=res.spec.bore_d)
    plate = probe_plate(sp, res.fit.length, barbs)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    meshio.save_stl(S.to_printable_trimesh(plate), path)
    return path


def run_grip(hand_path: str, outdir: str, spec: GripSpec | None = None, barbs=DEFAULT_BARBS, name: str | None = None,
             res: GripResult | None = None) -> dict:
    """Taladra la mano y escribe mano, placa e informe en `outdir`; `res` evita repetir el cálculo si ya se hizo."""
    spec = spec or GripSpec()
    res = res or compute_grip(hand_path, spec)
    stem = name or os.path.splitext(os.path.basename(hand_path))[0]
    os.makedirs(outdir, exist_ok=True)
    hand_out = write_grip_hand(res, os.path.join(outdir, f"{stem}_agarre.stl"))
    plate_out = write_grip_plate(res, os.path.join(outdir, f"{stem}_espadas_prueba.stl"), spec, barbs)
    info = {"mano": hand_out, "placa": plate_out, "canal_natural_mm": round(res.channel.free_d, 3), **res.fit.to_dict(),
            "variantes": grip_variants(spec, barbs)}
    with open(os.path.join(outdir, f"{stem}_agarre_informe.json"), "w") as f:
        json.dump(info, f, indent=2, ensure_ascii=False, default=float)
    return info


# ------------------------------------------------------------------ catálogo: agarre de la mano y del accesorio
GRIP_FRAME_NOTE = "eje de la empuñadura = +X; origen en la cara de la guarda que mira a la mano; fondo plano en z = −vástago/2"
HAND_GRIP_KEYS = ("diametro_taladro_mm", "centro", "eje", "largo_canal_mm", "t_entrada_mm", "t_salida_mm")
ACC_GRIP_KEYS = ("taladro_mm", "vastago_mm", "ranura_mm", "largo_lengueta_mm", "dentado_mm", "largo_canal_mm", "juego_axial_mm")


def accessory_grip(spec: GripSpec, hole_len: float) -> dict:
    """Clave «agarre» de una pieza `accesorio`: para qué taladro y qué largo de canal se diseñó la empuñadura."""
    return {"taladro_mm": float(spec.bore_d), "vastago_mm": float(spec.shaft_d), "ranura_mm": float(spec.slit_w),
            "largo_lengueta_mm": float(spec.finger_len), "dentado_mm": float(spec.barb_d), "largo_canal_mm": round(float(hole_len), 3),
            "juego_axial_mm": float(spec.play), "marco": GRIP_FRAME_NOTE}


def grip_problems(d: dict | None, kind: str) -> list[str]:
    """Problemas de formato de la clave «agarre» de una pieza `mano` (kind=«mano») o `accesorio` (kind=«accesorio»)."""
    keys = HAND_GRIP_KEYS if kind == "mano" else ACC_GRIP_KEYS
    if not isinstance(d, dict):
        return ["agarre: no es un objeto"]
    out = [f"agarre: falta «{k}»" for k in keys if k not in d]
    if out:
        return out
    try:
        if kind == "mano":
            if not (0.5 <= float(d["diametro_taladro_mm"]) <= 6.0) or float(d["largo_canal_mm"]) <= 0:
                out.append("agarre: el Ø del taladro o el largo del canal no son razonables")
            if abs(float(np.linalg.norm(d["eje"])) - 1.0) > 1e-2:
                out.append("agarre: el eje no es unitario")
        else:
            if float(d["dentado_mm"]) <= float(d["vastago_mm"]) or float(d["vastago_mm"]) >= float(d["taladro_mm"]):
                out.append("agarre: hace falta vástago < taladro < dentado")
            if float(d["largo_lengueta_mm"]) <= 0 or float(d["largo_canal_mm"]) <= 0:
                out.append("agarre: los largos deben ser positivos")
    except (TypeError, ValueError):
        out.append("agarre: valores que no son números")
    return out


def grip_compat(hand: dict, acc: dict, bore_tol: float = 0.05, max_strain: float = 0.01):
    """¿Entra y se sujeta este accesorio en esta mano? Compatibilidad «barata» (solo metadatos de `mano` y `accesorio`).

    `hand` es la clave «agarre» de la pieza `mano`; `acc`, la del `accesorio`. «no» si el taladro no coincide, el vástago no
    entra, el dentado no retiene o su hombro quedaría dentro del canal; «aviso» si la lengüeta se deforma de más, el juego axial
    es grande o la mano apoya el dentado en poco contorno."""
    from .ports import Compat

    motivos: list[str] = []
    avisos: list[str] = []
    bore = float(hand["diametro_taladro_mm"])
    if abs(float(acc["taladro_mm"]) - bore) > bore_tol:
        motivos.append(f"el accesorio se diseñó para un taladro de Ø{float(acc['taladro_mm']):g} mm y la mano tiene Ø{bore:g} mm")
    radial = (bore - float(acc["vastago_mm"])) / 2
    if radial < 0.05:
        motivos.append(f"el vástago (Ø{float(acc['vastago_mm']):g} mm) no entra en el taladro (Ø{bore:g} mm): holgura {radial:.2f} mm por lado")
    elif radial > 0.4:
        avisos.append(f"holgura radial de {radial:.2f} mm por lado: la empuñadura baila en el taladro")
    inter = (float(acc["dentado_mm"]) - bore) / 2
    if inter <= 0:
        motivos.append(f"el dentado (Ø{float(acc['dentado_mm']):g} mm) no supera el taladro: no retiene")
    t = (float(acc["vastago_mm"]) - float(acc["ranura_mm"])) / 2
    eps = 1.5 * t * max(inter, 0.0) / float(acc["largo_lengueta_mm"]) ** 2 if t > 0 else float("inf")
    if eps > 2 * max_strain:
        motivos.append(f"la lengüeta se deforma {100 * eps:.2f} % al pasar por el taladro (máximo {100 * max_strain:.1f} %): se rompe")
    elif eps > max_strain:
        avisos.append(f"la lengüeta se deforma {100 * eps:.2f} % (> {100 * max_strain:.1f} %): puede romperse en PLA; probá PETG")
    axial = float(acc["largo_canal_mm"]) + float(acc["juego_axial_mm"]) - float(hand["largo_canal_mm"])
    if axial < -0.1:
        motivos.append(f"el hombro del dentado queda {-axial:.1f} mm dentro del canal de la mano ({float(hand['largo_canal_mm']):g} mm): no pasa")
    elif axial > 1.5:
        avisos.append(f"el accesorio se diseñó para un canal de {float(acc['largo_canal_mm']):g} mm y la mano tiene "
                      f"{float(hand['largo_canal_mm']):g} mm: queda {axial:.1f} mm de juego axial")
    if float(hand.get("apoyo_dentado_grados", 360.0)) < 270.0:
        avisos.append(f"la mano apoya el dentado en ≈ {float(hand['apoyo_dentado_grados']):.0f}° del contorno: agarre más flojo que con un tubo completo")
    nivel = "no" if motivos else ("aviso" if avisos else "ok")
    return Compat(nivel=nivel, margenes_mm={"holgura_radial": round(radial, 3), "interferencia": round(inter, 3)},
                  motivos=motivos, avisos=avisos)


def add_to_catalog(res: GripResult, cat, barb: float | None = None, hand_id: str | None = None, acc_id: str | None = None,
                   new_version: bool = False, spec: GripSpec | None = None) -> tuple:
    """Guarda en el catálogo la mano taladrada (pieza `mano` con su agarre) y una espada de prueba (pieza `accesorio`).

    Devuelve (meta de la mano, meta del accesorio, avisos). El dentado es `barb` (por defecto el de `res.spec`)."""
    import tempfile
    from dataclasses import replace

    sp = replace(spec or res.spec, bore_d=res.spec.bore_d)
    if barb is not None:
        sp = replace(sp, barb_d=float(barb))
    prop = make_prop(sp, res.fit.length)                          # valida deformación y largo de lengüeta antes de tocar el catálogo
    with tempfile.TemporaryDirectory() as tmp:
        hand_path = write_grip_hand(res, os.path.join(tmp, "mano_agarre.stl"))
        hand_meta, w1 = cat.import_hand(hand_path, hand_id, new_version)
    stem = os.path.splitext(os.path.basename(res.source))[0]
    acc_meta, w2 = cat.add_accessory(S.to_printable_trimesh(prop), acc_id or "espada-prueba-01", accessory_grip(sp, res.fit.length),
                                     new_version, f"Espada de prueba con dentado Ø{sp.barb_d:g} mm", stem)
    return hand_meta, acc_meta, list(w1) + list(w2)
