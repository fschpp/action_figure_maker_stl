"""Prueba de integración con Azurduy (examples/): STL no estanco (4 aristas abiertas) y láminas en las suelas."""
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
STL = os.path.join(ROOT, "examples", "azurduy_lq_segmentado.stl")


@pytest.mark.skipif(not os.path.exists(STL), reason="falta el STL de ejemplo")
def test_azurduy_end_to_end(tmp_path):
    spec = importlib.util.spec_from_file_location("azurduy_example", os.path.join(ROOT, "examples", "azurduy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from afm.cli import run

    info = run(STL, mod.proj, str(tmp_path), "azurduy", progress=lambda *_: None)
    carga = info["carga"]
    assert carga["repaired"] and not carga["remeshed"]   # se cierra con fill_holes; la voxelización daba >1 M de triángulos
    val = info["validacion"]
    assert val["ok"], val["problems"]
    assert val["pieces"]["torso"]["componentes"] == 1     # sin restos de las láminas de las suelas
    for g in val["gaps"].values():
        assert 0.45 <= g <= 0.6
    assert (tmp_path / "azurduy_ensamblada.stl").exists()


def test_repair_keeps_topology_faces(tmp_path):
    """Regresión: no borrar triángulos «degenerados» si eso abre una malla que manifold3d aceptaba."""
    import trimesh
    from afm import meshio

    path = os.path.join(ROOT, "examples", "belgrano_lq_segmentado.stl")
    if not os.path.exists(path):
        pytest.skip("falta el STL de ejemplo")
    mesh, rep = meshio.load_figure(path, 100.0, None)
    assert not rep.remeshed
    assert len(mesh.faces) == len(trimesh.load(path).faces)
