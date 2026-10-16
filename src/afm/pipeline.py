"""Pipeline completo: STL normalizado -> piezas articuladas (plan §8.3–§8.10)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import numpy as np
import trimesh

from . import solids as S
from .head import HEAD_NAME, HeadSpec, build_head, split_head
from .limbs import LimbSpec, ContactFace, check_limb_plane, pin_margin, separate_limb
from .params import Params
from .pins import mushroom_pin
from .planes import Plane
from .trims import Trim, apply_trims

log = logging.getLogger(__name__)

DEFAULT_SWEEP = {"arm": (0.0, 0.0), "leg": None}  # leg: (0, sit_angle + margen)


@dataclass
class Piece:
    name: str
    solid: S.Manifold


@dataclass
class BuildResult:
    pieces: dict[str, S.Manifold]
    report: dict = field(default_factory=dict)
    motion: dict = field(default_factory=dict)   # name -> (limb de pie, centro, eje, (a0, a1)) para validar el recorrido


def align_to_symmetry(mesh: trimesh.Trimesh, sym: Plane | None) -> trimesh.Trimesh:
    """Lleva el plano de simetría a x = 0 con normal +X (solo rotación sobre Z + traslación)."""
    if sym is None:
        return mesh
    n = sym.normal.copy()
    if abs(n[2]) > 0.05:
        log.warning("El plano de simetría no es vertical (nz=%.3f)", n[2])
    ang = -np.arctan2(n[1], n[0]) if n[0] >= 0 else -np.arctan2(-n[1], -n[0])
    c, s = np.cos(ang), np.sin(ang)
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    mesh = mesh.copy()
    o = R @ sym.origin
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = [-o[0], 0, 0]
    mesh.apply_transform(T)
    return mesh


def _sweep_range(spec: LimbSpec, p: Params) -> tuple[float, float]:
    if spec.sweep_deg is not None:
        return spec.sweep_deg
    if spec.kind == "leg":
        return (0.0, p.sit_angle + p.hip_margin)
    return DEFAULT_SWEEP.get(spec.kind) or (0.0, 0.0)


def _pin_spec(spec: LimbSpec, p: Params):
    return p.hip_pin if spec.kind == "leg" else p.shoulder_pin


def _rotation_axis(face: ContactFace) -> np.ndarray:
    """Eje de giro: el del pasador, orientado de modo que +ángulo = hacia adelante (+Y) para -Z."""
    ax = face.outward / np.linalg.norm(face.outward)
    return ax if ax[0] >= 0 else -ax


def build(body: S.Manifold, limbs: list[LimbSpec], p: Params, sym: Plane | None = None,
          sit: bool = True, progress=None, trims: list[Trim] | None = None,
          head: HeadSpec | None = None) -> BuildResult:
    """Construye las piezas. `body` debe estar normalizado y con el plano de simetría en x = 0."""
    say = progress or (lambda *_: None)
    trims = trims or []
    for spec in limbs:
        check_limb_plane(spec.plane)       # falla rápido: un plano de canto da piezas inservibles
    half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)          # lado de trabajo (+X)
    head_build = None
    if head is not None:
        say("Separando la cabeza…")
        split = split_head(body, head, p)
        head_build = build_head(split, head, p, trims)
        half = split.body_half                                # los miembros se cortan del cuerpo sin cabeza
    torso_half = half
    built: dict[str, dict] = {}
    for spec in limbs:
        say(f"Separando {spec.name}…")
        limb_raw, face = separate_limb(half, spec, p.burr)
        limb_raw = apply_trims(limb_raw, trims, spec.name)
        pin = mushroom_pin(face.center, face.outward, _pin_spec(spec, p))
        limb = S.union(limb_raw, pin)
        say(f"Cavidad de {spec.name}…")
        grown = S.dilate(limb, p.clearance)
        a0, a1 = _sweep_range(spec, p)
        axis = _rotation_axis(face)
        angles = np.arange(a0, a1 + 1e-9, p.sweep_step) if a1 > a0 else [a0]
        if len(angles) > 1 and angles[-1] < a1 - 1e-9:
            angles = np.append(angles, a1)
        sweep = S.union(*[S.rotate_about_axis(grown, face.center, axis, a) for a in angles])
        torso_half = S.difference(torso_half, sweep)
        built[spec.name] = {"limb": limb, "face": face, "axis": axis, "spec": spec,
                            "angles": [float(a) for a in angles],
                            "pin_margin": pin_margin(face, _pin_spec(spec, p).shaft_d)}
    if head_build is not None:
        say("Cavidad del cuello…")
        torso_half = S.difference(torso_half, head_build.pocket)
    torso_half = apply_trims(torso_half, trims, "torso")
    torso_half = S.drop_small_components(torso_half, 0.02)

    say("Pose sentada y espejado…")
    pieces: dict[str, S.Manifold] = {}
    res_motion: dict = {}
    report: dict = {"limbs": {}}
    for name, b in built.items():
        limb = b["limb"]
        if sit and b["spec"].kind == "leg":
            limb = S.rotate_about_axis(limb, b["face"].center, b["axis"], p.sit_angle)
        res_motion[name] = (b["limb"], b["face"].center, b["axis"], (b["angles"][0], b["angles"][-1]))
        pieces[f"{name}_izq"] = limb
        pieces[f"{name}_der"] = S.mirror_x(limb)
        report["limbs"][name] = {
            "pin_center": b["face"].center.tolist(), "pin_dir": b["face"].outward.tolist(),
            "contact_area": b["face"].area, "sweep": [b["angles"][0], b["angles"][-1]],
        }
        if b["pin_margin"] is not None:
            report["limbs"][name]["pin_margin_mm"] = round(float(b["pin_margin"]), 3)
    pieces["torso"] = S.union(torso_half, S.mirror_x(torso_half))
    if head_build is not None:
        pieces[HEAD_NAME] = head_build.head
        report["head"] = {"pin_center": head_build.face.center.tolist(), "pin_dir": head_build.face.outward.tolist(),
                          "contact_area": head_build.face.area}
        if head_build.pin_margin is not None:
            report["head"]["pin_margin_mm"] = round(float(head_build.pin_margin), 3)
        res_motion[HEAD_NAME] = (head_build.head, head_build.face.center, head_build.axis, (0.0, 90.0))   # giro libre sobre el cuello: se valida 0–90°
    if p.decimate > 0:
        say("Reduciendo triángulos…")
        n0 = pieces["torso"].num_tri()
        pieces["torso"] = _decimate_keeping_clearance(pieces, p)
        report["torso_triangulos"] = [n0, pieces["torso"].num_tri()]
    return BuildResult(pieces=pieces, report=report, motion=res_motion)


def _decimate_keeping_clearance(pieces: dict, p: Params, tol: float = 0.02) -> S.Manifold:
    """Decima el torso bajando la intensidad hasta que la holgura mínima con cada miembro
    no caiga más de `tol` mm respecto de la original."""
    torso = pieces["torso"]
    limbs = [m for n, m in pieces.items() if n != "torso"]
    base = [m.min_gap(torso, 5.0) for m in limbs]
    for red in (p.decimate, 0.45, 0.3, 0.15):
        if red > p.decimate:
            continue
        cand = S.decimate(torso, red)
        if cand is torso:
            continue
        if all(m.min_gap(cand, 5.0) >= g - tol for m, g in zip(limbs, base)):
            return cand
    return torso


# ------------------------------------------------------------------ validación
def _nonmanifold_edges(mf: S.Manifold) -> int:
    """Aristas con != 2 caras tras soldar vértices coincidentes (lo que ve un slicer al leer el STL)."""
    tm = S.to_printable_trimesh(mf)      # la misma malla que se escribe en el STL
    _, counts = np.unique(tm.edges_sorted, axis=0, return_counts=True)
    return int((counts != 2).sum())


def validate(res: BuildResult, p: Params, limbs: list[LimbSpec], sit: bool = True,
             sweep_check_step: float = 2.5) -> dict:
    out: dict = {"ok": True, "problems": [], "avisos": [], "avisos_pasador": [], "pieces": {}, "gaps": {}}
    torso = res.pieces["torso"]
    for name, info in res.report.get("limbs", {}).items():
        m = info.get("pin_margin_mm")
        if m is not None and m < 0:
            out["avisos_pasador"].append(f"{name}: el vástago del pasador sobresale {-m:.2f} mm de la cara de contacto")
        elif m is not None and m < p.min_wall:
            out["avisos_pasador"].append(f"{name}: el vástago queda a {m:.2f} mm del borde de la cara "
                                         f"(< {p.min_wall:.1f} mm de pared)")
    hm = res.report.get("head", {}).get("pin_margin_mm")
    if hm is not None and hm < 0:
        out["avisos_pasador"].append(f"{HEAD_NAME}: el vástago del pasador sobresale {-hm:.2f} mm de la cara del cuello")
    elif hm is not None and hm < p.min_wall:
        out["avisos_pasador"].append(f"{HEAD_NAME}: el vástago queda a {hm:.2f} mm del borde de la cara del cuello "
                                     f"(< {p.min_wall:.1f} mm de pared)")
    if HEAD_NAME in res.pieces and p.neck_pin.shaft_d < p.min_pin_d:
        out["problems"].append(f"{HEAD_NAME}: vástago Ø{p.neck_pin.shaft_d} < {p.min_pin_d}")
    for name, mf in res.pieces.items():
        comps = len(mf.decompose())
        wt = mf.status() == S.m3.Error.NoError
        nme = _nonmanifold_edges(mf)
        out["pieces"][name] = {"componentes": comps, "variedad_ok": wt, "volumen": mf.volume(),
                               "aristas_no_variedad": nme, "triangulos": mf.num_tri()}
        if nme:
            out["avisos"].append(f"{name}: {nme} aristas con != 2 caras al soldar vértices por posición "
                                 "(el slicer puede avisar de 'non-manifold edge')")
        if not wt:
            out["problems"].append(f"{name}: no es variedad estanca")
        if name == "torso" and comps != 1:
            out["problems"].append(f"torso: {comps} componentes (debería ser 1, soldado)")
    for name, mf in res.pieces.items():
        if name == "torso":
            continue
        inter = (mf ^ torso).volume()
        gap = mf.min_gap(torso, 5.0)
        out["gaps"][name] = round(float(gap), 4)
        if inter > 1e-6:
            out["problems"].append(f"{name}: interpenetra el torso ({inter:.4f} mm³)")
        elif gap < p.clearance - 0.1 * p.clearance - 1e-6 and gap < 5.0:
            out["problems"].append(f"{name}: holgura mínima {gap:.3f} mm < {p.clearance:.3f} mm")
    names = [n for n in res.pieces if n != "torso"]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if (res.pieces[a] ^ res.pieces[b]).volume() > 1e-6:
                out["problems"].append(f"{a} y {b} se interpenetran")
    for spec in limbs:
        pin = _pin_spec(spec, p)
        if pin.shaft_d < p.min_pin_d:
            out["problems"].append(f"{spec.name}: vástago Ø{pin.shaft_d} < {p.min_pin_d}")
    out["recorrido"] = {}
    for name, (limb0, center, axis, (a0, a1)) in res.motion.items():
        if a1 <= a0:
            continue
        worst = 0.0
        for ang in np.arange(a0, a1 + 1e-9, sweep_check_step):
            worst = max(worst, (S.rotate_about_axis(limb0, center, axis, ang) ^ torso).volume())
        out["recorrido"][name] = round(float(worst), 5)
        if worst > 1e-4:
            out["problems"].append(f"{name}: interfiere con el torso durante el giro ({worst:.4f} mm³)")
    out["ok"] = not out["problems"]
    return out


# ----------------------------------------------------------------- exportación
def print_orientation(res: BuildResult, max_angle: float = 45.0, extra_tilts=(0.0,)):
    """Orientación de impresión del conjunto armado (§8.7). Todas las piezas comparten la misma
    transformación para seguir en un único sistema de coordenadas."""
    from .orient import best_orientation
    asm = trimesh.util.concatenate([S.to_trimesh(mf) for mf in res.pieces.values()])
    return best_orientation(asm, max_angle, extra_tilts)


def export(res: BuildResult, outdir: str, name: str = "figura", orient: bool = False,
           max_angle: float = 45.0, extra_tilts=(0.0,)) -> dict:
    """Escribe el ensamblado y un STL por parte. Con `orient=True` aplica a todo la orientación
    de impresión de menor voladizo y la informa."""
    os.makedirs(os.path.join(outdir, "piezas"), exist_ok=True)
    T = None
    info: dict = {}
    if orient:
        o = print_orientation(res, max_angle, extra_tilts)
        T = o.rotation
        info["orientacion"] = {"etiqueta": o.label, "voladizo_mm2": round(o.overhang_area, 2),
                               "base_mm2": round(o.base_area, 2), "angulo_max": max_angle}
    meshes = []
    for part, mf in res.pieces.items():
        tm = S.to_printable_trimesh(mf)
        if T is not None:
            tm.apply_transform(T)
        tm.export(os.path.join(outdir, "piezas", f"{name}_{part}.stl"))
        meshes.append(tm)
    asm = trimesh.util.concatenate(meshes)
    asm.export(os.path.join(outdir, f"{name}_ensamblada.stl"))
    info.update({"ensamblada": f"{name}_ensamblada.stl", "piezas": sorted(res.pieces)})
    return info
