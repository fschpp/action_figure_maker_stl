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


# --- Fase 3, corte 2: armar la cadena en pipeline.build ---------------------------------------------------------------

import dataclasses

import numpy as np

from afm import pipeline as P, solids as S
from afm.params import HingeSpec
from afm.project import demo_project
from afm.testfigure import make_doll


def _chain_joints():
    knee = JointSpec(name="rodilla", limb="pierna", kind="knee", plane=Plane.from_normal((0, 0, 14.0), (0, 0, 1), "art"),
                     axis_center=(9.0, 0.0, 17.4), prox_name="muslo", dist_name="pantorrilla")
    ankle = JointSpec(name="tobillo", limb="pierna", kind="knee", plane=Plane.from_normal((0, 0, 5.0), (0, 0, 1), "art"),
                      axis_center=(9.0, 0.0, 8.4), prox_name="pantorrilla", dist_name="pie", flex_range=(-20, 30),
                      print_flex=0.0, hinge=HingeSpec(pin_d=2.0, pin_len=0.5, tongue_t=2.0, end_r=2.5))
    return knee, ankle


@pytest.fixture(scope="module")
def chain_build():
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0)
    knee, ankle = _chain_joints()
    res = P.build(S.to_manifold(make_doll()), proj.limbs, p, proj.symmetry, joints=[ankle, knee])     # desordenadas a propósito
    return res, p, proj, [ankle, knee]


def test_two_joints_in_one_limb_build_three_pieces_from_the_torso_to_the_tip(chain_build):
    res, _p, _proj, _j = chain_build
    legs = {n for n in res.pieces if n.endswith("_izq") and n.split("_")[0] in ("muslo", "pantorrilla", "pie", "pierna")}
    assert legs == {"muslo_izq", "pantorrilla_izq", "pie_izq"}
    assert list(res.report["joints"]) == ["rodilla", "tobillo"]                       # del torso a la punta, no en el orden dado
    assert res.report["joints"]["tobillo"]["cadena"] == {"indice": 1, "previa": "rodilla"}
    assert "cadena" not in res.report["joints"]["rodilla"] or res.report["joints"]["rodilla"]["cadena"]["previa"] is None
    assert P._limb_piece_names(res, "pierna") == ["muslo", "pantorrilla", "pie"]
    assert all(res.pieces[n].volume() > 100 for n in ("muslo_izq", "pantorrilla_izq", "pie_izq"))


def test_chain_validates_clean_and_posed_pieces_compose_every_joint(chain_build):
    res, p, proj, joints = chain_build
    v = P.validate(res, p, proj.limbs, joints=joints)
    assert v["problems"] == [], v["problems"]
    straight = P.posed_pieces(res, 0.0, {"rodilla": 0.0, "tobillo": 0.0})
    bent = P.posed_pieces(res, 0.0, {"rodilla": 40.0, "tobillo": 20.0})
    assert {"muslo_izq", "pantorrilla_izq", "pie_izq"} <= set(straight)
    assert np.allclose(np.asarray(straight["muslo_izq"].bounding_box()), np.asarray(bent["muslo_izq"].bounding_box()))   # la proximal no se mueve
    c = lambda d, n: np.asarray(d[n].bounding_box(), float).reshape(2, 3).mean(axis=0)
    assert np.linalg.norm(c(straight, "pie_izq") - c(bent, "pie_izq")) > np.linalg.norm(c(straight, "pantorrilla_izq") - c(bent, "pantorrilla_izq"))


def test_print_flex_round_trips_and_is_omitted_when_unset():
    knee, ankle = _chain_joints()
    assert "print_flex" not in knee.to_dict() and JointSpec.from_dict(knee.to_dict()).print_flex is None
    assert JointSpec.from_dict(ankle.to_dict()).print_flex == 0.0


def test_flat_for_print_straightens_every_joint_of_the_chain(chain_build):
    res, p, _proj, _j = chain_build
    pieces, info = P.flat_for_print(res, 1.0, p)
    assert set(info["rodillas_estiradas"]) == {"rodilla", "tobillo"}
    assert {"muslo_izq", "pantorrilla_izq", "pie_izq", "pie_der"} <= set(pieces)
    # estirada: la pieza del pie queda en línea con la pantorrilla (misma altura de pose, sin la flexión de la rodilla)
    straight = P.posed_pieces(res, res.report["joints"]["rodilla"]["print_hip"], {"rodilla": 0.0, "tobillo": 0.0})
    for nm in ("pantorrilla_izq", "pie_izq"):
        assert pieces[nm].volume() > 0 and straight[nm].volume() > 0


def test_color_seeds_of_a_chained_piece_carry_every_previous_joint(chain_build):
    from afm.colorreg import _axes_for_piece, seeds_for_piece
    res, p, _proj, _j = chain_build
    assert len(_axes_for_piece("pie_izq", res)) == 3 and len(_axes_for_piece("pantorrilla_izq", res)) == 2   # tobillo+rodilla+cadera / rodilla+cadera
    assert len(_axes_for_piece("muslo_izq", res)) == 1
    seeds = seeds_for_piece("pie_izq", res, p)
    assert seeds and all(len(sd) == 3 for sd in seeds)
    assert all(len(sd) == 2 for sd in seeds_for_piece("pantorrilla_izq", res, p))
