"""O08 · «Girar 180°»: la cabeza con la textura al revés se corrige sin rehacer los roles (`docs/PLAN_orientacion_color_cabeza.md`).

Dos partes: (1) con los datos reales de «bolivar-cabeza» (el busto del FBX con sus roles y la pieza cortada en el cuello), girar la pose guardada
—la que dejó la cara en la nuca— sobre el cuello deja la cara del lado del frente; (2) sobre el catálogo de ejemplo con un FBX sintético:
`Catalog.turn_color`, `catalog_ops.turn_color`, `afm catalogo color-girar` y el formulario de la ventana (sin GTK)."""
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from afm import catalog_ops as OPS
from afm import cli, parts as P
from afm.catalog import Catalog, CatalogError
from afm.colorpiece import PieceColorError, package_from_fbx, turn_roles_package
from afm.colorroles import RolesPackage, build_package, propose
from afm.frame import FigureFrame, yaw_matrix
from afm.gui import catalog_import as CI
from afm.gui.catalog_manager import can_turn_color
from color_helpers import write_textured_fbx
from test_catalog_roles import line, new_meta, odd_frame, textured_from, two_color_palette  # noqa: F401  (fixtures y ayudas)

DATA = Path(__file__).parent / "data"
NECK = {"puertos": {"cuello": {"centro": [0.0, -3.172554, 107.810184], "eje": [0.0, -0.379463, -0.925207], "ref": [0.0, 0.925207, -0.379463]}}}
SAVED = np.array([[0.40620497691523527, 0.004457893824021798, 0.015824889196807194, -0.12517361588103304],
                  [0.016414563931472913, -0.13206412305399134, -0.38413853321303754, 2.6033102896971174],
                  [0.0009284537560188084, 0.3844632333406902, -0.13213607905863195, 96.52478187185811],
                  [0.0, 0.0, 0.0, 1.0]])
HEAD_ID = "cabeza-muneco-01"


def _rot_deg(A, B) -> float:
    """Ángulo (°) de la rotación relativa entre las partes rígidas de dos similitudes."""
    ra = A[:3, :3] / np.cbrt(abs(np.linalg.det(A[:3, :3])))
    rb = B[:3, :3] / np.cbrt(abs(np.linalg.det(B[:3, :3])))
    return float(np.degrees(np.arccos(np.clip((np.trace(ra @ rb.T) - 1) / 2, -1, 1))))


# ---------------------------------------------------------------------------------------------------- datos reales de Bolívar
@pytest.fixture(scope="module")
def pkg():
    return RolesPackage.load(DATA / "bolivar_cabeza_roles.npz")


@pytest.fixture(scope="module")
def piece():
    z = np.load(DATA / "bolivar_cabeza_pieza.npz")
    return trimesh.Trimesh(z["vertices"].astype(float), z["faces"], process=False)


def _fbx_of(pkg):
    """Lo que `load_textured` daría del FBX de este paquete (los mismos vértices, caras y UV)."""
    return SimpleNamespace(vertices=np.asarray(pkg.vertices, float), faces=pkg.faces, corner_uv=pkg.corner_uv)


def test_turning_the_saved_pose_puts_the_face_toward_the_front(pkg, piece):
    """La similitud que guardó la ficha tiene la cara hacia −Y (nuca). Girada 180° sobre el cuello y re-afinada, la cara mira al frente y calza."""
    out, frame = turn_roles_package(pkg, _fbx_of(pkg), SAVED, NECK, piece, 180.0)
    rep = frame.report
    assert rep["giro_grados"] == 180.0 and rep["recortada"] is True
    assert rep["frente_ok"] is True and rep["frente"]["angulo_deg"] < 30, rep
    assert rep["ok"] and not rep["ambigua"], rep
    assert _rot_deg(frame.matrix, SAVED) > 150                                          # giró de verdad (≈ 180° de diferencia con la guardada)
    V = pkg.vertices.astype(float)
    head = V[V[:, 1] > 30]
    nose = head[np.argmax(head[:, 2])]
    assert frame.apply(nose)[0, 1] > frame.apply(head.mean(0))[0, 1]       # la nariz hacia +Y, no a −Y (antes: y = −10,8 mm)
    assert np.array_equal(out.labels, pkg.labels) and out.roles == pkg.roles   # los roles y el atlas no cambian


def test_turning_twice_comes_back_to_the_start(pkg, piece):
    """Girar 180° dos veces vuelve a la pose guardada (la cara hacia atrás): `frente_ok` lo dice y `ok` es falso."""
    _, once = turn_roles_package(pkg, _fbx_of(pkg), SAVED, NECK, piece, 180.0)
    _, twice = turn_roles_package(pkg, _fbx_of(pkg), once.matrix, NECK, piece, 180.0)
    assert twice.report["frente_ok"] is False and twice.report["ok"] is False and twice.report["frente"]["angulo_deg"] > 150


def test_the_package_gets_the_original_vertices_and_keeps_the_roles(pkg):
    """`package_from_fbx` pone los vértices del FBX original (el guardado trae los deformados) y deja intactos roles, atlas y UV."""
    fbx = _fbx_of(pkg)
    fbx.vertices = fbx.vertices + 1.5
    out = package_from_fbx(pkg, fbx)
    assert np.allclose(out.vertices, pkg.vertices + 1.5, atol=1e-4)
    assert np.array_equal(out.labels, pkg.labels) and np.array_equal(out.corner_uv, pkg.corner_uv) and out.roles == pkg.roles


def test_a_different_fbx_is_refused(pkg):
    short = SimpleNamespace(vertices=pkg.vertices[:-3], faces=pkg.faces, corner_uv=pkg.corner_uv)
    with pytest.raises(PieceColorError, match="no es el de origen"):
        package_from_fbx(pkg, short)
    moved_uv = SimpleNamespace(vertices=pkg.vertices, faces=pkg.faces, corner_uv=pkg.corner_uv + 0.1)
    with pytest.raises(PieceColorError, match="UV"):
        package_from_fbx(pkg, moved_uv)


def test_from_cut_piece_pose_starts_from_the_given_pose(pkg, piece):
    """`from_cut_piece_pose` no busca arranques: parte de la pose dada y la re-afina (la guardada estaba a unos grados de la mejor), sin `ambigua`."""
    start = yaw_matrix([0.0, -3.172554, 107.810184], [0.0, 0.379463, 0.925207], 180.0) @ SAVED
    fr = FigureFrame.from_cut_piece_pose(pkg.vertices.astype(float), piece, start, source_faces=pkg.faces)
    assert "yaw180" not in fr.report and "ambigua" not in fr.report            # sin pista de frente no decide nada: solo calza
    assert _rot_deg(fr.matrix, start) < 20 and fr.report["ok"] and fr.report["media_mm"] < 0.6


# ---------------------------------------------------------------------------------------------------- catálogo (FBX sintético)
@pytest.fixture()
def colored(line, tmp_path):
    """(catálogo, ref de la cabeza con roles, ruta del FBX original): la cabeza del ejemplo con su FBX sintético y el sha256 real en el origen."""
    cat = Catalog.open(line)
    meta, mesh = cat.load(HEAD_ID)
    tm = textured_from(mesh, odd_frame())
    fbx = write_textured_fbx(tmp_path / "cabeza_origen.fbx", tm)
    pal = two_color_palette()
    prop = propose(tm, pal, k=10)
    assign = {i: ("rojo" if c[0] > c[2] else "azul") for i, c in enumerate(prop.centers_rgb)}
    pk = build_package(tm, prop, assign, origen={"nombre": "cabeza_origen.fbx", "sha256": P.sha256_file(fbx)})
    saved, _ = cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pk)
    return cat, saved.ref, fbx


def test_turn_color_saves_a_new_version_with_the_same_roles(colored):
    cat, ref, fbx = colored
    old = cat.meta(ref)
    old_labels = RolesPackage.load(cat.color_file(ref)).labels
    new, warns = cat.turn_color(ref, fbx, 180.0)
    assert new.version == old.version + 1 and new.id == old.id and new.sha256 == old.sha256          # la malla no cambia
    assert new.color["alineacion"]["giro_grados"] == 180.0 and new.color["alineacion"]["ok"] is True
    assert new.color["roles"] == old.color["roles"] and new.color["origen"]["sha256"] == old.color["origen"]["sha256"]
    assert np.array_equal(RolesPackage.load(cat.color_file(new.ref)).labels, old_labels)             # los roles no se rehacen
    A0, A1 = np.asarray(old.color["similitud"]), np.asarray(new.color["similitud"])
    assert _rot_deg(A0, A1) > 150                                                                     # la pose giró ≈ 180° sobre el cuello
    assert cat.verify().ok and not [w for w in warns if "retoque" in w]
    assert cat.meta(old.ref).version == old.version                                                   # la anterior queda


def test_turn_color_with_another_fbx_or_missing_one_writes_nothing(colored, line, tmp_path):
    cat, ref, fbx = colored
    other = write_textured_fbx(tmp_path / "otro.fbx", textured_from(trimesh.creation.box((6, 6, 60))))
    for bad, msg in ((other, "no es el FBX de origen"), (str(tmp_path / "no_existe.fbx"), "no existe")):
        with pytest.raises(CatalogError, match=msg):
            cat.turn_color(ref, bad)
    assert cat.versions(HEAD_ID)[-1] == cat.meta(ref).version                                         # no se escribió ninguna versión


def test_turn_color_needs_role_color_on_a_head(colored, line):
    cat, ref, fbx = colored
    plain = [m for m in cat.list() if m.tipo == "brazo"][0]
    with pytest.raises(CatalogError, match="no lleva color por roles"):
        cat.turn_color(plain.ref, fbx)


def test_ops_finds_the_original_fbx_by_the_name_in_the_ficha(colored, monkeypatch, tmp_path):
    cat, ref, fbx = colored
    monkeypatch.chdir(os.path.dirname(fbx))
    assert os.path.basename(OPS.find_origin_fbx(cat, ref)) == "cabeza_origen.fbx"
    monkeypatch.chdir(tmp_path / "..")
    shutil.move(fbx, fbx + ".bak")
    with pytest.raises(CatalogError, match="no encuentro el FBX original"):
        OPS.find_origin_fbx(cat, ref)
    assert OPS.find_origin_fbx(cat, ref, "x.fbx") == "x.fbx"                                          # una ruta explícita no se busca


def test_cli_color_girar(colored, capsys):
    cat, ref, fbx = colored
    assert cli.main(["catalogo", "color-girar", cat.root, ref, "--fbx", fbx]) == 0
    out = capsys.readouterr().out
    assert "Color girado 180° sobre el cuello" in out and f"{HEAD_ID}@" in out
    assert cat.meta(HEAD_ID).version == cat.meta(ref).version + 1
    assert cli.main(["catalogo", "color-girar", cat.root, "no-existe-01", "--fbx", fbx]) == 2


def test_a_turn_does_not_copy_the_old_retouch_but_says_so(colored):
    cat, ref, fbx = colored
    m = cat.meta(ref)
    m.color["retoque"] = {"fuente": "x"}                                  # la ficha dice que había retoque (basta para el aviso; no se lee)
    cat._write_meta(m)
    _, warns = cat.turn_color(ref, fbx, 180.0)
    assert any("retoque" in w and "--copiar-de" in w for w in warns)


# ---------------------------------------------------------------------------------------------------- ventana (sin GTK)
def test_gui_form_parses_to_the_op_arguments():
    parsed = CI.parse("girar_color", {"ref": " cabeza-x-01@2 ", "fbx": "", "grados": "180"})
    assert parsed["args"] == ("cabeza-x-01@2", None) and parsed["kwargs"] == {"degrees": 180.0}
    parsed = CI.parse("girar_color", {"ref": "c@1", "fbx": "/tmp/a.fbx", "grados": "90,5"})
    assert parsed["args"] == ("c@1", "/tmp/a.fbx") and parsed["kwargs"] == {"degrees": 90.5}
    assert CI.parse("girar_color", {"ref": "c@1"})["kwargs"] == {"degrees": 180.0}                   # sin grados: 180
    with pytest.raises(ValueError, match="Falta"):
        CI.parse("girar_color", {"ref": ""})
    with pytest.raises(ValueError, match="distinto de 0"):
        CI.parse("girar_color", {"ref": "c@1", "grados": "360"})
    assert CI.FORMS["girar_color"]["comando"] in OPS.COMMANDS and "girar_color" not in CI.LABELS      # tiene su botón, no va en «Importar ▾»


def test_gui_run_turns_the_color(colored):
    cat, ref, fbx = colored
    parsed = CI.parse("girar_color", {"ref": ref, "fbx": fbx})
    res = CI.run("girar_color", cat.root, parsed)
    assert res.meta.version == cat.meta(ref).version + 1                  # `ref` es id@n: sigue apuntando a la versión de partida
    assert any("Color girado" in ln for ln in CI.result_lines(res))


def test_can_turn_color_only_for_a_head_with_role_color(colored):
    cat, ref, _ = colored
    assert can_turn_color(cat.meta(ref)) == (True, "")
    assert can_turn_color(None)[0] is False
    assert can_turn_color([m for m in cat.list() if m.tipo == "brazo"][0])[0] is False
