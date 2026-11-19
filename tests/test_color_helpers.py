"""T06 · Muñeco texturizado sintético."""
import time

import numpy as np

from color_helpers import REGIONS, doll_textured, region_of


def test_doll_regions_have_expected_colors():
    tm = doll_textured()
    cen = tm.vertices[tm.faces].mean(axis=1)
    bary = np.full((len(tm.faces), 3), 1 / 3)
    rgb = tm.sample(np.arange(len(tm.faces)), bary)
    for i in range(0, len(tm.faces), 7):
        assert tuple(rgb[i]) == REGIONS[region_of(cen[i])]
    assert {region_of(c) for c in cen} >= {"brazo_izq", "brazo_der", "pierna_izq", "pierna_der", "cabeza", "pelvis", "torso"}


def test_left_and_right_arm_differ():
    assert REGIONS["brazo_izq"] != REGIONS["brazo_der"] and REGIONS["pierna_izq"] != REGIONS["pierna_der"]


def test_all_faces_have_uv_inside_atlas():
    tm = doll_textured()
    assert tm.corner_uv.min() >= 0 and tm.corner_uv.max() <= 1 and tm.image().shape == (256, 256, 3)


def test_doll_textured_is_deterministic():
    t = time.time()
    a, b = doll_textured(), doll_textured()
    assert np.array_equal(a.corner_uv, b.corner_uv) and np.array_equal(a.vertices, b.vertices) and a.images == b.images
    assert time.time() - t < 3
