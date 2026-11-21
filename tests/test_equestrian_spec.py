"""E01: HorseSpec / HorseMeta (plan de caballos compatibles)."""
import json
import pathlib
import re

import pytest

from afm.equestrian import HORSE_FORMAT, HorseError, HorseMeta, HorseSpec

SHA = "a" * 64


def spec(**kw):
    return HorseSpec(**{"archivo": "modelo.stl", "sha256": SHA, "escala": 3.0, "yaw": 180.0, **kw})


def test_spec_round_trip():
    s = spec(ref_tipo="alto_dorso", ref_mm=97.0)
    assert HorseSpec.from_dict(json.loads(json.dumps(s.to_dict()))) == s
    assert HorseSpec.from_dict(spec().to_dict()) == spec()


def test_spec_defaults_and_no_reference_key():
    s = HorseSpec.from_dict({"archivo": "m.stl", "sha256": SHA})
    assert (s.escala, s.yaw) == (1.0, 0.0) and "referencia" not in s.to_dict()


def test_different_hash_is_different_spec():
    assert spec() != spec(sha256="b" * 64)


@pytest.mark.parametrize("kw", [{"escala": 0}, {"escala": -1}, {"escala": float("nan")}, {"escala": True}, {"escala": "3"},
                                {"yaw": float("inf")}, {"sha256": "xyz"}, {"sha256": "A" * 64}, {"archivo": " "},
                                {"ref_tipo": "alto_dorso"}, {"ref_mm": 5.0}, {"ref_tipo": "otro", "ref_mm": 5.0},
                                {"ref_tipo": "largo", "ref_mm": 0}])
def test_spec_invalid(kw):
    with pytest.raises(HorseError):
        spec(**kw)


def test_spec_from_dict_errors():
    with pytest.raises(HorseError):
        HorseSpec.from_dict([])
    with pytest.raises(HorseError, match="sha256"):
        HorseSpec.from_dict({"archivo": "m.stl"})
    with pytest.raises(HorseError, match="referencia"):
        HorseSpec.from_dict({"archivo": "m.stl", "sha256": SHA, "referencia": {"tipo": "largo"}})


def test_meta_round_trip_and_ref():
    m = HorseMeta(id="caballo-pardo-01", version=2, spec=spec(), nombre="Pardo")
    d = m.to_dict()
    assert d["formato"] == HORSE_FORMAT and m.ref == "caballo-pardo-01@2"
    assert HorseMeta.from_dict(json.loads(json.dumps(d))) == m
    assert "nombre" not in HorseMeta(id="c-1", version=1, spec=spec()).to_dict()


@pytest.mark.parametrize("kw", [{"id": "Caballo"}, {"id": "a b"}, {"version": 0}, {"version": True}, {"nombre": "x" * 61},
                                {"nombre": "a\nb"}])
def test_meta_invalid(kw):
    with pytest.raises(HorseError):
        HorseMeta(**{"id": "c-1", "version": 1, "spec": spec(), **kw})


def test_meta_wrong_format():
    d = HorseMeta(id="c-1", version=1, spec=spec()).to_dict()
    with pytest.raises(HorseError, match="formato"):
        HorseMeta.from_dict({**d, "formato": "otro"})
    d.pop("id")
    with pytest.raises(HorseError, match="id"):
        HorseMeta.from_dict({**d, "formato": HORSE_FORMAT})


def test_module_does_not_import_manifold3d():
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "afm" / "equestrian.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import manifold3d|from manifold3d)", src, re.M)
