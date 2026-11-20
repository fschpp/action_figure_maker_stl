"""T47 · Lógica del asistente «Roles de color» (`gui/roles_wizard.py`), sin GTK ni OpenGL (la ventana no se prueba acá, R9)."""
import hashlib
import os
import shutil

import numpy as np
import pytest

from afm.catalog import Catalog
from afm.colorroles import RolesPackage
from afm.gui import palette_editor as PE
from afm.gui import roles_wizard as RW
from afm.gui.controller import Controller
from afm.palette import Palette, Role
from afm.project import demo_project
from afm.testfigure import make_doll

from color_helpers import doll_textured
from test_catalog_roles import HEAD_ID, ROOT, odd_frame, textured_from


@pytest.fixture(scope="module")
def head_mesh():
    return Catalog.open(ROOT).load(HEAD_ID)[1]


@pytest.fixture()
def line(tmp_path):
    """Copia del catálogo de ejemplo, SIN paleta."""
    d = str(tmp_path / "linea")
    shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    return d


def make_ctrl(root):
    c = Controller()
    assert c.open_catalog(root)
    return c


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def pal_file(root):
    return os.path.join(root, "paleta.json")


def two_color_tm(head_mesh, top=(200, 30, 30), bottom=(30, 60, 200), frame=True):
    """FBX sintético de la cabeza: mitad alta `top`, baja `bottom` (como en `test_catalog_roles`, con colores a elección)."""
    import test_catalog_roles as T

    old = (T.TOP, T.BOTTOM)
    T.TOP, T.BOTTOM = top, bottom
    try:
        return textured_from(head_mesh, odd_frame() if frame else None)
    finally:
        T.TOP, T.BOTTOM = old


def by_hex(w):
    """{hex: n de cluster}"""
    return {c["hex"]: c["n"] for c in w.clusters()}


def decide_by_color(w):
    """Rojo/azul por cercanía (rojo si R > B); crea los roles que la paleta no tenga."""
    for c in w.clusters():
        rid = "rojo" if int(c["hex"][1:3], 16) > int(c["hex"][5:7], 16) else "azul"
        if rid in w.line_palette().ids() or rid in w.new_role_ids():
            w.set_role(c["n"], rid)
        else:
            w.set_new(c["n"], rid, rid.capitalize())


def test_wizard_blocks_saving_with_undecided_clusters(line, head_mesh):
    root_pal = Palette([Role("rojo", "Rojo", "#C81E1E", ["#C81E1E"])])
    Catalog.open(line).set_palette(root_pal)
    ctrl = make_ctrl(line)
    w = RW.RolesWizard(ctrl)
    with pytest.raises(RW.RolesWizardError):                                 # sin análisis
        w.save_piece(f"{HEAD_ID}@1")
    w.load_textured(two_color_tm(head_mesh))
    cl = {c["hex"]: c for c in w.clusters()}
    assert cl["#C81E1E"]["estado"] == "sugerido" and cl["#C81E1E"]["sugerencia"] == "rojo"   # preseleccionado, marcado como sugerencia
    assert cl["#1E3CC8"]["estado"] == "sin_decidir" and cl["#1E3CC8"]["sugerencia"] is None
    before = digest(pal_file(line))
    n_pieces = len(Catalog.open(line).list(all_versions=True))
    assert not w.can_save and "1" in w.blockers()
    with pytest.raises(RW.RolesWizardError):
        w.save_piece(f"{HEAD_ID}@1")
    w.set_none(cl["#1E3CC8"]["n"])                                           # decidido…
    assert w.undecided() == [] and not w.can_save                            # …pero la sugerencia sigue sin confirmar
    assert w.blockers() != "" and w.unconfirmed() == [cl["#C81E1E"]["n"]]
    with pytest.raises(RW.RolesWizardError):
        w.save_piece(f"{HEAD_ID}@1")
    assert digest(pal_file(line)) == before and len(Catalog.open(line).list(all_versions=True)) == n_pieces
    assert w.confirm_suggestions() == 1 and w.can_save and w.blockers() == ""


def test_new_role_is_created_with_cluster_color_and_anchor(line, head_mesh):
    Catalog.open(line).set_palette(Palette([Role("rojo", "Rojo", "#C81E1E", ["#C81E1E"])]))
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(two_color_tm(head_mesh), {"nombre": "cabeza.fbx", "sha256": "ab" * 32})
    n_azul = by_hex(w)["#1E3CC8"]
    with pytest.raises(RW.RolesWizardError, match="ya existe"):
        w.set_new(n_azul, "rojo")
    with pytest.raises(RW.RolesWizardError):
        w.set_new(n_azul, "Id Malo")
    w.set_new(n_azul, "azul_marino", "Azul marino")
    w.confirm_suggestions()
    pal = w.final_palette()
    azul = pal.get("azul_marino")
    assert azul.hex == "#1E3CC8" and azul.anclas == ["#1E3CC8"] and azul.nombre == "Azul marino"
    assert Catalog.open(line).palette().ids() == ["rojo"]                    # nada se escribe hasta guardar
    new = w.save_piece(f"{HEAD_ID}@1")
    cat = Catalog.open(line)
    saved = cat.palette()
    assert saved.get("azul_marino").hex == "#1E3CC8" and saved.get("azul_marino").anclas == ["#1E3CC8"]
    assert saved.get("rojo").anclas == ["#C81E1E"]                           # ya estaba: no se duplica
    assert new.ref == f"{HEAD_ID}@2" and sorted(new.color["roles"]) == ["azul_marino", "rojo"]
    assert cat.verify().ok


def test_anchors_capped_at_eight(line):
    old = ["#010101", "#020202", "#030303", "#040404", "#050505", "#060606"]
    Catalog.open(line).set_palette(Palette([Role("todo", "Todo", "#808080", old)]))
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(doll_textured())
    assert w.proposal.k >= 4
    for c in range(w.proposal.k):
        w.set_role(c, "todo")
    anchors = w.final_palette().get("todo").anclas
    assert len(anchors) == 8 and anchors[:6] == old                          # las que ya tenía no se tocan; las nuevas llenan hasta 8
    # sin repetir un color que ya es ancla
    w2 = RW.RolesWizard(make_ctrl(line))
    w2.load_textured(doll_textured())
    for c in range(w2.proposal.k):
        w2.set_role(c, "todo")
    assert len(set(w2.final_palette().get("todo").anclas)) == len(w2.final_palette().get("todo").anclas)


def test_second_figure_gets_suggestions_from_first(line, head_mesh):
    a = RW.RolesWizard(make_ctrl(line))                                      # línea sin paleta: todo son roles nuevos
    a.load_textured(two_color_tm(head_mesh))
    decide_by_color(a)
    a.confirm_suggestions()
    a.save_piece(f"{HEAD_ID}@1")
    pal = Catalog.open(line).palette()
    assert pal.ids() == ["rojo", "azul"] or sorted(pal.ids()) == ["azul", "rojo"]
    b = RW.RolesWizard(make_ctrl(line))                                      # otra figura: colores parecidos pero no iguales, otro marco
    b.load_textured(two_color_tm(head_mesh, top=(210, 40, 25), bottom=(25, 70, 190), frame=False))
    sug = {("rojo" if int(c["hex"][1:3], 16) > int(c["hex"][5:7], 16) else "azul"): c["sugerencia"] for c in b.clusters()}
    assert sug == {"rojo": "rojo", "azul": "azul"}
    assert all(c["estado"] == "sugerido" for c in b.clusters()) and not b.can_save       # sugeridas, no asignadas
    assert b.confirm_suggestions() == 2 and b.can_save


def test_saved_piece_reloads_with_roles(line, head_mesh):
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(two_color_tm(head_mesh), {"nombre": "cabeza.fbx", "sha256": "cd" * 32})
    decide_by_color(w)
    new = w.save_piece(f"{HEAD_ID}@1")
    cat = Catalog.open(line)
    assert cat.meta(new.ref).color["modo"] == "roles" and cat.meta(new.ref).color["origen"]["nombre"] == "cabeza.fbx"
    pkg = RolesPackage.load(cat.color_file(new.ref))
    assert sorted(pkg.roles) == ["azul", "rojo"] and pkg.labels.dtype == np.uint8
    assert cat.verify().ok and not [p for p in cat.verify().problems if "rol" in p]
    ed = PE.PaletteEditor(make_ctrl(line))                                   # la ventana de paleta (T46) la ve con roles y la pinta
    assert new.ref in ed.preview_refs() and ed.usage_map()["rojo"] == [new.ref]
    shown = {tuple(c) for c in np.unique(ed.preview_atlas(new.ref).reshape(-1, 3), axis=0)}
    assert shown <= {tuple(r) for r in ed.preview_lut(new.ref)}


def test_failed_piece_restores_the_palette(line, head_mesh, monkeypatch):
    Catalog.open(line).set_palette(Palette([Role("rojo", "Rojo", "#C81E1E", ["#C81E1E"])]))
    before = digest(pal_file(line))
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(two_color_tm(head_mesh))
    decide_by_color(w)
    from afm.catalog import CatalogError

    def boom(self, *a, **k):
        raise CatalogError("falló a propósito")

    monkeypatch.setattr(Catalog, "add_piece", boom)
    with pytest.raises(RW.RolesWizardError, match="a propósito"):
        w.save_piece(f"{HEAD_ID}@1")
    assert digest(pal_file(line)) == before                                  # la paleta quedó como estaba
    # línea sin paleta: si falla, tampoco queda un paleta.json nuevo
    os.remove(pal_file(line))
    w2 = RW.RolesWizard(make_ctrl(line))
    w2.load_textured(two_color_tm(head_mesh))
    decide_by_color(w2)
    with pytest.raises(RW.RolesWizardError):
        w2.save_piece(f"{HEAD_ID}@1")
    assert not os.path.exists(pal_file(line))


def test_save_figure_writes_package_and_notes_it_in_the_project(line, tmp_path):
    from afm.project import Project

    proj = tmp_path / "muneco.proyecto"
    stl = str(tmp_path / "muneco.stl")
    make_doll().export(stl)
    demo_project().save(str(proj))
    ctrl = Controller()
    ctrl.open_project(str(proj), stl)
    assert ctrl.loaded and ctrl.open_catalog(line)
    w = RW.RolesWizard(ctrl)
    w.load_textured(doll_textured())
    for c in range(w.proposal.k):
        w.set_new(c, f"c{c}", "")
    out = w.save_figure(str(tmp_path / "muneco.color.npz"))
    assert os.path.isfile(out) and ctrl.color_spec().roles == "muneco.color.npz"      # relativa al proyecto
    assert RolesPackage.load(out).roles == [f"c{c}" for c in range(w.proposal.k)]
    assert Catalog.open(line).palette().ids() == [f"c{c}" for c in range(w.proposal.k)]
    assert ctrl.color_spec().to_dict()["roles"] == "muneco.color.npz"
    again = str(tmp_path / "guardado.proyecto")
    ctrl.save_project(again)
    assert Project.load(again).color.roles == "muneco.color.npz"             # el proyecto lo recuerda
    # sin figura abierta, o sin línea, se niega y no escribe nada
    c2 = Controller()
    assert c2.open_catalog(line)
    w2 = RW.RolesWizard(c2)
    w2.load_textured(doll_textured())
    w2.confirm_suggestions()
    for c in range(w2.proposal.k):
        w2.set_none(c)
    with pytest.raises(RW.RolesWizardError):
        w2.save_figure(str(tmp_path / "otro.color.npz"))
    assert not os.path.exists(tmp_path / "otro.color.npz")


def test_decisions_validate_and_new_role_owner_changes_drop_dependents(line, head_mesh):
    w = RW.RolesWizard(make_ctrl(line))
    with pytest.raises(RW.RolesWizardError):
        w.set_none(0)                                                        # sin análisis
    w.load_textured(two_color_tm(head_mesh))
    with pytest.raises(RW.RolesWizardError):
        w.set_none(7)                                                        # no existe ese cluster
    with pytest.raises(RW.RolesWizardError, match="no existe"):
        w.set_role(0, "fantasma")
    a, b = by_hex(w)["#C81E1E"], by_hex(w)["#1E3CC8"]
    w.set_new(a, "piel", "Piel")
    w.set_role(b, "piel")                                                    # el segundo cluster usa el rol nuevo del primero
    assert w.role_options() == ["piel"] and w.undecided() == [] and w.can_save
    w.set_none(a)                                                            # el dueño del rol nuevo se va: el dependiente queda sin decidir
    assert w.undecided() == [b] and w.new_role_ids() == []
    assert w.assignment.__name__ == "assignment"
    with pytest.raises(RW.RolesWizardError):
        w.assignment()


def test_preview_paints_clusters_highlights_one_and_shows_roles(line, head_mesh):
    from afm.gui.render import TexturedPart

    Catalog.open(line).set_palette(Palette([Role("rojo", "Rojo", "#FF0000", ["#C81E1E"])]))
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(two_color_tm(head_mesh))
    n_rojo, n_azul = by_hex(w)["#C81E1E"], by_hex(w)["#1E3CC8"]
    lut = w.preview_lut()
    assert tuple(lut[n_rojo]) == (200, 30, 30) and tuple(lut[n_azul]) == (30, 60, 200)
    hl = w.preview_lut(highlight=n_azul)
    assert tuple(hl[n_azul]) == RW.HIGHLIGHT_RGB                              # el elegido siempre en magenta, no en su color
    assert tuple(hl[n_rojo]) != (200, 30, 30) and hl[n_rojo].min() > 90        # el resto, lavado: se ve la figura, sin competir
    assert tuple(hl[n_rojo]) != RW.HIGHLIGHT_RGB
    w.confirm_suggestions()
    r = w.preview_lut(mode="roles")                                          # rojo → el hex del rol (no el del cluster); sin decidir → plano
    assert tuple(r[n_rojo]) == (255, 0, 0) and tuple(r[n_azul]) == (0xC8, 0xC8, 0xC8)
    parts = w.preview_parts()
    assert len(parts) == 1 and isinstance(parts[0], TexturedPart)
    assert np.array_equal(parts[0].image, lut[w.proposal.labels_atlas])
    assert parts[0].corner_uv.shape == (len(parts[0].mesh.faces), 3, 2)


def test_window_module_parses_and_its_keys_exist_in_three_languages():
    import ast
    import re

    from afm import i18n

    base = os.path.dirname(RW.__file__)
    text = open(os.path.join(base, "roles_wizard_window.py"), encoding="utf-8").read()
    ast.parse(text)
    keys = set(re.findall(r'tr\("(roles_wizard\.[a-z_]+)"', text + open(RW.__file__, encoding="utf-8").read()))
    assert keys
    for lang in ("es", "en", "pt"):
        i18n.set_language(lang)
        for k in keys:
            assert i18n.tr(k, n=1, hex="#000000", pct="1 %", error="e", rol="r", ref="x", roles="a", ruta="p", lista="1") != k, (lang, k)
    i18n.set_language("es")
