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


# Piezas simétricas respecto de x = 0: se escalan con ese plano fijo (si no, la escala en X las correría de la simetría).
BILATERAL = {"torso", "torso_sup", "pelvis", "cabeza"}
# Puerto de cada tipo de pieza alrededor del cual se escala (el resto de las uniones se vuelve a acoplar al armar).
SCALE_ANCHOR = {"brazo": "hombro", "pierna": "cadera", "pelvis": "cintura", "torso_sup": "cintura",
                "torso": "cuello", "cabeza": "cuello"}
AXIS_NAMES = ("X", "Y", "Z")


def scale_vector(value) -> np.ndarray:
    """Escala de una pieza como vector (sx, sy, sz): un número es uniforme; una lista de 3 es por eje (X lateral,
    Y adelante-atrás, Z alto, del marco del modelo). Todos finitos y mayores que cero."""
    k = np.asarray(value, float).reshape(-1)
    if k.size == 1:
        k = np.repeat(k, 3)
    if k.size != 3 or not np.all(np.isfinite(k)) or np.any(k <= 0):
        raise AssemblyError("la escala de una pieza son 1 o 3 números (x, y, z) finitos y mayores que cero")
    return k


def normalize_scale(value):
    """Forma que se guarda en el proyecto: `float` si los tres ejes son iguales, si no `[sx, sy, sz]`."""
    k = scale_vector(value)
    if np.allclose(k, k[0], rtol=0, atol=1e-12):
        return float(k[0])
    return [float(x) for x in k]


def is_identity(value) -> bool:
    return bool(np.allclose(scale_vector(value), 1.0, rtol=0, atol=1e-9))


def _scale_face(face: PortFace, old: PortFrame, new: PortFrame, T: np.ndarray) -> PortFace:
    """La cara de unión llevada por la transformación afín `T`: sus puntos (u, v) pasan al mundo con el marco viejo, por `T`,
    y vuelven a (u, v) con el marco nuevo."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    def move(uv):
        uv = np.asarray(uv, float).reshape(-1, 2)
        world = old.center + uv[:, :1] * old.ref + uv[:, 1:2] * old.y
        world = world @ T[:3, :3].T + T[:3, 3]
        return new.to_uv(world)

    polys = [Polygon(move(q["exterior"]), [move(r) for r in (q.get("interiors") or [])]).buffer(0) for q in face.polygons]
    return PortFace.from_shape(unary_union(polys))


def _scale_joint(j, T: np.ndarray, L: np.ndarray):
    """Articulación llevada por la escala `T` (afín, con parte lineal diagonal `L`). Sólo se mueven el centro y la
    orientación del eje: la bisagra (pasador, lengüeta, holgura) son milímetros de impresión y no se tocan."""
    import dataclasses
    from .planes import Plane
    inv_t = np.linalg.inv(L).T
    n = inv_t @ np.asarray(j.plane.normal, float)
    o = L @ np.asarray(j.plane.origin, float) + T[:3, 3]
    ax = L @ np.asarray(j.axis_dir, float)
    ax = ax / np.linalg.norm(ax)
    flip = j.flex_sign
    if ax[0] < 0:                       # el motor toma el eje con x >= 0
        ax, flip = -ax, -j.sign()
    return dataclasses.replace(j, plane=Plane.from_normal(o, n / np.linalg.norm(n), j.plane.name),
                               axis_center=tuple(float(x) for x in L @ np.asarray(j.axis_center, float) + T[:3, 3]),
                               axis_dir=tuple(float(x) for x in ax), flex_sign=flip)


def scale_anchor(meta) -> np.ndarray:
    """Punto fijo de la escala de una pieza: el centro de su unión principal (X = 0 en las piezas simétricas)."""
    port = meta.puertos.get(SCALE_ANCHOR.get(meta.tipo, ""))
    anchor = np.array(port.frame.center, float) if port is not None else np.zeros(3)
    if meta.tipo in BILATERAL:
        anchor[0] = 0.0
    return anchor


def scale_choice(choice: PartChoice, factor) -> PartChoice:
    """Escala una pieza alrededor de su unión principal, uniforme (`1.1`) o por eje (`(sx, sy, sz)` en X, Y, Z del modelo).

    Los puertos (centro, eje, referencia de giro y cara de unión) y las articulaciones acompañan a la malla; las medidas de
    pasadores y bisagras son milímetros de impresión y no se escalan: se regeneran al armar."""
    k = scale_vector(factor)
    meta = copy.deepcopy(choice.meta)
    anchor = scale_anchor(meta)
    L = np.diag(k)
    T = np.eye(4)
    T[:3, :3] = L
    T[:3, 3] = anchor - L @ anchor
    mesh = choice.mesh.copy()
    mesh.apply_transform(T)
    inv_t = np.linalg.inv(L).T
    for port in meta.puertos.values():
        f = port.frame
        axis = inv_t @ f.axis                           # la normal de la cara se transforma con la inversa traspuesta
        axis /= np.linalg.norm(axis)
        ref = L @ f.ref
        ref = ref - axis * float(ref @ axis)
        ref /= np.linalg.norm(ref)
        new = PortFrame(L @ f.center + T[:3, 3], axis, ref)
        if port.face is not None:
            port.face = _scale_face(port.face, f, new, T)
        port.frame = new
    from .joints import JointSpec
    meta.articulaciones = [_scale_joint(JointSpec.from_dict(j), T, L).to_dict() for j in meta.articulaciones]
    # La altura identifica la línea, no el tamaño de una pieza dentro de esa línea.
    meta.origen = {**meta.origen, "escala_pieza": normalize_scale(k)}
    return PartChoice(meta, mesh)


def fit_scale(part_face: PortFace | None, host_face: PortFace | None, frame: PortFrame) -> np.ndarray | None:
    """Escala (sx, sy, sz) que lleva el ANCHO y el ALTO de `host_face` a los de `part_face`, cada uno en su dirección.

    Las dos caras están en (u, v) del mismo puerto (u = `frame.ref`, v = `frame.y`; la cara del modelo ya viene espejada
    en v, como la compara `face_iou`). Cada eje del modelo recibe la media geométrica de los factores de u, v y de la
    normal de la cara (que queda en 1: no se toca el espesor), ponderados por cuánto ocupa cada dirección del puerto ese
    eje. Si el puerto no está alineado con los ejes, el factor se reparte entre ellos. Devuelve `None` si falta alguna cara
    o está degenerada."""
    if part_face is None or host_face is None:
        return None
    pb, hb = part_face.shape().bounds, host_face.shape().bounds
    hw, hh = hb[2] - hb[0], hb[3] - hb[1]
    if hw <= 1e-9 or hh <= 1e-9:
        return None
    ku, kv = (pb[2] - pb[0]) / hw, (pb[3] - pb[1]) / hh
    w = np.stack([frame.ref ** 2, frame.y ** 2, frame.axis ** 2])         # (3 direcciones del puerto, 3 ejes del modelo)
    logs = np.log([ku, kv, 1.0])
    return np.exp(logs @ w / w.sum(axis=0))


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
    places = {"torso", "pelvis", *(l.name for l in project.limbs)} | ({"cabeza"} if project.head is not None else set())
    for place, k in project.part_scales.items():
        if place not in places:
            raise AssemblyError(f"«{place}»: no es una pieza del modelo para escalar")
        try:
            scale_vector(k)
        except AssemblyError as exc:
            raise AssemblyError(f"«{place}»: {exc}") from exc
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
    keys = {"torso": "torso_sup" if project.waist else "torso", "pelvis": "pelvis", "cabeza": "cabeza"}
    keys.update({l.name: {"arm": "brazo", "leg": "pierna"}[l.kind] for l in project.limbs})
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
    # La escala es de la pieza que ocupa cada lugar: la del modelo o, si se eligió, la de catálogo.
    for place, k in project.part_scales.items():
        key = keys[place]
        if key in choices and not is_identity(k):
            choices[key] = scale_choice(choices[key], k)
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
    if progress:
        progress("Armando el cuerpo base…")
    host, choices = prepare(body, project, replacements)
    hand = project.hand
    if hand is not None and hand.points:
        from .hand import split_at_wrist
        arm = next((l for l in host.limbs if l.kind == "arm"), None)
        if arm is not None and "muneca" not in host.parts[arm.name].meta.puertos:
            ch = host.parts[arm.name]
            old = next(l for l in project.limbs if l.kind == "arm")
            k = scale_vector(project.part_scales.get(old.name, 1.)) if old.name not in (replacements or {}) else np.ones(3)
            anchor = ch.port("arm").frame.center
            pts = anchor + k * (np.asarray(hand.points) - anchor)
            ws = split_at_wrist(S.to_manifold(ch.mesh), WristFrame.from_points(pts), invert=hand.invert)
            ch.mesh = S.to_printable_trimesh(ws.rest)
            frame = PortFrame.from_wrist(ws.frame)
            ch.meta.puertos["muneca"] = Port("muneca", "toma", frame, face_from_mesh(ch.mesh, frame))
    res = P.build(host.body, host.limbs, project.params, None, True, progress, None, host.head, None,
                  host.waist, hand, host.parts, host_faces=host.faces)
    res.report["ajustes_piezas"] = {"escalas": {k: normalize_scale(v) for k, v in project.part_scales.items()},
                                    "origen": {k: "catalogo" if k in (replacements or {}) else "modelo"
                                               for k in ["torso", "pelvis", *(l.name for l in project.limbs)]}}
    return res, host.limbs


def preview_model(body, project, replacements=None):
    """Vista de todas las piezas separadas, usando el mismo huésped que la generación."""
    from .preflight import PartPreview, _overlay_shapes, _xyz_hint, scale_hint_for
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
        pv.scale_xyz = _xyz_hint(ch.port(spec.kind).face, hpf, pp.port.frame)
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
        pv.scale_xyz = _xyz_hint(lp.face, cp.face, cp.frame)
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
