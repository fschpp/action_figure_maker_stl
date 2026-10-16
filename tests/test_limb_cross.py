"""Segundo corte por miembro (costado + frente): el miembro es lo que queda dentro de los dos prismas."""
import numpy as np
import pytest

from afm import solids as S
from afm.gui.controller import Controller, Mode
from afm.gui.session import ARM_SLAB_MM, Session
from afm.limbs import CROSS_THICKNESS_MM, LimbCut, LimbSpec, limb_prism
from afm.planes import Plane
from afm.testfigure import make_doll

FRONT = Plane.from_normal((0, 0, 0), (0, 1, 0), "frente")
SIDE = Plane.from_normal((0, 0, 0), (1, 0, 0), "costado")
# Pierna del muñeco vista de frente (x, z): columna + escalón de cadera (como `demo_project`).
LEG_FRONT = np.array([[x, 0, z] for x, z in [(0, -1), (17, -1), (17, 34), (14, 34), (14, 42), (0, 42)]], float)


def _side_poly(y0, y1, z0=-1.0, z1=60.0):
    return np.array([[0, y0, z0], [0, y1, z0], [0, y1, z1], [0, y0, z1]], float)


@pytest.fixture(scope="module")
def half():
    return S.to_manifold(make_doll()).trim_by_plane((1.0, 0.0, 0.0), 0.0)


def _leg_front_only():
    return LimbSpec("pierna", "leg", FRONT, LEG_FRONT, 100.0)


def _leg_side_plus_front(y0=-30.0, y1=30.0):
    """Principal de costado atravesando todo el cuerpo + segundo corte de frente."""
    return LimbSpec("pierna", "leg", SIDE, _side_poly(y0, y1), CROSS_THICKNESS_MM, extrude="symmetric",
                    cross=LimbCut(FRONT, LEG_FRONT))


def test_cross_roundtrip_and_old_projects_load():
    spec = _leg_side_plus_front()
    d = spec.to_dict()
    back = LimbSpec.from_dict(d)
    assert back.cross is not None and np.allclose(back.cross.polygon, LEG_FRONT)
    assert np.allclose(back.cross.plane.normal, FRONT.normal) and back.cross.thickness == CROSS_THICKNESS_MM
    d.pop("cross")
    assert LimbSpec.from_dict(d).cross is None            # proyectos guardados antes de este cambio
    assert _leg_front_only().to_dict()["cross"] is None


def test_prism_is_intersection_of_both(half):
    spec = _leg_side_plus_front()
    both = S.intersection(half, limb_prism(spec))
    from afm.limbs import raise_ceiling
    # el techo del 2.º corte de una pierna sube hasta la silueta de costado (z = 60) + margen: no limita la altura
    front = raise_ceiling(LEG_FRONT, 60.0)
    only_front = S.intersection(half, limb_prism(LimbSpec("pierna", "leg", FRONT, front, 100.0)))
    assert both.volume() == pytest.approx(only_front.volume(), rel=1e-4)      # el de costado es generoso: no recorta
    narrow = S.intersection(half, limb_prism(_leg_side_plus_front(-3.0, 3.0)))
    assert 0 < narrow.volume() < 0.8 * only_front.volume()                    # el de costado sí limita en Y
    bb = narrow.bounding_box()                                                # (xmin, ymin, zmin, xmax, ymax, zmax)
    assert bb[1] >= -3.0 - 1e-6 and bb[4] <= 3.0 + 1e-6


def test_invalid_cross_is_ignored(half):
    spec = _leg_side_plus_front()
    spec.cross = LimbCut(FRONT, LEG_FRONT[:2])
    assert limb_prism(spec).volume() == pytest.approx(
        S.prism_from_polygon(SIDE, _side_poly(-30, 30), CROSS_THICKNESS_MM).volume())


# ---------------------------------------------------------------- sesión / controlador
@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    return c


def _closed_poly(ctrl, uvs):
    d = ctrl.draft
    for uv in uvs:
        d.add(np.asarray(uv, float))
    ctrl.close_polygon()


def test_cross_plane_is_perpendicular_to_main(ctrl):
    s = ctrl.session
    s.add_limb("brazo", "arm", Plane.from_normal((12, 0, 60), (-1, 0, 0)))
    s.add_limb("pierna", "leg", Plane.from_normal((5, 0, 40), (-1, 0, 0)))
    s.add_limb("pierna frontal", "leg", Plane.from_normal((0, 0, 0), (0, 1, 0)))
    assert abs(s.cross_plane("brazo").normal[1]) == pytest.approx(1.0)        # costado -> frontal
    assert abs(s.cross_plane("pierna frontal").normal[0]) == pytest.approx(1.0)  # frontal -> costado


def test_leg_cross_switches_main_to_through_all_and_arm_keeps_slab(ctrl):
    s = ctrl.session
    s.add_limb("brazo", "arm", Plane.from_normal((12, 0, 60), (-1, 0, 0)))
    s.add_limb("pierna", "leg", Plane.from_normal((5, 0, 40), (-1, 0, 0)))
    assert s.limb("pierna").extrude == "outward"
    s.limb("pierna").pin_center = (7.0, 0.0, 40.0)
    s.set_limb_cross("pierna", LEG_FRONT + [0, s.cross_plane("pierna").origin[1], 0])
    leg = s.limb("pierna")
    assert leg.extrude == "symmetric" and leg.thickness == CROSS_THICKNESS_MM and leg.pin_center is None
    s.set_limb_cross("brazo", LEG_FRONT)
    arm = s.limb("brazo")
    assert arm.extrude == "outward" and arm.thickness == pytest.approx(ARM_SLAB_MM)   # el brazo sigue con su losa
    assert s.clear_limb_cross("pierna") and s.limb("pierna").cross is None
    assert s.limb("pierna").extrude == "outward" and s.limb("pierna").thickness == pytest.approx(ARM_SLAB_MM)
    assert not s.clear_limb_cross("pierna")


def test_draw_cross_polygon_in_gui_flow(ctrl, tmp_path):
    s = ctrl.session
    ctrl.start_limb_plane("leg")
    ctrl.pending = [np.array(p, float) for p in [(5, -5, 40), (5, 5, 40), (5, 0, 20)]]
    ctrl._finish_limb_plane()
    name = s.project.limbs[0].name
    ctrl.start_polygon(name, cross=True)
    assert ctrl.mode == Mode.POLYGON and ctrl.edit_cross and abs(ctrl.draft.plane.normal[1]) == pytest.approx(1.0)
    assert ctrl.camera.yaw == pytest.approx(90.0)                            # cámara de frente al plano frontal
    plane = ctrl.draft.plane
    _closed_poly(ctrl, plane.to_uv(LEG_FRONT + [0, plane.origin[1], 0]))
    leg = s.limb(name)
    assert leg.cross is not None and len(leg.cross.polygon) == 6 and len(leg.polygon) == 0
    assert ctrl.draft.closed
    ctrl.cancel()
    assert not ctrl.edit_cross
    # el polígono del 2.º corte viaja con el proyecto
    path = str(tmp_path / "p.proyecto")
    s.save_project(path)
    back = Session.open_project(path, s.stl_path)
    assert back.limb(name).cross is not None and np.allclose(back.limb(name).cross.polygon, leg.cross.polygon)
    ctrl.remove_cross(name)
    assert s.limb(name).cross is None


def test_start_polygon_main_still_works(ctrl):
    s = ctrl.session
    s.add_limb("brazo", "arm", Plane.from_normal((12, 0, 60), (-1, 0, 0)))
    ctrl.start_polygon("brazo")
    assert not ctrl.edit_cross and ctrl.draft.plane is s.limb("brazo").plane


# ----------------------------------------------- el escalón de cadera no debe limitar la altura de la cara
def test_raise_cross_top_lifts_hip_step_to_main_silhouette(ctrl):
    s = ctrl.session
    s.add_limb("pierna", "leg", Plane.from_normal((5, 0, 40), (-1, 0, 0)))
    leg = s.limb("pierna")
    leg.polygon = _side_poly(-30, 30, -1.0, 60.0)                       # silueta de costado: llega a z = 60
    y0 = s.cross_plane("pierna").origin[1]
    s.set_limb_cross("pierna", LEG_FRONT + [0, y0, 0])                  # escalón de frente: tope en z = 42
    assert s.raise_cross_top("pierna") == pytest.approx(60.0)
    z = np.asarray(leg.cross.polygon)[:, 2]
    assert z.max() == pytest.approx(60.0)
    assert (z >= 59.9).sum() == 2                                       # solo los 2 vértices del borde superior
    assert s.raise_cross_top("pierna") is None                          # ya está arriba: nada que hacer


def test_controller_raise_top_button(ctrl):
    s = ctrl.session
    s.add_limb("pierna", "leg", Plane.from_normal((5, 0, 40), (-1, 0, 0)))
    s.limb("pierna").polygon = _side_poly(-30, 30, -1.0, 60.0)
    ctrl.start_polygon("pierna", cross=True)
    ctrl.raise_top()
    assert "Abrí el 2.º corte" in ctrl.message                          # aún sin polígono cerrado
    plane = ctrl.draft.plane
    _closed_poly(ctrl, plane.to_uv(LEG_FRONT + [0, plane.origin[1], 0]))
    ctrl.raise_top()
    assert np.asarray(s.limb("pierna").cross.polygon)[:, 2].max() == pytest.approx(60.0)
    assert ctrl.draft.world()[:, 2].max() == pytest.approx(60.0)


def test_leg_cross_ceiling_does_not_limit_contact_face_height(half):
    """El 2.º corte de una pierna solo saca la columna/rebaba: su techo irregular (aquí 40–42) no debe bajar la
    cara de contacto por debajo de la silueta de costado (aquí z = 60)."""
    from afm.limbs import raise_ceiling

    ragged = np.array([[x, 0, z] for x, z in [(0, -1), (17, -1), (17, 34), (14, 34), (14, 40.5), (9, 41.2),
                                              (5, 42), (0, 41)]], float)
    spec = LimbSpec("pierna", "leg", SIDE, _side_poly(-30, 30), CROSS_THICKNESS_MM, extrude="symmetric",
                    cross=LimbCut(FRONT, ragged))
    out = raise_ceiling(ragged, 62.0)
    assert out[:, 2].max() == 62.0 and np.allclose(out[:4], ragged[:4])           # la base y el escalón no se tocan
    assert (out[[4, 5, 6], 2] == 62.0).all()                                      # todo el techo irregular sube
    assert np.allclose(raise_ceiling(ragged, 10.0), ragged)                       # nunca baja
    leg = S.intersection(half, limb_prism(spec))
    closed = LimbSpec("pierna", "arm", SIDE, _side_poly(-30, 30), CROSS_THICKNESS_MM, extrude="symmetric",
                      cross=LimbCut(FRONT, ragged))                              # un brazo conserva su techo
    arm = S.intersection(half, limb_prism(closed))
    assert leg.bounding_box()[5] > arm.bounding_box()[5] + 1.0


def test_fix_limb_extrude_keeps_symmetric_leg_with_cross():
    """Al entrar al modo pasador, una pierna con 2.º corte de frente no debe volver a «outward» (dejaba la columna
    del muslo en el torso y descartaba el pasador)."""
    s = Session()
    leg = _leg_side_plus_front()
    leg.pin_center = (5.0, 0.0, 45.0)
    s.project.limbs.append(leg)
    assert s.fix_limb_extrude("pierna") is False
    assert leg.extrude == "symmetric" and leg.pin_center == (5.0, 0.0, 45.0)
    leg.cross = None                                    # sin 2.º corte sí se corrige, como antes
    assert s.fix_limb_extrude("pierna") is True and leg.extrude == "outward"
