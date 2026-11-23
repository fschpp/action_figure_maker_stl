"""Retoque · pincelada «sin origen» (`Stroke.huerfana`, R11): pinta solo lo que la fuente de color no cubre.

Reusa la placa de 20 × 20 mm de `test_retouch_fill` (z = 0, normal +Z, toda «piel»): un punto a 1 mm por encima de la placa no tiene origen (eps 0,15)."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from afm import colorbake, colorpreview as CP, colorsrc
from afm.retouch import FORMAT, FORMAT_FILL, FORMAT_ORPHAN, Retouch, RetouchError, Stroke
from afm.gui.retouch_session import ERASE, ORPHAN, PAINT, RetouchSession

from test_colorbake_query import built, meshes, regs, src, unws  # noqa: F401  (fixtures compartidas)
from test_retouch_fill import plate


def _rgb(src, rol):
    return tuple(int(x) for x in src._lut[src.label_roles.index(rol)])


# ------------------------------------------------------------------ Stroke / archivo
def test_orphan_stroke_needs_role_and_excludes_fill():
    with pytest.raises(RetouchError, match="rol"):
        Stroke((0, 0, 0), 1.0, None, None, huerfana=True)
    with pytest.raises(RetouchError, match="relleno"):
        Stroke((0, 0, 0), 1.0, "oro", None, relleno=True, huerfana=True)
    s = Stroke((0, 0, 0), 1.0, "oro", (0, 0, 1), huerfana=True)
    assert s.huerfana and not s.relleno


def test_format_3_only_when_there_are_orphan_strokes():
    plain = Retouch([Stroke((0, 0, 0), 1.0, "oro")])
    fill = Retouch([Stroke((0, 0, 0), 1.0, "oro", relleno=True)])
    orphan = Retouch([Stroke((0, 0, 0), 1.0, "oro"), Stroke((1, 0, 0), 1.0, "rojo", huerfana=True)])
    assert plain.to_dict()["formato"] == FORMAT
    assert fill.to_dict()["formato"] == FORMAT_FILL
    d = orphan.to_dict()
    assert d["formato"] == FORMAT_ORPHAN and d["pinceladas"][1]["k"] == "huerfana" and "k" not in d["pinceladas"][0]
    assert Retouch.from_dict(json.loads(json.dumps(d))) == orphan
    assert "k" not in plain.to_dict()["pinceladas"][0]          # los archivos viejos quedan idénticos


def test_older_formats_reject_orphan_strokes():
    d = Retouch([Stroke((0, 0, 0), 1.0, "oro", huerfana=True)]).to_dict()
    for old in (FORMAT, FORMAT_FILL):
        with pytest.raises(RetouchError, match=FORMAT_ORPHAN):
            Retouch.from_dict({**d, "formato": old})


def test_save_load_and_mirror_keep_the_flag(tmp_path):
    rt = Retouch([Stroke((1, 2, 3), 1.0, "oro", (1, 0, 0), huerfana=True)])
    path = rt.save(tmp_path / "r.json")
    assert Retouch.load(path) == rt
    m = rt.mirrored()
    assert m.strokes[0].huerfana and m.strokes[0].c[0] == -1.0


# ------------------------------------------------------------------ ColorSource
def test_orphan_stroke_paints_only_points_without_origin():
    src = plate()
    before = src.sample_label.copy()
    rep = src.paint([Stroke((0, 0, 1.0), 2.0, "oro", huerfana=True)])
    assert rep["sin_origen"] == 1 and rep["muestras"] == 0
    assert (src.sample_label == before).all()                     # no toca las muestras
    P = np.array([[0, 0, 1.0],       # sin origen, dentro del radio
                  [0, 0, 0.0],       # CON origen, dentro del radio: no se pisa
                  [9, 9, 1.0]])      # sin origen, fuera del radio
    q = src.query(P, np.tile([0, 0, 1.0], (3, 1)))
    assert list(q.has_origin) == [False, True, False]
    assert list(q.assigned) == [True, False, False]
    assert tuple(q.rgb[0]) == _rgb(src, "oro")
    assert tuple(q.rgb[1]) == _rgb(src, "piel") and tuple(q.rgb[2]) == _rgb(src, "piel")
    assert q.label[0] == src.label_roles.index("oro")


def test_orphan_includes_points_rejected_only_by_normal():
    """El caso del diagnóstico: el FBX está a < eps pero con la normal opuesta, así que el punto cuenta como sin origen."""
    src = plate()
    src.paint([Stroke((0, 0, 0), 2.0, "oro", huerfana=True)])
    q = src.query(np.array([[0, 0, 0.0]]), np.array([[0, 0, -1.0]]))
    assert not q.has_origin[0] and q.assigned[0] and tuple(q.rgb[0]) == _rgb(src, "oro")


def test_stroke_normal_filters_orphan_points():
    src = plate()
    src.paint([Stroke((0, 0, 1.0), 2.0, "oro", (0, 0, -1.0), huerfana=True)])
    q = src.query(np.array([[0, 0, 1.0], [0, 0, 1.0]]), np.array([[0, 0, 1.0], [0, 0, -1.0]]))
    assert list(q.assigned) == [False, True]                       # solo la cara de normal compatible con la pincelada
    assert src.query(np.array([[0, 0, 1.0]])).assigned[0]          # sin normales en la consulta no se filtra


def test_last_orphan_stroke_wins_and_no_orphans_means_none():
    src = plate()
    assert src.query(np.array([[0, 0, 1.0]])).assigned is None
    src.paint([Stroke((0, 0, 1.0), 2.0, "oro", huerfana=True), Stroke((0, 0, 1.0), 1.0, "rojo", huerfana=True)])
    q = src.query(np.array([[0, 0, 1.0], [1.5, 0, 1.0]]))
    assert tuple(q.rgb[0]) == _rgb(src, "rojo") and tuple(q.rgb[1]) == _rgb(src, "oro")


def test_orphan_role_must_exist_in_the_source():
    with pytest.raises(ValueError, match="verde"):
        plate().paint([Stroke((0, 0, 1.0), 2.0, "verde", huerfana=True)])


def test_recolor_keeps_orphan_strokes_in_sync():
    src = plate()
    src.paint([Stroke((0, 0, 1.0), 2.0, "oro", huerfana=True)])
    lut = src._lut.copy()
    lut[src.label_roles.index("oro")] = (1, 2, 3)
    src.recolor(lut)
    assert tuple(src.query(np.array([[0, 0, 1.0]])).rgb[0]) == (1, 2, 3)


# ------------------------------------------------------------------ vista previa y horneado
class _Src:
    """Fuente falsa: x < 0 con origen (rojo); x ≥ 0 sin origen y su muestra más cercana es azul; `assigned` marca x > 4 (pintadas de verde)."""

    def __init__(self, tm=None, spacing_mm=0.25, with_field=True):
        self.with_field = with_field

    def query(self, points, normals=None, eps=0.15, min_dot=0.3, workers=1):
        P = np.atleast_2d(np.asarray(points, float))
        left = P[:, 0] < 0
        rgb = np.where(left[:, None], [200, 0, 0], [0, 0, 200]).astype(np.uint8)
        painted = P[:, 0] > 4
        rgb[painted] = (0, 200, 0)
        out = SimpleNamespace(rgb=rgb, has_origin=left if eps < 1e6 else np.ones(len(P), bool))
        if self.with_field:
            out.assigned = painted
        return out


def test_preview_respects_orphan_paint_and_neighbour_fills_the_rest(monkeypatch):
    import trimesh

    monkeypatch.setattr(colorsrc, "ColorSource", _Src)
    parts = CP.color_parts(None, trimesh.creation.box(extents=(10.0, 10.0, 10.0)))
    cols = {tuple(int(round(x * 255)) for x in c[:3]) for _n, _m, c in parts}
    assert cols == {(200, 0, 0), (0, 200, 0)}                      # lo pintado se queda verde; el resto copia al vecino con origen (rojo); nada azul


def test_preview_and_bake_tolerate_queries_without_assigned(monkeypatch):
    import trimesh

    monkeypatch.setattr(colorsrc, "ColorSource", lambda tm, spacing_mm=0.25: _Src(with_field=False))
    parts = CP.color_parts(None, trimesh.creation.box(extents=(10.0, 10.0, 10.0)))
    assert {tuple(int(round(x * 255)) for x in c[:3]) for _n, _m, c in parts} == {(200, 0, 0)}


def test_bake_flat_fill_skips_painted_texels(meshes, unws, regs, src):  # noqa: F811
    """`fill="#RRGGBB"` pisaba todo lo sin origen con el color plano; lo que una pincelada «sin origen» pintó se conserva (y no cuenta como origen)."""
    name = "brazo_izq"

    class Painted:
        def __init__(self, inner):
            self.inner = inner

        def query(self, points, normals=None, eps=0.15, **kw):
            q = self.inner.query(points, normals, eps=eps, **kw)
            P = np.atleast_2d(np.asarray(points, float))
            lost = ~q.has_origin
            q.assigned = lost & (P[:, 0] > np.median(P[lost, 0])) if lost.any() else lost        # la mitad de lo sin origen (la de x mayor)
            q.rgb = np.array(q.rgb, copy=True)
            q.rgb[q.assigned] = (9, 8, 7)
            return q

    ref = colorbake.bake_piece(meshes[name], unws[name], src, regs[name], fill="#123456", dilate_px=0)
    got = colorbake.bake_piece(meshes[name], unws[name], Painted(src), regs[name], fill="#123456", dilate_px=0)
    no = ref.mask & ~ref.origin
    painted = no & (got.atlas == (9, 8, 7)).all(axis=2)
    plain = no & (got.atlas == (0x12, 0x34, 0x56)).all(axis=2)
    assert painted.any() and plain.any()
    assert (painted | plain)[no].all()                              # cada texel sin origen: pintado o color plano, nunca otra cosa
    assert np.array_equal(got.origin, ref.origin) and got.stats["pct_origen"] == ref.stats["pct_origen"]
    assert np.array_equal(got.atlas[ref.origin], ref.atlas[ref.origin])


# ------------------------------------------------------------------ sesión
def test_session_orphan_mode_writes_orphan_strokes():
    s = RetouchSession(None, ["piel", "oro"])
    s.set_mode(ORPHAN)
    assert s.can_paint and s.mode == ORPHAN
    s.begin_stroke((0, 0, 0), (0, 0, 1))
    s.add_point((3, 0, 0), (0, 0, 1))
    assert s.end_stroke() >= 2
    assert all(st.huerfana and st.rol == "piel" and st.n == (0.0, 0.0, 1.0) for st in s.retouch().strokes)
    assert s.retouch().to_dict()["formato"] == FORMAT_ORPHAN
    s.set_role("oro")                                               # elegir un rol no saca del modo «sin origen» (solo el borrador vuelve a pintar)
    assert s.mode == ORPHAN
    assert s.undo() and len(s) == 0


def test_session_orphan_then_paint_and_erase_are_plain_strokes():
    s = RetouchSession(None, ["piel"])
    s.set_mode(ORPHAN)
    s.begin_stroke((0, 0, 0)); s.end_stroke()
    s.set_mode(PAINT)
    s.begin_stroke((5, 0, 0)); s.end_stroke()
    s.set_mode(ERASE)
    s.begin_stroke((9, 0, 0)); s.end_stroke()
    flags = [st.huerfana for st in s.retouch().strokes]
    assert flags == [True, False, False]
    with pytest.raises(RetouchError):
        s.set_mode("otro")
