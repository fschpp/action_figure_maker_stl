"""Referencia del corte: a cuántos mm del plano de simetría corta una figura ya cortada del catálogo (sin GTK)."""
import os
import shutil

import numpy as np
import pytest

from afm import cut_ref as CR
from afm.catalog import Catalog
from afm.gui.controller import Controller
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture()
def root(tmp_path):
    dst = tmp_path / "linea"
    shutil.copytree(EJEMPLO, dst)
    return str(dst)


@pytest.fixture(scope="module")
def stl_path(tmp_path_factory):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path_factory.mktemp("cutref") / "muneco.stl")
    m.export(path)
    return path


def _ctrl(stl_path, root=None):
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    s = c.session
    for l in demo_project().limbs:
        s.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
    s.set_limb_polygon("brazo", demo_project().limbs[0].polygon)
    sym = s.project.symmetry
    poly = np.asarray(demo_project().limbs[0].polygon)
    z0, z1 = poly[:, 2].min(), poly[:, 2].max()
    s.add_limb("brazo 2", "arm", Plane.from_normal(sym.origin, sym.normal, "brazo 2"), auto=True, extrude="outward")
    s.set_limb_polygon("brazo 2", sym.to_world(np.array([[-40, z0], [40, z0], [40, z1], [-40, z1]], float)))
    s.set_limb_offset("brazo 2", 15.0)
    s.add_limb("pierna 2", "leg", Plane.from_normal(sym.origin, sym.normal, "pierna 2"), auto=True, extrude="outward")
    s.set_limb_polygon("pierna 2", sym.to_world(np.array([[-40, z0 - 30], [40, z0 - 30], [40, z0], [-40, z0]], float)))
    s.set_limb_offset("pierna 2", 7.0)
    if root:
        s.open_catalog(root)
    return c


@pytest.fixture()
def ctrl(stl_path, root):
    return _ctrl(stl_path, root)


class Scene:
    def __init__(self):
        self.items = {}

    def set_plane(self, key, plane, half, center=None, color=None):
        self.items[key] = plane

    def show(self, key, visible):
        self.shown = getattr(self, "shown", {})
        self.shown[key] = visible

    def remove(self, key):
        self.items.pop(key, None)


# ------------------------------------------------------------------ núcleo
def test_reference_cuts_read_the_lateral_port_x(root):
    cat = Catalog.open(root)
    figs = CR.reference_figures(cat)
    assert figs and CR.default_reference(figs) == figs[0]
    cuts = CR.reference_cuts(cat, figs[0])
    assert set(cuts) == {"arm", "leg"}
    for kind, port in (("arm", "hombro"), ("leg", "cadera")):
        c = cuts[kind]
        meta = cat.meta(c.ref)
        assert c.mm == pytest.approx(abs(float(meta.puertos[port].frame.center[0]))) and c.mm > 1.0
        assert c.altura_mm == meta.altura_figura_mm and c.figura == figs[0]
    assert CR.reference_cuts(cat, "no-existe") == {}


def test_default_reference_prefers_guemes():
    assert CR.default_reference(["belgrano.stl", "Güemes_lq2.stl"]) == "Güemes_lq2.stl"
    assert CR.default_reference(["belgrano.stl", "guemes_x.stl"]) == "guemes_x.stl"
    assert CR.default_reference(["belgrano.stl", "azurduy.stl"]) == "belgrano.stl"
    assert CR.default_reference([]) is None


def test_reading_and_text():
    ref = CR.RefCut("Güemes", "arm", "brazo-guemes-01@1", 7.7, 130.0)
    r = CR.reading(6.2, ref)
    assert r["ref_mm"] == 7.7 and r["dif_mm"] == pytest.approx(1.5) and not r["igual"] and r["prop_mm"] is None
    assert CR.reading(7.68, ref)["igual"]
    assert CR.reading(6.2, ref, 130.0)["prop_mm"] is None                      # misma altura: no hay valor proporcional
    assert CR.reading(6.2, ref, 100.0)["prop_mm"] == pytest.approx(7.7 * 100 / 130)
    txt = CR.readout_text("Brazo", 6.2, ref, 130.0)
    assert txt == "Brazo: tu corte a 6,20 mm · Güemes corta a 7,70 mm → mové +1,50 mm"
    assert CR.readout_text("Brazo", 7.7, ref).endswith("→ igual")
    assert "proporcional: 5,92 mm" in CR.readout_text("Brazo", 6.2, ref, 100.0)
    assert "mové -0,50 mm" in CR.readout_text("Brazo", 8.2, ref, 130.0)


# ------------------------------------------------------------------ controlador
def test_rows_text_and_match(ctrl):
    ctrl.edit_limb = "brazo 2"                                              # con dos miembros sobre la simetría hace falta uno activo
    rows = ctrl.cut_ref_rows()
    assert [r["id"] for r in rows] == ["brazo 2", "pierna 2"]              # solo los miembros sobre la simetría
    rows = [r for r in rows if r["id"] == "brazo 2"]
    ref = rows[0]["ref"]
    assert rows[0]["corte_mm"] == pytest.approx(15.0) and rows[0]["lectura"]["dif_mm"] == pytest.approx(ref.mm - 15.0)
    txt = ctrl.cut_ref_text()
    assert f"{ref.figura} corta a" in txt and "mové +" in txt
    lines = txt.split("\n")
    assert len(lines) == 2 and lines[0].startswith("▶ Brazo 2") and lines[1].startswith("Pierna 2")   # el activo lleva ▶
    ctrl.edit_limb = None                                                   # sin miembro activo la lectura se ve igual
    assert ctrl.cut_ref_text().count("corta a") == 2                        # (el «▶» sigue en el último editado)
    ctrl.edit_limb = "brazo 2"
    assert ctrl.match_cut_to_ref("brazo 2")
    assert ctrl.session.limb_offset("brazo 2") == pytest.approx(ref.mm)
    assert next(r for r in ctrl.cut_ref_rows() if r["id"] == "brazo 2")["lectura"]["igual"]


def test_unknown_reference_falls_back_to_default(ctrl):
    figs = ctrl.cut_ref_figures()
    assert ctrl.cut_ref_figure() == figs[0]
    ctrl.cut_ref_name = "otra.stl"
    assert ctrl.cut_ref_figure() == figs[0]
    ctrl.set_cut_ref_figure(figs[0])
    assert ctrl.cut_ref_figure() == figs[0]


def test_guide_plane_is_drawn_at_the_reference_distance(ctrl):
    ctrl.edit_limb = "brazo 2"
    sc = Scene()
    ctrl._sync_cut_ref(sc)
    ref = next(r["ref"] for r in ctrl.cut_ref_rows() if r["id"] == "brazo 2")
    assert list(sc.items) == ["corteref:brazo 2"]
    assert abs(float(sc.items["corteref:brazo 2"].origin[0])) == pytest.approx(ref.mm, abs=1e-6)   # simetría en x = 0
    ctrl.toggle_cut_ref()                                                   # apagada: se retira
    ctrl._sync_cut_ref(sc)
    assert sc.items == {}
    ctrl.toggle_cut_ref()
    ctrl._sync_cut_ref(sc)
    assert "corteref:brazo 2" in sc.items


def test_guide_follows_only_the_limb_being_edited(ctrl):
    """Al pasar de editar el brazo a la pierna, la guía del brazo se retira (no quedan las dos a la vez)."""
    sc = Scene()
    ctrl.edit_limb = "brazo 2"
    ctrl._sync_cut_ref(sc)
    assert list(sc.items) == ["corteref:brazo 2"]
    ctrl.edit_limb = "pierna 2"
    ctrl._sync_cut_ref(sc)
    assert list(sc.items) == ["corteref:pierna 2"]
    leg_ref = next(r["ref"] for r in ctrl.cut_ref_rows() if r["id"] == "pierna 2")
    assert abs(float(sc.items["corteref:pierna 2"].origin[0])) == pytest.approx(leg_ref.mm, abs=1e-6)


def test_guide_respects_the_plane_visibility_checkbox(ctrl):
    ctrl.set_group_visible("planes", True)                                  # «Mostrar todos los planos»
    ctrl.edit_limb = "pierna 2"
    sc = Scene()
    ctrl._sync_cut_ref(sc)
    assert sc.shown["corteref:pierna 2"] is True
    ctrl.toggle_plane("pierna 2")                                           # la casilla de la lista de planos
    ctrl._sync_cut_ref(sc)
    assert sc.shown["corteref:pierna 2"] is False
    ctrl.toggle_plane("pierna 2")
    ctrl.set_group_visible("planes", False)                                 # el interruptor general también la oculta
    ctrl._sync_cut_ref(sc)
    assert sc.shown["corteref:pierna 2"] is False


def test_without_catalog_says_what_is_missing(stl_path):
    c = _ctrl(stl_path)
    c.edit_limb = "brazo 2"
    assert c.cut_ref_rows() == [] and c.cut_ref_figures() == []
    assert "abrí un catálogo" in c.cut_ref_text()
    sc = Scene()
    c._sync_cut_ref(sc)
    assert sc.items == {}
    assert not c.match_cut_to_ref("brazo 2")
