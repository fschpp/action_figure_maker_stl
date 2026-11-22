"""R07 · Modo «Retocar» sin GTK: picking del clic sobre la vista previa, trazos y dibujo superpuesto."""
import numpy as np
import pytest

from afm.catalog import Catalog
from afm.gui.catalog_manager import widget_to_image
from afm.gui.retouch_editor import RetouchEditor
from test_catalog_retouch import REF, base_line, line  # noqa: F401  (fixtures)

IMG = (320, 320)
WIDGET = (320.0, 320.0)


@pytest.fixture()
def ed(line):
    cat = Catalog.open(line)
    meta, mesh = cat.load(REF)
    return cat, RetouchEditor(cat, meta, mesh)


def center_click(e):
    """Píxel del widget donde cae el centro de la cámara (siempre sobre la pieza)."""
    cam = e.camera(IMG, 0, 0, 1.0, (0, 0))
    px, py = cam.project(np.asarray(cam.target, float), *IMG)[0, :2]
    return float(px), float(py)


def test_pick_hits_piece_with_normal_facing_camera(ed):
    _cat, e = ed
    x, y = center_click(e)
    res = e.pick(x, y, WIDGET, IMG)
    assert res is not None
    p, n = res
    cam = e.camera(IMG, 0, 0, 1.0, (0, 0))
    _o, d = cam.ray(x, y, *IMG)
    assert float(n @ d) <= 0 and abs(np.linalg.norm(n) - 1) < 1e-6
    assert e.mesh.bounds[0].min() - 1e-6 <= p.min()


def test_pick_outside_image_or_background_is_none(ed):
    _cat, e = ed
    assert e.pick(-5, 10, WIDGET, IMG) is None
    assert e.pick(0.5, 0.5, WIDGET, IMG) is None          # esquina: fondo


def test_press_move_release_paints_a_stroke_and_undo(ed):
    _cat, e = ed
    x, y = center_click(e)
    assert e.press(x, y, WIDGET, IMG)
    e.session.radius = 0.5
    assert e.move(x + 25, y, WIDGET, IMG) >= 0
    n = e.release()
    assert n >= 1 and e.dirty and not e.session.stroking
    assert e.move(x, y, WIDGET, IMG) == 0                  # sin trazo abierto no agrega
    assert e.session.undo() and not e.dirty


def test_press_off_piece_returns_false(ed):
    _cat, e = ed
    assert e.press(0.5, 0.5, WIDGET, IMG) is False and not e.session.stroking


def test_apply_writes_catalog_and_clears_dirty(ed):
    cat, e = ed
    x, y = center_click(e)
    e.press(x, y, WIDGET, IMG); e.release()
    meta = e.apply(cat)
    assert meta.color["retoque"]["n"] >= 1 and not e.dirty
    assert Catalog.open(cat.root).retouch(REF) is not None


def test_widget_to_image_letterbox():
    assert widget_to_image(10, 10, 640, 320, 320, 320) is None       # margen izquierdo
    assert widget_to_image(320, 160, 640, 320, 320, 320) == (160.0, 160.0)


# ------------------------------------------------------------------ ayudas de la ventana (sin GTK)
def test_can_retouch_only_roles_mode():
    from types import SimpleNamespace as NS

    from afm.gui.catalog_manager import can_retouch
    assert not can_retouch(None)[0]
    assert not can_retouch(NS(color=None))[0]
    assert not can_retouch(NS(color={"modo": "rgb", "fbx": "x"}))[0]
    assert can_retouch(NS(color={"modo": "roles"})) == (True, "")


def test_preview_overlay_adds_and_removes_layers():
    from afm.gui.catalog_manager import PreviewRenderer

    class Scene:
        def __init__(self): self.pts, self.removed = {}, []
        def set_points(self, name, pts, rgba, size=10.0, on_top=False): self.pts[name] = (len(pts), rgba, size)
        def remove(self, name): self.removed.append(name); self.pts.pop(name, None)

    off = type("Off", (), {"scene": Scene()})()
    pr = PreviewRenderer.__new__(PreviewRenderer)
    pr._overlay(off, {"retoque_a": (np.zeros((3, 3)), (1, 0, 0, 1))}, 20.0)
    assert off.scene.pts["retoque_a"] == (3, (1, 0, 0, 1), 20.0)
    pr._overlay(off, {"retoque_b": (np.zeros((1, 3)), (0, 1, 0, 1))}, 20.0)
    assert "retoque_a" in off.scene.removed and "retoque_a" not in off.scene.pts
    pr._overlay(off, None, 20.0)
    assert not off.scene.pts


def test_color_cache_key_changes_with_saved_retouch(line):
    """Aplicar un retoque cambia el hash de la ficha: la vista a color guardada se vuelve a calcular."""
    from afm.retouch import Retouch, Stroke
    cat = Catalog.open(line)
    before = (cat.meta(REF).color or {}).get("retoque")
    _m, mesh = cat.load(REF)
    cat.set_retouch(REF, Retouch([Stroke(tuple(np.asarray(mesh.vertices)[0]), 2.0, cat.palette().ids()[0])]))
    after = cat.meta(REF).color["retoque"]["sha256"]
    assert before is None and len(after) == 64


# ---------------------------------------------------------------- resaltado bajo el mouse
def test_brush_faces_respects_radius_and_normal(ed):
    from afm.gui.retouch_editor import brush_faces

    _cat, e = ed
    x, y = center_click(e)
    c, n = e.pick(x, y, WIDGET, IMG)
    small = brush_faces(e.mesh, c, n, 0.5)
    big = brush_faces(e.mesh, c, n, 1e6)
    assert len(small) >= 1 and len(big) >= len(small)
    assert set(small) <= set(big)
    assert (e.mesh.face_normals[big] @ n >= 0.3 - 1e-9).all() or len(big) == 1   # MIN_DOT
    only = brush_faces(e.mesh, c, n, 1e6, only_nearest=True)
    assert len(only) == 1 and only[0] in small


def test_face_edges_unique_and_capped(ed):
    from afm.gui.retouch_editor import face_edges

    _cat, e = ed
    faces = np.arange(len(e.mesh.faces))
    seg = face_edges(e.mesh, faces, limit=10_000_000)
    assert seg.shape[1:] == (2, 3)
    assert len(seg) == len(e.mesh.edges_unique)
    assert len(face_edges(e.mesh, faces, limit=5)) <= 5


def test_hover_changes_and_clears(ed):
    _cat, e = ed
    assert e.hover_segments() is None and not e.clear_hover()
    x, y = center_click(e)
    assert e.hover(x, y, WIDGET, IMG) is True
    assert e.hover_segments() is not None and len(e.hover_segments()) > 0
    assert e.hover(x, y, WIDGET, IMG) is False                 # misma posición: no hay que redibujar
    assert e.hover(0.5, 0.5, WIDGET, IMG) is True              # cae al fondo: se quita
    assert e.hover_segments() is None


def test_preview_hover_adds_and_removes_layer():
    from afm.gui.catalog_manager import PreviewRenderer

    class Scene:
        def __init__(self): self.items, self.lines = {}, {}
        def set_lines(self, name, segs, color, on_top=False, width=1.0): self.items[name] = 1; self.lines[name] = (len(segs), color, width)
        def remove(self, name): self.items.pop(name, None)

    off = type("Off", (), {"scene": Scene()})()
    pr = PreviewRenderer.__new__(PreviewRenderer)
    pr._hover(off, np.zeros((4, 2, 3)))
    assert off.scene.lines["hover_pincel"][0] == 4 and "hover_pincel" in off.scene.items
    pr._hover(off, None)
    assert "hover_pincel" not in off.scene.items


def test_editor_draws_no_color_blobs(ed):
    """El modo «Retocar» no dibuja manchas de color sobre la pieza: la única guía es el resaltado de aristas."""
    _cat, e = ed
    assert not hasattr(e, "overlay")
    x, y = center_click(e)
    assert e.press(x, y, WIDGET, IMG) is True
    e.release()
    assert e.hover(x, y, WIDGET, IMG) is True and len(e.hover_segments()) > 0
