"""Pipeline completo: STL normalizado -> piezas articuladas (plan §8.3–§8.10)."""
from __future__ import annotations

import dataclasses

import logging
import os
from dataclasses import dataclass, field

import numpy as np
import trimesh

from . import solids as S
from . import detent as DT
from . import joint_types as JT
from .hand import (HAND_MIN_SHAFT_D, HAND_NAME, HandError, HandSpec, WristFrame, WristSplit, apply_hand, attach_hand,
                   load_standard_hand)
from .head import HEAD_NAME, MIN_BASE_IOU, HeadSpec, below_neck, build_head, neck_plane, split_head
from .joints import JOINT_KIND_BY_LIMB, JointBuild, JointSpec, build_joint, flex_range, hinge_for, print_flex
from .joint_chain import check_names as check_chain_names, group_by_limb, order_along
from .assembly import AssemblyError, PartChoice, PlacedPart, hip_notes, place_part, swap_pelvis
from .limbs import LimbSpec, ContactFace, check_limb_plane, limb_prism, pin_margin, separate_limb
from .params import Params
from .planes import Plane
from .trims import Trim, apply_trims
from .waist import PELVIS_NAME, WAIST_NAME, WaistBuild, WaistSpec, build_waist

log = logging.getLogger(__name__)

DEFAULT_SWEEP = {"arm": (0.0, 0.0), "leg": None}  # leg: (0, sit_angle + margen)
MIN_HAND_COVER = 0.98       # fracción de la mano estándar que tiene que quedar dentro del corte del brazo


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
    hand: dict | None = None                     # mano articulada: {"limb", "piece0" (mano de pie, sin girar), "center", "axis" (de la pose impresa), "pin"}
    waist: WaistBuild | None = None              # cintura: `pieces["torso"]` es el pecho (arriba) y `pieces["pelvis"]` la pelvis
    joint_specs: list = field(default_factory=list)   # todas las articulaciones usadas (las del modelo y las que traen las piezas de catálogo)
    torso_half: S.Manifold | None = None         # mitad +X del torso ya tallada, antes de espejar (la usa el lado derecho de una figura asimétrica)
    right: "BuildResult | None" = None           # figura asimétrica: construcción completa del lado −X, hecha sobre el cuerpo espejado (marco espejado)
    right_limbs: list = field(default_factory=list)   # miembros del lado derecho llevados al marco espejado (los de `right`)
    placed: dict = field(default_factory=dict)   # nombre -> assembly.PlacedPart de las piezas de catálogo (con su `T`); lo usa el color (T36)
    mounts: dict = field(default_factory=dict)   # nombre -> mount.MountBuild: monturas de armas ya unidas a su pieza (M05)
    host_bare: dict = field(default_factory=dict)   # pieza huésped -> la misma pieza SIN las monturas (M06: el plano de «sin soportes» se mide sin ellas)


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
    """Pasador de hongo del miembro: el de la línea, con encima las medidas propias del tipo `mushroom` si las hay."""
    from .joint_types.resolve import limb_overrides, limb_type_id, replace_params
    base = p.hip_pin if spec.kind == "leg" else p.shoulder_pin
    if limb_type_id(spec, p) == "mushroom":
        over = limb_overrides(spec, p, "mushroom")
        if over:
            return replace_params(base, over, spec.name)
    return base


def _ball_spec(spec: LimbSpec, p: Params):
    """Medidas de la rótula del miembro: las del tipo (`BallSpec()`) con encima las del perfil de línea y las propias del miembro."""
    from .joint_types.resolve import limb_overrides, replace_params
    base = JT.get("ball").default_params(p, spec.name)
    over = limb_overrides(spec, p, "ball")
    return replace_params(base, over, spec.name) if over else base


_rotation_axis = JT.rotation_axis       # el eje de giro del pasador vive en el contrato de los tipos (`joint_types.base`)


def build(body: S.Manifold, limbs: list[LimbSpec], p: Params, sym: Plane | None = None,
          sit: bool = True, progress=None, trims: list[Trim] | None = None,
          head: HeadSpec | None = None, joints: list[JointSpec] | None = None,
          waist: WaistSpec | None = None, hand: HandSpec | None = None,
          parts: dict[str, PartChoice] | None = None, host_faces: dict | None = None,
          limbs_right: list[LimbSpec] | None = None, mounts: list | None = None) -> BuildResult:
    """Construye las piezas. `body` debe estar normalizado y con el plano de simetría en x = 0.

    `hand`: mano estándar (STL) que reemplaza a la del modelo; se pega en el lado +X antes de cortar los miembros, así
    viaja con el brazo (o con el antebrazo, si hay codo) y el otro lado sale por espejo.

    `limbs_right`: figura asimétrica. Define los miembros del lado derecho (−X) con sus propias siluetas (deben ser los\n    mismos nombres que `limbs`, que pasan a ser los del lado izquierdo, +X). El lado derecho se construye completo sobre el\n    cuerpo espejado, con sus miembros espejados al lado de trabajo, y sus piezas y su mitad de torso vuelven a espejarse al lado\n    −X: así cada lado se corta, se talla y se valida con sus propios cortes. La cabeza, la mano estándar, las articulaciones y los\n    recortes se toman iguales para los dos lados. Con `None` (lo habitual) el resultado es idéntico al de siempre.\n\n    `mounts`: lista de `(mount.MountSpec, arma Manifold escalada)`; se suman al final (M05), sobre la pelvis o el torso ya terminados.

    `parts`: piezas de catálogo (`assembly.PartChoice`) por nombre de miembro (`LimbSpec.name`). En vez de cortar ese
    miembro del modelo, se lleva la pieza al puerto que el modelo tiene en ese lugar (sin escala); el resto del flujo
    (pasador, clic, barrido, tallado del torso, articulaciones) es el de siempre. Con `None` el resultado es idéntico."""
    say = progress or (lambda *_: None)
    trims = trims or []
    joints = list(joints or [])
    right_res: BuildResult | None = None
    right_limbs: list[LimbSpec] = []
    if limbs_right is not None:
        right_limbs, right_res = _build_right_side(body, limbs, limbs_right, p, sym, sit, say, trims, head, joints, hand, parts,
                                                   host_faces)
    by_limb: dict[str, JointSpec] = {}                    # la primera articulación de cada miembro (la que sale del torso)
    chains = group_by_limb(joints, (l.name for l in limbs))     # {miembro: [articulaciones]}; se ordenan al armar (`_order_chain`)
    for lname, chain in chains.items():
        by_limb[lname] = chain[0]
    parts = dict(parts or {})
    parts_src = set(parts)
    host_faces = dict(host_faces or {})
    pelvis_part = parts.pop("pelvis", None)          # pelvis de catálogo: cambia solo la pelvis del modelo (su torso queda)
    pelvis_pp = None
    if pelvis_part is not None:
        if host_faces:
            raise ValueError("«pelvis»: con torso de catálogo la pelvis va con él (torso_sup + pelvis)")
        say("Cambiando la pelvis…")
        try:
            body, pelvis_pp = swap_pelvis(body, limbs, waist, pelvis_part, p, p.ref_height)
        except AssemblyError as exc:
            raise ValueError(f"«pelvis»: {exc}") from exc
    for lname in host_faces:
        if lname not in parts:
            raise ValueError(f"«{lname}»: hay una toma sintetizada pero no una pieza de catálogo para ese lugar")
    hand_source = _catalog_hand(parts.pop("mano")) if "mano" in parts else None      # (hand_local, meta) de la pieza `mano`
    head_part = parts.pop("cabeza", None)                                           # pieza `cabeza` (puerto `cuello`)
    if head_part is not None and head is None:
        raise ValueError(f"la cabeza de catálogo «{head_part.meta.ref}» necesita el plano del cuello del modelo (HeadSpec)")
    for lname in parts:
        if lname not in {l.name for l in limbs}:
            raise ValueError(f"la pieza de catálogo «{parts[lname].meta.ref}» apunta al miembro «{lname}», que no existe")
    for spec in limbs:
        check_limb_plane(spec.plane)       # falla rápido: un plano de canto da piezas inservibles
    half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)          # lado de trabajo (+X)
    head_build = None
    if head is not None:
        say("Separando la cabeza…")
        split = split_head(body, head, p, head_part)
        head_build = build_head(split, head, p, trims)
        half = split.body_half                                # los miembros se cortan del cuerpo sin cabeza
    hand_build = None
    hand_cover: dict[str, float] = {}
    # Brazos de catálogo con puerto `muneca`: la mano va en ESE puerto (no en el plano de muñeca del modelo) y se pega
    # al brazo ya puesto, dentro del bucle de miembros.
    wrist_arms = {s.name for s in limbs if s.kind == "arm" and s.name in parts and "muneca" in parts[s.name].meta.puertos}
    hand_spec = hand if hand is not None else (HandSpec(path=hand_source[1].get("archivo", "")) if hand_source else None)
    host_hand = None if wrist_arms else hand_spec
    if host_hand is not None and not host_hand.points:
        raise ValueError("la mano de catálogo necesita el plano de muñeca del modelo (HandSpec.points) cuando el brazo no es de catálogo")
    part_notes: dict[str, list] = {}
    if host_hand is not None:
        say("Pegando la mano estándar…")
        hand_build = apply_hand(half, host_hand, p.ref_height, p, source=hand_source)
        half = hand_build.body                                # la mano nueva viaja con el brazo que se corta abajo
    half_nocav = half                                         # con «giro» la cavidad del pasador se talla aparte (ver abajo)
    pocket_in_body = 0.0
    if hand_build is not None and hand_build.pocket is not None:
        pocket_in_body = float(S.intersection(half_nocav, hand_build.pocket).volume())
        half = S.difference(half_nocav, hand_build.pocket)
    torso_half = half
    cutters: list[S.Manifold] = []        # lo que se resta del torso: se repite sobre el lado −X si el torso no se espeja
    built: dict[str, dict] = {}
    detents: dict[str, DT.DetentLimb] = {}
    placed: dict[str, PlacedPart] = {}
    if head_build is not None and split.placed is not None:
        placed["cabeza"] = split.placed
    if pelvis_pp is not None:
        placed["pelvis"] = pelvis_pp
    for spec in limbs:
        say(f"Separando {spec.name}…")
        if spec.name in parts:
            # El lugar lo define el miembro del modelo (su corte y su cara); la pieza de catálogo ocupa ese lugar.
            if spec.name in host_faces:
                host_face = host_faces[spec.name]               # huésped sintetizado: la toma sale del puerto del torso
            else:
                _own, host_face = separate_limb(half_nocav, spec, p.burr)
            try:
                pp = place_part(parts[spec.name], spec.kind, host_face, p, p.ref_height, host_mesh=half_nocav)
            except AssemblyError as exc:
                raise ValueError(f"«{spec.name}»: {exc}") from exc
            placed[spec.name] = pp
            for jd in pp.choice.meta.articulaciones or []:        # articulaciones internas de la pieza (rodilla, codo)
                j = JointSpec.from_dict(jd).transformed(pp.T, spec.name)
                if spec.name in by_limb:
                    raise ValueError(f"«{spec.name}»: la pieza «{pp.choice.meta.ref}» trae la articulación «{j.name}» y el "
                                     f"modelo ya define «{by_limb[spec.name].name}» (por ahora una sola por miembro)")
                if j.kind != JOINT_KIND_BY_LIMB.get(spec.kind):
                    raise ValueError(f"«{spec.name}»: la articulación «{j.name}» de la pieza es «{j.kind}» y el miembro es «{spec.kind}»")
                by_limb[spec.name] = j
                joints.append(j)
            if spec.name not in host_faces:
                own_cut = S.intersection(half_nocav, limb_prism(spec))      # el miembro propio sale del torso
                torso_half = S.difference(torso_half, own_cut)
                cutters.append(own_cut)
            limb_raw, face = pp.mesh, pp.face
        else:
            limb_raw, face = separate_limb(half_nocav, spec, p.burr)
        limb_raw = apply_trims(limb_raw, trims, spec.name)
        if spec.name in wrist_arms and hand_spec is None:
            part_notes.setdefault(spec.name, []).append(
                "el brazo de catálogo no trae mano y no se pidió ninguna (--mano o pieza «mano»): queda sin mano")
        elif spec.name in wrist_arms:
            if hand_build is not None:
                raise ValueError("la mano estándar solo puede ir en un brazo de catálogo")
            say(f"Mano estándar en {spec.name}…")
            wp = parts[spec.name].meta.puertos["muneca"].transformed(placed[spec.name].T)
            wf = WristFrame(wp.frame.center.copy(), wp.frame.axis.copy(), wp.frame.ref.copy())
            src = hand_source or load_standard_hand(hand_spec.path)
            face_r = float(wp.face.radius_eq) if wp.face is not None else 0.0
            wsplit = WristSplit(hand=S.Manifold(), rest=limb_raw, frame=wf,
                               face_area=float(np.pi * face_r ** 2), face_radius=face_r, hand_volume=0.0,
                               rest_volume=float(limb_raw.volume()))
            hand_build = attach_hand(wsplit, hand_spec, src[0], src[1], p.ref_height, p)
            limb_raw = hand_build.body                       # «solape»: brazo + mano soldados; «giro»: el brazo solo
            if hand_build.pocket is not None:
                pocket_in_body = float(S.intersection(limb_raw, hand_build.pocket).volume())
        if hand_build is not None and spec.kind != "leg":
            if hand_build.pocket is None:
                hand_cover[spec.name] = _hand_coverage(limb_raw, hand_build.hand)
            else:           # «giro»: la mano es otra pieza; lo que importa es que el brazo contenga la cavidad del pasador
                hand_cover[spec.name] = (float(S.intersection(limb_raw, hand_build.pocket).volume()) / pocket_in_body
                                         if pocket_in_body > 0 else 0.0)
                limb_raw = S.difference(limb_raw, hand_build.pocket)
        axis = _rotation_axis(face)
        if JT.resolve.limb_type_id(spec, p) == "ball":
            # Rótula: no hay un eje de giro. El zócalo (hija dilatada + cono del cuello) se resta del torso y el movimiento es un
            # `ConeMotion`; la pose impresa es la declarada por el movimiento (de pie: la rótula no llega a la pose sentada).
            bjp = _ball_spec(spec, p)
            jr = JT.get("ball").build(JT.JointSite(slot=spec.name, side="izq", child=limb_raw, face=face, params=p, jp=bjp,
                                                   print_hint={"print_tilt": (0.0, 0.0, 0.0)}, say=say))
            for c in jr.cutters["parent"]:
                torso_half = S.difference(torso_half, c)
                cutters.append(c)
            built[spec.name] = {"limb": jr.pieces["child"], "face": face, "axis": axis, "spec": spec,
                                "angles": [0.0, float(bjp.half_angle)], "print_angle": 0.0, "pin_margin": None,
                                "motion": jr.motion, "ball": jr}
            continue
        dspec = DT.detent_spec_for(spec, p)
        if dspec is not None:
            th = _print_angle(spec, p, dspec, sit)
            rng = _detent_sweep(spec, p)
            jr = JT.get("detent").build(JT.JointSite(slot=spec.name, side="izq", child=limb_raw, face=face, params=p, jp=dspec,
                                                     flex=rng, print_hint={"print_angle": th},
                                                     opts={"pin": _pin_spec(spec, p)}, say=say))
            dl = jr.native
            detents[spec.name] = dl
            for c in jr.cutters["parent"]:
                torso_half = S.difference(torso_half, c)
                cutters.append(c)
            built[spec.name] = {"limb": dl.limb, "face": face, "axis": axis, "spec": spec,
                                "angles": jr.report["angles"], "print_angle": th, "pin_margin": jr.report["pin_margin"]}
            continue
        jt = JT.get("mushroom")                  # el pasador de fricción lo construye el tipo registrado (plan de plugins, Fase 0)
        jr = jt.build(JT.JointSite(slot=spec.name, side="izq", child=limb_raw, face=face, params=p, jp=_pin_spec(spec, p),
                                   flex=_sweep_range(spec, p), say=say))
        limb = jr.pieces["child"]
        cutters.extend(jr.cutters["parent"])
        for c in jr.cutters["parent"]:
            torso_half = S.difference(torso_half, c)
        built[spec.name] = {"limb": limb, "face": face, "axis": axis, "spec": spec,
                            "angles": jr.report["angles"], "pin_margin": jr.report["pin_margin"]}
    joint_builds: dict[str, list[JointBuild]] = {}        # por miembro, del torso a la punta
    for lname in list(by_limb):
        chain = _order_chain(chains.get(lname) or [by_limb[lname]], built[lname])
        chains[lname] = chain
        by_limb[lname] = chain[0]
        child, toward, jbs = built[lname]["limb"], built[lname]["face"].center, []
        for k, j in enumerate(chain):
            tid = JT.resolve.joint_type_id(j, p)
            if k and tid != "hinge":
                raise ValueError(f"«{lname}»: «{j.name}» es de tipo «{tid}»; en una cadena solo se arman bisagras por ahora")
            jr = JT.get(tid).build(JT.JointSite(slot=j.name, side="izq", child=child, face=built[lname]["face"],
                                                params=p, jp=j, say=say))
            if k:
                jr.native.warnings.append(f"cadena: «{j.name}» se arma sobre la pieza distal de «{chain[k - 1].name}» a flexión 0°")
            jbs.append(jr.native)
            child = jr.native.distal
        joint_builds[lname] = jbs
    carved: dict[int, float] = {}                        # por `id(JointBuild)`: lo que su barrido le sacó al torso
    for lname, jb in ((n, jb) for n, jbs in joint_builds.items() for jb in jbs):
        if built[lname]["spec"].kind == "leg" or jb.sweep is None:
            continue          # la pierna ya queda libre del torso con el barrido de cadera (la validación de cadena lo comprueba)
        # Brazo: el miembro cuelga pegado al torso y solo se talló estirado; el antebrazo flexionado entra en el torso.
        # Se talla también el barrido de la flexión (el mismo ensanchado que talla el brazo proximal).
        before = torso_half.volume()
        torso_half = S.difference(torso_half, jb.sweep)
        cutters.append(jb.sweep)
        carved[id(jb)] = before - torso_half.volume()
    if head_build is not None:
        say("Cavidad del cuello…")
        torso_half = S.difference(torso_half, head_build.pocket)
        cutters.append(head_build.pocket)
    torso_half = apply_trims(torso_half, trims, "torso")
    torso_half = S.drop_small_components(torso_half, 0.02)

    say("Pose sentada y espejado…")
    pieces: dict[str, S.Manifold] = {}
    res_motion: dict = {}
    report: dict = {"limbs": {}}
    res_joints: dict[str, JointBuild] = {}
    carrier = None
    if hand_build is not None and hand_build.pocket is not None and hand_cover:
        carrier = max(hand_cover, key=hand_cover.get)
    hand_res = None
    for name, b in built.items():
        limb = b["limb"]
        hip_sit = sit and b["spec"].kind == "leg"
        th = b.get("print_angle", p.sit_angle if hip_sit else 0.0)       # giro de la pose impresa alrededor del pasador
        res_motion[name] = b.get("motion") or JT.RevoluteMotion(b["limb"], b["face"].center, b["axis"], (b["angles"][0], b["angles"][-1]))
        jbs = joint_builds.get(name)
        if jbs is None:
            limb = _print_posed(b, limb, th)
            pieces[f"{name}_izq"] = limb
            pieces[f"{name}_der"] = S.mirror_x(limb)
        else:
            chain = chains[name]
            names = check_chain_names(chain)                 # [proximal, …, distal]: la distal de una es la proximal de la siguiente
            flexes = [print_flex(j, p) for j in chain]
            posed = _chain_pieces([jb.proximal for jb in jbs] + [jbs[-1].distal],
                                  jbs, flexes)
            for nm, mf in zip(names, posed):
                mf = _print_posed(b, mf, th)
                pieces[f"{nm}_izq"] = mf
                pieces[f"{nm}_der"] = S.mirror_x(mf)
            for k, (j, jb) in enumerate(zip(chain, jbs)):
                pn, dn = j.names()
                res_joints[j.name] = jb
                report.setdefault("joints", {})[j.name] = {
                    "center": jb.center.tolist(), "axis": jb.axis.tolist(), "flex": list(jb.flex),
                    "shoulder_h": round(jb.shoulder_h, 3), "radius": round(jb.radius, 3), "avisos": jb.warnings,
                    "piezas": [pn, dn], "limb": name, "print_flex": flexes[k], "print_hip": float(th)}
                if len(chain) > 1:
                    report["joints"][j.name]["cadena"] = {"indice": k, "previa": chain[k - 1].name if k else None}
                if id(jb) in carved:
                    report["joints"][j.name]["torso_tallado_mm3"] = round(2 * carved[id(jb)], 3)     # ambos lados (espejo)
        if carrier == name:
            hand_res = _place_hand_piece(hand_build, jbs or [], chains.get(name, []), b, th, p, pieces)
        report["limbs"][name] = {
            "pin_center": b["face"].center.tolist(), "pin_dir": b["face"].outward.tolist(),
            "contact_area": b["face"].area, "sweep": [b["angles"][0], b["angles"][-1]],
        }
        if b["pin_margin"] is not None:
            report["limbs"][name]["pin_margin_mm"] = round(float(b["pin_margin"]), 3)
        if "ball" in b:
            report["limbs"][name]["mecanismo"] = "rotula"
            report["limbs"][name]["rotula"] = dict(b["ball"].report)
            mm = pin_margin(b["face"], 2.0 * b["ball"].report["mouth_radius_mm"])
            report["limbs"][name]["rotula"]["boca_margen_mm"] = None if mm is None else round(float(mm), 3)
        if name in detents:
            dl = detents[name]
            report["limbs"][name]["mecanismo"] = "clic"
            report["limbs"][name]["pin_shift_mm"] = dl.spec.disc_h          # el hongo queda `disc_h` detrás del disco
            report["limbs"][name]["disc_margin_mm"] = None if (m := pin_margin(b["face"], dl.spec.disc_d)) is None else round(float(m), 3)
            report.setdefault("detent", {})[name] = DT.summary(dl)
    torso_other = None
    if right_res is not None:
        # Figura asimétrica: el lado −X sale del lado derecho construido aparte (sus piezas y su mitad de torso, espejadas).
        if detents or right_res.detent:
            raise ValueError("el mecanismo de clic todavía no admite figuras con cortes asimétricos "
                             "(desactivá «Clic» en hombro y cadera, o volvé a cortes simétricos)")
        for nm, mf in right_res.pieces.items():
            if nm.endswith("_izq"):
                pieces[nm[:-4] + "_der"] = S.mirror_x(mf)
        torso_other = S.mirror_x(right_res.torso_half)
        report["torso"] = {"espejado": False, "asimetrica": True, "volumen_der_mm3": round(float(torso_half.volume()), 2),
                           "volumen_izq_mm3": round(float(right_res.torso_half.volume()), 2)}
        report["derecha"] = right_res.report
    elif not p.torso_mirror:
        say("Torso sin espejar: lado −X…")
        other, why = _other_side_half(body, p, head, host_hand, hand_source)
        if other is None:
            log.warning("Torso sin espejar: %s; se espeja el lado +X", why)
            report["torso"] = {"espejado": True, "aviso": why}
        else:
            for c in cutters:
                other = S.difference(other, c)
            other = S.drop_small_components(apply_trims(other, trims, "torso"), 0.02)
            torso_other = S.mirror_x(other)
            report["torso"] = {"espejado": False, "volumen_der_mm3": round(float(torso_half.volume()), 2),
                               "volumen_izq_mm3": round(float(other.volume()), 2)}
    pieces["torso"] = S.union(torso_half, S.mirror_x(torso_half) if torso_other is None else torso_other)
    if pelvis_pp is not None:
        legs = {n: b["face"] for n, b in built.items() if b["spec"].kind == "leg"}
        if hip_msgs := hip_notes(pelvis_pp, legs):
            part_notes.setdefault("pelvis", []).extend(hip_msgs)
    if placed:
        report["piezas"] = {name: pp.report() for name, pp in placed.items()}
        for name, notes in part_notes.items():
            report["piezas"][name]["avisos"] = notes
        for name in report["piezas"]:
            if name in by_limb and name in parts_src:
                report["piezas"][name]["articulaciones"] = [j.name for j in chains[name]]
    if hand_build is not None:
        best = max(hand_cover, key=hand_cover.get) if hand_cover else None
        report["hand"] = {**hand_build.info, "miembro": best, "cobertura": None if best is None else round(hand_cover[best], 4)}
        if hand_res is not None:
            report["hand"]["pieza"] = HAND_NAME
    if head_build is not None:
        pieces[HEAD_NAME] = head_build.head
        report["head"] = {"pin_center": head_build.face.center.tolist(), "pin_dir": head_build.face.outward.tolist(),
                          "contact_area": head_build.face.area}
        if split.replacement is not None:
            report["head"]["replacement"] = split.replacement
        if head_build.pin_margin is not None:
            report["head"]["pin_margin_mm"] = round(float(head_build.pin_margin), 3)
        res_motion[HEAD_NAME] = JT.RevoluteMotion(head_build.head, head_build.face.center, head_build.axis, (0.0, 90.0))   # giro libre sobre el cuello: se valida 0–90°
    # Manifold es perezoso: las booleanas devuelven al instante y el cómputo ocurre en la primera consulta de la malla.
    # Se fuerza acá (el resultado se necesita igual más abajo) para que «Reduciendo triángulos» mida solo la decimación.
    say("Evaluando las piezas…")
    for _m in pieces.values():
        _m.num_tri()
    # Las piezas llegan con ~100 veces más triángulos de los necesarios (brazo de 353 732 que en el STL son 6 000):
    # compactarlas acá abarata la decimación, la validación y la exportación (ver S.compact). Una misma malla puede estar
    # en `pieces` y en `res_motion`: se compacta una sola vez.
    say("Compactando las piezas…")
    _memo: dict = {}

    def _compact(m):
        if id(m) not in _memo:
            _memo[id(m)] = S.compact(m)
        return _memo[id(m)]

    pieces = {n: _compact(m) for n, m in pieces.items()}
    res_motion = {n: v.with_limb(_compact(v.limb)) for n, v in res_motion.items()}
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
            dl.info["protrusion_mm3"] = round(DT.protrusion_mm3(dl, body, per_side=not p.torso_mirror), 4)
            report["detent"][nm]["protrusion_mm3"] = dl.info["protrusion_mm3"]
            wall = DT.finger_wall_mm(pieces["torso"], dl)
            report["detent"][nm]["pared_sobre_lengueta_mm"] = None if wall is None else round(wall, 3)
    waist_build = None
    if waist is not None:
        say("Cintura…")
        waist_build = build_waist(pieces["torso"], waist, p, skin=body)
        pieces["torso"], pieces[PELVIS_NAME] = waist_build.upper, waist_build.lower
        report["waist"] = _waist_report(waist_build, built, chains, head_build is not None)
    mounts_built: dict = {}
    host_bare: dict = {}
    if mounts:
        say("Monturas de armas…")
        from .mount import MountError, apply_mounts

        pre_mounts = pieces
        try:
            pieces, mounts_built = apply_mounts(pieces, mounts, p.min_wall)
        except MountError as exc:
            raise ValueError(f"montura: {exc}") from exc
        host_bare = {mb.host: pre_mounts[mb.host] for mb in mounts_built.values()}
        report["monturas"] = {n: mb.info for n, mb in mounts_built.items()}
    return BuildResult(pieces=pieces, report=report, motion=res_motion, joints=res_joints, detent=detents, detent_ref=ref,
                       waist=waist_build, hand=hand_res, joint_specs=list(joints), torso_half=torso_half,
                       right=right_res, right_limbs=right_limbs, placed=placed, mounts=mounts_built,
                       host_bare=host_bare)


def check_asymmetric(limbs: list[LimbSpec], limbs_right: list[LimbSpec]) -> None:
    """Los miembros del lado derecho tienen que ser los mismos que los del izquierdo (mismos nombres y tipos)."""
    left = {l.name: l.kind for l in limbs}
    right = {l.name: l.kind for l in limbs_right}
    if left != right:
        raise ValueError("figura asimétrica: los miembros del lado derecho no coinciden con los del izquierdo "
                         f"(izquierdo: {', '.join(sorted(left)) or 'ninguno'}; derecho: {', '.join(sorted(right)) or 'ninguno'})")


def _build_right_side(body, limbs, limbs_right, p, sym, sit, say, trims, head, joints, hand, parts, host_faces):
    """Lado derecho (−X) de una figura asimétrica: la misma construcción de siempre sobre el cuerpo espejado, con los miembros del
    lado derecho espejados al lado de trabajo. Sin decimar ni cintura (eso se hace una sola vez sobre el torso completo)."""
    if parts or host_faces:
        raise ValueError("las piezas de catálogo todavía no admiten figuras con cortes asimétricos")
    check_asymmetric(limbs, limbs_right)
    mirrored = [l.mirrored() for l in limbs_right]
    say("Lado derecho (cortes propios)…")
    res = build(S.mirror_x(body), mirrored, dataclasses.replace(p, decimate=0.0), sym, sit,
                (lambda m: say(f"Lado derecho: {m}")), trims, head, joints, None, hand)
    return mirrored, res


def build_from_parts(parts: dict[str, PartChoice], p: Params, sit: bool = True, progress=None, trims: list[Trim] | None = None,
                     joints: list[JointSpec] | None = None, hand: HandSpec | None = None) -> BuildResult:
    """Figura armada solo con piezas de catálogo (Fase 6 del plan, gancho 1): el huésped se sintetiza con el torso
    (`torso`, o `torso_sup` + `pelvis`) y las demás piezas (`brazo`, `pierna`, `cabeza`, `mano`) se acoplan a sus puertos.
    No hace falta STL ni `Project`; el resto del flujo es el de `build`."""
    from .assembly import synthesize_host
    host = synthesize_host(parts, p)
    return build(host.body, host.limbs, p, None, sit, progress, trims, host.head, joints, host.waist, hand, host.parts,
                 host_faces=host.faces)


def _print_posed(b: dict, mf: S.Manifold, th: float) -> S.Manifold:
    """Una pieza del miembro en su pose impresa: con rótula, la que declara su movimiento (`Motion.print_pose`, hoy de pie = identidad);
    con un eje, el giro `th` alrededor del pasador. Sin giro devuelve la misma malla."""
    mo = b.get("motion")
    if mo is not None:
        T = np.asarray(mo.print_pose(), float)
        return mf if np.allclose(T, np.eye(4)) else JT.moved(mf, T)
    return S.rotate_about_axis(mf, b["face"].center, b["axis"], th) if th else mf


def _place_hand_piece(hb, jbs, joints, b: dict, th: float, p: Params, pieces: dict) -> dict:
    """Mano articulada: la pieza `mano` viaja con el miembro que la lleva (con articulaciones, con cada una a la flexión de la
    pose impresa, de la punta al torso; luego con el giro de la pose impresa del miembro). Devuelve lo que necesita la validación y la pose."""
    center = np.asarray(hb.pin_origin, float)
    axis = -np.asarray(hb.frame.axis, float)                  # del lado de la mano hacia el brazo
    ends = np.vstack([center, center + axis])
    mano = hb.hand
    for jb, joint in reversed(list(zip(jbs, joints))):
        flex = print_flex(joint, p)
        mano = jb.rotate_distal(flex, mano)
        ends = _rotate_pts(ends, jb.center, jb.axis, jb.raw(flex))
    if th:
        mano = S.rotate_about_axis(mano, b["face"].center, b["axis"], th)
        ends = _rotate_pts(ends, b["face"].center, b["axis"], th)
    pieces[f"{HAND_NAME}_izq"] = mano
    pieces[f"{HAND_NAME}_der"] = S.mirror_x(mano)
    return {"limb": b["spec"].name, "piece0": hb.hand, "center": ends[0], "axis": (ends[1] - ends[0]), "pin": hb.pin}


def _order_chain(chain: list[JointSpec], b: dict) -> list[JointSpec]:
    """La cadena del miembro del torso a la punta: la dirección es del centro de la cara de contacto al centro del miembro."""
    if len(chain) < 2:
        return list(chain)
    lo, hi = np.asarray(b["limb"].bounding_box(), float).reshape(2, 3)
    root = np.asarray(b["face"].center, float)
    return order_along(chain, root, (lo + hi) / 2 - root)


def _chain_pieces(pieces: list, jbs: list, flexes: list) -> list:
    """Piezas de una cadena a sus flexiones: la pieza `k` (distal de la articulación `k − 1`) gira con la articulación
    `k − 1` y luego con cada una de las anteriores, cada una alrededor de su propio eje de pie (de la punta al torso)."""
    out = []
    for k, mf in enumerate(pieces):
        for i in range(min(k, len(jbs)) - 1, -1, -1):
            mf = jbs[i].rotate_distal(flexes[i], mf)
        out.append(mf)
    return out


def _other_side_half(body: S.Manifold, p: Params, head: HeadSpec | None, hand: HandSpec | None, hand_source=None):
    """Lado −X real del cuerpo llevado a +X (espejado), con la misma cabeza cortada y la misma mano que el lado de
    trabajo, para tallarlo con los mismos barridos y volver a espejarlo: el torso conserva su diseño asimétrico.
    Devuelve (mitad, None) o (None, motivo) si no se pudo armar y hay que espejar el lado +X."""
    half = S.mirror_x(body)
    if head is not None:
        half = below_neck(half, neck_plane(head), p)
    half = half.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    if half.is_empty():
        return None, "el lado −X del cuerpo quedó vacío"
    if hand is not None:
        try:
            half = apply_hand(half, hand, p.ref_height, source=hand_source).body
        except HandError as exc:
            return None, f"no se pudo pegar la mano estándar en el lado −X ({exc})"
    return half, None


def _catalog_hand(choice) -> tuple:
    """Pieza `mano` de catálogo -> (mano en el marco estándar, metadatos), igual que `hand.load_standard_hand`."""
    if choice.meta.tipo != "mano" or "muneca" not in choice.meta.puertos:
        raise ValueError(f"«{choice.meta.ref}» es una pieza «{choice.meta.tipo}» y ese lugar pide una «mano» con puerto «muneca»")
    return S.to_manifold(choice.mesh), {"altura_figura_mm": choice.meta.altura_figura_mm, "archivo": choice.meta.ref}


def _hand_coverage(limb: S.Manifold, hand: S.Manifold) -> float:
    """Fracción del volumen de la mano (ya puesta en el modelo) que quedó dentro de la pieza del miembro."""
    v = float(hand.volume())
    if v <= 0:
        return 0.0
    return float(S.intersection(limb, hand).volume()) / v


def _waist_report(wb: WaistBuild, built: dict, chains: dict, has_head: bool) -> dict:
    """Informe de la cintura: datos del pasador y qué piezas quedan arriba (giran con el pecho) y cuáles abajo."""
    upper, lower = ["torso"], [PELVIS_NAME]
    if has_head:
        upper.append(HEAD_NAME)
    for name, b in built.items():
        side = float((b["face"].center - wb.plane.origin) @ wb.plane.normal)    # hombro: arriba; cadera: abajo
        names = check_chain_names(chains[name]) if name in chains else [name]
        (upper if side > 0 else lower).extend(f"{x}{s}" for x in names for s in ("_izq", "_der"))
    return {**wb.info, "arriba": upper, "abajo": lower}


def body_ref(res: BuildResult) -> S.Manifold:
    """Torso entero contra el que se miden los miembros: con cintura, pecho y pelvis juntos (separados por la holgura)."""
    torso = res.pieces["torso"]
    return S.union(torso, res.pieces[PELVIS_NAME]) if PELVIS_NAME in res.pieces else torso

def body_base_z(pieces: dict) -> float:
    """Altura del punto más bajo del cuerpo (la cola): con cintura es la pelvis, no el pecho (`torso`), que queda arriba."""
    return float(min(pieces[n].bounding_box()[2] for n in ("torso", PELVIS_NAME) if n in pieces))


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


def _gap_search(clearance: float) -> float:
    """Radio de búsqueda de `min_gap` para proteger la holgura. El costo crece muy rápido con el radio (demo nativa, 5 miembros
    contra el torso: 5 mm = 2,16 s; 3 mm = 0,86 s; 2 mm = 0,53 s; 1 mm = 0,27 s) y las holguras reales rondan 0,5 mm: con
    1 mm salen los mismos valores. Más allá del radio `min_gap` devuelve el radio, que ya es «holgura de sobra»."""
    return max(1.0, 2.0 * clearance)


def _decimate_keeping_clearance(pieces: dict, p: Params, tol: float = 0.02) -> S.Manifold:
    """Decima el torso bajando la intensidad hasta que la holgura mínima con cada miembro
    no caiga más de `tol` mm respecto de la original."""
    torso = pieces["torso"]
    if not S.can_decimate():       # sin fast_simplification (Pyodide) `S.decimate` no hace nada: no medir holguras en vano
        return torso
    limbs = [m for n, m in pieces.items() if n != "torso"]
    sl = _gap_search(p.clearance)
    base = [m.min_gap(torso, sl) for m in limbs]
    for red in (p.decimate, 0.45, 0.3, 0.15):
        if red > p.decimate:
            continue
        cand = S.decimate(torso, red)
        if cand is torso:
            continue
        if all(m.min_gap(cand, sl) >= g - tol for m, g in zip(limbs, base)):
            return cand
    return torso


def _bbox_gap(a: S.Manifold, b: S.Manifold) -> float:
    """Distancia entre las cajas envolventes de dos piezas (0 si se tocan o se solapan): cota inferior de la distancia real."""
    ba, bb = a.bounding_box(), b.bounding_box()
    d = [max(ba[i] - bb[i + 3], bb[i] - ba[i + 3], 0.0) for i in range(3)]
    return float(sum(x * x for x in d) ** 0.5)


def cap_export_triangles(pieces: dict, max_tris: int, tol: float = 0.02, max_vol_change: float = 0.002,
                          clearance: float = 0.5, only=None) -> tuple[dict, dict]:
    """Limita los triángulos de cada pieza antes de escribir el STL (las booleanas, el barrido por marching cubes y las
    holguras Minkowski multiplican las caras: un STL de entrada de 0,5 MB puede salir en decenas de MB).

    Solo toca las piezas que superan `max_tris`: las decima (cuádrica, `S.decimate`) hasta ese tope y acepta el
    resultado solo si el volumen cambia menos de `max_vol_change` y la holgura mínima con cada otra pieza no baja más de
    `tol` mm; si no, prueba una reducción menor y, si ninguna cumple, deja la pieza como está. Devuelve
    (piezas, {pieza: [antes, después]}) con las piezas que superaban el tope ([n, n] = no se pudo reducir).
    `only` (opcional, conjunto de nombres): solo se decima esas piezas; las demás cuentan para la holgura pero no se tocan
    (la exportación ecuestre, E10, le pone un tope propio al caballo y a la silla)."""
    out = dict(pieces)
    rep: dict = {}
    if not max_tris or max_tris <= 0:
        return out, rep
    for name, mf in pieces.items():
        n0 = mf.num_tri()
        if n0 <= max_tris or (only is not None and name not in only):
            continue
        # Solo las piezas que pueden estar a menos del radio de búsqueda (caja envolvente) y con ese radio, no 5 mm contra
        # todas: el costo de `min_gap` crece muy rápido con el radio (figura real, 190–430 k tri: 296 s en nativo, 1545 s en el navegador).
        sl = _gap_search(clearance)
        others = [m for n, m in out.items() if n != name and _bbox_gap(mf, m) <= sl]
        base = [mf.min_gap(o, sl) for o in others]
        red = 1.0 - 0.95 * max_tris / n0                   # 5 % por debajo del tope: la decimación no clava el objetivo
        done = None
        for k in (1.0, 0.8, 0.6, 0.4):
            cand = S.decimate(mf, red * k, max_vol_change)
            if cand is mf:
                continue
            if all(cand.min_gap(o, sl) >= g - tol for o, g in zip(others, base)):
                done = cand
                break
        if done is not None:
            out[name] = done
            rep[name] = [n0, done.num_tri()]
        else:
            rep[name] = [n0, n0]
    return out, rep


# ------------------------------------------------------------------ validación
def _components_detail(mf: S.Manifold) -> str:
    """«: el mayor 4521 mm³; sueltos: 12 mm³ en (x, y, z) …» para ver QUÉ parte de la pieza quedó suelta (un sable, un faldón, una
    borla que la malla trae como objeto aparte, o un trozo que el corte dejó flotando)."""
    parts = sorted(mf.decompose(), key=lambda m: -m.volume())
    if len(parts) < 2:
        return ""
    def where(m):
        lo, hi = m.bounding_box()[:3], m.bounding_box()[3:]
        c = [(a + b) / 2 for a, b in zip(lo, hi)]
        return f"{m.volume():.1f} mm³ en ({c[0]:.1f}, {c[1]:.1f}, {c[2]:.1f})"
    loose = "; ".join(where(m) for m in parts[1:4]) + (f"; y {len(parts) - 4} más" if len(parts) > 4 else "")
    return f": el mayor tiene {parts[0].volume():.1f} mm³; sueltos: {loose}"


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
        rot = info.get("rotula")
        if rot is not None:
            out["avisos"].extend(rot.get("avisos", []))
            bm = rot.get("boca_margen_mm")
            if bm is not None and bm < 0:
                out["avisos_pasador"].append(f"{name}: el cono de la rótula sobresale {-bm:.2f} mm de la cara de contacto")
            elif bm is not None and bm < p.min_wall:
                out["avisos_pasador"].append(f"{name}: el cono de la rótula queda a {bm:.2f} mm del borde de la cara (< {p.min_wall:.1f} mm de pared)")
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
    hrep = res.report.get("hand")
    if hrep is not None:
        out["avisos"].extend(f"mano: {a}" for a in hrep.get("avisos", []))
        cov = hrep.get("cobertura")
        if cov is None and hrep.get("union") == "giro":
            out["problems"].append("mano: la mano articulada es una pieza aparte y no hay ningún brazo que la lleve: "
                                   "definí el brazo con un plano de miembro")
        elif cov is None:
            out["avisos"].append("mano: no hay ningún brazo que la lleve (la mano queda pegada al torso): "
                                 "definí el brazo con un plano de miembro")
        elif hrep.get("union") == "giro":
            pass                       # la cobertura de la cavidad del pasador la valida `_validate_hand`
        elif cov < MIN_HAND_COVER:
            out["avisos"].append(f"mano: solo el {100 * cov:.0f} % de la mano queda dentro del corte de «{hrep['miembro']}»; "
                                 "el resto no sale en ninguna pieza. Ampliá la silueta (o el espesor) del brazo para que la incluya")
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
            out["problems"].append(f"{name}: {comps} componentes (debería ser 1, soldado)" + _components_detail(mf))
    clearance_of = {}          # holgura mínima exigida por pieza: con clic, la punta deja `detent_gap` contra el disco
    for lname, dl in res.detent.items():
        for pn in _limb_piece_names(res, lname):
            for side in ("_izq", "_der"):
                clearance_of[f"{pn}{side}"] = min(p.clearance, dl.spec.detent_gap)
    for name, mf in res.pieces.items():
        if name in ("torso", PELVIS_NAME):
            continue
        inter = (mf ^ torso).volume()
        need_gap = clearance_of.get(name, p.clearance)
        gap = mf.min_gap(torso, min(5.0, max(2.0, 3.0 * need_gap)))     # radio acotado (ver `_gap_search`); lejos del torso informa el radio
        out["gaps"][name] = round(float(gap), 4)
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
        if res.report.get("limbs", {}).get(spec.name, {}).get("mecanismo") == "rotula":
            continue                      # el cuello de la rótula avisa por su cuenta (`Ball.build`)
        pin = _pin_spec(spec, p)
        if pin.shaft_d < p.min_pin_d:
            out["problems"].append(f"{spec.name}: vástago Ø{pin.shaft_d} < {p.min_pin_d}")
    out["recorrido"] = {}
    for name, (limb0, center, axis, (a0, a1)) in JT.revolute_motions(res.motion).items():
        if a1 <= a0:
            continue
        worst = 0.0
        ref = res.detent_ref if (name in res.detent and res.detent_ref is not None) else torso   # sin la deflexión elástica de las lengüetas
        for ang in np.arange(a0, a1 + 1e-9, sweep_check_step):
            worst = max(worst, (S.rotate_about_axis(limb0, center, axis, ang) ^ ref).volume())
        out["recorrido"][name] = round(float(worst), 5)
        if worst > 1e-4:
            out["problems"].append(f"{name}: interfiere con el torso durante el giro ({worst:.4f} mm³)")
    for name, mo in res.motion.items():                      # rótulas: no hay un eje que barrer, se recorre la grilla de poses del cono
        if JT.as_revolute(mo) is not None:
            continue
        worst, n_poses = JT.motion_interference(mo, torso, max(float(sweep_check_step), 1.0))
        out["recorrido"][name] = round(float(worst), 5)
        out.setdefault("recorrido_poses", {})[name] = n_poses
        if worst > 1e-4:
            out["problems"].append(f"{name}: interfiere con el torso dentro de su cono de giro ({worst:.4f} mm³)")
    out["cadera"] = _validate_hip_pin(res, p, limbs, out)
    out["detent"] = _validate_detent(res, p, out, detent_free_step)
    out["articulaciones"] = _validate_joints(res, p, joints, out, sweep_check_step)
    out["cadena"] = _validate_chain(res, p, out)
    out["cintura"] = _validate_waist(res, p, out)
    if res.mounts:
        out["montura"] = _validate_mount(res, p, out, limbs)
    out["mano"] = _validate_hand(res, p, out)
    out["piezas"] = _validate_parts(res, out)
    if res.right is not None:
        rv = validate(res.right, p, res.right_limbs, sit, sweep_check_step, joints, detent_free_step)
        out["derecha"] = rv
        out["problems"].extend(f"derecha: {x}" for x in rv["problems"] if not x.startswith(("torso", PELVIS_NAME)))
        out["avisos"].extend(f"derecha: {x}" for x in rv["avisos"] if not x.startswith(("torso", PELVIS_NAME)))
        out["avisos_pasador"].extend(f"derecha: {x}" for x in rv["avisos_pasador"])
    out["ok"] = not out["problems"]
    return out


def _validate_parts(res: BuildResult, out: dict) -> dict:
    """Piezas de catálogo puestas en el modelo: la compatibilidad barata (`ports.port_compat`) entre el puerto de la
    pieza y la toma del huésped. «no» es un problema; «aviso» (escalón, IoU bajo, margen del pasador) queda listado."""
    info: dict = {}
    for lugar, r in res.report.get("piezas", {}).items():
        out["avisos"].extend(f"{lugar}: {a}" for a in r.get("avisos", []))
        c = r.get("compat")
        info[lugar] = {"pieza": f"{r['id']}@{r['version']}", "nivel": None if c is None else c["nivel"],
                       "iou": None if c is None else c["iou"], "escalon_mm": None if c is None else c["escalon_mm"]}
        if c is None:
            continue
        if c["nivel"] == "no":
            out["problems"].append(f"{lugar}: la pieza {r['id']}@{r['version']} no es compatible: " + "; ".join(c["motivos"]))
        out["avisos"].extend(f"{lugar} ({r['id']}@{r['version']}): {a}" for a in c["avisos"])
    return info


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
        if (rot := r.get("rotula")) is not None:
            # Sin pasador: el zócalo de una cadera no puede tocar el del otro lado. La bola (centro a neck_len + R de la cara, R = ball_d/2)
            # llega a `|x| − R`; separación entre las dos bolas = 2 · eso, y los zócalos (dilatados la holgura) piden 2 · holgura.
            d = np.asarray(r["pin_dir"], float)
            d = d / np.linalg.norm(d)
            R = float(_ball_spec(spec, p).ball_d) / 2
            reach = float(_ball_spec(spec, p).neck_len) + R
            sep = 2.0 * float((np.asarray(r["pin_center"], float) + d * reach)[0] - R)
            info[spec.name] = {"separacion_cabezas_mm": round(sep, 3)}
            if sep < 2 * p.clearance - 1e-9:
                out["problems"].append(f"{spec.name}: las bolas de las dos caderas quedan a {sep:.2f} mm "
                                       f"(< 2·holgura = {2 * p.clearance:.2f} mm)")
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
        m = JT.as_revolute(res.motion.get(r["limb"]))
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
    by_limb: dict = {}
    for jn, r in rep.items():                                 # en el orden de la cadena (el informe se arma así)
        by_limb.setdefault(r["limb"], []).append((jn, r))
    out: dict = {}
    for name, mo in res.motion.items():
        if name == HEAD_NAME:
            continue
        if JT.as_revolute(mo) is None:                       # rótula: sin ángulo de cadera que aplicar, va en su pose impresa
            mf = JT.moved(mo.limb, mo.print_pose())
            out[f"{name}_izq"] = mf
            out[f"{name}_der"] = S.mirror_x(mf)
            continue
        limb0, center, axis, (a0, a1) = mo
        turns = a1 > a0
        if name in by_limb:
            links = by_limb[name]
            jbs = [res.joints[jn] for jn, _r in links]
            fl = [float(flex.get(jn, r["print_flex"])) for jn, r in links]
            names = [links[0][1]["piezas"][0]] + [r["piezas"][1] for _jn, r in links]
            parts = dict(zip(names, _chain_pieces([jbs[0].proximal] + [jb.proximal for jb in jbs[1:]] + [jbs[-1].distal], jbs, fl)))
            if res.hand is not None and res.hand["limb"] == name:
                hand = res.hand["piece0"]
                for jb, f in zip(reversed(jbs), reversed(fl)):
                    hand = jb.rotate_distal(f, hand)
                parts[HAND_NAME] = hand
        else:
            parts = {name: limb0}
            if res.hand is not None and res.hand["limb"] == name:
                parts[HAND_NAME] = res.hand["piece0"]
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


HAND_SPIN_STEP_DEG = 30.0
HAND_PULL_MM = 1.0          # cuánto se tira de la mano hacia afuera para comprobar que el pasador la retiene


def _validate_hand(res: BuildResult, p: Params, out: dict) -> dict:
    """Mano articulada (`join = "giro"`): pasador que entra, giro libre de 360° y mano cautiva.

    * pasador: entra en el brazo con `min_wall` de pared (se achica solo; si ni así entra, es un problema) y el vástago
      llega a `HAND_MIN_SHAFT_D`;
    * giro libre: la mano gira alrededor del eje de la muñeca cada `HAND_SPIN_STEP_DEG`° sin tocar las piezas del miembro
      ni el torso;
    * cautiva: tirar 1 mm de la mano hacia afuera hace que la cabeza del pasador choque con el labio del brazo."""
    h = res.hand
    if h is None:
        return {}
    info: dict = {}
    rep = res.report.get("hand", {})
    pin = rep.get("pasador", {})
    if not pin.get("entra", True):
        out["problems"].append(f"{HAND_NAME}: el pasador no entra en el brazo con {p.min_wall:g} mm de pared (muñeca muy fina)")
    if pin and pin["vastago_mm"] < HAND_MIN_SHAFT_D:
        out["problems"].append(f"{HAND_NAME}: vástago Ø{pin['vastago_mm']:.1f} < {HAND_MIN_SHAFT_D:g} mm: no se imprime")
    cov = rep.get("cobertura")
    if cov is not None and cov < 0.98:
        out["problems"].append(f"{HAND_NAME}: la silueta de «{h['limb']}» solo contiene el {100 * cov:.0f} % de la cavidad del "
                               "pasador: ampliala para que el pasador quede dentro del brazo")
    mano = res.pieces[f"{HAND_NAME}_izq"]
    mates = [res.pieces[f"{pn}_izq"] for pn in _limb_piece_names(res, h["limb"]) if pn != HAND_NAME]
    mates.append(body_ref(res))
    center, axis = np.asarray(h["center"], float), np.asarray(h["axis"], float)
    worst = 0.0
    for ang in np.arange(0.0, 360.0, HAND_SPIN_STEP_DEG):
        rot = S.rotate_about_axis(mano, center, axis, float(ang)) if ang else mano
        worst = max(worst, max(float((rot ^ m).volume()) for m in mates))
    info["giro_libre_peor_mm3"] = round(worst, 5)
    info["giro_libre"] = worst <= 1e-4
    if worst > 1e-4:
        out["problems"].append(f"{HAND_NAME}: no gira libre alrededor de la muñeca ({worst:.4f} mm³ de interferencia)")
    gap = min(float(mano.min_gap(m, 5.0)) for m in mates[:-1])
    info["holgura_mm"] = round(gap, 4)
    if gap < 0.9 * p.clearance - 1e-6:
        out["problems"].append(f"{HAND_NAME}: holgura con el brazo {gap:.3f} mm < {p.clearance:.2f} mm (la mano toca el brazo)")
    pulled = mano.translate(tuple(float(x) for x in -axis / np.linalg.norm(axis) * HAND_PULL_MM))
    bite = max(float((pulled ^ m).volume()) for m in mates[:-1])
    info["cautiva"] = bite > 1e-6
    info["cautiva_mm3"] = round(bite, 5)
    if not info["cautiva"]:
        out["problems"].append(f"{HAND_NAME}: no queda cautiva (tirar {HAND_PULL_MM:g} mm de la mano no choca con el brazo)")
    return info


WAIST_STEP_DEG = 5.0
WAIST_SKIN_TOL_MM3 = 0.5       # lo que sobresale la cavidad engrosada `min_wall` por la teselación de la piel no es una pared fina
MIN_HEAD_SHAFT_RATIO_WAIST = 1.6


def _bbox_overlap(a: S.Manifold, b: S.Manifold, tol: float = 1e-6) -> bool:
    ba, bb = a.bounding_box(), b.bounding_box()
    return all(ba[i] < bb[i + 3] + tol and bb[i] < ba[i + 3] + tol for i in range(3))


def _limb_vs_limb(key: str) -> bool:
    """«brazo_izq~pierna_der»: choque entre un miembro de arriba y uno de abajo. Depende de la pose (sentada para imprimir):
    parada la figura y con los brazos separados no se tocan, así que no es un problema de la cintura sino un aviso."""
    a, b = key.split("~", 1)
    return a.endswith(("_izq", "_der")) and b.endswith(("_izq", "_der"))


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
        ok_to, ok_core = 0.0, 0.0
        hit, core_hit, core_done, any_done = {}, {}, False, False
        for ang in np.arange(step, limit + 1e-9, step):
            hit = {}
            for mn, mm in moving.items():
                rot = S.rotate_about_axis(mm, wb.face.center, n, sign * ang)
                for fn, fm in fixed.items():
                    if _bbox_overlap(rot, fm) and (rot ^ fm).volume() > JOINT_TOL_MM3:
                        hit[f"{mn}~{fn}"] = round(float((rot ^ fm).volume()), 5)
            core = {k: v for k, v in hit.items() if not _limb_vs_limb(k)}
            if hit and not any_done:
                any_done, first_hit = True, dict(hit)
            if core:
                core_hit = core
                break
            if not any_done:
                ok_to = float(ang)
            ok_core = float(ang)
        free[key] = {"hasta_grados": ok_to, "choca": first_hit if any_done else {},
                     "hasta_grados_sin_miembros": ok_core, "choca_sin_miembros": core_hit}
    info["giro_libre"] = free
    for key, side in (("+", "a un lado"), ("-", "al otro lado")):
        g = free[key]
        if g["hasta_grados_sin_miembros"] < need - 1e-6 and g["choca_sin_miembros"]:
            out["problems"].append(f"{WAIST_NAME}: al girar {side} choca a más de {g['hasta_grados_sin_miembros']:g}° "
                                   f"({', '.join(g['choca_sin_miembros'])}); el giro pedido es {need:g}°")
        elif g["hasta_grados"] < need - 1e-6 and g["choca"]:
            out["avisos"].append(f"{WAIST_NAME}: en la pose impresa, al girar {side} se tocan los miembros a más de "
                                 f"{g['hasta_grados']:g}° ({', '.join(g['choca'])}): no es un problema de la figura, que se para "
                                 f"y se separan los brazos antes de girar la cintura; el giro pedido es {need:g}°")
    return info


def _mount_poses(res: BuildResult, p: Params, limbs, side: str, hip_step: float, arm_step: float) -> list:
    """Poses en las que se mide la montura: `[(etiqueta, grados, {pieza del lado `side`: sólido}, de_pie)]`.

    De pie (cadera 0°, piernas estiradas, brazos colgando) y después cada miembro que gira sobre su recorrido: las piernas, la
    cadera de 0 a `sit_angle` (como `_validate_chain`); los brazos, su rango de `res.motion` (si lo tienen). De cada pose solo
    entran las piezas del lado de la montura y del miembro que gira, que es lo que puede tocar un puente de ese lado."""
    kinds = {sp.name: sp.kind for sp in limbs}
    straight = {jn: 0.0 for jn in straight_leg_flex(res)}

    def own(limb: str, posed: dict) -> dict:
        return {f"{n}{side}": posed[f"{n}{side}"] for n in _limb_piece_names(res, limb) if f"{n}{side}" in posed}

    stand = posed_pieces(res, 0.0, straight, p)
    poses = [("de pie", 0.0, {k: v for k, v in stand.items() if k.endswith(side)}, True)]
    for name, (_l, _c, _a, (a0, a1)) in JT.revolute_motions(res.motion).items():
        if name == HEAD_NAME or a1 <= a0:
            continue
        if kinds.get(name, "arm") == "leg":
            angles = np.arange(hip_step, p.sit_angle + 1e-9, hip_step)
            label = "cadera"
        else:
            angles = [a for a in np.arange(a0, a1 + 1e-9, arm_step) if abs(a) > 1e-9]
            label = "brazo"
        for ang in angles:
            poses.append((f"{label} {name}", float(ang), own(name, posed_pieces(res, float(ang), None, p)), False))
    return poses


def mount_flank_map(res: BuildResult, p: Params, limbs, lugar: str = "pelvis", lado: str = "izq", need_mm: float | None = None,
                    hip_step: float = 15.0, arm_step: float = 15.0, n_points: int | None = None, seed: int = 0):
    """M08: mapa de espacio libre del flanco (`mount_map.FlankMap`) de la pieza que lleva `lugar`, contra los miembros del mismo lado
    en las poses de `_mount_poses` y la cabeza. Usa la pieza sin montura (`host_bare`) si ya hay monturas. `need_mm` por defecto:
    una hoja de 0,8 mm con el juego y la pared de siempre (`mount_map.needed_height`)."""
    from . import mount_map as MM

    name = PELVIS_NAME if lugar == "pelvis" and PELVIS_NAME in res.pieces else "torso"
    if lugar not in ("pelvis", "torso") or name not in res.pieces:
        raise ValueError(f"lugar «{lugar}»: el mapa se hace sobre la pelvis o el torso")
    host = res.host_bare.get(name, res.pieces[name]) if getattr(res, "host_bare", None) else res.pieces[name]
    side = "_izq" if lado == "izq" else "_der"
    obstacles = []
    for _label, _ang, pieces, _standing in _mount_poses(res, p, limbs, side, hip_step, arm_step):
        obstacles += [S.to_trimesh(mf) for mf in pieces.values()]
    if HEAD_NAME in res.pieces:
        obstacles.append(S.to_trimesh(res.pieces[HEAD_NAME]))
    if need_mm is None:
        need_mm = MM.needed_height(0.8, wall=p.min_wall, member_clearance=p.clearance)
    kw = {} if n_points is None else {"n_points": n_points}
    return MM.flank_map(S.to_trimesh(host), obstacles, need_mm, host_name=name, lado=lado, seed=seed, **kw)


def _validate_mount(res: BuildResult, p: Params, out: dict, limbs=(), hip_step: float = 15.0, arm_step: float = 15.0,
                    extract_step: float | None = None) -> dict:
    """Monturas de armas (plan §6.4). Para cada una:

    * **extracción libre**: el arma sale por su eje (`lazo`) o levantándola de la superficie (`clip`, `cuna`), de a `extract_step` mm,
      hasta librar el puente, sin entrar en el cuerpo ni en los miembros (de pie): si no, es un problema;
    * **holgura a miembros**: el puente (sin su pie) contra los miembros de su lado, de pie y en todo el recorrido de la cadera y
      de los brazos: choque o holgura < 0,9 · `clearance` es un problema; el arma enfundada contra los mismos miembros es un
      problema de pie y un aviso en movimiento (el arma es un accesorio que se saca);
    * **retención**: avisos del lazo (la guarda no apoya) y gravedad (con la punta hacia arriba el arma se cae);
    * **imprimible**: pared ≥ `min_wall` (problema) e inclinación del eje del hueco ≤ 45° de la vertical (aviso);
    * **mano libre**: el puente empieza después de la guarda, sin tapar la empuñadura; **una sola pieza** del huésped (se informa; la
      validación general ya la exige).

    Estabilidad (centro de gravedad dentro del apoyo) todavía no se mide: depende de qué cuenta como apoyo (puente + flanco)."""
    from . import mount as MT

    info: dict = {}
    if not res.mounts:
        return info
    step = MT.EXTRACT_STEP if extract_step is None else extract_step
    body = {n: res.pieces[n] for n in ("torso", PELVIS_NAME, HEAD_NAME) if n in res.pieces}
    for name, mb in res.mounts.items():
        lz, rec = mb.lazo, {"huesped": mb.host}
        side = "_izq" if mb.spec.lado == "izq" else "_der"
        # una sola pieza / mano libre / pared / inclinación (aritmética, sin booleanas)
        rec["componentes_huesped"] = len(res.pieces[mb.host].decompose())
        guard_end = float(lz.info.get("guarda_fin_mm", 0.0))
        first = min(lz.stations) - float(lz.info["largo_puente_mm"]) / 2
        rec["mano_libre"] = bool(first >= guard_end - 1e-6)
        if not rec["mano_libre"]:
            out["problems"].append(f"{name}: el puente empieza a {first:.2f} mm de la empuñadura, antes de que termine la guarda "
                                   f"({guard_end:.2f} mm): taparía el agarre")
        wall = float(lz.info["pared_mm"])
        rec["pared_mm"] = wall
        if wall < p.min_wall - 1e-9:
            out["problems"].append(f"{name}: la pared del puente mide {wall:.2f} mm < {p.min_wall:g} mm")
        e = MT.handle_direction(lz)
        tilt = MT.tilt_from_vertical_deg(e)
        rec["inclinacion_grados"] = round(tilt, 2)
        if tilt > MT.MAX_PRINT_TILT_DEG + 1e-9:
            out["avisos"].append(f"{name}: el eje del hueco está a {tilt:.0f}° de la vertical (> {MT.MAX_PRINT_TILT_DEG:g}°): "\
                                 "el techo del puente puede necesitar soportes")
        # retención
        out["avisos"].extend(f"{name}: {a}" for a in lz.info.get("avisos", []))
        gc = MT.gravity_seat_cos(lz)
        rec["gravedad_cos"] = round(gc, 3)
        if gc < MT.GRAVITY_MIN_COS:
            out["avisos"].append(f"{name}: la punta no mira hacia abajo (coseno {gc:.2f}): la gravedad saca el arma del puente en lugar "
                                 "de apoyar la guarda")
        # poses
        poses = _mount_poses(res, p, limbs, side, hip_step, arm_step)
        stand = poses[0][2]
        # extracción libre (de pie)
        obstacles = dict(body, **stand)
        if lz.lips is not None and mb.host in obstacles:        # los labios del clip ceden al encajar la hoja: no cuentan como choque
            obstacles[mb.host] = S.difference(obstacles[mb.host], lz.lips)
        scan = MT.extraction_scan(lz, obstacles, step)
        rec["extraccion"] = scan
        for k, h in scan["choques"].items():
            how = "el arma no sale por su eje" if scan.get("modo", "deslizar") == "deslizar" else "el arma no se puede levantar de la montura"
            out["problems"].append(f"{name}: {how}: choca con {k} a {h['desde_mm']:g} mm de recorrido "
                                   f"({h['max_mm3']:.3f} mm³)")
        # holguras: puente y arma enfundada contra los miembros
        bridge = MT.mount_solid(lz)
        need = MT.CLEARANCE_FACTOR * p.clearance
        gaps, weapon_hits = {}, {}
        for label, ang, pieces, standing in poses:
            for pn, mf in pieces.items():
                if not _bbox_overlap(bridge, mf, 5.0):
                    continue
                inter = float((bridge ^ mf).volume())
                gap = 0.0 if inter > MT.CONTACT_TOL_MM3 else float(bridge.min_gap(mf, 5.0))
                key = f"{pn}@{label}"
                if key not in gaps or gap < gaps[key][0]:
                    gaps[key] = (gap, ang)
                if gap < need - 1e-6 and gap < 5.0:
                    out["problems"].append(f"{name}: el puente queda a {gap:.3f} mm de {pn} ({label} {ang:g}°) < {need:.3f} mm"
                                           if inter <= MT.CONTACT_TOL_MM3 else
                                           f"{name}: el puente choca con {pn} ({label} {ang:g}°, {inter:.3f} mm³)")
                if _bbox_overlap(lz.weapon, mf):
                    wv = float((lz.weapon ^ mf).volume())
                    if wv > MT.CONTACT_TOL_MM3 and (pn, standing) not in weapon_hits:
                        weapon_hits[(pn, standing)] = (label, ang, wv)
        rec["holguras_mm"] = {k: round(v[0], 4) for k, v in gaps.items()}
        for (pn, standing), (label, ang, wv) in weapon_hits.items():
            msg = f"{name}: el arma enfundada toca {pn} ({label}{'' if standing else f' {ang:g}°'}, {wv:.3f} mm³)"
            (out["problems"] if standing else out["avisos"]).append(msg)
        info[name] = rec
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
        need = 0.9 * min(p.clearance, p.axial_clearance)
        # radio acotado (ver `_gap_search`): solo hay que distinguir «menos de `need`»; a 5 mm costaba 61 s de 108 en la figura real
        gap = float(rest.min_gap(prox, min(5.0, max(2.0, 3.0 * need))))
        rec["holgura_mm"] = round(gap, 4)
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
    """Piezas (sin sufijo de lado) que componen un miembro: una sola, o proximal + distal si tiene articulación; con la mano
    articulada, también la pieza `mano` en el miembro que la lleva."""
    names = [limb]
    first = True
    for r in res.report.get("joints", {}).values():             # en el orden de la cadena: la distal de una es la proximal de la siguiente
        if r["limb"] == limb:
            names = list(r["piezas"]) if first else names + [r["piezas"][1]]
            first = False
    if res.hand is not None and res.hand["limb"] == limb:
        names.append(HAND_NAME)
    return names


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
            and n.rsplit("_", 1)[0] in {pn for ln, (_l, _c, _a, (a0, a1)) in JT.revolute_motions(res.motion).items() if a1 > a0
                                        for pn in _limb_piece_names(res, ln)}]
    if not legs:
        return 1.0
    ly = np.mean([(m.bounding_box()[1] + m.bounding_box()[4]) / 2 for m in legs])
    ty = (pieces["torso"].bounding_box()[1] + pieces["torso"].bounding_box()[4]) / 2
    return 1.0 if ly >= ty else -1.0


def flat_click_advice(res: BuildResult, depth: float = 1.0) -> dict:
    """Brazos con clic que no apoyan en el plano del corte «sin soportes»: {miembro: {...}}. El clic fija la pose del brazo\n    (la punta cae en una muesca solo cada 360/n°), así que no se lo puede girar después sin fundir la punta con el disco:\n    hay que generar la figura con `DetentSpec.print_angle` igual al ángulo en que el brazo apoya. Devuelve el ángulo\n    actual, el de apoyo, cuánto cuelga por debajo del plano (mm) y el estado de `_arm_turn_to_plane`."""
    out: dict = {}
    z_cut = body_base_z(res.pieces) + float(depth)
    fwd = _forward_sign(res, res.pieces)
    for n, dl in res.detent.items():
        mo = JT.as_revolute(res.motion.get(n))
        if mo is None or mo.range[1] > mo.range[0]:
            continue
        _l, center, axis, _r = mo
        pts = np.vstack([S.to_trimesh(res.pieces[f"{pn}_izq"]).vertices for pn in _limb_piece_names(res, n)])
        low = float(pts[:, 2].min())
        if low >= z_cut - 1e-6:
            continue
        sgn, theta, status = _arm_turn_to_plane(pts, center, axis, z_cut, fwd)
        out[n] = {"angulo_actual": float(dl.print_angle), "cuelga_mm": round(z_cut - low, 2), "estado": status,
                  "angulo_apoyo": round(float(dl.print_angle + sgn * theta), 1) if status == "ok" else None}
    return out


HIP_FILL_BRIDGE = 0.25      # mm que el relleno se mete en el muslo para quedar soldado a él
HIP_FILL_MIN_MM3 = 0.5      # relleno menor que esto no vale la pena
_BIG = 1e4


def _box(x0=-_BIG, x1=_BIG, y0=-_BIG, y1=_BIG, z0=-_BIG, z1=_BIG):
    return S.Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def _extend_thighs(res: BuildResult, pieces: dict, z_cut: float, p: Params) -> tuple[dict, dict]:
    """Un muslo se queda con la loncha de pelvis que el corte plano en `z_cut` le saca al cuerpo, y el hueco de cadera se
    vuelve a tallar en la pelvis recortada alrededor del muslo ya alargado.

    Sin esto, de pie queda entre el borde nuevo de la pelvis y el muslo un hueco del grosor del corte. La loncha pertenecía
    a la pelvis (vino del STL junto con el resto), así que no se la puede devolver a la pelvis (queda bajo el plano de
    impresión) ni agregar al muslo sin más (al girar choca con la pared del torso): se la pasa al muslo y se talla en la
    pelvis el barrido del relleno entre 0° y el límite de cadera, ensanchado la holgura. Con la pelvis recortada esa
    pared ya no existe de todos modos. Devuelve (piezas nuevas, informe); si algo choca o no hay nada que rellenar, las
    mismas piezas."""
    out, rep = dict(pieces), {}
    body = [n for n in ("torso", PELVIS_NAME) if n in pieces]
    for limb, (_l, c, ax, (a0, a1)) in JT.revolute_motions(res.motion).items():
        if limb == HEAD_NAME or a1 <= a0:
            continue
        key = f"{_limb_piece_names(res, limb)[0]}_izq"
        if key not in out:
            rep[limb] = {"aviso": f"no se encontró la pieza {key}"}
            continue
        r = next((r for r in res.report.get("joints", {}).values() if r["limb"] == limb), {})
        th = float(r.get("print_hip", p.sit_angle))
        cc, aa = np.asarray(c, float), np.asarray(ax, float) / np.linalg.norm(ax)
        leg0 = S.rotate_about_axis(out[key], cc, aa, -th)                       # de pie (0°)
        lx0, _a, _b, lx1, _c, _d = out[key].bounding_box()
        slab = S.intersection(S.intersection(S.union(*[out[n] for n in body]), _box(z1=z_cut)), _box(lx0, lx1))
        if slab.is_empty() or slab.volume() < HIP_FILL_MIN_MM3:
            rep[limb] = {"aviso": f"el plano de corte (z = {z_cut:.2f}) no saca pelvis sobre este muslo: no hay nada que rellenar"}
            continue
        thick = S.swept_offset([slab], cc, aa, 0.0, p.clearance + HIP_FILL_BRIDGE, 0.2, 3.0)
        shadow = S.Manifold.extrude(leg0.project(), z_cut + 1.0 - leg0.bounding_box()[2]).translate((0, 0, leg0.bounding_box()[2]))
        fill = S.intersection(S.intersection(thick, shadow), _box(lx0, lx1, z1=z_cut - p.clearance))
        fill = S.drop_small_components(fill, 0.0)
        if fill.is_empty() or fill.volume() < HIP_FILL_MIN_MM3:
            rep[limb] = {"aviso": "la pelvis que saca el corte no queda sobre el muslo (o el barrido de la pierna la ocuparía): nada que pasarle"}
            continue
        new0 = S.drop_small_components(S.union(leg0, fill), 0.01)
        sweep = S.swept_offset([fill], cc, aa, float(a1), p.clearance, 0.2, 3.0)
        carved = {n: S.difference(out[n], sweep, S.mirror_x(sweep)) for n in body}
        lost_tabs = None
        if limb in res.detent:                                           # las lengüetas que quedan sueltas se pierden (se avisa)
            carved = {n: S.drop_small_components(m, 0.02) for n, m in carved.items()}
        ref = res.detent_ref if limb in res.detent else None             # con clic: el cuerpo sin lengüetas es la referencia del giro libre
        if ref is not None:
            wide = _box(y0=cc[1] - 40, y1=cc[1] + 40, z0=cc[2] - 40, z1=cc[2] + 40)
            tabs = lambda ps: float(S.difference(S.intersection(S.union(*[ps[n] for n in body]), wide), ref).volume())
            lost_tabs = tabs(out) - tabs(carved)
        for n in body:
            if len(carved[n].decompose()) > len(out[n].decompose()):
                rep[limb] = {"aviso": f"el tallado parte {n} en piezas sueltas; no se rellena el muslo"}
        if limb in rep:
            continue
        rho = float(np.linalg.norm(np.cross(S.to_trimesh(fill).vertices - cc, aa), axis=1).max()) + 2.0
        near = _box(lx0 - 1, lx1 + 1, cc[1] - rho, cc[1] + rho, cc[2] - rho, cc[2] + rho)
        crop = (S.difference(S.intersection(ref, near), sweep, S.mirror_x(sweep)) if ref is not None
                else S.intersection(S.union(*[carved[n] for n in body]), near))
        worst = max(float(S.intersection(S.rotate_about_axis(new0, cc, aa, a), crop).volume())
                    for a in np.arange(0.0, a1 + 1e-6, 5.0))
        gap = float(new0.min_gap(crop, 3.0))
        rep[limb] = {"volumen_muslo_agregado_mm3": round(float(new0.volume() - leg0.volume()), 1),
                     "volumen_pelvis_tallado_mm3": round(float(sum(out[n].volume() - carved[n].volume() for n in body)), 1),
                     "choque_max_mm3": round(worst, 3), "holgura_de_pie_mm": round(gap, 3), "angulo_pose": th}
        if lost_tabs is not None:
            rep[limb]["lengueta_clic_perdida_mm3"] = round(lost_tabs, 2)
            if lost_tabs > 1e-3:
                rep[limb]["advertencia"] = (f"el relleno corta las lengüetas del clic de la cadera ({lost_tabs:.1f} mm³): "
                                            "el clic puede dejar de funcionar")
        if worst > 1e-3:
            rep[limb]["aviso"] = f"el relleno choca con la pelvis al girar ({worst:.2f} mm³); no se rellena el muslo"
            continue
        out.update(carved)
        seated = S.rotate_about_axis(new0, cc, aa, th)
        out[key], out[key.replace("_izq", "_der")] = seated, S.mirror_x(seated)
    return out, rep


def _reattach_mounts(res: BuildResult, pieces: dict, mounts: dict, z_cut: float, arm_pieces: set, avisos: list) -> dict:
    """M06: vuelve a unir las monturas a las piezas ya cortadas y talladas por `flat_for_print` (modifica `pieces`).

    `res.pieces` trae los huéspedes sin montura. Lo que pasa por montura, en el informe:
    - si el plano corta el puente (queda por debajo de `z_cut`), se lo corta con el plano y se avisa;
    - si el relleno de cadera o el tallado del brazo le sacó material al pie, se avisa (cuánto);
    - si ya no se puede unir (queda flotando o parte la pieza), se deja sin montura y se avisa;
    - si el puente toca un brazo girado, se avisa."""
    from .mount import MountError, attach_lazo

    above = _box(z0=z_cut)
    out: dict = {}
    for name, mb in mounts.items():
        lz, host = mb.lazo, mb.host
        rec: dict = {"huesped": host, "unida": False}
        outer, low = lz.outer, float(lz.outer.bounding_box()[2])
        if low < z_cut - 1e-6:
            outer = S.intersection(lz.outer, above)
            rec["cortada_mm"] = round(z_cut - low, 3)
            avisos.append(f"{name}: la montura queda cortada por el plano del corte ({z_cut - low:.2f} mm por debajo): "
                          "subí el ancla o usá menos corte")
        if outer.is_empty():
            avisos.append(f"{name}: el plano del corte se llevó toda la montura; la figura sale sin ella")
            out[name] = rec
            continue
        foot_before = float((outer ^ res.pieces[host]).volume())
        foot_after = float((outer ^ pieces[host]).volume())
        rec["pie_tallado_mm3"] = round(max(0.0, foot_before - foot_after), 3)
        if foot_before - foot_after > HIP_FILL_MIN_MM3:
            avisos.append(f"{name}: el relleno de cadera o el tallado del brazo le sacó {foot_before - foot_after:.2f} mm³ al pie de la montura")
        try:
            pieces[host] = attach_lazo(pieces[host], dataclasses.replace(lz, outer=outer))
        except MountError as exc:
            avisos.append(f"{name}: tras el corte no se pudo volver a unir la montura ({exc}); la figura sale sin ella")
            out[name] = rec
            continue
        rec["unida"] = True
        for an in sorted(arm_pieces):
            if an in pieces and (outer ^ pieces[an]).volume() > 1e-3:
                avisos.append(f"{name}: el puente toca {an} tras girar el brazo")
        out[name] = rec
    return out


def flat_for_print(res: BuildResult, depth: float = 1.0, p: Params | None = None) -> tuple[dict, dict]:
    """Prepara la figura sentada para imprimirla sin soportes: devuelve (piezas nuevas, informe).

    0. Las piernas con rodilla se estiran (flexión 0°): muslo y pantorrilla quedan alineados y horizontales.
    1. Corta con un plano horizontal a `depth` mm sobre el punto más bajo del torso (la cola): las piernas y la cola
       quedan con una base plana. Se corta todo salvo los brazos y la cabeza.
    2. Los brazos (sin tocarlos) quedan entonces por debajo del plano: giran hacia adelante alrededor del pasador del
       hombro, el mínimo necesario para que su punto más bajo apoye justo en ese plano (un brazo que cuelga sube al
       girar). Con articulación de codo gira la cadena entera.
    El relleno de muslos (`_extend_thighs`, `Params.flat_fill_hip`) se hace antes del corte, en tres corridas: la altura del
    plano sale del punto más bajo del cuerpo; se alarga cada muslo con la loncha de pelvis que ese plano sacaría y se talla
    la pelvis alrededor; si el tallado cambió el punto más bajo (y con él el plano) se repite con el plano nuevo; y recién
    después se corta todo por el plano final.
    3. El hueco del hombro en el torso se hizo para un brazo que no gira: se talla además con el barrido del brazo
       entre 0° y el giro (ensanchado la holgura), para que gire libre sobre el pasador.
    El conjunto queda apoyado en el plano del corte; la exportación lo baja a z = 0.
    """
    p = Params() if p is None else p
    depth = float(depth)
    mounts = dict(getattr(res, "mounts", None) or {})
    baked = {n: mb for n, mb in mounts.items() if mb.baked}      # M17: ya son parte de la pieza; el plano las corta como al resto del cuerpo
    mounts = {n: mb for n, mb in mounts.items() if not mb.baked}
    if mounts and res.host_bare:                      # M06: el plano, el relleno de cadera y los tallados se calculan sin las monturas
        res = dataclasses.replace(res, pieces={**res.pieces, **res.host_bare})   # (se vuelven a unir al final)
    else:
        mounts = {}
    pieces = dict(res.pieces)
    info: dict = {"corte_sobre_cola_mm": depth, "avisos": []}
    # 0. piernas estiradas: con rodilla, la pantorrilla pasa de la flexión de la pose impresa a 0° y queda en línea con el
    #    muslo (ambos horizontales con la cadera sentada), para que las dos piezas apoyen sobre el plano del corte
    stretched: dict = {}
    straight_by_limb: dict = {}
    for jn, fl in straight_leg_flex(res).items():             # en el orden de la cadena (el informe se arma así)
        straight_by_limb.setdefault(res.report["joints"][jn]["limb"], []).append((jn, fl))
    for limb_name, links in straight_by_limb.items():
        rs = [res.report["joints"][jn] for jn, _fl in links]
        jbs = [res.joints[jn] for jn, _fl in links]
        mo = res.motion[limb_name]
        hc, hax = ((rv.center, rv.axis) if (rv := JT.as_revolute(mo)) is not None else (mo.center, mo.axis))   # rótula: th = 0, no se gira
        th = float(rs[0].get("print_hip", p.sit_angle))
        names = [rs[0]["piezas"][0]] + [r["piezas"][1] for r in rs]
        posed = _chain_pieces([jbs[0].proximal] + [jb.proximal for jb in jbs[1:]] + [jbs[-1].distal], jbs, [fl for _jn, fl in links])
        for nm, mf in zip(names, posed):
            if th:
                mf = S.rotate_about_axis(mf, hc, hax, th)
            pieces[f"{nm}_izq"] = mf
            pieces[f"{nm}_der"] = S.mirror_x(mf)
        for (jn, fl), r in zip(links, rs):
            stretched[jn] = {"flexion_grados": fl, "flexion_impresa_grados": r["print_flex"]}
    info["rodillas_estiradas"] = stretched
    arm_limbs = [n for n, (_l, _c, _a, (a0, a1)) in JT.revolute_motions(res.motion).items() if n != HEAD_NAME and a1 <= a0 and n not in res.detent]
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
    rev = JT.revolute_motions(res.motion)
    protected = arm_pieces | {f"{pn}{side}" for n in res.detent if n in rev and rev[n].range[1] <= rev[n].range[0]
                              for pn in _limb_piece_names(res, n) for side in ("_izq", "_der")}
    if p.flat_fill_hip:
        z0 = body_base_z(pieces) + depth
        filled, fill_rep = _extend_thighs(res, pieces, z0, p)
        z1 = body_base_z(filled) + depth
        if abs(z1 - z0) > 0.02 and fill_rep:           # el tallado movió el punto más bajo: segunda pasada con el plano nuevo
            filled, fill_rep = _extend_thighs(res, pieces, z1, p)
        if fill_rep:
            info["relleno_cadera"] = fill_rep
            info["avisos"] += [f"{k}: {v[w]}" for k, v in fill_rep.items() for w in ("aviso", "advertencia") if w in v]
        pieces = filled
    z_cut = body_base_z(pieces) + depth
    info["altura_corte_z"] = round(float(z_cut), 3)
    for n, mb in baked.items():
        low = float(mb.lazo.outer.bounding_box()[2])
        if low < z_cut - 1e-6:
            info["avisos"].append(f"{n}: la montura de la pieza queda cortada por el plano del corte ({z_cut - low:.2f} mm por debajo): "
                                  "usá otra pieza con la montura más arriba o menos corte")
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
        if JT.as_revolute(res.motion[limb]) is None:
            angles[limb] = 0.0                  # rótula: no hay un eje sobre el que girar el brazo hacia el plano; queda como está
            continue
        _l, center, axis, _ = JT.as_revolute(res.motion[limb])
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
    if mounts:
        info["monturas"] = _reattach_mounts(res, pieces, mounts, z_cut, arm_pieces, info["avisos"])
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


def mounts_report(res: BuildResult, val: dict | None = None) -> dict:
    """Monturas de armas de la figura para el informe y la lista de piezas (M12): por montura, el arma, dónde va, la abertura del
    puente y, si se pasa el resultado de `validate`, el nivel y lo que la validación dijo (problemas y avisos). Vacío sin monturas."""
    from . import mount as MT

    if not getattr(res, "mounts", None):
        return {}
    by = MT.report_by_mount(val) if val else {}
    out = {}
    for name, mb in res.mounts.items():
        info, sp = mb.info, mb.spec
        rec = {"arma": sp.arma, "lugar": sp.lugar, "lado": sp.lado, "tipo": sp.tipo, "huesped": mb.host,
               "holgura_mm": float(sp.holgura), "pared_mm": info.get("pared_mm"), "abertura_mm": info.get("abertura_mm"),
               "retiene": info.get("retiene"), "avisos_construccion": list(info.get("avisos") or [])}
        if name in by:
            rec.update(by[name], nivel=MT.level_of(by[name]))
        out[name] = rec
    return out


def parts_list(res: BuildResult, name: str = "figura") -> dict | None:
    """Lista de piezas de catálogo usadas en la figura (plan de piezas intercambiables, §4.5): lugar -> id@versión,
    sha256, origen y resultado de compatibilidad. None si la figura no usa piezas de catálogo."""
    rep = res.report.get("piezas") or {}
    if not rep:
        return None
    lugares = {}
    for lugar, r in sorted(rep.items()):
        c = r.get("compat") or {}
        lugares[lugar] = {"pieza": f"{r.get('id')}@{r.get('version')}", "id": r.get("id"), "version": r.get("version"),
                          "sha256": r.get("sha256"), "origen": r.get("origen"), "lugar_puerto": r.get("lugar"),
                          "compat": c.get("nivel", c.get("resultado")), "avisos": list(c.get("avisos") or []) + list(r.get("avisos") or [])}
    lst = {"formato": "afm-lista-piezas-1", "figura": name, "piezas": lugares}
    if getattr(res, "mounts", None):                         # solo si hay: la lista de siempre no cambia
        lst["monturas"] = mounts_report(res)
    return lst


def parts_list_markdown(lst: dict) -> str:
    rows = ["| Lugar | Pieza | Origen | Compatibilidad | sha256 |", "|---|---|---|---|---|"]
    for lugar, r in lst["piezas"].items():
        rows.append(f"| {lugar} | {r['pieza']} | {r.get('origen') or '-'} | {r.get('compat') or '-'} | `{(r.get('sha256') or '')[:12]}` |")
    out = [f"# Lista de piezas: {lst['figura']}", "", *rows]
    notes = [(lugar, a) for lugar, r in lst["piezas"].items() for a in r["avisos"]]
    acc = lst.get("accesorios") or []
    if acc:
        out += ["", "## Accesorios", "", "| Accesorio | Archivo | Mano de la figura | sha256 |", "|---|---|---|---|"]
        out += [f"| {a['pieza']} | `{a['archivo']}` | {a.get('compat') or '-'} | `{(a.get('sha256') or '')[:12]}` |" for a in acc]
        notes += [(a["pieza"], x) for a in acc for x in list(a.get("motivos") or []) + list(a.get("avisos") or [])]
    mon = lst.get("monturas") or {}
    if mon:
        out += ["", "## Monturas", "", "| Montura | Arma | Va en | Abertura (mm) | Pared (mm) | Retiene | Estado |", "|---|---|---|---|---|---|---|"]
        for n, m in mon.items():
            ab = " × ".join(f"{x:g}" for x in (m.get("abertura_mm") or [])) or "-"
            ret = {True: "sí", False: "no"}.get(m.get("retiene"), "-")
            out.append(f"| {n} | {m['arma']} | {m['huesped']} ({m['lado']}) | {ab} | {m.get('pared_mm') or '-'} | {ret} | {m.get('nivel') or '-'} |")
            notes += [(n, a) for a in (list(m.get("problemas") or []) + list(m.get("avisos") or []) or m.get("avisos_construccion") or [])]
    if notes:
        out += ["", "## Avisos", *[f"- **{lugar}**: {a}" for lugar, a in notes]]
    return "\n".join(out) + "\n"


def attach_mounts_to_parts_list(outdir: str, name: str, monturas: dict) -> bool:
    """Pone (o actualiza) las monturas, ya con lo que dijo la validación, en la lista de piezas escrita por `export` (JSON y Markdown)."""
    import json

    path = os.path.join(outdir, f"{name}_lista_de_piezas.json")
    if not monturas or not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as fh:
        lst = json.load(fh)
    lst["monturas"] = monturas
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(lst, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(outdir, f"{name}_lista_de_piezas.md"), "w", encoding="utf-8") as fh:
        fh.write(parts_list_markdown(lst))
    return True


def attach_accessories_to_parts_list(outdir: str, name: str, accesorios: list[dict]) -> bool:
    """Agrega los accesorios a la lista de piezas ya escrita por `export` (JSON y Markdown). False si no hay lista."""
    import json

    path = os.path.join(outdir, f"{name}_lista_de_piezas.json")
    if not accesorios or not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as fh:
        lst = json.load(fh)
    lst["accesorios"] = accesorios
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(lst, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(outdir, f"{name}_lista_de_piezas.md"), "w", encoding="utf-8") as fh:
        fh.write(parts_list_markdown(lst))
    return True


def export(res: BuildResult, outdir: str, name: str = "figura", orient: bool | str = False,
           max_angle: float = 45.0, extra_tilts=(0.0,), flat: float | None = None, params: Params | None = None,
           progress=None, extras=None, colorize=None, extras_names=None) -> dict:
    """Escribe el ensamblado y un STL por parte. Con `orient=True` apoya todo el conjunto en la cama en la pose de
    la vista previa (sentada, cabeza arriba); con `orient="auto"` lo gira a la orientación de menor voladizo.
    En ambos casos informa la orientación. Con `flat` (altura del corte en mm sobre la cola; `Params.flat_depth`) antes se aplana la base y se
    giran los brazos para imprimir sin soportes (`flat_for_print`); el informe va en `info["sin_soportes"]`.
    `extras_names`: nombre de cada accesorio de `extras` (el que lleva su objeto en el 3MF de Bambu; por defecto `accesorio_N`).
    `colorize` (opcional, `colorexport.Colorizer`): por cada pieza se llama `colorize(nombre, malla, res)` con la malla en su pose,
    antes de la orientación de impresión, y al final `colorize.write(...)` deja los archivos de color en `info["color"]`. No cambia
    ningún STL (plan T15, D7)."""
    say = progress or (lambda *_: None)
    os.makedirs(os.path.join(outdir, "piezas"), exist_ok=True)
    T = None
    info: dict = {}
    res0 = res                       # el `res` completo (con `right`, `right_limbs`) para el registro del color
    if flat is not None:
        say("Aplanando la base…")
        pieces, rep = flat_for_print(res, flat, params)
        res = BuildResult(pieces=pieces, report=res.report, motion=res.motion, joints=res.joints, detent=res.detent,
                          detent_ref=res.detent_ref, waist=res.waist)
        info["sin_soportes"] = rep
    cap = (params if params is not None else Params()).export_max_tris
    if cap and cap > 0 and any(m.num_tri() > cap for m in res.pieces.values()):
        say("Reduciendo triángulos de las piezas…")
        capped, rep_tris = cap_export_triangles(res.pieces, cap, clearance=(params if params is not None else Params()).clearance)
        info["triangulos_reducidos"] = rep_tris             # {pieza: [antes, después]}; [n, n] = no se pudo reducir sin perder holgura/volumen
        res = BuildResult(pieces=capped, report=res.report, motion=res.motion, joints=res.joints, detent=res.detent,
                          detent_ref=res.detent_ref, waist=res.waist)
    if orient:
        say("Calculando la orientación de impresión…")
        o = print_orientation(res, max_angle, extra_tilts, mode="auto" if orient == "auto" else "pose")
        T = o.rotation
        info["orientacion"] = {"etiqueta": o.label, "voladizo_mm2": round(o.overhang_area, 2),
                               "base_mm2": round(o.base_area, 2), "angulo_max": max_angle}
    meshes = []
    if colorize is not None:
        import dataclasses
        # `flat`/decimación rearman el `BuildResult` sin el lado derecho; el registro de las piezas `_der` lo necesita
        hook_res = dataclasses.replace(res, right=res0.right, right_limbs=res0.right_limbs, placed=res0.placed)
    for part, mf in res.pieces.items():
        say(f"Escribiendo «{part}»…")
        tm = S.to_printable_trimesh(mf)
        if colorize is not None:
            say(f"Coloreando «{part}»…")
            colorize(part, tm.copy(), hook_res)
        if T is not None:
            tm.apply_transform(T)
        tm.export(os.path.join(outdir, "piezas", f"{name}_{part}.stl"))
        meshes.append(tm)
    say("Escribiendo la figura ensamblada…")
    asm = trimesh.util.concatenate(meshes)
    placed_extras: list = []
    if extras:                      # accesorios (armas): al lado de la figura, en el mismo archivo; sus STL propios los escribe quien llama
        from .accessories import place_beside

        placed = place_beside(asm, list(extras))
        names = list(extras_names or [])
        placed_extras = [(names[i] if i < len(names) else f"accesorio_{i + 1}", m) for i, m in enumerate(placed)]
        asm = trimesh.util.concatenate([asm] + placed)
        info["accesorios_en_ensamblada"] = len(extras)
    asm.export(os.path.join(outdir, f"{name}_ensamblada.stl"))
    info.update({"ensamblada": f"{name}_ensamblada.stl", "piezas": sorted(res.pieces)})
    if colorize is not None:
        say("Escribiendo los archivos de color…")
        info["color"] = colorize.write(outdir, name, T, accesorios_sin_color=len(extras or []), extras=placed_extras)
    say("Escribiendo la lista de piezas…")
    lst = parts_list(res, name)
    if lst is not None:
        import json
        with open(os.path.join(outdir, f"{name}_lista_de_piezas.json"), "w", encoding="utf-8") as fh:
            json.dump(lst, fh, ensure_ascii=False, indent=2)
        with open(os.path.join(outdir, f"{name}_lista_de_piezas.md"), "w", encoding="utf-8") as fh:
            fh.write(parts_list_markdown(lst))
        info["lista_de_piezas"] = f"{name}_lista_de_piezas.json"
    return info
