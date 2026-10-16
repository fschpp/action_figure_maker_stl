"""Cabeza articulada: corte por el plano del cuello + pasador tipo hongo (como los hombros)."""
import dataclasses

import numpy as np
import pytest

from afm import pipeline as P, solids as S
from afm.head import HEAD_NAME, HeadSpec, neck_plane, split_head
from afm.params import Params, PinSpec
from afm.planes import Plane
from afm.project import Project, demo_project
from afm.testfigure import make_doll
from afm.trims import Trim

NECK_Z = 80.0


def _neck(z=NECK_Z, **kw):
    return HeadSpec(Plane.from_points((-5, -5, z), (5, -5, z), (0, 5, z), "cabeza cuerpo"), **kw)


@pytest.fixture(scope="module")
def body():
    return S.to_manifold(make_doll())


@pytest.fixture(scope="module")
def params():
    return dataclasses.replace(demo_project().params, decimate=0)      # sin decimar: más rápido


@pytest.fixture(scope="module")
def built(body, params):
    res = P.build(body, [], params, head=_neck())
    return res, P.validate(res, params, [])


def test_head_is_a_single_watertight_piece(built):
    res, val = built
    assert HEAD_NAME in res.pieces and "torso" in res.pieces
    assert val["ok"], val["problems"]
    info = val["pieces"][HEAD_NAME]
    assert info["componentes"] == 1 and info["variedad_ok"] and info["aristas_no_variedad"] == 0
    assert val["pieces"]["torso"]["componentes"] == 1


def test_head_clearance_and_free_turn(built, params):
    res, val = built
    assert 0.45 <= val["gaps"][HEAD_NAME] <= 0.6                 # holgura ≈ 0,5 mm, como los hombros
    assert val["recorrido"][HEAD_NAME] < 1e-4                    # gira sobre el cuello sin tocar el cuerpo
    head, torso = res.pieces[HEAD_NAME], res.pieces["torso"]
    center, axis, _ = res.motion[HEAD_NAME][1:]
    for ang in (37.0, 180.0):
        assert (S.rotate_about_axis(head, center, axis, ang) ^ torso).volume() < 1e-4


def test_pin_is_captive(built):
    """La cabeza del hongo queda retenida: subir la cabeza 1 mm choca con el labio del cuerpo."""
    res, _ = built
    head, torso = res.pieces[HEAD_NAME], res.pieces["torso"]
    assert (head.translate((0, 0, 1.0)) ^ torso).volume() > 1e-6
    assert (head ^ torso).volume() < 1e-6


def test_pin_on_neck_axis_and_inside_face(built, params):
    res, val = built
    rep = res.report["head"]
    assert abs(rep["pin_center"][0]) < 1e-6 and abs(rep["pin_center"][2] - NECK_Z) < 1e-6
    assert rep["pin_margin_mm"] > params.min_wall                # el vástago cae bien dentro de la cara
    assert not val["avisos_pasador"] or all("cabeza" not in a for a in val["avisos_pasador"])


def test_neck_plane_is_symmetrized_and_checked():
    tilted = HeadSpec(Plane.from_points((-5, -5, 78), (5, -5, 78), (0, 5, 82), "inclinado"))
    n = neck_plane(tilted).normal
    assert abs(n[0]) < 1e-9 and n[2] > 0
    down = HeadSpec(Plane.from_normal((3, 0, 80), (0, 0, -1)))
    assert neck_plane(down).normal[2] > 0 and neck_plane(down).origin[0] == 0   # hacia arriba y sobre x = 0
    vertical = HeadSpec(Plane.from_points((0, 0, 70), (0, 10, 70), (0, 0, 80)))
    with pytest.raises(ValueError, match="vertical|costado"):
        neck_plane(vertical)


def test_plane_outside_figure_is_rejected(body, params):
    with pytest.raises(ValueError, match="no corta ninguna cabeza"):
        split_head(body, _neck(z=130.0), params)
    with pytest.raises(ValueError):
        split_head(body, _neck(z=-20.0), params)


def test_manual_pin_center_is_projected_onto_the_cut(body, params):
    from afm.head import build_head
    spec = _neck(pin_center=(1.5, 0.5, 90.0))
    hb = build_head(split_head(body, spec, params), spec, params)
    assert np.allclose(hb.face.center, [0.0, 0.5, NECK_Z], atol=1e-6)


def test_head_trim_is_mirrored(body, params):
    """Un recorte sobre «cabeza» (dibujado en el lado de trabajo) se espeja: el volumen baja y sigue simétrica."""
    from afm.head import build_head
    spec = _neck()
    sp = split_head(body, spec, params)
    base = build_head(sp, spec, params).head
    cut = Trim("cabeza", Plane.from_normal((0, 0, 0), (1, 0, 0)), np.array([[8, 5, 88], [14, 12, 96]], float),
               shape="box", op="subtract")                       # caja en el lado +X de la cabeza
    out = build_head(sp, spec, params, trims=[cut]).head
    assert out.volume() < base.volume() - 1.0
    mirrored = S.mirror_x(out)
    assert abs((out ^ mirrored).volume() - out.volume()) < 0.01 * out.volume()


def test_without_head_nothing_changes(params):
    demo = demo_project()
    res = P.build(S.to_manifold(make_doll()), demo.limbs[:1], params)
    assert HEAD_NAME not in res.pieces and "head" not in res.report


def test_project_roundtrip_keeps_head(tmp_path):
    p = demo_project()
    assert p.head is not None
    p.params.neck_pin = PinSpec(3.3, 2.1, 5.3, 1.3)
    f = tmp_path / "x.proyecto"
    p.save(str(f))
    q = Project.load(str(f))
    assert q.head is not None and np.allclose(q.head.plane.normal, p.head.plane.normal)
    assert q.params.neck_pin == p.params.neck_pin
    old = p.to_dict()
    old.pop("head")                                              # proyectos guardados antes de la cabeza
    assert Project.from_dict(old).head is None
    assert Params.from_dict({k: v for k, v in Params().to_dict().items() if k != "neck_pin"}).neck_pin.shaft_d == 4.1
