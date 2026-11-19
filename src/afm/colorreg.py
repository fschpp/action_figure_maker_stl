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


def seeds_for_piece(piece_name: str, res, params) -> list:
    """Semillas del registro de `piece_name`, la EXACTA primero.

    Clave = nombre sin sufijo `_izq/_der`. Si no está en `res.motion` (en `res.right.motion` para `_der` con `limbs_right`),
    solo la identidad. Exacta (§7.4 (0)): `th` = `Params.sit_angle` en una pierna (miembro con barrido de cadera: `a1 > a0`,
    la misma regla que usa el pipeline para separar brazos de piernas), `res.detent[clave].print_angle` en un brazo con clic y 0
    en el resto (la cabeza sale sin girar). Respaldo: identidad, ±sit_angle, ±print_angle y ±a1 (tope del recorrido).
    Cadenas de dos giros (rodilla, codo): T18.
    """
    from .head import HEAD_NAME

    key, side = _split_name(piece_name)
    owner = res.right if (side == "der" and getattr(res, "right", None) is not None) else res
    mo = owner.motion.get(key)
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
    cand = [th, 0.0, float(params.sit_angle), -float(params.sit_angle), pa, -pa, a1, -a1]
    angles: list[float] = []
    for a in cand:
        if all(abs(a - b) > 1e-6 for b in angles):
            angles.append(a)
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
    return float(np.minimum(src.distance(_apply(M, pts)), SWEEP_CAP_MM).mean())


def _wrap(a: float) -> float:
    return float((a + 180.0) % 360.0 - 180.0)


def register_piece(piece_name: str, mesh, src, res, params, n_points: int = 1500, thresh_mm: float = 0.3,
                   min_score: float = 0.4) -> Registration:
    """Lleva la pieza impresa `mesh` a la pose de reposo de la fuente `src` (`ColorSource`).

    (1) puntúa las semillas con la fracción de `n_points` puntos de la pieza a ≤ `thresh_mm` de la fuente y toma la mejor
    (gana la primera en un empate: la exacta); (2) si ninguna llega a `min_score`: barrido de 0–360° cada 15° alrededor del eje
    del miembro (el de `motion`, reflejado en `_der`) y refinado ±7,5° en pasos de 1°, eligiendo por distancia media recortada;
    el resultado se acepta si la fracción ≤ `thresh_mm` llega a `min_score`; (3) si no: `sin_origen`, matriz identidad.
    """
    pts = _surface_points(mesh, n_points)
    seeds = seeds_for_piece(piece_name, res, params)
    best_i, best_s, best_inv = -1, -1.0, np.eye(4)
    for i, sd in enumerate(seeds):
        inv = np.linalg.inv(seed_forward(sd))
        s = _score(inv, pts, src, thresh_mm)
        if s > best_s + 1e-9:
            best_i, best_s, best_inv = i, s, inv
    if best_s >= min_score:
        sd = seeds[best_i]
        steps = _steps(sd)
        c, ax, th = steps[0]
        identity = np.allclose(best_inv, np.eye(4), atol=1e-9)
        return Registration("identidad" if identity else "semilla", best_inv, 0.0 if identity else float(_wrap(-th)), best_s, sd,
                            tuple(map(float, c)), tuple(map(float, ax)))
    key, side = _split_name(piece_name)
    owner = res.right if (side == "der" and getattr(res, "right", None) is not None) else res
    mo = owner.motion.get(key)
    if mo is None:
        return Registration("sin_origen", np.eye(4), 0.0, best_s, None)
    c, ax = np.asarray(mo[1], float), np.asarray(mo[2], float)
    if side == "der":
        c, ax = MIRROR_X @ c, MIRROR_X @ ax
    coarse = [(_cost(rotation_matrix(c, ax, th), pts, src), th) for th in np.arange(0.0, 360.0, SWEEP_STEP)]
    th0 = min(coarse)[1]
    fine = [(_cost(rotation_matrix(c, ax, th), pts, src), th) for th in np.arange(th0 - REFINE_HALF, th0 + REFINE_HALF + 1e-9, 1.0)]
    th_best = min(fine)[1]
    M = rotation_matrix(c, ax, th_best)
    s = _score(M, pts, src, thresh_mm)
    if s >= min_score:
        return Registration("barrido", M, _wrap(th_best), s, None, tuple(map(float, c)), tuple(map(float, ax)))
    return Registration("sin_origen", np.eye(4), 0.0, max(s, best_s), None)
