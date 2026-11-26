"""Tipo `hinge` (bisagra de lengüeta en ranura): rodilla y codo (plan de plugins §1.2, Fase 0).

Envuelve `joints.build_joint` sin cambiar la cuenta. A diferencia del hongo, parte el miembro ENTERO en proximal y distal por el plano
de `site.jp` (un `JointSpec`) y su barrido talla el padre y, en brazos, el torso (`cutters["host"]`). El `JointBuild` viaja en `native`.
"""
from __future__ import annotations

import numpy as np

from .. import solids as S
from ..joints import JointSpec, build_joint
from ..params import HingeSpec, Params
from ..planes import Plane
from ..limbs import ContactFace
from .base import API_VERSION, FaceRequirements, JointResult, JointSite, RevoluteMotion


class Hinge:
    id = "hinge"
    api_version = API_VERSION
    labels = {"es": "Bisagra", "en": "Hinge", "pt": "Dobradiça"}
    params_cls = JointSpec
    slots = frozenset({"rodilla", "codo"})

    def default_params(self, params: Params, slot: str):
        raise ValueError("la bisagra necesita un JointSpec (plano y eje): no hay valor por defecto")

    def requires(self, jp: JointSpec, params: Params, slot: str | None = None) -> FaceRequirements | None:
        """None: la bisagra no pide pasador al anfitrión (como `capacity.demand`, que devuelve None para rodilla y codo)."""
        return None

    def build(self, site: JointSite) -> JointResult:
        site.say(f"Articulación {site.jp.name}…")
        jb = build_joint(site.child, site.jp, site.face.center, site.params, site.segments, site.opts.get("rod_step", 0.25))
        motion = RevoluteMotion(jb.distal, jb.center, np.asarray(jb.axis, float) * jb.sign, tuple(jb.flex))
        return JointResult(pieces={"prox": jb.proximal, "dist": jb.distal},
                           cutters={"parent": [], "host": [jb.sweep] if jb.sweep is not None else []},
                           motion=motion, report={"avisos": list(jb.warnings)}, warnings=list(jb.warnings), native=jb)

    def test_site(self):
        """Pierna del muñeco: cilindro Ø14 en x = 9 con la rodilla a 3,4 mm sobre el plano z = 14."""
        p = Params()
        child = S.Manifold.cylinder(38.0, 7.0, 7.0, 48).translate((9.0, 0.0, 0.0))
        joint = JointSpec(name="rodilla", limb="pierna", kind="knee", plane=Plane.from_normal((0, 0, 14.0), (0, 0, 1), "art"),
                          axis_center=(9.0, 0.0, 17.4))
        face = ContactFace(center=np.array([9.0, 0.0, 38.0]), outward=np.array([0.0, 0.0, 1.0]), area=154.0)
        return JointSite(slot="rodilla", side="izq", child=child, face=face, params=p, jp=joint), None
