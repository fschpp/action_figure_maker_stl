"""Prueba de integración con la figura real de Belgrano (examples/): STL en otras unidades (~1,16 de alto)."""
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
STL = os.path.join(ROOT, "examples", "belgrano_lq_segmentado.stl")


@pytest.mark.skipif(not os.path.exists(STL), reason="falta el STL de ejemplo")
def test_belgrano_end_to_end(tmp_path):
    spec = importlib.util.spec_from_file_location("belgrano_example", os.path.join(ROOT, "examples", "belgrano.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from afm.cli import run

    info = run(STL, mod.proj, str(tmp_path), "belgrano", progress=lambda *_: None)
    carga = info["carga"]
    assert carga["scale"] == pytest.approx(100 / 1.1595, rel=1e-3)   # se escaló a mm
    assert not carga["remeshed"]          # estanco: no hace falta remallar (regresión: limpieza de degeneradas)
    val = info["validacion"]
    assert val["ok"], val["problems"]
    assert val["pieces"]["torso"]["componentes"] == 1
    for g in val["gaps"].values():
        assert 0.45 <= g <= 0.6
    assert (tmp_path / "belgrano_ensamblada.stl").exists()
