"""Mecanismo de clic de hombro y cadera (PLAN_mecanismo_clic_hombro_cadera.md)."""
import dataclasses
import os

import numpy as np
import pytest

from afm import detent as DT, pipeline as P, probes, solids as S
from afm.gui.controller import Controller
from afm.limbs import LimbSpec
from afm.params import DetentSpec, Params, PinSpec
from afm.profile import detent_profile
from afm.project import Project, demo_project
from afm.testfigure import make_doll


# ---------------------------------------------------------------- cálculo mecánico (§2.2, §3.5)
def test_default_spec_meets_strain_limit():
    sp = DetentSpec()
    assert DT.tip_flex(sp) == pytest.approx(0.5)
    assert DT.tongue_strain(sp) == pytest.approx(0.009)                 # plan §2.2: 0,9 %
    assert DT.tongue_strain(sp) <= sp.max_strain
    # boquilla 0,8: 1,6 × 12 mm → 0,83 % (plan §2.2)
    s8 = DT.spec_for_nozzle(0.8)
    assert (s8.finger_t, s8.finger_len) == (1.6, 12.0)
    assert DT.tongue_strain(s8) == pytest.approx(0.0083, abs=1e-4)
    # una lengüeta corta y gruesa se pasa del límite (plan §2.2: L = 6)
    assert DT.tongue_strain(dataclasses.replace(sp, finger_len=6.0)) > sp.max_strain


def test_estimates_are_in_the_planned_range():
    sp = DetentSpec()
    assert DT.tongue_force(sp) == pytest.approx(2.16, abs=0.01)
    assert 8.0 < DT.hold_torque(sp) < 10.0       # el prototipo de Güemes dio ≈ 9 N·mm (< hold_torque_min = 10): hay que calibrar
    assert DT.angular_play_deg(sp) == pytest.approx(2.5, abs=0.1)     # ±2–3° (plan §2.5)


def test_click_angles_follow_the_print_pose():
    sp = DetentSpec()
    assert DT.click_angles(sp, 10.0) == [10.0, 100.0, 190.0, 280.0]       # opción A del plan §3.4
    assert DT.click_angles(sp, 0.0) == [0.0, 90.0, 180.0, 270.0]          # opción B (brazo colgando)
    assert DT.click_angles(sp, 90.0) == [0.0, 90.0, 180.0, 270.0]         # cadera sentada: el clic de 90° es la pose impresa
    assert DT.click_angles(dataclasses.replace(sp, n_notches=3), 5.0) == [5.0, 125.0, 245.0]


def test_fit_to_space_keeps_the_strain_limit():
    sp = DetentSpec()
    same, note = DT.fit_to_space(sp, 12.0)
    assert same is sp and note is None
    fit, note = DT.fit_to_space(sp, 7.2)
    assert note and fit.finger_len == pytest.approx(7.2)
    assert DT.tongue_strain(fit) <= sp.max_strain
    assert fit.notch_depth < sp.notch_depth
    assert fit.flex_gap == pytest.approx(DT.tip_flex(fit) + fit.slit_w)


# ---------------------------------------------------------------- persistencia
def test_params_and_limb_roundtrip():
    p = dataclasses.replace(Params(), shoulder_detent=True, detent=DetentSpec(finger_len=9.0))
    q = Params.from_dict(p.to_dict())
    assert q.detent.finger_len == 9.0 and q.shoulder_detent and not q.hip_detent
    lim = LimbSpec.from_dict({**demo_project().limbs[0].to_dict(), "mechanism": "detent", "detent": {"finger_len": 8.0}})
    assert lim.mechanism == "detent" and lim.detent.finger_len == 8.0
    assert LimbSpec.from_dict(lim.to_dict()).detent == lim.detent


def test_old_project_loads_as_mushroom():
    d = demo_project().to_dict()
    for k in ("detent", "shoulder_detent", "hip_detent"):
        d["params"].pop(k, None)
    for l in d["limbs"]:
        l.pop("mechanism", None)
        l.pop("detent", None)
    proj = Project.from_dict(d)
    assert all(DT.detent_spec_for(l, proj.params) is None for l in proj.limbs)


def test_mechanism_selection():
    p = Params()
    arm = demo_project().limbs[0]
    leg = demo_project().limbs[1]
    assert DT.detent_spec_for(arm, p) is None and DT.detent_spec_for(leg, p) is None
    p2 = dataclasses.replace(p, shoulder_detent=True)
    assert DT.detent_spec_for(arm, p2) is p2.detent and DT.detent_spec_for(leg, p2) is None
    arm.mechanism = "mushroom"
    assert DT.detent_spec_for(arm, p2) is None            # el miembro manda sobre Params
    leg.mechanism, leg.detent = "detent", DetentSpec(finger_len=7.0)
    assert DT.detent_spec_for(leg, p).finger_len == 7.0
    leg.mechanism = "bisagra"
    with pytest.raises(ValueError):
        DT.detent_spec_for(leg, p)


def test_profiles_per_nozzle():
    p4, p8 = detent_profile(0.4), detent_profile(0.8)
    assert p4.shoulder_detent and p4.hip_detent and p8.shoulder_detent
    assert (p4.detent.finger_t, p4.detent.finger_len) == (1.2, 10.0)
    assert (p8.detent.finger_t, p8.detent.finger_len) == (1.6, 12.0) and p8.nozzle == 0.8


# ---------------------------------------------------------------- geometría
def test_parts_are_watertight_and_symmetric_in_z():
    sp = DetentSpec()
    c = (16.14, -3.6, 108.9)
    parts = DT.make_parts(c, (-1.0, 0.0, 0.0), sp)
    for mf in (parts.disc_plain, parts.disc_notch, *[g for f in parts.fingers for g in (f.beam, f.ridge, f.slot)]):
        assert mf.status() == S.m3.Error.NoError and mf.volume() > 0
    up, down = parts.fingers
    bu, bd = up.beam.bounding_box(), down.beam.bounding_box()
    assert bu[0] == pytest.approx(bd[0]) and bu[3] == pytest.approx(bd[3])
    assert (bu[2] + bd[5]) / 2 == pytest.approx(c[2], abs=1e-6) or bu[2] - c[2] == pytest.approx(c[2] - bd[5])
    # el disco queda del lado del torso (−X) de la cara y la lengüeta sale hacia el centro del torso
    assert parts.disc_notch.bounding_box()[0] == pytest.approx(c[0] - sp.disc_h)
    assert bu[0] == pytest.approx(c[0] - parts.root_depth - 1.0) and parts.frame.X(parts.root_depth) == pytest.approx(4.84)
    # el mismo diseño del lado opuesto (torso hacia +X) es el espejo
    mirrored = DT.make_parts((-16.14, -3.6, 108.9), (1.0, 0.0, 0.0), sp)
    assert mirrored.frame.X(parts.root_depth) == pytest.approx(-4.84)
    assert abs(mirrored.disc_notch.volume() - parts.disc_notch.volume()) < 1e-6


def test_tip_clicks_in_the_printed_pose():
    sp = DetentSpec()
    parts = DT.make_parts((0.0, 0.0, 0.0), (-1.0, 0.0, 0.0), sp)
    ridges = S.union(*[f.ridge for f in parts.fingers])
    assert float((ridges ^ parts.disc_notch).volume()) < 1e-6          # pose impresa: la punta no toca el disco…
    assert ridges.min_gap(parts.disc_notch, 3.0) == pytest.approx(sp.detent_gap, abs=0.02)   # …y deja la holgura de impresión
    half = S.rotate_about_axis(parts.disc_notch, (0, 0, 0), (1, 0, 0), 45.0)
    assert float((ridges ^ half).volume()) > 0.5                       # entre clics la punta apoya en el flanco
    full = S.rotate_about_axis(parts.disc_notch, (0, 0, 0), (1, 0, 0), 90.0)
    assert float((ridges ^ full).volume()) < 1e-6                      # cada 90° vuelve a caer en una muesca


def test_revolved_envelope_covers_the_solid():
    a = S.cylinder_along((0, 0, 10), (1, 0, 0), 3.0, 5.0, 64)
    b = S.cylinder_along((0, 0, -12), (1, 0, 0), 2.0, 5.0, 64, start=2.0)
    solid = S.union(a, b)
    env = DT.revolved_envelope(solid, (0, 0, 0), (1, 0, 0), 0.5)
    assert env.status() == S.m3.Error.NoError
    assert S.difference(solid, env).volume() < 1e-6                      # lo cubre entero, girado en cualquier ángulo
    for ang in (37.0, 90.0, 211.0):
        assert S.difference(S.rotate_about_axis(solid, (0, 0, 0), (1, 0, 0), ang), env).volume() < 1e-3
    # engrosa 0,5 mm: la distancia al sólido original es ≥ 0,5 en todo el borde del anillo
    assert env.min_gap(S.cylinder_along((0, 0, 30), (1, 0, 0), 1.0, 5.0, 16), 5.0) > 0.0


def test_axis_must_be_perpendicular_to_the_contact_face():
    with pytest.raises(ValueError, match="perpendicular a X"):
        DT.make_parts((10, 0, 0), (-0.8, 0.6, 0.0), DetentSpec())


# ---------------------------------------------------------------- probeta (Fase 0)
def test_detent_probe_cell_and_labels():
    p = Params()
    cells = probes.detent_probe_cells(p)
    assert len(cells) == 9
    assert [round(c.spec.finger_t, 1) for c in cells[:3]] == [1.0, 1.2, 1.4]
    assert [c.spec.notch_depth for c in cells[::3]] == pytest.approx([0.6, 0.8, 1.0])
    assert all(c.spec.flex_gap == pytest.approx(DT.tip_flex(c.spec) + c.spec.slit_w) for c in cells)
    txt = probes.detent_labels_text(cells)
    assert txt.count("\n") == 8 and "par est." in txt and "#9" in txt
    cell = probes._detent_cell(cells[4], p)                           # 0,8 / 1,2 / 0,3
    assert cell.status() == S.m3.Error.NoError
    assert len(cell.decompose()) == 2                                 # bloque + miembro suelto
    bloque, miembro = sorted(cell.decompose(), key=lambda m: -m.volume())
    assert bloque.min_gap(miembro, 3.0) >= 0.9 * min(p.clearance, cells[4].spec.detent_gap)


# ---------------------------------------------------------------- GUI
def test_gui_exposes_the_detent_fields():
    c = Controller()
    assert c.get_param("shoulder_detent") == 0.0
    assert c.set_param("shoulder_detent", 1.0) and c.session.project.params.shoulder_detent
    assert c.set_param("detent.finger_len", 9.0) and c.session.project.params.detent.finger_len == 9.0
    assert c.set_param("detent.n_notches", 4.0) and c.session.project.params.detent.n_notches == 4
    assert not c.set_param("detent.finger_len", 99.0)


# ---------------------------------------------------------------- pipeline sobre el muñeco (hombro con clic)
# El muñeco mide 100 mm y su brazo Ø8: un disco de Ø8 no cabe, se usa un clic a escala (y un hongo fino: el vástago
# tiene que quedar dentro del radio de la punta, si no la punta roza el vástago).
DOLL_DETENT = DetentSpec(disc_d=6.0, notch_depth=0.7, tip_r=0.5, finger_t=1.0, finger_len=8.0, finger_w=3.0,
                         flex_gap=0.9)


@pytest.fixture(scope="module")
def doll_arm():
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0, shoulder_detent=True, detent=DOLL_DETENT,
                            shoulder_pin=PinSpec(3.0, 2.0, 5.0, 1.2))
    limbs = [proj.limbs[0]]
    body = S.to_manifold(make_doll())
    res = P.build(body, limbs, p, proj.symmetry)
    val = P.validate(res, p, limbs, detent_free_step=45.0)
    return res, val, p


def test_doll_shoulder_builds_with_detent(doll_arm):
    res, val, p = doll_arm
    assert "brazo" in res.detent
    assert val["ok"], val["problems"]
    assert val["pieces"]["torso"]["componentes"] == 1
    assert val["pieces"]["brazo_izq"]["componentes"] == 1 and val["pieces"]["brazo_izq"]["variedad_ok"]
    rep = val["detent"]["brazo"]
    assert rep["clics"] == [10.0, 100.0, 190.0, 280.0] and rep["barrido"] == "vuelta completa (revolución)"
    assert rep["deformacion_ok"] and rep["protrusion_mm3"] < 1e-3
    assert rep["giro_libre"]["clics_descartados"] == []
    assert val["recorrido"] == {}                                      # el brazo no declara giro de cadera: lo cubre `detent`


def test_doll_click_in_print_pose_has_the_planned_gap(doll_arm):
    res, val, p = doll_arm
    g = val["gaps"]["brazo_izq"]
    assert g == pytest.approx(DOLL_DETENT.detent_gap, abs=0.03)        # la punta deja 0,3 mm al disco: es el mínimo
    assert g < p.clearance
    table = val["detent"]["brazo"]["clic_por_giro"]
    for a in ("0", "90", "180", "270"):
        assert table[a]["interf_punta_disco_mm3"] < 1e-3
    for a in ("45", "135", "225", "315"):
        assert table[a]["interf_punta_disco_mm3"] > 0.1


def test_doll_arm_is_exported_at_the_print_angle(doll_arm):
    res, _val, p = doll_arm
    standing = res.detent["brazo"].limb
    printed = res.pieces["brazo_izq"]
    again = S.rotate_about_axis(standing, res.detent["brazo"].center, res.detent["brazo"].axis, p.detent.print_angle)
    assert float((again ^ printed).volume()) == pytest.approx(float(printed.volume()), rel=1e-6)
    assert float((res.pieces["brazo_der"] ^ S.mirror_x(printed)).volume()) == pytest.approx(float(printed.volume()), rel=1e-6)


def test_doll_free_rotation_has_no_torso_contact(doll_arm):
    """La vuelta completa del hombro queda libre (se talla como revolución): ni la piel del torso roza (§3.2)."""
    res, _val, _p = doll_arm
    dl = res.detent["brazo"]
    for a in range(0, 360, 30):
        rot = S.rotate_about_axis(dl.limb, dl.center, dl.axis, float(a))
        assert float((rot ^ res.detent_ref).volume()) < 1e-3, a


def test_doll_keeps_mushroom_when_not_selected():
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0)
    res = P.build(S.to_manifold(make_doll()), [proj.limbs[0]], p, proj.symmetry)
    assert res.detent == {} and res.detent_ref is None and "detent" not in res.report
    assert P.validate(res, p, [proj.limbs[0]])["detent"] == {}


# ---------------------------------------------------------------- caso real (opcional: tarda varios minutos)
@pytest.mark.skipif(os.environ.get("AFM_SLOW") != "1", reason="AFM_SLOW=1 para correr Güemes con clic (varios minutos)")
def test_guemes_shoulder_and_hip_with_detent(tmp_path):
    import importlib.util
    root = os.path.dirname(os.path.dirname(__file__))
    spec = importlib.util.spec_from_file_location("guemes_clic", os.path.join(root, "examples", "guemes_clic.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from afm.cli import run
    info = run(os.path.join(root, "examples", "guemes_segmentado_lq_fixed_resized.stl"), mod.proj, str(tmp_path), "g",
               progress=lambda *_: None)
    val = info["validacion"]
    assert val["ok"], val["problems"]
    assert val["pieces"]["torso"]["componentes"] == 1
    assert set(val["detent"]) == {"brazo", "pierna"}
