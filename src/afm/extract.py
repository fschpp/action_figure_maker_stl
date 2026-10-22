"""Extracción de piezas crudas con puertos desde una figura ya configurada (plan de piezas intercambiables, Fase 3, §4.1).

Corre solo la etapa de corte de `pipeline.build` sobre el cuerpo y su `Project` (planos, siluetas, cuello, cintura,
muñeca que ya existen: no hacen falta clics nuevos) y devuelve cada pieza **cruda**: sin pasadores, cavidades, clic ni
lengüetas (eso se genera al armar, con los parámetros de la línea). Recortes manuales, silueta, 2.º corte, `cap_*`,
rebaba y `pin_center` quedan horneados en la malla.

Convenciones:

* Mm reales, marco de la figura de origen (Z arriba, +Y frente, plano de simetría en x = 0). Lado de trabajo +X; la
  derecha sale por espejo (`espejable`).
* Brazo y pierna: puerto `hombro` / `cadera` (enchufe), `axis = ContactFace.outward` (del miembro hacia el torso), centro =
  centro del pasador, `ref` = +Z (en un eje vertical, +Y). Si se dan los 3 puntos de muñeca el brazo sale **sin mano**
  (puerto `muneca`, toma) y la mano sale aparte como pieza `mano` en el marco estándar (origen en la muñeca).
* Torso: lo que queda del cuerpo (entero, ambos lados) sin los miembros ni la cabeza. Sus puertos son los complementarios
  de los de las piezas (mismo centro y `ref`, eje opuesto): `coupling_transform(puerto_del_miembro, puerto_del_torso)` es
  la identidad. Con cintura se parte por el plano de la cintura en `torso_sup` y `pelvis`, sin holgura.
* El torso es `half − miembro` (el miembro tal cual salió de `separate_limb`): al restarle luego el barrido del miembro da
  el mismo torso que `pipeline.build`.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field

import numpy as np

from . import meshio, solids as S
from .hand import HandError, WristFrame, to_local
from .head import HeadSpec, neck_plane, split_head
from .limbs import LimbSpec, limb_prism, separate_limb
from .params import Params
from .parts import PieceMeta, make_origin
from .pipeline import _sweep_range, align_to_symmetry
from .planes import Plane
from .ports import Port, PortError, PortFace, PortFrame, centroid_of_face_triangles, face_from_mesh
from .project import Project
from .waist import waist_plane

KIND_PORT = {"arm": "hombro", "leg": "cadera"}
PLACES = ("cabeza", "torso", "brazo", "pierna", "mano")
SIDE_PORTS = {"hombro": "brazo", "cadera": "pierna"}


class ExtractError(ValueError):
    """No se pudo extraer una pieza (lugar que el proyecto no define, plano que no corta, etc.)."""


@dataclass
class Extracted:
    meta: PieceMeta
    mesh: object                 # trimesh estanco, listo para `Catalog.add_piece`


@dataclass
class ExtractResult:
    pieces: list = field(default_factory=list)       # list[Extracted]
    warnings: list = field(default_factory=list)

    def get(self, tipo: str) -> Extracted | None:
        return next((e for e in self.pieces if e.meta.tipo == tipo), None)


# ------------------------------------------------------------------ utilidades
def load_body(stl_path: str, project: Project, progress=None):
    """Cuerpo normalizado (altura, frente y simetría del proyecto), igual que `cli.run`. Devuelve (Manifold, LoadReport)."""
    mesh, rep = meshio.load_figure(stl_path, project.params.ref_height, project.crown_z)
    if progress:
        for w in rep.warnings:
            progress(f"AVISO: {w}")
    if project.front == "-Y":
        mesh.apply_transform([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    mesh = align_to_symmetry(mesh, project.symmetry)
    return S.to_manifold(mesh), rep


def reference_for(axis, prefer=(0.0, 0.0, 1.0)) -> np.ndarray:
    """Referencia de giro: `prefer` proyectada sobre el plano del puerto (si el eje es casi paralelo, +Y, luego +X)."""
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    for cand in (prefer, (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)):
        r = np.asarray(cand, float)
        r = r - (r @ axis) * axis
        n = float(np.linalg.norm(r))
        if n > 0.2:
            return r / n
    raise PortError("no se pudo elegir una referencia de giro")                 # pragma: no cover


def _flip_v(face: PortFace | None) -> PortFace | None:
    """La misma cara vista desde el puerto vecino: con ejes opuestos y la misma `ref`, v = axis × ref cambia de signo."""
    if face is None:
        return None

    def f(ring):
        return [[u, -v] for u, v in ring]

    polys = [{"exterior": f(p["exterior"]), "interiors": [f(r) for r in p["interiors"]]} for p in face.polygons]
    return PortFace(polys, face.area, face.radius_eq)


def complement(port: Port, clase: str | None = None) -> Port:
    """Puerto del vecino: mismo centro y referencia, eje opuesto, rol complementario y la cara con v invertida."""
    from .ports import COMPLEMENT

    fr = port.frame
    return Port(clase or port.clase, COMPLEMENT[port.rol], PortFrame(fr.center.copy(), -fr.axis, fr.ref.copy()),
                _flip_v(port.face), port.sweep_deg)


def mirror_port(port: Port) -> Port:
    """Puerto del lado −X (pieza espejada con `mirror_x`): se refleja el marco y la cara cambia de signo en v."""
    fr = port.frame
    m = np.array([-1.0, 1.0, 1.0])
    return Port(port.clase, port.rol, PortFrame(fr.center * m, fr.axis * m, fr.ref * m), _flip_v(port.face), port.sweep_deg)


HEAL_VOLUME_TOL = 5e-4          # fracción: lo que puede cambiar el volumen al curar una arista no-variedad


def _clean(mf, label: str, warnings: list):
    """Malla estanca de una pieza cruda (lo que se guarda en el STL).

    El motor deja a veces una arista con 4 caras donde el miembro roza su propio corte (pasa en la pierna de Güemes aun en
    `pipeline.build`, que solo lo avisa): el catálogo exige mallas cerradas, así que se cura con pymeshfix y se acepta solo
    si el volumen cambia menos de `HEAL_VOLUME_TOL`. Si no se puede, se devuelve la malla tal cual y `write_piece` lo rechaza."""
    tm = S.to_printable_trimesh(mf)
    if tm.is_watertight:
        return tm
    fixed = meshio._meshfix(tm)
    ref = abs(float(tm.volume))
    if fixed is not None and ref > 0 and abs(abs(float(fixed.volume)) - ref) / ref <= HEAL_VOLUME_TOL:
        warnings.append(f"{label}: se curó una arista no-variedad (pymeshfix; el volumen cambió {abs(abs(float(fixed.volume)) - ref) / ref:.4%})")
        return fixed
    warnings.append(f"{label}: la malla no es estanca (aristas no-variedad) y no se pudo curar")
    return tm


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "figura"


def _meta(tipo: str, prefix: str, ports: dict, p: Params, origin: dict, side: str, mirrorable: bool, **kw) -> PieceMeta:
    return PieceMeta(id=f"{tipo.replace('_', '-')}-{prefix}-01", version=1, tipo=tipo, puertos=ports, origen=origin,
                     altura_figura_mm=p.ref_height, lado=side, espejable=mirrorable, **kw)


# ------------------------------------------------------------------ extracción
def _pick_limbs(project: Project, warnings: list) -> dict:
    """Primer miembro de cada clase (brazo, pierna); los demás se avisan y se ignoran."""
    out: dict[str, LimbSpec] = {}
    for spec in project.limbs:
        if spec.kind not in KIND_PORT:
            warnings.append(f"«{spec.name}»: clase de miembro «{spec.kind}» sin puerto de catálogo; se ignora")
        elif spec.kind in out:
            warnings.append(f"«{spec.name}»: ya se extrajo «{out[spec.kind].name}» como {KIND_PORT[spec.kind]}; se ignora")
        else:
            out[spec.kind] = spec
    return out


def extract_all(body: S.Manifold, project: Project, prefix: str = "figura", places=None, wrist_points=None,
                invert_wrist: bool = False, figure: str = "", project_name: str = "") -> ExtractResult:
    """Piezas crudas con puertos de una figura configurada.

    `places`: subconjunto de `PLACES` (por defecto todos los que el proyecto permite). `wrist_points`: 3 puntos de la muñeca
    izquierda (marco de trabajo): el brazo sale sin mano y la mano como pieza `mano`."""
    p = project.params
    want = set(places or PLACES)
    bad = want - set(PLACES)
    if bad:
        raise ExtractError(f"lugar(es) desconocido(s): {', '.join(sorted(bad))} (hay {', '.join(PLACES)})")
    if wrist_points is not None:
        wrist_points = np.asarray(wrist_points, float).reshape(-1, 3)         # acepta 9 números seguidos o 3 puntos
    res = ExtractResult()
    origin = make_origin(figure, project_name)
    limbs = _pick_limbs(project, res.warnings)

    # --- cabeza y mitad de trabajo sin cabeza (cortada exactamente por el plano del cuello, sin holgura)
    half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    head_port = None
    neck = None
    if project.head is not None:
        neck = neck_plane(project.head)
        hs = dataclasses.replace(project.head, replacement_path=None)       # la cabeza original, no la de reemplazo
        split = split_head(body, hs, p)
        n_fr = PortFrame(split.face.center.copy(), split.face.outward.copy(), reference_for(split.face.outward, (0.0, 1.0, 0.0)))
        head_tm = _clean(split.head, "cabeza", res.warnings)
        head_port = Port("cuello", "enchufe", n_fr, face_from_mesh(head_tm, n_fr))
        if "cabeza" in want:
            res.pieces.append(Extracted(_meta("cabeza", prefix, {"cuello": head_port}, p, origin, "centro", False,
                                              descripcion=f"Cabeza de {prefix}"), head_tm))
        below = S.halfspace(Plane.from_normal(neck.origin, -neck.normal))
        half = S.intersection(half, below)
    elif "cabeza" in want:
        res.warnings.append("el proyecto no define el plano del cuello: no hay cabeza para extraer")

    # --- miembros
    ports_by_place: dict[str, Port] = {}        # puerto del miembro (lado +X), por clase
    cut_limbs: dict[str, S.Manifold] = {}
    hand_extract = None
    for kind, spec in limbs.items():
        limb, face = separate_limb(half, spec, p.burr)
        from .trims import apply_trims
        limb = apply_trims(limb, project.trims, spec.name)
        cut_limbs[kind] = limb
        frame = PortFrame(face.center.copy(), face.outward / np.linalg.norm(face.outward), reference_for(face.outward))
        place = "brazo" if kind == "arm" else "pierna"
        tm = _clean(limb, place, res.warnings)
        port = Port(KIND_PORT[kind], "enchufe", frame, face_from_mesh(tm, frame), tuple(float(x) for x in _sweep_range(spec, p)))
        ports_by_place[KIND_PORT[kind]] = port
        mesh, ports = tm, {port.clase: port}
        if kind == "arm" and wrist_points is not None:
            try:
                from .hand import split_at_wrist
                ws = split_at_wrist(limb, WristFrame.from_points(wrist_points), invert=invert_wrist)
            except HandError as exc:
                raise ExtractError(f"muñeca: {exc}") from exc
            wf = PortFrame.from_wrist(ws.frame, toward_hand=True)
            mesh = _clean(ws.rest, "brazo (sin mano)", res.warnings)
            ports["muneca"] = Port("muneca", "toma", wf, face_from_mesh(mesh, wf))
            hf = PortFrame.hand_standard()
            hand_local = _clean(to_local(ws.hand, ws.frame), "mano", res.warnings)
            hand_extract = Extracted(_meta("mano", prefix, {"muneca": Port("muneca", "enchufe", hf, face_from_mesh(hand_local, hf))},
                                           p, origin, "izq", True, descripcion=f"Mano de {prefix}"), hand_local)
        if place in want:
            meta = _meta(place, prefix, ports, p, origin, "izq", True, descripcion=f"{place.capitalize()} de {prefix}")
            meta.articulaciones = [j.to_dict() for j in project.joints if j.limb == spec.name]
            if spec.mechanism is not None:
                meta.mecanismo = {"mechanism": spec.mechanism}
            res.pieces.append(Extracted(meta, mesh))
    if "brazo" in want and "arm" not in limbs:
        res.warnings.append("el proyecto no define un brazo: no hay brazo para extraer")
    if "pierna" in want and "leg" not in limbs:
        res.warnings.append("el proyecto no define una pierna: no hay pierna para extraer")
    if "mano" in want:
        if hand_extract is not None:
            res.pieces.append(hand_extract)
        else:
            res.warnings.append("sin los 3 puntos de la muñeca no se extrae la mano (y el brazo conserva la suya)")

    # --- torso: el cuerpo sin los miembros ni la cabeza, entero
    if "torso" in want:
        torso = _whole_torso(body, half, neck, limbs, cut_limbs, p, res.warnings)
        _torso_pieces(res, torso, project, prefix, origin, ports_by_place, head_port)
    return res


def _closable(mf) -> bool:
    """¿La malla soldada es estanca, o `_clean` la cura sin cambiar el volumen?"""
    tm = S.to_printable_trimesh(mf)
    if tm.is_watertight:
        return True
    fixed = meshio._meshfix(tm)
    ref = abs(float(tm.volume))
    return fixed is not None and ref > 0 and abs(abs(float(fixed.volume)) - ref) / ref <= HEAL_VOLUME_TOL


TORSO_GAPS_MM = (0.0, 0.002, 0.03, 0.1)    # separación extra torso-miembro que se prueba si el corte deja aristas de 4 caras


def _whole_torso(body: S.Manifold, half: S.Manifold, neck, limbs: dict, cut_limbs: dict, p: Params, warnings: list) -> S.Manifold:
    """Torso entero: la figura sin cabeza ni miembros.

    * `torso_mirror`: como `pipeline.build`, se queda con el lado +X sin los miembros y se espeja (lo que tenga el lado −X
      se descarta).
    * sin `torso_mirror`: el lado −X sale del cuerpo real, sin lo que cae dentro del prisma de cada miembro espejado.

    Donde el miembro roza una arista del torso (la cadera de Güemes) el corte deja una arista de 4 caras y la malla no es
    estanca. Se prueba restar el miembro ensanchado `TORSO_GAPS_MM` (2 µm, 30 µm, 0,1 mm: muy por debajo de la resolución de
    impresión y de la holgura, que se vuelve a tallar al armar) y se queda con el primero que cierra. Como último recurso, con
    `torso_mirror`, se resta de la figura entera (conserva la diferencia entre lados si la hay)."""
    cut = body if neck is None else S.intersection(body, S.halfspace(Plane.from_normal(neck.origin, -neck.normal)))
    other = None
    if not p.torso_mirror:
        other = S.mirror_x(body).trim_by_plane((1.0, 0.0, 0.0), 0.0)
        if neck is not None:
            other = S.intersection(other, S.halfspace(Plane.from_normal(neck.origin, -neck.normal)))
        for spec in limbs.values():
            other = S.difference(other, limb_prism(spec))

    def candidate(eps: float, whole_body: bool) -> S.Manifold:
        grown = [S.dilate(l, eps) if eps else l for l in cut_limbs.values()]
        if whole_body:
            t = cut
            for g in grown:
                t = S.difference(t, g, S.mirror_x(g))
            return S.drop_small_components(t, 0.02)
        t_half = half
        for g in grown:
            t_half = S.difference(t_half, g)
        return S.drop_small_components(S.union(t_half, S.mirror_x(t_half) if other is None else S.mirror_x(other)), 0.02)

    last = None
    for whole_body in ((False, True) if p.torso_mirror else (False,)):
        for eps in TORSO_GAPS_MM:
            last = candidate(eps, whole_body)
            if _closable(last):
                if eps:
                    warnings.append(f"torso: el corte rozaba una arista (aristas de 4 caras); se separó {eps:g} mm de los miembros para cerrar la malla")
                if whole_body:
                    warnings.append("torso: se restaron los miembros de la figura entera, no de la mitad espejada "
                                    "(si el lado −X del cuerpo no es idéntico al +X, el torso conserva esa diferencia)")
                return last
    return last


def _torso_pieces(res: ExtractResult, torso: S.Manifold, project: Project, prefix: str, origin: dict, limb_ports: dict,
                  head_port: Port | None) -> None:
    p = project.params
    tm = _clean(torso, "torso", res.warnings)
    common: dict[str, Port] = {}
    if head_port is not None:
        c = complement(head_port)
        common["cuello"] = Port("cuello", "toma", c.frame, face_from_mesh(tm, c.frame))   # la cara real: el torso viene cortado por el plano
    else:
        res.warnings.append("sin plano del cuello el torso no tiene puerto «cuello» (queda con la cabeza original)")

    def limb_pair(clase: str) -> dict:
        lp = limb_ports.get(clase)
        if lp is None:
            return {}
        left = complement(lp)
        return {f"{clase}_izq": left, f"{clase}_der": mirror_port(left)}

    sides = {**limb_pair("hombro"), **limb_pair("cadera")}
    if project.waist is None:
        ports = {**common, **sides}
        res.pieces.append(Extracted(_meta("torso", prefix, ports, p, origin, "centro", False,
                                          descripcion=f"Torso de {prefix} (entero, sin miembros ni cabeza)"), tm))
        return
    plane = waist_plane(project.waist)
    n = plane.normal
    upper = S.intersection(torso, S.halfspace(plane))
    lower = S.intersection(torso, S.halfspace(Plane.from_normal(plane.origin, -n)))
    if upper.is_empty() or lower.is_empty():
        raise ExtractError("el plano de la cintura no parte el torso (¿está fuera de la figura?)")
    pre = PortFrame(plane.origin.copy(), -n, reference_for(-n, (0.0, 1.0, 0.0)))           # el de arriba mira hacia abajo
    upper_tm, lower_tm = _clean(upper, "torso superior", res.warnings), _clean(lower, "pelvis", res.warnings)
    c = centroid_of_face_triangles(upper_tm, pre)
    if c is None:
        raise ExtractError("el plano de la cintura no deja una cara plana en el torso de arriba")
    c = np.asarray(c, float)
    c[0] = 0.0
    up_frame = pre.with_center(c)
    lo_frame = PortFrame(c.copy(), n.copy(), up_frame.ref.copy())
    up_ports = {**common, **{k: v for k, v in sides.items() if k.startswith("hombro")},
                "cintura": Port("cintura", "toma", up_frame, face_from_mesh(upper_tm, up_frame))}
    lo_ports = {"cintura": Port("cintura", "enchufe", lo_frame, face_from_mesh(lower_tm, lo_frame)),
                **{k: v for k, v in sides.items() if k.startswith("cadera")}}
    res.pieces.append(Extracted(_meta("torso_sup", prefix, up_ports, p, origin, "centro", False,
                                      descripcion=f"Torso superior de {prefix} (cortado por la cintura)"), upper_tm))
    res.pieces.append(Extracted(_meta("pelvis", prefix, lo_ports, p, origin, "centro", False,
                                      descripcion=f"Pelvis de {prefix}"), lower_tm))
