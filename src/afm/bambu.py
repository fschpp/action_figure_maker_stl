"""Salida de color pensada para Bambu Studio: un color PLANO por triángulo, sin textura (PLAN: «Bambu 3MF»).

El GLB / OBJ con textura trae un atlas de «una celda por triángulo» (decenas de miles de celdas): Bambu Studio no lo lee bien. Acá el
color que ya se horneó (`colorbake.BakeResult`) se reduce a **un color por cara** y a **pocos colores** (los filamentos que se van a usar):

    <fig>_bambu.3mf              3MF estándar: una `basematerials` con N colores y cada triángulo con `pid`/`p1` (sin textura)
    <fig>_bambu.obj + .mtl       lo mismo en OBJ: un `newmtl` plano (`Kd`) por color; Bambu abre el diálogo «asignar filamentos»

Pasos (`build_face_colors`):
 1. color de cada cara: la **etiqueta de rol** que más se repite en 4 muestras de la celda de su triángulo (si la fuente trae roles) y el
    color de ese rol de la paleta; si no hay roles (fuente RGB), el promedio de las 4 muestras del atlas;
 2. se reducen a lo sumo `max_colors` colores **para toda la figura a la vez** (así dos piezas con el mismo color comparten filamento):
    sin superar el tope se dejan tal cual; si lo superan, k-means ponderado por área en Lab. Los colores con menos de `min_area_pct` %
    del área se funden con el más cercano (cada color de más cuesta cambios de filamento y torre de limpieza);
 3. `despeckle`: una cara que no coincide con ninguno de sus vecinos y cuyos vecinos coinciden entre sí toma su color (quita motas).

Selector (opcional): `candidates` lista los colores que la figura ya tiene (del más al menos usado, con su % de área y los roles de la paleta
que los generan); `BambuOptions.chosen` fija cuáles se usan (los demás pasan al más parecido en Lab) y `BambuOptions.remap` cambia con qué
color se escribe cada uno (solo en la salida: el filamento que se le va a asignar). `collect` + `build_from` separan lo que no depende de las
opciones (color crudo y geometría, se calcula una vez) de lo que sí, así la vista previa (`preview_parts`) se rehace en segundos.

Convenciones: el 3MF va en mm y **Z arriba** (como los STL, con la orientación de impresión ya aplicada); el OBJ va con **Y arriba**
(`frame.Z_UP_TO_Y_UP`), igual que `piezas_color/*.obj`. `trimesh` y `skimage` se importan dentro de las funciones que los usan (D10).
"""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field, replace

import numpy as np

from .frame import Z_UP_TO_Y_UP

DEFAULT_MAX_COLORS = 8
DEFAULT_MIN_AREA_PCT = 0.5
DEFAULT_DESPECKLE = 2
NO_LABEL = 255
ACCESSORY_RGB = (192, 192, 192)       # las armas y accesorios no traen color: salen del color de la figura más parecido a este gris acero
DETECT_MAX = 24                       # a lo sumo tantos colores candidatos en el selector
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
# 4 muestras por triángulo (coordenadas baricéntricas): el centro y un punto cerca de cada vértice
_SAMPLES = np.array([[1 / 3, 1 / 3, 1 / 3], [0.6, 0.2, 0.2], [0.2, 0.6, 0.2], [0.2, 0.2, 0.6]], np.float64)


def norm_hex(value) -> str:
    """`#rrggbb` → `#RRGGBB`. Cualquier otra cosa es `ValueError`."""
    if not isinstance(value, str) or not _HEX_RE.match(value):
        raise ValueError(f"bambu: «{value}» no es un color #RRGGBB")
    return value.upper()


@dataclass
class BambuOptions:
    max_colors: int = DEFAULT_MAX_COLORS
    min_area_pct: float = DEFAULT_MIN_AREA_PCT
    despeckle: int = DEFAULT_DESPECKLE
    chosen: tuple | None = None            # HEX de los colores candidatos que se usan (None = automático: tope y área mínima)
    remap: dict = field(default_factory=dict)          # {HEX candidato: HEX con que se escribe} (solo en la salida)

    @classmethod
    def coerce(cls, value) -> "BambuOptions | None":
        """`None`/`False` → apagado; `True` → por defecto; un entero → `max_colors`; un dict → sus claves; una `BambuOptions` tal cual."""
        if value is None or value is False:
            return None
        if isinstance(value, cls):
            opt = value
        elif value is True:
            opt = cls()
        elif isinstance(value, int):
            opt = cls(max_colors=int(value))
        elif isinstance(value, dict):
            opt = cls(**{k: value[k] for k in ("max_colors", "min_area_pct", "despeckle", "chosen", "remap") if k in value})
        else:
            raise ValueError(f"opciones de Bambu no válidas: {value!r}")
        if opt.max_colors < 1:
            raise ValueError("bambu: max_colors debe ser ≥ 1")
        if not 0.0 <= opt.min_area_pct < 100.0:
            raise ValueError("bambu: min_area_pct debe estar entre 0 y 100")
        if opt.despeckle < 0:
            raise ValueError("bambu: despeckle debe ser ≥ 0")
        chosen = opt.chosen
        if chosen is not None:
            if isinstance(chosen, str) or not len(chosen):
                raise ValueError("bambu: chosen debe ser una lista de colores #RRGGBB (al menos uno)")
            chosen = tuple(dict.fromkeys(norm_hex(h) for h in chosen))
        if not isinstance(opt.remap, dict):
            raise ValueError("bambu: remap debe ser {#RRGGBB: #RRGGBB}")
        remap = {norm_hex(k): norm_hex(v) for k, v in opt.remap.items()}
        return replace(opt, chosen=chosen, remap=remap)


@dataclass
class BambuPiece:
    name: str
    V: np.ndarray            # (n, 3) float64, Z arriba, con la orientación de impresión
    F: np.ndarray            # (m, 3) int64 (vértices fusionados, sin caras degeneradas)
    idx: np.ndarray          # (m,) int: color de cada cara (índice en `colors`)


@dataclass
class BambuResult:
    pieces: list
    colors: np.ndarray                         # (k, 3) uint8, del más usado al menos usado
    area_pct: list = field(default_factory=list)       # % del área de cada color
    faces: list = field(default_factory=list)          # nº de caras de cada color
    smoothed: int = 0                          # caras que cambió `despeckle`
    merged: int = 0                            # colores fundidos por área mínima o por el tope


# ------------------------------------------------------------------ color de cada cara
def _sample_px(unw, atlas_shape) -> tuple[np.ndarray, np.ndarray]:
    """Fila y columna de píxel de las 4 muestras de cada triángulo, `(m, 4)` cada una."""
    tri = np.asarray(unw.tri_px, np.float64)                                   # (m, 3, 2): x der, y abajo
    pts = np.einsum("sk,mkc->msc", _SAMPLES, tri)                              # (m, 4, 2)
    H, W = atlas_shape[:2]
    cols = np.clip(np.floor(pts[..., 0]).astype(np.int64), 0, W - 1)
    rows = np.clip(np.floor(pts[..., 1]).astype(np.int64), 0, H - 1)
    return rows, cols


def face_raw_colors(unw, bk, role_hex: dict | None = None) -> tuple[np.ndarray, bool]:
    """`(rgb (m, 3) uint8, exacto)`: color de cada cara de `unw` antes de reducir la paleta.

    `exacto` es True si salió de roles de la paleta (pocos colores, hay que conservarlos tal cual) y False si es un promedio del atlas."""
    atlas = np.asarray(bk.atlas)
    rows, cols = _sample_px(unw, atlas.shape)
    m = len(rows)
    avg = atlas[rows, cols].astype(np.float64).mean(axis=1)                    # (m, 3)
    labels = getattr(bk, "labels", None)
    roles = getattr(bk, "label_roles", None)
    if labels is None or not roles or not role_hex:
        return np.clip(np.rint(avg), 0, 255).astype(np.uint8), False
    table = np.zeros((256, 3), np.uint8)
    valid = np.zeros(256, bool)
    for i, rid in enumerate(roles[:NO_LABEL]):
        hx = role_hex.get(rid)
        if hx:
            table[i] = [int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)]
            valid[i] = True
    L = np.asarray(labels)[rows, cols]                                         # (m, 4)
    counts = (L[:, :, None] == L[:, None, :]).sum(axis=2)                      # cuántas muestras comparten la etiqueta de cada una
    pick = L[np.arange(m), np.argmax(counts, axis=1)]                          # la más repetida; en empate, la del centro
    flat = atlas[rows[:, 0], cols[:, 0]]                                       # sin rol (relleno plano): el color que se horneó
    return np.where(valid[pick][:, None], table[pick], flat).astype(np.uint8), True


# ------------------------------------------------------------------ reducir la paleta
def _lab(rgb: np.ndarray) -> np.ndarray:
    from skimage.color import rgb2lab

    return rgb2lab(np.asarray(rgb, np.float64).reshape(-1, 1, 3) / 255.0).reshape(-1, 3)


def reduce_colors(rgb: np.ndarray, weights: np.ndarray, max_colors: int, min_area_pct: float, exact: bool):
    """Reduce los colores de `rgb` (`(n, 3)` uint8, con `weights` = área) a lo sumo `max_colors`.

    Devuelve `(centros (k, 3) uint8 del más pesado al más liviano, asignación (n,) int, colores_fundidos)`. Determinista."""
    rgb = np.asarray(rgb, np.uint8).reshape(-1, 3)
    w = np.asarray(weights, np.float64)
    uniq, inv = np.unique(rgb, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    uw = np.bincount(inv, weights=w, minlength=len(uniq))
    total = float(uw.sum()) or 1.0
    start = len(uniq)
    # grupo[i] = a qué grupo pertenece el color único i; los grupos se funden de a uno hasta cumplir el tope y el área mínima
    group = np.arange(len(uniq))
    if len(uniq) > max_colors:
        group = _kmeans_groups(uniq, uw, max_colors)
    k = int(group.max()) + 1
    gw = np.bincount(group, weights=uw, minlength=k)
    # representante de cada grupo: el color exacto más pesado (paleta) o el centro de masa (promedios del atlas)
    centers = np.zeros((k, 3), np.float64)
    for g in range(k):
        sel = np.flatnonzero(group == g)
        if exact:
            centers[g] = uniq[sel[np.argmax(uw[sel])]]
        else:
            centers[g] = (uniq[sel] * uw[sel, None]).sum(axis=0) / max(uw[sel].sum(), 1e-12)
    centers = np.rint(centers)
    # área mínima: el grupo más chico por debajo del umbral se funde con el más cercano (en Lab)
    alive = np.ones(k, bool)
    lab = _lab(centers)
    redirect = np.arange(k)
    while alive.sum() > 1:
        cand = np.flatnonzero(alive)
        small = cand[np.argmin(gw[cand])]
        if 100.0 * gw[small] / total >= min_area_pct:
            break
        others = cand[cand != small]
        near = others[np.argmin(np.linalg.norm(lab[others] - lab[small], axis=1))]
        gw[near] += gw[small]
        gw[small] = 0.0
        alive[small] = False
        redirect[redirect == small] = near
    order = [g for g in np.argsort(-gw, kind="stable") if alive[g]]
    new_id = {g: i for i, g in enumerate(order)}
    final_center = np.array([centers[g] for g in order], np.uint8)
    per_unique = np.array([new_id[redirect[g]] for g in group], np.int64)
    return final_center, per_unique[inv], start - len(order)


def _kmeans_groups(uniq: np.ndarray, uw: np.ndarray, k: int, iters: int = 25) -> np.ndarray:
    """k-means ponderado en Lab sobre los colores únicos. Inicio determinista: el más pesado y después el más lejano de los ya elegidos
    (entre los que pesan al menos 0,05 % del total, para que una mota suelta no ocupe un centro)."""
    lab = _lab(uniq)
    total = float(uw.sum()) or 1.0
    eligible = uw >= 5e-4 * total
    if eligible.sum() < k:
        eligible = np.ones(len(uniq), bool)
    first = int(np.argmax(uw))
    chosen = [first]
    dist = np.linalg.norm(lab - lab[first], axis=1)
    while len(chosen) < k:
        nxt = int(np.argmax(np.where(eligible, dist, -1.0)))
        chosen.append(nxt)
        dist = np.minimum(dist, np.linalg.norm(lab - lab[nxt], axis=1))
    cent = lab[chosen].copy()
    group = np.zeros(len(uniq), np.int64)
    for _ in range(iters):
        d = np.linalg.norm(lab[:, None, :] - cent[None, :, :], axis=2)
        new = np.argmin(d, axis=1)
        if np.array_equal(new, group) and _ > 0:
            break
        group = new
        for g in range(k):
            sel = group == g
            if sel.any() and uw[sel].sum() > 0:
                cent[g] = (lab[sel] * uw[sel, None]).sum(axis=0) / uw[sel].sum()
    _, group = np.unique(group, return_inverse=True)                           # sin grupos vacíos
    return group.reshape(-1)


# ------------------------------------------------------------------ vecinos
def despeckle(idx: np.ndarray, faces: np.ndarray, k: int, passes: int) -> int:
    """Recolorea (en el lugar) las caras sueltas: la que no coincide con ninguno de sus vecinos por arista mientras 2 o más vecinos
    coinciden entre sí toma ese color. Devuelve cuántas caras cambió."""
    if passes <= 0 or len(faces) == 0 or k < 2:
        return 0
    import trimesh

    adj = np.asarray(trimesh.graph.face_adjacency(faces=faces), np.int64).reshape(-1, 2)
    if len(adj) == 0:
        return 0
    changed = 0
    ar = np.arange(len(faces))
    for _ in range(passes):
        cnt = np.zeros((len(faces), k), np.int32)
        np.add.at(cnt, (adj[:, 0], idx[adj[:, 1]]), 1)
        np.add.at(cnt, (adj[:, 1], idx[adj[:, 0]]), 1)
        best = np.argmax(cnt, axis=1)
        flip = (cnt[ar, idx] == 0) & (cnt[ar, best] >= 2)
        if not flip.any():
            break
        idx[flip] = best[flip]
        changed += int(flip.sum())
    return changed


def _merge_vertices(V: np.ndarray, F: np.ndarray):
    """Vértices compartidos (redondeo a 1e-4 mm) y sin caras degeneradas. Devuelve `(V2, F2, keep)`, `keep` = máscara de caras que quedan."""
    key = np.round(np.asarray(V, np.float64) * 1e4).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    F2 = inv[np.asarray(F, np.int64)]
    keep = (F2[:, 0] != F2[:, 1]) & (F2[:, 1] != F2[:, 2]) & (F2[:, 0] != F2[:, 2])
    return np.asarray(V, np.float64)[first], F2[keep], keep


# ------------------------------------------------------------------ armado
@dataclass
class Collected:
    """Lo que sale de las piezas horneadas y no depende de las opciones: color crudo y área de cada cara, y la geometría (sin orientar).
    Se calcula una vez y alimenta al selector (`candidates`), a la vista previa (`preview_parts`) y a la escritura (`build_from`)."""
    names: list
    raws: list                    # (m, 3) uint8 por pieza
    areas: list                   # (m,) mm² por pieza
    geo: list                     # (V, F) por pieza, tal como salen del despliegue (sin la orientación de impresión)
    exact: bool                   # el color salió de roles de la paleta (pocos colores exactos)
    role_hex: dict | None = None  # {rol: #RRGGBB} de la paleta usada
    _cand: tuple | None = field(default=None, repr=False)

    def candidate_reduction(self):
        """`(centros (k, 3) uint8, asignación (n,), fundidos)` con a lo sumo `DETECT_MAX` colores y sin área mínima: la lista de candidatos."""
        if self._cand is None:
            if not self.raws:
                self._cand = (np.zeros((0, 3), np.uint8), np.zeros(0, np.int64), 0)
            else:
                self._cand = reduce_colors(np.concatenate(self.raws), np.concatenate(self.areas), DETECT_MAX, 0.0, self.exact)
        return self._cand


def collect(pieces: dict, role_hex: dict | None) -> Collected:
    """`pieces`: `{nombre: (Unwrapped, BakeResult)}` (como las guarda `Colorizer.pieces`)."""
    names = sorted(pieces)
    raws, areas, geo, exact_all = [], [], [], True
    for name in names:
        unw, bk = pieces[name][0], pieces[name][1]
        rgb, exact = face_raw_colors(unw, bk, role_hex)
        exact_all = exact_all and exact
        V = np.asarray(unw.vertices, np.float64)
        F = np.asarray(unw.faces, np.int64)
        T = V[F]
        raws.append(rgb)
        areas.append(0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1))
        geo.append((V, F))
    return Collected(names, raws, areas, geo, exact_all, dict(role_hex) if role_hex else None)


def candidates(col: Collected) -> list:
    """Los colores que ofrece el selector, del más al menos usado: `[{hex, area_pct, caras, roles}]`. `roles`: los roles de la paleta que
    tienen exactamente ese color (vacío si el color salió de promediar una textura)."""
    centers, assign, _ = col.candidate_reduction()
    if not len(centers):
        return []
    area = np.concatenate(col.areas)
    w = np.bincount(assign, weights=area, minlength=len(centers))
    n = np.bincount(assign, minlength=len(centers))
    total = float(w.sum()) or 1.0
    by_hex: dict = {}
    for rid, hx in (col.role_hex or {}).items():
        try:
            by_hex.setdefault(norm_hex(hx), []).append(str(rid))
        except ValueError:
            continue
    out = []
    for c, wi, ni in zip(centers, w, n):
        hx = _hex(c)
        out.append({"hex": hx, "area_pct": round(100.0 * float(wi) / total, 2), "caras": int(ni), "roles": sorted(by_hex.get(hx, []))})
    return out


def default_selection(cands: list, max_colors: int = DEFAULT_MAX_COLORS, min_area_pct: float = DEFAULT_MIN_AREA_PCT) -> list:
    """La selección automática: los `max_colors` candidatos más usados que cubren al menos `min_area_pct` %. Siempre queda al menos uno."""
    if not cands:
        return []
    keep = [c["hex"] for c in cands[:max(1, int(max_colors))] if c["area_pct"] >= min_area_pct]
    return keep or [cands[0]["hex"]]


def follow_roles(old: dict, new: dict, chosen, remap: dict):
    """Una selección manual nombra los colores por HEX; si un rol de la paleta cambió de color (`old` → `new`: `{rol: hex}`), esos HEX pasan al
    color nuevo del rol, así la selección no queda apuntando a un color que ya no existe. Devuelve `(chosen, remap)` ya traducidos."""
    moved = {str(hx).upper(): str(new[rid]).upper() for rid, hx in (old or {}).items() if rid in (new or {}) and str(hx).upper() != str(new[rid]).upper()}
    if not moved:
        return chosen, dict(remap or {})
    chosen2 = chosen if chosen is None else type(chosen)(dict.fromkeys(moved.get(str(h).upper(), str(h).upper()) for h in chosen))
    return chosen2, {moved.get(str(k).upper(), str(k).upper()): v for k, v in (remap or {}).items()}


def _apply_chosen(col: Collected, chosen) -> tuple | None:
    """`(centros, asignación, fundidos)` usando solo los candidatos `chosen`: cada cara de un candidato que se saca pasa al más parecido (Lab)
    de los que quedan. Un HEX que ya no es candidato se ignora; si ninguno lo es, `None` (se vuelve al modo automático)."""
    cc, cassign, _ = col.candidate_reduction()
    hexes = [_hex(c) for c in cc]
    keep = [hexes.index(h) for h in chosen if h in hexes]
    if not keep:
        return None
    lab = _lab(cc)
    target = np.arange(len(cc))
    for k in range(len(cc)):
        if k not in keep:
            target[k] = keep[int(np.argmin(np.linalg.norm(lab[keep] - lab[k], axis=1)))]
    final = target[cassign]
    w = np.bincount(final, weights=np.concatenate(col.areas), minlength=len(cc))
    order = [g for g in np.argsort(-w, kind="stable") if w[g] > 0]
    new_id = np.zeros(len(cc), np.int64)
    for i, g in enumerate(order):
        new_id[g] = i
    return cc[order], new_id[final], len(cc) - len(order)


def build_from(col: Collected, orientation, opt: BambuOptions, extras=None) -> BambuResult:
    """Un color por cara para toda la figura a partir de lo ya reunido (`collect`) y las opciones (tope y área mínima, o `chosen`; luego `remap`).

    `extras`: `[(nombre, V, F)]` de los accesorios (armas), ya en su lugar final (al lado de la figura, con la orientación aplicada: no se vuelven a
    girar). No llevan color: cada uno sale de UN color liso, el de los que ya usa la figura más parecido a `ACCESSORY_RGB` (así no se pasa del
    tope de colores/filamentos). Sin ellos el 3MF no tenía el arma."""
    if not col.names:
        return BambuResult([], np.zeros((0, 3), np.uint8))
    chosen = opt.chosen
    if opt.remap and not chosen:                  # un cambio de color nombra candidatos: el modo automático se expresa entonces como selección
        chosen = default_selection(candidates(col), opt.max_colors, opt.min_area_pct)
    got = _apply_chosen(col, chosen) if chosen else None
    if got is None:
        got = reduce_colors(np.concatenate(col.raws), np.concatenate(col.areas), opt.max_colors, opt.min_area_pct, col.exact)
    centers, assign, merged = got
    if opt.remap:
        centers = np.array(centers, np.uint8)
        for i in range(len(centers)):
            to = opt.remap.get(_hex(centers[i]))
            if to:
                centers[i] = [int(to[1:3], 16), int(to[3:5], 16), int(to[5:7], 16)]
    k = len(centers)
    out, smoothed, off = [], 0, 0
    w_by_color = np.zeros(k)
    n_by_color = np.zeros(k, np.int64)
    for name, (V, F), area in zip(col.names, col.geo, col.areas):
        if orientation is not None:
            O = np.asarray(orientation, float)
            V = V @ O[:3, :3].T + O[:3, 3]
        idx = assign[off:off + len(F)].copy()
        off += len(F)
        V2, F2, keep = _merge_vertices(V, F)
        idx, area = idx[keep], area[keep]
        smoothed += despeckle(idx, F2, k, opt.despeckle)
        w_by_color += np.bincount(idx, weights=area, minlength=k)
        n_by_color += np.bincount(idx, minlength=k)
        out.append(BambuPiece(name, V2, F2, idx))
    if extras:
        gray = int(np.argmin(np.linalg.norm(_lab(np.asarray(centers, np.uint8)) - _lab(np.array([ACCESSORY_RGB], np.uint8)), axis=1)))
        for name, V, F in extras:
            V2, F2, _keep = _merge_vertices(np.asarray(V, np.float64), np.asarray(F, np.int64))
            if not len(F2):
                continue
            T = V2[F2]
            area = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
            w_by_color[gray] += float(area.sum())
            n_by_color[gray] += len(F2)
            out.append(BambuPiece(str(name), V2, F2, np.full(len(F2), gray, np.int64)))
    total = float(w_by_color.sum()) or 1.0
    return BambuResult(out, centers, [round(100.0 * float(x) / total, 2) for x in w_by_color], [int(n) for n in n_by_color], smoothed, merged)


def build_face_colors(pieces: dict, role_hex: dict | None, orientation, opt: BambuOptions) -> BambuResult:
    """`pieces`: `{nombre: (Unwrapped, BakeResult)}` (como las guarda `Colorizer.pieces`). Un color por cara para toda la figura."""
    return build_from(collect(pieces, role_hex), orientation, opt)


def preview_parts(res: BambuResult) -> list:
    """Vista previa de lo que saldrá en el 3MF: `[(nombre, trimesh.Trimesh, (r, g, b, 1))]`, una malla por pieza y color (el visor pinta
    cada malla de un color liso). Las coordenadas son las de `res` (Z arriba)."""
    import trimesh

    parts = []
    for p in res.pieces:
        for k in np.unique(p.idx):
            F = p.F[p.idx == k]
            used, inv = np.unique(F, return_inverse=True)
            mesh = trimesh.Trimesh(p.V[used], inv.reshape(-1, 3), process=False)
            c = res.colors[int(k)]
            parts.append((f"{p.name}#{int(k) + 1}", mesh, (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, 1.0)))
    return parts


# ------------------------------------------------------------------ escritura
def _hex(c) -> str:
    return "#%02X%02X%02X" % (int(c[0]), int(c[1]), int(c[2]))


def _xml_attr(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                  '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                  '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')


def model_xml(res: BambuResult, title: str) -> str:
    """`3D/3dmodel.model`: `basematerials` id 1, un objeto de malla por pieza (id 2…) y un objeto ensamblado con sus componentes."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
             f'<metadata name="Title">{_xml_attr(title)}</metadata><metadata name="Application">action_figure_maker_stl</metadata>',
             '<resources><basematerials id="1">']
    for i, c in enumerate(res.colors):
        parts.append(f'<base name="color_{i + 1}_{_hex(c)[1:]}" displaycolor="{_hex(c)}"/>')
    parts.append("</basematerials>")
    ids = []
    for n, p in enumerate(res.pieces):
        oid = n + 2
        ids.append(oid)
        parts.append(f'<object id="{oid}" name="{_xml_attr(p.name)}" type="model" pid="1" pindex="0"><mesh><vertices>')
        parts.append("".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in p.V.tolist()))
        parts.append("</vertices><triangles>")
        parts.append("".join(f'<triangle v1="{a}" v2="{b}" v3="{c}" pid="1" p1="{k}"/>' for (a, b, c), k in zip(p.F.tolist(), p.idx.tolist())))
        parts.append("</triangles></mesh></object>")
    if len(ids) == 1:
        build_id = ids[0]
    else:
        build_id = len(ids) + 2
        parts.append(f'<object id="{build_id}" name="{_xml_attr(title)}" type="model"><components>'
                     + "".join(f'<component objectid="{i}"/>' for i in ids) + "</components></object>")
    parts.append(f'</resources><build><item objectid="{build_id}"/></build></model>')
    return "".join(parts)


def write_3mf(path: str, res: BambuResult, title: str) -> str:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", model_xml(res, title))
    return path


def write_obj(outdir: str, base: str, res: BambuResult) -> tuple[str, str]:
    """`<base>.obj` + `<base>.mtl`: un `usemtl` plano por color (`Kd`, sin `map_Kd`), Y arriba. Devuelve los nombres de archivo."""
    with open(os.path.join(outdir, f"{base}.mtl"), "w", encoding="utf-8") as fh:
        for i, c in enumerate(res.colors):
            fh.write(f"newmtl color_{i + 1}_{_hex(c)[1:]}\nKa 1 1 1\nKd {c[0] / 255:.4f} {c[1] / 255:.4f} {c[2] / 255:.4f}\nKs 0 0 0\nd 1\nillum 1\n\n")
    off = 0
    with open(os.path.join(outdir, f"{base}.obj"), "w", encoding="utf-8") as fh:
        fh.write(f"mtllib {base}.mtl\n")
        for p in res.pieces:
            fh.write(f"o {p.name}\n")
            V = p.V @ Z_UP_TO_Y_UP.T
            fh.write("".join(f"v {x:.5f} {y:.5f} {z:.5f}\n" for x, y, z in V.tolist()))
            order = np.argsort(p.idx, kind="stable")
            last = -1
            for f, k in zip((p.F[order] + off + 1).tolist(), p.idx[order].tolist()):
                if k != last:
                    fh.write(f"usemtl color_{k + 1}_{_hex(res.colors[k])[1:]}\n")
                    last = k
                fh.write(f"f {f[0]} {f[1]} {f[2]}\n")
            off += len(p.V)
    return f"{base}.obj", f"{base}.mtl"


def write_bambu(outdir: str, fig_name: str, pieces: dict, role_hex: dict | None, orientation, opt: BambuOptions,
                col: "Collected | None" = None, extras=None) -> dict:
    """Escribe `<fig>_bambu.3mf`, `<fig>_bambu.obj` y `<fig>_bambu.mtl` en `outdir` y devuelve el informe (serializable).
    `col`: lo ya calculado por `collect` para esas mismas piezas (si no, se calcula acá). `extras`: los accesorios, `[(nombre, V, F)]` (ver `build_from`)."""
    res = build_from(col if col is not None else collect(pieces, role_hex), orientation, opt, extras=extras)
    base = f"{fig_name}_bambu"
    os.makedirs(outdir, exist_ok=True)
    write_3mf(os.path.join(outdir, f"{base}.3mf"), res, fig_name)
    obj, mtl = write_obj(outdir, base, res)
    return {"3mf": f"{base}.3mf", "obj": obj, "mtl": mtl,
            "colores": [{"hex": _hex(c), "area_pct": a, "caras": n} for c, a, n in zip(res.colors, res.area_pct, res.faces)],
            "opciones": {"max_colores": opt.max_colors, "area_minima_pct": opt.min_area_pct, "despeckle": opt.despeckle,
                         **({"colores_elegidos": list(opt.chosen)} if opt.chosen else {}), **({"cambios": dict(opt.remap)} if opt.remap else {})},
            "triangulos": int(sum(len(p.F) for p in res.pieces)), "caras_suavizadas": int(res.smoothed), "colores_fundidos": int(res.merged),
            **({"accesorios": [n for n, _v, _f in extras]} if extras else {})}
