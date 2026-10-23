"""Revisión previa de piezas de catálogo (sin generar): compatibilidad, pieza colocada, conflicto de articulación."""
import numpy as np
import pytest

from afm.catalog import Catalog
from afm.gui.session import Session
from afm.project import demo_project
from afm.testfigure import make_doll


@pytest.fixture(scope="module")
def doll_stl(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("doll") / "muneco.stl")
    make_doll().export(path)
    return path


@pytest.fixture(scope="module")
def catalogued(tmp_path_factory, doll_stl):
    """Catálogo con las piezas del muñeco de prueba ya catalogadas."""
    tmp = tmp_path_factory.mktemp("pre")
    proj = tmp / "m.proyecto"
    demo_project().save(str(proj))
    s = Session.open_project(str(proj), doll_stl)
    Catalog.create(str(tmp / "linea"), "prueba", 100.0)
    s.open_catalog(str(tmp / "linea"))
    s.catalog_add("muneco")
    return str(proj), str(tmp / "linea")


@pytest.fixture()
def sess(catalogued, doll_stl):
    proj, root = catalogued
    s = Session.open_project(proj, doll_stl)
    s.open_catalog(root)
    return s


def test_preview_without_parts_is_empty_and_fresh(sess):
    assert sess.preview_parts() == {} and sess.part_preview_fresh() is True


def test_preview_places_the_part_and_measures_the_fit(sess):
    ref = sess.catalog_choices("pierna")[0].ref
    sess.set_part("pierna", ref)
    assert sess.part_preview_fresh() is False
    items = sess.preview_parts()
    pv = items["pierna"]
    assert pv.error is None and pv.compat is not None and pv.nivel in ("ok", "aviso")
    assert pv.mesh is not None and len(pv.mesh.faces) > 0
    assert pv.part_shape is not None and pv.host_shape is not None and pv.scale_hint is not None
    assert sess.part_preview_fresh() is True
    row = {r["lugar"]: r for r in sess.part_rows()}["pierna"]
    assert row["previa"] is True and row["compat"] is not None


def test_preview_goes_stale_when_anything_changes(sess):
    sess.set_part("pierna", sess.catalog_choices("pierna")[0].ref)
    sess.preview_parts()
    assert sess.part_preview_fresh()
    sess.clear_part("pierna")
    sess.set_part("brazo", sess.catalog_choices("brazo")[0].ref)
    assert sess.part_preview_fresh() is False           # otra pieza elegida
    sess.preview_parts()
    sess.project.params.clearance += 0.05
    assert sess.part_preview_fresh() is False           # otros parámetros


def test_scaled_model_mismatch_shows_in_the_preview(sess):
    sess.set_part("pierna", sess.catalog_choices("pierna")[0].ref)
    k0 = sess.preview_parts()["pierna"].scale_hint
    assert k0 == pytest.approx(1.0, abs=0.05)           # misma figura: las caras coinciden
    sess.set_ref_height(sess.project.params.ref_height * 1.2)
    pv = sess.preview_parts()["pierna"]
    assert pv.compat.nivel in ("aviso", "no") and pv.scale_hint is not None and pv.scale_hint < 0.95


KNEE = [(5, -5, 14.0), (15, -5, 14.0), (9, 5, 14.0)]


def _with_knee(doll_stl, tmp_path, root):
    """Sesión del muñeco con rodilla propia, con el catálogo abierto."""
    proj = tmp_path / "k.proyecto"
    demo_project().save(str(proj))
    s = Session.open_project(str(proj), doll_stl)
    s.project.params = __import__("dataclasses").replace(s.project.params, decimate=0)
    s.add_joint("pierna", s.plane_from_points(KNEE, "a"), anchor=(9.0, 0.0, 14.0))
    s.open_catalog(root)
    return s


def test_joint_conflict_is_detected_before_generating_and_can_be_resolved(doll_stl, tmp_path):
    Catalog.create(str(tmp_path / "linea"), "prueba", 100.0)
    root = str(tmp_path / "linea")
    s = _with_knee(doll_stl, tmp_path, root)
    s.catalog_add("muneco")                       # la pierna catalogada trae su rodilla
    piece = s.catalog_choices("pierna")[0]
    assert piece.articulaciones, "la pieza catalogada debería traer la rodilla"
    s.set_part("pierna", piece.ref)
    pv = s.preview_parts()["pierna"]
    assert pv.nivel == "no" and pv.conflicts and "rodilla" in pv.conflicts[0].lower() or "knee" in pv.conflicts[0].lower()
    row = {r["lugar"]: r for r in s.part_rows()}["pierna"]
    assert row["conflictos"]

    msg = s.resolve_joint_conflict("pierna", "part")        # me quedo con la de la pieza
    assert "pierna" in msg and s.joint_of_limb("pierna") is None
    assert s.preview_parts()["pierna"].conflicts == []

    s2 = _with_knee(doll_stl, tmp_path, root)
    s2.set_part("pierna", piece.ref)
    s2.resolve_joint_conflict("pierna", "model")            # me quedo con la del modelo
    assert "pierna" not in s2.project.parts and s2.joint_of_limb("pierna") is not None
    with pytest.raises(ValueError):
        s2.resolve_joint_conflict("brazo", "part")


def test_a_failing_place_does_not_stop_the_others(sess):
    sess.set_part("pierna", sess.catalog_choices("pierna")[0].ref)
    sess.set_part("brazo", sess.catalog_choices("brazo")[0].ref)
    sess.project.head = None
    items = sess.preview_parts()
    assert items["pierna"].error is None and items["brazo"].error is None


def test_overlay_and_advice_explain_a_mismatch(sess):
    from afm.preflight import advice, overlay_polygons

    sess.set_part("pierna", sess.catalog_choices("pierna")[0].ref)
    sess.set_ref_height(sess.project.params.ref_height * 1.2)
    pv = sess.preview_parts()["pierna"]
    ov = overlay_polygons(pv)
    assert ov["pieza"] and ov["modelo"] and ov["solo_pieza"] or ov["solo_modelo"]
    lines = advice(pv, sess)
    assert any("escalás" in t for t in lines) and any("coinciden" in t or "radios" in t for t in lines)
