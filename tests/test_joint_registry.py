"""Registro de tipos de articulación y kit de conformidad (plan de plugins de articulación, Fase 0)."""
import numpy as np
import pytest

from afm import capacity as C
from afm import joint_types as JT
from afm.joint_types import testing as K
from afm.params import Params, PinSpec


def test_registry_has_mushroom_with_labels_in_three_languages():
    t = JT.get("mushroom")
    assert t.id == "mushroom" and "mushroom" in JT.ids()
    assert set(t.labels) >= {"es", "en", "pt"} and all(t.labels.values())
    assert t.params_cls is PinSpec and t.slots


def test_unknown_type_is_a_clear_error():
    with pytest.raises(ValueError, match="desconocido"):
        JT.get("rotula")


def test_duplicate_id_and_wrong_api_are_rejected():
    with pytest.raises(ValueError, match="ya está registrado"):
        JT.register(JT.get("mushroom"))

    class Other:
        id, api_version, labels, params_cls, slots = "otro", 99, {}, PinSpec, frozenset()

    with pytest.raises(ValueError, match="API"):
        JT.register(Other())
    assert "otro" not in JT.REGISTRY


@pytest.mark.parametrize("type_id", sorted(JT.REGISTRY))
def test_every_registered_type_passes_the_conformance_kit(type_id):
    checks = K.check_type(JT.get(type_id))
    assert not K.failures(checks), [(c.n, c.name, c.detail) for c in K.failures(checks)]
    assert {c.n for c in checks} >= {1, 2, 3, 5, 6, 7, 8}


def test_kit_catches_a_type_that_does_not_clear_its_pin():
    """Un tipo cuyo cortador del padre es vacío debe fallar el giro libre (el pasador choca con el padre)."""
    base = JT.get("mushroom")

    class NoCavity:
        id, api_version, labels, params_cls, slots = "sincavidad", JT.API_VERSION, base.labels, PinSpec, base.slots
        default_params = base.default_params
        requires = base.requires

        def build(self, site):
            r = base.build(site)
            r.cutters["parent"] = []
            return r

    bad = {c.n for c in K.failures(K.check_type(NoCavity()))}
    assert 3 in bad


def test_mushroom_requires_matches_capacity_demand():
    p = Params()
    t = JT.get("mushroom")
    assert t.requires(p.shoulder_pin, p, "hombro").to_dict() == C.demand("hombro", p)
    assert t.requires(p.hip_pin, p, "cadera").to_dict() == C.demand("cadera", p)


def test_revolute_motion_unpacks_like_the_old_tuple():
    from afm import solids as S
    m = JT.RevoluteMotion(S.Manifold.cube((1, 1, 1)), np.zeros(3), np.array([1.0, 0, 0]), (0.0, 30.0))
    limb, c, ax, (a0, a1) = m
    assert (a0, a1) == (0.0, 30.0) and m[3][1] == 30.0 and m[1] is c
    assert m.revolute() is m
    poses = list(m.poses(10.0))
    assert len(poses) == 4 and np.allclose(poses[0], np.eye(4))
    assert isinstance(m, tuple) and len(m) == 4


def test_sweep_angles_closes_on_the_end_of_the_range():
    assert JT.sweep_angles((0.0, 0.0), 5.0) == [0.0]
    assert JT.sweep_angles((0.0, 12.0), 5.0) == [0.0, 5.0, 10.0, 12.0]


def test_build_result_motion_is_revolute_for_every_limb():
    from afm.project import demo_project
    from afm.testfigure import make_doll
    from afm.pipeline import build
    from afm.limbs import LimbSpec  # noqa: F401
    from afm import solids as S
    proj = demo_project()
    body = S.to_manifold(make_doll())
    res = build(body, proj.limbs, proj.params, sym=None, sit=True, joints=proj.joints)
    assert res.motion and all(isinstance(m, JT.RevoluteMotion) for m in res.motion.values())
