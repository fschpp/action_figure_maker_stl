"""Aviso de «¿por qué no puedo poner el pasador ahí?»: la 2.ª silueta recorta el hombro más allá del plano de corte."""
import numpy as np
import trimesh

from afm import limbs as L
from afm.gui.session import Session
from afm.planes import Plane


def _session(inner_x: float):
    s = Session()
    s.project.symmetry = Plane.from_normal((0.0, 0, 0), (1, 0, 0))
    s.work = trimesh.creation.box(extents=(40, 20, 100), transform=trimesh.transformations.translation_matrix((0, 0, 50)))
    spec = s.add_limb("brazo", "arm", Plane.from_normal((0.0, 0, 60), (1, 0, 0)))
    spec.polygon = np.array([[0, -9, 40], [0, 9, 40], [0, 9, 95], [0, -9, 95]], float)
    spec.cut_offset = 10.0
    spec.burr = 0.0
    spec.cross = L.LimbCut(Plane.from_normal((0, 0, 0), (0, 1, 0)),
                            np.array([[inner_x, 0, 38], [30, 0, 38], [30, 0, 97], [inner_x, 0, 97]], float), 254.0)
    return s


def test_hint_names_the_second_silhouette_when_its_edge_is_past_the_cut_plane():
    s = _session(inner_x=14.0)
    uv = s.pin_preview("brazo").project(np.array([10.0, 0.0, 90.0]))
    hint = s.pin_miss_hint("brazo", uv)
    assert "2.ª silueta" in hint and "14.0" in hint and "z=90" in hint


def test_no_hint_when_the_second_silhouette_reaches_the_cut_plane():
    s = _session(inner_x=8.0)
    uv = s.pin_preview("brazo").project(np.array([10.0, 0.0, 90.0]))
    assert "2.ª silueta" not in s.pin_miss_hint("brazo", uv)
