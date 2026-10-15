"""Brazo: losa que sale del plano hacia +X (tutorial 008-009), sin importar el sentido de los 3 puntos."""
import numpy as np
import pytest

from afm import solids as S
from afm.gui.session import ARM_SLAB_MM, Session
from afm.limbs import LimbSpec, limb_prism
from afm.planes import Plane

SQUARE = np.array([[10, -5, 60], [10, 5, 60], [10, 5, 80], [10, -5, 80]], float)   # sobre x = 10


def _xrange(spec):
    b = S.to_trimesh(limb_prism(spec)).bounds
    return b[0, 0], b[1, 0]


@pytest.mark.parametrize("nx", [1.0, -1.0])
def test_outward_goes_to_plus_x_for_either_normal(nx):
    spec = LimbSpec("brazo", "arm", Plane.from_normal((10, 0, 70), (nx, 0, 0)), SQUARE, 12.0, extrude="outward")
    lo, hi = _xrange(spec)
    assert lo == pytest.approx(10.0, abs=1e-6) and hi == pytest.approx(22.0, abs=1e-6)


def test_symmetric_is_unchanged():
    spec = LimbSpec("pierna", "leg", Plane.from_normal((10, 0, 70), (1, 0, 0)), SQUARE, 20.0)
    lo, hi = _xrange(spec)
    assert (lo, hi) == (pytest.approx(0.0, abs=1e-6), pytest.approx(20.0, abs=1e-6))


def test_session_defaults_and_roundtrip():
    s = Session()
    arm = s.add_limb("brazo", "arm", Plane.from_normal((10, 0, 70), (-1, 0, 0)))
    leg = s.add_limb("pierna", "leg", Plane.from_normal((0, 0, 40), (0, 1, 0)))
    assert arm.extrude == "outward" and arm.thickness == pytest.approx(ARM_SLAB_MM)
    assert leg.extrude == "symmetric" and leg.thickness == 100.0
    assert LimbSpec.from_dict(arm.to_dict()).extrude == "outward"
