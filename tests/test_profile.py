from afm import profile
from afm.params import Params


def test_roundtrip(tmp_path):
    p = Params(clearance=0.42)
    profile.save_profile("guemes", p, str(tmp_path))
    assert profile.list_profiles(str(tmp_path)) == ["guemes"]
    assert profile.load_profile("guemes", str(tmp_path)).clearance == 0.42


def test_nozzle_default():
    assert profile.default_for_nozzle(0.8).clearance == 0.70
    assert profile.default_for_nozzle(0.4).nozzle == 0.4


def test_probe_result():
    q = profile.apply_probe_result(Params(), 0.4, 4.3, "shoulder")
    assert q.clearance == 0.4 and q.shoulder_pin.shaft_d == 4.3 and q.hip_pin.shaft_d == 5.0


# --- resultado de las probetas (la celda elegida pasa a los parámetros) -------------------------------------
import json

import pytest


def _manifest(tipo, celdas, **kw):
    return {"formato": "afm-probetas-1", "tipo": tipo, **kw, "celdas": celdas}


def test_apply_probe_cell_hongo_picks_the_pin_of_the_batch():
    m = _manifest("hongo", [{"n": 1, "holgura": 0.3, "vastago": 4.8}, {"n": 2, "holgura": 0.4, "vastago": 5.2}], pasador="cadera")
    q, resumen = profile.apply_probe_cell(Params(), m, 2)
    assert q.clearance == 0.4 and q.hip_pin.shaft_d == 5.2 and q.shoulder_pin.shaft_d == Params().shoulder_pin.shaft_d
    assert "5.2" in resumen and "cadera" in resumen
    q, _ = profile.apply_probe_cell(Params(), _manifest("hongo", m["celdas"], pasador="hombro"), 1)
    assert q.shoulder_pin.shaft_d == 4.8 and q.hip_pin.shaft_d == Params().hip_pin.shaft_d


def test_apply_probe_cell_hinge_and_clic():
    m = _manifest("bisagra", [{"n": 4, "holgura": 0.45, "holgura_axial": 0.25, "bulon": 2.2}])
    q, _ = profile.apply_probe_cell(Params(), m, 4)
    assert (q.clearance, q.axial_clearance, q.hinge_pin.pin_d) == (0.45, 0.25, 2.2)
    import dataclasses
    spec = dataclasses.asdict(dataclasses.replace(Params().detent, notch_depth=0.6, finger_t=1.4))
    c = _manifest("clic", [{"n": 1, "archivo": "a.stl", "detent": spec}, {"n": 1, "archivo": "b.stl", "detent": spec}])
    with pytest.raises(ValueError, match="indicá el archivo"):
        profile.apply_probe_cell(Params(), c, 1)
    q, _ = profile.apply_probe_cell(Params(), c, 1, "b.stl")
    assert q.detent.notch_depth == 0.6 and q.detent.finger_t == 1.4


def test_apply_probe_cell_errors():
    m = _manifest("hongo", [{"n": 1, "holgura": 0.3, "vastago": 4.8}])
    with pytest.raises(ValueError, match="no tiene la celda 9"):
        profile.apply_probe_cell(Params(), m, 9)
    with pytest.raises(ValueError, match="montura"):
        profile.apply_probe_cell(Params(), _manifest("montura", [{"n": 1, "holgura": 0.25}]), 1)


def test_load_probe_manifest_accepts_folder_and_rejects_other_json(tmp_path):
    (tmp_path / "probetas.json").write_text(json.dumps(_manifest("hongo", [])), encoding="utf-8")
    assert profile.load_probe_manifest(str(tmp_path))["tipo"] == "hongo"
    (tmp_path / "otro.json").write_text('{"x": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="no es un probetas.json"):
        profile.load_probe_manifest(str(tmp_path / "otro.json"))


def test_cli_probe_resultado_writes_the_profile(tmp_path, capsys):
    from afm.cli import main
    man = tmp_path / "probetas.json"
    man.write_text(json.dumps(_manifest("hongo", [{"n": 3, "holgura": 0.35, "vastago": 4.4}], pasador="hombro")), encoding="utf-8")
    assert main(["probe-resultado", str(man), "3", "--perfil", "mi linea", "--directorio", str(tmp_path / "perf")]) == 0
    q = profile.load_profile("mi linea", str(tmp_path / "perf"))
    assert q.clearance == 0.35 and q.shoulder_pin.shaft_d == 4.4
    assert main(["probe-resultado", str(man), "7", "--perfil", "mi linea", "--directorio", str(tmp_path / "perf")]) == 2
    assert "no tiene la celda 7" in capsys.readouterr().err
