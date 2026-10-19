"""Mecanismo de clic print-in-place para hombro y cadera (docs/PLAN_mecanismo_clic_hombro_cadera.md).

Un clic sin resorte es una **lengüeta impresa en voladizo** con una punta redondeada que cae en las muescas de un
**disco** (§2.1). Estructura (§3.1), la misma para el hombro y la cadera:

* el **pasador de hongo** de siempre queda como retención axial, pero desplazado `disc_h` mm hacia adentro del torso;
* el **disco de clic** va en el miembro (el que gira), entre la cara de contacto y el vástago, con `n_notches`
  muescas en el borde;
* el **bolsillo** del padre (torso) sale de la cavidad barrida del miembro + disco + pasador (como el hongo solo);
* **dos lengüetas** en el padre, arriba y abajo del bolsillo (±Z), enfrentadas para que las fuerzas sobre el disco
  se compensen. Van en el padre y no en la pieza que gira para que queden **horizontales al imprimir** (la
  tensión de flexión corre a lo largo de las capas: §2.4).

Desviación respecto del plan (§3.1), hallada al probarlo con Güemes de 13 cm: las lengüetas son **largas en X** (hacia
el centro del torso, de raíz a punta `finger_len` mm) y no en Y: junto a la cara del hombro el torso mide solo ≈13 mm
de profundidad. Siguen horizontales al imprimir (no cambia el argumento del §2.4).

La **fase** de las muescas sale de la pose de impresión (opción A del §3.4): los clics quedan en
`print_angle + k·360/n`, de modo que la pose impresa es un clic y la punta no se funde con el disco (§2.6).

Convención: todo se construye en la pose «de pie» (ángulo 0 del miembro, como el resto del motor). La pieza se exporta
girada `print_angle` (el hombro) o `sit_angle` (la cadera) alrededor del pasador.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import manifold3d as m3
import numpy as np
from shapely import affinity
from shapely.geometry import Point, Polygon, box as shp_box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from . import solids as S
from .params import DetentSpec, Params
from .pins import mushroom_pin

AXIS_TOL = 0.999        # |normal · X| mínimo: el disco y las lengüetas se construyen alineados con X
PRINT_UP = (0.0, 0.0, 1.0)   # «arriba» al imprimir (la figura sentada, cabeza arriba)
FREE_TOL_MM3 = 1e-3     # interferencia admitida al decidir que un ángulo está libre


# ------------------------------------------------------------------ cálculo mecánico (estimaciones)
def tip_flex(spec: DetentSpec) -> float:
    """Flexión de la punta al pasar de una muesca a otra: profundidad de muesca − holgura de impresión (§3.5)."""
    return spec.notch_depth - spec.detent_gap


def tongue_strain(spec: DetentSpec) -> float:
    """ε de un voladizo recto: 3·t·δ / (2·L²) (§2.2)."""
    return 1.5 * spec.finger_t * tip_flex(spec) / spec.finger_len ** 2


def tongue_force(spec: DetentSpec) -> float:
    """Fuerza (N) para flexar la punta δ: 3·E·I·δ / L³, con E en MPa y medidas en mm."""
    inertia = spec.finger_w * spec.finger_t ** 3 / 12.0
    return 3.0 * spec.elastic_modulus * inertia * tip_flex(spec) / spec.finger_len ** 3


def hold_torque(spec: DetentSpec) -> float:
    """Par de retención estimado (N·mm): 2 lengüetas · F · tan α · radio medio de la muesca. Estimación a calibrar."""
    return 2.0 * tongue_force(spec) * np.tan(np.radians(spec.flank_deg)) * (spec.disc_d / 2 - spec.notch_depth / 2)


def angular_play_deg(spec: DetentSpec) -> float:
    """Juego angular ±(gap·tan α)/R que deja la holgura de impresión punta/muesca (§2.5)."""
    return float(np.degrees(spec.detent_gap * np.tan(np.radians(spec.flank_deg)) / (spec.disc_d / 2)))


def click_angles(spec: DetentSpec, print_angle: float) -> list[float]:
    """Ángulos del miembro (pose de pie = 0°) en que la punta cae en una muesca: `print_angle + k·360/n` (opción A, §3.4)."""
    n = max(1, int(round(spec.n_notches)))
    return sorted({round((print_angle + k * 360.0 / n) % 360.0, 6) for k in range(n)})


def spec_for_nozzle(nozzle: float, base: DetentSpec | None = None) -> DetentSpec:
    """Perfil de línea del clic según la boquilla (§3.5): con 0,8 la lengüeta es más gruesa y más larga."""
    import dataclasses
    spec = dataclasses.replace(base or DetentSpec())
    if nozzle >= 0.7:
        spec.finger_t, spec.finger_len, spec.slit_w = 1.6, 12.0, 0.7
        spec.flex_gap = tip_flex(spec) + spec.slit_w
    elif nozzle >= 0.5:
        spec.finger_t, spec.finger_len, spec.slit_w = 1.4, 11.0, 0.6
        spec.flex_gap = tip_flex(spec) + spec.slit_w
    return spec


def fit_to_space(spec: DetentSpec, avail_len: float) -> tuple[DetentSpec, str | None]:
    """Acorta la lengüeta a `avail_len` mm (el torso no da más) y baja la profundidad de la muesca para seguir
    cumpliendo ε ≤ `max_strain` (ε = 1,5·t·δ/L²). Devuelve (spec, nota); nota None si no hizo falta. Si la muesca
    resultante es demasiado somera (δ < 0,2 mm) no se fuerza: la validación avisa."""
    import dataclasses
    if avail_len >= spec.finger_len - 1e-9:
        return spec, None
    L = max(avail_len, 1.0)
    delta = 0.95 * spec.max_strain * L ** 2 / (1.5 * spec.finger_t)
    notch = min(spec.notch_depth, delta + spec.detent_gap)
    new = dataclasses.replace(spec, finger_len=L, notch_depth=notch, flex_gap=(notch - spec.detent_gap) + spec.slit_w)
    note = (f"lengüeta acortada de {spec.finger_len:g} a {L:.2f} mm (el torso da {avail_len:.2f} mm); muesca "
            f"{spec.notch_depth:g} → {notch:.2f} mm para mantener ε ≤ {100 * spec.max_strain:g} %")
    return new, note


# ------------------------------------------------------------------ geometría
@dataclass
class Frame:
    """Sistema local del clic. `depth` crece desde la cara de contacto hacia adentro del torso (a lo largo de X con
    signo `s`: −1 para un miembro del lado +X). `u` = Y (con signo `s`, para que la base sea directa) y `v` = Z."""
    x_f: float
    cy: float
    cz: float
    s: float

    def X(self, depth: float) -> float:
        return self.x_f + self.s * depth

    def box(self, d0, d1, y0, y1, v0, v1) -> m3.Manifold:
        """Caja entre las profundidades d0 y d1, con y relativos a cy y z relativos a cz."""
        xa, xb = sorted((self.X(d0), self.X(d1)))
        return m3.Manifold.cube((xb - xa, y1 - y0, v1 - v0)).translate((xa, self.cy + y0, self.cz + v0))

    def extrude(self, poly: Polygon, d0: float, d1: float) -> m3.Manifold:
        """Polígono (u, v) extruido de la profundidad d0 a d1 (d1 > d0)."""
        pts = np.asarray(poly.exterior.coords)[:-1]
        if not poly.exterior.is_ccw:
            pts = pts[::-1]
        mf = m3.Manifold.extrude(m3.CrossSection([pts]), d1 - d0)
        s = self.s
        return mf.transform(np.array([[0, 0, s, self.X(d0)], [s, 0, 0, self.cy], [0, 1, 0, self.cz]], float))


def _groove(spec: DetentSpec) -> Polygon:
    """Muesca en +v: círculo de radio tip_r + gap con el fondo a `disc_d/2 − notch_depth`, y flancos a α."""
    rd = spec.disc_d / 2
    rg = spec.tip_r + spec.detent_gap
    vc = (rd - spec.notch_depth) + rg
    a = np.radians(spec.flank_deg)
    px, pv = rg * np.sin(a), vc - rg * np.cos(a)
    vt = rd + 0.6
    ut = px + (vt - pv) / np.sin(a) * np.cos(a)
    trap = Polygon([(-px, pv), (px, pv), (ut, vt), (-ut, vt)])
    return unary_union([trap, Point(0, vc).buffer(rg, 48)])


def disc_polygon(spec: DetentSpec, notched: bool = True) -> Polygon:
    disc = Point(0, 0).buffer(spec.disc_d / 2, 96)
    if not notched:
        return disc
    g = _groove(spec)
    n = max(1, int(round(spec.n_notches)))
    cuts = [affinity.rotate(g, -k * 360.0 / n, origin=(0, 0)) for k in range(n)]
    return disc.difference(unary_union(cuts))


@dataclass
class Finger:
    beam: m3.Manifold
    ridge: m3.Manifold
    slot: m3.Manifold


@dataclass
class DetentParts:
    frame: Frame
    spec: DetentSpec
    disc_plain: m3.Manifold      # disco liso (para tallar el bolsillo)
    disc_notch: m3.Manifold      # disco con muescas, ORIENTADO en la pose de impresión (muescas en ±Y, ±Z)
    fingers: list                # [Finger arriba, Finger abajo]
    tip_depth: tuple
    root_depth: float


def make_parts(center, out, spec: DetentSpec, head_r: float = 0.0) -> DetentParts:
    """Disco y lengüetas alrededor del centro del hombro/cadera (cara de contacto perpendicular a X).
    `head_r`: radio de la cabeza del hongo + holgura; la lengüeta pasa por encima de ella (cabezas de cadera Ø9,25 > disco)."""
    c = np.asarray(center, float)
    o = np.asarray(out, float)
    o = o / np.linalg.norm(o)
    if abs(o[0]) < AXIS_TOL:
        raise ValueError("el mecanismo de clic necesita la cara de contacto perpendicular a X "
                         f"(la normal forma {np.degrees(np.arccos(min(1.0, abs(o[0])))):.1f}° con X)")
    fr = Frame(float(c[0]), float(c[1]), float(c[2]), float(np.sign(o[0])))
    rd = spec.disc_d / 2
    # +0,3 mm sobre la cara: solapa con el miembro para que la unión sea sólida
    disc_plain = fr.extrude(disc_polygon(spec, False), -0.3, spec.disc_h)
    disc_notch = fr.extrude(disc_polygon(spec, True), -0.3, spec.disc_h)
    tip0, tip1 = 0.6, spec.disc_h - 0.5                      # apoyo axial de la punta dentro del espesor del disco
    tip_c = 0.5 * (tip0 + tip1)
    root = tip_c + spec.finger_len
    v_b = max(rd, head_r) + spec.slit_w                       # cara inferior de la lengüeta
    v_top = v_b + spec.finger_t
    vc_tip = (rd - spec.notch_depth + spec.detent_gap) + spec.tip_r      # centro de la punta
    w = spec.finger_w
    beam = fr.box(tip0, root + 1.0, -w / 2, w / 2, v_b, v_top)
    cyl = m3.Manifold.cylinder(tip1 - tip0, spec.tip_r, spec.tip_r, 48)
    # cilindro de eje X (profundidad) centrado en (cy, cz + vc_tip)
    ridge_cyl = cyl.transform(np.array([[0, 0, fr.s, fr.X(tip0)], [fr.s, 0, 0, fr.cy], [0, 1, 0, fr.cz + vc_tip]], float))
    ridge = S.union(ridge_cyl, fr.box(tip0, tip1, -spec.tip_r, spec.tip_r, vc_tip, v_b + 0.2))
    slot = fr.box(-0.8, root, -w / 2 - spec.slit_w, w / 2 + spec.slit_w, rd, v_top + spec.flex_gap)
    up = Finger(beam, ridge, slot)

    def flip(mf):
        return mf.mirror((0, 0, 1)).translate((0, 0, 2 * fr.cz))

    down = Finger(flip(beam), flip(ridge), flip(slot))
    return DetentParts(fr, spec, disc_plain, disc_notch, [up, down], (tip0, tip1), root)


# ------------------------------------------------------------------ barrido de 360° como sólido de revolución
def _even_odd(rings) -> list:
    """Polígonos (con agujeros) de una lista de anillos cerrados, por regla par-impar. Evita `Path2D.polygons_full`, que
    pide `rtree` (no es dependencia del proyecto) cuando el corte tiene varias componentes."""
    from shapely.geometry import MultiPolygon
    region = Polygon()
    for r in rings:
        r = np.asarray(r, float)
        if len(r) >= 3:
            poly = Polygon(r).buffer(0)
            if not poly.is_empty:
                region = region.symmetric_difference(poly)
    if region.is_empty:
        return []
    geoms = region.geoms if isinstance(region, MultiPolygon) or hasattr(region, "geoms") else [region]
    return [g for g in geoms if g.geom_type == "Polygon" and g.area > 1e-9]


def revolved_envelope(mf: m3.Manifold, center, axis, radius: float, step: float = 0.25, segments: int = 96) -> m3.Manifold:
    """Volumen que barre `mf` al dar la vuelta completa alrededor de (center, axis), ensanchado `radius` mm.

    Un giro completo genera un cuerpo de revolución: basta el perfil (t, r) = (posición sobre el eje, distancia al
    eje) que ocupa el sólido. En cada corte perpendicular al eje, cada componente conexa ocupa un intervalo
    [r_min, r_max]; se unen los intervalos de cortes consecutivos, se ensancha `radius` en 2D (la distancia a un cuerpo
    de revolución es la distancia a su perfil) y se revoluciona. Es exacto salvo el escalón de `step` y no depende de
    la cantidad de triángulos (los barridos por copias/Minkowski sobre ~300 k caras no terminan)."""
    import trimesh
    from shapely.geometry import MultiPolygon

    c = np.asarray(center, float)
    ax = np.asarray(axis, float)
    ax = ax / np.linalg.norm(ax)
    tm = S.to_trimesh(mf)
    t_all = (tm.vertices - c) @ ax
    t0, t1 = float(t_all.min()), float(t_all.max())
    n_cells = max(1, int(np.ceil((t1 - t0) / step)))
    eps = min(1e-3, step / 10)
    cells = []
    for i in range(n_cells):
        ta, tb = t0 + i * step, min(t0 + (i + 1) * step, t1 + step)      # la última celda puede pasarse del sólido
        spans = []
        # 3 cortes por celda (inicio, medio, fin): una cara perpendicular al eje o un escalón dentro de la celda no se pierde
        for t in (ta + eps, 0.5 * (ta + min(tb, t1)), min(tb, t1) - eps):
            t = float(np.clip(t, t0 + eps, t1 - eps))
            sec = tm.section(plane_origin=c + ax * t, plane_normal=ax)
            if sec is None:
                continue
            planar, to3d = (sec.to_2D() if hasattr(sec, "to_2D") else sec.to_planar())
            p2 = (np.linalg.inv(to3d) @ np.append(c + ax * t, 1.0))[:2]
            pt = Point(p2)
            for poly in _even_odd(planar.discrete):
                spans.append((float(poly.distance(pt)), float(np.max(np.hypot(*(np.asarray(poly.exterior.coords) - p2).T)))))
        spans.sort()
        merged = []
        for a, b in spans:
            if merged and a <= merged[-1][1] + 1e-9:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            cells.append(shp_box(a, ta - 1e-6, b, tb + 1e-6))
    if not cells:
        raise ValueError("revolved_envelope: el sólido no tiene corte con el eje")
    prof = unary_union(cells).buffer(radius, quad_segs=24)
    prof = prof.intersection(shp_box(0.0, -1e9, 1e9, 1e9)).simplify(0.01)
    k = 1.0 / np.cos(np.pi / segments)              # circunscribir: la faceta nunca queda por dentro del círculo
    parts = []
    for poly in (prof.geoms if isinstance(prof, MultiPolygon) else [prof]):
        if poly.is_empty or poly.area < 1e-6:
            continue
        poly = orient(affinity.scale(poly, xfact=k, yfact=1.0, origin=(0, 0)), 1.0)
        rings = [np.asarray(poly.exterior.coords)[:-1]] + [np.asarray(r.coords)[:-1] for r in poly.interiors]
        parts.append(m3.Manifold.revolve(m3.CrossSection(rings), segments))
    rev = S.union(*parts)
    # el revolve tiene el eje en Z; se lleva a (center, axis) con t = 0 en el centro
    from .planes import Plane
    return rev.transform(Plane.from_normal(c, ax).matrix()[:3, :])


# ------------------------------------------------------------------ construcción por miembro
@dataclass
class DetentLimb:
    name: str
    spec: DetentSpec
    parts: DetentParts
    center: np.ndarray
    axis: np.ndarray
    print_angle: float
    limb: m3.Manifold            # miembro de pie, con pasador desplazado y disco con muescas
    core: m3.Manifold            # miembro + pasador sin disco
    sweep: m3.Manifold           # volumen a tallar en el torso (miembro + disco + pasador ensanchados, barridos)
    sweep_range: tuple | None    # (a0, a1) del barrido discreto; None = vuelta completa (revolución)
    clicks: list                 # ángulos del miembro (de pie = 0°) con clic
    info: dict = field(default_factory=dict)
    head_r: float = 0.0          # radio de la cabeza del hongo + holgura (la lengüeta pasa por encima)


def detent_spec_for(limb_spec, p: Params) -> DetentSpec | None:
    """Mecanismo efectivo del miembro: su `detent` propio / el de Params, o None si usa solo el hongo."""
    mech = getattr(limb_spec, "mechanism", None)
    if mech is None:
        mech = "detent" if (p.hip_detent if limb_spec.kind == "leg" else p.shoulder_detent) else "mushroom"
    if mech not in ("mushroom", "detent"):
        raise ValueError(f"«{limb_spec.name}»: mecanismo desconocido {mech!r} (mushroom | detent)")
    return None if mech == "mushroom" else (getattr(limb_spec, "detent", None) or p.detent)


def build_detent_limb(name: str, limb_raw: m3.Manifold, face, axis, spec: DetentSpec, p: Params, pin_spec,
                      print_angle: float, sweep_range: tuple | None) -> DetentLimb:
    """Miembro con disco de clic + su volumen de tallado en el torso. `sweep_range` = (a0, a1) del barrido discreto
    (cadera: hasta `sit_angle + hip_margin`) o None para dar la vuelta completa (hombro: se talla como revolución)."""
    c = np.asarray(face.center, float)
    out = np.asarray(face.outward, float)
    out = out / np.linalg.norm(out)
    fit_note = None
    if out[0] < 0:        # el torso queda hacia −X: la lengüeta no puede pasar del plano de simetría (x = 0) menos la pared mínima
        tip_c = 0.5 * (0.6 + spec.disc_h - 0.5)
        spec, fit_note = fit_to_space(spec, abs(float(c[0])) - p.min_wall - tip_c)
    parts = make_parts(c, out, spec, pin_spec.head_d / 2 + p.clearance)
    pin = mushroom_pin(c + out * spec.disc_h, out, pin_spec)           # el hongo retiene detrás del disco
    core = S.union(limb_raw, pin)
    disc_standing = S.rotate_about_axis(parts.disc_notch, c, axis, -print_angle)
    limb = S.union(core, disc_standing)
    solid = S.union(core, parts.disc_plain)
    if sweep_range is None:
        sweep = revolved_envelope(solid, c, axis, p.clearance)
    else:
        a0, a1 = sweep_range
        grown = S.dilate(solid, p.clearance)
        angles = np.arange(a0, a1 + 1e-9, p.sweep_step) if a1 > a0 else [a0]
        if len(angles) > 1 and angles[-1] < a1 - 1e-9:
            angles = np.append(angles, a1)
        sweep = S.union(*[S.rotate_about_axis(grown, c, axis, a) for a in angles])
    clicks = click_angles(spec, print_angle)
    if sweep_range is not None:
        clicks = [a for a in clicks if sweep_range[0] - 1e-6 <= a <= sweep_range[1] + 1e-6]
    dl = DetentLimb(name, spec, parts, c, np.asarray(axis, float), float(print_angle), limb, core, sweep,
                    sweep_range, clicks)
    dl.head_r = pin_spec.head_d / 2 + p.clearance
    if fit_note:
        dl.info["ajuste_lengueta"] = fit_note
    return dl


def add_fingers(torso: m3.Manifold, detents: list) -> m3.Manifold:
    """Talla las ranuras de las lengüetas y suma lengüetas y puntas en el torso completo (ambos lados).

    Se hace sobre el torso ya decimado: las ranuras de 0,5 mm no sobreviven a una decimación agresiva."""
    slots, beams, ridges = [], [], []
    for dl in detents:
        for f in dl.parts.fingers:
            slots += [f.slot, S.mirror_x(f.slot)]
            beams += [f.beam, S.mirror_x(f.beam)]
            ridges += [f.ridge, S.mirror_x(f.ridge)]
    out = S.difference(torso, *slots)
    out = S.union(out, *beams, *ridges)
    return S.drop_small_components(out, 0.02)


# ------------------------------------------------------------------ validación
def finger_wall_mm(torso: m3.Manifold, dl: DetentLimb) -> float | None:
    """Pared de torso que queda sobre la lengüeta de arriba: de la ranura superior a la piel (rayo en +Z)."""
    from .pipeline import _ray_hits
    fr, sp = dl.parts.frame, dl.spec
    rd = sp.disc_d / 2
    v_top = max(rd, dl.head_r) + sp.slit_w + sp.finger_t
    x = fr.X(0.5 * (dl.parts.tip_depth[0] + dl.parts.root_depth))
    origin = np.array([x, fr.cy, fr.cz + v_top + sp.flex_gap - 0.02])
    t = _ray_hits(np.asarray(S.to_trimesh(torso).triangles, float), origin, (0.0, 0.0, 1.0))
    if len(t) == 0:
        return None
    return float(t[1] - t[0]) if len(t) >= 2 else float(t[0])


def protrusion_mm3(dl: DetentLimb, body: m3.Manifold | None) -> float:
    """Volumen de lengüetas y puntas fuera de la figura original (se vería como un bulto en la piel)."""
    if body is None:
        return 0.0
    both = S.union(*[g for f in dl.parts.fingers for g in (f.beam, f.ridge)])
    return float(S.difference(both, body, S.mirror_x(body)).volume())


def click_table(dl: DetentLimb) -> dict:
    """Por cada giro múltiplo de media muesca desde la pose impresa: interferencia y distancia punta–disco.
    En los clics la interferencia debe ser 0 y la distancia ≈ `detent_gap`; entre clics la punta apoya sobre el flanco
    (interferencia elástica: la lengüeta se flexa)."""
    ridges = S.union(*[f.ridge for f in dl.parts.fingers])
    n = max(1, int(round(dl.spec.n_notches)))
    out = {}
    for k in range(2 * n):
        d = k * 180.0 / n
        disc = S.rotate_about_axis(dl.parts.disc_notch, dl.center, dl.axis, d)
        out[f"{d:g}"] = {"interf_punta_disco_mm3": round(float((ridges ^ disc).volume()), 4),
                         "dist_punta_disco_mm": round(float(ridges.min_gap(disc, 3.0)), 3)}
    return out


def free_range(dl: DetentLimb, blockers: dict, step: float = 10.0) -> dict:
    """Ángulos (del miembro de pie = 0°) en que el miembro gira sin tocar ninguna de las piezas `blockers`
    (torso sin lengüetas, otras piezas, cabeza). Devuelve el mapa de interferencias y los clics alcanzables/descartados
    (plan §2.7: el motor informa el rango libre real, no lo oculta)."""
    angles = sorted({float(a) for a in np.arange(0.0, 360.0, step)} | {float(a) for a in dl.clicks})
    if dl.sweep_range is not None:
        angles = [a for a in angles if dl.sweep_range[0] - 1e-6 <= a <= dl.sweep_range[1] + 1e-6]
    hits, free = {}, []
    for a in angles:
        rot = S.rotate_about_axis(dl.limb, dl.center, dl.axis, a)
        v = {nm: float((rot ^ mf).volume()) for nm, mf in blockers.items()}
        bad = {nm: round(x, 4) for nm, x in v.items() if x > FREE_TOL_MM3}
        hits[f"{a:g}"] = bad
        if not bad:
            free.append(a)
    reach = [a for a in dl.clicks if a in free]
    return {"paso_grados": step, "angulos_libres": free, "interferencias": {k: v for k, v in hits.items() if v},
            "clics": list(dl.clicks), "clics_alcanzables": reach,
            "clics_descartados": [a for a in dl.clicks if a not in free]}


def summary(dl: DetentLimb) -> dict:
    sp = dl.spec
    return {"mecanismo": "clic", "n_muescas": int(round(sp.n_notches)), "angulo_impresion": dl.print_angle,
            "clics": list(dl.clicks), "barrido": "vuelta completa (revolución)" if dl.sweep_range is None else list(dl.sweep_range),
            "deformacion_pct": round(100 * tongue_strain(sp), 3), "deformacion_ok": bool(tongue_strain(sp) <= sp.max_strain),
            "fuerza_flexion_N": round(tongue_force(sp), 3), "par_retencion_est_Nmm": round(hold_torque(sp), 2),
            "juego_angular_deg": round(angular_play_deg(sp), 2),
            "raiz_x": round(dl.parts.frame.X(dl.parts.root_depth), 3), "lado_torso": int(dl.parts.frame.s),
            "ajuste_lengueta": dl.info.get("ajuste_lengueta"),
            "punta_x": [round(dl.parts.frame.X(d), 3) for d in dl.parts.tip_depth],
            "spec": {k: getattr(sp, k) for k in sp.__dataclass_fields__}}
