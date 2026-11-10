"""Reajustar la articulación de una pieza del catálogo (gestor, sin GTK): edición pura, vista previa de la pieza sola,
versión nueva en el catálogo y el modo `JointEditor`."""
import dataclasses
import json
import os

import numpy as np
import pytest

from afm import joint_edit as JE
from afm import solids as S
from afm.catalog import Catalog, CatalogError
from afm.extract import extract_all
from afm.gui import catalog_manager as CM
from afm.gui.controller import Controller
from afm.gui.session import Session
from afm.joint_preview import prepare_piece, preview_joint
from afm.joints import JointSpec
from afm.params import HingeSpec
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll


def elbow(h: float = 3.0, **kw) -> JointSpec:
    d = dict(name="codo", limb="brazo", kind="elbow", plane=Plane.from_normal((18.5, 0, 59.0), (0, 0, 1), "articulación"),
             axis_center=(18.5, 0.0, 59.0 + h), hinge=HingeSpec(pin_d=2.0, pin_len=0.6, tongue_t=2.0, end_r=3.0))
    d.update(kw)
    return JointSpec(**d)


@pytest.fixture(scope="module")
def doll_pieces():
    ext = extract_all(S.to_manifold(make_doll()), demo_project(), "muneco", figure="doll").pieces
    return {e.meta.tipo: e for e in ext}


@pytest.fixture()
def ctrl(tmp_path, doll_pieces):
    """Controlador sin figura con un catálogo que tiene el brazo del muñeco con su codo."""
    Catalog.create(str(tmp_path / "linea"), "prueba", 100.0)
    c = Controller()
    assert c.open_catalog(str(tmp_path / "linea"))
    e = doll_pieces["brazo"]
    meta = type(e.meta).from_dict(e.meta.to_dict())
    meta.articulaciones = [elbow().to_dict()]
    c.session._catalog().add_piece(e.mesh, meta)
    return c


@pytest.fixture()
def editor(ctrl):
    mgr = CM.CatalogManager(ctrl)
    meta = next(m for m in mgr.pieces() if m.tipo == "brazo")
    return CM.JointEditor(ctrl, meta, mgr.mesh(meta.ref))


# ------------------------------------------------------------------ edición pura
def test_pure_edits_do_not_touch_the_input_and_match_the_session():
    j = elbow()
    assert JE.height(j) == pytest.approx(3.0) and JE.side_tilt(j) == pytest.approx(0.0, abs=1e-9)
    j2 = JE.with_height(j, 5.0)
    assert JE.height(j2) == pytest.approx(5.0) and JE.height(j) == pytest.approx(3.0)        # el original no se tocó
    assert j2.axis_center[:2] == j.axis_center[:2]                                           # sube en vertical
    j3 = JE.with_side_tilt(j2, 12.0)
    assert JE.side_tilt(j3) == pytest.approx(12.0, abs=1e-6) and JE.height(j3) == pytest.approx(5.0, abs=1e-6)
    assert j3.axis_center == j2.axis_center                                                  # se corre el plano, no el eje
    assert JE.with_front_tilt(j, 9.5).axis_dir[2] == pytest.approx(np.sin(np.radians(9.5)))
    assert JE.front_tilt(JE.with_front_tilt(j, 9.5)) == pytest.approx(9.5)
    assert JE.with_range(j, 0, 120).flex_range == (0.0, 120.0) and JE.with_range(j, None, None).flex_range is None
    k = JE.with_axis_point(j, (99.0, 1.5, 63.0))
    assert k.axis_center == (18.5, 1.5, 63.0)                                                # la x se conserva
    # la misma cuenta que hace la sesión con las articulaciones del proyecto
    p = demo_project()
    p.joints.append(elbow())
    s = Session()
    s.project = p
    s.set_joint_height("codo", 5.0)
    s.set_joint_plane_side_tilt("codo", 12.0)
    assert s.joint("codo").plane.normal == pytest.approx(j3.plane.normal, abs=1e-9)


@pytest.mark.parametrize("fn,arg", [(JE.with_height, 0.2), (JE.with_side_tilt, 75.0), (JE.with_front_tilt, 50.0)])
def test_pure_edits_reject_bad_values(fn, arg):
    with pytest.raises(ValueError):
        fn(elbow(), arg)
    with pytest.raises(ValueError):
        JE.with_range(elbow(), 100, 10)
    with pytest.raises(ValueError):
        JE.with_axis_point(elbow(), (0, 0, 58.0))                                           # bajo el plano


def test_differs_sees_every_kind_of_change():
    j = elbow()
    assert not JE.differs(j, elbow())
    for new in (JE.with_height(j, 4.0), JE.with_side_tilt(j, 5.0), JE.with_front_tilt(j, 3.0), JE.with_range(j, 0, 100)):
        assert JE.differs(j, new)


# ------------------------------------------------------------------ la pieza sola
def test_piece_previews_alone_like_the_model_arm(doll_pieces):
    e = doll_pieces["brazo"]
    limb, face = prepare_piece(e.meta, e.mesh)
    p = dataclasses.replace(demo_project().params, decimate=0)
    pv = preview_joint(limb, face, elbow(), p)
    assert pv.h == pytest.approx(3.0) and pv.carved_mm3 > 1.0
    assert preview_joint(limb, face, elbow(h=2.0), p).carved_mm3 < pv.carved_mm3             # eje más cerca del plano: se lleva menos


def test_prepare_piece_needs_an_arm_or_a_leg_with_its_port(doll_pieces):
    with pytest.raises(ValueError, match="solo un brazo o una pierna"):
        prepare_piece(doll_pieces["cabeza"].meta, doll_pieces["cabeza"].mesh)
    meta = type(doll_pieces["brazo"].meta).from_dict(doll_pieces["brazo"].meta.to_dict())
    del meta.puertos["hombro"]
    with pytest.raises(ValueError, match="hombro"):
        prepare_piece(meta, doll_pieces["brazo"].mesh)


# ------------------------------------------------------------------ catálogo: versión nueva
def test_retune_saves_a_new_version_and_leaves_the_old_one(ctrl):
    cat = ctrl.session._catalog()
    ref = cat.list()[0].ref
    old_json = open(os.path.join(cat.root, "piezas", "brazo", f"{ref}.pieza.json")).read()
    new, warns = cat.retune_joint(ref, JE.with_height(elbow(), 4.5))
    assert new.version == 2 and new.estado == "borrador" and new.id == cat.meta(ref).id
    assert JointSpec.from_dict(new.articulaciones[0]).axis_center[2] == pytest.approx(59.0 + 4.5)
    assert "reajustada desde" in new.origen["nota"] and warns == []
    assert open(os.path.join(cat.root, "piezas", "brazo", f"{ref}.pieza.json")).read() == old_json      # la versión 1 no se toca
    m1, mesh1 = cat.load(ref)
    m2, mesh2 = cat.load(new.ref)
    assert np.allclose(mesh1.vertices, mesh2.vertices) and set(m1.puertos) == set(m2.puertos)         # misma malla y puertos
    assert cat.verify().ok


def test_retune_refuses_what_makes_no_sense(ctrl, doll_pieces):
    cat = ctrl.session._catalog()
    ref = cat.list()[0].ref
    with pytest.raises(CatalogError, match="no cambió"):
        cat.retune_joint(ref, elbow())
    with pytest.raises(CatalogError, match="«knee»|rodilla|knee"):
        cat.retune_joint(ref, dataclasses.replace(elbow(), kind="knee"))
    with pytest.raises(CatalogError, match="no trae una articulación"):
        cat.retune_joint(ref, JE.with_height(dataclasses.replace(elbow(), name="otra"), 4.0))
    with pytest.raises(CatalogError, match="al menos"):
        bad = dataclasses.replace(elbow(), axis_center=(18.5, 0.0, 59.1))
        cat.retune_joint(ref, bad)
    cat.add_piece(doll_pieces["cabeza"].mesh, doll_pieces["cabeza"].meta)
    head = next(m for m in cat.list() if m.tipo == "cabeza")
    with pytest.raises(CatalogError, match="solo un brazo o una pierna"):
        cat.retune_joint(head.ref, elbow())


# ------------------------------------------------------------------ el modo del gestor
def test_can_retune_joint_explains_why_not(ctrl, doll_pieces):
    assert CM.can_retune_joint(None)[0] is False
    mgr = CM.CatalogManager(ctrl)
    arm = next(m for m in mgr.pieces() if m.tipo == "brazo")
    assert CM.can_retune_joint(arm) == (True, "")
    assert "cabeza" in CM.can_retune_joint(doll_pieces["cabeza"].meta)[1]
    assert "no trae articulación" in CM.can_retune_joint(doll_pieces["brazo"].meta)[1]       # el brazo extraído sin codo


def test_editor_changes_previews_and_saves(ctrl, editor):
    assert editor.names == ["codo"] and not editor.changed and editor.height() == pytest.approx(3.0)
    lo, hi = editor.limits()
    assert lo < 0 < hi                                                                        # hiperextensión y flexión de la línea
    editor.compute()
    assert editor.error == "" and editor.pv is not None and "Hueco" in editor.message()
    base = editor.pv.carved_mm3
    # el estado sale con la pieza entera hasta que hay cálculo; después con las dos partes de la articulación
    parts, bounds, marks = editor.view()
    assert [n for n, _m, _c in parts] == ["proximal", "distal"] and bounds is None and marks.shape == (2, 3)
    editor.set_flex(60.0)
    assert editor.view()[0] is not parts                                                     # otra flexión: otro dibujo
    assert editor.view()[0] is editor.view()[0]                                              # igual: el mismo objeto (no se resube)

    editor.set_height(2.0)
    assert editor.changed and editor.needs_compute()
    editor.compute()
    assert editor.pv.carved_mm3 < base and "Sin cambios" not in editor.message()

    editor.set_height(0.1)                                                                    # no sirve: queda el valor anterior
    assert "No sirve" in editor.error and editor.height() == pytest.approx(2.0)

    meta = editor.save()
    assert meta is not None and meta.version == 2 and "versión nueva" in ctrl.message
    assert [m.ref for m in CM.CatalogManager(ctrl).pieces(tipo="brazo")] == [editor.meta.ref, meta.ref]
    assert editor.reset() and not editor.changed


def test_editor_refuses_to_save_without_changes(ctrl, editor):
    assert editor.save() is None and "No cambiaste nada" in ctrl.message
    assert len(CM.CatalogManager(ctrl).pieces(tipo="brazo")) == 1


def test_editor_discards_a_result_that_no_longer_applies(editor):
    key, run = editor.job()
    pv = run()
    editor.set_height(4.0)                                                                    # cambió mientras se calculaba
    assert editor.done(key, pv, None) is False and editor.pv is None and not editor.busy
    assert editor.needs_compute()


def test_editor_reports_an_engine_failure_instead_of_raising(editor):
    editor.joint = dataclasses.replace(editor.joint, axis_center=(18.5, 0.0, 400.0))         # el eje queda fuera del miembro
    editor.joints[0] = editor.joint
    editor.compute()
    assert editor.pv is None and editor.error and not editor.busy


def test_editor_axis_by_click(editor):
    c = editor.joint.axis_center
    assert "Hacé clic" in editor.start_pick() and editor.pick_axis
    cam = CM.preview_camera(CM.view_bounds(editor.mesh, editor.meta), 840 / 630, 0.0, 0.0, 1.0, (0.0, 0.0))
    px, py, _ = cam.project(np.array([22.0, 0.0, 63.0]), 840, 630)[0]                        # el costado +X del brazo, a z = 63
    msg = editor.pick_at(px, py, (840.0, 630.0), (840, 630))
    assert not editor.pick_axis and editor.error == "" and "Hacé clic" not in msg
    assert editor.joint.axis_center[0] == c[0]                                               # la x se conserva
    assert editor.joint.axis_center[2] == pytest.approx(63.0, abs=0.3) and editor.changed
    editor.start_pick()
    px, py, _ = cam.project(np.array([22.0, 0.0, 55.0]), 840, 630)[0]                        # por debajo del plano (z = 59): no sirve
    assert "No sirve" in editor.pick_at(px, py, (840.0, 630.0), (840, 630)) and editor.pick_axis
    editor.cancel_pick()
    assert not editor.pick_axis


def test_find_piece_resolves_like_the_figure_rows(ctrl):
    metas = CM.CatalogManager(ctrl).pieces()
    arm = next(m for m in metas if m.tipo == "brazo")
    assert CM.find_piece(metas, arm.ref).ref == arm.ref and CM.find_piece(metas, arm.id).ref == arm.ref
    assert CM.find_piece(metas, arm.id + "@9") is None and CM.find_piece(metas, None) is None and CM.find_piece(metas, "otra@1") is None
    ctrl.session._catalog().retune_joint(arm.ref, JE.with_height(elbow(), 4.0))
    metas = CM.CatalogManager(ctrl).pieces()
    assert CM.find_piece(metas, arm.id).version == 2 and CM.find_piece(metas, arm.id + "@1").version == 1     # sin versión = la última
