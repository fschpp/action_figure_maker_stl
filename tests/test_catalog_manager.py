"""Gestor del catálogo: eliminar una o más versiones de pieza, la vista previa 3/4 y la integración con la figura abierta.

La ventana GTK (`catalog_window.py`) no se prueba acá (no hay GTK en el entorno de pruebas): toda la lógica está en
`Catalog.delete_pieces`, `Session.catalog_*`, `Controller.catalog_*` y `gui/catalog_manager.py`."""
import json
import os

import numpy as np
import pytest
import trimesh

from afm import parts as P
from afm import solids as S
from afm.catalog import Catalog, CatalogError
from afm.cli import main
from afm.compat import COMPAT_FORMAT, CompatCache
from afm.gui import catalog_manager as CM
from afm.gui.controller import Controller
from afm.ports import port_from_points
from afm.project import demo_project
from afm.testfigure import make_doll

M = S.Manifold
BASE = [(3.0, 0.0, 0.0), (-1.5, -2.6, 0.0), (-1.5, 2.6, 0.0)]


def arm_piece(pid="brazo-test-01", r=5.0, h=30.0, figura="", **kw):
    mesh = S.to_trimesh(M.cylinder(h, r, r, 64))
    ports = {"hombro": port_from_points(mesh, BASE, "hombro", "enchufe"),
             "muneca": port_from_points(mesh, [(a, b, h) for a, b, _ in BASE], "muneca", "toma")}
    meta = P.PieceMeta(id=pid, version=1, tipo="brazo", puertos=ports, altura_figura_mm=100.0,
                       origen=P.make_origin(figura, "viejo.proyecto" if figura else ""), **kw)
    return mesh, meta


@pytest.fixture
def cat(tmp_path):
    return Catalog.create(str(tmp_path / "linea"), "prueba", 100.0, 0.4)


def files_of(cat, ref):
    pid, ver = ref.split("@")
    return [os.path.join(cat.root, "piezas", "brazo", f"{pid}@{ver}.stl"), os.path.join(cat.root, "piezas", "brazo", f"{pid}@{ver}.pieza.json")]


# ------------------------------------------------------------------ Catalog.delete_pieces
def test_delete_removes_files_and_index_but_not_other_pieces(cat):
    for pid in ("brazo-a-01", "brazo-b-01", "brazo-c-01"):
        mesh, meta = arm_piece(pid)
        cat.add_piece(mesh, meta)
    res = cat.delete_pieces(["brazo-a-01@1", "brazo-b-01@1"])
    assert res.refs == ["brazo-a-01@1", "brazo-b-01@1"] and res.errors == []
    assert not any(os.path.exists(f) for f in files_of(cat, "brazo-a-01@1") + files_of(cat, "brazo-b-01@1"))
    assert all(os.path.exists(f) for f in files_of(cat, "brazo-c-01@1"))
    assert [m.ref for m in cat.list(all_versions=True)] == ["brazo-c-01@1"]
    index = open(os.path.join(cat.root, "CATALOGO.md")).read()
    assert "brazo-c-01@1" in index and "brazo-a-01@1" not in index
    assert cat.verify().ok
    with pytest.raises(CatalogError, match="no hay ninguna pieza"):
        cat.meta("brazo-a-01")


def test_delete_needs_an_explicit_version_and_is_all_or_nothing(cat):
    mesh, meta = arm_piece()
    cat.add_piece(mesh, meta)
    with pytest.raises(CatalogError, match="indicar la versión"):
        cat.delete_pieces(["brazo-test-01"])
    with pytest.raises(CatalogError, match="no hay ninguna pieza"):
        cat.delete_pieces(["brazo-test-01@1", "otra-01@1"])          # la segunda no existe: no se borra ni la primera
    with pytest.raises(CatalogError, match="ninguna pieza"):
        cat.delete_pieces([])
    assert [m.ref for m in cat.list(all_versions=True)] == ["brazo-test-01@1"] and all(os.path.exists(f) for f in files_of(cat, "brazo-test-01@1"))
    with pytest.raises(CatalogError):
        cat.delete_pieces(["brazo-test-01@7"])


def test_deleting_one_version_leaves_a_gap_that_is_a_warning_and_new_versions_keep_counting(cat):
    for r in (5.0, 5.5, 6.0):
        mesh, meta = arm_piece(r=r)
        cat.add_piece(mesh, meta, new_version=cat.versions("brazo-test-01") != [])
    assert cat.versions("brazo-test-01") == [1, 2, 3]
    cat.delete_pieces(["brazo-test-01@2"])
    assert cat.versions("brazo-test-01") == [1, 3]
    rep = cat.verify()
    assert rep.ok and any("no consecutivas" in w for w in rep.warnings)
    assert cat.meta("brazo-test-01").version == 3            # «la última» sigue siendo la 3
    mesh, meta = arm_piece(r=6.5)
    new, _ = cat.add_piece(mesh, meta, new_version=True)
    assert new.version == 4
    cat.delete_pieces(["brazo-test-01@4", "brazo-test-01@3"])
    assert cat.meta("brazo-test-01").version == 1


def test_delete_prunes_the_compat_cache(cat):
    mesh, meta = arm_piece()
    meta, _ = cat.add_piece(mesh, meta)
    mesh, other = arm_piece("brazo-otro-01")
    other, _ = cat.add_piece(mesh, other)
    cache = CompatCache(cat.root)
    cache.put("k1", {"formato": COMPAT_FORMAT, "piezas": {"brazo": {"ref": meta.ref, "sha256": meta.sha256}}})
    cache.put("k2", {"formato": COMPAT_FORMAT, "piezas": {"brazo": {"ref": other.ref, "sha256": other.sha256}}})
    assert set(cache.load()["entradas"]) == {"k1", "k2"}
    cat.delete_pieces([meta.ref])
    assert set(cache.load()["entradas"]) == {"k2"}


def test_delete_with_a_stray_stl_in_another_folder_name_still_removes_the_json(cat):
    mesh, meta = arm_piece()
    meta, _ = cat.add_piece(mesh, meta)
    os.remove(files_of(cat, meta.ref)[0])                      # el STL ya no está: se borra igual la ficha
    res = cat.delete_pieces([meta.ref])
    assert res.refs == [meta.ref] and not os.path.exists(files_of(cat, meta.ref)[1])


def test_usages_non_recursive_and_project_without_version(cat, tmp_path):
    mesh, meta = arm_piece()
    cat.add_piece(mesh, meta)
    carpeta = tmp_path / "figs"
    (carpeta / "sub").mkdir(parents=True)
    (carpeta / "a.proyecto").write_text(json.dumps({"parts": {"brazo": {"id": "brazo-test-01", "version": None}}}))
    (carpeta / "sub" / "b.proyecto").write_text(json.dumps({"parts": {"brazo": {"id": "brazo-test-01", "version": 1}}}))
    assert len(cat.usages("brazo-test-01@1", [str(carpeta)])) == 2
    assert [os.path.basename(u["archivo"]) for u in cat.usages("brazo-test-01@1", [str(carpeta)], recursive=False)] == ["a.proyecto"]


# ------------------------------------------------------------------ CLI
def test_cli_eliminar(tmp_path, capsys):
    root = str(tmp_path / "cat")
    assert main(["catalogo", "crear", root, "--linea", "l", "--altura", "100"]) == 0
    cat = Catalog.open(root)
    for pid in ("brazo-a-01", "brazo-b-01"):
        mesh, meta = arm_piece(pid, figura="belgrano_viejo.stl")
        cat.add_piece(mesh, meta)
    capsys.readouterr()
    assert main(["catalogo", "eliminar", root, "brazo-a-01@1"]) == 2          # sin --si y sin terminal: no borra
    assert "confirmar" in capsys.readouterr().err and len(cat.list(all_versions=True)) == 2
    assert main(["catalogo", "eliminar", root, "brazo-a-01"]) == 2            # sin versión
    assert "indicar la versión" in capsys.readouterr().err
    assert main(["catalogo", "eliminar", root, "brazo-a-01@1", "brazo-b-01@1", "--si"]) == 0
    out = capsys.readouterr().out
    assert "Eliminada brazo-a-01@1" in out and "Eliminada brazo-b-01@1" in out and "belgrano_viejo.stl" in out
    assert Catalog.open(root).list(all_versions=True) == []


# ------------------------------------------------------------------ lógica del gestor (sin GTK)
def test_filter_finds_everything_cataloged_from_one_figure(cat):
    for pid, fig in (("brazo-a-01", "belgrano_viejo.stl"), ("brazo-b-01", "guemes.stl")):
        mesh, meta = arm_piece(pid, figura=fig)
        cat.add_piece(mesh, meta)
    metas = cat.list(all_versions=True)
    assert [m.id for m in metas if CM.matches(m, "belgrano")] == ["brazo-a-01"]
    assert [m.id for m in metas if CM.matches(m, "BRAZO Belgrano")] == ["brazo-a-01"]      # palabras sueltas, sin mayúsculas
    assert len([m for m in metas if CM.matches(m, "", "brazo")]) == 2 and not [m for m in metas if CM.matches(m, "", "pierna")]
    assert not [m for m in metas if CM.matches(m, "belgrano guemes")]
    assert CM.row_text(metas[0]).startswith("brazo-a-01@1 · brazo · borrador · belgrano_viejo.stl")


def test_detail_text_and_confirm_texts(cat):
    mesh, meta = arm_piece(figura="belgrano_viejo.stl")
    meta, _ = cat.add_piece(mesh, meta)
    txt = CM.detail_text(meta, ["la figura abierta (lugar «brazo»)"])
    assert "brazo-test-01@1" in txt and "10,0 × 10,0 × 30,0 mm" in txt and "belgrano_viejo.stl" in txt
    assert "hombro (enchufe)" in txt and "Se usa en: la figura abierta" in txt
    assert "ninguna figura ni receta" in CM.detail_text(meta, [])
    msg, detail = CM.confirm_texts([meta], {meta.ref: ["receta «b.receta.json» (lugar «brazo»)"]})
    assert "esta pieza" in msg and "brazo-test-01@1" in detail and "b.receta.json" in detail and "no se puede deshacer" in detail
    many = [meta] * 15
    msg, detail = CM.confirm_texts(many, {})
    assert "15 piezas" in msg and "y 3 más" in detail


def test_three_quarter_camera_shows_the_whole_piece_from_front_side_and_above():
    bounds = np.array([[-10.0, -4.0, 0.0], [10.0, 6.0, 80.0]])
    corners = np.array([[x, y, z] for x in bounds[:, 0] for y in bounds[:, 1] for z in bounds[:, 2]])
    for aspect in (1.6, 1.0, 0.5):
        cam = CM.three_quarter_camera(bounds, aspect)
        h = np.c_[corners, np.ones(len(corners))] @ cam.mvp(aspect).T
        ndc = h[:, :3] / h[:, 3:4]
        assert np.all(np.abs(ndc) <= 1.0), f"se recorta con aspecto {aspect}"
    cam = CM.three_quarter_camera(bounds)
    d = cam.eye() - cam.target
    assert d[0] > 0 and d[1] > 0 and d[2] > 0                    # adelante (+Y), a un lado (+X) y desde arriba: 3/4
    assert abs(d[0]) < abs(d[1]) * 2 and abs(d[1]) < abs(d[0]) * 2   # ni de frente ni de costado
    assert np.allclose(cam.target, bounds.mean(axis=0))


def test_port_markers(cat):
    _mesh, meta = arm_piece()
    pts, segs = CM.port_markers(meta, 5.0)
    assert pts.shape == (2, 3) and len(segs) == 2
    for p, s in zip(pts, segs):
        assert np.allclose(s[0], p) and abs(np.linalg.norm(s[1] - s[0]) - 5.0) < 1e-9
    pts, segs = CM.port_markers(P.PieceMeta(id="x-01", version=1, tipo="accesorio", puertos={}), 5.0)
    assert pts.shape == (0, 3) and segs == []


# ------------------------------------------------------------------ figura abierta + gestor
@pytest.fixture(scope="module")
def doll_stl(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("doll") / "muneco.stl")
    make_doll().export(path)
    return path


@pytest.fixture()
def ctrl(tmp_path, doll_stl):
    proj = tmp_path / "muneco.proyecto"
    demo_project().save(str(proj))
    c = Controller()
    c.open_project(str(proj), doll_stl)
    assert c.loaded
    Catalog.create(str(tmp_path / "linea"), "prueba", 100.0)
    assert c.open_catalog(str(tmp_path / "linea"))
    return c


def test_manager_over_an_empty_or_closed_catalog():
    c = Controller()
    mgr = CM.CatalogManager(c)
    assert mgr.pieces() == [] and mgr.types() == []
    assert c.catalog_pieces() == [] and c.catalog_usages("x-01@1") == []
    assert c.catalog_delete([]) is False and "ninguna pieza" in c.message
    assert c.catalog_delete(["x-01@1"]) is False and "No se eliminó nada" in c.message


def test_manager_lists_previews_and_deletes_and_the_open_figure_follows(ctrl):
    assert ctrl.catalog_add_current("viejo") is True
    mgr = CM.CatalogManager(ctrl)
    todas = mgr.pieces()
    assert len(todas) >= 4 and {m.tipo for m in todas} >= {"brazo", "pierna", "cabeza"}
    refs = lambda ms: [m.ref for m in ms]          # noqa: E731  (PieceMeta lleva arreglos numpy: no se compara con ==)
    assert refs(mgr.pieces("viejo")) == refs(todas) and mgr.pieces("no-hay-nada") == []
    assert all(m.tipo == "pierna" for m in mgr.pieces(tipo="pierna")) and refs(mgr.pieces(tipo=CM.ALL_TYPES)) == refs(todas)

    # vista previa: malla + cámara que la encuadra entera (la segunda lectura sale del caché)
    pierna = mgr.pieces(tipo="pierna")[0]
    mesh = mgr.mesh(pierna.ref)
    assert isinstance(mesh, trimesh.Trimesh) and mesh.is_watertight and mgr.mesh(pierna.ref) is mesh
    cam = mgr.camera_for(pierna.ref, 1.3)
    h = np.c_[mesh.vertices, np.ones(len(mesh.vertices))] @ cam.mvp(1.3).T
    assert np.all(np.abs(h[:, :3] / h[:, 3:4]) <= 1.0 + 1e-9)

    # la figura abierta usa esa pierna y ese brazo: el gestor avisa dónde se usan y, al borrar, vuelven a la pieza del modelo
    brazo = mgr.pieces(tipo="brazo")[0]
    assert ctrl.catalog_set_part("pierna", pierna.ref) and ctrl.catalog_set_part("brazo", brazo.ref)
    assert mgr.uses(pierna.ref) == ["la figura abierta (lugar «pierna»)"]
    msg, detail = mgr.confirm_for([pierna.ref, brazo.ref])
    assert "2 piezas" in msg and "lugar «pierna»" in detail and "lugar «brazo»" in detail

    assert mgr.delete([pierna.ref, brazo.ref]) is True
    assert "Eliminadas 2 pieza(s)" in ctrl.message and "pierna" in ctrl.message and "volver a generar" in ctrl.message
    assert ctrl.session.project.parts == {} and ctrl.session.outcome is None
    assert pierna.ref not in [m.ref for m in mgr.pieces()] and len(mgr.pieces()) == len(todas) - 2
    assert not os.path.exists(os.path.join(ctrl.session.catalog_root, "piezas", "pierna", f"{pierna.ref}.pieza.json"))
    rows = {r["lugar"]: r for r in ctrl.catalog_rows()}
    assert rows["pierna"]["pieza"] is None and rows["brazo"]["pieza"] is None
    assert Catalog.open(ctrl.session.catalog_root).verify().ok


def test_deleting_other_pieces_keeps_the_chosen_ones(ctrl):
    ctrl.catalog_add_current("viejo")
    mgr = CM.CatalogManager(ctrl)
    pierna = mgr.pieces(tipo="pierna")[0]
    brazo = mgr.pieces(tipo="brazo")[0]
    ctrl.catalog_set_part("pierna", pierna.ref)
    assert mgr.delete([brazo.ref]) is True
    assert ctrl.session.project.parts["pierna"].ref == pierna.ref and "Vuelven" not in ctrl.message
    # una versión nueva de la pierna: borrar la vieja quita la elegida (fija la versión) pero no una «última» sin versión fija
    ctrl.catalog_add_current("viejo", places=["pierna"], new_version=True)
    assert [m.version for m in mgr.pieces(tipo="pierna")] == [1, 2]
    assert mgr.delete([f"{pierna.id}@1"]) is True
    assert "pierna" not in ctrl.session.project.parts and "Vuelven a la pieza del modelo: pierna" in ctrl.message


def test_a_part_without_a_fixed_version_only_goes_away_with_the_last_version(ctrl):
    from afm.project import PartRef

    ctrl.catalog_add_current("viejo", places=["pierna"])
    mgr = CM.CatalogManager(ctrl)
    pierna = mgr.pieces(tipo="pierna")[0]
    ctrl.catalog_add_current("viejo", places=["pierna"], new_version=True)
    ctrl.session.project.parts["pierna"] = PartRef(id=pierna.id, version=None, linea="prueba")
    assert mgr.uses(f"{pierna.id}@2") == ["la figura abierta (lugar «pierna»)"] and mgr.uses(f"{pierna.id}@1") == []
    mgr.delete([f"{pierna.id}@2"])
    assert "pierna" in ctrl.session.project.parts                  # sigue existiendo la @1: «la última» ahora es esa
    mgr.delete([f"{pierna.id}@1"])
    assert "pierna" not in ctrl.session.project.parts


# ------------------------------------------------------------------ vista previa fuera de pantalla
def test_preview_renderer_draws_the_piece_in_three_quarter_view(cat):
    pytest.importorskip("moderngl")
    mesh, meta = arm_piece()
    meta, _ = cat.add_piece(mesh, meta)
    pr = CM.PreviewRenderer(320, 240)
    if not pr.available:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {pr.error}")
    try:
        m = cat.load(meta.ref)[1]
        img = pr.render(m, meta)
        assert img.shape == (240, 320, 4)
        drawn = (np.abs(img[..., :3].astype(int) - img[0, 0, :3].astype(int)).sum(-1) > 12)
        assert 500 < drawn.sum() < 0.8 * drawn.size                      # hay pieza, y no ocupa toda la imagen
        ys, xs = np.nonzero(drawn)
        assert ys.min() > 0 and xs.min() > 0 and ys.max() < 239 and xs.max() < 319   # entera, sin recortar
        girada = pr.render(m, meta, pitch_off=45.0)      # (el cilindro es simétrico en yaw)
        assert not np.array_equal(img, girada)
        pr.clear()
    finally:
        pr.close()
