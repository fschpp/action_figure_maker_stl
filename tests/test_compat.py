"""Compatibilidad completa, caché `compat.json` e informe de matriz (Fase 7 del plan de piezas intercambiables)."""
import json
import os
import shutil

import pytest

from afm import cli, compat
from afm.catalog import Catalog
from afm.recipe import Recipe

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture()
def cat_dir(tmp_path):
    """Copia del catálogo de ejemplo: `afm armar` y `compat` escriben `compat.json` junto al catálogo."""
    dst = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, dst, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    return dst


@pytest.fixture()
def recipe(cat_dir):
    return Recipe.load(os.path.join(cat_dir, "recetas", "muneco.receta.json"))


# ------------------------------------------------------------------------------------------------ clave
def test_key_is_stable_and_lists_the_pieces(cat_dir, recipe):
    cat = Catalog.open(cat_dir)
    k1, piezas = compat.recipe_key(recipe, cat)
    k2, _ = compat.recipe_key(Recipe.from_dict(recipe.to_dict()), cat)
    assert k1 == k2 and len(k1) == 64
    assert piezas["pierna"]["ref"].startswith("pierna-muneco-01@") and len(piezas["pierna"]["sha256"]) == 64


def test_key_changes_with_options_adjustments_and_piece_version(cat_dir, recipe):
    cat = Catalog.open(cat_dir)
    base, _ = compat.recipe_key(recipe, cat)
    d = recipe.to_dict()
    d["opciones"] = {**d["opciones"], "clic_hombro": not d["opciones"]["clic_hombro"]}
    assert compat.recipe_key(Recipe.from_dict(d), cat)[0] != base
    d = recipe.to_dict()
    d["ajustes"] = {"mano": {"yaw": 15.0}}
    assert compat.recipe_key(Recipe.from_dict(d), cat)[0] != base
    # una versión nueva de una pieza (receta sin número = la última) cambia la clave; con número fijo no
    pinned = recipe.to_dict()
    pinned["lugares"]["pierna"] = cat.meta("pierna-muneco-01").ref
    k_pinned, _ = compat.recipe_key(Recipe.from_dict(pinned), cat)
    loose = recipe.to_dict()
    loose["lugares"]["pierna"] = "pierna-muneco-01"
    k_loose, _ = compat.recipe_key(Recipe.from_dict(loose), cat)
    meta, mesh = cat.load("pierna-muneco-01")
    mesh.apply_scale(1.01)
    meta.sha256 = ""
    cat.add_piece(mesh, meta, new_version=True)
    assert compat.recipe_key(Recipe.from_dict(pinned), cat)[0] == k_pinned
    assert compat.recipe_key(Recipe.from_dict(loose), cat)[0] != k_loose


# ------------------------------------------------------------------------------------------------ caché
def test_cache_round_trip_and_corrupt_file(cat_dir):
    cache = compat.CompatCache(cat_dir)
    assert cache.get("x") is None
    cache.put("x", {"ok": True})
    cache.put("y", {"ok": False})
    assert cache.get("x") == {"ok": True} and set(cache.load()["entradas"]) == {"x", "y"}
    assert not [f for f in os.listdir(cat_dir) if f.endswith(".tmp")]
    open(cache.path, "w").write("{no es json")
    assert cache.load()["entradas"] == {}
    cache.put("z", {"ok": True})                     # se recupera sobre un archivo roto
    assert cache.get("z") == {"ok": True}


def test_prune_drops_entries_of_vanished_pieces(cat_dir, recipe):
    cat = Catalog.open(cat_dir)
    key, piezas = compat.recipe_key(recipe, cat)
    cache = compat.CompatCache(cat_dir)
    cache.put(key, {"ok": True, "piezas": piezas})
    cache.put("viejo", {"ok": True, "piezas": {"pierna": {"ref": "pierna-muneco-01@9", "sha256": "0" * 64}}})
    assert cache.prune(cat) == 1 and list(cache.load()["entradas"]) == [key]


def test_entry_summarises_validation():
    r = Recipe("p", "l", {"torso": "t"})
    val = {"ok": False, "problems": ["x"], "avisos": ["a"], "gaps": {"h": 0.5, "k": 0.42},
           "piezas": {"brazo": {"nivel": "ok"}, "pierna": {"nivel": "aviso"}}}
    e = compat.entry_from_validation(r, "k", {"torso": {"ref": "t@1", "sha256": "s"}}, val, 12.34)
    assert (e["ok"], e["nivel"], e["holgura_min_mm"], e["segundos"]) == (False, "aviso", 0.42, 12.3)
    assert e["problemas"] == ["x"] and e["avisos"] == ["a"]


# ------------------------------------------------------------------------------------------------ informe
def test_matrix_report_lists_pairs_and_empty_full_section(cat_dir):
    cat = Catalog.open(cat_dir)
    md = compat.matrix_markdown(cat)
    assert md.startswith("# Compatibilidad de la línea") and "Matriz barata" in md
    assert "torso" in md and "brazo-muneco-01@1" in md
    assert "todavía no se validó ninguna figura" in md
    out = compat.write_matrix(cat)
    assert out == os.path.join(cat_dir, "COMPATIBILIDAD.md") and open(out, encoding="utf-8").read() == md


def test_matrix_report_shows_cached_results(cat_dir):
    cat = Catalog.open(cat_dir)
    compat.CompatCache(cat_dir).put("k", {"personaje": "demo", "ok": False, "nivel": "no", "holgura_min_mm": 0.31,
                                          "piezas": {"pierna": {"ref": "pierna-x@2", "sha256": "s"}},
                                          "problemas": ["pierna: choca"], "avisos": ["escalón"], "fecha": "2026-10-22T10:00:00"})
    md = compat.matrix_markdown(cat)
    assert "| demo | CON PROBLEMAS | no | 0.31 |" in md and "pierna=`pierna-x@2`" in md
    assert "**demo** (problema): pierna: choca" in md and "**demo** (aviso): escalón" in md


def test_cli_matriz_writes_the_file(cat_dir, tmp_path, capsys):
    out = str(tmp_path / "m.md")
    assert cli.main(["catalogo", "matriz", cat_dir, "--out", out]) == 0
    assert "Escrito" in capsys.readouterr().out and os.path.exists(out)


def test_cli_compat_without_recipes_says_so(cat_dir, capsys):
    shutil.rmtree(os.path.join(cat_dir, "recetas"))
    assert cli.main(["catalogo", "compat", cat_dir]) == 0
    assert "no hay recetas" in capsys.readouterr().out


def test_cli_compat_reports_a_broken_recipe(cat_dir, tmp_path, capsys):
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({**json.load(open(os.path.join(cat_dir, "recetas", "muneco.receta.json"))), "linea": "otra"}))
    assert cli.main(["catalogo", "compat", cat_dir, "--receta", str(bad)]) == 2
    assert "otra" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ completa (lenta, ≈35 s)
def test_armar_records_and_compat_reuses_the_result(cat_dir, recipe, tmp_path, capsys):
    out = str(tmp_path / "fig")
    assert cli.main(["armar", cat_dir, os.path.join(cat_dir, "recetas", "muneco.receta.json"), "--out", out]) == 0
    entradas = compat.CompatCache(cat_dir).load()["entradas"]
    assert len(entradas) == 1
    (key, e), = entradas.items()
    assert e["ok"] is True and e["personaje"] == "muneco" and e["holgura_min_mm"] > 0.3 and e["segundos"] > 0
    # `compat` encuentra el resultado de `armar` en el caché: no vuelve a armar
    res = compat.check(recipe, cat_dir)
    assert res["cache"] is True and res["clave"] == key and res["ok"] is True
    capsys.readouterr()
    assert cli.main(["catalogo", "compat", cat_dir, "--receta", os.path.join(cat_dir, "recetas", "muneco.receta.json")]) == 0
    assert "caché" in capsys.readouterr().out
    md = compat.matrix_markdown(Catalog.open(cat_dir))
    assert "| muneco | ok |" in md


def test_matrix_report_shows_only_the_latest_entry_of_each_figure(cat_dir):
    cat = Catalog.open(cat_dir)
    c = compat.CompatCache(cat_dir)
    base = {"personaje": "demo", "linea": "l", "ok": True, "nivel": "ok", "piezas": {}, "problemas": [], "avisos": []}
    c.put("vieja", {**base, "ok": False, "holgura_min_mm": 0.11, "fecha": "2026-10-20T10:00:00"})
    c.put("nueva", {**base, "holgura_min_mm": 0.47, "fecha": "2026-10-22T10:00:00"})
    c.put("otra", {**base, "personaje": "otra-fig", "holgura_min_mm": 0.5, "fecha": "2026-10-01T10:00:00"})
    md = compat.matrix_markdown(cat)
    assert md.count("| demo |") == 1 and "| demo | ok | ok | 0.47 |" in md and "0.11" not in md
    assert "| otra-fig |" in md
