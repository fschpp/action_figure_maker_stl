"""GUI (sin GTK): plano del cuello, entrada en la lista de planos, generación solo con cabeza."""
import dataclasses

import numpy as np
import pytest

from afm.gui.controller import Controller, Mode
from afm.gui.picking import PickResult
from afm.testfigure import make_doll

NECK = [(-5, -5, 80.0), (5, -5, 80.0), (0, 5, 80.0)]


@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    return c


def feed(c, pts):
    it = iter(pts)
    c.picker.pick_pixel = lambda *a, **k: PickResult(*(np.asarray(p, float) for p in (next(it),) * 2), 0,
                                                     np.array([0.0, 1.0, 0.0]), True)
    for _ in pts:
        c.click(10, 10, 100, 100)


def test_neck_plane_needs_symmetry(tmp_path):
    m = make_doll()
    path = str(tmp_path / "m.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.start_neck_plane()
    assert c.mode == Mode.VIEW and "simetría" in c.message


def test_three_clicks_define_the_head(ctrl):
    ctrl.start_neck_plane()
    assert ctrl.mode == Mode.NECK_PLANE
    feed(ctrl, NECK)
    assert ctrl.mode == Mode.VIEW and ctrl.session.project.head is not None
    ent = {e.id: e for e in ctrl.plane_entries()}
    assert ent["cabeza"].kind == "head"
    assert "cabeza" in ctrl.trim_targets()
    assert ctrl.problems() == []                       # solo la cabeza alcanza para generar


def test_vertical_neck_plane_is_rejected(ctrl):
    ctrl.start_neck_plane()
    feed(ctrl, [(0, 0, 70.0), (0, 10, 70.0), (0, 0, 80.0)])
    assert ctrl.session.project.head is None and "No sirve" in ctrl.message


def test_remove_head_plane_and_symmetry(ctrl):
    ctrl.session.set_head(ctrl.session.plane_from_points(NECK, "cabeza"))
    ctrl.remove_plane("cabeza")
    assert ctrl.session.project.head is None and not any(e.id == "cabeza" for e in ctrl.plane_entries())
    ctrl.session.set_head(ctrl.session.plane_from_points(NECK, "cabeza"))
    ctrl.remove_plane("simetria")
    assert ctrl.session.project.head is None           # depende de la simetría, igual que los miembros


def test_generate_head_only_and_pose_ignores_head(ctrl):
    sess = ctrl.session
    sess.project.params = dataclasses.replace(sess.project.params, decimate=0,
                                              neck_pin=dataclasses.replace(sess.project.params.neck_pin,
                                                                           shaft_d=3.2, head_d=5.2))
    sess.set_head(sess.plane_from_points(NECK, "cabeza"))
    out = sess.build()
    assert set(out.result.pieces) == {"torso", "cabeza"} and out.validation["ok"], out.validation["problems"]
    ctrl.session.outcome = out
    assert ctrl.pose_limit() is None                   # la cabeza no cuenta como cadera
    assert ctrl._posed_pieces(90.0) == {}              # ni entra en la pose sentada


def test_front_change_clears_head(ctrl):
    ctrl.session.set_head(ctrl.session.plane_from_points(NECK, "cabeza"))
    ctrl.session.set_front("-Y")
    assert ctrl.session.project.head is None


# ------------------------------------------------------------ cabeza de reemplazo
@pytest.fixture()
def new_head(tmp_path):
    import trimesh
    from afm import solids as S
    ball = S.Manifold.sphere(15, 64)
    flat = S.intersection(ball, S.Manifold.cube((100, 100, 100), center=True).translate((0, 0, 40)))   # base plana z = -10
    path = str(tmp_path / "otra_cabeza.stl")
    S.to_trimesh(flat).export(path)
    return path


def _small_pin(ctrl):
    sess = ctrl.session
    sess.project.params = dataclasses.replace(sess.project.params, decimate=0,
                                              neck_pin=dataclasses.replace(sess.project.params.neck_pin,
                                                                           shaft_d=3.2, head_d=5.2))


def test_replacement_needs_the_neck_plane(ctrl, new_head):
    assert ctrl.set_head_replacement(new_head) is False
    assert "plano del cuello" in ctrl.message and ctrl.session.project.head is None


def test_replacement_is_loaded_and_reported(ctrl, new_head):
    ctrl.session.set_head(ctrl.session.plane_from_points(NECK, "cabeza"))
    assert ctrl.set_head_replacement(new_head) is True
    h = ctrl.session.project.head
    assert h.replacement_path == new_head and "escala ×" in ctrl.message and "base coincide" in ctrl.message
    assert ctrl.session.outcome is None
    # cambiar el plano del cuello conserva la cabeza elegida
    ctrl.session.set_head(ctrl.session.plane_from_points([(-5, -5, 79.0), (5, -5, 79.0), (0, 5, 79.0)], "cabeza"))
    assert ctrl.session.project.head.replacement_path == new_head


def test_bad_replacement_changes_nothing(ctrl, tmp_path):
    ctrl.session.set_head(ctrl.session.plane_from_points(NECK, "cabeza"))
    assert ctrl.set_head_replacement(str(tmp_path / "no_existe.stl")) is False
    assert "No se pudo usar esa cabeza" in ctrl.message and ctrl.session.project.head.replacement_path is None
    bad = tmp_path / "vacio.stl"
    bad.write_text("solid x\nendsolid x\n")
    assert ctrl.set_head_replacement(str(bad)) is False and ctrl.session.project.head.replacement_path is None


def test_generate_with_replacement_then_back_to_original(ctrl, new_head):
    _small_pin(ctrl)
    sess = ctrl.session
    sess.set_head(sess.plane_from_points(NECK, "cabeza"))
    top0 = sess.build().result.pieces["cabeza"].bounding_box()[5]
    assert ctrl.set_head_replacement(new_head)
    out = sess.build()
    assert out.validation["ok"], out.validation["problems"]
    assert out.result.pieces["cabeza"].bounding_box()[5] < top0 - 3          # otra cabeza, más baja
    ctrl.clear_head_replacement()
    assert sess.project.head.replacement_path is None and "original" in ctrl.message
    assert sess.outcome is None


def test_cli_build_with_replacement_exports_the_toy(tmp_path, new_head):
    import os

    import trimesh

    from afm import cli
    from afm.project import demo_project
    stl, proj, out = str(tmp_path / "m.stl"), str(tmp_path / "m.proyecto"), str(tmp_path / "out")
    make_doll().export(stl)
    demo_project().save(proj)
    assert cli.main(["build", stl, proj, "--cabeza", new_head, "--out", out, "--name", "toy"]) == 0
    head = trimesh.load(os.path.join(out, "piezas", "toy_cabeza.stl"))
    asm = trimesh.load(os.path.join(out, "toy_ensamblada.stl"))
    assert head.is_watertight and head.bounds[1][2] < 97.0                    # la original llegaba a 100 mm
    assert asm.bounds[1][2] == pytest.approx(head.bounds[1][2], abs=1e-3)
    saved = open(os.path.join(out, "toy.proyecto")).read()
    assert "otra_cabeza.stl" in saved                                         # el proyecto guardado recuerda la cabeza
