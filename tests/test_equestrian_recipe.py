"""E12: `Recipe.cabalgadura` (ida y vuelta, recetas viejas, validaciones) y `ride` (clave propia, hash del caballo y de la geometría)."""
import dataclasses
import inspect
import json
import os

import pytest
import trimesh

from afm import compat
from afm.catalog import Catalog
from afm.equestrian import CarveLimits, HorseError, ReliefSpec, RidingFitter, SaddleSpec, sha256_file
from afm.horse_library import HorseLibrary
from afm.recipe import Recipe, RecipeError
from afm.ride import RideSpec, geometry_hash, geometry_warnings, resolve_horse, riding_key
from test_equestrian_validate import assembly

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECIPE = os.path.join(ROOT, "recetas", "muneco.receta.json")
HORSE_DIR = os.path.join(os.path.dirname(__file__), "..", "examples", "referencia", "caballo")
HORSE = os.path.join(HORSE_DIR, "horse_baseless.stl")
SHA_A, SHA_B = "a" * 64, "b" * 64


def ride(**kw):
    base = {"caballo": "pardo-01@1", "sha256": SHA_A, "asiento": [0, -20, 8.5]}
    return {**base, **kw}


def with_ride(**kw):
    r = Recipe.load(RECIPE)
    d = r.to_dict()
    d["cabalgadura"] = ride(**kw)
    return Recipe.from_dict(d)


FULL = dict(direccion=[0, 1, 0], inclinacion=5, modo="ambos", silla={"ancho": 30, "largo": 40, "alto": 4, "bajada": 6},
            relieve={"clearance": 0.6, "skirt_drop": 5}, holgura=0.45,
            limites={"max_depth": 8, "min_wall": 1.5, "max_removed_frac": 0.02, "zone": [-20, -40, -5, 20, 40, 30]}, hash_geometria="c" * 64)


# ------------------------------------------------------------------------------------------------------------ formato
def test_an_old_recipe_loads_and_writes_exactly_as_before():
    r = Recipe.load(RECIPE)
    assert r.cabalgadura == {} and r.ride_spec() is None and "cabalgadura" not in r.to_dict()
    assert Recipe.from_dict(r.to_dict()).to_dict() == r.to_dict()
    old = Recipe("p", "l", {"torso": "t"}, {}, {}, [], {})                    # el constructor posicional de siempre sigue andando
    assert old.cabalgadura == {} and old.ride_problems() == []


def test_round_trip_through_json_file(tmp_path):
    r = with_ride(**FULL)
    p = tmp_path / "r.json"
    r.save(str(p))
    r2 = Recipe.load(str(p))
    assert r2.to_dict() == r.to_dict() == json.load(open(p)) and r2.ride_spec() == r.ride_spec()
    spec = r2.ride_spec()
    assert spec.silla == SaddleSpec(ancho=30, largo=40, alto=4, bajada=6) and spec.relieve == ReliefSpec(clearance=0.6, skirt_drop=5)
    assert spec.limites == CarveLimits(max_depth=8, min_wall=1.5, max_removed_frac=0.02, zone=(-20, -40, -5, 20, 40, 30))
    assert spec.holgura == 0.45 and spec.hash_geometria == "c" * 64 and spec.asiento == (0.0, -20.0, 8.5)


def test_the_stored_form_is_canonical_and_minimal():
    r = with_ride()
    assert r.cabalgadura == {"caballo": "pardo-01@1", "sha256": SHA_A, "asiento": [0.0, -20.0, 8.5], "direccion": [0.0, 1.0, 0.0],
                             "inclinacion": 0.0, "modo": "tallado"}                  # sin claves vacías
    assert RideSpec.from_dict(r.cabalgadura).to_dict() == r.cabalgadura


@pytest.mark.parametrize("bad", [
    {"caballo": "pardo-01"}, {"caballo": "Pardo@1"}, {"caballo": "pardo-01@0"}, {"sha256": "xyz"}, {"asiento": [0, 1]},
    {"asiento": [0, 1, float("nan")]}, {"asiento": "abc"}, {"direccion": [0, 0, 1]}, {"direccion": [0, 0]}, {"modo": "otro"},
    {"inclinacion": "5"}, {"inclinacion": True}, {"silla": {"ancho": 30}}, {"relieve": {"clearance": 0.5}},
    {"modo": "silla", "limites": {"max_depth": 5}}, {"modo": "tallado", "limites": {"max_depth": 11}},
    {"modo": "ambos", "limites": {"max_depth": 11}}, {"modo": "ambos", "limites": {"otro": 1}},
    {"modo": "silla", "silla": {"ancho": -1}}, {"modo": "silla", "silla": {"ancho": 3, "otro": 1}},
    {"modo": "silla", "relieve": {"skirt_thick": 0}}, {"holgura": -0.1}, {"holgura": "x"}, {"hash_geometria": "abc"},
    {"otro": 1}])
def test_a_malformed_cabalgadura_is_rejected(bad):
    d = Recipe.load(RECIPE).to_dict()
    d["cabalgadura"] = ride(**bad)
    with pytest.raises(RecipeError, match="cabalgadura"):
        Recipe.from_dict(d)


def test_missing_fields_and_wrong_types_are_rejected():
    d = Recipe.load(RECIPE).to_dict()
    for k in ("caballo", "sha256", "asiento"):
        d["cabalgadura"] = {a: b for a, b in ride().items() if a != k}
        with pytest.raises(RecipeError, match=k):
            Recipe.from_dict(d)
    for v in ("pardo", ["x"], 3):
        d["cabalgadura"] = v
        with pytest.raises(RecipeError):
            Recipe.from_dict(d)
    r = Recipe.load(RECIPE)
    r.cabalgadura = {"caballo": "x"}                                          # armada a mano y mal: `problems` lo dice
    assert any("cabalgadura" in p for p in r.problems())


def test_the_rest_of_the_recipe_is_untouched_and_replace_keeps_it():
    r, plain = with_ride(**FULL), Recipe.load(RECIPE)
    d = r.to_dict()
    d.pop("cabalgadura")
    assert d == plain.to_dict()
    assert dataclasses.replace(r, ajustes={}).cabalgadura == r.cabalgadura


# ------------------------------------------------------------------------------------------------------------ claves
def test_changing_horse_or_seat_never_changes_the_recipe_key_but_changes_the_riding_key():
    cat = Catalog.open(ROOT)
    plain, a = Recipe.load(RECIPE), with_ride()
    b = with_ride(caballo="tordillo@2", sha256=SHA_B)
    c = with_ride(asiento=[0, 10, 8.5])
    ka, kb = compat.recipe_key(a, cat)[0], compat.recipe_key(b, cat)[0]
    assert compat.recipe_key(plain, cat)[0] == ka == kb == compat.recipe_key(c, cat)[0]
    keys = {riding_key(ka, r.ride_spec()) for r in (a, b, c, with_ride(modo="silla"), with_ride(holgura=0.7), with_ride(sha256=SHA_B))}
    assert len(keys) == 6
    assert riding_key(ka, a.ride_spec()) == riding_key(ka, with_ride(hash_geometria="d" * 64).ride_spec())   # el hash derivado es salida, no entrada
    assert riding_key(ka, a.ride_spec()) != riding_key("0" * 64, a.ride_spec())


# ------------------------------------------------------------------------------------------------------------ caballo
def test_resolve_horse_warns_when_the_hash_changed(tmp_path):
    src = tmp_path / "h.stl"
    trimesh.creation.box(extents=(10, 20, 10)).export(src)
    lib = HorseLibrary(str(tmp_path / "cat"))
    m = lib.add(str(src), "pardo-01")
    ok = RideSpec.from_dict(ride(sha256=m.spec.sha256))
    meta, warns = resolve_horse(ok, lib)
    assert meta == m and warns == []
    _, warns = resolve_horse(RideSpec.from_dict(ride(sha256=SHA_B)), lib)
    assert len(warns) == 1 and "revalidar" in warns[0]
    with pytest.raises(HorseError, match="no hay un caballo"):
        resolve_horse(RideSpec.from_dict(ride(caballo="otro@1")), lib)
    with pytest.raises(HorseError, match="versión 2"):
        resolve_horse(RideSpec.from_dict(ride(caballo="pardo-01@2")), lib)


# ------------------------------------------------------------------------------------------------------------ geometría
def test_geometry_hash_is_stable_and_follows_the_derived_geometry(tmp_path):
    ra = assembly(tmp_path, with_saddle=True, calf_x=(9.3, 14.3))
    h = geometry_hash(ra)
    assert len(h) == 64 and geometry_hash(ra) == h
    ra2 = assembly(tmp_path, with_saddle=True, calf_x=(9.3, 14.3))             # armado otra vez desde cero: mismo hash
    assert geometry_hash(ra2) == h
    ra3 = assembly(tmp_path, with_saddle=True, calf_x=(8.8, 14.3))             # otra pierna → otro tallado y otra silla
    assert geometry_hash(ra3) != h
    spec = RideSpec.from_dict(ride(modo="ambos", hash_geometria=h))
    assert geometry_warnings(spec, ra) == []
    assert "revalidar" in geometry_warnings(spec, ra3)[0]
    assert geometry_warnings(RideSpec.from_dict(ride()), ra3) == []            # sin hash guardado no hay con qué comparar
    only_saddle = assembly(tmp_path, with_saddle=True)
    only_saddle.carve = None
    assert len(geometry_hash(only_saddle)) == 64
    no_derived = assembly(tmp_path, with_saddle=False)
    no_derived.carve = None
    with pytest.raises(HorseError, match="geometría derivada"):
        geometry_hash(no_derived)


def test_fit_kwargs_match_the_fitter_signature():
    spec = RideSpec.from_dict(ride(**FULL))
    params = set(inspect.signature(RidingFitter.fit).parameters) - {"self"}
    assert set(spec.fit_kwargs()) <= params
    assert spec.carve_limits().max_depth == 8 and RideSpec.from_dict(ride()).carve_limits(min_wall=1.4).min_wall == 1.4


@pytest.mark.skipif(not os.path.exists(HORSE), reason="falta examples/referencia/caballo/horse_baseless.stl")
def test_a_saved_ride_reproduces_the_same_assembly(tmp_path):
    from afm import recipe as R

    lib = HorseLibrary(str(tmp_path / "cat"))
    meta = lib.add(HORSE, "referencia", escala=3.0, yaw=180.0)
    h = lib.open("referencia")
    asm = R.assemble(Recipe.load(RECIPE), ROOT, sit=True)
    fitter = RidingFitter(h, asm, limits=CarveLimits(max_depth=10.0))
    z = fitter.back_z(0.0, -20.0)
    spec = RideSpec(caballo=meta.ref, sha256=meta.spec.sha256, asiento=(0.0, -20.0, z), modo="tallado", limites=CarveLimits(max_depth=10.0))
    fit = fitter.fit(**spec.fit_kwargs())
    assert fit.ok, fit.problems
    spec = spec.with_geometry_hash(geometry_hash(fit.assembly))
    r = dataclasses.replace(Recipe.load(RECIPE), cabalgadura=spec.to_dict())
    p = tmp_path / "r.json"
    r.save(str(p))
    spec2 = Recipe.load(str(p)).ride_spec()                                    # guardar, abrir y volver a calcular
    _, warns = resolve_horse(spec2, lib)
    assert warns == []
    fit2 = RidingFitter(lib.open(spec2.caballo), asm, limits=spec2.carve_limits()).fit(**spec2.fit_kwargs())
    assert fit2.ok and geometry_warnings(spec2, fit2.assembly) == []
