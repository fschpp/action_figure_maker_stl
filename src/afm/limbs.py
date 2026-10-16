"""Separación de miembros del cuerpo y detección de la cara de contacto (plan §8.3, §8.5)."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from . import solids as S
from .planes import Plane


def check_limb_plane(plane: Plane, min_frontal: float = 0.0) -> None:
    """Ya no se rechaza ninguna orientación: el plano de un brazo/pierna es el que definen los 3 puntos
    no colineales que eligió quien diseñó el STL (p. ej. la cara chata del hombro, que puede tener
    cualquier normal). La única condición es que los puntos no sean colineales, y eso lo valida
    `Plane.from_points`. Se conserva la función (y `min_frontal` opcional) por compatibilidad."""
    if min_frontal > 0 and abs(float(np.asarray(plane.normal, float)[1])) < min_frontal:
        raise ValueError(f"el plano «{plane.name or 'sin nombre'}» está de canto (|n·Y| < {min_frontal})")


def plane_is_lateral(plane: Plane, contact_axis=(1.0, 0.0, 0.0), min_dot: float = 0.85) -> bool:
    """El plano está de costado: su normal es (casi) paralela al eje del pasador. Un plano así es él mismo la
    cara de contacto (separa el miembro del torso), y la silueta tiene que extruirse hacia afuera: con un
    prisma simétrico el plano atravesaría el miembro y no quedaría ninguna cara de contacto."""
    ax = np.asarray(contact_axis, float)
    n = np.asarray(plane.normal, float)
    return abs(float(n @ ax)) / (np.linalg.norm(n) * np.linalg.norm(ax)) >= min_dot


# Desplazamiento por defecto del corte de la pierna cuando su silueta se dibuja sobre el plano de simetría:
# cara de contacto a x = 7 mm con la figura normalizada a 100 mm (calibrado con Güemes, Belgrano y Azurduy; ver autosil).
DEFAULT_LEG_CUT_X = 7.0


def plane_on_symmetry(plane: Plane, contact_axis=(1.0, 0.0, 0.0), tol: float = 0.5) -> bool:
    """El plano es (casi) el de simetría x = 0: de costado y pasando por el origen."""
    return plane_is_lateral(plane, contact_axis) and abs(float(np.asarray(plane.origin, float)[0])) <= tol


# Espesor por defecto del segundo corte: simétrico respecto de su plano, cubre todo el cuerpo en esa dimensión.
CROSS_THICKNESS_MM = 200.0


CROSS_CEILING_MARGIN_MM = 2.0
CROSS_CEILING_BAND = 0.10      # fracción de la altura del polígono que cuenta como «techo»


def raise_ceiling(poly: np.ndarray, top: float | None, band: float = CROSS_CEILING_BAND) -> np.ndarray:
    """Lleva a la cota `top` (si es más alta) todos los vértices del techo del polígono: los que están dentro del
    `band` superior de su altura (no solo los más altos, el techo suele ser irregular). El escalón de la cadera,
    mucho más abajo, no se toca."""
    poly = np.array(poly, float)
    if top is None or len(poly) < 3:
        return poly
    z = poly[:, 2]
    zmax, zmin = z.max(), z.min()
    if top <= zmax + 1e-9:
        return poly
    poly[z >= zmax - max(0.35, band * (zmax - zmin)), 2] = top
    return poly


@dataclass
class LimbCut:
    """Segundo corte de un miembro: polígono sobre otro plano (normalmente el frontal, perpendicular al de
    costado), extruido simétrico a través de todo el cuerpo. El miembro es la intersección de los dos prismas
    con la figura, como en Onshape: un recorte de costado + uno de frente (tutorial, pasos 007–011)."""

    plane: Plane
    polygon: np.ndarray            # Nx3 sobre `plane`
    thickness: float = CROSS_THICKNESS_MM

    def valid(self) -> bool:
        poly = np.asarray(self.polygon, float)
        return poly.ndim == 2 and len(poly) >= 3

    def prism(self, open_to: float | None = None) -> S.Manifold:
        """Prisma del corte; con `open_to` el techo del polígono se sube hasta esa cota (ver `raise_ceiling`)."""
        poly = raise_ceiling(self.polygon, open_to) if open_to is not None else self.polygon
        return S.prism_from_polygon(self.plane, poly, self.thickness, symmetric=True)

    def to_dict(self) -> dict:
        return {"plane": self.plane.to_dict(), "polygon": np.asarray(self.polygon).tolist(),
                "thickness": self.thickness}

    @classmethod
    def from_dict(cls, d: dict) -> "LimbCut":
        return cls(Plane.from_dict(d["plane"]), np.asarray(d["polygon"], float),
                   d.get("thickness", CROSS_THICKNESS_MM))


@dataclass
class LimbSpec:
    """Definición de un miembro del lado de trabajo (izquierdo, +X)."""

    name: str                      # p. ej. "brazo", "pierna"
    kind: str                      # "arm" | "leg"
    plane: Plane                   # plano de la silueta
    polygon: np.ndarray            # Nx3, vértices sobre el plano (silueta que cubre el miembro)
    thickness: float               # espesor de la extrusión del prisma de la silueta
    # "symmetric": el prisma se centra en el plano (piernas: «corte al medio», 25 mm simétrico).
    # "outward": el prisma sale del plano hacia el lado de trabajo (+X), sin atravesar el torso
    # (brazos: losa de 12 mm del tutorial, pasos 008-009, sobre la cara chata del hombro).
    extrude: str = "symmetric"
    contact_axis: tuple = (1.0, 0.0, 0.0)   # eje (sin signo) en que está la cara de contacto
    pin_center: tuple | None = None         # centro del pasador (opcional; si no, centroide de la cara)
    sweep_deg: tuple | None = None          # (min, max) del barrido; por defecto según el tipo
    # Extremo redondeado (tutorial pasos 027-028): el extremo del miembro del lado del torso se
    # recorta a un cilindro de eje paralelo al pasador. Solo se toca lo que queda más allá de
    # `cap_center` en la dirección opuesta a `cap_dir` (hacia el torso); el resto del miembro no cambia.
    # Desplazamiento (mm, hacia el lado de trabajo +X) del plano de corte respecto del plano donde se dibujó la
    # silueta. Permite dibujar siempre sobre el plano de simetría y cortar más afuera. None = 7 mm en las
    # piernas sobre el plano de simetría (DEFAULT_LEG_CUT_X) y 0 en el resto.
    cut_offset: float | None = None
    auto: bool = False                      # proponer pin_center / cap_* desde la cara de contacto (si no se indican)
    burr: float | None = None               # rebaba a rebajar en la cara de contacto; None = el de Params
    cap_radius: float | None = None         # radio del cilindro (mm); None = sin recorte
    cap_center: tuple | None = None         # un punto del eje del cilindro
    cap_dir: tuple = (0.0, 0.0, -1.0)       # dirección del miembro, del torso hacia la punta
    # Segundo corte (opcional) sobre otro plano, p. ej. la silueta de frente cuando la principal es de costado.
    # El miembro es lo que queda dentro de los dos prismas; sin él todo funciona como antes.
    cross: LimbCut | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "plane": self.plane.to_dict(),
            "polygon": np.asarray(self.polygon).tolist(), "thickness": self.thickness,
            "extrude": self.extrude,
            "contact_axis": list(self.contact_axis),
            "pin_center": None if self.pin_center is None else list(self.pin_center),
            "sweep_deg": None if self.sweep_deg is None else list(self.sweep_deg),
            "cut_offset": self.cut_offset,
            "auto": self.auto,
            "burr": self.burr,
            "cap_radius": self.cap_radius,
            "cap_center": None if self.cap_center is None else list(self.cap_center),
            "cap_dir": list(self.cap_dir),
            "cross": None if self.cross is None else self.cross.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LimbSpec":
        return cls(
            name=d["name"], kind=d["kind"], plane=Plane.from_dict(d["plane"]),
            polygon=np.asarray(d["polygon"], float), thickness=d["thickness"],
            extrude=d.get("extrude", "symmetric"),
            contact_axis=tuple(d.get("contact_axis", (1, 0, 0))),
            pin_center=None if d.get("pin_center") is None else tuple(d["pin_center"]),
            sweep_deg=None if d.get("sweep_deg") is None else tuple(d["sweep_deg"]),
            cut_offset=d.get("cut_offset"),
            auto=d.get("auto", False),
            burr=d.get("burr"),
            cap_radius=d.get("cap_radius"),
            cap_center=None if d.get("cap_center") is None else tuple(d["cap_center"]),
            cap_dir=tuple(d.get("cap_dir", (0.0, 0.0, -1.0))),
            cross=None if d.get("cross") is None else LimbCut.from_dict(d["cross"]),
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


def effective_cut_offset(spec: LimbSpec) -> float:
    """Desplazamiento del corte respecto del plano de la silueta (mm). Ver `LimbSpec.cut_offset`."""
    if spec.cut_offset is not None:
        return float(spec.cut_offset)
    if spec.kind == "leg" and plane_on_symmetry(spec.plane, spec.contact_axis):
        return DEFAULT_LEG_CUT_X
    if plane_on_symmetry(spec.plane, spec.contact_axis):
        raise ValueError(f"«{spec.name}»: la silueta está sobre el plano de simetría; indicá el desplazamiento del corte "
                         "(la cara donde el miembro se separa del torso no está en x = 0)")
    return 0.0


def limb_prism(spec: LimbSpec) -> S.Manifold:
    """Herramienta de corte del miembro: el prisma de su silueta y, si hay segundo corte, la intersección con
    el prisma de éste."""
    prism = _main_prism(spec)
    if spec.cross is not None and spec.cross.valid():
        open_to = None
        if spec.kind == "leg" and len(spec.polygon) >= 3:
            # El 2.º corte de una pierna solo saca la columna del muslo y la rebaba: la altura la marca la silueta
            # de costado, así que su techo no puede quedar por debajo (limitaba la cara de contacto y el pasador).
            open_to = float(np.asarray(spec.polygon, float)[:, 2].max()) + CROSS_CEILING_MARGIN_MM
        prism = S.intersection(prism, spec.cross.prism(open_to))
    return prism


def _main_prism(spec: LimbSpec) -> S.Manifold:
    """Prisma de la silueta principal. En modo «outward» se orienta la normal hacia +X (lado de trabajo), así el
    sentido en que se eligieron los 3 puntos no importa; para un plano sin componente X se usa su normal."""
    if spec.extrude == "outward":
        n = np.asarray(spec.plane.normal, float)
        if n[0] < -1e-6:
            n = -n
        n = n / np.linalg.norm(n)
        plane = Plane.from_normal(np.asarray(spec.plane.origin, float) + n * effective_cut_offset(spec), n, spec.plane.name)
        return S.prism_from_polygon(plane, spec.polygon, spec.thickness, symmetric=False)
    if spec.extrude != "symmetric":
        raise ValueError(f"extrude debe ser 'symmetric' o 'outward', no {spec.extrude!r}")
    return S.prism_from_polygon(spec.plane, spec.polygon, spec.thickness, symmetric=True)


def _cut_limb(half: S.Manifold, spec: LimbSpec, burr: float) -> tuple[S.Manifold, ContactFace]:
    """Recorta el miembro con el prisma de la silueta, rebaja la rebaba y devuelve la cara de contacto
    (con sus triángulos). Es la parte de `separate_limb` anterior a elegir el pasador y el extremo."""
    prism = limb_prism(spec)
    limb = S.largest_component(S.intersection(half, prism))
    face = find_contact_face(limb, spec.contact_axis)
    burr = spec.burr if spec.burr is not None else burr
    if burr > 0:
        # Extrude Remove de `burr` mm sobre la huella de la cara de contacto (tutorial, paso 014)
        limb = S.difference(limb, _footprint_slab(face, burr))
        limb = S.largest_component(limb)
        face = find_contact_face(limb, spec.contact_axis)
    return limb, face


def separate_limb(half: S.Manifold, spec: LimbSpec, burr: float) -> tuple[S.Manifold, ContactFace]:
    """Recorta el miembro de la mitad del cuerpo (intersección con el prisma de la silueta)."""
    limb, face = _cut_limb(half, spec, burr)
    if spec.auto:
        spec = propose_geometry(face, spec)
    if spec.pin_center is not None:
        pc = np.asarray(spec.pin_center, float)
        face = ContactFace(pc - ((pc - face.center) @ face.outward) * face.outward, face.outward, face.area,
                           face.triangles)
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


# ------------------------------------------------------- colocación asistida del pasador (GUI)
@dataclass
class PinPreview:
    """Cara de contacto (el plano de corte donde va el pasador) de un miembro, lista para mostrarla y
    elegir sobre ella el centro del pasador. Se calcula sin construir la figura entera."""

    face: ContactFace
    plane: Plane                 # plano de la cara: origen = centro de la cara, normal = hacia el torso
    shape: object                # huella de la cara (shapely) en coordenadas (u, v) de `plane`
    default_center: np.ndarray   # dónde lo pondría el programa si no se indica `pin_center` (mundo)

    def rings_world(self) -> list[np.ndarray]:
        """Contornos de la huella (exteriores y agujeros) en coordenadas del mundo, para dibujarlos."""
        geoms = list(self.shape.geoms) if hasattr(self.shape, "geoms") else [self.shape]
        out = []
        for g in geoms:
            if g.is_empty or g.geom_type != "Polygon":
                continue
            out.append(self.plane.to_world(np.asarray(g.exterior.coords)))
            out.extend(self.plane.to_world(np.asarray(h.coords)) for h in g.interiors)
        return out

    def project(self, point_world) -> np.ndarray:
        """Punto del mundo -> coordenadas (u, v) sobre el plano de la cara (se proyecta a lo largo de la normal)."""
        return self.plane.to_uv(np.asarray(point_world, float))[0]

    def fit(self, uv, shaft_d: float) -> tuple[bool, float]:
        """(el centro cae dentro de la cara, margen en mm entre el vástago y el borde de la cara).
        Margen negativo: el vástago sobresale de la cara."""
        return pin_fit(self.shape, uv, shaft_d)


def silhouette_on_plane(spec: LimbSpec, plane: Plane) -> list[np.ndarray]:
    """Silueta del miembro proyectada a lo largo de la normal de `plane` (el plano de trabajo del pasador,
    paralelo al de simetría). Devuelve una lista de contornos Nx3 en el mundo, vacía si aún no hay silueta.

    Si el plano de la silueta es de costado se obtiene su contorno completo; si es frontal (u oblicuo),
    su proyección: el tramo que cubre sobre el plano de trabajo."""
    poly = np.asarray(spec.polygon, float)
    if poly.ndim != 2 or len(poly) < 3:
        return []
    return [plane.to_world(plane.to_uv(poly))]


def pin_fit(shape, uv, shaft_d: float) -> tuple[bool, float]:
    from shapely.geometry import Point

    pt = Point(float(uv[0]), float(uv[1]))
    inside = bool(shape.contains(pt))
    d = float(shape.boundary.distance(pt))
    return inside, (d if inside else -d) - shaft_d / 2


def pin_margin(face: ContactFace, shaft_d: float) -> float | None:
    """Margen (mm) entre el vástago y el borde de la cara de contacto; negativo = sobresale. None si la
    cara no trae sus triángulos."""
    if face.triangles is None:
        return None
    _plane, shape = _face_shape(face)
    return pin_fit(shape, (0.0, 0.0), shaft_d)[1]


def contact_preview(half: S.Manifold, spec: LimbSpec, burr: float) -> PinPreview:
    """Cara de contacto de `spec` sobre la mitad de trabajo `half` y centro de pasador por defecto.

    Repite los pasos de `separate_limb` hasta la cara (misma silueta, misma rebaba), de modo que el
    plano que se muestra es el mismo donde después `pipeline.build` pone el pasador. El centro por
    defecto es el que usaría el programa sin `pin_center`: el propuesto con `auto`, o el centroide.
    """
    _limb, face = _cut_limb(half, spec, burr)
    default = face.center
    if spec.auto:
        prop = propose_geometry(face, dataclasses.replace(spec, pin_center=None))
        if prop.pin_center is not None:
            default = np.asarray(prop.pin_center, float)
    plane, shape = _face_shape(face)
    return PinPreview(face, plane, shape, np.asarray(default, float))
