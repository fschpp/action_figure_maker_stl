"""Vista previa a color en «Armar figura desde catálogo» (`FigureBuilder.preview_color`, `piece_color_parts`), lógica sin GTK."""
import numpy as np
import pytest

from afm.catalog import Catalog
from afm.gui import figure_builder as FB

from test_catalog_roles import odd_frame, textured_from, two_color_palette, package_of, new_meta  # noqa: F401
from test_figure_builder import _doll, root  # noqa: F401  (fixtures)
from color_helpers import write_textured_fbx


def _color_piece(root, ref_id, tmp_path, roles=False):
    cat = Catalog.open(root)
    cat.set_palette(two_color_palette())
    meta, mesh = cat.load(ref_id)
    tm = textured_from(mesh, odd_frame())
    if roles:
        saved, _w = cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=package_of(tm, two_color_palette()))
    else:
        fbx = write_textured_fbx(tmp_path / f"{ref_id}.fbx", tm)
        saved, _w = cat.add_piece(mesh, new_meta(meta), new_version=True, color_fbx=fbx)
    return saved


def _colors(parts):
    return {tuple(int(round(x * 255)) for x in c[:3]) for _n, _m, c in parts}


def test_group_preview_colors_only_pieces_that_carry_color(root, tmp_path):
    saved = _color_piece(root, "brazo-muneco-01", tmp_path)
    b = _doll(root)
    b.set_choice("brazo", saved.ref)
    flat, _ = b.preview()
    assert b.has_colored_choice()
    parts, notes = b.preview_color()
    assert notes == []
    names = [n for n, _m, _c in parts]
    assert "torso" in names and "cabeza" in names                                  # sin color: igual que la vista de siempre
    assert any(n.startswith("brazo_c_") for n in names) and any(n.startswith("brazo_der_c_") for n in names)
    assert "brazo" not in names                                                    # el brazo salió partido por color
    pc = {n: c for n, _m, c in parts}
    assert pc["torso"] == FB.PREVIEW_COLORS["torso"]
    arm_cols = {tuple(c[:3]) for n, _m, c in parts if n.startswith("brazo_c_")}
    assert any(c[0] > c[2] for c in arm_cols) and any(c[2] > c[0] for c in arm_cols)      # rojo y azul de la fuente
    flat_arm = next(m for n, m, _c in flat if n == "brazo")
    col_arm = [m for n, m, _c in parts if n.startswith("brazo_c_")]
    assert min(m.bounds[0][0] for m in col_arm) == pytest.approx(flat_arm.bounds[0][0], abs=1e-6)     # el color cae sobre la pieza puesta, no en otro lado


def test_group_preview_mirrors_the_color_to_the_other_side(root, tmp_path):
    saved = _color_piece(root, "brazo-muneco-01", tmp_path, roles=True)
    b = _doll(root)
    b.set_choice("brazo", saved.ref)
    parts, _ = b.preview_color()
    left = [m for n, m, _c in parts if n.startswith("brazo_c_")]
    right = [m for n, m, _c in parts if n.startswith("brazo_der_c_")]
    assert left and right
    assert min(m.bounds[0][0] for m in left) == pytest.approx(-max(m.bounds[1][0] for m in right), abs=1e-6)


def test_figure_palette_override_changes_the_preview_color(root, tmp_path):
    saved = _color_piece(root, "cabeza-muneco-01", tmp_path, roles=True)
    b = _doll(root)
    b.set_choice("cabeza", saved.ref)
    before = _colors([p for p in b.preview_color()[0] if p[0].startswith("cabeza_c_")])
    b.set_override("rojo", "#00FF00")
    after = _colors([p for p in b.preview_color()[0] if p[0].startswith("cabeza_c_")])
    assert before != after and any(c[1] > 200 and c[0] < 80 for c in after)       # el rojo pasó a verde solo en esta figura
    b.clear_override()
    assert _colors([p for p in b.preview_color()[0] if p[0].startswith("cabeza_c_")]) == before


def test_single_piece_preview_and_no_color_cases(root, tmp_path):
    saved = _color_piece(root, "brazo-muneco-01", tmp_path)
    b = _doll(root)
    cat = b.catalog()
    meta, mesh = cat.load(saved.ref, check_hash=False)
    parts = b.piece_color_parts(meta, mesh)
    assert parts and sum(len(m.faces) for _n, m, _c in parts) >= len(mesh.faces)
    plain, pmesh = cat.load("brazo-muneco-01@1")
    assert b.piece_color_parts(plain, pmesh) is None
    b2 = _doll(root)
    b2.set_choice("brazo", "brazo-muneco-01@1")                                    # la versión sin color
    assert not b2.has_colored_choice()


def test_missing_color_file_falls_back_to_flat_with_a_note(root, tmp_path):
    import os

    saved = _color_piece(root, "brazo-muneco-01", tmp_path)
    b = _doll(root)
    b.set_choice("brazo", saved.ref)
    os.remove(b.catalog().color_file(saved.ref))
    parts, notes = b.preview_color()
    assert any(saved.ref in n for n in notes)
    assert "brazo" in [n for n, _m, _c in parts]                                   # vuelve al color plano de su tipo


def test_fit_affine_recovers_rotation_mirror_and_rejects_deformation():
    from afm import colorpreview as CP

    rng = np.random.default_rng(1)
    V = rng.random((50, 3)) * 20
    th = 0.7
    R = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1]])
    W = (V * [-1, 1, 1]) @ R.T + [3, -2, 5]
    M = CP.fit_affine(V, W)
    assert M is not None and np.linalg.det(M[:3, :3]) < 0
    assert np.allclose(V @ M[:3, :3].T + M[:3, 3], W, atol=1e-6)
    W2 = W.copy()
    W2[0] += 5
    assert CP.fit_affine(V, W2) is None and CP.fit_affine(V, W[:-1]) is None
