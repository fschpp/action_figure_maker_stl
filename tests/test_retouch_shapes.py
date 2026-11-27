"""Retoque · formas (línea, círculo, cuadrado) sin GTK: geometría, sesión y editor. Borde y relleno llevan el rol elegido."""
import numpy as np
import pytest

from afm.catalog import Catalog
from afm.gui import retouch_shapes as SH
from afm.gui.retouch_editor import RetouchEditor
from afm.gui.retouch_session import PAINT, RetouchSession
from afm.retouch import MAX_STROKES, RetouchError
from test_catalog_retouch import REF, base_line, line  # noqa: F401  (fixtures)
from test_retouch_editor import IMG, WIDGET, center_click

Z = np.array([0.0, 0.0, 1.0])
U, V = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])


def flat(P, n, tol):
    """Plano z = 0 de ±20 mm: todo punto dentro cae sobre él."""
    P = np.asarray(P, float)
    return (np.array([P[0], P[1], 0.0]), Z) if abs(P[0]) <= 20 and abs(P[1]) <= 20 else None


# ------------------------------------------------------------------ geometría
def test_tangent_basis_is_orthonormal_and_follows_the_camera_right():
    u, v = SH.tangent_basis(Z, (1, 0, 0.5))
    assert np.allclose(u, U) and np.allclose(v, V) and abs(u @ Z) < 1e-9
    u2, v2 = SH.tangent_basis((1, 0, 0), (1, 0, 0))                   # de canto: igual da una base válida
    assert abs(np.linalg.norm(u2) - 1) < 1e-9 and abs(u2 @ v2) < 1e-9


def test_circle_is_one_stroke_with_the_dragged_radius_and_clipped():
    (c, r, n), = SH.samples(SH.CIRCLE, (1, 2, 0), (4, 6, 0), Z, U, V, 1.5, flat)
    assert c == (1.0, 2.0, 0.0) and r == pytest.approx(5.0) and n == (0.0, 0.0, 1.0)
    assert SH.samples(SH.CIRCLE, (0, 0, 0), (100, 0, 0), Z, U, V, 1.5, flat)[0][1] == SH.MAX_RADIUS_MM


def test_line_goes_from_a_to_b_with_the_brush_radius():
    items = SH.samples(SH.LINE, (0, 0, 0), (10, 0, 0), Z, U, V, 1.0, flat)
    xs = [c[0] for c, _r, _n in items]
    assert xs[0] == pytest.approx(0.0) and xs[-1] == pytest.approx(10.0) and len(items) >= 20
    assert all(r == pytest.approx(1.0) for _c, r, _n in items)
    assert max(np.diff(xs)) <= 0.5 + 1e-9                                # separación = radio · 0,5


def test_square_covers_the_area_and_stays_inside_its_side():
    items = SH.samples(SH.SQUARE, (0, 0, 0), (6, 3, 0), Z, U, V, 1.0, flat)      # mitad del lado = max(6, 3) = 6
    assert SH.half_side((0, 0, 0), (6, 3, 0), U, V) == 6.0
    for c, r, _n in items:
        assert max(abs(c[0]), abs(c[1])) + r <= 6.0 + 1e-9                # ninguna bola se sale del cuadrado
    # cobertura: toda muestra del cuadrado queda a ≤ r de alguna pincelada (las esquinas salen redondeadas por el radio del pincel: se excluyen)
    g = np.linspace(-5.9, 5.9, 25)
    C = np.array([c[:2] for c, _r, _n in items]); R = np.array([r for _c, r, _n in items])
    for x in g:
        for y in g:
            if abs(x) > 6.0 - 1.0 and abs(y) > 6.0 - 1.0:
                continue
            assert (np.hypot(C[:, 0] - x, C[:, 1] - y) <= R + 1e-9).any(), (x, y)


def test_points_that_miss_the_piece_are_skipped():
    items = SH.samples(SH.LINE, (15, 0, 0), (30, 0, 0), Z, U, V, 1.0, flat)     # de 20 en adelante no hay pieza
    assert items and max(c[0] for c, _r, _n in items) <= 20.0


def test_outline_segments_shape():
    assert SH.outline(SH.LINE, (0, 0, 0), (1, 0, 0), Z, U, V).shape == (1, 2, 3)
    assert SH.outline(SH.CIRCLE, (0, 0, 0), (2, 0, 0), Z, U, V).shape == (SH.CIRCLE_SEGMENTS, 2, 3)
    sq = SH.outline(SH.SQUARE, (0, 0, 0), (2, 1, 0), Z, U, V)
    assert sq.shape == (4, 2, 3) and np.allclose(sq[0, 1], sq[1, 0])
    with pytest.raises(ValueError):
        SH.outline("estrella", (0, 0, 0), (1, 0, 0), Z, U, V)


# ------------------------------------------------------------------ sesión
def test_session_shape_paints_with_the_role_as_one_undoable_step():
    s = RetouchSession(None, ["piel", "oro"])
    s.set_shape(SH.SQUARE)
    assert s.mode == PAINT and s.shape == SH.SQUARE
    s.set_role("oro")
    n = s.add_shape([((0, 0, 0), 1.0, (0, 0, 1)), ((1, 0, 0), 1.0, (0, 0, 1))])
    assert n == 2 and len(s) == 2 and all(st.rol == "oro" for st in s.retouch().strokes)
    assert s.undo() and len(s) == 0 and s.redo() and len(s) == 2


def test_session_shape_with_paint_orphan_writes_both_kinds():
    s = RetouchSession(None, ["piel", "oro"])
    s.set_paint_orphan(True)
    s.set_shape(SH.CIRCLE)
    assert s.add_shape([((0, 0, 0), 2.0, (0, 0, 1))]) == 2
    assert sorted(st.huerfana for st in s.retouch().strokes) == [False, True]


def test_session_set_mode_leaves_the_shape_and_bad_shapes_are_rejected():
    s = RetouchSession(None, ["piel", "oro"])
    s.set_shape(SH.LINE)
    s.set_mode("borrar")
    assert s.shape is None
    with pytest.raises(RetouchError):
        s.set_shape("estrella")


def test_session_shape_over_the_limit_changes_nothing():
    s = RetouchSession(None, ["piel", "oro"])
    s.set_shape(SH.LINE)
    s._strokes = [object()] * (MAX_STROKES - 1)
    with pytest.raises(RetouchError, match="no entran"):
        s.add_shape([((0, 0, 0), 1.0, None), ((1, 0, 0), 1.0, None)])
    assert len(s._strokes) == MAX_STROKES - 1 and not s.can_undo


# ------------------------------------------------------------------ editor
@pytest.fixture()
def ed(line):
    cat = Catalog.open(line)
    meta, mesh = cat.load(REF)
    return RetouchEditor(cat, meta, mesh)


@pytest.mark.parametrize("kind", SH.SHAPES)
def test_editor_draws_each_shape_and_undo_removes_it(ed, kind):
    x, y = center_click(ed)
    ed.session.set_shape(kind)
    ed.session.radius = 0.5
    assert ed.press(x, y, WIDGET, IMG) and ed.shaping
    ed.move(x + 30, y + 10, WIDGET, IMG)
    assert ed.hover_segments() is not None                                 # el contorno se ve mientras se arrastra
    n = ed.release()
    assert n >= 1 and ed.dirty and not ed.shaping and ed.hover_segments() is None
    assert len(ed.session) == n and ed.session.undo() and not ed.dirty


def test_editor_shape_ignores_polygon_mode_and_hover_of_the_brush(ed):
    x, y = center_click(ed)
    ed.session.set_polygon(True)
    ed.session.set_shape(SH.CIRCLE)
    assert ed.hover(x, y, WIDGET, IMG) is False and ed.hover_segments() is None
    ed.press(x, y, WIDGET, IMG); ed.move(x + 20, y, WIDGET, IMG); ed.release()
    assert ed.dirty and all(s.tri is None for s in ed.session.retouch().strokes)


def test_editor_shape_press_off_piece_returns_false(ed):
    ed.session.set_shape(SH.LINE)
    assert ed.press(0.5, 0.5, WIDGET, IMG) is False and not ed.shaping


def test_editor_click_without_drag_makes_a_small_shape(ed):
    x, y = center_click(ed)
    ed.session.set_shape(SH.SQUARE)
    ed.press(x, y, WIDGET, IMG)
    assert ed.release() >= 1
