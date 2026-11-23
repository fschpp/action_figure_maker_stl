"""Roles de color a partir de un FBX con textura (T40; `PLAN_paleta_color_linea.md` §4, §5.2, §7).

`propose` convierte la textura de un `TexturedMesh` en **clusters de color** (k-means en Lab sobre muestras de la superficie, ponderadas por
área) y un **atlas de etiquetas** (un cluster por píxel). Los clusters son *tonos*, no roles: la textura trae la luz horneada, así que un
mismo uniforme aparece en tres grises (§4). El usuario agrupa los clusters en roles; `build_package` aplica esa asignación y devuelve el
`RolesPackage` con las etiquetas ya en ids de rol.

**Nada se asigna solo**: `propose` solo *sugiere* un rol por cluster (`suggested`, a partir de las anclas de la paleta) y `build_package`
se niega si falta decidir algún cluster (un umbral de distancia no detecta el error: el pelo oscuro cayó en el verde del cuello, §4).

`RolesPackage` se guarda y se lee como `<id>@<v>.color.npz` (`afm-color-roles-1`, T41, §6; `afm-color-roles-2` si trae atlas de sombreado) y `to_textured` lo lleva a un `TexturedMesh` con
etiquetas y LUT, que el motor muestrea como una fuente RGB cuyos únicos colores son los de la paleta. `PIL`, `scipy.cluster` y `skimage`
se importan dentro de las funciones (R3).

**Sombreado** (`PLAN_color_sombreado.md`): con `build_package(..., shade=True)` el paquete guarda además un atlas uint8 con el factor de brillo de cada
píxel respecto del color medio de su rol (luminancia del píxel / luminancia media del rol, acotado a `SHADE_MIN`…`SHADE_MAX`). Al armar, el color de cada
punto es el hex del rol × ese factor: se conserva la luz y la sombra horneadas de la textura y cambiar el hex de un rol sigue recoloreando todo.
Un paquete sin sombreado (`shade is None`, los `afm-color-roles-1` de siempre) se comporta exactamente como antes.
"""
from __future__ import annotations

import io
import json
import os
import warnings
from dataclasses import dataclass, field

import numpy as np

from .palette import NO_ROLE, Palette, PaletteError, Role
from .texmesh import SHADE_MAX, SHADE_MIN, SHADE_ONE, apply_shade, shade_code, shade_factor

FORMAT = "afm-color-roles-1"
FORMAT_SHADE = "afm-color-roles-2"   # igual que el 1 + el array `shade` (atlas de sombreado, mismo tamaño que `labels`)
FORMATS = (FORMAT, FORMAT_SHADE)
SHADE_MIN_LUMA = 8.0             # un rol más oscuro que esto (luminancia 0–255 media) no se sombrea: el cociente no mide luz sino ruido
MAX_ATLAS = 2048                 # lado máximo del atlas de etiquetas (§5.2)
FILTER_PX = 5                    # lado de la ventana del filtro de mayoría
PIECE_FILTER_PX = 7              # O10: ventana del filtro de mayoría sobre una pieza (las franjas de transición piel→pelo de 3–5 px no sobreviven)
FIGURE_K = 10                    # O10: clusters por defecto de una figura entera
REGION_K = 8                     # O10: clusters por defecto de una pieza recortada (el FBX es un busto; la pieza es una parte)
MERGE_MIN_PCT = 1.0              # O10: un cluster con menos de este % de la región se funde con el más parecido (no gasta un rol)
ROLE_MIN_PCT = 0.5               # O10: un rol con menos de este % de la región no se guarda en la pieza
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
    region: np.ndarray | None = field(default=None, repr=False)        # O10: máscara de triángulos con que se calcularon los clusters (None = toda la malla)
    merged: int = 0                                                    # O10: cuántos clusters chicos se fundieron con otro (`MERGE_MIN_PCT`)

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
    shade: np.ndarray | None = None         # (h, w) uint8, mismo tamaño que `labels`: factor de brillo del píxel (128 = ×1,0); None = sin sombreado

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
        if self.shade is not None:
            sh = np.asarray(self.shade)
            if sh.dtype != np.uint8 or sh.shape != lab.shape:
                out.append(f"shade: debe ser un atlas uint8 del mismo tamaño que labels (forma {sh.shape}, tipo {sh.dtype}; labels {lab.shape})")
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
        shade = None if self.shade is None else np.asarray(self.shade, np.uint8)
        meta = {"formato": FORMAT if shade is None else FORMAT_SHADE, "roles": list(self.roles), "atlas": [int(lab.shape[1]), int(lab.shape[0])], "origen": dict(self.origen)}
        path = os.fspath(path)
        tmp = path + ".tmp"
        extra = {} if shade is None else {"shade": shade}
        with open(tmp, "wb") as fh:                         # con un archivo abierto numpy no le agrega «.npz» al nombre
            np.savez_compressed(fh, meta=np.frombuffer(json.dumps(meta, ensure_ascii=False).encode("utf-8"), np.uint8),
                                vertices=np.asarray(self.vertices, np.float32), faces=np.asarray(self.faces, np.int32),
                                corner_uv=np.asarray(self.corner_uv, np.float32), labels=lab, **extra)
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
                has_shade = "shade" in z.files
                if has_shade:
                    arrays["shade"] = z["shade"]
        except RolesError:
            raise
        except (OSError, ValueError, KeyError, UnicodeDecodeError, EOFError) as exc:     # incluye BadZipFile, pickle y JSON roto
            raise RolesError(f"{os.path.basename(os.fspath(path))}: no es un paquete de roles legible ({exc})") from exc
        if not isinstance(meta, dict) or meta.get("formato") not in FORMATS:
            raise RolesError(f"formato desconocido: {meta.get('formato') if isinstance(meta, dict) else meta!r} (se esperaba «{FORMAT}» o «{FORMAT_SHADE}»)")
        if meta["formato"] == FORMAT_SHADE and not has_shade:
            raise RolesError(f"el paquete es «{FORMAT_SHADE}» pero falta el array shade")
        if not isinstance(meta.get("roles"), list):
            raise RolesError("meta: falta la lista «roles»")
        shade = arrays.get("shade") if meta["formato"] == FORMAT_SHADE else None      # un «-1» con un shade de más: se ignora (es el formato viejo)
        pkg = cls(arrays["vertices"], arrays["faces"], arrays["corner_uv"], arrays["labels"], list(meta["roles"]), dict(meta.get("origen") or {}), shade)
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
        return RolesPackage(self.vertices, self.faces, self.corner_uv, self.labels, roles, dict(self.origen or {}), self.shade)

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
        preview = lut[np.asarray(self.labels)]
        if self.shade is not None:
            preview = apply_shade(preview.reshape(-1, 3), shade_factor(self.shade).reshape(-1)).reshape(preview.shape)
        Image.fromarray(preview).save(buf, format="PNG")
        info = {"avisos": [], "roles": list(self.roles), "origen": dict(self.origen), "n_materiales": 1}
        return TexturedMesh(np.asarray(self.vertices, np.float64), np.asarray(self.faces, np.int32), np.asarray(self.corner_uv, np.float32),
                            {"base_color": buf.getvalue()}, info, labels=np.asarray(self.labels, np.uint8), lut=lut,
                            shade=None if self.shade is None else np.asarray(self.shade, np.uint8))


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


def _surface_samples(tm, n: int, rng, region=None) -> np.ndarray:
    """`n` colores (n, 3) uint8 de puntos de la superficie, uniformes en área (cara por área, punto uniforme dentro de la cara). `region` (O10): máscara
    de triángulos; si se pasa, solo se muestrean esos."""
    V = np.asarray(tm.vertices, float)
    F = np.asarray(tm.faces, np.int64)
    t = V[F]
    area = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
    if region is not None:
        region = np.asarray(region, bool)
        if region.shape != (len(F),):
            raise RolesError(f"la región tiene {region.size} valores y la malla {len(F)} triángulos")
        area = np.where(region, area, 0.0)
    if not np.isfinite(area).all() or area.sum() <= 0:
        raise RolesError("la malla no tiene superficie: no hay de dónde sacar colores" if region is None
                         else "la región de la pieza no tiene superficie en el FBX: no hay de dónde sacar colores")
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
def propose(tm, palette: Palette | None, k: int = FIGURE_K, seed: int = 0, n_samples: int = 300_000,
            max_delta_e: float = SUGGEST_MAX_DELTA_E, region=None, min_share: float = 0.0) -> Proposal:
    """Clusters de color de la textura de `tm` y su atlas de etiquetas. Determinista (semilla fija).

    1. `n_samples` puntos de la superficie, uniformes en área, con su color. 2. K-means en Lab (`scipy.cluster.vq.kmeans2`, `++`).
    3. `centers_rgb` y `share` (% del área), ordenados del más grande al más chico; los clusters vacíos se descartan (con pocos colores
    distintos salen menos de `k`). 4. `suggested`: rol de la ancla más cercana de `palette` (o `None`). 5. Atlas de etiquetas ≤ 2048² y
    su versión filtrada por mayoría 5×5.

    O10 · `region`: máscara de triángulos (p. ej. `colorpiece.head_region`). Los colores se muestrean **solo** ahí, así los clusters no se gastan en lo
    que la pieza no tiene (en un busto, el uniforme) y `share` es el % de la región. `min_share` (% de la región): un cluster que cubre menos se funde con
    el más parecido en Lab (el color medio se recalcula con los dos); con la región entera y `min_share = 0` no cambia nada."""
    from scipy.cluster.vq import kmeans2

    if k < 1:
        raise RolesError("k tiene que ser al menos 1")
    rng = np.random.default_rng(seed)
    rgb = _surface_samples(tm, int(n_samples), rng, region)
    lab = _lab(rgb).astype(np.float64)
    n_unique = len(np.unique(rgb, axis=0))
    k = min(int(k), n_unique)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")                          # «one of the clusters is empty»: se descartan más abajo
        centers, _ = kmeans2(lab, k, iter=25, minit="++", seed=rng, missing="warn")
    centers = np.nan_to_num(centers)
    lab_of = ((lab[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    merged = 0
    if min_share > 0:
        lab_of, merged = _merge_small(lab, lab_of, k, float(min_share))
    counts = np.bincount(lab_of, minlength=k)
    order = [c for c in np.argsort(-counts, kind="stable") if counts[c] > 0]
    centers_rgb = np.array([np.rint(rgb[lab_of == c].mean(axis=0)) for c in order]).astype(np.uint8)
    share = np.array([counts[c] for c in order], float) * (100.0 / counts.sum())
    suggested = []
    for c in centers_rgb:
        near = palette.nearest(tuple(int(x) for x in c), max_delta_e) if palette is not None and palette.roles else None
        suggested.append(near[0] if near else None)
    raw = _classify_atlas(tm.image("base_color"), _lab(centers_rgb).astype(np.float32), MAX_ATLAS)
    return Proposal(centers_rgb, share, mode_filter(raw, len(centers_rgb), FILTER_PX), suggested, raw,
                    None if region is None else np.asarray(region, bool), merged)


def _merge_small(lab: np.ndarray, lab_of: np.ndarray, k: int, min_pct: float) -> tuple[np.ndarray, int]:
    """O10 · Funde los clusters que cubren menos de `min_pct` % de las muestras con el cluster más cercano (en Lab, por su color medio), del más chico al
    más grande y recalculando los centros; siempre queda al menos un cluster. Devuelve `(lab_of nuevo, cuántos se fundieron)`."""
    lab_of = lab_of.copy()
    n = len(lab_of)
    merged = 0
    while True:
        counts = np.bincount(lab_of, minlength=k)
        alive = [c for c in range(k) if counts[c] > 0]
        small = [c for c in alive if counts[c] * 100.0 / n < min_pct]
        if not small or len(alive) <= 1:
            return lab_of, merged
        c = min(small, key=lambda x: counts[x])                  # el más chico primero
        mean = {a: lab[lab_of == a].mean(axis=0) for a in alive}
        others = [a for a in alive if a != c]
        dest = min(others, key=lambda a: float(((mean[a] - mean[c]) ** 2).sum()))
        lab_of[lab_of == c] = dest
        merged += 1


# ------------------------------------------------------------------ paquete
def build_package(tm, proposal: Proposal, assign: dict, *, filter_px: int = FILTER_PX, max_atlas: int = MAX_ATLAS,
                  origen: dict | None = None, shade: bool = False) -> RolesPackage:
    """Aplica la asignación `{cluster: rol | None}` (varios clusters pueden ir al mismo rol; `None` = «sin rol», etiqueta 255).

    **No decide nada**: si algún cluster de la propuesta no está en `assign` levanta `RolesError` con la lista. El filtro de mayoría se
    aplica sobre las etiquetas **ya en roles** (dos tonos del mismo rol no son «ruido» entre sí). `roles` queda en el orden de primera
    aparición por cluster (del más grande al más chico).

    `shade=True`: además del atlas de etiquetas guarda el de **sombreado** (`shade_atlas`): cuánto más claro u oscuro que el promedio de su rol es cada
    píxel de la textura. Con `shade=False` (por defecto) el paquete es el de siempre."""
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
    atlas_shade = None
    if shade and roles:
        atlas_shade = shade_atlas(tm, proposal, assign, roles, lab)
    f = max(1, int(np.ceil(max(lab.shape) / max_atlas)))
    if f > 1:
        lab = np.ascontiguousarray(lab[::f, ::f])
        if atlas_shade is not None:
            atlas_shade = np.ascontiguousarray(atlas_shade[::f, ::f])
    return RolesPackage(np.asarray(tm.vertices, np.float32), np.asarray(tm.faces, np.int32), np.asarray(tm.corner_uv, np.float32),
                        lab, roles, dict(origen or {}), atlas_shade)


# ------------------------------------------------------------------ sombreado
_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)      # Rec. 709, sobre los valores sRGB 0–255 (sin linealizar: es lo que ve el ojo en la textura)


def _block_luma(img: np.ndarray, max_atlas: int) -> np.ndarray:
    """Luminancia (0–255, float32) de la textura reducida por promedio de bloques, con la **misma grilla** que `_classify_atlas` (mismo `f`, mismo
    tamaño de salida), así que cada píxel coincide con el de las etiquetas sin filtrar."""
    h, w = img.shape[:2]
    f = max(1, int(np.ceil(max(h, w) / max_atlas)))
    oh, ow = -(-h // f), -(-w // f)
    out = np.empty((oh, ow), np.float32)
    rows = max(1, _CHUNK_PX // ow)
    for r0 in range(0, oh, rows):
        r1 = min(oh, r0 + rows)
        blk = img[r0 * f:r1 * f].astype(np.float32)
        pad_h, pad_w = (r1 - r0) * f - blk.shape[0], ow * f - w
        if pad_h or pad_w:
            blk = np.pad(blk, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
        avg = blk.reshape(r1 - r0, f, ow, f, 3).mean(axis=(1, 3)) if f > 1 else blk
        out[r0:r1] = avg @ _LUMA
    return out


def shade_atlas(tm, proposal: Proposal, assign: dict, roles: list, lab: np.ndarray) -> np.ndarray:
    """Atlas de sombreado `(h, w) uint8` (128 = ×1,0) del paquete que `build_package` está armando; `lab` son sus etiquetas (ya en ids de rol, 255 = sin rol).

    Para cada rol, `luma_rol` es la luminancia media de los clusters que se le asignaron, **ponderada por el área que cubren en la superficie**
    (`proposal.share`, no por píxeles del atlas, que incluye el fondo sin usar). El factor de cada píxel es `luminancia del píxel / luma_rol`, acotado a
    `SHADE_MIN`…`SHADE_MAX` y guardado como `uint8` (`texmesh.shade_code`). Los píxeles sin rol y los roles casi negros (`SHADE_MIN_LUMA`) quedan en ×1,0.
    Como el cociente es de brillo solamente, el color de un rol recoloreado conserva su matiz (ver `texmesh.apply_shade`)."""
    luma = _block_luma(tm.image("base_color"), MAX_ATLAS)
    if luma.shape != lab.shape:
        raise RolesError(f"el atlas de sombreado ({luma.shape[1]}×{luma.shape[0]}) no coincide con el de etiquetas ({lab.shape[1]}×{lab.shape[0]})")
    centers_luma = np.asarray(proposal.centers_rgb, np.float32) @ _LUMA
    per_role = np.full(len(roles), 1.0, np.float32)
    for i, r in enumerate(roles):
        cs = [c for c in range(proposal.k) if assign[c] == r]
        w = np.asarray([proposal.share[c] for c in cs], np.float64)
        m = float((centers_luma[cs] * w).sum() / w.sum()) if cs and w.sum() > 0 else 0.0
        per_role[i] = m if m >= SHADE_MIN_LUMA else 0.0                 # 0 = «no sombrear este rol»
    base = np.concatenate([per_role, np.zeros(256 - len(roles), np.float32)])       # índice = etiqueta; 255 y los que sobran → 0
    mean = base[lab]
    factor = np.ones(lab.shape, np.float32)
    ok = mean > 0
    factor[ok] = luma[ok] / mean[ok]
    return shade_code(factor)


# ------------------------------------------------------------------ roles que sirven a la pieza (O10)
def role_shares(pkg, region=None) -> dict:
    """O10 · `{rol: % del área}` que cada rol cubre en los triángulos de `region` (máscara; por defecto todos). Los triángulos «sin rol» no suman a ningún
    rol, pero cuentan en el total."""
    F = np.asarray(pkg.faces, np.int64)
    tri = np.asarray(pkg.vertices, float)[F]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    reg = np.ones(len(F), bool) if region is None else np.asarray(region, bool)
    total = float(area[reg].sum())
    if total <= 0:
        return {}
    fr = face_roles(pkg)
    return {r: float(area[reg & (fr == i)].sum()) * 100.0 / total for i, r in enumerate(pkg.roles)}


def prune_small_roles(pkg, region, min_pct: float = ROLE_MIN_PCT) -> tuple:
    """O10 · `(paquete, [roles quitados])`: los roles que cubren menos de `min_pct` % de la región de la pieza no se guardan; sus píxeles del atlas quedan
    «sin rol» (255) y `roles` se renumera. Sin nada que quitar devuelve el mismo paquete. Los roles que no usa ninguna parte de la región (los del uniforme
    de un busto) también salen de la ficha: no son de la pieza."""
    shares = role_shares(pkg, region)
    drop = [r for r in pkg.roles if shares.get(r, 0.0) < min_pct]
    if not drop or len(drop) == len(pkg.roles):                 # nada que quitar; o quitaría todo (no se deja una pieza sin ningún rol)
        return pkg, []
    keep = [r for r in pkg.roles if r not in drop]
    remap = np.full(256, NO_ROLE, np.uint8)
    for i, r in enumerate(pkg.roles):
        if r in keep:
            remap[i] = keep.index(r)
    labels = remap[np.asarray(pkg.labels)]
    return RolesPackage(pkg.vertices, pkg.faces, pkg.corner_uv, labels, keep, dict(pkg.origen or {}), pkg.shade), drop
