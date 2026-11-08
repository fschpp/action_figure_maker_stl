"""GUI (sin GTK): «Nuevo héroe desde el patrón» (Controller) y `patron.crear_desde_patron`."""
import json
import os

import pytest

from afm.gui.controller import Controller
from afm.project import demo_project
from afm.testfigure import make_doll


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("gpatron")
    ref_proj, hero_stl = str(d / "ref.proyecto"), str(d / "heroe.stl")
    hero = make_doll()
    hero.apply_scale([1.1, 0.95, 1.05])
    hero.export(hero_stl)
    proj = demo_project()
    proj.trims = []
    with open(ref_proj, "w") as f:
        json.dump(proj.to_dict(), f)
    return ref_proj, hero_stl, str(d)


def test_job_validates_paths_before_running(files):
    ref_proj, hero_stl, d = files
    c = Controller()
    with pytest.raises(ValueError, match="patrón"):
        c.new_from_pattern_job(os.path.join(d, "no_existe.proyecto"), hero_stl, os.path.join(d, "x.proyecto"))
    with pytest.raises(ValueError, match="STL"):
        c.new_from_pattern_job(ref_proj, os.path.join(d, "no_existe.stl"), os.path.join(d, "x.proyecto"))
    with pytest.raises(ValueError, match="guardar"):
        c.new_from_pattern_job(ref_proj, hero_stl, "")


def test_job_creates_project_and_image_and_finish_opens_it(files):
    ref_proj, hero_stl, d = files
    out = os.path.join(d, "heroe_nuevo.proyecto")
    c = Controller()
    job = c.new_from_pattern_job(ref_proj, hero_stl, out)
    c.busy = True
    res = job(lambda *_: None)
    assert res[0] == out and os.path.isfile(out) and os.path.isfile(res[1]) and res[1].endswith("_revision.png")
    image = c.finish_new_from_pattern(res, None, hero_stl)
    assert image == res[1] and c.busy is False and c.loaded
    assert "Héroe creado desde el patrón" in c.message


def test_finish_reports_a_failure_without_opening(files):
    c = Controller()
    c.busy = True
    assert c.finish_new_from_pattern(None, ValueError("boom"), "x.stl") is None
    assert c.busy is False and "boom" in c.message and not c.loaded
