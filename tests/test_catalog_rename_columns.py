"""Gestor del catálogo: renombrar piezas, columnas por tipo y pin de articulación bien visible (sin GTK ni GL)."""
import json
import os

import numpy as np
import pytest

from afm import parts as P
from afm import solids as S
from afm.catalog import Catalog, CatalogError
from afm.gui import catalog_manager as CM
from afm.ports import port_from_points

M = S.Manifold
BASE = [(3.0, 0.0, 0.0), (-1.5, -2.6, 0.0), (-1.5, 2.6, 0.0)]


def limb(pid="brazo-a-01", tipo="brazo", with_joint=False, h=30.0):
    mesh = S.to_trimesh(M.cylinder(h, 5.0, 5.0, 64))
    ports = {("hombro" if tipo == "brazo" else "cadera"): port_from_points(mesh, BASE, "hombro" if tipo == "brazo" else "cadera", "enchufe")}
    meta = P.PieceMeta(id=pid, version=1, tipo=tipo, puertos=ports, altura_figura_mm=100.0, origen=P.make_origin("belgrano.stl"))
    if with_joint:
        from afm.joints import JointSpec
        from afm.planes import Plane

        kind = "elbow" if tipo == "brazo" else "knee"
        j = JointSpec(name="codo" if tipo == "brazo" else "rodilla", limb="arm" if tipo == "brazo" else "leg", kind=kind,
                      plane=Plane.from_normal(np.array([0.0, 0.0, h / 2]), np.array([0.0, 0.0, 1.0])),
                      axis_center=(0.0, 0.0, h / 2 + 3.0))
        meta.articulaciones = [j.to_dict()]
    return mesh, meta


@pytest.fixture
def cat(tmp_path):
    return Catalog.create(str(tmp_path / "linea"), "prueba", 100.0, 0.4)


def test_rename_changes_display_name_only(cat):
    mesh, meta = limb()
    saved, _ = cat.add_piece(mesh, meta)
    stl = os.path.join(cat.root, "piezas", "brazo", "brazo-a-01@1.stl")
    before = open(stl, "rb").read()
    cat.rename_piece("brazo-a-01@1", "  Brazo   Belgrano  ")
    m = cat.meta("brazo-a-01@1")
    assert m.nombre == "Brazo Belgrano" and m.display_name == "Brazo Belgrano"
    assert m.ref == "brazo-a-01@1" and m.sha256 == saved.sha256 and open(stl, "rb").read() == before
    assert "Brazo Belgrano" in open(os.path.join(cat.root, "CATALOGO.md")).read()
    assert cat.verify().ok
    cat.rename_piece("brazo-a-01", "")
    assert cat.meta("brazo-a-01").display_name == "brazo-a-01"
    js = json.load(open(os.path.join(cat.root, "piezas", "brazo", "brazo-a-01@1.pieza.json")))
    assert "nombre" not in js                         # sin nombre, la ficha queda como antes


def test_rename_applies_to_all_versions_and_rejects_duplicates(cat):
    mesh, meta = limb()
    cat.add_piece(mesh, meta)
    mesh, meta = limb()
    cat.add_piece(mesh, meta, new_version=True)
    cat.rename_piece("brazo-a-01", "Brazo largo")
    assert {m.nombre for m in cat.list(all_versions=True)} == {"Brazo largo"}
    mesh, meta = limb("brazo-b-01")
    cat.add_piece(mesh, meta)
    with pytest.raises(CatalogError, match="ya lo usa"):
        cat.rename_piece("brazo-b-01", "brazo largo")
    with pytest.raises(CatalogError, match="ya lo usa"):
        cat.rename_piece("brazo-b-01", "brazo-a-01")
    with pytest.raises(CatalogError, match="demasiado largo"):
        cat.rename_piece("brazo-b-01", "x" * 61)
    with pytest.raises(CatalogError, match="no hay ninguna pieza"):
        cat.rename_piece("nada-01", "algo")


def test_filter_finds_by_name_and_columns_sort_by_type(cat):
    for tipo, pid in (("pierna", "pierna-z-01"), ("brazo", "brazo-a-01")):
        mesh, meta = limb(pid, tipo)
        cat.add_piece(mesh, meta)
    cat.rename_piece("brazo-a-01", "Zurdo")
    metas = cat.list(all_versions=True)
    assert [CM.matches(m, "zurdo") for m in metas].count(True) == 1
    assert [m.tipo for m in CM.sort_pieces(metas, "tipo")] == ["brazo", "pierna"]
    assert [m.tipo for m in CM.sort_pieces(metas, "tipo", descending=True)] == ["pierna", "brazo"]
    assert [m.display_name for m in CM.sort_pieces(metas, "nombre")] == ["pierna-z-01", "Zurdo"]
    cells = CM.row_cells(next(m for m in metas if m.tipo == "brazo"))
    assert cells["nombre"] == "Zurdo" and cells["tipo"] == "brazo" and cells["version"] == "@1"
    assert {k for k, _t, _w in CM.COLUMNS} == set(cells)
    with pytest.raises(ValueError):
        CM.sort_pieces(metas, "no-existe")


def test_joint_label_says_clearly_if_there_is_elbow_or_knee():
    _m, arm = limb("brazo-a-01", "brazo")
    _m, arm_j = limb("brazo-b-01", "brazo", with_joint=True)
    _m, leg = limb("pierna-a-01", "pierna")
    _m, leg_j = limb("pierna-b-01", "pierna", with_joint=True)
    assert CM.joint_label(arm) == "○ sin codo" and CM.joint_label(arm_j) == "● con codo"
    assert CM.joint_label(leg) == "○ sin rodilla" and CM.joint_label(leg_j) == "● con rodilla"
    assert CM.has_joint_pin(arm_j) and not CM.has_joint_pin(arm)
    head = P.PieceMeta(id="cabeza-a-01", version=1, tipo="cabeza", puertos={})
    assert CM.joint_label(head) == "—"
    assert "Articulación: ● con codo" in CM.detail_text(arm_j) and "Articulación" not in CM.detail_text(head)
    assert CM.matches(arm_j, "con codo") and not CM.matches(arm, "con codo")


def test_pins_are_solid_cylinders_not_one_pixel_lines():
    mesh, arm_j = limb("brazo-b-01", "brazo", with_joint=True)
    pins = dict((n, (m, c)) for n, m, c in CM.pin_meshes(arm_j, mesh))
    assert "pasador:hombro" in pins and "eje:codo" in pins
    ext = float(max(mesh.extents))
    for name, (pm, _c) in pins.items():
        assert pm.is_watertight and pm.volume > 0
        assert min(pm.extents) >= 2 * CM.pin_radius(ext) - 1e-6          # grueso, no una línea
    eje, _c = pins["eje:codo"]
    assert pins["eje:codo"][1] == CM.JOINT_COLOR != pins["pasador:hombro"][1]
    assert eje.extents[0] > 2 * 5.0                                        # el eje (en X) atraviesa y sobresale de la pieza de r = 5
    assert [n for n, _p in CM.joint_planes(arm_j)] == ["plano:codo"]
    _m, plain = limb("brazo-a-01", "brazo")
    assert [n for n in (x[0] for x in CM.pin_meshes(plain, mesh))] == ["pasador:hombro"] and CM.joint_planes(plain) == []


def test_controller_rename_and_old_piece_files_load(cat, tmp_path):
    mesh, meta = limb()
    cat.add_piece(mesh, meta)
    p = os.path.join(cat.root, "piezas", "brazo", "brazo-a-01@1.pieza.json")
    d = json.load(open(p))
    assert P.PieceMeta.from_dict(d).nombre == ""                         # fichas viejas sin «nombre»
    d["nombre"] = "Otro"
    assert P.PieceMeta.from_dict(d).display_name == "Otro"


def test_arrow_keys_pick_previous_and_next_row_and_stay_at_the_ends():
    n = CM.next_row
    assert n(0, "down", None, []) is None
    assert n(5, "down", None, []) == 0 and n(5, "up", None, []) == 4           # sin nada elegido: ↓ la primera, ↑ la última
    assert n(5, "down", None, [1, 3]) == 4 and n(5, "up", None, [1, 3]) == 0   # desde lo elegido: ↓ la última, ↑ la primera
    assert n(5, "down", 2, [2]) == 3 and n(5, "up", 2, [2]) == 1
    assert n(5, "down", 4, [4]) == 4 and n(5, "up", 0, [0]) == 0               # se queda en los extremos
    assert n(30, "pgdn", 5, []) == 15 and n(30, "pgup", 5, []) == 0
    assert n(30, "home", 7, [7]) == 0 and n(30, "end", 7, [7]) == 29
