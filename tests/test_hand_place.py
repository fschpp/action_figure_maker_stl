"""Pegar la mano estándar en otro modelo: `HandSpec` / `apply_hand` (motor, sin GUI)."""
import numpy as np
import pytest

from afm import solids as S
from afm.hand import (HandError, HandSpec, WristFrame, apply_hand, extract_standard_hand, hand_meta,
                      save_standard_hand, split_at_wrist)
from afm.project import Project

M = S.Manifold
WRIST_X = 36.0
PTS = [(WRIST_X, 0.0, 72.0), (WRIST_X, -2.5, 68.5), (WRIST_X, 2.5, 68.5)]


def _figure(arm_r: float, hand_r: float):
    """Figura simétrica de 100 mm con brazos horizontales en ±X que terminan en una esfera (la «mano»)."""
    parts = [M.cube((30, 16, 50), center=True).translate((0, 0, 55)),
             M.cube((24, 14, 30), center=True).translate((0, 0, 15)),
             M.sphere(10, 48).translate((0, 0, 90))]
    for sx in (+1, -1):
        arm = M.cylinder(30.0, arm_r, arm_r, 32).rotate((0, 90, 0)).translate((15, 0, 70))
        parts.append(arm.mirror((1, 0, 0)) if sx < 0 else arm)
        parts.append(M.sphere(hand_r, 32).translate((sx * 42.0, 0, 70)))
    return S.union(*parts)


@pytest.fixture(scope="module")
def standard(tmp_path_factory):
    """Mano estándar de una figura con brazo Ø6 y mano esférica de radio 5, guardada como STL + JSON."""
    src = _figure(3.0, 5.0)
    hand, split = extract_standard_hand(src, PTS)
    path = str(tmp_path_factory.mktemp("mano") / "estandar.stl")
    save_standard_hand(path, hand, hand_meta(split, height_mm=130.0, source="origen.stl"))
    return path, split


@pytest.fixture(scope="module")
def target():
    """Modelo distinto: brazo Ø5 y una mano esférica más chica (radio 3,5)."""
    return _figure(2.5, 3.5)


@pytest.fixture(scope="module")
def target_half(target):
    return target.trim_by_plane((1.0, 0.0, 0.0), 0.0)


def test_apply_swaps_the_hand(standard, target_half):
    path, src = standard
    old = split_at_wrist(target_half, WristFrame.from_points(PTS))
    hb = apply_hand(target_half, HandSpec(path, tuple(PTS)))
    # la mano original ya no está: la nueva (esfera de radio 5) llega más lejos que la vieja (radio 4)
    assert hb.body.bounding_box()[3] > old.hand.bounding_box()[3] + 0.5
    assert hb.body.bounding_box()[3] == pytest.approx(42.0 + 5.0 - 0.5, abs=0.2)     # centro 42 + radio 5, menos el solape
    assert hb.original.volume() == pytest.approx(old.hand_volume, rel=1e-6)
    assert S.to_trimesh(hb.body).is_watertight


def test_hand_stays_welded_to_the_body(standard, target_half):
    # la mano nueva queda unida al brazo: no suma componentes sueltos al cuerpo
    path, _ = standard
    hb = apply_hand(target_half, HandSpec(path, tuple(PTS)))
    assert len(hb.body.decompose()) == len(target_half.decompose())


def test_volume_accounts_for_overlap(standard, target_half):
    path, _ = standard
    sp = split_at_wrist(target_half, WristFrame.from_points(PTS))
    h0 = apply_hand(target_half, HandSpec(path, tuple(PTS), overlap=0.0))
    h1 = apply_hand(target_half, HandSpec(path, tuple(PTS), overlap=0.5))
    # sin solape: resto + mano; con solape se hunde en el brazo, así que el volumen total baja (≈ área × 0,5)
    assert h0.body.volume() == pytest.approx(sp.rest_volume + h0.hand.volume(), rel=1e-4)
    assert h1.body.volume() < h0.body.volume()
    assert h1.body.volume() > sp.rest_volume


def test_points_on_the_other_arm_are_mirrored(standard, target_half):
    path, _ = standard
    left = apply_hand(target_half, HandSpec(path, tuple(PTS)))
    right_pts = tuple((-x, y, z) for x, y, z in PTS)
    right = apply_hand(target_half, HandSpec(path, right_pts))
    assert right.info["puntos_espejados"] and not left.info["puntos_espejados"]
    assert right.body.volume() == pytest.approx(left.body.volume(), rel=1e-6)
    assert np.allclose(right.frame.center, left.frame.center, atol=1e-6)


def test_yaw_twists_the_reference_only(standard, target_half):
    path, _ = standard
    a = apply_hand(target_half, HandSpec(path, tuple(PTS)))
    b = apply_hand(target_half, HandSpec(path, tuple(PTS), yaw=90.0))
    assert np.allclose(a.frame.axis, b.frame.axis) and np.allclose(a.frame.center, b.frame.center)
    assert abs(a.frame.ref @ b.frame.ref) < 1e-9                                        # 90°: perpendiculares
    assert b.info["giro_grados"] == 90.0


def test_twisted_is_a_rotation_about_the_axis():
    f = WristFrame.from_points(PTS)
    g = f.with_axis_sign(1.0).twisted(180.0)
    assert np.allclose(g.ref, -f.ref, atol=1e-9)
    assert np.allclose(f.twisted(0.0).ref, f.ref)


def test_info_reports_radii_and_step(standard, target_half):
    path, _ = standard
    hb = apply_hand(target_half, HandSpec(path, tuple(PTS)))
    assert hb.info["radio_brazo_mm"] == pytest.approx(2.5, rel=0.03)
    assert hb.info["radio_mano_mm"] == pytest.approx(3.0, rel=0.03)
    assert hb.info["escalon_mm"] == pytest.approx(0.5, abs=0.15)
    assert hb.info["avisos"] == []                                                      # 0,5 mm de escalón: sin aviso


def test_warns_about_wrist_step_and_scale(standard):
    path, _ = standard
    thin = _figure(1.5, 4.0).trim_by_plane((1.0, 0.0, 0.0), 0.0)                        # brazo Ø3: escalón de 1,5 mm
    hb = apply_hand(thin, HandSpec(path, tuple(PTS)), ref_height=100.0)
    text = " ".join(hb.info["avisos"])
    assert "escalón" in text                                                            # radios 1,5 vs 3
    assert "130 mm" in text and "100 mm" in text                                        # la mano se midió a 130 y esta figura es de 100


def test_rejects_bad_inputs(standard, target_half, tmp_path):
    path, _ = standard
    with pytest.raises(HandError, match="unión"):
        apply_hand(target_half, HandSpec(path, tuple(PTS), join="giro"))
    with pytest.raises(HandError, match="3 puntos"):
        apply_hand(target_half, HandSpec(path, tuple(PTS[:2])))
    with pytest.raises(HandError, match="alineados"):
        apply_hand(target_half, HandSpec(path, ((36, 0, 70), (36, 1, 70), (36, 2, 70))))
    ball = str(tmp_path / "bola.stl")
    S.to_trimesh(M.sphere(5, 32)).export(ball)                                          # sin cara plana en z = 0
    with pytest.raises(HandError, match="cara plana"):
        apply_hand(target_half, HandSpec(ball, tuple(PTS)))
    with pytest.raises(HandError, match="atraviesa"):
        apply_hand(target_half, HandSpec(path, ((70, 0, 70), (70, -2, 68), (70, 2, 68))))


def test_spec_roundtrip_and_project(standard, tmp_path):
    path, _ = standard
    spec = HandSpec(path, tuple(PTS), yaw=15.0, invert=True, overlap=0.3)
    assert HandSpec.from_dict(spec.to_dict()) == spec
    proj = Project(hand=spec)
    f = str(tmp_path / "p.proyecto")
    proj.save(f)
    assert Project.load(f).hand == spec
    old = Project().to_dict()
    old.pop("hand", None)                                                               # proyecto viejo sin mano
    assert Project.from_dict(old).hand is None


def test_scale_geometry_moves_points_not_the_hand_file(standard):
    path, _ = standard
    proj = Project(hand=HandSpec(path, tuple(PTS), overlap=0.5))
    proj.scale_geometry(2.0)
    assert proj.hand.points[0] == (WRIST_X * 2, 0.0, 144.0)
    assert proj.hand.overlap == 0.5 and proj.hand.path == path                          # mm reales: no se escalan


# ------------------------------------------------------------------ pipeline completo (brazo colgando, como Güemes)
import dataclasses  # noqa: E402

from afm import pipeline as P  # noqa: E402
from afm.limbs import LimbSpec  # noqa: E402
from afm.params import Params, PinSpec  # noqa: E402
from afm.planes import Plane  # noqa: E402

ARM_PTS = [(21.0, 3.0, 57.0), (18.0, -3.0, 57.0), (24.0, -3.0, 57.0)]      # plano de muñeca z = 57, punto 1 hacia +Y
SYM = Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría")


def _ell(rx, ry, h, z0, cx=0.0):
    return M.cylinder(h, 1, 1, 64).scale((rx, ry, 1)).translate((cx, 0, z0))


def _armed(hand_r: float):
    """Muñeco de 100 mm con los brazos separados del torso (unidos solo en el hombro) y una esfera de mano de radio `hand_r`."""
    parts = [_ell(23, 12, 8, 34), _ell(15, 10, 33, 42), M.sphere(11, 48).translate((0, 0, 89)), _ell(4, 4, 10, 72)]
    for sx in (+1, -1):
        parts.append(_ell(7, 7, 38, 0.0, cx=sx * 9))
        bridge = M.cylinder(12, 3.5, 3.5, 32).rotate((0, 90, 0)).translate((10 if sx > 0 else -22, 0, 71))
        parts += [M.cylinder(24, 3, 3, 32).translate((sx * 21, 0, 50)), bridge, M.sphere(hand_r, 32).translate((sx * 21, 0, 50))]
    return S.union(*parts)


def _arm(z0: float) -> LimbSpec:
    fp = Plane.from_normal((0, 0, 0), (0, 1, 0), "corte al medio")
    return LimbSpec("brazo", "arm", fp, np.array([[15, 0, z0], [28, 0, z0], [28, 0, 76], [15, 0, 76]], float), 25.0)


@pytest.fixture(scope="module")
def std_hand(tmp_path_factory):
    hand, split = extract_standard_hand(_armed(5.0), ARM_PTS)
    path = str(tmp_path_factory.mktemp("estandar") / "mano.stl")
    save_standard_hand(path, hand, hand_meta(split, height_mm=100.0, source="origen"))
    return path


@pytest.fixture(scope="module")
def params():
    return dataclasses.replace(Params(), decimate=0, shoulder_pin=PinSpec(3.2, 2.0, 5.2, 1.2))


@pytest.fixture(scope="module")
def built(std_hand, params):
    res = P.build(_armed(3.5), [_arm(40.0)], params, SYM, hand=HandSpec(std_hand, tuple(ARM_PTS)))
    return res, P.validate(res, params, [_arm(40.0)])


def test_build_puts_the_standard_hand_on_both_arms(built):
    res, val = built
    assert val["ok"], val["problems"]
    rep = res.report["hand"]
    assert rep["miembro"] == "brazo" and rep["cobertura"] == pytest.approx(1.0, abs=1e-3)
    assert rep["radio_mano_mm"] == pytest.approx(rep["radio_brazo_mm"], rel=0.02)
    bl, br = res.pieces["brazo_izq"].bounding_box(), res.pieces["brazo_der"].bounding_box()
    assert bl[2] == pytest.approx(57.0 + 0.5 - 12.0, abs=0.3)             # mano de largo 12 desde la muñeca, hundida 0,5
    assert bl[2] == pytest.approx(br[2], abs=1e-6)                         # el lado derecho es el espejo del izquierdo
    assert bl[0] == pytest.approx(-br[3], abs=1e-6) and bl[3] == pytest.approx(-br[0], abs=1e-6)
    assert [a for a in val["avisos"] if a.startswith("mano")] == []
    assert val["pieces"]["torso"]["componentes"] == 1 and val["pieces"]["brazo_izq"]["componentes"] == 1


def test_build_hand_is_bigger_than_the_models_own(built, params):
    res, _ = built
    plain = P.build(_armed(3.5), [_arm(40.0)], params, SYM)
    assert "hand" not in plain.report
    assert res.pieces["brazo_izq"].volume() > plain.pieces["brazo_izq"].volume()     # esfera de radio 5 vs 3,5


def test_build_warns_when_the_arm_silhouette_cuts_the_hand(std_hand, params):
    arm = _arm(52.0)                                                       # la silueta empieza arriba de la mano
    res = P.build(_armed(3.5), [arm], params, SYM, hand=HandSpec(std_hand, tuple(ARM_PTS)))
    val = P.validate(res, params, [arm])
    assert res.report["hand"]["cobertura"] < 0.9
    assert any(a.startswith("mano: solo el") and "silueta" in a for a in val["avisos"])


def test_build_warns_without_any_arm(std_hand, params):
    res = P.build(_armed(3.5), [], params, SYM, hand=HandSpec(std_hand, tuple(ARM_PTS)))
    val = P.validate(res, params, [])
    assert res.report["hand"]["miembro"] is None
    assert any("ningún brazo" in a for a in val["avisos"])


def test_cli_build_with_standard_hand(tmp_path, std_hand, capsys):
    from afm.cli import main

    stl = str(tmp_path / "figura.stl")
    S.to_trimesh(_armed(3.5)).export(stl)
    proj = Project(params=dataclasses.replace(Params(), decimate=0, shoulder_pin=PinSpec(3.2, 2.0, 5.2, 1.2)),
                   symmetry=SYM, limbs=[_arm(40.0)])
    pf = str(tmp_path / "f.proyecto")
    proj.save(pf)
    out = str(tmp_path / "out")
    flat = [c for q in ARM_PTS for c in q]
    rc = main(["build", stl, pf, "--out", out, "--name", "fig", "--mano", std_hand, "--mano-puntos", *map(str, flat)])
    assert rc == 0, capsys.readouterr()
    saved = Project.load(str(tmp_path / "out" / "fig.proyecto"))                 # la mano queda en el proyecto guardado
    assert saved.hand is not None and saved.hand.path == std_hand and saved.hand.points[0] == ARM_PTS[0]


def test_cli_build_reports_bad_wrist_plane(tmp_path, std_hand, capsys):
    from afm.cli import main

    stl = str(tmp_path / "figura.stl")
    S.to_trimesh(_armed(3.5)).export(stl)
    pf = str(tmp_path / "f.proyecto")
    Project(symmetry=SYM, limbs=[_arm(40.0)]).save(pf)
    far = [60, 0, 70, 60, -2, 60, 60, 2, 60]                                     # plano fuera de la figura
    rc = main(["build", stl, pf, "--out", str(tmp_path / "o"), "--mano", std_hand, "--mano-puntos", *map(str, far)])
    assert rc == 2 and "ERROR (mano)" in capsys.readouterr().err


def test_cli_build_needs_wrist_points(tmp_path, std_hand):
    from afm.cli import main

    pf = str(tmp_path / "f.proyecto")
    Project(symmetry=SYM, limbs=[_arm(40.0)]).save(pf)
    with pytest.raises(SystemExit):
        main(["build", str(tmp_path / "x.stl"), pf, "--out", str(tmp_path / "o"), "--mano", std_hand])
