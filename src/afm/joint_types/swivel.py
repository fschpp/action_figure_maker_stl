"""Tipo `swivel` (pivote / flat plane): giro libre sobre el eje del miembro, para bíceps, muslo y canilla (plan de plugins §4.1, Fase 2).

El miembro se parte por un plano transversal. La mitad distal lleva un pasador de hongo sobre la normal del plano que entra en la mitad
proximal; ésta lleva la cavidad `dilate(pasador, holgura)`. Como el pasador es de revolución, la cavidad no hace falta barrerla por
ángulos (el hombro sí, porque todo el miembro entra en el torso): el giro es libre en 360°. La cabeza queda alojada detrás de un collar de
material proximal que la retiene (imprime armada, sin soportes en la cabeza). Entre las dos mitades queda una luz axial igual a la holgura.

A diferencia de la bisagra, no pasa por `joints.build_joint` (aquella exige un eje casi perpendicular a la normal; aquí es paralelo).
`site.plane` es el plano de corte; la normal se orienta hacia `site.face.center` (el lado proximal). El eje pasa por el centro de la
sección del miembro sobre el plano.
"""
from __future__ import annotations

import numpy as np

from .. import solids as S
from ..limbs import ContactFace
from ..params import Params, PinSpec
from ..pins import mushroom_pin
from ..planes import Plane
from .base import API_VERSION, FaceRequirements, JointResult, JointSite, RevoluteMotion

DEFAULT_PIN = PinSpec(3.0, 3.0, 5.0, 1.2)      # vástago Ø, largo, cabeza Ø, alto: para una figura de ~130 mm
FULL_TURN = (0.0, 360.0)
_SECTION_T = 0.1                               # semiespesor de la loncha con que se mide el centro de la sección


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


class Swivel:
    id = "swivel"
    api_version = API_VERSION
    labels = {"es": "Giro (pivote)", "en": "Swivel", "pt": "Giro (pivô)"}
    params_cls = PinSpec
    slots = frozenset({"biceps", "muslo", "canilla"})

    def default_params(self, params: Params, slot: str) -> PinSpec:
        return DEFAULT_PIN

    def requires(self, jp: PinSpec, params: Params, slot: str | None = None) -> FaceRequirements:
        """Cuerpo proximal necesario detrás del plano: luz + vástago + cabeza + holgura de la cabeza. Sin enfrentarse a su espejo."""
        depth = float(params.clearance) + float(jp.shaft_len) + float(jp.head_h) + float(params.clearance)
        return FaceRequirements(depth, float(params.min_wall), float(jp.head_d) / 2)

    def pin_shift(self, jp: PinSpec, params: Params, slot: str | None = None) -> float:
        return 0.0

    @staticmethod
    def collar_mm(jp: PinSpec, params: Params) -> float:
        """Espesor del collar proximal que retiene la cabeza: del fondo de la cara proximal a la base de la cámara de la cabeza.
        La base de la cabeza arranca a `shaft_len − 0,3` (solape con el vástago, ver `pins.mushroom_pin`) y la cámara lleva holgura."""
        return float(jp.shaft_len) - 0.3 - 2.0 * float(params.clearance)

    def build(self, site: JointSite) -> JointResult:
        if site.plane is None:
            raise ValueError("swivel: hace falta el plano de corte (`site.plane`)")
        p, spec = site.params, site.jp
        o = np.asarray(site.plane.origin, float)
        n = _unit(site.plane.normal)
        if (np.asarray(site.face.center, float) - o) @ n < 0:
            n = -n                                           # la normal apunta al lado proximal
        pl = lambda d: Plane.from_normal(o + n * d, n)       # plano paralelo a distancia d sobre la normal
        slab = S.intersection(site.child, S.difference(S.halfspace(pl(-_SECTION_T)), S.halfspace(pl(_SECTION_T))))
        if float(slab.volume()) <= 0:
            raise ValueError(f"swivel «{site.slot}»: el plano de corte no toca el miembro")
        c = np.asarray(S.to_trimesh(slab).center_mass, float)
        c = c - ((c - o) @ n) * n                            # el eje pasa por el centro de la sección y es normal al plano

        gap = float(p.clearance)
        prox0 = S.intersection(site.child, S.halfspace(pl(gap)))      # lado proximal, con la luz axial
        dist0 = S.difference(site.child, S.halfspace(pl(0.0)))
        pin = mushroom_pin(c, n, spec)
        site.say(f"Cavidad de {site.slot}…")
        cavity = S.dilate(pin, p.clearance, site.segments)
        prox, dist = S.difference(prox0, cavity), S.union(dist0, pin)

        warnings = []
        collar = self.collar_mm(spec, p)
        if collar < p.min_wall:
            warnings.append(f"{site.slot}: el collar que retiene la cabeza queda en {collar:.2f} mm (< pared mínima {p.min_wall:g} mm)")
        if spec.shaft_d < p.min_pin_d:
            warnings.append(f"{site.slot}: el vástago es de Ø{spec.shaft_d:g} mm (< mínimo {p.min_pin_d:g} mm)")
        motion = RevoluteMotion(dist, c, n, FULL_TURN)
        return JointResult(pieces={"prox": prox, "dist": dist}, cutters={"parent": [], "host": []}, motion=motion,
                           report={"collar_mm": collar, "avisos": list(warnings)}, warnings=warnings)

    def test_site(self):
        """Antebrazo de ejemplo: cilindro Ø12 de 40 mm de largo, partido a 20 mm de la cara de contacto (arriba)."""
        p = Params()
        child = S.Manifold.cylinder(40.0, 6.0, 6.0, 48).translate((0.0, 0.0, -40.0))
        face = ContactFace(center=np.array([0.0, 0.0, 0.0]), outward=np.array([0.0, 0.0, 1.0]), area=113.0)
        plane = Plane.from_normal((0.0, 0.0, -20.0), (0.0, 0.0, 1.0), "giro")
        return JointSite(slot="canilla", side="izq", child=child, face=face, params=p, jp=DEFAULT_PIN, plane=plane,
                         flex=FULL_TURN), None
