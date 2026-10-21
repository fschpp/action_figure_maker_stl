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
        apply_hand(target_half, HandSpec(path, tuple(PTS), join="pegamento"))
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


# ------------------------------------------------------------------ mano articulada («giro»): pieza aparte con pasador
from afm.hand import HAND_NAME, fit_hand_pin  # noqa: E402
from afm.joints import JointSpec  # noqa: E402
from afm.params import HingeSpec  # noqa: E402

SPIN_PARAMS = dataclasses.replace(Params(), decimate=0, shoulder_pin=PinSpec(3.2, 2.0, 5.2, 1.2),
                                  clearance=0.3, min_wall=0.8)           # muñeca de Ø6: con 0,5 / 1,2 el pasador no llega a 2 mm


def _swivel(path, **kw):
    return HandSpec(path, tuple(ARM_PTS), join="giro", **kw)


def test_split_rest_has_no_zero_thickness_skirt(target_half):
    # restar la mano del cuerpo dejaba una «falda» de espesor cero pegada al corte (caras coincidentes de la pared del brazo)
    sp = split_at_wrist(_armed(3.5).trim_by_plane((1.0, 0.0, 0.0), 0.0), WristFrame.from_points(ARM_PTS))
    assert all(c.volume() > 1e-6 for c in sp.rest.decompose())
    assert sp.rest.bounding_box()[2] == pytest.approx(0.0, abs=1e-6)                  # el pie del muñeco: nada más abajo
    near_arm = S.to_trimesh(sp.rest).vertices
    near_arm = near_arm[(near_arm[:, 0] > 17) & (near_arm[:, 0] < 25) & (near_arm[:, 2] > 30)]
    assert near_arm[:, 2].min() > 56.9 or near_arm[:, 2].min() < 43.0                  # ni un vértice del brazo bajo la muñeca


def test_swivel_hand_is_a_separate_piece_with_a_pin(std_hand):
    half = _armed(3.5).trim_by_plane((1.0, 0.0, 0.0), 0.0)
    hb = apply_hand(half, _swivel(std_hand), params=SPIN_PARAMS)
    assert hb.pocket is not None and hb.pin is not None and hb.info["union"] == "giro"
    assert len(hb.hand.decompose()) == 1                                                # mano + pasador, una sola pieza
    z = hb.hand.bounding_box()
    assert z[2] == pytest.approx(56.7 - 12.0, abs=0.1)                                  # la cara de la mano a `clearance` del corte (57)
    assert z[5] == pytest.approx(56.7 + hb.pin.shaft_len + hb.pin.head_h, abs=0.1)      # el pasador entra en el brazo
    assert hb.body.volume() == pytest.approx(split_at_wrist(half, WristFrame.from_points(ARM_PTS)).rest_volume, rel=1e-6)
    assert hb.info["pasador"]["entra"] and hb.info["solape_mm"] == 0.0
    assert hb.pin_origin == pytest.approx([21.0, 0.0, 56.7], abs=1e-6)


def test_swivel_pin_shrinks_to_fit_a_thin_wrist(std_hand):
    half = _armed(3.5).trim_by_plane((1.0, 0.0, 0.0), 0.0)
    easy = apply_hand(half, _swivel(std_hand), params=dataclasses.replace(SPIN_PARAMS, clearance=0.2, min_wall=0.4))
    tight = apply_hand(half, _swivel(std_hand), params=SPIN_PARAMS)
    assert easy.info["pasador"]["escala"] > tight.info["pasador"]["escala"]            # pared más fina → pasador más grande
    assert tight.info["pasador"]["escala"] < 1.0
    assert any("se achicó" in a for a in tight.info["avisos"])
    assert tight.pin.shaft_d / tight.pin.head_d == pytest.approx(SPIN_PARAMS.hand_pin.shaft_d / SPIN_PARAMS.hand_pin.head_d, rel=1e-6)


def test_swivel_reports_when_the_pin_does_not_fit(std_hand):
    half = _armed(3.5).trim_by_plane((1.0, 0.0, 0.0), 0.0)
    hb = apply_hand(half, _swivel(std_hand), params=dataclasses.replace(SPIN_PARAMS, min_wall=3.0))   # pared imposible en un brazo de radio 3
    assert hb.info["pasador"]["entra"] is False
    assert any("no entra" in a for a in hb.info["avisos"])


def test_swivel_pin_fit_is_cached_and_yaw_does_not_refit(std_hand):
    half = _armed(3.5).trim_by_plane((1.0, 0.0, 0.0), 0.0)
    cache: dict = {}
    a = apply_hand(half, _swivel(std_hand), params=SPIN_PARAMS, pin_cache=cache)
    assert len(cache) == 1
    b = apply_hand(half, _swivel(std_hand, yaw=45.0), params=SPIN_PARAMS, pin_cache=cache)
    assert len(cache) == 1 and a.pin == b.pin                                           # el giro de la mano no cambia el pasador


def test_swivel_pin_fit_respects_the_skin_of_the_arm(std_hand):
    half = _armed(3.5).trim_by_plane((1.0, 0.0, 0.0), 0.0)
    sp = split_at_wrist(half, WristFrame.from_points(ARM_PTS))
    spec, k, fits = fit_hand_pin(sp.rest, sp.frame, SPIN_PARAMS)
    assert fits
    # con ese pasador, la cavidad + pared queda dentro del brazo: no sobresale
    from afm.pins import mushroom_pin
    zone = S.dilate(mushroom_pin(sp.frame.center - sp.frame.axis * 0.0 + sp.frame.axis * 0.3, -sp.frame.axis, spec), 0.3 + 0.8)
    arm_side = S.halfspace(Plane.from_normal(sp.frame.center, -sp.frame.axis))
    assert S.difference(S.intersection(zone, arm_side), sp.rest).volume() <= 0.2


@pytest.fixture(scope="module")
def swivel_built(std_hand):
    res = P.build(_armed(3.5), [_arm(40.0)], SPIN_PARAMS, SYM, hand=_swivel(std_hand))
    return res, P.validate(res, SPIN_PARAMS, [_arm(40.0)])


def test_swivel_build_makes_hand_pieces_that_spin_free(swivel_built):
    res, val = swivel_built
    assert val["ok"], val["problems"]
    assert {"mano_izq", "mano_der", "brazo_izq", "brazo_der"} <= set(res.pieces)
    assert res.report["hand"]["pieza"] == HAND_NAME and res.report["hand"]["cobertura"] == pytest.approx(1.0, abs=1e-3)
    m = val["mano"]
    assert m["giro_libre"] and m["cautiva"] and m["holgura_mm"] >= 0.9 * SPIN_PARAMS.clearance
    assert val["pieces"]["brazo_izq"]["componentes"] == 1 and val["pieces"]["mano_izq"]["componentes"] == 1
    bl, br = res.pieces["mano_izq"].bounding_box(), res.pieces["mano_der"].bounding_box()
    assert bl[0] == pytest.approx(-br[3], abs=1e-6) and bl[2] == pytest.approx(br[2], abs=1e-6)   # el derecho es el espejo


def test_swivel_arm_has_the_cavity_and_is_not_welded_to_the_hand(swivel_built, std_hand):
    res, _ = swivel_built
    solid = P.build(_armed(3.5), [_arm(40.0)], SPIN_PARAMS, SYM)                        # sin mano estándar: brazo con su mano
    arm = res.pieces["brazo_izq"]
    assert arm.bounding_box()[2] == pytest.approx(57.0, abs=0.05)                       # termina en el plano de muñeca
    assert (arm ^ res.pieces["mano_izq"]).volume() < 1e-6
    assert float(arm.min_gap(res.pieces["mano_izq"], 5.0)) >= 0.9 * SPIN_PARAMS.clearance
    assert solid.pieces["brazo_izq"].bounding_box()[2] < 50.0                           # el brazo original sí llevaba la mano


def test_swivel_spin_is_free_at_any_angle(swivel_built):
    res, _ = swivel_built
    h = res.hand
    mano = res.pieces["mano_izq"]
    arm = res.pieces["brazo_izq"]
    for ang in (37.0, 90.0, 180.0, 271.0):
        rot = S.rotate_about_axis(mano, h["center"], h["axis"], ang)
        assert (rot ^ arm).volume() < 1e-6


def test_swivel_hand_travels_in_posed_pieces_and_limb_names(swivel_built):
    res, _ = swivel_built
    assert P._limb_piece_names(res, "brazo") == ["brazo", HAND_NAME]
    posed = P.posed_pieces(res, 0.0)
    assert {"mano_izq", "mano_der"} <= set(posed)
    assert posed["mano_izq"].bounding_box() == pytest.approx(res.pieces["mano_izq"].bounding_box(), abs=1e-6)


def test_swivel_validate_flags_a_silhouette_that_cuts_the_pocket(std_hand):
    arm = _arm(59.0)                                                                    # la silueta empieza arriba del pasador
    res = P.build(_armed(3.5), [arm], SPIN_PARAMS, SYM, hand=_swivel(std_hand))
    val = P.validate(res, SPIN_PARAMS, [arm])
    assert res.report["hand"]["cobertura"] < 0.9
    assert any(pr.startswith("mano:") and "cavidad" in pr for pr in val["problems"])


def test_swivel_without_any_arm_is_a_problem(std_hand):
    res = P.build(_armed(3.5), [], SPIN_PARAMS, SYM, hand=_swivel(std_hand))
    val = P.validate(res, SPIN_PARAMS, [])
    assert res.hand is None and "mano_izq" not in res.pieces
    assert any("ningún brazo" in pr for pr in val["problems"])


def test_swivel_default_params_flag_a_pin_too_thin_for_this_wrist(std_hand):
    res = P.build(_armed(3.5), [_arm(40.0)], dataclasses.replace(SPIN_PARAMS, clearance=0.5, min_wall=1.2), SYM,
                  hand=_swivel(std_hand))
    val = P.validate(res, dataclasses.replace(SPIN_PARAMS, clearance=0.5, min_wall=1.2), [_arm(40.0)])
    assert not val["ok"] and any("no se imprime" in pr for pr in val["problems"])        # vástago de Ø1,5: no se imprime


def test_swivel_hand_rides_the_forearm_with_an_elbow(std_hand):
    j = JointSpec(name="codo", limb="brazo", kind="elbow", plane=Plane.from_normal((0, 0, 66.0), (0, 0, 1), "art"),
                  axis_center=(21.0, 0.0, 69.0), hinge=HingeSpec(pin_d=2.0, pin_len=0.6, tongue_t=2.0, end_r=3.0))
    res = P.build(_armed(3.5), [_arm(40.0)], SPIN_PARAMS, SYM, joints=[j], hand=_swivel(std_hand))
    val = P.validate(res, SPIN_PARAMS, [_arm(40.0)], joints=[j])
    assert res.hand["limb"] == "brazo" and P._limb_piece_names(res, "brazo") == ["brazo", "antebrazo", HAND_NAME]
    assert not [pr for pr in val["problems"] if pr.startswith("mano")], val["problems"]
    assert val["mano"]["giro_libre"] and val["mano"]["cautiva"]
    assert float(res.pieces["antebrazo_izq"].min_gap(res.pieces["mano_izq"], 5.0)) >= 0.9 * SPIN_PARAMS.clearance
    posed = P.posed_pieces(res, 0.0, {"codo": 45.0})                                    # al flexionar, la mano acompaña al antebrazo
    dist = posed["antebrazo_izq"]
    assert float(dist.min_gap(posed["mano_izq"], 5.0)) >= 0.9 * SPIN_PARAMS.clearance


def test_params_roundtrip_with_hand_pin():
    p = dataclasses.replace(Params(), hand_pin=PinSpec(2.6, 2.4, 4.0, 1.0))
    assert Params.from_dict(p.to_dict()).hand_pin == PinSpec(2.6, 2.4, 4.0, 1.0)
    old = Params().to_dict()
    old.pop("hand_pin")
    assert Params.from_dict(old).hand_pin == Params().hand_pin                         # proyectos viejos sin hand_pin


def test_cli_build_with_swivel_hand(tmp_path, std_hand):
    from afm.cli import main

    stl = str(tmp_path / "figura.stl")
    S.to_trimesh(_armed(3.5)).export(stl)
    pf = str(tmp_path / "f.proyecto")
    Project(params=SPIN_PARAMS, symmetry=SYM, limbs=[_arm(40.0)]).save(pf)
    flat = [c for q in ARM_PTS for c in q]
    rc = main(["build", stl, pf, "--out", str(tmp_path / "o"), "--name", "fig", "--mano", std_hand, "--mano-puntos",
               *map(str, flat), "--mano-union", "giro"])
    assert rc == 0
    assert Project.load(str(tmp_path / "o" / "fig.proyecto")).hand.join == "giro"
    names = {f.name for f in (tmp_path / "o" / "piezas").iterdir()}
    assert any("mano_izq" in n for n in names) and any("mano_der" in n for n in names)           # la mano sale como pieza aparte
