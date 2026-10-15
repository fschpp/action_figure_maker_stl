import numpy as np
import trimesh

from afm.gui.camera import OrbitCamera
from afm.gui.picking import Picker


def test_front_view_looks_from_plus_y():
    cam = OrbitCamera(target=np.array([0.0, 0.0, 50.0]), distance=200, yaw=90, pitch=0)
    assert np.allclose(cam.eye(), [0, 200, 50], atol=1e-9)


def test_center_pixel_ray_hits_target():
    cam = OrbitCamera(target=np.array([1.0, 2.0, 3.0]), distance=100, yaw=30, pitch=20)
    o, d = cam.ray(400, 300, 800, 600)
    # la recta pasa por el objetivo
    t = (cam.target - o) @ d
    assert np.linalg.norm(o + t * d - cam.target) < 1e-6


def test_project_inverts_ray():
    cam = OrbitCamera(target=np.array([0.0, 0.0, 40.0]), distance=250, yaw=70, pitch=15)
    pt = np.array([5.0, -3.0, 55.0])
    px, py, _ = cam.project(pt, 800, 600)[0]
    o, d = cam.ray(px, py, 800, 600)
    t = (pt - o) @ d
    assert np.linalg.norm(o + t * d - pt) < 1e-6


def test_orbit_and_zoom_limits():
    cam = OrbitCamera()
    cam.orbit(0, 10_000)
    assert cam.pitch == 89.0
    cam.zoom(1e-6)
    assert cam.distance >= cam.radius * 0.1


def test_pan_moves_target_with_cursor():
    cam = OrbitCamera(target=np.array([0.0, 0.0, 0.0]), distance=100, yaw=90, pitch=0)
    before = cam.project(cam.target, 800, 600)[0, :2]
    cam.pan(50, 0, 600)
    after = cam.project(np.array([0.0, 0.0, 0.0]), 800, 600)[0, :2]
    assert after[0] - before[0] > 40            # lo que estaba en el centro sigue al cursor


def test_pick_snaps_to_vertex():
    box = trimesh.creation.box((10, 10, 10))
    pk = Picker(box)
    r = pk.pick((0.2, 0.2, 30), (0, 0, -1), snap_radius=20)   # cae cerca del centro de la cara superior
    assert r is not None and r.snapped and abs(r.point[2] - 5) < 1e-9
    assert np.allclose(np.abs(r.point[:2]), 5)                # esquina de la caja
    r2 = pk.pick((0.2, 0.2, 30), (0, 0, -1), snap_radius=0)
    assert not r2.snapped and np.allclose(r2.point, [0.2, 0.2, 5])
    assert pk.pick((50, 50, 30), (0, 0, -1)) is None
