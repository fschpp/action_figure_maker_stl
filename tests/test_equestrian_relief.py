"""E07: despeje del jinete (pelvis + piernas completas) y faldones de la silla (plano de caballos compatibles, 5.4)."""
import math
import os

import numpy as np
import pytest
import trimesh

from afm import recipe as R
from afm import solids as S
from afm.equestrian import (HorseSpec, ReliefSpec, SaddleError, SaddleSpec, build_saddle, load_horse_mesh, place_rider,
                            relieve_saddle, rider_lower, rider_pairs, rider_profile,
                            seat_frame, sha256_file, suggest_saddle_spec)

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RAD = 10.0


def horse_from(tmp_path, mesh, name="h.stl"):
    p = tmp_path / name
    mesh.export(p)
    return load_horse_mesh(HorseSpec(archivo=p.name, sha256=sha256_file(str(p))), base_dir=str(tmp_path))


def ridge_horse(tmp_path, radius=RAD):          # lomo redondo a lo largo de Y, cima en z = radius
    m = trimesh.creation.cylinder(radius=radius, height=200, sections=256)
    m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    return horse_from(tmp_path, m)


def box(x0, x1, y0, y1, z0, z1):
    m = trimesh.creation.box(extents=(x1 - x0, y1 - y0, z1 - z0))
    m.apply_translation(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    return S.to_manifold(m)


def rider(calf_x=(10.5, 15.5), calf_z=(-5, 17), thigh_x=(5.0, 13.0)):
    """Pelvis sobre la cara superior (z = 15), muslos apoyados en ella y pantorrillas que cuelgan al costado del lomo."""
    r = {"pelvis": box(-12, 12, -8, 8, 15, 27)}
    for k, s in (("izq", 1), ("der", -1)):
        r[f"muslo_{k}"] = box(*sorted((s * thigh_x[0], s * thigh_x[1])), -10, 10, 15, 21)
        r[f"pantorrilla_{k}"] = box(*sorted((s * calf_x[0], s * calf_x[1])), 0, 8, *calf_z)
    return r


def setup(tmp_path, **kw):
    h = ridge_horse(tmp_path)
    sad = build_saddle(h, SaddleSpec(ancho=40, largo=30, alto=5, bajada=8), (0, 0, RAD))
    assert sad.ok
    return h, sad


def test_relief_clears_the_rider_and_keeps_one_clean_piece(tmp_path):
    h, sad = setup(tmp_path)
    r = rider()
    out = relieve_saddle(h, sad, r, ReliefSpec(clearance=0.5))
    assert out.ok, out.problems
    assert out.manifold is not None and out.components == 1 and out.voids == 0
    assert all(v == pytest.approx(0, abs=1e-3) for v in out.overlaps.values())
    assert out.gap >= 0.5 - 0.05 and set(out.gaps) == set(r)
    assert out.wall_min is not None and out.wall_min >= 1.2
    assert 0 < out.removed_volume and out.skirt_volume > 0
    assert out.volume == pytest.approx(float(sad.manifold.volume()) - out.removed_volume + out.skirt_volume, rel=0.02)


def test_skirts_hang_below_the_pad_and_can_be_removed(tmp_path):
    h, sad = setup(tmp_path)
    r = rider()
    with_sk = relieve_saddle(h, sad, r, ReliefSpec(skirt_drop=8.0, skirt_thick=1.6))
    none = relieve_saddle(h, sad, r, ReliefSpec(skirt_drop=0.0))
    zmin = lambda b: np.asarray(b.manifold.bounding_box()).reshape(2, 3)[0][2]
    base_zmin = np.asarray(sad.manifold.bounding_box()).reshape(2, 3)[0][2]
    assert with_sk.ok and none.ok
    assert zmin(with_sk) == pytest.approx(base_zmin - 8.0, abs=1e-6)
    assert zmin(none) == pytest.approx(base_zmin, abs=1e-6) and none.skirt_volume == 0
    assert with_sk.volume > none.volume


def test_the_rider_the_base_saddle_and_the_horse_are_left_untouched(tmp_path):
    h, sad = setup(tmp_path)
    r = rider()
    vr = {k: float(v.volume()) for k, v in r.items()}
    vs, vh = float(sad.manifold.volume()), float(h.manifold.volume())
    relieve_saddle(h, sad, r)
    assert {k: float(v.volume()) for k, v in r.items()} == vr
    assert float(sad.manifold.volume()) == vs and float(h.manifold.volume()) == vh


def test_a_thin_wall_next_to_the_leg_channel_is_rejected(tmp_path):
    h, sad = setup(tmp_path)
    r = rider(calf_x=(13.7, 18.7), calf_z=(5, 17), thigh_x=(5.0, 18.7))                    # canal hasta 19,2 mm: quedan ≈0,8 mm de silla hasta el borde (20 mm)
    out = relieve_saddle(h, sad, r, ReliefSpec(clearance=0.5))
    assert not out.ok and out.manifold is None and out.attempt is not None
    assert out.wall_min == pytest.approx(0.8, abs=0.15)
    assert any("pared" in p for p in out.problems)


def test_a_cut_that_splits_the_saddle_is_rejected(tmp_path):
    h, sad = setup(tmp_path)
    r = {"muslo_izq": box(-5, 5, -20, 20, 5, 25)}     # atraviesa toda la silla de punta a punta
    out = relieve_saddle(h, sad, r, ReliefSpec(clearance=0.5, skirt_drop=0.0))
    assert not out.ok and out.components >= 2
    assert any("componentes" in p for p in out.problems)


def test_a_thin_skirt_is_rejected(tmp_path):
    h, sad = setup(tmp_path)
    out = relieve_saddle(h, sad, rider(), ReliefSpec(skirt_thick=0.8), min_wall=1.2)
    assert not out.ok and any("faldones miden" in p for p in out.problems)


def test_a_bad_base_saddle_is_reported_with_its_problems(tmp_path):
    h = ridge_horse(tmp_path)
    floating = build_saddle(h, SaddleSpec(ancho=40, largo=30, alto=5, bajada=3), (0, 0, RAD + 6))
    assert not floating.ok
    out = relieve_saddle(h, floating, rider())
    assert not out.ok and any(p.startswith("silla base:") for p in out.problems)


def test_a_tool_without_the_clearance_is_detected(tmp_path):
    h, sad = setup(tmp_path)
    r = rider()
    good = relieve_saddle(h, sad, r, ReliefSpec(clearance=0.5, skirt_drop=0.0))
    assert rider_pairs(good.manifold, r, 0.5)[2] == []
    bare = S.difference(sad.manifold, *r.values())                       # mutación: la holgura quedó fuera de la herramienta
    ov, gaps, problems = rider_pairs(bare, r, 0.5)
    assert min(gaps.values()) < 0.05 and any("holgura" in p for p in problems)
    assert any("siguen dentro" in p for p in rider_pairs(sad.manifold, r, 0.5)[2])     # sin despejar


def test_a_rider_that_does_not_touch_the_saddle_warns(tmp_path):
    h, sad = setup(tmp_path)
    out = relieve_saddle(h, sad, {"muslo_izq": box(-5, 5, -5, 5, 30, 40)}, ReliefSpec(skirt_drop=0.0))
    assert out.ok and out.removed_volume == 0 and out.warnings
    assert out.volume == pytest.approx(float(sad.manifold.volume()), rel=1e-6)


def test_spec_roundtrip_and_bad_arguments(tmp_path):
    s = ReliefSpec(clearance=0.4, skirt_drop=6, skirt_thick=2)
    assert ReliefSpec.from_dict(s.to_dict()) == s
    for bad in ({"clearance": -1}, {"skirt_drop": -1}, {"skirt_thick": 0}):
        with pytest.raises(SaddleError):
            ReliefSpec(**bad)
    with pytest.raises(SaddleError, match="desconocidos"):
        ReliefSpec.from_dict({"otro": 1})
    h, sad = setup(tmp_path)
    with pytest.raises(SaddleError, match="jinete"):
        relieve_saddle(h, sad, {})
    with pytest.raises(SaddleError, match="min_wall"):
        relieve_saddle(h, sad, rider(), min_wall=0)


def test_demo_rider_on_a_ridge_horse(tmp_path):
    asm = R.assemble(R.Recipe.load(os.path.join(ROOT, "recetas", "muneco.receta.json")), ROOT, sit=True)
    rp = rider_profile(asm)
    h = ridge_horse(tmp_path)
    spec = suggest_saddle_spec(rp)
    sad = build_saddle(h, spec, (0, 0, RAD), direction=(0, 1, 0))
    fr = seat_frame(rp, (0, 0, RAD + sad.lift), (0, 1, 0))
    low = rider_lower(rp, place_rider(asm, fr))
    assert set(low) == {rp.pelvis_name, *rp.legs}
    out = relieve_saddle(h, sad, low, ReliefSpec(clearance=0.5, skirt_drop=0.0))
    assert out.gap is not None and all(v == pytest.approx(0, abs=1e-3) for v in out.overlaps.values())
    assert out.gap >= 0.5 - 0.05 or out.gap == float("inf")


def test_rider_lower_reports_missing_pieces():
    class RP:
        pelvis_name = "pelvis"
        legs = {"muslo_izq": None}
    with pytest.raises(Exception, match="faltan"):
        rider_lower(RP, {"pelvis": 1})
