"""«Guardar» sobrescribe el .proyecto abierto (o el último guardado); sin archivo previo, la ventana pide dónde."""
import json
import os

import pytest

from afm.gui.controller import Controller
from afm.testfigure import make_doll


@pytest.fixture()
def ctrl(tmp_path):
    stl = str(tmp_path / "muneco.stl")
    make_doll().export(stl)
    c = Controller()
    c.open_stl(stl)
    c.auto_symmetry()
    return c


def test_new_figure_has_no_project_path(ctrl):
    assert ctrl.session.project_path is None


def test_saving_remembers_the_path_and_opening_sets_it(ctrl, tmp_path):
    p = str(tmp_path / "a.proyecto")
    ctrl.save_project(p)
    assert ctrl.session.project_path == os.path.abspath(p)
    c2 = Controller()
    c2.open_project(p)
    assert c2.session.project_path == os.path.abspath(p)


def test_resaving_the_opened_project_overwrites_the_same_file(ctrl, tmp_path):
    p = str(tmp_path / "a.proyecto")
    ctrl.save_project(p)
    c2 = Controller()
    c2.open_project(p)
    c2.session.project.params = type(c2.session.project.params)(**{**c2.session.project.params.__dict__, "ref_height": 123.0})
    c2.save_project(c2.session.project_path)
    assert json.load(open(p))["params"]["ref_height"] == 123.0
    assert sorted(os.listdir(tmp_path)) == ["a.proyecto", "muneco.stl"]      # sin copias nuevas


def test_opening_another_stl_forgets_the_project_path(ctrl, tmp_path):
    p = str(tmp_path / "a.proyecto")
    ctrl.save_project(p)
    ctrl.open_stl(str(tmp_path / "muneco.stl"))
    assert ctrl.session.project_path is None
