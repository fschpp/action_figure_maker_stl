"""M08: mapa de espacio libre del flanco: dónde cabe un puente de montura antes de elegir el ancla.

Sobre la superficie del flanco de la pieza huésped (pelvis o torso, lado `izq` = +X o `der` = −X) se toman puntos con su normal. Por
cada punto se mide la **altura libre**: cuánto se puede subir por la normal antes de topar con un miembro (en todas las poses en que
`pipeline._mount_poses` los mide: de pie, cadera hasta sentada, brazos en su recorrido), dentro de un radio lateral (media anchura
de un puente). Se compara con la **altura que necesita** el puente (`needed_height`): hoja + juego por los dos lados + techo + la
holgura a miembros que exige la validación. Sin GTK ni `rtree`: la distancia sale de puntos muestreados en los miembros (`scipy.cKDTree`),
con un error de a lo sumo la separación del muestreo (por defecto 0,4 mm).

Es una cota aproximada para **elegir** dónde probar, no una validación: la que manda es `_validate_mount`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

from .mount import CLEARANCE_FACTOR, DEFAULT_CLEARANCE

DEFAULT_LATERAL_MM = 2.0          # media anchura del puente: los obstáculos más lejos que esto del eje de la normal no cuentan
DEFAULT_MAX_MM = 8.0              # altura libre máxima que se mide (más allá es «de sobra»)
DEFAULT_POINTS = 4000             # puntos sobre el flanco
DEFAULT_SPACING_MM = 0.4          # separación del muestreo de los miembros (error máximo de la distancia)
NORMAL_MIN = 0.5                  # cos del ángulo entre la normal del punto y el eje X del lado: solo cuenta lo que mira hacia afuera
MARGIN_OK_MM = 0.5                # sobre lo necesario, «cabe»; entre lo necesario y esto, «justo»
LEVELS = ("no", "justo", "cabe")  # 0, 1, 2
COLORS = {0: (214, 69, 65, 255), 1: (242, 190, 52, 255), 2: (68, 170, 98, 255)}


def needed_height(blade_thickness: float, clearance: float = DEFAULT_CLEARANCE, wall: float = 1.2, member_clearance: float = 0.5) -> float:
    """Altura (mm) sobre el flanco que ocupa un puente más la holgura que la validación exige a los miembros: espesor de la hoja
    + juego por los dos lados + techo (`wall`) + `CLEARANCE_FACTOR` · `member_clearance` (`Params.clearance`).
    Con los números del plan (hoja 0,8, juego 0,25, pared 1,2, holgura 0,5) da ≈ 2,95 mm."""
    return float(blade_thickness) + 2.0 * float(clearance) + float(wall) + CLEARANCE_FACTOR * float(member_clearance)


@dataclass
class FlankMap:
    """Puntos del flanco con su altura libre. Marco de la pieza huésped (el de `BuildResult.pieces`)."""

    host: str
    lado: str
    points: np.ndarray             # (N, 3)
    normals: np.ndarray            # (N, 3), salientes
    free_mm: np.ndarray            # (N,) altura libre por la normal, tope `max_mm`
    need_mm: float
    max_mm: float
    lateral_mm: float

    def levels(self) -> np.ndarray:
        """0 = no cabe, 1 = justo (cabe con menos de `MARGIN_OK_MM` de sobra), 2 = cabe."""
        lv = np.zeros(len(self.free_mm), dtype=int)
        lv[self.free_mm >= self.need_mm] = 1
        lv[self.free_mm >= self.need_mm + MARGIN_OK_MM] = 2
        return lv

    def fits(self) -> np.ndarray:
        return self.free_mm >= self.need_mm


def flank_points(host: trimesh.Trimesh, lado: str = "izq", n: int = DEFAULT_POINTS, normal_min: float = NORMAL_MIN, seed: int = 0):
    """Puntos y normales de la superficie de `host` que miran hacia afuera del lado `lado`. Determinista (`seed`)."""
    if lado not in ("izq", "der"):
        raise ValueError(f"lado «{lado}» desconocido (hay izq, der)")
    sx = 1.0 if lado == "izq" else -1.0
    pts, face = trimesh.sample.sample_surface(host, int(n), seed=seed)
    nrm = np.asarray(host.face_normals)[face]
    keep = nrm[:, 0] * sx >= normal_min
    return np.asarray(pts)[keep], nrm[keep]


def sample_obstacles(meshes, spacing: float = DEFAULT_SPACING_MM, seed: int = 0) -> np.ndarray:
    """Puntos de la superficie de los obstáculos, con una separación de ~`spacing` mm. (M, 3); vacío si no hay."""
    chunks = []
    for i, m in enumerate(meshes):
        count = int(min(80000, max(300, float(m.area) / (spacing * spacing))))
        chunks.append(np.asarray(trimesh.sample.sample_surface(m, count, seed=seed + i)[0]))
        chunks.append(np.asarray(m.vertices))
    return np.vstack(chunks) if chunks else np.zeros((0, 3))


def free_heights(points: np.ndarray, normals: np.ndarray, obstacles: np.ndarray, lateral: float = DEFAULT_LATERAL_MM,
                 max_mm: float = DEFAULT_MAX_MM) -> np.ndarray:
    """Altura libre por la normal en cada punto: la menor `t ≥ 0` de un punto de obstáculo con distancia al eje ≤ `lateral`."""
    free = np.full(len(points), float(max_mm))
    if len(points) == 0 or len(obstacles) == 0:
        return free
    from scipy.spatial import cKDTree

    tree = cKDTree(obstacles)
    reach = float(np.hypot(max_mm, lateral))
    for i, idx in enumerate(tree.query_ball_point(points, reach)):
        if not idx:
            continue
        d = obstacles[idx] - points[i]
        t = d @ normals[i]
        lat = np.linalg.norm(d - np.outer(t, normals[i]), axis=1)
        ok = (t >= -0.05) & (lat <= lateral)
        if ok.any():
            free[i] = min(float(max_mm), max(0.0, float(t[ok].min())))
    return free


def flank_map(host: trimesh.Trimesh, obstacles, need_mm: float, host_name: str = "torso", lado: str = "izq", n_points: int = DEFAULT_POINTS,
              lateral: float = DEFAULT_LATERAL_MM, max_mm: float = DEFAULT_MAX_MM, spacing: float = DEFAULT_SPACING_MM, seed: int = 0) -> FlankMap:
    """Mapa del flanco `lado` de `host` contra `obstacles` (lista de `trimesh.Trimesh`: los miembros en todas sus poses)."""
    pts, nrm = flank_points(host, lado, n_points, seed=seed)
    free = free_heights(pts, nrm, sample_obstacles(obstacles, spacing, seed), lateral, max_mm)
    return FlankMap(host=host_name, lado=lado, points=pts, normals=nrm, free_mm=free, need_mm=float(need_mm), max_mm=float(max_mm), lateral_mm=float(lateral))


def summarize(fm: FlankMap, z_step: float = 4.0) -> dict:
    """Resumen por franjas de altura (z): cuántos puntos hay, qué fracción cabe, la mayor altura libre y, de los que caben, el rango
    de y. `cabe` es el rectángulo (z, y) que contiene todo lo que cabe, o None."""
    out: dict = {"host": fm.host, "lado": fm.lado, "necesita_mm": round(fm.need_mm, 3), "puntos": int(len(fm.points)), "franjas": []}
    if len(fm.points) == 0:
        out["cabe"] = None
        out["cabe_pct"] = 0.0
        return out
    z = fm.points[:, 2]
    z0 = np.floor(z.min() / z_step) * z_step
    while z0 <= z.max():
        sel = (z >= z0) & (z < z0 + z_step)
        if sel.any():
            fit = sel & fm.fits()
            out["franjas"].append({"z": [float(z0), float(z0 + z_step)], "puntos": int(sel.sum()),
                                   "cabe_pct": round(100.0 * float(fit.sum()) / float(sel.sum()), 1),
                                   "libre_max_mm": round(float(fm.free_mm[sel].max()), 2),
                                   "y_cabe": [round(float(fm.points[fit, 1].min()), 2), round(float(fm.points[fit, 1].max()), 2)] if fit.any() else None})
        z0 += z_step
    fit = fm.fits()
    out["cabe_pct"] = round(100.0 * float(fit.sum()) / float(len(fit)), 1)
    out["cabe"] = None if not fit.any() else {"z": [round(float(z[fit].min()), 2), round(float(z[fit].max()), 2)],
                                              "y": [round(float(fm.points[fit, 1].min()), 2), round(float(fm.points[fit, 1].max()), 2)]}
    return out


def to_point_cloud(fm: FlankMap) -> trimesh.PointCloud:
    """Los puntos del flanco coloreados: verde = cabe, amarillo = justo, rojo = no cabe."""
    lv = fm.levels()
    return trimesh.PointCloud(fm.points, colors=np.array([COLORS[int(k)] for k in lv], dtype=np.uint8).reshape(-1, 4))


def markdown(summary: dict) -> str:
    rows = ["| z (mm) | puntos | cabe % | libre máx. (mm) | y donde cabe |", "|---|---|---|---|---|"]
    for f in summary["franjas"]:
        y = "-" if f["y_cabe"] is None else f"{f['y_cabe'][0]:g} … {f['y_cabe'][1]:g}"
        rows.append(f"| {f['z'][0]:g} … {f['z'][1]:g} | {f['puntos']} | {f['cabe_pct']:g} | {f['libre_max_mm']:g} | {y} |")
    head = [f"# Espacio libre en el flanco {summary['lado']} de «{summary['host']}»", "",
            f"Altura que necesita el puente: **{summary['necesita_mm']:g} mm** (hoja + juego + techo + holgura a miembros).", ""]
    tail = ["", ("No hay ningún punto donde quepa." if not summary.get("cabe") else
                 f"Cabe en z {summary['cabe']['z'][0]:g} … {summary['cabe']['z'][1]:g} mm, y {summary['cabe']['y'][0]:g} … {summary['cabe']['y'][1]:g} mm "
                 f"({summary['cabe_pct']:g} % de los puntos)."),
            "Aproximación por puntos muestreados: la validación (`_validate_mount`) es la que decide."]
    return "\n".join(head + rows + tail) + "\n"
