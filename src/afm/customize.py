"""Dividir el modelo y ajustar piezas crudas antes de regenerar las uniones.

Sólo se usa con escalas por pieza o pelvis de catálogo sin torso de catálogo.
El flujo histórico sigue intacto cuando no se solicita ninguno de esos cambios.
"""
from __future__ import annotations

import copy
from dataclasses import replace

import numpy as np

from .assembly import AssemblyError, PartChoice, place_part, synthesize_host, transform_choice
from .ports import PortFace, PortFrame


def needed(project) -> bool:
    return "torso" not in project.parts and bool(project.part_scales or "pelvis" in project.parts)


def scale_choice(choice: PartChoice, factor: float) -> PartChoice:
    """Escala uniforme alrededor de su unión principal; conserva medidas de pasadores."""
    k = float(factor)
    if not np.isfinite(k) or k <= 0:
        raise AssemblyError("la escala de una pieza debe ser finita y mayor que cero")
    meta = copy.deepcopy(choice.meta)
    primary = {"brazo": "hombro", "pierna": "cadera", "pelvis": "cintura", "torso_sup": "cintura",
               "torso": "cuello", "cabeza": "cuello"}.get(meta.tipo)
    anchor = meta.puertos[primary].frame.center if primary in meta.puertos else np.zeros(3)
    T = np.eye(4)
    T[:3, :3] *= k
    T[:3, 3] = anchor * (1.0 - k)
    mesh = choice.mesh.copy()
    mesh.apply_transform(T)
    for port in meta.puertos.values():
        f = port.frame
        port.frame = PortFrame(anchor + k * (f.center - anchor), f.axis.copy(), f.ref.copy())
        if port.face is not None:
            from shapely.affinity import scale
            port.face = PortFace.from_shape(scale(port.face.shape(), k, k, origin=(0, 0)))
    from .joints import JointSpec
    meta.articulaciones = [JointSpec.from_dict(j).transformed(T).to_dict() for j in meta.articulaciones]
    # La altura identifica la línea, no el tamaño de una pieza dentro de esa línea.
    meta.origen = {**meta.origen, "escala_pieza": k}
    return PartChoice(meta, mesh)


def choices_for_model(body, project, replacements=None) -> dict:
    """Conserva todas las piezas originales salvo las elegidas explícitamente en el catálogo."""
    from .extract import extract_all
    from .preflight import joint_conflicts

    replacements = dict(replacements or {})
    if "torso" in replacements:
        raise AssemblyError("la escala por pieza ajusta el modelo: quitá el torso de catálogo para usarla")
    if ("pelvis" in replacements or "pelvis" in project.part_scales) and project.waist is None:
        raise AssemblyError("definí el plano de cintura para separar la pelvis del torso del modelo")
    kinds = [l.kind for l in project.limbs]
    if len(kinds) != len(set(kinds)):
        raise AssemblyError("dividir y escalar admite un brazo y una pierna de trabajo; el otro lado se espeja")
    places = {"torso", "pelvis", *(l.name for l in project.limbs)}
    for place, k in project.part_scales.items():
        if place not in places:
            raise AssemblyError(f"«{place}»: no es una pieza del modelo para escalar")
        if not np.isfinite(k) or k <= 0:
            raise AssemblyError(f"«{place}»: la escala debe ser finita y mayor que cero")
        # Una escala guardada queda inactiva al elegir una pieza de catálogo.
    raw = extract_all(body, project, "modelo", places=["torso", "brazo", "pierna", "cabeza"])
    choices = {e.meta.tipo: PartChoice(e.meta, e.mesh) for e in raw.pieces}
    # Recortes del torso: los miembros ya salen recortados de extract_all.
    from . import solids as S
    from .trims import apply_trims
    for key in ("torso", "torso_sup", "pelvis"):
        if key in choices:
            ch = choices[key]
            half = S.to_manifold(ch.mesh).trim_by_plane((1., 0., 0.), 0.)
            cut = apply_trims(half, project.trims, "torso")
            if project.params.torso_mirror:
                ch.mesh = S.to_printable_trimesh(S.union(cut, S.mirror_x(cut)))
            elif project.trims:
                other = S.mirror_x(S.to_manifold(ch.mesh)).trim_by_plane((1., 0., 0.), 0.)
                ch.mesh = S.to_printable_trimesh(S.union(cut, S.mirror_x(apply_trims(other, project.trims, "torso"))))
    keys = {"torso": "torso_sup" if project.waist else "torso", "pelvis": "pelvis"}
    keys.update({l.name: {"arm": "brazo", "leg": "pierna"}[l.kind] for l in project.limbs})
    for place, k in project.part_scales.items():
        key = keys[place]
        if place not in replacements:
            choices[key] = scale_choice(choices[key], k)
    for place, ch in replacements.items():
        key = keys.get(place, place)
        spec = next((l for l in project.limbs if l.name == place), None)
        if spec is not None:
            conflicts = joint_conflicts(ch, spec.kind, place, project.joints)
            if conflicts:
                raise AssemblyError("; ".join(conflicts))
            ch = copy.deepcopy(ch)
            if not ch.meta.articulaciones:
                ch.meta.articulaciones = [j.to_dict() for j in project.joints if j.limb == place]
        choices[key] = ch
    return choices


def prepare(body, project, replacements=None):
    """Huésped mixto listo para build; conserva nombres, mecanismos y opciones del proyecto."""
    choices = choices_for_model(body, project, replacements)
    host = synthesize_host(choices, project.params)
    for spec in host.limbs:
        old = next(l for l in project.limbs if l.kind == spec.kind)
        key = spec.name
        spec.name = old.name
        host.faces[old.name] = host.faces.pop(key)
        host.parts[old.name] = host.parts.pop(key)
        spec.mechanism = old.mechanism
        spec.detent = copy.deepcopy(old.detent)
        spec.sweep_deg = old.sweep_deg
        # El JSON del catálogo redondea los centros. Un corrimiento submicrométrico puede llevar
        # vértices de la simetría al lado opuesto y producir una falsa interpenetración del par.
        from .assembly import host_frame
        from .ports import coupling_transform
        ch = host.parts[old.name]
        T = coupling_transform(ch.port(spec.kind).frame, host_frame(host.faces[old.name]))
        ch = transform_choice(ch, T)
        vertices = ch.mesh.vertices.copy()
        vertices[np.abs(vertices[:, 0]) < 1e-6, 0] = 0.0
        ch.mesh.vertices = vertices
        host.parts[old.name] = ch
    if host.head is not None and project.head is not None:
        # Traslada el cuello junto al torso, sin escalar los pasadores ni la cabeza.
        neck = choices["torso_sup" if project.waist else "torso"].meta.puertos["cuello"]
        pc = None if project.head.pin_center is None else tuple(neck.frame.center)
        host.head = replace(project.head, plane=host.head.plane, pin_center=pc)
        # La cabeza original ya fue colocada en el cuello por synthesize_host: no es un reemplazo de catálogo.
        if "cabeza" not in (replacements or {}):
            host.parts.pop("cabeza", None)
    if host.waist is not None and project.waist is not None:
        host.waist = replace(project.waist, plane=host.waist.plane)
    return host, choices


def build_model(body, project, replacements=None, progress=None):
    from . import pipeline as P
    from .hand import WristFrame
    from .ports import Port, face_from_mesh
    from . import solids as S

    # Con mano de reemplazo se corta la mano original antes de escalar el brazo:
    # la muñeca viaja como un puerto y attach_hand utiliza sus medidas actualizadas.
    host, choices = prepare(body, project, replacements)
    hand = project.hand
    if hand is not None and hand.points:
        from .hand import split_at_wrist
        arm = next((l for l in host.limbs if l.kind == "arm"), None)
        if arm is not None and "muneca" not in host.parts[arm.name].meta.puertos:
            ch = host.parts[arm.name]
            old = next(l for l in project.limbs if l.kind == "arm")
            k = project.part_scales.get(old.name, 1.) if old.name not in (replacements or {}) else 1.
            anchor = ch.port("arm").frame.center
            pts = anchor + k * (np.asarray(hand.points) - anchor)
            ws = split_at_wrist(S.to_manifold(ch.mesh), WristFrame.from_points(pts), invert=hand.invert)
            ch.mesh = S.to_printable_trimesh(ws.rest)
            frame = PortFrame.from_wrist(ws.frame)
            ch.meta.puertos["muneca"] = Port("muneca", "toma", frame, face_from_mesh(ch.mesh, frame))
    res = P.build(host.body, host.limbs, project.params, None, True, progress, None, host.head, None,
                  host.waist, hand, host.parts, host_faces=host.faces)
    res.report["ajustes_piezas"] = {"escalas": dict(project.part_scales),
                                    "origen": {k: "catalogo" if k in (replacements or {}) else "modelo"
                                               for k in ["torso", "pelvis", *(l.name for l in project.limbs)]}}
    return res, host.limbs


def preview_model(body, project, replacements=None):
    """Vista de todas las piezas separadas, usando el mismo huésped que la generación."""
    from .preflight import PartPreview, _overlay_shapes, scale_hint_for
    from .assembly import face_of_triangles, host_frame
    from .ports import port_compat

    host, choices = prepare(body, project, replacements)
    previews = {}
    for spec in host.limbs:
        ch = host.parts[spec.name]
        face = host.faces[spec.name]
        pp = place_part(ch, spec.kind, face, project.params, project.params.ref_height)
        mesh = ch.mesh.copy()
        mesh.apply_transform(pp.T)
        pv = PartPreview(spec.name, ch.meta.ref, kind=spec.kind, mesh=mesh, compat=pp.compat,
                         nivel=pp.compat.nivel if pp.compat else "ok")
        hpf = face_of_triangles(face, host_frame(face))
        pv.part_shape, pv.host_shape = _overlay_shapes(ch.port(spec.kind), hpf)
        pv.scale_hint = scale_hint_for(ch.port(spec.kind).face, hpf)
        previews[spec.name] = pv
    upper = choices.get("torso_sup", choices.get("torso"))
    previews["torso"] = PartPreview("torso", upper.meta.ref, mesh=upper.mesh)
    if "pelvis" in choices:
        lower = choices["pelvis"]
        from .ports import coupling_transform
        cp = upper.meta.puertos["cintura"]
        lp = lower.meta.puertos["cintura"]
        aligned = transform_choice(lower, coupling_transform(lp.frame, cp.frame))
        compat = port_compat(lp, cp, lower.meta.altura_figura_mm, upper.meta.altura_figura_mm, project.params)
        pv = PartPreview("pelvis", lower.meta.ref, mesh=aligned.mesh, compat=compat, nivel=compat.nivel)
        pv.part_shape, pv.host_shape = _overlay_shapes(lp, cp.face)
        pv.scale_hint = scale_hint_for(lp.face, cp.face)
        previews["pelvis"] = pv
    if host.head is not None:
        from .ports import coupling_transform
        ch = choices["cabeza"]
        T = coupling_transform(ch.port("head").frame, upper.meta.puertos["cuello"].frame)
        head_mesh = ch.mesh.copy()
        head_mesh.apply_transform(T)
        previews["cabeza"] = PartPreview("cabeza", ch.meta.ref, mesh=head_mesh,
                                          notes=["El cuello se valida al generar."])
    if "mano" in choices:
        previews["mano"] = PartPreview("mano", choices["mano"].meta.ref,
                                        notes=["La muñeca se valida al generar."])
    return previews
