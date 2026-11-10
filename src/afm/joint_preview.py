"""Vista previa rápida de una articulación (rodilla / codo) sin generar toda la figura.

`build_joint` talla la pieza proximal con el barrido de la distal en todo el rango de flexión: por eso mover el
eje, inclinar el plano o cambiar el rango cambia cuánto material se lleva el hueco. Para poder ver ese efecto mientras se
ajustan los valores, este módulo arma SOLO la articulación:

* `prepare_limb`: el miembro del modelo (corte + recortes), calculado una vez por miembro (es lo caro).
* `preview_joint`: recorta el miembro a una caja alrededor del eje (`crop_mm`) y llama a `build_joint` con un paso de
  barrido más grueso (`PREVIEW_STEP`). Devuelve el proximal tallado, el distal girado a la flexión que se pida y los
  números que importan para decidir: volumen que se llevó el hueco, altura del eje sobre el plano, avisos.

No toca el pipeline: la figura final se sigue generando con `pipeline.build` (paso fino, pasador del hombro, clic, etc.).
La vista previa usa el mismo `build_joint`, así que la forma del hueco es la que va a salir (salvo el escalón del paso).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import numpy as np
import trimesh

from . import solids as S
from .joints import JointBuild, JointSpec, build_joint
from .limbs import ContactFace, LimbSpec, separate_limb
from .params import Params
from .planes import Plane
from .trims import Trim, apply_trims

CROP_MM = 28.0           # mitad del lado de la caja que se conserva alrededor del eje
PREVIEW_STEP = 10.0      # paso del barrido en la vista previa (el de la figura final es `Params.sweep_step`)
PREVIEW_SEGMENTS = 8     # resolución de la dilatación por holgura (la figura final usa 12)
PREVIEW_ROD_STEP = 0.5   # paso con que se mide el largo del bastago (la figura final usa 0,25 mm)


@dataclass
class JointPreview:
    joint: JointSpec
    build: JointBuild
    carved_mm3: float                 # volumen del proximal (dentro de la caja) que se llevó el hueco
    proximal_mm3: float
    prox_before_mm3: float            # el proximal sin tallar, para comparar
    flex_range: tuple
    warnings: list = field(default_factory=list)
    _proximal_mesh: trimesh.Trimesh | None = None

    @property
    def h(self) -> float:
        """Distancia del eje al plano de articulación, en mm."""
        return float(self.build.shoulder_h)

    @property
    def carved_pct(self) -> float:
        return 100.0 * self.carved_mm3 / self.prox_before_mm3 if self.prox_before_mm3 > 0 else 0.0

    def proximal_mesh(self) -> trimesh.Trimesh:
        if self._proximal_mesh is None:
            self._proximal_mesh = S.to_trimesh(self.build.proximal)
        return self._proximal_mesh

    def distal_mesh(self, flex: float = 0.0) -> trimesh.Trimesh:
        """La pieza distal girada `flex` grados (flexión positiva = convención del rango de la articulación)."""
        return S.to_trimesh(self.build.rotate_distal(float(flex)))

    def summary(self) -> str:
        lo, hi = self.flex_range
        text = (f"Hueco: se lleva {self.carved_mm3:.0f} mm³ del brazo ({self.carved_pct:.0f} %) · eje a {self.h:.2f} mm del "
                f"plano · radio de la lengüeta {self.build.radius:.2f} mm · barrido {lo:g}° … {hi:g}°")
        return text + ("".join(f"\n  · {w}" for w in self.warnings) if self.warnings else "")


def prepare_limb(body: S.Manifold, spec: LimbSpec, p: Params, trims: list[Trim] | None = None,
                 half: S.Manifold | None = None) -> tuple[S.Manifold, ContactFace]:
    """El miembro tal como lo recibe `build_joint` en `pipeline.build` para un miembro del modelo: cortado de la mitad
    +X del cuerpo, con los recortes manuales. (No lleva el pasador del hombro: está lejos del codo / de la rodilla.)"""
    if half is None:
        half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    limb_raw, face = separate_limb(half, spec, p.burr)
    return apply_trims(limb_raw, list(trims or []), spec.name), face


def _crop_box(center, half_size: float) -> S.Manifold:
    c = np.asarray(center, float)
    return S.Manifold.cube((2 * half_size,) * 3).translate(tuple(c - half_size))


def preview_joint(limb: S.Manifold, face: ContactFace, joint: JointSpec, p: Params,
                  crop_mm: float = CROP_MM, step: float = PREVIEW_STEP) -> JointPreview:
    """Arma la articulación sobre el miembro (ya cortado, ver `prepare_limb`). Falla con ValueError como `build_joint`
    (eje a menos de 0,5 mm del plano, eje perpendicular al plano…)."""
    fast = dataclasses.replace(p, sweep_step=max(float(p.sweep_step), float(step)))
    cropped = S.intersection(limb, _crop_box(joint.axis_center, crop_mm)) if crop_mm else limb
    if cropped.is_empty():
        raise ValueError(f"«{joint.name}»: el eje queda fuera del miembro (revisá su posición)")
    jb = build_joint(cropped, joint, face.center, fast, PREVIEW_SEGMENTS, PREVIEW_ROD_STEP)
    n = np.asarray(joint.plane.normal, float)
    n = n / np.linalg.norm(n)
    if (np.asarray(face.center, float) - joint.plane.origin) @ n < 0:
        n = -n
    before = float(S.intersection(cropped, S.halfspace(Plane.from_normal(joint.plane.origin, n))).volume())
    after = float(jb.proximal.volume())
    return JointPreview(joint=joint, build=jb, carved_mm3=max(before - after, 0.0), proximal_mm3=after,
                        prox_before_mm3=before, flex_range=tuple(jb.flex), warnings=list(jb.warnings))
