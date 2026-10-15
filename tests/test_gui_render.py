import numpy as np
import pytest

moderngl = pytest.importorskip("moderngl")

from afm.gui.camera import OrbitCamera
from afm.planes import Plane
from afm.testfigure import make_doll


@pytest.fixture(scope="module")
def off():
    from afm.gui.render import OffscreenRenderer
    try:
        return OffscreenRenderer(480, 640)
    except Exception as e:           # sin EGL/GL en este entorno
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {e}")


@pytest.fixture(scope="module")
def doll():
    return make_doll()


def _cam(doll):
    cam = OrbitCamera()
    cam.frame(doll.bounds)
    cam.set_view("frente")
    return cam


def _bg(off):
    return (np.array(off.scene.background) * 255).astype(int)


def test_mesh_is_drawn_where_camera_says(off, doll):
    off.scene.clear()
    off.scene.set_mesh("figura", doll)
    cam = _cam(doll)
    img = off.render(cam)
    bg = _bg(off)
    px, py, _ = cam.project(np.array([0.0, 0.0, 55.0]), off.width, off.height)[0]     # torso
    assert np.abs(img[int(py), int(px), :3].astype(int) - bg[:3]).max() > 40            # pinta la malla
    assert np.abs(img[5, 5, :3].astype(int) - bg[:3]).max() < 4                          # fondo en la esquina


def test_point_sprite_lands_on_projected_pixel(off, doll):
    off.scene.clear()
    off.scene.set_mesh("figura", doll)
    cam = _cam(doll)
    pt = np.array([0.0, 11.0, 55.0])                       # sobre la superficie frontal del torso
    off.scene.set_points("pick", [pt], color=(1, 0, 0, 1), size=14)
    img = off.render(cam)
    px, py, _ = cam.project(pt, off.width, off.height)[0]
    r, g, b = img[int(round(py)), int(round(px)), :3].astype(int)
    assert r > 200 and g < 60 and b < 60


def test_clip_x_hides_half(off, doll):
    off.scene.clear()
    off.scene.set_mesh("figura", doll, clip_x=0.0)
    cam = _cam(doll)
    img = off.render(cam)
    bg = _bg(off)
    keep = cam.project(np.array([9.0, 0.0, 20.0]), off.width, off.height)[0]    # pierna +X (se ve)
    cut = cam.project(np.array([-9.0, 0.0, 20.0]), off.width, off.height)[0]    # pierna -X (recortada)
    assert keep[0] < cut[0]                  # vista frontal: +X queda a la izquierda de la pantalla
    shown = img[int(keep[1]), int(keep[0]), :3].astype(int)
    hidden = img[int(cut[1]), int(cut[0]), :3].astype(int)
    assert np.abs(shown - bg[:3]).max() > 40 and np.abs(hidden - bg[:3]).max() < 4


def test_plane_and_lines_render_without_error(off, doll, tmp_path):
    off.scene.clear()
    off.scene.set_mesh("figura", doll)
    off.scene.set_plane("plano", Plane.from_normal((0, 0, 0), (0, 1, 0)), 60, center=(0, 0, 50))
    off.scene.set_lines("poli", np.array([[15, 0, 43], [26, 0, 43], [26, 0, 76], [15, 0, 76]], float), closed=True)
    img = off.render(_cam(doll))
    assert img.shape == (640, 480, 4)
    off.save_png(_cam(doll), str(tmp_path / "escena.png"))
    off.scene.remove("plano")
    assert "plano" not in off.scene.items and "plano:borde" not in off.scene.items
