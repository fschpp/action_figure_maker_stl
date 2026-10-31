"""Armas en la ventana (controlador sin GTK): cargar, escalar, 3 clics del plano de corte, vista previa, guardar y elegir accesorios."""
import json
import os

import numpy as np
import pytest
import trimesh

from afm import accessories as A
from afm import solids as S
from afm.gui.controller import Controller, Mode

from test_gui_hand_place import feed, std_hand                                   # noqa: F401  (fixture)
from test_gui_phases35 import FakeScene
from test_weapon import HAND_AG, cat_hand, sword_stl                              # noqa: F401  (fixtures)


@pytest.fixture()
def ctrl(cat_hand):                                                               # noqa: F811
    c = Controller()
    assert c.set_weapon_catalog(cat_hand.root) is True
    return c


def _shown(sword_stl):
    """Los 3 puntos del plano (en coordenadas del STL original) llevados a lo que se ve: escalados."""
    path, pts = sword_stl
    return path, np.asarray(pts, float)


def test_catalog_needs_a_hand_with_grip(tmp_path):
    from afm.catalog import Catalog

    empty = Catalog.create(str(tmp_path / "vacia"), "linea-vacia", 127.0)
    c = Controller()
    assert c.set_weapon_catalog(empty.root) is False and "ninguna mano con agarre" in c.message
    assert c.set_weapon_catalog("/no/hay/catalogo") is False and "no se pudo abrir" in c.message


def test_open_weapon_scales_to_the_lines_height(ctrl, sword_stl):
    path, _ = sword_stl
    assert ctrl.open_weapon(path) is True and ctrl.weapon_active
    assert ctrl.weapon.length_mm == pytest.approx(0.40 * 127.0, rel=0.01)           # 50,8 mm para una línea de 127
    assert "Arma cargada" in ctrl.message and Mode.VIEW == ctrl.mode


def test_open_bad_weapon_reports(ctrl, tmp_path):
    bad = tmp_path / "roto.stl"
    bad.write_text("no es una malla")
    assert ctrl.open_weapon(str(bad)) is False and "No se pudo cargar el arma" in ctrl.message and not ctrl.weapon_active


def test_scale_by_factor_or_length(ctrl, sword_stl):
    path, _ = sword_stl
    assert ctrl.set_weapon_scale(length_mm=30.0) is False and "Primero cargá" in ctrl.message
    ctrl.open_weapon(path)
    assert ctrl.set_weapon_scale(length_mm=30.0) and ctrl.weapon.length_mm == pytest.approx(30.0, rel=0.01)
    assert ctrl.set_weapon_scale(factor=2.0) and ctrl.weapon.scale == pytest.approx(2.0)
    assert ctrl.set_weapon_scale(factor=-1.0) is False and "positiva" in ctrl.message


def test_three_clicks_cut_the_handle_and_preview_the_result(ctrl, sword_stl):
    path, pts = _shown(sword_stl)
    ctrl.open_weapon(path)
    ctrl.set_weapon_scale(factor=10.0)                                              # el STL está en cm: ×10 = mm
    ctrl.start_weapon_cut_plane()
    assert ctrl.mode == Mode.WEAPON_CUT_PLANE and ctrl.picker is not None
    scene = FakeScene()
    feed(ctrl, [p * 10.0 for p in pts])                                             # los clics caen sobre el arma escalada
    assert ctrl.mode == Mode.VIEW and ctrl.weapon.build is not None
    assert "se conserva el" in ctrl.message and "AVISO" in ctrl.message             # mango redondo de Ø4: soporte bajo el vástago
    ctrl.sync(scene)
    assert {"arma:original", "arma:resultado", "arma:plano"} <= set(scene.items)
    res = scene.items["arma:resultado"][1]
    assert res.is_watertight and res.volume > 0


def test_weapon_view_replaces_the_figure_and_cancel_restores_it(ctrl, sword_stl):
    path, pts = _shown(sword_stl)
    ctrl.open_weapon(path)
    scene = FakeScene()
    ctrl.sync(scene)
    assert "arma:original" in scene.items and "figura" not in scene.items
    ctrl.close_weapon()
    assert not ctrl.weapon_active and ctrl.mode == Mode.VIEW


def test_pending_points_are_drawn_and_cancel_clears_them(ctrl, sword_stl):
    path, pts = _shown(sword_stl)
    ctrl.open_weapon(path)
    ctrl.start_weapon_cut_plane()
    feed(ctrl, [pts[0] * ctrl.weapon.scale])
    assert len(ctrl.pending) == 1
    scene = FakeScene()
    ctrl.sync(scene)
    assert "pendientes" in scene.items
    ctrl.cancel()
    assert ctrl.mode == Mode.VIEW and ctrl.pending == []


def test_flip_and_roll(ctrl, sword_stl):
    path, pts = _shown(sword_stl)
    ctrl.open_weapon(path)
    ctrl.flip_weapon_cut()
    assert "Primero elegí el plano" in ctrl.message
    ctrl.set_weapon_scale(factor=10.0)
    ctrl.weapon.set_cut(pts * 10.0)
    keep = ctrl.weapon.build.info["volumen_mantenido_pct"]
    ctrl.flip_weapon_cut()
    assert ctrl.weapon.build.info["volumen_mantenido_pct"] < keep - 20
    ctrl.flip_weapon_cut()
    ctrl.set_weapon_roll(40.0)
    assert ctrl.weapon.build.info["giro_grados"] == pytest.approx(40.0)
    ctrl.set_weapon_roll(None)


def test_save_weapon_to_the_catalog(ctrl, cat_hand, sword_stl):                   # noqa: F811
    path, pts = _shown(sword_stl)
    assert ctrl.save_weapon() is False and "Primero cargá" in ctrl.message
    ctrl.open_weapon(path)
    assert ctrl.save_weapon() is False and "plano de corte" in ctrl.message           # todavía sin corte
    ctrl.set_weapon_scale(factor=10.0)
    ctrl.weapon.set_cut(pts * 10.0)
    assert ctrl.save_weapon("excalibur-01") is True and "excalibur-01@1" in ctrl.message
    assert cat_hand.meta("excalibur-01").tipo == "accesorio"
    assert ctrl.save_weapon("excalibur-01") is False and "No se pudo guardar el arma" in ctrl.message      # mismo id sin pedir versión nueva
    assert ctrl.save_weapon("excalibur-01", new_version=True) is True and "excalibur-01@2" in ctrl.message


def test_project_accessories_travel_with_the_project(ctrl, cat_hand, sword_stl, tmp_path):   # noqa: F811
    path, pts = _shown(sword_stl)
    ctrl.open_weapon(path)
    ctrl.set_weapon_scale(factor=10.0)
    ctrl.weapon.set_cut(pts * 10.0)
    ctrl.save_weapon("excalibur-01")
    assert [m.ref for m in ctrl.accessory_pieces()] == ["excalibur-01@1"]
    assert ctrl.project_accessory_labels() == {"excalibur-01@1": "sin datos de agarre"}      # el proyecto no tiene mano pegada
    assert ctrl.set_project_accessory("excalibur-01@1") is True
    acc = ctrl.session.project.accesorios
    assert [a.pieza for a in acc] == ["excalibur-01@1"] and acc[0].catalogo == os.path.abspath(cat_hand.root) and acc[0].sha256
    assert ctrl.set_project_accessory("mano-agarre-01@1") is False and "no un accesorio" in ctrl.message
    assert ctrl.set_project_accessory("no-existe-01") is False
    d = json.loads(json.dumps(ctrl.session.project.to_dict()))
    assert d["accesorios"][0]["pieza"] == "excalibur-01@1"
    assert ctrl.set_project_accessory("excalibur-01", False) is True and ctrl.session.project.accesorios == []


def test_project_accessory_needs_a_catalog():
    c = Controller()
    c.weapon_catalog = ""
    assert c.set_project_accessory("excalibur-01@1") is False and "catálogo" in c.message


def test_weapon_uses_the_catalog_already_open_in_the_window(cat_hand, sword_stl):                   # noqa: F811
    path, _ = sword_stl
    c = Controller()
    assert c.open_catalog(cat_hand.root)
    assert c.open_weapon(path) is True
    assert c.weapon.catalog is not None and c.weapon.hand.ref == "mano-agarre-01@1"
    assert c.weapon.length_mm == pytest.approx(0.40 * 127.0, rel=0.01)
    assert c.accessory_pieces() == []                                                 # todavía no se guardó ninguna arma
