import numpy as np
import pytest

from afm.gui.controller import Controller, Mode, SYMMETRY_ID
from afm.gui.picking import PickResult
from afm.testfigure import make_doll


@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)                       # el STL "viene" en otras unidades
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    return c


def _hit(p):
    p = np.asarray(p, float)
    return PickResult(p, p, 0, np.array([0.0, 1.0, 0.0]), True)


def feed(c, pts):
    """Simula clics que impactan en los puntos dados (sin depender de la cámara)."""
    it = iter(pts)
    c.picker.pick_pixel = lambda *a, **k: _hit(next(it))
    for _ in pts:
        c.click(10, 10, 100, 100)


def test_open_frames_camera_and_normalizes(ctrl):
    assert ctrl.loaded and ctrl.mode == Mode.VIEW
    assert abs(ctrl.session.work.extents[2] - 100.0) < 1e-6
    assert np.allclose(ctrl.camera.target, ctrl.session.work.bounds.mean(axis=0))


def test_clicks_ignored_in_view_mode(ctrl):
    assert ctrl.click(10, 10, 100, 100) is None and ctrl.pending == []


def test_real_ray_click_hits_the_figure(ctrl):
    ctrl.start_symmetry()
    h, w = 600, 400
    c = ctrl.session.work.bounds.mean(axis=0)
    px, py, _ = ctrl.camera.project(c, w, h)[0]
    hit = ctrl.click(px, py, w, h)
    assert hit is not None and len(ctrl.pending) == 1
    ctrl.click(1, 1, w, h)                    # esquina del visor: no toca la figura
    assert len(ctrl.pending) == 1 and "no tocó" in ctrl.message


def test_crown_rescales_and_requires_no_planes(ctrl):
    ctrl.start_crown()
    assert ctrl.mode == Mode.CROWN
    feed(ctrl, [(0, 0, 90.0)])
    assert ctrl.mode == Mode.VIEW
    assert abs(ctrl.session.work.extents[2] - 100.0 * 100.0 / 90.0) < 1e-6
    ctrl.auto_symmetry()
    ctrl.start_crown()
    assert ctrl.mode == Mode.VIEW and "antes de definir" in ctrl.message


def test_symmetry_from_three_points(ctrl):
    ctrl.start_symmetry()
    feed(ctrl, [(0, -5, 10), (0, -5, 60), (0, 8, 40)])
    assert ctrl.mode == Mode.VIEW and ctrl.pending == []
    sym = ctrl.session.project.symmetry
    assert sym is not None and abs(abs(sym.normal[0]) - 1) < 1e-9
    assert [e.id for e in ctrl.plane_entries()] == [SYMMETRY_ID]


def test_non_vertical_symmetry_is_rejected(ctrl):
    ctrl.start_symmetry()
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 10, 10)])        # plano horizontal
    assert ctrl.session.project.symmetry is None and "No sirve" in ctrl.message and ctrl.pending == []


def test_limb_plane_requires_symmetry_then_adds_unique_names(ctrl):
    ctrl.start_limb_plane("arm")
    assert ctrl.mode == Mode.VIEW and "simetría" in ctrl.message
    ctrl.auto_symmetry()
    for _ in range(2):
        ctrl.start_limb_plane("arm")
        feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    ctrl.start_limb_plane("leg")
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    ids = [e.id for e in ctrl.plane_entries()]
    assert ids == [SYMMETRY_ID, "brazo", "brazo 2", "pierna"]
    assert ctrl.session.limb("pierna").kind == "leg"
    assert "silueta" in ctrl.problems()[0]


def test_collinear_points_rejected_and_undo_cancel(ctrl):
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("arm")
    feed(ctrl, [(0, 0, 10), (0, 0, 20), (0, 0, 30)])
    assert ctrl.session.project.limbs == [] and "No sirve" in ctrl.message
    ctrl.start_limb_plane("arm")
    feed(ctrl, [(0, 0, 10), (5, 0, 20)])
    ctrl.undo_point()
    assert len(ctrl.pending) == 1
    ctrl.cancel()
    assert ctrl.mode == Mode.VIEW and ctrl.pending == []


def test_remove_and_hide_planes(ctrl):
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("arm")
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    ctrl.toggle_plane("brazo")
    assert [e.visible for e in ctrl.plane_entries()] == [True, False]
    ctrl.remove_plane("brazo")
    assert [e.id for e in ctrl.plane_entries()] == [SYMMETRY_ID]
    ctrl.remove_plane(SYMMETRY_ID)
    assert ctrl.plane_entries() == [] and ctrl.session.project.symmetry is None


def test_set_front_resets_planes(ctrl):
    ctrl.auto_symmetry()
    ctrl.set_front("-Y")
    assert ctrl.plane_entries() == [] and ctrl.session.project.front == "-Y"


def test_project_roundtrip(ctrl, tmp_path):
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("leg")
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    p = str(tmp_path / "x.proyecto")
    ctrl.save_project(p)
    other = Controller()
    other.open_project(p)
    assert [e.id for e in other.plane_entries()] == [SYMMETRY_ID, "pierna"]
    assert other.picker is not None


def test_on_change_callback_fires(ctrl):
    calls = []
    ctrl.on_change = lambda: calls.append(1)
    ctrl.start_symmetry()
    ctrl.cancel()
    assert len(calls) >= 2


def test_sync_draws_planes_and_pending_points(ctrl):
    moderngl = pytest.importorskip("moderngl")
    from afm.gui.render import OffscreenRenderer
    try:
        off = OffscreenRenderer(320, 400)
    except Exception as e:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {e}")
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("arm")
    ctrl.pending = [np.array([0.0, 0.0, 10.0]), np.array([10.0, 0.0, 10.0])]
    ctrl.toggle_plane("simetria")
    ctrl.sync(off.scene)
    assert {"figura", "plano:simetria", "plano:simetria:borde", "pendientes", "pendientes:lineas"} <= set(off.scene.items)
    assert not off.scene.items["plano:simetria"].visible
    ctrl.one_side = True
    ctrl.sync(off.scene)
    assert off.scene.items["figura"].clip_x == 0.0
    ctrl.cancel()
    ctrl.remove_plane("simetria")
    ctrl.sync(off.scene)
    assert "plano:simetria" not in off.scene.items and "pendientes" not in off.scene.items
    img = off.render(ctrl.camera)
    assert img.shape == (400, 320, 4)


def test_overhang_faces_marks_ceiling_of_t_shape(tmp_path):
    """La máscara de voladizos coincide cara por cara con la malla `work` y cambia con el ángulo."""
    import trimesh
    post = trimesh.creation.box((4, 4, 60))
    post.apply_translation((0, 0, 30))
    top = trimesh.creation.box((40, 40, 10))
    top.apply_translation((0, 0, 65))
    t = trimesh.boolean.union([post, top], engine="manifold")
    path = str(tmp_path / "t.stl")
    t.export(path)
    c = Controller()
    c.open_stl(path)
    mask, res = c.overhang_faces()
    assert mask.shape == (len(c.session.work.faces),)
    assert res.overhang_area < 1.0 or mask.any()
    # con un ángulo muy alto (80°) casi nada es voladizo; con 10° lo es toda pared inclinada
    c.set_overhang_angle(80.0)
    m80, _ = c.overhang_faces()
    c.set_overhang_angle(10.0)
    m10, _ = c.overhang_faces()
    assert m80.sum() <= m10.sum()


def test_overhang_toggle_updates_message_and_cache(ctrl):
    ctrl.set_show_overhangs(True)
    assert ctrl.show_overhangs and "Voladizos en rojo" in ctrl.message
    first = ctrl._overhang_cache
    ctrl.set_show_overhangs(False)
    ctrl.set_show_overhangs(True)
    assert ctrl._overhang_cache is first                      # no se recalcula si nada cambió
    ctrl.set_overhang_angle(200.0)
    assert ctrl.overhang_angle == 80.0                        # se acota a 10°–80°
    ctrl.set_overhang_angle(-5.0)
    assert ctrl.overhang_angle == 10.0


def test_overhang_without_figure_is_harmless():
    c = Controller()
    c.set_show_overhangs(True)
    assert c.overhang_faces() is None


def test_sync_splits_mesh_when_overhangs_shown(ctrl):
    pytest.importorskip("moderngl")
    from afm.gui.render import OffscreenRenderer
    try:
        off = OffscreenRenderer(320, 400)
    except Exception as e:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {e}")
    ctrl.set_overhang_angle(10.0)                             # muñeco con curvas: seguro hay caras que cuelgan
    ctrl.set_show_overhangs(True)
    ctrl.sync(off.scene)
    mask, _ = ctrl.overhang_faces()
    assert mask.any() and "voladizos" in off.scene.items and "figura" in off.scene.items
    n_total = len(ctrl.session.work.faces)
    assert off.scene.items["figura"].count + off.scene.items["voladizos"].count == n_total * 3
    ctrl.set_show_overhangs(False)
    ctrl.sync(off.scene)
    assert "voladizos" not in off.scene.items
    assert off.render(ctrl.camera).shape == (400, 320, 4)


def test_overhang_preview_logic(ctrl):
    import numpy as np
    assert ctrl.overhang_faces() is not None
    mask, res = ctrl.overhang_faces()
    assert mask.shape == (len(ctrl.session.work.faces),) and res.overhang_area >= 0
    ctrl.set_show_overhangs(True)
    assert "Voladizos en rojo" in ctrl.message
    first = ctrl._overhang_cache
    ctrl.set_overhang_angle(1000)                       # se acota a 80°
    assert ctrl.overhang_angle == 80.0 and ctrl._overhang_cache is not first
    ctrl.set_overhang_angle(45)
    ctrl.set_show_overhangs(False)
    assert not ctrl.show_overhangs


# ------------------------------------------------------------------ siluetas (polígonos)
W, H = 600, 800


@pytest.fixture()
def arm(ctrl):
    """Control con simetría y un plano de brazo y=0 (u = x, v = z - 10), cámara de frente."""
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("arm")
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    return ctrl


def _px(c, uv):
    plane = c.session.limb("brazo").plane
    return tuple(c.camera.project(plane.to_world(uv), W, H)[0, :2])


def _click_uv(c, uv):
    c.click(*_px(c, uv), W, H)


SQUARE = [(5, 5), (25, 5), (25, 40), (5, 40)]


def _draw_square(c):
    c.start_polygon("brazo")
    for uv in SQUARE:
        _click_uv(c, uv)
    _click_uv(c, SQUARE[0])                      # clic sobre el primero: cierra


def test_draw_polygon_with_real_clicks_closes_and_saves(arm):
    assert "silueta" in arm.problems()[0]
    _draw_square(arm)
    d = arm.draft
    assert arm.mode == Mode.POLYGON and d.closed and len(d.pts) == 4
    poly = arm.session.limb("brazo").polygon
    assert poly.shape == (4, 3)
    plane = arm.session.limb("brazo").plane
    assert np.allclose(plane.signed_distance(poly), 0.0, atol=1e-6)
    assert np.allclose(plane.to_uv(poly), np.array(SQUARE, float), atol=1e-6)
    assert not any("silueta" in p for p in arm.problems())


def test_polygon_close_button_and_too_few_points(arm):
    arm.start_polygon("brazo")
    _click_uv(arm, (5, 5)); _click_uv(arm, (25, 5))
    arm.close_polygon()
    assert not arm.draft.closed and "3 vértices" in arm.message
    _click_uv(arm, (25, 40))
    arm.close_polygon()
    assert arm.draft.closed and len(arm.session.limb("brazo").polygon) == 3


def test_polygon_backspace_while_drawing(arm):
    arm.start_polygon("brazo")
    _click_uv(arm, (5, 5)); _click_uv(arm, (25, 5))
    arm.undo_point()
    assert len(arm.draft.pts) == 1


def test_polygon_move_insert_delete_vertex(arm):
    _draw_square(arm)
    _click_uv(arm, (25, 5))                       # elige el vértice 1
    assert arm.draft.selected == 1
    _click_uv(arm, (30, 2))                       # lo mueve
    assert arm.draft.selected is None and np.allclose(arm.draft.pts[1], (30, 2), atol=1e-6)
    assert np.allclose(arm.session.limb("brazo").plane.to_uv(arm.session.limb("brazo").polygon)[1], (30, 2), atol=1e-6)
    _click_uv(arm, (5, 22.5))                     # sobre el borde 3 -> 0 (x = 5)
    assert len(arm.draft.pts) == 5 and len(arm.session.limb("brazo").polygon) == 5
    _click_uv(arm, (5, 22.5))                     # elige el vértice nuevo (índice 4)
    arm.undo_point()                              # Retroceso lo borra
    assert len(arm.draft.pts) == 4 and len(arm.session.limb("brazo").polygon) == 4


def test_polygon_cannot_drop_below_three_vertices(arm):
    arm.start_polygon("brazo")
    for uv in [(5, 5), (25, 5), (15, 40)]:
        _click_uv(arm, uv)
    _click_uv(arm, (5, 5))
    assert arm.draft.closed
    _click_uv(arm, (15, 40))
    arm.undo_point()
    assert len(arm.draft.pts) == 3 and "3 vértices" in arm.message


def test_self_intersecting_polygon_is_not_saved(arm):
    arm.start_polygon("brazo")
    for uv in [(5, 5), (25, 40), (25, 5), (5, 40)]:       # moño
        _click_uv(arm, uv)
    arm.close_polygon()
    assert arm.draft.closed and "cruza" in arm.message
    assert len(arm.session.limb("brazo").polygon) == 0


def test_edit_existing_polygon_and_clear(arm):
    _draw_square(arm)
    arm.cancel()
    assert arm.mode == Mode.VIEW and arm.draft is None
    arm.start_polygon("brazo")
    assert arm.draft.closed and len(arm.draft.pts) == 4        # se carga la silueta guardada
    arm.clear_polygon()
    assert not arm.draft.closed and arm.draft.pts == []


def test_edge_on_plane_gives_hint_instead_of_vertex(arm):
    arm.start_polygon("brazo")
    arm.camera.set_view("izquierda")                          # mirando a lo largo de x: el plano y=0 va de canto
    arm.click(W / 2, H / 2, W, H)
    assert arm.draft.pts == [] and "canto" in arm.message


def test_use_outline_as_base_and_remove_plane_cancels(arm):
    arm.start_polygon("brazo")
    arm.use_outline_as_base()
    assert arm.draft.closed and len(arm.draft.pts) >= 3 and arm.draft.validity_error() is None
    assert len(arm.session.limb("brazo").polygon) == len(arm.draft.pts)
    arm.remove_plane("brazo")
    assert arm.mode == Mode.VIEW and arm.draft is None


def test_sync_draws_polygon_guide_and_saved_outline(arm):
    pytest.importorskip("moderngl")
    from afm.gui.render import OffscreenRenderer
    try:
        off = OffscreenRenderer(320, 400)
    except Exception as e:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {e}")
    arm.start_polygon("brazo")
    _click_uv(arm, (5, 5)); _click_uv(arm, (25, 5))
    arm.sync(off.scene)
    assert {"guia", "poligono", "poligono:vertices"} <= set(off.scene.items)
    _click_uv(arm, (25, 40)); arm.close_polygon()
    _click_uv(arm, (25, 5))                                   # vértice elegido
    arm.sync(off.scene)
    assert "poligono:sel" in off.scene.items
    arm.cancel()
    arm.sync(off.scene)
    assert "silueta:brazo" in off.scene.items and "poligono" not in off.scene.items and "guia" not in off.scene.items
    assert off.render(arm.camera).shape == (400, 320, 4)


class _FakeScene:
    """Registra lo que `Controller.sync` pide dibujar, sin OpenGL."""

    def __init__(self):
        self.items = {}

    def set_mesh(self, name, mesh, color=None, clip_x=-1e9):
        self.items[name] = ("mesh", mesh)

    def set_plane(self, name, plane, hs, center=None, color=None):
        self.items[name] = ("quad", plane)
        self.items[name + ":borde"] = ("lines", None)

    def set_lines(self, name, pl, color=None, closed=False):
        self.items[name] = ("lines", pl)

    def set_points(self, name, pts, color=None, size=10.0):
        self.items[name] = ("points", np.asarray(pts))

    def show(self, name, visible):
        pass

    def remove(self, name):
        self.items.pop(name, None)
        self.items.pop(name + ":borde", None)

    def clear(self):
        self.items.clear()


def test_sync_polygon_items_without_opengl(arm):
    sc = _FakeScene()
    arm.start_polygon("brazo")
    _click_uv(arm, (5, 5)); _click_uv(arm, (25, 5))
    arm.sync(sc)
    assert {"guia", "poligono", "poligono:vertices"} <= set(sc.items) and "poligono:sel" not in sc.items
    plane = arm.session.limb("brazo").plane
    assert np.allclose(plane.signed_distance(sc.items["poligono:vertices"][1]), 0.0, atol=1e-6)
    _click_uv(arm, (25, 40)); arm.close_polygon()
    _click_uv(arm, (25, 5))
    arm.sync(sc)
    assert sc.items["poligono:sel"][1].shape == (1, 3)
    arm.cancel()
    arm.sync(sc)
    assert "silueta:brazo" in sc.items
    assert not {"guia", "poligono", "poligono:vertices", "poligono:sel"} & set(sc.items)
    arm.remove_plane("brazo")
    arm.sync(sc)
    assert "silueta:brazo" not in sc.items
