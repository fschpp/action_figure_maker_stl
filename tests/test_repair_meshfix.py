"""Regresión: un STL con agujeros no triangulares y una arista no-variedad se repara con pymeshfix,
sin caer en la voxelización (lenta, con mucha RAM y sin aviso)."""
import os
import time

import pytest

from afm import meshio

ROOT = os.path.dirname(os.path.dirname(__file__))
STL = os.path.join(ROOT, "examples", "belgrano_lq_rsized.stl")


@pytest.mark.skipif(not os.path.exists(STL), reason="falta el STL de ejemplo")
def test_belgrano_rsized_repaired_without_voxelizing():
    pytest.importorskip("pymeshfix")
    msgs = []
    t0 = time.monotonic()
    mesh, rep = meshio.load_figure(STL, 100.0, None, progress=msgs.append)
    assert time.monotonic() - t0 < 30
    assert rep.repaired and not rep.remeshed
    assert mesh.is_watertight and 9000 < len(mesh.faces) < 11000     # ≈ los 9870 triángulos originales
    assert any("pymeshfix" in w for w in rep.warnings)
    assert any("leyendo" in m for m in msgs) and any("reparando" in m for m in msgs)
    assert abs(mesh.extents[2] - 100.0) < 1.0
