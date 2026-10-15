"""La cámara es ortográfica: el tamaño en pantalla no depende de la distancia, y los rayos son paralelos."""
import numpy as np

from afm.gui.camera import OrbitCamera


def test_ortho_size_independent_of_depth():
    cam = OrbitCamera()
    cam.set_view("frente")
    near_pt, far_pt = np.array([[5.0, 20.0, 50.0]]), np.array([[5.0, -20.0, 50.0]])   # misma x,z, distinta profundidad
    a = cam.project(near_pt, 800, 600)[0, :2]
    b = cam.project(far_pt, 800, 600)[0, :2]
    assert np.allclose(a, b, atol=1e-6)


def test_ortho_rays_are_parallel():
    cam = OrbitCamera()
    cam.set_view("izquierda")
    (o1, d1), (o2, d2) = cam.ray(100, 100, 800, 600), cam.ray(700, 500, 800, 600)
    assert np.allclose(d1, d2, atol=1e-9) and not np.allclose(o1, o2)


def test_ray_roundtrip_through_projection():
    cam = OrbitCamera()
    cam.yaw, cam.pitch = 50.0, 25.0
    px = cam.project(np.array([[3.0, 4.0, 60.0]]), 800, 600)[0, :2]
    o, d = cam.ray(px[0], px[1], 800, 600)
    t = (np.array([3.0, 4.0, 60.0]) - o) @ d
    assert np.allclose(o + t * d, [3.0, 4.0, 60.0], atol=1e-6)


def test_perspective_still_available():
    cam = OrbitCamera(ortho=False)
    assert cam.proj(1.5)[3, 2] == -1.0
