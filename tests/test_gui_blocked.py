"""Motivos de botón apagado (UX etapa 1b): `Controller.blocked` y su uso en la ventana."""
import re
from pathlib import Path

import pytest

from afm.gui.controller import Controller, Mode
from afm.testfigure import make_doll

from test_gui_controller import feed

KEYS = ("open", "save", "generate", "stl", "sym", "limbs", "head", "head_pin_reset", "head_replacement",
        "head_replacement_cut", "waist", "waist_pin_reset", "hand_extract", "hand_placed", "outcome", "grip",
        "grip_plate", "grip_hand", "drawing", "raise", "pin", "pin_any", "weapon")


@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    return c


def test_every_key_answers_without_a_figure():
    c = Controller()
    for k in KEYS:
        why = c.blocked(k)
        assert why is None or (isinstance(why, str) and why.strip()), k


def test_unknown_key_is_an_error(ctrl):
    with pytest.raises(KeyError):
        ctrl.blocked("no_existe")


def test_window_only_uses_known_keys():
    """Si la ventana pide una clave que el controlador no conoce, falla acá y no al abrir la ventana."""
    src = (Path(__file__).parent.parent / "src" / "afm" / "gui" / "window.py").read_text(encoding="utf-8")
    used = set(re.findall(r'ctrl\.blocked\("([a-z_]+)"\)', src))
    assert used, "la ventana debería usar Controller.blocked"
    assert used <= set(KEYS), used - set(KEYS)


def test_needs_a_figure_first():
    c = Controller()
    for k in ("save", "generate", "stl", "sym", "limbs", "head", "waist", "hand_extract", "hand_placed"):
        assert "STL" in c.blocked(k), k
    assert c.blocked("open") is None and c.blocked("outcome") is not None


def test_symmetry_then_limbs_then_figure(ctrl):
    assert ctrl.blocked("stl") is None and ctrl.blocked("generate") is None
    assert "simetría" in ctrl.blocked("sym") and "simetría" in ctrl.blocked("limbs")
    ctrl.auto_symmetry()
    assert ctrl.blocked("sym") is None
    assert "brazo o una pierna" in ctrl.blocked("limbs")
    ctrl.start_limb_plane("leg")
    feed(ctrl, [(0, 0, 10), (10, 0, 10), (0, 0, 50)])
    assert ctrl.blocked("limbs") is None
    assert "Generá la figura" in ctrl.blocked("outcome")


def test_busy_blocks_with_wait_reason(ctrl):
    ctrl.busy = True
    for k in ("open", "save", "generate", "grip"):
        assert "termine la tarea" in ctrl.blocked(k), k


def test_mode_dependent_buttons_explain_how_to_enable(ctrl):
    assert "silueta" in ctrl.blocked("drawing") and "pasador" in ctrl.blocked("pin")
    assert ctrl.blocked("pin_any") is not None
    ctrl.mode = Mode.PIN
    assert ctrl.blocked("pin") is None and ctrl.blocked("pin_any") is None
    ctrl.mode = Mode.HEAD_PIN
    assert ctrl.blocked("pin") is not None and ctrl.blocked("pin_any") is None
    ctrl.mode = Mode.POLYGON
    assert ctrl.blocked("drawing") is None


def test_stale_outcome_flag_and_export_reason(ctrl):
    assert not ctrl.outcome_stale and "Generá la figura primero" in ctrl.blocked("outcome")
    ctrl.generated_once, ctrl.session.outcome = True, object()          # como tras una generación lograda
    assert not ctrl.outcome_stale and ctrl.blocked("outcome") is None
    ctrl.set_param("hip_pin.shaft_len", 5.5)                            # invalida la figura
    assert ctrl.outcome_stale
    assert "desactualizada" in ctrl.blocked("outcome")
    ctrl.busy = True
    assert not ctrl.outcome_stale                                       # mientras genera no se muestra el aviso
    ctrl.busy = False


def test_export_only_param_keeps_outcome_valid(ctrl):
    from afm.gui.controller import EXPORT_ONLY_PARAMS

    if not EXPORT_ONLY_PARAMS:
        pytest.skip("no hay parámetros solo de exportación")
    key = sorted(EXPORT_ONLY_PARAMS)[0]
    ctrl.generated_once, ctrl.session.outcome = True, object()
    lo_hi = next(f for f in __import__("afm.gui.controller", fromlist=["PARAM_FIELDS"]).PARAM_FIELDS if f[0] == key)
    new = lo_hi[2] if abs(ctrl.get_param(key) - lo_hi[2]) > 1e-9 else lo_hi[3]
    ctrl.set_param(key, new)
    assert not ctrl.outcome_stale


def test_opening_a_figure_clears_stale(ctrl, tmp_path):
    ctrl.generated_once = True
    path = str(tmp_path / "otra.stl")
    m = make_doll()
    m.apply_scale(0.05)
    m.export(path)
    ctrl.open_stl(path)
    assert not ctrl.generated_once and not ctrl.outcome_stale
