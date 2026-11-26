"""Tipo `mushroom` (hongo): el pasador de fricción del hombro y la cadera (plan de plugins §1.2, Fase 0).

Es el código que estaba escrito a mano en `pipeline.build`, movido sin cambios de cuenta: vástago + cabeza en la hija; la
cavidad del anfitrión es `dilate(hija + pasador)` barrida por los ángulos del recorrido. La salida debe ser idéntica a la de
antes (`tools/golden.py comparar todos`).
"""
from __future__ import annotations

import numpy as np

from .. import solids as S
from ..limbs import pin_margin
from ..params import Params, PinSpec
from ..pins import mushroom_pin
from .base import (API_VERSION, FaceRequirements, JointResult, JointSite, RevoluteMotion, rotation_axis, sweep_angles)

MIRROR_SLOTS = frozenset({"cadera", "pierna"})   # las que el motor valida por cabezas enfrentadas (`capacity.MIRROR_CLASSES`)


class Mushroom:
    id = "mushroom"
    api_version = API_VERSION
    labels = {"es": "Hongo", "en": "Mushroom", "pt": "Cogumelo"}
    params_cls = PinSpec
    slots = frozenset({"hombro", "cadera", "brazo", "pierna"})

    def default_params(self, params: Params, slot: str) -> PinSpec:
        """El pasador de la línea para ese lugar: cadera/pierna → `hip_pin`; el resto → `shoulder_pin`."""
        return params.hip_pin if slot in MIRROR_SLOTS else params.shoulder_pin

    def requires(self, jp: PinSpec, params: Params, slot: str | None = None) -> FaceRequirements:
        depth = float(jp.shaft_len) + float(jp.head_h) + float(params.clearance)
        gap = 2.0 * float(params.clearance) if slot in MIRROR_SLOTS else None
        return FaceRequirements(depth, float(params.min_wall), float(jp.head_d) / 2, gap)

    def build(self, site: JointSite) -> JointResult:
        face, p = site.face, site.params
        axis = rotation_axis(face)
        pin = mushroom_pin(face.center, face.outward, site.jp)
        limb = S.union(site.child, pin)
        site.say(f"Cavidad de {site.slot}…")
        grown = S.dilate(limb, p.clearance, site.segments)
        rng = site.flex if site.flex is not None else (0.0, 0.0)
        angles = sweep_angles(rng, p.sweep_step)
        sweep = S.union(*[S.rotate_about_axis(grown, face.center, axis, a) for a in angles])
        motion = RevoluteMotion(limb, face.center, axis, (angles[0], angles[-1]))
        return JointResult(pieces={"child": limb}, cutters={"parent": [sweep], "host": []}, motion=motion,
                           report={"angles": angles, "pin_margin": pin_margin(face, site.jp.shaft_d)})
