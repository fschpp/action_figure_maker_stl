"""Altura de referencia editable desde la GUI (`Session.set_ref_height` / `Controller.set_ref_height`)."""
import numpy as np
import pytest

from afm import meshio
from afm.gui.controller import Controller
from afm.gui.session import Session
from afm.project import Project, demo_project
from afm.testfigure import make_doll


@pytest.fixture(scope="module")
def doll_stl(tmp_path_factory):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path_factory.mktemp("doll") / "muneco.stl")
    m.export(path)
    return path


def test_rescale_matches_reload_at_new_height(doll_stl):
    s = Session()
    s.load(doll_stl)
    assert abs(s.set_ref_height(129.0) - 1.29) < 1e-12
    ref, _ = meshio.load_figure(doll_stl, 129.0, None)
    assert np.allclose(ref.bounds, s.base.bounds, atol=1e-6) and s.project.params.ref_height == 129.0
    s.set_ref_height(100.0)                      # ida y vuelta: sin deriva
    ref, _ = meshio.load_figure(doll_stl, 100.0, None)
    assert np.allclose(ref.bounds, s.base.bounds, atol=1e-6)


def test_rescale_keeps_crown_consistent(doll_stl):
    s = Session()
    s.load(doll_stl)
    s.set_crown_from_mm(90.0)
    s.set_ref_height(129.0)
    ref, _ = meshio.load_figure(doll_stl, 129.0, s.project.crown_z)
    assert np.allclose(ref.bounds, s.base.bounds, atol=1e-6)
    assert abs(s.base.bounds[1, 2] - 129.0 * 100 / 90) < 1e-6      # coronilla marcada a 90 mm → 129 mm


def test_project_geometry_scales_but_absolute_params_do_not(doll_stl):
    s = Session(demo_project())
    s.load(doll_stl)
    before = Project.from_dict(s.project.to_dict())
    pins = s.project.params.to_dict()
    s.set_ref_height(129.0)
    k, after = 1.29, s.project
    assert np.allclose(after.limbs[0].polygon, before.limbs[0].polygon * k)
    assert np.allclose(after.limbs[1].plane.origin, before.limbs[1].plane.origin * k)
    assert np.allclose(after.head.plane.origin, before.head.plane.origin * k)
    assert np.allclose(after.limbs[0].plane.normal, before.limbs[0].plane.normal)
    assert abs(after.limbs[0].thickness - before.limbs[0].thickness * k) < 1e-9
    new_params = after.params.to_dict()
    new_params.pop("ref_height"), pins.pop("ref_height")
    assert new_params == pins                       # pasadores, holgura, clic: absolutos
    s.set_ref_height(100.0)
    assert np.allclose(s.project.limbs[0].polygon, before.limbs[0].polygon)


def test_invalidates_outcome_and_rejects_bad_values(doll_stl):
    s = Session()
    s.load(doll_stl)
    s.outcome = object()
    s.set_ref_height(110.0)
    assert s.outcome is None
    for bad in (0.0, 5.0, 1000.0, float("nan")):
        with pytest.raises(ValueError):
            s.set_ref_height(bad)
    assert s.project.params.ref_height == 110.0
    s.outcome = object()
    assert s.set_ref_height(110.0) == 1.0 and s.outcome is not None      # sin cambio: no invalida


def test_set_height_without_figure_just_sets_param():
    s = Session()
    s.set_ref_height(129.0)
    assert s.project.params.ref_height == 129.0 and s.base is None


def test_project_roundtrip_reloads_at_saved_height(doll_stl, tmp_path):
    s = Session()
    s.load(doll_stl)
    s.set_ref_height(129.0)
    path = str(tmp_path / "x.proyecto")
    s.save_project(path)
    t = Session.open_project(path)
    assert abs(t.base.extents[2] - 129.0) < 1e-6


def test_controller_message_and_profile_keeps_height(doll_stl, tmp_path):
    c = Controller()
    c.open_stl(doll_stl)
    assert c.set_ref_height(129.0) is True and "129" in c.message and c.session.project.params.ref_height == 129.0
    assert c.save_profile("linea", str(tmp_path)) is not None
    c.set_ref_height(100.0)
    assert c.load_profile("linea", str(tmp_path)) is True
    assert c.session.project.params.ref_height == 100.0            # el perfil no pisa la altura de la figura abierta
    assert abs(c.session.base.extents[2] - 100.0) < 1e-6
    assert c.set_ref_height(5.0) is False and c.session.project.params.ref_height == 100.0


def test_controller_scales_joint_guides_and_leaves_edit_modes(doll_stl):
    import numpy as np
    c = Controller()
    c.open_stl(doll_stl)
    c.joint_guides["rodilla"] = (np.array([[1.0, 0, 2], [1, 0, 3]]), np.array([[1.0, 0, 3], [1, 0, 4]]))
    c.set_ref_height(200.0)
    assert np.allclose(c.joint_guides["rodilla"][0], np.array([[2.0, 0, 4], [2, 0, 6]]))
