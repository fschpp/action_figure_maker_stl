"""Parámetros, perfiles de línea y probetas desde el controlador (Fases 3–5 de la GUI)."""
import os

import pytest

from afm.gui.controller import PARAM_FIELDS, Controller


def test_fields_all_exist_and_are_in_range():
    c = Controller()
    for key, _label, lo, hi, _step, _dig in PARAM_FIELDS:
        assert lo <= c.get_param(key) <= hi, key


def test_set_param_invalidates_and_validates():
    c = Controller()
    c.session.outcome = object()
    assert c.set_param("hip_pin.shaft_len", 2.0) is True
    assert c.session.project.params.hip_pin.shaft_len == 2.0 and c.session.outcome is None
    assert c.set_param("clearance", 9.0) is False and c.session.project.params.clearance == 0.5
    assert c.set_param("nope", 1.0) is False


def test_profile_roundtrip(tmp_path):
    c = Controller()
    c.set_param("clearance", 0.4)
    c.set_param("hip_pin.shaft_len", 1.5)
    assert c.save_profile("linea guemes", str(tmp_path)) is not None
    assert c.profile_names(str(tmp_path)) == ["linea guemes"]
    d = Controller()
    assert d.load_profile("linea guemes", str(tmp_path)) is True
    assert d.get_param("clearance") == 0.4 and d.get_param("hip_pin.shaft_len") == 1.5
    assert d.load_profile("no existe", str(tmp_path)) is False
    assert c.save_profile("  ", str(tmp_path)) is None


def test_probes_and_result(tmp_path):
    c = Controller()
    out = c.export_probes(str(tmp_path / "p"), which="shoulder", clearances=[0.4, 0.5], shaft_deltas=[0.0, 0.2])
    assert out["celdas"] == 4 and os.path.getsize(out["stl"]) > 1000
    assert "holgura 0.4 mm" in open(out["tabla"]).read()
    c.apply_probe_result(0.4, 3.9, which="shoulder")
    p = c.session.project.params
    assert p.clearance == 0.4 and p.shoulder_pin.shaft_d == 3.9 and p.hip_pin.shaft_d == 5.0
