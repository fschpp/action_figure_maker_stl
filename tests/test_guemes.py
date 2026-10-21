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


@pytest.mark.skipif(not os.path.exists(STL), reason="falta el STL de ejemplo")
@pytest.mark.parametrize("depth", [1.0, 3.0])
def test_guemes_flat_thigh_fill_closes_the_hip_gap(depth):
    """Sin soportes + rellenar muslos: el muslo se alarga hasta la pelvis recortada, gira libre y de pie queda a una holgura."""
    import dataclasses

    import numpy as np

    from afm import meshio, pipeline as P, solids as S

    spec = importlib.util.spec_from_file_location("guemes_example", os.path.join(ROOT, "examples", "guemes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    proj = mod.proj
    mesh, _ = meshio.load_figure(STL, proj.params.ref_height, proj.crown_z)
    mesh.apply_transform([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    mesh = P.align_to_symmetry(mesh, proj.symmetry)
    res = P.build(S.to_manifold(mesh), proj.limbs, proj.params, proj.symmetry, progress=lambda *a: None)
    plain, info0 = P.flat_for_print(res, depth, proj.params)
    assert "relleno_cadera" not in info0
    filled, info = P.flat_for_print(res, depth, dataclasses.replace(proj.params, flat_fill_hip=True))
    rep = info["relleno_cadera"]["pierna"]
    assert "aviso" not in rep and rep["volumen_muslo_agregado_mm3"] > 5
    assert rep["choque_max_mm3"] == 0.0 and 0.3 <= rep["holgura_de_pie_mm"] <= 0.6
    z_cut = info["altura_corte_z"]
    for n, m in filled.items():
        assert m.status().name == "NoError" and len(m.decompose()) == len(plain[n].decompose()), n
    for leg in ("pierna_izq", "pierna_der"):
        assert filled[leg].bounding_box()[2] >= z_cut - 1e-3
        assert filled[leg].volume() > plain[leg].volume()
    assert filled["torso"].volume() < plain["torso"].volume()          # la pelvis se talló alrededor del muslo alargado
    c, ax, a1 = res.motion["pierna"][1], res.motion["pierna"][2], res.motion["pierna"][3][1]
    for a in np.arange(0.0, a1 + 1e-6, 15.0):                          # gira libre en todo el recorrido
        moved = S.rotate_about_axis(S.rotate_about_axis(filled["pierna_izq"], c, ax, -rep["angulo_pose"]), c, ax, a)
        assert S.intersection(moved, filled["torso"]).volume() < 1e-3
