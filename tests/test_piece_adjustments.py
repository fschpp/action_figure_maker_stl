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


# ------------------------------------------------------------------ escala por eje (X, Y, Z)
from afm.customize import fit_scale, is_identity, normalize_scale, scale_anchor, scale_vector  # noqa: E402


def test_scale_vector_and_storage_form():
    assert scale_vector(1.2).tolist() == [1.2, 1.2, 1.2]
    assert scale_vector([1, 2, 3]).tolist() == [1, 2, 3]
    assert normalize_scale([1.1, 1.1, 1.1]) == pytest.approx(1.1) and isinstance(normalize_scale([1.1, 1.1, 1.1]), float)
    assert normalize_scale((1, 2, 3)) == [1.0, 2.0, 3.0]
    assert is_identity(1.0) and is_identity([1, 1, 1]) and not is_identity([1, 1, 1.01])
    for bad in (0, -1, [1, 2], [1, 0, 1], [1, float("nan"), 1], float("inf")):
        with pytest.raises(ValueError):
            scale_vector(bad)


def test_per_axis_scales_roundtrip_in_project(project):
    project.part_scales = {"torso": [1.0, 1.2, 0.9], "pelvis": 1.05, "brazo": [0.9, 1.0, 1.1]}
    restored = Project.from_dict(project.to_dict())
    assert restored.part_scales == project.part_scales
    import json
    assert Project.from_dict(json.loads(json.dumps(project.to_dict()))).part_scales == project.part_scales


@pytest.mark.parametrize("part,port", [("torso_sup", "cintura"), ("pelvis", "cintura"), ("brazo", "hombro"),
                                       ("pierna", "cadera"), ("cabeza", "cuello")])
def test_per_axis_scale_moves_mesh_ports_and_faces(body, project, part, port):
    raw = extract_all(body, project).get(part)
    ch = PartChoice(raw.meta, raw.mesh)
    before = copy.deepcopy(ch.meta.to_dict())
    k = np.array([0.8, 1.3, 1.1])
    out = scale_choice(ch, k)
    anchor = scale_anchor(ch.meta)
    # volumen y caja: cada eje crece por su factor alrededor del punto fijo
    assert out.mesh.volume == pytest.approx(ch.mesh.volume * k.prod(), rel=1e-5)
    lo, hi = ch.mesh.bounds
    assert out.mesh.bounds[0] == pytest.approx(anchor + k * (lo - anchor), abs=1e-6)
    assert out.mesh.bounds[1] == pytest.approx(anchor + k * (hi - anchor), abs=1e-6)
    p0, p1 = ch.meta.puertos[port], out.meta.puertos[port]
    assert p1.frame.center == pytest.approx(anchor + k * (p0.frame.center - anchor), abs=1e-6)
    assert np.linalg.norm(p1.frame.axis) == pytest.approx(1.) and np.linalg.norm(p1.frame.ref) == pytest.approx(1.)
    assert float(p1.frame.axis @ p1.frame.ref) == pytest.approx(0., abs=1e-9)
    # la cara escalada coincide con la cara de la malla escalada (se mide sobre la propia malla)
    assert p1.face.area > 0
    assert 0.0 < p1.face.area < p0.face.area * k.max() ** 2 * 1.01
    assert ch.meta.to_dict() == before                    # la pieza de origen no se toca


def test_axis_aligned_face_area_scales_with_in_plane_factors(body, project):
    raw = extract_all(body, project).get("brazo")
    ch = PartChoice(raw.meta, raw.mesh)
    p0 = ch.meta.puertos["hombro"]
    n = np.abs(p0.frame.axis)
    assert n.max() > 0.999, "el hombro del muñeco es una cara perpendicular a un eje"
    k = np.array([0.7, 1.4, 1.2])
    out = scale_choice(ch, k)
    inplane = np.prod(np.delete(k, int(n.argmax())))
    assert out.meta.puertos["hombro"].face.area == pytest.approx(p0.face.area * inplane, rel=1e-4)


def test_symmetric_pieces_keep_the_symmetry_plane(body, project):
    for part in ("torso_sup", "pelvis", "cabeza"):
        raw = extract_all(body, project).get(part)
        out = scale_choice(PartChoice(raw.meta, raw.mesh), (1.3, 1.0, 1.0))
        lo, hi = raw.mesh.bounds[:, 0], out.mesh.bounds[:, 0]
        assert out.mesh.bounds[0, 0] == pytest.approx(1.3 * raw.mesh.bounds[0, 0], abs=1e-6)
        assert out.mesh.bounds[1, 0] == pytest.approx(1.3 * raw.mesh.bounds[1, 0], abs=1e-6)


def test_per_axis_joint_axis_follows_and_printing_dims_stay(body, project):
    project.joints = [JointSpec("rodilla", "pierna", "knee", Plane.from_normal((8, 0, 22), (0, 0, 1)), (8, 0, 25))]
    raw = choices_for_model(body, project)["pierna"]
    k = np.array([1.2, 0.9, 0.8])
    out = scale_choice(raw, k)
    anchor = scale_anchor(raw.meta)
    j = JointSpec.from_dict(out.meta.articulaciones[0])
    assert j.axis_center == pytest.approx(anchor + k * (np.array((8, 0, 25)) - anchor))
    assert j.hinge == project.joints[0].hinge
    assert np.linalg.norm(j.axis_dir) == pytest.approx(1.) and j.axis_dir[0] >= 0
    assert np.linalg.norm(j.plane.normal) == pytest.approx(1.)
    assert j.plane.normal[2] == pytest.approx(1.)         # un plano horizontal sigue horizontal


def test_head_is_scalable_and_follows_the_neck(body, project):
    assert project.head is not None
    orig = choices_for_model(body, project)
    project.part_scales = {"cabeza": [1.0, 1.0, 1.25]}
    adj = choices_for_model(body, project)
    assert adj["cabeza"].mesh.volume == pytest.approx(orig["cabeza"].mesh.volume * 1.25, rel=1e-5)
    host, choices = prepare(body, project)
    assert host.head is not None and "cabeza" not in host.parts       # la cabeza del modelo viaja en el cuerpo


def test_scale_applies_to_the_catalog_piece_too(body, project):
    raw = extract_all(body, project).get("pierna")
    rep = PartChoice(raw.meta, raw.mesh)
    project.part_scales = {"pierna": [1.0, 1.1, 1.2]}
    choices = choices_for_model(body, project, {"pierna": rep})
    assert choices["pierna"].mesh.volume == pytest.approx(rep.mesh.volume * 1.1 * 1.2, rel=1e-5)
    assert rep.mesh.volume == pytest.approx(raw.mesh.volume)          # la pieza elegida no se toca


def test_each_part_scales_independently(body, project):
    orig = choices_for_model(body, project)
    project.part_scales = {"torso": [1.0, 1.1, 1.0], "pelvis": [0.9, 1.0, 1.0], "cabeza": 1.2}
    adj = choices_for_model(body, project)
    assert adj["torso_sup"].mesh.volume == pytest.approx(orig["torso_sup"].mesh.volume * 1.1, rel=1e-5)
    assert adj["pelvis"].mesh.volume == pytest.approx(orig["pelvis"].mesh.volume * 0.9, rel=1e-5)
    assert adj["cabeza"].mesh.volume == pytest.approx(orig["cabeza"].mesh.volume * 1.2**3, rel=1e-5)
    for key in ("brazo", "pierna"):
        assert adj[key].mesh.volume == pytest.approx(orig[key].mesh.volume, rel=1e-9)


def test_fit_scale_matches_width_and_height_in_port_axes():
    from afm.ports import PortFace, PortFrame
    from shapely.geometry import box
    frame = PortFrame(np.zeros(3), np.array([1., 0, 0]), np.array([0., 0, 1]))     # u = Z, v = axis × ref = Y (hacia −Y: (1,0,0)×(0,0,1))
    part = PortFace.from_shape(box(-5, -3, 5, 3))                                    # 10 × 6
    host = PortFace.from_shape(box(-2.5, -3, 2.5, 3))                                # 5 × 6
    k = fit_scale(part, host, frame)
    assert k[2] == pytest.approx(2.0, rel=1e-6)       # u (Z) debe duplicarse
    assert k[1] == pytest.approx(1.0, rel=1e-6)       # v (Y) igual
    assert k[0] == pytest.approx(1.0, rel=1e-6)       # el espesor (normal X) no se toca
    assert fit_scale(None, host, frame) is None


def test_cli_accepts_per_axis_scale(tmp_path, project, monkeypatch):
    from afm import cli
    path = str(tmp_path / "doll.proyecto")
    project.save(path)
    seen = {}

    def fake_run(stl_path, proj, *args, **kwargs):
        seen.update(proj.to_dict())
        return {"validacion": {"ok": True, "problems": [], "gaps": {}}, "archivos": {}}

    monkeypatch.setattr(cli, "run", fake_run)
    assert cli.main(["build", "unused.stl", path, "--escala-pieza", "torso=1,1.1,1", "--escala-pieza", "cabeza=1.1",
                     "--escala-pieza", "pelvis=0.9;1;1.05", "--escala-pieza", "brazo=1.2,1.2,1.2"]) == 0
    assert seen["part_scales"] == {"torso": [1.0, 1.1, 1.0], "cabeza": 1.1, "pelvis": [0.9, 1.0, 1.05], "brazo": 1.2}


def test_cli_rejects_bad_per_axis_scale(tmp_path, project):
    from afm import cli
    path = str(tmp_path / "doll.proyecto")
    project.save(path)
    for bad in ("torso=1,2", "torso=0", "torso=1,0,1", "torso=a,b,c", "nada=1.1", "torso=1,1,1,1"):
        with pytest.raises(SystemExit) as exc:
            cli.main(["build", "unused.stl", path, "--escala-pieza", bad])
        assert exc.value.code == 2


def test_gui_per_axis_scale_and_xyz_fit(tmp_path, project):
    path = str(tmp_path / "doll.stl")
    make_doll().export(path)
    sess = Session(project)
    sess.load(path)
    c = Controller()
    c.session = sess
    assert "cabeza" in sess.scale_places()
    assert c.set_part_scale("torso", (1.0, 1.1, 0.95))
    assert sess.part_scale("torso") == pytest.approx((1.0, 1.1, 0.95))
    assert sess.project.part_scales["torso"] == [1.0, 1.1, 0.95]
    assert c.set_part_scale("torso", (1.2, 1.2, 1.2)) and sess.project.part_scales["torso"] == pytest.approx(1.2)
    assert c.set_part_scale("torso", (1.0, 0.0, 1.0)) is False and sess.part_scale("torso") == pytest.approx((1.2,) * 3)
    out = str(tmp_path / "ejes.proyecto")
    c.set_part_scale("pierna", (1.0, 0.9, 1.1))
    sess.save_project(out)
    assert Session.open_project(out).project.part_scales["pierna"] == [1.0, 0.9, 1.1]


def test_build_with_per_axis_scales_changes_only_the_asked_axes(body, project):
    base, _ = build_model(body, project)
    project.part_scales = {"torso": [1.0, 1.15, 1.0], "cabeza": [1.0, 1.0, 1.2]}
    res, limbs = build_model(body, project)
    val = P.validate(res, project.params, limbs, joints=res.joint_specs)
    assert val["ok"], val["problems"]
    def extent(m):
        b = m.bounding_box()
        return np.array(b[3:]) - np.array(b[:3])

    e0, e1 = extent(base.pieces["torso"]), extent(res.pieces["torso"])
    assert e1[1] == pytest.approx(e0[1] * 1.15, rel=0.02)          # profundidad ×1,15
    assert e1[0] == pytest.approx(e0[0], rel=0.02) and e1[2] == pytest.approx(e0[2], rel=0.02)
    h0, h1 = extent(base.pieces["cabeza"]), extent(res.pieces["cabeza"])
    assert h1[2] > h0[2] * 1.12 and h1[0] == pytest.approx(h0[0], rel=0.02)
    assert res.report["ajustes_piezas"]["escalas"] == {"torso": [1.0, 1.15, 1.0], "cabeza": [1.0, 1.0, 1.2]}
