import json
import os

import numpy as np
import pytest
import trimesh

from afm import meshio
from afm.gui.session import Session
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll


@pytest.fixture(scope="module")
def doll_stl(tmp_path_factory):
    d = tmp_path_factory.mktemp("doll")
    m = make_doll()
    m.apply_scale(0.05)                      # el STL "viene" en otras unidades (5 de alto)
    path = str(d / "muneco.stl")
    m.export(path)
    return path


def test_load_normalizes_to_100mm(doll_stl):
    s = Session()
    s.load(doll_stl)
    assert abs(s.base.extents[2] - 100.0) < 1e-6 and abs(s.base.bounds[0, 2]) < 1e-9


def test_crown_rescale_matches_reload(doll_stl):
    s = Session()
    s.load(doll_stl)
    s.set_crown_from_mm(90.0)                # "la coronilla está a 90 mm": el resto es sombrero
    ref, _ = meshio.load_figure(doll_stl, 100.0, s.project.crown_z)
    assert np.allclose(ref.bounds, s.base.bounds, atol=1e-6)
    assert abs(s.base.bounds[1, 2] - 100 * 100 / 90) < 1e-6


def test_set_front_flips_and_resets(doll_stl):
    s = Session()
    s.load(doll_stl)
    s.auto_symmetry()
    s.add_limb("brazo", "arm", Plane.from_normal((0, 0, 0), (0, 1, 0)))
    pt = np.array([3.0, 7.0, 50.0])
    a = s.base.vertices[np.argmin(np.linalg.norm(s.base.vertices - pt, axis=1))]
    s.set_front("-Y")
    assert s.project.limbs == [] and s.project.symmetry is None
    b = s.figure.vertices[np.argmin(np.linalg.norm(s.figure.vertices - np.array([-3.0, -7.0, 50.0]), axis=1))]
    assert np.allclose(b, a * [-1, -1, 1], atol=1e-6)


def test_symmetry_must_be_vertical(doll_stl):
    s = Session()
    s.load(doll_stl)
    with pytest.raises(ValueError):
        s.set_symmetry(Plane.from_normal((0, 0, 0), (0, 0.3, 1)))
    plane = Session.plane_from_points([(0, -5, 10), (0, 5, 10), (0, 0, 60)])
    s.set_symmetry(plane)
    assert abs(abs(plane.normal[0]) - 1) < 1e-9


def test_problems_and_silhouette(doll_stl):
    s = Session()
    assert s.problems() == ["Abrí un STL"]
    s.load(doll_stl)
    assert any("simetría" in p for p in s.problems())
    s.auto_symmetry()
    plane = Plane.from_normal((0, 0, 0), (0, 1, 0))
    s.add_limb("brazo", "arm", plane)
    assert any("silueta" in p for p in s.problems())
    sil = s.silhouette(plane)
    assert len(sil) == 1
    ext = sil[0]["exterior"]
    # u = -x en un plano frontal: el lado de trabajo (x >= 0) queda en u <= 0
    assert ext[:, 0].max() <= 1e-6 and ext[:, 0].min() < -20 and ext[:, 1].max() > 95
    full = s.silhouette(plane, positive_x_only=False)
    assert full[0]["exterior"][:, 0].max() > 20


def test_full_flow_and_project_roundtrip(doll_stl, tmp_path):
    s = Session()
    s.load(doll_stl)
    s.auto_symmetry()
    demo = demo_project()
    for l in demo.limbs:
        s.add_limb(l.name, l.kind, l.plane, l.thickness, auto=False)
        s.set_limb_polygon(l.name, l.polygon)
    assert s.problems() == []
    steps = []
    out = s.build(progress=steps.append)
    assert out.validation["ok"], out.validation["problems"]
    assert out.validation["pieces"]["torso"]["componentes"] == 1
    assert steps, "debe informar progreso"
    info = s.export(str(tmp_path), "muneco", orient=True)
    assert os.path.exists(tmp_path / "muneco_ensamblada.stl") and "orientacion" in info["archivos"]
    # el proyecto guardado se reabre con el STL de origen
    s2 = Session.open_project(str(tmp_path / "muneco.proyecto"))
    assert [l.name for l in s2.project.limbs] == ["brazo", "pierna"] and s2.work is not None
    assert json.load(open(tmp_path / "muneco.proyecto"))["source"] == os.path.abspath(doll_stl)
    with pytest.raises(FileNotFoundError):
        Session.open_project(str(tmp_path / "muneco.proyecto"), stl_path=str(tmp_path / "nada.stl"))
