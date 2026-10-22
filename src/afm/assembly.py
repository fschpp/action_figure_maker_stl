"""Colocar piezas de catálogo en el puerto de la figura huésped (plan de piezas intercambiables, §4.2 y §4.3).

Una pieza de catálogo es una malla cruda con puertos (`parts.py`). Acá se la lleva, con una transformación rígida (sin
escala), al lugar donde el huésped tiene su toma: el puerto de la pieza (enchufe) se acopla con el marco de la toma del
huésped (mismo centro y referencia de giro, ejes opuestos). El resultado es lo mismo que `separate_limb` devuelve para un
miembro del propio modelo (malla + `ContactFace`), así que `pipeline.build` sigue desde ahí igual que siempre.

Este módulo no importa `pipeline` ni `extract` a nivel de módulo (`pipeline` lo importa a él).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import solids as S
from .limbs import ContactFace
from .ports import Compat, Port, PortFace, PortFrame, coupling_transform, port_compat

# Clase de miembro del huésped (`LimbSpec.kind`) -> (tipo de pieza de catálogo, puerto de la pieza)
LIMB_PART = {"arm": ("brazo", "hombro"), "leg": ("pierna", "cadera")}


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
    """Lleva la pieza al lugar del miembro de tipo `kind` ("arm" | "leg") cuya cara de contacto en el huésped es `host_face`.

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
