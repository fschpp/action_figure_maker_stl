"""T34–T36 · Color propio de la cabeza importada (T34), de las piezas de catálogo (T35) y del armado desde el catálogo (T36).

Todo sobre el muñeco y sus piezas del catálogo de ejemplo, con FBX sintéticos (sin figuras reales)."""
import json
import os
import shutil

import numpy as np
import pytest
import trimesh
from scipy.spatial.transform import Rotation

from afm import cli, parts as P
from afm.catalog import Catalog, CatalogError
from afm.head import HeadSpec
from afm.planes import Plane
from afm.recipe import Recipe
from afm.texmesh import TexturedMesh
from color_helpers import png_bytes, write_textured_fbx

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECETA = os.path.join(ROOT, "recetas", "muneco.receta.json")
HEAD_ID = "cabeza-muneco-01"

TOP, BOTTOM = (200, 30, 30), (30, 60, 200)       # arriba rojo, abajo azul: se ve si el color llegó a su lugar


def textured_from(mesh, M=None) -> TexturedMesh:
    """FBX sintético de `mesh` (trimesh): rojo en la mitad alta, azul en la baja; con `M` (4×4) los vértices se llevan a otro marco
    (escala, giro y traslado), como un FBX que no está en el marco del STL."""
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


def other_fbx(path):
    """FBX de una forma que NO es la de ninguna cabeza (barra larga y fina): no puede calzar con la pieza."""
    return write_textured_fbx(path, textured_from(trimesh.creation.box((6, 6, 60))))


def odd_frame():
    """Escala ×1,3, giro de 30° sobre Z y traslado: el FBX no está en el marco del STL."""
    M = np.eye(4)
    M[:3, :3] = 1.3 * Rotation.from_euler("z", 30, degrees=True).as_matrix()
    M[:3, 3] = [7.0, -4.0, 11.0]
    return M


def rgb_of(info_or_cz, name):
    unw, bk, reg, info = info_or_cz.pieces[name]
    return bk.atlas[bk.mask].reshape(-1, 3), info


@pytest.fixture(scope="module")
def line(tmp_path_factory):
    """Copia del catálogo de ejemplo (`afm armar` guarda compat.json: no ensuciar el del repo)."""
    d = str(tmp_path_factory.mktemp("linea") / "linea")
    shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    return d


@pytest.fixture(scope="module")
def head_fbx(tmp_path_factory, line):
    meta, mesh = Catalog.open(line).load(HEAD_ID)
    return write_textured_fbx(tmp_path_factory.mktemp("fbx") / "cabeza.fbx", textured_from(mesh, odd_frame())), meta, mesh


# ------------------------------------------------------------------ T34
def test_headspec_color_fields_round_trip_and_stay_out_of_old_json():
    plane = Plane.from_points((-5, -5, 80), (5, -5, 80), (0, 5, 80), "cabeza cuerpo")
    plain = HeadSpec(plane, replacement_path="c.stl")
    assert "replacement_color_path" not in plain.to_dict()                       # sin color propio el JSON no cambia
    colored = HeadSpec(plane, replacement_path="c.stl", replacement_color_path="c.fbx", replacement_color_sha256="ab")
    back = HeadSpec.from_dict(colored.to_dict())
    assert (back.replacement_color_path, back.replacement_color_sha256) == ("c.fbx", "ab")
    assert HeadSpec.from_dict(plain.to_dict()).replacement_color_path is None


@pytest.fixture(scope="module")
def loose_head(tmp_path_factory):
    """Muñeco construido con una cabeza importada (esfera de base plana) y el FBX de esa cabeza en otro marco."""
    import dataclasses

    from afm import pipeline as P_, solids as S
    from afm.project import demo_project
    from afm.testfigure import make_doll

    d = tmp_path_factory.mktemp("suelta")
    M = S.Manifold
    ball = M.sphere(15, 64)
    flat = S.intersection(ball, M.cube((100, 100, 100), center=True).translate((0, 0, 40)))      # base plana z = -10
    stl = str(d / "cabeza.stl")
    S.to_trimesh(flat).export(stl)
    mesh = trimesh.load(stl, force="mesh")
    fbx = write_textured_fbx(d / "cabeza.fbx", textured_from(mesh, odd_frame()))
    pr = demo_project()
    pr.head = dataclasses.replace(pr.head, replacement_path=stl, replacement_color_path=fbx)
    params = dataclasses.replace(pr.params, decimate=0)
    res = P_.build(S.to_manifold(make_doll()), pr.limbs, params, pr.symmetry, head=pr.head)
    return res, params, pr.head, S.to_printable_trimesh(res.pieces["cabeza"])


def test_loose_head_gets_the_color_of_its_own_fbx(loose_head):
    from afm.colorexport import Colorizer

    res, params, head, mesh = loose_head
    src, info = cli.head_color_sources(head, None, lambda *_: None)
    assert set(src) == {"cabeza"} and "omitido" not in info
    cz = Colorizer(None, params, sources=src)            # figura sin FBX propio: solo la cabeza lleva color
    rep = cz("cabeza", mesh, res)
    assert rep["fuente"] == "pieza" and rep["estado"] != "sin_origen" and rep["pct_origen"] >= 85, rep
    rgb, _ = rgb_of(cz, "cabeza")
    near = lambda c: (np.abs(rgb.astype(int) - c).sum(1) < 30).mean()
    assert near(TOP) > 0.2 and near(BOTTOM) > 0.2       # llegaron los dos colores de SU FBX, no el plano
    # otra pieza, sin fuente propia ni de figura: todo plano
    rep2 = cz("torso", trimesh.creation.box((10, 10, 10)), res)
    assert rep2["fuente"] == "plano" and rep2["estado"] == "sin_origen"


def test_head_fbx_that_does_not_fit_is_reported_and_the_head_stays_flat(loose_head, tmp_path):
    from afm.colorexport import Colorizer
    res, params, head, mesh = loose_head
    import dataclasses

    bad = dataclasses.replace(head, replacement_color_path=other_fbx(tmp_path / "otra.fbx"), replacement_color_sha256=None)
    src, _ = cli.head_color_sources(bad, None, lambda *_: None)
    cz = Colorizer(None, params, sources=src)
    rep = cz("cabeza", mesh, res)
    assert rep["fuente"] == "plano" and "aviso_fuente" in rep and "cabeza" in cz.source_errors


def test_head_fbx_missing_or_changed_is_a_warning_not_an_error(loose_head, tmp_path):
    import dataclasses

    _res, _params, head, _mesh = loose_head
    msgs = []
    gone = dataclasses.replace(head, replacement_color_path=str(tmp_path / "no_existe.fbx"))
    assert cli.head_color_sources(gone, None, msgs.append)[0] == {} and msgs
    changed = dataclasses.replace(head, replacement_color_sha256="0" * 64)
    assert cli.head_color_sources(changed, None, msgs.append)[0] == {}
    assert cli.head_color_sources(dataclasses.replace(head, replacement_color_path=None), None)[0] == {}


# ------------------------------------------------------------------ T35
def test_example_catalog_pieces_have_no_color_and_still_verify():
    cat = Catalog.open(ROOT)
    assert all(m.color is None for m in cat.list())
    assert not [p for p in cat.verify().problems if "color" in p]


def test_a_piece_cataloged_with_its_fbx_reloads_with_color(line, head_fbx):
    fbx, meta, mesh = head_fbx
    cat = Catalog.open(line)
    saved, _w = cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_fbx=fbx)
    assert saved.version == 2 and saved.color["estado"] == "alineada" and saved.color["fuente"] == f"{HEAD_ID}@2.fbx"
    again = Catalog.open(line).meta(f"{HEAD_ID}@2")
    assert again.color == saved.color and np.asarray(again.color["similitud"]).shape == (4, 4)
    assert again.color["alineacion"]["ok"] and again.color["sha256"] == P.sha256_file(cat.color_file(f"{HEAD_ID}@2"))
    # la similitud recupera el marco raro: A · M ≈ identidad en escala
    A = np.asarray(again.color["similitud"], float)
    assert abs(np.cbrt(abs(np.linalg.det(A[:3, :3]))) * 1.3 - 1.0) < 0.01
    assert cat.color_file(f"{HEAD_ID}@1") is None
    assert not [p for p in cat.verify().problems if "color" in p]


def test_verify_catches_a_modified_or_missing_color_fbx(tmp_path, line, head_fbx):
    fbx, meta, mesh = head_fbx
    root = str(tmp_path / "l")
    shutil.copytree(line, root)
    cat = Catalog.open(root)
    path = cat.color_file(f"{HEAD_ID}@2") if cat.versions(HEAD_ID)[-1] >= 2 else None
    if path is None:
        cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_fbx=fbx)
        path = cat.color_file(f"{HEAD_ID}@2")
    with open(path, "ab") as f:
        f.write(b"x")
    with pytest.raises(CatalogError, match="hash"):
        cat.color_file(f"{HEAD_ID}@2")
    assert any("FBX de color" in p for p in cat.verify().problems)
    os.remove(path)
    assert any("falta el FBX" in p for p in cat.verify().problems)


def test_an_fbx_that_does_not_fit_the_piece_writes_nothing(tmp_path, line):
    root = str(tmp_path / "l")
    shutil.copytree(line, root)
    cat = Catalog.open(root)
    before = sorted(os.listdir(os.path.join(root, "piezas", "cabeza")))
    meta, mesh = cat.load(HEAD_ID + "@1")
    with pytest.raises(CatalogError, match="color"):
        cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_fbx=other_fbx(tmp_path / "o.fbx"))
    assert sorted(os.listdir(os.path.join(root, "piezas", "cabeza"))) == before


def test_a_new_version_does_not_inherit_the_color(tmp_path, line, head_fbx):
    fbx, meta, mesh = head_fbx
    root = str(tmp_path / "l")
    shutil.copytree(line, root)
    cat = Catalog.open(root)
    v2, _ = cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_fbx=fbx)
    v3, warns = cat.add_piece(mesh, P.PieceMeta.from_dict(v2.to_dict()), new_version=True)
    assert v3.color is None and any("color" in w for w in warns)


def test_bad_color_block_is_a_format_problem():
    assert P.color_problems(None) == [] and P.color_problems({"fuente": "a", "sha256": "b", "similitud": np.eye(4).tolist()}) == []
    assert P.color_problems({"fuente": "a", "sha256": "b", "similitud": np.zeros((4, 4)).tolist()})
    assert P.color_problems({"fuente": "a"})


# ------------------------------------------------------------------ T36
@pytest.fixture(scope="module")
def armed(tmp_path_factory, line, head_fbx):
    fbx, meta, mesh = head_fbx
    cat = Catalog.open(line)
    if cat.versions(HEAD_ID)[-1] < 2:
        cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_fbx=fbx)
    r = json.load(open(RECETA))
    r["lugares"]["cabeza"] = f"{HEAD_ID}@2"
    rec = str(tmp_path_factory.mktemp("rec") / "muneco_color.receta.json")
    json.dump(r, open(rec, "w"))
    out = str(tmp_path_factory.mktemp("fig"))
    info = cli.run_armar(rec, out, "muneco", line, progress=lambda *_: None)
    return info, out


def test_armed_figure_colors_the_cataloged_head_and_leaves_the_rest_flat(armed):
    info, out = armed
    col = info["color"]
    assert col["piezas"]["cabeza"]["pieza"] == f"{HEAD_ID}@2" and not col["omitidas"]
    pieces = col["salida"]["piezas"]
    assert os.path.isdir(os.path.join(out, col["salida"]["carpeta"]))
    cab = next(v for k, v in pieces.items() if k == "cabeza")
    assert cab["fuente"] == "pieza" and cab["estado"] != "sin_origen" and cab["pct_origen"] >= 85, cab
    flat = [k for k, v in pieces.items() if k != "cabeza"]
    assert flat and all(pieces[k]["fuente"] == "plano" for k in flat)
    assert col["salida"]["sin_origen"] == sorted(flat)                              # marcadas en el informe
    assert os.path.exists(os.path.join(out, "muneco_ensamblada.glb"))              # la ventana de armado lo toma para «Textura»


def test_figure_without_colored_pieces_is_armed_exactly_as_before(tmp_path, line, capsys):
    out = str(tmp_path / "f")
    assert cli.main(["armar", line, RECETA, "--out", out]) == 0
    info = json.load(open(os.path.join(out, "muneco_informe.json")))
    assert "color" not in info and not os.path.exists(os.path.join(out, "piezas_color"))


def test_scaled_piece_is_left_without_color(tmp_path, line, armed):
    r = json.load(open(RECETA))
    r["lugares"]["cabeza"] = f"{HEAD_ID}@2"
    r["ajustes"]["escalas"] = {"cabeza": 1.1}
    rec = str(tmp_path / "r.json")
    json.dump(r, open(rec, "w"))
    msgs = []
    info = cli.run_armar(rec, str(tmp_path / "o"), "muneco", line, progress=msgs.append)
    assert "cabeza" in info.get("color", {}).get("omitidas", {}) or "color" not in info
    assert any("escalada" in m for m in msgs)
