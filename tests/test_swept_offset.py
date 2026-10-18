"""`solids.swept_offset`: barrido ensanchado por campo de distancia (el paso «sin soportes» no puede usar Minkowski
sobre brazos de ~200k caras)."""
import time

import numpy as np
import manifold3d as m3
import trimesh

from afm import solids as S


def _mf(mesh):
    return S.to_manifold(mesh)


def test_swept_offset_matches_reference_union_of_dilated_copies():
    box = trimesh.creation.box(extents=(4, 4, 12))
    box.apply_translation((0, 0, -8))             # cuelga bajo el eje de giro (x)
    mf = _mf(box)
    c, ax, theta, r = (0, 0, 0), (1, 0, 0), 60.0, 0.5
    sweep = S.swept_offset([mf], c, ax, theta, r, pitch=0.1, step_deg=2.0)
    ref = S.union(*[S.rotate_about_axis(S.dilate(mf, r), c, ax, a) for a in np.linspace(0, theta, 31)])
    assert sweep.status() == m3.Error.NoError and not sweep.is_empty()
    assert abs(sweep.volume() - ref.volume()) < 0.03 * ref.volume()
    # cubre al sólido en todas las poses
    for a in (0, 20, 45, 60):
        assert S.difference(S.rotate_about_axis(mf, c, ax, a), sweep).volume() < 0.01


def test_swept_offset_is_fast_with_hundreds_of_thousands_of_faces():
    ball = trimesh.creation.icosphere(subdivisions=7, radius=5.0)   # ~327k caras: Minkowski no terminaba
    assert len(ball.faces) > 200_000
    ball.apply_translation((0, 0, -9))
    t = time.time()
    sweep = S.swept_offset([_mf(ball)], (0, 0, 0), (1, 0, 0), 40.0, 0.45, pitch=0.25)
    assert time.time() - t < 60
    assert sweep.num_tri() < 100_000 and sweep.volume() > ball.volume
