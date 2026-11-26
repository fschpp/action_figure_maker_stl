"""Tipo `detent` (clic): hongo + disco con muescas en la hija y lengüetas en el padre (plan de plugins §1.2, Fase 0).

Envuelve `detent.build_detent_limb` sin cambiar la cuenta. Las lengüetas del padre las suma `pipeline` al final con `DT.add_fingers` sobre
el torso ya decimado (las ranuras de 0,5 mm no sobreviven a una decimación agresiva), por eso `parent_adds` va vacío y el `DetentLimb`
viaja en `JointResult.native`.
"""
from __future__ import annotations

import numpy as np

from .. import detent as DT
from ..limbs import pin_margin
from ..params import DetentSpec, Params, PinSpec
from .base import API_VERSION, FaceRequirements, JointResult, JointSite, RevoluteMotion, rotation_axis, sweep_angles
from .mushroom import Mushroom


class Detent:
    id = "detent"
    api_version = API_VERSION
    labels = {"es": "Clic", "en": "Detent", "pt": "Clique"}
    params_cls = DetentSpec
    slots = Mushroom.slots

    def default_params(self, params: Params, slot: str) -> DetentSpec:
        return params.detent

    def requires(self, jp: DetentSpec, params: Params, slot: str | None = None) -> FaceRequirements:
        """Lo mismo que el hongo del lugar (como `capacity.demand` hoy): el disco no cambia lo que se pide detrás de la cara."""
        return Mushroom().requires(Mushroom().default_params(params, slot or ""), params, slot)

    def build(self, site: JointSite) -> JointResult:
        p, face = site.params, site.face
        pin: PinSpec = site.opts.get("pin") or Mushroom().default_params(p, site.slot)
        th = float(site.print_hint.get("print_angle", 0.0))
        axis = rotation_axis(face)
        site.say(f"Clic de {site.slot}…")
        dl = DT.build_detent_limb(site.slot, site.child, face, axis, site.jp, p, pin, th, site.flex)
        rng = site.flex
        angles = sweep_angles(rng, p.sweep_step) if rng and rng[1] > rng[0] else [0.0]
        motion = RevoluteMotion(dl.limb, face.center, axis, (angles[0], angles[-1]))
        return JointResult(pieces={"child": dl.limb}, cutters={"parent": [dl.sweep], "host": []}, motion=motion,
                           report={"angles": angles, "print_angle": th, "pin_margin": pin_margin(face, pin.shaft_d)},
                           native=dl)

    def test_site(self):
        """El corte sintético del hongo corrido 25 mm hacia +X: con el torso hacia −X la lengüeta no puede pasar del plano de simetría."""
        import dataclasses
        from .testing import synthetic_site
        site, parent = synthetic_site(self, flex=(0.0, 20.0))
        dx = (25.0, 0.0, 0.0)
        face = dataclasses.replace(site.face, center=site.face.center + np.array(dx))
        return dataclasses.replace(site, child=site.child.translate(dx), face=face), parent.translate(dx)
