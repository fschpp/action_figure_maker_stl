"""E15: asiento del jinete elegido con el ratón sobre el caballo y vista conjunta con colores de diagnóstico (plan E15, §4.4 y §4.7).

Lógica pura (numpy + cámara de la vista previa), sin GTK: la ventana solo traduce el clic y el arrastre a píxeles del widget y muestra el
resultado. Misma idea que `mount_pick` (M14): la vista previa es una imagen que dibuja `PreviewRenderer.render_parts`, así que aquí se arma
**la misma cámara** (`mount_pick.group_camera`) para que el punto elegido caiga donde se hizo clic.

* **Asiento:** el rayo del clic tiene que dar primero en el caballo. Si antes da en otra pieza (el jinete que ya está sentado, la silla) se
  avisa en vez de poner el asiento donde no se ve. La altura no sale del clic sino del lomo: `FigureBuilder.seat_on_back(x, y)` toma el punto
  más alto del caballo bajo ese `(x, y)`.
* **Dirección hacia la cabeza:** el arrastre se proyecta sobre el plano **horizontal** que pasa por el asiento (la dirección del jinete solo
  cuenta en XY; `RiderFrame` ignora la parte vertical). Con la cámara casi a ras de ese plano el gesto no define nada y se avisa.
* **Colores de diagnóstico:** `diagnostic_scene` pinta de rojo lo que `validate_riding` nombró en un problema, de ámbar lo que nombró un aviso,
  y de verde azulado las caras que abrió el tallado en el caballo. Los nombres salen de los mismos mensajes del motor («pierna_izq ↔ caballo»).

No reemplaza a `validate_riding`: solo dibuja lo que ya informó.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass

import numpy as np
import trimesh

from .. import solids as S
from ..equestrian import REF_KINDS, _surface_distance
from ..i18n import tr
from . import mount_pick as MP
from .catalog_manager import widget_to_image
from .picking import ray_first_hit

HORSE_PART = "caballo"           # nombre del caballo en la vista (y prefijo de sus partes: «caballo_tallado»)
CUT_PART = "caballo_tallado"
SADDLE_PART = "silla"
HORSE_COLOR = (0.62, 0.50, 0.38, 1.0)
SADDLE_COLOR = (0.55, 0.36, 0.22, 1.0)
RIDER_COLOR = (0.72, 0.74, 0.78, 1.0)
LEG_COLOR = (0.50, 0.62, 0.80, 1.0)          # pelvis y piernas completas: lo que el asiento tiene que despejar
CUT_COLOR = (0.20, 0.62, 0.55, 1.0)          # caras que abrió el tallado
BAD_COLOR = (0.84, 0.27, 0.25, 1.0)          # nombrado en un problema
WARN_COLOR = (0.95, 0.75, 0.20, 1.0)         # nombrado en un aviso
TINT = 0.6                                   # cuánto se tiñe el caballo o la silla (son grandes: del todo rojos taparían el detalle)
CUT_BAND_MM = 0.25                           # una cara del caballo tallado a menos de esto de la herramienta es pared del corte
PREVIEW_MAX_TRIS = 30_000                    # tope por pieza de la vista previa (la figura real tiene cientos de miles de triángulos)
MIN_GROUND_DZ = 0.05                         # componente vertical mínima de un rayo para cortar el plano horizontal del asiento
MARK_STEP_MM = 3.0                           # separación de los puntos que dibujan la dirección del asiento
MARK_COUNT = 8
YAW_CHOICES = (0.0, 90.0, 180.0, 270.0)      # guiños que ofrece la importación (el STL de origen mira a −Y: 180° lo da vuelta)

PAIR_RE = re.compile(r"([^\s:↔]+) ↔ ([^\s:↔]+)")
PREFIX_RE = re.compile(r"^\s*([A-Za-zÁÉÍÓÚáéíóúñ ]+?):")
PREFIX_TARGET = {"caballo tallado": HORSE_PART, "tallado": HORSE_PART, "caballo": HORSE_PART, "silla": SADDLE_PART, "despeje": SADDLE_PART}


class RidePickError(ValueError):
    """El clic, el arrastre o la medida no definen lo pedido; el mensaje está listo para mostrar."""


@dataclass(frozen=True)
class SeatPick:
    x: float              # (x, y) bajo el clic, en el marco del caballo (mm); la altura la da `seat_on_back`
    y: float
    z: float              # altura del punto tocado (informativa: el asiento va al lomo)
    parte: str            # parte del caballo que se tocó


@dataclass
class RideScene:
    """Lo que se dibuja en la vista conjunta del caballo: `parts` = [(nombre, malla, rgba)] (la misma lista mientras nada cambie, para que
    girar la vista no vuelva a subir todo a la GPU), el encuadre que usa también el clic, y qué pasó (`level`, `text`)."""

    parts: list
    bounds: np.ndarray
    marks: np.ndarray | None = None
    level: str = "sin_calcular"            # «ok», «aviso», «no» o «sin_calcular»
    text: str = ""


# --------------------------------------------------------------------------------------------------------------------- marcas y cámara
def seat_marks(asiento, direccion=None) -> np.ndarray | None:
    """Puntos para dibujar encima de la vista (`render_parts(marks=…)`): el asiento y una fila de puntos hacia la cabeza (solo XY). None si no hay."""
    if asiento is None:
        return None
    a = np.asarray(asiento, float)
    d = np.asarray(direccion if direccion is not None else (0.0, 1.0, 0.0), float)
    d = np.array([d[0], d[1], 0.0])
    n = float(np.linalg.norm(d))
    pts = [a]
    if n > 1e-9:
        pts.extend(a + (d / n) * (MARK_STEP_MM * k) for k in range(1, MARK_COUNT + 1))
    return np.array(pts)


def _is_horse(name: str) -> bool:
    return name.startswith(HORSE_PART)


def pick_seat(camera, px: float, py: float, width: float, height: float, parts) -> SeatPick:
    """Punto del caballo bajo el píxel (px, py) de una imagen `width` × `height`. Levanta `RidePickError` si el clic cae fuera del caballo o
    si lo primero que ve el rayo no es el caballo."""
    meshes = {n: m for n, m, *_c in parts}
    o, d = camera.ray(px, py, width, height)
    best = None
    for name, mesh in meshes.items():
        r = ray_first_hit(np.asarray(mesh.triangles, float), o, d)
        if r is not None and (best is None or r[1] < best[1]):
            best = (name, r[1])
    if best is None:
        raise RidePickError(tr("ride_pick.fuera"))
    if not _is_horse(best[0]):
        raise RidePickError(tr("ride_pick.tapado", pieza=best[0]))
    hit = o + best[1] * d
    x, y, z = (float(round(float(v), 2)) + 0.0 for v in hit)
    return SeatPick(x, y, z, best[0])


def pick_seat_from_widget(x: float, y: float, widget_size, image_size, parts, yaw_off: float = 0.0, pitch_off: float = 0.0) -> SeatPick:
    """Clic en el widget (x, y) → punto del caballo. `widget_size` e `image_size` son (ancho, alto); la imagen se ve con `ContentFit.CONTAIN`."""
    iw, ih = image_size
    hit = widget_to_image(x, y, widget_size[0], widget_size[1], iw, ih)
    if hit is None:
        raise RidePickError(tr("ride_pick.fuera"))
    return pick_seat(MP.group_camera(parts, iw, ih, yaw_off, pitch_off), hit[0], hit[1], iw, ih, parts)


def direction_on_ground(camera, anchor, p0, p1, width: float, height: float, min_px: float = MP.MIN_DRAG_PX) -> tuple:
    """Dirección del arrastre de `p0` a `p1` (píxeles de la imagen) sobre el plano horizontal que pasa por `anchor`: unitaria, con z = 0 y
    redondeada a 0,01. Levanta `RidePickError` si el arrastre es corto o si la cámara está tan a ras del plano que no lo corta."""
    if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) < min_px:
        raise RidePickError(tr("ride_pick.arrastre_corto"))
    z0 = float(np.asarray(anchor, float)[2])
    pts = []
    for px, py in (p0, p1):
        o, d = camera.ray(px, py, width, height)
        if abs(float(d[2])) < MIN_GROUND_DZ:
            raise RidePickError(tr("ride_pick.vista_rasante"))
        pts.append(o + ((z0 - float(o[2])) / float(d[2])) * d)
    v = pts[1][:2] - pts[0][:2]
    n = float(np.linalg.norm(v))
    if n < 1e-9:
        raise RidePickError(tr("ride_pick.arrastre_corto"))
    v = v / n
    return (float(round(float(v[0]), 2)) + 0.0, float(round(float(v[1]), 2)) + 0.0, 0.0)


def direction_from_widget(x0: float, y0: float, x1: float, y1: float, widget_size, image_size, parts, anchor,
                          yaw_off: float = 0.0, pitch_off: float = 0.0) -> tuple:
    """Arrastre en el widget de (x0, y0) a (x1, y1) → dirección hacia la cabeza del caballo para el asiento `anchor`."""
    iw, ih = image_size
    if min(widget_size[0], widget_size[1], iw, ih) <= 0:
        raise RidePickError(tr("ride_pick.arrastre_corto"))
    p0 = MP._widget_to_image_free(x0, y0, widget_size[0], widget_size[1], iw, ih)
    p1 = MP._widget_to_image_free(x1, y1, widget_size[0], widget_size[1], iw, ih)
    k = min(widget_size[0] / iw, widget_size[1] / ih)
    return direction_on_ground(MP.group_camera(parts, iw, ih, yaw_off, pitch_off), anchor, p0, p1, iw, ih, min_px=MP.MIN_DRAG_PX / max(k, 1e-9))


# --------------------------------------------------------------------------------------------------------------------- importar caballo
def suggest_horse_id(path: str) -> str:
    """Un `id` válido para la biblioteca (minúsculas, dígitos y guiones) a partir del nombre del archivo."""
    base = os.path.splitext(os.path.basename(path))[0].lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base or "caballo"


def horse_measure(mesh, tipo: str) -> float:
    """Medida (en las unidades del STL, ya girado para que la cabeza mire a +Y) que fija la escala: `largo` (Y), `alto_total` (Z) o `alto_dorso`:
    del suelo a la altura máxima de la **mitad trasera** (la más lejos de la cabeza). El cuello y la cruz suben mucho más que el dorso y la
    grupa, así que no cuentan; el dorso de `horse_baseless.stl` queda a 32,5 u de las pezuñas (≈97 mm a 3×)."""
    if tipo not in REF_KINDS:
        raise RidePickError(tr("ride_pick.ref_desconocida", tipo=tipo))
    v = np.asarray(mesh.vertices, float)
    if tipo == "largo":
        return float(np.ptp(v[:, 1]))
    if tipo == "alto_total":
        return float(np.ptp(v[:, 2]))
    rear = v[v[:, 1] <= (float(v[:, 1].min()) + float(v[:, 1].max())) / 2.0]
    return float(rear[:, 2].max() - v[:, 2].min()) if len(rear) else float(np.ptp(v[:, 2]))


def suggest_scale(stl_path: str, tipo: str, mm: float, yaw: float = 0.0) -> float:
    """Escala que lleva la medida `tipo` del STL (girado `yaw` grados sobre Z) a `mm` milímetros reales."""
    try:
        mm = float(mm)
    except (TypeError, ValueError):
        raise RidePickError(tr("ride_pick.ref_mm")) from None
    if not mm > 0:
        raise RidePickError(tr("ride_pick.ref_mm"))
    try:
        mesh = trimesh.load(stl_path, file_type="stl", process=False, force="mesh")
    except Exception as exc:                                  # noqa: BLE001  (trimesh levanta tipos variados)
        raise RidePickError(tr("ride_pick.stl_ilegible", exc=exc)) from None
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise RidePickError(tr("ride_pick.stl_vacio"))
    mesh = mesh.copy()
    mesh.apply_transform(trimesh.transformations.rotation_matrix(math.radians(float(yaw)), [0, 0, 1]))
    m = horse_measure(mesh, tipo)
    if not m > 1e-9:
        raise RidePickError(tr("ride_pick.sin_medida", tipo=tipo))
    return mm / m


# --------------------------------------------------------------------------------------------------------------------- escenas
def _bounds(parts) -> np.ndarray:
    return MP.parts_bounds(parts)


def horse_scene(horse) -> RideScene:
    """El caballo solo (en su marco), que es donde se elige el asiento."""
    parts = [(HORSE_PART, horse.mesh, HORSE_COLOR)]
    return RideScene(parts, _bounds(parts))


def flagged(texts) -> set:
    """Nombres que los mensajes del motor señalan: las dos puntas de cada «A ↔ B» y, por el prefijo, el caballo («tallado:», «caballo tallado:»)
    o la silla («silla:», «despeje:»)."""
    out = set()
    for t in texts:
        for a, b in PAIR_RE.findall(t):
            out.update((a, b))
        m = PREFIX_RE.match(t)
        if m and m.group(1).strip().lower() in PREFIX_TARGET:
            out.add(PREFIX_TARGET[m.group(1).strip().lower()])
    return out


def _tint(base, tone, k: float = TINT):
    return tuple(float(b * (1.0 - k) + t * k) for b, t in zip(base, tone))


def _preview_mesh(mf):
    """Malla para dibujar: la pieza decimada si es muy pesada (solo la vista previa; la validación y la exportación usan la original)."""
    try:
        n = int(mf.num_tri())
        if n > PREVIEW_MAX_TRIS and S.can_decimate():
            mf = S.decimate(mf, reduction=1.0 - PREVIEW_MAX_TRIS / n)
    except Exception:                                         # noqa: BLE001  (sin decimar se dibuja entera)
        pass
    return S.to_trimesh(mf)


def split_cut(carved_mesh, tool) -> tuple:
    """Parte la malla del caballo tallado en (resto, caras del corte): las caras a menos de `CUT_BAND_MM` de la herramienta son la pared del
    corte. `caras del corte` es None si no hay ninguna."""
    tm = S.to_trimesh(tool)
    cent = np.asarray(carved_mesh.triangles_center, float)
    near = _surface_distance(cent, np.asarray(tm.vertices, float), np.asarray(tm.faces, np.int64)) < CUT_BAND_MM
    if not near.any():
        return carved_mesh, None
    v, f = np.asarray(carved_mesh.vertices, float), np.asarray(carved_mesh.faces, np.int64)
    rest = trimesh.Trimesh(v, f[~near], process=False) if (~near).any() else None
    return rest, trimesh.Trimesh(v, f[near], process=False)


def level_of(fit) -> str:
    return "no" if not fit.ok else ("aviso" if fit.warnings else "ok")


def fit_text(fit) -> str:
    """El resultado de «Calcular y comprobar pose» para mostrar: veredicto, problemas y avisos del motor, lo que se talló, la pared de la silla y
    la holgura más corta; al final, qué significa cada color."""
    level = level_of(fit)
    lines = [tr({"ok": "ride_pick.fit_ok", "aviso": "ride_pick.fit_aviso", "no": "ride_pick.fit_no"}[level])]
    lines += [f"- {p}" for p in fit.problems]
    lines += [f"- {w}" for w in fit.warnings]
    if not fit.ok:
        lines.append(tr("ride_pick.fit_etapa", etapa=fit.stage))
    v = fit.validation or {}
    info = v.get("info") or {}
    t = info.get("tallado")
    if t:
        wall = t.get("pared_medida_mm", t.get("pared_mm"))
        lines.append(tr("ride_pick.fit_tallado", vol=f"{t['retirado_mm3'] / 1000.0:.2f}", pct=f"{100.0 * t['fraccion']:.2f} %",
                        prof=f"{t['profundidad_mm']:.2f}", pared="–" if wall is None else f"{wall:.2f}"))
    if info.get("silla_pared_mm") is not None:
        lines.append(tr("ride_pick.fit_silla_pared", mm=f"{info['silla_pared_mm']:.2f}"))
    gaps = {k: g for k, g in (v.get("gaps") or {}).items() if g is not None}
    if gaps:
        k = min(gaps, key=gaps.get)
        lines.append(tr("ride_pick.fit_holgura", par=k, mm=f"{gaps[k]:.2f}"))
    lines.append(tr("ride_pick.fit_leyenda"))
    return "\n".join(lines)


def diagnostic_scene(fit, horse=None) -> RideScene:
    """El conjunto jinete + caballo (+ silla) de un `FitResult` con los colores de diagnóstico. `horse` solo hace falta si el ajuste se detuvo
    antes de colocar al jinete (no hay `fit.assembly`): se dibuja el caballo con el asiento."""
    ra = fit.assembly
    horse = ra.horse if ra is not None else horse
    if horse is None:
        raise RidePickError(tr("ride_pick.sin_caballo"))
    bad = flagged(fit.problems)
    warn = flagged(fit.warnings) - bad

    def tone(name):
        return BAD_COLOR if name in bad else WARN_COLOR if name in warn else None

    def big(name, base):                                       # caballo y silla: teñidos, no del todo de color
        t = tone(name)
        return base if t is None else _tint(base, t)

    parts = []
    carve = ra.carve if ra is not None else None
    if carve is not None and carve.ok and carve.carved is not None and carve.tool is not None and carve.removed_volume > 0:
        carved = S.to_trimesh(carve.carved)                    # sin decimar: la pared del corte tiene que seguir sobre la herramienta
        try:
            rest, cut = split_cut(carved, carve.tool)
        except Exception:                                      # noqa: BLE001  (sin colorear el corte se dibuja igual)
            rest, cut = carved, None
        if rest is not None:
            parts.append((HORSE_PART, rest, big(HORSE_PART, HORSE_COLOR)))
        if cut is not None:
            parts.append((CUT_PART, cut, CUT_COLOR))
    else:
        parts.append((HORSE_PART, horse.mesh, big(HORSE_PART, HORSE_COLOR)))
    if ra is not None:
        sad = ra.saddle_mf
        if sad is not None:
            parts.append((SADDLE_PART, _preview_mesh(sad), big(SADDLE_PART, SADDLE_COLOR)))
        for name, mf in ra.rider.items():
            parts.append((name, _preview_mesh(mf), tone(name) or (LEG_COLOR if name in ra.lower else RIDER_COLOR)))
    return RideScene(parts, _bounds(parts), seat_marks(fit.seat_origin, fit.direction), level_of(fit), fit_text(fit))
