"""E04: RiderFrame y matriz rígida."""
import itertools
import math
import os

import numpy as np
import pytest

from afm import recipe as R
from afm import solids as S
from afm.equestrian import RiderError, place_rider, rider_profile, seat_anchor, seat_frame

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture(scope="module")
def doll():
    return R.assemble(R.Recipe.load(os.path.join(ROOT, "recetas", "muneco.receta.json")), ROOT, sit=True)


@pytest.fixture(scope="module")
def rp(doll):
    return rider_profile(doll)


def test_anchor_origin_lands_on_the_seat(rp):
    f = seat_frame(rp, (3.0, -2.0, 50.0), (0, 1, 0))
    assert f.apply_points(f.anchor)[0] == pytest.approx([3.0, -2.0, 50.0])
    assert f.anchor[:2] == pytest.approx(((rp.hip_left.center + rp.hip_right.center) / 2)[:2])


def test_matrix_is_a_proper_rotation(rp):
    for d, pitch in (((0, 1, 0), 0), ((1, 1, 5), 0), ((0, -1, 0), 20), ((-3, 2, 0), -35)):
        f = seat_frame(rp, (1, 2, 3), d, pitch)
        R_ = f.matrix[:3, :3]
        assert np.allclose(R_.T @ R_, np.eye(3), atol=1e-9) and np.linalg.det(R_) == pytest.approx(1.0)
        assert np.allclose(f.matrix[3], [0, 0, 0, 1])


def test_facing_ends_up_along_direction_and_z_stays_up(rp):
    for d in ((0, 1, 0), (0, -1, 0), (1, 0, 0), (1, 1, 0)):
        f = seat_frame(rp, (0, 0, 0), d)
        dn = np.array(d, float) / np.linalg.norm(d)
        assert f.apply_vectors(rp.facing)[0] == pytest.approx(dn, abs=1e-9)
        assert f.apply_vectors([0, 0, 1])[0] == pytest.approx([0, 0, 1], abs=1e-9)


def test_yaw_180_flips_the_rider(rp):
    f = seat_frame(rp, (0, 0, 0), (0, -1, 0))                      # la figura mira a +Y: hay que girarla 180°
    assert f.matrix[:3, :3] == pytest.approx(np.diag([-1, -1, 1.0]), abs=1e-4)      # el frente medido no es +Y exacto


def test_pitch_tilts_forward(rp):
    f = seat_frame(rp, (0, 0, 0), (0, 1, 0), pitch_deg=10)
    up = f.apply_vectors([0, 0, 1])[0]
    assert up[1] > 0.1 and up[2] == pytest.approx(math.cos(math.radians(10)))      # la cabeza va hacia adelante (+Y)
    assert f.apply_points(f.anchor)[0] == pytest.approx([0, 0, 0], abs=1e-9)       # el ancla no se mueve


def test_distances_volumes_and_left_right_preserved(doll, rp):
    f = seat_frame(rp, (4.0, 7.0, 90.0), (1, 2, 0), pitch_deg=8)
    moved = place_rider(doll, f)
    assert set(moved) == set(doll.res.pieces)
    for k, v in doll.res.pieces.items():
        assert float(moved[k].volume()) == pytest.approx(float(v.volume()), rel=1e-6)
    # distancias entre centros de masa de cada par de piezas: invariantes bajo una rotación + traslación
    cm = lambda pcs: {k: np.asarray(S.to_trimesh(v).center_mass) for k, v in pcs.items()}
    c0, c1 = cm(doll.res.pieces), cm(moved)
    for p, q in itertools.combinations(sorted(c0), 2):
        assert np.linalg.norm(c1[p] - c1[q]) == pytest.approx(np.linalg.norm(c0[p] - c0[q]), abs=1e-3)
    # izquierda/derecha conservadas: el producto mixto (lateral × adelante · arriba) no cambia de signo
    lat0, lat1 = c0["brazo_izq"] - c0["brazo_der"], c1["brazo_izq"] - c1["brazo_der"]
    fw = f.apply_vectors(rp.facing)[0]
    assert np.dot(np.cross(lat1, fw), [0, 0, 1]) * np.dot(np.cross(lat0, rp.facing), [0, 0, 1]) > 0


def test_move_and_undo_returns_the_original(doll, rp):
    f = seat_frame(rp, (10, -5, 33), (-1, 0.3, 0), pitch_deg=-12)
    inv = f.inverse()
    assert np.allclose(inv @ f.matrix, np.eye(4), atol=1e-9)
    for k in ("pierna_izq", "torso", "cabeza"):
        v0 = doll.res.pieces[k]
        v1 = f.apply(v0).transform(inv[:3, :])
        assert np.asarray(v1.bounding_box()) == pytest.approx(np.asarray(v0.bounding_box()), abs=1e-4)
        assert float(v1.volume()) == pytest.approx(float(v0.volume()), rel=1e-6)
        p0, p1 = np.asarray(S.to_trimesh(v0).vertices), np.asarray(S.to_trimesh(v1).vertices)
        assert len(p0) == len(p1) and np.abs(p0 - p1).max() < 1e-3


def test_original_pieces_are_untouched(doll, rp):
    before = {k: tuple(np.round(np.ravel(v.bounding_box()), 6)) for k, v in doll.res.pieces.items()}
    place_rider(doll, seat_frame(rp, (50, 50, 50), (0, 1, 0), 15))
    assert before == {k: tuple(np.round(np.ravel(v.bounding_box()), 6)) for k, v in doll.res.pieces.items()}


@pytest.mark.parametrize("kw", [{"seat_origin": (0, 0)}, {"seat_origin": (0, 0, float("nan"))}, {"seat_origin": (0, 0, 0), "direction": (0, 0, 1)},
                                {"seat_origin": (0, 0, 0), "direction": (0, 0, 0)}, {"seat_origin": (0, 0, 0), "pitch_deg": 90},
                                {"seat_origin": (0, 0, 0), "pitch_deg": True}])
def test_invalid_inputs(rp, kw):
    with pytest.raises(RiderError):
        seat_frame(rp, **kw)


def test_anchor_for_doll_uses_pelvis_base(rp):
    assert seat_anchor(rp)[2] == pytest.approx(np.asarray(rp.pelvis.bounding_box(), float).reshape(2, 3)[0][2])
