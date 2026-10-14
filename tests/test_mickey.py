"""Integración con el Mickey original (examples/)."""
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
STL = os.path.join(ROOT, "examples", "mickey_reescalado.stl")


@pytest.mark.skipif(not os.path.exists(STL), reason="falta el STL de ejemplo")
def test_mickey_end_to_end(tmp_path):
    spec = importlib.util.spec_from_file_location("mickey_example", os.path.join(ROOT, "examples", "mickey.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from afm.cli import run

    info = run(STL, mod.proj, str(tmp_path), "mickey", progress=lambda *_: None)
    val = info["validacion"]
    assert val["ok"], val["problems"]
    assert val["pieces"]["torso"]["componentes"] == 1
    for g in val["gaps"].values():
        assert 0.45 <= g <= 0.6
