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

from afm.colorpiece import _check_front, _front_message, front_hint, head_region, piece_front
from afm.colorroles import RolesPackage, face_direction
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


NECK = {"puertos": {"cuello": {"centro": [0.0, -3.172554, 107.810184], "eje": [0.0, -0.379463, -0.925207], "ref": [0.0, 0.925207, -0.379463]}}}


@pytest.fixture(scope="module")
def pkg():
    return RolesPackage.load(DATA / "bolivar_cabeza_roles.npz")


@pytest.fixture(scope="module")
def aligned(pkg, piece):
    """La alineación de pieza recortada con la pista de frente (una sola vez: son ≈9 s)."""
    return FigureFrame.from_cut_piece_similarity(pkg.vertices.astype(float), piece, source_faces=pkg.faces, front_hint=front_hint(NECK, piece, pkg))


def test_piece_front_of_the_real_head(piece):
    """O02: el frente de la pieza es +Y (tomado en el plano perpendicular al eje del cuello, que se inclina ≈ 22°) y coincide con la referencia del puerto."""
    pf = piece_front(NECK, piece)
    assert _angle(pf["front"], FRONT) < 25 and pf["ref_dev_deg"] < 5
    assert abs(pf["front"] @ pf["up"]) < 1e-9 and _angle(pf["up"], [0, 0, 1]) < 25
    no_port = piece_front(None, piece)                       # sin puerto: +Y y arriba = +Z
    assert np.allclose(no_port["front"], FRONT) and no_port["ref_dev_deg"] is None


def test_face_direction_of_the_fbx_points_at_plus_z(pkg):
    """O03: sobre la cabeza del busto (por encima de y = 30) la cara mira a +Z del FBX; sin rol de piel no hay dirección."""
    h = pkg.vertices[pkg.faces].mean(1)[:, 1] > HEAD_MIN_Y
    vec, conf = face_direction(pkg, region=h)
    assert _angle(vec, [0, 0, 1]) < 40 and vec[2] > 10 and conf > 0.3     # la cabeza está cabeceada: la piel queda un poco por debajo del centro
    assert face_direction(pkg, skin_roles=("no_existe",)) == (None, 0.0)


def test_head_region_leaves_out_the_shoulders(pkg, piece, aligned):
    """O04: con la pose alineada, la región por encima del plano del cuello es la cabeza (≈ 41 % del área del busto), no el busto entero."""
    pf = piece_front(NECK)
    V = aligned.apply(pkg.vertices.astype(float))
    m = head_region(V, pkg.faces, pf, piece.vertices)
    tri = V[pkg.faces]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    assert 0.35 < area[m].sum() / area.sum() < 0.5          # la cabeza del busto es el 41 % del área


def test_the_saved_pose_is_flagged_as_facing_backwards(pkg, piece):
    """O06: la similitud guardada (cara hacia atrás) baja `ok` y el mensaje propone girar 180°."""
    fr = FigureFrame(SAVED.copy(), 0.4065, {"ok": True, "media_mm": 0.3})
    _check_front(fr, front_hint(NECK, piece, pkg))
    assert fr.report["frente_ok"] is False and fr.report["ok"] is False and fr.report["frente"]["angulo_deg"] > 150
    assert "180" in _front_message(fr.report)


def test_cut_alignment_puts_the_face_toward_the_front_of_the_piece(pkg, aligned):
    """O01(a)/O05: la alineación de la cabeza de Bolívar deja la cara del FBX del lado del frente de la pieza (antes: 163° = en la nuca)."""
    rep = aligned.report
    assert rep["frente_ok"] is True and rep["frente"]["angulo_deg"] < 30 and rep["yaw180"]["girada"] is True
    assert rep["ok"] and not rep["ambigua"]
    assert _angle(front_hint(NECK, None, pkg).angle(aligned.matrix)[1], FRONT) < 30


def test_nose_ends_up_on_the_front_side(pkg, aligned):
    """§7 del plan: la punta de la nariz del FBX (mayor z de la cabeza) queda hacia +Y de la pieza, no a −Y (antes: y = −10,8 mm)."""
    V = pkg.vertices.astype(float)
    head = V[V[:, 1] > HEAD_MIN_Y]
    nose = head[np.argmax(head[:, 2])]
    assert aligned.apply(nose)[0, 1] > aligned.apply(head.mean(0))[0, 1]


def test_without_skin_role_a_flat_decision_is_reported(pkg, piece):
    """Sin pista de cara (sin rol de piel) se decide por el calce y se informa el margen de la pose opuesta."""
    fr = FigureFrame.from_cut_piece_similarity(pkg.vertices.astype(float), piece, source_faces=pkg.faces, front_hint=front_hint(NECK, piece, None))
    assert fr.report["frente_ok"] is None and "margen_mm" in fr.report["yaw180"] and isinstance(fr.report["ambigua"], bool)


def test_o07_gate_refuses_to_warp_the_saved_pose_of_the_real_head(pkg, piece):
    """O07: con la pose guardada (cara hacia atrás) y el frente sin verificar, el ajuste no rígido se frena: pide mover el FBX 0,9 mm de media
    (> media arista, 0,35 mm) y hasta 4,55 mm en una cabeza de 20 mm. Con el frente verificado (`front_ok=True`) el ajuste se aplica."""
    from afm import colorwarp as cw

    out, rep = cw.warp_package(pkg, SAVED, piece)
    assert out is pkg and rep["aplicada"] is False and rep["pose_sospechosa"] is True
    assert rep["desplazamiento_medio_mm"] > 0.5 * 0.70 and rep["desplazamiento_max_mm"] > 4.0
