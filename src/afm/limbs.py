"""Separación de miembros del cuerpo y detección de la cara de contacto (plan §8.3, §8.5)."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from . import solids as S
from .planes import Plane


@dataclass
class LimbSpec:
    """Definición de un miembro del lado de trabajo (izquierdo, +X)."""

    name: str                      # p. ej. "brazo", "pierna"
    kind: str                      # "arm" | "leg"
    plane: Plane                   # plano de la silueta
    polygon: np.ndarray            # Nx3, vértices sobre el plano (silueta que cubre el miembro)
    thickness: float               # espesor de la extrusión simétrica sobre el plano
    contact_axis: tuple = (1.0, 0.0, 0.0)   # eje (sin signo) en que está la cara de contacto
    pin_center: tuple | None = None         # centro del pasador (opcional; si no, centroide de la cara)
    sweep_deg: tuple | None = None          # (min, max) del barrido; por defecto según el tipo
    # Extremo redondeado (tutorial pasos 027-028): el extremo del miembro del lado del torso se
    # recorta a un cilindro de eje paralelo al pasador. Solo se toca lo que queda más allá de
    # `cap_center` en la dirección opuesta a `cap_dir` (hacia el torso); el resto del miembro no cambia.
    auto: bool = False                      # proponer pin_center / cap_* desde la cara de contacto (si no se indican)
    burr: float | None = None               # rebaba a rebajar en la cara de contacto; None = el de Params
    cap_radius: float | None = None         # radio del cilindro (mm); None = sin recorte
    cap_center: tuple | None = None         # un punto del eje del cilindro
    cap_dir: tuple = (0.0, 0.0, -1.0)       # dirección del miembro, del torso hacia la punta

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "plane": self.plane.to_dict(),
            "polygon": np.asarray(self.polygon).tolist(), "thickness": self.thickness,
            "contact_axis": list(self.contact_axis),
            "pin_center": None if self.pin_center is None else list(self.pin_center),
            "sweep_deg": None if self.sweep_deg is None else list(self.sweep_deg),
            "auto": self.auto,
            "burr": self.burr,
            "cap_radius": self.cap_radius,
            "cap_center": None if self.cap_center is None else list(self.cap_center),
            "cap_dir": list(self.cap_dir),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LimbSpec":
        return cls(
            name=d["name"], kind=d["kind"], plane=Plane.from_dict(d["plane"]),
            polygon=np.asarray(d["polygon"], float), thickness=d["thickness"],
            contact_axis=tuple(d.get("contact_axis", (1, 0, 0))),
            pin_center=None if d.get("pin_center") is None else tuple(d["pin_center"]),
            sweep_deg=None if d.get("sweep_deg") is None else tuple(d["sweep_deg"]),
            auto=d.get("auto", False),
            burr=d.get("burr"),
            cap_radius=d.get("cap_radius"),
            cap_center=None if d.get("cap_center") is None else tuple(d["cap_center"]),
            cap_dir=tuple(d.get("cap_dir", (0.0, 0.0, -1.0))),
        )


@dataclass
class ContactFace:
    center: np.ndarray
    outward: np.ndarray   # normal que sale del miembro (hacia el torso)
    area: float
    triangles: np.ndarray | None = None   # (n,3,3) triángulos de la cara, para extruir su huella


def find_contact_face(limb: S.Manifold, axis_hint, ignore_x: float | None = 0.0,
                      tol: float = 1e-3) -> ContactFace:
    """Cara plana más grande del miembro cuya normal es paralela al eje indicado.

    Se ignoran las caras sobre el plano de simetría (x = ignore_x), que son del corte a la mitad.
    """
    tm = S.to_trimesh(limb)
    ax = np.asarray(axis_hint, float)
    ax /= np.linalg.norm(ax)
    nrm = tm.face_normals
    dots = nrm @ ax
    cand = np.abs(dots) > 0.999
    if ignore_x is not None:
        on_sym = np.all(np.abs(tm.vertices[tm.faces][:, :, 0] - ignore_x) < tol, axis=1)
        cand &= ~on_sym
    idx = np.where(cand)[0]
    if len(idx) == 0:
        raise ValueError("No se encontró una cara de contacto plana; ajustá la silueta o indicá pin_center")
    sign = np.sign(dots[idx])
    off = np.round((tm.triangles_center[idx] @ ax) / 0.01).astype(int)
    best, best_area = None, -1.0
    for s in (-1.0, 1.0):
        for o in np.unique(off[sign == s]):
            sel = idx[(sign == s) & (off == o)]
            a = tm.area_faces[sel].sum()
            if a > best_area:
                best, best_area, bs = sel, a, s
    c = (tm.triangles_center[best] * tm.area_faces[best, None]).sum(0) / best_area
    return ContactFace(center=c, outward=bs * ax, area=float(best_area), triangles=tm.triangles[best])


def separate_limb(half: S.Manifold, spec: LimbSpec, burr: float) -> tuple[S.Manifold, ContactFace]:
    """Recorta el miembro de la mitad del cuerpo (intersección con el prisma de la silueta)."""
    prism = S.prism_from_polygon(spec.plane, spec.polygon, spec.thickness, symmetric=True)
    limb = S.largest_component(S.intersection(half, prism))
    face = find_contact_face(limb, spec.contact_axis)
    burr = spec.burr if spec.burr is not None else burr
    if burr > 0:
        # Extrude Remove de `burr` mm sobre la huella de la cara de contacto (tutorial, paso 014)
        limb = S.difference(limb, _footprint_slab(face, burr))
        limb = S.largest_component(limb)
        face = find_contact_face(limb, spec.contact_axis)
    if spec.auto:
        spec = propose_geometry(face, spec)
    if spec.pin_center is not None:
        pc = np.asarray(spec.pin_center, float)
        face = ContactFace(pc - ((pc - face.center) @ face.outward) * face.outward, face.outward, face.area)
    if spec.cap_radius:
        limb = S.largest_component(round_end(limb, face, spec))
    return limb, face


def round_end(limb: S.Manifold, face: ContactFace, spec: LimbSpec) -> S.Manifold:
    """Recorta el extremo del miembro (lado del torso) a un cilindro de radio `cap_radius`.

    Se conserva todo lo que está del lado de la punta respecto del plano que pasa por `cap_center`
    (perpendicular a `cap_dir`) y, del otro lado, solo lo que cae dentro del cilindro.
    """
    c = np.asarray(spec.cap_center if spec.cap_center is not None else face.center, float)
    ax = face.outward / np.linalg.norm(face.outward)
    cyl = S.cylinder_along(c, ax, spec.cap_radius, 400.0, start=-200.0)
    tip_side = S.halfspace(Plane.from_normal(c, spec.cap_dir))
    return S.intersection(limb, S.union(cyl, tip_side))


def _footprint_slab(face: ContactFace, depth: float) -> S.Manifold:
    """Losa de espesor `depth` con la forma de la cara de contacto, hacia adentro del miembro."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from shapely.geometry.polygon import orient
    import manifold3d as m3

    plane = Plane.from_normal(face.center, face.outward)
    polys = []
    for t in face.triangles:
        uv = plane.to_uv(t)
        pg = Polygon(uv)
        if pg.area > 1e-12:
            polys.append(pg.buffer(0))
    shape = unary_union(polys)
    geoms = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
    rings = []
    for g in geoms:
        g = orient(g, 1.0)
        rings.append(np.array(g.exterior.coords)[:-1])
        for h in g.interiors:
            rings.append(np.array(h.coords)[:-1])
    cs = m3.CrossSection(rings)
    slab = S.Manifold.extrude(cs, depth + 0.5)       # z in [0, depth+0.5]
    slab = slab.translate((0, 0, -depth))            # z in [-depth, 0.5]: entra al miembro y sobresale 0,5
    # la normal del plano apunta hacia afuera del miembro: z<0 es "hacia adentro"
    return slab.transform(plane.matrix()[:3, :])


def _face_shape(face: ContactFace):
    """Huella de la cara de contacto como geometría shapely en el plano (u, v) de la cara."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    plane = Plane.from_normal(face.center, face.outward)
    polys = []
    for t in face.triangles:
        pg = Polygon(plane.to_uv(t))
        if pg.area > 1e-12:
            polys.append(pg.buffer(0))
    return plane, unary_union(polys)


def propose_geometry(face: ContactFace, spec: LimbSpec) -> LimbSpec:
    """Propone centro de pasador y extremo redondeado a partir de la cara de contacto.

    Se toma el mayor círculo inscrito en la cara (polo de inaccesibilidad): su centro es el del pasador
    en los brazos; en las piernas el círculo es además el recorte cilíndrico del extremo de cadera
    (radio R) y el pasador se sube R/3 hacia el torso, como en el proceso manual (tutorial 027-034).
    El cociente 1/3 está calibrado con una sola figura (Mickey: 2,49 mm / R 7,40): revisar con Güemes y Belgrano.
    Lo que el usuario ya indicó en `spec` no se pisa. Medido contra Mickey: centro del círculo a 0,02 mm
    del cliente en el eje lateral y radio 3 % menor (7,40 vs 7,6); ver PROGRESS.md.
    """
    from shapely.ops import polylabel

    plane, shape = _face_shape(face)
    geoms = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
    big = max(geoms, key=lambda g: g.area)
    pole = polylabel(big, tolerance=0.01)
    radius = float(big.exterior.distance(pole))
    for h in big.interiors:
        radius = min(radius, float(h.distance(pole)))
    center = plane.to_world(np.array([[pole.x, pole.y]]))[0]
    changes = {}
    if spec.kind == "leg":
        up = -np.asarray(spec.cap_dir, float)
        up /= np.linalg.norm(up)
        if spec.pin_center is None:
            pin = center + up * (radius / 3)
            changes["pin_center"] = tuple(float(x) for x in pin)
        if spec.cap_radius is None:
            changes["cap_radius"] = radius
            changes["cap_center"] = tuple(float(x) for x in center)
    elif spec.pin_center is None:
        changes["pin_center"] = tuple(float(x) for x in center)
    return dataclasses.replace(spec, **changes)
