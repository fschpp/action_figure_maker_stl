"""Agarre de accesorios: canal de la mano, taladro, empuñadura dentada y placa de pruebas (`afm.props`)."""
from dataclasses import replace

import numpy as np
import pytest
import trimesh

from afm import props as P
from afm import solids as S


def _ring(major=4.4, minor=3.2, tilt=(0.3, 0.2, 0.93)):
    """Toro con un agujero pasante (radio major−minor = 1,2 mm → Ø2,4) y eje inclinado, como una mano de juguete."""
    t = trimesh.creation.torus(major_radius=major, minor_radius=minor, major_sections=48, minor_sections=24)
    d = np.array(tilt, float) / np.linalg.norm(tilt)
    z = np.array([0, 0, 1.0])
    v = np.cross(z, d); s = np.linalg.norm(v); c = z @ d
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    R = np.eye(3) + K + K @ K * ((1 - c) / s ** 2)
    T = np.eye(4); T[:3, :3] = R; T[:3, 3] = (1.0, -0.5, 5.0)
    t.apply_transform(T)
    return S.to_manifold(t), d


def _ch(hand, bore=2.4):
    """Canal con parámetros gruesos: el toro de prueba es mucho más grande que una mano y la búsqueda fina tardaría minutos."""
    return P.find_channel(hand, bore_d=bore, pitch=0.2, n_dirs=150, refine=False)


def test_strain_formula_matches_defaults():
    sp = P.GripSpec()
    assert grip_ok(sp)
    # t = (2,0 − 0,5)/2 = 0,75 ; δ = (2,9 − 2,4)/2 = 0,25 ; L = 8  →  ε = 1,5·0,75·0,25/64
    assert P.grip_strain(sp) == pytest.approx(1.5 * 0.75 * 0.25 / 64)


def grip_ok(sp):
    return P.grip_strain(sp) < sp.max_strain


def test_find_channel_axis_and_size():
    hand, d = _ring()
    ch = _ch(hand)
    assert abs(float(ch.axis @ d)) > 0.97                       # eje del toro
    assert 1.5 <= ch.free_d <= 2.8                            # Ø2,4 real; la búsqueda gruesa subestima
    assert np.linalg.norm((ch.center - np.array([1.0, -0.5, 5.0])) - d * ((ch.center - np.array([1.0, -0.5, 5.0])) @ d)) < 0.5


def test_carve_keeps_one_solid_and_ring():
    hand, _ = _ring(minor=3.4)                                    # agujero libre Ø2,0: hay que ensancharlo a 2,4
    out, fit = P.carve_grip(hand, P.GripSpec(bore_d=2.4), _ch(hand))
    assert len(out.decompose()) == 1 and out.volume() < hand.volume()
    assert fit.wall_min >= 0.8 and fit.length > 5.0 and fit.bore_d == 2.4


def test_carve_refuses_a_bore_that_opens_the_ring():
    hand, _ = _ring()
    with pytest.raises(P.GripError):
        P.carve_grip(hand, P.GripSpec(bore_d=14.0, min_wall=0.8), _ch(hand))


def test_prop_fits_through_the_bore():
    hand, _ = _ring()
    sp = P.GripSpec(finger_len=6.0)                              # el toro de prueba es más corto que una mano
    out, fit = P.carve_grip(hand, sp, _ch(hand))
    prop = P.place_in_hand(P.make_prop(sp, fit.length), fit)
    # solo el eje liso (hasta antes de la rampa del dentado) no debe chocar con la mano
    xs = fit.length + sp.play
    shaft_box = S.Manifold.cube((xs - sp.entry_len, 6, 6)).translate((0, -3, -3))
    shaft_only = S.intersection(P.make_prop(sp, fit.length), shaft_box)
    placed = P.place_in_hand(shaft_only, fit)
    assert S.intersection(out, placed).volume() < 0.01


def test_prop_rejects_bad_specs():
    sp = P.GripSpec()
    with pytest.raises(P.GripError):
        P.make_prop(replace(sp, barb_d=2.4), 9.0)                  # no supera el taladro
    with pytest.raises(P.GripError):
        P.make_prop(replace(sp, barb_d=3.9), 9.0)                  # lengüeta pasada de deformación
    with pytest.raises(P.GripError):
        P.make_prop(replace(sp, finger_len=30.0), 9.0)             # lengüeta más larga que la empuñadura


def test_probe_plate_has_one_prop_per_barb():
    sp = P.GripSpec()
    plate = P.probe_plate(sp, 9.5, barbs=(2.7, 2.9, 3.1))
    assert len(plate.decompose()) == 3
    tm = S.to_trimesh(plate)
    assert tm.bounds[0][2] == pytest.approx(0.0, abs=1e-3)         # acostadas sobre la cama


def test_prop_has_a_flat_common_bottom():
    """Con el dentado más ancho que el eje, nada debe sobresalir por debajo del plano de apoyo."""
    sp = P.GripSpec(barb_d=3.1)
    tm = S.to_trimesh(P.make_prop(sp, 9.5))
    assert tm.bounds[0][2] == pytest.approx(-sp.shaft_d / 2, abs=1e-3)
