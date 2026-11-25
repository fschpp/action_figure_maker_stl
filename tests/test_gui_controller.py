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


def test_front_change_loss_lists_work_to_discard(ctrl):
    assert ctrl.front_change_loss() == []
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("leg")
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    loss = ctrl.front_change_loss()
    assert "el plano de simetría" in loss and any("miembro" in t for t in loss)
    ctrl.set_front("-Y")
    assert ctrl.front_change_loss() == []


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


def _drag_uv(c, src, dst, steps=3):
    """Simula arrastrar con el mouse del vértice en `src` (u, v) hasta `dst`, en `steps` movimientos."""
    x0, y0 = _px(c, src)
    x1, y1 = _px(c, dst)
    assert c.begin_vertex_drag(x0, y0, W, H)
    for k in range(1, steps + 1):
        c.drag_vertex(x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps, W, H)
    return c.end_vertex_drag()


def test_drag_vertex_moves_it_live_and_saves_once_on_release(arm):
    _draw_square(arm)
    plane = arm.session.limb("brazo").plane
    arm.history.observe(arm._project_text(), "base")
    n_undo = len(arm.history._undo) if hasattr(arm.history, "_undo") else None
    x0, y0 = _px(arm, SQUARE[2])
    x1, y1 = _px(arm, (30, 44))
    assert arm.begin_vertex_drag(x0, y0, W, H)
    arm.drag_vertex(x1, y1, W, H)
    assert np.allclose(arm.draft.pts[2], (30, 44), atol=0.2)                       # se ve en el borrador mientras se arrastra
    assert np.allclose(plane.to_uv(arm.session.limb("brazo").polygon)[2], SQUARE[2], atol=1e-6)   # todavía no se guardó
    assert arm.end_vertex_drag()
    assert np.allclose(plane.to_uv(arm.session.limb("brazo").polygon)[2], (30, 44), atol=0.2)      # al soltar se guarda
    assert arm.draft.selected is None and "movido" in arm.message
    if n_undo is not None:
        assert len(arm.history._undo) == n_undo + 1                                # un único paso de deshacer


def test_drag_vertex_far_from_any_vertex_does_nothing(arm):
    _draw_square(arm)
    assert not arm.begin_vertex_drag(*_px(arm, (15, 22)), W, H)
    arm.drag_vertex(10, 10, W, H)                                                  # sin arrastre en curso: ignorado
    assert not arm.end_vertex_drag()
    assert np.allclose(arm.draft.pts, np.array(SQUARE, float), atol=1e-6)


def test_drag_vertex_only_in_polygon_mode(arm):
    assert not arm.begin_vertex_drag(10, 10, W, H)


def test_press_on_vertex_without_moving_is_still_a_click_that_picks_it(arm):
    _draw_square(arm)
    x, y = _px(arm, SQUARE[1])
    assert arm.begin_vertex_drag(x, y, W, H)
    assert not arm.end_vertex_drag()                                               # no se movió: la ventana lo trata como clic
    arm.click(x, y, W, H)
    assert arm.draft.selected == 1


def test_cancel_drag_restores_the_vertex(arm):
    _draw_square(arm)
    x0, y0 = _px(arm, SQUARE[0])
    x1, y1 = _px(arm, (0, 0))
    assert arm.begin_vertex_drag(x0, y0, W, H)
    arm.drag_vertex(x1, y1, W, H)
    assert arm.cancel_vertex_drag()
    assert np.allclose(arm.draft.pts[0], SQUARE[0], atol=1e-6)
    assert np.allclose(arm.session.limb("brazo").plane.to_uv(arm.session.limb("brazo").polygon)[0], SQUARE[0], atol=1e-6)
    assert not arm.cancel_vertex_drag()                                            # ya no hay arrastre


def test_drag_that_crosses_the_polygon_is_reported_and_not_saved(arm):
    _draw_square(arm)
    plane = arm.session.limb("brazo").plane
    saved = plane.to_uv(arm.session.limb("brazo").polygon).copy()
    assert _drag_uv(arm, SQUARE[0], (30, 20))                                      # el vértice pasa al otro lado del borde opuesto: se cruza
    assert "cruza" in arm.message
    assert np.allclose(plane.to_uv(arm.session.limb("brazo").polygon), saved, atol=1e-6)


def test_leaving_polygon_mode_drops_a_drag_in_progress(arm):
    _draw_square(arm)
    assert arm.begin_vertex_drag(*_px(arm, SQUARE[0]), W, H)
    arm.cancel()
    assert not arm.end_vertex_drag()


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

    def set_lines(self, name, pl, color=None, closed=False, on_top=False, width=1.0):
        self.items[name] = ("lines", pl)

    def set_points(self, name, pts, color=None, size=10.0, on_top=False):
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


# ----------------------------------------------------------------- generar / exportar
def _fill_demo_limbs(c):
    from afm.project import demo_project
    c.auto_symmetry()
    for l in demo_project().limbs:
        c.session.add_limb(l.name, l.kind, l.plane, l.thickness, auto=False)
        c.session.set_limb_polygon(l.name, l.polygon)


def test_generate_refuses_while_problems_pending(ctrl):
    assert ctrl.begin_generate() is None and "Todavía no se puede generar" in ctrl.message
    assert not ctrl.busy
    assert ctrl.export("/tmp/no_importa") is None and "Primero generá" in ctrl.message


def test_generate_export_and_invalidation(ctrl, tmp_path):
    _fill_demo_limbs(ctrl)
    steps = []
    out = ctrl.generate(progress=steps.append)
    assert out is not None and out.validation["ok"], out and out.validation["problems"]
    assert not ctrl.busy and steps and "Figura generada y válida" in ctrl.message
    assert "holgura mínima" in ctrl.message
    info = ctrl.export(str(tmp_path / "salida"), orient=True)
    assert info and any((tmp_path / "salida").glob("*_ensamblada.stl"))
    assert any((tmp_path / "salida" / "piezas").glob("*_torso.stl"))
    assert "Exportado en" in ctrl.message and "Orientación" in ctrl.message
    # editar una silueta invalida el resultado: hay que volver a generar antes de exportar
    spec = ctrl.session.limb("brazo")
    ctrl.session.set_limb_polygon("brazo", spec.polygon)
    assert ctrl.session.outcome is None
    assert ctrl.export(str(tmp_path / "otra")) is None and "Primero generá" in ctrl.message


def test_busy_blocks_clicks_and_progress_is_reported(arm):
    arm.start_polygon("brazo")
    arm.busy = True
    _click_uv(arm, (5, 5))
    assert arm.draft.pts == []
    arm.report_progress("cavidades")
    assert "Generando… cavidades" in arm.message
    arm.finish_generate(error=RuntimeError("boom"))
    assert not arm.busy and "Falló la generación: boom" in arm.message


def test_validation_summary_formats_problems_and_ok():
    from afm.gui.controller import validation_summary
    bad = {"problems": ["brazo: interpenetra el torso"], "gaps": {}, "recorrido": {}, "avisos": [], "pieces": {}}
    assert "tiene problemas" in validation_summary(bad, 0.5) and "interpenetra" in validation_summary(bad, 0.5)
    ok = {"problems": [], "gaps": {"brazo": 0.476, "pierna": 0.48}, "recorrido": {"pierna": 0.0},
          "avisos": ["x"], "pieces": {"torso": {}, "brazo": {}, "pierna": {}}}
    txt = validation_summary(ok, 0.5)
    assert "3 piezas" in txt and "0.476" in txt and "recorrido" in txt and "1 aviso" in txt


def _sym_plane(c, kind):
    """Agrega un plano de miembro sobre el plano de simetría (x = 0), como lo haría el usuario con 3 clics."""
    c.start_limb_plane(kind)
    feed(c, [(0, 0, 10), (0, 10, 10), (0, 0, 50)])


def test_new_plane_takes_over_cut_offset_from_previous_limb(ctrl):
    """Regresión: con el brazo ya ajustado y dibujado, al agregar el plano de la pierna el campo «Corte a» tiene que
    mover el de la pierna, no seguir editando el desplazamiento del brazo."""
    ctrl.auto_symmetry()
    _sym_plane(ctrl, "arm")
    ctrl.start_polygon("brazo")                      # el usuario trabajó el brazo (la ventana consulta offset_limb)
    assert ctrl.offset_limb() == "brazo"
    ctrl.set_limb_offset("brazo", 25.0)
    ctrl.cancel(quiet=True)
    _sym_plane(ctrl, "leg")
    assert ctrl.offset_limb() == "pierna"
    ctrl.set_limb_offset(ctrl.offset_limb(), 40.0)
    assert ctrl.session.limb_offset("pierna") == pytest.approx(40.0)
    assert ctrl.session.limb_offset("brazo") == pytest.approx(25.0)


def test_cut_offset_target_is_forgotten_when_opening_another_figure(ctrl, tmp_path):
    """Regresión: el «último miembro editado» de una figura no debe pasar a la siguiente que se abre."""
    ctrl.auto_symmetry()
    _sym_plane(ctrl, "arm")
    ctrl.start_polygon("brazo")
    assert ctrl.offset_limb() == "brazo"
    ctrl.cancel(quiet=True)
    path = str(tmp_path / "otro.stl")
    m = make_doll()
    m.apply_scale(0.05)
    m.export(path)
    ctrl.open_stl(path)
    ctrl.auto_symmetry()
    _sym_plane(ctrl, "arm")
    _sym_plane(ctrl, "leg")
    assert ctrl.offset_limb() == "pierna"            # el último plano agregado, no el nombre heredado «brazo»


class _RecordingScene:
    """Escena falsa: anota qué se dibuja y si va por delante de la figura (`on_top`)."""

    def __init__(self):
        self.items, self.on_top = {}, {}

    def set_lines(self, name, pl, color=None, closed=False, on_top=False, width=1.0):
        self.items[name], self.on_top[name] = pl, on_top

    def set_points(self, name, pts, color=None, size=10.0, on_top=False):
        self.items[name], self.on_top[name] = pts, on_top

    def remove(self, name):
        self.items.pop(name, None)
        self.on_top.pop(name, None)

    def show(self, name, visible):
        pass


def test_silhouettes_are_drawn_on_top_so_the_figure_does_not_hide_them(arm):
    """Regresión: el plano de corte queda dentro de la figura; las siluetas (guardadas y en edición) se dibujaban con
    prueba de profundidad y la malla las tapaba."""
    _draw_square(arm)
    arm.cancel()                                               # sale del modo silueta: queda la guardada
    sc = _RecordingScene()
    arm._sync_polygons(sc)
    assert sc.on_top["silueta:brazo"]                          # la guardada
    arm.start_polygon("brazo")                                 # ahora en edición: el borrador y sus vértices
    arm.draft.selected = 0
    arm._sync_polygons(sc)
    for key in ("poligono", "poligono:vertices", "poligono:sel"):
        assert sc.on_top[key], key


def test_silhouettes_are_drawn_thick(arm):
    """Regresión: a 1 px la silueta casi no se distingue sobre la figura; el controller pide un trazo más grueso."""
    from afm.gui.controller import SILHOUETTE_PX
    assert SILHOUETTE_PX >= 2.0
    widths = {}

    class _Scene(_RecordingScene):
        def set_lines(self, name, pl, color=None, closed=False, on_top=False, width=1.0):
            super().set_lines(name, pl, color, closed, on_top, width)
            widths[name] = width

    _draw_square(arm)
    arm.cancel()
    sc = _Scene()
    arm._sync_polygons(sc)
    arm.start_polygon("brazo")
    arm._sync_polygons(sc)
    from afm.gui.controller import DRAFT_PX
    assert widths["silueta:brazo"] == SILHOUETTE_PX
    assert widths["poligono"] == DRAFT_PX > SILHOUETTE_PX      # el borrador (p. ej. la silueta propuesta) es todavía más grueso


def test_draft_silhouette_uses_a_loud_color_distinct_from_saved_ones(arm):
    """Regresión: el borrador (la silueta propuesta) iba en amarillo sobre una figura gris clara y no se veía; ahora es magenta flúor,
    distinto de los colores de las siluetas guardadas, y el vértice elegido no se confunde con él."""
    from afm.gui.controller import DRAFT_COLOR, DRAFT_SELECTED_COLOR
    colors = {}

    class _Scene(_RecordingScene):
        def set_lines(self, name, pl, color=None, closed=False, on_top=False, width=1.0):
            super().set_lines(name, pl, color, closed, on_top, width)
            colors[name] = color

        def set_points(self, name, pts, color=None, size=10.0, on_top=False):
            super().set_points(name, pts, color, size, on_top)
            colors[name] = color

    _draw_square(arm)
    arm.cancel()
    sc = _Scene()
    arm._sync_polygons(sc)
    arm.start_polygon("brazo")
    arm.draft.selected = 0
    arm._sync_polygons(sc)
    assert colors["poligono"] == colors["poligono:vertices"] == DRAFT_COLOR
    assert colors["poligono:sel"] == DRAFT_SELECTED_COLOR
    assert DRAFT_COLOR != colors["silueta:brazo"] and DRAFT_SELECTED_COLOR != DRAFT_COLOR
    assert max(DRAFT_COLOR[:3]) - min(DRAFT_COLOR[:3]) >= 0.9      # saturación máxima: no es un gris ni un pastel


def test_thick_lines_cover_more_pixels_than_thin_ones():
    pytest.importorskip("moderngl")
    pytest.importorskip("trimesh")
    from afm.gui.camera import OrbitCamera
    from afm.gui.render import OffscreenRenderer
    try:
        off = OffscreenRenderer(240, 180)
    except Exception as e:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {e}")
    cam = OrbitCamera()
    cam.frame(np.array([[-30.0, -30, -30], [30, 30, 30]]))
    cam.look_along((1, 0, 0), target=(0, 0, 0))
    sq = np.array([[0, -20, -20], [0, 20, -20], [0, 20, 20], [0, -20, 20]], float)

    def lit(width):
        off.scene.clear()
        off.scene.set_lines("s", sq, (1.0, 0.0, 0.0, 1.0), closed=True, on_top=True, width=width)
        img = off.render(cam)
        return int(((img[..., 0] > 200) & (img[..., 1] < 60)).sum())

    thin, thick = lit(1.0), lit(4.0)
    assert thin > 0 and thick > 2.5 * thin


def test_guide_outline_is_thick_loud_and_on_top(arm):
    """Regresión: la guía del contorno (lo que se ve al empezar una silueta o al proponerla) iba en gris pálido de 1 px, detrás de la malla;
    ahora es un trazo grueso, naranja flúor y por delante, distinto del magenta del borrador."""
    from afm.gui.controller import DRAFT_COLOR, GUIDE_COLOR, GUIDE_PX, SILHOUETTE_PX
    seen = {}

    class _Scene(_RecordingScene):
        def set_lines(self, name, pl, color=None, closed=False, on_top=False, width=1.0):
            super().set_lines(name, pl, color, closed, on_top, width)
            seen[name] = (color, on_top, width)

    arm.start_polygon("brazo")
    arm._sync_polygons(_Scene())
    color, on_top, width = seen["guia"]
    assert color == GUIDE_COLOR != DRAFT_COLOR and on_top and width == GUIDE_PX >= SILHOUETTE_PX


def test_grip_axis_choice_limits_and_stale(ctrl):
    from types import SimpleNamespace

    assert ctrl.grip_channel == 1 and ctrl.grip_tilt == 0.0 and not ctrl.grip_axis_stale
    assert ctrl.set_grip_axis("grip_channel", 3) and ctrl.grip_channel == 3
    assert not ctrl.set_grip_axis("grip_channel", 5)                 # solo hay hasta 4 candidatos
    assert not ctrl.set_grip_axis("grip_tilt", 20.0)                 # la inclinación fina llega a ±15°
    assert ctrl.set_grip_axis("grip_tilt", -8.0) and ctrl.grip_tilt == -8.0
    assert not ctrl.set_grip_axis("nada", 1)
    # una mano taladrada con otra elección queda marcada como vieja; con la misma, no
    ctrl.grip = SimpleNamespace(meta={"agarre": {"eleccion": {"canal": 3, "inclinar_grados": -8.0, "azimut_grados": 0.0}}})
    assert not ctrl.grip_axis_stale
    ctrl.set_grip_axis("grip_channel", 2)
    assert ctrl.grip_axis_stale
    ctrl.grip = SimpleNamespace(meta={"agarre": {}})                  # sin «eleccion»: canal 1 sin inclinar
    ctrl.set_grip_axis("grip_channel", 1)
    ctrl.set_grip_axis("grip_tilt", 0.0)
    assert not ctrl.grip_axis_stale


def test_grip_preview_runs_without_drilling(ctrl, tmp_path, monkeypatch):
    """La previsualización calcula el taladro en memoria con la elección vigente; no escribe nada y se invalida al cambiarla."""
    from afm import hand as H
    from afm import props as P
    from tests.test_props import _ch, _ring

    hand, _ = _ring()
    path = str(tmp_path / "mano.stl")
    H.save_standard_hand(path, hand, {"formato": H.HAND_FORMAT})
    before = sorted(p.name for p in tmp_path.iterdir())
    loaded_hand, _meta = H.load_standard_hand(path)
    monkeypatch.setattr(P, "find_channel_options", lambda h, bore, **k: [_ch(loaded_hand, bore)])
    ctrl.session.project.hand = H.HandSpec(path=path)

    assert not ctrl.grip_preview_stale                      # apagada: nada que recalcular
    assert ctrl.set_grip_preview(True) and ctrl.grip_preview_stale
    job = ctrl.begin_grip_preview()
    res = job()
    ctrl.finish_grip_preview(res)
    assert not res["error"], res["error"]
    assert res["fit"].wall_min >= ctrl.grip_spec.min_wall if hasattr(ctrl.grip_spec, "min_wall") else res["fit"].wall_min > 0
    assert not ctrl.grip_preview_stale and not ctrl.busy
    meshes = ctrl.grip_preview_meshes({"matrix": np.eye(4)})          # mano taladrada + espada de prueba, en el mundo
    assert meshes is not None and meshes[0].is_watertight
    assert (meshes[1] is None) == bool(res["prop_error"])             # sin espada solo si no cabe, y entonces se avisa
    assert "prop_error" in res
    assert sorted(p.name for p in tmp_path.iterdir()) == before          # nada guardado
    ctrl.set_grip_axis("grip_channel", 2)                    # elegir otro canal invalida lo calculado
    assert ctrl.grip_preview_stale
    res = ctrl.begin_grip_preview()()
    assert "solo tiene 1 canales" in res["error"]
    ctrl.finish_grip_preview(res)
    assert not ctrl.grip_preview_stale                       # con error y la misma clave no se reintenta en bucle
    assert ctrl.set_grip_preview(False)
