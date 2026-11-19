"""T16 · `tools/render_color_compare.py`: el rasterizador por software (sin GL)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import render_color_compare as R  # noqa: E402

from color_helpers import doll_textured  # noqa: E402


def _tex(rgb):
    return np.full((4, 4, 3), rgb, np.uint8)


def _tri(x0, z0, y, size=10.0):
    return np.array([[[x0, y, z0], [x0 + size, y, z0], [x0, y, z0 + size]]], float)


UV = np.array([[[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]]], float)


def test_nearer_triangle_wins_the_zbuffer():
    far, near = _tri(-5, 0, -3.0), _tri(-5, 0, 3.0)
    tris = np.concatenate([far, near])
    uv = np.concatenate([UV, UV])
    img = R.render(tris, uv, _tex((200, 0, 0)), "frente", size=(80, 80), px_per_mm=4)      # el rojo cercano tapa al lejano
    img2 = R.render(tris[::-1], uv, _tex((200, 0, 0)), "frente", size=(80, 80), px_per_mm=4)
    assert np.array_equal(img, img2)                                                       # no depende del orden de dibujo
    assert (img[:, :, 0] > 100).any()
    # el triángulo lejano (verde) queda tapado por el cercano (rojo) en la vista de frente y se ve en la de espalda
    tex = np.zeros((4, 4, 3), np.uint8)
    tex[:, :2] = (200, 0, 0)
    tex[:, 2:] = (0, 200, 0)
    uv2 = np.concatenate([np.array([[[0.1, 0.5]] * 3]), np.array([[[0.9, 0.5]] * 3])])   # lejano = rojo; cercano = verde
    front = R.render(np.concatenate([far, near]), uv2, tex, "frente", size=(80, 80), px_per_mm=4).astype(int)
    back = R.render(np.concatenate([far, near]), uv2, tex, "espalda", size=(80, 80), px_per_mm=4).astype(int)
    assert (front[:, :, 1] > front[:, :, 0] + 60).any() and not (front[:, :, 0] > front[:, :, 1] + 60).any()
    assert (back[:, :, 0] > back[:, :, 1] + 60).any() and not (back[:, :, 1] > back[:, :, 0] + 60).any()


def test_doll_left_arm_is_red_and_right_is_blue_in_front_view():
    tm = doll_textured()
    tris = np.asarray(tm.vertices, float)[tm.faces]
    img = R.render(tris, np.asarray(tm.corner_uv, float), tm.image("base_color"), "frente", size=(300, 480), px_per_mm=4, z0=0.0)
    red = (img[:, :, 0] > 150) & (img[:, :, 1] < 90) & (img[:, :, 2] < 90)
    blue = (img[:, :, 2] > 150) & (img[:, :, 0] < 90) & (img[:, :, 1] < 90)
    assert red.sum() > 200 and blue.sum() > 200
    # frente (observador en +Y mirando a −Y): la derecha de pantalla es −X, así que el brazo izquierdo (x > 0, rojo) queda a la izquierda
    assert np.where(red)[1].mean() < np.where(blue)[1].mean() - 40


def test_background_untouched_where_nothing_is_drawn():
    img = R.render(_tri(-5, 0, 0), UV, _tex((10, 200, 10)), "lado", size=(60, 60), px_per_mm=3)
    assert tuple(img[0, 0]) == (238, 238, 238)
