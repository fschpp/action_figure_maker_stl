"""Proyecto versión 2 (plan de plugins §3.6, Fase 1): se escribe solo si usa tipos de articulación; se leen ambas versiones."""
import dataclasses
import json

import pytest

from afm.project import PROJECT_VERSION, Project, demo_project


def test_a_project_without_new_data_stays_version_1_and_is_byte_identical():
    proj = demo_project()
    d = proj.to_dict()
    assert d["version"] == 1 and PROJECT_VERSION == 2
    assert "joints" not in d["params"]
    assert all("mechanism_params" not in l for l in d["limbs"])
    assert json.dumps(Project.from_dict(d).to_dict(), sort_keys=True) == json.dumps(d, sort_keys=True)


@pytest.mark.parametrize("change", ["params", "limb", "joint"])
def test_using_a_new_field_writes_version_2_and_round_trips(change):
    from afm.joints import JointSpec
    from afm.planes import Plane
    proj = demo_project()
    if change == "params":
        proj.params = dataclasses.replace(proj.params, joints={"hombro": {"type": "detent", "params": {"disc_d": 9.0}}})
    elif change == "limb":
        proj.limbs[0] = dataclasses.replace(proj.limbs[0], mechanism_params={"shaft_d": 2.8})
    else:
        proj.joints = [JointSpec(name="rodilla", limb="pierna", kind="knee", plane=Plane.from_normal((0, 0, 14.0), (0, 0, 1), "a"),
                                 axis_center=(9.0, 0.0, 17.4), type_params={"pin_d": 2.2})]
    d = json.loads(json.dumps(proj.to_dict()))
    assert d["version"] == 2
    back = Project.from_dict(d)
    assert json.dumps(back.to_dict(), sort_keys=True) == json.dumps(d, sort_keys=True)


def test_both_versions_load_and_a_newer_one_is_a_clear_error():
    d = demo_project().to_dict()
    assert Project.from_dict({**d, "version": 1}).limbs and Project.from_dict({**d, "version": 2}).limbs
    d.pop("version")
    assert Project.from_dict(d).limbs                                    # un archivo muy viejo sin versión
    with pytest.raises(ValueError, match="versión 3.*hasta la 2"):
        Project.from_dict({**demo_project().to_dict(), "version": 3})


def test_save_and_load_a_v2_file(tmp_path):
    proj = demo_project()
    proj.params = dataclasses.replace(proj.params, joints={"cadera": {"type": "detent"}})
    path = tmp_path / "x.proyecto"
    proj.save(str(path))
    assert json.load(open(path))["version"] == 2
    assert Project.load(str(path)).params.joints == {"cadera": {"type": "detent"}}
