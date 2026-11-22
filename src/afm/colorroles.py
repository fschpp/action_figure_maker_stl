"""Roles de color a partir de un FBX con textura (T40; `PLAN_paleta_color_linea.md` §4, §5.2, §7).

`propose` convierte la textura de un `TexturedMesh` en **clusters de color** (k-means en Lab sobre muestras de la superficie, ponderadas por
área) y un **atlas de etiquetas** (un cluster por píxel). Los clusters son *tonos*, no roles: la textura trae la luz horneada, así que un
mismo uniforme aparece en tres grises (§4). El usuario agrupa los clusters en roles; `build_package` aplica esa asignación y devuelve el
`RolesPackage` con las etiquetas ya en ids de rol.

**Nada se asigna solo**: `propose` solo *sugiere* un rol por cluster (`suggested`, a partir de las anclas de la paleta) y `build_package`
se niega si falta decidir algún cluster (un umbral de distancia no detecta el error: el pelo oscuro cayó en el verde del cuello, §4).

`RolesPackage` se guarda y se lee como `<id>@<v>.color.npz` (`afm-color-roles-1`, T41, §6) y `to_textured` lo lleva a un `TexturedMesh` con
etiquetas y LUT, que el motor muestrea como una fuente RGB cuyos únicos colores son los de la paleta. `PIL`, `scipy.cluster` y `skimage`
se importan dentro de las funciones (R3).
"""
from __future__ import annotations

import io
import json
import os
import warnings
from dataclasses import dataclass, field

import numpy as np

from .palette import NO_ROLE, Palette, PaletteError, Role

FORMAT = "afm-color-roles-1"
MAX_ATLAS = 2048                 # lado máximo del atlas de etiquetas (§5.2)
FILTER_PX = 5                    # lado de la ventana del filtro de mayoría
SUGGEST_MAX_DELTA_E = 18.0       # más lejos que esto de toda ancla → sin sugerencia (§4: es solo una sugerencia, no un control de calidad)
_CHUNK_PX = 400_000              # píxeles por bloque al clasificar (acota la memoria)


class RolesError(ValueError):
    """La asignación de clusters a roles no sirve (faltan clusters, rol inválido, …); el mensaje dice cuál es el problema."""


@dataclass
class Proposal:
    centers_rgb: np.ndarray                 # (k, 3) uint8: color medio de cada cluster, del más grande al más chico
    share: np.ndarray                       # (k,) float: % del área de la superficie que cubre cada cluster (suma 100)
    labels_atlas: np.ndarray                # (h, w) uint8: cluster de cada píxel, ya filtrado por mayoría (≤ MAX_ATLAS por lado)
    suggested: list                         # k × (id de rol | None): rol de la ancla más cercana, solo como sugerencia
    raw_atlas: np.ndarray | None = field(default=None, repr=False)     # el mismo atlas sin filtrar (lo usa `build_package`)

    @property
    def k(self) -> int:
        return len(self.centers_rgb)


@dataclass
class RolesPackage:
    """Geometría + UV del FBX y el atlas de etiquetas por rol (`afm-color-roles-1`, §6). Guardar/leer: T41."""
    vertices: np.ndarray                    # (V, 3) float32, unidades del FBX tras `Lcl Scaling`
    faces: np.ndarray                       # (F, 3) int32
    corner_uv: np.ndarray                   # (F, 3, 2) float32
    labels: np.ndarray                      # (h, w) uint8: índice en `roles`, o 255 = «sin rol»
    roles: list                             # ids de rol, en el orden de las etiquetas
    origen: dict = field(default_factory=dict)      # {"nombre": …, "sha256": …} del FBX de origen

    # ------------------------------------------------------------------ disco (T41)
    def problems(self) -> list[str]:
        """Todo lo que está mal en el paquete (formas, rangos, ids de rol, etiquetas); vacío si sirve."""
        out: list[str] = []
        v, f, uv, lab = (np.asarray(x) for x in (self.vertices, self.faces, self.corner_uv, self.labels))
        if v.ndim != 2 or v.shape[1] != 3 or len(v) == 0:
            out.append(f"vertices: forma {v.shape}, se esperaba (V, 3)")
        if f.ndim != 2 or f.shape[1] != 3 or len(f) == 0:
            out.append(f"faces: forma {f.shape}, se esperaba (F, 3)")
        elif v.ndim == 2 and len(v) and (f.min() < 0 or f.max() >= len(v)):
            out.append("faces: hay índices fuera de los vértices")
        if uv.shape != (len(f), 3, 2):
            out.append(f"corner_uv: forma {uv.shape}, se esperaba ({len(f)}, 3, 2)")
        elif not np.isfinite(uv).all():
            out.append("corner_uv: hay valores no finitos")
        if lab.ndim != 2 or lab.size == 0 or lab.dtype != np.uint8:
            out.append(f"labels: debe ser un atlas 2-D uint8 (forma {lab.shape}, tipo {lab.dtype})")
        elif max(lab.shape) > MAX_ATLAS:
            out.append(f"labels: atlas de {lab.shape[1]}×{lab.shape[0]}, el máximo es {MAX_ATLAS} por lado")
        roles = list(self.roles)
        if len(set(roles)) != len(roles):
            out.append("roles: hay ids repetidos")
        for r in roles:
            try:
                Role(r, r, "#000000")
            except (PaletteError, TypeError):
                out.append(f"roles: id inválido {r!r}")
        if lab.ndim == 2 and lab.dtype == np.uint8 and lab.size:
            bad = np.unique(lab[(lab >= len(roles)) & (lab != NO_ROLE)])
            if len(bad):
                out.append("labels: usa etiquetas que no son un rol de la lista ni 255: " + ", ".join(str(int(b)) for b in bad[:8]))
        return out

    def save(self, path) -> str:
        """Escribe el paquete (`np.savez_compressed`, sin pickle). Archivo temporal + `os.replace`: nunca queda un `.npz` a medias.
        Valida antes de escribir. Devuelve la ruta."""
        probs = self.problems()
        if probs:
            raise RolesError("el paquete no es válido: " + "; ".join(probs))
        lab = np.asarray(self.labels, np.uint8)
        meta = {"formato": FORMAT, "roles": list(self.roles), "atlas": [int(lab.shape[1]), int(lab.shape[0])], "origen": dict(self.origen)}
        path = os.fspath(path)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:                         # con un archivo abierto numpy no le agrega «.npz» al nombre
            np.savez_compressed(fh, meta=np.frombuffer(json.dumps(meta, ensure_ascii=False).encode("utf-8"), np.uint8),
                                vertices=np.asarray(self.vertices, np.float32), faces=np.asarray(self.faces, np.int32),
                                corner_uv=np.asarray(self.corner_uv, np.float32), labels=lab)
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path) -> "RolesPackage":
        """Lee un `.npz` con `allow_pickle=False`. Un archivo que no es del formato, con formas que no cierran o con etiquetas que no
        son un rol → `RolesError` con el motivo."""
        try:
            with np.load(os.fspath(path), allow_pickle=False) as z:
                missing = [k for k in ("meta", "vertices", "faces", "corner_uv", "labels") if k not in z.files]
                if missing:
                    raise RolesError("faltan arrays: " + ", ".join(missing))
                meta = json.loads(bytes(np.asarray(z["meta"], np.uint8)).decode("utf-8"))
                arrays = {k: z[k] for k in ("vertices", "faces", "corner_uv", "labels")}
        except RolesError:
            raise
        except (OSError, ValueError, KeyError, UnicodeDecodeError, EOFError) as exc:     # incluye BadZipFile, pickle y JSON roto
            raise RolesError(f"{os.path.basename(os.fspath(path))}: no es un paquete de roles legible ({exc})") from exc
        if not isinstance(meta, dict) or meta.get("formato") != FORMAT:
            raise RolesError(f"formato desconocido: {meta.get('formato') if isinstance(meta, dict) else meta!r} (se esperaba «{FORMAT}»)")
        if not isinstance(meta.get("roles"), list):
            raise RolesError("meta: falta la lista «roles»")
        pkg = cls(arrays["vertices"], arrays["faces"], arrays["corner_uv"], arrays["labels"], list(meta["roles"]), dict(meta.get("origen") or {}))
        probs = pkg.problems()
        if meta.get("atlas") is not None and arrays["labels"].ndim == 2 and list(meta["atlas"]) != [arrays["labels"].shape[1], arrays["labels"].shape[0]]:
            probs.append("meta: el tamaño del atlas no coincide con labels")
        if probs:
            raise RolesError("paquete inválido: " + "; ".join(probs))
        return pkg

    def with_roles(self, extra) -> "RolesPackage":
        """Copia con los roles de `extra` que el paquete no trae agregados **al final** de `roles` (R02): las etiquetas existentes no cambian, así
        que lo ya clasificado queda igual y la LUT incluye los roles nuevos (los usan los retoques). Sin roles nuevos devuelve `self`."""
        new = [r for r in dict.fromkeys(extra) if r not in self.roles]
        if not new:
            return self
        roles = list(self.roles) + new
        if len(roles) >= NO_ROLE:
            raise RolesError(f"{len(roles)} roles: una etiqueta de 8 bits admite hasta {NO_ROLE - 1}")
        return RolesPackage(self.vertices, self.faces, self.corner_uv, self.labels, roles, dict(self.origen or {}))

    # ------------------------------------------------------------------ al motor (T41)
    def to_textured(self, palette: Palette, overrides: dict | None = None):
        """`TexturedMesh` con `labels` y `lut = palette.with_overrides(overrides).lut(roles)`: `sample` devuelve por vecino más cercano los
        colores de la paleta. `info["roles"]` lleva los ids. Un rol que la paleta no tiene → `PaletteError` con la lista de los que faltan
        (no se inventa color). Las `images["base_color"]` son un PNG del atlas ya coloreado (vista previa y chequeos de «trae textura»);
        el muestreo no pasa por él."""
        from PIL import Image

        from .texmesh import TexturedMesh

        lut = palette.with_overrides(overrides or {}).lut(self.roles)
        buf = io.BytesIO()
        Image.fromarray(lut[np.asarray(self.labels)]).save(buf, format="PNG")
        info = {"avisos": [], "roles": list(self.roles), "origen": dict(self.origen), "n_materiales": 1}
        return TexturedMesh(np.asarray(self.vertices, np.float64), np.asarray(self.faces, np.int32), np.asarray(self.corner_uv, np.float32),
                            {"base_color": buf.getvalue()}, info, labels=np.asarray(self.labels, np.uint8), lut=lut)


# ------------------------------------------------------------------ color
SKIN_ROLES = ("piel",)           # ids de rol que cuentan como «piel» para saber hacia dónde mira la cara


def face_roles(pkg) -> np.ndarray:
    """(F,) índice de rol (255 = sin rol) de cada triángulo del paquete: la etiqueta del atlas en el centro de su UV."""
    uv = np.asarray(pkg.corner_uv, float).mean(1)
    h, w = pkg.labels.shape
    return pkg.labels[np.clip(((1 - uv[:, 1]) * h).astype(int), 0, h - 1), np.clip((uv[:, 0] * w).astype(int), 0, w - 1)]


def face_direction(pkg, skin_roles=SKIN_ROLES, region=None, vertices=None):
    """`(vector, confianza)`: hacia dónde mira la cara del FBX, en el marco de `vertices` (por defecto los del paquete).

    Es el vector del centro de la región al centroide, ponderado por área, de los triángulos con rol de piel (la nuca es pelo; la cara, orejas y
    cuello son piel, pero el cuello queda detrás del centro de una cabeza cortada y no pesa si `region` ya lo dejó afuera). `region`: máscara
    de triángulos (por defecto todos). La confianza es el largo del vector sobre el radio de la región (0–1). Sin piel, `(None, 0.0)`."""
    V = np.asarray(pkg.vertices if vertices is None else vertices, float)
    F = np.asarray(pkg.faces, np.int64)
    tri = V[F]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    cen = tri.mean(1)
    reg = np.ones(len(F), bool) if region is None else np.asarray(region, bool)
    skin_ids = [i for i, r in enumerate(pkg.roles) if any(str(r).startswith(sr) for sr in skin_roles)]
    skin = np.isin(face_roles(pkg), skin_ids) & reg
    if not skin_ids or area[skin].sum() <= 0 or area[reg].sum() <= 0:
        return None, 0.0
    c_reg = (cen[reg] * area[reg, None]).sum(0) / area[reg].sum()
    vec = (cen[skin] * area[skin, None]).sum(0) / area[skin].sum() - c_reg
    radius = float(np.sqrt((area[reg] * ((cen[reg] - c_reg) ** 2).sum(1)).sum() / area[reg].sum()))
    return vec, float(min(1.0, np.linalg.norm(vec) / max(radius, 1e-9)))


def _lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (…, 3) 0–255 → Lab (…, 3) float32."""
    from skimage.color import rgb2lab

    a = np.asarray(rgb, np.float64).reshape(-1, 1, 3) / 255.0
    return rgb2lab(a).reshape(np.shape(rgb)).astype(np.float32)


def _surface_samples(tm, n: int, rng) -> np.ndarray:
    """`n` colores (n, 3) uint8 de puntos de la superficie, uniformes en área (cara por área, punto uniforme dentro de la cara)."""
    V = np.asarray(tm.vertices, float)
    F = np.asarray(tm.faces, np.int64)
    t = V[F]
    area = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
    if not np.isfinite(area).all() or area.sum() <= 0:
        raise RolesError("la malla no tiene superficie: no hay de dónde sacar colores")
    face = rng.choice(len(F), size=n, p=area / area.sum())
    r1, r2 = rng.random(n), rng.random(n)
    s = np.sqrt(r1)
    bary = np.column_stack([1.0 - s, s * (1.0 - r2), s * r2])
    return tm.sample(face, bary, "base_color")


# ------------------------------------------------------------------ atlas de etiquetas
def _classify_atlas(img: np.ndarray, centers_lab: np.ndarray, max_atlas: int) -> np.ndarray:
    """Reduce la textura por promedio de bloques (a ≤ `max_atlas` por lado) y clasifica cada píxel por el centro más cercano en Lab."""
    h, w = img.shape[:2]
    f = max(1, int(np.ceil(max(h, w) / max_atlas)))
    oh, ow = -(-h // f), -(-w // f)
    out = np.empty((oh, ow), np.uint8)
    rows = max(1, _CHUNK_PX // ow)
    for r0 in range(0, oh, rows):
        r1 = min(oh, r0 + rows)
        blk = img[r0 * f:r1 * f].astype(np.float32)
        pad_h, pad_w = (r1 - r0) * f - blk.shape[0], ow * f - w
        if pad_h or pad_w:
            blk = np.pad(blk, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
        avg = blk.reshape(r1 - r0, f, ow, f, 3).mean(axis=(1, 3)) if f > 1 else blk
        lab = _lab(avg).reshape(-1, 3)
        d = ((lab[:, None, :] - centers_lab[None, :, :]) ** 2).sum(axis=2)
        out[r0:r1] = d.argmin(axis=1).astype(np.uint8).reshape(r1 - r0, ow)
    return out


def mode_filter(labels: np.ndarray, n_classes: int, size: int = FILTER_PX, block: int = 256) -> np.ndarray:
    """Filtro de **mayoría** `size`×`size`: cada píxel toma la etiqueta más votada de su ventana (no mediana: con etiquetas categóricas la
    mediana inventa clases). Quita el «sal y pimienta» sin correr los bordes rectos; en un empate gana la etiqueta que el píxel ya tenía.
    Se hace por bloques de filas (con el solape que pide la ventana) para no tener `n_classes` planos del tamaño del atlas a la vez."""
    from scipy.ndimage import uniform_filter

    labels = np.asarray(labels)
    if size <= 1 or n_classes <= 1:
        return labels.copy()
    h = labels.shape[0]
    half = size // 2
    out = np.empty_like(labels)
    for r0 in range(0, h, block):
        r1 = min(h, r0 + block)
        a0, a1 = max(0, r0 - half), min(h, r1 + half)
        sl = labels[a0:a1]
        best = np.full(sl.shape, -1.0, np.float32)
        arg = np.zeros(sl.shape, labels.dtype)
        for c in range(n_classes):
            mask = sl == c
            if not mask.any() and c != 0:
                # sin píxeles de esta clase en el bloque no tiene votos; la clase 0 se evalúa siempre y fija el mínimo de partida
                continue
            votes = uniform_filter(mask.astype(np.float32), size=size, mode="nearest") + 1e-3 * mask
            better = votes > best
            best[better] = votes[better]
            arg[better] = c
        out[r0:r1] = arg[r0 - a0:r0 - a0 + (r1 - r0)]
    return out


# ------------------------------------------------------------------ propuesta
def propose(tm, palette: Palette | None, k: int = 10, seed: int = 0, n_samples: int = 300_000,
            max_delta_e: float = SUGGEST_MAX_DELTA_E) -> Proposal:
    """Clusters de color de la textura de `tm` y su atlas de etiquetas. Determinista (semilla fija).

    1. `n_samples` puntos de la superficie, uniformes en área, con su color. 2. K-means en Lab (`scipy.cluster.vq.kmeans2`, `++`).
    3. `centers_rgb` y `share` (% del área), ordenados del más grande al más chico; los clusters vacíos se descartan (con pocos colores
    distintos salen menos de `k`). 4. `suggested`: rol de la ancla más cercana de `palette` (o `None`). 5. Atlas de etiquetas ≤ 2048² y
    su versión filtrada por mayoría 5×5."""
    from scipy.cluster.vq import kmeans2

    if k < 1:
        raise RolesError("k tiene que ser al menos 1")
    rng = np.random.default_rng(seed)
    rgb = _surface_samples(tm, int(n_samples), rng)
    lab = _lab(rgb).astype(np.float64)
    n_unique = len(np.unique(rgb, axis=0))
    k = min(int(k), n_unique)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")                          # «one of the clusters is empty»: se descartan más abajo
        centers, _ = kmeans2(lab, k, iter=25, minit="++", seed=rng, missing="warn")
    centers = np.nan_to_num(centers)
    lab_of = ((lab[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    counts = np.bincount(lab_of, minlength=k)
    order = [c for c in np.argsort(-counts, kind="stable") if counts[c] > 0]
    centers_rgb = np.array([np.rint(rgb[lab_of == c].mean(axis=0)) for c in order]).astype(np.uint8)
    share = np.array([counts[c] for c in order], float) * (100.0 / counts.sum())
    suggested = []
    for c in centers_rgb:
        near = palette.nearest(tuple(int(x) for x in c), max_delta_e) if palette is not None and palette.roles else None
        suggested.append(near[0] if near else None)
    raw = _classify_atlas(tm.image("base_color"), _lab(centers_rgb).astype(np.float32), MAX_ATLAS)
    return Proposal(centers_rgb, share, mode_filter(raw, len(centers_rgb), FILTER_PX), suggested, raw)


# ------------------------------------------------------------------ paquete
def build_package(tm, proposal: Proposal, assign: dict, *, filter_px: int = FILTER_PX, max_atlas: int = MAX_ATLAS,
                  origen: dict | None = None) -> RolesPackage:
    """Aplica la asignación `{cluster: rol | None}` (varios clusters pueden ir al mismo rol; `None` = «sin rol», etiqueta 255).

    **No decide nada**: si algún cluster de la propuesta no está en `assign` levanta `RolesError` con la lista. El filtro de mayoría se
    aplica sobre las etiquetas **ya en roles** (dos tonos del mismo rol no son «ruido» entre sí). `roles` queda en el orden de primera
    aparición por cluster (del más grande al más chico)."""
    k = proposal.k
    missing = [c for c in range(k) if c not in assign]
    if missing:
        raise RolesError("faltan clusters por asignar (un rol o None): " + ", ".join(str(c) for c in missing))
    extra = [c for c in assign if not isinstance(c, (int, np.integer)) or not 0 <= c < k]
    if extra:
        raise RolesError(f"clusters que no existen en la propuesta (hay {k}): " + ", ".join(str(c) for c in extra))
    roles: list[str] = []
    for c in range(k):
        r = assign[c]
        if r is None:
            continue
        try:
            Role(r, r, "#000000")                                # valida el id con la regla de la paleta
        except (PaletteError, TypeError) as exc:
            raise RolesError(f"cluster {c}: {exc}") from exc
        if r not in roles:
            roles.append(r)
    if len(roles) >= NO_ROLE:
        raise RolesError(f"{len(roles)} roles: una etiqueta de 8 bits admite hasta {NO_ROLE - 1}")
    remap = np.array([NO_ROLE if assign[c] is None else roles.index(assign[c]) for c in range(k)], np.uint8)
    src = proposal.raw_atlas if proposal.raw_atlas is not None else proposal.labels_atlas
    lab = remap[src]
    if filter_px > 1 and roles:
        # el filtro vota entre los ids de rol; «sin rol» (255) se pasa a un índice más para que también cuente como clase
        tmp = np.where(lab == NO_ROLE, len(roles), lab).astype(np.uint8)
        tmp = mode_filter(tmp, len(roles) + 1, filter_px)
        lab = np.where(tmp == len(roles), NO_ROLE, tmp).astype(np.uint8)
    f = max(1, int(np.ceil(max(lab.shape) / max_atlas)))
    if f > 1:
        lab = np.ascontiguousarray(lab[::f, ::f])
    return RolesPackage(np.asarray(tm.vertices, np.float32), np.asarray(tm.faces, np.int32), np.asarray(tm.corner_uv, np.float32),
                        lab, roles, dict(origen or {}))
