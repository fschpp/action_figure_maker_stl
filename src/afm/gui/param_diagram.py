"""Diagrama con cotas estilo CAD de los parámetros de articulación (motor puro: no depende de GTK).

Cada parámetro de `PARAM_FIELDS` pertenece a una ESCENA (un dibujo a escala en mm, con sus cotas). La escena se arma con
los valores actuales: al cambiar un valor el dibujo cambia de verdad (el vástago se alarga, el ángulo se abre…). Las cotas y
formas del parámetro activo se resaltan. `Animator` produce el vaivén que se ve al hacer clic en una etiqueta.

El dibujo se hace sobre un `Canvas` mínimo (líneas, polígonos, círculos, arcos y texto): la ventana usa uno de cairo y las
pruebas / la vista previa uno de SVG (`SvgCanvas`). Coordenadas de escena: mm, x a la derecha, y hacia arriba.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..i18n import tr

Pt = tuple[float, float]

# ------------------------------------------------------------------------------------------------ colores
LIGHT = {"bg": "#f6f5f4", "body": "#dcdad5", "body_edge": "#5e5c64", "pin": "#99c1f1", "pin_edge": "#1a5fb4",
         "ghost": "#9a9996", "dim": "#77767b", "dim_active": "#c64600", "text": "#3d3846", "axis": "#a51d2d",
         "hot": "#ffbe6f", "hot_edge": "#c64600"}
DARK = {"bg": "#2b2b2b", "body": "#4a4a4a", "body_edge": "#b0aeb6", "pin": "#3a5f8f", "pin_edge": "#99c1f1",
        "ghost": "#77767b", "dim": "#9a9996", "dim_active": "#ffa348", "text": "#deddda", "axis": "#f66151",
        "hot": "#7a5a2a", "hot_edge": "#ffa348"}


class Canvas(Protocol):
    """Lo mínimo que hace falta para dibujar una escena (coordenadas de pantalla: y hacia abajo)."""

    def line(self, p0: Pt, p1: Pt, color: str, width: float = 1.0, dash: tuple | None = None) -> None: ...
    def poly(self, pts: list[Pt], fill: str | None, stroke: str | None, width: float = 1.0, dash: tuple | None = None,
             closed: bool = True) -> None: ...
    def circle(self, c: Pt, r: float, fill: str | None, stroke: str | None, width: float = 1.0, dash: tuple | None = None) -> None: ...
    def text(self, p: Pt, s: str, color: str, size: float = 11.0, anchor: str = "middle", bg: str | None = None) -> None: ...
    def text_width(self, s: str, size: float) -> float: ...


# ------------------------------------------------------------------------------------------------ escena
@dataclass
class Shape:
    """`kind`: poly | circle | line | arc. `keys`: parámetros a los que pertenece (se resalta si uno está activo)."""
    kind: str
    pts: list[Pt]
    style: str = "body"                  # body | pin | ghost | axis | edge
    r: float = 0.0
    keys: tuple = ()
    closed: bool = True
    a0: float = 0.0                      # arc: grados (antihorario)
    a1: float = 0.0


@dataclass
class Dim:
    """Cota. `lin`/`dia`: entre `a` y `b` (mm), línea de cota desplazada `off` px; `ang`: arco de `a0` a `a1` grados con
    centro `a` y radio `off` px; `note`: texto con una guía desde `a` hacia `b`."""
    key: str
    kind: str
    a: Pt
    b: Pt = (0.0, 0.0)
    off: float = 18.0
    text: str = ""
    a0: float = 0.0
    a1: float = 0.0
    outside: bool = False                # flechas por fuera (cotas muy chicas)


@dataclass
class Scene:
    sid: tuple
    shapes: list[Shape] = field(default_factory=list)
    dims: list[Dim] = field(default_factory=list)

    def keys(self) -> set[str]:
        return {d.key for d in self.dims if d.key} | {k for s in self.shapes for k in s.keys}

    def bbox(self) -> tuple[float, float, float, float]:
        xs, ys = [], []
        for s in self.shapes:
            if s.kind == "circle":
                xs += [s.pts[0][0] - s.r, s.pts[0][0] + s.r]
                ys += [s.pts[0][1] - s.r, s.pts[0][1] + s.r]
            elif s.kind == "arc":
                xs += [s.pts[0][0] - s.r, s.pts[0][0] + s.r]
                ys += [s.pts[0][1] - s.r, s.pts[0][1] + s.r]
            else:
                xs += [p[0] for p in s.pts]
                ys += [p[1] for p in s.pts]
        for d in self.dims:
            if d.kind in ("lin", "dia", "note"):
                xs += [d.a[0], d.b[0]]
                ys += [d.a[1], d.b[1]]
        if not xs:
            return (-1.0, -1.0, 1.0, 1.0)
        return (min(xs), min(ys), max(xs), max(ys))


def _fmt(v: float, digits: int = 2) -> str:
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return (s or "0").replace(".", ",")


def _rot(p: Pt, deg: float, c: Pt = (0.0, 0.0)) -> Pt:
    a = math.radians(deg)
    x, y = p[0] - c[0], p[1] - c[1]
    return (c[0] + x * math.cos(a) - y * math.sin(a), c[1] + x * math.sin(a) + y * math.cos(a))


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[Pt]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _hatch(x0: float, y0: float, x1: float, y1: float, step: float) -> list[Shape]:
    """Rayado a 45° dentro de un rectángulo (corte)."""
    out, k = [], x0 - (y1 - y0)
    while k < x1:
        a, b = (k, y0), (k + (y1 - y0), y1)
        ax, ay, bx, by = a[0], a[1], b[0], b[1]
        if ax < x0:
            ay += x0 - ax
            ax = x0
        if bx > x1:
            by -= bx - x1
            bx = x1
        if ax < bx and ay < by:
            out.append(Shape("line", [(ax, ay), (bx, by)], style="hatch", closed=False))
        k += step
    return out


# ------------------------------------------------------------------------------------------------ escenas
def _label(sc: Scene, text: str, a: Pt, b: Pt) -> None:
    """Rótulo con guía que no pertenece a ningún parámetro (nombra una pieza del dibujo)."""
    sc.dims.append(Dim("", "note", a, b, 0.0, text))


def _axis_h(x0: float, x1: float, y: float = 0.0) -> Shape:
    return Shape("line", [(x0, y), (x1, y)], style="axis", closed=False)


def scene_pin(v: Callable[[str], float], prefix: str) -> Scene:
    """Corte por el eje de un pasador de hongo: vástago fijo al cuerpo, pared del miembro entre cuerpo y cabeza (con la holgura
    a cada lado) y la cabeza. Eje horizontal."""
    d, L = v(f"{prefix}.shaft_d"), v(f"{prefix}.shaft_len")
    hd, hh = v(f"{prefix}.head_d"), v(f"{prefix}.head_h")
    c = v("clearance")
    ext = max(hd, d + 2 * c) / 2 + 3.0
    sc = Scene(("pin", prefix))
    S = sc.shapes
    S.append(Shape("poly", _rect(-6.0, -ext, 0.0, ext), "body"))                          # cuerpo (torso / pelvis)
    S.append(Shape("poly", _rect(0.0, -d / 2, L + hh, d / 2) if False else _rect(0.0, -d / 2, L, d / 2), "pin", keys=(f"{prefix}.shaft_d", f"{prefix}.shaft_len")))
    S.append(Shape("poly", _rect(L, -hd / 2, L + hh, hd / 2), "pin", keys=(f"{prefix}.head_d", f"{prefix}.head_h")))
    # pared del miembro: entre la cara (+holgura) y la cabeza (−holgura), con el agujero del vástago + holgura
    w0, w1 = c, max(c + 0.05, L - c)
    hole = d / 2 + c
    S.append(Shape("poly", _rect(w0, hole, w1, ext), "body", keys=("clearance",)))
    S.append(Shape("poly", _rect(w0, -ext, w1, -hole), "body", keys=("clearance",)))
    S += _hatch(w0, hole, w1, ext, 0.9) + _hatch(w0, -ext, w1, -hole, 0.9)
    S.append(_axis_h(-7.0, L + hh + 2.5))
    dm = sc.dims
    dm.append(Dim(f"{prefix}.shaft_d", "dia", (0.0, -d / 2), (0.0, d / 2), 30.0, "Ø" + _fmt(d, 1)))
    dm.append(Dim(f"{prefix}.shaft_len", "lin", (0.0, -ext), (L, -ext), -16.0, _fmt(L, 1)))
    dm.append(Dim(f"{prefix}.head_d", "dia", (L + hh, -hd / 2), (L + hh, hd / 2), -24.0, "Ø" + _fmt(hd, 1)))
    dm.append(Dim(f"{prefix}.head_h", "lin", (L, hd / 2), (L + hh, hd / 2), 16.0, _fmt(hh, 2)))
    ym = -(hole + (ext - hole) / 2)
    dm.append(Dim("clearance", "lin", (0.0, ym), (w0, ym), 0.0, _fmt(c, 2), outside=True))
    dm.append(Dim("clearance", "lin", (w1, ym), (L, ym), 0.0, _fmt(c, 2), outside=True))
    _label(sc, tr("diagrama.p_cuerpo"), (-3.0, ext * 0.45), (-6.6, ext * 0.45))
    _label(sc, tr("diagrama.p_pared"), ((w0 + w1) / 2, ext * 0.8), ((w0 + w1) / 2 + 2.0, ext + 1.2))
    _label(sc, tr("diagrama.p_pasador"), (L + hh * 0.5, 0.0), (L + hh + 4.0, 0.0))
    return sc


def scene_face(v: Callable[[str], float]) -> Scene:
    """Cara de contacto cuerpo / miembro: holgura entre las dos y la rebaba que se rebaja en el borde del miembro."""
    c, b = v("clearance"), v("burr")
    sc = Scene(("face",))
    S = sc.shapes
    h = 14.0
    S.append(Shape("poly", _rect(-8.0, -h, 0.0, h), "body"))
    # miembro: su cara está a `c` de la del cuerpo; en el borde inferior lleva la rebaba (escalón b × b) que se rebaja
    limb = [(c, -h + b), (c + b, -h + b), (c + b, -h), (c + 9.0, -h), (c + 9.0, h), (c, h)]
    S.append(Shape("poly", limb, "body", keys=("burr",)))
    S.append(Shape("poly", [(c, -h), (c + b, -h), (c + b, -h + b), (c, -h + b)], "ghost", keys=("burr",)))
    S.append(Shape("line", [(c, -h), (c, -h + b)], style="ghost", closed=False))
    S += _hatch(-8.0, -h, 0.0, h, 1.2)
    sc.dims.append(Dim("clearance", "lin", (0.0, 0.0), (c, 0.0), 20.0, _fmt(c, 2), outside=True))
    sc.dims.append(Dim("burr", "lin", (c + b, -h), (c + b, -h + b), 22.0, _fmt(b, 2), outside=True))
    return sc


def _limb_angle(sid: tuple, v: Callable[[str], float], ang: float, ang_key: str, margin: float = 0.0,
                margin_key: str | None = None) -> Scene:
    """Vista de costado: torso fijo, muslo / brazo que cuelga y se gira `ang` hacia adelante (+x)."""
    sc = Scene(sid)
    S = sc.shapes
    piv = (0.0, 0.0)
    S.append(Shape("poly", [(-7.0, 0.0), (7.0, 0.0), (8.0, 26.0), (-8.0, 26.0)], "body"))          # torso sobre el pivote
    n = 26.0
    w = 4.2
    base = [(-w, 0.0), (w, 0.0), (w, -n), (-w, -n)]
    S.append(Shape("poly", base, "ghost", closed=True))                                                 # estirado (0°)
    S.append(Shape("poly", [_rot(p, -ang, piv) for p in base], "pin", keys=(ang_key,)))
    if margin_key:
        S.append(Shape("poly", [_rot(p, -(ang + margin), piv) for p in base], "ghost", keys=(margin_key,)))
    S.append(Shape("circle", [piv], "edge", r=1.2))
    r_px = 54.0
    sc.dims.append(Dim(ang_key, "ang", piv, off=r_px, text=_fmt(ang, 0) + "°", a0=-90.0, a1=-90.0 + ang))
    if margin_key:
        sc.dims.append(Dim(margin_key, "ang", piv, off=r_px + 22.0, text="+" + _fmt(margin, 0) + "°", a0=-90.0 + ang, a1=-90.0 + ang + margin))
    return sc


def scene_sit(v: Callable[[str], float]) -> Scene:
    return _limb_angle(("sit",), v, v("sit_angle"), "sit_angle", v("hip_margin"), "hip_margin")


def scene_print_angle(v: Callable[[str], float]) -> Scene:
    return _limb_angle(("print",), v, v("detent.print_angle"), "detent.print_angle")


def scene_mirror(v: Callable[[str], float]) -> Scene:
    """Torso de frente: con espejo la mitad derecha es el reflejo de la izquierda; sin espejo conserva su propio detalle."""
    on = v("torso_mirror") >= 0.5
    sc = Scene(("mirror",))
    S = sc.shapes
    outline = [(-12.0, -14.0), (12.0, -14.0), (14.0, 12.0), (6.0, 16.0), (-6.0, 16.0), (-14.0, 12.0)]
    S.append(Shape("poly", outline, "body"))
    S.append(Shape("circle", [(-6.0, 4.0)], "pin", r=3.0))                                                # detalle del lado de trabajo
    if on:
        S.append(Shape("circle", [(6.0, 4.0)], "ghost", r=3.0, keys=("torso_mirror",)))                   # su reflejo
    else:
        S.append(Shape("poly", _rect(3.5, 1.0, 8.5, 7.0), "pin", keys=("torso_mirror",)))                 # detalle propio, distinto
    S.append(Shape("line", [(0.0, -18.0), (0.0, 19.0)], style="axis", closed=False, keys=("torso_mirror",)))
    sc.dims.append(Dim("torso_mirror", "note", (7.0, -9.0), (7.0, -9.0), 0.0, tr("diagrama.espejo") if on else tr("diagrama.propio")))
    return sc


def scene_flat(v: Callable[[str], float]) -> Scene:
    """«Sin soportes»: la cola de la pierna (círculo) se corta a `flat_depth` sobre su punto más bajo y apoya en la cama."""
    dpt = min(v("flat_depth"), 9.0)
    sc = Scene(("flat",))
    R = 10.0
    cy = R
    body, cut = [], []
    for i in range(0, 361, 6):
        a = math.radians(i)
        p = (R * math.cos(a), cy + R * math.sin(a))
        (body if p[1] >= dpt else cut).append(p)
    seg = [(math.sqrt(max(R * R - (dpt - cy) ** 2, 0.0)) * s, dpt) for s in (1, -1)]
    sc.shapes.append(Shape("poly", [p for p in body] + [], "body"))
    sc.shapes.append(Shape("circle", [(0.0, cy)], "ghost", r=R, keys=("flat_depth",)))
    sc.shapes.append(Shape("poly", [(-16.0, dpt), (16.0, dpt)], "axis", closed=False, keys=("flat_depth",)))
    sc.shapes.append(Shape("line", [(-16.0, 0.0), (16.0, 0.0)], style="ghost", closed=False))
    sc.dims.append(Dim("flat_depth", "lin", (R + 3.0, 0.0), (R + 3.0, dpt), 22.0, _fmt(dpt, 1), outside=True))
    del seg
    return sc


def scene_waist(v: Callable[[str], float]) -> Scene:
    """Cintura vista desde arriba: el torso gira `waist_twist` a cada lado respecto de la pelvis."""
    tw = v("waist_twist")
    sc = Scene(("waist",))
    S = sc.shapes
    S.append(Shape("poly", _rect(-11.0, -12.0, 11.0, -3.0), "body"))
    box = _rect(-10.0, -2.0, 10.0, 8.0)
    box = [(x, y + 0.0) for x, y in box]
    S.append(Shape("poly", box, "ghost"))
    S.append(Shape("poly", [_rot(p, -tw, (0.0, 0.0)) for p in box], "pin", keys=("waist_twist",)))
    S.append(Shape("poly", [_rot(p, tw, (0.0, 0.0)) for p in box], "pin", keys=("waist_twist",)))
    S.append(Shape("circle", [(0.0, 0.0)], "edge", r=1.2))
    sc.dims.append(Dim("waist_twist", "ang", (0.0, 0.0), off=62.0, text="±" + _fmt(tw, 0) + "°", a0=90.0, a1=90.0 + tw))
    sc.dims.append(Dim("waist_twist", "ang", (0.0, 0.0), off=62.0, text="", a0=90.0, a1=90.0 - tw))
    return sc


def scene_disc(v: Callable[[str], float]) -> Scene:
    """Disco de clic visto desde arriba (muescas + punta de la lengüeta) y, debajo, de costado (Ø y alto)."""
    n = max(2, int(round(v("detent.n_notches"))))
    D, depth, flank = v("detent.disc_d"), v("detent.notch_depth"), v("detent.flank_deg")
    tip, gap, hdisc = v("detent.tip_r"), v("detent.detent_gap"), v("detent.disc_h")
    on = v("shoulder_detent") >= 0.5 or v("hip_detent") >= 0.5
    st = "body" if on else "ghost"
    R = D / 2
    beta = math.radians(max(5.0, min(85.0, 90.0 - flank)))                    # semiángulo del V respecto del radio
    hw = min(depth * math.tan(beta), R * math.sin(math.pi / n) * 0.9)         # semiancho de la muesca en la superficie
    dang = math.asin(min(hw / R, 0.99))
    pts: list[Pt] = []
    for k in range(n):
        a = 2 * math.pi * k / n + math.pi / 2                                # la muesca 0 mira hacia arriba
        a_next = 2 * math.pi * (k + 1) / n + math.pi / 2
        pts.append((R * math.cos(a - dang), R * math.sin(a - dang)) if False else (0, 0))
        pts.pop()
        pts.append((R * math.cos(a - dang), R * math.sin(a - dang)))
        pts.append(((R - depth) * math.cos(a), (R - depth) * math.sin(a)))
        pts.append((R * math.cos(a + dang), R * math.sin(a + dang)))
        steps = 8
        lo, hi = a + dang, a_next - dang
        for i in range(1, steps):
            t = lo + (hi - lo) * i / steps
            pts.append((R * math.cos(t), R * math.sin(t)))
    top = 14.0
    cy = top
    pts = [(x, y + cy) for x, y in pts]
    sc = Scene(("disc",))
    S = sc.shapes
    S.append(Shape("poly", pts, st, keys=("detent.n_notches", "detent.notch_depth", "detent.flank_deg", "shoulder_detent", "hip_detent")))
    S.append(Shape("circle", [(0.0, cy)], "edge", r=1.0))
    tc = (R - depth) + (tip + gap) / max(math.sin(beta), 0.2)                # centro de la punta apoyada en el fondo del V
    S.append(Shape("circle", [(0.0, cy + tc)], "pin", r=tip, keys=("detent.tip_r", "detent.detent_gap")))
    S.append(Shape("poly", _rect(-1.0, cy + tc, 1.0, cy + R + 4.0), "pin", keys=("detent.tip_r",)))
    # vista de costado
    y0 = -hdisc - 4.0
    S.append(Shape("poly", _rect(-R, y0, R, y0 + hdisc), st, keys=("detent.disc_d", "detent.disc_h")))
    sc.dims.append(Dim("detent.disc_d", "dia", (-R, y0), (R, y0), -22.0, "Ø" + _fmt(D, 1)))
    sc.dims.append(Dim("detent.disc_h", "lin", (R, y0), (R, y0 + hdisc), 22.0, _fmt(hdisc, 1)))
    # profundidad: en la muesca de la izquierda (a 180° − 90°·…: usamos la de la derecha cuando hay n par)
    sc.dims.append(Dim("detent.notch_depth", "note", (-(R - depth / 2), cy), (-(R + 4.0), cy + 4.0), 0.0, _fmt(depth, 2)))
    sc.dims.append(Dim("detent.flank_deg", "note", ((R - depth / 2) * 0.0 + hw * 0.6, cy + R - depth * 0.4), (hw + 6.0, cy + R + 6.5), 0.0, _fmt(flank, 0) + "°"))
    sc.dims.append(Dim("detent.n_notches", "note", (-R * 0.55, cy + R * 0.55), (-R - 3.0, cy + R + 3.0), 0.0, "×" + str(n)))
    if not on:
        _label(sc, tr("diagrama.clic_apagado"), (0.0, cy), (R * 0.6, cy - R * 0.2))
    sc.dims.append(Dim("detent.tip_r", "note", (tip * 0.7, cy + tc + tip * 0.7), (R * 0.9 + 3.0, cy + tc + 7.0), 0.0, "R" + _fmt(tip, 2)))
    sc.dims.append(Dim("detent.detent_gap", "note", (hw * 0.5, cy + R - depth + 0.2), (R + 5.0, cy + R - depth - 3.0), 0.0, _fmt(gap, 2)))
    return sc


def scene_finger(v: Callable[[str], float]) -> Scene:
    """Lengüeta del clic en el cuerpo padre: ranura alrededor (lateral, inferior, superior de flexión) y vista de frente."""
    Lf, tf, wf = v("detent.finger_len"), v("detent.finger_t"), v("detent.finger_w")
    slit, flex, tip = v("detent.slit_w"), v("detent.flex_gap"), v("detent.tip_r")
    sc = Scene(("finger",))
    S = sc.shapes
    pad = 3.0
    x1 = Lf
    ytop = tf / 2 + flex + 2.0
    block = _rect(-2.0 - slit, -tf / 2 - slit - 2.0, Lf + pad, ytop)
    S.append(Shape("poly", block, "body"))
    S += _hatch(-2.0 - slit, -tf / 2 - slit - 2.0, Lf + pad, -tf / 2 - slit, 1.0)
    S.append(Shape("poly", _rect(-2.0, tf / 2 + flex, Lf + pad, ytop), "body"))
    # ranuras (vacío): sobre y bajo la lengüeta
    S.append(Shape("poly", _rect(0.0, -tf / 2 - slit, x1, -tf / 2), "bg", keys=("detent.slit_w",)))
    S.append(Shape("poly", _rect(0.0, tf / 2, x1, tf / 2 + flex), "bg", keys=("detent.flex_gap",)))
    S.append(Shape("poly", _rect(0.0, -tf / 2, x1, tf / 2), "pin", keys=("detent.finger_len", "detent.finger_t")))
    S.append(Shape("circle", [(x1, 0.0)], "pin", r=min(tip, tf / 2), keys=("detent.tip_r",)))
    sc.dims.append(Dim("detent.finger_len", "lin", (0.0, -tf / 2 - slit - 2.0), (x1, -tf / 2 - slit - 2.0), -16.0, _fmt(Lf, 1)))
    sc.dims.append(Dim("detent.finger_t", "lin", (x1 * 0.5, -tf / 2), (x1 * 0.5, tf / 2), 0.0, _fmt(tf, 1), outside=True))
    sc.dims.append(Dim("detent.slit_w", "lin", (x1 * 0.8, -tf / 2 - slit), (x1 * 0.8, -tf / 2), 0.0, _fmt(slit, 2), outside=True))
    sc.dims.append(Dim("detent.flex_gap", "lin", (x1 * 0.8, tf / 2), (x1 * 0.8, tf / 2 + flex), 0.0, _fmt(flex, 2), outside=True))
    # vista de frente (a la derecha): ancho de la lengüeta
    fx = Lf + pad + 9.0
    S.append(Shape("poly", _rect(fx, -tf / 2, fx + wf, tf / 2), "pin", keys=("detent.finger_w", "detent.finger_t")))
    sc.dims.append(Dim("detent.finger_w", "lin", (fx, -tf / 2), (fx + wf, -tf / 2), -18.0, _fmt(wf, 1)))
    return sc


def scene_hinge_face(v: Callable[[str], float]) -> Scene:
    """Bisagra vista a lo largo del eje: lengüeta (con cuello recto o disco suelto) y el bulón."""
    pd, er, neck = v("hinge_pin.pin_d"), v("hinge_pin.end_r"), v("hinge_pin.neck") >= 0.5
    sc = Scene(("hinge_face",))
    S = sc.shapes
    pts: list[Pt] = []
    for i in range(0, 181, 6):                                           # media circunferencia superior
        a = math.radians(i)
        pts.append((er * math.cos(a), er * math.sin(a)))
    if neck:
        pts += [(-er, -12.0), (er, -12.0)]
    else:
        for i in range(180, 361, 6):
            a = math.radians(i)
            pts.append((er * math.cos(a), er * math.sin(a)))
    S.append(Shape("poly", pts, "body", keys=("hinge_pin.end_r", "hinge_pin.neck")))
    S.append(Shape("circle", [(0.0, 0.0)], "pin", r=pd / 2, keys=("hinge_pin.pin_d",)))
    S.append(Shape("line", [(-er - 3.0, 0.0), (er + 3.0, 0.0)], style="axis", closed=False))
    S.append(Shape("line", [(0.0, -er - 2.0), (0.0, er + 3.0)], style="axis", closed=False))
    sc.dims.append(Dim("hinge_pin.pin_d", "note", (pd * 0.35, -pd * 0.35), (er * 0.9 + 6.0, -er * 0.7), 0.0, "Ø" + _fmt(pd, 1)))
    sc.dims.append(Dim("hinge_pin.end_r", "note", (er * math.cos(math.radians(40)), er * math.sin(math.radians(40))),
                       (er + 8.0, er + 5.0), 0.0, "R" + _fmt(er, 1)))
    sc.dims.append(Dim("hinge_pin.neck", "note", (-er, -8.0), (-er - 9.0, -8.0), 0.0, tr("diagrama.cuello") if neck else tr("diagrama.disco")))
    return sc


def scene_hinge_section(v: Callable[[str], float]) -> Scene:
    """Bisagra en corte por el eje: ranura del brazo, lengüeta (`tongue_t`) y bulones o bastago pasante."""
    pd, pl, tt = v("hinge_pin.pin_d"), v("hinge_pin.pin_len"), v("hinge_pin.tongue_t")
    rod, wall = v("hinge_pin.rod") >= 0.5, v("hinge_pin.rod_wall")
    ax = v("clearance") * 0 + 0.35                                         # holgura axial (fija en `Params`)
    sc = Scene(("hinge_section",))
    S = sc.shapes
    H = 9.0
    inner = tt + 2 * ax
    cheek = 4.0 if not rod else max(pl, 0.0) + wall + 1.0
    S.append(Shape("poly", _rect(-inner / 2 - cheek, -H, -inner / 2, H), "body"))
    S.append(Shape("poly", _rect(inner / 2, -H, inner / 2 + cheek, H), "body"))
    S += _hatch(-inner / 2 - cheek, -H, -inner / 2, H, 1.0) + _hatch(inner / 2, -H, inner / 2 + cheek, H, 1.0)
    S.append(Shape("poly", _rect(-tt / 2, -H, tt / 2, H), "pin", keys=("hinge_pin.tongue_t",)))
    if rod:
        reach = inner / 2 + cheek - wall
        S.append(Shape("poly", _rect(-reach, -pd / 2, reach, pd / 2), "pin", keys=("hinge_pin.rod", "hinge_pin.pin_d", "hinge_pin.rod_wall", "hinge_pin.pin_len")))
        sc.dims.append(Dim("hinge_pin.rod_wall", "lin", (reach, pd * 1.5), (inner / 2 + cheek, pd * 1.5), 18.0, _fmt(wall, 1), outside=True))
    else:
        for sgn in (-1, 1):
            S.append(Shape("poly", _rect(min(sgn * tt / 2, sgn * (tt / 2 + pl)), -pd / 2, max(sgn * tt / 2, sgn * (tt / 2 + pl)), pd / 2), "pin",
                           keys=("hinge_pin.pin_len", "hinge_pin.pin_d")))
        sc.dims.append(Dim("hinge_pin.pin_len", "lin", (tt / 2, pd / 2), (tt / 2 + pl, pd / 2), 22.0, _fmt(pl, 1), outside=True))
    S.append(Shape("line", [(0.0, -H - 2.0), (0.0, H + 2.0)], style="axis", closed=False))
    sc.dims.append(Dim("hinge_pin.tongue_t", "lin", (-tt / 2, -H), (tt / 2, -H), -18.0, _fmt(tt, 1), outside=True))
    return sc


# ------------------------------------------------------------------------------------------------ catálogo de escenas
def scene_id_for_key(key: str) -> tuple:
    if key in ("clearance",):
        return ("pin", "shoulder_pin")
    if key == "burr":
        return ("face",)
    if key in ("sit_angle", "hip_margin"):
        return ("sit",)
    if key == "torso_mirror":
        return ("mirror",)
    if key == "flat_depth":
        return ("flat",)
    if key == "waist_twist":
        return ("waist",)
    if "_pin." in key and key.split(".")[0] in ("shoulder_pin", "hip_pin", "waist_pin", "hand_pin"):
        return ("pin", key.split(".")[0])
    if key in ("shoulder_detent", "hip_detent"):
        return ("disc",)
    if key == "detent.print_angle":
        return ("print",)
    if key in ("detent.finger_len", "detent.finger_t", "detent.finger_w", "detent.slit_w", "detent.flex_gap"):
        return ("finger",)
    if key.startswith("detent."):
        return ("disc",)
    if key in ("hinge_pin.pin_d", "hinge_pin.end_r", "hinge_pin.neck"):
        return ("hinge_face",)
    if key.startswith("hinge_pin."):
        return ("hinge_section",)
    return ("pin", "shoulder_pin")


SCENE_TITLES = {("pin", "shoulder_pin"): "diagrama.t_pasador_hombro", ("pin", "hip_pin"): "diagrama.t_pasador_cadera",
                ("pin", "waist_pin"): "diagrama.t_pasador_cintura", ("pin", "hand_pin"): "diagrama.t_pasador_mano",
                ("face",): "diagrama.t_cara", ("sit",): "diagrama.t_sentada", ("print",): "diagrama.t_angulo_impresion",
                ("mirror",): "diagrama.t_espejo", ("flat",): "diagrama.t_sin_soportes", ("waist",): "diagrama.t_giro_cintura",
                ("disc",): "diagrama.t_disco_clic", ("finger",): "diagrama.t_lengueta_clic",
                ("hinge_face",): "diagrama.t_bisagra_cara", ("hinge_section",): "diagrama.t_bisagra_corte"}


def scene_title(sid: tuple) -> str:
    return tr(SCENE_TITLES[sid])


def build_scene(sid: tuple, v: Callable[[str], float]) -> Scene:
    kind = sid[0]
    if kind == "pin":
        return scene_pin(v, sid[1])
    return {"face": scene_face, "sit": scene_sit, "print": scene_print_angle, "mirror": scene_mirror, "flat": scene_flat,
            "waist": scene_waist, "disc": scene_disc, "finger": scene_finger, "hinge_face": scene_hinge_face,
            "hinge_section": scene_hinge_section}[kind](v)


def reference_values(ranges: dict[str, tuple[float, float]], v: Callable[[str], float]) -> Callable[[str], float]:
    """Valores con los que se fija la escala: cada parámetro al 60 % de su rango, o el actual si es mayor (así el dibujo
    no se reescala al editar un valor y siempre entra)."""
    def ref(key: str) -> float:
        lo, hi = ranges.get(key, (0.0, 1.0))
        return max(v(key), lo + 0.6 * (hi - lo)) if key not in ("torso_mirror", "shoulder_detent", "hip_detent", "hinge_pin.neck", "hinge_pin.rod") else v(key)
    return ref


# ------------------------------------------------------------------------------------------------ animación
class Animator:
    """Vaivén de un parámetro alrededor de su valor actual (se dispara al hacer clic en su etiqueta). Un booleano se invierte
    a mitad de la animación y vuelve."""
    DURATION = 1.8

    def __init__(self) -> None:
        self.key: str | None = None
        self.t0 = 0.0
        self.cur = 0.0
        self.lo = 0.0
        self.hi = 1.0
        self.boolean = False

    def start(self, key: str, cur: float, lo: float, hi: float, now: float, boolean: bool = False) -> None:
        self.key, self.cur, self.lo, self.hi, self.t0, self.boolean = key, cur, lo, hi, now, boolean

    def stop(self) -> None:
        self.key = None

    def active(self, now: float) -> bool:
        return self.key is not None and (now - self.t0) < self.DURATION

    def value(self, now: float) -> float | None:
        """Valor del parámetro animado en `now` (None si no hay animación en curso)."""
        if not self.active(now):
            self.key = None
            return None
        u = (now - self.t0) / self.DURATION
        if self.boolean:
            return 1.0 - self.cur if 0.25 <= u < 0.75 else self.cur
        amp = 0.4 * (self.hi - self.lo)
        up = min(amp, self.hi - self.cur)
        dn = min(amp, self.cur - self.lo)
        if up <= 0 and dn <= 0:
            return self.cur
        s = math.sin(2 * math.pi * u)                                       # 0 → + → 0 → − → 0
        return self.cur + (up * s if s >= 0 else dn * s)


# ------------------------------------------------------------------------------------------------ dibujo
def fit(bbox: tuple[float, float, float, float], w: float, h: float, margin: float) -> tuple[float, float, float]:
    """(escala px/mm, origen x, origen y) para que la caja entre en w × h con margen; el origen es el de la escena (0,0)."""
    x0, y0, x1, y1 = bbox
    bw, bh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    s = min((w - 2 * margin) / bw, (h - 2 * margin) / bh)
    ox = w / 2 - s * (x0 + x1) / 2
    oy = h / 2 + s * (y0 + y1) / 2
    return s, ox, oy


def _unit(a: Pt, b: Pt) -> Pt:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def _arrow(cv: Canvas, tip: Pt, direction: Pt, color: str, size: float = 7.0) -> None:
    ux, uy = direction
    base = (tip[0] - ux * size, tip[1] - uy * size)
    nx, ny = -uy, ux
    cv.poly([tip, (base[0] + nx * size * 0.28, base[1] + ny * size * 0.28), (base[0] - nx * size * 0.28, base[1] - ny * size * 0.28)],
            color, None)


def draw_dim(cv: Canvas, d: Dim, tf: Callable[[Pt], Pt], active: bool, pal: dict[str, str], scale: float) -> None:
    col = pal["dim_active"] if active else pal["dim"]
    wd = 1.6 if active else 1.0
    size = 11.0 if active else 10.0
    if d.kind in ("lin", "dia"):
        a, b = tf(d.a), tf(d.b)
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.5:
            return
        u = _unit(a, b)
        n = (-u[1], u[0])
        o = d.off
        # en pantalla y crece hacia abajo: el desplazamiento positivo va hacia «arriba» de la cota
        sgn = -1.0 if o >= 0 else 1.0
        off = abs(o) * sgn
        a2, b2 = (a[0] + n[0] * off, a[1] + n[1] * off), (b[0] + n[0] * off, b[1] + n[1] * off)
        ext = 4.0 * sgn
        for p, q in ((a, a2), (b, b2)):
            cv.line((p[0] + n[0] * 2.0 * sgn, p[1] + n[1] * 2.0 * sgn), (q[0] + n[0] * ext, q[1] + n[1] * ext), col, 0.8)
        span = math.hypot(b2[0] - a2[0], b2[1] - a2[1])
        out = d.outside or span < 26.0
        if out:
            L = 12.0
            cv.line((a2[0] - u[0] * L, a2[1] - u[1] * L), (b2[0] + u[0] * L, b2[1] + u[1] * L), col, wd)
            _arrow(cv, a2, (u[0], u[1]), col)
            _arrow(cv, b2, (-u[0], -u[1]), col)
        else:
            cv.line(a2, b2, col, wd)
            _arrow(cv, a2, (-u[0], -u[1]), col)
            _arrow(cv, b2, (u[0], u[1]), col)
        mid = ((a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2)
        tp = mid if not out else (b2[0] + u[0] * (14.0 + cv.text_width(d.text, size) / 2), b2[1] + u[1] * (14.0 + cv.text_width(d.text, size) / 2))
        cv.text((tp[0], tp[1] + size * 0.35), d.text, col, size, "middle", bg=pal["bg"])
    elif d.kind == "ang":
        c = tf(d.a)
        r = d.off
        n = max(6, int(abs(d.a1 - d.a0) / 4))
        pts = []
        for i in range(n + 1):
            t = math.radians(d.a0 + (d.a1 - d.a0) * i / n)
            pts.append((c[0] + r * math.cos(t), c[1] - r * math.sin(t)))
        if len(pts) >= 2:
            cv.poly(pts, None, col, wd, closed=False)
            for ang in (d.a0, d.a1):
                t = math.radians(ang)
                cv.line((c[0] + (r - 6) * math.cos(t), c[1] - (r - 6) * math.sin(t)), (c[0] + (r + 5) * math.cos(t), c[1] - (r + 5) * math.sin(t)), col, 0.8)
            sgn = 1.0 if d.a1 >= d.a0 else -1.0
            for end, direction in ((pts[0], pts[1]), (pts[-1], pts[-2])):
                _arrow(cv, end, _unit(direction, end) if end is pts[-1] else _unit(direction, end), col, 6.0)
        if d.text:
            t = math.radians((d.a0 + d.a1) / 2)
            cv.text((c[0] + (r + 14) * math.cos(t), c[1] - (r + 14) * math.sin(t) + size * 0.35), d.text, col, size, "middle", bg=pal["bg"])
    elif d.kind == "note":
        a, b = tf(d.a), tf(d.b)
        if d.text:
            cv.line(a, b, col, 0.9)
            cv.circle(a, 1.8, col, None)
            anchor = "start" if b[0] >= a[0] else "end"
            cv.text((b[0] + (3 if anchor == "start" else -3), b[1] + size * 0.35), d.text, col, size, anchor, bg=pal["bg"])


def render(cv: Canvas, sc: Scene, active_key: str | None, w: float, h: float, pal: dict[str, str], scale_px: float | None = None,
           margin: float | tuple[float, float] = (58.0, 40.0), frame: tuple[float, float, float, float] | None = None) -> float:
    """Dibuja la escena centrada en w × h. `frame` (caja en mm) fija el encuadre y la escala —se usa la caja con los valores de
    referencia para que el dibujo no se reescale al editar—; `scale_px` fija la escala (px/mm). Devuelve la escala usada."""
    x0, y0, x1, y1 = frame or sc.bbox()
    mx, my = (margin, margin) if isinstance(margin, (int, float)) else margin
    s_fit = min((w - 2 * mx) / max(x1 - x0, 1e-6), (h - 2 * my) / max(y1 - y0, 1e-6))
    s = s_fit if scale_px is None else min(scale_px, s_fit)
    ox = w / 2 - s * (x0 + x1) / 2
    oy = h / 2 + s * (y0 + y1) / 2

    def tf(p: Pt) -> Pt:
        return (ox + s * p[0], oy - s * p[1])

    for shape in sc.shapes:
        hot = active_key is not None and active_key in shape.keys
        style = shape.style
        if style == "hatch":
            cv.line(tf(shape.pts[0]), tf(shape.pts[1]), pal["ghost"], 0.6)
            continue
        if style == "bg":
            cv.poly([tf(p) for p in shape.pts], pal["bg"], None)
            if hot:
                cv.poly([tf(p) for p in shape.pts], None, pal["hot_edge"], 1.2, dash=(3, 2))
            continue
        fill = {"body": pal["body"], "pin": pal["pin"], "ghost": None, "axis": None, "edge": None}.get(style)
        edge = {"body": pal["body_edge"], "pin": pal["pin_edge"], "ghost": pal["ghost"], "axis": pal["axis"], "edge": pal["body_edge"]}[style]
        dash = (4, 3) if style in ("ghost",) else ((8, 3, 2, 3) if style == "axis" else None)
        if hot and style in ("body", "pin"):
            fill, edge = pal["hot"], pal["hot_edge"]
        width = 2.0 if hot else 1.2
        if shape.kind == "circle":
            cv.circle(tf(shape.pts[0]), shape.r * s, fill, edge, width, dash)
        elif shape.kind == "line":
            cv.line(tf(shape.pts[0]), tf(shape.pts[1]), edge, 1.8 if hot and style == "axis" else 1.0, dash)
        else:
            cv.poly([tf(p) for p in shape.pts], fill, edge, width, dash, closed=shape.closed)
    for d in sc.dims:
        if d.key != active_key:
            draw_dim(cv, d, tf, False, pal, s)
    for d in sc.dims:                                                       # la activa al final: queda encima
        if d.key == active_key:
            draw_dim(cv, d, tf, True, pal, s)
    return s


# ------------------------------------------------------------------------------------------------ lienzo SVG
class SvgCanvas:
    """Lienzo que acumula SVG (para pruebas y para ver el dibujo sin GTK)."""

    def __init__(self, w: float, h: float, bg: str) -> None:
        self.w, self.h = w, h
        self.out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:g}" height="{h:g}" viewBox="0 0 {w:g} {h:g}">',
                    f'<rect width="100%" height="100%" fill="{bg}"/>']

    @staticmethod
    def _dash(dash) -> str:
        return f' stroke-dasharray="{",".join(str(x) for x in dash)}"' if dash else ""

    def line(self, p0, p1, color, width=1.0, dash=None):
        self.out.append(f'<line x1="{p0[0]:.2f}" y1="{p0[1]:.2f}" x2="{p1[0]:.2f}" y2="{p1[1]:.2f}" stroke="{color}" stroke-width="{width}"{self._dash(dash)}/>')

    def poly(self, pts, fill, stroke, width=1.0, dash=None, closed=True):
        tag = "polygon" if closed else "polyline"
        p = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        self.out.append(f'<{tag} points="{p}" fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{width}"{self._dash(dash)}/>')

    def circle(self, c, r, fill, stroke, width=1.0, dash=None):
        self.out.append(f'<circle cx="{c[0]:.2f}" cy="{c[1]:.2f}" r="{r:.2f}" fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{width}"{self._dash(dash)}/>')

    def text_width(self, s, size):
        return 0.58 * size * len(s)

    def text(self, p, s, color, size=11.0, anchor="middle", bg=None):
        if not s:
            return
        if bg:
            wd = self.text_width(s, size) + 4
            x = p[0] - wd / 2 if anchor == "middle" else (p[0] if anchor == "start" else p[0] - wd)
            self.out.append(f'<rect x="{x - 2:.2f}" y="{p[1] - size:.2f}" width="{wd:.2f}" height="{size + 3:.2f}" fill="{bg}"/>')
        self.out.append(f'<text x="{p[0]:.2f}" y="{p[1]:.2f}" fill="{color}" font-size="{size}" font-family="sans-serif" text-anchor="{anchor}">{s}</text>')

    def svg(self) -> str:
        return "\n".join(self.out + ["</svg>"])
