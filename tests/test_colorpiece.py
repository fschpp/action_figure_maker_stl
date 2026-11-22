"""T32 · El FBX de una pieza alineado con su STL y llevado al marco del huésped (`colorpiece`, `FigureFrame.from_piece_similarity`,
`TexturedMesh.transformed`)."""
import numpy as np
import pytest
import trimesh
from scipy.spatial.transform import Rotation

from afm.colorexport import Colorizer
from afm.colorpiece import PieceColorError, align_fbx_to_piece, fbx_in_host_frame, piece_color_source
from afm.colorsrc import ColorSource
from afm.frame import FigureFrame
from afm.texmesh import TexturedMesh
from color_helpers import REGIONS, doll_params, doll_pieces, doll_textured, piece_trimeshes, region_of


def _sim(R, s=1.0, t=(0.0, 0.0, 0.0)):
    M = np.eye(4)
    M[:3, :3] = s * np.asarray(R, float)
    M[:3, 3] = t
    return M


def _rz(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1.0]])


def _sub(tm, sel):
    F = tm.faces[sel]
    u, inv = np.unique(F, return_inverse=True)
    return TexturedMesh(tm.vertices[u], inv.reshape(-1, 3).astype(np.int32), tm.corner_uv[sel], tm.images, tm.info)


def _region(name):
    """La región `name` del muñeco texturizado como una malla propia (el «FBX de la pieza»)."""
    tm = doll_textured()
    cen = tm.vertices[tm.faces].mean(1)
    return _sub(tm, np.array([region_of(c) == name for c in cen]))


def _stl(tm):
    return trimesh.Trimesh(tm.vertices.copy(), tm.faces.copy(), process=False)


@pytest.fixture(scope="module")
def params():
    return doll_params()


@pytest.fixture(scope="module")
def res(params):
    return doll_pieces(params)


@pytest.fixture(scope="module")
def meshes(res):
    return piece_trimeshes(res)


# ------------------------------------------------------------------ TexturedMesh.transformed
def test_transformed_moves_vertices_and_shares_texture():
    tm = _region("brazo_izq")
    M = _sim(_rz(30), 1.3, (7, -4, 11))
    out = tm.transformed(M)
    assert np.allclose(out.vertices, tm.vertices @ M[:3, :3].T + M[:3, 3])
    assert out.faces is tm.faces and out.corner_uv is tm.corner_uv and out.images is tm.images
    assert np.array_equal(tm.vertices, _region("brazo_izq").vertices)               # la original no se toca


def test_transformed_mirror_keeps_normals_outward_and_colors():
    tm = _region("cabeza")
    out = tm.transformed(np.diag([-1.0, 1.0, 1.0, 1.0]))
    assert np.array_equal(out.faces, tm.faces[:, ::-1]) and np.array_equal(out.corner_uv, tm.corner_uv[:, ::-1])
    mesh = out.to_trimesh()
    mesh.fix_normals()
    assert mesh.volume > 0 and np.allclose(mesh.volume, _stl(tm).volume, rtol=1e-6)
    f = np.arange(len(tm.faces))
    b = np.full((len(f), 3), 1 / 3)
    c0 = tm.sample(f, b)
    c1 = out.sample(f, np.array([1 / 3, 1 / 3, 1 / 3]) * np.ones((len(f), 3)))
    assert np.array_equal(c0, c1)                                                   # cada cara conserva su color


# ------------------------------------------------------------------ FigureFrame.from_piece_similarity
@pytest.mark.parametrize("name", ["brazo_izq", "pierna_izq", "torso"])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_piece_similarity_recovers_any_rotation(name, seed):
    piece = _region(name)
    G = _sim(Rotation.random(random_state=seed).as_matrix(), 1.3, (7, -4, 11))
    fbx = piece.transformed(G)
    f = FigureFrame.from_piece_similarity(fbx.vertices, _stl(piece), source_faces=fbx.faces)
    assert f.report["ok"] and f.report["media_mm"] < 0.01
    assert np.abs(f.apply(fbx.vertices) - piece.vertices).max() < 0.01
    assert f.scale == pytest.approx(1 / 1.3, rel=1e-4) and abs(f.det() - 1.0) < 1e-9


def test_piece_similarity_tolerates_a_cut_piece():
    """El STL de la pieza suele ser el FBX con la base recortada: sigue alineando (ICP)."""
    piece = _region("pierna_izq")
    stl_full = _stl(piece)
    keep = stl_full.triangles_center[:, 2] > stl_full.bounds[0][2] + 0.15 * stl_full.extents[2]
    cut = trimesh.Trimesh(stl_full.vertices, stl_full.faces[keep], process=True)
    G = _sim(Rotation.random(random_state=5).as_matrix(), 0.8, (-3, 9, 2))
    fbx = piece.transformed(G)
    f = FigureFrame.from_piece_similarity(fbx.vertices, cut, source_faces=fbx.faces)
    assert f.report["media_mm"] < 0.1
    assert np.abs(f.apply(fbx.vertices[:5]) - piece.vertices[:5]).max() < 0.2


def test_piece_similarity_rejects_other_shape():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=6.0)
    arm = _region("brazo_izq")
    f = FigureFrame.from_piece_similarity(arm.vertices, sphere, source_faces=arm.faces)
    assert not f.report["ok"]


# ------------------------------------------------------------------ align_fbx_to_piece
def test_align_raises_on_mismatch_and_reports_without_require_ok():
    arm = _region("brazo_izq")
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=6.0)
    with pytest.raises(PieceColorError, match="no coincide"):
        align_fbx_to_piece(arm, sphere)
    fr = align_fbx_to_piece(arm, sphere, require_ok=False)
    assert fr.report["ok"] is False


def test_align_needs_a_color_texture():
    arm = _region("brazo_izq")
    bare = TexturedMesh(arm.vertices, arm.faces, arm.corner_uv, {}, arm.info)
    with pytest.raises(PieceColorError, match="textura de color"):
        align_fbx_to_piece(bare, _stl(arm))
    with pytest.raises(PieceColorError, match="textura de color"):
        piece_color_source(bare, _stl(arm), similarity=np.eye(4))


# ------------------------------------------------------------------ fbx_in_host_frame y piece_color_source
def test_fbx_in_host_frame_composes_both_stages():
    tm = _region("brazo_izq")
    A = _sim(_rz(20), 0.9, (1, 2, 3))
    T = _sim(_rz(-50), 1.1, (10, 0, -5))
    out = fbx_in_host_frame(tm, A, T)
    assert np.allclose(out.vertices, tm.transformed(T @ A).vertices)
    assert np.allclose(fbx_in_host_frame(tm, A).vertices, tm.transformed(A).vertices)
    with pytest.raises(PieceColorError, match="singular"):
        fbx_in_host_frame(tm, np.zeros((4, 4)))
    with pytest.raises(PieceColorError, match="4×4"):
        fbx_in_host_frame(tm, np.eye(3))


def test_piece_color_source_aligns_and_reports():
    piece = _region("cabeza")
    G = _sim(_rz(30), 1.3, (7, -4, 11))                      # el FBX viene en otro marco, más grande y girado
    fbx = piece.transformed(G)
    K = _sim(_rz(-40), 1.0, (5, 6, 7))                       # STL de la pieza → huésped
    stl_in_piece_frame = piece.transformed(np.linalg.inv(K))
    pc = piece_color_source(fbx, _stl(stl_in_piece_frame), K)
    assert np.allclose(pc.source.vertices, piece.vertices, atol=0.01)                # la fuente quedó en el marco del huésped
    assert np.allclose(pc.matrix, K @ pc.similarity) and np.allclose(pc.to_host, K)
    assert pc.report["alineacion"]["ok"] and pc.report["escala"] == pytest.approx(1 / 1.3, rel=1e-3)
    assert pc.report["espejo"] is False
    again = piece_color_source(fbx, None, K, similarity=pc.similarity)               # la similitud guardada no recalcula nada
    assert again.report["alineacion"] == {"origen": "guardada"}
    assert np.allclose(again.source.vertices, pc.source.vertices)


# ------------------------------------------------------------------ aceptación: el registro encuentra la pieza
def test_registration_finds_the_piece_in_a_scaled_rotated_fbx(res, params, meshes):
    """Plan T32: FBX del muñeco ×1,3, girado 30° sobre Z y trasladado; con su similitud la pieza se registra sin girar y con el MISMO
    `pct_origen` que con la fuente de la figura (≈ 69 % en el brazo: pasador y cavidad no tienen origen). El 85 % que el plan proponía
    no se alcanza ni sin transformar nada, así que el umbral es la referencia sin transformar, con ±0,5 puntos."""
    G = _sim(_rz(30), 1.3, (7, -4, 11))
    fbx = doll_textured().transformed(G)
    K = _sim(_rz(-25), 1.0, (3, -8, 4))                      # la pieza «catalogada» tiene su propio marco
    pc = piece_color_source(fbx, None, K, similarity=np.linalg.inv(K) @ np.linalg.inv(G), spacing_mm=0.1)
    assert np.allclose(pc.source.vertices, doll_textured().vertices, atol=1e-6)
    plain = Colorizer(ColorSource(doll_textured(), spacing_mm=0.1), params, texel_mm=0.3)
    ref = plain("brazo_izq", meshes["brazo_izq"], res)
    cz = Colorizer(ColorSource(doll_textured(), spacing_mm=0.1), params, texel_mm=0.3, sources={"brazo": pc.source})
    info = cz("brazo_izq", meshes["brazo_izq"], res)
    assert info["estado"] in ("identidad", "semilla") and info["fuente"] == "pieza"
    assert info["estado"] == ref["estado"] and info["pct_origen"] == pytest.approx(ref["pct_origen"], abs=0.5)
    assert info["pct_origen"] >= 60.0
    bk, bk0 = cz.pieces["brazo_izq"][1], plain.pieces["brazo_izq"][1]
    assert bk.atlas.shape == bk0.atlas.shape and np.array_equal(bk.mask, bk0.mask)
    assert np.abs(bk.atlas[bk.mask].astype(int) - bk0.atlas[bk0.mask].astype(int)).mean() < 1.0      # mismo color que con la fuente de la figura
    assert (np.abs(bk.atlas[bk.mask].astype(int) - np.array(REGIONS["brazo_izq"])).max(axis=1) <= 10).mean() > 0.5   # y es mayormente el rojo del brazo


# ------------------------------------------------------------------ pieza recortada (cabeza cortada en la base, con tapa y hueca)
def _bumpy_head(subdivisions=4):
    """Cabeza sintética asimétrica (esfera con bultos distintos) como `TexturedMesh` con textura de color."""
    sph = trimesh.creation.icosphere(subdivisions=subdivisions, radius=15.0)
    v = sph.vertices.copy()
    v[:, 0] *= 0.9
    v[:, 1] *= 1.05
    for c, r in [((15, 2, 2), 6), ((-14, -3, 4), 4), ((0, 14, 6), 5), ((5, -14, -4), 3), ((-6, 9, 12), 4), ((9, 9, -3), 2.5)]:
        d = np.linalg.norm(v - np.array(c), axis=1)
        v += (np.exp(-(d / r) ** 2)[:, None] * np.array(c, float) / np.linalg.norm(c)) * (2.5 + 0.7 * r)
    base = doll_textured()
    return TexturedMesh(v, sph.faces.astype(np.int32), np.zeros((len(sph.faces), 3, 2), np.float32), base.images, base.info)


@pytest.mark.parametrize("hollow", [False, True])
def test_align_accepts_a_capped_cut_head_even_if_hollow(hollow):
    """La cabeza del catálogo es el FBX cortado en la base con su tapa (y quizá hueco por dentro): la alineación estricta no calza, la recortada sí."""
    head = _bumpy_head()
    full = _stl(head)
    piece = trimesh.intersections.slice_mesh_plane(full, [0, 0, 1], [0, 0, -6], cap=True)
    if hollow:
        piece = trimesh.util.concatenate([piece, trimesh.Trimesh(piece.vertices * 0.8, piece.faces[:, ::-1], process=False)])
    G = _sim(Rotation.from_euler("xyz", [20, 30, 40], degrees=True).as_matrix(), 0.9, (3, -4, 5))
    piece.apply_transform(G)
    strict = FigureFrame.from_piece_similarity(head.vertices, piece, source_faces=head.faces)
    assert not strict.report["ok"]
    fr = align_fbx_to_piece(head, piece)
    assert fr.report["ok"] and fr.report["recortada"] is True and fr.report["cubierta"] >= 0.6
    assert np.abs(fr.apply(head.vertices[:300]) - trimesh.transform_points(head.vertices[:300], G)).max() < 0.3
    with pytest.raises(PieceColorError, match="no coincide"):
        align_fbx_to_piece(head, piece, cut=False)


def test_align_cut_fallback_still_rejects_other_shape():
    arm = _region("brazo_izq")
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=6.0)
    with pytest.raises(PieceColorError, match="no coincide"):
        align_fbx_to_piece(arm, sphere)


def test_align_accepts_a_cut_head_with_a_coarser_mesh_than_the_fbx():
    """La cabeza «de más calidad» del catálogo es otra malla (más gruesa) que la del FBX: misma forma, vértices en otros lugares. Con la
    tolerancia fija de 0,1 mm no calzaba; la de la pieza recortada sigue la arista de la pieza."""
    head = _bumpy_head(4)                                   # el FBX
    coarse = _stl(_bumpy_head(2))                           # la pieza: la misma forma con otra malla, bastante más gruesa
    piece = trimesh.intersections.slice_mesh_plane(coarse, [0, 0, 1], [0, 0, -6], cap=True)
    G = _sim(Rotation.from_euler("xyz", [-15, 25, 70], degrees=True).as_matrix(), 0.7, (2, 3, -4))
    piece.apply_transform(G)
    fr = align_fbx_to_piece(head, piece)
    assert fr.report["ok"] and fr.report["recortada"] is True
    assert fr.report["tolerancia_mm"] > 0.1 and fr.report["media_mm"] <= fr.report["tolerancia_mm"]
    assert np.abs(fr.apply(head.vertices[:300]) - trimesh.transform_points(head.vertices[:300], G)).max() < 1.0
