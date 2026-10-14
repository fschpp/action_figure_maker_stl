"""Propuesta automática de pasador y recorte de cadera (limbs.propose_geometry) contra el ajuste manual de Mickey."""
import dataclasses
import importlib.util
import os

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
STL = os.path.join(ROOT, "examples", "mickey_reescalado.stl")


@pytest.mark.skipif(not os.path.exists(STL), reason="falta el STL de ejemplo")
def test_auto_matches_manual_fit_on_mickey():
    from afm import meshio, pipeline as P, solids as S
    from afm.limbs import find_contact_face, propose_geometry, separate_limb

    spec = importlib.util.spec_from_file_location("mickey_example", os.path.join(ROOT, "examples", "mickey.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    proj = mod.proj
    mesh, _ = meshio.load_figure(STL, proj.params.ref_height, proj.crown_z)
    mesh.apply_transform([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    mesh = P.align_to_symmetry(mesh, proj.symmetry)
    half = S.to_manifold(mesh).trim_by_plane((1.0, 0.0, 0.0), 0.0)

    manual = next(l for l in proj.limbs if l.name == "pierna")
    bare = dataclasses.replace(manual, pin_center=None, cap_radius=None, cap_center=None, burr=0.0)
    limb, face = separate_limb(half, bare, 0.0)
    auto = propose_geometry(face, dataclasses.replace(bare, auto=True))

    assert np.allclose(auto.pin_center[1:], manual.pin_center[1:], atol=0.3)   # X se proyecta sobre la cara
    assert abs(auto.cap_radius - manual.cap_radius) < 0.4
    assert abs(auto.cap_center[1] - manual.cap_center[1]) < 0.3
    # lo indicado por el usuario no se pisa
    keep = propose_geometry(face, dataclasses.replace(bare, auto=True, pin_center=(1, 2, 3), cap_radius=5.0))
    assert keep.pin_center == (1, 2, 3) and keep.cap_radius == 5.0
