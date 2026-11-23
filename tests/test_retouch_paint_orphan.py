"""«Pintar» también pinta lo que la fuente de color no cubre: cada punto del mouse suma una pincelada normal y una «sin origen»."""
from __future__ import annotations

import pytest

from afm.gui.retouch_session import ERASE, ORPHAN, PAINT, RetouchSession
from afm.retouch import MAX_STROKES

TRI = ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0))


def _s(**kw):
    s = RetouchSession(None, ["rojo", "azul"], radius=2.0)
    s.set_paint_orphan(True)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_default_session_is_unchanged():
    s = RetouchSession(None, ["rojo"], radius=2.0)
    s.begin_stroke((0, 0, 0))
    s.end_stroke()
    assert [st.huerfana for st in s.retouch().strokes] == [False]


def test_paint_adds_normal_and_orphan_stroke():
    s = _s()
    assert s.begin_stroke((0, 0, 0)) == 2
    st = s.retouch().strokes
    assert [x.huerfana for x in st] == [False, True] and all(x.rol == "rojo" and x.c == st[0].c for x in st)


def test_drag_pairs_every_point_and_undo_is_one_step():
    s = _s()
    s.begin_stroke((0, 0, 0))
    s.add_point((3, 0, 0))
    s.end_stroke()
    st = s.retouch().strokes
    assert len(st) % 2 == 0 and len(st) >= 4
    assert [x.huerfana for x in st] == [False, True] * (len(st) // 2)
    assert s.undo() and len(s.retouch().strokes) == 0


def test_polygon_paints_both_strokes_on_the_triangle():
    s = _s(polygon=True)
    assert s.begin_stroke((0.5, 0.5, 0), tri=TRI) == 2
    assert s.add_point((0.6, 0.6, 0), tri=TRI) == 0                  # mismo triángulo: no se repite
    st = s.retouch().strokes
    assert [x.huerfana for x in st] == [False, True] and all(x.tri is not None for x in st)


def test_erase_and_orphan_modes_not_doubled():
    s = _s()
    s.set_mode(ERASE)
    assert s.begin_stroke((0, 0, 0)) == 1
    s.end_stroke()
    s.set_mode(ORPHAN)
    s.begin_stroke((5, 0, 0))
    s.add_point((9, 0, 0))
    s.end_stroke()
    st = s.retouch().strokes
    assert st[0].rol is None and all(x.huerfana for x in st[1:])


def test_pair_never_splits_at_the_stroke_limit(monkeypatch):
    monkeypatch.setattr("afm.gui.retouch_session.MAX_STROKES", 3)
    s = _s()
    assert s.begin_stroke((0, 0, 0)) == 2
    s.add_point((10, 0, 0))
    assert s.truncated and len(s.retouch().strokes) == 2
