"""`FaceRequirements` / `class_pin` reemplazan a `PIN_OF_CLASS`, `capacity.MIRROR_CLASSES` y la lógica de tipo de `pin_shift`
(plan de plugins §3.5–§3.7, Fase 1, tarea 2). Sin perfil de línea todo da lo mismo que antes."""
import dataclasses

import pytest

from afm import capacity as C
from afm import interface as I
from afm import ports as PO
from afm.joint_types import resolve as R
from afm.params import Params

P = Params()


def test_the_old_class_table_is_gone_and_its_stored_names_are_kept():
    assert not hasattr(PO, "PIN_OF_CLASS") and not hasattr(C, "MIRROR_CLASSES")
    assert R.PIN_ATTR_OF_CLASS == {"cuello": "neck_pin", "hombro": "shoulder_pin", "cadera": "hip_pin", "cintura": "waist_pin"}
    assert I.ClassContract("hombro").to_dict()["pasador"] == "shoulder_pin"          # el dato guardado en los contratos no cambia
    assert "pasador" not in I.ClassContract("muneca").to_dict()


@pytest.mark.parametrize("clase,attr", sorted(R.PIN_ATTR_OF_CLASS.items()))
def test_without_a_profile_the_class_pin_is_the_one_of_params(clase, attr):
    assert R.class_pin(clase, P) is getattr(P, attr) and C.pin_of(clase, P) is getattr(P, attr) and PO._pin_for(clase, P) is getattr(P, attr)


@pytest.mark.parametrize("clase", ["muneca", "rodilla", "codo", "torso"])
def test_classes_without_a_pin_have_no_type_pin_or_demand(clase):
    assert R.class_type_id(clase, P) is None and R.class_pin(clase, P) is None
    assert R.class_requirements(clase, P) is None and C.demand(clase, P) is None and C.pin_shift(clase, P) == 0.0


def test_class_type_follows_the_profile_then_the_detent_flags():
    assert [R.class_type_id(c, P) for c in ("hombro", "cadera", "cuello", "cintura")] == ["mushroom"] * 4
    flags = dataclasses.replace(P, shoulder_detent=True, hip_detent=True)
    assert R.class_type_id("hombro", flags) == "detent" and R.class_type_id("cadera", flags) == "detent"
    assert R.class_type_id("cuello", flags) == "mushroom"                              # cuello y cintura no se intercambian
    prof = dataclasses.replace(flags, joints={"hombro": {"type": "mushroom"}})
    assert R.class_type_id("hombro", prof) == "mushroom" and R.class_type_id("cadera", prof) == "detent"   # el perfil pisa a las banderas
    with pytest.raises(ValueError, match="desconocido 'rotula'"):
        R.class_type_id("hombro", dataclasses.replace(P, joints={"hombro": {"type": "rotula"}}))


def test_demand_is_the_type_requirements_and_is_unchanged_without_a_profile():
    pin = P.shoulder_pin
    assert C.demand("hombro", P) == {"profundidad_mm": pin.shaft_len + pin.head_h + P.clearance, "pared_min_mm": P.min_wall,
                                     "huella_radio_mm": pin.head_d / 2}
    assert C.demand("cadera", P)["separacion_min_mm"] == pytest.approx(2 * P.clearance)
    assert "separacion_min_mm" not in C.demand("cuello", P)
    assert C.demand("hombro", dataclasses.replace(P, shoulder_detent=True)) == C.demand("hombro", P)    # el disco no cambia lo que se pide


def test_a_mushroom_profile_changes_what_capacity_and_ports_measure():
    """Cierra el límite de la tarea 1: el perfil de línea llega a capacidad, compatibilidad de puertos y contratos."""
    prof = dataclasses.replace(P, joints={"hombro": {"type": "mushroom", "params": {"shaft_len": P.shoulder_pin.shaft_len + 2.0,
                                                                                   "shaft_d": 2.6}}})
    assert C.demand("hombro", prof)["profundidad_mm"] == pytest.approx(C.demand("hombro", P)["profundidad_mm"] + 2.0)
    assert C.pin_of("hombro", prof).shaft_d == 2.6 and PO._pin_for("hombro", prof).shaft_d == 2.6
    assert C.pin_of("cadera", prof) is P.hip_pin                                       # solo el lugar del perfil
    assert I.floor_radius("hombro", prof) == pytest.approx(2.6 / 2 + prof.min_wall)
    assert I.floor_radius("hombro", P) == pytest.approx(P.shoulder_pin.shaft_d / 2 + P.min_wall)
    assert I.floor_radius("muneca", prof) is None and I.floor_radius("hombro", None) is None


def test_pin_shift_belongs_to_the_type_of_the_class():
    assert C.pin_shift("cadera", P) == 0.0
    flag = dataclasses.replace(P, hip_detent=True)
    assert C.pin_shift("cadera", flag) == pytest.approx(P.detent.disc_h)
    assert C.pin_shift("hombro", dataclasses.replace(P, shoulder_detent=True)) == 0.0    # solo la clase enfrentada a su espejo
    prof = dataclasses.replace(P, joints={"cadera": {"type": "detent", "params": {"disc_h": 3.1}}})
    assert C.pin_shift("cadera", prof) == pytest.approx(3.1)
    assert C.pin_shift("cadera", dataclasses.replace(flag, joints={"cadera": {"type": "mushroom"}})) == 0.0

