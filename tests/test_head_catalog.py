"""Catalogar la cabeza de REEMPLAZO (la que se importó con más detalle) en lugar de la del modelo."""
import dataclasses
import os

import numpy as np
import pytest

from afm import solids as S
from afm.catalog import Catalog
from afm.extract import ExtractError, extract_all
from afm.gui.controller import Controller
from afm.project import demo_project
from afm.testfigure import make_doll

M = S.Manifold


@pytest.fixture(scope="module")
def doll():
    return S.to_manifold(make_doll())


@pytest.fixture(scope="module")
def head_stl(tmp_path_factory):
    """Cabeza «con más detalle»: más baja que la original (que llega a 100 mm) y con una nariz que la hace inconfundible."""
    d = tmp_path_factory.mktemp("cabeza")
    ball = M.sphere(15, 64)
    flat = S.intersection(ball, M.cube((100, 100, 100), center=True).translate((0, 0, 50 - 10)))      # base plana z = -10
    head = S.union(flat, M.sphere(5, 32).translate((0, 15, 0)))
    path = str(d / "cabeza_detallada.stl")
    S.to_trimesh(head).export(path)
    return path


def with_replacement(path, **kw):
    pr = demo_project()
    pr.head = dataclasses.replace(pr.head, replacement_path=path, **kw)
    return pr


def head_of(res):
    return next(e for e in res.pieces if e.meta.tipo == "cabeza")


def test_the_replacement_head_is_cataloged_not_the_models(doll, head_stl):
    orig = head_of(extract_all(doll, demo_project(), "muneco"))
    res = extract_all(doll, with_replacement(head_stl), "muneco")
    new = head_of(res)
    assert new.meta.problems() == [] and new.mesh.is_watertight
    assert new.mesh.bounds[1][2] < orig.mesh.bounds[1][2] - 3          # la importada es más baja: no es la del modelo
    assert abs(new.mesh.volume - orig.mesh.volume) > 0.05 * abs(orig.mesh.volume)
    assert new.meta.origen["cabeza_reemplazo"] == "cabeza_detallada.stl" and "reemplazo" in new.meta.descripcion
    assert new.meta.id == "cabeza-muneco-01" and set(new.meta.puertos) == {"cuello"}
    assert "cabeza_reemplazo" not in orig.meta.origen
    # el puerto «cuello» queda sobre el mismo plano y la misma toma que el de la cabeza del modelo (base igualada)
    a, b = new.meta.puertos["cuello"], orig.meta.puertos["cuello"]
    assert np.allclose(a.frame.axis, b.frame.axis, atol=1e-3) and abs(float((a.frame.center - b.frame.center) @ b.frame.axis)) < 0.05
    assert np.linalg.norm(a.frame.center - b.frame.center) < 1.5
    assert a.face is not None and abs(a.face.area - b.face.area) / b.face.area < 0.1
    # el resto de las piezas no cambia
    base = {e.meta.tipo: e for e in extract_all(doll, demo_project(), "muneco").pieces}
    for e in res.pieces:
        if e.meta.tipo != "cabeza":
            assert abs(e.mesh.volume - base[e.meta.tipo].mesh.volume) < 1e-6, e.meta.tipo


def test_replacement_scale_and_orientation_are_honored(doll, head_stl):
    a = head_of(extract_all(doll, with_replacement(head_stl), "m", ["cabeza"]))
    b = head_of(extract_all(doll, with_replacement(head_stl, replacement_scale=1.3), "m", ["cabeza"]))
    assert 1.3 ** 3 * 0.9 < b.mesh.volume / a.mesh.volume < 1.3 ** 3 * 1.1


def test_only_the_head_skips_the_limbs(doll, head_stl):
    res = extract_all(doll, with_replacement(head_stl), "muneco", ["cabeza"])
    assert [e.meta.tipo for e in res.pieces] == ["cabeza"] and res.warnings == []      # sin avisos de mano ni de torso: no se tocaron
    assert [e.meta.tipo for e in extract_all(doll, demo_project(), "muneco", ["cabeza"]).pieces] == ["cabeza"]


def test_a_missing_replacement_file_is_reported(doll, tmp_path):
    with pytest.raises(ExtractError, match="ya no existe"):
        extract_all(doll, with_replacement(str(tmp_path / "no_esta.stl")), "muneco", ["cabeza"])


# ------------------------------------------------------------------ GUI (sesión / controlador, sin GTK)
@pytest.fixture(scope="module")
def doll_stl(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("doll") / "belgrano.stl")
    make_doll().export(path)
    return path


@pytest.fixture()
def ctrl(tmp_path, doll_stl):
    proj = tmp_path / "belgrano.proyecto"
    demo_project().save(str(proj))
    c = Controller()
    c.open_project(str(proj), doll_stl)
    Catalog.create(str(tmp_path / "linea"), "prueba", 100.0)
    assert c.open_catalog(str(tmp_path / "linea"))
    return c


def test_catalog_options_list_places_and_what_already_exists(ctrl, head_stl):
    o = ctrl.catalog_add_options()
    assert o["prefijo"] == "belgrano" and o["cabeza_reemplazo"] is None
    assert [r["lugar"] for r in o["lugares"]] == ["cabeza", "torso", "brazo", "pierna"]
    assert all(r["disponible"] and r["existentes"] == [] for r in o["lugares"])
    assert ctrl.catalog_add_current("belgrano") is True
    o = ctrl.catalog_add_options()
    assert {r["lugar"]: r["existentes"] for r in o["lugares"]} == {
        "cabeza": ["cabeza-belgrano-01@1"], "torso": ["torso-belgrano-01@1"], "brazo": ["brazo-belgrano-01@1"],
        "pierna": ["pierna-belgrano-01@1"]}
    ctrl.session.project.head = dataclasses.replace(ctrl.session.project.head, replacement_path=head_stl)
    o = ctrl.catalog_add_options()
    assert o["cabeza_reemplazo"] == "cabeza_detallada.stl" and "cabeza_detallada.stl" in o["lugares"][0]["nota"]


def test_cataloging_again_without_new_version_writes_nothing(ctrl):
    ctrl.catalog_add_current("belgrano")
    before = [m.ref for m in ctrl.catalog_pieces()]
    assert ctrl.catalog_add_current("belgrano") is False and "ya están en el catálogo" in ctrl.message
    assert [m.ref for m in ctrl.catalog_pieces()] == before            # ni siquiera la primera pieza


def test_new_head_version_replaces_only_the_head_and_old_versions_stay(ctrl, head_stl):
    ctrl.catalog_add_current("belgrano")
    old = next(m for m in ctrl.catalog_pieces() if m.tipo == "cabeza")
    ctrl.session.project.head = dataclasses.replace(ctrl.session.project.head, replacement_path=head_stl)
    # solo la cabeza, como versión nueva; el resto de la figura ni se mira (se puede catalogar sin tener todo terminado)
    res = ctrl.session.catalog_add("belgrano", places=["cabeza"], new_version=True)
    assert [m.ref for m in res["piezas"]] == ["cabeza-belgrano-01@2"]
    refs = [m.ref for m in ctrl.catalog_pieces()]
    assert refs.count("cabeza-belgrano-01@1") == 1 and "cabeza-belgrano-01@2" in refs
    assert len([r for r in refs if r.startswith("torso-")]) == 1 and len([r for r in refs if r.startswith("brazo-")]) == 1
    new = next(m for m in ctrl.catalog_pieces() if m.ref == "cabeza-belgrano-01@2")
    assert new.origen["cabeza_reemplazo"] == "cabeza_detallada.stl" and new.volumen_mm3 != old.volumen_mm3
    assert ctrl.session.catalog_load("cabeza-belgrano-01@2")[1].bounds[1][2] < ctrl.session.catalog_load(old.ref)[1].bounds[1][2] - 3
    assert Catalog.open(ctrl.session.catalog_root).verify().ok


def test_head_only_needs_the_neck_plane_not_a_finished_figure(ctrl, head_stl):
    ctrl.session.project.limbs = []                                    # figura sin terminar: faltan los miembros
    ctrl.session.project.head = None
    with pytest.raises(ValueError, match="plano del cuello"):
        ctrl.session.catalog_add("belgrano", places=["cabeza"])
