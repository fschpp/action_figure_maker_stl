"""Dibujo de «cómo queda el arma en la mano» (sin GTK): el puño de la mano estándar agarrando la empuñadura.

Reemplaza al bloque de texto del panel de armas. Se lee de un vistazo:

* la **mano** (color piel) con el **arma** (acero) atravesando el puño, vista de costado;
* el **largo que sobresale** del puño como una cota vertical; la **guarda** (barra continua) frente al **puño** (barra de
  trazos) para ver si la guarda cubre la mano;
* tres **insignias** con pictograma (contacto, agarre, tamaño) en verde / ámbar / rojo, sin una palabra;
* a la derecha, una **figurita de referencia** con el arma al lado, a escala de la altura de la figura.

`build_view` es geometría pura (numpy): proyecta las mallas ya en el marco del accesorio (eje de la empuñadura = +X, la hoja
en x < 0) y deja polígonos 2D listos. `draw` solo necesita un contexto cairo (`cr`), por eso se prueba sin ventana.
Todo el texto largo (los avisos) va al `tip` (tooltip) y al desplegable «Detalles», no al dibujo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..weapon_fit import LEVEL_NO, LEVEL_OK, LEVEL_WARN

# --------------------------------------------------------------------------- qué aviso va a qué insignia
CONTACT_CODES = {"choca_mano"}
GRIP_CODES = {"guarda_chica", "torcida"}
SIZE_CODES = {"corta_vs_mano", "corta_vs_figura", "larga_vs_figura"}
BADGES = ("contacto", "agarre", "tamano")            # orden de izquierda a derecha

MAX_TRIS = 9000                                       # tope de triángulos que se dibujan (la mano estándar trae muchos)


@dataclass
class GripView:
    """Todo lo que `draw` necesita, en mm de pantalla (x hacia la derecha, y hacia arriba; y = 0 es la cara del puño)."""

    tris: np.ndarray                                  # (n, 3, 2) triángulos ya ordenados de atrás hacia adelante
    shade: np.ndarray                                 # (n,) brillo 0..1
    is_weapon: np.ndarray                             # (n,) bool: el triángulo es del arma (no de la mano)
    bounds: tuple                                     # (xmin, ymin, xmax, ymax) de todo lo dibujado
    has_weapon: bool = False
    reach_mm: float = 0.0
    guard_mm: float = 0.0
    fist_mm: float = 0.0
    guard_y: float = 1.0                              # altura (y de pantalla) de la barra de la guarda
    fist_y: float = -4.0                              # ... y de la barra del puño (dentro de la mano)
    channel_mm: float = 10.0                          # largo del canal de agarre (para marcar el eje)
    hand_top: float = 0.0                             # punto más alto de la mano (y de pantalla)
    hand_bottom: float = 0.0                          # ... y el más bajo
    hand_w: float = 0.0                               # ancho de la mano
    tilt_deg: float = 0.0
    figure_ratio: float | None = None                 # sobresale / altura de la figura
    levels: dict = field(default_factory=lambda: {b: None for b in BADGES})   # insignia → ok | aviso | no | None (sin dato)
    tip: str = ""                                     # texto completo para el tooltip

    @property
    def level(self) -> str | None:
        """El peor de las insignias (None si todavía no hay arma)."""
        vals = [v for v in self.levels.values() if v]
        if not vals:
            return None
        return LEVEL_NO if LEVEL_NO in vals else LEVEL_WARN if LEVEL_WARN in vals else LEVEL_OK


# --------------------------------------------------------------------------- geometría
def badge_levels(notes, build_warnings: int = 0) -> dict:
    """Nivel de cada insignia a partir de los avisos `(código, variables)` de `weapon_fit` y de los avisos del armado."""
    codes = {c for c, _v in notes}
    out = {b: LEVEL_OK for b in BADGES}
    if codes & CONTACT_CODES:
        out["contacto"] = LEVEL_NO
    if codes & GRIP_CODES or build_warnings:
        out["agarre"] = LEVEL_WARN
    if codes & SIZE_CODES:
        out["tamano"] = LEVEL_WARN
    return out


def _tri_arrays(mesh, M_inv, basis, max_tris):
    """(triángulos 3×2, profundidad, brillo) de `mesh` pasada al marco del accesorio y proyectada con `basis`."""
    sx, sy, nz = basis                                # vectores (marco del accesorio) de pantalla: derecha, arriba, hacia el ojo
    v = np.asarray(mesh.vertices, float)
    v = v @ M_inv[:3, :3].T + M_inv[:3, 3]
    f = np.asarray(mesh.faces, int)
    if len(f) > max_tris:                             # solo para dibujar: un paso fijo conserva la silueta de sobra
        f = f[:: int(math.ceil(len(f) / max_tris))]
    tri = v[f]                                        # (n, 3, 3)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-12
    tri, n, ln = tri[ok], n[ok], ln[ok]
    n = n / ln[:, None]
    facing = n @ nz > 0.0                             # caras de atrás fuera (las mallas son cerradas)
    tri, n = tri[facing], n[facing]
    pts = np.stack([tri @ sx, tri @ sy], axis=-1)     # (n, 3, 2)
    depth = (tri @ nz).mean(axis=1)
    light = np.array(nz) * 0.75 + np.array(sy) * 0.45 - np.array(sx) * 0.25
    light = light / np.linalg.norm(light)
    shade = 0.38 + 0.62 * np.clip(n @ light, 0.0, 1.0)
    return pts, depth, shade


def _screen_basis(acc_blade_vertices) -> tuple:
    """Eje de pantalla en el marco del accesorio: arriba = −X (la hoja sube); a la derecha, el lateral en que la hoja es más ancha
    (la vemos de plano); hacia el ojo, el otro lateral."""
    up = np.array([-1.0, 0.0, 0.0])
    y_ext = z_ext = 0.0
    if acc_blade_vertices is not None and len(acc_blade_vertices):
        y_ext = float(np.ptp(acc_blade_vertices[:, 1]))
        z_ext = float(np.ptp(acc_blade_vertices[:, 2]))
    if y_ext >= z_ext:
        right, eye = np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0])     # y × (−x) = +z: el ojo mira desde +z
    else:
        right, eye = np.array([0.0, 0.0, 1.0]), np.array([0.0, -1.0, 0.0])    # z × (−x) = −y
    return right, up, eye


def _level_bar_y(pts_x, pts_y, lo: float, hi: float) -> float:
    """Altura (en pantalla) con la sección más ancha entre `lo` y `hi` (por tramos de 1 mm) de los vértices del arma."""
    best_y, best_w = (lo + hi) / 2.0, -1.0
    for y0 in np.arange(lo, hi, 1.0):
        m = (pts_y >= y0) & (pts_y < y0 + 1.0)
        if m.any():
            w = float(np.ptp(pts_x[m]))
            if w > best_w:
                best_w, best_y = w, y0 + 0.5
    return best_y


def build_view(hand_mesh, fit, sim=None, build_warnings: int = 0, tip: str = "") -> GripView:
    """Arma el dibujo de `hand_mesh` (marco local de la mano) con `fit` (`GripFit`) y, si hay, `sim` (`HandSim`).

    Sin `sim` queda el puño vacío (a la espera de un arma). `build_warnings`: cantidad de avisos del armado de la empuñadura."""
    from ..props import prop_to_hand_matrix

    M = prop_to_hand_matrix(fit)
    M_inv = np.linalg.inv(M)
    blade_v = None
    if sim is not None:
        wv = np.asarray(sim.weapon.vertices, float) @ M_inv[:3, :3].T + M_inv[:3, 3]
        blade_v = wv[wv[:, 0] <= 0.0]
    basis = _screen_basis(blade_v)
    parts = [(_tri_arrays(hand_mesh, M_inv, basis, MAX_TRIS), False)]
    if sim is not None:
        parts.append((_tri_arrays(sim.weapon, M_inv, basis, MAX_TRIS), True))
    tris = np.concatenate([p[0][0] for p in parts])
    depth = np.concatenate([p[0][1] for p in parts])
    shade = np.concatenate([p[0][2] for p in parts])
    weap = np.concatenate([np.full(len(p[0][0]), p[1]) for p in parts])
    order = np.argsort(depth, kind="stable")          # de atrás hacia adelante
    tris, shade, weap = tris[order], shade[order], weap[order]
    allp = tris.reshape(-1, 2) if len(tris) else np.zeros((1, 2))
    view = GripView(tris, shade, weap.astype(bool), (float(allp[:, 0].min()), float(allp[:, 1].min()),
                                                    float(allp[:, 0].max()), float(allp[:, 1].max())),
                    channel_mm=float(fit.length), tip=tip)
    view.fist_y = -float(fit.length) / 2.0
    hp = parts[0][0][0].reshape(-1, 2)
    if len(hp):
        view.hand_top, view.hand_bottom = float(hp[:, 1].max()), float(hp[:, 1].min())
        view.hand_w = float(np.ptp(hp[:, 0]))
    if sim is not None:
        view.has_weapon = True
        view.reach_mm, view.guard_mm, view.fist_mm = float(sim.reach_mm), float(sim.guard_mm), float(sim.fist_mm)
        view.tilt_deg = float(sim.tilt_deg)
        view.figure_ratio = None if sim.reach_vs_figure is None else float(sim.reach_vs_figure)
        sx = blade_v @ basis[0]
        sy = blade_v @ basis[1]
        view.guard_y = _level_bar_y(sx, sy, 0.5, min(10.0, max(1.5, view.reach_mm)))
        view.levels = badge_levels(sim.notes, build_warnings)
    return view


# --------------------------------------------------------------------------- colores
_BG = (0.17, 0.19, 0.23)
_SKIN = (0.94, 0.74, 0.60)
_STEEL = (0.60, 0.72, 0.88)
_HIT = (0.95, 0.38, 0.30)
_LEVEL_RGB = {LEVEL_OK: (0.30, 0.78, 0.45), LEVEL_WARN: (0.97, 0.72, 0.18), LEVEL_NO: (0.93, 0.31, 0.28), None: (0.50, 0.53, 0.58)}


def _shade_rgb(rgb, k):
    return tuple(min(1.0, c * (0.45 + 0.75 * k)) for c in rgb)


# --------------------------------------------------------------------------- trazado (cairo)
def _round_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _mark(cr, level, cx, cy, r):
    """Marca de estado (✓ / ! / ✕) dibujada con trazos, en un disco de radio `r`."""
    cr.save()
    cr.new_path()
    cr.set_source_rgb(*_LEVEL_RGB[level])
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    cr.set_line_width(max(1.4, r * 0.28))
    cr.set_line_cap(1)                                # ROUND
    cr.set_line_join(1)
    k = r * 0.5
    if level == LEVEL_OK:
        cr.move_to(cx - k, cy + 0.0)
        cr.line_to(cx - k * 0.25, cy + k * 0.7)
        cr.line_to(cx + k, cy - k * 0.6)
        cr.stroke()
    elif level == LEVEL_WARN:
        cr.move_to(cx, cy - k * 0.9)
        cr.line_to(cx, cy + k * 0.15)
        cr.stroke()
        cr.arc(cx, cy + k * 0.7, max(0.9, r * 0.14), 0, 2 * math.pi)
        cr.fill()
    elif level == LEVEL_NO:
        cr.move_to(cx - k * 0.8, cy - k * 0.8)
        cr.line_to(cx + k * 0.8, cy + k * 0.8)
        cr.move_to(cx + k * 0.8, cy - k * 0.8)
        cr.line_to(cx - k * 0.8, cy + k * 0.8)
        cr.stroke()
    cr.restore()


def _glyph(cr, name, cx, cy, s):
    """Pictograma de la insignia (blanco, trazos finos) dentro de un cuadro de lado `s` centrado en (cx, cy)."""
    cr.save()
    cr.set_source_rgb(1, 1, 1)
    cr.set_line_width(max(1.3, s * 0.1))
    cr.set_line_cap(1)
    cr.set_line_join(1)
    h = s / 2.0
    if name == "contacto":                            # dos cuadrados que se pisan
        cr.rectangle(cx - h, cy - h * 0.2, h * 1.1, h * 1.1)
        cr.rectangle(cx - h * 0.1, cy - h, h * 1.1, h * 1.1)
        cr.stroke()
    elif name == "agarre":                            # el puño (círculo) con la empuñadura que lo atraviesa
        cr.arc(cx, cy, h * 0.55, 0, 2 * math.pi)
        cr.stroke()
        cr.move_to(cx, cy - h)
        cr.line_to(cx, cy + h)
        cr.stroke()
    else:                                             # tamaño: flecha de medida con topes
        cr.move_to(cx, cy - h)
        cr.line_to(cx, cy + h)
        for yy in (cy - h, cy + h):
            cr.move_to(cx - h * 0.45, yy)
            cr.line_to(cx + h * 0.45, yy)
        cr.stroke()
        cr.move_to(cx - h * 0.3, cy - h * 0.5)
        cr.line_to(cx, cy - h)
        cr.line_to(cx + h * 0.3, cy - h * 0.5)
        cr.stroke()
    cr.restore()


def _badge(cr, name, level, cx, cy, r):
    cr.save()
    cr.new_path()
    cr.set_source_rgba(*_LEVEL_RGB[level], 0.28 if level else 0.18)
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.fill_preserve()
    cr.set_source_rgb(*_LEVEL_RGB[level])
    cr.set_line_width(2.0)
    cr.stroke()
    cr.restore()
    _glyph(cr, name, cx, cy, r * 1.05)
    if level:
        _mark(cr, level, cx + r * 0.78, cy + r * 0.78, r * 0.46)


def _text(cr, s, x, y, size=10.0, rgb=(1, 1, 1), anchor="m"):
    cr.save()
    cr.set_source_rgb(*rgb)
    cr.set_font_size(size)
    ext = cr.text_extents(s)
    dx = {"l": 0.0, "m": -ext.width / 2.0 - ext.x_bearing, "r": -ext.width - ext.x_bearing}[anchor]
    cr.new_path()
    cr.move_to(x + dx, y)
    cr.show_text(s)
    cr.new_path()
    cr.restore()


def _dim_v(cr, x, y0, y1, rgb, label=None, label_dx=-5.0):
    """Cota vertical entre `y0` e `y1` (pantalla) en la columna `x`, con topes."""
    cr.save()
    cr.set_source_rgb(*rgb)
    cr.set_line_width(1.5)
    cr.move_to(x, y0)
    cr.line_to(x, y1)
    for yy in (y0, y1):
        cr.move_to(x - 4, yy)
        cr.line_to(x + 4, yy)
    cr.stroke()
    cr.restore()
    if label:
        _text(cr, label, x + label_dx, (y0 + y1) / 2.0 + 3.5, 10.5, rgb, "r")


def _dim_h(cr, x0, x1, y, rgb, dashed=False, label=None):
    cr.save()
    cr.set_source_rgb(*rgb)
    cr.set_line_width(2.4 if not dashed else 1.6)
    if dashed:
        cr.set_dash([4.0, 3.0])
    cr.move_to(x0, y)
    cr.line_to(x1, y)
    cr.stroke()
    cr.set_dash([])
    cr.set_line_width(1.6)
    for xx in (x0, x1):
        cr.move_to(xx, y - 4)
        cr.line_to(xx, y + 4)
    cr.stroke()
    cr.restore()
    if label:
        _text(cr, label, x1 + 4, y + 3.5, 10.0, rgb, "l")


def _figure_strip(cr, x0, y0, x1, y1, ratio, level):
    """Figurita de pie con el arma al lado, a la misma escala: cuánto de la altura de la figura es el arma."""
    rgb = _LEVEL_RGB[level]
    sw = x1 - x0
    ground = y1 - 6
    H = min(ground - (y0 + 16), sw * 1.7)
    if H < 30:
        return
    cx = x0 + sw * 0.32
    cr.save()
    cr.new_path()
    cr.set_line_cap(1)
    cr.set_source_rgba(1, 1, 1, 0.35)
    cr.set_line_width(1.0)
    cr.move_to(x0, ground)
    cr.line_to(x1, ground)
    cr.stroke()
    cr.set_source_rgba(1, 1, 1, 0.55)
    cr.set_line_width(max(2.0, H * 0.045))
    head = H * 0.075
    cr.arc(cx, ground - H + head, head, 0, 2 * math.pi)
    cr.stroke()
    neck = ground - H + head * 2
    hip = ground - H * 0.46
    cr.move_to(cx, neck)
    cr.line_to(cx, hip)
    cr.move_to(cx - H * 0.15, neck + H * 0.06)
    cr.line_to(cx + H * 0.15, neck + H * 0.06)
    cr.move_to(cx, hip)
    cr.line_to(cx - H * 0.08, ground)
    cr.move_to(cx, hip)
    cr.line_to(cx + H * 0.08, ground)
    cr.stroke()
    # el arma, parada junto a la figura, con el largo que sobresale a la misma escala
    bx = x0 + sw * 0.80
    bh = max(2.0, min(ratio, 1.0) * H)
    cr.set_source_rgb(*rgb)
    cr.rectangle(bx - 1.8, ground - bh, 3.6, bh)
    cr.fill()
    cr.rectangle(bx - 5.5, ground - 3.0, 11.0, 3.0)    # la guarda, al pie
    cr.fill()
    cr.restore()
    _text(cr, f"{ratio * 100:.0f} %", bx, max(y0 + 10, ground - bh - 5), 10.5, rgb, "m")


def _break_marks(cr, x, y, rgb):
    """Dos rayitas oblicuas: «el arma sigue» (se recortó para que la mano se vea grande)."""
    cr.save()
    cr.new_path()
    cr.set_source_rgb(*rgb)
    cr.set_line_width(1.6)
    for dy in (-2.5, 2.5):
        cr.move_to(x - 6, y + dy + 3)
        cr.line_to(x + 6, y + dy - 3)
    cr.stroke()
    cr.restore()


def draw(cr, w: int, h: int, view: GripView | None) -> None:
    """Dibuja `view` en un área de `w` × `h` px (`view` None: solo el fondo)."""
    cr.save()
    cr.new_path()
    cr.set_source_rgb(*_BG)
    _round_rect(cr, 0.5, 0.5, w - 1.0, h - 1.0, 10)
    cr.fill()
    if view is None or len(view.tris) == 0:
        cr.restore()
        return
    has_strip = view.has_weapon and view.figure_ratio is not None
    top, left = 40.0, 50.0
    right = w * (0.68 if has_strip else 0.96)
    sx0, sy0, sx1, sy1 = left, top, right, h - 10.0
    xmin, ymin, xmax, ymax = view.bounds
    # la mano manda en la escala: el arma se muestra hasta ~2,5 × el alto de la mano y, si es más larga, se corta con «//»
    hand_h = max(view.hand_top - view.hand_bottom, 1e-6)
    y_cap = ymax
    cropped = False
    if view.has_weapon:
        y_cap = min(ymax, max(view.hand_top, 0.0) + 2.5 * hand_h)
        cropped = ymax > y_cap + 1e-6
    else:                                             # a la espera: lugar arriba del puño para el arma que viene
        y_cap = max(ymax, view.hand_top) + 0.9 * hand_h
    bw = max(max(xmax - xmin, view.hand_w), 1e-6)
    bh = max(y_cap - ymin, 1e-6)
    k = min((sx1 - sx0 - 8.0) / bw, (sy1 - sy0) / bh)
    xc = (xmin + xmax) / 2.0 if view.has_weapon else (view.bounds[0] + view.bounds[2]) / 2.0
    ox = sx0 + (sx1 - sx0) / 2.0 - xc * k
    yc = (y_cap + ymin) / 2.0
    oy = sy0 + (sy1 - sy0) / 2.0

    def P(x, y):
        return ox + x * k, oy + (yc - y) * k          # la y de pantalla crece hacia abajo

    # --- las dos piezas, de atrás hacia adelante (recortadas arriba si el arma es muy larga)
    contact = view.levels.get("contacto") == LEVEL_NO
    cr.save()
    cr.new_path()
    cr.rectangle(0, P(0, y_cap)[1], w, h)
    cr.clip()
    cr.set_line_join(1)
    for tri, sh, wp in zip(view.tris, view.shade, view.is_weapon):
        rgb = _shade_rgb(_HIT if (wp and contact) else _STEEL if wp else _SKIN, float(sh))
        cr.set_source_rgb(*rgb)
        cr.move_to(*P(*tri[0]))
        cr.line_to(*P(*tri[1]))
        cr.line_to(*P(*tri[2]))
        cr.close_path()
        cr.fill_preserve()
        cr.set_line_width(0.5)
        cr.stroke()
    cr.restore()
    # --- eje de la empuñadura (se ve si el arma sale torcida)
    cr.save()
    if not view.has_weapon:
        cr.rectangle(0, 0, 0, 0)
    cr.new_path()
    cr.set_source_rgba(1, 1, 1, 0.45)
    cr.set_line_width(1.0)
    cr.set_dash([5.0, 4.0])
    cr.move_to(*P(0.0, y_cap))
    cr.line_to(*P(0.0, -view.channel_mm))
    cr.stroke()
    cr.restore()
    # --- cotas
    if view.has_weapon:
        rgb_size = _LEVEL_RGB[view.levels.get("tamano")]
        top_y = min(view.reach_mm, y_cap)
        xl = max(P(xmin, 0)[0], sx0 + 2.0) - 9.0
        _dim_v(cr, xl, P(0, 0)[1], P(0, top_y)[1], rgb_size, f"{view.reach_mm:.0f} mm", -5.0)
        if cropped:
            _break_marks(cr, P(0, 0)[0], P(0, y_cap)[1] + 3.0, (1, 1, 1))
        g_rgb = _LEVEL_RGB[LEVEL_WARN if "guarda_chica" in _codes_hint(view) else LEVEL_OK]
        gx0, gx1 = P(-view.guard_mm / 2, 0)[0], P(view.guard_mm / 2, 0)[0]
        _dim_h(cr, gx0, gx1, P(0, min(view.guard_y, y_cap))[1], g_rgb, False, f"{view.guard_mm:.0f}")
        fx0, fx1 = P(-view.fist_mm / 2, 0)[0], P(view.fist_mm / 2, 0)[0]
        _dim_h(cr, fx0, fx1, P(0, view.fist_y)[1], (1, 1, 1), True, f"{view.fist_mm:.0f}")
    else:
        cr.save()                                     # arma fantasma: una espada de trazos sobre el puño vacío
        cr.new_path()
        cr.set_source_rgba(*_STEEL, 0.6)
        cr.set_line_width(1.6)
        cr.set_dash([5.0, 4.0])
        base = P(0, 0)
        tip = P(0, y_cap - 0.04 * bh)
        bwid = max(4.0, 0.045 * (sx1 - sx0))
        gwid = bwid * 2.6
        cr.move_to(base[0] - bwid / 2, base[1] - 5)
        cr.line_to(base[0] - bwid / 2, tip[1] + bwid * 1.4)
        cr.line_to(base[0], tip[1])
        cr.line_to(base[0] + bwid / 2, tip[1] + bwid * 1.4)
        cr.line_to(base[0] + bwid / 2, base[1] - 5)
        cr.move_to(base[0] - gwid, base[1] - 5)
        cr.line_to(base[0] + gwid, base[1] - 5)
        cr.stroke()
        cr.restore()
    # --- figurita de referencia
    if has_strip:
        _figure_strip(cr, w * 0.70, top, w - 10.0, h - 4.0, view.figure_ratio, view.levels.get("tamano") or LEVEL_OK)
    # --- insignias
    r = 13.0
    for i, name in enumerate(BADGES):
        _badge(cr, name, view.levels.get(name), 10.0 + r + i * (2 * r + 12.0), 8.0 + r, r)
    cr.restore()


def _codes_hint(view: GripView) -> set:
    """«guarda_chica» ⇔ la barra de la guarda es más angosta que la del puño (se vuelve a decidir con las medidas dibujadas)."""
    return {"guarda_chica"} if 0 < view.guard_mm < view.fist_mm else set()
