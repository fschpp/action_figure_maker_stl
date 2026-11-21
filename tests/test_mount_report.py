"""M12: las monturas en el informe, la lista de piezas y el nivel de `compat`."""
import json
import os
import shutil

import pytest

from afm import cli, compat
from afm import mount as MT
from afm import props as P
from afm import solids as S
from afm.catalog import Catalog
from afm.recipe import Recipe

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECETA = os.path.join(ROOT, "recetas", "muneco.receta.json")
ARMA = "espada-prueba-01@1"
N = "montura_1_pelvis_izq"


# ---------------------------------------------------------------- partes puras
def test_report_by_mount_reparte_por_nombre_y_quita_el_prefijo():
    val = {"montura": {N: {}, "montura_2_torso_der": {}},
           "problems": [f"{N}: choca con la pierna", "brazo: otra cosa"],
           "avisos": [f"{N}: la punta no mira hacia abajo", "montura_2_torso_der: se desliza"]}
    r = MT.report_by_mount(val)
    assert r[N] == {"problemas": ["choca con la pierna"], "avisos": ["la punta no mira hacia abajo"]}
    assert r["montura_2_torso_der"] == {"problemas": [], "avisos": ["se desliza"]}
    assert MT.report_by_mount({"problems": [f"{N}: x"]}) == {}          # sin monturas no inventa nada


@pytest.mark.parametrize("rec,nivel", [({"problemas": ["x"], "avisos": ["y"]}, "no"), ({"problemas": [], "avisos": ["y"]}, "aviso"),
                                       ({"problemas": [], "avisos": []}, "ok")])
def test_level_of(rec, nivel):
    assert MT.level_of(rec) == nivel


def _recipe():
    return Recipe.from_dict(json.load(open(RECETA)))


def test_el_nivel_de_compat_sube_con_la_montura():
    base = {"ok": True, "problems": [], "avisos": [], "piezas": {"cabeza": {"nivel": "ok"}}}
    assert compat.entry_from_validation(_recipe(), "k", {}, base)["nivel"] == "ok"
    assert "monturas" not in compat.entry_from_validation(_recipe(), "k", {}, base)      # sin monturas, la entrada es la de siempre
    con_aviso = {**base, "montura": {N: {}}, "avisos": [f"{N}: se desliza"]}
    e = compat.entry_from_validation(_recipe(), "k", {}, con_aviso)
    assert e["nivel"] == "aviso" and e["monturas"][N]["nivel"] == "aviso" and e["monturas"][N]["avisos"] == ["se desliza"]
    con_problema = {**con_aviso, "ok": False, "problems": [f"{N}: choca con pierna_izq"]}
    e = compat.entry_from_validation(_recipe(), "k", {}, con_problema)
    assert e["nivel"] == "no" and e["monturas"][N]["problemas"] == ["choca con pierna_izq"]


def test_la_montura_sin_piezas_de_catalogo_tambien_da_nivel():
    val = {"ok": True, "problems": [], "avisos": [f"{N}: x"], "montura": {N: {}}}
    assert compat.entry_from_validation(_recipe(), "k", {}, val)["nivel"] == "aviso"


# ---------------------------------------------------------------- `afm armar` de punta a punta
@pytest.fixture(scope="module")
def armados(tmp_path_factory):
    base = tmp_path_factory.mktemp("m12")
    c = str(base / "linea")
    shutil.copytree(ROOT, c, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    Catalog.open(c).add_accessory(S.to_printable_trimesh(P.make_prop(P.GripSpec(), 10.0)), "espada-prueba-01",
                                  P.accessory_grip(P.GripSpec(), 10.0), descripcion="espada")
    d = json.load(open(RECETA))
    out = {}
    for tag, montura in (("sin", None), ("con", {"arma": ARMA, "lugar": "pelvis", "ancla": [23.0, 0.0, 38.0], "direccion": [0.0, 0.0, 1.0]})):
        rec = dict(d, accesorios=[ARMA])
        if montura:
            rec["ajustes"] = {**d["ajustes"], "monturas": [montura]}
        path = base / f"{tag}.receta.json"
        path.write_text(json.dumps(rec))
        folder = str(base / tag)
        assert cli.main(["armar", c, str(path), "--out", folder, "--name", "fig"]) == 0
        out[tag] = folder
    return c, out


def test_el_informe_trae_las_monturas_con_su_nivel(armados):
    _, out = armados
    info = json.load(open(os.path.join(out["con"], "fig_informe.json")))
    m = info["monturas"][N]
    assert m["arma"] == ARMA and m["huesped"] == "torso" and m["lado"] == "izq" and len(m["abertura_mm"]) == 2
    assert m["nivel"] == "aviso" and any("no mira hacia abajo" in a for a in m["avisos"])      # direccion +Z: la punta mira hacia arriba
    assert m["problemas"] == []
    assert "monturas" not in json.load(open(os.path.join(out["sin"], "fig_informe.json")))


def test_la_lista_de_piezas_trae_las_monturas(armados):
    _, out = armados
    lst = json.load(open(os.path.join(out["con"], "fig_lista_de_piezas.json")))
    assert lst["monturas"][N]["nivel"] == "aviso" and lst["accesorios"][0]["pieza"] == ARMA       # y los accesorios siguen ahí
    md = open(os.path.join(out["con"], "fig_lista_de_piezas.md"), encoding="utf-8").read()
    assert "## Monturas" in md and N in md and "no mira hacia abajo" in md
    sin = json.load(open(os.path.join(out["sin"], "fig_lista_de_piezas.json")))
    assert "monturas" not in sin
    assert "## Monturas" not in open(os.path.join(out["sin"], "fig_lista_de_piezas.md"), encoding="utf-8").read()


def test_compat_json_refleja_la_montura(armados):
    c, out = armados
    cat = Catalog.open(c)
    k_con, _ = compat.recipe_key(Recipe.load(os.path.join(out["con"], "fig.receta.json")), cat)
    k_sin, _ = compat.recipe_key(Recipe.load(os.path.join(out["sin"], "fig.receta.json")), cat)
    e_con, e_sin = compat.CompatCache(c).get(k_con), compat.CompatCache(c).get(k_sin)
    assert e_con["monturas"][N]["nivel"] == "aviso" and e_con["nivel"] in ("aviso", "no")
    assert "monturas" not in e_sin
