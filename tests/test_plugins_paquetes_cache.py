"""Plugins de articulación, Fase 1 tarea 4: `recipe_key` con los tipos, `tipos_articulacion` en `paquete.json`, aviso de `instalar`."""
import copy
import json
import os
import shutil
import zipfile

import pytest

from afm import compat
from afm import instalar as I
from afm import joint_types as JT
from afm import paquete as Q
from afm.catalog import Catalog
from afm.params import Params
from afm.recipe import Recipe
from test_exportar import OWNER, add_arm, cat  # noqa: F401  (cat es una fixture)
from test_instalar import dest, export  # noqa: F401  (dest es una fixture)
from test_paquete import coleccion

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


class _Fake(JT.Mushroom):
    """Un «plugin»: un hongo con otro id y versión."""
    id = "fake_t"
    version = 1


@pytest.fixture()
def fake(monkeypatch):
    t = _Fake()
    monkeypatch.setitem(JT.REGISTRY, "fake_t", t)
    return t


def _params_with(type_id):
    return Params(joints={"hombro": {"type": type_id, "params": {}}})


# ------------------------------------------------------------------------------------------------ recipe_key
def test_internal_types_leave_the_key_alone():
    assert JT.cache_key_info(Params()) == {}
    assert JT.cache_key_info(Params(shoulder_detent=True, hip_detent=True)) == {}


def test_plugin_type_enters_the_key_with_version_and_module_hash(fake):
    info = JT.cache_key_info(_params_with("fake_t"))
    assert set(info) == {"fake_t"} and info["fake_t"]["version"] == 1 and len(info["fake_t"]["sha256"]) == 64


def test_updating_a_plugin_changes_the_key(fake):
    a = JT.cache_key_info(_params_with("fake_t"))
    fake.version = 2
    b = JT.cache_key_info(_params_with("fake_t"))
    assert a != b


def test_recipe_key_changes_with_plugin_version_but_not_for_internal_types(tmp_path, fake, monkeypatch):
    dst = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, dst, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    cat_ = Catalog.open(dst)
    recipe = Recipe.load(os.path.join(dst, "recetas", "muneco.receta.json"))
    base, _ = compat.recipe_key(recipe, cat_)
    assert compat.recipe_key(recipe, cat_)[0] == base
    from afm import recipe as R
    real = R.recipe_params
    monkeypatch.setattr(R, "recipe_params", lambda r, c: _params_with("fake_t"))
    k1, _ = compat.recipe_key(recipe, cat_)
    fake.version = 3
    k2, _ = compat.recipe_key(recipe, cat_)
    assert k1 != base and k1 != k2
    monkeypatch.setattr(R, "recipe_params", real)
    assert compat.recipe_key(recipe, cat_)[0] == base


# ------------------------------------------------------------------------------------------------ paquete.json
def _manifest(**kw):
    d = {"formato": Q.FORMAT, "id": "demo", "nombre": "Demo", "version": "1.0.0", "descripcion": "x", "tipo": "coleccion",
         "autores": [{"nombre": "a", "github": "a"}], "licencia": "CC0-1.0", "origen": "propia", "contenido": {"pines": []}, **kw}
    return d


def test_tipos_articulacion_roundtrip_and_absent_by_default():
    p = Q.Paquete.from_dict(_manifest())
    assert p.tipos_articulacion == [] and "tipos_articulacion" not in p.to_dict()
    p = Q.Paquete.from_dict(_manifest(tipos_articulacion=[{"id": "ball", "api": 1}]))
    assert p.tipos_articulacion == [{"id": "ball", "api": 1}]
    assert Q.Paquete.from_dict(p.to_dict()) == p
    assert not any("tipos_articulacion" in a for a in p.avisos)


def test_tipos_articulacion_bad_shapes():
    with pytest.raises(Q.PaqueteError):
        Q.Paquete.from_dict(_manifest(tipos_articulacion=["ball"]))
    with pytest.raises(Q.PaqueteError):
        Q.Paquete.from_dict(_manifest(tipos_articulacion=[{"id": "ball", "api": "1"}]))


def test_tipos_articulacion_validation():
    p = Q.Paquete.from_dict(_manifest(tipos_articulacion=[{"id": "Ball!", "api": 1}, {"id": "ok", "api": 0}, {"id": "ok", "api": 1}]))
    texts = " ".join(str(i) for i in p.issues())
    assert "no es un id de tipo válido" in texts and "repetido" in texts and "1 o más" in texts


def test_schema_accepts_tipos_articulacion():
    js = pytest.importorskip("jsonschema")
    v = js.Draft202012Validator(Q.schema())
    assert not list(v.iter_errors({**coleccion(), "tipos_articulacion": [{"id": "ball", "api": 1}]}))
    assert list(v.iter_errors({**coleccion(), "tipos_articulacion": [{"id": "ball"}]}))


# ------------------------------------------------------------------------------------------------ instalar
def _zip_with_types(cat, tmp_path, types, name="t.zip"):
    z = export(cat, tmp_path, name, id="conplugin")
    with zipfile.ZipFile(z) as src:
        files = {n: src.read(n) for n in src.namelist()}
    m = json.loads(files["paquete.json"])
    m["tipos_articulacion"] = types
    files["paquete.json"] = json.dumps(m).encode()
    out = str(tmp_path / ("u_" + name))
    with zipfile.ZipFile(out, "w") as o:
        for n, b in files.items():
            o.writestr(n, b)
    return out


def test_instalar_warns_about_a_missing_joint_type_and_never_installs_code(cat, dest, tmp_path):
    add_arm(cat)
    z = _zip_with_types(cat, tmp_path, [{"id": "tcrotch", "api": 1}])
    before = set(JT.REGISTRY)
    plan = I.plan_install(z, dest.root)
    assert plan.ok
    assert any("tipo de articulación «tcrotch»" in i.message and "no está instalado" in i.message for i in plan.warnings)
    I.apply(plan)
    assert set(JT.REGISTRY) == before


def test_instalar_is_quiet_when_the_type_is_there_and_warns_on_api_mismatch(cat, dest, tmp_path):
    add_arm(cat)
    z = _zip_with_types(cat, tmp_path, [{"id": "detent", "api": 1}], "a.zip")
    assert not any("tipo" in i.message for i in I.plan_install(z, dest.root).warnings)
    z2 = _zip_with_types(cat, tmp_path, [{"id": "detent", "api": 9}], "b.zip")
    assert any("API 9" in i.message for i in I.plan_install(z2, dest.root).warnings)
