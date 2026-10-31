"""Armas para la línea: importar un STL cualquiera, escalarlo, cortarle el mango original por un plano y ponerle la
empuñadura dentada de la línea (`props.grip_stub`), que ya sabemos que entra y se sujeta en la mano estándar con agarre.

Igual que la cabeza de reemplazo: el plano se elige con 3 puntos sobre el STL importado, en sus coordenadas originales (sin
escalar ni girar). Se descarta el lado del mango y se conserva el resto (hoja, guarda…); la empuñadura nueva sale del centro
de la sección de corte, en la dirección de la normal.

Marco del accesorio resultante (ver `props`): eje de la empuñadura = +X por (y=0, z=0); origen en el centro de la sección de
corte (la cara que mira a la mano); el arma queda en x < 0. El giro sobre el eje se elige para que el arma quede lo más baja
posible (hoja plana acostada) y las lengüetas, que flexionan en Y, trabajen en el plano de la cama.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import trimesh

from . import solids as S
from .planes import Plane
from .props import GripSpec, grip_stub


class WeaponError(ValueError):
    """El arma no se puede preparar (STL ilegible, plano que no corta, sección demasiado chica…)."""


@dataclass
class WeaponSpec:
    """Cómo se prepara un arma importada. `scale` y `cut_points` se refieren al STL original."""
    path: str
    scale: float = 1.0                      # factor sobre las unidades del STL (mm de la figura = scale × unidades del STL)
    cut_points: tuple = ()                  # 3 puntos (x, y, z) del plano de corte, en coordenadas del STL original
    flip: bool = False                      # False: se descarta el lado al que apunta la normal de los 3 puntos
    roll: float | None = None               # giro (°) sobre el eje de la empuñadura; None = el que deja el arma más baja
    overlap: float = 1.0                    # mm que la empuñadura se hunde dentro del arma (para que la unión sea sólida)
    margin: float = 0.4                     # pared mínima de arma alrededor del vástago en la sección de corte (aviso)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cut_points"] = [list(map(float, q)) for q in self.cut_points]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "WeaponSpec":
        return cls(path=d["path"], scale=float(d.get("scale", 1.0)),
                   cut_points=tuple(tuple(map(float, q)) for q in d.get("cut_points") or ()),
                   flip=bool(d.get("flip", False)), roll=None if d.get("roll") is None else float(d["roll"]),
                   overlap=float(d.get("overlap", 1.0)), margin=float(d.get("margin", 0.4)))


@dataclass
class WeaponBuild:
    """Resultado de `build_weapon`: el arma con su empuñadura (marco del accesorio) y lo medido al armarla."""
    mesh: S.Manifold
    grip: GripSpec
    hole_len: float
    info: dict
    to_mesh_frame: np.ndarray | None = None      # 4×4: marco del accesorio → marco del arma ya escalada (para dibujar el resultado donde estaba el arma)


# ------------------------------------------------------------------ carga y escala
def load_weapon_mesh(path: str, scale: float = 1.0, progress=None) -> trimesh.Trimesh:
    """STL del arma en mm de la figura (`scale` × unidades del archivo), reparado. No se orienta ni se normaliza."""
    from . import meshio

    say = progress or (lambda text: None)
    say("leyendo el STL del arma…")
    try:
        mesh = trimesh.load(path, force="mesh")
    except Exception as exc:                          # noqa: BLE001 — cualquier formato roto se informa igual
        raise WeaponError(f"no se pudo leer «{path}»: {exc}") from exc
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) < 4:
        raise WeaponError(f"«{path}» no tiene una malla de triángulos")
    mesh = mesh.copy()
    mesh.apply_scale(float(scale))                     # las tolerancias de la reparación son absolutas: se escala antes
    return meshio.repair(mesh, progress=progress)


def longest_dimension(mesh: trimesh.Trimesh) -> float:
    """Dimensión mayor del arma (mm), medida a lo largo de sus ejes principales: no depende de cómo venga girado el STL."""
    v = np.asarray(mesh.vertices, float)
    v = v - v.mean(0)
    _, _, vt = np.linalg.svd(v, full_matrices=False)
    proj = v @ vt.T
    return float(np.max(proj.max(0) - proj.min(0)))


def scale_for_length(mesh: trimesh.Trimesh, target_mm: float) -> float:
    """Factor que deja la dimensión mayor del arma (`longest_dimension`) en `target_mm`."""
    longest = longest_dimension(mesh)
    if longest <= 0 or target_mm <= 0:
        raise WeaponError("largo objetivo o malla inválidos")
    return target_mm / longest


def suggest_length(figure_height_mm: float, ratio: float = 0.40) -> float:
    """Largo sugerido (mm) de un arma para una figura de esa altura (por defecto, 0,40 × la altura: una espada de ≈ 51 mm en 127)."""
    return float(figure_height_mm) * ratio


# ------------------------------------------------------------------ geometría
def _rot_to_z(n: np.ndarray) -> np.ndarray:
    """Rotación R con R @ n = +Z."""
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(n, z)
    s = float(np.linalg.norm(v))
    c = float(n @ z)
    if s < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K * ((1 - c) / s ** 2)


def _section_centroid(polys) -> tuple[np.ndarray, float]:
    """(centroide, área) de una sección de contornos (los huecos van con orientación contraria y restan)."""
    a_tot, cx, cy = 0.0, 0.0, 0.0
    for p in polys:
        x, y = p[:, 0], p[:, 1]
        x1, y1 = np.roll(x, -1), np.roll(y, -1)
        cr = x * y1 - x1 * y
        a = cr.sum() / 2
        a_tot += a
        cx += ((x + x1) * cr).sum() / 6
        cy += ((y + y1) * cr).sum() / 6
    if abs(a_tot) < 1e-9:
        raise WeaponError("la sección de corte no tiene área")
    return np.array([cx / a_tot, cy / a_tot]), abs(a_tot)


def _mf(mesh: trimesh.Trimesh) -> S.Manifold:
    try:
        return S.to_manifold(mesh)
    except Exception as exc:                          # noqa: BLE001
        raise WeaponError(f"la malla del arma no es un sólido cerrado: {exc}") from exc


def cut_plane(spec: WeaponSpec) -> Plane:
    """Plano de corte en el marco del arma ya escalado; su normal apunta hacia el mango que se descarta."""
    if len(spec.cut_points) != 3:
        raise WeaponError("faltan los 3 puntos del plano de corte")
    pts = np.asarray(spec.cut_points, float) * float(spec.scale)
    try:
        pl = Plane.from_points(*pts, name="corte del arma")
    except ValueError as exc:
        raise WeaponError(str(exc)) from exc
    return Plane.from_normal(pl.origin, -pl.normal if spec.flip else pl.normal, pl.name)


def build_weapon(mesh: trimesh.Trimesh, spec: WeaponSpec, grip: GripSpec, hole_len: float) -> WeaponBuild:
    """Corta el arma (ya en mm: ver `load_weapon_mesh`) por el plano, descarta el mango y le pega la empuñadura de `grip`.

    `hole_len` es el largo del canal de la mano con agarre de la línea (`largo_canal_mm`): fija dónde queda el hombro del dentado."""
    pl = cut_plane(spec)
    n, o = pl.normal, pl.origin
    kept = _mf(mesh).trim_by_plane(tuple(-n), float(-(n @ o)))        # se queda con el lado −normal; el mango (+normal) se va
    if kept.is_empty():
        raise WeaponError("el plano de corte deja el arma vacía: la normal apunta al lado equivocado o el plano no toca la malla")
    full_vol = _mf(mesh).volume()
    # marco local: plano en z=0, mango descartado hacia +z, arma en z ≤ 0
    R0 = _rot_to_z(n)
    M0 = np.c_[R0, -R0 @ o].astype(np.float64)
    local = kept.transform(M0)
    cs = local.slice(-0.05)                                            # un poco adentro de la cara: no coplanar con ella
    polys = cs.to_polygons()
    if not polys:
        raise WeaponError("el plano no corta el arma: movelo sobre el mango")
    (cx, cy), area = _section_centroid(polys)
    local = local.translate((-cx, -cy, 0.0))
    # sección vs vástago: el arma tiene que rodearlo con pared suficiente
    from manifold3d import CrossSection

    r = grip.shaft_d / 2
    need = CrossSection.circle(r + float(spec.margin), 48).translate((0.0, 0.0))
    cs0 = local.slice(-0.05)
    short = float((need - cs0).area())
    warnings: list[str] = []
    if short > 1e-3 * np.pi * (r + spec.margin) ** 2:
        warnings.append(f"la sección de corte ({area:.1f} mm²) no rodea el vástago (Ø{grip.shaft_d:g} mm) con {spec.margin:g} mm de pared: "
                        f"cortá en una zona más gruesa del mango o escalá el arma más grande")
    # giro sobre el eje: el que deja el arma más baja (z_acc = y' tras el giro φ)
    verts = np.asarray(local.to_mesh().vert_properties[:, :3], float)
    if spec.roll is None:
        best = None
        for phi in np.radians(np.arange(0.0, 180.0, 2.0)):
            yp = np.sin(phi) * verts[:, 0] + np.cos(phi) * verts[:, 1]
            h = float(yp.max() - yp.min())
            if best is None or h < best[0] - 1e-9:
                best = (h, phi)
        phi = best[1]
    else:
        phi = np.radians(float(spec.roll))
    c, s_ = np.cos(phi), np.sin(phi)
    # (x_l, y_l, z_l) → x' = c·x − s·y ; y' = s·x + c·y ; acc = (z_l, x', y')
    A = np.array([[0.0, 0.0, 1.0], [c, -s_, 0.0], [s_, c, 0.0]])
    weapon = local.transform(np.c_[A, np.zeros(3)].astype(np.float64))
    stub = grip_stub(grip, hole_len, overlap=spec.overlap)
    body = S.union(weapon, stub)
    bb = np.asarray(body.bounding_box(), float).reshape(2, 3) if hasattr(body, "bounding_box") else None
    zmin = float(bb[0, 2]) if bb is not None else -r
    gap = max(0.0, -r - zmin)                                         # cuánto sobresale el arma por debajo del vástago
    if gap > 0.15:
        warnings.append(f"el vástago queda {gap:.1f} mm por encima de la cama al imprimir acostada: usá soporte solo bajo el vástago")
    info = {"escala": float(spec.scale), "dimensiones_mm": [round(float(x), 2) for x in (bb[1] - bb[0])] if bb is not None else None,
            "seccion_corte_mm2": round(area, 2), "volumen_mantenido_pct": round(100 * kept.volume() / full_vol, 1),
            "giro_grados": round(float(np.degrees(phi)), 1), "vastago_sobre_cama_mm": round(gap, 2), "avisos": warnings}
    # acc = A·(R0·(p − o) − (cx, cy, 0))  →  p = R0ᵀ·(Aᵀ·acc + (cx, cy, 0)) + o
    back = np.eye(4)
    back[:3, :3] = R0.T @ A.T
    back[:3, 3] = R0.T @ np.array([cx, cy, 0.0]) + o
    return WeaponBuild(mesh=body, grip=grip, hole_len=float(hole_len), info=info, to_mesh_frame=back)


# ------------------------------------------------------------------ catálogo de armas de la línea
def line_grip(hand_agarre: dict) -> tuple[GripSpec, float]:
    """Empuñadura que encaja en la mano con agarre de la línea (su clave «agarre»): taladro y largo del canal salen de la mano;
    vástago = taladro − 0,4 mm (0,2 por lado) y dentado = taladro + 0,5 mm (interferencia 0,25 por lado), los valores que ya
    probamos con la mano de Güemes. Devuelve (GripSpec, largo del canal)."""
    bore = float(hand_agarre["diametro_taladro_mm"])
    return GripSpec(bore_d=bore, shaft_d=round(bore - 0.4, 3), barb_d=round(bore + 0.5, 3)), float(hand_agarre["largo_canal_mm"])


def line_hand(cat, hand_ref: str | None = None):
    """Pieza `mano` con agarre del catálogo: la pedida, o la de id/versión más alta que tenga agarre."""
    if hand_ref:
        meta = cat.meta(hand_ref)
        if meta.tipo != "mano" or not (meta.mecanismo or {}).get("agarre"):
            raise WeaponError(f"«{meta.ref}» no es una mano con agarre (taladrada con `afm agarre`)")
        return meta
    cands = [m for m in cat.list(tipo="mano") if (m.mecanismo or {}).get("agarre")]
    if not cands:
        raise WeaponError("el catálogo no tiene ninguna mano con agarre: taladrá una con `afm agarre … --catalogo RUTA`")
    return max(cands, key=lambda m: (m.id, m.version))


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "arma"


def add_weapon_to_catalog(cat, mesh: trimesh.Trimesh, spec: WeaponSpec, hand_ref: str | None = None, piece_id: str | None = None,
                          new_version: bool = False, descripcion: str = ""):
    """Prepara el arma (corte + empuñadura de la línea) y la guarda en el catálogo como pieza `accesorio`.

    Devuelve (meta, avisos). El mecanismo de la pieza lleva el agarre (para la compatibilidad con la mano) y «arma»: la mano
    para la que se armó, cómo se preparó (escala, plano…) y lo medido (dimensiones, avisos)."""
    import os

    from .props import accessory_grip

    hand = line_hand(cat, hand_ref)
    grip, hole_len = line_grip(hand.mecanismo["agarre"])
    b = build_weapon(mesh, spec, grip, hole_len)
    stem = os.path.splitext(os.path.basename(spec.path))[0]
    arma = {"mano": hand.ref, "preparacion": {**spec.to_dict(), "path": os.path.basename(spec.path)}, "medidas": b.info}
    meta, warns = cat.add_accessory(S.to_printable_trimesh(b.mesh), piece_id or f"{_slug(stem)}-01", accessory_grip(grip, hole_len),
                                    new_version, descripcion or f"Arma «{stem}» con la empuñadura de la línea", stem, extra={"arma": arma})
    return meta, list(b.info["avisos"]) + list(warns)
