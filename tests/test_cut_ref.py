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

    def set_translucent(self, key, alpha, cull=True):
        self.translucent = getattr(self, "translucent", {})
        self.translucent[key] = alpha

    def set_mesh(self, key, mesh, color=None, clip_x=-1e9, clip_plane=None, ghost=False):
        self.items[key] = mesh
        self.ghost = getattr(self, "ghost", {})
        self.ghost[key] = (ghost, color)


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


# ------------------------------------------------------------------ fantasma
def _ref_meshes(root):
    from afm.proportions import reference_meshes

    cat = Catalog.open(root)
    return reference_meshes(cat, CR.reference_figures(cat)[0])


def test_ghost_places_pick_body_and_limb():
    m = {"torso": 1, "pelvis": 2, "brazo": 3, "pierna": 4, "cabeza": 5}
    assert CR.ghost_places("arm", m) == ["torso", "brazo"] and CR.ghost_places("leg", m) == ["pelvis", "pierna"]
    assert CR.ghost_places("leg", {"torso": 1, "pierna": 4}) == ["torso", "pierna"]      # sin pelvis aparte: el torso entero
    assert CR.ghost_places("arm", {"brazo": 3}) == ["brazo"] and CR.ghost_places("arm", {}) == []


def test_place_ghost_aligns_feet_and_center_without_touching_x(root):
    meshes = _ref_meshes(root)
    wb = np.array([[-30.0, -8.0, 5.0], [30.0, 12.0, 105.0]])
    placed = CR.place_ghost(meshes, wb)
    assert set(placed) == set(meshes)
    assert min(m.bounds[0][2] for m in placed.values()) == pytest.approx(5.0)                       # los pies a la altura de la figura
    ylo = min(m.bounds[0][1] for m in placed.values())
    yhi = max(m.bounds[1][1] for m in placed.values())
    assert 0.5 * (ylo + yhi) == pytest.approx(2.0)                                                   # centro en y de la figura
    for place, m in placed.items():                                                                   # x y tamaño intactos
        assert m.bounds[:, 0] == pytest.approx(meshes[place].bounds[:, 0])
        assert m.extents == pytest.approx(meshes[place].extents)
    assert CR.place_ghost({}, wb) == {} and CR.body_height(meshes) == pytest.approx(max(m.bounds[1][2] for m in meshes.values()) - min(m.bounds[0][2] for m in meshes.values()))


def test_ghost_is_drawn_translucent_for_the_active_limb_only(ctrl):
    sc = Scene()
    ctrl.cut_ghost_visible = False
    ctrl.edit_limb = "brazo 2"
    ctrl._sync_cut_ghost(sc)
    assert not [k for k in sc.items if k.startswith("corteghost:")]                                  # apagado
    ctrl.toggle_cut_ghost()
    assert ctrl.cut_ghost_visible
    ctrl._sync_cut_ghost(sc)
    arm_keys = sorted(k for k in sc.items if k.startswith("corteghost:"))
    assert arm_keys and "corteghost:brazo" in arm_keys and "corteghost:pierna" not in arm_keys
    assert all(sc.ghost[k][0] is True and sc.ghost[k][1][3] < 1.0 for k in arm_keys)                 # translúcido
    ctrl.edit_limb = "pierna 2"                                                                      # al pasar a la pierna, cambia el fantasma
    ctrl._sync_cut_ghost(sc)
    leg_keys = sorted(k for k in sc.items if k.startswith("corteghost:"))
    assert "corteghost:pierna" in leg_keys and "corteghost:brazo" not in leg_keys
    ctrl.toggle_cut_ghost()
    ctrl._sync_cut_ghost(sc)
    assert not [k for k in sc.items if k.startswith("corteghost:")]


def test_ghost_sits_where_the_reference_cuts(ctrl):
    """El contacto del brazo fantasma queda a la distancia de la guía: su puerto de hombro está en x = distancia de la referencia."""
    ctrl.edit_limb = "brazo 2"
    ctrl.cut_ghost_visible = True
    sc = Scene()
    ctrl._sync_cut_ghost(sc)
    ref = next(r["ref"] for r in ctrl.cut_ref_rows() if r["id"] == "brazo 2")
    arm = sc.items["corteghost:brazo"]
    assert arm.bounds[0][0] <= ref.mm + 1e-6 <= arm.bounds[1][0] + 1e-6                              # la pieza abarca el plano de la guía


def test_ghost_without_catalog_draws_nothing_and_says_so(stl_path):
    c = _ctrl(stl_path)
    c.edit_limb = "brazo 2"
    c.toggle_cut_ghost()
    assert "hace falta un catálogo" in c.message
    sc = Scene()
    c._sync_cut_ghost(sc)
    assert not [k for k in sc.items if k.startswith("corteghost:")]


def test_original_figure_is_translucent_only_while_the_ghost_shows(ctrl):
    from afm.gui.controller import _CUT_GHOST_MODEL_ALPHA

    sc = Scene()
    ctrl.edit_limb = "brazo 2"
    ctrl._sync_cut_ghost(sc)                                                # sin fantasma no toca la figura (sigue opaca)
    assert not hasattr(sc, "translucent")
    ctrl.toggle_cut_ghost()
    ctrl._sync_cut_ghost(sc)
    assert sc.translucent == {"figura": _CUT_GHOST_MODEL_ALPHA, "espejo:figura": _CUT_GHOST_MODEL_ALPHA}
    assert 0.3 < _CUT_GHOST_MODEL_ALPHA < 1.0
    sc.translucent.clear()
    ctrl._sync_cut_ghost(sc)                                                # sin cambios en el fantasma igual la reaplica (la sync sube la figura opaca)
    assert sc.translucent["figura"] == _CUT_GHOST_MODEL_ALPHA
