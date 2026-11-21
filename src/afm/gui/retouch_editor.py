"""Modo «Retocar color» de la ventana de Catálogo, sin GTK (`PLAN_retoque_color_pieza.md`, R07).

Une tres cosas: la `RetouchSession` (R06: rol, radio, trazos, deshacer), la malla de la pieza (marco del STL, el mismo de las pinceladas) y el
*picking* (rayo del píxel → punto y normal de la superficie). La ventana solo traduce el mouse a `press` / `move` / `release` y dibuja lo que
devuelve `overlay()`.

El punto pintado es el impacto exacto del rayo (sin ajustar a vértices: el pincel no es un selector de puntos) y la normal es la de la cara.
"""
from __future__ import annotations

import numpy as np

from ..retouch import MIN_DOT, Retouch
from .camera import OrbitCamera
from .catalog_manager import preview_camera, view_bounds, widget_to_image
from .picking import Picker
from .retouch_session import ERASE, FILL, PAINT, RetouchSession

OVERLAY_MAX_POINTS = 3000          # puntos que se dibujan encima de la pieza (los más recientes): dibujar 20 000 en cada movimiento sobra
ERASER_COLOR = (1.0, 1.0, 1.0, 1.0)
HOVER_MAX_EDGES = 6000             # aristas que se resaltan como máximo (con el pincel grande sobre una malla densa)


def brush_faces(mesh, c, n, radius: float, only_nearest: bool = False) -> np.ndarray:
    """Índices de las caras que el pincel de radio `radius` en `c` (normal `n`) alcanzaría a pintar: las que tienen su centro o algún
    vértice a menos de `radius` mm de `c` y una normal compatible con `n` (el mismo criterio de `MIN_DOT` del horneado). Siempre incluye la
    cara más cercana. `only_nearest=True` devuelve solo esa (la semilla de «Rellenar región», cuyo alcance real depende del color)."""
    c = np.asarray(c, float)
    tri = np.asarray(mesh.triangles, float)                          # (F, 3, 3)
    d_v = np.linalg.norm(tri - c, axis=2)                            # (F, 3)
    d_c = np.linalg.norm(tri.mean(axis=1) - c, axis=1)               # (F,)
    d = np.minimum(d_v.min(axis=1), d_c)
    near = int(np.argmin(d_c))
    if only_nearest:
        return np.array([near])
    ok = d <= float(radius)
    if n is not None:
        ok &= np.asarray(mesh.face_normals, float) @ np.asarray(n, float) >= MIN_DOT
    ok[near] = True
    idx = np.flatnonzero(ok)
    return idx


def face_edges(mesh, faces: np.ndarray, limit: int = HOVER_MAX_EDGES) -> np.ndarray:
    """Aristas (sin repetir) de `faces` como segmentos `(E, 2, 3)` en el marco de la malla; como mucho `limit`."""
    e = np.asarray(mesh.faces)[faces][:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    e = np.unique(np.sort(e, axis=1), axis=0)
    if len(e) > limit:
        e = e[:: int(np.ceil(len(e) / limit))]
    return np.asarray(mesh.vertices, float)[e]


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
        self._hover: np.ndarray | None = None      # aristas (E, 2, 3) de lo que el pincel pintaría bajo el mouse (None = nada)

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
        self.result_shown = False                  # el color real queda viejo: mientras se pinta vuelven los puntos guía
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

    # ------------------------------------------------------------------ resaltado bajo el mouse
    def hover(self, *pick_args, **pick_kw) -> bool:
        """El mouse está sobre la vista: calcula las aristas de las caras que se pintarían con un clic ahí (el pincel actual; en «Rellenar»,
        la cara donde empezaría el relleno). Devuelve `True` si el resaltado cambió (hay que redibujar)."""
        p = self.pick(*pick_args, **pick_kw)
        if p is None:
            return self.clear_hover()
        c, n = p
        faces = brush_faces(self.mesh, c, n, self.session.radius, only_nearest=self.session.mode == FILL)
        new = face_edges(self.mesh, faces)
        changed = self._hover is None or self._hover.shape != new.shape or not np.array_equal(self._hover, new)
        self._hover = new
        return changed

    def clear_hover(self) -> bool:
        """Quita el resaltado (el mouse salió de la pieza o de la vista). `True` si había uno."""
        had = self._hover is not None
        self._hover = None
        return had

    def hover_segments(self) -> np.ndarray | None:
        """Aristas `(E, 2, 3)` a resaltar encima de la pieza, o `None`."""
        return self._hover

    # ------------------------------------------------------------------ dibujo
    def overlay(self) -> dict:
        """Lo que se dibuja encima de la pieza: `{nombre: (puntos Nx3, rgba)}`, un grupo por rol (el borrador, en blanco). Son los centros de las
        pinceladas, no el color real (ese lo da «Ver resultado» o «Aplicar»). Solo son la guía mientras se pinta y mientras se calcula el color
        real: cuando la vista ya muestra el color real (`result_shown`) no se dibujan, para no tapar la pieza con manchas."""
        if self.result_shown and not self.session.stroking:
            return {}
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


__all__ = ["RetouchEditor", "PAINT", "ERASE", "FILL", "OVERLAY_MAX_POINTS", "HOVER_MAX_EDGES", "brush_faces", "face_edges"]
