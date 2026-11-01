"""Gestor del catálogo: modo «Cortar la mano…» (lógica sin GTK: la ventana solo reenvía clics y dibuja)."""
import numpy as np
import pytest

from afm.catalog import Catalog
from afm.extract import extract_all
from afm.gui import catalog_manager as CM
from afm.gui.controller import Controller
from afm.project import demo_project
from afm.testfigure import make_doll

IMG = (840, 630)            # tamaño de la imagen de la vista previa
WIDGET = (1000.0, 500.0)    # el Gtk.Picture es más ancho que la imagen: queda centrada con márgenes a los lados
# El brazo del muñeco es un cilindro elíptico (x 15…22, y −4…4, z 45…75) con la mano en z 45…50: los puntos de la muñeca son
# los de su borde a z = 50 que la vista 3/4 ve de frente (los de atrás no se pueden tocar)


@pytest.fixture(scope="module")
def doll_stl(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("doll") / "muneco.stl")
    make_doll().export(path)
    return path


@pytest.fixture()
def ctrl(tmp_path, doll_stl):
    proj = tmp_path / "muneco.proyecto"
    demo_project().save(str(proj))
    c = Controller()
    c.open_project(str(proj), doll_stl)
    assert c.loaded
    Catalog.create(str(tmp_path / "linea"), "prueba", 100.0)
    assert c.open_catalog(str(tmp_path / "linea"))
    return c


@pytest.fixture()
def cutter(ctrl):
    """Brazo con la mano pegada (extraído sin puntos de muñeca) listo para cortar."""
    cat = ctrl.session._catalog()
    e = extract_all(S_doll(), demo_project(), "muneco", places=["brazo"]).pieces[0]
    cat.add_piece(e.mesh, e.meta)
    mgr = CM.CatalogManager(ctrl)
    meta = next(m for m in mgr.pieces() if m.tipo == "brazo")
    return CM.WristCutter(ctrl, meta, mgr.mesh(meta.ref))


def S_doll():
    from afm import solids as S
    return S.to_manifold(make_doll())


def widget_xy(cutter, world, yaw_off=0.0, pitch_off=0.0, zoom=1.0, pan=(0.0, 0.0)):
    """Dónde hay que hacer clic en el widget para tocar el punto `world` (inversa de lo que hace `pick_at`)."""
    cam = CM.preview_camera(CM.view_bounds(cutter.mesh, cutter.meta), IMG[0] / IMG[1], yaw_off, pitch_off, zoom, pan)
    px, py, _ = cam.project(world, *IMG)[0]
    k = min(WIDGET[0] / IMG[0], WIDGET[1] / IMG[1])
    return (WIDGET[0] - IMG[0] * k) / 2 + px * k, (WIDGET[1] - IMG[1] * k) / 2 + py * k


def ring_points(cutter, z=50.0, **view):
    """Tres puntos del contorno del brazo a altura `z` que se ven desde la vista previa (primero, del medio y último, de lado a lado)."""
    from afm.gui.picking import ray_first_hit

    cam = CM.preview_camera(CM.view_bounds(cutter.mesh, cutter.meta), IMG[0] / IMG[1], view.get("yaw_off", 0.0), view.get("pitch_off", 0.0))
    ring = np.vstack(cutter.mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1]).discrete)
    tris = np.asarray(cutter.mesh.triangles, float)
    seen = []
    for p in ring:
        px, py, _ = cam.project(p, *IMG)[0]
        o, d = cam.ray(px, py, *IMG)
        _f, t = ray_first_hit(tris, o, d)
        if np.linalg.norm(o + t * d - p) < 1e-3:
            seen.append(p)
    c = np.mean(ring, axis=0)
    seen.sort(key=lambda p: np.arctan2(p[1] - c[1], p[0] - c[0]))
    assert len(seen) >= 6
    return [tuple(seen[0]), tuple(seen[len(seen) // 2]), tuple(seen[-1])]


def click_all(cutter, targets=None, **view):
    for t in (targets or ring_points(cutter, **view)):
        x, y = widget_xy(cutter, t, **view)
        cutter.pick_at(x, y, WIDGET, IMG, **view)


def test_widget_to_image_centers_the_picture_and_rejects_the_margins():
    k = 500 / 630
    left = (1000 - 840 * k) / 2
    assert CM.widget_to_image(left + 0.5 * k * 840, 250, 1000, 500, 840, 630) == pytest.approx((420, 315))
    assert CM.widget_to_image(left - 5, 250, 1000, 500, 840, 630) is None                      # margen izquierdo
    assert CM.widget_to_image(500, 250 + 400, 1000, 500, 840, 630) is None                    # debajo de la imagen
    assert CM.widget_to_image(left, 0, 1000, 500, 840, 630) == pytest.approx((0, 0))
    assert CM.widget_to_image(10, 10, 0, 0, 840, 630) is None                                 # widget todavía sin tamaño


def test_only_an_arm_with_its_hand_attached_can_be_cut(ctrl, cutter):
    ok, why = CM.can_cut_wrist(cutter.meta)
    assert ok and why == ""
    assert CM.can_cut_wrist(None)[0] is False
    ctrl.catalog_add_current("otro", places=["pierna"])
    mgr = CM.CatalogManager(ctrl)
    pierna = next(m for m in mgr.pieces() if m.tipo == "pierna")
    assert "solo se le corta la mano a un brazo" in CM.can_cut_wrist(pierna)[1]
    cat = ctrl.session._catalog()
    cat.cut_wrist(cutter.meta.ref, [(22.0, 0.0, 50.0), (14.0, 0.0, 50.0), (18.0, 4.0, 50.0)])
    sin_mano = cat.meta("brazo-muneco-01@2")
    assert "ya está sin mano" in CM.can_cut_wrist(sin_mano)[1]


def test_three_clicks_pick_the_points_under_the_cursor_and_give_a_cut(cutter):
    assert "PULGAR" in cutter.message()
    for i, t in enumerate(ring_points(cutter)):
        x, y = widget_xy(cutter, t)
        cutter.pick_at(x, y, WIDGET, IMG)
        assert len(cutter.points) == i + 1
        assert np.allclose(cutter.points[-1], t, atol=1e-3)                   # el clic cae exactamente donde se apuntó
    assert cutter.complete and cutter.plan is not None and cutter.error == ""
    assert cutter.plan.arm_mesh.bounds[0][2] == pytest.approx(50.0, abs=1e-3)           # el brazo queda sin la mano (z 45…50)
    assert cutter.plan.hand_world.bounds[0][2] == pytest.approx(45.0, abs=1e-3)
    assert "Mano cortada (verde)" in cutter.message() and "Invertir lado" in cutter.message()
    parts = cutter.preview_parts()
    assert [n for n, _m, _c in parts] == ["brazo", "mano"] and parts[1][2] == CM.HAND_COLOR
    assert cutter.marks().shape == (3, 3)


def test_the_clicks_follow_the_rotated_view(cutter):
    """Girar la vista previa cambia la cámara: el clic tiene que usar la misma que el dibujo."""
    view = {"yaw_off": -20.0, "pitch_off": 15.0}
    first = ring_points(cutter, **view)[0]
    cutter.pick_at(*widget_xy(cutter, first, **view), WIDGET, IMG, **view)
    assert np.allclose(cutter.points[0], first, atol=1e-3)


def test_clicks_outside_the_piece_or_the_image_are_ignored(cutter):
    msg = cutter.pick_at(2.0, 2.0, WIDGET, IMG)                               # margen de la izquierda
    assert "fuera de la imagen" in msg and cutter.points == []
    k = min(WIDGET[0] / IMG[0], WIDGET[1] / IMG[1])
    msg = cutter.pick_at((WIDGET[0] - IMG[0] * k) / 2 + 3, 3, WIDGET, IMG)    # esquina de la imagen: fondo, no pieza
    assert "no hay pieza" in msg and cutter.points == []


def test_undo_reset_and_a_fourth_click(cutter):
    click_all(cutter)
    assert "Ya están los 3 puntos" in cutter.pick_at(*widget_xy(cutter, ring_points(cutter)[0]), WIDGET, IMG) and len(cutter.points) == 3
    cutter.undo()
    assert len(cutter.points) == 2 and cutter.plan is None and "Punto 3 de 3" in cutter.message()
    cutter.reset()
    assert cutter.points == [] and "Punto 1 de 3" in cutter.message()


def test_invert_takes_the_other_side_as_the_hand(cutter):
    click_all(cutter)
    small = cutter.plan.hand_mesh.volume
    cutter.toggle_invert()
    assert cutter.plan is not None and cutter.invert
    assert cutter.plan.hand_mesh.volume > 3 * small                          # ahora «la mano» es el lado grande
    cutter.toggle_invert()
    assert cutter.plan.hand_mesh.volume == pytest.approx(small, rel=1e-6)


def test_collinear_points_are_reported_and_can_be_undone(cutter):
    cutter.points = [np.array(p, float) for p in [(22.0, 0.0, 50.0), (22.0, 0.0, 55.0), (22.0, 0.0, 60.0)]]       # los 3 en una vertical
    cutter._recompute()
    assert cutter.complete and cutter.plan is None and "alineados" in cutter.error.lower()
    assert "No sirve" in cutter.message() and "Deshacer" in cutter.message()
    cutter.undo()
    assert cutter.error == "" and len(cutter.points) == 2


def test_save_writes_both_pieces_and_leaves_the_original(ctrl, cutter):
    click_all(cutter)
    res = cutter.save()
    assert res is not None and (res.arm.ref, res.hand.ref) == ("brazo-muneco-01@2", "mano-muneco-01@1")
    assert "no se tocó" in ctrl.message and "brazo-muneco-01@1" in ctrl.message
    cat = ctrl.session._catalog()
    assert cat.versions("brazo-muneco-01") == [1, 2] and cat.verify().ok
    assert "muneca" not in cat.meta("brazo-muneco-01@1").puertos
    # el gestor ve las piezas nuevas
    assert {"brazo-muneco-01@2", "mano-muneco-01@1"} <= {m.ref for m in CM.CatalogManager(ctrl).pieces()}


def test_hand_id_that_exists_needs_a_new_version_or_another_id(ctrl, cutter):
    click_all(cutter)
    assert cutter.save() is not None
    again = CM.WristCutter(ctrl, cutter.meta, cutter.mesh)                    # el brazo @1 otra vez
    click_all(again)
    assert again.save() is None and "ya existe" in ctrl.message
    assert ctrl.session._catalog().versions("brazo-muneco-01") == [1, 2]      # no escribió nada
    again.set_hand_id_and_recompute("mano-otra-01")
    assert again.plan.hand_meta.id == "mano-otra-01"
    assert again.save().hand.ref == "mano-otra-01@1"
    third = CM.WristCutter(ctrl, cutter.meta, cutter.mesh)
    click_all(third)
    assert third.save(new_hand_version=True).hand.ref == "mano-muneco-01@2"
    bad = CM.WristCutter(ctrl, cutter.meta, cutter.mesh)
    click_all(bad)
    bad.set_hand_id_and_recompute("Mano Mala")
    assert bad.plan is None and "id de mano inválido" in bad.error
    assert bad.save() is None


def test_saving_before_the_third_point_is_refused(ctrl, cutter):
    assert cutter.save() is None and "Primero elegí los 3 puntos" in ctrl.message


def test_default_hand_id():
    assert Catalog.default_hand_id("brazo-guemes-01") == "mano-guemes-01"
    assert Catalog.default_hand_id("miembro-x-01") == "mano-miembro-x-01"


def test_preview_renderer_draws_marks_and_the_cut(cutter):
    pytest.importorskip("moderngl")
    pr = CM.PreviewRenderer(320, 240)
    if not pr.available:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {pr.error}")
    try:
        plain = pr.render(cutter.mesh, cutter.meta)
        click_all(cutter)
        marked = pr.render(cutter.mesh, cutter.meta, marks=cutter.marks())
        red = (marked[..., 0] > 200) & (marked[..., 1] < 90) & (marked[..., 2] < 90)
        assert not np.array_equal(plain, marked) and red.sum() > 10                # los puntos rojos se ven
        cut = pr.render_parts(cutter.preview_parts())
        green = (cut[..., 1] > 150) & (cut[..., 0] < 120) & (cut[..., 2] < 140)
        assert green.sum() > 50                                                     # la mano, en verde
    finally:
        pr.close()


# ---- zoom de la vista previa
def test_zoom_scales_the_visible_size_and_defaults_do_not_change_the_view(cutter):
    b = CM.view_bounds(cutter.mesh, cutter.meta)
    base = CM.preview_camera(b, 1.3)
    assert CM.preview_camera(b, 1.3, 0, 0, 1.0, (0.0, 0.0)).distance == pytest.approx(base.distance)
    assert CM.preview_camera(b, 1.3, zoom=2.0).distance == pytest.approx(base.distance / 2)
    assert CM.preview_camera(b, 1.3, zoom=1e6).distance == pytest.approx(base.distance / CM.ZOOM_MAX)     # tope


def test_zoom_about_keeps_the_point_under_the_cursor_fixed(cutter):
    b = CM.view_bounds(cutter.mesh, cutter.meta)
    aspect = IMG[0] / IMG[1]
    u, v = 0.3, -0.2                                              # cursor, en alturas de imagen desde el centro
    z, pan = 1.5, (0.1, -0.05)

    def world_under(zoom, pn):
        cam = CM.preview_camera(b, aspect, zoom=zoom, pan=pn)
        o, d = cam.ray(IMG[0] / 2 + u * IMG[1], IMG[1] / 2 - v * IMG[1], *IMG)
        return o + d * np.dot(np.asarray(cam.target) - o, d)       # ortográfica: el rayo es paralelo al eje, vale cualquier plano

    z2, pan2 = CM.zoom_about(z, pan, 1.25, u, v)
    assert z2 == pytest.approx(1.875)
    p1, p2 = world_under(z, pan), world_under(z2, pan2)
    cam = CM.preview_camera(b, aspect)
    right, up = cam.view()[0, :3], cam.view()[1, :3]
    assert np.dot(p1 - p2, right) == pytest.approx(0.0, abs=1e-6) and np.dot(p1 - p2, up) == pytest.approx(0.0, abs=1e-6)


def test_zoom_limits():
    z, _ = CM.zoom_about(1.0, (0, 0), 1e9)
    assert z == CM.ZOOM_MAX
    z, _ = CM.zoom_about(1.0, (0, 0), 1e-9)
    assert z == CM.ZOOM_MIN


def test_clicks_follow_the_zoomed_and_panned_view(cutter):
    """Con zoom y movimiento la cámara cambia: el clic tiene que usar la misma que el dibujo."""
    view = {"zoom": 4.0, "pan": (0.05, -0.1)}
    pts = ring_points(cutter)
    # centrar la vista en la muñeca para que los puntos queden dentro de la imagen
    b = CM.view_bounds(cutter.mesh, cutter.meta)
    cam0 = CM.preview_camera(b, IMG[0] / IMG[1])
    c = np.mean(pts, axis=0) - np.asarray(cam0.target)
    right, up = cam0.view()[0, :3], cam0.view()[1, :3]
    h = 2 * cam0.distance * np.tan(np.radians(cam0.fov) / 2)
    view["pan"] = (float(np.dot(c, right) / h), float(np.dot(c, up) / h))
    for i, t in enumerate(pts):
        x, y = widget_xy(cutter, t, **view)
        assert 0 <= x <= WIDGET[0] and 0 <= y <= WIDGET[1]
        cutter.pick_at(x, y, WIDGET, IMG, **view)
        assert np.allclose(cutter.points[-1], t, atol=1e-3)
    assert cutter.complete


def test_render_with_zoom_shows_a_bigger_piece(cutter):
    pytest.importorskip("moderngl")
    r = CM.PreviewRenderer()
    if r._ensure() is None:
        pytest.skip("sin EGL")
    try:
        a = r.render(cutter.mesh, cutter.meta)
        z = r.render(cutter.mesh, cutter.meta, zoom=2.0)
    finally:
        r.close()
    bg = a[0, 0]
    assert (np.any(a != bg, axis=2)).sum() < (np.any(z != bg, axis=2)).sum()
