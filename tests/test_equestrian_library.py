"""E11: biblioteca `caballos/` de la línea (agregar, listar, abrir, verificar; versiones inmutables)."""
import json
import os

import pytest
import trimesh

from afm.equestrian import HorseError, sha256_file
from afm.horse_library import HorseLibrary, parse_horse_ref

HORSE = os.path.join(os.path.dirname(__file__), "..", "examples", "referencia", "caballo", "horse_baseless.stl")


def stl(tmp_path, name="src.stl", size=10.0):
    p = tmp_path / name
    trimesh.creation.box(extents=(size, 2 * size, size)).export(p)
    return str(p)


def test_add_list_open_round_trip(tmp_path):
    lib = HorseLibrary(str(tmp_path / "cat"))
    assert lib.list() == []
    src = stl(tmp_path)
    m = lib.add(src, "pardo-01", nombre="  Pardo  grande ", escala=2.0, yaw=180.0, ref_tipo="alto_dorso", ref_mm=20.0)
    assert m.ref == "pardo-01@1" and m.nombre == "Pardo grande" and m.spec.sha256 == sha256_file(src)
    assert [x.ref for x in lib.list()] == ["pardo-01@1"] and lib.meta("pardo-01") == m == lib.meta("pardo-01@1")
    h = lib.open("pardo-01")
    assert h.report.extents == pytest.approx((20.0, 40.0, 20.0), abs=1e-6)       # escala 2×, guiño 180° (caja simétrica)
    assert os.path.isfile(lib.path("pardo-01")) and lib.path("pardo-01").startswith(str(tmp_path / "cat" / "caballos"))
    assert json.load(open(os.path.join(tmp_path, "cat", "caballos", "pardo-01", "v1", "caballo.json")))["formato"] == "afm-caballo-1"


def test_the_managed_copy_is_independent_of_the_source(tmp_path):
    lib = HorseLibrary(str(tmp_path / "cat"))
    src = stl(tmp_path)
    lib.add(src, "pardo-01")
    sha = sha256_file(src)
    os.remove(src)                                                        # se borra el STL de origen: la copia administrada sigue
    assert lib.open("pardo-01").spec.sha256 == sha and lib.verify() == {}


def test_versions_are_never_overwritten(tmp_path):
    lib = HorseLibrary(str(tmp_path / "cat"))
    a, b = stl(tmp_path, "a.stl", 10.0), stl(tmp_path, "b.stl", 12.0)
    m1 = lib.add(a, "pardo-01")
    assert lib.add(a, "pardo-01").ref == "pardo-01@1" and lib.versions("pardo-01") == [1]   # idéntico: no crea nada
    with pytest.raises(HorseError, match="no se sobrescriben"):
        lib.add(b, "pardo-01")                                           # otro STL, mismo id, sin pedir versión
    with pytest.raises(HorseError, match="no se sobrescriben"):
        lib.add(a, "pardo-01", escala=2.0)                               # mismo STL, otra ficha
    m2 = lib.add(b, "pardo-01", new_version=True)
    assert m2.ref == "pardo-01@2" and lib.versions("pardo-01") == [1, 2]
    assert lib.meta("pardo-01") == m2 and lib.meta("pardo-01@1") == m1   # la 1 no cambió; sin versión = la última
    assert [x.ref for x in lib.list()] == ["pardo-01@2"] and [x.ref for x in lib.list(all_versions=True)] == ["pardo-01@1", "pardo-01@2"]
    assert lib.open("pardo-01@1").report.extents != lib.open("pardo-01@2").report.extents


def test_invalid_inputs_leave_nothing_behind(tmp_path):
    lib = HorseLibrary(str(tmp_path / "cat"))
    src = stl(tmp_path)
    for bad in ("Pardo", "a b", "x@2", "../x", ""):
        with pytest.raises(HorseError):
            lib.add(src, bad)
    with pytest.raises(HorseError, match="no existe"):
        lib.add(str(tmp_path / "nada.stl"), "pardo-01")
    with pytest.raises(HorseError, match="escala"):
        lib.add(src, "pardo-01", escala=0)
    open_mesh = trimesh.creation.box(extents=(1, 1, 1))
    open_mesh.update_faces(open_mesh.faces[:, 0] != open_mesh.faces[0, 0])
    p = tmp_path / "abierto.stl"
    open_mesh.export(p)
    with pytest.raises(HorseError):
        lib.add(str(p), "pardo-01", repair=False)
    assert lib.list() == [] and not any(n.startswith(".nuevo") for n in os.listdir(tmp_path / "cat" / "caballos" / "pardo-01"))


def test_missing_horse_and_bad_refs(tmp_path):
    lib = HorseLibrary(str(tmp_path / "cat"))
    lib.add(stl(tmp_path), "pardo-01")
    with pytest.raises(HorseError, match="no hay un caballo"):
        lib.meta("otro")
    with pytest.raises(HorseError, match="no tiene la versión 3"):
        lib.meta("pardo-01@3")
    for bad in ("Pardo", "a@b", "a@0", "", None):
        with pytest.raises(HorseError):
            parse_horse_ref(bad)
    assert parse_horse_ref("pardo-01") == ("pardo-01", None) and parse_horse_ref(" pardo-01@2 ") == ("pardo-01", 2)


def test_verify_detects_a_changed_or_missing_stl_and_a_misplaced_card(tmp_path):
    lib = HorseLibrary(str(tmp_path / "cat"))
    lib.add(stl(tmp_path), "pardo-01")
    lib.add(stl(tmp_path, "o.stl", 8.0), "tordillo")
    assert lib.verify() == {} and lib.verify("tordillo", deep=True) == {}
    p = lib.path("pardo-01")
    trimesh.creation.box(extents=(5, 5, 5)).export(p)                      # alguien cambia el STL administrado
    bad = lib.verify()
    assert list(bad) == ["pardo-01@1"] and "SHA-256" in bad["pardo-01@1"][0]
    with pytest.raises(HorseError, match="SHA-256"):
        lib.open("pardo-01")
    os.remove(lib.path("tordillo"))
    assert "falta el STL" in lib.verify()["tordillo@1"][0]
    os.rename(tmp_path / "cat" / "caballos" / "tordillo" / "v1", tmp_path / "cat" / "caballos" / "tordillo" / "v7")
    assert "pero está en" in lib.verify()["tordillo@7"][0]


def test_the_piece_catalog_ignores_the_library(tmp_path):
    from afm.catalog import Catalog

    cat = Catalog.create(str(tmp_path / "cat"), "linea", 127.0)
    before = [m.id for m in cat.list()]
    HorseLibrary(cat.root).add(stl(tmp_path), "pardo-01")
    assert [m.id for m in Catalog.open(cat.root).list()] == before


@pytest.mark.skipif(not os.path.exists(HORSE), reason="falta examples/referencia/caballo/horse_baseless.stl")
def test_reference_horse_through_the_library(tmp_path):
    lib = HorseLibrary(str(tmp_path / "cat"))
    lib.add(HORSE, "referencia", nombre="Caballo de referencia", escala=3.0, yaw=180.0, ref_tipo="alto_dorso", ref_mm=97.0)
    h = lib.open("referencia")
    assert h.report.triangles == 64324 and h.report.components == 1
    assert h.report.extents == pytest.approx((41.8, 152.4, 132.7), abs=0.6)    # ≈ 152 × 42 × 133 mm (E02)
    assert lib.verify(deep=True) == {}
