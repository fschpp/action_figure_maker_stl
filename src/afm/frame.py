"""Marco de la figura: UNA matriz 4×4 archivo → marco de trabajo (plan §6–§7.2, T03/T04).

El motor aplica a la geometría: `orient_z_up` → escala → `repair` → `normalize` (pies en z=0, centrada) → giro de 180° sobre Z si
`front == "-Y"` → `align_to_symmetry`. `FigureFrame` compone lo mismo en 4×4 para llevar la fuente de color al mismo marco.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np


@dataclass
class FigureFrame:
    matrix: np.ndarray                      # (4, 4): archivo -> marco de la figura (mm, pies en z=0, centrada, simetría en x=0)
    scale: float = 1.0
    report: dict = field(default_factory=dict)

    def apply(self, points) -> np.ndarray:
        p = np.atleast_2d(np.asarray(points, float))
        return p @ self.matrix[:3, :3].T + self.matrix[:3, 3]

    def det(self) -> float:
        """Determinante de la parte 3×3 dividido por `scale³`: +1 = solo rotación (sin espejo)."""
        return float(np.linalg.det(self.matrix[:3, :3]) / self.scale ** 3)

    @classmethod
    def from_load(cls, report, front: str = "+Y", symmetry=None) -> "FigureFrame":
        """`M = A · F · N`: `N` = orientar + escalar + apoyar + centrar (igual que `LoadReport.to_figure`), `F` = giro de 180° sobre Z si
        el frente es −Y, `A` = lo que hace `align_to_symmetry` (rotación sobre Z + traslado en x)."""
        R = np.eye(3) if not report.orient_matrix else np.asarray(report.orient_matrix, float).reshape(3, 3)
        N = np.eye(4)
        N[:3, :3] = report.scale * R
        N[:3, 3] = [-report.center_xy[0], -report.center_xy[1], -report.scale * report.zmin_file]
        # `to_figure` trabaja sobre puntos ya orientados: el traslado de z es -scale·zmin_file en el marco orientado
        F = np.diag([-1.0, -1.0, 1.0, 1.0]) if front == "-Y" else np.eye(4)
        A = _symmetry_matrix(symmetry)
        return cls(A @ F @ N, float(report.scale))

    @staticmethod
    def engine_post(front: str = "+Y", symmetry=None) -> np.ndarray:
        """4×4 de lo que el motor hace DESPUÉS de cargar la figura: giro de 180° sobre Z si el frente es −Y y `align_to_symmetry`
        (`A · F` de `from_load`). Sirve para llevar a la fuente al marco del motor cuando se alineó contra el STL tal como se cargó."""
        F = np.diag([-1.0, -1.0, 1.0, 1.0]) if front == "-Y" else np.eye(4)
        return _symmetry_matrix(symmetry) @ F

    def then(self, post: np.ndarray) -> "FigureFrame":
        """Marco compuesto: primero este, después `post` (4×4 rígida, sin escala). Conserva `scale` y `report`."""
        return FigureFrame(np.asarray(post, float) @ self.matrix, self.scale, self.report)

    @classmethod
    def from_similarity(cls, source_vertices, target_mesh, refine: bool | None = None, source_faces=None) -> "FigureFrame":
        """Modo B (plan §7.2, T04): lleva los vértices de la fuente (FBX) al marco de la geometría `target_mesh` (el STL ya trabajado).

        (1) rotación de `orient_z_up_matrix`; (2) escala = alto del destino / alto de la fuente, cajas centradas en X e Y y z mínimo
        apoyado; (3) ICP de similitud solo si `refine` es True, o si es None y la media sin ICP supera `ICP_AUTO_MEAN_MM` (D11);
        (4) se mide la distancia de los vértices del destino a la superficie de la fuente y se carga `report`.
        `source_faces` (opcional) permite medir contra la superficie; sin ellas se mide contra los vértices (cota superior).
        Solo rotaciones propias: det(R) = +1.
        """
        import trimesh

        from .meshio import orient_z_up_matrix

        V = np.asarray(source_vertices.vertices if hasattr(source_vertices, "vertices") else source_vertices, float)
        if source_faces is None and hasattr(source_vertices, "faces"):
            source_faces = source_vertices.faces
        Q = np.asarray(target_mesh.vertices, float)
        R0 = orient_z_up_matrix(trimesh.PointCloud(V))
        Vo = V @ R0.T
        lo, hi = Vo.min(0), Vo.max(0)
        tlo, thi = Q.min(0), Q.max(0)
        s = float(thi[2] - tlo[2]) / float(hi[2] - lo[2])
        t = np.array([(tlo[0] + thi[0]) / 2 - s * (lo[0] + hi[0]) / 2, (tlo[1] + thi[1]) / 2 - s * (lo[1] + hi[1]) / 2, tlo[2] - s * lo[2]])
        M = _similarity_matrix(s * R0, t)
        surf = _SourceSurface(V, source_faces)
        stats = _target_distances(surf, M, Q)
        iters = 0
        if refine is True or (refine is None and stats[0] > ICP_AUTO_MEAN_MM):
            M2, stats2, iters = _icp_similarity(surf, M, Q, stats)
            if stats2[0] < stats[0] or refine is True:
                M, stats = M2, stats2
            else:
                iters = 0
        scale = float(np.cbrt(abs(np.linalg.det(M[:3, :3]))))
        mean, p95, mx = stats
        rep = {"media_mm": mean, "p95_mm": p95, "max_mm": mx, "ok": bool(mean <= SIMILARITY_OK_MM), "iteraciones": int(iters)}
        return cls(M, scale, rep)

    @classmethod
    def from_piece_similarity(cls, source_vertices, piece_mesh, refine: bool | None = None, source_faces=None) -> "FigureFrame":
        """Como `from_similarity`, pero para una PIEZA suelta (T32): no supone Z arriba ni la misma altura, porque el STL de una pieza
        (una cabeza, una mano) puede estar en cualquier giro respecto del FBX.

        (1) momentos de la superficie de ambas (muestras por área): escala = raíz del cociente de las trazas de covarianza, traslado de
        centroide a centroide; (2) las 24 rotaciones propias que llevan los ejes principales de la fuente sobre los del destino se
        puntúan con una submuestra de vértices del destino; (3) ICP de similitud sobre las 3 mejores (con `refine=True` siempre, con `False`
        nunca y con None solo si la media sin ICP supera `ICP_AUTO_MEAN_MM`) y se queda la de menor media. Solo rotaciones propias.
        El informe es el de `from_similarity`."""
        V = np.asarray(source_vertices.vertices if hasattr(source_vertices, "vertices") else source_vertices, float)
        if source_faces is None and hasattr(source_vertices, "faces"):
            source_faces = source_vertices.faces
        Q = np.asarray(piece_mesh.vertices, float)
        QF = getattr(piece_mesh, "faces", None)
        surf = _SourceSurface(V, source_faces)
        mom_s = _surface_moments(V, source_faces)
        mom_t = _surface_moments(Q, QF)
        (cs, Cs), (ct, Ct) = mom_s, mom_t
        s = float(np.sqrt(np.trace(Ct) / np.trace(Cs)))
        _, Es = np.linalg.eigh(Cs)
        _, Et = np.linalg.eigh(Ct)
        sub = Q if len(Q) <= PIECE_SCORE_POINTS else Q[np.random.default_rng(0).choice(len(Q), PIECE_SCORE_POINTS, replace=False)]
        cands = []
        for perm in itertools.permutations(range(3)):
            for signs in itertools.product((1.0, -1.0), repeat=3):
                R = Et @ (np.eye(3)[list(perm)] * np.array(signs)[:, None]) @ Es.T
                if np.linalg.det(R) < 0:
                    continue
                M = _similarity_matrix(s * R, ct - s * R @ cs)
                cands.append((_target_distances(surf, M, sub)[0], M))
        cands.sort(key=lambda c: c[0])
        best, best_stats, iters = None, None, 0
        for k, (_, M) in enumerate(cands[:3]):
            stats = _target_distances(surf, M, Q)
            it = 0
            if refine is True or (refine is None and stats[0] > ICP_AUTO_MEAN_MM):
                M2, stats2, it = _icp_similarity(surf, M, Q, stats)
                if stats2[0] < stats[0] or refine is True:
                    M, stats = M2, stats2
                else:
                    it = 0
            if best_stats is None or stats[0] < best_stats[0]:
                best, best_stats, iters = M, stats, it
            if refine is None and stats[0] <= ICP_AUTO_MEAN_MM:     # ya calza sin ICP: las demás candidatas sobran
                break
        scale = float(np.cbrt(abs(np.linalg.det(best[:3, :3]))))
        mean, p95, mx = best_stats
        rep = {"media_mm": mean, "p95_mm": p95, "max_mm": mx, "ok": bool(mean <= SIMILARITY_OK_MM), "iteraciones": int(iters)}
        return cls(best, scale, rep)


    @classmethod
    def from_cut_piece_similarity(cls, source_vertices, piece_mesh, source_faces=None, keep: float | None = None,
                                  front_hint: "FrontHint | None" = None) -> "FigureFrame":
        """Como `from_piece_similarity`, pero para una pieza que es un **trozo recortado** de la fuente: una cabeza cortada en la base del
        cuello, con su tapa plana, su pasador y quizá hueca por dentro. Esas partes no están en la superficie del FBX y inflaban la media
        de la alineación estricta (que pedía ≤ 0,1 mm con TODOS los vértices), así que la pieza no calzaba aunque fuera la misma cabeza.

        Mide la superficie de la pieza (muestras por área) contra la del FBX y se queda solo con la fracción `keep` que mejor calza
        (distancia recortada): la tapa, el hueco y el agujero quedan afuera. Arranca de las 24 rotaciones de los ejes principales × varias escalas
        y traslaciones (los momentos de un trozo no coinciden con los del todo), puntúa cada arranque con la distancia recortada y refina
        con un ICP de similitud recortado los mejores. El informe es el de `from_piece_similarity` más `cubierta` (fracción de la pieza a
        menos de `2 · SIMILARITY_OK_MM` del FBX); `media_mm` / `p95_mm` / `max_mm` son de la fracción que calza. `ok` pide además `cubierta` ≥
        `CUT_MIN_COVER`, para que dos formas distintas no pasen por coincidir en un pedazo. Solo rotaciones propias (sin espejo).

        El calce es **ciego al sentido adelante/atrás**: una cabeza con mucho pelo es casi igual de redonda por delante que por detrás y la
        distancia recortada tira justo lo que las distingue. Con `front_hint` (O05) se decide ese giro de 180° sobre el eje del cuello: se
        prueba también la pose opuesta (re-afinada) y se queda la que deja la cara del FBX del lado del frente de la pieza; sin dirección de
        cara, la que mejor calza, y si la diferencia es menor que `FRONT_AMBIGUOUS_MM` el resultado queda `ambigua` y `ok = False`. El informe suma
        `frente` (`fbx`: hacia dónde mira la cara, `pieza`: el frente, `angulo_deg`), `frente_ok`, `ambigua` y `yaw180` (`media_alt_mm`, `margen_mm`, `girada`)."""
        keep = CUT_KEEP if keep is None else float(keep)
        V = np.asarray(source_vertices.vertices if hasattr(source_vertices, "vertices") else source_vertices, float)
        if source_faces is None and hasattr(source_vertices, "faces"):
            source_faces = source_vertices.faces
        Q0 = np.asarray(piece_mesh.vertices, float)
        QF = getattr(piece_mesh, "faces", None)
        surf = _SourceSurface(V, source_faces, samples=CUT_SOURCE_SAMPLES)
        Q = _cut_piece_points(Q0, QF)
        (cs, Cs), (ct, Ct) = _surface_moments(V, source_faces), _surface_moments(Q0, QF)
        s0 = float(np.sqrt(np.trace(Ct) / np.trace(Cs)))
        _, Es = np.linalg.eigh(Cs)
        _, Et = np.linalg.eigh(Ct)
        sub = Q[np.random.default_rng(0).choice(len(Q), min(len(Q), CUT_COARSE_POINTS), replace=False)]
        step = 0.5 * float(np.sqrt(np.trace(Ct)))
        offsets = [np.zeros(3)] + [sg * step * Et[:, i] for i in range(3) for sg in (1.0, -1.0)]
        starts = []
        for perm in itertools.permutations(range(3)):
            for signs in itertools.product((1.0, -1.0), repeat=3):
                R = Et @ (np.eye(3)[list(perm)] * np.array(signs)[:, None]) @ Es.T
                if np.linalg.det(R) < 0:
                    continue
                for f in CUT_SCALES:
                    for off in offsets:
                        M = _similarity_matrix(f * s0 * R, ct + off - f * s0 * R @ cs)
                        starts.append((_trimmed(surf, M, sub, keep)[0], M))
        starts.sort(key=lambda c: c[0])
        best, best_stats = None, None
        Qi = Q[np.random.default_rng(1).choice(len(Q), min(len(Q), CUT_ICP_POINTS), replace=False)]
        rough = [_icp_trimmed(surf, M, Qi, keep, iters=8) for _, M in starts[:CUT_REFINE]]
        # O11: arranques con la vertical conocida. La puntuación gruesa no los distingue (un arranque a 10° del bueno puntúa peor que uno malo con la
        # traslación mejor puesta), así que cada uno pasa por un ICP barato y solo los mejores siguen.
        Qp = Q[np.random.default_rng(2).choice(len(Q), min(len(Q), CUT_PRIOR_ICP_POINTS), replace=False)]
        cheap = sorted((_icp_trimmed(surf, M, Qp, keep, iters=CUT_PRIOR_ICP_ITER) for M in _prior_starts(V, Q0, ct, s0)), key=lambda c: c[1][0])
        rough += [_icp_trimmed(surf, M, Qi, keep, iters=8) for M, _ in cheap[:CUT_PRIOR_TOP]]
        rough.sort(key=lambda c: c[1][0])
        for M, _ in rough[:CUT_FINAL]:
            M, stats = _icp_trimmed(surf, M, Qi, keep)
            M, stats = _polish_trimmed(surf, M, Qi, keep)
            if best_stats is None or stats[0] < best_stats[0]:
                best, best_stats = M, stats
        front_rep = {}
        if front_hint is not None:
            best, best_stats, front_rep = _resolve_front(surf, best, best_stats, Qi, keep, front_hint)
        return cls(*_cut_result(surf, best, Q, Q0, QF, keep, front_rep))

    @classmethod
    def from_cut_piece_pose(cls, source_vertices, piece_mesh, start, source_faces=None, keep: float | None = None,
                            front_hint: "FrontHint | None" = None) -> "FigureFrame":
        """O08 · Re-afina una pose DADA (`start`, 4×4 FBX → pieza) para una pieza recortada, sin buscar entre los 24 arranques: el ICP recortado y el
        afinado directo parten de `start`. Sirve para «Girar 180°»: la persona (o `yaw_matrix`) ya decidió hacia dónde mira la cara y solo falta
        que calce. El informe es el de `from_cut_piece_similarity` con `frente` / `frente_ok` si hay `front_hint`; la decisión de adelante/atrás ya
        está tomada, así que `ambigua` es siempre falso (si la cara quedó hacia atrás lo dice `frente_ok`, no la ambigüedad)."""
        keep = CUT_KEEP if keep is None else float(keep)
        V = np.asarray(source_vertices.vertices if hasattr(source_vertices, "vertices") else source_vertices, float)
        if source_faces is None and hasattr(source_vertices, "faces"):
            source_faces = source_vertices.faces
        Q0 = np.asarray(piece_mesh.vertices, float)
        QF = getattr(piece_mesh, "faces", None)
        surf = _SourceSurface(V, source_faces, samples=CUT_SOURCE_SAMPLES)
        Q = _cut_piece_points(Q0, QF)
        Qi = Q[np.random.default_rng(1).choice(len(Q), min(len(Q), CUT_ICP_POINTS), replace=False)]
        M, stats = _icp_trimmed(surf, np.asarray(start, float), Qi, keep)
        M, stats = _polish_trimmed(surf, M, Qi, keep)
        front_rep = {}
        if front_hint is not None:
            ang, vec = front_hint.angle(M)
            front_rep = {"ambigua": False, "frente_ok": None if ang is None else bool(ang < FRONT_MAX_DEG),
                         "frente": {"fbx": None if vec is None else [round(float(x), 3) for x in _unit(vec)],
                                    "pieza": [round(float(x), 3) for x in front_hint.front], "angulo_deg": None if ang is None else round(ang, 1)}}
        return cls(*_cut_result(surf, M, Q, Q0, QF, keep, front_rep))


def _prior_starts(V: np.ndarray, Q0: np.ndarray, ct: np.ndarray, s0: float) -> list[np.ndarray]:
    """O11 · Arranques para `from_cut_piece_similarity` que usan lo único que se sabe sin mirar la forma: la pieza está en un marco **Z arriba** (el del motor)
    y el FBX tiene **Y arriba** (OBJ, glTF, FBX de Blender) o Z arriba. Para cada vertical posible del FBX se prueban giros sobre la vertical (`CUT_PRIOR_YAWS`),
    cabeceos y alabeos (`CUT_PRIOR_PITCH`, `CUT_PRIOR_ROLL`) y escalas (`CUT_PRIOR_SCALES` × `s0`); el FBX se apoya con la franja alta de su vertical (del
    alto de la pieza, o la mitad del FBX si es menor) sobre el centro de la pieza, porque una cabeza es lo más alto del busto o de la figura.

    Los arranques por ejes principales (los momentos del busto entero no son los de la cabeza) dejaban la pose a 39° de cabeceo de la buena con un FBX de
    busto: la distancia recortada de un solo lado (pieza → FBX) la acepta, el FBX tiene pelo, cuello y hombros donde caer. Devuelve una lista de 4×4."""
    from scipy.spatial.transform import Rotation

    height = float(np.ptp(Q0[:, 2]))
    out = []
    for Rup in (Rotation.from_euler("x", 90.0, degrees=True).as_matrix(), np.eye(3)):
        for yaw in CUT_PRIOR_YAWS:
            for pitch in CUT_PRIOR_PITCH:
                for roll in CUT_PRIOR_ROLL:
                    if roll and yaw % 180.0:
                        continue
                    R = Rotation.from_euler("zxy", [yaw, pitch, roll], degrees=True).as_matrix() @ Rup
                    Vr = V @ R.T
                    z = Vr[:, 2]
                    for f in CUT_PRIOR_SCALES:
                        s = f * s0
                        band = min(height / s, 0.5 * float(np.ptp(z)))
                        top = z >= z.max() - band
                        out.append(_similarity_matrix(s * R, ct - s * Vr[top].mean(axis=0)))
    return out


def _cut_piece_points(Q0: np.ndarray, QF) -> np.ndarray:
    """Muestras (por área) de la superficie EXTERIOR de la pieza recortada: la cáscara interior de una pieza hueca (su normal apunta al centro) se descarta."""
    Qs_ = _SourceSurface(Q0, QF, samples=CUT_PIECE_SAMPLES)
    Q = Qs_.xyz
    if Qs_.face is not None:
        tri = Qs_._tri[Qs_.face]
        nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        outer = np.einsum("ij,ij->i", nrm, Q - Q.mean(0)) > 0
        if outer.mean() > 0.3:
            Q = Q[outer]
    return Q


def _cut_result(surf, best, Q, Q0, QF, keep: float, front_rep: dict):
    """`(matriz, escala, informe)` de una alineación recortada ya resuelta: las medidas de `Q` contra `surf` y `ok`."""
    edge = _median_edge(Q0, QF)
    tol = max(SIMILARITY_OK_MM, CUT_RES_FACTOR * edge)           # una pieza más gruesa que el FBX (o al revés) no puede calzar mejor que su malla
    mean, p95, mx, cover = _trimmed(surf, best, Q, keep, exact=True, cover_mm=2 * tol)
    scale = float(np.cbrt(abs(np.linalg.det(best[:3, :3]))))
    rep = {"media_mm": mean, "p95_mm": p95, "max_mm": mx, "cubierta": cover, "iteraciones": CUT_ICP_ITER,
           "tolerancia_mm": tol, "arista_pieza_mm": edge,
           "ok": bool(mean <= tol and cover >= CUT_MIN_COVER and front_rep.get("frente_ok") is not False and not front_rep.get("ambigua"))}
    rep.update(front_rep)
    return best, scale, rep


FRONT_AMBIGUOUS_MM = 0.15        # O05: si la pose y su giro de 180° calzan con menos diferencia que esto y no hay dirección de cara, no se decide
FRONT_MAX_DEG = 90.0             # la cara del FBX tiene que mirar hacia el lado del frente de la pieza (ángulo < 90°)


@dataclass
class FrontHint:
    """Lo que desempata adelante/atrás en la alineación de una pieza recortada, todo en el marco de la PIEZA (O05).

    `front`: hacia dónde mira la pieza (el +Y del marco de la figura). `up` y `center`: el eje de la pieza (el del cuello, hacia la cabeza) y un
    punto de él, alrededor del cual se gira 180°. `face_dir(M)`: dada la 4×4 FBX → pieza, hacia dónde mira la cara del FBX (vector en el marco de
    la pieza) o `None` si no se puede saber (el FBX no tiene rol de piel)."""
    front: np.ndarray
    up: np.ndarray
    center: np.ndarray
    face_dir: object = None

    def __post_init__(self):
        self.up = _unit(self.up)
        self.front = _unit(_perp(self.front, self.up))
        self.center = np.asarray(self.center, float)

    def angle(self, M) -> tuple[float | None, np.ndarray | None]:
        """(ángulo en grados entre la cara y el frente, medido en el plano horizontal de la pieza; vector de la cara)."""
        v = None if self.face_dir is None else self.face_dir(M)
        if v is None:
            return None, None
        h = _perp(np.asarray(v, float), self.up)
        if np.linalg.norm(h) < 1e-9:
            return None, None
        return _angle_deg(h, self.front), h


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    if n < 1e-12:
        raise ValueError("vector nulo")
    return v / n


def _perp(v, up) -> np.ndarray:
    """Componente de `v` perpendicular a `up` (unitario)."""
    v = np.asarray(v, float)
    return v - (v @ up) * up


def _angle_deg(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    return float(np.degrees(np.arccos(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)), -1.0, 1.0))))


def yaw_matrix(center, axis, deg: float = 180.0) -> np.ndarray:
    """4×4 rígida: giro de `deg` grados alrededor del eje `axis` que pasa por `center` (la pieza se gira sobre su cuello)."""
    from scipy.spatial.transform import Rotation

    R = Rotation.from_rotvec(_unit(axis) * np.radians(deg)).as_matrix()
    c = np.asarray(center, float)
    return _similarity_matrix(R, c - R @ c)


def _resolve_front(surf, M, stats, Qi, keep, hint: FrontHint):
    """Decide entre la pose `M` y su giro de 180° sobre el eje del cuello. Devuelve (matriz, stats, informe parcial).

    Con dirección de cara se queda la pose que la deja mejor orientada; sin ella, la que mejor calza, y si la diferencia es `< FRONT_AMBIGUOUS_MM`
    el informe lleva `ambigua`. La opuesta se re-afina (ICP + afinado) desde el giro exacto, así su media es comparable con la de la elegida."""
    alt, st_alt = _icp_trimmed(surf, yaw_matrix(hint.center, hint.up) @ M, Qi, keep)
    alt, st_alt = _polish_trimmed(surf, alt, Qi, keep)
    ang, vec = hint.angle(M)
    ang_alt, vec_alt = hint.angle(alt)
    margin = abs(st_alt[0] - stats[0])
    if ang is not None and ang_alt is not None:
        flip = ang_alt < ang
        ambiguous = False
    else:
        flip = st_alt[0] < stats[0]
        ambiguous = margin < FRONT_AMBIGUOUS_MM
    (M2, st2, a2, v2) = (alt, st_alt, ang_alt, vec_alt) if flip else (M, stats, ang, vec)
    rep = {"yaw180": {"media_alt_mm": round(float(stats[0] if flip else st_alt[0]), 4), "margen_mm": round(float(margin), 4), "girada": bool(flip)},
           "ambigua": bool(ambiguous),
           "frente_ok": None if a2 is None else bool(a2 < FRONT_MAX_DEG),
           "frente": {"fbx": None if v2 is None else [round(float(x), 3) for x in _unit(v2)],
                      "pieza": [round(float(x), 3) for x in hint.front],
                      "angulo_deg": None if a2 is None else round(a2, 1)}}
    return M2, st2, rep


# Z arriba (marco del motor y de los STL) -> Y arriba (convención de OBJ y glTF): (x, y, z) -> (x, z, -y). Es la inversa de la 3×3 que
# `meshio.orient_z_up` aplica a un archivo Y-arriba. Bambu Studio y Blender convierten a Z arriba al importar un OBJ y los visores de glTF
# suponen Y arriba, así que un archivo de color escrito en Z arriba aparece acostado.
Z_UP_TO_Y_UP = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])

ICP_AUTO_MEAN_MM = 0.05          # D11: el ICP es respaldo; sobra si la media sin ICP no pasa de esto
SIMILARITY_OK_MM = 0.1           # §7.2: el FBX es la misma figura si la media ≤ 0,1 mm
PIECE_SCORE_POINTS = 4000        # vértices del destino con que se puntúan las 24 rotaciones candidatas de `from_piece_similarity`
ICP_MAX_ITER = 20
ICP_STOP_DELTA = 1e-4


def _surface_moments(V: np.ndarray, F=None, samples: int = 60_000):
    """(centroide, covarianza 3×3) de la superficie (muestras por área; sin caras, de los vértices)."""
    P = _SourceSurface(V, F, samples).xyz
    c = P.mean(0)
    return c, np.cov((P - c).T, bias=True)


def _similarity_matrix(A: np.ndarray, t: np.ndarray) -> np.ndarray:
    M = np.eye(4)
    M[:3, :3] = A
    M[:3, 3] = t
    return M


class _SourceSurface:
    """Muestras densas de la superficie de la fuente (en SU marco) para medir distancias punto-superficie sin `rtree`."""

    def __init__(self, V: np.ndarray, F=None, samples: int = 400_000):
        from scipy.spatial import cKDTree

        self.V = V
        if F is None:                                           # solo vértices: distancia punto a punto (cota superior)
            self.F, self.xyz, self.face = None, V, None
        else:
            self.F = np.asarray(F, np.int64)
            tri = V[self.F]
            area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
            counts = np.maximum(1, np.ceil(area / (area.sum() / samples)).astype(np.int64))
            face = np.repeat(np.arange(len(self.F)), counts)
            r = np.random.default_rng(0).random((len(face), 2))
            flip = r.sum(1) > 1
            r[flip] = 1 - r[flip]
            b = np.column_stack([1 - r.sum(1), r])
            self.xyz, self.face = np.einsum("nk,nkc->nc", b, tri[face]), face
            self._tri = tri
        self.tree = cKDTree(self.xyz)

    def nearest(self, P: np.ndarray) -> np.ndarray:
        """Punto de la fuente (muestra) más cercano a cada punto de `P`."""
        return self.xyz[self.tree.query(P)[1]]

    def distance(self, P: np.ndarray, k: int = 3) -> np.ndarray:
        """Distancia al triángulo más cercano entre los de las `k` muestras vecinas (exacta punto-triángulo)."""
        if self.F is None:
            return self.tree.query(P)[0]
        import trimesh

        idx = self.tree.query(P, k=k)[1]
        best = np.full(len(P), np.inf)
        for j in range(idx.shape[1]):
            f = self.face[idx[:, j]]
            cp = trimesh.triangles.closest_point(self._tri[f], P)
            best = np.minimum(best, np.linalg.norm(cp - P, axis=1))
        return best


def _target_distances(surf: _SourceSurface, M: np.ndarray, Q: np.ndarray):
    """(media, p95, máx) en el marco del destino, de los vértices `Q` del destino a la superficie de la fuente transformada con `M`."""
    s = float(np.cbrt(abs(np.linalg.det(M[:3, :3]))))
    Qs = (Q - M[:3, 3]) @ np.linalg.inv(M[:3, :3]).T           # destino → marco de la fuente
    d = surf.distance(Qs) * s
    return float(d.mean()), float(np.percentile(d, 95)), float(d.max())


def _umeyama(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Similitud (escala · R + t, con det(R) = +1) que lleva `A` sobre `B` por mínimos cuadrados."""
    ca, cb = A.mean(0), B.mean(0)
    Ac, Bc = A - ca, B - cb
    U, S, Vt = np.linalg.svd(Bc.T @ Ac / len(A))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U) * np.linalg.det(Vt)) or 1.0
    R = U @ D @ Vt
    s = float(np.trace(np.diag(S) @ D) / Ac.var(0).sum())
    return _similarity_matrix(s * R, cb - s * R @ ca)


def _icp_similarity(surf: _SourceSurface, M: np.ndarray, Q: np.ndarray, stats0):
    """ICP de similitud: cada vértice del destino se lleva al marco de la fuente, se busca su muestra más cercana y Umeyama da la
    transformación fuente→destino completa (el árbol de la fuente se arma una sola vez). Devuelve (matriz, stats, iteraciones)."""
    best, best_stats, it_done = M, stats0, 0
    cur = M
    for it in range(1, ICP_MAX_ITER + 1):
        Qs = (Q - cur[:3, 3]) @ np.linalg.inv(cur[:3, :3]).T
        cur = _umeyama(surf.nearest(Qs), Q)
        stats = _target_distances(surf, cur, Q)
        it_done = it
        improved = best_stats[0] - stats[0]
        if stats[0] < best_stats[0]:
            best, best_stats = cur, stats
        if improved < ICP_STOP_DELTA:
            break
    return best, best_stats, it_done


CUT_KEEP = 0.8                    # fracción de la superficie de la pieza que se compara (la que mejor calza): deja afuera tapa, hueco y agujeros
CUT_MIN_COVER = 0.6              # fracción mínima de la pieza a menos de 2·tolerancia del FBX para dar la alineación por buena
CUT_RES_FACTOR = 0.75            # la tolerancia de la pieza recortada sube a esta fracción de su arista mediana: el STL de una cabeza «de más
                                 # calidad» suele ser OTRA malla (otro diezmado) que la del FBX y se aparta ~media arista aunque sea la misma forma
CUT_PIECE_SAMPLES = 4000          # muestras de la superficie de la pieza
CUT_SOURCE_SAMPLES = 80_000       # muestras de la superficie del FBX
CUT_COARSE_POINTS = 400          # muestras con las que se puntúa cada arranque
CUT_SCALES = (0.85, 1.0, 1.18)
CUT_REFINE = 16                   # arranques que se refinan un poco con ICP (los 3 mejores siguen hasta converger)
CUT_PATIENCE = 4
CUT_POLISH_EVALS = 450
CUT_ICP_POINTS = 900             # muestras de la pieza que entran al ICP y al afinado           # evaluaciones del afinado directo de la similitud
CUT_ICP_ITER = 40
# O11 · arranques con la vertical conocida (ver `_prior_starts`)
CUT_PRIOR_YAWS = (0.0, 90.0, 180.0, 270.0)       # giro sobre la vertical
CUT_PRIOR_PITCH = (-45.0, -30.0, -15.0, 0.0, 15.0, 30.0, 45.0)   # cabeceo: una cabeza modelada inclinada no es un caso raro (Bolívar: 39° de error con arranques por ejes)
CUT_PRIOR_ROLL = (-15.0, 0.0, 15.0)              # alabeo (solo con la cara hacia adelante o atrás)
CUT_PRIOR_SCALES = (1.0, 1.5, 2.25)              # × la escala de momentos del FBX entero, que SUBestima si el FBX es un busto o una figura (Bolívar: ×1,5)
CUT_PRIOR_ICP_POINTS = 400                       # el ICP barato de cada arranque usa menos muestras…
CUT_PRIOR_ICP_ITER = 6                           # …y menos vueltas
CUT_PRIOR_TOP = 12                               # los que siguen con el ICP normal
CUT_FINAL = 3                                    # los que llegan al ICP completo y al afinado directo


def _median_edge(V: np.ndarray, F) -> float:
    """Arista mediana (mm) de la malla `(V, F)`; 0 si no hay caras."""
    if F is None or len(F) == 0:
        return 0.0
    T = np.asarray(V, float)[np.asarray(F, np.int64)]
    e = np.concatenate([np.linalg.norm(T[:, i] - T[:, (i + 1) % 3], axis=1) for i in range(3)])
    return float(np.median(e))


def _trimmed(surf: _SourceSurface, M: np.ndarray, Q: np.ndarray, keep: float, exact: bool = False, cover_mm: float | None = None):
    """(media, p95, máx, cubierta) en el marco del destino de la fracción `keep` de `Q` que más se acerca a la superficie de la fuente (con `M`).
    `exact=False` mide contra la muestra más cercana (rápido, para puntuar arranques y vueltas de ICP); `exact=True`, contra el triángulo."""
    s = float(np.cbrt(abs(np.linalg.det(M[:3, :3]))))
    Qs = (Q - M[:3, 3]) @ np.linalg.inv(M[:3, :3]).T
    d = np.sort((surf.distance(Qs) if exact else surf.tree.query(Qs)[0]) * s)
    n = max(1, int(np.ceil(keep * len(d))))
    best = d[:n]
    cover_mm = 2 * SIMILARITY_OK_MM if cover_mm is None else cover_mm
    return float(best.mean()), float(np.percentile(best, 95)), float(best.max()), float((d <= cover_mm).mean())


def _icp_trimmed(surf: _SourceSurface, M: np.ndarray, Q: np.ndarray, keep: float, iters: int = CUT_ICP_ITER):
    """ICP de similitud recortado: en cada vuelta solo entran en el ajuste los `keep` de `Q` más cercanos. Aguanta `CUT_PATIENCE` vueltas sin
    mejorar antes de parar (el recorte hace que la media no baje siempre). Devuelve (matriz, stats de `_trimmed`)."""
    cur, best, best_stats = M, M, _trimmed(surf, M, Q, keep)
    idle = 0
    for _ in range(iters):
        Qs = (Q - cur[:3, 3]) @ np.linalg.inv(cur[:3, :3]).T
        near = surf.nearest(Qs)
        d = np.linalg.norm(near - Qs, axis=1)
        sel = np.argsort(d)[: max(8, int(np.ceil(keep * len(d))))]
        try:
            cur = _umeyama(near[sel], Q[sel])
        except np.linalg.LinAlgError:
            break
        stats = _trimmed(surf, cur, Q, keep)
        if stats[0] < best_stats[0] - ICP_STOP_DELTA:
            best, best_stats, idle = cur, stats, 0
        else:
            idle += 1
            if stats[0] < best_stats[0]:
                best, best_stats = cur, stats
            if idle >= CUT_PATIENCE:
                break
    return best, best_stats


def _polish_trimmed(surf: _SourceSurface, M: np.ndarray, Q: np.ndarray, keep: float):
    """Afina `M` buscando directamente los 7 parámetros de la similitud (rotación, log-escala, traslado) que minimizan la media recortada
    (Powell). Sirve donde el ICP se queda deslizando sobre una superficie lisa. Devuelve (matriz, stats de `_trimmed`)."""
    from scipy.optimize import minimize
    from scipy.spatial.transform import Rotation

    s0 = float(np.cbrt(abs(np.linalg.det(M[:3, :3]))))
    R0 = M[:3, :3] / s0
    size = float(np.ptp(Q, axis=0).max())

    def build(x):
        R = Rotation.from_rotvec(x[:3]).as_matrix() @ R0
        return _similarity_matrix(s0 * np.exp(x[3]) * R, M[:3, 3] + x[4:] * size)

    res = minimize(lambda x: _trimmed(surf, build(x), Q, keep)[0], np.zeros(7), method="Powell",
                   options={"xtol": 1e-4, "ftol": 1e-6, "maxfev": CUT_POLISH_EVALS},
                   bounds=[(-0.5, 0.5)] * 3 + [(-0.3, 0.3)] + [(-0.3, 0.3)] * 3)
    M2 = build(res.x)
    st2 = _trimmed(surf, M2, Q, keep)
    st0 = _trimmed(surf, M, Q, keep)
    return (M2, st2) if st2[0] < st0[0] else (M, st0)


def _symmetry_matrix(symmetry) -> np.ndarray:
    """La transformación rígida de `align_to_symmetry`, obtenida aplicándola a cuatro puntos (Kabsch)."""
    if symmetry is None:
        return np.eye(4)
    import trimesh

    from . import pipeline as P

    pts = np.array([[0.0, 0, 0], [10.0, 0, 0], [0, 10.0, 0], [0, 0, 10.0]])
    tet = trimesh.Trimesh(pts, [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]], process=False)
    out = np.asarray(P.align_to_symmetry(tet, symmetry).vertices)
    a, b = pts, out
    ca, cb = a.mean(0), b.mean(0)
    U, _, Vt = np.linalg.svd((a - ca).T @ (b - cb))
    d = np.sign(np.linalg.det(Vt.T @ U.T)) or 1.0
    Rm = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    M = np.eye(4)
    M[:3, :3] = Rm
    M[:3, 3] = cb - Rm @ ca
    return M
