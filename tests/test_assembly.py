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
