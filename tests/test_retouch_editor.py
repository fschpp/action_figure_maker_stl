"""R07 · Modo «Retocar» sin GTK: picking del clic sobre la vista previa, trazos y dibujo superpuesto."""
import numpy as np
import pytest

from afm.catalog import Catalog
from afm.gui.catalog_manager import widget_to_image
from afm.gui.retouch_editor import OVERLAY_MAX_POINTS, RetouchEditor
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


def test_overlay_groups_by_role_and_eraser_is_white(ed):
    _cat, e = ed
    x, y = center_click(e)
    e.press(x, y, WIDGET, IMG); e.release()
    e.session.set_mode("borrar")
    e.press(x, y, WIDGET, IMG); e.release()
    ov = e.overlay()
    assert "retoque_borrador" in ov and ov["retoque_borrador"][1] == (1.0, 1.0, 1.0, 1.0)
    rol = e.session.palette_ids[0]
    pts, rgba = ov["retoque_" + rol]
    assert pts.shape[1] == 3 and len(rgba) == 4


def test_overlay_is_capped(ed):
    _cat, e = ed
    x, y = center_click(e)
    e.press(x, y, WIDGET, IMG)
    p = np.asarray(e.session.retouch().strokes[0].c)
    e.session.radius = 0.05
    for i in range(1, OVERLAY_MAX_POINTS + 50):
        e.session.add_point(tuple(p + (i * 0.1, 0, 0)))
    e.release()
    total = sum(len(v[0]) for v in e.overlay().values())
    assert total <= OVERLAY_MAX_POINTS


def test_dot_size_follows_radius_and_is_clamped(ed):
    _cat, e = ed
    e.session.set_radius(0.1); small = e.dot_size_px(IMG, 0, 0, 1.0, (0, 0))
    e.session.set_radius(10.0); big = e.dot_size_px(IMG, 0, 0, 1.0, (0, 0))
    assert 4.0 <= small <= big <= 96.0


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
