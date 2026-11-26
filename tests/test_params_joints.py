"""Perfil de línea `Params.joints` y medidas propias por tipo (plan de plugins §3.6, Fase 1)."""
import dataclasses

import pytest

from afm import detent as DT
from afm import joint_types as JT
from afm import pipeline as P
from afm.joints import hinge_for
from afm.limbs import LimbSpec
from afm.params import Params


def test_default_params_do_not_write_joints_so_old_lines_and_hashes_do_not_change():
    d = Params().to_dict()
    assert "joints" not in d
    assert set(d) == {f.name for f in dataclasses.fields(Params)} - {"joints"}
    assert Params.from_dict(d) == Params()                      # una línea sin `joints` carga igual


def test_params_joints_round_trip_through_json():
    import json
    p = Params(joints={"hombro": {"type": "detent", "params": {"disc_d": 9.0}}, "cadera": {"type": "mushroom"}})
    d = json.loads(json.dumps(p.to_dict()))
    assert d["joints"]["hombro"] == {"type": "detent", "params": {"disc_d": 9.0}}
    assert Params.from_dict(d) == p


@pytest.mark.parametrize("bad", [[1], {"hombro": "detent"}, {"hombro": {"params": {}}}, {"hombro": {"type": "x", "params": [1]}}])
def test_malformed_profile_is_a_clear_error(bad):
    with pytest.raises(ValueError, match="joints"):
        Params.from_dict({"joints": bad})


def _limb(demo_limbs, kind):
    return next(l for l in demo_limbs if l.kind == kind)


@pytest.fixture(scope="module")
def limbs():
    from afm.project import demo_project
    return demo_project().limbs


def test_type_resolution_precedence(limbs):
    arm, leg = _limb(limbs, "arm"), _limb(limbs, "leg")
    p = Params()
    assert DT.mechanism_id(arm, p) == "mushroom" and DT.detent_spec_for(arm, p) is None
    assert DT.mechanism_id(arm, dataclasses.replace(p, shoulder_detent=True)) == "detent"      # lo de siempre
    prof = dataclasses.replace(p, joints={"hombro": {"type": "detent"}})
    assert DT.mechanism_id(arm, prof) == "detent" and DT.mechanism_id(leg, prof) == "mushroom"  # el perfil es por lugar
    assert DT.mechanism_id(dataclasses.replace(arm, mechanism="mushroom"), prof) == "mushroom"  # el miembro pisa al perfil
    assert DT.mechanism_id(dataclasses.replace(arm, mechanism="detent"), p) == "detent"


@pytest.mark.parametrize("mech", ["rotula", "hinge"])
def test_unknown_or_wrong_slot_type_names_the_valid_ones(limbs, mech):
    with pytest.raises(ValueError, match=r"desconocido.*mushroom \| detent"):
        DT.mechanism_id(dataclasses.replace(_limb(limbs, "arm"), mechanism=mech), Params())


def test_mushroom_measures_precedence(limbs):
    arm = _limb(limbs, "arm")
    p = Params()
    assert P._pin_spec(arm, p) == p.shoulder_pin                                              # sin nada: el de la línea
    prof = dataclasses.replace(p, joints={"hombro": {"type": "mushroom", "params": {"shaft_d": 2.5, "head_d": 5.0}}})
    got = P._pin_spec(arm, prof)
    assert (got.shaft_d, got.head_d, got.shaft_len) == (2.5, 5.0, p.shoulder_pin.shaft_len)    # el perfil pisa lo que dice
    own = dataclasses.replace(arm, mechanism_params={"shaft_d": 2.8})
    assert P._pin_spec(own, prof).shaft_d == 2.8 and P._pin_spec(own, prof).head_d == 5.0      # el miembro pisa al perfil
    assert P._pin_spec(dataclasses.replace(arm, mechanism_params={"shaft_d": 2.8}), p).head_d == p.shoulder_pin.head_d


def test_detent_measures_precedence(limbs):
    arm = dataclasses.replace(_limb(limbs, "arm"), mechanism="detent")
    p = Params()
    assert DT.detent_spec_for(arm, p) == p.detent
    prof = dataclasses.replace(p, joints={"hombro": {"type": "detent", "params": {"disc_d": 9.0}}})
    assert DT.detent_spec_for(arm, prof).disc_d == 9.0
    assert DT.detent_spec_for(dataclasses.replace(arm, mechanism_params={"disc_d": 10.0}), prof).disc_d == 10.0
    assert DT.detent_spec_for(dataclasses.replace(arm, mechanism_params={"disc_d": 10.0}), p).notch_depth == p.detent.notch_depth


def test_measures_of_another_type_are_not_applied(limbs):
    """El perfil dice `detent` con medidas de clic; un miembro que fija `mushroom` no las hereda (ni falla por ellas)."""
    arm = dataclasses.replace(_limb(limbs, "arm"), mechanism="mushroom")
    prof = Params(joints={"hombro": {"type": "detent", "params": {"disc_d": 9.0}}})
    assert P._pin_spec(arm, prof) == prof.shoulder_pin


def test_unknown_measure_key_is_a_clear_error(limbs):
    own = dataclasses.replace(_limb(limbs, "arm"), mechanism_params={"diametro": 3})
    with pytest.raises(ValueError, match="parámetro desconocido 'diametro'.*PinSpec"):
        P._pin_spec(own, Params())


def test_limbspec_new_field_is_only_written_when_used():
    from afm.project import demo_project
    arm = _limb(demo_project().limbs, "arm")
    assert "mechanism_params" not in arm.to_dict()
    own = dataclasses.replace(arm, mechanism_params={"shaft_d": 2.8})
    d = own.to_dict()
    assert d["mechanism_params"] == {"shaft_d": 2.8} and LimbSpec.from_dict(d).mechanism_params == {"shaft_d": 2.8}
    assert own.mirrored().mechanism_params == {"shaft_d": 2.8} and own.mirrored().mechanism_params is not own.mechanism_params
    assert LimbSpec.from_dict(arm.to_dict()).mechanism_params is None


def _knee(**kw):
    from afm.joints import JointSpec
    from afm.planes import Plane
    d = dict(name="rodilla", limb="pierna", kind="knee", plane=Plane.from_normal((0, 0, 14.0), (0, 0, 1), "art"),
             axis_center=(9.0, 0.0, 17.4))
    d.update(kw)
    return JointSpec(**d)


def test_jointspec_type_defaults_to_hinge_and_is_only_written_when_it_is_not():
    from afm.joints import JointSpec
    j = _knee()
    assert j.type == "hinge" and j.type_params is None
    d = j.to_dict()
    assert "type" not in d and "type_params" not in d
    assert JointSpec.from_dict(d).type == "hinge"                                  # una clave ausente se lee como «hinge»
    j2 = _knee(type_params={"pin_d": 2.2})
    assert JointSpec.from_dict(j2.to_dict()).type_params == {"pin_d": 2.2}
    assert JointSpec.from_dict({**d, "type": "swivel"}).type == "swivel"
    assert JointSpec.from_dict(j2.to_dict()).transformed(__import__("numpy").eye(4)).type_params == {"pin_d": 2.2}


def test_hinge_measures_precedence_and_type_resolution():
    p = Params()
    assert hinge_for(_knee(), p) == p.hinge_pin
    prof = Params(joints={"rodilla": {"type": "hinge", "params": {"pin_d": 2.4}}})
    assert hinge_for(_knee(), prof).pin_d == 2.4 and hinge_for(_knee(name="otra", kind="elbow"), prof) == prof.hinge_pin
    assert hinge_for(_knee(type_params={"pin_d": 2.6}), prof).pin_d == 2.6
    assert JT.resolve.joint_type_id(_knee(), prof) == "hinge"
    with pytest.raises(ValueError, match="desconocido 'rotula'"):
        JT.resolve.joint_type_id(_knee(type="rotula"), p)
    with pytest.raises(ValueError, match="desconocido 'detent'"):          # `detent` no sirve en la rodilla
        JT.resolve.joint_type_id(_knee(), Params(joints={"rodilla": {"type": "detent"}}))


def test_profile_selects_the_detent_when_building_the_doll():
    """De punta a punta: el perfil de línea cambia el tipo de los dos hombros sin tocar los miembros."""
    from afm import solids as S
    from afm.project import demo_project
    from afm.testfigure import make_doll
    proj = demo_project()
    params = dataclasses.replace(proj.params, decimate=0, joints={"hombro": {"type": "detent"}})
    res = P.build(S.to_manifold(make_doll()), proj.limbs, params, sym=None, sit=True)
    assert set(res.detent) == {l.name for l in proj.limbs if l.kind == "arm"} and res.detent
