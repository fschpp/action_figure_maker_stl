"""Recetas `afm-receta-1` y `afm armar` (Fase 6 del plan de piezas intercambiables)."""
import json
import os
import shutil

import pytest

from afm import cli
from afm.recipe import Recipe, RecipeError

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECETA = os.path.join(ROOT, "recetas", "muneco.receta.json")


def _d(**over):
    d = json.load(open(RECETA))
    d.update(over)
    return d


def test_example_recipe_loads_and_round_trips(tmp_path):
    r = Recipe.load(RECETA)
    assert r.personaje == "muneco" and r.parts_keys()["torso"] == "torso" and "pelvis" not in r.parts_keys()
    out = tmp_path / "r.json"
    r.save(str(out))
    assert Recipe.load(str(out)).to_dict() == r.to_dict()


def test_with_waist_the_torso_is_torso_sup():
    r = Recipe.from_dict(_d(opciones={"cintura": True}, lugares={**_d()["lugares"], "pelvis": "pelvis-x"}))
    assert r.parts_keys()["torso"] == "torso_sup" and r.parts_keys()["pelvis"] == "pelvis"


@pytest.mark.parametrize("cambio,texto", [
    ({"formato": "otra"}, "formato"),
    ({"lugares": {"torso": "torso-x", "cola": "cola-1"}}, "cola"),
    ({"opciones": {"alas": True}}, "alas"),
    ({"lugares": {"brazo": "brazo-1"}}, "torso"),
    ({"opciones": {"cintura": True}}, "pelvis"),
    ({"ajustes": {"mano": {"union": "pegada"}}}, "union"),
    ({"lugares": {"torso": "Torso Raro"}}, "torso"),
])
def test_bad_recipes_are_rejected(cambio, texto):
    with pytest.raises(RecipeError, match=texto):
        Recipe.from_dict(_d(**cambio))


def test_armar_cli_builds_the_doll_only_from_catalog_parts(tmp_path, capsys):
    out = str(tmp_path / "fig")
    cat = str(tmp_path / "linea")       # `afm armar` guarda compat.json junto al catálogo: no ensuciar el del repo
    shutil.copytree(ROOT, cat, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    code = cli.main(["armar", cat, RECETA, "--out", out])
    txt = capsys.readouterr().out
    assert code == 0, txt
    assert "Validación: OK" in txt and "Pieza de catálogo en cabeza" in txt
    for f in ("muneco_ensamblada.stl", "muneco_lista_de_piezas.json", "muneco_informe.json", "muneco.receta.json"):
        assert os.path.exists(os.path.join(out, f)), f
    lst = json.load(open(os.path.join(out, "muneco_lista_de_piezas.json")))
    assert {"brazo", "pierna", "cabeza"} <= set(lst["piezas"])


def test_armar_cli_reports_a_wrong_line(tmp_path, capsys):
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps(_d(linea="otra-linea")))
    assert cli.main(["armar", ROOT, str(bad), "--out", str(tmp_path / "o")]) == 2
    assert "otra-linea" in capsys.readouterr().err
