"""Puertos: marco, acople, cara de unión y compatibilidad barata (Fase 1 del plan de piezas intercambiables)."""
import numpy as np
import pytest

from afm import solids as S
from afm.ports import (Port, PortError, PortFace, PortFrame, coupling_transform, face_from_mesh, face_iou, port_class,
                       port_compat, port_from_points)

M = S.Manifold
PTS = [(3.0, 0.0, 0.0), (-1.5, -2.6, 0.0), (-1.5, 2.6, 0.0)]      # plano z = 0, el punto 1 a +X


def cyl(r, h, z0=0.0, seg=96):
    return S.to_trimesh(M.cylinder(h, r, r, seg).translate((0, 0, z0)))


def d_shape(z0, h):
    """Cilindro Ø10 recortado a x < 2 (cara en D: asimétrica en u, para probar la convención de espejo)."""
    cut = M.cube((12, 20, h + 2), center=True).translate((2 - 6, 0, z0 + h / 2))
    return S.to_trimesh(S.intersection(M.cylinder(h, 5, 5, 96).translate((0, 0, z0)), cut))


def test_port_class():
    assert port_class("hombro_izq") == "hombro" and port_class("cuello") == "cuello"
    with pytest.raises(PortError):
        port_class("rodete_izq")


def test_frame_from_points_axis_leaves_the_piece():
    mesh = cyl(5, 30)                                       # la pieza está en z > 0
    f = PortFrame.from_points(PTS, mesh.center_mass)
    assert np.allclose(f.axis, [0, 0, -1]) and np.allclose(f.ref, [1, 0, 0])
    below = cyl(5, 30, z0=-30)                              # la pieza está en z < 0: el eje se da vuelta
    assert np.allclose(PortFrame.from_points(PTS, below.center_mass).axis, [0, 0, 1])
    with pytest.raises(Exception):
        PortFrame.from_points([(0, 0, 0), (1, 0, 0), (2, 0, 0)])


def test_matrix_is_a_proper_rotation_and_uv_roundtrip():
    f = PortFrame((1, 2, 3), (0, 0, -1), (1, 0, 0))
    m = f.matrix()
    assert np.allclose(m[:3, :3] @ m[:3, :3].T, np.eye(3)) and np.isclose(np.linalg.det(m[:3, :3]), 1.0)
    assert np.allclose(f.to_uv([[2, 2, 3], [1, 4, 3]]), [[1, 0], [0, -2]])       # v = axis × ref = −Y


def test_coupling_transform_aligns_opposite_axes_same_ref_and_center():
    f = PortFrame((1, 2, 3), (0, 0, -1), (1, 0, 0))
    target = PortFrame((10, 0, 50), (1, 0, 0), (0, 0, 1))
    T = coupling_transform(f, target)
    assert np.allclose(T[:3, :3] @ T[:3, :3].T, np.eye(3)) and np.isclose(np.linalg.det(T[:3, :3]), 1.0)
    g = f.transformed(T)
    assert np.allclose(g.axis, -target.axis) and np.allclose(g.ref, target.ref) and np.allclose(g.center, target.center)


def test_face_of_a_cylinder_and_roundtrip():
    mesh = cyl(5, 30)
    port = port_from_points(mesh, PTS, "hombro", "enchufe")
    assert np.allclose(port.frame.center, [0, 0, 0], atol=1e-6)
    assert port.face.radius_eq == pytest.approx(5.0, rel=0.01)
    assert len(port.face.polygons) == 1 and port.face.polygons[0]["interiors"] == []
    again = Port.from_dict(port.to_dict())
    assert again.clase == "hombro" and again.rol == "enchufe"
    assert np.allclose(again.frame.axis, port.frame.axis) and again.face.area == pytest.approx(port.face.area, rel=1e-6)


def test_plane_off_the_mesh_is_an_error():
    with pytest.raises(PortError):
        port_from_points(cyl(5, 30), [(3, 0, 10), (-1.5, -2.6, 10), (-1.5, 2.6, 10)], "hombro", "enchufe")


def test_mating_faces_coincide_in_both_frames():
    """Dos piezas que se tocan por la misma cara en D: vistas cada una desde su marco (ejes opuestos) dan IoU ≈ 1."""
    a = port_from_points(d_shape(0, 30), PTS, "hombro", "enchufe")
    b = port_from_points(d_shape(-30, 30), PTS, "hombro", "toma")
    assert np.allclose(a.frame.axis, [0, 0, -1]) and np.allclose(b.frame.axis, [0, 0, 1])
    assert face_iou(a.face, b.face) > 0.99
    # y la cara espejada en u (otra D) NO coincide: la convención distingue el lado
    from shapely import affinity
    flipped = PortFace.from_shape(affinity.scale(b.face.shape(), -1.0, 1.0, origin=(0, 0)))
    assert face_iou(a.face, flipped) < 0.9


def _port(r, rol, clase="hombro", up=True):
    mesh = cyl(r, 20, z0=0 if up else -20)
    return port_from_points(mesh, PTS, clase, rol)


def test_compat_ok_and_warnings_and_no():
    ok = port_compat(_port(8, "enchufe"), _port(8, "toma", up=False), 130, 130)
    assert ok.nivel == "ok" and ok.iou > 0.99 and ok.escalon_mm < 0.1 and not ok.avisos
    step = port_compat(_port(8, "enchufe"), _port(6.5, "toma", up=False))
    assert step.nivel == "aviso" and step.escalon_mm == pytest.approx(1.5, abs=0.05)
    assert any("escalón" in x for x in step.avisos) and any("coinciden" in x for x in step.avisos)
    alt = port_compat(_port(8, "enchufe"), _port(8, "toma", up=False), 130, 120)
    assert alt.nivel == "aviso" and any("alturas" in x for x in alt.avisos)
    assert port_compat(_port(8, "enchufe"), _port(8, "toma", clase="cadera", up=False)).nivel == "no"      # clases distintas
    assert port_compat(_port(8, "enchufe"), _port(8, "enchufe", up=False)).nivel == "no"                   # dos enchufes
    small = port_compat(_port(2, "enchufe"), _port(2, "toma", up=False))                                  # vástago Ø4,1 no entra
    assert small.nivel == "no" and any("no entra" in x for x in small.motivos)
    thin = port_compat(_port(2.8, "enchufe"), _port(2.8, "toma", up=False))                               # entra pero sin pared
    assert thin.nivel == "aviso" and any("pared" in x for x in thin.avisos)


def test_wrist_port_skips_the_pin_check():
    c = port_compat(_port(3, "enchufe", clase="muneca"), _port(3, "toma", clase="muneca", up=False))
    assert c.nivel == "ok" and c.margenes_mm == {}


def test_face_from_mesh_handles_a_ring():
    ring = S.to_trimesh(S.difference(M.cylinder(10, 6, 6, 64), M.cylinder(12, 3, 3, 64).translate((0, 0, -1))))
    frame = PortFrame.from_points(PTS, ring.center_mass)
    face = face_from_mesh(ring, frame.with_center((0, 0, 0)))
    assert face.area == pytest.approx(np.pi * (36 - 9), rel=0.01) and len(face.polygons[0]["interiors"]) == 1


def test_face_from_mesh_error_names_place_and_measures_tilt():
    """Plano girado 4° respecto de la cara de la pieza: el error dice el lugar, el giro y el corrimiento."""
    from afm.ports import diagnose_plane
    mesh = cyl(5, 30)                                       # cara de unión en z = 0, mirando a −Z
    tilt = np.radians(4.0)
    axis = np.array([np.sin(tilt), 0.0, -np.cos(tilt)])    # eje del puerto girado 4° alrededor de Y
    frame = PortFrame((0.0, 0.0, 0.0), axis, (1.0, 0.0, 0.0) - ((1.0, 0.0, 0.0) @ axis) * axis)
    with pytest.raises(PortError) as exc:
        face_from_mesh(mesh, frame, place="brazo")
    err = exc.value
    assert err.place == "brazo" and "«brazo»" in str(err)
    assert err.diag["inclinacion_deg"] == pytest.approx(4.0, abs=0.2)
    assert "4,0°" in str(err) and "girá el plano" in str(err)
    assert diagnose_plane(mesh, frame)["en_plano"] == 0


def test_diagnose_plane_far_from_piece_and_exact_face():
    from afm.ports import diagnose_plane, describe_plane_failure
    mesh = cyl(5, 30)
    far = PortFrame((0.0, 0.0, 50.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0))     # el plano no toca la pieza
    d = diagnose_plane(mesh, far)
    assert d["inclinacion_deg"] is None and "no toca la pieza" in describe_plane_failure(d)
    ok = PortFrame((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0))
    d = diagnose_plane(mesh, ok)
    assert d["en_plano"] > 0 and d["inclinacion_deg"] == pytest.approx(0.0, abs=1e-6)
    offset = PortFrame((0.0, 0.0, -1.2), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0))  # paralelo pero corrido
    d = diagnose_plane(mesh, offset)
    assert d["inclinacion_deg"] == pytest.approx(0.0, abs=1e-6) and abs(abs(d["desvio_mm"]) - 1.2) < 1e-6
    assert "corrido" in describe_plane_failure(d)


def test_snap_frame_follows_the_real_face_up_to_a_few_degrees():
    """El eje supuesto del puerto (p. ej. X) puede quedar 1° girado respecto del corte real: el puerto sigue la cara."""
    from afm.ports import snap_frame_to_face
    mesh = cyl(5, 30)                                       # cara real en z = 0, normal −Z
    for deg, expect in ((1.0, True), (2.5, True), (6.0, False)):
        t = np.radians(deg)
        axis = np.array([np.sin(t), 0.0, -np.cos(t)])
        frame = PortFrame((0.0, 0.0, 0.0), axis, (1.0, 0.0, 0.0) - ((1.0, 0.0, 0.0) @ axis) * axis)
        with pytest.raises(PortError):
            face_from_mesh(mesh, frame)                     # con el eje girado la tolerancia de 1 µm no alcanza
        snapped = snap_frame_to_face(mesh, frame)
        assert (snapped is not None) is expect
        if expect:
            new, tilt = snapped
            assert tilt == pytest.approx(deg, abs=0.05)
            assert np.allclose(new.axis, (0.0, 0.0, -1.0), atol=1e-6)
            assert abs(new.axis @ new.ref) < 1e-9
            assert face_from_mesh(mesh, new).area == pytest.approx(np.pi * 25, rel=0.02)


def test_extract_tolerates_a_slightly_tilted_cut_plane():
    """Con el plano del brazo girado 1° la extracción no falla: avisa y deja el puerto en la cara real."""
    from afm.extract import extract_all
    from afm.planes import Plane
    from afm.project import demo_project
    from afm.testfigure import make_doll
    proj = demo_project()
    arm = next(l for l in proj.limbs if l.kind == "arm")
    t = np.radians(1.0)
    n = np.asarray(arm.plane.normal, float)
    arm.plane = Plane.from_normal(arm.plane.origin, np.array([[np.cos(t), -np.sin(t), 0], [np.sin(t), np.cos(t), 0], [0, 0, 1]]) @ n,
                                  arm.plane.name)
    res = extract_all(S.to_manifold(make_doll()), proj, "doll", places=["brazo"])
    assert any("girado 1,0°" in w for w in res.warnings)
    port = res.pieces[0].meta.puertos["hombro"]
    assert abs(port.frame.axis[1]) == pytest.approx(np.sin(t), abs=2e-3) and port.face.area > 1.0
