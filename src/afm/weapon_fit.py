"""Simulación del tamaño de un arma (sin GTK): puesta en la mano estándar y colgada del cuerpo.

Sirve para decidir si un arma salió chica o grande **antes** de imprimir:

* `simulate_in_hand`: el accesorio (arma con la empuñadura de la línea, marco del accesorio) puesto en el puño de la mano
  estándar con agarre, exactamente donde lo deja el armado (`props.prop_to_hand_matrix`). Mide cuánto sobresale del puño, qué
  tan ancha es la guarda frente al puño, si el arma toca la mano y qué fracción de la altura de la figura ocupa.
* `simulate_on_body`: el arma ya enfundada en su montura (marco de la vista conjunta) frente a la figura de pie: la fracción
  de la altura que ocupa, a qué distancia del piso queda la punta, cuánto asoma la empuñadura sobre la cabeza y si choca con
  los miembros del mismo lado en esa pose.

El resultado es datos: los avisos son pares `(código, variables)` y la interfaz (`gui/weapon_sim.py`) los pasa a texto. Los
umbrales de abajo son **orientativos** (una espada de la línea mide ≈ 0,40 × la altura, ver `weapon.suggest_length`); no
bloquean nada, solo avisan.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh

from . import solids as S
from .props import GripFit, prop_to_hand_matrix
from .weapon import longest_dimension

# ------------------------------------------------------------------ umbrales (orientativos)
OVERLAP_MM3 = 0.5            # volumen en común hoja–mano / arma–miembro a partir del cual se avisa que chocan
GAP_SEARCH_MM = 4.0          # hasta dónde se busca la distancia hoja–mano
SHORT_VS_HAND = 1.0          # el arma sobresale del puño menos que el largo de la mano: casi no se ve
SHORT_VS_FIGURE = 0.15       # fracción de la altura de la figura por debajo de la cual es una daga diminuta
LONG_VS_FIGURE = 0.65        # ... y por encima de la cual es desmedida (la sugerida es 0,40)
TILT_WARN_DEG = 35.0         # el cuerpo del arma se aparta tanto del eje del puño: sale torcida de la mano
FLOOR_MIN_MM = 2.0           # la punta enfundada queda a menos que esto del piso: roza o arrastra
GUARD_PROBE_MM = 0.5         # la primera sección del arma se mide así de adentro de la cara del puño (no coplanar con ella)
GUARD_BAND_MM = 10.0         # el «lado del puño» del arma: lo que está a menos de esto de la cara del puño (guarda, nudo, base de la hoja)

LEVEL_OK, LEVEL_WARN, LEVEL_NO = "ok", "aviso", "no"


class FitError(ValueError):
    """No se puede simular (accesorio sin arma, malla que no cierra…)."""


@dataclass
class HandSim:
    """Arma puesta en el puño de la mano estándar. Las mallas están en el marco local de la mano."""

    weapon: trimesh.Trimesh                 # el accesorio entero (arma + empuñadura) en el marco de la mano
    matrix: np.ndarray                      # 4×4 accesorio → mano
    reach_mm: float                         # cuánto sobresale del puño el arma (a lo largo del eje de la empuñadura)
    guard_mm: float                         # lado mayor de la sección más ancha del arma pegada al puño (`GUARD_BAND_MM`)
    fist_mm: float                          # lado mayor de la sección del puño a media altura del canal
    hand_len_mm: float                      # dimensión mayor de la mano
    figure_mm: float | None                 # altura de la figura de referencia
    reach_vs_hand: float
    reach_vs_figure: float | None
    tilt_deg: float
    overlap_mm3: float                      # volumen de hoja que entra en la mano (0 = no la toca)
    gap_mm: float | None                    # distancia mínima hoja–mano si no se tocan (None: más lejos que `GAP_SEARCH_MM`)
    notes: list = field(default_factory=list)       # [(código, {variables})]

    @property
    def level(self) -> str:
        return level_of(self.notes)


@dataclass
class BodyFit:
    """Arma enfundada frente a la figura de pie (marco de la vista conjunta, Z hacia arriba)."""

    length_mm: float
    figure_mm: float
    length_vs_figure: float
    tip_z_mm: float                         # punto más bajo del arma
    floor_gap_mm: float                     # tip_z − base de la figura (negativo: atraviesa el piso)
    top_over_head_mm: float                 # punto más alto del arma − tope de la cabeza (negativo: queda por debajo)
    limb_overlap_mm3: dict = field(default_factory=dict)    # {nombre de la pieza: volumen en común} solo los que chocan
    notes: list = field(default_factory=list)

    @property
    def level(self) -> str:
        return level_of(self.notes)


# códigos que son error (no solo aviso): el arma atraviesa algo
_NO_CODES = {"choca_mano", "choca_miembro", "atraviesa_piso"}
NOTE_CODES = ("choca_mano", "guarda_chica", "corta_vs_mano", "corta_vs_figura", "larga_vs_figura", "torcida",
              "atraviesa_piso", "punta_cerca_piso", "choca_miembro")        # todos los que puede dar este módulo (cada uno tiene su texto en la interfaz)


def level_of(notes) -> str:
    if any(code in _NO_CODES for code, _v in notes):
        return LEVEL_NO
    return LEVEL_WARN if notes else LEVEL_OK


def load_hand_with_grip(cat, hand_ref: str | None = None):
    """(ficha, malla local, `GripFit`) de la mano con agarre del catálogo: la pedida o la más reciente (`weapon.line_hand`).

    `WeaponError` si el catálogo no tiene una mano con agarre."""
    from .weapon import line_hand

    meta = line_hand(cat, hand_ref)
    _m, mesh = cat.load(meta.ref)
    return meta, mesh, GripFit.from_dict(meta.mecanismo["agarre"])


# ------------------------------------------------------------------ piezas del accesorio
def blade_part(acc: S.Manifold) -> S.Manifold:
    """El arma sin la empuñadura: lo que queda del lado de la hoja (x ≤ 0) del marco del accesorio (ver `props`)."""
    part = acc.trim_by_plane((-1.0, 0.0, 0.0), 0.0)
    if part.is_empty():
        raise FitError("el accesorio no tiene arma del lado de la hoja (x ≤ 0 del marco del accesorio)")
    return part


def _lateral_extent(mesh: trimesh.Trimesh, x: float) -> float:
    """Lado mayor de la sección de `mesh` por el plano x = `x` (marco del accesorio); 0 si el plano no la corta."""
    try:
        path = mesh.section(plane_origin=(x, 0.0, 0.0), plane_normal=(1.0, 0.0, 0.0))
    except Exception:                                   # noqa: BLE001 — una malla rara no tiene que tumbar la simulación
        return 0.0
    if path is None or len(path.vertices) == 0:
        return 0.0
    v = np.asarray(path.vertices, float)
    return float(max(np.ptp(v[:, 1]), np.ptp(v[:, 2])))


def _tilt_deg(blade_tm: trimesh.Trimesh) -> float:
    """Ángulo entre la dirección «hacia la hoja» ideal (−X del accesorio) y la que va del puño al centro del arma."""
    c = np.asarray(blade_tm.center_mass if blade_tm.is_watertight else blade_tm.bounds.mean(0), float)
    n = float(np.linalg.norm(c))
    if n < 1e-9:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(-c[0] / n, -1.0, 1.0))))


# ------------------------------------------------------------------ arma en la mano
def simulate_in_hand(acc_mesh: trimesh.Trimesh, hand_mesh: trimesh.Trimesh, fit: GripFit,
                     figure_height_mm: float | None = None) -> HandSim:
    """Pone `acc_mesh` (accesorio, marco del accesorio) en el puño de `hand_mesh` (mano estándar, marco local) y mide.

    `fit` es el agarre de la mano (`GripFit.from_dict(meta.mecanismo["agarre"])`). Con `figure_height_mm` también compara
    el arma con la altura de la figura."""
    try:
        acc = S.to_manifold(acc_mesh)
        hand = S.to_manifold(hand_mesh)
    except S.MeshNotManifold as exc:
        raise FitError(f"la malla no cierra: {exc}") from exc
    blade = blade_part(acc)
    bt = S.to_trimesh(blade)
    verts = np.asarray(bt.vertices, float)
    reach = float(-verts[:, 0].min())
    M = prop_to_hand_matrix(fit)
    hand_len = float(longest_dimension(hand_mesh))
    # el puño visto desde el accesorio: sección a media altura del canal (los dedos cerrados alrededor del arma)
    hand_in_acc = hand_mesh.copy()
    hand_in_acc.apply_transform(np.linalg.inv(M))
    guard = max(_lateral_extent(bt, -x) for x in np.arange(GUARD_PROBE_MM, GUARD_BAND_MM + 1e-9, 1.0))
    fist = _lateral_extent(hand_in_acc, max(GUARD_PROBE_MM, fit.length / 2.0))
    # ¿la hoja toca la mano?
    blade_h = blade.transform(M[:3, :].astype(np.float64))
    overlap = float((hand ^ blade_h).volume())
    gap = None
    if overlap <= OVERLAP_MM3:
        g = float(hand.min_gap(blade_h, GAP_SEARCH_MM))
        gap = g if g < GAP_SEARCH_MM - 1e-6 else None
    tilt = _tilt_deg(bt)
    weapon = acc_mesh.copy()
    weapon.apply_transform(M)
    rvh = reach / hand_len if hand_len > 0 else 0.0
    rvf = reach / figure_height_mm if figure_height_mm else None
    notes: list = []
    if overlap > OVERLAP_MM3:
        notes.append(("choca_mano", {"mm3": round(overlap, 1)}))
    if fist > 0 and guard > 0 and guard < fist:
        notes.append(("guarda_chica", {"guarda": round(guard, 1), "puno": round(fist, 1)}))
    if rvh < SHORT_VS_HAND:
        notes.append(("corta_vs_mano", {"alcance": round(reach, 1), "mano": round(hand_len, 1)}))
    if rvf is not None and rvf < SHORT_VS_FIGURE:
        notes.append(("corta_vs_figura", {"pct": round(100 * rvf)}))
    if rvf is not None and rvf > LONG_VS_FIGURE:
        notes.append(("larga_vs_figura", {"pct": round(100 * rvf)}))
    if tilt > TILT_WARN_DEG:
        notes.append(("torcida", {"grados": round(tilt)}))
    return HandSim(weapon, M, reach, guard, fist, hand_len, figure_height_mm, rvh, rvf, tilt, overlap, gap, notes)


# ------------------------------------------------------------------ arma en el cuerpo
def _bounds_of(parts) -> np.ndarray:
    lo = np.min([np.asarray(m.bounds, float)[0] for _n, m, *_c in parts], axis=0)
    hi = np.max([np.asarray(m.bounds, float)[1] for _n, m, *_c in parts], axis=0)
    return np.array([lo, hi])


def _side_limbs(parts, lado: str) -> list:
    """Piezas de la vista conjunta que se mueven del lado de la montura: brazo, mano y pierna (el otro lado lleva «_der»)."""
    suf = "" if lado == "izq" else "_der"
    return [(n, m) for n, m, *_c in parts if n in {f"{k}{suf}" for k in ("brazo", "mano", "pierna")}]


def simulate_on_body(weapon_world: trimesh.Trimesh, parts, lado: str = "izq") -> BodyFit:
    """Arma enfundada (`weapon_world`, marco de la vista conjunta) frente a la figura que forman `parts` ([(nombre, malla, …)]).

    Z hacia arriba. La base de la figura es el punto más bajo de todas las piezas."""
    if not parts:
        raise FitError("no hay piezas de la figura para comparar")
    fb = _bounds_of(parts)
    height = float(fb[1, 2] - fb[0, 2])
    if height <= 0:
        raise FitError("la figura no tiene altura")
    head = [m for n, m, *_c in parts if n == "cabeza"]
    head_top = float(max(np.asarray(m.bounds, float)[1, 2] for m in head)) if head else float(fb[1, 2])
    wb = np.asarray(weapon_world.bounds, float)
    length = float(longest_dimension(weapon_world))
    tip_z = float(wb[0, 2])
    floor_gap = tip_z - float(fb[0, 2])
    over_head = float(wb[1, 2]) - head_top
    overlaps: dict = {}
    try:
        w = S.to_manifold(weapon_world)
    except S.MeshNotManifold:
        w = None
    if w is not None:
        for name, mesh in _side_limbs(parts, lado):
            try:
                v = float((w ^ S.to_manifold(mesh)).volume())
            except S.MeshNotManifold:
                continue
            if v > OVERLAP_MM3:
                overlaps[name] = round(v, 1)
    lvf = length / height
    notes: list = []
    if floor_gap < 0:
        notes.append(("atraviesa_piso", {"mm": round(-floor_gap, 1)}))
    elif floor_gap < FLOOR_MIN_MM:
        notes.append(("punta_cerca_piso", {"mm": round(floor_gap, 1)}))
    for name, v in overlaps.items():
        notes.append(("choca_miembro", {"pieza": name, "mm3": v}))
    if lvf > LONG_VS_FIGURE:
        notes.append(("larga_vs_figura", {"pct": round(100 * lvf)}))
    elif lvf < SHORT_VS_FIGURE:
        notes.append(("corta_vs_figura", {"pct": round(100 * lvf)}))
    return BodyFit(length, height, lvf, tip_z, floor_gap, over_head, overlaps, notes)
