"""Miniaturas de las piezas del catálogo (la fila de cada «lugar» muestra cómo es la pieza elegida).

Sin GL ni GTK: la malla se dibuja con Pillow (algoritmo del pintor, sombreado plano) y se devuelve una imagen RGBA.
La ventana la convierte en textura. Se guarda en memoria y en la carpeta de caché del usuario, con la fecha del STL
en el nombre, para que abrir el catálogo no vuelva a renderizar nada.
"""
from __future__ import annotations

import hashlib
import os
import threading

import numpy as np

SIZE = 64                  # lado de la miniatura en píxeles
MAX_FACES = 1500           # por encima se simplifica la malla antes de dibujar
_SS = 3                    # sobremuestreo para suavizar bordes
BG = (0, 0, 0, 0)
BASE = np.array([120.0, 165.0, 215.0])

_lock = threading.Lock()
_memory: dict[str, object] = {}


def _simplify(mesh):
    if len(mesh.faces) <= MAX_FACES:
        return mesh
    try:
        return mesh.simplify_quadric_decimation(face_count=MAX_FACES)
    except Exception:       # sin fast-simplification o malla rara: se dibuja entera
        return mesh


def render_thumbnail(mesh, size: int = SIZE):
    """Imagen RGBA `size`×`size` de `mesh`: se mira a lo largo del eje más corto de la caja, con el eje más largo hacia arriba."""
    from PIL import Image, ImageDraw

    verts = np.asarray(mesh.vertices, dtype=float)
    if len(verts) == 0:
        return Image.new("RGBA", (size, size), BG)
    mesh = _simplify(mesh)
    verts = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=int)
    ext = verts.max(axis=0) - verts.min(axis=0)
    order = np.argsort(ext)                       # [corto, medio, largo]
    depth_axis, side_axis, up_axis = int(order[0]), int(order[1]), int(order[2])
    center = (verts.max(axis=0) + verts.min(axis=0)) / 2.0
    p = verts - center
    # la cámara mira hacia -depth: lo más cercano tiene depth mayor; los ejes (side, up) se ven derecha-arriba
    x, y, z = p[:, side_axis], p[:, up_axis], p[:, depth_axis]
    if np.linalg.det(np.array([np.eye(3)[side_axis], np.eye(3)[up_axis], np.eye(3)[depth_axis]])) < 0:
        x = -x                                    # mantiene la mano derecha para no espejar la pieza
    span = max(float(x.max() - x.min()), float(y.max() - y.min()), 1e-9)
    hi = size * _SS
    scale = (hi * 0.88) / span
    px = hi / 2 + x * scale
    py = hi / 2 - y * scale
    tri = faces
    tz = z[tri].mean(axis=1)
    a, b, c = p[tri[:, 0]], p[tri[:, 1]], p[tri[:, 2]]
    n = np.cross(b - a, c - a)
    norm = np.linalg.norm(n, axis=1)
    norm[norm == 0] = 1.0
    n = n / norm[:, None]
    nz = np.abs(n[:, depth_axis])
    light = 0.35 + 0.65 * np.clip(nz * 0.8 + n[:, up_axis] * 0.2, 0.0, 1.0)
    img = Image.new("RGBA", (hi, hi), BG)
    draw = ImageDraw.Draw(img)
    for i in np.argsort(tz):                      # del fondo hacia el frente
        col = tuple(int(v) for v in np.clip(BASE * light[i], 0, 255)) + (255,)
        draw.polygon([(px[j], py[j]) for j in tri[i]], fill=col)
    return img.resize((size, size), Image.LANCZOS)


def _cache_dir(root: str) -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "afm", "miniaturas", hashlib.sha1(os.path.abspath(root).encode()).hexdigest()[:12])


def _key(root: str, ref: str, stl_path: str | None) -> str:
    stamp = ""
    if stl_path and os.path.isfile(stl_path):
        st = os.stat(stl_path)
        stamp = f"{int(st.st_mtime)}-{st.st_size}"
    return f"{os.path.abspath(root)}|{ref}|{stamp}"


def get_thumbnail(root: str, ref: str, loader, stl_path: str | None = None, size: int = SIZE):
    """Miniatura de `ref` (caché en memoria → disco → render). `loader()` devuelve la malla; None si no se pudo dibujar."""
    from PIL import Image

    key = _key(root, ref, stl_path)
    with _lock:
        if key in _memory:
            return _memory[key]
    path = os.path.join(_cache_dir(root), hashlib.sha1(key.encode()).hexdigest()[:16] + f"_{size}.png")
    img = None
    if os.path.isfile(path):
        try:
            img = Image.open(path).convert("RGBA")
        except OSError:
            img = None
    if img is None:
        try:
            img = render_thumbnail(loader(), size)
        except Exception:
            return None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            img.save(path)
        except OSError:
            pass                                  # sin caché en disco: igual se usa la de memoria
    with _lock:
        _memory[key] = img
    return img


def clear_memory() -> None:
    with _lock:
        _memory.clear()
