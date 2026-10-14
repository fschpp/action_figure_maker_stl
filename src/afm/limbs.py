"""Separación de miembros del cuerpo y detección de la cara de contacto (plan §8.3, §8.5)."""
from __future__ import annotations

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

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "plane": self.plane.to_dict(),
            "polygon": np.asarray(self.polygon).tolist(), "thickness": self.thickness,
            "contact_axis": list(self.contact_axis),
            "pin_center": None if self.pin_center is None else list(self.pin_center),
            "sweep_deg": None if self.sweep_deg is None else list(self.sweep_deg),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LimbSpec":
        return cls(
            name=d["name"], kind=d["kind"], plane=Plane.from_dict(d["plane"]),
            polygon=np.asarray(d["polygon"], float), thickness=d["thickness"],
            contact_axis=tuple(d.get("contact_axis", (1, 0, 0))),
            pin_center=None if d.get("pin_center") is None else tuple(d["pin_center"]),
            sweep_deg=None if d.get("sweep_deg") is None else tuple(d["sweep_deg"]),
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
    if burr > 0:
        # Extrude Remove de `burr` mm sobre la huella de la cara de contacto (tutorial, paso 014)
        limb = S.difference(limb, _footprint_slab(face, burr))
        limb = S.largest_component(limb)
        face = find_contact_face(limb, spec.contact_axis)
    if spec.pin_center is not None:
        pc = np.asarray(spec.pin_center, float)
        face = ContactFace(pc - ((pc - face.center) @ face.outward) * face.outward, face.outward, face.area)
    return limb, face


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
