"""`Catalog.cut_wrist` / `afm catalogo cortar-muneca`: cortarle la mano a un brazo que ya está en el catálogo."""
import numpy as np
import pytest

from afm import solids as S
from afm.catalog import Catalog, CatalogError
from afm.cli import main
from afm.extract import extract_all
from afm.ports import coupling_transform
from afm.project import demo_project
from afm.testfigure import make_doll

WRIST = [(22.0, 0.0, 50.0), (14.0, 0.0, 50.0), (18.0, 4.0, 50.0)]      # plano z = 50 del brazo del muñeco (z 45..75)


@pytest.fixture(scope="module")
def doll():
    return S.to_manifold(make_doll())


@pytest.fixture
def cat(tmp_path, doll):
    """Catálogo con el brazo del muñeco CON la mano pegada (extraído sin puntos de muñeca)."""
    c = Catalog.create(str(tmp_path / "linea"), "test-100", 100.0, 0.4)
    r = extract_all(doll, demo_project(), "muneco", places=["brazo"])
    assert set(r.pieces[0].meta.puertos) == {"hombro"}
    c.add_piece(r.pieces[0].mesh, r.pieces[0].meta)
    return c


def vol(mesh):
    return abs(float(mesh.volume))


def test_cut_wrist_gives_arm_without_hand_and_a_separate_hand(cat):
    old_meta, old_mesh = cat.load("brazo-muneco-01")
    res = cat.cut_wrist("brazo-muneco-01", WRIST)
    assert (res.from_ref, res.arm.ref, res.hand.ref) == ("brazo-muneco-01@1", "brazo-muneco-01@2", "mano-muneco-01@1")
    arm_meta, arm = cat.load("brazo-muneco-01@2")
    hand_meta, hand = cat.load("mano-muneco-01")
    assert set(arm_meta.puertos) == {"hombro", "muneca"} and arm_meta.puertos["muneca"].rol == "toma"
    assert hand_meta.tipo == "mano" and hand_meta.puertos["muneca"].rol == "enchufe"
    assert arm_meta.estado == "borrador" and arm_meta.problems() == [] and hand_meta.problems() == []
    assert arm.is_watertight and hand.is_watertight
    assert arm.bounds[0][2] == pytest.approx(50.0, abs=1e-3)                       # sin la mano
    # el acople deja la mano donde estaba
    T = coupling_transform(hand_meta.puertos["muneca"].frame, arm_meta.puertos["muneca"].frame)
    placed = hand.copy()
    placed.apply_transform(T)
    assert placed.bounds[1][2] == pytest.approx(50.0, abs=1e-3) and placed.bounds[0][2] == pytest.approx(45.0, abs=1e-3)
    assert vol(arm) + vol(hand) == pytest.approx(vol(old_mesh), rel=1e-3)
    # lo demás del brazo no cambia: hombro, lado, altura y la versión 1 intacta
    assert arm_meta.puertos["hombro"].to_dict() == old_meta.puertos["hombro"].to_dict()
    assert (arm_meta.lado, arm_meta.altura_figura_mm, arm_meta.espejable) == (old_meta.lado, old_meta.altura_figura_mm, old_meta.espejable)
    again_meta, _ = cat.load("brazo-muneco-01@1")
    assert again_meta.sha256 == old_meta.sha256 and "muneca" not in again_meta.puertos
    assert cat.verify().ok


def test_same_result_as_extracting_with_the_wrist_points(cat, doll):
    ref = {e.meta.tipo: e for e in extract_all(doll, demo_project(), "muneco", places=["brazo", "mano"], wrist_points=WRIST).pieces}
    cat.cut_wrist("brazo-muneco-01", WRIST)
    _m, arm = cat.load("brazo-muneco-01@2")
    _h, hand = cat.load("mano-muneco-01")
    assert vol(arm) == pytest.approx(vol(ref["brazo"].mesh), rel=1e-3) and vol(hand) == pytest.approx(vol(ref["mano"].mesh), rel=1e-3)
    mw = cat.meta("brazo-muneco-01@2").puertos["muneca"].frame
    rw = ref["brazo"].meta.puertos["muneca"].frame
    assert np.allclose(mw.center, rw.center, atol=1e-3) and np.allclose(mw.axis, rw.axis, atol=1e-6) and np.allclose(mw.ref, rw.ref, atol=1e-6)


def test_points_on_the_negative_side_are_mirrored(cat):
    res = cat.cut_wrist("brazo-muneco-01", [(-x, y, z) for x, y, z in WRIST])
    assert any("espejaron" in w for w in res.warnings)
    assert cat.meta("brazo-muneco-01@2").puertos["muneca"].frame.center[0] > 0


def test_existing_hand_id_needs_new_version_and_nothing_is_written_before(cat):
    cat.cut_wrist("brazo-muneco-01", WRIST)
    with pytest.raises(CatalogError, match="ya existe"):
        cat.cut_wrist("brazo-muneco-01@1", WRIST)                       # el brazo v1 aún no tiene muñeca, pero la mano sí existe
    assert cat.versions("brazo-muneco-01") == [1, 2] and cat.versions("mano-muneco-01") == [1]
    res = cat.cut_wrist("brazo-muneco-01@1", WRIST, hand_id="mano-otra-01")
    assert res.hand.ref == "mano-otra-01@1" and res.arm.ref == "brazo-muneco-01@3"
    res = cat.cut_wrist("brazo-muneco-01@1", WRIST, new_hand_version=True)
    assert res.hand.ref == "mano-muneco-01@2"
    assert any("versiones más nuevas" in w for w in res.warnings)


def test_errors_write_nothing(cat):
    with pytest.raises(CatalogError, match="muñeca"):
        cat.cut_wrist("brazo-muneco-01", [(0, 0, 500), (1, 0, 500), (0, 1, 500)])         # el plano no toca el brazo
    with pytest.raises(CatalogError, match="3 puntos"):
        cat.cut_wrist("brazo-muneco-01", WRIST[:2])
    with pytest.raises(CatalogError, match="inválido"):
        cat.cut_wrist("brazo-muneco-01", WRIST, hand_id="Mano Mala")
    assert cat.versions("brazo-muneco-01") == [1] and cat.versions("mano-muneco-01") == []
    cat.cut_wrist("brazo-muneco-01", WRIST)
    with pytest.raises(CatalogError, match="ya tiene el puerto «muneca»"):
        cat.cut_wrist("brazo-muneco-01@2", WRIST)
    with pytest.raises(CatalogError, match="no hay ninguna pieza"):
        cat.cut_wrist("brazo-no-existe-01", WRIST)


def test_only_arms_can_be_cut(tmp_path, doll):
    c = Catalog.create(str(tmp_path / "l"), "test-100", 100.0, 0.4)
    r = extract_all(doll, demo_project(), "muneco", places=["pierna"])
    c.add_piece(r.pieces[0].mesh, r.pieces[0].meta)
    with pytest.raises(CatalogError, match="es para un «brazo»"):
        c.cut_wrist("pierna-muneco-01", WRIST)


def test_cli_cortar_muneca(cat, capsys):
    root = cat.root
    assert main(["catalogo", "cortar-muneca", root, "brazo-muneco-01", "--muneca", *map(str, np.asarray(WRIST).ravel())]) == 0
    out = capsys.readouterr().out
    assert "brazo-muneco-01@2" in out and "mano-muneco-01@1" in out and "siguen armando igual" in out
    assert main(["catalogo", "verificar", root]) == 0
    # segunda corrida sobre el mismo brazo: la mano ya existe → error claro (2), nada nuevo
    assert main(["catalogo", "cortar-muneca", root, "brazo-muneco-01@1", "--muneca", *map(str, np.asarray(WRIST).ravel())]) == 2
    assert "ya existe" in capsys.readouterr().err
    assert Catalog.open(root).versions("brazo-muneco-01") == [1, 2]


def test_recipe_assembles_with_the_cut_arm_and_a_separate_hand(tmp_path, doll):
    """El problema de origen: un brazo con la mano pegada no admite una pieza «mano»; cortada la muñeca, sí."""
    from afm.recipe import Recipe, assemble

    c = Catalog.create(str(tmp_path / "completo"), "test-100", 100.0, 0.4)
    for e in extract_all(doll, demo_project(), "muneco").pieces:             # figura entera, sin muñeca: el brazo trae su mano
        c.add_piece(e.mesh, e.meta)
    donor = {e.meta.tipo: e for e in extract_all(doll, demo_project(), "otro", wrist_points=WRIST).pieces}["mano"]
    c.add_piece(donor.mesh, donor.meta)                                      # una mano estándar de la línea
    lugares = {"cabeza": "cabeza-muneco-01@1", "torso": "torso-muneco-01@1", "pelvis": None, "brazo": "brazo-muneco-01@1",
               "pierna": "pierna-muneco-01@1", "mano": "mano-otro-01@1"}
    ajustes = {"mano": {"yaw": 0, "union": "solape"}}
    with pytest.raises(ValueError, match="plano de muñeca"):
        assemble(Recipe("muneco", "test-100", lugares, {}, ajustes), c.root)
    res = c.cut_wrist("brazo-muneco-01", WRIST)
    asm = assemble(Recipe("muneco", "test-100", {**lugares, "brazo": res.arm.ref}, {}, ajustes), c.root)
    assert "radio_mano_mm" in asm.res.report["hand"]
