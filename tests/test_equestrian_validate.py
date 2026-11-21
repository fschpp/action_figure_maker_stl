"""E08: validación ecuestre (plano 6) con caballo, silla y jinete sintéticos."""
import math

import numpy as np
import pytest
import trimesh

from afm import solids as S
from afm.equestrian import (CarveLimits, ReliefSpec, RidingAssembly, SaddleSpec, build_saddle, carve_horse, load_horse_mesh,
                            relieve_saddle, sha256_file, validate_riding, HorseSpec)
from afm.params import Params

RAD = 10.0


def horse_from(tmp_path, mesh, name="h.stl"):
    p = tmp_path / name
    mesh.export(p)
    return load_horse_mesh(HorseSpec(archivo=p.name, sha256=sha256_file(str(p))), base_dir=str(tmp_path))


def ridge_horse(tmp_path):
    m = trimesh.creation.cylinder(radius=RAD, height=200, sections=256)
    m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    return horse_from(tmp_path, m)


def box(x0, x1, y0, y1, z0, z1):
    m = trimesh.creation.box(extents=(x1 - x0, y1 - y0, z1 - z0))
    m.apply_translation(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    return S.to_manifold(m)


def lower(calf_x=(10.5, 15.5), pelvis_z=15.0):
    r = {"pelvis": box(-12, 12, -8, 8, pelvis_z, pelvis_z + 12)}
    for k, s in (("izq", 1), ("der", -1)):
        r[f"muslo_{k}"] = box(*sorted((s * 5.0, s * 13.0)), -10, 10, 15, 21)
        r[f"pantorrilla_{k}"] = box(*sorted((s * calf_x[0], s * calf_x[1])), 0, 8, -5, 17)
    return r


def torso():
    return box(-10, 10, -6, 6, 27, 45)


def assembly(tmp_path, with_saddle=True, calf_x=(10.5, 15.5), pelvis_z=15.0, extra=None, encaje=0.0, relieve=True):
    h = ridge_horse(tmp_path)
    low = lower(calf_x, pelvis_z)
    rider = {**low, "torso": torso(), **(extra or {})}
    carve = carve_horse(h, {k: v for k, v in low.items() if k.startswith(("muslo", "pantorrilla"))}, 0.5)
    sad = rel = None
    if with_saddle:
        sad = build_saddle(h, SaddleSpec(ancho=40, largo=30, alto=5, bajada=8, encaje=encaje), (0, 0, RAD))
        if relieve:
            rel = relieve_saddle(h, sad, low, ReliefSpec())
    return RidingAssembly(horse=h, rider=rider, lower=low, carve=carve, saddle=sad, relief=rel, limits=CarveLimits())


def test_a_good_assembly_validates(tmp_path):
    ra = assembly(tmp_path)
    v = validate_riding(ra, Params())
    assert v["ok"], v["problems"]
    assert v["gaps"]["pelvis ↔ silla"] >= 0.45
    assert all(g is None or g >= 0.45 for k, g in v["gaps"].items() if "↔ silla" in k and "caballo" not in k)
    assert v["info"]["altura_jinete_mm"] > 0 and len(v["info"]["caballo_mm"]) == 3


def test_carve_only_without_saddle(tmp_path):
    ra = assembly(tmp_path, with_saddle=False, calf_x=(9.3, 14.3))
    assert ra.carve.ok and ra.carve.removed_volume > 0
    v = validate_riding(ra, Params())
    assert v["ok"], v["problems"]
    assert v["info"]["silla"].startswith("sin silla") and v["info"]["tallado"]["retirado_mm3"] > 0
    assert any("pelvis ↔ caballo" in k for k in v["gaps"])


def test_rider_inside_the_uncarved_horse_names_the_pair(tmp_path):
    ra = assembly(tmp_path, with_saddle=False, calf_x=(9.3, 14.3))
    ra.carve = None                                         # sin tallar: las piernas siguen dentro del caballo
    v = validate_riding(ra, Params())
    assert not v["ok"] and any(p.startswith("pantorrilla_izq ↔ caballo") for p in v["problems"])


def test_a_rejected_carve_is_reported(tmp_path):
    ra = assembly(tmp_path, with_saddle=False, calf_x=(9.3, 14.3))
    ra.carve = carve_horse(ra.horse, {k: v for k, v in ra.lower.items() if k.startswith(("muslo", "pantorrilla"))}, 0.5,
                           CarveLimits(max_depth=0.5))
    v = validate_riding(ra, Params())
    assert not ra.carve.ok and not v["ok"] and any(p.startswith("caballo tallado:") for p in v["problems"])


def test_arm_inside_the_horse_is_a_problem(tmp_path):
    ra = assembly(tmp_path, extra={"brazo_izq": box(-3, 3, -3, 3, 2, 8)})
    v = validate_riding(ra, Params())
    assert not v["ok"] and any(p.startswith("brazo_izq ↔ caballo") for p in v["problems"])


def test_an_unrelieved_saddle_collides_with_the_legs(tmp_path):
    ra = assembly(tmp_path, relieve=False)
    v = validate_riding(ra, Params())
    assert not v["ok"] and any("↔ silla" in p and "siguen dentro" in p for p in v["problems"])


def test_a_floating_saddle_is_reported(tmp_path):
    ra = assembly(tmp_path, with_saddle=False)
    h = ra.horse
    ra.saddle = build_saddle(h, SaddleSpec(ancho=40, largo=30, alto=5, bajada=3), (0, 0, RAD + 3.5))     # cara inferior 3,5 mm sobre el lomo
    v = validate_riding(ra, Params())
    assert not v["ok"] and any("flota" in p for p in v["problems"])


def test_a_pelvis_that_does_not_rest_warns(tmp_path):
    ra = assembly(tmp_path, pelvis_z=20.0)
    v = validate_riding(ra, Params())
    assert any("pelvis no apoya" in a or "pelvis ↔ silla" in a for a in v["avisos"])


def test_a_declared_fusion_is_not_a_collision(tmp_path):
    ra = assembly(tmp_path, encaje=1.0)
    v = validate_riding(ra, Params())
    assert not any("silla ↔ caballo" in p for p in v["problems"])
    assert any("encaje" in u for u in v["info"].get("uniones", []))


def test_default_params_and_tolerances_follow_the_pipeline(tmp_path):
    from afm import equestrian as E
    assert E.RIDING_OVERLAP_TOL == 1e-6 and E.RIDING_GAP_REL_TOL == 0.1
    ra = assembly(tmp_path)
    assert validate_riding(ra)["ok"]                         # sin params usa 0,5 / 1,2


def test_a_failed_relief_is_reported_once_not_against_the_base_saddle(tmp_path):
    ra = assembly(tmp_path, calf_x=(13.7, 18.7))             # el despeje deja una pared fina y se rechaza
    ra.relief = relieve_saddle(ra.horse, ra.saddle, ra.lower, ReliefSpec(skirt_thick=0.8))
    assert ra.relief.manifold is None
    v = validate_riding(ra, Params())
    assert not v["ok"] and any(p.startswith("silla:") for p in v["problems"])
    assert not any("siguen dentro" in p and "↔ silla" in p for p in v["problems"])
