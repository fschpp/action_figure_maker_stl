"""«Modificar para imprimir sin soportes»: corte plano de piernas y cola + brazos girados hacia adelante."""
import json
import os

import numpy as np
import pytest

from afm import cli, pipeline as P, solids as S
from afm.params import Params

CENTER = np.array([9.0, 0.0, 24.0])      # pasador del hombro
AXIS = np.array([1.0, 0.0, 0.0])


def box(x0, x1, y0, y1, z0, z1):
    return S.Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def synthetic(arm_x=(9.0, 12.0), arm_z0=0.0, torso_half=5.0):
    """Muñeco mínimo sentado (adelante = +Y): torso con la cola a 3 mm de la cama, piernas cilíndricas hacia adelante
    apoyadas en z = 0 y un brazo que cuelga del pasador del hombro hasta la cama."""
    torso = box(-torso_half, torso_half, -4, 4, 3, 30)
    leg = S.cylinder_along((4.2, 4.5, 4.0), (0, 1, 0), 4.0, 25.0, 48, start=0.0)
    arm = box(arm_x[0], arm_x[1], -1.5, 1.5, arm_z0, 25.0)
    pieces = {"torso": torso, "pierna_izq": leg, "pierna_der": S.mirror_x(leg),
              "brazo_izq": arm, "brazo_der": S.mirror_x(arm)}
    motion = {"brazo": (None, CENTER, AXIS, (0.0, 0.0)), "pierna": (None, np.array([4.2, 4.5, 4.0]), AXIS, (0.0, 90.0))}
    return P.BuildResult(pieces=pieces, report={}, motion=motion)


def zmin(mf):
    return mf.bounding_box()[2]


def test_arms_turn_forward_until_they_touch_the_cut_plane():
    res = synthetic()
    pieces, info = P.flat_for_print(res, 1.0)
    z_cut = 3.0 + 1.0
    assert info["altura_corte_z"] == pytest.approx(z_cut)
    assert zmin(pieces["torso"]) == pytest.approx(z_cut, abs=1e-3)
    for leg in ("pierna_izq", "pierna_der"):
        assert zmin(pieces[leg]) == pytest.approx(z_cut, abs=1e-3)          # base plana en el plano del corte
        assert info["volumen_quitado_mm3"][leg] > 0
    # el punto más bajo es la esquina de atrás del brazo (y = −1,5): ya parte 3,6° detrás de la vertical del pasador
    r = np.hypot(24.0, 1.5)
    expected = np.degrees(np.arccos((CENTER[2] - z_cut) / r)) + np.degrees(np.arctan(1.5 / 24.0))
    assert info["giro_brazos_grados"]["brazo"] == pytest.approx(expected, abs=0.1)
    for arm in ("brazo_izq", "brazo_der"):
        assert zmin(pieces[arm]) == pytest.approx(z_cut, abs=0.02)           # apoya justo en el plano, ni más ni menos
        assert pieces[arm].bounding_box()[4] > res.pieces[arm].bounding_box()[4] + 5   # se fue hacia adelante (+Y)
    assert info["avisos"] == []


def test_right_arm_is_the_mirror_of_the_left_one():
    pieces, _ = P.flat_for_print(synthetic(), 1.0)
    a = S.to_trimesh(pieces["brazo_izq"]).bounds
    b = S.to_trimesh(pieces["brazo_der"]).bounds
    assert a[:, 1:] == pytest.approx(b[:, 1:], abs=1e-6)
    assert a[:, 0] == pytest.approx(-b[::-1, 0], abs=1e-6)


def test_arm_already_above_the_plane_is_left_alone():
    res = synthetic(arm_z0=6.0)                                              # el puño ya queda sobre el plano z = 4
    pieces, info = P.flat_for_print(res, 1.0)
    assert info["giro_brazos_grados"]["brazo"] == 0.0
    assert pieces["brazo_izq"] is res.pieces["brazo_izq"]                    # no se tocó


def test_torso_is_carved_where_the_turning_arm_would_hit_it():
    res = synthetic(torso_half=8.7)                                          # brazo a 0,3 mm del torso: menos que la holgura
    pieces, info = P.flat_for_print(res, 1.0, Params())
    assert S.intersection(pieces["brazo_izq"], pieces["torso"]).volume() < 1e-3
    assert not any("choca" in w for w in info["avisos"])
    flat_cut = 2 * 8.7 * 8.0 * 1.0                                           # solo el corte plano: 17,4 × 8 × 1 mm
    assert info["volumen_quitado_mm3"]["torso"] > flat_cut + 5               # y además el tallado del barrido del brazo
    assert info["volumen_quitado_mm3"]["torso"] == pytest.approx(res.pieces["torso"].volume() - pieces["torso"].volume(), abs=0.2)


def test_the_flat_cut_gives_a_real_base_and_less_overhang(tmp_path):
    res = synthetic()
    plain = P.export(res, str(tmp_path / "a"), "f", orient=True)["orientacion"]
    flat = P.export(res, str(tmp_path / "b"), "f", orient=True, flat=1.0)
    assert flat["orientacion"]["base_mm2"] > 3 * plain["base_mm2"]           # cilindros: sin corte solo una facetita de contacto
    assert flat["orientacion"]["base_mm2"] > 100
    assert flat["orientacion"]["voladizo_mm2"] < plain["voladizo_mm2"]
    assert flat["sin_soportes"]["corte_sobre_cola_mm"] == 1.0
    import trimesh
    zs = [trimesh.load(str(p)).bounds[0][2] for p in (tmp_path / "b" / "piezas").glob("*.stl")]
    assert min(zs) == pytest.approx(0.0, abs=1e-3) and max(zs) < 31          # todas bajaron lo mismo: apoyadas en z = 0
    assert all(abs(z) < 0.05 for z in zs if z < 1)                           # piernas, cola y puños en el mismo plano


def test_exported_pieces_have_no_non_manifold_edges(tmp_path):
    info = P.export(synthetic(), str(tmp_path), "f", orient=True, flat=1.0)
    assert info["sin_soportes"]["bordes_no_variedad"] == {}


def test_default_depth_and_gui_field_exist():
    from afm.gui.controller import PARAM_FIELDS
    assert Params().flat_depth == 1.0
    assert any(f[0] == "flat_depth" for f in PARAM_FIELDS)


def test_cli_flag_parses(tmp_path, monkeypatch):
    seen = {}

    def fake_run(stl, project, outdir, name, **kw):
        seen.update(kw)
        return {"archivos": {}, "validacion": {"ok": True, "problems": [], "gaps": {}}, "construccion": {}}

    monkeypatch.setattr(cli, "run", fake_run)
    from afm.project import demo_project
    proj = tmp_path / "p.proyecto"
    demo_project().save(str(proj))
    stl = tmp_path / "x.stl"
    stl.write_text("solid x\nendsolid x\n")
    cli.main(["build", str(stl), str(proj), "--out", str(tmp_path / "o"), "--sin-soportes"])
    assert seen["flat"] == pytest.approx(Params().flat_depth)
    cli.main(["build", str(stl), str(proj), "--out", str(tmp_path / "o"), "--sin-soportes", "2.5"])
    assert seen["flat"] == 2.5
    cli.main(["build", str(stl), str(proj), "--out", str(tmp_path / "o")])
    assert seen["flat"] is None


# ---------------------------------------------------------------- rodillas estiradas (muslo y pantorrilla horizontales)
@pytest.fixture(scope="module")
def knee_doll():
    import dataclasses

    from afm.planes import Plane
    from afm.joints import JointSpec
    from afm.project import demo_project
    from afm.testfigure import make_doll

    proj = demo_project()
    params = dataclasses.replace(proj.params, decimate=0)
    joint = JointSpec(name="rodilla", limb="pierna", kind="knee", plane=Plane.from_normal((0, 0, 14.0), (0, 0, 1), "art"),
                      axis_center=(9.0, 0.0, 17.4))
    res = P.build(S.to_manifold(make_doll()), proj.limbs, params, proj.symmetry, head=proj.head, joints=[joint])
    return res, params


def extent(mf):
    b = mf.bounding_box()
    return b[3] - b[0], b[4] - b[1], b[5] - b[2]


def test_straight_leg_flex_only_covers_leg_joints(knee_doll):
    res, _p = knee_doll
    assert P.straight_leg_flex(res) == {"rodilla": 0.0}
    assert res.report["joints"]["rodilla"]["print_hip"] == 90.0


def test_posed_leg_with_zero_flex_is_horizontal_like_the_thigh(knee_doll):
    res, params = knee_doll
    bent = P.posed_pieces(res, params.sit_angle)
    straight = P.posed_pieces(res, params.sit_angle, P.straight_leg_flex(res))
    _dx, dy, dz = extent(bent["pantorrilla_izq"])
    assert dz > dy                                    # flexionada 85°: la pantorrilla cuelga (vertical)
    _dx, dy, dz = extent(straight["pantorrilla_izq"])
    assert dy > dz                                    # estirada: horizontal, como el muslo
    mt, ms = straight["muslo_izq"].bounding_box(), straight["pantorrilla_izq"].bounding_box()
    assert ms[2] >= mt[2] - 1e-6 and ms[5] <= mt[5] + 1e-6          # dentro de la altura del muslo
    assert ms[4] > mt[4] - 1e-6                                       # sigue hacia adelante (+Y), no vuelve


def test_flat_print_stretches_knees_and_both_leg_pieces_rest_on_the_plane(knee_doll):
    res, params = knee_doll
    pieces, info = P.flat_for_print(res, 1.0, params)
    assert info["rodillas_estiradas"] == {"rodilla": {"flexion_grados": 0.0, "flexion_impresa_grados": params.knee_print_angle}}
    z_cut = info["altura_corte_z"]
    for side in ("izq", "der"):
        assert zmin(pieces[f"muslo_{side}"]) == pytest.approx(z_cut, abs=1e-3)
        assert zmin(pieces[f"pantorrilla_{side}"]) == pytest.approx(z_cut, abs=1e-3)      # antes colgaba por debajo del plano
        dx, dy, dz = extent(pieces[f"pantorrilla_{side}"])
        assert dy > dz
    assert not any("pantorrilla" in a and "sueltas" in a for a in info["avisos"])


def test_thigh_fill_is_opt_in_and_never_collides():
    res = synthetic()
    plain, info0 = P.flat_for_print(res, 1.0, Params())
    assert "relleno_cadera" not in info0
    import dataclasses
    filled, info = P.flat_for_print(res, 1.0, dataclasses.replace(Params(), flat_fill_hip=True))
    for leg in ("pierna_izq", "pierna_der"):
        assert zmin(filled[leg]) >= info["altura_corte_z"] - 1e-3          # el relleno nunca baja del plano
    for rep in info.get("relleno_cadera", {}).values():
        assert rep.get("choque_max_mm3", 0.0) <= 1e-3


def test_export_message_reports_the_thigh_fill():
    from afm.gui.controller import fill_hip_message
    ok = {"relleno_cadera": {"pierna": {"volumen_muslo_agregado_mm3": 30.8, "volumen_pelvis_tallado_mm3": 135.6,
                                        "holgura_de_pie_mm": 0.434}}}
    assert fill_hip_message(ok, False) == ""
    msg = fill_hip_message(ok, True)
    assert "+30.8 mm³" in msg and "135.6" in msg and "0.434" in msg
    assert fill_hip_message({}, True) == " Relleno de muslos: no se aplicó."
    assert "no se aplicó" in fill_hip_message({"relleno_cadera": {"pierna": {"aviso": "choca"}}}, True)


def test_thigh_fill_says_why_it_is_skipped():
    import dataclasses
    res = synthetic()
    res.detent["pierna"] = object()                       # cadera con mecanismo de clic: todavía no se rellena
    _pieces, info = P.flat_for_print(res, 1.0, dataclasses.replace(Params(), flat_fill_hip=True))
    assert "clic" in info["relleno_cadera"]["pierna"]["aviso"]
    assert any(a.startswith("pierna:") and "clic" in a for a in info["avisos"])
