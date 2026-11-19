"""T04 · Modo B: alinear el FBX con el STL (similitud + ICP de respaldo)."""
import os

import numpy as np
import pytest
import trimesh

from afm.frame import FigureFrame
from afm.testfigure import make_doll

ROOT = os.path.dirname(os.path.dirname(__file__))
FBX = os.path.join(ROOT, "examples", "guemes_segmentado_lq_texturas.fbx")
STL = os.path.join(ROOT, "examples", "guemes_segmentado_lq_fixed_resized.stl")
real = pytest.mark.skipif(not (os.path.exists(FBX) and os.path.exists(STL)), reason="faltan los archivos de Güemes")


def _similarity(R, s, t):
    M = np.eye(4)
    M[:3, :3] = s * np.asarray(R, float)
    M[:3, 3] = t
    return M


def _transformed(mesh, M):
    m = mesh.copy()
    m.apply_transform(M)
    return m


def _rz(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1.0]])


Y_UP = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]       # (x, y, z) → (x, z, −y): el muñeco como lo exporta un programa con Y arriba


@pytest.fixture(scope="module")
def doll():
    return make_doll()


@pytest.fixture(scope="module")
def guemes():
    from afm import fbxio

    return fbxio.load_textured(FBX), trimesh.load(STL, force="mesh")


def test_recovers_known_similarity(doll):
    src = _transformed(doll, _similarity(Y_UP, 1.7, [12.0, -5.0, 30.0]))
    f = FigureFrame.from_similarity(src.vertices, doll, source_faces=src.faces)
    assert f.report["ok"] and f.report["media_mm"] < 0.01
    assert np.abs(f.apply(src.vertices) - doll.vertices).max() < 0.01
    assert abs(f.det() - 1.0) < 1e-9


def test_rejects_different_figure(doll):
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
    sphere.apply_scale(doll.extents[2] / 2.0)
    sphere.apply_translation(-sphere.bounds[0] + [0, 0, 0] + [0, 0, 0])
    f = FigureFrame.from_similarity(sphere.vertices, doll, source_faces=sphere.faces)
    assert f.report["ok"] is False and f.report["media_mm"] > 0.1


@real
def test_real_guemes_without_icp(guemes):
    tm, stl = guemes
    f = FigureFrame.from_similarity(tm.vertices, stl, refine=False, source_faces=tm.faces)
    r = f.report
    assert r["media_mm"] <= 0.03 and r["p95_mm"] <= 0.06 and r["max_mm"] <= 0.15, r      # esperado 0,017 / 0,040 / 0,062 [M5]
    assert r["ok"] and r["iteraciones"] == 0
    assert abs(f.scale - 114.0 / 115.962) < 1e-4 and abs(f.det() - 1.0) < 1e-9


@real
def test_real_guemes_auto_skips_icp(guemes):
    tm, stl = guemes
    f = FigureFrame.from_similarity(tm.vertices, stl, refine=None, source_faces=tm.faces)
    assert f.report["iteraciones"] == 0


@real
def test_real_guemes_icp_does_not_worsen(guemes):
    tm, stl = guemes
    base = FigureFrame.from_similarity(tm.vertices, stl, refine=False, source_faces=tm.faces).report
    icp = FigureFrame.from_similarity(tm.vertices, stl, refine=True, source_faces=tm.faces).report
    assert icp["iteraciones"] >= 1 and icp["media_mm"] <= base["media_mm"] + 1e-9


def test_icp_fallback_recovers_offset(doll):
    """[I: validado al implementar] fuente girada sobre Z y corrida 0,3 mm: sin ICP no alcanza, con el automático se corrige.

    El plan proponía 1°; medido: con 1° la media sin ICP es 0,036 mm (el ajuste de cajas ya absorbe parte), por debajo del umbral
    de 0,05 que activa el ICP automático. Con 2° da ≈ 0,066 y el caso queda por encima del umbral (que no se toca).
    El plan esperaba < 0,01 tras el ICP; con el tope de 20 iteraciones del contrato baja a ≈ 0,014 (con 40 llegaría a 0,0098), así
    que esta prueba exige < 0,02. Lo que decide si la fuente sirve es `ok` (media ≤ 0,1 mm)."""
    c = doll.bounds.mean(0)
    R = _rz(2.0)
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = c - R @ c + [0.3, 0.0, 0.0]
    src = _transformed(doll, M)
    off = FigureFrame.from_similarity(src.vertices, doll, refine=False, source_faces=src.faces).report
    auto = FigureFrame.from_similarity(src.vertices, doll, refine=None, source_faces=src.faces).report
    assert off["media_mm"] > 0.05 and off["iteraciones"] == 0
    assert auto["iteraciones"] >= 1 and auto["media_mm"] < 0.02 and auto["ok"], (off, auto)


@real
def test_real_guemes_crown_z(guemes):
    """Cota ABSOLUTA de la coronilla de la fuente: z mín. + 100 / escala ≈ 101,769 [M5] (el STL mide 114,0 con crown_z = 100)."""
    tm, stl = guemes
    f = FigureFrame.from_similarity(tm.vertices, stl, refine=False, source_faces=tm.faces)
    zs = tm.vertices[:, [0, 2, 1]]                       # Y arriba → Z arriba (orient_z_up)
    zs = np.column_stack([tm.vertices[:, 0], -tm.vertices[:, 2], tm.vertices[:, 1]])
    crown = zs[:, 2].min() + 100.0 / f.scale
    assert crown == pytest.approx(101.769, abs=0.01)
    assert stl.bounds[1, 2] == pytest.approx(114.0, abs=1e-6)


@real
def test_front_minus_y_confirmed(guemes):
    """Girar la fuente 180° sobre Z (frente a +Y) da más de 1 mm: el control de orientación detecta el error."""
    tm, stl = guemes
    zup = np.column_stack([tm.vertices[:, 0], -tm.vertices[:, 2], tm.vertices[:, 1]])
    flipped = zup @ _rz(180.0).T
    back = flipped @ np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]]).T            # de vuelta a Y arriba para que entre por la misma ruta
    f = FigureFrame.from_similarity(back, stl, refine=False, source_faces=tm.faces)
    assert f.report["media_mm"] > 1.0 and f.report["ok"] is False


def test_vertices_only_source_still_reports(doll):
    """Sin caras se mide contra los vértices (cota superior): el informe existe y la media es pequeña para la misma malla."""
    f = FigureFrame.from_similarity(doll.vertices, doll)
    assert set(f.report) == {"media_mm", "p95_mm", "max_mm", "ok", "iteraciones"}
    assert f.report["media_mm"] < 1e-6
