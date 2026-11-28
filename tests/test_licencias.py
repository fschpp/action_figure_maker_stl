"""D23 · licencia efectiva del armado y CREDITOS.txt (`licencias.py`, PLAN_catalogos_online.md §5.8)."""
import itertools
import json
import os
import shutil

import pytest

from afm import cli
from afm import licencias as L
from afm import paquete as Q
from afm.catalog import Catalog

EJ = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECETA = os.path.join(EJ, "recetas", "muneco.receta.json")


def F(ref, lic, lugar="torso", paquete="p"):
    return L.Fuente(lugar, ref, paquete=paquete, version="1.0.0", autores=[{"nombre": "Ana", "github": "ana"}], licencia=lic, github="ana/repo")


def eff(*lics):
    return L.efectiva([F(f"pieza-{i}@1", lic, paquete=f"p{i}") for i, lic in enumerate(lics)])


# ---------------------------------------------------------------------------------------------------------- matriz de licencias

@pytest.mark.parametrize("lic,nc,sa,nd,reservada", [
    ("CC0-1.0", 0, 0, 0, 0), ("Unlicense", 0, 0, 0, 0), ("MIT", 0, 0, 0, 0), ("Apache-2.0", 0, 0, 0, 0),
    ("CC-BY-4.0", 0, 0, 0, 0), ("CC-BY-SA-4.0", 0, 1, 0, 0), ("CC-BY-ND-4.0", 0, 0, 1, 0), ("CC-BY-NC-4.0", 1, 0, 0, 0),
    ("CC-BY-NC-SA-4.0", 1, 1, 0, 0), ("CC-BY-NC-ND-4.0", 1, 0, 1, 0), ("GPL-3.0-or-later", 0, 1, 0, 0),
    (Q.ALL_RIGHTS_RESERVED, 0, 0, 0, 1),
])
def test_traits_de_cada_licencia_de_la_lista_cerrada(lic, nc, sa, nd, reservada):
    t = L.traits(lic)
    assert (t.nc, t.sa, t.nd, t.reservada) == (bool(nc), bool(sa), bool(nd), bool(reservada)) and t.conocida


def test_toda_licencia_aceptada_tiene_traits_conocidos():
    assert all(L.traits(x).conocida for x in Q.LICENSES)
    assert not L.traits("").conocida and not L.traits("Inventada-1.0").conocida


def test_una_nc_vuelve_no_comercial_todo_el_armado():
    e = eff("CC-BY-4.0", "CC-BY-NC-4.0", "CC0-1.0")
    assert not e.comercial and e.restringe and e.por_nc == ["pieza-1@1"] and "no comercial" in e.etiqueta


def test_una_sa_obliga_a_compartir_igual_y_nombra_la_pieza():
    e = eff("CC-BY-4.0", "CC-BY-SA-4.0")
    assert e.comercial and e.compartir_igual and e.por_sa == ["pieza-1@1"] and not e.conflictos


def test_solo_libres_no_restringe_y_pide_credito():
    e = eff("CC0-1.0", "MIT", "CC-BY-4.0")
    assert not e.restringe and e.atribucion and e.comercial
    assert not eff("CC0-1.0", "Unlicense").atribucion and not eff("CC0-1.0").restringe


def test_reservada_pide_permiso_y_no_es_comercial():
    e = eff("CC0-1.0", Q.ALL_RIGHTS_RESERVED)
    assert e.reservadas and not e.comercial and e.por_reservada == ["pieza-1@1"]


def test_dos_sa_distintas_son_conflicto_pero_la_misma_dos_veces_no():
    assert eff("CC-BY-SA-4.0", "GPL-3.0-or-later").conflictos
    assert eff("CC-BY-SA-3.0", "CC-BY-SA-4.0").conflictos
    assert not eff("CC-BY-SA-4.0", "CC-BY-SA-4.0").conflictos


def test_nd_con_sa_es_conflicto_y_nd_solo_no():
    assert eff("CC-BY-ND-4.0", "CC-BY-SA-4.0").conflictos
    e = eff("CC-BY-ND-4.0", "CC0-1.0")
    assert e.sin_derivadas and not e.conflictos and e.restringe


def test_la_licencia_efectiva_no_depende_del_orden():
    lics = ["CC-BY-4.0", "CC-BY-NC-4.0", "CC-BY-SA-4.0", "MIT"]
    base = eff(*lics).to_dict()
    for perm in itertools.permutations(lics):
        d = eff(*perm).to_dict()
        assert {k: d[k] for k in ("comercial", "compartir_igual", "sin_derivadas", "licencias")} == \
               {k: base[k] for k in ("comercial", "compartir_igual", "sin_derivadas", "licencias")}


def test_piezas_sin_paquete_o_sin_licencia_no_imponen_nada_pero_se_listan():
    e = L.efectiva([L.Fuente("torso", "t@1"), F("b@1", "", paquete="q")])
    assert not e.restringe and e.propias == ["t@1"] and e.sin_licencia == ["b@1"]
    assert any("b@1" in x for x in e.lineas())


# ---------------------------------------------------------------------------------------------------------- de dónde sale cada pieza

def _registro(root, paquetes):
    with open(os.path.join(root, "paquetes.json"), "w", encoding="utf-8") as fh:
        json.dump({"formato": "afm-paquetes-1", "paquetes": paquetes}, fh)


@pytest.fixture
def linea(tmp_path):
    root = str(tmp_path / "linea")
    shutil.copytree(EJ, root, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    return root


def test_fuentes_lee_paquetes_json_y_deja_sin_paquete_lo_demas(linea):
    _registro(linea, {"heroe": {"version": "1.2.0", "nombre": "Héroe", "github": "ana/heroe", "licencia": "CC-BY-NC-4.0", "origen": "propia",
                                "autores": [{"nombre": "Ana", "github": "ana"}], "refs": {"piezas": ["brazo-muneco-01@1"]}}})
    fs = L.fuentes(linea, [("brazo", "brazo-muneco-01@1"), ("torso", "torso-muneco-01@1")])
    assert fs[0].paquete == "heroe" and fs[0].licencia == "CC-BY-NC-4.0" and fs[0].url == "https://github.com/ana/heroe"
    assert fs[1].sin_paquete and "Ana (https://github.com/ana)" == fs[0].autores_texto()


def test_si_dos_paquetes_traen_la_misma_pieza_gana_la_mas_restrictiva(linea):
    _registro(linea, {"a": {"licencia": "CC0-1.0", "refs": {"piezas": ["x@1"]}}, "b": {"licencia": "CC-BY-NC-4.0", "refs": {"piezas": ["x@1"]}}})
    assert L.fuentes(linea, [("torso", "x@1")])[0].paquete == "b"


def test_un_registro_ilegible_no_frena_los_creditos(linea):
    with open(os.path.join(linea, "paquetes.json"), "w") as fh:
        fh.write("{no es json")
    assert L.fuentes(linea, [("torso", "x@1")])[0].sin_paquete


def test_usos_de_receta_resuelve_el_id_sin_version_y_suma_accesorios(linea):
    from afm.recipe import Recipe

    r = Recipe.load(RECETA)
    r.lugares["torso"] = "torso-muneco-01"
    cat = Catalog.open(linea)
    usos = dict(L.usos_de_receta(r, cat))
    assert usos["torso"] == "torso-muneco-01@1" and usos["brazo"] == "brazo-muneco-01@1"
    assert "pelvis" not in usos


# ---------------------------------------------------------------------------------------------------------- archivos

def test_creditos_txt_lista_autor_licencia_y_enlace_de_cada_pieza():
    e = L.efectiva([F("torso-x@2", "CC-BY-NC-4.0"), L.Fuente("brazo", "brazo-y@1")])
    txt = L.creditos_texto(e, "heroe")
    assert "heroe" in txt and "torso-x@2" in txt and "CC-BY-NC-4.0" in txt and "https://github.com/ana/repo" in txt
    assert "Ana (https://github.com/ana)" in txt and "brazo-y@1" in txt and "no comercial" in txt and "asesoramiento" in txt


def test_armar_deja_creditos_junto_al_stl_y_amplia_la_lista_de_piezas(linea, tmp_path, capsys):
    _registro(linea, {"heroe": {"version": "1.0.0", "github": "ana/heroe", "licencia": "CC-BY-NC-4.0", "origen": "propia",
                                "autores": [{"nombre": "Ana"}], "refs": {"piezas": ["brazo-muneco-01@1"]}}})
    out = str(tmp_path / "fig")
    code = cli.main(["armar", linea, RECETA, "--out", out])
    txt = capsys.readouterr().out
    assert code == 0, txt
    assert "AVISO: licencia efectiva del armado" in txt                    # lo dice antes de terminar, en el progreso
    assert os.path.exists(os.path.join(out, "CREDITOS.txt")) and os.path.exists(os.path.join(out, "muneco_ensamblada.stl"))
    assert "CC-BY-NC-4.0" in open(os.path.join(out, "CREDITOS.txt"), encoding="utf-8").read()
    lst = json.load(open(os.path.join(out, "muneco_lista_de_piezas.json")))
    assert lst["licencia"]["comercial"] is False and lst["licencia"]["por_nc"] == ["brazo-muneco-01@1"]
    md = open(os.path.join(out, "muneco_lista_de_piezas.md"), encoding="utf-8").read()
    assert "Licencia efectiva" in md and "brazo-muneco-01@1" in md
    inf = json.load(open(os.path.join(out, "muneco_informe.json")))
    assert inf["licencia"]["archivo"] == "CREDITOS.txt" and inf["archivos"]["creditos"] == "CREDITOS.txt"


def test_armar_sin_paquetes_deja_creditos_y_no_avisa(linea, tmp_path, capsys):
    out = str(tmp_path / "fig")
    assert cli.main(["armar", linea, RECETA, "--out", out]) == 0
    assert "AVISO: licencia efectiva" not in capsys.readouterr().out
    assert "sin paquete" in open(os.path.join(out, "CREDITOS.txt"), encoding="utf-8").read()


def test_el_constructor_de_la_gui_calcula_la_licencia_sin_armar(linea):
    from afm.gui.figure_builder import FigureBuilder

    _registro(linea, {"h": {"licencia": "CC-BY-SA-4.0", "refs": {"piezas": ["torso-muneco-01@1"]}}})
    b = FigureBuilder(linea)
    assert b.license_report() is None
    b.set_choice("torso", "torso-muneco-01@1")
    e = b.license_report()
    assert e.compartir_igual and e.restringe and "compartir igual" in e.resumen()


def test_claves_de_licencias_iguales_en_los_tres_idiomas():
    import re

    base = os.path.join(os.path.dirname(L.__file__), "locales")
    d = {l: json.load(open(os.path.join(base, f"{l}.json"), encoding="utf-8"))["licencias"] for l in ("es", "en", "pt")}
    assert set(d["en"]) == set(d["es"]) == set(d["pt"])
    ph = lambda s: sorted(re.findall(r"%\{\w+\}", s))
    assert all(ph(d[l][k]) == ph(d["es"][k]) for l in ("en", "pt") for k in d["es"])
