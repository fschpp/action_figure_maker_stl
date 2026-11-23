"""Color de la fuente (malla original con textura) para puntos 3D (plan §6–§7.3, T07).

Camino por defecto (D4): muestras densas sobre la superficie (una cada ~0,08 mm) con su cara, baricéntricas y color, en un
`cKDTree`. No usa `rtree`. La malla original no se modifica; se la lleva al marco de la figura con un `FigureFrame`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .texmesh import TexturedMesh

FILL_LINK_FACTOR = 3.0           # «rellenar región»: dos muestras se unen si están a ≤ 3 × espaciado (las muestras son aleatorias, no una grilla)
MAX_SAMPLES = 6_000_000          # RG5: tope de memoria de la fuente densa; si se pasa, sube el espaciado


@dataclass
class ColorQuery:
    rgb: np.ndarray              # (n, 3) uint8
    dist: np.ndarray             # (n,) float32: distancia (mm) al triángulo de la muestra más cercana
    has_origin: np.ndarray       # (n,) bool: dist ≤ eps y normal compatible
    face: np.ndarray             # (n,) int32
    label: np.ndarray | None = None   # (n,) uint8: etiqueta de rol de la muestra (solo fuentes con etiquetas, T45); None en las demás
    # R11: puntos que una pincelada «sin origen» (`Stroke.huerfana`) pintó. Nunca coincide con `has_origin` (esas pinceladas solo tocan puntos
    # sin origen). `None` = la fuente no tiene pinceladas de ese tipo (o una consulta armada sin el campo): equivale a «ninguno».
    assigned: np.ndarray | None = None   # (n,) bool


class ColorSource:
    def __init__(self, tm: TexturedMesh, frame=None, spacing_mm: float = 0.08, seed: int = 0):
        from scipy.spatial import cKDTree

        V = np.asarray(tm.vertices, float)
        V = V if frame is None else frame.apply(V)
        self.faces = np.asarray(tm.faces, np.int32)
        self.vertices = V
        self._tris = V[self.faces]
        cr = np.cross(self._tris[:, 1] - self._tris[:, 0], self._tris[:, 2] - self._tris[:, 0])
        self.area = 0.5 * np.linalg.norm(cr, axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            n = cr / np.linalg.norm(cr, axis=1, keepdims=True)
        self.face_normals = np.nan_to_num(n)
        self._tm = tm
        self._mesh = None
        self.spacing_mm = float(spacing_mm)
        counts = self._counts(self.spacing_mm)
        while counts.sum() > MAX_SAMPLES:                       # tope de memoria: espaciado mayor
            self.spacing_mm *= 1.1 * float(np.sqrt(counts.sum() / MAX_SAMPLES))
            counts = self._counts(self.spacing_mm)
        rng = np.random.default_rng(seed)
        face = np.repeat(np.arange(len(self.faces), dtype=np.int32), counts)
        r = rng.random((len(face), 2))
        flip = r.sum(axis=1) > 1.0
        r[flip] = 1.0 - r[flip]
        bary = np.column_stack([1.0 - r.sum(axis=1), r]).astype(np.float32)
        self.sample_face, self.sample_bary = face, bary
        self.sample_xyz = np.einsum("nk,nkc->nc", bary.astype(np.float64), self._tris[face])
        # T41: con etiquetas de rol se guarda también la etiqueta de cada muestra, así `recolor` cambia el color sin rehacer el árbol
        self.sample_label = tm.sample_labels(face, bary) if tm.labels is not None else None
        # T45: ids de rol de las etiquetas (índice = etiqueta), para recolorear lo ya horneado con otra paleta
        self.label_roles = list(tm.info.get("roles", [])) if tm.labels is not None else None
        self.sample_rgb = tm.sample(face, bary, "base_color")
        self._lut = None if tm.labels is None or tm.lut is None else np.asarray(tm.lut)       # R02: para `paint`
        self._base_label = None                                 # etiquetas del paquete, antes de pintar (las crea `paint`)
        self._orphans: list = []                                # R11: pinceladas «sin origen» como (Stroke, etiqueta); las aplica `query`, no tocan las muestras
        tm.release_image("base_color")                          # RG5: se libera la imagen decodificada
        self._tree = cKDTree(self.sample_xyz)
        self._coarse = cKDTree(self.sample_xyz[::4])

    def recolor(self, lut) -> None:
        """Cambia el color de las muestras con otra tabla `lut` (256, 3) uint8 (`sample_rgb = lut[etiqueta]`) sin tocar geometría ni
        el `cKDTree`. Solo para fuentes armadas con etiquetas de rol."""
        if self.sample_label is None:
            raise ValueError("la fuente no se armó con etiquetas de rol: no se puede recolorear")
        lut = np.asarray(lut)
        if lut.shape != (256, 3) or lut.dtype != np.uint8:
            raise ValueError("la tabla de colores debe ser (256, 3) uint8")
        self.sample_rgb = lut[self.sample_label]
        self._lut = lut                                         # R11: las pinceladas «sin origen» toman el color de esta misma tabla

    def paint(self, strokes) -> dict:
        """Aplica pinceladas de retoque (`retouch.Stroke`, **ya en el marco de esta fuente**, ver `retouch.transform_strokes`) a las muestras.

        Cada pincelada pasa a `rol` las muestras a menos de `r` mm de su centro; con normal `n`, solo las de normal compatible (`MIN_DOT`):
        no atraviesa una solapa fina. `rol = None` (borrador) devuelve las muestras a su etiqueta original. Se aplican en orden, la última gana.
        Una pincelada de **relleno** (`Stroke.relleno`) no pinta toda la esfera sino solo la región conectada de la semilla (`_fill_region`).
        El rol tiene que estar en `label_roles` (se agregan al armar la fuente con `RolesPackage.with_roles`); si no, `ValueError` con la lista.
        Después recalcula `sample_rgb = lut[sample_label]`. Informe: `{"pinceladas": n, "muestras": cantidad de muestras que cambiaron}`.

        R11: las pinceladas **«sin origen»** (`Stroke.huerfana`) no cambian ninguna muestra: la fuente solo las recuerda y `query` las aplica a los
        puntos consultados que **no tienen origen** (nada a ≤ `eps` con normal compatible), dentro de su radio. El informe suma `"sin_origen": n`.
        Quedan en el orden en que llegaron: entre ellas la última gana; las del pincel normal no las pisan (solo cambian muestras, y un punto sin origen no lee muestras)."""
        from .retouch import MIN_DOT

        strokes = list(strokes)
        if not strokes:
            return {"pinceladas": 0, "muestras": 0}
        if self.sample_label is None or self._lut is None or self.label_roles is None:
            raise ValueError("la fuente no se armó con etiquetas de rol: no se puede retocar")
        index = {r: i for i, r in enumerate(self.label_roles)}
        missing = sorted({s.rol for s in strokes if s.rol is not None and s.rol not in index})
        if missing:
            raise ValueError("la fuente no tiene etiqueta para los roles del retoque: " + ", ".join(missing))
        total = len(strokes)
        orphans = [s for s in strokes if getattr(s, "huerfana", False)]
        strokes = [s for s in strokes if not getattr(s, "huerfana", False)]
        self._orphans.extend((s, index[s.rol]) for s in orphans)
        if self._base_label is None:
            self._base_label = self.sample_label.copy()
        before = self.sample_label.copy()
        sample_normals = None
        for s in strokes:
            idx = np.asarray(self._tree.query_ball_point(np.asarray(s.c, float), float(s.r)), np.int64)
            if idx.size == 0:
                continue
            if s.n is not None:
                if sample_normals is None:
                    sample_normals = self.face_normals[self.sample_face]
                idx = idx[sample_normals[idx] @ np.asarray(s.n, float) > MIN_DOT]
                if idx.size == 0:
                    continue
            if getattr(s, "relleno", False):
                idx = self._fill_region(s, idx)                 # solo la región conectada de la semilla, no toda la esfera
                if idx.size == 0:
                    continue
            self.sample_label[idx] = self._base_label[idx] if s.rol is None else np.uint8(index[s.rol])
        self.sample_rgb = self._lut[self.sample_label]
        report = {"pinceladas": total, "muestras": int((self.sample_label != before).sum())}
        if orphans:
            report["sin_origen"] = len(orphans)
        return report

    def _fill_region(self, stroke, idx: np.ndarray) -> np.ndarray:
        """«Rellenar región»: de las muestras candidatas `idx` (dentro del alcance y con normal compatible), las que forman una sola región con la
        semilla: la muestra candidata más cercana a `stroke.c`; la región son las muestras con **su misma etiqueta actual** unidas entre sí por
        saltos de a lo sumo `FILL_LINK_FACTOR` × espaciado. Una mancha suelta de otro rol queda separada de las demás, y la solapa de atrás no se
        alcanza (ya la sacó el filtro de normal). Devuelve los índices de la región (vacío si no hay semilla)."""
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        from scipy.spatial import cKDTree

        c = np.asarray(stroke.c, float)
        seed_at = int(np.argmin(np.linalg.norm(self.sample_xyz[idx] - c, axis=1)))
        label = self.sample_label[idx[seed_at]]
        keep = np.flatnonzero(self.sample_label[idx] == label)
        if keep.size == 0:
            return keep
        cand = idx[keep]
        seed_local = int(np.flatnonzero(keep == seed_at)[0])
        pairs = cKDTree(self.sample_xyz[cand]).query_pairs(FILL_LINK_FACTOR * self.spacing_mm, output_type="ndarray")
        n = len(cand)
        g = coo_matrix((np.ones(len(pairs), np.int8), (pairs[:, 0], pairs[:, 1])), shape=(n, n)) if len(pairs) else coo_matrix((n, n), dtype=np.int8)
        _, comp = connected_components(g, directed=False)
        return cand[comp == comp[seed_local]]

    def _counts(self, spacing: float) -> np.ndarray:
        return np.maximum(1, np.ceil(self.area / (spacing * spacing)).astype(np.int64))

    def to_trimesh(self):
        import trimesh

        if self._mesh is None:
            self._mesh = trimesh.Trimesh(self.vertices, self.faces, process=False)
        return self._mesh

    @property
    def mesh(self):
        """Geometría de la fuente en el marco de la figura (`trimesh.Trimesh`, perezosa)."""
        return self.to_trimesh()

    # ------------------------------------------------------------------ consulta
    def query(self, points, normals=None, eps: float = 0.15, min_dot: float = 0.3, workers: int = 1) -> ColorQuery:
        import trimesh

        P = np.atleast_2d(np.asarray(points, float))
        _, idx = self._tree.query(P, workers=workers)
        face = self.sample_face[idx]
        # distancia exacta al triángulo de la muestra más cercana (no sobreestima por el espaciado)
        cp = trimesh.triangles.closest_point(self._tris[face], P)
        dist = np.linalg.norm(cp - P, axis=1)
        ok = dist <= eps
        if normals is not None:
            ok &= np.einsum("nc,nc->n", self.face_normals[face], np.asarray(normals, float)) > min_dot
        label = None if self.sample_label is None else self.sample_label[idx]
        rgb = self.sample_rgb[idx]
        assigned = None
        if self._orphans:
            rgb, label, assigned = self._apply_orphans(P, normals, rgb, label, ok)
        return ColorQuery(rgb, dist.astype(np.float32), ok, face.astype(np.int32), label, assigned)

    def _apply_orphans(self, P, normals, rgb, label, ok):
        """R11: aplica las pinceladas «sin origen» a los puntos de `P` que no tienen origen (`~ok`): los que caen a ≤ `r` de su centro (y, con normal
        `n` en la pincelada y `normals` en la consulta, con normal compatible) toman el color y la etiqueta del rol. En orden: la última gana.
        Devuelve `(rgb, label, assigned)` con copias; no toca `sample_*`."""
        from scipy.spatial import cKDTree

        from .retouch import MIN_DOT

        rgb = np.array(rgb, copy=True)
        label = None if label is None else np.array(label, copy=True)
        assigned = np.zeros(len(P), bool)
        todo = np.flatnonzero(~ok)
        if todo.size == 0:
            return rgb, label, assigned
        tree = cKDTree(P[todo])
        nrm = None if normals is None else np.asarray(normals, float)
        for s, lab in self._orphans:
            hit = np.asarray(tree.query_ball_point(np.asarray(s.c, float), float(s.r)), np.int64)
            if hit.size == 0:
                continue
            sel = todo[hit]
            if s.n is not None and nrm is not None:
                sel = sel[nrm[sel] @ np.asarray(s.n, float) > MIN_DOT]
                if sel.size == 0:
                    continue
            rgb[sel] = self._lut[lab]
            if label is not None:
                label[sel] = np.uint8(lab)
            assigned[sel] = True
        return rgb, label, assigned

    def distance(self, points, workers: int = 1, upper: float | None = None) -> np.ndarray:
        """Distancia gruesa (una muestra de cada 4), para puntuar registros. Con `upper`, los puntos más lejos que eso
        devuelven `inf` sin terminar la búsqueda (mucho más rápido en los barridos del registro, donde casi todos los
        giros de prueba dejan la pieza lejos de la fuente)."""
        P = np.atleast_2d(np.asarray(points, float))
        kw = {} if upper is None else {"distance_upper_bound": float(upper)}
        return self._coarse.query(P, workers=workers, **kw)[0].astype(np.float32)
