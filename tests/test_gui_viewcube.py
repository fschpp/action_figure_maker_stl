import numpy as np
import pytest

from afm.gui.camera import OrbitCamera
from afm.gui.viewcube import FACES, Hit, ViewCube, face_basis, interpolate_view, view_angles

SIZE = 120
OLD_VIEWS = {"Frente": "frente", "Espalda": "espalda", "Izq.": "izquierda", "Der.": "derecha"}


def _rot(yaw, pitch):
    return OrbitCamera(yaw=yaw, pitch=pitch).view()[:3, :3]


def test_face_centers_reproduce_the_old_view_buttons():
    """El centro de cada cara lleva a la misma cámara que los botones Frente / Espalda / Izquierda / Derecha / Arriba."""
    for k, (name, _n) in enumerate(FACES):
        yaw, pitch = view_angles(Hit(k, 0, 0).direction())
        if name in OLD_VIEWS:
            cam = OrbitCamera()
            cam.set_view(OLD_VIEWS[name])
            assert (yaw, pitch) == pytest.approx((cam.yaw, cam.pitch), abs=1e-6)
        elif name == "Arriba":
            assert (yaw, pitch) == (90.0, 89.0)
        else:
            assert (yaw, pitch) == (90.0, -89.0)


def test_corner_is_isometric_and_edge_is_45_degrees():
    corner = Hit(0, 1, 1)                      # frente + borde de u + borde de v
    assert corner.kind == "corner"
    assert np.linalg.norm(corner.direction()) == pytest.approx(1.0)
    assert np.allclose(np.abs(corner.direction()), 1 / np.sqrt(3))
    edge = Hit(0, 0, 1)                        # frente + arriba
    assert edge.kind == "edge"
    yaw, pitch = view_angles(edge.direction())
    assert yaw == pytest.approx(90.0) and pitch == pytest.approx(45.0)


def test_basis_is_right_handed_for_every_face():
    for _name, n in FACES:
        u, v = face_basis(n)
        assert np.allclose(np.cross(u, v), n)


def test_hit_center_of_the_face_looking_at_us():
    cube = ViewCube(SIZE)
    for k, (name, _n) in enumerate(FACES):
        yaw, pitch = view_angles(Hit(k, 0, 0).direction())
        hit = cube.hit(_rot(yaw, pitch), SIZE / 2, SIZE / 2)
        assert hit == Hit(k, 0, 0), name


def test_hit_outside_the_cube_is_none():
    cube = ViewCube(SIZE)
    assert cube.hit(_rot(90, 0), 1, 1) is None
    assert cube.hit(_rot(90, 0), SIZE - 1, SIZE - 1) is None


def test_hit_on_the_border_cells_of_the_front_face():
    cube = ViewCube(SIZE)
    rot = _rot(90, 0)                          # de frente: a la derecha de la pantalla está -X
    s = SIZE * 0.27
    top = cube.hit(rot, SIZE / 2, SIZE / 2 - 0.9 * s)
    assert top == Hit(0, 0, 1) and top.kind == "edge"
    corner = cube.hit(rot, SIZE / 2 + 0.9 * s, SIZE / 2 - 0.9 * s)
    assert corner.kind == "corner" and corner.face == 0
    assert corner.direction()[0] < 0 and corner.direction()[2] > 0     # arriba, a la derecha de la pantalla = -X


def test_clicking_any_cell_gives_a_camera_that_looks_at_that_cell():
    """Ida y vuelta: la celda elegida queda mirando a la cámara (centro de la cara) o en su dirección."""
    cube = ViewCube(SIZE)
    for k in range(len(FACES)):
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                d = Hit(k, i, j).direction()
                yaw, pitch = view_angles(d)
                eye = OrbitCamera(yaw=yaw, pitch=pitch).eye() - OrbitCamera().target
                eye = eye / np.linalg.norm(eye)
                if not (i == 0 and j == 0 and k >= 4):          # los polos pierden 1° por el límite de pitch
                    assert eye == pytest.approx(d, abs=1e-6)
                else:
                    assert float(eye @ d) > 0.999
        rot = _rot(*view_angles(Hit(k, 0, 0).direction()))
        facing = [(rot @ np.asarray(n))[2] for _name, n in FACES]
        assert int(np.argmax(facing)) == k and k in cube.visible_faces(rot)


def test_interpolation_takes_the_short_way_around():
    a = interpolate_view((350.0, 10.0), (10.0, 30.0), 0.5)
    assert a[0] == pytest.approx(0.0, abs=1e-9) and a[1] == pytest.approx(20.0)
    assert interpolate_view((90.0, 0.0), (270.0, 0.0), 0.0) == (90.0, 0.0)
    assert interpolate_view((90.0, 0.0), (180.0, 45.0), 1.0) == pytest.approx((180.0, 45.0))


def test_draw_renders_without_errors_and_paints_pixels():
    cairo = pytest.importorskip("cairo")
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    cr = cairo.Context(surf)
    ViewCube(SIZE).draw(cr, _rot(60, 20), hover=Hit(0, 1, 1))
    data = bytes(surf.get_data())
    assert any(data[3::4])                      # canal alfa: hay algo dibujado
    assert data[3] == 0                         # el fondo (esquina) queda transparente
