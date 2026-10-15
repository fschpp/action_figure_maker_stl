"""Regresión: piezas exportadas sin aristas no-variedad y rechazo de planos de miembro de canto."""
import numpy as np
import pytest
import trimesh

from afm import solids as S
from afm.limbs import check_limb_plane
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll
from afm import pipeline as P


def _edge_counts(tm):
    _, c = np.unique(tm.edges_sorted, axis=0, return_counts=True)
    return c


def test_printable_trimesh_drops_repeated_index_faces():
    # Cubo con un vértice duplicado en la misma posición y un triángulo colapsado sobre una arista:
    # al soldar, ese triángulo queda con un índice repetido (arista con 3 caras).
    box = trimesh.creation.box((10, 10, 10))
    mf = S.to_manifold(box)
    tm = S.to_printable_trimesh(mf)
    assert tm.is_watertight and (_edge_counts(tm) == 2).all()
    assert tm.volume == pytest.approx(1000.0, rel=1e-6)


def test_export_pieces_are_clean(tmp_path):
    proj = demo_project()
    body = S.to_manifold(make_doll())
    res = P.build(body, proj.limbs, proj.params, proj.symmetry)
    info = P.export(res, str(tmp_path), "t")
    for part in info["piezas"]:
        tm = trimesh.load(str(tmp_path / "piezas" / f"t_{part}.stl"), process=True)
        assert tm.is_watertight, part
        assert (_edge_counts(tm) == 2).all(), part
    val = P.validate(res, proj.params, proj.limbs)
    assert all(v["aristas_no_variedad"] == 0 for v in val["pieces"].values())


def test_side_plane_is_rejected_for_limbs():
    side = Plane.from_normal((9.66, 0, 78), (-1, 0, 0), "brazo")       # de canto: normal ≈ -X
    with pytest.raises(ValueError, match="de canto"):
        check_limb_plane(side)
    check_limb_plane(Plane.from_normal((0, 0, 0), (0, 1, 0), "frontal"))
    check_limb_plane(Plane.from_normal((0, 0, 0), (0.2, -0.95, 0.1), "casi frontal"))


def test_build_fails_fast_on_side_plane():
    proj = demo_project()
    proj.limbs[0].plane = Plane.from_normal((9.66, 0, 60), (-1, 0, 0), "brazo")
    with pytest.raises(ValueError, match="de canto"):
        P.build(S.to_manifold(make_doll()), proj.limbs, proj.params, proj.symmetry)
