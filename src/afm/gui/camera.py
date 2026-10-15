"""Cámara orbital para el visor 3D (Z arriba). Matemática pura con numpy: se prueba sin GTK ni OpenGL."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def look_at(eye, target, up=(0.0, 0.0, 1.0)) -> np.ndarray:
    eye, target, up = (np.asarray(v, float) for v in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def perspective(fov_y_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    t = 1.0 / np.tan(np.radians(fov_y_deg) / 2)
    m = np.zeros((4, 4))
    m[0, 0] = t / aspect
    m[1, 1] = t
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def orthographic(half_h: float, aspect: float, near: float, far: float) -> np.ndarray:
    """Proyección ortográfica: el tamaño en pantalla no depende de la distancia a la cámara."""
    m = np.eye(4)
    m[0, 0] = 1.0 / (half_h * aspect)
    m[1, 1] = 1.0 / half_h
    m[2, 2] = -2.0 / (far - near)
    m[2, 3] = -(far + near) / (far - near)
    return m


@dataclass
class OrbitCamera:
    """`yaw` es el acimut (90° = mirando la figura de frente, desde +Y) y `pitch` la elevación."""

    target: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 50.0]))
    distance: float = 300.0
    yaw: float = 90.0
    pitch: float = 10.0
    fov: float = 35.0
    radius: float = 100.0          # radio de la escena (para near/far)
    ortho: bool = True             # ortográfica por defecto: sin perspectiva no se deforma lo que se elige

    def eye(self) -> np.ndarray:
        y, p = np.radians(self.yaw), np.radians(self.pitch)
        d = np.array([np.cos(p) * np.cos(y), np.cos(p) * np.sin(y), np.sin(p)])
        return np.asarray(self.target, float) + self.distance * d

    def view(self) -> np.ndarray:
        return look_at(self.eye(), self.target)

    def clip(self) -> tuple[float, float]:
        near = max(self.distance - 2.5 * self.radius, self.distance * 0.02, 0.05)
        return near, self.distance + 2.5 * self.radius

    def proj(self, aspect: float) -> np.ndarray:
        near, far = self.clip()
        if self.ortho:
            # misma altura visible que la perspectiva en el objetivo, así el zoom se siente igual
            return orthographic(self.distance * np.tan(np.radians(self.fov) / 2), aspect, near, far)
        return perspective(self.fov, aspect, near, far)

    def mvp(self, aspect: float) -> np.ndarray:
        return self.proj(aspect) @ self.view()

    # --- interacción
    def orbit(self, dx_px: float, dy_px: float, speed: float = 0.4) -> None:
        self.yaw = (self.yaw - dx_px * speed) % 360.0
        self.pitch = float(np.clip(self.pitch + dy_px * speed, -89.0, 89.0))

    def pan(self, dx_px: float, dy_px: float, height_px: float) -> None:
        """Desplaza el objetivo en el plano de la pantalla (arrastrar mueve la escena con el cursor)."""
        v = self.view()
        right, up = v[0, :3], v[1, :3]
        per_px = 2 * self.distance * np.tan(np.radians(self.fov) / 2) / max(height_px, 1.0)
        self.target = np.asarray(self.target, float) - right * dx_px * per_px + up * dy_px * per_px

    def zoom(self, factor: float) -> None:
        self.distance = float(np.clip(self.distance * factor, self.radius * 0.1, self.radius * 40))

    def frame(self, bounds) -> None:
        """Encuadra una caja envolvente (2x3)."""
        b = np.asarray(bounds, float)
        self.target = b.mean(axis=0)
        self.radius = float(np.linalg.norm(b[1] - b[0]) / 2) or 1.0
        self.distance = self.radius / np.sin(np.radians(self.fov) / 2) * 1.05

    def set_view(self, name: str) -> None:
        views = {"frente": (90.0, 0.0), "espalda": (270.0, 0.0), "izquierda": (180.0, 0.0),
                 "derecha": (0.0, 0.0), "arriba": (90.0, 89.0), "iso": (60.0, 20.0)}
        self.yaw, self.pitch = views[name]

    # --- proyección y rayos
    def ray(self, px: float, py: float, width: float, height: float) -> tuple[np.ndarray, np.ndarray]:
        """Rayo (origen, dirección unitaria) del píxel (px, py), con py creciendo hacia abajo."""
        x = 2.0 * px / width - 1.0
        y = 1.0 - 2.0 * py / height
        inv = np.linalg.inv(self.mvp(width / height))
        p_near = inv @ np.array([x, y, -1.0, 1.0])
        p_far = inv @ np.array([x, y, 1.0, 1.0])
        p_near, p_far = p_near[:3] / p_near[3], p_far[:3] / p_far[3]
        d = p_far - p_near
        # en ortográfica los rayos son paralelos y salen del plano cercano, no del ojo
        return (p_near if self.ortho else self.eye()), d / np.linalg.norm(d)

    def project(self, pts, width: float, height: float) -> np.ndarray:
        """Puntos del mundo -> píxeles (x, y) y profundidad NDC (columna 3)."""
        p = np.atleast_2d(np.asarray(pts, float))
        h = np.c_[p, np.ones(len(p))] @ self.mvp(width / height).T
        w = h[:, 3:4]
        ndc = h[:, :3] / w
        return np.c_[(ndc[:, 0] + 1) * 0.5 * width, (1 - ndc[:, 1]) * 0.5 * height, ndc[:, 2]]
