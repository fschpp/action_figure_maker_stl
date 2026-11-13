"""Deshacer / rehacer global (UX etapa 1c)."""
import pytest

from afm.gui.controller import Controller, Mode
from afm.gui.history import History
from afm.testfigure import make_doll

from test_gui_controller import feed


# ------------------------------------------------------------------ History (puro)
def test_history_undo_redo_cycle():
    h = History()
    h.reset("A")
    assert not h.can_undo and h.undo("A") is None
    assert h.observe("B", "pasó a B") and h.observe("C", "pasó a C")
    assert h.undo_label == "pasó a C"
    assert h.undo("C").text == "B" and h.redo_label == "pasó a C"
    assert h.undo("B").text == "A" and not h.can_undo
    assert h.redo("A").text == "B" and h.redo("B").text == "C" and h.redo("C") is None


def test_history_new_change_clears_redo():
    h = History()
    h.reset("A")
    h.observe("B")
    h.undo("B")
    assert h.can_redo
    h.observe("X")
    assert not h.can_redo


def test_history_same_state_is_not_a_step():
    h = History()
    h.reset("A")
    assert not h.observe("A") and not h.can_undo


def test_history_unrecorded_change_is_kept_by_undo():
    h = History()
    h.reset("A")
    assert h.undo("B").text == "A"          # B no se había registrado: se registra y se deshace
    assert h.redo("A").text == "B"


def test_history_limits():
    h = History(limit=3)
    h.reset("0")
    for i in range(1, 8):
        h.observe(str(i))
    assert len(h._undo) == 3
    h2 = History(max_bytes=10)
    h2.reset("x")
    for i in range(5):
        h2.observe("y" * 6 + str(i))
    assert sum(len(s.text) for s in h2._undo) <= 10


# --------------------------------------------------------------- con el controlador
@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    return c


def test_undo_symmetry_and_redo(ctrl):
    assert not ctrl.can_undo and ctrl.undo_blocked() is None
    ctrl.auto_symmetry()
    assert ctrl.session.project.symmetry is not None and ctrl.can_undo
    assert ctrl.undo() and ctrl.session.project.symmetry is None
    assert "Deshecho" in ctrl.message and ctrl.can_redo
    assert ctrl.redo() and ctrl.session.project.symmetry is not None
    assert "Rehecho" in ctrl.message and not ctrl.can_redo


def test_undo_limb_and_param(ctrl):
    ctrl.auto_symmetry()
    ctrl.start_limb_plane("leg")
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    n = len(ctrl.session.project.limbs)
    assert n >= 1
    ctrl.set_param("hip_pin.shaft_len", 5.5)
    assert ctrl.get_param("hip_pin.shaft_len") == 5.5
    ctrl.undo()
    assert ctrl.get_param("hip_pin.shaft_len") != 5.5 and len(ctrl.session.project.limbs) == n
    ctrl.undo()
    assert len(ctrl.session.project.limbs) == n - 1


def test_undo_restores_front_change(ctrl):
    ctrl.auto_symmetry()
    ctrl.set_front("-Y")
    assert ctrl.session.project.symmetry is None and ctrl.session.project.front == "-Y"
    ctrl.undo()
    assert ctrl.session.project.front == "+Y" and ctrl.session.project.symmetry is not None
    assert ctrl.session.work is not None


def test_undo_restores_height_and_scale(ctrl):
    ctrl.auto_symmetry()
    h0 = float(ctrl.session.work.extents[2])
    ctrl.session.set_ref_height(150.0)
    ctrl._changed("Altura 150.")
    assert abs(ctrl.session.work.extents[2] - 150.0) < 1e-3
    ctrl.undo()
    assert abs(ctrl.session.project.params.ref_height - 100.0) < 1e-9
    assert abs(ctrl.session.work.extents[2] - h0) < 1e-3


def test_undo_invalidates_generated_figure_and_cancels_mode(ctrl):
    ctrl.auto_symmetry()
    ctrl.session.outcome = object()
    ctrl.start_symmetry()
    ctrl.undo_blocked()
    ctrl.cancel(quiet=True)
    ctrl.undo()
    assert ctrl.session.outcome is None and ctrl.mode == Mode.VIEW


def test_undo_while_drawing_points_removes_last_point(ctrl):
    ctrl.start_symmetry()
    feed(ctrl, [(0, 0, 10), (10, 0, 10)])
    assert len(ctrl.pending) == 2
    ctrl.undo()
    assert len(ctrl.pending) == 1


def test_undo_blocked_reasons(ctrl):
    assert "STL" in Controller().undo_blocked()
    ctrl.busy = True
    assert "termine" in ctrl.undo_blocked() and not ctrl.can_undo
    assert ctrl.undo() is False


def test_deferred_history_records_on_demand(ctrl):
    ctrl.defer_history = True
    ctrl.auto_symmetry()
    assert not ctrl.history.can_undo          # todavía no se registró
    ctrl.record_history()
    assert ctrl.history.can_undo
    ctrl.undo()
    assert ctrl.session.project.symmetry is None


def test_opening_a_figure_resets_history(ctrl, tmp_path):
    ctrl.auto_symmetry()
    path = str(tmp_path / "otra.stl")
    m = make_doll()
    m.apply_scale(0.05)
    m.export(path)
    ctrl.open_stl(path)
    assert not ctrl.history.can_undo and not ctrl.can_redo
