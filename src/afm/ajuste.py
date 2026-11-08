"""Ajuste de la cintura y de la muñeca de un héroe nuevo contra la pelvis y la mano de la figura patrón (Güemes).

Contexto: el héroe sale de una IA con proporciones propias, pero se reutilizan la pelvis, las piernas y las manos de la
figura patrón. Lo que cruza entre figuras son dos caras: la de la **cintura** (donde la pelvis del patrón se acopla al
torso del héroe) y la de la **muñeca** (donde la mano del patrón se acopla al brazo del héroe). Este módulo mueve cada
plano de corte hasta que la cara del héroe tenga el radio equivalente de la del patrón y dice cuánta escala sobra.

* Cintura: se desliza el plano a lo largo de su normal (±`WAIST_SPAN_MM`) y se mide el radio equivalente de la cara del
  corte sobre el torso del héroe (la figura sin los miembros y sin la cabeza, como en `extract`). El contrato de la
  cintura es blando (escala ≤ ±10 %, `interface.SOFT_SCALE_MAX`): si tras mover el plano aún sobra más escala que eso, se
  dice que no alcanza (queda la pieza adaptadora, ver `PENDIENTES.md`).
* Muñeca: se desliza el marco de 3 puntos a lo largo del eje del brazo (±`WRIST_SPAN_MM`) y se mide la cara con
  `hand.split_at_wrist`. El contrato de la muñeca es duro (talles); acá solo se acerca el radio al del patrón y el
  talle lo juzga `interface.label_port` / `afm catalogo cobertura`.

Todos los radios se llevan a la altura del contrato / del patrón con el mismo factor que `interface.label_port`
(`altura_patrón / altura_héroe`), así que valen aunque las figuras se hayan normalizado a alturas distintas.
No modifica el proyecto: devuelve `[AjusteResult]` y `aplicar` los escribe.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import numpy as np

from . import solids as S
from .interface import SOFT_SCALE_MAX
from .planes import Plane

WAIST_SPAN_MM = 12.0         # barrido del plano de la cintura a cada lado (mm de la figura)
WAIST_STEP_MM = 1.0
WAIST_REFINE_MM = 0.2
WRIST_SPAN_MM = 8.0          # barrido de la muñeca a lo largo del eje del brazo
WRIST_STEP_MM = 0.5
WRIST_REFINE_MM = 0.1
NEAR_MM = 0.05               # por debajo de esta diferencia de radio no se mueve nada


@dataclass
class AjusteResult:
    """Qué mover para que la cara de `lugar` ('cintura' o 'muñeca') calce con la del patrón."""

    lugar: str
    r_ref_mm: float | None = None            # radio equivalente de la cara del patrón (a su altura)
    r_antes_mm: float | None = None          # radio del héroe antes y después, llevado a la altura del patrón
    r_despues_mm: float | None = None
    desplazamiento_mm: float = 0.0           # lo que se mueve el plano (mm de la figura; + = hacia arriba / hacia la mano)
    escala: float | None = None              # escala uniforme que aún falta para igualar (r_ref / r_despues)
    ok: bool = False                         # cabe dentro del tope de escala (cintura) o del radio de la mano (muñeca)
    avisos: list = field(default_factory=list)
    error: str | None = None

    @property
    def cambio(self) -> bool:
        return abs(self.desplazamiento_mm) > 1e-9


def _scale(params, ref_height_mm: float | None) -> float:
    """Factor mm de esta figura -> mm de la altura de referencia (la del patrón)."""
    h = getattr(params, "ref_height", None)
    return (ref_height_mm / h) if (ref_height_mm and h) else 1.0


def _grid(span: float, step: float) -> list:
    n = int(round(span / step))
    return [round(i * step, 4) for i in range(-n, n + 1)]


def _torso_below_neck(body: S.Manifold, project) -> S.Manifold:
    """Mitad +X del cuerpo sin la cabeza ni los miembros: el torso donde se corta la cintura."""
    from .extract import _pick_limbs
    from .head import neck_plane
    from .limbs import limb_prism

    half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    if project.head is not None:
        neck = neck_plane(project.head)
        half = S.intersection(half, S.halfspace(Plane.from_normal(neck.origin, -neck.normal)))
    for spec in _pick_limbs(project, []).values():
        half = S.difference(half, limb_prism(spec))
    return half


def _waist_radius(torso: S.Manifold, plane: Plane, offset: float) -> float:
    """Radio equivalente (√(área/π)) de la cara del corte con el plano desplazado `offset` mm sobre su normal."""
    from .waist import _face_on

    n = plane.normal
    moved = Plane.from_normal(plane.origin + n * offset, n)
    lower = S.intersection(torso, S.halfspace(Plane.from_normal(moved.origin, -n)))
    if lower.is_empty() or lower.volume() < 1.0:
        raise ValueError("el plano queda fuera del torso")
    face = _face_on(lower, moved, n)
    # la cara se midió sobre la mitad +X: el área entera es el doble
    return float(np.sqrt(2.0 * face.area / np.pi))


def fit_waist(project, body: S.Manifold, r_ref_mm: float, ref_height_mm: float | None = None,
              max_scale: float = SOFT_SCALE_MAX) -> AjusteResult:
    """Desplaza el plano de la cintura del héroe hasta que su cara tenga el radio `r_ref_mm` de la del patrón."""
    from .waist import waist_plane

    res = AjusteResult("cintura", r_ref_mm=float(r_ref_mm))
    if project.waist is None:
        res.error = "el proyecto no define el plano de la cintura"
        return res
    try:
        plane = waist_plane(project.waist)
        torso = _torso_below_neck(body, project)
        k = _scale(project.params, ref_height_mm)
        r0 = _waist_radius(torso, plane, 0.0)
    except ValueError as exc:
        res.error = str(exc)
        return res
    res.r_antes_mm = r0 * k

    def err(off):
        try:
            return abs(_waist_radius(torso, plane, off) * k - r_ref_mm)
        except ValueError:
            return None

    best_off, best_err = 0.0, abs(r0 * k - r_ref_mm)
    for off in _grid(WAIST_SPAN_MM, WAIST_STEP_MM):
        e = err(off)
        if e is not None and e < best_err - 1e-9:
            best_off, best_err = off, e
    for d in _grid(WAIST_STEP_MM, WAIST_REFINE_MM):                       # afinar alrededor del mejor
        e = err(best_off + d)
        if e is not None and e < best_err - 1e-9:
            best_off, best_err = best_off + d, e
    if abs(abs(r0 * k - r_ref_mm) - best_err) < NEAR_MM:                  # no mejora de verdad: no se mueve
        best_off, best_err = 0.0, abs(r0 * k - r_ref_mm)
    res.desplazamiento_mm = round(float(best_off), 3)
    res.r_despues_mm = _waist_radius(torso, plane, best_off) * k
    res.escala = float(r_ref_mm / res.r_despues_mm) if res.r_despues_mm else None
    res.ok = res.escala is not None and abs(res.escala - 1.0) <= max_scale + 1e-9
    if res.escala is not None and not res.ok:
        res.avisos.append(f"aun moviendo el plano falta una escala de ×{res.escala:.2f} (el tope es ±{max_scale * 100:.0f} %): "
                          "la cintura de este héroe es muy distinta de la del patrón; hace falta la pieza adaptadora")
    elif res.escala is not None and abs(res.escala - 1.0) > 0.005:
        res.avisos.append(f"queda una escala de ×{res.escala:.3f} dentro del tope: se puede aplicar a la pelvis (`part_scales`)")
    return res


def _wrist_radius(limb: S.Manifold, frame, offset: float) -> float:
    from .hand import split_at_wrist

    sp = split_at_wrist(limb, frame.with_center(frame.center + frame.axis * offset))
    return float(sp.face_radius)


def fit_wrist(project, body: S.Manifold, r_ref_mm: float, ref_height_mm: float | None = None,
              tol_mm: float = 0.2) -> AjusteResult:
    """Desplaza el plano de la muñeca del héroe a lo largo del brazo hasta que su cara tenga el radio de la del patrón."""
    from .extract import _pick_limbs
    from .hand import HandError, WristFrame
    from .limbs import separate_limb

    res = AjusteResult("muñeca", r_ref_mm=float(r_ref_mm))
    hand = project.hand
    if hand is None or len(hand.points) != 3:
        res.error = "el proyecto no tiene los 3 puntos de la muñeca"
        return res
    spec = _pick_limbs(project, []).get("arm")
    if spec is None:
        res.error = "el proyecto no define el brazo de trabajo"
        return res
    try:
        half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
        limb, _face = separate_limb(half, spec, project.params.burr)
        pts = np.asarray(hand.points, float).copy()
        pts[:, 0] = np.abs(pts[:, 0])                                     # se trabaja del lado +X
        frame = WristFrame.from_points(pts)
        k = _scale(project.params, ref_height_mm)
        r0 = _wrist_radius(limb, frame, 0.0)
    except (ValueError, HandError) as exc:
        res.error = str(exc)
        return res
    res.r_antes_mm = r0 * k

    def err(off):
        try:
            return abs(_wrist_radius(limb, frame, off) * k - r_ref_mm)
        except (ValueError, HandError):
            return None

    best_off, best_err = 0.0, abs(r0 * k - r_ref_mm)
    for off in _grid(WRIST_SPAN_MM, WRIST_STEP_MM):
        e = err(off)
        if e is not None and e < best_err - 1e-9:
            best_off, best_err = off, e
    for d in _grid(WRIST_STEP_MM, WRIST_REFINE_MM):
        e = err(best_off + d)
        if e is not None and e < best_err - 1e-9:
            best_off, best_err = best_off + d, e
    if abs(abs(r0 * k - r_ref_mm) - best_err) < NEAR_MM:
        best_off, best_err = 0.0, abs(r0 * k - r_ref_mm)
    res.desplazamiento_mm = round(float(best_off), 3)
    res.r_despues_mm = _wrist_radius(limb, frame, best_off) * k
    res.escala = float(r_ref_mm / res.r_despues_mm) if res.r_despues_mm else None
    res.ok = best_err <= tol_mm
    if not res.ok:
        res.avisos.append(f"la muñeca queda a {best_err:.2f} mm de radio de la mano del patrón (tolerancia {tol_mm:.2f}): "
                          "se puede escalar la mano (×{:.2f}) o cortar la muñeca en otro punto".format(res.escala or 1.0))
    return res


def aplicar(project, results: list) -> int:
    """Escribe en el proyecto los desplazamientos de `results` (solo los `cambio`). Devuelve cuántos aplicó."""
    n = 0
    for r in results:
        if r.error or not r.cambio:
            continue
        if r.lugar == "cintura" and project.waist is not None:
            from .waist import waist_plane
            nrm = waist_plane(project.waist).normal                         # normal simetrizada, hacia arriba
            old = project.waist.plane
            project.waist = dataclasses.replace(
                project.waist, plane=Plane.from_normal(np.asarray(old.origin, float) + nrm * r.desplazamiento_mm, old.normal, old.name))
            n += 1
        elif r.lugar == "muñeca" and project.hand is not None and len(project.hand.points) == 3:
            from .hand import WristFrame
            pts = np.asarray(project.hand.points, float)
            axis = WristFrame.from_points(np.column_stack([np.abs(pts[:, 0]), pts[:, 1], pts[:, 2]])).axis
            sgn = 1.0 if pts[0, 0] >= 0 else -1.0
            axis = axis * np.array([sgn, 1.0, 1.0])                         # el eje vive del lado +X; se refleja con los puntos
            moved = pts + axis * r.desplazamiento_mm
            project.hand = dataclasses.replace(project.hand, points=tuple(tuple(float(x) for x in q) for q in moved))
            n += 1
    return n


def format_ajuste(r: AjusteResult) -> list:
    """Líneas de texto de un `AjusteResult`."""
    if r.error:
        return [f"{r.lugar}: no se pudo ajustar: {r.error}"]
    f = lambda x: "—" if x is None else f"{x:.2f}".replace(".", ",")
    out = [f"{r.lugar}: radio {f(r.r_antes_mm)} mm; el patrón tiene {f(r.r_ref_mm)} mm"]
    if r.cambio:
        out.append(f"  → mover el plano {r.desplazamiento_mm:+.2f} mm; queda en {f(r.r_despues_mm)} mm"
                   f" (escala restante ×{f(r.escala)})" + ("" if r.ok else " — no alcanza"))
    else:
        out.append("  → sin cambios" + (f" (escala restante ×{f(r.escala)})" if r.escala else "") + ("" if r.ok else " — no alcanza"))
    out.extend(f"  aviso: {a}" for a in r.avisos)
    return out


# ---------------------------------------------------------------------------------------------------- referencias y CLI
def reference_radius(cat, tipo: str, clase: str, ref: str | None = None) -> tuple[float, float | None, str]:
    """(radio equivalente, altura de la figura de origen, ref) del enchufe `clase` de la pieza `tipo` del catálogo.

    Sin `ref` toma la única pieza de ese tipo; si hay varias, hay que decir cuál."""
    metas = [cat.meta(ref)] if ref else list(cat.list(tipo))
    metas = [m for m in metas if any(p.clase == clase and p.rol == "enchufe" and p.face is not None for p in m.puertos.values())]
    if not metas:
        raise ValueError(f"el catálogo no tiene una pieza «{tipo}» con enchufe de {clase} para usar de patrón")
    if len(metas) > 1:
        raise ValueError(f"hay {len(metas)} piezas «{tipo}» ({', '.join(m.ref for m in metas)}): elegí una con --{tipo}")
    m = metas[0]
    port = next(p for p in m.puertos.values() if p.clase == clase and p.rol == "enchufe" and p.face is not None)
    return float(port.face.radius_eq), m.altura_figura_mm, m.ref


def ajustar(project, body: S.Manifold, cat, pelvis: str | None = None, mano: str | None = None) -> list:
    """Ajusta cintura y muñeca del proyecto contra la pelvis y la mano del catálogo (no modifica el proyecto)."""
    out = []
    for tipo, clase, fn, lugar in (("pelvis", "cintura", fit_waist, "cintura"), ("mano", "muneca", fit_wrist, "muñeca")):
        try:
            r, h, _ref = reference_radius(cat, tipo, clase, pelvis if tipo == "pelvis" else mano)
        except ValueError as exc:
            out.append(AjusteResult(lugar, error=str(exc)))
            continue
        out.append(fn(project, body, r, h))
    return out
