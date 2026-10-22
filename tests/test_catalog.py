"""Piezas `afm-pieza-1` y catálogo en disco (Fases 1 y 2 del plan de piezas intercambiables)."""
import json
import os

import numpy as np
import pytest
import trimesh

from afm import parts as P
from afm import solids as S
from afm.catalog import Catalog, CatalogError, parse_ref
from afm.cli import main
from afm.hand import HAND_FORMAT, save_standard_hand
from afm.ports import Port, port_from_points

M = S.Manifold
BASE = [(3.0, 0.0, 0.0), (-1.5, -2.6, 0.0), (-1.5, 2.6, 0.0)]          # plano z = 0
TOP = [(3.0, 0.0, 30.0), (-1.5, -2.6, 30.0), (-1.5, 2.6, 30.0)]        # plano z = 30


def cyl(r, h, z0=0.0):
    return S.to_trimesh(M.cylinder(h, r, r, 96).translate((0, 0, z0)))


def arm_piece(pid="brazo-test-01", r=5.0, h=30.0, **kw):
    """Brazo: cilindro con el hombro en su base (z = 0) y la muñeca en su extremo (z = h)."""
    mesh = cyl(r, h)
    ports = {"hombro": port_from_points(mesh, BASE, "hombro", "enchufe"),
             "muneca": port_from_points(mesh, [(a, b, h) for a, b, _ in BASE], "muneca", "toma")}
    return mesh, P.PieceMeta(id=pid, version=1, tipo="brazo", puertos=ports, altura_figura_mm=130.0, **kw)


@pytest.fixture
def cat(tmp_path):
    return Catalog.create(str(tmp_path / "linea"), "proceres-130", 130.0, 0.4)


def test_parse_ref():
    assert parse_ref("pierna-botas-01") == ("pierna-botas-01", None) and parse_ref("a-1@3") == ("a-1", 3)
    for bad in ("Pierna", "a@", "a b", "a@x"):
        with pytest.raises(CatalogError):
            parse_ref(bad)


def test_meta_validation():
    _m, meta = arm_piece()
    assert meta.problems() == []
    meta.puertos.pop("hombro")
    assert any("falta el puerto «hombro»" in x for x in meta.problems())
    _m, meta = arm_piece(pid="Brazo Malo")
    assert any("id inválido" in x for x in meta.problems())
    _m, meta = arm_piece()
    meta.puertos["hombro"] = Port("hombro", "toma", meta.puertos["hombro"].frame, meta.puertos["hombro"].face)
    assert any("el rol es «toma»" in x for x in meta.problems())
    _m, meta = arm_piece()
    meta.puertos["cadera"] = meta.puertos["hombro"]
    assert any("no previsto" in x for x in meta.problems())


def test_write_read_roundtrip_hash_and_immutability(tmp_path):
    mesh, meta = arm_piece()
    P.write_piece(str(tmp_path), meta, mesh)
    assert meta.stl == "brazo-test-01@1.stl" and len(meta.sha256) == 64
    assert meta.volumen_mm3 == pytest.approx(np.pi * 25 * 30, rel=0.01)
    got, m2 = P.read_piece(str(tmp_path), "brazo-test-01", 1)
    assert got.to_dict() == meta.to_dict() and m2.is_watertight
    _m, again = arm_piece()
    with pytest.raises(P.PieceError, match="inmutables"):
        P.write_piece(str(tmp_path), again, mesh)
    with open(os.path.join(tmp_path, meta.stl), "ab") as f:             # alguien toca el STL
        f.write(b"x")
    with pytest.raises(P.PieceError, match="hash"):
        P.read_piece(str(tmp_path), "brazo-test-01", 1)


def test_open_mesh_is_refused(tmp_path):
    mesh, meta = arm_piece()
    open_mesh = trimesh.Trimesh(mesh.vertices, mesh.faces[:-4], process=False)
    with pytest.raises(P.PieceError, match="no es estanca"):
        P.write_piece(str(tmp_path), meta, open_mesh)


def test_create_open_and_params(cat):
    assert Catalog.open(cat.root).name == "proceres-130"
    assert cat.height_mm == 130.0 and cat.params.ref_height == 130.0 and cat.params.clearance == pytest.approx(0.5)
    assert os.path.isfile(os.path.join(cat.root, "CATALOGO.md"))
    with pytest.raises(CatalogError, match="ya es un catálogo"):
        Catalog.create(cat.root, "otra", 100)
    with pytest.raises(CatalogError, match="no es un catálogo"):
        Catalog.open(os.path.join(cat.root, "piezas"))


def test_add_list_versions_and_state(cat):
    mesh, meta = arm_piece(etiquetas=["estandar"])
    meta, warns = cat.add_piece(mesh, meta)
    assert meta.ref == "brazo-test-01@1" and warns == []
    mesh, dup = arm_piece()
    with pytest.raises(CatalogError, match="inmutables"):
        cat.add_piece(mesh, dup)
    mesh, v2 = arm_piece(r=5.5)
    v2, _ = cat.add_piece(mesh, v2, new_version=True)
    assert v2.version == 2 and cat.versions("brazo-test-01") == [1, 2]
    assert cat.meta("brazo-test-01").version == 2 and cat.meta("brazo-test-01@1").version == 1
    assert [m.ref for m in cat.list()] == ["brazo-test-01@2"] and len(cat.list(all_versions=True)) == 2
    assert cat.list(tag="estandar") == []                  # la v2 no heredó la etiqueta y por defecto se lista solo la última
    assert cat.list(tipo="pierna") == []
    cat.set_state("brazo-test-01@2", "validada")
    assert cat.list(state="validada")[0].ref == "brazo-test-01@2" and cat.meta("brazo-test-01@1").estado == "borrador"
    with pytest.raises(CatalogError):
        cat.meta("nada-01")
    assert "brazo-test-01@2" in open(os.path.join(cat.root, "CATALOGO.md")).read()


def test_height_mismatch_warns(cat):
    mesh, meta = arm_piece()
    meta.altura_figura_mm = 114.0
    _m, warns = cat.add_piece(mesh, meta)
    assert len(warns) == 1 and "114" in warns[0]


def test_verify_clean_and_detects_tampering(cat):
    mesh, meta = arm_piece()
    cat.add_piece(mesh, meta)
    rep = cat.verify()
    assert rep.ok and rep.warnings == []
    with open(os.path.join(cat.root, "piezas", "brazo", meta.stl), "ab") as f:
        f.write(b"x")
    assert any("hash" in x for x in cat.verify().problems)


def test_verify_detects_missing_stl_and_version_gap(cat):
    mesh, meta = arm_piece()
    cat.add_piece(mesh, meta)
    os.remove(os.path.join(cat.root, "piezas", "brazo", meta.stl))
    assert any("falta el STL" in x for x in cat.verify().problems)


def _standard_hand(tmp_path, r=3.0):
    local = S.union(M.cylinder(8, r, r, 96), M.sphere(5, 48).translate((0, 0, 10)))       # muñeca en z = 0, dedos hacia +Z
    path = str(tmp_path / "mano_estandar.stl")
    save_standard_hand(path, local, {"formato": HAND_FORMAT, "lado_origen": "izquierda", "altura_figura_mm": 130.0,
                                     "origen_figura": "guemes.stl"})
    return path


def test_import_standard_hand_and_compatibility(cat, tmp_path):
    meta, warns = cat.import_hand(_standard_hand(tmp_path))
    assert meta.ref == "mano-estandar-01@1" and warns == []
    p = meta.puertos["muneca"]
    assert p.rol == "enchufe" and np.allclose(p.frame.axis, [0, 0, -1]) and p.face.radius_eq == pytest.approx(3.0, rel=0.01)
    assert meta.lado == "izq" and meta.altura_figura_mm == 130.0 and meta.origen["figura"] == "guemes.stl"
    mesh, arm = arm_piece()
    cat.add_piece(mesh, arm)
    res = cat.compatible_with(meta.id)                                     # radio 3 contra una muñeca de radio 5
    assert [(o.id, na, nb, c.nivel) for o, na, nb, c in res] == [("brazo-test-01", "muneca", "muneca", "aviso")]
    assert res[0][3].escalon_mm == pytest.approx(2.0, abs=0.05)
    assert cat.verify().ok


def test_standard_hand_without_json_is_refused(cat, tmp_path):
    path = str(tmp_path / "suelta.stl")
    S.to_trimesh(M.cylinder(8, 3, 3, 48)).export(path)
    with pytest.raises(P.PieceError, match="mano.json"):
        cat.import_hand(path)


def test_usages_in_recipes(cat, tmp_path):
    mesh, meta = arm_piece()
    cat.add_piece(mesh, meta)
    with open(os.path.join(cat.root, "figuras", "guemes.receta.json"), "w") as f:
        json.dump({"formato": "afm-receta-1", "personaje": "guemes", "linea": cat.name,
                   "lugares": {"brazo": "brazo-test-01@1", "pierna": None}}, f)
    with open(os.path.join(cat.root, "figuras", "belgrano.receta.json"), "w") as f:
        json.dump({"formato": "afm-receta-1", "personaje": "belgrano", "linea": cat.name,
                   "lugares": {"brazo": "brazo-test-01@2"}}, f)
    assert {os.path.basename(u["archivo"]) for u in cat.usages("brazo-test-01")} == {"guemes.receta.json", "belgrano.receta.json"}
    assert [os.path.basename(u["archivo"]) for u in cat.usages("brazo-test-01@2")] == ["belgrano.receta.json"]
    assert cat.usages("otra-01") == []
    proj = tmp_path / "figs"
    proj.mkdir()
    (proj / "x.proyecto").write_text(json.dumps({"parts": {"brazo": {"id": "brazo-test-01", "version": 1}}}))
    assert any(u["archivo"].endswith("x.proyecto") for u in cat.usages("brazo-test-01", [str(proj)]))


def test_cli_roundtrip(tmp_path, capsys):
    root = str(tmp_path / "cat")
    assert main(["catalogo", "crear", root, "--linea", "proceres-130", "--altura", "130"]) == 0
    hand = _standard_hand(tmp_path)
    assert main(["catalogo", "importar-mano", root, hand]) == 0
    stl = str(tmp_path / "brazo.stl")
    cyl(5, 30).export(stl)
    pts = [v for p in BASE for v in p]
    top = [v for a, b, _ in BASE for v in (a, b, 30.0)]
    assert main(["catalogo", "agregar-stl", root, stl, "--tipo", "brazo", "--id", "brazo-cli-01", "--etiqueta", "estandar",
                 "--puerto", "hombro", *map(str, pts), "--puerto", "muneca", *map(str, top)]) == 0
    capsys.readouterr()
    assert main(["catalogo", "listar", root]) == 0
    out = capsys.readouterr().out
    assert "brazo-cli-01@1" in out and "mano-estandar-01@1" in out
    assert main(["catalogo", "listar", root, "--tipo", "mano", "--compatible-con", "brazo-cli-01"]) == 0
    assert "muneca ↔ muneca: aviso" in capsys.readouterr().out
    assert main(["catalogo", "verificar", root]) == 0
    assert main(["catalogo", "estado", root, "brazo-cli-01", "impresa"]) == 0
    assert main(["catalogo", "usos", root, "brazo-cli-01"]) == 0
    assert "no la usa ninguna figura" in capsys.readouterr().out


def test_cli_errors_are_clean(tmp_path, capsys):
    assert main(["catalogo", "listar", str(tmp_path / "nada")]) == 2
    assert "no es un catálogo" in capsys.readouterr().err
    root = str(tmp_path / "cat")
    main(["catalogo", "crear", root, "--linea", "l", "--altura", "100"])
    stl = str(tmp_path / "c.stl")
    cyl(5, 30).export(stl)
    assert main(["catalogo", "agregar-stl", root, stl, "--tipo", "brazo", "--puerto", "cadera", *["0"] * 9]) == 2
    assert "no previsto" in capsys.readouterr().err
    assert main(["catalogo", "agregar-stl", root, stl, "--tipo", "pierna", "--puerto", "cadera", "3", "0", "9", "-1.5", "-2.6", "9", "-1.5", "2.6", "9"]) == 2
    assert "cara plana" in capsys.readouterr().err
