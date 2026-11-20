"""Modo «Retocar color» de la ventana de Catálogo, sin GTK (`PLAN_retoque_color_pieza.md`, R07).

Une tres cosas: la `RetouchSession` (R06: rol, radio, trazos, deshacer), la malla de la pieza (marco del STL, el mismo de las pinceladas) y el
*picking* (rayo del píxel → punto y normal de la superficie). La ventana solo traduce el mouse a `press` / `move` / `release` y dibuja lo que
devuelve `overlay()`.

El punto pintado es el impacto exacto del rayo (sin ajustar a vértices: el pincel no es un selector de puntos) y la normal es la de la cara.
"""
from __future__ import annotations

import numpy as np

from ..retouch import Retouch
from .camera import OrbitCamera
from .catalog_manager import preview_camera, view_bounds, widget_to_image
from .picking import Picker
from .retouch_session import ERASE, FILL, PAINT, RetouchSession

OVERLAY_MAX_POINTS = 3000          # puntos que se dibujan encima de la pieza (los más recientes): dibujar 20 000 en cada movimiento sobra
ERASER_COLOR = (1.0, 1.0, 1.0, 1.0)


def _rgba(hex_color: str | None, alpha: float = 1.0):
    if not hex_color or not str(hex_color).startswith("#") or len(hex_color) != 7:
        return (0.8, 0.8, 0.8, alpha)
    return tuple(int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5)) + (alpha,)


class RetouchEditor:
    def __init__(self, catalog, meta, mesh, retouch: Retouch | None = None):
        self.meta, self.mesh = meta, mesh
        self.palette = catalog.palette()
        ids = self.palette.ids() if self.palette is not None else []
        self.session = RetouchSession(retouch if retouch is not None else catalog.retouch(meta.ref), ids)
        self._picker: Picker | None = None
        self.message = ""
        self.result_shown = False                  # la vista ya muestra el color real del retoque sin guardar («Ver resultado»)

    # ------------------------------------------------------------------ picking
    def camera(self, image_size, yaw_off: float, pitch_off: float, zoom: float, pan) -> OrbitCamera:
        iw, ih = image_size
        return preview_camera(view_bounds(self.mesh, self.meta), iw / ih, yaw_off, pitch_off, zoom, pan)

    def pick(self, x: float, y: float, widget_size, image_size, yaw_off=0.0, pitch_off=0.0, zoom=1.0, pan=(0.0, 0.0)):
        """Clic en (x, y) del widget → `(punto, normal)` sobre la pieza (marco del STL), o `None` si cayó fuera de la imagen o sobre el fondo."""
        iw, ih = image_size
        hit = widget_to_image(x, y, widget_size[0], widget_size[1], iw, ih)
        if hit is None:
            return None
        cam = self.camera(image_size, yaw_off, pitch_off, zoom, pan)
        origin, direction = cam.ray(hit[0], hit[1], iw, ih)
        if self._picker is None:
            self._picker = Picker(self.mesh)
        res = self._picker.pick(origin, direction, snap_radius=0.0)
        if res is None:
            return None
        n = np.asarray(res.normal, float)
        if float(n @ direction) > 0:               # la normal de la cara tiene que mirar hacia la cámara
            n = -n
        return np.asarray(res.hit, float), n

    # ------------------------------------------------------------------ mouse
    def press(self, *pick_args, **pick_kw) -> bool:
        """Empieza un trazo donde se hizo clic. `False` si ahí no hay pieza."""
        p = self.pick(*pick_args, **pick_kw)
        if p is None:
            return False
        self.session.begin_stroke(*p)
        return True

    def move(self, *pick_args, **pick_kw) -> int:
        """Sigue el trazo; devuelve las pinceladas agregadas (0 si el mouse salió de la pieza)."""
        if not self.session.stroking:
            return 0
        p = self.pick(*pick_args, **pick_kw)
        return 0 if p is None else self.session.add_point(*p)

    def release(self) -> int:
        n = self.session.end_stroke()
        if self.session.truncated:
            self.message = f"máx. de pinceladas alcanzado ({len(self.session)})"
        return n

    # ------------------------------------------------------------------ dibujo
    def overlay(self) -> dict:
        """Lo que se dibuja encima de la pieza: `{nombre: (puntos Nx3, rgba)}`, un grupo por rol (el borrador, en blanco). Son los centros de las
        pinceladas, no el color real (ese lo da «Ver resultado» o «Aplicar»)."""
        strokes = self.session.retouch().strokes[-OVERLAY_MAX_POINTS:]
        groups: dict = {}
        for s in strokes:
            groups.setdefault(s.rol, []).append(s.c)
        out = {}
        for rol, pts in groups.items():
            color = ERASER_COLOR if rol is None else _rgba(self.palette.hex_of(rol) if self.palette is not None and rol in self.palette.ids() else None)
            out["retoque_" + (rol or "borrador")] = (np.asarray(pts, float), color)
        return out

    def dot_size_px(self, image_size, yaw_off, pitch_off, zoom, pan) -> float:
        """Diámetro en píxeles de un punto del pincel con el radio actual (para que el pincel dibujado tenga el tamaño del pincel real)."""
        iw, ih = image_size
        cam = self.camera(image_size, yaw_off, pitch_off, zoom, pan)
        c = np.asarray(cam.target, float)
        a = cam.project(c, iw, ih)[0, :2]
        v = cam.view()
        b = cam.project(c + v[0, :3], iw, ih)[0, :2]          # 1 mm hacia la derecha de la pantalla
        return float(np.clip(2.0 * self.session.radius * float(np.linalg.norm(b - a)), 4.0, 96.0))

    # ------------------------------------------------------------------ estado
    @property
    def dirty(self) -> bool:
        return self.session.dirty

    def apply(self, catalog):
        """Guarda en el catálogo (lo que ve la vista previa guardada cambia). `CatalogError` si la paleta no tiene algún rol."""
        meta = self.session.apply(catalog, self.meta.ref)
        self.meta = meta
        return meta


__all__ = ["RetouchEditor", "PAINT", "ERASE", "FILL", "OVERLAY_MAX_POINTS"]
