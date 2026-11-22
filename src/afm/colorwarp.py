"""Ajuste no rígido del FBX a una pieza que NO es un trozo de él (cabeza de reemplazo con su propio FBX).

`colorpiece.align_fbx_to_piece` calza el FBX con la pieza por una **similitud** (escala + giro + traslación). Eso alcanza cuando el STL de
la pieza salió del mismo modelo que el FBX (un brazo cortado de la figura). Una cabeza de reemplazo es OTRA malla (otra versión del
modelo, re-mallada, con orejas y frente distintas): después del mejor calce rígido quedan 1–2 mm de diferencia en una cabeza de 20 mm. El
horneado y la vista previa deciden que una cara «tiene origen» si el FBX está a ≤ `ORIGIN_EPS_MM` (0,15 mm) con la normal compatible; con
esa diferencia solo ≈ 20 % del área la tiene, el resto copia el color de la cara con origen más cercana (casi siempre pelo) y la piel queda
a parches, sin frente ni pómulos.

`warp_fbx_to_piece` lleva los vértices del FBX (ya en el marco de la pieza) hacia la superficie de la pieza con un campo de
desplazamiento **suave**: en cada vuelta cada vértice se mueve hacia el punto más cercano de la pieza (solo si está a ≤ `reach` y las normales
son compatibles) y esos movimientos se promedian con pesos gaussianos entre los vértices vecinos, de modo que el FBX se deforma como una
lámina y no se arruga. Los UV no se tocan: la textura (y el atlas de roles) viaja pegada a los vértices.

`warp_package` aplica eso a un `RolesPackage` **guardando los vértices corregidos en el propio paquete** (en el marco del FBX, deshaciendo la
similitud `A`): la ficha de la pieza, `similitud` y todo lo que ya lee el paquete (vista previa, armado, retoques) siguen igual, sin formato nuevo.

Solo se aplica si mejora la cobertura medida (fracción del área de la pieza con origen a `COVER_EPS_MM`); si no, se devuelve el paquete tal cual.

**Compuerta (O07).** El ajuste NO rígido no puede disimular una pose mala: una pose correcta de la misma cabeza necesita décimas de mm de corrección,
no milímetros. Un FBX puesto al revés (cara en la nuca) calza por forma casi igual de bien y el ajuste lo «arreglaba» moviendo los vértices
2–4 mm en una cabeza de 20 mm, con lo que la ficha quedaba «alineada». Por eso `warp_package` no aplica (y marca `pose_sospechosa: True`, con el
`motivo`) si la cobertura previa es < `GATE_MIN_COVER`, si el desplazamiento medio pasa de `GATE_MEAN_EDGE` aristas de la pieza o si el máximo pasa de
`GATE_MAX_DIAG` de su diagonal. **Salvo que el frente esté verificado** (`front_ok=True`: O05/O06 midieron que la cara del FBX mira del lado del frente
de la pieza): con la cabeza real de Bolívar la pose CORRECTA también necesita 2,1 mm de desplazamiento medio y tiene 32 % de cobertura previa (la
v2 es otro modelado, no solo otra malla), así que los umbrales no distinguen la pose buena de la girada; lo que las distingue es el frente. En ese caso el
ajuste se aplica igual y el informe lleva `desplazamiento_alto: True` con el `motivo` como aviso.
`trimesh` se importa dentro de las funciones (D10).
"""
from __future__ import annotations

import numpy as np

ORIGIN_EPS_MM = 0.15          # la tolerancia de `colorbake`/`colorpreview` («tiene origen»)
COVER_EPS_MM = 0.30           # con la que se mide si el ajuste sirvió (el doble: la pieza y el FBX nunca son idénticos)
MIN_DOT = 0.3                 # normales compatibles (como el horneado)
WARP_MIN_MEAN_MM = 0.12       # por debajo de esta distancia media del calce rígido, el FBX ya es la misma malla: no se toca
MIN_GAIN = 0.02               # el ajuste se conserva si la cobertura sube al menos 2 puntos
REACH_FRAC = 0.09             # alcance de la búsqueda = esta fracción de la diagonal de la pieza (≈ 3 mm en una cabeza de 34 mm)
SIGMA_FRAC = 0.075            # ancho del suavizado gaussiano (en fracción de la diagonal)
ITERATIONS = 8
STEP = 0.8                    # fracción del desplazamiento que se aplica por vuelta
K_NEIGHBORS = 24
SURFACE_SAMPLES = 300_000
GATE_MIN_COVER = 0.5          # O07: con menos cobertura previa (a `COVER_EPS_MM`) la pose es sospechosa y el ajuste no se aplica
GATE_MEAN_EDGE = 0.5          # O07: el desplazamiento medio no puede pasar de esta fracción de la arista mediana de la pieza…
GATE_MAX_DIAG = 0.15          # O07: …ni el máximo de esta fracción de su diagonal


class _Surface:
    """Muestras densas de una malla (marco propio) con la normal de su cara: distancias punto-superficie sin `rtree`."""

    def __init__(self, V, F, samples: int = SURFACE_SAMPLES):
        from scipy.spatial import cKDTree

        V, F = np.asarray(V, float), np.asarray(F, np.int64)
        tri = V[F]
        cr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        norm = np.linalg.norm(cr, axis=1)
        area = 0.5 * norm
        if not np.isfinite(area).all() or area.sum() <= 0:
            raise ValueError("la malla no tiene superficie")
        counts = np.maximum(1, np.ceil(area / (area.sum() / samples)).astype(np.int64))
        face = np.repeat(np.arange(len(F)), counts)
        r = np.random.default_rng(0).random((len(face), 2))
        flip = r.sum(1) > 1
        r[flip] = 1 - r[flip]
        b = np.column_stack([1 - r.sum(1), r])
        self.xyz = np.einsum("nk,nkc->nc", b, tri[face])
        self.normals = (cr / np.maximum(norm, 1e-30)[:, None])[face]
        self.tree = cKDTree(self.xyz)

    def nearest(self, P):
        """(distancia a la muestra más cercana, punto, normal de su cara)."""
        d, i = self.tree.query(P)
        return d, self.xyz[i], self.normals[i]


def _vertex_normals(V, F) -> np.ndarray:
    import trimesh

    return np.asarray(trimesh.Trimesh(V, F, process=False).vertex_normals, float)


def coverage(piece_V, piece_F, fbx_V, fbx_F, eps: float = COVER_EPS_MM, min_dot: float = MIN_DOT) -> float:
    """Fracción (0–1) del **área de la pieza** cuyo centro de cara tiene la superficie del FBX a ≤ `eps` con la normal compatible.
    Es la misma regla de «tiene origen» del horneado, con otra tolerancia."""
    pV, pF = np.asarray(piece_V, float), np.asarray(piece_F, np.int64)
    tri = pV[pF]
    cr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    norm = np.linalg.norm(cr, axis=1)
    area = 0.5 * norm
    normals = cr / np.maximum(norm, 1e-30)[:, None]
    centers = tri.mean(axis=1)
    fV, fF = np.asarray(fbx_V, float), np.asarray(fbx_F, np.int64)
    from scipy.spatial import cKDTree

    # solo las caras del FBX cerca de la pieza (el busto entero no importa y diluye las muestras)
    near = cKDTree(centers).query(fV[fF].mean(axis=1))[0] <= max(eps * 8.0, 2.0)
    if not near.any():
        return 0.0
    surf = _Surface(fV, fF[near])
    d, _, n = surf.nearest(centers)
    ok = (d <= eps) & (np.einsum("nc,nc->n", n, normals) > min_dot)
    return float((area * ok).sum() / area.sum())


def warp_fbx_to_piece(fbx_V, fbx_F, piece_V, piece_F, *, iterations: int = ITERATIONS, reach: float | None = None,
                      sigma: float | None = None, step: float = STEP, min_dot: float = MIN_DOT):
    """Vértices del FBX (en el marco de la pieza) deformados hacia la superficie de la pieza. Devuelve `(V_nuevos, informe)`.

    `reach` y `sigma` (mm) por defecto son fracciones de la diagonal de la pieza. El desplazamiento total de un vértice no pasa de `1,5 · reach`."""
    from scipy.spatial import cKDTree

    V0 = np.asarray(fbx_V, float)
    F = np.asarray(fbx_F, np.int64)
    pV = np.asarray(piece_V, float)
    diag = float(np.linalg.norm(np.ptp(pV, axis=0)))
    reach = float(reach if reach is not None else REACH_FRAC * diag)
    sigma = float(sigma if sigma is not None else SIGMA_FRAC * diag)
    surf = _Surface(pV, piece_F)
    V = V0.copy()
    n_good = 0
    for _ in range(max(1, iterations)):
        d, nearest, pn = surf.nearest(V)
        good = (d < reach) & (np.einsum("nc,nc->n", pn, _vertex_normals(V, F)) > min_dot)
        n_good = int(good.sum())
        if n_good < 8:
            break
        disp = nearest - V
        anchors = np.where(good)[0]
        k = min(K_NEIGHBORS, len(anchors))
        dist, idx = cKDTree(V[anchors]).query(V, k=k, distance_upper_bound=3.0 * sigma)
        dist, idx = dist.reshape(len(V), -1), idx.reshape(len(V), -1)
        valid = np.isfinite(dist)
        w = np.where(valid, np.exp(-0.5 * (np.where(valid, dist, 0.0) / sigma) ** 2), 0.0)
        pick = anchors[np.where(valid, idx, 0)]
        wsum = w.sum(axis=1)
        smooth = np.einsum("nk,nkc->nc", w, disp[pick]) / np.maximum(wsum, 1e-9)[:, None]
        smooth *= np.minimum(1.0, wsum / 3.0)[:, None]               # lejos de toda ancla (el resto del busto) el desplazamiento se apaga
        V = V + step * smooth
        move = V - V0
        over = np.linalg.norm(move, axis=1) > 1.5 * reach
        if over.any():
            m = np.linalg.norm(move[over], axis=1)
            V[over] = V0[over] + move[over] * (1.5 * reach / m)[:, None]
    mv = np.linalg.norm(V - V0, axis=1)
    return V, {"reach_mm": round(reach, 3), "sigma_mm": round(sigma, 3), "vueltas": int(iterations), "anclas": n_good,
               "desplazamiento_medio_mm": round(float(mv[mv > 1e-6].mean()) if (mv > 1e-6).any() else 0.0, 4),
               "desplazamiento_max_mm": round(float(mv.max()), 4)}


def _gate_reasons(before: float, rep: dict, edge: float, diag: float) -> list[str]:
    """Motivos (texto) por los que la pose se considera sospechosa y el ajuste no rígido no se aplica; vacío si pasa la compuerta (O07)."""
    why = []
    if before < GATE_MIN_COVER:
        why.append(f"cobertura previa {before * 100:.0f} % < {GATE_MIN_COVER * 100:.0f} %")
    if rep and edge > 0 and rep["desplazamiento_medio_mm"] > GATE_MEAN_EDGE * edge:
        why.append(f"desplazamiento medio {rep['desplazamiento_medio_mm']:.2f} mm > {GATE_MEAN_EDGE * edge:.2f} mm ({GATE_MEAN_EDGE:g} aristas)")
    if rep and rep["desplazamiento_max_mm"] > GATE_MAX_DIAG * diag:
        why.append(f"desplazamiento máximo {rep['desplazamiento_max_mm']:.2f} mm > {GATE_MAX_DIAG * diag:.2f} mm ({GATE_MAX_DIAG * 100:.0f} % de la diagonal)")
    return why


def warp_package(pkg, similarity, piece_mesh, *, min_gain: float = MIN_GAIN, gate: bool = True, front_ok: bool | None = None):
    """`(RolesPackage, informe)`: el paquete con los vértices del FBX ajustados a la pieza (o `pkg` tal cual si el ajuste no mejora).

    `similarity`: 4×4 FBX → marco de la pieza (la de `align_fbx_to_piece`). Los vértices nuevos se guardan en el marco del FBX
    (`A⁻¹ · V'`), así `similitud` sigue valiendo. El informe lleva `aplicada`, `cobertura_antes`/`cobertura_despues` (a `COVER_EPS_MM`) y las
    medidas del desplazamiento.

    Compuerta (O07, `gate=True`): si la pose es sospechosa (cobertura previa baja o desplazamiento grande, ver el docstring del módulo) el ajuste
    no se aplica; el informe lleva `pose_sospechosa: True` y `motivo`. Con la cobertura previa baja ni siquiera se calcula el ajuste.
    `front_ok=True` (el frente ya se verificó): los motivos no frenan el ajuste, quedan como aviso (`desplazamiento_alto`, `motivo`)."""
    from .colorroles import RolesPackage
    from .frame import _median_edge

    A = np.asarray(similarity, float)
    Ainv = np.linalg.inv(A)
    V_fbx = np.asarray(pkg.vertices, float)
    V_piece = V_fbx @ A[:3, :3].T + A[:3, 3]
    pV, pF = np.asarray(piece_mesh.vertices, float), np.asarray(piece_mesh.faces, np.int64)
    edge, diag = _median_edge(pV, pF), float(np.linalg.norm(np.ptp(pV, axis=0)))
    before = coverage(pV, pF, V_piece, pkg.faces)
    rep = {"cobertura_eps_mm": COVER_EPS_MM, "cobertura_antes": round(before, 4)}
    verified = front_ok is True
    if gate and not verified and before < GATE_MIN_COVER:
        rep.update({"aplicada": False, "pose_sospechosa": True, "motivo": "; ".join(_gate_reasons(before, {}, edge, diag))})
        return pkg, rep
    V_new, warp_rep = warp_fbx_to_piece(V_piece, pkg.faces, pV, pF)
    rep.update(warp_rep)
    why = _gate_reasons(before, warp_rep, edge, diag) if gate else []
    if why and not verified:
        rep.update({"aplicada": False, "pose_sospechosa": True, "motivo": "; ".join(why)})
        return pkg, rep
    if why:                                                  # frente verificado: el ajuste grande es del modelado, no de la pose
        rep.update({"desplazamiento_alto": True, "motivo": "; ".join(why)})
    after = coverage(pV, pF, V_new, pkg.faces)
    rep["cobertura_despues"] = round(after, 4)
    if after < before + min_gain:
        rep["aplicada"] = False
        return pkg, rep
    rep["aplicada"] = True
    back = V_new @ Ainv[:3, :3].T + Ainv[:3, 3]
    return RolesPackage(back.astype(np.float32), pkg.faces, pkg.corner_uv, pkg.labels, list(pkg.roles), dict(pkg.origen or {})), rep
