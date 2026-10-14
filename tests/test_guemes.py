"""Prueba de integración con la figura real de Güemes (examples/)."""
import importlib.util
import json
import os
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
STL = os.path.join(ROOT, "examples", "guemes_segmentado_lq_fixed_resized.stl")


@pytest.mark.skipif(not os.path.exists(STL), reason="falta el STL de ejemplo")
def test_guemes_end_to_end(tmp_path):
    spec = importlib.util.spec_from_file_location("guemes_example", os.path.join(ROOT, "examples", "guemes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from afm.cli import run

    t = time.time()
    info = run(STL, mod.proj, str(tmp_path), "guemes", progress=lambda *_: None)
    val = info["validacion"]
    assert val["ok"], val["problems"]
    assert val["pieces"]["torso"]["componentes"] == 1
    for g in val["gaps"].values():
        assert 0.45 <= g <= 0.6
    assert (tmp_path / "guemes_ensamblada.stl").exists()
    print("tiempo", round(time.time() - t, 1), "s")
