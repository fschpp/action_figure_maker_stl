"""Tipo `ball` (rótula): una esfera sobre un cuello, alojada en un zócalo del anfitrión (plan de plugins §4.3, Fase 4).

La hija lleva un cuello (cilindro Ø`neck_d` desde la cara de contacto) y una bola Ø`ball_d` con su centro a `neck_len + ball_d/2` de la
cara, hacia adentro del anfitrión. El anfitrión recibe el zócalo: la hija de pie dilatada la holgura (deja la luz alrededor del cuerpo, como
el hongo) más un cono que sale de la bola hasta la cara y deja inclinar el cuello hasta `half_angle` grados en cualquier dirección. Donde el
cono se une a la esfera del zócalo queda un anillo de material, el labio, más angosto que la bola: la retiene sin soportes (imprime
armada). `lip_margin_mm` es cuánto más ancha que el labio es la bola; sin margen positivo la bola saldría y el tipo no arma.

La esfera es igual vista desde cualquier giro: la cavidad no se barre por ángulos. El movimiento es un `ConeMotion` (no tiene un eje), así
que la validación la recorre por grilla de poses. Lo que limita la inclinación real es el cuerpo de la hija contra el anfitrión (la luz es
la holgura): la validación lo detecta y se baja `half_angle`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import solids as S
from ..params import Params, PinSpec
from ..planes import Plane
from .base import API_VERSION, ConeMotion, FaceRequirements, JointResult, JointSite

MIRROR_SLOTS = frozenset({"cadera", "pierna"})
MIN_LIP_MM = 0.4          # el labio debe agarrar la bola al menos una línea de extrusión (0,4 mm) para que no salte
_OVERLAP = 0.3            # el cuello entra en la hija para soldarse a ella
_EXIT = 1.0               # el cono sale 1 mm más allá de la cara, para abrir la luz


@dataclass(frozen=True)
class BallSpec:
    """Medidas de la rótula, en mm absolutos de impresión (figura de ~130 mm)."""
    ball_d: float = 7.0         # Ø de la bola
    neck_d: float = 3.0         # Ø del cuello
    neck_len: float = 1.5       # de la cara de contacto al polo cercano de la bola
    half_angle: float = 20.0    # inclinación máxima del cuello, grados
    twist: float = 0.0          # giro propio ± que se valida, grados (0 = solo se valida la inclinación)


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def lip(jp: BallSpec, clearance: float) -> tuple[float, float]:
    """`(z, radio)` del labio: la altura sobre el centro de la bola (hacia la cara) y el radio del anillo donde el cono del cuello corta la
    esfera del zócalo (radio `ball_d/2 + holgura`). Es lo más angosto que tiene que pasar la bola para salir."""
    R, c, t = jp.ball_d / 2 + clearance, clearance, np.tan(np.radians(jp.half_angle))
    a = jp.neck_d / 2 + c                                  # radio del cono en el centro de la bola
    k = 1.0 + t * t
    disc = a * a * t * t - k * (a * a - R * R)
    z = (-a * t + np.sqrt(max(disc, 0.0))) / k
    return float(z), float(a + z * t)


def lip_margin(jp: BallSpec, clearance: float) -> float:
    """Cuánto más ancha que el labio es la bola (mm). Positivo = retenida."""
    return float(jp.ball_d / 2 - lip(jp, clearance)[1])


def mouth_radius(jp: BallSpec, clearance: float) -> float:
    """Radio del cono del zócalo sobre el plano de la cara de contacto: lo que tiene que caber en la cara del anfitrión."""
    t = np.tan(np.radians(jp.half_angle))
    return float(jp.neck_d / 2 + clearance + (jp.neck_len + jp.ball_d / 2) * t)


def equivalent_pin(jp: BallSpec, clearance: float) -> PinSpec:
    """`PinSpec` que hace de la rótula para lo que solo sabe de pasadores (guía de cortes, capacidad del anfitrión, contrato de interfaz,
    cabezas enfrentadas de la cadera): el «vástago» es el cono del cuello sobre la cara (Ø = 2 · `mouth_radius`, lo que tiene que caber en
    ella) y la «cabeza» es la bola. Con la bola vista como un disco la separación entre caderas sale algo menor que la real: del lado seguro."""
    return PinSpec(2.0 * mouth_radius(jp, clearance), float(jp.neck_len), float(jp.ball_d), float(jp.ball_d))


class Ball:
    id = "ball"
    api_version = API_VERSION
    labels = {"es": "Rótula", "en": "Ball joint", "pt": "Rótula"}
    params_cls = BallSpec
    slots = frozenset({"hombro", "cadera", "brazo", "pierna"})

    def default_params(self, params: Params, slot: str) -> BallSpec:
        return BallSpec()

    def requires(self, jp: BallSpec, params: Params, slot: str | None = None) -> FaceRequirements:
        """Cuerpo detrás de la cara: cuello + bola + holgura. La cabeza que se enfrenta a su espejo (cadera) pide la luz de siempre."""
        depth = float(jp.neck_len) + float(jp.ball_d) + float(params.clearance)
        gap = 2.0 * float(params.clearance) if slot in MIRROR_SLOTS else None
        return FaceRequirements(depth, float(params.min_wall), float(jp.ball_d) / 2, gap)

    def pin_shift(self, jp: BallSpec, params: Params, slot: str | None = None) -> float:
        return 0.0

    face_what = "el cono del cuello"

    def face_radius(self, jp: BallSpec, params: Params, slot: str | None = None) -> float:
        return mouth_radius(jp, float(params.clearance))

    def build(self, site: JointSite) -> JointResult:
        jp, p, face = site.jp, site.params, site.face
        if not 0.0 < jp.half_angle <= 60.0:
            raise ValueError(f"ball «{site.slot}»: half_angle {jp.half_angle:g}° fuera de (0, 60]°")
        if not 0.0 < jp.neck_d < jp.ball_d:
            raise ValueError(f"ball «{site.slot}»: el cuello (Ø{jp.neck_d:g}) tiene que ser más fino que la bola (Ø{jp.ball_d:g})")
        c, R = float(p.clearance), jp.ball_d / 2
        margin = lip_margin(jp, c)
        if margin <= 0:
            raise ValueError(f"ball «{site.slot}»: con {jp.half_angle:g}° de cono el labio no retiene la bola (margen {margin:.2f} mm); "
                             "bajá el ángulo, afiná el cuello o agrandá la bola")
        d = _unit(face.outward)                              # hacia adentro del anfitrión
        o = np.asarray(face.center, float)
        reach = jp.neck_len + R                              # de la cara al centro de la bola
        center = o + d * reach
        neck = S.cylinder_along(o, d, jp.neck_d / 2, reach + _OVERLAP, 48, start=-_OVERLAP)
        ball = S.Manifold.sphere(R, 48).translate(tuple(center))
        limb = S.union(site.child, neck, ball)

        site.say(f"Zócalo de {site.slot}…")
        t = np.tan(np.radians(jp.half_angle))
        h = reach + _EXIT
        a = jp.neck_d / 2 + c
        cone = S.Manifold.cylinder(h, a, a + h * t, 48).transform(Plane.from_normal(center, -d).matrix()[:3, :])
        socket = S.union(S.dilate(limb, c, site.segments), cone)

        warnings = []
        if margin < MIN_LIP_MM:
            warnings.append(f"{site.slot}: el labio retiene la bola solo {margin:.2f} mm (< {MIN_LIP_MM:g} mm): puede saltar")
        if jp.neck_d < p.min_pin_d:
            warnings.append(f"{site.slot}: el cuello es de Ø{jp.neck_d:g} mm (< mínimo {p.min_pin_d:g} mm)")
        tilt = tuple(float(x) for x in site.print_hint.get("print_tilt", (0.0, 0.0, 0.0)))
        tw = float(jp.twist)
        motion = ConeMotion(limb, center, d, float(jp.half_angle), (-tw, tw), tilt)
        return JointResult(pieces={"child": limb}, cutters={"parent": [socket], "host": []}, motion=motion,
                           report={"lip_margin_mm": round(margin, 3), "mouth_radius_mm": round(mouth_radius(jp, c), 3),
                                   "half_angle": float(jp.half_angle), "avisos": list(warnings)},
                           warnings=warnings)

    def test_site(self):
        """Brazo de ejemplo: cilindro Ø6 que sale de la cara x = 0 hacia +X, contra un padre en x ≤ 0 (la rótula entra en −X)."""
        from ..limbs import ContactFace
        p = Params()
        jp = BallSpec(half_angle=4.0)                        # el borde de la cara también se corre de costado (L·sen a) fuera del redondeo de la luz: con 0,5 mm solo admite ≈4°
        child = S.cylinder_along((0.0, 0.0, -8.0), (1.0, 0.0, 0.0), 3.0, 30.0, 48)
        parent = S.Manifold.cube((20.0, 40.0, 60.0)).translate((-20.0, -20.0, -45.0))
        face = ContactFace(center=np.array([0.0, 0.0, -8.0]), outward=np.array([-1.0, 0.0, 0.0]), area=28.0)
        return JointSite(slot="hombro", side="izq", child=child, face=face, params=p, jp=jp, parent=parent), parent
