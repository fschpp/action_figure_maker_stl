"""Vista previa rápida de una articulación (rodilla / codo) sin generar toda la figura.

`build_joint` talla la pieza proximal con el barrido de la distal en todo el rango de flexión: por eso mover el
eje, inclinar el plano o cambiar el rango cambia cuánto material se lleva el hueco. Para poder ver ese efecto mientras se
ajustan los valores, este módulo arma SOLO la articulación:

* `prepare_limb` / `prepare_limb_from_part`: el miembro del modelo (corte + recortes) o la pieza de catálogo colocada en su
  lugar, calculado una vez por miembro (es lo caro).
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


def prepare_limb_from_part(body: S.Manifold, spec: LimbSpec, choice, p: Params, trims: list[Trim] | None = None,
                           half: S.Manifold | None = None) -> tuple[S.Manifold, ContactFace]:
    """Igual que `prepare_limb`, pero el miembro es una **pieza de catálogo** (`assembly.PartChoice`) llevada al lugar del
    miembro del modelo, como hace `pipeline.build`: el lugar lo da la cara de contacto del miembro propio en el torso y la
    pieza se coloca por su puerto (sin escala). Los recortes manuales se aplican a la pieza ya colocada."""
    from .assembly import AssemblyError, place_part

    if half is None:
        half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    _own, host_face = separate_limb(half, spec, p.burr)
    try:
        pp = place_part(choice, spec.kind, host_face, p, p.ref_height)       # sin `host_mesh`: la capacidad no cambia la forma
    except AssemblyError as exc:
        raise ValueError(f"«{spec.name}»: {exc}") from exc
    return apply_trims(pp.mesh, list(trims or []), spec.name), pp.face


def prepare_piece(meta, mesh) -> tuple[S.Manifold, ContactFace]:
    """La pieza de catálogo SOLA, en su propio marco (el de la figura de origen), lista para `preview_joint`: la malla y la
    cara de contacto de su puerto (`hombro` / `cadera`). Es lo que `prepare_limb_from_part` calcula al colocarla, con la
    transformación identidad: sirve para reajustar la articulación de la pieza (`meta.articulaciones`, que está en ese
    mismo marco) sin figura huésped. Falla con ValueError si la pieza no es un brazo / pierna o no tiene su puerto."""
    from .assembly import AssemblyError, PartChoice, _face_triangles

    kind = {"brazo": "arm", "pierna": "leg"}.get(meta.tipo)
    if kind is None:
        raise ValueError(f"«{meta.ref}» es una pieza «{meta.tipo}»: solo un brazo o una pierna lleva rodilla / codo")
    try:
        port = PartChoice(meta, mesh).port(kind)
    except AssemblyError as exc:
        raise ValueError(str(exc)) from exc
    f = port.frame
    face = ContactFace(f.center.copy(), f.axis.copy(), float(port.face.area if port.face is not None else 0.0),
                       None if port.face is None else _face_triangles(port.face, f))
    return S.to_manifold(mesh), face


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


# ------------------------------------------------------------------------------------------------ cadera
HIP_PREFIX = "cadera de "        # nombre de la «articulación» de cadera de un miembro en la vista previa: «cadera de pierna»
HIP_CROP_MM = 45.0               # mitad del lado de la caja que se conserva alrededor del pasador de cadera


def hip_preview_name(limb_name: str) -> str:
    return HIP_PREFIX + limb_name


def hip_limb_name(name: str) -> str | None:
    """Nombre del miembro de una entrada de cadera de la vista previa, o None si no lo es."""
    return name[len(HIP_PREFIX):] if name.startswith(HIP_PREFIX) and len(name) > len(HIP_PREFIX) else None


@dataclass
class HipBuild:
    """Lo mínimo de `JointBuild` que usa el visor: el eje del pasador, la pelvis tallada y el muslo con su pasador."""

    center: np.ndarray                 # centro del pasador sobre la cara de contacto
    axis: np.ndarray                   # eje de giro (+ángulo = hacia adelante, como en `pipeline`)
    tips: tuple                        # largo del eje que se dibuja a cada lado (mm)
    proximal: S.Manifold               # pelvis / torso tallado con el barrido de cadera
    distal: S.Manifold                 # muslo con el pasador (o el disco de clic), de pie
    flex: tuple                        # rango del barrido (mín, máx) en grados

    def rotate_distal(self, angle_deg: float) -> S.Manifold:
        return S.rotate_about_axis(self.distal, self.center, self.axis, float(angle_deg))


@dataclass
class HipPreview:
    """Vista previa de la cadera con la misma interfaz que `JointPreview` (el visor dibuja las dos igual)."""

    limb: str
    build: HipBuild
    carved_mm3: float                  # volumen de la pelvis (dentro de la caja) que se llevó el hueco del barrido
    proximal_mm3: float
    prox_before_mm3: float
    flex_range: tuple
    pin_margin_mm: float | None = None
    mechanism: str = "hongo"
    warnings: list = field(default_factory=list)
    _proximal_mesh: trimesh.Trimesh | None = None

    @property
    def carved_pct(self) -> float:
        return 100.0 * self.carved_mm3 / self.prox_before_mm3 if self.prox_before_mm3 > 0 else 0.0

    def proximal_mesh(self) -> trimesh.Trimesh:
        if self._proximal_mesh is None:
            self._proximal_mesh = S.to_trimesh(self.build.proximal)
        return self._proximal_mesh

    def distal_mesh(self, flex: float = 0.0) -> trimesh.Trimesh:
        """El muslo girado `flex` grados alrededor del pasador (0 = de pie; `sit_angle` = pose sentada)."""
        return S.to_trimesh(self.build.rotate_distal(float(flex)))

    def summary(self) -> str:
        lo, hi = self.flex_range
        text = (f"Cadera ({self.mechanism}): el hueco se lleva {self.carved_mm3:.0f} mm³ de la pelvis ({self.carved_pct:.0f} %) · "
                f"barrido {lo:g}° … {hi:g}° · «Flexión mostrada» gira el muslo alrededor del pasador")
        if self.pin_margin_mm is not None:
            text += f" · margen del vástago al borde de la cara {self.pin_margin_mm:.1f} mm"
        return text + ("".join(f"\\n  · {w}" for w in self.warnings) if self.warnings else "")


def preview_hip(half: S.Manifold, limb: S.Manifold, face: ContactFace, spec: LimbSpec, p: Params,
                crop_mm: float = HIP_CROP_MM, step: float = PREVIEW_STEP) -> HipPreview:
    """Arma SOLO la cadera: el muslo con su pasador y la pelvis tallada con el barrido de cadera, recortados a una caja
    alrededor del pasador. Hace lo mismo que `pipeline.build` para un miembro `leg` (hongo: el miembro + pasador ensanchado
    por la holgura y girado en todo el rango; clic: el volumen del mecanismo), con un paso de barrido más grueso.

    `half`: la mitad +X del cuerpo; `limb` / `face`: el muslo ya cortado (`prepare_limb`). El muslo del torso se resta antes de
    tallar, así que `carved_mm3` es lo que el hueco le saca a la pelvis ADEMÁS del lugar del propio muslo. Con la caja se
    ignora la parte del muslo que queda fuera de ella (la forma del hueco cerca del pasador es la misma)."""
    from . import detent as DT
    from .limbs import pin_margin
    from .pins import mushroom_pin
    from .pipeline import _detent_sweep, _pin_spec, _rotation_axis, _sweep_range

    if spec.kind != "leg":
        raise ValueError(f"«{spec.name}» no es una pierna: la cadera solo existe en las piernas")
    center = np.asarray(face.center, float)
    box = _crop_box(center, crop_mm)
    limb_c = S.intersection(limb, box)
    torso_c = S.intersection(half, box)
    if limb_c.is_empty() or torso_c.is_empty():
        raise ValueError(f"«{spec.name}»: el pasador de cadera queda fuera del cuerpo (revisá el plano de la pierna)")
    torso_c = S.difference(torso_c, limb_c)
    before = float(torso_c.volume())
    pin = _pin_spec(spec, p)
    axis = _rotation_axis(face)
    fast = dataclasses.replace(p, sweep_step=max(float(p.sweep_step), float(step)))
    dspec = DT.detent_spec_for(spec, p)
    if dspec is not None:
        rng = _detent_sweep(spec, p) or (0.0, 0.0)
        dl = DT.build_detent_limb(spec.name, limb_c, face, axis, dspec, fast, pin, float(p.sit_angle), rng)
        distal, sweep, mech = dl.limb, dl.sweep, "clic"
    else:
        distal = S.union(limb_c, mushroom_pin(face.center, face.outward, pin))
        grown = S.dilate(distal, p.clearance, PREVIEW_SEGMENTS)
        a0, a1 = _sweep_range(spec, p)
        angles = np.arange(a0, a1 + 1e-9, fast.sweep_step) if a1 > a0 else [a0]
        if len(angles) > 1 and angles[-1] < a1 - 1e-9:
            angles = np.append(angles, a1)
        sweep = S.union(*[S.rotate_about_axis(grown, center, axis, float(a)) for a in angles])
        mech = "hongo"
    a0, a1 = _sweep_range(spec, p)
    proximal = S.difference(torso_c, sweep)
    margin = pin_margin(face, pin.shaft_d)
    warnings: list = []
    if margin is not None and margin < 0:
        warnings.append(f"el vástago de Ø{pin.shaft_d:g} mm sobresale {-margin:.2f} mm de la cara de contacto")
    elif margin is not None and margin < p.min_wall:
        warnings.append(f"pared de {margin:.2f} mm alrededor del vástago (mínimo {p.min_wall:g} mm)")
    after = float(proximal.volume())
    reach = float(pin.shaft_len + pin.head_h)
    build = HipBuild(center=center, axis=axis, tips=(reach, reach), proximal=proximal, distal=distal, flex=(float(a0), float(a1)))
    return HipPreview(limb=spec.name, build=build, carved_mm3=max(before - after, 0.0), proximal_mm3=after,
                      prox_before_mm3=before, flex_range=(float(a0), float(a1)), pin_margin_mm=margin, mechanism=mech,
                      warnings=warnings)
