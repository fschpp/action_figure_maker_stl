"""E05: tallado del caballo (plano de caballos compatibles, 5.3) con caballos y piernas sintéticos."""
import numpy as np
import pytest
import trimesh

from afm import solids as S
from afm.equestrian import (CarveLimits, HorseError, HorseSpec, carve_horse, load_horse_mesh, sha256_file, verify_carved)


def horse_from(tmp_path, mesh, name="h.stl"):
    p = tmp_path / name
    mesh.export(p)
    return load_horse_mesh(HorseSpec(archivo=p.name, sha256=sha256_file(str(p))), base_dir=str(tmp_path)), p


def block_horse(tmp_path, width=40.0, length=80.0, height=40.0, top=0.0):
    m = trimesh.creation.box(extents=(width, length, height))
    m.apply_translation((0, 0, top - height / 2))
    return horse_from(tmp_path, m)


def leg_box(x, y, zlo, zhi, w=6.0, d=8.0):
    m = trimesh.creation.box(extents=(w, d, zhi - zlo))
    m.apply_translation((x, y, (zlo + zhi) / 2))
    return S.to_manifold(m)


def legs_pair(zlo=-5.0, zhi=30.0, w=6.0, dx=8.0):
    return {"pierna_izq": leg_box(dx, 0, zlo, zhi, w), "pierna_der": leg_box(-dx, 0, zlo, zhi, w)}


def test_wide_horse_adapts_with_limited_cuts(tmp_path):
    h, _ = block_horse(tmp_path)
    legs = legs_pair()
    r = carve_horse(h, legs, clearance=0.5)
    assert r.ok and r.carved is not None and r.problems == []
    raw = sum(S.intersection(h.manifold, m).volume() for m in legs.values())
    # entre el volumen de las piernas dentro y el de sus cajas ensanchadas 0,52 mm por lado
    wide = sum(S.intersection(h.manifold, leg_box(x, 0, -5.52, 30.52, 7.04, 9.04)).volume() for x in (8, -8))
    assert raw < r.removed_volume <= wide + 1e-6
    assert r.removed_volume == pytest.approx(sum(r.zones.values()), rel=1e-6)
    assert r.legs_overlap == pytest.approx(0, abs=1e-3) and r.gap >= 0.5 - 0.05
    assert r.components == 1 and r.genus_delta == 0 and r.voids == 0
    assert r.max_depth == pytest.approx(5.5, abs=0.3)          # 5 mm de pierna + holgura
    assert r.wall_min is None or r.wall_min >= 1.2


def test_too_deep_is_rejected_by_limit_and_allowed_with_a_bigger_one(tmp_path):
    h, _ = block_horse(tmp_path)
    legs = legs_pair(zlo=-9.0)
    r = carve_horse(h, legs, limits=CarveLimits(max_depth=6.0))
    assert not r.ok and r.carved is None and r.attempt is not None
    assert any("bajo la superficie" in p for p in r.problems) and any("no admite" in p for p in r.problems)
    assert r.max_depth == pytest.approx(9.5, abs=0.3)
    ok = carve_horse(h, legs, limits=CarveLimits(max_depth=12.0))
    assert ok.ok and ok.carved is not None


def test_thin_wall_is_measured_and_rejected_before_it_pierces(tmp_path):
    h, _ = block_horse(tmp_path, width=14.0)
    thick = carve_horse(h, {"p": leg_box(0, 0, -5, 30, w=10.0)})                  # cavidad de ≈11 mm en 14 mm: paredes de ≈1,5 mm
    assert thick.ok and thick.wall_min == pytest.approx(1.5, abs=0.15)
    thin = carve_horse(h, {"p": leg_box(0, 0, -5, 30, w=11.0)})                   # paredes de ≈1,0 mm: todavía sin atravesar
    assert not thin.ok and thin.components == 1 and thin.genus_delta == 0
    assert thin.wall_min == pytest.approx(1.0, abs=0.15)
    assert any("pared" in p for p in thin.problems)


def test_a_cut_that_splits_the_horse_is_rejected(tmp_path):
    h, _ = block_horse(tmp_path, width=8.0, height=10.0)
    r = carve_horse(h, {"p": leg_box(0, 0, -20, 30, w=12.0)}, limits=CarveLimits(max_depth=50, min_wall=0.1, max_removed_frac=1.0))
    assert not r.ok and r.carved is None
    assert any("componentes" in p or "túnel" in p for p in r.problems)


def test_a_tunnel_is_detected(tmp_path):
    h, _ = block_horse(tmp_path, height=20.0)
    r = carve_horse(h, {"p": leg_box(0, 0, -40, 30, w=6.0)}, limits=CarveLimits(max_depth=50, min_wall=0.1, max_removed_frac=1.0))
    assert not r.ok and r.genus_delta == 1 and any("túnel" in p for p in r.problems)


def test_legs_that_do_not_touch_leave_the_horse_as_is(tmp_path):
    h, _ = block_horse(tmp_path)
    r = carve_horse(h, {"p": leg_box(0, 0, 5, 30)})
    assert r.ok and r.removed_volume == 0 and r.carved is h.manifold and r.warnings


def test_zone_limit(tmp_path):
    h, _ = block_horse(tmp_path)
    legs = legs_pair()
    assert carve_horse(h, legs, limits=CarveLimits(zone=(-30, -30, -20, 30, 30, 1))).ok
    r = carve_horse(h, legs, limits=CarveLimits(zone=(-5, -30, -20, 30, 30, 1)))
    assert not r.ok and any("zona" in p for p in r.problems)


def test_source_horse_stl_and_model_are_untouched(tmp_path):
    h, p = block_horse(tmp_path)
    sha, vol, nt = sha256_file(str(p)), float(h.manifold.volume()), len(h.mesh.faces)
    carve_horse(h, legs_pair(), clearance=0.5)
    assert sha256_file(str(p)) == sha and float(h.manifold.volume()) == vol and len(h.mesh.faces) == nt


def test_a_tool_without_the_clearance_is_detected(tmp_path):
    h, _ = block_horse(tmp_path)
    legs = legs_pair()
    good = carve_horse(h, legs, clearance=0.5).carved
    assert verify_carved(good, legs, 0.5)[2] == []
    bare = S.difference(h.manifold, *legs.values())                                # mutación: holgura fuera de la herramienta
    overlap, gap, problems = verify_carved(bare, legs, 0.5)
    assert gap < 0.05 and any("holgura" in p for p in problems)
    inside = h.manifold                                                           # sin tallar: las piernas siguen dentro
    assert any("siguen" in p for p in verify_carved(inside, legs, 0.5)[2])


def test_bad_arguments(tmp_path):
    h, _ = block_horse(tmp_path)
    with pytest.raises(HorseError, match="piernas"):
        carve_horse(h, {})
    with pytest.raises(HorseError, match="clearance"):
        carve_horse(h, legs_pair(), clearance=-1)
    for bad in ({"max_depth": 0}, {"min_wall": -1}, {"max_removed_frac": 2}, {"zone": (1, 1, 1, 0, 0, 0)}):
        with pytest.raises(HorseError):
            CarveLimits(**bad)
