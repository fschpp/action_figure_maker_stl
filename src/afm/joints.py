"""Articulaciones de bisagra (rodilla, codo): lengüeta en ranura con dos bulones, print-in-place.

Plan: docs/PLAN_articulaciones_rodilla_codo_cadera.md (§3).

Un miembro ya separado del torso (`limbs.separate_limb` + pasador de hombro/cadera) se parte por el
«plano de articulación» en proximal (muslo, brazo) y distal (pantorrilla, antebrazo):

* distal = parte del miembro del otro lado del plano + **lengüeta** (disco de radio R alrededor del eje,
  de espesor `tongue_t`, que se mete en la pieza proximal) + **dos bulones** (uno a cada lado de la lengüeta);
* proximal = parte del lado del torso menos el **barrido** del distal dilatado (holgura radial `clearance`,
  axial `axial_clearance`) al girar por el rango; la ranura y los agujeros ciegos de los bulones salen solos.

El tope no se agrega aparte: es el material del proximal que queda fuera del barrido (igual que el límite
de cadera). El eje queda `h` mm del lado proximal del plano; hace falta h ≥ radio del bulón + holguras + pared
para que alrededor del bulón quede material (ver `HingeSpec` y `min_shoulder`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import solids as S
from .params import HingeSpec, Params
from .planes import Plane

KINDS = ("knee", "elbow")
JOINT_KIND_BY_LIMB = {"leg": "knee", "arm": "elbow"}
DEFAULT_NAMES = {"knee": ("muslo", "pantorrilla"), "elbow": ("brazo", "antebrazo")}
BOLT_OVERLAP = 0.3       # cuánto entra el bulón en la lengüeta (unión sólida)
BOLT_CHAMFER = 0.4
SHOULDER_WALL = 1.0      # pared mínima recomendada entre el bulón (con su holgura) y el barrido del hombro


@dataclass
class JointSpec:
    """Articulación de un miembro del lado de trabajo (+X)."""

    name: str                          # p. ej. "rodilla"
    limb: str                          # nombre del `LimbSpec` que se parte, p. ej. "pierna"
    kind: str                          # "knee" | "elbow"
    plane: Plane                       # plano de articulación (3 puntos): corta proximal / distal
    axis_center: tuple                 # punto 3D del eje del bulón (centro de la bisagra)
    axis_dir: tuple = (1.0, 0.0, 0.0)  # dirección del eje (transversal; X en las figuras actuales)
    hinge: HingeSpec | None = None     # None = `Params.hinge_pin`
    flex_range: tuple | None = None    # (min, max) de flexión en grados, hiperextensión negativa; None = según Params
    flex_sign: int | None = None       # signo del giro en torno a `axis_dir` para flexionar; None = según el tipo
    prox_name: str | None = None
    dist_name: str | None = None

    def names(self) -> tuple[str, str]:
        d = DEFAULT_NAMES[self.kind]
        return self.prox_name or d[0], self.dist_name or d[1]

    def sign(self) -> int:
        """+1/−1: sentido del giro en torno a +X que flexiona. Con eje +X un miembro que cuelga hacia −Z va hacia
        +Y (adelante) con ángulo positivo: el codo flexiona hacia adelante (+1) y la rodilla hacia atrás (−1)."""
        if self.flex_sign is not None:
            return 1 if self.flex_sign >= 0 else -1
        return -1 if self.kind == "knee" else 1

    def to_dict(self) -> dict:
        return {"name": self.name, "limb": self.limb, "kind": self.kind, "plane": self.plane.to_dict(),
                "axis_center": list(self.axis_center), "axis_dir": list(self.axis_dir),
                "hinge": None if self.hinge is None else vars(self.hinge).copy(),
                "flex_range": None if self.flex_range is None else list(self.flex_range),
                "flex_sign": self.flex_sign, "prox_name": self.prox_name, "dist_name": self.dist_name}

    @classmethod
    def from_dict(cls, d: dict) -> "JointSpec":
        return cls(name=d["name"], limb=d["limb"], kind=d["kind"], plane=Plane.from_dict(d["plane"]),
                   axis_center=tuple(d["axis_center"]), axis_dir=tuple(d.get("axis_dir", (1, 0, 0))),
                   hinge=None if d.get("hinge") is None else HingeSpec(**d["hinge"]),
                   flex_range=None if d.get("flex_range") is None else tuple(d["flex_range"]),
                   flex_sign=d.get("flex_sign"), prox_name=d.get("prox_name"), dist_name=d.get("dist_name"))


def hinge_for(joint: JointSpec, p: Params) -> HingeSpec:
    return joint.hinge or p.hinge_pin


def flex_range(joint: JointSpec, p: Params) -> tuple[float, float]:
    """(mínimo, máximo) de flexión en grados; el mínimo es −hiperextensión."""
    if joint.flex_range is not None:
        return float(joint.flex_range[0]), float(joint.flex_range[1])
    lo, hi = p.knee_range if joint.kind == "knee" else p.elbow_range
    hyper = p.knee_hyperext if joint.kind == "knee" else p.elbow_hyperext
    return float(lo) - float(hyper), float(hi)


def print_flex(joint: JointSpec, p: Params) -> float:
    return p.knee_print_angle if joint.kind == "knee" else p.elbow_print_angle


def min_shoulder(hinge: HingeSpec, p: Params) -> float:
    """Distancia mínima del eje al plano para que alrededor del bulón quede pared (ver módulo)."""
    return hinge.pin_d / 2 + p.clearance + p.clearance + SHOULDER_WALL


def _unit_axis(d) -> np.ndarray:
    ax = np.asarray(d, float)
    ax = ax / np.linalg.norm(ax)
    return ax if ax[0] >= 0 else -ax


def _bolt(center, direction, spec: HingeSpec, segments: int = 32) -> S.Manifold:
    """Bulón que arranca en el costado de la lengüeta y sale `pin_len` mm, con la punta achaflanada."""
    d = np.asarray(direction, float)
    r = spec.pin_d / 2
    ch = min(BOLT_CHAMFER, spec.pin_len / 2, r / 2)
    start = spec.tongue_t / 2 - BOLT_OVERLAP
    body = S.cylinder_along(center, d, r, spec.pin_len - ch + BOLT_OVERLAP, segments, start=start)
    cone = S.Manifold.cylinder(ch + 0.05, r, r - ch, segments)
    cone = cone.transform(Plane.from_normal(np.asarray(center, float) + d * (spec.tongue_t / 2 + spec.pin_len - ch - 0.05),
                                            d).matrix()[:3, :])
    return S.union(body, cone)


def dilate_anisotropic(mf: S.Manifold, radial: float, axial: float, axis, segments: int = 12) -> S.Manifold:
    """Dilata `radial` mm en el plano perpendicular a `axis` y `axial` mm a lo largo de `axis`
    (suma de Minkowski con un elipsoide circunscrito, como `solids.dilate`)."""
    if radial <= 0 and axial <= 0:
        return mf
    k = 1.0 / np.cos(np.pi / segments)
    ell = S.Manifold.sphere(1.0, segments).scale((max(radial, 1e-3) * k, max(radial, 1e-3) * k, max(axial, 1e-3) * k))
    rot = Plane.from_normal((0, 0, 0), axis).matrix()[:3, :]
    rot[:, 3] = 0
    return mf.minkowski_sum(ell.transform(rot))


@dataclass
class JointBuild:
    proximal: S.Manifold
    distal: S.Manifold
    center: np.ndarray
    axis: np.ndarray
    sign: int
    flex: tuple                 # (mín, máx) de flexión, en grados
    angles: list                # ángulos crudos (alrededor de `axis`) del barrido
    shoulder_h: float
    radius: float
    warnings: list = field(default_factory=list)
    sweep: S.Manifold | None = None     # barrido del distal ensanchado en todo el rango de flexión (talla padre y, en brazos, torso)

    def raw(self, flex_deg: float) -> float:
        return self.sign * flex_deg

    def rotate_distal(self, flex_deg: float, mf: S.Manifold | None = None) -> S.Manifold:
        return S.rotate_about_axis(self.distal if mf is None else mf, self.center, self.axis, self.raw(flex_deg))


def build_joint(limb: S.Manifold, joint: JointSpec, toward: np.ndarray, p: Params) -> JointBuild:
    """Parte `limb` (miembro de pie, con su pasador) en proximal y distal. `toward` es un punto del lado
    proximal (la cara de contacto con el torso): orienta la normal del plano hacia él."""
    if joint.kind not in KINDS:
        raise ValueError(f"tipo de articulación desconocido: {joint.kind!r}")
    hinge = hinge_for(joint, p)
    axis = _unit_axis(joint.axis_dir)
    c = np.asarray(joint.axis_center, float)
    n = np.asarray(joint.plane.normal, float)
    n = n / np.linalg.norm(n)
    if (np.asarray(toward, float) - joint.plane.origin) @ n < 0:
        n = -n                                           # la normal apunta al lado proximal
    if abs(n @ axis) > 0.5:
        raise ValueError(f"«{joint.name}»: el eje de la bisagra no puede ser perpendicular al plano de articulación")
    h = float((c - joint.plane.origin) @ n)
    if h < 0.5:
        raise ValueError(f"«{joint.name}»: el eje debe quedar al menos 0,5 mm del lado proximal del plano "
                         f"de articulación (queda a {h:.2f} mm)")
    warnings = []
    need = min_shoulder(hinge, p)
    if h < need - 1e-9:
        warnings.append(f"{joint.name}: el eje queda a {h:.2f} mm del plano de articulación (< {need:.2f} mm): "
                        "poca pared alrededor del bulón")
    radius = max(hinge.end_r, h + 0.3)                   # el disco tiene que alcanzar el cuerpo distal
    if radius > hinge.end_r + 1e-9:
        warnings.append(f"{joint.name}: radio de la lengüeta subido a {radius:.2f} mm (eje a {h:.2f} mm del plano)")

    up = S.halfspace(Plane.from_normal(joint.plane.origin, n))
    prox0 = S.intersection(limb, up)
    below = S.difference(limb, up)
    slab = S.cylinder_along(c, axis, 400.0, hinge.tongue_t, 16, start=-hinge.tongue_t / 2)
    disc = S.cylinder_along(c, axis, radius, 400.0, 64, start=-200.0)
    tongue = S.intersection(S.intersection(limb, slab), disc)      # llega bajo el plano: se funde con `below`
    bolts = [_bolt(c, axis * s, hinge, 32) for s in (1.0, -1.0)]
    body_dist = S.largest_component(S.union(below, tongue))
    distal = S.largest_component(S.union(body_dist, *bolts))

    lo, hi = flex_range(joint, p)
    sgn = joint.sign()
    raw = sorted((sgn * lo, sgn * hi))
    step = p.sweep_step
    angles = list(np.arange(raw[0], raw[1] + 1e-9, step)) if raw[1] > raw[0] else [raw[0]]
    if angles[-1] < raw[1] - 1e-9:
        angles.append(raw[1])
    # Los bulones son cuerpos de revolución sobre el propio eje: girarlos no los mueve, y unir 20 copias casi idénticas
    # (facetas que coinciden casi pero no del todo) deja aristas de 4 caras en la pieza proximal. Se ensanchan una vez
    # y se suman sin rotar; el resto del distal sí se barre. Dilatar distribuye sobre la unión: el sólido es el mismo.
    grown = dilate_anisotropic(body_dist, p.clearance, p.axial_clearance, axis)
    grown_bolts = [dilate_anisotropic(b, p.clearance, p.axial_clearance, axis) for b in bolts]
    sweep = S.union(*[S.rotate_about_axis(grown, c, axis, a) for a in angles], *grown_bolts)
    proximal = S.largest_component(S.difference(prox0, sweep))
    return JointBuild(proximal, distal, c, axis, sgn, (lo, hi), [float(a) for a in angles], h, radius, warnings,
                      sweep)
