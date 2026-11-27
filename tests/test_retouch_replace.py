"""Retoque · «Reemplazar color de la pieza» (`Stroke.desde`, formato `afm-retoque-5`): todo lo que tiene un rol pasa a otro, en toda la pieza.

Reusa la placa de 20 × 20 mm de `test_retouch_fill` (toda «piel»; roles de la fuente: piel, oro, rojo)."""
import json

import numpy as np
import pytest

from afm.gui.retouch_session import RetouchSession
from afm.retouch import FORMAT, FORMAT_ORPHAN, FORMAT_REPLACE, Retouch, RetouchError, Stroke, transform_strokes

from test_colorbake_query import built, meshes, regs, src, unws  # noqa: F401  (fixtures compartidas)
from test_retouch_fill import plate


def _rgb(src, rol):
    return tuple(int(x) for x in src._lut[src.label_roles.index(rol)])


def _repl(desde, a):
    return Stroke((0, 0, 0), 0.05, a, desde=desde)


# ------------------------------------------------------------------ Stroke / archivo
def test_replace_stroke_validation():
    assert _repl("piel", "oro").desde == "piel"
    with pytest.raises(RetouchError, match="mismo"):
        Stroke((0, 0, 0), 1.0, "oro", desde="oro")
    with pytest.raises(RetouchError, match="rol"):
        Stroke((0, 0, 0), 1.0, None, desde="oro")
    with pytest.raises(RetouchError, match="inválido"):
        Stroke((0, 0, 0), 1.0, "rojo", desde="Oro!")
    with pytest.raises(RetouchError):
        Stroke((0, 0, 0), 1.0, "rojo", huerfana=True, desde="oro")


def test_format_5_only_when_there_is_a_replace_and_roundtrip(tmp_path):
    plain = Retouch([Stroke((0, 0, 0), 1.0, "oro")])
    rt = Retouch([Stroke((0, 0, 0), 1.0, "oro"), _repl("oro", "rojo")])
    assert plain.to_dict()["formato"] == FORMAT
    d = rt.to_dict()
    assert d["formato"] == FORMAT_REPLACE and d["pinceladas"][1]["k"] == "reemplazo" and d["pinceladas"][1]["d"] == "oro"
    assert Retouch.from_dict(json.loads(json.dumps(d))) == rt
    assert Retouch.load(rt.save(tmp_path / "r.json")) == rt
    assert "k" not in plain.to_dict()["pinceladas"][0]


def test_replace_mixes_with_orphan_strokes_in_format_5():
    rt = Retouch([Stroke((0, 0, 0), 1.0, "oro", huerfana=True), _repl("oro", "rojo")])
    assert Retouch.from_dict(rt.to_dict()) == rt


def test_older_formats_reject_replace():
    d = Retouch([_repl("oro", "rojo")]).to_dict()
    for old in (FORMAT, FORMAT_ORPHAN):
        with pytest.raises(RetouchError, match=FORMAT_REPLACE):
            Retouch.from_dict({**d, "formato": old})
    bad = {**d, "pinceladas": [{"c": [0, 0, 0], "r": 1.0, "rol": "rojo", "k": "reemplazo"}]}
    with pytest.raises(RetouchError, match="«d»"):
        Retouch.from_dict(bad)


def test_roles_used_includes_the_searched_role_and_transform_keeps_it():
    rt = Retouch([_repl("oro", "rojo")])
    assert rt.roles_used() == ["oro", "rojo"]
    M = np.eye(4)
    M[0, 0] = -1.0
    assert transform_strokes(rt.strokes, M)[0].desde == "oro"
    assert rt.mirrored().strokes[0].desde == "oro"


# ------------------------------------------------------------------ ColorSource
def test_replace_changes_every_sample_with_the_role():
    src = plate()
    src.paint([Stroke((0, 0, 0), 2.0, "oro"), Stroke((8, 8, 0), 1.0, "oro")])
    oro = src.label_roles.index("oro")
    n_oro = int((src.sample_label == oro).sum())
    assert n_oro > 0
    n_piel = int((src.sample_label == src.label_roles.index("piel")).sum())
    rep = src.paint([_repl("oro", "rojo")])
    assert rep["muestras"] == n_oro and not (src.sample_label == oro).any()
    assert int((src.sample_label == src.label_roles.index("rojo")).sum()) == n_oro
    assert int((src.sample_label == src.label_roles.index("piel")).sum()) == n_piel      # lo demás no se toca
    q = src.query(np.array([[0, 0, 0.0], [-8, -8, 0.0]]))
    assert tuple(q.rgb[0]) == _rgb(src, "rojo") and tuple(q.rgb[1]) == _rgb(src, "piel")


def test_replace_of_a_role_not_in_the_piece_changes_nothing():
    src = plate()
    before = src.sample_label.copy()
    rep = src.paint([_repl("oro", "rojo")])
    assert rep["muestras"] == 0 and (src.sample_label == before).all()


def test_replace_order_matters_later_strokes_win():
    src = plate()
    src.paint([_repl("piel", "oro"), Stroke((0, 0, 0), 2.0, "rojo")])
    q = src.query(np.array([[0, 0, 0.0], [8, 8, 0.0]]))
    assert tuple(q.rgb[0]) == _rgb(src, "rojo") and tuple(q.rgb[1]) == _rgb(src, "oro")


def test_replace_also_changes_earlier_orphan_strokes_but_not_later_ones():
    src = plate()
    src.paint([Stroke((0, 0, 1.0), 2.0, "oro", huerfana=True), _repl("oro", "rojo"), Stroke((8, 8, 1.0), 1.0, "oro", huerfana=True)])
    q = src.query(np.array([[0, 0, 1.0], [8, 8, 1.0]]))
    assert tuple(q.rgb[0]) == _rgb(src, "rojo") and tuple(q.rgb[1]) == _rgb(src, "oro")
    assert q.label[0] == src.label_roles.index("rojo")


def test_eraser_returns_to_the_original_role_not_the_replaced_one():
    src = plate()
    src.paint([_repl("piel", "oro"), Stroke((0, 0, 0), 2.0, None)])
    q = src.query(np.array([[0, 0, 0.0], [8, 8, 0.0]]))
    assert tuple(q.rgb[0]) == _rgb(src, "piel") and tuple(q.rgb[1]) == _rgb(src, "oro")


def test_replace_roles_must_exist_in_the_source():
    with pytest.raises(ValueError, match="verde"):
        plate().paint([_repl("verde", "oro")])
    with pytest.raises(ValueError, match="verde"):
        plate().paint([_repl("oro", "verde")])


# ------------------------------------------------------------------ sesión
def test_session_preview_does_not_change_the_session():
    s = RetouchSession(None, ["piel", "oro", "rojo"])
    rt = s.preview_replace("oro", "rojo")
    assert len(rt) == 1 and len(s) == 0 and not s.dirty and not s.can_undo


def test_session_add_replace_is_one_undoable_step():
    s = RetouchSession(Retouch([Stroke((0, 0, 0), 1.0, "oro")]), ["piel", "oro", "rojo"])
    assert s.add_replace("oro", "rojo") == 1
    assert len(s) == 2 and s.dirty and s.retouch().strokes[-1].desde == "oro"
    assert s.undo() and len(s) == 1 and not s.dirty
    assert s.redo() and len(s) == 2


def test_session_rejects_bad_replaces_without_changing_anything():
    s = RetouchSession(None, ["piel", "oro"])
    for args in (("oro", "oro"), ("oro", "verde"), ("verde", "oro")):
        with pytest.raises(RetouchError):
            s.add_replace(*args)
        with pytest.raises(RetouchError):
            s.preview_replace(*args)
    assert len(s) == 0 and not s.can_undo


def test_session_apply_writes_format_5_in_the_catalog_retouch():
    s = RetouchSession(None, ["piel", "oro", "rojo"])
    s.add_replace("oro", "rojo")
    assert s.retouch().to_dict()["formato"] == FORMAT_REPLACE

