"""«Polígono entero»: el pincel de radio mínimo no llega a ninguna muestra de color; la pincelada de polígono pinta el triángulo de la pieza."""
import numpy as np
import pytest

from afm.colorsrc import ColorSource
from afm.retouch import FORMAT_ORPHAN, FORMAT_TRI, Retouch, RetouchError, Stroke, in_triangle_prism, transform_strokes, triangle_of
from afm.gui.retouch_session import ERASE, ORPHAN, PAINT, RetouchSession
from test_retouch import cube_package, palette

TRI = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (0.0, 3.0, 0.0))


def _src(spacing=0.25) -> ColorSource:
    return ColorSource(cube_package().with_roles(("azul", "oro")).to_textured(palette()), spacing_mm=spacing)


def _top_faces(src):
    """Caras de la tapa del cubo (z = +5): son dos triángulos coplanares."""
    return [i for i in range(len(src.faces)) if np.allclose(src._tris[i][:, 2], 5.0)]


# ------------------------------------------------------------------ modelo
def test_roundtrip_and_format_4_only_with_polygons():
    s = Stroke((1, 1, 0), 2.5, "azul", (0, 0, 1), tri=TRI)
    assert Stroke.from_dict(s.to_dict()) == s and s.tri == TRI
    assert Retouch([s]).to_dict()["formato"] == FORMAT_TRI
    assert Retouch([Stroke((0, 0, 0), 1, "azul")]).to_dict()["formato"] != FORMAT_TRI          # sin polígonos, bytes de siempre
    assert Retouch.from_dict(Retouch([s, Stroke((0, 0, 0), 1, "oro", huerfana=True)]).to_dict()).strokes[1].huerfana


def test_older_formats_reject_polygons_and_bad_triangles():
    d = Retouch([Stroke((1, 1, 0), 2.5, "azul", tri=TRI)]).to_dict()
    d["formato"] = FORMAT_ORPHAN
    with pytest.raises(RetouchError):
        Retouch.from_dict(d)
    with pytest.raises(RetouchError):
        Stroke((0, 0, 0), 1, "azul", tri=TRI[:2])
    with pytest.raises(RetouchError):
        Stroke((0, 0, 0), 1, "azul", tri=((0, 0, 0), (1, 1, 1), (2, 2, 2)))          # degenerado
    with pytest.raises(RetouchError):
        Stroke((0, 0, 0), 1, "azul", relleno=True, tri=TRI)


def test_transform_and_mirror_carry_the_triangle():
    s = Stroke((1, 1, 0), 2.5, "azul", (0, 0, 1), tri=TRI)
    M = np.eye(4)
    M[:3, 3] = (10, 0, 0)
    t = transform_strokes([s], M)[0]
    assert np.allclose(np.asarray(t.tri) - np.asarray(TRI), (10, 0, 0))
    m = Retouch([s]).mirrored().strokes[0]
    assert np.allclose(np.asarray(m.tri)[:, 0], -np.asarray(TRI)[:, 0])


def test_in_triangle_prism():
    P = np.array([[1, 1, 0], [1, 1, 0.3], [1, 1, 0.6], [3.9, 2.9, 0], [-1, 0.5, 0], [1, 1, -0.3]])
    assert list(in_triangle_prism(P, TRI)) == [True, True, False, False, False, True]
    c, r = triangle_of(TRI)
    assert r >= np.linalg.norm(np.asarray(TRI) - c, axis=1).max()


# ------------------------------------------------------------------ color
def test_tiny_ball_misses_samples_but_the_polygon_paints_all_of_it():
    src = _src()
    face = _top_faces(src)[0]
    tri = tuple(map(tuple, src._tris[face]))
    c = np.asarray(tri).mean(axis=0)
    ball = _src()
    ball.paint([Stroke(c + (0, 0, 0.0), 0.05, "azul", (0, 0, 1))])
    on_face = src.sample_face == face
    painted_ball = (ball.sample_label[on_face] == ball.label_roles.index("azul")).mean()
    assert painted_ball < 0.2                                             # el bug: el pincel mínimo casi no toca nada
    rep = src.paint([Stroke(c, 5.0, "azul", (0, 0, 1), tri=tri)])
    azul = src.label_roles.index("azul")
    assert (src.sample_label[on_face] == azul).mean() > 0.97
    other = np.isin(src.sample_face, [f for f in _top_faces(src) if f != face])
    assert (src.sample_label[other] == azul).mean() < 0.06                # el triángulo vecino (misma cara plana) casi no se toca
    assert (src.sample_label[src.sample_xyz[:, 2] < 4.0] != azul).all()   # ni las otras caras del cubo
    assert rep["muestras"] > 0


def test_eraser_polygon_restores_the_package_label():
    src = _src()
    face = _top_faces(src)[0]
    tri = tuple(map(tuple, src._tris[face]))
    c = np.asarray(tri).mean(axis=0)
    src.paint([Stroke(c, 5.0, "azul", (0, 0, 1), tri=tri)])
    src.paint([Stroke(c, 5.0, None, (0, 0, 1), tri=tri)])
    assert (src.sample_label == src.label_roles.index("piel")).all()


def test_orphan_polygon_paints_the_points_without_origin_over_the_triangle():
    src = _src()
    tri = ((-20.0, -20.0, 20.0), (-10.0, -20.0, 20.0), (-20.0, -10.0, 20.0))        # lejos del cubo: nada tiene origen
    src.paint([Stroke((-16, -16, 20), 4.0, "oro", (0, 0, 1), huerfana=True, tri=tri)])
    P = np.array([[-18, -18, 20.0], [-12, -12, 20.0], [-18, -18, 20.2]])             # dentro / fuera del triángulo / dentro, a 0,2 mm del plano
    q = src.query(P, np.tile([0, 0, 1.0], (3, 1)))
    assert list(q.assigned) == [True, False, True]
    assert tuple(q.rgb[0]) != tuple(q.rgb[1])


# ------------------------------------------------------------------ sesión
def _session():
    s = RetouchSession(None, ["piel", "azul"])
    s.set_polygon(True)
    return s


def test_session_polygon_mode_adds_one_stroke_per_triangle_and_undoes_together():
    s = _session()
    t2 = ((4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
    assert s.begin_stroke((1, 1, 0), (0, 0, 1), TRI) == 1
    assert s.add_point((1.2, 1.1, 0), (0, 0, 1), TRI) == 0                 # el mismo triángulo no se repite
    assert s.add_point((3, 2.5, 0), (0, 0, 1), t2) == 1
    assert s.end_stroke() == 2 and len(s) == 2
    assert all(st.tri is not None and st.r >= 0.05 for st in s.retouch().strokes)
    assert s.undo() and len(s) == 0


def test_session_polygon_applies_to_erase_and_orphan_and_off_means_ball():
    s = _session()
    s.set_mode(ERASE)
    s.begin_stroke((1, 1, 0), (0, 0, 1), TRI)
    s.end_stroke()
    s.set_mode(ORPHAN)
    s.begin_stroke((1, 1, 0), (0, 0, 1), ((0, 0, 1), (4, 0, 1), (0, 3, 1)))
    s.end_stroke()
    a, b = s.retouch().strokes
    assert a.rol is None and a.tri and b.huerfana and b.tri
    s.set_polygon(False)
    s.set_mode(PAINT)
    s.begin_stroke((1, 1, 0), (0, 0, 1), TRI)
    s.end_stroke()
    assert s.retouch().strokes[-1].tri is None


# ------------------------------------------------------------------ editor
from test_catalog_retouch import REF, base_line, line  # noqa: E402,F401  (fixtures)
from afm.catalog import Catalog  # noqa: E402
from afm.gui.retouch_editor import RetouchEditor  # noqa: E402
from test_retouch_editor import IMG, WIDGET, center_click  # noqa: E402


def test_editor_polygon_click_paints_the_triangle_under_the_mouse(line):
    cat = Catalog.open(line)
    meta, mesh = cat.load(REF)
    e = RetouchEditor(cat, meta, mesh)
    e.session.set_polygon(True)
    x, y = center_click(e)
    assert e.hover(x, y, WIDGET, IMG) and len(e.hover_segments()) == 3         # se ilumina un solo polígono
    assert e.press(x, y, WIDGET, IMG)
    e.release()
    st = e.session.retouch().strokes[-1]
    tri = np.asarray(st.tri)
    assert np.allclose(np.sort(tri, axis=0), np.sort(np.asarray(mesh.triangles)[e._face], axis=0), atol=1e-3)
    assert in_triangle_prism(np.asarray(st.c)[None], st.tri)[0]
