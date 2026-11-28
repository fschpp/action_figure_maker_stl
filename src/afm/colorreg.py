"""Registro pieza → fuente de color (plan §7.4, T08).

Cada pieza de la figura es la superficie original recortada y girada de forma rígida (más un espejo en `_der`), así que basta
una rotación de UN grado de libertad sobre el eje del miembro para llevarla a la pose de reposo de la fuente. El motor no se
instrumenta (D6): las semillas salen de `BuildResult.motion`, `BuildResult.detent` y de `Params`.

Convención. Una **semilla** es lo que el motor le HIZO a la pieza: `(centro, eje, ángulo_deg)`, o una lista de esos pasos
aplicados en orden. `Registration.matrix` lleva la pieza impresa a la pose de reposo (la inversa de la semilla) y
`Registration.angle_deg` es el ángulo de esa rotación alrededor de `(center, axis)` (por eso es `−th` en la pierna sentada).
Lado derecho: `piezaDer = M · piezaIzq` con `M = diag(−1, 1, 1)` y `M·R(a, θ)·M = R(M·a, −θ)`, de modo que la semilla de `_der`
es `(M·centro, M·eje, −ángulo)` siempre. Con `limbs_right` (`res.right`) los datos de `_der` son los de `res.right.motion`,
que están en el marco espejado; la fórmula es la misma.

`PIL` y `trimesh` no se importan aquí (D10): la malla se usa por sus arreglos `vertices` y `faces`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

Seed = tuple | list      # (centro(3), eje(3), ángulo_deg) o lista de esos pasos, aplicados en orden

MIRROR_X = np.diag([-1.0, 1.0, 1.0])
SWEEP_STEP = 15.0        # barrido grueso (°)
REFINE_HALF = 7.5        # refinado alrededor del mejor ángulo grueso (±°), en pasos de 1°
SWEEP_CAP_MM = 4.0       # el barrido puntúa con la distancia media recortada a este valor (ver `register_piece`)
TIE_MARGIN = 0.02        # una semilla posterior reemplaza a la mejor solo si la supera por más de esto (fracción de puntos):
#                          en piezas de revolución (la cabeza sobre el cuello) todos los giros puntúan igual ± ruido (T17)
IDENTITY_SEED = ([0.0, 0.0, 0.0], [0.0, 0.0, 1.0], 0.0)


@dataclass
class Registration:
    status: str              # "identidad" | "semilla" | "barrido" | "sin_origen"
    matrix: np.ndarray       # (4, 4): pieza en pose → pose de reposo de la fuente
    angle_deg: float         # ángulo de `matrix` alrededor de (center, axis); 0 si no hay giro
    score: float             # fracción de puntos de la pieza a ≤ thresh_mm de la fuente
    seed: object             # la semilla (o None si vino del barrido o no hay registro)
    center: tuple | None = None
    axis: tuple | None = None
    chain_deg: tuple = ()    # ángulos registrados por eje, en el orden (articulación, cadera/hombro); solo en cadenas de dos giros (T18)


# ------------------------------------------------------------------ geometría
def rotation_matrix(center, axis, angle_deg: float) -> np.ndarray:
    """4×4 de la rotación de `angle_deg` alrededor de la recta (centro, eje) (Rodrigues)."""
    c = np.asarray(center, float)
    ax = np.asarray(axis, float)
    ax = ax / np.linalg.norm(ax)
    a = np.radians(angle_deg)
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    R = np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * (K @ K)
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = c - R @ c
    return M


def _steps(seed) -> list:
    """Una semilla como lista de pasos: un paso suelto `(c, a, θ)` (θ escalar) pasa a `[(c, a, θ)]`."""
    if len(seed) == 3 and isinstance(seed[2], (int, float, np.floating, np.integer)):
        return [tuple(seed)]
    return [tuple(s) for s in seed]


def seed_forward(seed) -> np.ndarray:
    """La transformación que el motor aplicó a la pieza de reposo (los pasos en orden)."""
    F = np.eye(4)
    for c, ax, th in _steps(seed):
        F = rotation_matrix(c, ax, float(th)) @ F
    return F


def _apply(M: np.ndarray, P: np.ndarray) -> np.ndarray:
    return P @ M[:3, :3].T + M[:3, 3]


def _surface_points(mesh, n: int, seed: int = 0) -> np.ndarray:
    """`n` puntos de la superficie, ponderados por área (rng fijo)."""
    V = np.asarray(mesh.vertices, float)
    F = np.asarray(mesh.faces, np.int64)
    tri = V[F]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    rng = np.random.default_rng(seed)
    face = rng.choice(len(F), size=n, p=area / area.sum())
    r = rng.random((n, 2))
    flip = r.sum(1) > 1
    r[flip] = 1 - r[flip]
    b = np.column_stack([1 - r.sum(1), r])
    return np.einsum("nk,nkc->nc", b, tri[face])


# ------------------------------------------------------------------ semillas
def _split_name(name: str):
    for suf in ("_izq", "_der"):
        if name.endswith(suf):
            return name[: -len(suf)], suf[1:]
    return name, None


def _uniq(vals) -> list[float]:
    out: list[float] = []
    for a in vals:
        if all(abs(a - b) > 1e-6 for b in out):
            out.append(float(a))
    return out


def _revolute(motions):
    """`{nombre: RevoluteMotion}` de los movimientos de un solo eje (la rótula no tiene semillas de giro). Import perezoso: este módulo no arrastra `manifold3d`."""
    from .joint_types.base import revolute_motions
    return revolute_motions(motions)


def _as_revolute(motion):
    from .joint_types.base import as_revolute
    return as_revolute(motion)


def _mirror_step(step):
    c, ax, th = step
    return (MIRROR_X @ np.asarray(c, float)).tolist(), (MIRROR_X @ np.asarray(ax, float)).tolist(), 0.0 - float(th)


def _owner(res, side):
    return res.right if (side == "der" and getattr(res, "right", None) is not None) else res


def _joint_info(owner, key: str):
    """Si `key` es una pieza de articulación (rodilla, codo…): `(nombre_art, informe, índice 0=proximal 1=distal)`."""
    for jn, r in getattr(owner, "report", {}).get("joints", {}).items():
        piezas = r.get("piezas", [])
        if key in piezas and r.get("limb") in _revolute(owner.motion) and jn in getattr(owner, "joints", {}):
            return jn, r, piezas.index(key)
    return None


def _ancestors(owner, jn: str) -> list:
    """Las articulaciones que preceden a `jn` en su cadena, la más cercana primero: `[(nombre, informe)]` (vacío si es la primera)."""
    rep = getattr(owner, "report", {}).get("joints", {})
    out, cur = [], rep.get(jn, {}).get("cadena", {}).get("previa")
    while cur is not None and cur in rep and cur in getattr(owner, "joints", {}):
        out.append((cur, rep[cur]))
        cur = rep[cur].get("cadena", {}).get("previa")
    return out


def _axes_for_piece(piece_name: str, res):
    """Ejes de giro de la pieza, en el orden de la cadena y ya reflejados en `_der`: `[(centro, eje)]`.

    Miembro simple: el de `motion`. Pieza proximal de una articulación: el de cadera/hombro. Distal: primero el de la
    articulación y luego el de cadera/hombro. Sin entrada en `motion`: lista vacía."""
    key, side = _split_name(piece_name)
    owner = _owner(res, side)
    out = []
    ji = _joint_info(owner, key)
    if ji is not None:
        jn, r, idx = ji
        jb = owner.joints[jn]
        mo = _revolute(owner.motion)[r["limb"]]
        if idx == 1:
            out.append((np.asarray(jb.center, float), np.asarray(jb.axis, float)))
            for an, _ar in _ancestors(owner, jn):             # cadena: la pieza también gira con las articulaciones anteriores
                ab = owner.joints[an]
                out.append((np.asarray(ab.center, float), np.asarray(ab.axis, float)))
        out.append((np.asarray(mo[1], float), np.asarray(mo[2], float)))
    elif key in _revolute(owner.motion):          # una rótula no tiene un eje: sin semillas de giro (la pieza sale por identidad)
        mo = _revolute(owner.motion)[key]
        out.append((np.asarray(mo[1], float), np.asarray(mo[2], float)))
    if side == "der":
        out = [(MIRROR_X @ c, MIRROR_X @ a) for c, a in out]
    return out


def _joint_seeds(owner, key, jn, r, idx, params) -> list:
    """Semillas de una pieza de articulación (lado izquierdo). Proximal: un solo paso (cadera/hombro). Distal: cadena
    «flexión de la articulación y después cadera/hombro» (lo que hace `pipeline.build`), la exacta primero; de respaldo, las
    4 combinaciones de signo (flexión ±, giro ±) y los giros sin flexión."""
    jb = owner.joints[jn]
    mo = _revolute(owner.motion)[r["limb"]]
    c_h, ax_h = np.asarray(mo[1], float).tolist(), np.asarray(mo[2], float).tolist()
    th, pf = float(r.get("print_hip", 0.0)), float(r.get("print_flex", 0.0))
    sg = float(jb.sign)
    sit = float(params.sit_angle)
    hips = _uniq([th, -th, 0.0, sit, -sit])
    if idx == 0:
        return [[(c_h, ax_h, a)] for a in hips]
    jc, jax = np.asarray(jb.center, float).tolist(), np.asarray(jb.axis, float).tolist()
    anc = [(np.asarray(owner.joints[an].center, float).tolist(), np.asarray(owner.joints[an].axis, float).tolist(),
            float(owner.joints[an].sign), float(ar.get("print_flex", 0.0))) for an, ar in _ancestors(owner, jn)]
    flexes = _uniq([sg * pf, -sg * pf, 0.0])
    out = []
    for f in flexes:
        for mult in ((1.0, -1.0, 0.0) if anc else (1.0,)):     # cadena: flexión impresa de las anteriores (±) o sin flexión
            steps = [(jc, jax, f)] + [(c, a, mult * sgn * pfl) for c, a, sgn, pfl in anc]
            out.extend([steps + [(c_h, ax_h, h)] for h in hips])
    return out


def seeds_for_piece(piece_name: str, res, params) -> list:
    """Semillas del registro de `piece_name`, la EXACTA primero.

    Clave = nombre sin sufijo `_izq/_der`. Si no está en `res.motion` (en `res.right.motion` para `_der` con `limbs_right`),
    solo la identidad. Exacta (§7.4 (0)): `th` = `Params.sit_angle` en una pierna (miembro con barrido de cadera: `a1 > a0`,
    la misma regla que usa el pipeline para separar brazos de piernas), `res.detent[clave].print_angle` en un brazo con clic y 0
    en el resto (la cabeza sale sin girar). Respaldo: identidad, ±sit_angle, ±print_angle y ±a1 (tope del recorrido).
    Piezas de una articulación (T18): la proximal lleva un paso (cadera/hombro, `print_hip` del informe) y la distal una
    CADENA `[(articulación, flexión), (cadera, giro)]` con la flexión `sign · print_flex`; ver `_joint_seeds`.
    """
    from .head import HEAD_NAME

    key, side = _split_name(piece_name)
    owner = _owner(res, side)
    ji = _joint_info(owner, key)
    if ji is not None:
        jn, r, idx = ji
        seeds = _joint_seeds(owner, key, jn, r, idx, params)
        if side == "der":
            seeds = [[_mirror_step(st) for st in sd] for sd in seeds]
        return seeds
    mo = _as_revolute(owner.motion.get(key))
    if mo is None:
        return [IDENTITY_SEED]
    _limb, c, ax, rng = mo
    a0, a1 = float(rng[0]), float(rng[1])
    det = getattr(owner, "detent", {}).get(key)
    if det is not None:
        th = float(det.print_angle)
    elif key != HEAD_NAME and a1 > a0:
        th = float(params.sit_angle)
    else:
        th = 0.0
    pa = float(params.detent.print_angle)         # el plan lo lista en `Params`; vive en `Params.detent` (DetentSpec)
    angles = _uniq([th, 0.0, float(params.sit_angle), -float(params.sit_angle), pa, -pa, a1, -a1])
    c, ax = np.asarray(c, float), np.asarray(ax, float)
    if side == "der":                       # M·R(a, θ)·M = R(M·a, −θ)
        c, ax, angles = MIRROR_X @ c, MIRROR_X @ ax, [0.0 - a for a in angles]
    return [(c.tolist(), ax.tolist(), float(a)) for a in angles]


# ------------------------------------------------------------------ registro
def _score(M: np.ndarray, pts: np.ndarray, src, thresh_mm: float) -> float:
    return float(np.mean(src.distance(_apply(M, pts)) <= thresh_mm))


def _cost(M: np.ndarray, pts: np.ndarray, src) -> float:
    """Distancia media recortada a `SWEEP_CAP_MM`: baja de forma continua al acercarse al ángulo bueno (el porcentaje ≤ 0,3 mm
    es casi cero hasta que se está a unos pocos grados y el barrido grueso no tendría cómo elegir)."""
    return float(np.minimum(src.distance(_apply(M, pts), upper=SWEEP_CAP_MM), SWEEP_CAP_MM).mean())


def _wrap(a: float) -> float:
    return float((a + 180.0) % 360.0 - 180.0)


SWEEP2_POINTS = 300      # puntos del barrido grueso en dos ángulos (24 × 24 = 576 giros)
SWEEP2_TOP = 10          # casillas del barrido grueso que se refinan (la cuenca del óptimo es más angosta que 15° y el costo
#                          grueso de una pieza chica cerca del eje es casi plano: la mejor casilla no siempre es la buena)
FINE2_STEP = 2.5         # paso del refinado en dos ángulos (°); después, 1° alrededor del mejor


def _sweep_two_axes(axes, pts, src, thresh_mm):
    """Barrido en DOS ángulos (RG10): 24 × 24 pasos de 15° sobre los ejes `axes = [(c1, a1), (c2, a2)]` (articulación y
    cadera); se refinan las `SWEEP2_TOP` mejores casillas (±7,5° cada 2,5°, con `SWEEP2_POINTS` puntos), la mejor de ellas
    a 1° con todos los puntos. La matriz buscada es `R1(u1) · R2(u2)` (lleva la pieza a reposo). Devuelve `(M, u1, u2)`."""
    (c1, a1), (c2, a2) = axes
    grid = np.arange(0.0, 360.0, SWEEP_STEP)
    sub = pts[np.linspace(0, len(pts) - 1, min(SWEEP2_POINTS, len(pts))).astype(int)]
    R1 = [rotation_matrix(c1, a1, u) for u in grid]
    R2 = [rotation_matrix(c2, a2, u) for u in grid]
    P2 = np.stack([_apply(M, sub) for M in R2])                                  # (24, n, 3): R2(grid[j]) primero
    allp = np.stack([_apply(M, P2.reshape(-1, 3)).reshape(P2.shape) for M in R1])   # (24 i, 24 j, n, 3): después R1(grid[i])
    d = np.minimum(src.distance(allp.reshape(-1, 3), upper=SWEEP_CAP_MM).reshape(allp.shape[:3]), SWEEP_CAP_MM).mean(axis=2)
    order = np.argsort(d, axis=None, kind="stable")[:SWEEP2_TOP]

    def cost(u1, u2, pp):
        return float(np.minimum(src.distance(_apply(rotation_matrix(c1, a1, u1) @ rotation_matrix(c2, a2, u2), pp),
                                             upper=SWEEP_CAP_MM), SWEEP_CAP_MM).mean())

    best, best_c = (0.0, 0.0), np.inf
    for k in order:
        i, j = np.unravel_index(int(k), d.shape)
        r1 = np.arange(grid[i] - REFINE_HALF, grid[i] + REFINE_HALF + 1e-9, FINE2_STEP)
        r2 = np.arange(grid[j] - REFINE_HALF, grid[j] + REFINE_HALF + 1e-9, FINE2_STEP)
        for u1 in r1:
            for u2 in r2:
                c = cost(u1, u2, sub)
                if c < best_c:
                    best, best_c = (float(u1), float(u2)), c
    r1 = np.arange(best[0] - FINE2_STEP, best[0] + FINE2_STEP + 1e-9, 1.0)
    r2 = np.arange(best[1] - FINE2_STEP, best[1] + FINE2_STEP + 1e-9, 1.0)
    cand = [(u1, u2) for u1 in r1 for u2 in r2]
    u1, u2 = min(cand, key=lambda uu: cost(uu[0], uu[1], pts))
    return rotation_matrix(c1, a1, u1) @ rotation_matrix(c2, a2, u2), u1, u2


def register_piece(piece_name: str, mesh, src, res, params, n_points: int = 1500, thresh_mm: float = 0.3,
                   min_score: float = 0.4) -> Registration:
    """Lleva la pieza impresa `mesh` a la pose de reposo de la fuente `src` (`ColorSource`).

    (1) puntúa las semillas con la fracción de `n_points` puntos de la pieza a ≤ `thresh_mm` de la fuente y toma la mejor
    (gana la primera en un empate, o si la supera por ≤ `TIE_MARGIN`: la exacta); (2) si ninguna llega a `min_score`: barrido de 0–360° cada 15° alrededor del eje
    del miembro (el de `motion`, reflejado en `_der`) y refinado ±7,5° en pasos de 1°, eligiendo por distancia media recortada;
    el resultado se acepta si la fracción ≤ `thresh_mm` llega a `min_score`; en la pieza distal de una articulación el barrido es
    en DOS ángulos (articulación y cadera, T18); (3) si no: `sin_origen`, matriz identidad.

    En una cadena de dos giros, `center/axis/angle_deg` describen el giro de cadera/hombro (el último paso, igual que en la
    pieza proximal) y `chain_deg` trae los dos ángulos registrados en el orden (articulación, cadera).
    """
    pts = _surface_points(mesh, n_points)
    seeds = seeds_for_piece(piece_name, res, params)
    best_i, best_s, best_inv = -1, -1.0, np.eye(4)
    for i, sd in enumerate(seeds):
        inv = np.linalg.inv(seed_forward(sd))
        s = _score(inv, pts, src, thresh_mm)
        if s > best_s + (TIE_MARGIN if best_i >= 0 else 1e-9):
            best_i, best_s, best_inv = i, s, inv
    if best_s >= min_score:
        sd = seeds[best_i]
        steps = _steps(sd)
        c, ax, th = steps[-1]                       # una cadena se describe por su último paso (cadera/hombro)
        identity = np.allclose(best_inv, np.eye(4), atol=1e-9)
        chain = tuple(float(_wrap(-st[2])) for st in steps) if len(steps) > 1 else ()
        return Registration("identidad" if identity else "semilla", best_inv, 0.0 if identity else float(_wrap(-th)), best_s, sd,
                            tuple(map(float, c)), tuple(map(float, ax)), () if identity else chain)
    axes = _axes_for_piece(piece_name, res)
    if not axes:
        return Registration("sin_origen", np.eye(4), 0.0, best_s, None)
    if len(axes) == 2:
        M, u1, u2 = _sweep_two_axes(axes, pts, src, thresh_mm)
        s = _score(M, pts, src, thresh_mm)
        if s >= min_score:
            return Registration("barrido", M, _wrap(u2), s, None, tuple(map(float, axes[1][0])), tuple(map(float, axes[1][1])),
                                (_wrap(u1), _wrap(u2)))
        return Registration("sin_origen", np.eye(4), 0.0, max(s, best_s), None)
    c, ax = axes[0]
    coarse = [(_cost(rotation_matrix(c, ax, th), pts, src), th) for th in np.arange(0.0, 360.0, SWEEP_STEP)]
    th0 = min(coarse)[1]
    fine = [(_cost(rotation_matrix(c, ax, th), pts, src), th) for th in np.arange(th0 - REFINE_HALF, th0 + REFINE_HALF + 1e-9, 1.0)]
    th_best = min(fine)[1]
    M = rotation_matrix(c, ax, th_best)
    s = _score(M, pts, src, thresh_mm)
    if s >= min_score:
        return Registration("barrido", M, _wrap(th_best), s, None, tuple(map(float, c)), tuple(map(float, ax)))
    return Registration("sin_origen", np.eye(4), 0.0, max(s, best_s), None)
