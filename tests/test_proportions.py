"""Comparador de proporciones (sin GTK): núcleo, sesión y controlador."""
import shutil

import numpy as np
import pytest
import trimesh

from afm import proportions as PR
from afm.catalog import Catalog
from afm.gui.controller import Controller
from afm.gui.session import Session
from afm.project import demo_project
from afm.testfigure import make_doll

EJEMPLO = __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "catalogo", "ejemplo")


def box(lo, hi):
    b = trimesh.creation.box(extents=np.subtract(hi, lo))
    b.apply_translation(np.add(lo, hi) / 2.0)
    return b


def body(k=1.0):
    """Un cuerpo de cajas: `k` alarga el torso en Z."""
    return {"cabeza": box((-5, -5, 90), (5, 5, 100)), "torso": box((-10, -5, 50), (10, 5, 50 + 40 * k)),
            "brazo": box((10, -3, 40), (16, 3, 50 + 38 * k)), "pelvis": box((-9, -4, 35), (9, 4, 50)),
            "pierna": box((0, -4, 0), (9, 4, 38))}


def test_catalog_figures_and_reference(tmp_path):
    root = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, root)
    cat = Catalog.open(root)
    figs = PR.catalog_figures(cat)
    assert figs and {"torso", "brazo", "pierna", "cabeza"} <= set(next(iter(figs.values())))
    ref = PR.reference_meshes(cat, next(iter(figs)))
    assert set(ref) == set(next(iter(figs.values())))
    with pytest.raises(ValueError, match="no tiene piezas"):
        PR.reference_meshes(cat, "nadie")


def test_normalize_puts_the_feet_on_z0_and_the_torso_on_y0():
    m = {k: v.copy() for k, v in body().items()}
    for v in m.values():
        v.apply_translation((0.0, 7.0, 12.0))
    n = PR.normalize(m)
    assert min(v.bounds[0][2] for v in n.values()) == pytest.approx(0.0)
    assert 0.5 * (n["torso"].bounds[0][1] + n["torso"].bounds[1][1]) == pytest.approx(0.0)
    assert m["torso"].bounds[0][2] == pytest.approx(62.0)             # la original no se toca


def test_compare_reads_a_longer_torso_and_suggests_the_scale():
    rows = {r.place: r for r in PR.compare(body(1.0), body(0.8), {"torso": 1.0})}
    t = rows["torso"]
    assert t.ratio[2] == pytest.approx(32.0 / 40.0) and t.ratio[0] == pytest.approx(1.0)
    assert t.suggested == pytest.approx((1.0, 1.0, 0.8)) and t.nivel == "no"
    assert "Z +20 %" in t.text() and "X igual" in t.text()
    assert rows["cabeza"].nivel == "ok" and rows["pierna"].nivel == "ok"
    again = {r.place: r for r in PR.compare(body(1.0), body(0.8), {"torso": (1.0, 1.0, 1.1)})}["torso"]
    assert again.suggested[2] == pytest.approx(1.1 * 0.8)             # sugerida = actual × razón


def test_compare_without_a_piece_says_so():
    m = body()
    m.pop("pelvis")
    row = {r.place: r for r in PR.compare(m, body())}["pelvis"]
    assert row.nivel == "sin_dato" and row.suggested is None and "sin pieza" in row.text()


def test_landmarks_are_the_reference_heights():
    marks = dict(PR.landmarks(PR.dims(body())))
    assert marks["coronilla"] == 100.0 and marks["cintura"] == 50.0 and marks["pies"] == 0.0 and marks["hombro"] == pytest.approx(88.0)


def test_render_view_paints_each_piece_and_mirrors_the_limbs():
    m = body()
    b = PR.view_bounds(m, m, "frente")
    img = PR.render_view(m, "frente", b, px=0.5)
    assert img.shape[2] == 4 and img[..., 3].any()
    h0, v0, h1, v1 = b
    col = lambda x, z: tuple(img[int(img.shape[0] - 1 - (z - v0) / 0.5), int((x - h0) / 0.5)][:3])   # noqa: E731
    assert col(0, 95) == PR.PALETTE["cabeza"] and col(0, 70) == PR.PALETTE["torso"]
    assert col(13, 45) == PR.PALETTE["brazo"] and col(-13, 45) == PR.PALETTE["brazo"]              # el brazo +X también sale a −X
    assert img[int(img.shape[0] - 1 - (3 - v0) / 0.5), int((-15 - h0) / 0.5)][3] == 0               # fuera de la figura, vacío
    side = PR.render_view(m, "costado", PR.view_bounds(m, m, "costado"))
    top = PR.render_view(m, "arriba", PR.view_bounds(m, m, "arriba"))
    assert side[..., 3].any() and top[..., 3].any()


def test_same_bounds_for_both_sides_make_them_comparable():
    a, b = body(1.0), body(0.8)
    bounds = PR.view_bounds(a, b, "frente")
    assert PR.render_view(a, "frente", bounds).shape == PR.render_view(b, "frente", bounds).shape


# ------------------------------------------------------------------ sesión y controlador
@pytest.fixture()
def ctrl(tmp_path):
    stl = str(tmp_path / "muneco.stl")
    make_doll().export(stl)
    root = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, root)
    c = Controller()
    c.session = Session(demo_project())
    c.session.load(stl)
    c.session.open_catalog(root)
    return c


def test_controller_lists_the_reference_figures(ctrl):
    assert ctrl.proportion_figures()
    assert Controller().proportion_figures() == []


def test_snapshot_divides_the_figure_and_compares_it_with_the_reference(ctrl):
    fig = ctrl.proportion_figures()[0]
    job = ctrl.proportions_job(fig)
    assert job is not None and ctrl.busy
    snap = job()
    ctrl.finish_proportions()
    assert not ctrl.busy
    assert {"torso", "brazo", "pierna"} <= set(snap["model"]) and {"torso", "brazo", "pierna"} <= set(snap["ref"])
    assert ctrl.session.project.part_scales                                  # quedó dividida (escalas ×1)
    rows = PR.compare(PR.normalize(snap["model"]), PR.normalize(snap["ref"]), snap["scales"])
    assert [r.place for r in rows] == list(PR.PLACES)
    assert all(r.suggested is not None for r in rows if r.place in ("torso", "brazo", "pierna"))


def test_scaling_a_piece_changes_the_next_snapshot(ctrl):
    fig = ctrl.proportion_figures()[0]
    first = ctrl.proportions_job(fig)()
    ctrl.finish_proportions()
    assert ctrl.set_part_scale("brazo", (1.0, 1.0, 1.25))
    second = ctrl.proportions_job(fig)()
    ctrl.finish_proportions()
    z1, z2 = (PR.dims(s["model"])["brazo"].size[2] for s in (first, second))
    assert z2 == pytest.approx(z1 * 1.25, rel=0.02)
    assert second["scales"]["brazo"] == (1.0, 1.0, 1.25)


def test_job_needs_an_open_catalog(ctrl):
    ctrl.session.catalog_root = None
    assert ctrl.proportions_job("x") is None and "catálogo" in ctrl.message


def test_overlay_shows_the_reference_in_grey_and_the_model_over_it():
    model, ref = body(1.0), body(0.8)
    b = PR.view_bounds(model, ref, "frente")
    img = PR.render_overlay(model, ref, "frente", b)
    h0, v0, *_ = b
    px = lambda x, z: tuple(img[int(img.shape[0] - 1 - (z - v0) / 0.5), int((x - h0) / 0.5)])   # noqa: E731
    assert px(0, 85)[:3] == PR.PALETTE["torso"] and px(0, 85)[3] == 170   # el torso del modelo (más largo) sobresale del patrón: color propio
    assert px(0, 60)[3] == 255                                              # donde están los dos, se ve mezclado
    assert px(-30, 20)[3] == 0


def test_bgra_premultiplied_layout():
    img = np.array([[[200, 100, 50, 128], [10, 20, 30, 0]]], np.uint8)
    out = PR.bgra_premultiplied(img)
    assert list(out[:4]) == [25, 50, 100, 128] and list(out[4:8]) == [0, 0, 0, 0]


def test_guide_rows_fall_on_the_right_pixel():
    b = (-10.0, -4.0, 10.0, 104.0)
    rows = dict(PR.guide_rows(b, 0.5, [("pies", 0.0), ("coronilla", 100.0)]))
    assert rows["pies"] == pytest.approx(216 - 8.0) and rows["coronilla"] == pytest.approx(216 - 208.0)


def test_harmonize_merges_the_pelvis_when_only_one_side_has_it():
    model, ref = body(), body()
    ref.pop("pelvis")
    m, r, note = PR.harmonize(model, ref)
    assert "pelvis" not in m and "pelvis" not in r and "torso entero" in note
    assert m["torso"].bounds[0][2] == pytest.approx(35.0) and m["torso"].bounds[1][2] == pytest.approx(90.0)   # torso 50–90 + pelvis 35–50
    assert "pelvis" in model and model["torso"].bounds[0][2] == pytest.approx(50.0)                            # las originales no cambian
    same = PR.harmonize(body(), body())
    assert same[2] is None and "pelvis" in same[0] and "pelvis" in same[1]
