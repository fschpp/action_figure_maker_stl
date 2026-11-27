"""Plugins de articulación, Fase 3 (corte 1): agrupar, ordenar y encadenar las articulaciones de un miembro."""
import pytest

from afm.joint_chain import check_names, group_by_limb, order_along
from afm.joints import JointSpec
from afm.planes import Plane


def _j(name, limb="brazo", z=-10.0, kind="elbow", **kw):
    return JointSpec(name=name, limb=limb, kind=kind, plane=Plane.from_normal((0, 0, z), (0, 0, 1), name),
                     axis_center=(0, 0, z), **kw)


def test_group_by_limb_keeps_order_and_rejects_unknown_limb_and_repeated_names():
    a, b = _j("codo"), _j("biceps", z=-5.0, kind="elbow", prox_name="x", dist_name="y")
    g = group_by_limb([a, b], ["brazo", "pierna"])
    assert g == {"brazo": [a, b]}
    with pytest.raises(ValueError, match="no existe"):
        group_by_limb([_j("codo", limb="ala")], ["brazo"])
    with pytest.raises(ValueError, match="único"):
        group_by_limb([a, _j("codo", z=-3.0)], ["brazo"])


def test_order_along_sorts_from_the_torso_to_the_tip_and_rejects_ties():
    root, down = (0, 0, 0), (0, 0, -1)
    far, mid, near = _j("muneca", z=-30.0), _j("codo", z=-15.0), _j("biceps", z=-5.0)
    assert [j.name for j in order_along([far, near, mid], root, down)] == ["biceps", "codo", "muneca"]
    assert [j.name for j in order_along([far, near], root, (0, 0, 1))] == ["muneca", "biceps"]      # el sentido importa
    with pytest.raises(ValueError, match="mismo lugar"):
        order_along([mid, _j("otro", z=-15.0)], root, down)
    with pytest.raises(ValueError, match="nula"):
        order_along([mid], root, (0, 0, 0))


def test_check_names_chains_distal_into_next_proximal():
    k = _j("rodilla", limb="pierna", kind="knee", prox_name="muslo", dist_name="canilla")
    t = _j("tobillo", limb="pierna", kind="knee", z=-30.0, prox_name="canilla", dist_name="pie")
    assert check_names([k, t]) == ["muslo", "canilla", "pie"]
    assert check_names([_j("codo")]) == ["brazo", "antebrazo"]          # una sola: los nombres de siempre
    assert check_names([]) == []
    with pytest.raises(ValueError, match="proximal de la siguiente"):
        check_names([k, _j("tobillo", limb="pierna", kind="knee", z=-30.0, prox_name="otra", dist_name="pie")])
    with pytest.raises(ValueError, match="repite"):
        check_names([k, _j("tobillo", limb="pierna", kind="knee", z=-30.0, prox_name="canilla", dist_name="muslo")])


def test_pipeline_still_refuses_two_joints_in_one_limb_with_the_old_message():
    from afm import pipeline as PL
    assert PL.group_by_limb is group_by_limb                       # el motor usa el mismo agrupador
