"""Armar desde catálogo: brazo / pierna con o sin codo / rodilla (juguete simple de 6 puntos o completo de 10)."""
import json
import os
import shutil
import types

import pytest

from afm import compat
from afm.assembly import PartChoice, rigid_choice
from afm.catalog import Catalog
from afm.gui import figure_builder as FB
from afm.recipe import Recipe, RecipeError, articulation_points

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECETA = os.path.join(EJEMPLO, "recetas", "muneco.receta.json")


def _recipe(**opciones):
    d = json.load(open(RECETA))
    d["opciones"] = {**d.get("opciones", {}), **opciones}
    return Recipe.from_dict(d)


class _Cat:
    """Catálogo mínimo: `meta(ref)` con o sin articulación según el id."""

    def meta(self, ref):
        return types.SimpleNamespace(articulaciones=[{"name": "x"}] if "conart" in ref else [])


def _with_joints(**opciones):
    r = _recipe(**opciones)
    r.lugares["brazo"], r.lugares["pierna"] = "brazo-conart", "pierna-conart"
    return r


def test_default_recipe_is_unchanged_and_has_both_joints():
    r = _recipe()
    assert r.joint_on("brazo") and r.joint_on("pierna")
    assert "codo" not in r.to_dict()["opciones"] and "rodilla" not in r.to_dict()["opciones"]
    assert r.options() == _recipe(codo=True, rodilla=True).options()        # «con» explícito = el defecto: misma clave


def test_without_joint_is_written_and_round_trips(tmp_path):
    r = _recipe(codo=False)
    assert r.to_dict()["opciones"]["codo"] is False and "rodilla" not in r.to_dict()["opciones"]
    assert not r.joint_on("brazo") and r.joint_on("pierna")
    out = tmp_path / "r.json"
    r.save(str(out))
    assert Recipe.load(str(out)).joint_on("brazo") is False


def test_joint_options_change_the_compat_key_only_when_off():
    cat = Catalog.open(EJEMPLO)
    base = compat.recipe_key(_recipe(), cat)[0]
    assert compat.recipe_key(_recipe(codo=True), cat)[0] == base
    assert compat.recipe_key(_recipe(codo=False), cat)[0] != base
    assert compat.recipe_key(_recipe(codo=False), cat)[0] != compat.recipe_key(_recipe(rodilla=False), cat)[0]


def test_non_boolean_joint_option_is_rejected():
    d = json.load(open(RECETA))
    d["opciones"] = {"codo": "no"}
    with pytest.raises(RecipeError, match="codo"):
        Recipe.from_dict(d)


def test_rigid_choice_drops_the_joints_without_touching_the_catalog():
    cat = Catalog.open(EJEMPLO)
    ch = PartChoice.from_catalog(cat, "brazo-muneco-01")
    ch.meta.articulaciones = [{"name": "codo"}]
    rig = rigid_choice(ch)
    assert rig.meta.articulaciones == [] and ch.meta.articulaciones == [{"name": "codo"}]
    assert rig.mesh is ch.mesh
    assert rigid_choice(rig) is rig


def test_apply_joint_options_only_touches_the_members_asked_rigid():
    cat = Catalog.open(EJEMPLO)
    mk = lambda ref: PartChoice.from_catalog(cat, ref)
    choices = {"brazo": mk("brazo-muneco-01"), "pierna": mk("pierna-muneco-01"), "torso": mk("torso-muneco-01")}
    for k in ("brazo", "pierna"):
        choices[k].meta.articulaciones = [{"name": k}]
    out = _recipe(rodilla=False).apply_joint_options(choices)
    assert out["brazo"] is choices["brazo"] and out["torso"] is choices["torso"]
    assert out["pierna"].meta.articulaciones == [] and choices["pierna"].meta.articulaciones


@pytest.mark.parametrize("opc,esperado", [({}, 10), ({"codo": False}, 8), ({"rodilla": False}, 8),
                                          ({"codo": False, "rodilla": False}, 6)])
def test_articulation_points_simple_6_and_complete_10(opc, esperado):
    assert articulation_points(_full(opc), _Cat()) == esperado


def _full(opc):
    r = _with_joints(**opc)
    r.opciones["cintura"] = True               # cabeza + cintura + 2 hombros + 2 caderas = 6; + codos + rodillas = 10
    return r


def test_pieces_without_a_joint_stay_at_6_whatever_the_option():
    r = _recipe()
    r.opciones["cintura"] = True
    assert articulation_points(r, _Cat()) == 6


def test_builder_options_load_and_save_the_recipe(tmp_path):
    root = str(tmp_path / "ejemplo")
    shutil.copytree(EJEMPLO, root)
    b = FB.FigureBuilder(root, "simple")
    for place, ref in (("torso", "torso-muneco-01"), ("cabeza", "cabeza-muneco-01"), ("brazo", "brazo-muneco-01"),
                       ("pierna", "pierna-muneco-01")):
        b.set_choice(place, ref)
    assert b.options["codo"] and b.options["rodilla"] and not b.piece_has_joint("brazo")
    b.options["codo"] = False
    path = b.save_recipe()
    assert json.load(open(path))["opciones"]["codo"] is False
    b2 = FB.FigureBuilder(root, "otra")
    b2.load_recipe(path)
    assert b2.options["codo"] is False and b2.options["rodilla"] is True
