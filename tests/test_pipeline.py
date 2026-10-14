import json
import os

import pytest

from afm import cli
from afm.project import Project, demo_project


@pytest.fixture(scope="module")
def demo_out(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    assert cli.main(["demo", "--out", str(out)]) == 0
    return out


def test_demo_outputs(demo_out):
    names = os.listdir(demo_out / "piezas")
    for part in ("torso", "brazo_izq", "brazo_der", "pierna_izq", "pierna_der"):
        assert f"muneco_{part}.stl" in names
    assert (demo_out / "muneco_ensamblada.stl").exists()


def test_demo_validation(demo_out):
    info = json.load(open(demo_out / "muneco_informe.json"))
    val = info["validacion"]
    assert val["ok"], val["problems"]
    assert val["pieces"]["torso"]["componentes"] == 1          # torso soldado
    for g in val["gaps"].values():
        assert 0.45 <= g <= 0.6                                 # holgura ≈ 0,5 mm


def test_project_roundtrip(tmp_path):
    p = demo_project()
    f = tmp_path / "x.proyecto"
    p.save(str(f))
    q = Project.load(str(f))
    assert len(q.limbs) == 2 and q.params.clearance == p.params.clearance
    assert q.limbs[1].kind == "leg"
