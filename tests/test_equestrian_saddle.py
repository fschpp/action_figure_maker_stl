"""E06: volumen base de la silla (plano de caballos compatibles, 5.4). Caballos sintéticos cerrados; el catálogo de ejemplo
sirve solo para sugerir medidas."""
import math
import os

import numpy as np
import pytest
import trimesh

from afm import recipe as R
from afm import solids as S
from afm.equestrian import (HorseSpec, SaddleBuild, SaddleError, SaddleSpec, build_saddle, load_horse_mesh, rider_profile,
                            sha256_file, shell_stats, suggest_saddle_spec)

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


def horse_from(tmp_path, mesh, name="h.stl"):
    p = tmp_path / name
    mesh.export(p)
    return load_horse_mesh(HorseSpec(archivo=p.name, sha256=sha256_file(str(p))), base_dir=str(tmp_path))


def flat_horse(tmp_path):                       # losa ancha con el lomo plano en z = 0
    m = trimesh.creation.box(extents=(200, 200, 60))
    m.apply_translation((0, 0, -30))
    return horse_from(tmp_path, m)


def ridge_horse(tmp_path, radius=10.0):         # cilindro a lo largo de Y: lomo redondo con la cima en z = radius
    m = trimesh.creation.cylinder(radius=radius, height=200, sections=256)
    m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    return horse_from(tmp_path, m)


def test_spec_roundtrip_and_validation():
    s = SaddleSpec(ancho=30, largo=40, alto=4, bajada=6, encaje=0.5, desplazamiento=-2)
    assert SaddleSpec.from_dict(s.to_dict()) == s
    for bad in ({"ancho": 0}, {"largo": -1}, {"alto": 0}, {"bajada": -1}, {"encaje": 99}, {"ancho": float("nan")}):
        with pytest.raises(SaddleError if "nan" not in str(bad) else Exception):
            SaddleSpec(**bad)
    with pytest.raises(SaddleError, match="desconocidos"):
        SaddleSpec.from_dict({"ancho": 3, "otro": 1})


def test_flat_back_gives_a_slab(tmp_path):
    h = flat_horse(tmp_path)
    sp = SaddleSpec(ancho=30, largo=40, alto=5, bajada=8)
    b = build_saddle(h, sp, (3, -4, 0))
    assert b.ok and b.components == 1 and b.voids == 0 and not b.floating
    assert b.volume == pytest.approx(30 * 40 * 5, rel=1e-6)
    assert b.contact_volume == pytest.approx(0, abs=1e-3) and b.gap == pytest.approx(0, abs=1e-6)
    assert b.z_top == pytest.approx(5) and b.lift == 5 and b.top_thickness == pytest.approx(5)
    bb = np.asarray(b.manifold.bounding_box()).reshape(2, 3)
    assert bb[0] == pytest.approx([3 - 15, -4 - 20, 0]) and bb[1] == pytest.approx([3 + 15, -4 + 20, 5])


def test_encaje_sinks_into_the_horse_and_negative_leaves_a_gap(tmp_path):
    h = flat_horse(tmp_path)
    sp = SaddleSpec(ancho=30, largo=40, alto=5, bajada=8, encaje=1.0)
    b = build_saddle(h, sp, (0, 0, 0))
    assert b.volume == pytest.approx(30 * 40 * 6, rel=1e-6)
    assert b.contact_volume == pytest.approx(30 * 40 * 1, rel=1e-6)
    sp2 = SaddleSpec(ancho=30, largo=40, alto=5, bajada=8, encaje=-0.5)
    b2 = build_saddle(h, sp2, (0, 0, 0))
    assert b2.ok and b2.gap == pytest.approx(0.5, abs=1e-6) and b2.contact_volume == pytest.approx(0, abs=1e-3)
    assert b2.volume == pytest.approx(30 * 40 * 4.5, rel=1e-6)


def test_cap_over_round_ridge_matches_the_analytic_volume(tmp_path):
    R_, w, L, alto, baj = 10.0, 30.0, 40.0, 5.0, 8.0
    h = ridge_horse(tmp_path, R_)
    b = build_saddle(h, SaddleSpec(ancho=w, largo=L, alto=alto, bajada=baj), (0, 0, R_))
    x = np.linspace(-w / 2, w / 2, 20001)
    surf = np.sqrt(np.clip(R_ ** 2 - x ** 2, 0, None))
    zlow = R_ - baj
    col = (R_ + alto) - np.maximum(zlow, surf)
    expected = L * np.trapezoid(col, x) if hasattr(np, "trapezoid") else L * np.trapz(col, x)
    assert b.ok and b.components == 1
    assert b.volume == pytest.approx(expected, rel=0.01)
    assert b.top_thickness == pytest.approx(alto, abs=0.05)


def test_direction_rotates_the_footprint(tmp_path):
    h = flat_horse(tmp_path)
    b = build_saddle(h, SaddleSpec(ancho=30, largo=40, alto=5), (0, 0, 0), direction=(1, 0, 0))
    ext = np.ptp(np.asarray(b.manifold.bounding_box()).reshape(2, 3), axis=0)
    assert ext[0] == pytest.approx(40) and ext[1] == pytest.approx(30)
    b2 = build_saddle(h, SaddleSpec(ancho=30, largo=40, alto=5, desplazamiento=10), (0, 0, 0))
    assert np.asarray(b2.manifold.bounding_box()).reshape(2, 3).mean(axis=0)[1] == pytest.approx(10)


def test_floating_saddle_is_detected(tmp_path):
    h = flat_horse(tmp_path)
    b = build_saddle(h, SaddleSpec(ancho=30, largo=40, alto=5, bajada=3), (0, 0, 6))      # cara inferior a 3 mm del lomo
    assert b.floating and not b.ok and b.gap == pytest.approx(3, abs=1e-6)
    assert any("flota" in p for p in b.problems)
    far = build_saddle(h, SaddleSpec(ancho=30, largo=40, alto=5, bajada=3), (0, 0, 40))    # demasiado lejos para medirlo
    assert far.floating and not far.ok and any("no hay caballo" in p for p in far.problems)


def test_no_horse_under_the_seat(tmp_path):
    h = flat_horse(tmp_path)
    b = build_saddle(h, SaddleSpec(), (500, 500, 0))
    assert b.floating and not b.ok and any("no hay caballo" in p for p in b.problems)


def test_back_that_sticks_out_through_the_saddle_is_a_problem(tmp_path):
    slab = trimesh.creation.box(extents=(200, 200, 60))
    slab.apply_translation((0, 0, -30))
    wall = trimesh.creation.box(extents=(60, 3, 12))              # muro que atraviesa toda la huella y sobrepasa la cara superior
    wall.apply_translation((0, 0, 4))
    both = S.to_trimesh(S.union(S.to_manifold(slab), S.to_manifold(wall)))
    h = horse_from(tmp_path, both)
    b = build_saddle(h, SaddleSpec(ancho=30, largo=40, alto=5, bajada=8), (0, 0, 0))
    assert not b.ok and b.components == 2
    assert b.top_thickness < 0
    assert any("componentes" in p for p in b.problems) and any("sobre él" in p for p in b.problems)


def test_the_horse_is_left_untouched(tmp_path):
    h = flat_horse(tmp_path)
    v, n = float(h.manifold.volume()), len(h.mesh.faces)
    build_saddle(h, SaddleSpec(encaje=1.0), (0, 0, 0))
    assert float(h.manifold.volume()) == v and len(h.mesh.faces) == n


def test_shell_stats_counts_closed_voids_and_fragments():
    outer = S.to_manifold(trimesh.creation.box(extents=(10, 10, 10)))
    hollow = S.difference(outer, S.to_manifold(trimesh.creation.box(extents=(4, 4, 4))))
    st = shell_stats(hollow)
    assert st["components"] == 1 and st["voids"] == 1
    two = S.union(outer, S.to_manifold(trimesh.creation.box(extents=(2, 2, 2)).apply_translation((20, 0, 0))))
    st2 = shell_stats(two)
    assert st2["components"] == 2 and st2["voids"] == 0 and st2["fragments"] == [pytest.approx(8.0)]


def test_bad_origin_or_direction():
    class H:                                  # no se llega a usar el caballo
        manifold = None
    with pytest.raises(SaddleError, match="seat_origin"):
        build_saddle(H, SaddleSpec(), (0, 0))
    with pytest.raises(SaddleError, match="direction"):
        build_saddle(H, SaddleSpec(), (0, 0, 0), direction=(0, 0, 1))


def test_suggested_spec_from_the_demo_rider():
    asm = R.assemble(R.Recipe.load(os.path.join(ROOT, "recetas", "muneco.receta.json")), ROOT, sit=True)
    rp = rider_profile(asm)
    sp = suggest_saddle_spec(rp)
    legs = np.vstack([np.asarray(m.bounding_box()).reshape(2, 3) for m in rp.legs.values()])
    assert sp.ancho == pytest.approx(np.ptp(legs[:, 0]) + 8) and sp.largo == pytest.approx(np.ptp(legs[:, 1]) + 8)
