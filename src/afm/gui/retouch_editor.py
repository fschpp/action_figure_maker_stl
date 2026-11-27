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
from . import retouch_shapes as SH
from .picking import Picker
from .retouch_session import ERASE, FILL, ORPHAN, PAINT, RetouchSession

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


def radius_clipped(a, b) -> bool:
    return SH.radius_of(a, b) > SH.MAX_RADIUS_MM


class RetouchEditor:
    def __init__(self, catalog, meta, mesh, retouch: Retouch | None = None):
        self.meta, self.mesh = meta, mesh
        self.palette = catalog.palette()
        ids = self.palette.ids() if self.palette is not None else []
        self.session = RetouchSession(retouch if retouch is not None else catalog.retouch(meta.ref), ids)
        self._picker: Picker | None = None
        self._face: int | None = None               # cara de la pieza que dio el último `pick` (para «Polígono entero»)
        self.message = ""
        self.result_shown = False                  # la vista ya muestra el color real del retoque sin guardar («Ver resultado»)
        self._hover: np.ndarray | None = None      # aristas (E, 2, 3) de lo que el pincel pintaría bajo el mouse (None = nada)
        self._sd: dict | None = None               # arrastre de una forma en curso: {"a", "n", "u", "v", "b"} (None = no hay)

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
            self._face = None
            return None
        self._face = int(res.face)
        n = np.asarray(res.normal, float)
        if float(n @ direction) > 0:               # la normal de la cara tiene que mirar hacia la cámara
            n = -n
        return np.asarray(res.hit, float), n

    # ------------------------------------------------------------------ mouse
    def _tri(self):
        """Los 3 vértices de la cara bajo el mouse si «Polígono entero» está activo (y no se está rellenando); si no, `None`."""
        if not self.session.polygon or self.session.mode == FILL or self._face is None:
            return None
        return np.asarray(self.mesh.triangles, float)[self._face]

    # ------------------------------------------------------------------ formas (línea, círculo, cuadrado)
    def _project(self, P, n, tol: float):
        """Proyecta `P` sobre la pieza con un rayo en dirección `-n` desde `tol + 1` mm por encima: `(impacto, normal)` o `None` si no hay pieza
        a menos de `tol` mm del plano tangente o la cara no mira hacia `n`."""
        P, n = np.asarray(P, float), np.asarray(n, float)
        if self._picker is None:
            self._picker = Picker(self.mesh)
        res = self._picker.pick(P + n * (tol + 1.0), -n, snap_radius=0.0)
        if res is None:
            return None
        hit = np.asarray(res.hit, float)
        if abs(float((hit - P) @ n)) > tol:
            return None
        nrm = np.asarray(res.normal, float)
        if float(nrm @ n) < 0:
            nrm = -nrm
        return (hit, nrm) if float(nrm @ n) >= MIN_DOT else None

    def _shape_press(self, x, y, widget_size, image_size, yaw_off=0.0, pitch_off=0.0, zoom=1.0, pan=(0.0, 0.0)) -> bool:
        p = self.pick(x, y, widget_size, image_size, yaw_off, pitch_off, zoom, pan)
        if p is None:
            return False
        a, n = p
        right = self.camera(image_size, yaw_off, pitch_off, zoom, pan).view()[0, :3]
        u, v = SH.tangent_basis(n, right)
        self._sd = {"a": a, "n": n, "u": u, "v": v, "b": a}
        self.result_shown = False
        self._hover = SH.outline(self.session.shape, a, a, n, u, v)
        return True

    def _shape_move(self, x, y, widget_size, image_size, yaw_off=0.0, pitch_off=0.0, zoom=1.0, pan=(0.0, 0.0)) -> int:
        d = self._sd
        p = self.pick(x, y, widget_size, image_size, yaw_off, pitch_off, zoom, pan)
        if p is not None:                                          # fuera de la pieza se queda el último punto bueno
            d["b"] = p[0]
            self._hover = SH.outline(self.session.shape, d["a"], d["b"], d["n"], d["u"], d["v"])
        return 0

    def _shape_release(self) -> int:
        d, self._sd = self._sd, None
        self._hover = None
        items = SH.samples(self.session.shape, d["a"], d["b"], d["n"], d["u"], d["v"], self.session.radius, self._project)
        try:
            n = self.session.add_shape(items)
        except Exception as exc:                                   # noqa: BLE001  (RetouchError: no entran / sin rol)
            self.message = str(exc)
            return 0
        if SH.CIRCLE == self.session.shape and radius_clipped(d["a"], d["b"]):
            self.message = f"círculo recortado al radio máximo ({SH.MAX_RADIUS_MM:g} mm)"
        return n

    @property
    def shaping(self) -> bool:
        return self._sd is not None

    def press(self, *pick_args, **pick_kw) -> bool:
        """Empieza un trazo donde se hizo clic. `False` si ahí no hay pieza."""
        if self.session.shape:
            return self._shape_press(*pick_args, **pick_kw)
        p = self.pick(*pick_args, **pick_kw)
        if p is None:
            return False
        self.session.begin_stroke(*p, tri=self._tri())
        self.result_shown = False                  # el color real queda viejo
        return True

    def move(self, *pick_args, **pick_kw) -> int:
        """Sigue el trazo; devuelve las pinceladas agregadas (0 si el mouse salió de la pieza)."""
        if self._sd is not None:
            return self._shape_move(*pick_args, **pick_kw)
        if not self.session.stroking:
            return 0
        p = self.pick(*pick_args, **pick_kw)
        return 0 if p is None else self.session.add_point(*p, tri=self._tri())

    def release(self) -> int:
        if self._sd is not None:
            return self._shape_release()
        n = self.session.end_stroke()
        if self.session.truncated:
            self.message = f"máx. de pinceladas alcanzado ({len(self.session)})"
        return n

    # ------------------------------------------------------------------ resaltado bajo el mouse
    def hover(self, *pick_args, **pick_kw) -> bool:
        """El mouse está sobre la vista: calcula las aristas de las caras que se pintarían con un clic ahí (el pincel actual; en «Rellenar»,
        la cara donde empezaría el relleno). Devuelve `True` si el resaltado cambió (hay que redibujar).
        Con una forma elegida no hay resaltado del pincel (mientras se arrastra, el contorno de la forma lo pone `move`)."""
        if self.session.shape:
            return False if self._sd is not None else self.clear_hover()
        p = self.pick(*pick_args, **pick_kw)
        if p is None:
            return self.clear_hover()
        c, n = p
        faces = (np.array([self._face]) if self.session.polygon and self._face is not None and self.session.mode != FILL     # «Polígono entero»: solo la cara que se pintaría
                 else brush_faces(self.mesh, c, n, self.session.radius, only_nearest=self.session.mode == FILL))
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
    # ------------------------------------------------------------------ estado
    @property
    def dirty(self) -> bool:
        return self.session.dirty

    def apply(self, catalog):
        """Guarda en el catálogo (lo que ve la vista previa guardada cambia). `CatalogError` si la paleta no tiene algún rol."""
        meta = self.session.apply(catalog, self.meta.ref)
        self.meta = meta
        return meta


__all__ = ["RetouchEditor", "PAINT", "ERASE", "FILL", "ORPHAN", "HOVER_MAX_EDGES", "brush_faces", "face_edges"]
