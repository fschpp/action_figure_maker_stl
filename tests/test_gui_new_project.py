"""«Nuevo»: descarta la figura y el proyecto y vuelve al estado de inicio, conservando lo que engancha la ventana."""
import pytest

from afm.gui.controller import Controller, Mode
from afm.testfigure import make_doll


@pytest.fixture()
def ctrl(tmp_path):
    stl = str(tmp_path / "muneco.stl")
    make_doll().export(stl)
    c = Controller()
    c.open_stl(stl)
    c.auto_symmetry()
    return c


def test_new_project_clears_the_loaded_figure_and_state(ctrl, tmp_path):
    ctrl.save_project(str(tmp_path / "a.proyecto"))
    ctrl.start_symmetry()
    assert ctrl.loaded and ctrl.mode != Mode.VIEW
    assert ctrl.new_project() is True
    assert not ctrl.loaded and ctrl.session.project_path is None and ctrl.session.stl_path is None
    assert ctrl.mode == Mode.VIEW and ctrl.pending == [] and ctrl.picker is None
    assert ctrl.session.project.symmetry is None and not ctrl.dirty
    assert ctrl.message == "Abrí un STL para empezar."
    assert not ctrl.history.can_undo


def test_new_project_keeps_window_hooks(ctrl):
    calls = []
    ctrl.on_change = lambda: calls.append(1)
    ctrl.call_in_main = lambda f, *a: f(*a)
    ctrl.defer_history = True
    ctrl.new_project()
    assert calls and ctrl.call_in_main is not None and ctrl.defer_history is True and ctrl.scene_dirty


def test_new_project_is_refused_while_busy(ctrl):
    ctrl.busy = True
    assert ctrl.new_project() is False and ctrl.loaded


def test_can_open_again_after_new(ctrl, tmp_path):
    ctrl.new_project()
    ctrl.open_stl(str(tmp_path / "muneco.stl"))
    assert ctrl.loaded
