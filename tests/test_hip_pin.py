"""Cadera: preset de la figura de referencia y chequeos de pared / cabezas enfrentadas (plan de articulaciones, Fase 3)."""
import dataclasses

import numpy as np
import pytest

from afm import pipeline as P, solids as S
from afm.cli import main as cli_main
from afm.params import HIP_PIN_REFERENCE, HIP_WALL_REFERENCE, Params
from afm.pipeline import MIN_HEAD_SHAFT_RATIO, hip_pin_walls
from afm.project import demo_project
from afm.testfigure import make_doll


def test_reference_values_match_measurements():
    r = HIP_PIN_REFERENCE
    assert (r.shaft_d, r.head_d, r.head_h) == (4.8, 9.25, 2.44)
    assert r.head_d / r.shaft_d >= MIN_HEAD_SHAFT_RATIO and r.head_d / r.shaft_d == pytest.approx(1.93, abs=0.01)
    # pared de la figura de referencia: vástago libre 2,3 − 2 · 0,5 de holgura = 1,3 mm
    w = hip_pin_walls(r, (13.0, 0, 38.0), (1, 0, 0), 0.5)
    assert w["pared_mm"] == pytest.approx(HIP_WALL_REFERENCE, abs=0.01)
    assert w["pared_mm"] >= Params().min_wall


def test_with_hip_reference_is_a_copy():
    p = Params()
    q = p.with_hip_reference()
    assert q.hip_pin == HIP_PIN_REFERENCE and q.hip_pin is not HIP_PIN_REFERENCE
    assert p.hip_pin != HIP_PIN_REFERENCE and q.shoulder_pin == p.shoulder_pin


def test_head_separation_facing_and_crossing():
    pin = HIP_PIN_REFERENCE
    # pasador que va hacia −X desde x = 10: la cabeza queda lejos del plano de simetría
    far = hip_pin_walls(pin, (10.0, 0, 0), (-1, 0, 0), 0.5)
    assert far["separacion_cabezas_mm"] == pytest.approx(2 * (10 - pin.shaft_len - pin.head_h), abs=1e-6)
    # desde x = 4 (la cabeza llega hasta x = −0,74): la cabeza cruza x = 0 → separación negativa
    cross = hip_pin_walls(pin, (4.0, 0, 0), (-1, 0, 0), 0.5)
    assert cross["separacion_cabezas_mm"] < 0
    # pasador paralelo al plano de simetría (eje Y): el semiancho en X es el radio de la cabeza
    side = hip_pin_walls(pin, (10.0, 0, 0), (0, 1, 0), 0.5)
    assert side["separacion_cabezas_mm"] == pytest.approx(2 * (10 - pin.head_d / 2), abs=1e-6)


@pytest.fixture(scope="module")
def doll():
    return S.to_manifold(make_doll()), demo_project()


def _build(doll, params):
    body, proj = doll
    p = dataclasses.replace(params, decimate=0)
    res = P.build(body, proj.limbs, p, proj.symmetry, head=proj.head)
    return res, P.validate(res, p, proj.limbs), p


def test_validate_reports_hip_walls_with_default_pin(doll):
    _res, val, p = _build(doll, doll[1].params)
    w = val["cadera"]["pierna"]
    assert w["pared_mm"] == pytest.approx(p.hip_pin.shaft_len - 2 * p.clearance, abs=1e-3)
    assert w["separacion_cabezas_mm"] > 2 * p.clearance
    assert not any("pared tras la cabeza" in x or "cabezas de las dos caderas" in x for x in val["problems"])


def test_thin_wall_is_a_problem(doll):
    thin = dataclasses.replace(doll[1].params, hip_pin=dataclasses.replace(doll[1].params.hip_pin, shaft_len=1.5))
    _res, val, _p = _build(doll, thin)
    assert any("pared tras la cabeza" in x for x in val["problems"])
    assert not val["ok"]


def test_reference_pin_builds_ok_on_doll(doll):
    ref = doll[1].params.with_hip_reference()
    _res, val, _p = _build(doll, ref)
    assert val["cadera"]["pierna"]["pared_mm"] == pytest.approx(1.3, abs=0.01)
    assert not any("tras la cabeza" in x for x in val["problems"])


def test_cli_flag_registered():
    with pytest.raises(SystemExit) as e:
        cli_main(["build", "--help"])
    assert e.value.code == 0


def test_reference_figure_profile():
    p = Params.reference_figure()
    assert p.ref_height == 129.0 and p.hip_pin == HIP_PIN_REFERENCE
    assert p.hinge_pin == Params().hinge_pin                # bulones de bisagra: mm absolutos, sin escalar
    assert Params.from_dict(p.to_dict()) == p
