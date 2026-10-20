"""Cabeza de reemplazo: se importa otro STL, se descarta la original, no se simetriza, se escala a la base del cuello."""
import dataclasses
import os

import numpy as np
import pytest
import trimesh

from afm import pipeline as P, solids as S
from afm.head import HEAD_NAME, HeadSpec, fit_replacement, load_replacement, neck_plane, split_head
from afm.planes import Plane
from afm.project import Project, demo_project
from afm.testfigure import make_doll

M = S.Manifold


def _neck(**kw):
    return HeadSpec(Plane.from_points((-5, -5, 80), (5, -5, 80), (0, 5, 80), "cabeza cuerpo"), **kw)


def _write(tmp_path, mf, name):
    path = str(tmp_path / name)
    S.to_trimesh(mf).export(path)
    return path


@pytest.fixture(scope="module")
def assets(tmp_path_factory):
    d = tmp_path_factory.mktemp("cab")
    ball = M.sphere(15, 64)
    flat = S.intersection(ball, M.cube((100, 100, 100), center=True).translate((0, 0, 50 - 10)))    # base plana z = -10
    nose = M.sphere(7, 32).translate((18, 0, 0))                                                    # solo del lado +X
    asym = S.union(flat, nose)
    return {"asym": _write(d, asym, "asim.stl"), "round": _write(d, ball, "bola.stl"),
            "wide": _write(d, S.intersection(M.sphere(15, 64).scale((2.0, 1.0, 1.0)),
                                             M.cube((200, 200, 100), center=True).translate((0, 0, 40))), "ancha.stl")}


@pytest.fixture(scope="module")
def params():
    return dataclasses.replace(demo_project().params, decimate=0)


@pytest.fixture(scope="module")
def body():
    return S.to_manifold(make_doll())


@pytest.fixture(scope="module")
def built(body, params, assets):
    spec = _neck(replacement_path=assets["asym"])
    res = P.build(body, [], params, head=spec)
    return res, P.validate(res, params, [])


def test_original_head_is_discarded(built, body, params):
    res, val = built
    top = res.pieces[HEAD_NAME].bounding_box()[5]
    assert top < 97.0                                   # la original llegaba a 100 mm; esta es más baja
    assert val["ok"], val["problems"]
    assert val["pieces"][HEAD_NAME]["componentes"] == 1 and val["pieces"][HEAD_NAME]["variedad_ok"]
    assert val["pieces"][HEAD_NAME]["aristas_no_variedad"] == 0
    orig = split_head(body, _neck(), params).head
    assert abs(res.pieces[HEAD_NAME].volume() - orig.volume()) > 100      # no es la misma cabeza


def test_no_symmetry_is_applied(built):
    head = built[0].pieces[HEAD_NAME]
    sym = (head ^ S.mirror_x(head)).volume()
    assert sym < 0.97 * head.volume()                   # la «nariz» en +X NO se espejó ni se perdió
    bb = head.bounding_box()
    assert bb[3] > abs(bb[0]) + 1.0                     # sobresale más del lado +X que del −X


def test_scaled_to_the_neck_base(built, body, params, assets):
    res, _ = built
    rep = res.report["head"]["replacement"]
    orig_face = split_head(body, _neck(), params).face
    assert rep["base_igualada_mm2"] == pytest.approx(orig_face.area, rel=1e-6)
    assert res.report["head"]["contact_area"] == pytest.approx(orig_face.area, rel=0.01)   # la base nueva = la original
    expected_k = np.sqrt(orig_face.area / (np.pi * (225 - 100)))              # círculo de la base plana: r² = 15² − 10²
    assert rep["escala"] == pytest.approx(expected_k, rel=0.02)
    assert rep["coincidencia_base"] > 0.95 and not rep["base_cortada"]
    assert rep["alto_mm"] == pytest.approx(25.0 * rep["escala"], rel=0.03)   # escala uniforme: el alto sigue la misma k (alto = 25 en el archivo)


def test_head_sits_on_the_neck_plane_with_pin(built, params):
    res, val = built
    head, torso = res.pieces[HEAD_NAME], res.pieces["torso"]
    assert head.bounding_box()[2] == pytest.approx(80.0 - params.neck_pin.shaft_len - params.neck_pin.head_h, abs=0.3)
    assert 0.45 <= val["gaps"][HEAD_NAME] <= 0.6 and val["recorrido"][HEAD_NAME] < 1e-4
    assert (head ^ torso).volume() < 1e-6
    assert (head.translate((0, 0, 1.0)) ^ torso).volume() > 1e-6          # el hongo la retiene


def test_round_head_without_flat_base_is_cut_flat(body, params, assets):
    res = P.build(body, [], params, head=_neck(replacement_path=assets["round"]))
    rep = res.report["head"]["replacement"]
    assert rep["base_cortada"] and P.validate(res, params, [])["ok"]
    assert res.pieces[HEAD_NAME].bounding_box()[5] > 80.0 + 10


def test_mismatched_base_warns(body, params, assets):
    res = P.build(body, [], params, head=_neck(replacement_path=assets["wide"]))
    val = P.validate(res, params, [])
    assert res.report["head"]["replacement"]["coincidencia_base"] < 0.8
    assert any("coincide solo" in a for a in val["avisos"])


def test_yaw_and_up_options(body, params, assets):
    a = split_head(body, _neck(replacement_path=assets["asym"]), params).head
    b = split_head(body, _neck(replacement_path=assets["asym"], replacement_yaw=180.0), params).head
    assert a.bounding_box()[3] > 0 > b.bounding_box()[3] - 5 or b.bounding_box()[0] < a.bounding_box()[0] - 1.0
    with pytest.raises(ValueError, match="replacement_up"):
        load_replacement(assets["asym"], up="arriba")
    flipped = split_head(body, _neck(replacement_path=assets["asym"], replacement_up="-Z"), params).head
    assert flipped.volume() > 0 and flipped.bounding_box()[2] >= 80.0 - 1e-6


def test_bad_file_is_reported(body, params, tmp_path):
    empty = str(tmp_path / "vacio.stl")
    with open(empty, "w") as fh:
        fh.write("solid x\nendsolid x\n")
    with pytest.raises(ValueError):
        split_head(body, _neck(replacement_path=empty), params)


def test_exports_assembled_toy_with_new_head(built, tmp_path):
    res, _ = built
    info = P.export(res, str(tmp_path), "toy")
    head_stl = trimesh.load(os.path.join(tmp_path, "piezas", "toy_cabeza.stl"))
    asm = trimesh.load(os.path.join(tmp_path, "toy_ensamblada.stl"))
    assert head_stl.is_watertight and head_stl.bounds[1][2] < 97.0
    assert asm.bounds[1][2] == pytest.approx(head_stl.bounds[1][2], abs=1e-3)   # el ensamblado lleva la cabeza nueva
    assert "cabeza" in info["piezas"]


def test_project_roundtrip(tmp_path):
    p = demo_project()
    p.head = dataclasses.replace(p.head, replacement_path="/x/c.stl", replacement_yaw=30.0, replacement_up="+Y")
    f = tmp_path / "p.proyecto"
    p.save(str(f))
    q = Project.load(str(f))
    assert (q.head.replacement_path, q.head.replacement_yaw, q.head.replacement_up) == ("/x/c.stl", 30.0, "+Y")
    old = p.to_dict()
    for k in ("replacement_path", "replacement_yaw", "replacement_up"):
        old["head"].pop(k)
    assert Project.from_dict(old).head.replacement_path is None


# ------------------------------------------------- orientación X/Y, escala manual y corte de la base
def test_rx_ry_match_the_up_axis_rotations(assets):
    """Con rx/ry = 180°/90° se obtiene lo mismo que los ejes «arriba» -Z / +X (el orden de giros no cambia lo anterior)."""
    base, _ = load_replacement(assets["asym"], up="-Z")
    rx, _ = load_replacement(assets["asym"], rx_deg=180.0)
    assert np.allclose(base.bounds, rx.bounds, atol=1e-6)
    base, _ = load_replacement(assets["asym"], up="+X")
    ry, _ = load_replacement(assets["asym"], ry_deg=90.0)
    assert np.allclose(base.bounds, ry.bounds, atol=1e-6)
    same, _ = load_replacement(assets["asym"], rx_deg=0.0, ry_deg=0.0)
    ref, _ = load_replacement(assets["asym"])
    assert np.allclose(same.bounds, ref.bounds, atol=1e-9)


def test_ry_flips_the_nose_to_the_other_side(body, params, assets):
    a = split_head(body, _neck(replacement_path=assets["asym"]), params).head.bounding_box()
    b = split_head(body, _neck(replacement_path=assets["asym"], replacement_ry=180.0), params).head.bounding_box()
    assert a[3] > abs(a[0]) + 1.0                       # nariz del lado +X
    assert b[0] < -(abs(b[3]) + 1.0)                    # girada 180° sobre Y: pasa al lado −X


def test_manual_scale_multiplies_the_auto_fit(body, params, assets):
    one = split_head(body, _neck(replacement_path=assets["asym"]), params)
    big = split_head(body, _neck(replacement_path=assets["asym"], replacement_scale=1.5), params)
    assert big.replacement["escala"] == pytest.approx(1.5 * one.replacement["escala"], rel=1e-9)
    assert big.replacement["alto_mm"] == pytest.approx(1.5 * one.replacement["alto_mm"], rel=1e-9)
    assert big.replacement["escala_manual"] == 1.5
    assert big.replacement["coincidencia_base"] < one.replacement["coincidencia_base"]   # la base ya no iguala a la del cuello
    small = split_head(body, _neck(replacement_path=assets["asym"], replacement_scale=0.5), params)
    assert small.head.volume() == pytest.approx(0.5 ** 3 * one.head.volume(), rel=0.03)
    with pytest.raises(ValueError, match="escala"):
        split_head(body, _neck(replacement_path=assets["asym"], replacement_scale=9.0), params)


def test_base_cut_plane_removes_the_lower_part(body, params, assets):
    one = split_head(body, _neck(replacement_path=assets["asym"]), params)
    cut = split_head(body, _neck(replacement_path=assets["asym"], replacement_cut=20.0), params)
    rep = cut.replacement
    assert rep["base_cortada"] and rep["corte_pct"] == 20.0 and not one.replacement["base_cortada"]
    assert cut.head.bounding_box()[2] == pytest.approx(80.0, abs=1e-3)           # la nueva base sigue sobre el plano del cuello
    assert rep["alto_mm"] == pytest.approx(20.0 * rep["escala"], rel=0.03)       # 25 mm del archivo − 20 % = 20 mm
    assert rep["escala"] < one.replacement["escala"]                              # a esa altura la bola es más ancha → k menor
    assert cut.head.volume() < one.head.volume()
    assert rep["coincidencia_base"] > 0.7        # el corte pasa por la nariz: la base ya no es un círculo perfecto
    with pytest.raises(ValueError, match="corte"):
        split_head(body, _neck(replacement_path=assets["asym"], replacement_cut=80.0), params)


def test_cut_head_builds_a_valid_piece(body, params, assets):
    spec = _neck(replacement_path=assets["asym"], replacement_cut=15.0, replacement_scale=1.1, replacement_rx=10.0)
    res = P.build(body, [], params, head=spec)
    val = P.validate(res, params, [])
    assert val["pieces"][HEAD_NAME]["componentes"] == 1 and val["pieces"][HEAD_NAME]["variedad_ok"]


def test_new_fields_roundtrip_and_old_projects_load(tmp_path):
    p = demo_project()
    p.head = dataclasses.replace(p.head, replacement_path="/x/c.stl", replacement_rx=12.5, replacement_ry=-30.0,
                                 replacement_scale=1.25, replacement_cut=8.0)
    f = tmp_path / "p.proyecto"
    p.save(str(f))
    q = Project.load(str(f))
    h = q.head
    assert (h.replacement_rx, h.replacement_ry, h.replacement_scale, h.replacement_cut) == (12.5, -30.0, 1.25, 8.0)
    old = p.to_dict()
    for k in ("replacement_rx", "replacement_ry", "replacement_scale", "replacement_cut"):
        old["head"].pop(k)
    h = Project.from_dict(old).head
    assert (h.replacement_rx, h.replacement_ry, h.replacement_scale, h.replacement_cut) == (0.0, 0.0, 1.0, 0.0)
