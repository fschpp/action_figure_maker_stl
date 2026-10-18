"""Pipeline completo: STL normalizado -> piezas articuladas (plan §8.3–§8.10)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import numpy as np
import trimesh

from . import solids as S
from .head import HEAD_NAME, MIN_BASE_IOU, HeadSpec, build_head, split_head
from .joints import JointBuild, JointSpec, build_joint, flex_range, hinge_for, print_flex
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
    joints: dict = field(default_factory=dict)   # nombre de la articulación -> JointBuild (rodilla, codo)


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
          head: HeadSpec | None = None, joints: list[JointSpec] | None = None) -> BuildResult:
    """Construye las piezas. `body` debe estar normalizado y con el plano de simetría en x = 0."""
    say = progress or (lambda *_: None)
    trims = trims or []
    joints = list(joints or [])
    by_limb: dict[str, JointSpec] = {}
    for j in joints:
        if j.limb in by_limb:
            raise ValueError(f"«{j.limb}»: por ahora una sola articulación por miembro")
        if j.limb not in {l.name for l in limbs}:
            raise ValueError(f"la articulación «{j.name}» apunta al miembro «{j.limb}», que no existe")
        by_limb[j.limb] = j
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
    joint_builds: dict[str, JointBuild] = {}
    for lname, j in by_limb.items():
        say(f"Articulación {j.name}…")
        joint_builds[lname] = build_joint(built[lname]["limb"], j, built[lname]["face"].center, p)
    carved: dict[str, float] = {}
    for lname, jb in joint_builds.items():
        if built[lname]["spec"].kind == "leg" or jb.sweep is None:
            continue          # la pierna ya queda libre del torso con el barrido de cadera (la validación de cadena lo comprueba)
        # Brazo: el miembro cuelga pegado al torso y solo se talló estirado; el antebrazo flexionado entra en el torso.
        # Se talla también el barrido de la flexión (el mismo ensanchado que talla el brazo proximal).
        before = torso_half.volume()
        torso_half = S.difference(torso_half, jb.sweep)
        carved[lname] = before - torso_half.volume()
    if head_build is not None:
        say("Cavidad del cuello…")
        torso_half = S.difference(torso_half, head_build.pocket)
    torso_half = apply_trims(torso_half, trims, "torso")
    torso_half = S.drop_small_components(torso_half, 0.02)

    say("Pose sentada y espejado…")
    pieces: dict[str, S.Manifold] = {}
    res_motion: dict = {}
    report: dict = {"limbs": {}}
    res_joints: dict[str, JointBuild] = {}
    for name, b in built.items():
        limb = b["limb"]
        hip_sit = sit and b["spec"].kind == "leg"
        res_motion[name] = (b["limb"], b["face"].center, b["axis"], (b["angles"][0], b["angles"][-1]))
        jb = joint_builds.get(name)
        if jb is None:
            if hip_sit:
                limb = S.rotate_about_axis(limb, b["face"].center, b["axis"], p.sit_angle)
            pieces[f"{name}_izq"] = limb
            pieces[f"{name}_der"] = S.mirror_x(limb)
        else:
            j = by_limb[name]
            prox, dist = jb.proximal, jb.rotate_distal(print_flex(j, p))     # flexión de la pose impresa
            if hip_sit:
                prox = S.rotate_about_axis(prox, b["face"].center, b["axis"], p.sit_angle)
                dist = S.rotate_about_axis(dist, b["face"].center, b["axis"], p.sit_angle)
            pn, dn = j.names()
            for nm, mf in ((pn, prox), (dn, dist)):
                pieces[f"{nm}_izq"] = mf
                pieces[f"{nm}_der"] = S.mirror_x(mf)
            res_joints[j.name] = jb
            report.setdefault("joints", {})[j.name] = {
                "center": jb.center.tolist(), "axis": jb.axis.tolist(), "flex": list(jb.flex),
                "shoulder_h": round(jb.shoulder_h, 3), "radius": round(jb.radius, 3), "avisos": jb.warnings,
                "piezas": [pn, dn], "limb": name, "print_flex": print_flex(j, p)}
            if name in carved:
                report["joints"][j.name]["torso_tallado_mm3"] = round(2 * carved[name], 3)     # ambos lados (espejo)
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
        if split.replacement is not None:
            report["head"]["replacement"] = split.replacement
        if head_build.pin_margin is not None:
            report["head"]["pin_margin_mm"] = round(float(head_build.pin_margin), 3)
        res_motion[HEAD_NAME] = (head_build.head, head_build.face.center, head_build.axis, (0.0, 90.0))   # giro libre sobre el cuello: se valida 0–90°
    if p.decimate > 0:
        say("Reduciendo triángulos…")
        n0 = pieces["torso"].num_tri()
        pieces["torso"] = _decimate_keeping_clearance(pieces, p)
        report["torso_triangulos"] = [n0, pieces["torso"].num_tri()]
    return BuildResult(pieces=pieces, report=report, motion=res_motion, joints=res_joints)


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
             sweep_check_step: float = 2.5, joints: list[JointSpec] | None = None) -> dict:
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
    rep = res.report.get("head", {}).get("replacement")
    if rep is not None and rep["coincidencia_base"] < MIN_BASE_IOU:
        out["avisos"].append(f"{HEAD_NAME}: la base de la cabeza importada coincide solo un {100 * rep['coincidencia_base']:.0f} % "
                             f"con la del cuello (escala ×{rep['escala']:.3f}): puede quedar desalineada; probá otro giro o «arriba»")
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
    out["cadera"] = _validate_hip_pin(res, p, limbs, out)
    out["articulaciones"] = _validate_joints(res, p, joints, out, sweep_check_step)
    out["cadena"] = _validate_chain(res, p, out)
    out["ok"] = not out["problems"]
    return out


def hip_pin_walls(spec_pin, center, direction, clearance: float) -> dict:
    """Medidas de retención de un pasador de cadera (plan de articulaciones, Fase 3).

    * `pared_mm`: material del torso que queda entre la cavidad del miembro (dilatada `clearance`) y la de la
      cabeza: `shaft_len − 2·clearance` (la figura de referencia: 2,3 − 2·0,5 = 1,3 mm).
    * `separacion_cabezas_mm`: distancia entre la cabeza de este lado y su espejo (x → −x); negativa si se cruzan.
    * `relacion_cabeza_vastago`: head_d / shaft_d.
    """
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    c = np.asarray(center, float)
    r = spec_pin.head_d / 2
    half_x = r * float(np.sqrt(max(0.0, 1.0 - d[0] ** 2)))        # semiancho en X de un disco perpendicular a `d`
    x_base = (c + d * spec_pin.shaft_len)[0] - half_x
    x_tip = (c + d * (spec_pin.shaft_len + spec_pin.head_h))[0] - half_x
    return {"pared_mm": spec_pin.shaft_len - 2.0 * clearance,
            "separacion_cabezas_mm": 2.0 * min(x_base, x_tip),
            "relacion_cabeza_vastago": spec_pin.head_d / spec_pin.shaft_d}


MIN_HEAD_SHAFT_RATIO = 1.6


def _validate_hip_pin(res: BuildResult, p: Params, limbs: list[LimbSpec], out: dict) -> dict:
    """Pared detrás de la cabeza (≥ `min_wall`), cabezas enfrentadas (separación ≥ 2·holgura) y relación
    cabeza/vástago ≥ 1,6, para el pasador de cadera de cada pierna."""
    info: dict = {}
    for spec in limbs:
        r = res.report.get("limbs", {}).get(spec.name)
        if spec.kind != "leg" or r is None:
            continue
        pin = _pin_spec(spec, p)
        w = hip_pin_walls(pin, r["pin_center"], r["pin_dir"], p.clearance)
        info[spec.name] = {k: round(float(v), 3) for k, v in w.items()}
        if w["pared_mm"] < p.min_wall - 1e-9:
            out["problems"].append(f"{spec.name}: la pared tras la cabeza del pasador queda en {w['pared_mm']:.2f} mm "
                                   f"(< {p.min_wall:.1f} mm): alargá el vástago o bajá la holgura")
        if w["separacion_cabezas_mm"] < 2 * p.clearance - 1e-9:
            out["problems"].append(f"{spec.name}: las cabezas de las dos caderas quedan a {w['separacion_cabezas_mm']:.2f} mm "
                                   f"(< 2·holgura = {2 * p.clearance:.2f} mm)")
        if w["relacion_cabeza_vastago"] < MIN_HEAD_SHAFT_RATIO:
            out["avisos"].append(f"{spec.name}: cabeza/vástago = {w['relacion_cabeza_vastago']:.2f} (< {MIN_HEAD_SHAFT_RATIO}): "
                                 "la cabeza puede salirse")
    return info


RETENTION_MM = 1.0
JOINT_TOL_MM3 = 1e-4


def posed_pieces(res: BuildResult, hip_angle: float, flex: dict | None = None,
                 p: Params | None = None) -> dict:
    """Miembros del lado izquierdo y sus espejos (`<pieza>_izq/_der`) con la cadena de rotaciones compuesta.

    Una pierna con rodilla se compone de proximal (muslo) y distal (pantorrilla): el distal gira `flex[nombre de la
    articulación]` grados (por defecto la flexión de la pose impresa) alrededor del eje de la bisagra y luego el
    conjunto gira `hip_angle` alrededor del pasador de cadera. Los miembros sin articulación giran enteros. La cabeza
    no participa. Es la misma composición que usa `build` para la pose sentada (plan de articulaciones, Fase 4)."""
    flex = flex or {}
    rep = res.report.get("joints", {})
    by_limb = {r["limb"]: (jn, r) for jn, r in rep.items()}
    out: dict = {}
    for name, (limb0, center, axis, (a0, a1)) in res.motion.items():
        if name == HEAD_NAME:
            continue
        turns = a1 > a0
        if name in by_limb:
            jn, r = by_limb[name]
            jb = res.joints[jn]
            f = float(flex.get(jn, r["print_flex"]))
            parts = {r["piezas"][0]: jb.proximal, r["piezas"][1]: jb.rotate_distal(f)}
        else:
            parts = {name: limb0}
        for nm, mf in parts.items():
            if turns:
                mf = S.rotate_about_axis(mf, center, axis, hip_angle)
            out[f"{nm}_izq"] = mf
            out[f"{nm}_der"] = S.mirror_x(mf)
    return out


def _validate_chain(res: BuildResult, p: Params, out: dict, hip_step: float = 15.0) -> dict:
    """Recorrido de la cadena completa (plan de articulaciones §4, Fase 4): cadera de 0° a `sit_angle` combinada con
    la flexión de cada articulación (mínima, media y máxima) y sin choque con el torso ni con las demás piezas
    (pantorrilla vs. pierna opuesta, antebrazo vs. torso). Solo interesan las piezas distales: la proximal ya se
    valida con el barrido de cadera; proximal–distal de un mismo lado tiene su propia validación."""
    info: dict = {}
    if not res.joints:
        return info
    torso = res.pieces["torso"]
    jr = {jn: r for jn, r in res.report.get("joints", {}).items()}
    hips = list(np.arange(0.0, p.sit_angle + 1e-9, hip_step))
    names_cache: dict = {}
    for jn, jb in res.joints.items():
        r = jr[jn]
        lo, hi = jb.flex
        dist_nm = r["piezas"][1]
        prox_nm = r["piezas"][0]
        worst = {}
        for ha in hips:
            for fl in sorted({lo, max(lo, 0.0) + (hi - max(lo, 0.0)) / 2, hi}):
                key = (ha, fl)
                posed = names_cache.get(key)
                if posed is None:
                    posed = names_cache[key] = posed_pieces(res, ha, {k: (fl if k == jn else (v.flex[1] / 2)) for k, v in res.joints.items()})
                d = posed[f"{dist_nm}_izq"]
                hits = {"torso": float((d ^ torso).volume())}
                for other, mf in posed.items():
                    if other in (f"{dist_nm}_izq", f"{prox_nm}_izq"):
                        continue
                    hits[other] = float((d ^ mf).volume())
                for other, v in hits.items():
                    if v > worst.get(other, (0.0,))[0]:
                        worst[other] = (v, ha, fl)
        bad = {k: v for k, v in worst.items() if v[0] > JOINT_TOL_MM3}
        info[jn] = {k: {"mm3": round(v[0], 5), "cadera": v[1], "flexion": v[2]} for k, v in bad.items()}
        for k, (v, ha, fl) in bad.items():
            out["problems"].append(f"{jn}: {dist_nm}_izq choca con {k} (cadera {ha:.0f}°, flexión {fl:.0f}°, {v:.3f} mm³)")
    return info


def _ray_hits(tris: np.ndarray, origin, direction, eps: float = 1e-12) -> np.ndarray:
    """Distancias (ordenadas, sin duplicados por aristas compartidas) de todos los cruces del rayo con la malla.
    Möller–Trumbore vectorizado; no usa rtree/embree (igual que `gui.picking`)."""
    o = np.asarray(origin, float)
    d = np.asarray(direction, float)
    v0 = tris[:, 0]
    e1, e2 = tris[:, 1] - v0, tris[:, 2] - v0
    p = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > eps
    inv = np.zeros_like(det)
    inv[ok] = 1.0 / det[ok]
    sv = o - v0
    u = np.einsum("ij,ij->i", sv, p) * inv
    q = np.cross(sv, e1)
    v = (q @ d) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    t = np.sort(t[ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 1e-6)])
    return t[np.concatenate(([True], np.diff(t) > 1e-5))] if len(t) else t


def joint_wall_mm(jb: JointBuild, hinge, rays: int = 16) -> float | None:
    """Pared mínima del cuerpo proximal alrededor de los bulones (plan de articulaciones §4, Fase 4).

    Desde cerca de la punta de cada bulón se lanzan rayos a lo largo del eje (pared axial detrás del agujero ciego) y
    radiales (pared alrededor del agujero); el espesor es la distancia entre el 1.er cruce (cara del agujero) y el
    2.º (superficie exterior). Los rayos que salen por la ranura (un solo cruce) no cuentan. None si no hay datos."""
    tris = np.asarray(S.to_trimesh(jb.proximal).triangles, float)
    ax = np.asarray(jb.axis, float)
    u = np.cross(ax, (0.0, 0.0, 1.0))
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(ax, (0.0, 1.0, 0.0))
    u /= np.linalg.norm(u)
    v = np.cross(ax, u)
    dirs_for = lambda s: [s * ax] + [np.cos(t) * u + np.sin(t) * v for t in np.linspace(0, 2 * np.pi, rays, endpoint=False)]
    best = None
    for s, tip in ((1.0, jb.tips[1] or hinge.tongue_t / 2 + hinge.pin_len), (-1.0, jb.tips[0] or hinge.tongue_t / 2 + hinge.pin_len)):
        # cerca de la punta del bulón (agujero ciego); en el bastago, 1 mm antes de la punta para medir sobre la parte
        # cilíndrica del orificio y no sobre su chaflán, pero siempre fuera de la ranura de la lengüeta
        back = max(tip - 1.0, hinge.tongue_t / 2 + 0.6) if getattr(hinge, "rod", False) else tip - 0.1
        origin = np.asarray(jb.center, float) + s * ax * back
        ds = dirs_for(s)
        for d in ds:
            t = _ray_hits(tris, origin, d)
            if len(t) >= 2:
                t = t[np.concatenate(([True], np.diff(t) > 0.01))]     # un rayo que roza una arista cuenta dos veces el mismo cruce
            if len(t) >= 2:
                best = float(t[1] - t[0]) if best is None else min(best, float(t[1] - t[0]))
    return best


def _validate_joints(res: BuildResult, p: Params, joints, out: dict, step: float) -> dict:
    """Rodilla / codo: recorrido libre dentro del rango, tope efectivo fuera de él, holgura mínima,
    retención (la pieza distal no sale al desplazarla) y medidas mínimas (plan de articulaciones §4, Fase 4)."""
    info: dict = {}
    by_name = {j.name: j for j in (joints or [])}
    for jname, jb in res.joints.items():
        j = by_name.get(jname)
        hinge = hinge_for(j, p) if j is not None else p.hinge_pin
        lo, hi = jb.flex
        prox = jb.proximal
        rec = {}
        worst = 0.0
        for f in np.arange(lo, hi + 1e-9, step):
            worst = max(worst, float((jb.rotate_distal(float(f)) ^ prox).volume()))
        rec["recorrido_mm3"] = round(worst, 5)
        if worst > JOINT_TOL_MM3:
            out["problems"].append(f"{jname}: la pieza distal interfiere con la proximal dentro del rango "
                                   f"{lo:.0f}°…{hi:.0f}° ({worst:.4f} mm³)")
        reach = p.stop_margin + 5.0
        for label, f in (("flexion", hi + reach), ("extension", lo - reach)):
            v = float((jb.rotate_distal(f) ^ prox).volume())
            rec[f"tope_{label}_mm3"] = round(v, 5)
            if v <= JOINT_TOL_MM3:
                out["problems"].append(f"{jname}: sin tope de {'flexión' if label == 'flexion' else 'extensión'}: "
                                       f"a {f:.0f}° todavía no choca (el tope debe estar a ≤ {reach:.0f}° del límite)")
        rest = jb.distal if lo > 0 or hi < 0 else jb.rotate_distal(0.0)
        gap = float(rest.min_gap(prox, 5.0))
        rec["holgura_mm"] = round(gap, 4)
        need = 0.9 * min(p.clearance, p.axial_clearance)
        if gap < need:
            out["problems"].append(f"{jname}: holgura mínima {gap:.3f} mm < {need:.3f} mm")
        free = []
        for lbl, vec in (("+X", (1, 0, 0)), ("-X", (-1, 0, 0)), ("+Y", (0, 1, 0)), ("-Y", (0, -1, 0)),
                         ("+Z", (0, 0, 1)), ("-Z", (0, 0, -1))):
            moved = jb.rotate_distal(max(lo, min(hi, 0.0))).translate(tuple(RETENTION_MM * c for c in vec))
            if float((moved ^ prox).volume()) <= JOINT_TOL_MM3:
                free.append(lbl)
        rec["retencion_libre"] = free
        if free:
            out["problems"].append(f"{jname}: la pieza distal sale sin chocar al desplazarla {RETENTION_MM:g} mm "
                                   f"hacia {', '.join(free)}")
        if hinge.pin_d < p.min_hinge_pin_d:
            out["problems"].append(f"{jname}: bulón Ø{hinge.pin_d} < {p.min_hinge_pin_d}")
        if hinge.tongue_t < p.min_tongue:
            out["problems"].append(f"{jname}: lengüeta de {hinge.tongue_t} mm < {p.min_tongue}")
        wall = joint_wall_mm(jb, hinge)
        rec["pared_mm"] = None if wall is None else round(wall, 3)
        if wall is not None and wall < 2 * p.nozzle:
            out["problems"].append(f"{jname}: pared del cuerpo proximal alrededor del bulón de {wall:.2f} mm "
                                   f"< 2 líneas de boquilla ({2 * p.nozzle:g} mm): no se imprime")
        elif wall is not None and wall < p.min_wall:
            out["avisos"].append(f"{jname}: pared del cuerpo proximal alrededor del bulón de {wall:.2f} mm "
                                 f"< {p.min_wall:g} mm (subir el eje, alargar el miembro o achicar el bulón)")
        out["avisos"].extend(jb.warnings)
        info[jname] = rec
    return info


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
