"""Mano de reemplazo: plano de muñeca por 3 puntos, corte con cierre de malla y mano estándar (motor, sin GUI)."""
import json
import os

import numpy as np
import pytest
import trimesh

from afm import solids as S
from afm.hand import (HAND_FORMAT, HandError, WristFrame, extract_standard_hand, hand_meta, load_standard_hand,
                      meta_path, place_hand, save_standard_hand, split_at_wrist, to_local)

M = S.Manifold
WRIST_X = 36.0
ARM_R = 3.0
HAND_R = 5.0
# puntos de la muñeca (plano x = WRIST_X): el 1.º arriba (lado «pulgar»), los otros dos abajo a ambos lados
PTS = [(WRIST_X, 0.0, 72.0), (WRIST_X, -2.5, 68.5), (WRIST_X, 2.5, 68.5)]


def _figure():
    """Figura simétrica de 100 mm con brazos horizontales en ±X que terminan en una esfera (la «mano»)."""
    parts = [M.cube((30, 16, 50), center=True).translate((0, 0, 55)),            # torso z 30..80
             M.cube((24, 14, 30), center=True).translate((0, 0, 15)),            # piernas
             M.sphere(10, 48).translate((0, 0, 90))]                              # cabeza hasta z = 100
    for sx in (+1, -1):
        arm = M.cylinder(30.0, ARM_R, ARM_R, 32).rotate((0, 90, 0)).translate((15, 0, 70))   # x 15..45
        parts.append(arm.mirror((1, 0, 0)) if sx < 0 else arm)
        parts.append(M.sphere(HAND_R, 32).translate((sx * 42.0, 0, 70)))
    return S.union(*parts)


@pytest.fixture(scope="module")
def body():
    return _figure()


@pytest.fixture(scope="module")
def extracted(body):
    return extract_standard_hand(body, PTS)


def test_frame_from_points():
    f = WristFrame.from_points(PTS)
    assert abs(abs(f.axis[0]) - 1.0) < 1e-9                                  # el plano x = const: eje ±X
    assert abs(np.linalg.norm(f.ref) - 1.0) < 1e-9 and abs(f.ref @ f.axis) < 1e-9
    assert f.ref[2] > 0.99                                                    # el punto 1 está arriba del centro
    assert np.allclose(f.center, np.mean(PTS, axis=0))


def test_frame_rejects_collinear_points():
    with pytest.raises(HandError, match="alineados"):
        WristFrame.from_points([(WRIST_X, 0, 70), (WRIST_X, 1, 70), (WRIST_X, 2, 70)])
    with pytest.raises(HandError, match="3 puntos"):
        WristFrame.from_points([(0, 0, 0), (1, 0, 0)])


def test_split_takes_the_small_side_as_hand_and_closes_both(body):
    sp = split_at_wrist(body, WristFrame.from_points(PTS))
    assert sp.hand_volume < 0.1 * sp.rest_volume
    assert sp.frame.axis @ np.array([1.0, 0, 0]) > 0.99                       # el eje apunta a la mano (+X)
    hb = sp.hand.bounding_box()
    assert hb[0] > WRIST_X - 1e-3 and hb[3] > 46.0                            # la mano está más allá de la muñeca
    for mf in (sp.hand, sp.rest):                                             # cerradas: manifold3d las da estancas
        tm = S.to_trimesh(mf)
        assert tm.is_watertight
    # el corte es el disco del brazo: radio equivalente ≈ ARM_R
    assert sp.face_radius == pytest.approx(ARM_R, rel=0.03)
    assert sp.hand_volume + sp.rest_volume == pytest.approx(body.volume(), rel=1e-4)


def test_split_is_the_same_for_any_point_order(body):
    a = split_at_wrist(body, WristFrame.from_points(PTS))
    b = split_at_wrist(body, WristFrame.from_points([PTS[0], PTS[2], PTS[1]]))   # normal opuesta
    assert a.hand_volume == pytest.approx(b.hand_volume, rel=1e-6)
    assert b.frame.axis @ np.array([1.0, 0, 0]) > 0.99                        # igual: siempre hacia la mano


def test_invert_takes_the_big_side(body):
    sp = split_at_wrist(body, WristFrame.from_points(PTS), invert=True)
    assert sp.hand_volume > 5 * split_at_wrist(body, WristFrame.from_points(PTS)).hand_volume
    assert sp.frame.axis @ np.array([1.0, 0, 0]) < -0.99


def test_plane_outside_the_figure_is_rejected(body):
    far = [(80.0, 0, 70), (80.0, -2, 68), (80.0, 2, 68)]
    with pytest.raises(HandError, match="no atraviesa"):
        split_at_wrist(body, WristFrame.from_points(far))


def test_plane_through_the_middle_is_not_a_hand(body):
    mid = [(0.0, 0, 70), (0.0, -2, 60), (0.0, 2, 60)]                         # plano de simetría: mitades iguales
    with pytest.raises(HandError, match="casi por la mitad"):
        split_at_wrist(body, WristFrame.from_points(mid))


def test_standard_hand_is_in_the_local_frame(extracted):
    hand, sp = extracted
    b = hand.bounding_box()
    assert b[2] == pytest.approx(0.0, abs=1e-3)                               # cara plana del corte en z = 0
    assert b[5] > 8.0                                                         # los dedos hacia +Z
    face_tri = S.to_trimesh(hand).triangles
    assert (np.abs(face_tri[:, :, 2]) < 1e-3).all(axis=1).any()               # hay una cara en z = 0
    # el punto 1 (referencia de giro) queda sobre +X local: la mano sale orientada igual en cualquier modelo
    p1_local = np.linalg.inv(sp.frame.matrix()) @ np.append(np.mean(PTS, axis=0) + (np.array(PTS[0]) - np.mean(PTS, axis=0)), 1)
    assert p1_local[0] > 0 and abs(p1_local[1]) < 1e-9 and abs(p1_local[2]) < 1e-6


def test_place_hand_roundtrip_restores_the_original_hand(extracted):
    hand, sp = extracted
    back = place_hand(hand, sp.frame)
    inter = S.intersection(back, sp.hand).volume()
    assert inter / sp.hand_volume > 0.999
    assert back.volume() == pytest.approx(sp.hand_volume, rel=1e-6)


def test_place_hand_does_not_rescale_and_can_be_mirrored(extracted):
    hand, sp = extracted
    other = WristFrame(center=np.array([20.0, 5.0, 60.0]), axis=np.array([0.0, 1.0, 0.0]), ref=np.array([0.0, 0.0, 1.0]))
    placed = place_hand(hand, other)
    assert placed.volume() == pytest.approx(hand.volume(), rel=1e-9)         # mm reales, sin escalar
    assert placed.bounding_box()[1] >= 5.0 - 1e-6                             # la mano crece hacia +Y (eje del marco)
    mir = S.mirror_x(placed)
    assert mir.volume() == pytest.approx(placed.volume(), rel=1e-9)
    assert mir.bounding_box()[3] <= -20.0 + 1e-6 + 6.0


def test_save_and_load_standard_hand(tmp_path, extracted):
    hand, sp = extracted
    path = str(tmp_path / "linea" / "mano.stl")
    save_standard_hand(path, hand, hand_meta(sp, height_mm=100.0, source="figura.stl"))
    assert os.path.exists(path) and os.path.exists(meta_path(path))
    loaded, meta = load_standard_hand(path)
    assert meta["formato"] == HAND_FORMAT and meta["lado_origen"] == "izquierda"
    assert meta["altura_figura_mm"] == 100.0 and meta["radio_muneca_mm"] == pytest.approx(ARM_R, rel=0.03)
    assert meta["largo_mm"] > 8.0
    assert loaded.volume() == pytest.approx(hand.volume(), rel=1e-3)
    assert S.to_trimesh(loaded).is_watertight


def test_load_rejects_foreign_metadata(tmp_path, extracted):
    hand, sp = extracted
    path = str(tmp_path / "m.stl")
    save_standard_hand(path, hand, hand_meta(sp))
    with open(meta_path(path)) as f:
        d = json.load(f)
    d["formato"] = "otra-cosa"
    with open(meta_path(path), "w") as f:
        json.dump(d, f)
    with pytest.raises(HandError, match="no es una mano estándar"):
        load_standard_hand(path)


def test_right_hand_is_flagged_in_metadata(body):
    mirrored = [(-x, y, z) for x, y, z in PTS]
    hand, sp = extract_standard_hand(body, mirrored)
    assert hand_meta(sp)["lado_origen"] == "derecha"


# ------------------------------------------------------------------------------------------------ CLI
def test_cli_extract_hand(tmp_path, body, capsys):
    from afm.cli import main
    from afm.project import Project

    stl = str(tmp_path / "figura.stl")
    S.to_trimesh(body).export(stl)
    proj = str(tmp_path / "f.proyecto")
    Project().save(proj)                               # ref_height = 100 y la figura ya mide 100: normalizar no la toca
    out = str(tmp_path / "linea")
    flat = [c for p in PTS for c in p]
    rc = main(["extraer-mano", stl, proj, "--puntos", *map(str, flat), "--out", out, "--name", "mano_guemes"])
    assert rc == 0
    hand, meta = load_standard_hand(os.path.join(out, "mano_guemes.stl"))
    assert meta["lado_origen"] == "izquierda" and meta["radio_muneca_mm"] == pytest.approx(ARM_R, rel=0.05)
    assert hand.bounding_box()[2] == pytest.approx(0.0, abs=1e-2)
    assert "AVISO" in capsys.readouterr().out          # 100 mm ≠ 130 mm de la línea


def test_cli_extract_hand_reports_bad_plane(tmp_path, body, capsys):
    from afm.cli import main
    from afm.project import Project

    stl = str(tmp_path / "figura.stl")
    S.to_trimesh(body).export(stl)
    proj = str(tmp_path / "f.proyecto")
    Project().save(proj)
    bad = [0, 0, 70, 0, -2, 60, 0, 2, 60]               # plano de simetría
    rc = main(["extraer-mano", stl, proj, "--puntos", *map(str, bad), "--out", str(tmp_path / "o")])
    assert rc == 2 and "ERROR" in capsys.readouterr().err


# ------------------------------------------------------------------ brazos colgando (el plano es infinito)
def _hanging_figure():
    """Como Güemes: brazos colgando a los costados, con la mano (separada de las piernas por un hueco) más abajo."""
    parts = [M.cube((30, 16, 50), center=True).translate((0, 0, 55)),            # torso z 30..80
             M.cube((24, 14, 30), center=True).translate((0, 0, 15)),            # piernas z 0..30
             M.sphere(10, 48).translate((0, 0, 90))]
    for sx in (+1, -1):
        arm = M.cylinder(34.0, ARM_R, ARM_R, 32).translate((sx * 24.0, 0, 40))   # brazo z 40..74, a 9 mm del torso
        hand = M.sphere(HAND_R, 32).translate((sx * 24.0, 0, 37))
        parts += [arm, hand, M.cylinder(4, 5, 5, 32).translate((sx * 24.0, 0, 70))]   # puño de la manga: hombro conectado
        parts.append(M.cube((10, 4, 4), center=True).translate((sx * 19.5, 0, 72)))    # hombro: une brazo y torso
    return S.union(*parts)


HANG_PTS = [(24.0, 3.0, 41.5), (21.5, -2.0, 41.5), (26.5, -2.0, 41.5)]       # plano z = 41.5, horizontal


def test_hanging_arm_horizontal_plane_isolates_only_the_hand():
    fig = _hanging_figure()
    sp = split_at_wrist(fig, WristFrame.from_points(HANG_PTS))
    assert sp.frame.axis @ np.array([0.0, 0.0, -1.0]) > 0.99                   # la mano está ABAJO del plano
    assert sp.hand_volume < 0.03 * fig.volume()                                # no arrastra piernas ni pelvis
    hb = sp.hand.bounding_box()
    assert hb[3] < 30.0 and hb[0] > 18.0 and hb[5] <= 41.5 + 1e-3              # solo la mano izquierda
    assert S.to_trimesh(sp.hand).is_watertight and S.to_trimesh(sp.rest).is_watertight
    assert sp.hand_volume + sp.rest_volume == pytest.approx(fig.volume(), rel=1e-4)


def test_hanging_arm_standard_hand_has_fingers_toward_plus_z():
    hand, sp = extract_standard_hand(_hanging_figure(), HANG_PTS)
    b = hand.bounding_box()
    assert b[2] == pytest.approx(0.0, abs=1e-3) and b[5] > 4.0
    assert hand.volume() == pytest.approx(sp.hand_volume, rel=1e-9)
