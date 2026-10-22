"""Lista de piezas de catálogo (plan de piezas intercambiables, §4.5)."""
import json
import os

from afm import pipeline as P


class _Res:
    def __init__(self, report):
        self.report = report


def _rep():
    return {"piezas": {"pierna_izq": {"id": "pierna-guemes", "version": 2, "sha256": "ab" * 32, "origen": "guemes", "lugar": "cadera",
                                      "compat": {"nivel": "aviso", "avisos": ["escalón 1,07 mm"]}},
                       "brazo_izq": {"id": "brazo-guemes", "version": 1, "sha256": "cd" * 32, "origen": "guemes", "lugar": "hombro",
                                     "compat": {"nivel": "ok", "avisos": []}, "avisos": ["sin mano"]}}}


def test_sin_piezas_no_hay_lista():
    assert P.parts_list(_Res({}), "x") is None
    assert P.parts_list(_Res({"piezas": {}}), "x") is None


def test_lista_y_markdown():
    lst = P.parts_list(_Res(_rep()), "belgrano")
    assert lst["formato"] == "afm-lista-piezas-1" and lst["figura"] == "belgrano"
    assert lst["piezas"]["pierna_izq"]["pieza"] == "pierna-guemes@2"
    assert lst["piezas"]["pierna_izq"]["compat"] == "aviso"
    assert lst["piezas"]["brazo_izq"]["avisos"] == ["sin mano"]
    md = P.parts_list_markdown(lst)
    assert "| pierna_izq | pierna-guemes@2 | guemes | aviso |" in md and "**pierna_izq**: escalón 1,07 mm" in md
    json.dumps(lst)
