"""Agarre para accesorios desde el controlador (sin ventana): valores, tarea en dos pasos, guardado y avisos."""
import json
import os

import numpy as np
import pytest
import trimesh

from afm import props as P
from afm import solids as S
from afm.gui.controller import GRIP_FIELDS, Controller


@pytest.fixture()
def hand_stl(tmp_path, monkeypatch):
    """Mano de juguete (toro con agujero Ø2,4) y búsqueda de canal gruesa: el toro es mucho más grande que una mano."""
    t = trimesh.creation.torus(major_radius=4.4, minor_radius=3.2, major_sections=48, minor_sections=24)
    path = str(tmp_path / "mano_toro.stl")
    t.export(path)
    real = P.find_channel
    monkeypatch.setattr(P, "find_channel", lambda hand, bore_d=2.4, **kw: real(hand, bore_d, pitch=0.2, n_dirs=150, refine=False))
    return path


def _short_fingers(c):
    """El toro de prueba tiene un canal de ≈ 6 mm (una mano real, 10): lengüetas y dentado acordes."""
    assert c.set_grip_param("finger_len", 5.5) and c.set_grip_barbs("2.7, 2.8")


def test_fields_exist_and_defaults_are_in_range():
    c = Controller()
    for key, _label, lo, hi, _step, _dig in GRIP_FIELDS:
        assert lo <= getattr(c.grip_spec, key) <= hi, key


def test_set_grip_param_validates():
    c = Controller()
    assert c.set_grip_param("bore_d", 2.2) is True and c.grip_spec.bore_d == 2.2
    assert c.set_grip_param("bore_d", 9.0) is False and c.grip_spec.bore_d == 2.2
    assert c.set_grip_param("nope", 1.0) is False


def test_set_grip_barbs():
    c = Controller()
    assert c.set_grip_barbs("2.6, 2.8 ; 3.0") is True and c.grip_barbs == (2.6, 2.8, 3.0)
    assert c.set_grip_barbs("2.6, hola") is False and c.grip_barbs == (2.6, 2.8, 3.0)
    assert c.set_grip_barbs("") is False
    assert c.set_grip_barbs("1,2,3,4,5,6,7") is False


def test_two_step_task_and_busy_flag(hand_stl):
    c = Controller()
    job = c.begin_grip(hand_stl)
    assert job is not None and c.busy
    assert c.begin_grip(hand_stl) is None                       # no se lanzan dos tareas a la vez
    c.finish_grip(job(), None)
    assert not c.busy and c.grip is not None
    s = c.grip_summary()
    assert "Taladro Ø2.4" in s and "Dentado Ø2.9" in s and not c.grip_stale


def test_error_frees_the_controller(tmp_path):
    c = Controller()
    bad = tmp_path / "vacio.stl"
    bad.write_text("no es una malla")
    assert c.run_grip(str(bad)) is None
    assert not c.busy and c.grip is None and "No se pudo taladrar" in c.message


def test_save_hand_and_plate(hand_stl, tmp_path):
    c = Controller()
    assert c.save_grip_hand(str(tmp_path / "x.stl")) is False   # todavía no hay mano taladrada
    assert c.run_grip(hand_stl) is not None
    _short_fingers(c)
    out_hand, out_plate = str(tmp_path / "mano_agarre.stl"), str(tmp_path / "placa.stl")
    assert c.save_grip_hand(out_hand) and c.save_grip_plate(out_plate)
    meta = json.load(open(os.path.splitext(out_hand)[0] + ".mano.json"))
    assert meta["formato"] == "afm-mano-1" and meta["agarre"]["diametro_taladro_mm"] == 2.4
    assert len(trimesh.load(out_plate).split(only_watertight=False)) == len(c.grip_barbs)
    from afm.hand import load_standard_hand                      # la mano con agarre sigue siendo una mano estándar válida
    hand, m = load_standard_hand(out_hand)
    assert hand.volume() > 0 and m["agarre"]["eje"]


def test_stale_bore_blocks_saving_the_hand_but_not_the_plate(hand_stl, tmp_path):
    c = Controller()
    c.run_grip(hand_stl)
    _short_fingers(c)
    c.set_grip_param("bore_d", 2.0)
    assert c.grip_stale and "volvé a taladrar" in c.grip_summary()
    assert c.save_grip_hand(str(tmp_path / "x.stl")) is False
    assert c.save_grip_plate(str(tmp_path / "p.stl")) is True    # la placa usa el taladro ya hecho (2,4)


def test_plate_reports_bad_spec(hand_stl, tmp_path):
    c = Controller()
    c.run_grip(hand_stl)
    _short_fingers(c)
    c.set_grip_barbs("2.3")                                      # no supera el taladro: no retiene
    assert c.save_grip_plate(str(tmp_path / "p.stl")) is False and "no retiene" in c.message
