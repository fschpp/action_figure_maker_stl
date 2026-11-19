"""T03 · FigureFrame y la matriz de orient_z_up."""
import numpy as np
import pytest
import trimesh

from afm import meshio, pipeline as P
from afm.frame import FigureFrame
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll


def _y_up(mesh):
    """El muñeco (Z arriba) como lo exporta un programa con Y arriba: (x, y, z) → (x, z, −y)."""
    m = mesh.copy()
    R = np.eye(4)
    R[:3, :3] = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]
    m.apply_transform(R)
    return m


@pytest.fixture(scope="module")
def doll_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("frame")
    doll = make_doll()
    up_z, up_y = str(d / "doll_z.stl"), str(d / "doll_y.stl")
    doll.export(up_z)
    _y_up(doll).export(up_y)
    return {"z": up_z, "y": up_y, "mesh": doll, "ymesh": _y_up(doll)}


def test_orient_matrix_recorded_for_y_up(doll_files):
    _m, rep = meshio.load_figure(doll_files["y"], 100.0)
    assert np.array_equal(np.array(rep.orient_matrix).reshape(3, 3), [[1, 0, 0], [0, 0, -1], [0, 1, 0]])
    _m, rep_z = meshio.load_figure(doll_files["z"], 100.0)
    assert np.allclose(np.array(rep_z.orient_matrix).reshape(3, 3), np.eye(3))
    assert isinstance(rep.orient_matrix, tuple) and len(rep.orient_matrix) == 9


def test_orient_z_up_matrix_function_matches_orient_z_up(doll_files):
    m = doll_files["ymesh"]
    R = meshio.orient_z_up_matrix(m)
    out = meshio.orient_z_up(m)
    assert np.allclose(np.asarray(m.vertices) @ R.T, out.vertices) and R.shape == (3, 3)


def test_load_figure_output_unchanged(doll_files):
    """El muñeco guardado como STL se carga igual que antes: orientar, escalar a 100 mm, pies en z=0, centrado."""
    mesh, rep = meshio.load_figure(doll_files["z"], 100.0)
    ref = trimesh.load(doll_files["z"], force="mesh")
    ref = meshio.orient_z_up(ref)
    assert np.allclose(np.sort(np.asarray(mesh.vertices), axis=0), np.sort(np.asarray(rep.to_figure(ref.vertices)), axis=0), atol=1e-6)
    assert mesh.bounds[0, 2] == pytest.approx(0.0, abs=1e-9)


def test_frame_matches_to_figure_for_points(doll_files):
    mesh, rep = meshio.load_figure(doll_files["y"], 100.0)
    f = FigureFrame.from_load(rep)
    raw = np.asarray(doll_files["ymesh"].vertices)
    expected = rep.to_figure(meshio.orient_z_up(doll_files["ymesh"]).vertices)
    assert np.allclose(f.apply(raw), expected, atol=1e-9)


def test_frame_front_minus_y_flips_xy(doll_files):
    _m, rep = meshio.load_figure(doll_files["z"], 100.0)
    pts = np.array([[3.0, 5.0, 40.0], [-7.0, 2.0, 10.0]])
    a, b = FigureFrame.from_load(rep, "+Y").apply(pts), FigureFrame.from_load(rep, "-Y").apply(pts)
    assert np.allclose(b[:, :2], -a[:, :2]) and np.allclose(b[:, 2], a[:, 2])


def test_frame_matches_cli_pipeline(doll_files):
    """El marco aplicado a los vértices del archivo da la malla que arma `cli.run`: orientar, normalizar, frente −Y y alinear simetría."""
    sym = Plane.from_normal((2.0, 1.0, 0), (np.cos(0.1), np.sin(0.1), 0), "simetría")
    mesh, rep = meshio.load_figure(doll_files["y"], 100.0)
    mesh.apply_transform([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    mesh = P.align_to_symmetry(mesh, sym)
    f = FigureFrame.from_load(rep, "-Y", sym)
    raw = np.asarray(doll_files["ymesh"].vertices)
    got = f.apply(raw)
    # `load_figure` puede reordenar vértices al reparar: se compara cada punto con la superficie de la malla del motor
    d = trimesh.proximity.closest_point(mesh, got)[1]
    assert d.max() < 1e-6


def test_det_is_plus_one(doll_files):
    _m, rep = meshio.load_figure(doll_files["y"], 100.0)
    sym = Plane.from_normal((2.0, 1.0, 0), (np.cos(0.3), np.sin(0.3), 0), "simetría")
    for front in ("+Y", "-Y"):
        assert FigureFrame.from_load(rep, front, sym).det() == pytest.approx(1.0, abs=1e-9)


def test_report_with_orient_matrix_is_json_serializable(doll_files):
    import json
    _m, rep = meshio.load_figure(doll_files["y"], 100.0)
    json.dumps(rep.__dict__, default=float)
