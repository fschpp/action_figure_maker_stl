"""M14: ancla y dirección de una montura elegidas con el ratón sobre la vista previa conjunta (plan §7, punto 2).

Lógica pura (numpy + cámara de la vista previa), sin GTK: la ventana solo traduce el clic y el arrastre a píxeles del widget y
muestra el resultado. La vista previa es una imagen que dibuja `PreviewRenderer.render_parts` con `preview_camera`; aquí se
arma **la misma cámara** (mismos límites, mismo giro) para que el punto elegido caiga donde se hizo clic.

* **Ancla:** el rayo del clic tiene que dar primero en la pieza que lleva la montura (pelvis o torso). Si antes da en otra (un
  brazo que tapa el flanco) se avisa en vez de poner el ancla en un lugar que no se ve. El lado sale del signo de X: los
  puntos de +X son `izq` y los de −X `der`, y el ancla queda en el marco real de la pieza, como pide `MountSpec`.
* **Dirección:** el arrastre va de la empuñadura hacia la punta. Se proyecta sobre el plano que pasa por el ancla y es
  perpendicular a la cámara; la profundidad no se puede leer de un gesto en 2D, así que la dirección sale en ese plano (para
  salir de él se gira la vista o se escriben los números). Cerca de la vertical se pega a ±Z, que es la que retiene.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..i18n import tr
from ..mount import surface_normal
from .catalog_manager import preview_camera, widget_to_image
from .picking import ray_first_hit

HOST_PARTS = {"pelvis": ("pelvis", "torso"), "torso": ("torso",)}   # pieza de la vista previa que lleva cada `lugar` (la pelvis cae en el torso sin cintura)
CLICK_SLOP_PX = 4.0           # un gesto que se mueve menos que esto es un clic; más, un giro de la vista
MIN_DRAG_PX = 8.0             # un arrastre más corto que esto no define una dirección
SNAP_VERTICAL_DEG = 6.0       # a menos de este ángulo de ±Z la dirección se pega a la vertical
MARK_STEP_MM = 1.5            # separación de los puntos que dibujan la dirección de cada montura
MARK_COUNT = 8


class MountPickError(ValueError):
    """El clic o el arrastre no definen un ancla o una dirección; el mensaje está listo para mostrar."""


@dataclass(frozen=True)
class AnchorPick:
    ancla: tuple          # (x, y, z) en el marco de la pieza, redondeado a 0,01 mm (lo que muestra el formulario)
    lado: str             # «izq» si x >= 0, «der» si x < 0
    normal: tuple         # normal saliente de la superficie en el ancla
    pieza: str            # nombre de la pieza de la vista previa que se tocó


def host_part_name(lugar: str, names) -> str:
    """Nombre de la pieza de la vista previa que lleva una montura en `lugar`."""
    have = set(names)
    for name in HOST_PARTS.get(lugar, ()):
        if name in have:
            return name
    raise MountPickError(tr("figure_window.pick_sin_pieza", lugar=lugar))


def parts_bounds(parts) -> np.ndarray:
    """Caja (2x3) de las piezas de la vista previa; es el encuadre con el que se dibuja y con el que se elige."""
    lo = np.min([np.asarray(m.bounds, float)[0] for _n, m, *_c in parts], axis=0)
    hi = np.max([np.asarray(m.bounds, float)[1] for _n, m, *_c in parts], axis=0)
    return np.array([lo, hi])


def group_camera(parts, img_w: int, img_h: int, yaw_off: float = 0.0, pitch_off: float = 0.0):
    """La cámara con la que `render_parts(parts, yaw_off, pitch_off, bounds=parts_bounds(parts))` dibuja la vista conjunta."""
    return preview_camera(parts_bounds(parts), img_w / img_h, yaw_off, pitch_off)


def pick_anchor(camera, px: float, py: float, width: float, height: float, parts, lugar: str) -> AnchorPick:
    """Ancla bajo el píxel (px, py) de una imagen `width` × `height`. Levanta `MountPickError` si el clic cae fuera de la figura o
    si lo primero que ve el rayo no es la pieza que lleva la montura."""
    meshes = {n: m for n, m, *_c in parts}
    host = host_part_name(lugar, meshes)
    o, d = camera.ray(px, py, width, height)
    best = None
    for name, mesh in meshes.items():
        r = ray_first_hit(np.asarray(mesh.triangles, float), o, d)
        if r is not None and (best is None or r[1] < best[1]):
            best = (name, r[1])
    if best is None:
        raise MountPickError(tr("figure_window.pick_fuera_de_la_figura"))
    if best[0] != host:
        raise MountPickError(tr("figure_window.pick_tapado", pieza=best[0], host=host))
    hit = o + best[1] * d
    normal = surface_normal(meshes[host], hit)
    ancla = tuple(float(round(float(v), 2)) + 0.0 for v in hit)
    return AnchorPick(ancla, "izq" if hit[0] >= 0.0 else "der", tuple(float(v) for v in normal), host)


def direction_from_drag(camera, anchor, p0, p1, width: float, height: float, snap_deg: float = SNAP_VERTICAL_DEG,
                        min_px: float = MIN_DRAG_PX) -> tuple:
    """Dirección empuñadura → punta de un arrastre de `p0` a `p1` (píxeles de la imagen): el vector entre los dos puntos
    proyectados sobre el plano de `anchor` perpendicular a la cámara, unitario y redondeado a 0,01. Cerca de ±Z se pega a la vertical."""
    if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) < min_px:
        raise MountPickError(tr("figure_window.pick_arrastre_corto"))
    fwd = np.asarray(camera.target, float) - np.asarray(camera.eye(), float)
    fwd /= np.linalg.norm(fwd)
    a = np.asarray(anchor, float)
    pts = []
    for px, py in (p0, p1):
        o, d = camera.ray(px, py, width, height)
        den = float(d @ fwd)
        if abs(den) < 1e-9:
            raise MountPickError(tr("figure_window.pick_arrastre_corto"))
        pts.append(o + (float((a - o) @ fwd) / den) * d)
    v = pts[1] - pts[0]
    n = float(np.linalg.norm(v))
    if n < 1e-9:
        raise MountPickError(tr("figure_window.pick_arrastre_corto"))
    v = v / n
    for z in ((0.0, 0.0, -1.0), (0.0, 0.0, 1.0)):
        if math.degrees(math.acos(float(np.clip(v @ np.array(z), -1.0, 1.0)))) <= snap_deg:
            v = np.array(z)
    return tuple(float(round(float(c), 2)) + 0.0 for c in v)


def _widget_to_image_free(x: float, y: float, widget_w: float, widget_h: float, img_w: int, img_h: int):
    """Como `widget_to_image` pero sin recortar al borde de la imagen: el final de un arrastre puede salirse y sigue valiendo."""
    k = min(widget_w / img_w, widget_h / img_h)
    return (x - (widget_w - img_w * k) / 2.0) / k, (y - (widget_h - img_h * k) / 2.0) / k


def pick_from_widget(x: float, y: float, widget_size, image_size, parts, lugar: str, yaw_off: float = 0.0, pitch_off: float = 0.0) -> AnchorPick:
    """Clic en el widget (x, y) → ancla. `widget_size` e `image_size` son (ancho, alto); la imagen se ve con `ContentFit.CONTAIN`."""
    iw, ih = image_size
    hit = widget_to_image(x, y, widget_size[0], widget_size[1], iw, ih)
    if hit is None:
        raise MountPickError(tr("figure_window.pick_fuera_de_la_figura"))
    return pick_anchor(group_camera(parts, iw, ih, yaw_off, pitch_off), hit[0], hit[1], iw, ih, parts, lugar)


def direction_from_widget(x0: float, y0: float, x1: float, y1: float, widget_size, image_size, parts, anchor,
                          yaw_off: float = 0.0, pitch_off: float = 0.0) -> tuple:
    """Arrastre en el widget de (x0, y0) a (x1, y1) → dirección de la montura cuyo ancla es `anchor`."""
    iw, ih = image_size
    if min(widget_size[0], widget_size[1], iw, ih) <= 0:
        raise MountPickError(tr("figure_window.pick_arrastre_corto"))
    p0 = _widget_to_image_free(x0, y0, widget_size[0], widget_size[1], iw, ih)
    p1 = _widget_to_image_free(x1, y1, widget_size[0], widget_size[1], iw, ih)
    k = min(widget_size[0] / iw, widget_size[1] / ih)
    cam = group_camera(parts, iw, ih, yaw_off, pitch_off)
    return direction_from_drag(cam, anchor, p0, p1, iw, ih, min_px=MIN_DRAG_PX / max(k, 1e-9))


def is_click(dx: float, dy: float) -> bool:
    """¿El gesto se movió tan poco que es un clic y no un giro de la vista?"""
    return math.hypot(dx, dy) <= CLICK_SLOP_PX


def mount_marks(mounts) -> np.ndarray | None:
    """Puntos para dibujar encima de la vista previa (`render_parts(marks=…)`): el ancla de cada montura y una fila de puntos
    a lo largo de su dirección (empuñadura → punta). None si no hay monturas."""
    pts = []
    for m in mounts:
        a = np.asarray(m.ancla, float)
        d = np.asarray(m.direccion, float)
        n = float(np.linalg.norm(d))
        pts.append(a)
        if n > 1e-9:
            pts.extend(a + (d / n) * (MARK_STEP_MM * k) for k in range(1, MARK_COUNT + 1))
    return np.array(pts) if pts else None
