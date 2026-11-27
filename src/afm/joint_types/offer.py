"""Oferta de tipos por corte (plan de plugins §3.5, Fase 2): qué tipos de articulación admite un corte y con qué margen.

`offer` es BARATA (milisegundos, sin armar la figura): compara lo que cada tipo pide (`JointType.requires` → `FaceRequirements`) con lo que
el corte tiene — el radio libre de la cara alrededor del centro del pasador y la profundidad de cuerpo detrás de ella (`capacity.measure`).
Usa las mismas reglas que `capacity.check` (motivo por debajo de lo pedido, aviso por debajo de `CAP_WARN_MM` de margen). Un tipo
instalado aparece solo. Elegir uno es escribir `LimbSpec.mechanism` / `JointSpec.type` o, para toda la línea, `Params.joints[slot]`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..capacity import CAP_WARN_MM
from ..params import Params

LEVELS = ("ok", "aviso", "no")


@dataclass(frozen=True)
class Offer:
    type_id: str
    nivel: str                                   # «ok» | «aviso» | «no»
    motivos: tuple = ()                          # por qué no, o con qué reparo
    margen_pared_mm: float | None = None         # lo que sobra de cara alrededor de la cabeza (None = no se midió)
    margen_profundidad_mm: float | None = None   # lo que sobra de cuerpo detrás de la cara (None = no se midió)

    def to_dict(self) -> dict:
        return {"tipo": self.type_id, "nivel": self.nivel, "motivos": list(self.motivos),
                "margen_pared_mm": self.margen_pared_mm, "margen_profundidad_mm": self.margen_profundidad_mm}


def _face_radius(face) -> float | None:
    """Radio del mayor círculo de la cara centrado en el pasador (el origen de (u, v)); None si no hay cara que medir."""
    if face is None:
        return None
    from shapely.geometry import Point
    shape = face.shape() if hasattr(face, "shape") else face
    pt = Point(0.0, 0.0)
    return float(shape.boundary.distance(pt)) if shape.contains(pt) else -float(shape.boundary.distance(pt))


def offer_one(t, params: Params, slot: str, face=None, depth_mm: float | None = None, jp=None) -> Offer:
    """El veredicto de un tipo para un corte. `face`: `PortFace` (o geometría shapely) en (u, v) con el centro del pasador en (0, 0);
    `depth_mm`: profundidad detrás de la cara; `jp`: medidas propias (por defecto las del tipo para la línea)."""
    try:
        jp = jp if jp is not None else t.default_params(params, slot)
    except ValueError:
        jp = None                                 # un tipo sin medidas por defecto (bisagra) tampoco le pide nada a la cara
    req = t.requires(jp, params, slot) if jp is not None else None
    if req is None:
        return Offer(t.id, "ok")
    motivos, level = [], "ok"
    wall = depth = None
    r = _face_radius(face)
    if r is not None:
        wall = r - req.huella_radio_mm
        if wall < 0:
            level = "no"
            motivos.append(f"la cabeza (radio {req.huella_radio_mm:.2f} mm) no entra en la cara: queda un radio libre de {max(r, 0.0):.2f} mm")
        elif wall < req.pared_min_mm:
            level = "aviso" if level == "ok" else level
            motivos.append(f"poca pared alrededor de la cabeza: {wall:.2f} mm (mínimo {req.pared_min_mm:g} mm)")
    if depth_mm is not None:
        need = req.profundidad_mm + req.pared_min_mm
        depth = float(depth_mm) - need
        if depth < 0:
            level = "no"
            motivos.append(f"el cuerpo deja {depth_mm:.2f} mm detrás de la cara y el tipo pide {need:.2f} mm")
        elif depth < CAP_WARN_MM:
            level = "aviso" if level == "ok" else level
            motivos.append(f"poca profundidad detrás de la cara: sobran {depth:.2f} mm (aviso por debajo de {CAP_WARN_MM:g} mm)")
    return Offer(t.id, level, tuple(motivos), wall, depth)


def offer(params: Params, slot: str, face=None, depth_mm: float | None = None) -> list[Offer]:
    """Los tipos registrados que admiten el lugar `slot`, del más al menos apto (ok, aviso, no; a igual nivel, por id)."""
    from . import REGISTRY
    out = [offer_one(t, params, slot, face, depth_mm) for _id, t in sorted(REGISTRY.items()) if slot in t.slots]
    return sorted(out, key=lambda o: (LEVELS.index(o.nivel), o.type_id))
