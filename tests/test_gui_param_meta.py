"""Metadatos de parámetros, valores por defecto y «restaurar» (UX etapa 3)."""
import pytest

from afm.gui import param_meta as PM
from afm.gui.controller import PARAM_FIELDS, Controller
from afm.testfigure import make_doll


def test_every_field_has_a_known_group_and_a_nonempty_short_label():
    for key, label, *_ in PARAM_FIELDS:
        short, hint = PM.split_label(label)
        assert short.strip(), key
        assert PM.group_of(key) in PM.PARAM_GROUPS + (PM.GENERATE_GROUP,), key


def test_split_label_keeps_unit_and_moves_explanation_to_hint():
    assert PM.split_label("Cadera: Ø vástago (mm)") == ("Cadera: Ø vástago (mm)", "")
    short, hint = PM.split_label("Cintura: largo vástago (mm; pared tras la cabeza = largo − 2·holgura)")
    assert short == "Cintura: largo vástago (mm)" and hint.startswith("pared tras la cabeza")
    assert PM.split_label("Sin paréntesis") == ("Sin paréntesis", "")


def test_boolean_labels_become_on_off_hints():
    short, hint = PM.split_label("Hombro: mecanismo de clic (1 = sí, 0 = solo hongo)")
    assert short == "Hombro: mecanismo de clic" and hint == "Encendido: sí. Apagado: solo hongo."
    for key in PM.BOOLEAN_KEYS:                       # todos existen y son de 0 a 1
        spec = next(f for f in PARAM_FIELDS if f[0] == key)
        assert (spec[2], spec[3]) == (0.0, 1.0), key


def test_groups_hand_and_flat_are_relocated():
    assert PM.group_of("hand_pin.head_d") == "Mano articulada"
    assert PM.group_of("flat_depth") == PM.GENERATE_GROUP
    assert PM.group_of("clearance") == "Generales" and PM.group_of("waist_twist") == "Cintura"


def test_search_ignores_case_and_accents():
    assert PM.matches("holgura", "clearance", "Holgura miembro–cavidad (mm)")
    assert PM.matches("VASTAGO cadera", "hip_pin.shaft_d", "Cadera: Ø vástago (mm)")
    assert not PM.matches("vastago hombro", "hip_pin.shaft_d", "Cadera: Ø vástago (mm)")
    assert PM.matches("", "x", "y") and PM.matches("clic", "detent.n_notches", "Clic: cantidad de muescas")


@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    return c


def test_defaults_match_a_fresh_project_and_none_modified(ctrl):
    assert ctrl.modified_params() == []
    for key, *_ in PARAM_FIELDS:
        assert abs(ctrl.get_param(key) - ctrl.param_default(key)) < 1e-12, key


def test_modify_and_reset_one(ctrl):
    ctrl.set_param("clearance", 0.8)
    assert ctrl.param_modified("clearance") and ctrl.modified_params() == ["clearance"]
    ctrl.reset_param("clearance")
    assert not ctrl.param_modified("clearance")


def test_reset_all_is_a_single_undo_step_and_undoable(ctrl):
    ctrl.set_param("clearance", 0.8)
    ctrl.set_param("hip_pin.shaft_len", 5.5)
    ctrl.set_param("shoulder_detent", 0.0) if ctrl.get_param("shoulder_detent") else ctrl.set_param("shoulder_detent", 1.0)
    n = len(ctrl.modified_params())
    assert n == 3
    assert ctrl.reset_params() == n and ctrl.modified_params() == []
    ctrl.undo()
    assert len(ctrl.modified_params()) == n
    assert ctrl.reset_params() == n


def test_reset_with_nothing_modified_says_so(ctrl):
    assert ctrl.reset_params() == 0 and "ya tienen" in ctrl.message
