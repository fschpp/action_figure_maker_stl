"""Vista previa del corte de «sin soportes» en «Armar figura desde catálogo»: figura sentada, plano de la cama y material cortado."""
import json
import os

import numpy as np
import pytest

from afm.gui import figure_builder as FB

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture()
def builder():
    b = FB.FigureBuilder(ROOT)
    r = json.load(open(os.path.join(ROOT, "recetas", "muneco.receta.json")))
    for place, ref in r["lugares"].items():
        if ref:
            b.choices[place] = ref
    b.flat = True
    return b


def test_plane_sits_depth_above_the_lowest_body_point(builder):
    builder.flat_depth = 1.0
    parts, plane, _notes = builder.preview_flat()
    torso_low = min(m.bounds[0][2] for n, m, _c in parts if n == "torso_corte" or n == "torso")
    assert torso_low == pytest.approx(0.0, abs=1e-6)                       # la cola, sin cortar, está en z = 0
    assert plane["z"] == pytest.approx(1.0, abs=1e-6)
    kept = [m for n, m, _c in parts if n == "torso"]
    assert kept and kept[0].bounds[0][2] == pytest.approx(plane["z"], abs=1e-6)      # lo que queda no baja del plano


def test_cut_material_is_shown_in_red_and_lies_under_the_plane(builder):
    builder.flat_depth = 3.0
    parts, plane, _ = builder.preview_flat()
    gone = [(n, m, c) for n, m, c in parts if n.endswith("_corte")]
    assert gone and all(c == FB.PREVIEW_CUT_COLOR for _n, _m, c in gone)
    assert all(m.bounds[1][2] <= plane["z"] + 1e-6 for _n, m, _c in gone)


def test_deeper_cut_removes_more(builder):
    def removed(depth):
        builder.flat_depth = depth
        parts, _p, _n = builder.preview_flat()
        return sum(abs(m.volume) for n, m, _c in parts if n.endswith("_corte"))
    assert removed(4.0) > removed(1.0) > 0


def test_legs_are_seated_and_arms_and_head_are_not_cut(builder):
    builder.flat_depth = 1.0
    parts, plane, _ = builder.preview_flat()
    names = {n for n, _m, _c in parts}
    assert {"pierna", "pierna_der", "brazo", "brazo_der", "cabeza"} <= names
    assert not any(n.startswith(("brazo", "cabeza", "mano")) and n.endswith("_corte") for n in names)
    leg = next(m for n, m, _c in parts if n == "pierna")
    assert np.ptp(leg.bounds[:, 1]) > np.ptp(leg.bounds[:, 2])             # sentada: la pierna se extiende hacia adelante, no hacia abajo


def test_warns_when_the_legs_float_above_the_plane(builder, monkeypatch):
    builder.flat_depth = 1.0
    monkeypatch.setattr(FB, "tr", lambda key, **kw: key + str(kw))
    parts, plane, notes = builder.preview_flat()
    low = min(m.bounds[0][2] for n, m, _c in parts if n == "pierna")
    assert low > plane["z"] + 0.05                                          # en el muñeco de ejemplo las piernas quedan en el aire
    assert any("corte_pierna_no_toca" in n for n in notes)


def test_bounds_cover_all_parts_and_the_plane(builder):
    parts, plane, _ = builder.preview_flat()
    lo, hi = plane["bounds"]
    assert all(m.bounds[0][2] >= lo[2] - 1e-6 and m.bounds[1][2] <= hi[2] + 1e-6 for _n, m, _c in parts)
    assert lo[2] <= plane["z"] <= hi[2]


def test_renderer_draws_the_bed_and_removes_it(builder):
    pytest.importorskip("moderngl")
    from afm.gui.catalog_manager import PreviewRenderer

    pr = PreviewRenderer(200, 150)
    if not pr.available:
        pytest.skip(pr.error or "sin GL")
    try:
        parts, plane, _ = builder.preview_flat()
        pr.render_parts(parts, 0, 0, bounds=plane["bounds"], plane=plane)
        assert PreviewRenderer.BED_NAME in pr._off.scene.items
        item = pr._off.scene.items[PreviewRenderer.BED_NAME]
        pr.render_parts(parts, 30, 10, bounds=plane["bounds"], plane=plane)          # girar la vista no vuelve a subir el plano
        assert pr._off.scene.items[PreviewRenderer.BED_NAME] is item
        pr.render_parts(parts, 0, 0)                                                  # sin plano: se quita
        assert PreviewRenderer.BED_NAME not in pr._off.scene.items
    finally:
        pr.close()
