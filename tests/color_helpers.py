"""Ayudas de las pruebas del color (plan T06 y T14). Parte 1: muñeco texturizado; parte 2: piezas del muñeco."""
from __future__ import annotations

import numpy as np

from afm.testfigure import make_doll
from afm.texmesh import TexturedMesh
from fbx_synth import png_bytes

REGIONS = {
    "brazo_izq": (220, 30, 30),
    "brazo_der": (30, 30, 220),
    "pierna_izq": (30, 200, 60),
    "pierna_der": (160, 210, 40),
    "cabeza": (240, 220, 30),
    "pelvis": (240, 130, 20),
    "torso": (245, 245, 245),
    "resto": (128, 128, 128),
}
_ORDER = list(REGIONS)


def region_of(c: np.ndarray) -> str:
    """Región según el centroide `c` (x, y, z), en el marco del muñeco."""
    x, _y, z = c
    if x > 15:
        return "brazo_izq"
    if x < -15:
        return "brazo_der"
    if 2 < x < 16 and z < 38:
        return "pierna_izq"
    if -16 < x < -2 and z < 38:
        return "pierna_der"
    if z > 78:
        return "cabeza"
    if 34 <= z < 42:
        return "pelvis"
    if 42 <= z <= 75:
        return "torso"
    return "resto"


def doll_textured() -> TexturedMesh:
    """El muñeco con un color por región: atlas 256×256 con un bloque de 8×8 px por color; los UV de cada cara son constantes (centro del bloque)."""
    mesh = make_doll()
    V, F = np.asarray(mesh.vertices, float), np.asarray(mesh.faces, np.int32)
    cen = V[F].mean(axis=1)
    names = [region_of(c) for c in cen]
    img = np.zeros((256, 256, 3), np.uint8)
    uv_of = {}
    for i, n in enumerate(_ORDER):
        r, c = divmod(i, 32)
        img[r * 8:(r + 1) * 8, c * 8:(c + 1) * 8] = REGIONS[n]
        uv_of[n] = ((c * 8 + 4) / 256.0, 1.0 - (r * 8 + 4) / 256.0)
    uv = np.array([uv_of[n] for n in names], np.float32)
    corner_uv = np.repeat(uv[:, None, :], 3, axis=1)
    return TexturedMesh(V, F, corner_uv, {"base_color": png_bytes(img)}, {"avisos": [], "scaling": [1, 1, 1], "n_materiales": 1})
