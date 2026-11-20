"""El color de la figura armada desde catálogo llega a TODAS las piezas exportadas.

Antes: solo `brazo` y `pierna` tenían fuente; el antebrazo, el muslo y la pantorrilla (piezas de una articulación) y el torso/pelvis
del huésped salían `sin_origen` (color plano) aunque se vieran bien en la vista previa.
"""
from types import SimpleNamespace

import numpy as np

from afm import cli
from afm.colorexport import Colorizer


def _res():
    joints = {"codo": {"limb": "brazo", "piezas": ["brazo", "antebrazo"]}, "rodilla": {"limb": "pierna", "piezas": ["muslo", "pantorrilla"]}}
    return SimpleNamespace(report={"joints": joints}, motion={"brazo": 1, "pierna": 1}, joints={"codo": object(), "rodilla": object()}, right=None)


def test_joint_pieces_use_the_source_of_their_limb():
    arm, leg, arm_r = (SimpleNamespace(query=None) for _ in range(3))      # `source_for` solo mira que tenga `query`
    cz = Colorizer(None, None, sources={"brazo": arm, "pierna": leg, "brazo_der": arm_r})
    res = _res()
    assert cz.source_for("antebrazo_izq", res) == (arm, "pieza")
    assert cz.source_for("antebrazo_der", res) == (arm_r, "pieza")      # el lado con fuente propia (reflejada) va antes
    assert cz.source_for("muslo_izq", res)[0] is leg
    assert cz.source_for("pantorrilla_der", res)[0] is leg
    assert cz.source_for("cabeza", res) == (None, "plano")


def test_host_bodies_are_added_to_placed():
    choice = SimpleNamespace(meta=SimpleNamespace(ref="torso@1"))
    asm = SimpleNamespace(res=SimpleNamespace(placed={"brazo_izq": 1}), host=SimpleNamespace(bodies={"torso": (choice, np.eye(4))}))
    placed = cli._placed_with_host_bodies(asm)
    assert set(placed) == {"brazo_izq", "torso"} and placed["torso"].choice is choice
    assert cli._placed_with_host_bodies(SimpleNamespace(res=SimpleNamespace(placed={"a": 1}), host=None)) == {"a": 1}
