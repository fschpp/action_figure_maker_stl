"""T42 · Catalogar una pieza con roles de color (`add_piece(color_roles=)`, `verify`, `color_file`, papelera y CLI `afm catalogo color`).

Sobre el catálogo de ejemplo y FBX sintéticos (sin figuras reales). El caso de la pieza recortada de una figura usa el muñeco."""
import json
import os
import shutil

import numpy as np
import pytest
import trimesh
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

from afm import cli, parts as P
from afm.catalog import Catalog, CatalogError
from afm.colorroles import RolesPackage, build_package, propose
from afm.frame import FigureFrame
from afm.palette import Palette, Role
from afm.texmesh import TexturedMesh
from color_helpers import doll_textured, png_bytes, write_textured_fbx

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
HEAD_ID = "cabeza-muneco-01"
TOP, BOTTOM = (200, 30, 30), (30, 60, 200)


def textured_from(mesh, M=None) -> TexturedMesh:
    """FBX sintético de `mesh`: rojo en la mitad alta, azul en la baja; con `M` (4×4) los vértices van a otro marco."""
    V = np.asarray(mesh.vertices, float)
    F = np.asarray(mesh.faces, np.int32)
    zc = V[F][:, :, 2].mean(1)
    mid = (V[:, 2].min() + V[:, 2].max()) / 2
    img = np.zeros((16, 16, 3), np.uint8)
    img[:8] = TOP
    img[8:] = BOTTOM
    uv = np.where((zc > mid)[:, None], [0.5, 0.75], [0.5, 0.25]).astype(np.float32)
    if M is not None:
        V = V @ np.asarray(M, float)[:3, :3].T + np.asarray(M, float)[:3, 3]
    return TexturedMesh(V, F, np.repeat(uv[:, None, :], 3, axis=1), {"base_color": png_bytes(img)}, {"avisos": []})


def odd_frame():
    """Escala ×1,3, giro de 30° sobre Z y traslado: el FBX no está en el marco del STL."""
    M = np.eye(4)
    M[:3, :3] = 1.3 * Rotation.from_euler("z", 30, degrees=True).as_matrix()
    M[:3, 3] = [7.0, -4.0, 11.0]
    return M


def yup_frame():
    """FBX de la figura entera tal como sale de un DCC con Y arriba: giro de −90° sobre X, escala ×1,3 y traslado. (Un giro de yaw libre no lo
    resuelve `from_similarity` en el muñeco, que es casi simétrico: no es lo que se prueba acá.)"""
    M = np.eye(4)
    M[:3, :3] = 1.3 * Rotation.from_euler("x", -90, degrees=True).as_matrix()
    M[:3, 3] = [7.0, -4.0, 11.0]
    return M


def two_color_palette(extra=()):
    return Palette([Role("rojo", "Rojo", "#C81E1E", ["#C81E1E"]), Role("azul", "Azul", "#1E3CC8", ["#1E3CC8"])] + [Role(r, r, "#808080") for r in extra])


def package_of(tm, pal) -> RolesPackage:
    """Paquete con un rol por color de `tm` (rojo/azul por cercanía del centro del cluster)."""
    prop = propose(tm, pal, k=10)
    assign = {}
    for i, c in enumerate(prop.centers_rgb):
        assign[i] = "rojo" if c[0] > c[2] else "azul"
    return build_package(tm, prop, assign, origen={"nombre": "origen.fbx", "sha256": "ab" * 32})


@pytest.fixture()
def line(tmp_path):
    """Copia del catálogo de ejemplo, con una paleta rojo/azul."""
    d = str(tmp_path / "linea")
    shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    cat = Catalog.open(d)
    cat.set_palette(two_color_palette())
    return d


@pytest.fixture(scope="module")
def head(tmp_path_factory):
    """Cabeza del catálogo de ejemplo + su FBX sintético (en otro marco) + el paquete de roles de ese FBX."""
    cat = Catalog.open(ROOT)
    meta, mesh = cat.load(HEAD_ID)
    tm = textured_from(mesh, odd_frame())
    fbx = write_textured_fbx(tmp_path_factory.mktemp("fbx") / "cabeza.fbx", tm)
    return meta, mesh, fbx, package_of(tm, two_color_palette())


def new_meta(meta):
    return P.PieceMeta.from_dict(meta.to_dict())


def listing(root):
    return sorted(os.listdir(os.path.join(root, "piezas", "cabeza")))


def test_piece_ficha_without_color_unchanged(line):
    """Una pieza sin color no gana ningún campo, ni al guardarla de nuevo ni al abrir el catálogo."""
    cat = Catalog.open(line)
    before = json.load(open(os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@1.pieza.json")))
    meta, mesh = cat.load(HEAD_ID)
    v2, _ = cat.add_piece(mesh, new_meta(meta), new_version=True)
    after = json.load(open(os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@2.pieza.json")))
    assert "color" not in before and "color" not in after and set(after) == set(before) and v2.color is None
    assert not [p for p in Catalog.open(ROOT).verify().problems if "color" in p]


def test_add_piece_with_roles_writes_npz_and_ficha(line, head):
    meta, mesh, _fbx, pkg = head
    cat = Catalog.open(line)
    saved, _w = cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg)
    npz = os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@2.color.npz")
    assert os.path.isfile(npz) and not os.path.exists(os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@2.fbx"))
    c = Catalog.open(line).meta(f"{HEAD_ID}@2").color
    assert c == saved.color
    assert c["modo"] == "roles" and c["fuente"] == f"{HEAD_ID}@2.color.npz" and c["sha256"] == P.sha256_file(npz)
    assert sorted(c["roles"]) == ["azul", "rojo"] and np.asarray(c["similitud"]).shape == (4, 4)
    assert c["origen"] == {"nombre": "origen.fbx", "sha256": "ab" * 32} and c["estado"] == "alineada" and c["alineacion"]["ok"]
    back = RolesPackage.load(cat.color_file(f"{HEAD_ID}@2"))
    assert np.array_equal(back.labels, pkg.labels) and back.roles == pkg.roles
    A = np.asarray(c["similitud"], float)                                        # la similitud recupera el marco raro (escala ×1,3)
    assert abs(np.cbrt(abs(np.linalg.det(A[:3, :3]))) * 1.3 - 1.0) < 0.01
    assert not [p for p in cat.verify().problems if "color" in p or "rol" in p]
    assert cat.palette_usage("rojo") == [f"{HEAD_ID}@2"]
    assert os.path.getsize(npz) < 1_000_000                                      # el plan pide < 1 MB por pieza (con Güemes real: AFM_SLOW)


def test_roles_missing_from_palette_writes_nothing(line, head):
    meta, mesh, _fbx, pkg = head
    cat = Catalog.open(line)
    cat.set_palette(Palette([Role("rojo", "Rojo", "#C81E1E")]))                  # falta «azul»
    before = listing(line)
    with pytest.raises(CatalogError, match="azul"):
        cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg)
    assert listing(line) == before and cat.versions(HEAD_ID) == [1]
    with pytest.raises(CatalogError, match="la paleta de la línea no tiene"):      # con la similitud dada no se alinea nada: el chequeo es el de la paleta
        cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg, similarity=np.eye(4))
    assert listing(line) == before
    os.remove(os.path.join(line, "paleta.json"))                                 # sin paleta: tampoco se escribe
    with pytest.raises(CatalogError, match="no tiene paleta"):
        cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg)
    assert listing(line) == before


def test_roles_with_fbx_at_the_same_time_or_unusable_package_write_nothing(line, head):
    meta, mesh, fbx, pkg = head
    cat = Catalog.open(line)
    before = listing(line)
    with pytest.raises(CatalogError, match="no los dos"):
        cat.add_piece(mesh, new_meta(meta), new_version=True, color_fbx=fbx, color_roles=pkg)
    bad = RolesPackage(pkg.vertices, pkg.faces, pkg.corner_uv, np.full_like(pkg.labels, 255), list(pkg.roles), {})
    with pytest.raises(CatalogError, match="ningún rol"):
        cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=bad)
    with pytest.raises(CatalogError, match="4×4"):
        cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg, similarity=np.zeros((4, 4)))
    assert listing(line) == before


def test_verify_detects_modified_or_missing_npz(line, head):
    meta, mesh, _fbx, pkg = head
    cat = Catalog.open(line)
    cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg)
    path = cat.color_file(f"{HEAD_ID}@2")
    with open(path, "ab") as f:
        f.write(b"x")
    with pytest.raises(CatalogError, match="hash"):
        cat.color_file(f"{HEAD_ID}@2")
    assert any("paquete de roles" in p and "hash" in p for p in cat.verify().problems)
    os.remove(path)
    assert any("falta el paquete de roles" in p for p in cat.verify().problems)


def test_delete_and_restore_move_the_npz_with_the_piece(line, head):
    meta, mesh, _fbx, pkg = head
    cat = Catalog.open(line)
    cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg)
    npz = os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@2.color.npz")
    digest = P.sha256_file(npz)
    res = cat.delete_pieces([f"{HEAD_ID}@2"])
    assert not res.errors and not os.path.exists(npz)
    assert os.path.isfile(os.path.join(res.trash, "piezas", "cabeza", f"{HEAD_ID}@2.color.npz"))
    cat.restore_deleted()
    assert os.path.isfile(npz) and P.sha256_file(npz) == digest
    assert cat.color_file(f"{HEAD_ID}@2") == npz and not [p for p in cat.verify().problems if "color" in p]


def test_new_version_does_not_inherit_roles(line, head):
    meta, mesh, _fbx, pkg = head
    cat = Catalog.open(line)
    v2, _ = cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg)
    v3, warns = cat.add_piece(mesh, new_meta(v2), new_version=True)
    assert v3.color is None and any("color" in w for w in warns)
    assert cat.color_file(f"{HEAD_ID}@3") is None and not os.path.exists(os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@3.color.npz"))
    assert cat.palette_usage("rojo") == [f"{HEAD_ID}@2"]                         # solo la versión que lo tiene


def test_legacy_rgb_piece_still_loads_and_verifies(line, head):
    meta, mesh, fbx, _pkg = head
    cat = Catalog.open(line)
    v2, _ = cat.add_piece(mesh, new_meta(meta), new_version=True, color_fbx=fbx)
    assert "modo" not in v2.color                                                # sin `modo` = rgb: la ficha de T35 queda como estaba
    assert P.color_mode(v2.color) == "rgb" and v2.color["fuente"].endswith(".fbx")
    assert not [p for p in cat.verify().problems if "color" in p]
    path = cat.color_file(f"{HEAD_ID}@2")
    with open(path, "ab") as f:
        f.write(b"x")
    assert any("FBX de color" in p for p in cat.verify().problems)
    assert P.color_problems({"modo": "otro", "fuente": "a", "sha256": "b", "similitud": np.eye(4).tolist()})
    assert P.color_problems({"modo": "roles", "fuente": "a", "sha256": "b", "similitud": np.eye(4).tolist()})                 # falta «roles»
    assert not P.color_problems({"modo": "roles", "fuente": "a", "sha256": "b", "similitud": np.eye(4).tolist(), "roles": ["piel"]})


def test_color_file_works_for_both_modes(line, head):
    meta, mesh, fbx, pkg = head
    cat = Catalog.open(line)
    cat.add_piece(mesh, new_meta(meta), new_version=True, color_fbx=fbx)
    cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg)
    assert cat.color_file(f"{HEAD_ID}@1") is None
    assert cat.color_file(f"{HEAD_ID}@2").endswith(f"{HEAD_ID}@2.fbx")
    assert cat.color_file(f"{HEAD_ID}@3").endswith(f"{HEAD_ID}@3.color.npz")


def test_cut_piece_uses_figure_similarity_not_moments(line):
    """La mitad alta del muñeco (un trozo de una figura) con el FBX de la figura entera: la alineación por momentos no calza, la matriz FBX → figura sí."""
    from afm.testfigure import make_doll

    doll = make_doll()
    fbx_tm = doll_textured().transformed(yup_frame())                            # el FBX de la figura entera, en otro marco
    figure_frame = FigureFrame.from_similarity(fbx_tm, doll)
    assert figure_frame.report["ok"]                                           # la figura entera sí calza con su matriz
    z = float(np.asarray(doll.vertices)[:, 2].mean())
    cut = doll.slice_plane((0, 0, z), (0, 0, 1), cap=True)
    assert cut.is_watertight
    pal = Palette([Role(f"r{i}", f"r{i}", "#%02X%02X%02X" % (25 * i, 200 - 20 * i, 30 + 10 * i)) for i in range(10)])
    prop = propose(fbx_tm, pal, k=10)
    pkg = build_package(fbx_tm, prop, {i: f"r{i}" for i in range(prop.k)})
    cat = Catalog.open(line)
    cat.set_palette(pal)
    meta, _ = cat.load(HEAD_ID)
    before = listing(line)
    with pytest.raises(CatalogError, match="no coincide con la pieza"):         # por momentos: un trozo no calza con la figura entera
        cat.add_piece(cut, new_meta(meta), new_version=True, color_roles=pkg)
    assert listing(line) == before
    saved, _ = cat.add_piece(cut, new_meta(meta), new_version=True, color_roles=pkg, similarity=figure_frame.matrix)
    A = np.asarray(saved.color["similitud"], float)
    assert np.allclose(A, figure_frame.matrix) and saved.color["alineacion"] == {"origen": "figura"}      # se guarda la de la figura, no otra
    # distancia exacta de los vértices del trozo a la superficie del FBX llevado con cada matriz (la tapa del corte no existe en el FBX: se mira la mediana)
    def median_distance(M):
        M = np.asarray(M, float)
        surf = trimesh.Trimesh(fbx_tm.vertices @ M[:3, :3].T + M[:3, 3], fbx_tm.faces, process=False)
        return float(np.median(trimesh.proximity.closest_point(surf, np.asarray(cut.vertices))[1]))

    from afm.colorpiece import align_fbx_to_piece
    by_moments = align_fbx_to_piece(fbx_tm, cut, require_ok=False).matrix
    assert median_distance(A) < 0.01 and median_distance(by_moments) > 1.0      # medido: 0,000 mm contra 1,65 mm


def test_cli_color_saves_a_new_version_with_roles(line, head, tmp_path, capsys):
    meta, mesh, fbx, _pkg = head
    pal = Catalog.open(line).palette()
    from afm.fbxio import load_textured

    prop = propose(load_textured(fbx), pal, k=10)
    assign = {str(i): ("rojo" if c[0] > c[2] else "azul") for i, c in enumerate(prop.centers_rgb)}
    asg = tmp_path / "asignacion.json"
    asg.write_text(json.dumps(assign))
    assert cli.main(["catalogo", "color", line, f"{HEAD_ID}@1", "--fbx", fbx, "--roles", str(asg)]) == 0
    out = capsys.readouterr().out
    cat = Catalog.open(line)
    assert f"{HEAD_ID}@2" in out and cat.meta(f"{HEAD_ID}@2").color["modo"] == "roles" and cat.meta(f"{HEAD_ID}@1").color is None
    assert cat.meta(f"{HEAD_ID}@2").color["origen"]["nombre"] == "cabeza.fbx"
    assert not [p for p in cat.verify().problems if "color" in p or "rol" in p]
    # asignación incompleta: error claro y nada escrito
    asg.write_text(json.dumps({"0": "rojo"}))
    before = listing(line)
    assert cli.main(["catalogo", "color", line, f"{HEAD_ID}@1", "--fbx", fbx, "--roles", str(asg)]) == 2
    assert "faltan clusters" in capsys.readouterr().err and listing(line) == before


def test_armed_figure_skips_a_roles_piece_until_t43(line, head):
    """Hasta T43 una pieza con roles no rompe el armado: queda sin color propio y el informe lo dice."""
    from afm.colorpiece import catalog_color_sources

    meta, mesh, _fbx, pkg = head
    cat = Catalog.open(line)
    v2, _ = cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg)

    class _Choice:
        pass

    class _Placed:
        pass

    pp = _Placed()
    pp.choice = _Choice()
    pp.choice.meta = v2
    pp.T = np.eye(4)
    sources, info = catalog_color_sources({"cabeza": pp}, cat)
    assert sources == {} and "cabeza" in info["omitidas"] and "roles" in info["omitidas"]["cabeza"]


# ------------------------------------------------------------------ figuras reales (AFM_SLOW)
EX = os.path.join(os.path.dirname(__file__), "..", "examples")
slow = pytest.mark.skipif(not os.environ.get("AFM_SLOW"), reason="figura real: AFM_SLOW=1")


@slow
def test_real_guemes_cut_pieces_use_the_figure_matrix_and_the_npz_is_small(tmp_path):
    """Medición de §5.7 con Güemes real: torso, brazo y pierna extraídos del STL con el FBX de la figura entera.

    Con la matriz FBX → figura (modo B + giro de `front`) la mediana de las distancias de la pieza al FBX es < 0,1 mm; la alineación por momentos
    de T35 queda a > 0,5 mm (medido: 0,05 contra 0,8–1,5 mm). Y el paquete de una pieza pesa < 1 MB."""
    import sys

    from afm import extract, meshio
    from afm.fbxio import load_textured

    sys.path.insert(0, EX)
    import guemes

    proj = guemes.proj
    stl = os.path.join(EX, "guemes_segmentado_lq_fixed_resized.stl")
    fbx = os.path.join(EX, "guemes_segmentado_lq_texturas.fbx")
    mesh, _rep = meshio.load_figure(stl, proj.params.ref_height, proj.crown_z)
    body, _ = extract.load_body(stl, proj)
    tm = load_textured(fbx)
    figure = FigureFrame.from_similarity(tm, mesh).then(FigureFrame.engine_post(proj.front, proj.symmetry))
    assert figure.report["ok"], figure.report
    pieces = extract.extract_all(body, proj, prefix="guemes").pieces
    assert {e.meta.tipo for e in pieces} >= {"torso", "brazo", "pierna"}

    pal = Palette([Role(f"r{i}", f"r{i}", "#%02X%02X%02X" % (20 * i, 200 - 15 * i, 40 + 12 * i)) for i in range(6)])
    prop = propose(tm, None, k=10)
    pkg = build_package(tm, prop, {i: f"r{i % 6}" for i in range(prop.k)}, origen={"nombre": os.path.basename(fbx), "sha256": ""})
    root = str(tmp_path / "linea")
    cat = Catalog.create(root, "guemes", 114.0)
    cat.set_palette(pal)
    V = np.asarray(tm.vertices, float) @ figure.matrix[:3, :3].T + figure.matrix[:3, 3]
    surf = trimesh.Trimesh(V, tm.faces, process=False)
    pts, _ = trimesh.sample.sample_surface(surf, 800_000, seed=0)
    tree = cKDTree(pts)
    for e in pieces:
        if e.meta.tipo not in ("torso", "brazo", "pierna"):
            continue
        sample, _ = trimesh.sample.sample_surface(e.mesh, 20_000, seed=1)
        assert float(np.median(tree.query(sample)[0])) < 0.1, e.meta.tipo                  # la matriz de la figura calza con la pieza recortada
        saved, _ = cat.add_piece(e.mesh, e.meta, color_roles=pkg, similarity=figure.matrix)
        assert os.path.getsize(cat.color_file(saved.ref)) < 1_000_000, saved.ref
    assert not [p for p in cat.verify().problems if "color" in p or "rol" in p]
