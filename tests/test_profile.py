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
