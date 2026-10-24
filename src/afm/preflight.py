"""Revisión previa de las piezas de catálogo elegidas, SIN generar la figura (mejoras de la GUI del catálogo).

`build` tarda: separa todos los miembros, talla barridos, valida. Pero lo que decide si una pieza de catálogo «entra»
en un lugar es barato: la cara de corte del modelo en ese lugar (`separate_limb` / `split_head`) y el puerto de la pieza.
Acá se hace solo eso, y se devuelve por lugar:

* la pieza ya colocada (malla rígida, sin escala: es exactamente la que irá a la figura) para dibujarla de antemano;
* la compatibilidad barata (`ports.port_compat`: IoU, escalón, pared del pasador, altura);
* las dos caras superpuestas (para dibujar «por qué no combina»);
* el conflicto de articulación (la pieza trae rodilla/codo y el modelo ya define uno en ese miembro: `build` lo rechaza);
* una sugerencia de escala para que las caras coincidan.

No importa `pipeline` a nivel de módulo (lo importa `gui.session`, que ya lo tiene cargado).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import solids as S
from .assembly import AssemblyError, PartChoice, face_of_triangles, hip_notes, host_frame, place_part, swap_pelvis
from .joints import JOINT_KIND_BY_LIMB, JointSpec
from .ports import Compat, Port, PortError, PortFrame

# Lugares que se pueden revisar de antemano. La mano se acopla en la muñeca del brazo ya puesto: se mide al generar.
SKIPPED = {"mano": "la mano se acopla en la muñeca del brazo: su acople se mide al generar"}


@dataclass
class PartPreview:
    """Resultado de revisar la pieza de un lugar."""

    place: str
    ref: str
    kind: str | None = None                 # "arm" | "leg" | "head"
    nivel: str = "ok"                       # "ok" | "aviso" | "no"  (el peor entre compatibilidad y conflictos)
    compat: Compat | None = None
    mesh: object | None = None              # trimesh de la pieza ya colocada en el marco del modelo (lado +X)
    part_shape: object | None = None        # shapely: cara de la pieza, en (u, v) de su propio puerto
    host_shape: object | None = None        # shapely: cara del modelo vista desde el puerto de la pieza (espejada en v)
    conflicts: list = field(default_factory=list)   # conflictos que impiden generar (articulación doble, etc.)
    notes: list = field(default_factory=list)       # cosas para saber que no son aviso ni error
    scale_hint: float | None = None         # factor para multiplicar la altura del modelo y que las caras coincidan
    scale_xyz: tuple | None = None          # (sx, sy, sz) que iguala ancho y alto de la cara del modelo con los de la pieza
    error: str | None = None                # no se pudo ni revisar (el lugar no existe, no hay cara de corte…)

    # ------------------------------------------------------------------ textos
    def problems(self) -> list[str]:
        """Todo lo que conviene leer antes de generar, en orden de gravedad."""
        out = []
        if self.error:
            out.append(self.error)
        out += list(self.conflicts)
        if self.compat is not None:
            out += list(self.compat.motivos) + list(self.compat.avisos)
        return out

    def summary(self) -> str:
        if self.error:
            return f"no se pudo revisar: {self.error}"
        parts = []
        c = self.compat
        if c is not None and c.iou is not None:
            parts.append(f"IoU {c.iou:.2f}".replace(".", ","))
        if c is not None and c.escalon_mm is not None:
            parts.append(f"escalón {c.escalon_mm:.2f} mm".replace(".", ","))
        if self.conflicts:
            parts.append("conflicto de articulación")
        return self.nivel + (f" ({', '.join(parts)})" if parts else "")

    def to_dict(self) -> dict:
        return {"lugar": self.place, "pieza": self.ref, "nivel": self.nivel, "error": self.error,
                "conflictos": list(self.conflicts), "notas": list(self.notes), "escala_sugerida": self.scale_hint,
                "escala_xyz_sugerida": None if self.scale_xyz is None else [round(float(x), 4) for x in self.scale_xyz],
                "compat": None if self.compat is None else self.compat.to_dict()}


def joint_conflicts(choice: PartChoice, kind: str, limb_name: str, joints) -> list[str]:
    """Conflictos de rodilla/codo de poner `choice` en el miembro `limb_name` (`kind`): `build` solo admite UNA
    articulación por miembro, así que si la pieza trae la suya y el modelo ya definió otra, falla recién al generar."""
    own = [j for j in joints if j.limb == limb_name]
    out = []
    for jd in choice.meta.articulaciones or []:
        j = JointSpec.from_dict(jd)
        if j.kind != JOINT_KIND_BY_LIMB.get(kind):
            out.append(f"la pieza «{choice.meta.ref}» trae la articulación «{j.name}» ({j.kind}) y «{limb_name}» es un "
                       f"miembro «{kind}»")
        for mine in own:
            out.append(f"la pieza «{choice.meta.ref}» ya trae su {j.kind} («{j.name}») y el modelo también define "
                       f"«{mine.name}» en «{limb_name}»: quitá la del modelo o elegí una pieza sin articulación")
    return out


def scale_hint_for(part_face, host_face) -> float | None:
    """Cuánto hay que multiplicar el modelo para que la cara del corte tenga el radio equivalente de la de la pieza.
    >1 = el modelo es más chico que la pieza en ese lugar."""
    if part_face is None or host_face is None or host_face.radius_eq <= 1e-9:
        return None
    return float(part_face.radius_eq / host_face.radius_eq)


def _xyz_hint(part_face, host_face, frame):
    """`customize.fit_scale` como tupla (o None): escala por eje de la pieza del MODELO que le da a su cara el ancho y el
    alto de la de la pieza elegida."""
    from .customize import fit_scale
    k = fit_scale(part_face, host_face, frame)
    return None if k is None else tuple(float(x) for x in k)


def _overlay_shapes(part: Port, host_port_face):
    """(cara de la pieza, cara del modelo espejada en v) en el marco del puerto de la pieza: como las compara `face_iou`."""
    from shapely import affinity

    if part.face is None or host_port_face is None:
        return None, None
    return part.face.shape(), affinity.scale(host_port_face.shape(), 1.0, -1.0, origin=(0, 0))


def preview_part(place: str, choice: PartChoice, project, body: S.Manifold, half: S.Manifold | None = None) -> PartPreview:
    """Revisa la pieza `choice` en `place` (miembro del modelo, o «cabeza»). `body` es el cuerpo entero (Manifold) y
    `half` su mitad de trabajo (+X), que se calcula si no se pasa."""
    from .head import split_head
    from .limbs import separate_limb

    pv = PartPreview(place, choice.meta.ref)
    p = project.params
    try:
        if place == "mano":
            pv.notes.append(SKIPPED["mano"])
            return pv
        if place == "cabeza":
            if project.head is None:
                raise AssemblyError("el modelo no tiene plano de cuello: sin él no hay dónde poner la cabeza")
            pv.kind = "head"
            split = split_head(body, project.head, p, choice)
            placed = split.placed
            host_face = None            # la cara del cuello ya se midió dentro de `split_head`
            host_port_face = None
        elif place == "pelvis":
            pv.kind = "pelvis"
            _body, placed = swap_pelvis(body, project.limbs, project.waist, choice, p, p.ref_height)
            f = placed.port.frame                 # la toma del torso de arriba mira al revés que el enchufe de la pelvis
            host_port_face = face_of_triangles(placed.face, PortFrame(f.center.copy(), -f.axis, f.ref.copy()))
            legs = {}
            for l in project.limbs:
                if l.kind == "leg":
                    try:
                        legs[l.name] = separate_limb(half if half is not None else body.trim_by_plane((1.0, 0.0, 0.0), 0.0), l, p.burr)[1]
                    except ValueError:
                        pass
            pv.notes += hip_notes(placed, legs)
        else:
            spec = next((l for l in project.limbs if l.name == place), None)
            if spec is None:
                raise AssemblyError(f"«{place}» no es un miembro del modelo")
            pv.kind = spec.kind
            if half is None:
                half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
            _own, host_face = separate_limb(half, spec, p.burr)
            placed = place_part(choice, spec.kind, host_face, p, p.ref_height)
            pv.conflicts += joint_conflicts(choice, spec.kind, place, project.joints)
            host_port_face = face_of_triangles(host_face, host_frame(host_face))
    except (AssemblyError, PortError, ValueError) as exc:
        pv.error = str(exc)
        pv.nivel = "no"
        return pv
    pv.compat = placed.compat
    mesh = choice.mesh.copy()
    mesh.apply_transform(placed.T)
    pv.mesh = mesh
    if host_port_face is not None:
        pv.part_shape, pv.host_shape = _overlay_shapes(choice.port(pv.kind), host_port_face)
        pv.scale_hint = scale_hint_for(choice.port(pv.kind).face, host_port_face)
        pv.scale_xyz = _xyz_hint(choice.port(pv.kind).face, host_port_face, placed.port.frame)
    elif pv.kind == "head" and placed.compat is not None and placed.compat.escalon_mm is not None:
        pv.notes.append("la cara del cuello se mide dentro del corte de la cabeza: sin superposición dibujable")
    nivel = placed.compat.nivel if placed.compat is not None else "ok"
    if pv.conflicts:
        nivel = "no"
    pv.nivel = nivel
    return pv


def preview_parts(project, parts: dict[str, PartChoice], body: S.Manifold, progress=None) -> dict[str, PartPreview]:
    """Revisa todas las piezas elegidas. Un lugar que falla no impide revisar los demás."""
    say = progress or (lambda *_: None)
    half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    out = {}
    for place, choice in parts.items():
        say(f"Revisando «{place}»…")
        out[place] = preview_part(place, choice, project, body, half)
    return out


# ------------------------------------------------------------------------------------------------ explicación visual
def _rings(geom) -> list:
    """Polígonos de una geometría shapely como lista de {"exterior": [[u, v], …], "interiors": [...]} (vacía si no hay)."""
    if geom is None or geom.is_empty:
        return []
    polys = geom.geoms if hasattr(geom, "geoms") else [geom]
    out = []
    for q in polys:
        if q.geom_type != "Polygon" or q.is_empty:
            continue
        out.append({"exterior": [[float(x), float(y)] for x, y in list(q.exterior.coords)[:-1]],
                    "interiors": [[[float(x), float(y)] for x, y in list(r.coords)[:-1]] for r in q.interiors]})
    return out


def overlay_polygons(pv: PartPreview) -> dict:
    """Las dos caras superpuestas para dibujarlas: `pieza` (la del catálogo), `modelo` (la del corte del modelo, ya
    espejada como la compara `face_iou`), `comun` (lo que coincide), `solo_pieza` (sobra de la pieza) y `solo_modelo`
    (sobra del modelo). Coordenadas (u, v) en mm, mismo origen (centros acoplados). Todo vacío si no hay caras."""
    a, b = pv.part_shape, pv.host_shape
    if a is None or b is None:
        return {"pieza": [], "modelo": [], "comun": [], "solo_pieza": [], "solo_modelo": []}
    return {"pieza": _rings(a), "modelo": _rings(b), "comun": _rings(a.intersection(b)),
            "solo_pieza": _rings(a.difference(b)), "solo_modelo": _rings(b.difference(a))}


def advice(pv: PartPreview, session=None) -> list[str]:
    """Por qué la pieza no combina con el lugar, en lenguaje llano y con qué hacer. Lista vacía si todo está bien."""
    out: list[str] = []
    if pv.error:
        out.append(f"No se pudo revisar: {pv.error}.")
        if "cara plana" in pv.error or "cara de contacto" in pv.error:
            out.append("El plano del miembro tiene que cortar el cuerpo dejando una cara plana: revisá la inclinación del "
                       "plano y su desplazamiento de corte (Plano → «Corte a (mm)»).")
        return out
    c = pv.compat
    for t in pv.conflicts:
        out.append(f"Conflicto de articulación: {t}.")
    if c is None:
        return out
    k = pv.scale_hint
    for m in c.motivos:
        out.append(f"No se puede: {m}.")
    if c.iou is not None and c.iou < 0.8:
        out.append(f"Las caras coinciden solo {100 * c.iou:.0f} %: la parte verde de la imagen es lo que se toca; el resto "
                   "queda como escalón o hueco entre el modelo y la pieza.")
    if c.escalon_mm is not None and c.escalon_mm > 0.5:
        out.append(f"Los radios de las caras difieren {c.escalon_mm:.2f} mm.")
    if k is not None and abs(k - 1.0) > 0.03:
        sentido = "agrandarlo" if k > 1.0 else "achicarlo"
        out.append(f"Si escalás TODO el modelo ×{k:.3f} ({sentido} {abs(k - 1) * 100:.0f} %) las caras de este lugar "
                   "tendrían el mismo tamaño («Escalar la figura para este lugar»); ojo: mueve también los demás lugares. "
                   "Para no tocar los demás, escalá solo la pieza del modelo que lleva esta unión, por separado en X, Y y Z "
                   "(«Dividir figura actual y ajustar piezas»).")
    xyz = pv.scale_xyz
    if xyz is not None and max(abs(v - 1.0) for v in xyz) > 0.03:
        out.append("Escala por eje de la pieza del modelo para igualar ancho y alto de la cara: "
                   f"X ×{xyz[0]:.3f}, Y ×{xyz[1]:.3f}, Z ×{xyz[2]:.3f}.".replace(".", ","))
    for t in c.avisos:
        if not any(t in o for o in out):
            out.append(t[0].upper() + t[1:] + ("" if t.endswith(".") else "."))
    return out


# ------------------------------------------------------------------------------------------------ figura toda de catálogo
BASE_NOTE = "es la base de la figura: no hay otra pieza contra la que medirla"


def preview_catalog_only(params, choices: dict, place_of: dict) -> dict[str, PartPreview]:
    """Revisión de una figura armada SOLO con piezas de catálogo (torso de catálogo): cada brazo/pierna contra la toma
    del torso (`assembly.synthesize_host`). `choices` son las claves de `synthesize_host` (`torso`|`torso_sup`, `pelvis`,
    `brazo`, `pierna`, `cabeza`, `mano`) y `place_of` pasa cada clave al lugar de la GUI (`brazo` → «brazo 2», etc.)."""
    from .assembly import synthesize_host

    out: dict[str, PartPreview] = {}
    try:
        host = synthesize_host(choices, params)
    except AssemblyError as exc:
        for key, choice in choices.items():
            pv = PartPreview(place_of.get(key, key), choice.meta.ref, error=str(exc), nivel="no")
            out[pv.place] = pv
        return out
    for key, choice in choices.items():
        place = place_of.get(key, key)
        pv = PartPreview(place, choice.meta.ref)
        out[place] = pv
        face = host.faces.get(key)
        if face is None:
            pv.notes.append(BASE_NOTE if key in ("torso", "torso_sup", "pelvis") else
                            "se acopla al armar: su acople se mide al generar")
            continue
        pv.kind = "arm" if key == "brazo" else "leg"
        try:
            placed = place_part(choice, pv.kind, face, params, params.ref_height)
        except (AssemblyError, PortError, ValueError) as exc:
            pv.error, pv.nivel = str(exc), "no"
            continue
        pv.compat = placed.compat
        mesh = choice.mesh.copy()
        mesh.apply_transform(placed.T)
        pv.mesh = mesh
        hpf = face_of_triangles(face, host_frame(face))
        pv.part_shape, pv.host_shape = _overlay_shapes(choice.port(pv.kind), hpf)
        pv.scale_hint = scale_hint_for(choice.port(pv.kind).face, hpf)
        pv.nivel = placed.compat.nivel if placed.compat is not None else "ok"
    return out
