"""M10: las monturas de armas en la receta (`ajustes["monturas"]`), la clave de compatibilidad y `assemble`."""
import json
import os
import shutil

import pytest

from afm import cli, compat
from afm import props as P
from afm import solids as S
from afm.catalog import Catalog
from afm.recipe import Recipe, RecipeError, assemble, load_mounts

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECETA = os.path.join(ROOT, "recetas", "muneco.receta.json")
ARMA = "espada-prueba-01@1"


def _montura(**over):
    d = {"arma": ARMA, "lugar": "pelvis", "ancla": [23.0, 0.0, 38.0], "direccion": [0.0, 0.0, 1.0]}
    d.update(over)
    return d


def _d(monturas=None, accesorios=(ARMA,), **over):
    d = json.load(open(RECETA))
    d["accesorios"] = list(accesorios)
    if monturas is not None:
        d["ajustes"] = {**d["ajustes"], "monturas": monturas}
    d.update(over)
    return d


@pytest.fixture(scope="module")
def cat(tmp_path_factory):
    c = str(tmp_path_factory.mktemp("m10") / "linea")
    shutil.copytree(ROOT, c, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    cat = Catalog.open(c)
    cat.add_accessory(S.to_printable_trimesh(P.make_prop(P.GripSpec(), 10.0)), "espada-prueba-01",
                      P.accessory_grip(P.GripSpec(), 10.0), descripcion="espada")
    return cat


def _key(d, cat):
    return compat.recipe_key(Recipe.from_dict(d), cat)[0]


# ---------------------------------------------------------------- formato y validación
def test_una_receta_sin_monturas_no_cambia():
    r = Recipe.from_dict(_d())
    assert "monturas" not in r.ajustes and r.mount_specs() == []
    assert r.mount_problems() == []


def test_las_monturas_hacen_ida_y_vuelta(tmp_path):
    r = Recipe.from_dict(_d([_montura(lado="der", holgura=0.3)]))
    out = tmp_path / "r.json"
    r.save(str(out))
    again = Recipe.load(str(out))
    assert again.to_dict() == r.to_dict()
    (spec,) = again.mount_specs()
    assert spec.lado == "der" and spec.holgura == 0.3 and spec.ancla == (23.0, 0.0, 38.0)


def test_la_montura_se_guarda_completa_aunque_se_escriba_con_defectos():
    r = Recipe.from_dict(_d([_montura()]))
    (m,) = r.ajustes["monturas"]
    assert m["lado"] == "izq" and m["tipo"] == "lazo" and m["holgura"] == 0.25 and m["pared"] is None and m["estaciones"] == 1


@pytest.mark.parametrize("cambio,texto", [
    (dict(monturas=[_montura(arma="otra-01@1")]), "no está entre los accesorios"),
    (dict(monturas=[_montura(arma="espada-prueba-01@2")]), "accesorio de la receta"),
    (dict(monturas=[_montura(lugar="cola")]), "lugar"),
    (dict(monturas=[{**_montura(), "colorcito": 1}]), "campo desconocido"),
    (dict(monturas=[{"arma": ARMA}]), "le falta"),
    (dict(monturas=[_montura(holgura=9)]), "holgura"),
    (dict(monturas="si"), "lista"),
    (dict(monturas=[_montura()], accesorios=()), "no está entre los accesorios"),
])
def test_monturas_mal_formadas_se_rechazan(cambio, texto):
    with pytest.raises(RecipeError, match=texto):
        Recipe.from_dict(_d(**cambio))


def test_el_arma_sin_version_coincide_con_el_accesorio_con_version():
    r = Recipe.from_dict(_d([_montura(arma="espada-prueba-01")]))
    assert r.mount_problems() == []


# ---------------------------------------------------------------- clave de compatibilidad
def test_la_clave_sin_monturas_es_la_de_siempre(cat):
    base = _key(_d(), cat)
    assert _key(_d(monturas=[]), cat) == base            # lista vacía = sin lista
    assert _key({**_d(), "accesorios": []}, cat) == base  # los accesorios nunca entraron en la clave


def test_la_clave_cambia_con_la_montura(cat):
    base = _key(_d(), cat)
    k = _key(_d([_montura()]), cat)
    assert k != base
    assert _key(_d([_montura(ancla=[23.0, 1.0, 38.0])]), cat) != k      # mover el ancla
    assert _key(_d([_montura(holgura=0.35)]), cat) != k
    assert _key(_d([_montura(lado="der")]), cat) != k
    assert _key(_d([_montura(pared=1.5)]), cat) != k
    assert _key(_d([_montura(direccion=[0.0, 0.2, 1.0])]), cat) != k


def test_la_clave_no_depende_de_como_se_escribio_la_montura(cat):
    corta = _montura()
    larga = _montura(lado="izq", tipo="lazo", holgura=0.25, pared=None, estaciones=1)
    assert _key(_d([corta]), cat) == _key(_d([larga]), cat)


def test_la_clave_cambia_si_cambia_el_arma(cat, tmp_path):
    k = _key(_d([_montura()]), cat)
    c2 = str(tmp_path / "linea2")
    shutil.copytree(cat.root, c2)
    cat2 = Catalog.open(c2)
    meta, mesh = cat2.load("espada-prueba-01")
    mesh.apply_scale(1.05)
    meta.sha256 = ""
    cat2.add_piece(mesh, meta, new_version=True)
    d = _d([_montura(arma="espada-prueba-01@1")])
    d2 = _d([_montura(arma="espada-prueba-01@2")], accesorios=["espada-prueba-01@2"])
    assert _key(d2, cat2) != k and _key(d, cat2) != _key(d2, cat2)


# ---------------------------------------------------------------- assemble
def test_load_mounts_entrega_spec_y_arma(cat):
    ((spec, arma),) = load_mounts(Recipe.from_dict(_d([_montura()])), cat)
    assert spec.arma == ARMA and arma.volume() > 1.0
    assert load_mounts(Recipe.from_dict(_d()), cat) == []


def test_assemble_suma_la_montura_al_torso(cat):
    sin = assemble(Recipe.from_dict(_d()), cat.root)
    con = assemble(Recipe.from_dict(_d([_montura()])), cat.root)
    assert sin.res.mounts == {}
    (name, mb), = con.res.mounts.items()
    assert name == "montura_1_pelvis_izq" and mb.host == "torso"
    assert con.res.pieces["torso"].volume() > sin.res.pieces["torso"].volume() + 5.0
    for n, m in sin.res.pieces.items():                                  # lo demás queda igual
        if n != "torso":
            assert con.res.pieces[n].volume() == pytest.approx(m.volume(), abs=1e-6)


def test_assemble_con_un_ancla_en_el_aire_da_un_error_de_receta(cat):
    with pytest.raises(RecipeError, match="montura"):
        assemble(Recipe.from_dict(_d([_montura(ancla=[60.0, 0.0, 38.0])])), cat.root)


def test_armar_cli_con_montura(cat, tmp_path, capsys):
    rec = tmp_path / "r.receta.json"
    rec.write_text(json.dumps(_d([_montura()])))
    code = cli.main(["armar", cat.root, str(rec), "--out", str(tmp_path / "fig")])
    txt = capsys.readouterr().out
    assert code == 0, txt
    assert "montura_1_pelvis_izq" in txt                                  # la validación la nombra (aviso de gravedad: la punta mira hacia arriba)
    info = json.load(open(tmp_path / "fig" / "muneco_informe.json"))
    assert "montura_1_pelvis_izq" in json.dumps(info)
    assert Recipe.load(str(tmp_path / "fig" / "muneco.receta.json")).mount_specs()[0].arma == ARMA   # la receta se guarda con la montura
