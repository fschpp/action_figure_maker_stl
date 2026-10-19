"""Cintura articulada: corte del torso por un plano de 3 puntos + pasador de hongo robusto (giro a los costados)."""
import dataclasses
import json

import numpy as np
import pytest

from afm import pipeline as P, solids as S
from afm.params import Params, PinSpec
from afm.planes import Plane
from afm.project import Project, demo_project
from afm.testfigure import make_doll
from afm.waist import PELVIS_NAME, WAIST_NAME, WaistSpec, build_waist, fit_pin, waist_plane

WAIST_Z = 55.0


def _waist(z=WAIST_Z, **kw):
    return WaistSpec(Plane.from_points((-10, -6, z), (10, -6, z), (0, 8, z), "cintura"), **kw)


@pytest.fixture(scope="module")
def doll():
    return S.to_manifold(make_doll())


@pytest.fixture(scope="module")
def params():
    return dataclasses.replace(demo_project().params, decimate=0)      # sin decimar: más rápido


@pytest.fixture(scope="module")
def built(doll, params):
    pr = demo_project()
    res = P.build(doll, pr.limbs, params, pr.symmetry, head=pr.head, waist=_waist())
    return res, P.validate(res, params, pr.limbs)


# ------------------------------------------------------------------ pipeline completo
def test_torso_splits_into_two_watertight_single_pieces(built):
    res, val = built
    assert {"torso", PELVIS_NAME} <= set(res.pieces)
    assert val["ok"], val["problems"]
    for nm in ("torso", PELVIS_NAME):
        assert val["pieces"][nm]["componentes"] == 1 and val["pieces"][nm]["variedad_ok"]
    z_up = res.pieces["torso"].bounding_box()[2]
    z_lo = res.pieces[PELVIS_NAME].bounding_box()[5]
    assert z_up == pytest.approx(WAIST_Z + 0.5, abs=1e-3)                         # el pecho arranca `clearance` sobre el plano
    assert z_lo >= WAIST_Z                                                        # el pasador sube por encima del plano
    assert res.pieces[PELVIS_NAME].bounding_box()[2] < res.pieces["torso"].bounding_box()[2]


def test_clearance_between_halves_and_captive_pin(built, params):
    res, val = built
    info = val["cintura"]
    assert info["holgura_mm"] == pytest.approx(params.clearance, abs=0.05)
    assert info["cautivo"]
    up, lo = res.pieces["torso"], res.pieces[PELVIS_NAME]
    assert (up ^ lo).volume() < 1e-6
    assert (up.translate((0, 0, 1.0)) ^ lo).volume() > 1e-6          # separar 1 mm choca con el labio de la cavidad


def test_free_turn_of_both_halves_about_the_axis(built):
    res, _ = built
    wb = res.waist
    up, lo = res.pieces["torso"], res.pieces[PELVIS_NAME]
    for ang in (37.0, 90.0, 180.0, -60.0):
        assert (S.rotate_about_axis(up, wb.face.center, wb.axis, ang) ^ lo).volume() < 1e-4


def test_pin_grows_from_the_pelvis_upwards(built, params):
    res, _ = built
    wb = res.waist
    assert wb.pin_on == "lower"
    assert abs(wb.face.center[0]) < 1e-6 and wb.face.center[2] == pytest.approx(WAIST_Z, abs=1e-6)
    assert np.allclose(wb.axis, (0, 0, 1))
    assert S.difference(wb.pin, res.pieces[PELVIS_NAME]).volume() < 1e-6      # el pasador es parte de la pelvis
    assert (wb.pin ^ res.pieces["torso"]).volume() < 1e-6                     # y no toca el pecho
    sp = wb.pin_spec
    top = wb.pin.bounding_box()[5]
    assert top == pytest.approx(WAIST_Z + sp.shaft_len + sp.head_h, abs=0.05)


def test_report_and_validation_of_the_waist(built, params):
    res, val = built
    rep = res.report["waist"]
    assert rep["arriba"] == ["torso", "cabeza", "brazo_izq", "brazo_der"]
    assert rep["abajo"] == [PELVIS_NAME, "pierna_izq", "pierna_der"]
    assert rep["pared_tras_cabeza_mm"] == pytest.approx(rep["pin"]["shaft_len"] - 2 * params.clearance)
    assert rep["relacion_cabeza_vastago"] >= 1.6
    assert val["cintura"]["giro_libre"]["+"]["hasta_grados"] == pytest.approx(params.waist_twist)
    assert val["cintura"]["giro_libre"]["-"]["hasta_grados"] == pytest.approx(params.waist_twist)
    assert val["cintura"]["giro_libre"]["+"]["choca"] == {}


def test_waist_pin_is_sturdier_than_the_shoulder_pin(params):
    w, s = params.waist_pin, params.shoulder_pin
    assert w.shaft_d >= 1.4 * s.shaft_d and w.head_d > s.head_d
    assert w.shaft_len - 2 * params.clearance >= params.min_wall + 0.5         # pared tras la cabeza holgada


def test_limbs_are_still_validated_against_pecho_and_pelvis(built):
    res, val = built
    assert set(val["gaps"]) == {"brazo_izq", "brazo_der", "pierna_izq", "pierna_der", "cabeza"}
    assert min(val["gaps"].values()) >= 0.4
    assert P.body_ref(res).bounding_box()[5] == pytest.approx(res.pieces["torso"].bounding_box()[5])


def test_twist_collision_is_detected(built, params):
    """Una pieza fija en el camino de un brazo que gira: el giro libre se corta y se informa."""
    res, _ = built
    arm = res.pieces["brazo_izq"]
    wb = res.waist
    blocker = S.rotate_about_axis(arm, wb.face.center, wb.axis, 25.0)       # donde estará el brazo con 25° de giro
    pieces = dict(res.pieces, bloqueo=blocker)
    report = dict(res.report, waist=dict(res.report["waist"], abajo=res.report["waist"]["abajo"] + ["bloqueo"]))
    fake = dataclasses.replace(res, pieces=pieces, report=report)
    out = {"problems": [], "avisos": [], "avisos_pasador": []}
    info = P._validate_waist(fake, params, out)
    assert info["giro_libre"]["+"]["hasta_grados"] < 25.0
    assert any("brazo_izq~bloqueo" in k for k in info["giro_libre"]["+"]["choca"])
    assert any("choca" in pr for pr in out["problems"])


def test_flat_for_print_uses_the_pelvis_as_the_lowest_part(built, params):
    res, _ = built
    pieces, info = P.flat_for_print(res, 1.0, params)
    assert info["altura_corte_z"] == pytest.approx(res.pieces[PELVIS_NAME].bounding_box()[2] + 1.0, abs=1e-3)
    assert pieces[PELVIS_NAME].bounding_box()[2] == pytest.approx(info["altura_corte_z"], abs=1e-3)
    assert "torso" in pieces and PELVIS_NAME in pieces


def test_without_waist_nothing_changes(doll, params):
    pr = demo_project()
    res = P.build(doll, pr.limbs, params, pr.symmetry, head=pr.head)
    assert PELVIS_NAME not in res.pieces and res.waist is None and "waist" not in res.report
    assert P.validate(res, params, pr.limbs)["cintura"] == {}


# ------------------------------------------------------------------ el motor solo (sobre el cuerpo entero)
def test_pin_on_upper_puts_the_pin_on_the_chest(doll, params):
    wb = build_waist(doll, _waist(pin_on="upper"), params)
    assert S.difference(wb.pin, wb.upper).volume() < 1e-6
    assert (wb.pin ^ wb.lower).volume() < 1e-6
    assert wb.face.outward[2] < 0                                               # la cara del pecho mira hacia abajo
    assert (wb.upper.translate((0, 0, 1.0)) ^ wb.lower).volume() > 1e-6         # cautivo: separar 1 mm choca con el labio
    with pytest.raises(ValueError, match="pin_on"):
        build_waist(doll, _waist(pin_on="medio"), params)


def test_tilted_plane_turns_about_its_normal(doll, params):
    tilted = WaistSpec(Plane.from_points((-10, -6, 53), (10, -6, 53), (0, 8, 58), "cintura"))
    wb = build_waist(doll, tilted, params)
    n = waist_plane(tilted).normal
    assert abs(n[0]) < 1e-9 and 0.3 < n[2] < 0.99 and abs(n[1]) > 0.1
    assert np.allclose(wb.axis, n)
    for ang in (30.0, 120.0):
        assert (S.rotate_about_axis(wb.upper, wb.face.center, wb.axis, ang) ^ wb.lower).volume() < 1e-4
    assert wb.upper.min_gap(wb.lower, 5.0) == pytest.approx(params.clearance, abs=0.06)


def test_plane_is_symmetrized_and_checked():
    n = waist_plane(WaistSpec(Plane.from_normal((3, 0, 55), (0, 0, -1)))).normal
    assert n[2] > 0 and waist_plane(WaistSpec(Plane.from_normal((3, 0, 55), (0, 0, -1)))).origin[0] == 0
    with pytest.raises(ValueError, match="vertical"):
        waist_plane(WaistSpec(Plane.from_points((0, 0, 40), (0, 10, 40), (0, 0, 60))))
    with pytest.raises(ValueError, match="costado"):
        waist_plane(WaistSpec(Plane.from_points((0, 0, 40), (0, 10, 41), (10, 0, 60))))


def test_plane_outside_the_figure_is_rejected(doll, params):
    with pytest.raises(ValueError, match="parte de arriba"):
        build_waist(doll, _waist(z=150.0), params)
    with pytest.raises(ValueError, match="pelvis"):
        build_waist(doll, _waist(z=-20.0), params)


def test_pin_shrinks_to_fit_a_narrow_cut_and_reports_it(doll, params):
    big = dataclasses.replace(params, waist_pin=PinSpec(12.0, 3.0, 22.0, 3.0))
    wb = build_waist(doll, _waist(), big, skin=doll)
    assert wb.info["pin_reducido"] is not None and wb.info["pin_reducido"] < 1.0
    assert wb.info["margen_cabeza_mm"] >= big.min_wall - 0.05                 # la cabeza entra con `min_wall` de pared
    assert wb.pin_spec.head_d / wb.pin_spec.shaft_d == pytest.approx(22.0 / 12.0)
    assert wb.info["pasador_fuera_mm3"] < 0.05
    fixed = build_waist(doll, _waist(auto=False, pin=PinSpec(12.0, 3.0, 22.0, 3.0)), params, skin=doll)
    assert fixed.info["pin_reducido"] is None and fixed.info["pasador_fuera_mm3"] > 10.0      # sin auto no se toca y avisa


def test_a_face_too_small_for_the_pin_raises(doll, params):
    huge = dataclasses.replace(params, waist_pin=PinSpec(30.0, 3.0, 55.0, 3.0))
    with pytest.raises(ValueError, match="demasiado chica"):
        build_waist(doll, _waist(), huge)


def test_explicit_pin_center_is_projected_on_the_cut_and_x_zero(doll, params):
    wb = build_waist(doll, _waist(pin_center=(4.0, 2.0, 70.0)), params)
    assert wb.face.center[0] == 0 and wb.face.center[2] == pytest.approx(WAIST_Z)
    assert wb.face.center[1] == pytest.approx(2.0)


def test_fit_pin_keeps_a_pin_that_fits(doll, params):
    wb = build_waist(doll, _waist(), params)
    same, k = fit_pin(PinSpec(3.0, 2.0, 5.0, 2.0), wb.face, params.min_wall)
    assert k == 1.0 and same == PinSpec(3.0, 2.0, 5.0, 2.0)


# ------------------------------------------------------------------ proyecto
def test_project_roundtrip_with_waist(tmp_path):
    pr = demo_project()
    pr.waist = _waist(pin=PinSpec(5.0, 3.0, 9.0, 2.5), twist_deg=30.0, pin_on="upper")
    path = str(tmp_path / "p.proyecto")
    pr.save(path)
    back = Project.load(path)
    assert back.waist is not None and back.waist.pin == PinSpec(5.0, 3.0, 9.0, 2.5)
    assert back.waist.twist_deg == 30.0 and back.waist.pin_on == "upper"
    assert np.allclose(back.waist.plane.normal, pr.waist.plane.normal)
    assert back.params.waist_pin == pr.params.waist_pin


def test_old_projects_without_waist_load(tmp_path):
    d = demo_project().to_dict()
    d.pop("waist")
    d["params"].pop("waist_pin")
    d["params"].pop("waist_twist")
    path = tmp_path / "viejo.proyecto"
    path.write_text(json.dumps(d))
    old = Project.load(str(path))
    assert old.waist is None and old.params.waist_pin == Params().waist_pin


def test_scale_geometry_scales_the_waist_plane_but_not_the_pin():
    pr = demo_project()
    pr.waist = _waist(pin_center=(0.0, 1.0, WAIST_Z))
    pin = dataclasses.replace(pr.params.waist_pin)
    pr.scale_geometry(1.29)
    assert pr.waist.plane.origin[2] == pytest.approx(WAIST_Z * 1.29)
    assert pr.waist.pin_center[2] == pytest.approx(WAIST_Z * 1.29)
    assert pr.params.waist_pin == pin                       # medidas absolutas de impresión
