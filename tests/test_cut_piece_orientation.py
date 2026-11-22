"""O01 · La cabeza de reemplazo con color por roles no puede quedar con la cara hacia atrás (`docs/PLAN_orientacion_color_cabeza.md`).

Datos reales de «bolivar-cabeza» (`proceres-127`): el busto del FBX con sus roles (`tests/data/bolivar_cabeza_roles.npz`, el `.color.npz`
guardado) y la malla de la pieza cortada en la base del cuello (`tests/data/bolivar_cabeza_pieza.npz`, el STL del catálogo). En el FBX la
cara mira a +Z (Y arriba); en la pieza el frente es +Y (marco de la figura, Z arriba). La similitud guardada en la ficha tiene la cara hacia −Y:
girada 180° sobre el eje del cuello, pero con una media de 0,30 mm que pasa la tolerancia (0,52 mm), así que el calce solo no la delata.
"""
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from afm.frame import FigureFrame

DATA = Path(__file__).parent / "data"
FRONT = np.array([0.0, 1.0, 0.0])        # el frente de la pieza en el marco de la figura
HEAD_MIN_Y = 30.0                        # en el FBX (Y arriba) la cabeza es lo que está por encima de este valor; debajo, hombros y uniforme
# Similitud FBX → pieza que guardó la ficha `cabeza-simon-bolivar-cuerpo-fixed-01@2` (la que dejó la cara hacia atrás).
SAVED = np.array([[0.40620497691523527, 0.004457893824021798, 0.015824889196807194, -0.12517361588103304],
                  [0.016414563931472913, -0.13206412305399134, -0.38413853321303754, 2.6033102896971174],
                  [0.0009284537560188084, 0.3844632333406902, -0.13213607905863195, 96.52478187185811],
                  [0.0, 0.0, 0.0, 1.0]])


def _angle(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return float(np.degrees(np.arccos(np.clip(a @ b / np.linalg.norm(a) / np.linalg.norm(b), -1, 1))))


@pytest.fixture(scope="module")
def bust():
    """(vértices, caras, dirección de la cara en el FBX): el busto con los roles guardados. La dirección es del centro de la cabeza al
    centroide (ponderado por área) de los triángulos de la cabeza con rol «piel»."""
    z = np.load(DATA / "bolivar_cabeza_roles.npz", allow_pickle=True)
    V, F = z["vertices"].astype(float), z["faces"].astype(np.int64)
    roles = json.loads(bytes(z["meta"]).decode())["roles"]
    lab, uv = z["labels"], z["corner_uv"].astype(float).mean(1)
    h, w = lab.shape
    face_role = lab[np.clip(((1 - uv[:, 1]) * h).astype(int), 0, h - 1), np.clip((uv[:, 0] * w).astype(int), 0, w - 1)]
    tri = V[F]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    cen = tri.mean(1)
    head = cen[:, 1] > HEAD_MIN_Y
    skin = (face_role == roles.index("piel")) & head
    face = (cen[skin] * area[skin, None]).sum(0) / area[skin].sum() - (cen[head] * area[head, None]).sum(0) / area[head].sum()
    return V, F, face


@pytest.fixture(scope="module")
def piece():
    z = np.load(DATA / "bolivar_cabeza_pieza.npz")
    return trimesh.Trimesh(z["vertices"].astype(float), z["faces"], process=False)


def _face_in_piece(M, face_fbx):
    """Hacia dónde mira la cara del FBX en el marco de la pieza (solo la rotación de `M`)."""
    return M[:3, :3] @ face_fbx


def test_the_saved_pose_has_the_face_at_the_back(bust):
    """El error de la ficha guardada: la cara del FBX (+Z) cae sobre −Y de la pieza. Es la medida que usan las demás pruebas."""
    _, _, face = bust
    assert face[2] > 10 and abs(face[0]) < 3                            # la cara del FBX mira a +Z
    f = _face_in_piece(SAVED, face)
    assert _angle(np.array([f[0], f[1], 0.0]), FRONT) > 150


def test_the_wrong_pose_fits_as_well_as_the_right_one(bust, piece):
    """El calce NO delata el error: con la pose guardada (cara atrás) la media pasa la tolerancia del motor (0,52 mm)."""
    from afm.frame import _SourceSurface, _trimmed

    V, F, _ = bust
    surf = _SourceSurface(V, F, samples=40_000)
    mean, _, _, cover = _trimmed(surf, SAVED, np.asarray(piece.vertices, float), 0.8, exact=True, cover_mm=1.04)
    assert mean <= 0.5188 and cover >= 0.6


@pytest.mark.xfail(strict=True, reason="O05: la alineación de pieza recortada es ciega al sentido adelante/atrás")
def test_cut_alignment_puts_the_face_toward_the_front_of_the_piece(bust, piece):
    """O01(a): la alineación de la cabeza de Bolívar con su pieza deja la cara del FBX del lado del frente (+Y), no del de la nuca."""
    V, F, face = bust
    fr = FigureFrame.from_cut_piece_similarity(V, piece, source_faces=F)
    f = _face_in_piece(fr.matrix, face)
    assert _angle(np.array([f[0], f[1], 0.0]), FRONT) < 60, f"la cara quedó hacia {f.round(1)}"
