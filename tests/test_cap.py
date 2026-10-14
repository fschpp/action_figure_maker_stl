"""Extremo redondeado de miembro (cap) y rebaba por miembro."""
import numpy as np

from afm import solids as S
from afm.limbs import ContactFace, LimbSpec, round_end
from afm.planes import Plane


def _spec(**kw):
    p = Plane.from_normal((0, 0, 0), (0, 1, 0))
    return LimbSpec("pierna", "leg", p, np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1]], float), 10.0, **kw)


def test_round_end_cuts_only_beyond_center():
    box = S.Manifold.cube((10, 10, 40), center=True)            # x,y in [-5,5], z in [-20,20]
    face = ContactFace(np.zeros(3), np.array([-1.0, 0, 0]), 1.0)
    spec = _spec(cap_radius=3.0, cap_center=(0, 0, 0), cap_dir=(0, 0, -1))
    out = round_end(box, face, spec)
    # por debajo del centro (lado de la punta) no cambia; por encima queda un cilindro R=3 de eje X
    assert abs(out.volume() - (10 * 10 * 20 + (np.pi * 9 * 10) / 2 * 0 + _cyl_upper(3.0, 10, 20))) < 25
    bb = out.bounding_box()
    assert bb[5] <= 3.0 + 1e-6 and bb[2] >= -20 - 1e-6        # z máx = radio; punta intacta


def _cyl_upper(r, length, height):
    # semicilindro superior (z >= 0) de eje X recortado a la caja de y en [-5,5]
    return np.pi * r * r / 2 * length


def test_limbspec_roundtrip_with_cap_and_burr():
    s = _spec(burr=0.0, cap_radius=7.6, cap_center=(1, 2, 3))
    d = LimbSpec.from_dict(s.to_dict())
    assert d.burr == 0.0 and d.cap_radius == 7.6 and d.cap_center == (1, 2, 3) and d.cap_dir == (0, 0, -1)
