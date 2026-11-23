"""Los lados de los polígonos siempre a la vista al retocar el color: claros sobre un color oscuro, oscuros sobre uno claro."""
import numpy as np
import pytest
import trimesh

from afm import colorpreview as CP


def _box():
    return trimesh.creation.box((10, 10, 10))


def _rgb(mesh, c):
    return np.tile(np.asarray(c, float), (len(mesh.faces), 1))


def test_dark_color_gets_light_edges_and_light_color_dark_edges():
    m = _box()
    light, dark = CP.edge_segments_by_contrast(m, _rgb(m, (0.02, 0.02, 0.03)))       # casi negro
    assert len(light) == len(m.edges_unique) and len(dark) == 0
    light, dark = CP.edge_segments_by_contrast(m, _rgb(m, (0.95, 0.95, 0.9)))        # casi blanco
    assert len(dark) == len(m.edges_unique) and len(light) == 0


@pytest.mark.parametrize("rgb,expect_light", [((0.0, 0.0, 0.0), True), ((0.5, 0.0, 0.0), True), ((0.1, 0.2, 0.5), True),
                                              ((1.0, 1.0, 0.0), False), ((0.8, 0.82, 0.86), False), ((0.95, 0.75, 0.4), False), ((0.9, 0.35, 0.1), True)])
def test_every_color_gets_the_contrasting_edge(rgb, expect_light):
    m = _box()
    light, dark = CP.edge_segments_by_contrast(m, _rgb(m, rgb))
    assert (len(light) > 0) is expect_light and (len(dark) > 0) is (not expect_light)


def test_each_edge_is_in_exactly_one_layer_and_keeps_its_ends():
    m = _box()
    rgb = _rgb(m, (0.9, 0.9, 0.9))
    rgb[: len(m.faces) // 2] = (0.01, 0.01, 0.01)                                    # mitad de las caras negras, mitad blancas
    light, dark = CP.edge_segments_by_contrast(m, rgb)
    assert len(light) + len(dark) == len(m.edges_unique)
    got = {tuple(map(tuple, np.round(s, 6))) for s in np.vstack([light, dark])}
    want = {tuple(map(tuple, np.round(s, 6))) for s in m.vertices[m.edges_unique]}
    assert got == want


def test_default_color_is_the_light_gray_of_the_piece():
    light, dark = CP.edge_segments_by_contrast(_box())
    assert len(dark) == len(_box().edges_unique) and len(light) == 0


def test_face_colors_follow_the_painted_parts():
    m = _box()
    z = m.triangles_center[:, 2]
    top, bottom = z > 0, z < 0
    parts = [("a", trimesh.Trimesh(m.vertices, m.faces[top], process=False), (0, 0, 0, 1)),
             ("b", trimesh.Trimesh(m.vertices, m.faces[bottom], process=False), (1, 1, 1, 1))]
    rgb = CP.face_rgb_from_parts(m, parts)
    assert np.allclose(rgb[top], 0) and np.allclose(rgb[bottom], 1)
    assert np.allclose(CP.face_rgb_from_parts(m, None)[0], (0.80, 0.82, 0.86))


def test_huge_meshes_skip_the_edges(monkeypatch):
    monkeypatch.setattr(CP, "EDGE_MAX", 5)
    light, dark = CP.edge_segments_by_contrast(_box())
    assert len(light) == 0 and len(dark) == 0


def test_wrong_face_rgb_shape_is_rejected():
    with pytest.raises(ValueError):
        CP.edge_segments_by_contrast(_box(), np.zeros((3, 3)))


# ------------------------------------------------------------------ dibujo (necesita OpenGL fuera de pantalla)
pytest.importorskip("moderngl")


@pytest.fixture(scope="module")
def renderer():
    from afm.gui.catalog_manager import PreviewRenderer

    r = PreviewRenderer(320, 320)
    if not r.available:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {r.error}")
    yield r
    r.close()


def _sphere():
    return trimesh.creation.icosphere(subdivisions=1, radius=8.0)


def _black_parts(mesh):
    return [("negro", mesh, (0.0, 0.0, 0.0, 1.0))]


def _count_light(img):
    return int(((img[..., :3] > 200).all(axis=2) & (img[..., 3] > 0)).sum())


def test_edges_stay_visible_on_a_black_piece(renderer):
    m = _sphere()
    parts = _black_parts(m)
    flat = renderer.render(m, None, color_parts=parts, guides=False)
    with_edges = renderer.render(m, None, color_parts=parts, guides=False, edges=True)
    assert _count_light(flat) < 5                                                    # sin aristas, el negro se pierde del todo
    assert _count_light(with_edges) > 150                                            # con aristas se ve la malla


def test_edges_stay_visible_on_a_light_piece(renderer):
    m = _sphere()
    parts = [("claro", m, (0.95, 0.95, 0.92, 1.0))]
    flat = renderer.render(m, None, color_parts=parts, guides=False)
    with_edges = renderer.render(m, None, color_parts=parts, guides=False, edges=True)
    dark = lambda im: int(((im[..., :3] < 40).all(axis=2) & (im[..., 3] > 0)).sum())     # noqa: E731
    assert dark(with_edges) > dark(flat) + 150


def test_turning_edges_off_removes_them(renderer):
    m = _sphere()
    parts = _black_parts(m)
    renderer.render(m, None, color_parts=parts, guides=False, edges=True)
    again = renderer.render(m, None, color_parts=parts, guides=False, edges=False)
    assert _count_light(again) < 5
