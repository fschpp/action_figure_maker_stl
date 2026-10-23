"""Colocar piezas de catálogo en el puerto de la figura huésped (plan de piezas intercambiables, §4.2 y §4.3).

Una pieza de catálogo es una malla cruda con puertos (`parts.py`). Acá se la lleva, con una transformación rígida (sin
escala), al lugar donde el huésped tiene su toma: el puerto de la pieza (enchufe) se acopla con el marco de la toma del
huésped (mismo centro y referencia de giro, ejes opuestos). El resultado es lo mismo que `separate_limb` devuelve para un
miembro del propio modelo (malla + `ContactFace`), así que `pipeline.build` sigue desde ahí igual que siempre.

Este módulo no importa `pipeline` ni `extract` a nivel de módulo (`pipeline` lo importa a él).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import solids as S
from .limbs import ContactFace
from .ports import Compat, Port, PortFace, PortFrame, coupling_transform, port_compat

# Clase de miembro del huésped (`LimbSpec.kind`) -> (tipo de pieza de catálogo, puerto de la pieza)
LIMB_PART = {"arm": ("brazo", "hombro"), "leg": ("pierna", "cadera"), "head": ("cabeza", "cuello"),
             "pelvis": ("pelvis", "cintura")}     # la pelvis no es un miembro: va en la cintura del torso del modelo


class AssemblyError(ValueError):
    """La pieza no se puede poner en ese lugar (tipo equivocado, sin puerto, etc.)."""


@dataclass
class PartChoice:
    """Pieza elegida del catálogo para un lugar del huésped: metadatos + malla cruda (trimesh), sin colocar."""

    meta: object            # parts.PieceMeta
    mesh: object            # trimesh estanco, en el marco de la figura de origen

    @classmethod
    def from_catalog(cls, catalog, ref: str) -> "PartChoice":
        meta, mesh = catalog.load(ref)
        return cls(meta, mesh)

    def port(self, kind: str) -> Port:
        tipo, name = LIMB_PART[kind]
        if self.meta.tipo != tipo:
            raise AssemblyError(f"«{self.meta.ref}» es una pieza «{self.meta.tipo}» y ese lugar pide «{tipo}»")
        port = self.meta.puertos.get(name)
        if port is None:
            raise AssemblyError(f"«{self.meta.ref}» no tiene el puerto «{name}»")
        return port


@dataclass
class PlacedPart:
    """Pieza ya colocada en el marco del huésped (lado de trabajo +X)."""

    choice: PartChoice
    kind: str
    mesh: S.Manifold
    face: ContactFace
    T: np.ndarray
    port: Port                  # el puerto de la pieza, llevado al marco del huésped
    compat: Compat | None = None

    def report(self) -> dict:
        m = self.choice.meta
        out = {"id": m.id, "version": int(m.version), "sha256": m.sha256, "origen": m.origen,
               "lugar": LIMB_PART[self.kind][1]}
        if self.compat is not None:
            out["compat"] = self.compat.to_dict()
        return out


def reference_for(axis) -> np.ndarray:
    from .extract import reference_for as _ref
    return _ref(axis)


def host_frame(face: ContactFace) -> PortFrame:
    """Marco de la toma del huésped para la cara de contacto `face` del miembro del modelo: sale del torso hacia el
    miembro (eje opuesto a `face.outward`, que sale del miembro hacia el torso). Misma convención que `extract_all`."""
    out = np.asarray(face.outward, float)
    out = out / np.linalg.norm(out)
    return PortFrame(np.asarray(face.center, float).copy(), -out, reference_for(out))


def face_of_triangles(face: ContactFace, frame: PortFrame) -> PortFace | None:
    """Cara del huésped como `PortFace` (polígonos en (u, v) del marco dado); None si no trae triángulos."""
    if face.triangles is None:
        return None
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polys = [Polygon(frame.to_uv(t)).buffer(0) for t in face.triangles]
    return PortFace.from_shape(unary_union([q for q in polys if q.area > 1e-12]))


def _face_triangles(port_face: PortFace, frame: PortFrame) -> np.ndarray:
    """Triángulos (n, 3, 3) de la cara de un puerto, en el mundo del marco `frame`."""
    import trimesh
    from shapely.geometry import Polygon

    out = []
    y = np.cross(frame.axis, frame.ref)
    for q in port_face.polygons:
        poly = Polygon(q["exterior"], q.get("interiors") or []).buffer(0)
        for g in (poly.geoms if hasattr(poly, "geoms") else [poly]):
            if g.is_empty or g.area < 1e-9:
                continue
            verts, faces = trimesh.creation.triangulate_polygon(g, engine="earcut")
            w = frame.center + verts[:, :1] * frame.ref + verts[:, 1:2] * y
            out.append(w[faces])
    if not out:
        raise AssemblyError("la cara de unión de la pieza está vacía")
    return np.concatenate(out)


def place_part(choice: PartChoice, kind: str, host_face: ContactFace, params=None, host_height: float | None = None) -> PlacedPart:
    """Lleva la pieza al lugar del miembro de tipo `kind` ("arm" | "leg") —o de la cabeza, `kind = "head"`— cuya cara de
    contacto en el huésped es `host_face` (para la cabeza, la cara del cuello; su `outward` mira hacia el cuerpo).

    Devuelve la malla colocada, la `ContactFace` equivalente (centro de pasador = centro del puerto) y la compatibilidad
    barata entre el puerto de la pieza y la toma del huésped."""
    port = choice.port(kind)
    target = host_frame(host_face)
    T = coupling_transform(port.frame, target)
    mesh = choice.mesh.copy()
    mesh.apply_transform(T)
    placed_port = port.transformed(T)
    f = placed_port.frame
    face = ContactFace(f.center.copy(), f.axis.copy(), float(port.face.area if port.face is not None else 0.0),
                       None if port.face is None else _face_triangles(port.face, f))
    compat = None
    host_port = Port(port.clase, "toma", target, face_of_triangles(host_face, target))
    try:
        compat = port_compat(port, host_port, choice.meta.altura_figura_mm, host_height, params)
    except Exception as exc:           # la compatibilidad es informativa: no debe impedir armar
        compat = Compat("aviso", avisos=[f"no se pudo medir la compatibilidad: {exc}"])
    return PlacedPart(choice, kind, S.to_manifold(mesh), face, T, placed_port, compat)


HIP_TOL_MM = 1.0        # distancia máxima entre la cadera de la pelvis de catálogo y la del miembro del modelo sin avisar


def swap_pelvis(body: S.Manifold, limbs: list, waist, choice: PartChoice, params=None,
                host_height: float | None = None) -> tuple[S.Manifold, PlacedPart]:
    """Cambia SOLO la pelvis del modelo por la de catálogo `choice` (pieza `pelvis`, puerto `cintura`), dejando el torso de
    arriba, la cabeza y todos los miembros del modelo tal cual.

    La pelvis vieja es lo que queda del cuerpo bajo el plano de la cintura una vez sacados los prismas de corte de los
    miembros (brazos que cuelgan y piernas se conservan: `build` los corta después como siempre). La nueva se acopla,
    rígida y sin escala, a la toma que deja el torso de arriba en ese plano (centro = centroide de su cara de corte).
    Devuelve el cuerpo nuevo y la pieza colocada (`PlacedPart`, `kind="pelvis"`) con la compatibilidad de la cara."""
    from .extract import reference_for as ref_for
    from .limbs import limb_prism
    from .planes import Plane
    from .waist import _face_on, waist_plane

    if waist is None:
        raise AssemblyError("el modelo no tiene plano de cintura: sin él no se sabe dónde cortar la pelvis (Planos → Cintura)")
    port = choice.port("pelvis")                       # valida el tipo «pelvis» y el puerto «cintura»
    plane = waist_plane(waist)
    n = plane.normal
    prisms = [limb_prism(l) for l in limbs]
    region = S.union(*(prisms + [S.mirror_x(m) for m in prisms])) if prisms else None
    torso_only = body if region is None else S.difference(body, region)
    above = S.halfspace(plane)
    below = S.halfspace(Plane.from_normal(plane.origin, -n))
    upper = S.intersection(torso_only, above)
    if upper.is_empty() or upper.volume() < 1.0:
        raise AssemblyError("el plano de la cintura deja el torso sin parte de arriba: no hay dónde acoplar la pelvis")
    try:
        face = _face_on(upper, plane, -n)
    except ValueError as exc:
        raise AssemblyError(str(exc)) from exc
    frame = PortFrame(np.asarray(face.center, float).copy(), -n, ref_for(-n, (0.0, 1.0, 0.0)))
    T = coupling_transform(port.frame, frame)
    mesh = choice.mesh.copy()
    mesh.apply_transform(T)
    placed_mf = S.to_manifold(mesh)
    old = S.intersection(body, below)
    if region is not None:
        old = S.difference(old, region)
    if old.is_empty():
        raise AssemblyError("el modelo no tiene pelvis bajo el plano de la cintura (¿está por debajo de la figura?)")
    new_body = S.union(S.difference(body, old), placed_mf)
    host_port = Port("cintura", "toma", frame, face_of_triangles(face, frame))
    try:
        compat = port_compat(port, host_port, choice.meta.altura_figura_mm, host_height, params)
    except Exception as exc:           # informativa: no debe impedir armar
        compat = Compat("aviso", avisos=[f"no se pudo medir la compatibilidad: {exc}"])
    return new_body, PlacedPart(choice, "pelvis", placed_mf, face, T, port.transformed(T), compat)


def hip_notes(placed: PlacedPart, leg_faces: dict) -> list[str]:
    """Avisos de que las caderas de la pelvis de catálogo (puerto `cadera_izq`, ya en el marco del modelo) no caen donde
    el modelo tiene sus piernas: la pelvis se talla igual con el barrido de cada pierna, pero quedaría hueco o choque."""
    hip = placed.choice.meta.puertos.get("cadera_izq")
    if hip is None:
        return ["la pelvis de catálogo no trae el puerto «cadera_izq»: no se pudo comprobar dónde quedan las caderas"]
    c = np.asarray(hip.transformed(placed.T).frame.center, float)
    out = []
    for name, f in leg_faces.items():
        d = float(np.linalg.norm(np.asarray(f.center, float) - c))
        if d > HIP_TOL_MM:
            out.append(f"la cadera de la pelvis queda a {d:.2f} mm de la de «{name}» (aviso desde {HIP_TOL_MM:g} mm): "
                       "la pierna no coincide con el hueco de la pelvis")
    return out


def resolve_parts(refs: dict, catalog_root: str | None = None) -> dict[str, PartChoice]:
    """`Project.parts` (lugar -> `PartRef`) -> `pipeline.build(parts=)`. Abre el catálogo de la línea de cada pieza:
    `catalog_root` si se da, si no `$AFM_CATALOGO` o la ruta por defecto de la línea (`catalog.default_root`)."""
    from .catalog import Catalog, CatalogError, default_root

    opened: dict[str, Catalog] = {}
    out: dict[str, PartChoice] = {}
    for place, r in refs.items():
        root = catalog_root or (default_root(r.linea) if r.linea else None)
        if root is None:
            raise AssemblyError(f"«{place}»: la pieza «{r.ref}» no dice de qué línea es: pasá --catalogo RUTA")
        try:
            cat = opened.get(root) or opened.setdefault(root, Catalog.open(root))
            if r.linea and cat.name != r.linea:
                raise AssemblyError(f"«{place}»: la pieza «{r.ref}» es de la línea «{r.linea}» y el catálogo es el de «{cat.name}»")
            out[place] = PartChoice.from_catalog(cat, r.ref)
        except CatalogError as exc:
            raise AssemblyError(f"«{place}»: {exc}") from exc
    return out


# ------------------------------------------------------------------ huésped sintetizado (gancho 1 del plan, §4.3)
# Tipo de miembro -> (nombre del miembro en el huésped sintetizado, puerto de la toma en el torso, tipo de pieza)
HOST_LIMBS = {"brazo": ("arm", "hombro_izq"), "pierna": ("leg", "cadera_izq")}


@dataclass
class SyntheticHost:
    """Lo que `pipeline.build` necesita cuando no hay figura de origen: el cuerpo armado con piezas de catálogo."""

    body: S.Manifold
    limbs: list
    faces: dict                 # nombre de miembro -> ContactFace de la toma del torso (en vez de `separate_limb`)
    parts: dict                 # lo que queda de `parts` (miembros, mano, cabeza) para `build(parts=)`
    head: object | None = None  # HeadSpec desde el puerto `cuello` del torso
    waist: object | None = None  # WaistSpec desde el puerto `cintura`
    notes: list = field(default_factory=list)


def _choice_mesh(choice: PartChoice, T=None):
    m = choice.mesh.copy()
    if T is not None:
        m.apply_transform(T)
    return S.to_manifold(m)


def synthesize_host(parts: dict, params=None) -> SyntheticHost:
    """Arma el huésped con el torso de catálogo: `torso` (entero) o `torso_sup` + `pelvis` (se sueldan y `build_waist` las
    vuelve a partir con el `WaistSpec` armado desde el puerto `cintura`). Cada miembro de `parts` (`brazo`, `pierna`) se
    acopla a la toma del torso (`hombro_izq`, `cadera_izq`); la cabeza (pieza `cabeza`) se suma al cuerpo en su propio
    cuello para que `split_head` la corte y la reemplace, como con una figura. Sin escala, todo rígido."""
    from .head import HeadSpec
    from .limbs import LimbSpec
    from .planes import Plane
    from .waist import WaistSpec

    parts = dict(parts)
    notes: list = []
    if "torso" in parts:
        upper = lower = parts.pop("torso")
        if upper.meta.tipo != "torso":
            raise AssemblyError(f"«{upper.meta.ref}» es una pieza «{upper.meta.tipo}» y ese lugar pide «torso»")
        if "torso_sup" in parts or "pelvis" in parts:
            raise AssemblyError("se pidió «torso» entero y también «torso_sup»/«pelvis»: elegí uno")
    elif "torso_sup" in parts and "pelvis" in parts:
        upper, lower = parts.pop("torso_sup"), parts.pop("pelvis")
        for ch, tipo in ((upper, "torso_sup"), (lower, "pelvis")):
            if ch.meta.tipo != tipo:
                raise AssemblyError(f"«{ch.meta.ref}» es una pieza «{ch.meta.tipo}» y ese lugar pide «{tipo}»")
    else:
        raise AssemblyError("sin figura de origen hace falta una pieza «torso» (o «torso_sup» y «pelvis»)")
    waist = None
    body = _choice_mesh(upper)
    if upper is not lower:
        cp = upper.meta.puertos.get("cintura")
        if cp is None:
            raise AssemblyError(f"«{upper.meta.ref}» no tiene el puerto «cintura»")
        body = S.union(body, _choice_mesh(lower))
        waist = WaistSpec(plane=Plane.from_normal(cp.frame.center, -cp.frame.axis, "cintura"))
    head = None
    if "cabeza" in parts:
        hc = parts["cabeza"]
        neck = upper.meta.puertos.get("cuello")
        if neck is None:
            raise AssemblyError(f"«{upper.meta.ref}» no tiene puerto «cuello»: no se le puede poner la cabeza «{hc.meta.ref}»")
        T = coupling_transform(hc.port("head").frame, neck.frame)
        body = S.union(body, _choice_mesh(hc, T))       # la cabeza «original» del huésped: build la corta y pone la de catálogo
        head = HeadSpec(plane=Plane.from_normal(neck.frame.center, neck.frame.axis, "cuello"))
    limbs, faces = [], {}
    for name, (kind, tomas) in HOST_LIMBS.items():
        if name not in parts:
            continue
        owner = upper if kind == "arm" else lower
        toma = owner.meta.puertos.get(tomas)
        if toma is None:
            raise AssemblyError(f"«{owner.meta.ref}» no tiene el puerto «{tomas}»: no se le puede poner «{parts[name].meta.ref}»")
        out = -np.asarray(toma.frame.axis, float)          # `ContactFace.outward` sale del miembro hacia el torso
        tris = None if toma.face is None else _face_triangles(toma.face, toma.frame)
        faces[name] = ContactFace(np.asarray(toma.frame.center, float).copy(), out,
                                  float(toma.face.area if toma.face is not None else 0.0), tris)
        lp = parts[name].port(kind)
        mech = (parts[name].meta.mecanismo or {}).get("mechanism")
        c = np.asarray(toma.frame.center, float)
        limbs.append(LimbSpec(name=name, kind=kind, plane=Plane.from_normal(c, toma.frame.axis, name),
                              polygon=np.zeros((3, 3)), thickness=1.0, contact_axis=tuple(abs(float(x)) for x in toma.frame.axis),
                              pin_center=tuple(float(x) for x in c), sweep_deg=None if lp.sweep_deg is None else tuple(lp.sweep_deg),
                              mechanism=mech))
    return SyntheticHost(body, limbs, faces, parts, head, waist, notes)
