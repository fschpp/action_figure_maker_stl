"""Cubo de vista interactivo (como el de Bambu Studio): reemplaza los botones Frente / Arriba / Derecha…

Cada cara del cubo se divide en una grilla de 3×3 celdas: el centro es la vista de esa cara (6 vistas), las
celdas del borde son las vistas a 45° entre dos caras (12) y las de las esquinas son las vistas isométricas (8).
Hacer clic en una celda lleva la cámara a esa dirección; arrastrar el cubo orbita la cámara.

La matemática (qué celda está bajo el cursor, a qué ángulos corresponde, cómo se interpola) no depende de GTK ni
de cairo y se prueba sin pantalla. `draw` solo usa los métodos del contexto de cairo que recibe.

Convención de ejes (la misma que `OrbitCamera.set_view`): Z arriba, frente = +Y, «Der.» = mirar desde +X.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# (etiqueta, normal exterior). El orden fija el índice de cara que usan `Hit` y `draw`.
FACES = (
    ("Frente", (0.0, 1.0, 0.0)),
    ("Espalda", (0.0, -1.0, 0.0)),
    ("Der.", (1.0, 0.0, 0.0)),
    ("Izq.", (-1.0, 0.0, 0.0)),
    ("Arriba", (0.0, 0.0, 1.0)),
    ("Abajo", (0.0, 0.0, -1.0)),
)

SCALE = 0.27                  # semiarista del cubo = SCALE * lado del widget (deja lugar a las esquinas en diagonal)
CELL_EDGES = {-1: (-1.0, -1.0 / 3), 0: (-1.0 / 3, 1.0 / 3), 1: (1.0 / 3, 1.0)}
TOP_YAW = 90.0                # al mirar de arriba / de abajo el frente queda hacia abajo / arriba de la pantalla
POLE_PITCH = 89.0             # la cámara no llega a 90° (límite de `OrbitCamera.orbit`)


def face_basis(normal) -> tuple[np.ndarray, np.ndarray]:
    """Ejes (u, v) de una cara con u × v = normal; v apunta «hacia arriba» del texto de la cara."""
    n = np.asarray(normal, float)
    z = np.array([0.0, 0.0, 1.0])
    if abs(n[2]) < 0.5:                       # caras laterales: el texto se lee derecho
        return np.cross(z, n), z
    if n[2] > 0:                              # arriba: visto desde la vista «Arriba», el frente queda abajo
        return np.array([-1.0, 0.0, 0.0]), np.array([0.0, -1.0, 0.0])
    return np.array([-1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])


@dataclass(frozen=True)
class Hit:
    """Celda del cubo: cara `face` (índice en `FACES`), celda (i, j) con i, j ∈ {-1, 0, 1} sobre los ejes de la cara."""

    face: int
    i: int
    j: int

    @property
    def kind(self) -> str:
        return {0: "face", 1: "edge", 2: "corner"}[int(self.i != 0) + int(self.j != 0)]

    def direction(self) -> np.ndarray:
        """Dirección (unitaria) desde el centro de la figura hacia donde se pone la cámara."""
        n = np.asarray(FACES[self.face][1], float)
        u, v = face_basis(n)
        d = n + self.i * u + self.j * v
        return d / np.linalg.norm(d)


def view_angles(direction) -> tuple[float, float]:
    """(yaw, pitch) en grados de `OrbitCamera` para una cámara ubicada hacia `direction`."""
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    if abs(d[0]) < 1e-9 and abs(d[1]) < 1e-9:           # polos: el acimut no está definido
        return TOP_YAW, math.copysign(POLE_PITCH, d[2])
    yaw = math.degrees(math.atan2(d[1], d[0])) % 360.0
    pitch = float(np.clip(math.degrees(math.asin(np.clip(d[2], -1.0, 1.0))), -POLE_PITCH, POLE_PITCH))
    return yaw, pitch


def interpolate_view(start: tuple[float, float], end: tuple[float, float], t: float) -> tuple[float, float]:
    """Pasa de (yaw, pitch) a otro por el camino angular más corto, con arranque y frenado suaves (t de 0 a 1)."""
    t = min(max(t, 0.0), 1.0)
    k = t * t * (3.0 - 2.0 * t)
    dyaw = (end[0] - start[0] + 180.0) % 360.0 - 180.0
    return (start[0] + dyaw * k) % 360.0, start[1] + (end[1] - start[1]) * k


class ViewCube:
    """Geometría en pantalla del cubo para una rotación de cámara (matriz 3×3 mundo → vista) y un lado en píxeles."""

    def __init__(self, size: int = 120):
        self.size = size

    # ------------------------------------------------------------ geometría
    @property
    def _scale(self) -> float:
        return SCALE * self.size

    def project(self, rot, p) -> np.ndarray:
        """Punto del cubo (en unidades de semiarista) -> píxel (x, y) con y hacia abajo."""
        q = np.asarray(rot, float) @ np.asarray(p, float)
        c = self.size / 2.0
        return np.array([c + self._scale * q[0], c - self._scale * q[1]])

    def visible_faces(self, rot) -> list[int]:
        """Caras que miran a la cámara (en la vista, +z apunta hacia el observador)."""
        r = np.asarray(rot, float)
        return [k for k, (_, n) in enumerate(FACES) if (r @ np.asarray(n, float))[2] > 1e-6]

    def hit(self, rot, x: float, y: float) -> Hit | None:
        """Celda bajo el píxel (x, y), o None si el cursor está fuera del cubo."""
        r = np.asarray(rot, float)
        for k in self.visible_faces(r):
            n = np.asarray(FACES[k][1], float)
            u, v = face_basis(n)
            origin = self.project(r, n)
            su = self.project(r, n + u) - origin           # píxeles por unidad sobre u
            sv = self.project(r, n + v) - origin
            m = np.array([[su[0], sv[0]], [su[1], sv[1]]])
            if abs(np.linalg.det(m)) < 1e-9:               # cara casi de canto
                continue
            a, b = np.linalg.solve(m, np.array([x, y]) - origin)
            if abs(a) <= 1.0 and abs(b) <= 1.0:
                return Hit(k, _cell_index(a), _cell_index(b))
        return None

    # --------------------------------------------------------------- dibujo
    def draw(self, cr, rot, hover: Hit | None = None) -> None:
        """Dibuja el cubo con un contexto de cairo (fondo transparente)."""
        import cairo

        r = np.asarray(rot, float)
        faces = self.visible_faces(r)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for k in faces:
            n = np.asarray(FACES[k][1], float)
            u, v = face_basis(n)
            for i in (-1, 0, 1):
                for j in (-1, 0, 1):
                    cell_hover = hover is not None and (hover.face, hover.i, hover.j) == (k, i, j)
                    self._cell_path(cr, r, n, u, v, i, j)
                    if cell_hover:
                        cr.set_source_rgba(0.0, 0.68, 0.26, 0.95)          # verde de Bambu Studio
                    elif i == 0 and j == 0:
                        cr.set_source_rgba(0.90, 0.91, 0.93, 0.96)
                    else:
                        cr.set_source_rgba(0.70, 0.72, 0.76, 0.96)
                    cr.fill_preserve()
                    cr.set_source_rgba(0.25, 0.27, 0.30, 0.9)
                    cr.set_line_width(0.8)
                    cr.stroke()
        for k in faces:
            self._label(cr, r, k)
        self._axes(cr, r)

    def _cell_path(self, cr, rot, n, u, v, i, j) -> None:
        (a0, a1), (b0, b1) = CELL_EDGES[i], CELL_EDGES[j]
        pts = [self.project(rot, n + a * u + b * v) for a, b in ((a0, b0), (a1, b0), (a1, b1), (a0, b1))]
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()

    def _label(self, cr, rot, k: int) -> None:
        import cairo

        name, n = FACES[k]
        n = np.asarray(n, float)
        u, v = face_basis(n)
        o = self.project(rot, n)
        su = self.project(rot, n + u) - o
        sv = self.project(rot, n + v) - o
        cr.save()
        # el texto se dibuja en coordenadas de la cara: x sigue a u, y (hacia abajo) sigue a -v; det > 0 → sin espejo
        cr.transform(cairo.Matrix(su[0], su[1], -sv[0], -sv[1], o[0], o[1]))
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(0.30)
        ext = cr.text_extents(name)
        cr.move_to(-(ext.width / 2 + ext.x_bearing), -(ext.height / 2 + ext.y_bearing))
        cr.set_source_rgba(0.12, 0.13, 0.15, 1.0)
        cr.show_text(name)
        cr.restore()

    def _axes(self, cr, rot) -> None:
        """Mini ejes X / Y / Z en la esquina inferior izquierda, que giran con la cámara."""
        cr.save()
        cr.select_font_face("Sans")
        cr.set_font_size(9.0)
        cr.set_line_width(1.6)
        o = np.array([10.0, self.size - 10.0])
        for axis, color, label in ((0, (0.90, 0.25, 0.25), "X"), (1, (0.30, 0.78, 0.30), "Y"), (2, (0.30, 0.50, 0.95), "Z")):
            e = np.zeros(3)
            e[axis] = 1.0
            q = np.asarray(rot, float) @ e
            tip = o + 18.0 * np.array([q[0], -q[1]])
            cr.set_source_rgba(*color, 1.0)
            cr.move_to(*o)
            cr.line_to(*tip)
            cr.stroke()
            cr.move_to(tip[0] + 2.0, tip[1] + 3.0)
            cr.show_text(label)
        cr.restore()


def _cell_index(t: float) -> int:
    return -1 if t < -1.0 / 3 else (1 if t > 1.0 / 3 else 0)
