"""Ganchos de piezas de catálogo en `pipeline.build` (Fase 4 del plan de piezas intercambiables), con el muñeco."""
import numpy as np
import pytest

from afm import solids as S
from afm.assembly import AssemblyError, PartChoice, place_part
from afm.extract import extract_all
from afm.limbs import separate_limb
from afm.pipeline import build, validate
from afm.project import demo_project
from afm.testfigure import make_doll


@pytest.fixture(scope="module")
def doll():
    return S.to_manifold(make_doll())


@pytest.fixture(scope="module")
def project():
    return demo_project()


@pytest.fixture(scope="module")
def extracted(doll, project):
    r = extract_all(doll, project, "muneco", figure="doll")
    return {e.meta.tipo: e for e in r.pieces}


@pytest.fixture(scope="module")
def direct(doll, project):
    return build(doll, project.limbs, project.params, head=project.head)


def vol(pieces, name):
    return float(pieces[name].volume())


def test_without_parts_there_is_no_report_entry(direct):
    assert "piezas" not in direct.report


@pytest.mark.parametrize("name,kind", [("pierna", "leg"), ("brazo", "arm")])
def test_part_in_its_own_port_is_identity(doll, project, extracted, name, kind):
    """La pieza extraída de la figura, puesta en su propio lugar, queda donde estaba (marco y cara)."""
    spec = next(l for l in project.limbs if l.name == name)
    _limb, face = separate_limb(doll.trim_by_plane((1.0, 0.0, 0.0), 0.0), spec, project.params.burr)
    pp = place_part(PartChoice(extracted[name].meta, extracted[name].mesh), kind, face, project.params, 100.0)
    assert np.allclose(pp.T, np.eye(4), atol=1e-6)
    assert np.allclose(pp.face.center, face.center, atol=1e-6)
    assert pp.face.triangles is not None and pp.face.area > 10
    assert pp.compat.nivel in ("ok", "aviso") and pp.compat.iou == pytest.approx(1.0, abs=0.02)


def test_wrong_kind_is_rejected(doll, project, extracted):
    spec = next(l for l in project.limbs if l.name == "pierna")
    _l, face = separate_limb(doll.trim_by_plane((1.0, 0.0, 0.0), 0.0), spec, project.params.burr)
    with pytest.raises(AssemblyError):
        place_part(PartChoice(extracted["brazo"].meta, extracted["brazo"].mesh), "leg", face, project.params)


def test_unknown_limb_name_is_rejected(doll, project, extracted):
    with pytest.raises(ValueError, match="no existe"):
        build(doll, project.limbs, project.params, head=project.head,
              parts={"cola": PartChoice(extracted["pierna"].meta, extracted["pierna"].mesh)})


def test_leg_and_arm_placed_in_their_own_ports_equal_the_direct_build(doll, project, extracted, direct):
    parts = {n: PartChoice(extracted[n].meta, extracted[n].mesh) for n in ("pierna", "brazo")}
    res = build(doll, project.limbs, project.params, head=project.head, parts=parts)
    assert set(res.pieces) == set(direct.pieces)
    for name in direct.pieces:
        # El torso admite algo más: la dilatación de Minkowski de manifold3d depende de la triangulación de la malla
        # (la misma geometría da 0,3 % de diferencia en el barrido), así que la holgura real varía ≈ 0,01 mm.
        rel = 0.005 if name == "torso" else 0.002
        assert vol(res.pieces, name) == pytest.approx(vol(direct.pieces, name), rel=rel), name
    assert res.report["piezas"]["pierna"]["id"] == extracted["pierna"].meta.id
    assert res.report["piezas"]["brazo"]["lugar"] == "hombro"
    assert res.report["limbs"]["pierna"]["pin_center"] == pytest.approx(direct.report["limbs"]["pierna"]["pin_center"], abs=1e-3)


def test_validation_of_the_placed_build_is_as_good_as_the_direct_one(doll, project, extracted, direct):
    parts = {n: PartChoice(extracted[n].meta, extracted[n].mesh) for n in ("pierna", "brazo")}
    res = build(doll, project.limbs, project.params, head=project.head, parts=parts)
    v0 = validate(direct, project.params, project.limbs)
    v1 = validate(res, project.params, project.limbs)
    assert v1["ok"] == v0["ok"]
    assert v1["problems"] == v0["problems"]
    gap0, gap1 = min(v0["gaps"].values()), min(v1["gaps"].values())
    assert gap1 >= 0.9 * project.params.clearance and gap1 >= gap0 - 0.02


# --- gancho 3 (§4.3): la mano estándar va en el puerto `muneca` del brazo de catálogo -----------------------------------

WRIST = [(22.0, 0.0, 50.0), (14.0, 0.0, 50.0), (18.0, 4.0, 50.0)]


@pytest.fixture(scope="module")
def wrist_pieces(doll, project):
    r = extract_all(doll, project, "muneco", wrist_points=WRIST, figure="doll")
    return {e.meta.tipo: e for e in r.pieces}


def _choice(e):
    return PartChoice(e.meta, e.mesh)


def test_catalog_hand_on_catalog_arm_rebuilds_the_original_arm(doll, project, wrist_pieces, direct):
    res = build(doll, project.limbs, project.params, head=project.head,
                parts={"brazo": _choice(wrist_pieces["brazo"]), "mano": _choice(wrist_pieces["mano"])})
    assert res.report["hand"]["escalon_mm"] == pytest.approx(0.0, abs=0.05)
    assert res.report["hand"]["miembro"] == "brazo" and res.report["hand"]["cobertura"] == pytest.approx(1.0, abs=1e-3)
    assert vol(res.pieces, "brazo_izq") == pytest.approx(vol(direct.pieces, "brazo_izq"), rel=0.03)
    # brazo sin mano + mano soldada = el brazo de siempre, salvo el solape: la mano se hunde 0,5 mm (πr²·0,5 ≈ 25 mm³ de menos)
    assert vol(res.pieces, "torso") == pytest.approx(vol(direct.pieces, "torso"), rel=0.01)
    assert not any("volumen" in a for a in res.report["hand"]["avisos"])          # sin mano original no hay comparación de volumen
    val = validate(res, project.params, project.limbs)
    assert val["ok"], val["problems"]


def test_arm_without_hand_is_reported(doll, project, wrist_pieces):
    res = build(doll, project.limbs, project.params, head=project.head, parts={"brazo": _choice(wrist_pieces["brazo"])})
    assert "hand" not in res.report
    assert any("sin mano" in a for a in res.report["piezas"]["brazo"]["avisos"])
    assert any("sin mano" in a for a in validate(res, project.params, project.limbs)["avisos"])


def test_catalog_hand_must_be_a_hand(doll, project, wrist_pieces):
    with pytest.raises(ValueError, match="mano"):
        build(doll, project.limbs, project.params, head=project.head,
              parts={"brazo": _choice(wrist_pieces["brazo"]), "mano": _choice(wrist_pieces["pierna"])})


def test_catalog_hand_needs_wrist_points_if_the_arm_is_the_models(doll, project, wrist_pieces):
    with pytest.raises(ValueError, match="plano de muñeca"):
        build(doll, project.limbs, project.params, head=project.head, parts={"mano": _choice(wrist_pieces["mano"])})


def test_catalog_hand_swivel_is_a_separate_piece_with_its_pocket(doll, project, wrist_pieces):
    """Unión «giro» por el puerto: la mano es otra pieza, no toca el brazo y queda a ≈ `clearance`. (La validación de giro
    libre no se exige acá: la «mano» del muñeco es un cilindro recortado por la silueta, descentrado respecto de la muñeca.)"""
    from afm.hand import HandSpec

    res = build(doll, project.limbs, project.params, head=project.head, hand=HandSpec(path="", join="giro"),
                parts={"brazo": _choice(wrist_pieces["brazo"]), "mano": _choice(wrist_pieces["mano"])})
    assert {"mano_izq", "mano_der"} <= set(res.pieces) and res.report["hand"]["union"] == "giro"
    assert res.report["hand"]["pieza"] == "mano" and res.report["hand"]["cobertura"] == pytest.approx(1.0, abs=1e-3)
    arm, hand = res.pieces["brazo_izq"], res.pieces["mano_izq"]
    assert (arm ^ hand).volume() < 1e-6
    assert arm.min_gap(hand, 5.0) == pytest.approx(project.params.clearance, abs=0.1)
    assert res.hand is not None and res.hand["limb"] == "brazo"


# --- gancho 4 (§4.3): cabeza de catálogo por el puerto `cuello` -------------------------------------------------------------

def test_catalog_head_in_its_own_port_is_identity(doll, project, extracted, direct):
    res = build(doll, project.limbs, project.params, head=project.head, parts={"cabeza": _choice(extracted["cabeza"])})
    assert vol(res.pieces, "cabeza") == pytest.approx(vol(direct.pieces, "cabeza"), rel=0.005)
    assert np.allclose(res.pieces["cabeza"].bounding_box(), direct.pieces["cabeza"].bounding_box(), atol=0.05)
    r = res.report["piezas"]["cabeza"]
    assert r["id"] == extracted["cabeza"].meta.id and r["lugar"] == "cuello" and r["compat"]["nivel"] in ("ok", "aviso")
    val = validate(res, project.params, project.limbs)
    assert val["ok"], val["problems"]
    assert val["piezas"]["cabeza"]["pieza"] == extracted["cabeza"].meta.ref


def test_catalog_head_is_placed_by_its_port_not_by_its_coordinates(doll, project, extracted, direct):
    """La cabeza guardada en otro marco (girada 30° y trasladada, con su puerto llevado igual) se acopla al mismo cuello."""
    import dataclasses

    import trimesh

    e = extracted["cabeza"]
    T = trimesh.transformations.rotation_matrix(np.radians(30.0), [0, 0, 1], [3.0, -2.0, 0.0])
    T[:3, 3] += [10.0, 5.0, -7.0]
    mesh = e.mesh.copy()
    mesh.apply_transform(T)
    meta = dataclasses.replace(e.meta, puertos={k: v.transformed(T) for k, v in e.meta.puertos.items()})
    res = build(doll, project.limbs, project.params, head=project.head, parts={"cabeza": PartChoice(meta, mesh)})
    assert vol(res.pieces, "cabeza") == pytest.approx(vol(direct.pieces, "cabeza"), rel=0.005)
    assert np.allclose(res.pieces["cabeza"].bounding_box(), direct.pieces["cabeza"].bounding_box(), atol=0.05)


def test_catalog_head_needs_the_neck_plane_and_a_head_piece(doll, project, extracted):
    with pytest.raises(ValueError, match="cuello"):
        build(doll, project.limbs, project.params, head=None, parts={"cabeza": _choice(extracted["cabeza"])})
    with pytest.raises(ValueError, match="cabeza"):
        build(doll, project.limbs, project.params, head=project.head, parts={"cabeza": _choice(extracted["pierna"])})


# ---------------------------------------------------------------- gancho 1: huésped sintetizado
def _choices(extracted, *tipos):
    return {t: PartChoice(extracted[t].meta, extracted[t].mesh) for t in tipos}


def test_host_needs_a_torso(extracted):
    from afm.assembly import synthesize_host
    with pytest.raises(AssemblyError):
        synthesize_host(_choices(extracted, "brazo", "pierna"))
    with pytest.raises(AssemblyError):
        synthesize_host({"torso": PartChoice(extracted["brazo"].meta, extracted["brazo"].mesh)})


def test_synthesized_host_pieces(extracted, project):
    from afm.assembly import synthesize_host
    h = synthesize_host(_choices(extracted, "torso", "brazo", "pierna", "cabeza"), project.params)
    assert {l.name for l in h.limbs} == {"brazo", "pierna"} and set(h.faces) == {"brazo", "pierna"}
    assert h.head is not None and h.waist is None
    assert set(h.parts) == {"brazo", "pierna", "cabeza"}
    assert all(f.triangles is not None and f.area > 10 for f in h.faces.values())


def test_built_only_from_parts_matches_direct(extracted, project, direct):
    from afm.pipeline import build_from_parts
    res = build_from_parts(_choices(extracted, "torso", "brazo", "pierna", "cabeza"), project.params)
    assert set(res.pieces) == set(direct.pieces)
    for nm in direct.pieces:
        tol = 0.02 if nm == "torso" else 0.005
        assert vol(res.pieces, nm) == pytest.approx(vol(direct.pieces, nm), rel=tol), nm
    assert set(res.report["piezas"]) == {"brazo", "pierna", "cabeza"}
    v = validate(res, project.params, project.limbs)
    assert v["ok"], v


def test_built_from_waist_pieces_has_pelvis(doll, project):
    """Con cintura: torso_sup + pelvis se sueldan y `build_waist` las vuelve a partir."""
    import copy
    from afm.pipeline import build_from_parts
    from afm.planes import Plane
    from afm.waist import WaistSpec
    pr = copy.deepcopy(project)
    pr.waist = WaistSpec(Plane.from_points((-5, -5, 55), (5, -5, 55), (0, 5, 55), "cintura"))
    r = extract_all(doll, pr, "muneco", figure="doll")
    ex = {e.meta.tipo: e for e in r.pieces}
    direct_w = build(doll, pr.limbs, pr.params, head=pr.head, waist=pr.waist)
    res = build_from_parts(_choices(ex, "torso_sup", "pelvis", "brazo", "pierna", "cabeza"), pr.params)
    assert set(res.pieces) == set(direct_w.pieces) and "pelvis" in res.pieces
    for nm in ("torso", "pelvis"):
        assert vol(res.pieces, nm) == pytest.approx(vol(direct_w.pieces, nm), rel=0.03), nm
    assert res.waist is not None
