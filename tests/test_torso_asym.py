"""Torso sin espejar: el lado −X conserva el diseño asimétrico del STL; los miembros siguen espejados."""
import dataclasses

import numpy as np
import pytest

from afm import pipeline as P, solids as S
from afm.project import demo_project
from afm.testfigure import make_doll

BUMP = ((-10.0, 7.0, 56.0), (-4.0, 12.0, 63.0))      # una «condecoración» solo en el pecho −X (sobresale de la piel, lejos de los brazos)


def _bumped() -> S.Manifold:
    bump = S.Manifold.cube(tuple(np.subtract(BUMP[1], BUMP[0])), center=False).translate(BUMP[0])
    return S.union(S.to_manifold(make_doll()), bump)


@pytest.fixture(scope="module")
def project():
    pr = demo_project()
    pr.params = dataclasses.replace(pr.params, decimate=0)
    return pr


def _build(body, project, **kw):
    p = dataclasses.replace(project.params, **kw.pop("params", {}))
    kw.setdefault("head", project.head)
    return P.build(body, project.limbs, p, project.symmetry, trims=project.trims, **kw)


def _sym(a: S.Manifold, b: S.Manifold) -> float:
    """Volumen de la diferencia simétrica (en manifold3d `^` es la intersección)."""
    return float(S.difference(a, b).volume() + S.difference(b, a).volume())


def _in_bump(torso: S.Manifold) -> float:
    box = S.Manifold.cube(tuple(np.subtract(BUMP[1], BUMP[0])), center=False).translate(BUMP[0])
    return float(S.intersection(torso, box).volume())


def test_mirrored_torso_is_symmetric_by_default(project):
    res = _build(_bumped(), project)
    assert res.report.get("torso") is None
    torso = res.pieces["torso"]
    assert _sym(torso, S.mirror_x(torso)) < 1e-3                               # simétrico: la condecoración −X se pierde


def test_unmirrored_torso_keeps_the_asymmetric_design(project):
    body = _bumped()
    res = _build(body, project, params={"torso_mirror": False})
    torso = res.pieces["torso"]
    assert res.report["torso"]["espejado"] is False
    assert _in_bump(torso) > 20.0                                              # la condecoración −X sigue ahí
    assert _sym(torso, S.mirror_x(torso)) > 20.0                               # y el torso ya no es simétrico
    # el lado +X es el mismo que con espejo: solo cambia −X
    mir = _build(body, project).pieces["torso"]
    right = lambda m: m.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    assert _sym(right(torso), right(mir)) < 1e-3
    # los miembros siguen siendo espejo exacto
    for nm in ("brazo", "pierna"):
        a, b = res.pieces[f"{nm}_izq"], res.pieces[f"{nm}_der"]
        assert _sym(a, S.mirror_x(b)) < 1e-6


def test_unmirrored_torso_is_valid_and_limbs_still_move_freely(project):
    res = _build(_bumped(), project, params={"torso_mirror": False})
    val = P.validate(res, dataclasses.replace(project.params, torso_mirror=False), project.limbs)
    assert val["ok"], val["problems"]
    assert val["pieces"]["torso"]["componentes"] == 1


def test_unmirrored_torso_without_head_split(project):
    res = _build(_bumped(), project, params={"torso_mirror": False}, head=None)
    assert "cabeza" not in res.pieces
    assert _in_bump(res.pieces["torso"]) > 20.0


def test_symmetric_figure_gives_same_torso_either_way(project):
    body = S.to_manifold(make_doll())
    a = _build(body, project).pieces["torso"]
    b = _build(body, project, params={"torso_mirror": False}).pieces["torso"]
    assert _sym(a, b) < 1.0


def test_unmirrored_torso_with_click_mechanism(project):
    """Con clic, las lengüetas se espejan hacia −X sobre el torso asimétrico y la protrusión se mide por lado."""
    res = _build(_bumped(), project, params={"torso_mirror": False, "shoulder_detent": True})
    assert "brazo" in res.detent and res.report["torso"]["espejado"] is False
    assert res.report["detent"]["brazo"]["protrusion_mm3"] >= 0.0
    assert res.pieces["torso"].num_tri() > 0 and _in_bump(res.pieces["torso"]) > 20.0
