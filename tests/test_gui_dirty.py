"""«Cambios sin guardar» (UX etapa 7): `Controller.dirty` sigue lo registrado por el historial."""
from afm.gui.controller import Controller
from afm.testfigure import make_doll


def _ctrl(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)
    stl = str(tmp_path / "muneco.stl")
    m.export(stl)
    c = Controller()
    c.open_stl(stl)
    return c


def test_dirty_follows_changes_save_and_undo(tmp_path):
    c = _ctrl(tmp_path)
    assert not c.dirty                       # recién abierto
    c.auto_symmetry()
    assert c.dirty
    c.save_project(str(tmp_path / "x.proyecto"))
    assert not c.dirty                       # guardado
    c.set_param("hip_pin.shaft_len", c.get_param("hip_pin.shaft_len") + 0.5)
    assert c.dirty
    c.undo()
    assert not c.dirty                       # volver al estado guardado limpia la marca


def test_dirty_false_without_a_figure():
    assert not Controller().dirty
