"""T09 · registro pieza → fuente con Güemes real (reproduce la tabla del §3.3 del plan). Lento: AFM_SLOW=1."""
import importlib.util
import os

import numpy as np
import pytest

from afm import meshio, solids as S
from afm.cli import run
from afm.colorreg import register_piece
from afm.colorsrc import ColorSource
from afm.fbxio import load_textured
from afm.frame import FigureFrame

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STL = os.path.join(ROOT, "examples", "guemes_segmentado_lq_fixed_resized.stl")
FBX = os.path.join(ROOT, "examples", "guemes_segmentado_lq_texturas.fbx")
THRESH_MM = 0.6

pytestmark = [
    pytest.mark.skipif(os.environ.get("AFM_SLOW") != "1", reason="AFM_SLOW=1 para correr Güemes (≈ 15 s de motor)"),
    pytest.mark.skipif(not (os.path.isfile(STL) and os.path.isfile(FBX)), reason="faltan los archivos de ejemplo de Güemes"),
]


def _guemes_project():
    spec = importlib.util.spec_from_file_location("guemes_example", os.path.join(ROOT, "examples", "guemes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.proj


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    proj = _guemes_project()
    keep: dict = {}
    run(STL, proj, str(tmp_path_factory.mktemp("guemes")), "guemes", progress=lambda *_: None, keep=keep)
    return proj, keep["res"], keep["params"]


@pytest.fixture(scope="module")
def src(built):
    proj = built[0]
    mesh, _rep = meshio.load_figure(STL, proj.params.ref_height, proj.crown_z)
    tm = load_textured(FBX)
    frame = FigureFrame.from_similarity(tm, mesh).then(FigureFrame.engine_post(proj.front, proj.symmetry))
    assert frame.report["ok"], frame.report
    return ColorSource(tm, frame)


@pytest.fixture(scope="module")
def registered(built, src):
    _proj, res, params = built
    out = {}
    for name, mf in sorted(res.pieces.items()):
        mesh = S.to_printable_trimesh(mf)
        pts = mesh.sample(20000)
        out[name] = (mesh, pts, register_piece(name, mesh, src, res, params))
    return out


def _coverage(src, pts, matrix) -> float:
    q = pts @ matrix[:3, :3].T + matrix[:3, 3]
    return float((src.query(q).dist <= THRESH_MM).mean())


def test_piece_names(registered):
    assert sorted(registered) == ["brazo_der", "brazo_izq", "pierna_der", "pierna_izq", "torso"]


def test_torso_and_arms_identity(registered):
    for n in ("torso", "brazo_izq", "brazo_der"):
        assert registered[n][2].status == "identidad", n


def test_legs_rotation_is_minus_90_about_x(registered):
    """La pierna izquierda se registra con −90° sobre +X; la derecha con +90° sobre −X (el mismo giro espejado)."""
    for n in ("pierna_izq", "pierna_der"):
        reg = registered[n][2]
        assert reg.status == "semilla", n
        axis = np.asarray(reg.axis, float)
        assert abs(abs(axis[0]) - 1.0) < 1e-6, (n, axis)
        about_x = reg.angle_deg * np.sign(axis[0])               # ángulo equivalente sobre +X
        assert abs(about_x - (-90.0)) <= 2.0, (n, reg.angle_deg, axis)


def test_coverage_per_piece(registered, src):
    cov = {n: _coverage(src, pts, reg.matrix) for n, (_m, pts, reg) in registered.items()}
    assert cov["torso"] >= 0.70, cov
    for n in ("brazo_izq", "brazo_der", "pierna_izq", "pierna_der"):
        assert cov[n] >= 0.80, cov


def test_unregistered_leg_coverage_is_low(registered, src):
    for n in ("pierna_izq", "pierna_der"):
        assert _coverage(src, registered[n][1], np.eye(4)) < 0.15, n
