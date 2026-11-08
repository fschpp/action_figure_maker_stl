"""GUI (sin GTK): «Ajustar cintura y muñeca al patrón» (Session / Controller)."""
import os
import shutil

import numpy as np
import pytest

from afm import ajuste as A
from afm.gui.controller import Controller
from afm.hand import HandSpec
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll
from afm.waist import WaistSpec, waist_plane

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture()
def ctrl(tmp_path):
    stl = str(tmp_path / "muneco.stl")
    make_doll().export(stl)
    pr = demo_project()
    pr.waist = WaistSpec(Plane.from_points((-10, -6, 48), (10, -6, 48), (0, 8, 48), "cintura"))
    pr.hand = HandSpec(path="", points=((21.0, 3.0, 68.0), (18.0, -3.0, 68.0), (24.0, -3.0, 68.0)))
    proj = tmp_path / "muneco.proyecto"
    pr.save(str(proj))
    c = Controller()
    c.open_project(str(proj), stl)
    assert c.loaded
    return c


@pytest.fixture()
def cat_root(tmp_path):
    root = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, root)
    return root


def test_needs_an_open_catalog(ctrl):
    assert ctrl.waist_wrist_options() == {"pelvis": [], "mano": []}
    assert ctrl.waist_wrist_fit_lines() is None and "catálogo" in ctrl.message
    assert ctrl.apply_waist_wrist_fit() is False


def test_options_and_lines_with_catalog(ctrl, cat_root):
    ctrl.session.open_catalog(cat_root)
    opts = ctrl.waist_wrist_options()
    assert opts["pelvis"] == [] and len(opts["mano"]) == 1
    lines = ctrl.waist_wrist_fit_lines(None, opts["mano"][0])
    text = "\n".join(lines)
    assert "cintura: no se pudo ajustar" in text and "pelvis" in text            # el ejemplo no trae pelvis
    assert "muñeca: radio" in text


def test_apply_moves_the_planes_and_invalidates(ctrl, cat_root, monkeypatch):
    ctrl.session.open_catalog(cat_root)
    z0 = float(waist_plane(ctrl.session.project.waist).origin[2])
    fake = [A.AjusteResult("cintura", r_ref_mm=9.0, r_antes_mm=12.0, r_despues_mm=9.0, desplazamiento_mm=-6.0, escala=1.0, ok=True)]
    monkeypatch.setattr(A, "ajustar", lambda *a, **k: fake)
    ctrl.session.outcome = object()
    assert ctrl.apply_waist_wrist_fit() is True
    assert float(waist_plane(ctrl.session.project.waist).origin[2]) == pytest.approx(z0 - 6.0, abs=1e-6)
    assert ctrl.session.outcome is None and "Ajustado al patrón" in ctrl.message


def test_apply_without_changes_says_so(ctrl, cat_root):
    ctrl.session.open_catalog(cat_root)
    assert ctrl.apply_waist_wrist_fit(None, None) is False
    assert "necesitan moverse" in ctrl.message


def test_apply_can_scale_the_pelvis(ctrl, cat_root, monkeypatch):
    ctrl.session.open_catalog(cat_root)
    fake = [A.AjusteResult("cintura", r_ref_mm=9.0, r_antes_mm=9.9, r_despues_mm=9.9, escala=9.0 / 9.9, ok=True)]
    monkeypatch.setattr(A, "ajustar", lambda *a, **k: fake)
    assert ctrl.apply_waist_wrist_fit(None, None, escalar_pelvis=False) is False
    assert ctrl.apply_waist_wrist_fit(None, None, escalar_pelvis=True) is True
    assert ctrl.session.project.part_scales["pelvis"] == pytest.approx(9.9 / 9.0)


def test_cataloging_the_arm_uses_the_wrist_marked_on_the_figure(ctrl, cat_root, tmp_path):
    """Con los 3 puntos de la muñeca en el proyecto, el brazo catalogado lleva la toma `muneca` (y sin ellos, no)."""
    from afm import hand as H, solids as S
    from afm.catalog import Catalog
    pts = ctrl.session.project.hand.points
    hand_local, split = H.extract_standard_hand(S.to_manifold(make_doll()).trim_by_plane((1.0, 0.0, 0.0), 0.0), pts)
    std = str(tmp_path / "mano_std.stl")
    H.save_standard_hand(std, hand_local, H.hand_meta(split, 100.0, "test"))
    ctrl.session.project.hand = H.HandSpec(path=std, points=pts)
    ctrl.session.open_catalog(cat_root)
    assert ctrl.catalog_add_current("conmuneca", places=["brazo"]), ctrl.message
    cat = Catalog.open(cat_root)
    arm = next(m for m in cat.list("brazo") if "conmuneca" in m.id)
    assert "muneca" in arm.puertos and arm.puertos["muneca"].rol == "toma"
    ctrl.session.project.hand = None
    assert ctrl.catalog_add_current("sinmuneca", places=["brazo"]), ctrl.message
    arm2 = next(m for m in Catalog.open(cat_root).list("brazo") if "sinmuneca" in m.id)
    assert "muneca" not in arm2.puertos
