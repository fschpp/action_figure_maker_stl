"""E10: exportación agrupada del conjunto jinete + caballo (+ silla), con caballo, silla y jinete sintéticos."""
import os

import numpy as np
import pytest
import trimesh

from afm import solids as S
from afm.equestrian import (EXPORT_MAX_TRIS, HorseError, export_riding, riding_components, sha256_file, validate_riding)
from afm.params import Params
from afm.pipeline import cap_export_triangles
from test_equestrian_validate import assembly, box


def load(outdir, name, part):
    return trimesh.load(os.path.join(outdir, "piezas", f"{name}_{part}.stl"))


def test_every_component_moves_with_the_same_translation(tmp_path):
    ra = assembly(tmp_path, with_saddle=True)
    out = str(tmp_path / "out")
    info = export_riding(ra, out, "fig", Params())
    dz = info["apoyo"]["traslado_z_mm"]
    comps = riding_components(ra)
    assert set(comps) == set(ra.rider) | {"caballo", "silla"}
    for n, mf in comps.items():
        want = np.asarray(mf.bounding_box(), float).reshape(2, 3) + [0, 0, dz]
        got = load(out, "fig", n).bounds
        assert np.allclose(got, want, atol=2e-3), n                        # misma traslación en Z, nada más


def test_the_group_rests_on_z0_at_the_hooves(tmp_path):
    ra = assembly(tmp_path, with_saddle=True)
    out = str(tmp_path / "out")
    info = export_riding(ra, out, "fig")
    assert info["apoyo"]["componente_mas_bajo"] == "caballo" and "avisos" not in info
    assert load(out, "fig", "caballo").bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    assert trimesh.load(os.path.join(out, "fig_ensamblada.stl")).bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    for n in list(ra.rider) + ["silla"]:
        assert load(out, "fig", n).bounds[0][2] >= -1e-6


def test_a_rider_piece_below_the_hooves_becomes_the_support(tmp_path):
    ra = assembly(tmp_path, with_saddle=False, extra={"pie": box(-3, 3, -3, 3, -40, -30)})
    out = str(tmp_path / "out")
    info = export_riding(ra, out, "fig")
    assert info["apoyo"]["componente_mas_bajo"] == "pie" and info["avisos"]
    assert load(out, "fig", "pie").bounds[0][2] == pytest.approx(0.0, abs=1e-6)


def test_carved_horse_and_relieved_saddle_are_what_gets_written(tmp_path):
    ra = assembly(tmp_path, with_saddle=True, calf_x=(9.3, 14.3))
    assert ra.carve.ok
    out = str(tmp_path / "out")
    info = export_riding(ra, out, "fig")
    assert info["caballo_tallado"] and info["silla"] == "piezas/fig_silla.stl"
    assert load(out, "fig", "caballo").volume == pytest.approx(ra.carve.carved.volume(), rel=1e-3)
    assert load(out, "fig", "caballo").volume < ra.horse.manifold.volume()
    assert load(out, "fig", "silla").volume == pytest.approx(ra.relief.manifold.volume(), rel=1e-3)


def test_without_saddle_there_is_no_saddle_file(tmp_path):
    ra = assembly(tmp_path, with_saddle=False)
    out = str(tmp_path / "out")
    info = export_riding(ra, out, "fig")
    assert info["silla"] is None and not os.path.exists(os.path.join(out, "piezas", "fig_silla.stl"))
    assert os.path.exists(os.path.join(out, "fig_ensamblada.stl"))


def test_weapon_mount_pieces_travel_with_the_rider(tmp_path):
    ra = assembly(tmp_path, with_saddle=False, extra={"torso_montura": box(-2, 2, 6, 9, 30, 34)})
    out = str(tmp_path / "out")
    info = export_riding(ra, out, "fig")
    assert "torso_montura" in info["piezas"] and os.path.exists(os.path.join(out, "piezas", "fig_torso_montura.stl"))


def test_relative_positions_between_rider_and_horse_are_intact(tmp_path):
    ra = assembly(tmp_path, with_saddle=False)
    out = str(tmp_path / "out")
    export_riding(ra, out, "fig")
    c0 = {n: np.asarray(mf.bounding_box(), float).reshape(2, 3).mean(0) for n, mf in riding_components(ra).items()}
    c1 = {n: load(out, "fig", n).bounds.mean(0) for n in c0}
    for n in c0:
        assert np.allclose(c1[n] - c1["caballo"], c0[n] - c0["caballo"], atol=2e-3)


def test_export_does_not_touch_the_assembly_or_the_source_stl(tmp_path):
    ra = assembly(tmp_path, with_saddle=True)
    vols = {n: mf.volume() for n, mf in riding_components(ra).items()}
    src = tmp_path / "h.stl"
    sha = sha256_file(str(src))
    export_riding(ra, str(tmp_path / "out"), "fig")
    assert {n: mf.volume() for n, mf in riding_components(ra).items()} == vols
    assert sha256_file(str(src)) == sha


def test_orient_and_flat_are_refused(tmp_path):
    ra = assembly(tmp_path, with_saddle=False)
    for kw in ({"orient": True}, {"orient": "auto"}, {"flat": 1.0}):
        with pytest.raises(HorseError, match="orient"):
            export_riding(ra, str(tmp_path / "out"), "fig", **kw)
    assert not os.path.exists(tmp_path / "out" / "fig_ensamblada.stl")


def test_a_rejected_carve_a_saddle_without_relief_or_a_failed_validation_are_not_exported(tmp_path):
    ra = assembly(tmp_path, with_saddle=False, calf_x=(9.3, 14.3))
    ra.carve.carved = None
    with pytest.raises(HorseError, match="tallado"):
        export_riding(ra, str(tmp_path / "o1"), "fig")
    rb = assembly(tmp_path, with_saddle=True, relieve=False)
    with pytest.raises(HorseError, match="despeje"):
        export_riding(rb, str(tmp_path / "o2"), "fig")
    rc = assembly(tmp_path, with_saddle=False)
    with pytest.raises(HorseError, match="validación"):
        export_riding(rc, str(tmp_path / "o3"), "fig", validation={"ok": False, "problems": ["x"]})
    export_riding(rc, str(tmp_path / "o4"), "fig", validation=validate_riding(rc, Params()))


def test_a_rider_piece_named_like_the_horse_is_refused(tmp_path):
    ra = assembly(tmp_path, with_saddle=False, extra={"caballo": box(0, 1, 0, 1, 30, 31)})
    with pytest.raises(HorseError, match="caballo"):
        export_riding(ra, str(tmp_path / "out"), "fig")


def test_the_horse_cap_is_its_own_and_keeps_the_volume(tmp_path):
    ra = assembly(tmp_path, with_saddle=False)
    n = ra.horse_mf.num_tri()
    info = export_riding(ra, str(tmp_path / "o1"), "fig", horse_max_tris=n * 10)
    assert "triangulos_reducidos" not in info
    info = export_riding(ra, str(tmp_path / "o2"), "fig", horse_max_tris=n // 2)
    before, after = info["triangulos_reducidos"]["caballo"]
    assert before == n and after <= before
    assert load(str(tmp_path / "o2"), "fig", "caballo").volume == pytest.approx(ra.horse_mf.volume(), rel=0.003)
    assert not any(k in info["triangulos_reducidos"] for k in ra.rider)       # el tope del caballo no toca al jinete
    with pytest.raises(HorseError, match="horse_max_tris"):
        export_riding(ra, str(tmp_path / "o3"), "fig", horse_max_tris=-1)
    assert EXPORT_MAX_TRIS == 150_000


def test_loose_accessories_go_beside_in_the_assembled_file_only(tmp_path):
    ra = assembly(tmp_path, with_saddle=False)
    out = str(tmp_path / "out")
    plain = export_riding(ra, str(tmp_path / "p"), "fig")
    w = trimesh.creation.box(extents=(4, 4, 30))
    info = export_riding(ra, out, "fig", extras=[w])
    assert info["accesorios_en_ensamblada"] == 1 and "accesorios_en_ensamblada" not in plain
    a0 = trimesh.load(os.path.join(tmp_path / "p", "fig_ensamblada.stl")).bounds
    a1 = trimesh.load(os.path.join(out, "fig_ensamblada.stl")).bounds
    assert a1[1][0] > a0[1][0] + 4 and np.allclose(a1[0][2], 0.0, atol=1e-6)
    assert sorted(os.listdir(os.path.join(out, "piezas"))) == sorted(os.listdir(os.path.join(tmp_path / "p", "piezas")))


def test_cap_export_triangles_only_leaves_the_other_pieces_alone():
    big = S.to_manifold(trimesh.creation.icosphere(subdivisions=5, radius=10))
    a, b = big, big.translate([40, 0, 0])
    n = big.num_tri()
    out, rep = cap_export_triangles({"a": a, "b": b}, n // 2, only={"b"})
    assert set(rep) == {"b"} and out["a"] is a and out["b"].num_tri() < n
    out2, rep2 = cap_export_triangles({"a": a, "b": b}, n // 2)
    assert set(rep2) == {"a", "b"}                                            # sin `only` todo igual que antes


# ------------------------------------------------------------------------------ caballo de referencia + muñeco de ejemplo
ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
HORSE_DIR = os.path.join(os.path.dirname(__file__), "..", "examples", "referencia", "caballo")
HORSE = os.path.join(HORSE_DIR, "horse_baseless.stl")


@pytest.mark.skipif(not os.path.exists(HORSE), reason="falta examples/referencia/caballo/horse_baseless.stl")
def test_reference_horse_with_the_demo_figure_exports_as_a_group(tmp_path):
    from afm import recipe as R
    from afm.equestrian import CarveLimits, HorseSpec, RidingFitter, load_horse_mesh

    h = load_horse_mesh(HorseSpec(archivo="horse_baseless.stl", sha256=sha256_file(HORSE), escala=3.0, yaw=180.0), base_dir=HORSE_DIR)
    asm = R.assemble(R.Recipe.load(os.path.join(ROOT, "recetas", "muneco.receta.json")), ROOT, sit=True)
    fit = RidingFitter(h, asm, limits=CarveLimits(max_depth=10.0)).sweep([-20.0], mode="tallado")[0]
    assert fit.ok, fit.problems
    out = str(tmp_path / "out")
    info = export_riding(fit.assembly, out, "muneco", asm.params, res=asm.res, validation=fit.validation)
    assert info["apoyo"]["componente_mas_bajo"] == "caballo" and info["caballo_tallado"] and info["silla"] is None
    horse = load(out, "muneco", "caballo")
    assert horse.bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    assert horse.volume < h.manifold.volume()                                  # el tallado se exportó, no el original
    whole = trimesh.load(os.path.join(out, "muneco_ensamblada.stl"))
    assert whole.bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    dz = info["apoyo"]["traslado_z_mm"]
    for n, mf in fit.assembly.rider.items():                                    # el jinete con su articulación intacta: misma traslación
        want = np.asarray(mf.bounding_box(), float).reshape(2, 3) + [0, 0, dz]
        assert np.allclose(load(out, "muneco", n).bounds, want, atol=2e-3), n
