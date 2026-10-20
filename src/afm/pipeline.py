"""Pipeline completo: STL normalizado -> piezas articuladas (plan §8.3–§8.10)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import numpy as np
import trimesh

from . import solids as S
from . import detent as DT
from .head import HEAD_NAME, MIN_BASE_IOU, HeadSpec, build_head, split_head
from .joints import JointBuild, JointSpec, build_joint, flex_range, hinge_for, print_flex
from .limbs import LimbSpec, ContactFace, check_limb_plane, pin_margin, separate_limb
from .params import Params
from .pins import mushroom_pin
from .planes import Plane
from .trims import Trim, apply_trims
from .waist import PELVIS_NAME, WAIST_NAME, WaistBuild, WaistSpec, build_waist

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
    detent: dict = field(default_factory=dict)   # nombre del miembro -> DetentLimb (mecanismo de clic)
    detent_ref: S.Manifold | None = None         # torso SIN lengüetas ni puntas: referencia para el giro libre del clic
    waist: WaistBuild | None = None              # cintura: `pieces["torso"]` es el pecho (arriba) y `pieces["pelvis"]` la pelvis


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
          head: HeadSpec | None = None, joints: list[JointSpec] | None = None,
          waist: WaistSpec | None = None) -> BuildResult:
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
    detents: dict[str, DT.DetentLimb] = {}
    for spec in limbs:
        say(f"Separando {spec.name}…")
        limb_raw, face = separate_limb(half, spec, p.burr)
        limb_raw = apply_trims(limb_raw, trims, spec.name)
        axis = _rotation_axis(face)
        dspec = DT.detent_spec_for(spec, p)
        if dspec is not None:
            say(f"Clic de {spec.name}…")
            th = _print_angle(spec, p, dspec, sit)
            rng = _detent_sweep(spec, p)
            dl = DT.build_detent_limb(spec.name, limb_raw, face, axis, dspec, p, _pin_spec(spec, p), th, rng)
            detents[spec.name] = dl
            torso_half = S.difference(torso_half, dl.sweep)
            angles = np.arange(rng[0], rng[1] + 1e-9, p.sweep_step) if rng and rng[1] > rng[0] else [0.0]
            if len(angles) > 1 and angles[-1] < rng[1] - 1e-9:
                angles = np.append(angles, rng[1])
            built[spec.name] = {"limb": dl.limb, "face": face, "axis": axis, "spec": spec,
                                "angles": [float(a) for a in angles], "print_angle": th,
                                "pin_margin": pin_margin(face, _pin_spec(spec, p).shaft_d)}
            continue
        pin = mushroom_pin(face.center, face.outward, _pin_spec(spec, p))
        limb = S.union(limb_raw, pin)
        say(f"Cavidad de {spec.name}…")
        grown = S.dilate(limb, p.clearance)
        a0, a1 = _sweep_range(spec, p)
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
        th = b.get("print_angle", p.sit_angle if hip_sit else 0.0)       # giro de la pose impresa alrededor del pasador
        res_motion[name] = (b["limb"], b["face"].center, b["axis"], (b["angles"][0], b["angles"][-1]))
        jb = joint_builds.get(name)
        if jb is None:
            if th:
                limb = S.rotate_about_axis(limb, b["face"].center, b["axis"], th)
            pieces[f"{name}_izq"] = limb
            pieces[f"{name}_der"] = S.mirror_x(limb)
        else:
            j = by_limb[name]
            prox, dist = jb.proximal, jb.rotate_distal(print_flex(j, p))     # flexión de la pose impresa
            if th:
                prox = S.rotate_about_axis(prox, b["face"].center, b["axis"], th)
                dist = S.rotate_about_axis(dist, b["face"].center, b["axis"], th)
            pn, dn = j.names()
            for nm, mf in ((pn, prox), (dn, dist)):
                pieces[f"{nm}_izq"] = mf
                pieces[f"{nm}_der"] = S.mirror_x(mf)
            res_joints[j.name] = jb
            report.setdefault("joints", {})[j.name] = {
                "center": jb.center.tolist(), "axis": jb.axis.tolist(), "flex": list(jb.flex),
                "shoulder_h": round(jb.shoulder_h, 3), "radius": round(jb.radius, 3), "avisos": jb.warnings,
                "piezas": [pn, dn], "limb": name, "print_flex": print_flex(j, p), "print_hip": float(th)}
            if name in carved:
                report["joints"][j.name]["torso_tallado_mm3"] = round(2 * carved[name], 3)     # ambos lados (espejo)
        report["limbs"][name] = {
            "pin_center": b["face"].center.tolist(), "pin_dir": b["face"].outward.tolist(),
            "contact_area": b["face"].area, "sweep": [b["angles"][0], b["angles"][-1]],
        }
        if b["pin_margin"] is not None:
            report["limbs"][name]["pin_margin_mm"] = round(float(b["pin_margin"]), 3)
        if name in detents:
            dl = detents[name]
            report["limbs"][name]["mecanismo"] = "clic"
            report["limbs"][name]["pin_shift_mm"] = dl.spec.disc_h          # el hongo queda `disc_h` detrás del disco
            report["limbs"][name]["disc_margin_mm"] = None if (m := pin_margin(b["face"], dl.spec.disc_d)) is None else round(float(m), 3)
            report.setdefault("detent", {})[name] = DT.summary(dl)
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
    ref = None
    if detents:
        say("Lengüetas del clic…")
        ref = pieces["torso"]
        pieces["torso"] = DT.add_fingers(ref, list(detents.values()))
        for nm, dl in detents.items():
            dl.info["protrusion_mm3"] = round(DT.protrusion_mm3(dl, body), 4)
            report["detent"][nm]["protrusion_mm3"] = dl.info["protrusion_mm3"]
            wall = DT.finger_wall_mm(pieces["torso"], dl)
            report["detent"][nm]["pared_sobre_lengueta_mm"] = None if wall is None else round(wall, 3)
    waist_build = None
    if waist is not None:
        say("Cintura…")
        waist_build = build_waist(pieces["torso"], waist, p, skin=body)
        pieces["torso"], pieces[PELVIS_NAME] = waist_build.upper, waist_build.lower
        report["waist"] = _waist_report(waist_build, built, by_limb, head_build is not None)
    return BuildResult(pieces=pieces, report=report, motion=res_motion, joints=res_joints, detent=detents, detent_ref=ref,
                       waist=waist_build)


def _waist_report(wb: WaistBuild, built: dict, by_limb: dict, has_head: bool) -> dict:
    """Informe de la cintura: datos del pasador y qué piezas quedan arriba (giran con el pecho) y cuáles abajo."""
    upper, lower = ["torso"], [PELVIS_NAME]
    if has_head:
        upper.append(HEAD_NAME)
    for name, b in built.items():
        side = float((b["face"].center - wb.plane.origin) @ wb.plane.normal)    # hombro: arriba; cadera: abajo
        names = list(by_limb[name].names()) if name in by_limb else [name]
        (upper if side > 0 else lower).extend(f"{x}{s}" for x in names for s in ("_izq", "_der"))
    return {**wb.info, "arriba": upper, "abajo": lower}


def body_ref(res: BuildResult) -> S.Manifold:
    """Torso entero contra el que se miden los miembros: con cintura, pecho y pelvis juntos (separados por la holgura)."""
    torso = res.pieces["torso"]
    return S.union(torso, res.pieces[PELVIS_NAME]) if PELVIS_NAME in res.pieces else torso

def _print_angle(spec: LimbSpec, p: Params, dspec, sit: bool) -> float:
    """Ángulo del miembro en la pose impresa: la pose sentada de la cadera, o el del brazo (`DetentSpec.print_angle`)."""
    if spec.kind == "leg":
        return float(p.sit_angle) if sit else 0.0
    return float(dspec.print_angle)


def _detent_sweep(spec: LimbSpec, p: Params):
    """Rango del barrido con clic: la cadera hasta el tope (como el hongo); el brazo da la vuelta completa (None)
    salvo que el miembro declare `sweep_deg`."""
    if spec.sweep_deg is not None:
        return tuple(spec.sweep_deg)
    return (0.0, p.sit_angle + p.hip_margin) if spec.kind == "leg" else None


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
             sweep_check_step: float = 2.5, joints: list[JointSpec] | None = None,
             detent_free_step: float = 10.0) -> dict:
    out: dict = {"ok": True, "problems": [], "avisos": [], "avisos_pasador": [], "pieces": {}, "gaps": {}}
    torso = body_ref(res)         # con cintura: pecho + pelvis
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
        if name in ("torso", PELVIS_NAME) and comps != 1:
            out["problems"].append(f"{name}: {comps} componentes (debería ser 1, soldado)")
    clearance_of = {}          # holgura mínima exigida por pieza: con clic, la punta deja `detent_gap` contra el disco
    for lname, dl in res.detent.items():
        for pn in _limb_piece_names(res, lname):
            for side in ("_izq", "_der"):
                clearance_of[f"{pn}{side}"] = min(p.clearance, dl.spec.detent_gap)
    for name, mf in res.pieces.items():
        if name in ("torso", PELVIS_NAME):
            continue
        inter = (mf ^ torso).volume()
        gap = mf.min_gap(torso, 5.0)
        out["gaps"][name] = round(float(gap), 4)
        need_gap = clearance_of.get(name, p.clearance)
        if inter > 1e-6:
            out["problems"].append(f"{name}: interpenetra el torso ({inter:.4f} mm³)")
        elif gap < need_gap - 0.1 * need_gap - 1e-6 and gap < 5.0:
            out["problems"].append(f"{name}: holgura mínima {gap:.3f} mm < {need_gap:.3f} mm")
    names = [n for n in res.pieces if n not in ("torso", PELVIS_NAME)]
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
        ref = res.detent_ref if (name in res.detent and res.detent_ref is not None) else torso   # sin la deflexión elástica de las lengüetas
        for ang in np.arange(a0, a1 + 1e-9, sweep_check_step):
            worst = max(worst, (S.rotate_about_axis(limb0, center, axis, ang) ^ ref).volume())
        out["recorrido"][name] = round(float(worst), 5)
        if worst > 1e-4:
            out["problems"].append(f"{name}: interfiere con el torso durante el giro ({worst:.4f} mm³)")
    out["cadera"] = _validate_hip_pin(res, p, limbs, out)
    out["detent"] = _validate_detent(res, p, out, detent_free_step)
    out["articulaciones"] = _validate_joints(res, p, joints, out, sweep_check_step)
    out["cadena"] = _validate_chain(res, p, out)
    out["cintura"] = _validate_waist(res, p, out)
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
        d = np.asarray(r["pin_dir"], float)
        shift = r.get("pin_shift_mm", 0.0)          # con clic el hongo queda `disc_h` mm más adentro
        w = hip_pin_walls(pin, np.asarray(r["pin_center"], float) + d / np.linalg.norm(d) * shift, r["pin_dir"], p.clearance)
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


def _validate_detent(res: BuildResult, p: Params, out: dict, step: float) -> dict:
    """Mecanismo de clic (plan de clic §6): deformación de la lengüeta, pared sobre ella y raíz lejos del plano de
    simetría, lengüetas dentro de la piel, clic en la pose impresa, giro libre y clics alcanzables, par estimado."""
    info: dict = {}
    for lname, dl in res.detent.items():
        sp = dl.spec
        rec = DT.summary(dl)
        rec.pop("spec", None)
        eps = DT.tongue_strain(sp)
        if eps > sp.max_strain:
            out["problems"].append(f"{lname}: lengüeta del clic con deformación {100 * eps:.2f} % > {100 * sp.max_strain:.1f} % "
                                   "(alargala, afinala o bajá la profundidad de la muesca)")
        if sp.notch_depth <= sp.detent_gap:
            out["problems"].append(f"{lname}: profundidad de muesca ({sp.notch_depth}) ≤ holgura punta/muesca ({sp.detent_gap}): no hace clic")
        if sp.slit_w < 2 * p.nozzle * 0.5 - 1e-9:
            out["avisos"].append(f"{lname}: ranura del clic de {sp.slit_w} mm < 1 línea de boquilla ({p.nozzle} mm): se puede fusionar")
        if sp.finger_t < 3 * p.nozzle - 1e-9:
            out["avisos"].append(f"{lname}: lengüeta de {sp.finger_t} mm < 3 líneas de boquilla")
        torque = DT.hold_torque(sp)
        if torque < sp.hold_torque_min:
            out["avisos"].append(f"{lname}: par de retención estimado {torque:.1f} N·mm < {sp.hold_torque_min:g} (estimación: "
                                 "subí finger_w o notch_depth y calibrá con probetas)")
        elif torque > sp.move_torque_max:
            out["avisos"].append(f"{lname}: par de retención estimado {torque:.1f} N·mm > {sp.move_torque_max:g}: cuesta mover la pose")
        if rec.get("ajuste_lengueta"):
            out["avisos"].append(f"{lname}: {rec['ajuste_lengueta']}")
        if rec["lado_torso"] < 0 and rec["raiz_x"] < p.min_wall - 1e-6:
            out["problems"].append(f"{lname}: la raíz de la lengüeta queda a {rec['raiz_x']:.2f} mm del plano de simetría (< {p.min_wall:g} mm)")
        wall = res.report["detent"][lname].get("pared_sobre_lengueta_mm")
        rec["pared_sobre_lengueta_mm"] = wall
        if wall is None:
            out["avisos"].append(f"{lname}: no se pudo medir la pared sobre la lengüeta")
        elif wall < 2 * p.nozzle:
            out["problems"].append(f"{lname}: la pared de torso sobre la lengüeta es de {wall:.2f} mm < 2 líneas de boquilla")
        elif wall < p.min_wall:
            out["avisos"].append(f"{lname}: la pared de torso sobre la lengüeta es de {wall:.2f} mm < {p.min_wall:g} mm")
        prot = dl.info.get("protrusion_mm3", 0.0)
        rec["protrusion_mm3"] = prot
        if prot > 1e-3:
            out["avisos"].append(f"{lname}: las lengüetas sobresalen de la piel original ({prot:.3f} mm³): se verá un bulto")
        dm = res.report["limbs"][lname].get("disc_margin_mm")
        if dm is not None and dm < 0:
            out["avisos"].append(f"{lname}: el disco del clic sobresale {-dm:.2f} mm de la cara de contacto del miembro")
        rec["clic_por_giro"] = DT.click_table(dl)
        at_print = rec["clic_por_giro"].get(f"{0.0:g}")
        if at_print and (at_print["interf_punta_disco_mm3"] > 1e-3 or at_print["dist_punta_disco_mm"] < 0.9 * sp.detent_gap):
            out["problems"].append(f"{lname}: la punta no queda libre en la muesca de la pose impresa "
                                   f"(interferencia {at_print['interf_punta_disco_mm3']} mm³, distancia {at_print['dist_punta_disco_mm']} mm)")
        own = {f"{pn}{side}" for pn in _limb_piece_names(res, lname) for side in ("_izq", "_der")}
        blockers = {"torso": res.detent_ref if res.detent_ref is not None else res.pieces["torso"]}
        blockers.update({n: mf for n, mf in res.pieces.items() if n not in ("torso", PELVIS_NAME) and n not in own})
        fr = DT.free_range(dl, blockers, step)
        rec["giro_libre"] = fr
        if dl.print_angle in fr["clics_descartados"] or any(abs(a - dl.print_angle) < 1e-6 for a in fr["clics_descartados"]):
            out["problems"].append(f"{lname}: el clic de la pose impresa ({dl.print_angle:g}°) cae fuera del giro libre")
        for a in fr["clics_descartados"]:
            if abs(a - dl.print_angle) > 1e-6:
                hit = fr["interferencias"].get(f"{a:g}", {})
                out["avisos"].append(f"{lname}: clic a {a:g}° descartado (toca {', '.join(hit) or 'otra pieza'})")
        info[lname] = rec
    return info


def straight_leg_flex(res: BuildResult) -> dict:
    """Flexión 0° (pierna estirada) para cada articulación de una pierna (miembro que gira en la cadera). Es la pose
    de «imprimir sin soportes»: muslo y pantorrilla en línea, ambos horizontales con la cadera sentada. Los codos no
    se tocan (el brazo se gira entero hacia adelante)."""
    rep = res.report.get("joints", {})
    out: dict = {}
    for jn, r in rep.items():
        m = res.motion.get(r["limb"])
        if m is not None and m[3][1] > m[3][0]:
            out[jn] = 0.0
    return out


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
    torso = body_ref(res)
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


WAIST_STEP_DEG = 5.0
WAIST_SKIN_TOL_MM3 = 0.5       # lo que sobresale la cavidad engrosada `min_wall` por la teselación de la piel no es una pared fina
MIN_HEAD_SHAFT_RATIO_WAIST = 1.6


def _bbox_overlap(a: S.Manifold, b: S.Manifold, tol: float = 1e-6) -> bool:
    ba, bb = a.bounding_box(), b.bounding_box()
    return all(ba[i] < bb[i + 3] + tol and bb[i] < ba[i + 3] + tol for i in range(3))


def _validate_waist(res: BuildResult, p: Params, out: dict, step: float = WAIST_STEP_DEG,
                    max_deg: float | None = None) -> dict:
    """Cintura: pasador (Ø mínimo, pared tras la cabeza, relación cabeza/vástago, que no sobresalga de la piel),
    holgura pecho–pelvis, pasador cautivo, y giro libre: todo lo que cuelga del pecho (pecho, cabeza, brazos) gira
    hasta `twist_deg` a cada lado alrededor del eje del pasador sin chocar con la pelvis ni las piernas.
    `info["giro_libre"]` da el ángulo máximo sin choque a cada lado (hasta `max_deg`; por defecto `twist_deg`)."""
    wb = res.waist
    if wb is None:
        return {}
    rep = res.report["waist"]
    sp, info = wb.pin_spec, dict(rep)
    if sp.shaft_d < p.min_pin_d:
        out["problems"].append(f"{WAIST_NAME}: vástago Ø{sp.shaft_d:.2f} < {p.min_pin_d:g}"
                               + (f" (se achicó ×{rep['pin_reducido']} para entrar en la cara: elegí un plano más ancho)"
                                  if rep.get("pin_reducido") else ""))
    elif rep.get("pin_reducido"):
        out["avisos"].append(f"{WAIST_NAME}: la cara del corte es angosta: el pasador se achicó ×{rep['pin_reducido']} "
                             f"(vástago Ø{sp.shaft_d:.1f}, cabeza Ø{sp.head_d:.1f})")
    if rep["pared_tras_cabeza_mm"] < p.min_wall - 1e-9:
        out["problems"].append(f"{WAIST_NAME}: la pared tras la cabeza del pasador queda en {rep['pared_tras_cabeza_mm']:.2f} mm "
                               f"(< {p.min_wall:.1f} mm): alargá el vástago o bajá la holgura")
    if rep["relacion_cabeza_vastago"] < MIN_HEAD_SHAFT_RATIO_WAIST:
        out["avisos"].append(f"{WAIST_NAME}: cabeza/vástago = {rep['relacion_cabeza_vastago']:.2f} "
                             f"(< {MIN_HEAD_SHAFT_RATIO_WAIST}): la cabeza puede salirse")
    m = rep.get("margen_vastago_mm")
    if m is not None and m < 0:
        out["avisos_pasador"].append(f"{WAIST_NAME}: el vástago del pasador sobresale {-m:.2f} mm de la cara del corte")
    elif m is not None and m < p.min_wall:
        out["avisos_pasador"].append(f"{WAIST_NAME}: el vástago queda a {m:.2f} mm del borde de la cara del corte "
                                     f"(< {p.min_wall:.1f} mm de pared)")
    if rep.get("pasador_fuera_mm3", 0.0) > WAIST_SKIN_TOL_MM3:
        out["avisos_pasador"].append(f"{WAIST_NAME}: el pasador con {p.min_wall:g} mm de pared sobresale de la piel del torso "
                                     f"({rep['pasador_fuera_mm3']:.3f} mm³): corré el plano a una parte más ancha o achicá el pasador")
    upper, lower = res.pieces["torso"], res.pieces[PELVIS_NAME]
    inter = float((upper ^ lower).volume())
    gap = float(upper.min_gap(lower, 5.0))
    info["holgura_mm"] = round(gap, 4)
    if inter > 1e-6:
        out["problems"].append(f"{WAIST_NAME}: pecho y pelvis se interpenetran ({inter:.4f} mm³)")
    elif gap < 0.9 * p.clearance - 1e-6 and gap < 5.0:
        out["problems"].append(f"{WAIST_NAME}: holgura pecho–pelvis {gap:.3f} mm < {p.clearance:.3f} mm")
    # pasador cautivo: separar las mitades 1 mm a lo largo del eje las hace chocar contra el labio de la cavidad
    n = wb.axis
    moved = upper.translate(tuple(n * 1.0)) if wb.pin_on == "lower" else lower.translate(tuple(-n * 1.0))
    still = lower if wb.pin_on == "lower" else upper
    info["cautivo"] = bool((moved ^ still).volume() > 1e-6)
    if not info["cautivo"]:
        out["problems"].append(f"{WAIST_NAME}: el pasador no queda retenido (separar las mitades 1 mm no choca)")
    # giro libre
    need = float(rep["giro_pedido_grados"])
    limit = float(need if max_deg is None else max_deg)
    moving = {nm: res.pieces[nm] for nm in rep["arriba"] if nm in res.pieces}
    fixed = {nm: res.pieces[nm] for nm in rep["abajo"] if nm in res.pieces}
    free = {}
    for sign, key in ((1.0, "+"), (-1.0, "-")):
        ok_to = 0.0
        hit = {}
        for ang in np.arange(step, limit + 1e-9, step):
            hit = {}
            for mn, mm in moving.items():
                rot = S.rotate_about_axis(mm, wb.face.center, n, sign * ang)
                for fn, fm in fixed.items():
                    if _bbox_overlap(rot, fm) and (rot ^ fm).volume() > JOINT_TOL_MM3:
                        hit[f"{mn}~{fn}"] = round(float((rot ^ fm).volume()), 5)
            if hit:
                break
            ok_to = float(ang)
        free[key] = {"hasta_grados": ok_to, "choca": hit}
    info["giro_libre"] = free
    for key, side in (("+", "a un lado"), ("-", "al otro lado")):
        if free[key]["hasta_grados"] < need - 1e-6 and free[key]["choca"]:
            out["problems"].append(f"{WAIST_NAME}: al girar {side} choca a más de {free[key]['hasta_grados']:g}° "
                                   f"({', '.join(free[key]['choca'])}); el giro pedido es {need:g}°")
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


# ----------------------------------------------------------------- imprimir sin soportes
FLAT_ANGLE_MAX = 80.0       # tope del giro de los brazos hacia adelante (°)


def _limb_piece_names(res: BuildResult, limb: str) -> list[str]:
    """Piezas (sin sufijo de lado) que componen un miembro: una sola, o proximal + distal si tiene articulación."""
    for r in res.report.get("joints", {}).values():
        if r["limb"] == limb:
            return list(r["piezas"])
    return [limb]


def _rotate_pts(pts: np.ndarray, origin, axis, angle_deg: float) -> np.ndarray:
    ax = np.asarray(axis, float)
    ax = ax / np.linalg.norm(ax)
    a = np.radians(angle_deg)
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    R = np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * (K @ K)
    o = np.asarray(origin, float)
    return (pts - o) @ R.T + o


def _carve_torso_for_arm(torso: S.Manifold, arm_parts: list, center, axis, theta: float, p: Params) -> tuple:
    """Talla el torso con el barrido del brazo entre 0° y `theta` (ensanchado una holgura algo menor que la de la
    cavidad, para no tocar donde ya hay hueco). El barrido se calcula con un campo de distancia voxelizado
    (`S.swept_offset`): Minkowski sobre un brazo de ~200k caras no termina. El resultado depende de cómo cae la malla
    del barrido sobre la del torso: una combinación (holgura, voxel) puede dejar vértices que pinchan la malla, así que se prueban unas pocas y
    se queda con la primera sin aristas no variedad (o la que menos tiene). Devuelve (torso, {holgura, paso, aristas})."""
    best = None
    for factor, pitch in ((0.9, 0.2), (0.9, 0.25), (0.95, 0.2), (0.85, 0.22)):
        sweep = S.swept_offset(arm_parts, center, axis, theta, factor * p.clearance, pitch, step_deg=3.0)
        cand = S.difference(torso, S.union(sweep, S.mirror_x(sweep)))
        bad = _nonmanifold_edges(cand)
        if best is None or bad < best[2]:
            best = (cand, (factor, pitch), bad)
        if bad == 0:
            break
    return best[0], {"holgura_factor": best[1][0], "voxel_mm": best[1][1], "paso_grados": 3.0, "aristas_no_variedad": best[2]}


def _arm_turn_to_plane(pts: np.ndarray, center, axis, z_cut: float, fwd: float) -> tuple[float, float, str]:
    """Giro mínimo (signo, grados, estado) del brazo hacia adelante para que su punto más bajo apoye en z_cut.
    estado: «ok», «no_sube» (girando hacia adelante no se despega del plano) o «no_llega» (ni con FLAT_ANGLE_MAX)."""
    best = None
    for sgn in (1.0, -1.0):
        probe = _rotate_pts(pts, center, axis, sgn * 10.0)
        low = pts[:, 2] < pts[:, 2].min() + 1.0
        moved = (probe[low, 1] - pts[low, 1]).mean() * fwd
        rises = probe[:, 2].min() - pts[:, 2].min()
        if rises > 0 and moved > 0:
            best = sgn
    if best is None:
        return 1.0, 0.0, "no_sube"
    lo, hi = 0.0, FLAT_ANGLE_MAX
    if _rotate_pts(pts, center, axis, best * hi)[:, 2].min() < z_cut:
        return best, hi, "no_llega"
    for _ in range(40):
        mid = (lo + hi) / 2
        if _rotate_pts(pts, center, axis, best * mid)[:, 2].min() < z_cut:
            lo = mid
        else:
            hi = mid
    return best, hi, "ok"


def _forward_sign(res: BuildResult, pieces: dict) -> float:
    legs = [m for n, m in pieces.items() if n != "torso" and n != HEAD_NAME
            and n.rsplit("_", 1)[0] in {pn for ln, (_l, _c, _a, (a0, a1)) in res.motion.items() if a1 > a0
                                        for pn in _limb_piece_names(res, ln)}]
    if not legs:
        return 1.0
    ly = np.mean([(m.bounding_box()[1] + m.bounding_box()[4]) / 2 for m in legs])
    ty = (pieces["torso"].bounding_box()[1] + pieces["torso"].bounding_box()[4]) / 2
    return 1.0 if ly >= ty else -1.0


def flat_click_advice(res: BuildResult, depth: float = 1.0) -> dict:
    """Brazos con clic que no apoyan en el plano del corte «sin soportes»: {miembro: {...}}. El clic fija la pose del brazo\n    (la punta cae en una muesca solo cada 360/n°), así que no se lo puede girar después sin fundir la punta con el disco:\n    hay que generar la figura con `DetentSpec.print_angle` igual al ángulo en que el brazo apoya. Devuelve el ángulo\n    actual, el de apoyo, cuánto cuelga por debajo del plano (mm) y el estado de `_arm_turn_to_plane`."""
    out: dict = {}
    z_cut = float(res.pieces["torso"].bounding_box()[2]) + float(depth)
    fwd = _forward_sign(res, res.pieces)
    for n, dl in res.detent.items():
        if n not in res.motion or res.motion[n][3][1] > res.motion[n][3][0]:
            continue
        _l, center, axis, _r = res.motion[n]
        pts = np.vstack([S.to_trimesh(res.pieces[f"{pn}_izq"]).vertices for pn in _limb_piece_names(res, n)])
        low = float(pts[:, 2].min())
        if low >= z_cut - 1e-6:
            continue
        sgn, theta, status = _arm_turn_to_plane(pts, center, axis, z_cut, fwd)
        out[n] = {"angulo_actual": float(dl.print_angle), "cuelga_mm": round(z_cut - low, 2), "estado": status,
                  "angulo_apoyo": round(float(dl.print_angle + sgn * theta), 1) if status == "ok" else None}
    return out


def flat_for_print(res: BuildResult, depth: float = 1.0, p: Params | None = None) -> tuple[dict, dict]:
    """Prepara la figura sentada para imprimirla sin soportes: devuelve (piezas nuevas, informe).

    0. Las piernas con rodilla se estiran (flexión 0°): muslo y pantorrilla quedan alineados y horizontales.
    1. Corta con un plano horizontal a `depth` mm sobre el punto más bajo del torso (la cola): las piernas y la cola
       quedan con una base plana. Se corta todo salvo los brazos y la cabeza.
    2. Los brazos (sin tocarlos) quedan entonces por debajo del plano: giran hacia adelante alrededor del pasador del
       hombro, el mínimo necesario para que su punto más bajo apoye justo en ese plano (un brazo que cuelga sube al
       girar). Con articulación de codo gira la cadena entera.
    3. El hueco del hombro en el torso se hizo para un brazo que no gira: se talla además con el barrido del brazo
       entre 0° y el giro (ensanchado la holgura), para que gire libre sobre el pasador.
    El conjunto queda apoyado en el plano del corte; la exportación lo baja a z = 0.
    """
    p = Params() if p is None else p
    depth = float(depth)
    pieces = dict(res.pieces)
    info: dict = {"corte_sobre_cola_mm": depth, "avisos": []}
    # 0. piernas estiradas: con rodilla, la pantorrilla pasa de la flexión de la pose impresa a 0° y queda en línea con el
    #    muslo (ambos horizontales con la cadera sentada), para que las dos piezas apoyen sobre el plano del corte
    stretched: dict = {}
    for jn, fl in straight_leg_flex(res).items():
        r = res.report["joints"][jn]
        jb = res.joints[jn]
        _l, hc, hax, _rng = res.motion[r["limb"]]
        th = float(r.get("print_hip", p.sit_angle))
        for nm, mf in zip(r["piezas"], (jb.proximal, jb.rotate_distal(fl))):
            if th:
                mf = S.rotate_about_axis(mf, hc, hax, th)
            pieces[f"{nm}_izq"] = mf
            pieces[f"{nm}_der"] = S.mirror_x(mf)
        stretched[jn] = {"flexion_grados": fl, "flexion_impresa_grados": r["print_flex"]}
    info["rodillas_estiradas"] = stretched
    arm_limbs = [n for n, (_l, _c, _a, (a0, a1)) in res.motion.items() if n != HEAD_NAME and a1 <= a0 and n not in res.detent]
    info["clic_brazos"] = flat_click_advice(res, depth)
    for n, a in info["clic_brazos"].items():
        if a["angulo_apoyo"] is None:
            info["avisos"].append(f"{n}: el brazo con clic cuelga {a['cuelga_mm']:.1f} mm bajo el plano y no llega a apoyar girando hacia adelante; "
                                  "queda así (el clic fija su pose)")
        else:
            info["avisos"].append(f"{n}: el brazo con clic cuelga {a['cuelga_mm']:.1f} mm bajo el plano (el clic fija su pose, no se lo gira): "
                                  f"generá la figura con «Clic: ángulo del brazo al imprimir» = {a['angulo_apoyo']:g}° (ahora {a['angulo_actual']:g}°) "
                                  "para que apoye")
    arm_pieces = {f"{pn}{side}" for n in arm_limbs for pn in _limb_piece_names(res, n) for side in ("_izq", "_der")}
    protected = arm_pieces | {f"{pn}{side}" for n in res.detent if res.motion[n][3][1] <= res.motion[n][3][0]
                              for pn in _limb_piece_names(res, n) for side in ("_izq", "_der")}
    z_cut = min(pieces[n].bounding_box()[2] for n in ("torso", PELVIS_NAME) if n in pieces) + depth
    info["altura_corte_z"] = round(float(z_cut), 3)
    # 1. aplanar piernas y cola (todo menos brazos y cabeza)
    above = S.Manifold.cube((1e4, 1e4, 1e4)).translate((-5e3, -5e3, z_cut))
    removed: dict = {}
    for name, mf in list(pieces.items()):
        if name in protected or name == HEAD_NAME or mf.bounding_box()[2] >= z_cut:
            continue
        before_parts = len(mf.decompose())
        cut = S.intersection(mf, above)
        removed[name] = round(float(mf.volume() - cut.volume()), 1)
        if len(cut.decompose()) > before_parts:
            info["avisos"].append(f"{name}: el corte lo parte en más piezas sueltas; probá con menos corte")
        pieces[name] = cut
    info["volumen_quitado_mm3"] = removed
    # 2. brazos hacia adelante hasta apoyar en el plano
    legs = [m for n, m in pieces.items() if n not in arm_pieces and n not in ("torso", PELVIS_NAME, HEAD_NAME)]
    fwd = 1.0
    if legs:
        ly = np.mean([(m.bounding_box()[1] + m.bounding_box()[4]) / 2 for m in legs])
        ty = (pieces["torso"].bounding_box()[1] + pieces["torso"].bounding_box()[4]) / 2
        fwd = 1.0 if ly >= ty else -1.0          # hacia dónde apuntan las piernas sentadas = adelante
    angles: dict = {}
    for limb in arm_limbs:
        _l, center, axis, _ = res.motion[limb]
        left = {pn: res.pieces[f"{pn}_izq"] for pn in _limb_piece_names(res, limb)}
        pts = np.vstack([S.to_trimesh(m).vertices for m in left.values()])
        if pts[:, 2].min() >= z_cut - 1e-6:
            angles[limb] = 0.0
            continue
        sgn, theta, status = _arm_turn_to_plane(pts, center, axis, z_cut, fwd)
        if status == "no_sube":
            info["avisos"].append(f"{limb}: el brazo no sube al girar hacia adelante; no se gira")
            angles[limb] = 0.0
            continue
        if status == "no_llega":
            info["avisos"].append(f"{limb}: ni girando {FLAT_ANGLE_MAX:.0f}° llega al plano del corte; queda en el aire")
        angles[limb] = round(float(theta), 2)
        for pn, mf in left.items():
            rot = S.rotate_about_axis(mf, center, axis, sgn * theta)
            pieces[f"{pn}_izq"] = rot
            pieces[f"{pn}_der"] = S.mirror_x(rot)
        t_before = pieces["torso"].volume()
        pieces["torso"], used = _carve_torso_for_arm(pieces["torso"], list(left.values()), center, axis, sgn * theta, p)
        info.setdefault("tallado_torso", {})[limb] = used
        removed["torso"] = round(removed.get("torso", 0.0) + float(t_before - pieces["torso"].volume()), 1)
    info["giro_brazos_grados"] = angles
    info["volumen_quitado_mm3"] = removed
    # 3. los brazos girados no pueden chocar con nada
    gaps: dict = {}
    for name in sorted(n for n in arm_pieces if n.endswith("_izq")):
        for other, mo in pieces.items():
            if other in arm_pieces or other == HEAD_NAME:
                continue
            if S.intersection(pieces[name], mo).volume() > 1e-3:
                info["avisos"].append(f"{name} choca con {other} tras girar: usá menos corte")
            gaps[f"{name}~{other}"] = round(float(pieces[name].min_gap(mo, 5.0)), 3)
    info["holgura_brazos_mm"] = gaps
    changed = [n for n in pieces if pieces[n] is not res.pieces.get(n)]
    bad = {n: _nonmanifold_edges(pieces[n]) for n in changed}
    info["bordes_no_variedad"] = {n: k for n, k in bad.items() if k}
    for n, k in info["bordes_no_variedad"].items():
        info["avisos"].append(f"{n}: {k} aristas no variedad tras el corte")
    return pieces, info


# ----------------------------------------------------------------- exportación
def print_orientation(res: BuildResult, max_angle: float = 45.0, extra_tilts=(0.0,), mode: str = "pose"):
    """Orientación de impresión del conjunto armado (§8.7). Todas las piezas comparten la misma
    transformación para seguir en un único sistema de coordenadas.

    `mode="pose"` (por defecto): la figura tal como se ve en la vista de la pose de cadera (sentada, cabeza arriba),
    solo apoyada en la cama. `mode="auto"`: la orientación de menor voladizo (`best_orientation`), que puede dar
    vuelta la figura."""
    from .orient import best_orientation, pose_orientation
    asm = trimesh.util.concatenate([S.to_trimesh(mf) for mf in res.pieces.values()])
    if mode == "auto":
        return best_orientation(asm, max_angle, extra_tilts)
    return pose_orientation(asm, max_angle)


def export(res: BuildResult, outdir: str, name: str = "figura", orient: bool | str = False,
           max_angle: float = 45.0, extra_tilts=(0.0,), flat: float | None = None, params: Params | None = None) -> dict:
    """Escribe el ensamblado y un STL por parte. Con `orient=True` apoya todo el conjunto en la cama en la pose de
    la vista previa (sentada, cabeza arriba); con `orient="auto"` lo gira a la orientación de menor voladizo.
    En ambos casos informa la orientación. Con `flat` (altura del corte en mm sobre la cola; `Params.flat_depth`) antes se aplana la base y se
    giran los brazos para imprimir sin soportes (`flat_for_print`); el informe va en `info["sin_soportes"]`."""
    os.makedirs(os.path.join(outdir, "piezas"), exist_ok=True)
    T = None
    info: dict = {}
    if flat is not None:
        pieces, rep = flat_for_print(res, flat, params)
        res = BuildResult(pieces=pieces, report=res.report, motion=res.motion, joints=res.joints, detent=res.detent,
                          detent_ref=res.detent_ref, waist=res.waist)
        info["sin_soportes"] = rep
    if orient:
        o = print_orientation(res, max_angle, extra_tilts, mode="auto" if orient == "auto" else "pose")
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
