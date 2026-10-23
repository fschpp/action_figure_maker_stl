"""Piezas originales ajustables y pelvis de catálogo con torso del modelo."""
import copy

import numpy as np
import pytest

from afm import pipeline as P, solids as S
from afm.assembly import PartChoice
from afm.catalog import Catalog
from afm.customize import build_model, choices_for_model, prepare, scale_choice
from afm.extract import extract_all
from afm.gui.controller import Controller
from afm.gui.session import Session
from afm.joints import JointSpec
from afm.planes import Plane
from afm.project import Project, demo_project
from afm.testfigure import make_doll
from afm.waist import WaistSpec


@pytest.fixture()
def project():
    p = demo_project()
    p.params.decimate = 0
    p.waist = WaistSpec(Plane.from_normal((0, 0, 55), (0, 0, 1), "cintura"))
    return p


@pytest.fixture(scope="module")
def body():
    return S.to_manifold(make_doll())


def test_old_projects_and_scale_roundtrip(project):
    assert Project.from_dict({}).part_scales == {}
    project.part_scales = {"torso": 1.05, "pelvis": 0.95, "brazo": 1.1, "pierna": 0.9}
    restored = Project.from_dict(project.to_dict())
    assert restored.part_scales == project.part_scales
    before = restored.params.to_dict()
    restored.scale_geometry(2.)
    assert restored.part_scales == project.part_scales
    assert restored.params.to_dict() == before


@pytest.mark.parametrize("part,port", [("torso_sup", "cintura"), ("pelvis", "cintura"), ("brazo", "hombro"), ("pierna", "cadera")])
def test_scales_mesh_and_port_faces_without_mutating_source(body, project, part, port):
    raw = extract_all(body, project).get(part)
    ch = PartChoice(raw.meta, raw.mesh)
    before = copy.deepcopy(ch.meta.to_dict())
    scaled = scale_choice(ch, 1.2)
    assert scaled.mesh.volume == pytest.approx(ch.mesh.volume * 1.2**3, rel=1e-5)
    assert scaled.meta.puertos[port].frame.center == pytest.approx(ch.meta.puertos[port].frame.center)
    assert scaled.meta.puertos[port].face.area == pytest.approx(ch.meta.puertos[port].face.area * 1.2**2)
    for p in scaled.meta.puertos.values():
        assert np.linalg.norm(p.frame.axis) == pytest.approx(1.)
        assert np.linalg.norm(p.frame.ref) == pytest.approx(1.)
    assert ch.meta.to_dict() == before


def test_only_requested_original_changes(body, project):
    orig = choices_for_model(body, project)
    project.part_scales = {"brazo": 1.1}
    adjusted = choices_for_model(body, project)
    for key in orig:
        factor = 1.1**3 if key == "brazo" else 1.
        assert adjusted[key].mesh.volume == pytest.approx(orig[key].mesh.volume * factor, rel=1e-5)


def test_scaled_joint_moves_but_keeps_printing_dimensions(body, project):
    project.joints = [JointSpec("rodilla", "pierna", "knee", Plane.from_normal((8, 0, 22), (0, 0, 1)), (8, 0, 25))]
    raw = choices_for_model(body, project)["pierna"]
    scaled = scale_choice(raw, 0.8)
    anchor = raw.port("leg").frame.center
    expected = anchor + 0.8 * (np.array((8, 0, 25)) - anchor)
    j = JointSpec.from_dict(scaled.meta.articulaciones[0])
    assert j.axis_center == pytest.approx(expected)
    assert j.hinge == project.joints[0].hinge
    assert np.linalg.norm(j.axis_dir) == pytest.approx(1.)


def test_moved_catalog_pelvis_uses_original_upper_and_limbs(body, project):
    raw = extract_all(body, project).get("pelvis")
    pelvis = PartChoice(raw.meta, raw.mesh)
    from afm.assembly import transform_choice
    T = np.eye(4)
    T[:3, 3] = (0, 4, 20)
    pelvis = transform_choice(pelvis, T)
    host, choices = prepare(body, project, {"pelvis": pelvis})
    assert {"torso_sup", "pelvis", "brazo", "pierna", "cabeza"} <= set(choices)
    assert host.faces["pierna"].center[2] < 55  # puerto de cadera alineado, sin los +20 mm del catálogo
    res, limbs = build_model(body, project, {"pelvis": pelvis})
    assert {"torso", "pelvis", "brazo_izq", "pierna_izq", "cabeza"} <= set(res.pieces)
    val = P.validate(res, project.params, limbs, joints=res.joint_specs)
    assert val["ok"], val["problems"]
    assert res.report["ajustes_piezas"]["origen"]["torso"] == "modelo"


@pytest.mark.parametrize("scales,problem", [({"torso": 1.05}, None), ({"pelvis": 1.05}, "componentes"),
                                             ({"brazo": 1.05}, "choca"), ({"pierna": 1.05}, "interpenetran")])
def test_each_scaled_part_builds_and_reports_physical_fit(body, project, scales, problem):
    project.part_scales = scales
    res, limbs = build_model(body, project)
    val = P.validate(res, project.params, limbs, joints=res.joint_specs)
    if problem is None:
        assert val["ok"], val["problems"]
    else:
        # Escalar no garantiza compatibilidad: la validación conserva su capacidad de rechazar el resultado.
        assert not val["ok"] and any(problem in msg for msg in val["problems"])
    assert res.report["ajustes_piezas"]["escalas"] == scales


def test_missing_waist_and_invalid_factors_have_clear_errors(body):
    p = demo_project()
    p.part_scales = {"pelvis": 1.1}
    with pytest.raises(ValueError, match="cintura"):
        prepare(body, p)
    for k in (0, -1, float("nan"), float("inf")):
        p.part_scales = {"brazo": k}
        with pytest.raises(ValueError, match="escala"):
            prepare(body, p)


def test_divide_preview_save_and_reset_without_catalog(tmp_path, project):
    path = str(tmp_path / "doll.stl")
    make_doll().export(path)
    sess = Session(project)
    sess.load(path)
    c = Controller()
    c.session = sess
    assert c.divide_model(), c.message
    assert {"torso", "pelvis", "brazo", "pierna"} <= set(sess.part_preview_items())
    assert sess.part_preview_fresh()
    assert c.set_part_scale("brazo", 1.1)
    assert sess.part_preview_items() == {} and sess.outcome is None
    assert not sess.part_preview_fresh()
    assert c.preview_parts() is not None
    out = str(tmp_path / "scaled.proyecto")
    sess.save_project(out)
    restored = Session.open_project(out)
    assert restored.project.part_scales["brazo"] == 1.1
    assert c.set_part_scale("brazo", 0) is False
    assert sess.project.part_scales["brazo"] == 1.1
    c.reset_part_scales()
    assert sess.project.part_scales == {} and sess.part_preview_items() == {}


def test_gui_pelvis_does_not_enable_catalog_only(tmp_path, project):
    path = str(tmp_path / "doll.stl")
    make_doll().export(path)
    sess = Session(project)
    sess.load(path)
    cat = Catalog.create(str(tmp_path / "cat"), "linea", project.params.ref_height)
    raw = extract_all(S.to_manifold(sess.work), project).get("pelvis")
    meta, _ = cat.add_piece(raw.mesh, raw.meta)
    sess.open_catalog(cat.root)
    sess.set_part("pelvis", meta.ref)
    assert not sess.catalog_only()
    assert sess.problems() == []
    previews = sess.preview_parts()
    assert previews["pelvis"].mesh is not None and previews["pelvis"].compat is not None
    assert sess.part_preview_fresh()
    out = sess.build()
    assert out.validation["ok"], out.validation["problems"]
    project.waist = None
    assert any("cintura" in e for e in sess.problems())


def test_cli_accepts_pelvis_and_piece_scales(tmp_path, project, monkeypatch):
    from afm import cli
    path = str(tmp_path / "p.proyecto")
    project.save(path)
    cat = Catalog.create(str(tmp_path / "cat"), "linea", 100.)
    raw = extract_all(S.to_manifold(make_doll()), project).get("pelvis")
    meta, _ = cat.add_piece(raw.mesh, raw.meta)
    seen = {}

    def fake_run(stl_path, proj, *args, **kwargs):
        seen.update(proj.to_dict())
        return {"validacion": {"ok": True, "problems": [], "gaps": {}}, "archivos": {}}

    monkeypatch.setattr(cli, "run", fake_run)
    assert cli.main(["build", "unused.stl", path, "--catalogo", cat.root, "--pieza", f"pelvis={meta.ref}",
                     "--escala-pieza", "torso=1.05", "--escala-pieza", "brazo=0.98"]) == 0
    assert seen["parts"]["pelvis"]["id"] == meta.id
    assert seen["part_scales"] == {"torso": 1.05, "brazo": 0.98}
    for text in ("brazo=nan", "torso=0", "pelvis=inf", "cola=1", "brazo=texto"):
        with pytest.raises(SystemExit) as exc:
            cli.main(["build", "unused.stl", path, "--escala-pieza", text])
        assert exc.value.code == 2


def test_cataloguing_keeps_original_piece_scales(tmp_path, project):
    path = str(tmp_path / "doll.stl")
    make_doll().export(path)
    sess = Session(project)
    sess.load(path)
    sess.create_catalog(str(tmp_path / "cat"), "linea", 100.)
    raw = extract_all(S.to_manifold(sess.work), project).get("brazo")
    sess.set_part_scale("brazo", 1.1)
    result = sess.catalog_add("escalada", places=["brazo"])
    cat = Catalog.open(sess.catalog_root)
    _meta, mesh = cat.load(result["piezas"][0].ref)
    assert mesh.volume == pytest.approx(raw.mesh.volume * 1.1**3, rel=1e-5)


def test_cli_generates_scaled_project_and_printable_files(tmp_path):
    import json
    from afm.cli import run
    p = demo_project()
    p.params.decimate = 0
    p.part_scales = {"brazo": 1.1}
    stl = str(tmp_path / "doll.stl")
    make_doll().export(stl)
    out = tmp_path / "out"
    out.mkdir()
    info = run(stl, p, str(out), "scaled", progress=lambda *a: None)
    assert info["validacion"]["ok"], info["validacion"]["problems"]
    assert list(out.glob("*.stl"))
    assert Project.load(str(out / "scaled.proyecto")).part_scales == {"brazo": 1.1}
    report = json.loads((out / "scaled_informe.json").read_text())
    assert report["construccion"]["ajustes_piezas"]["escalas"]["brazo"] == 1.1
