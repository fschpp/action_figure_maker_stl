"""E13: `afm caballo agregar|listar|verificar` y `afm armar` con una receta que trae `cabalgadura` (caballo de referencia + muñeco)."""
import dataclasses
import json
import os
import shutil

import pytest
import trimesh

from afm import cli
from afm import recipe as R
from afm.cli import main
from afm.equestrian import CarveLimits, RidingFitter
from afm.horse_library import HorseLibrary
from afm.ride import RideSpec

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECIPE = os.path.join(ROOT, "recetas", "muneco.receta.json")
HORSE = os.path.join(os.path.dirname(__file__), "..", "examples", "referencia", "caballo", "horse_baseless.stl")
pytestmark = pytest.mark.skipif(not os.path.exists(HORSE), reason="falta examples/referencia/caballo/horse_baseless.stl")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """Una copia del catálogo de ejemplo con el caballo de referencia ya agregado (por la CLI) y dos recetas: una que monta bien y una
    que retira demasiado del caballo."""
    base = tmp_path_factory.mktemp("e13")
    cat = str(base / "cat")
    shutil.copytree(ROOT, cat)
    assert main(["caballo", "agregar", HORSE, "--id", "referencia", "--nombre", "Referencia", "--escala", "3", "--yaw", "180", "--catalogo", cat]) == 0
    lib = HorseLibrary(cat)
    meta, horse = lib.meta("referencia@1"), lib.open("referencia@1")
    rec = R.Recipe.load(RECIPE)
    fitter = RidingFitter(horse, R.assemble(rec, cat, sit=True), limits=CarveLimits(max_depth=10.0))

    def make(y, name):
        z = fitter.back_z(0.0, y)
        spec = RideSpec(caballo=meta.ref, sha256=meta.spec.sha256, asiento=(0.0, y, z), modo="tallado", limites=CarveLimits(max_depth=10.0))
        path = str(base / name)
        dataclasses.replace(rec, cabalgadura=spec.to_dict()).save(path)
        return path

    return {"cat": cat, "base": base, "good": make(-20.0, "bien.receta.json"), "bad": make(0.0, "mal.receta.json")}


# ------------------------------------------------------------------------------------------------------------ afm caballo
def test_caballo_agregar_listar_verificar(tmp_path, capsys):
    cat = str(tmp_path / "cat")
    shutil.copytree(ROOT, cat)
    assert main(["caballo", "listar", "--catalogo", cat]) == 0
    assert "No hay caballos" in capsys.readouterr().out
    args = ["caballo", "agregar", HORSE, "--id", "pardo-01", "--nombre", "Pardo", "--escala", "3", "--yaw", "180", "--catalogo", cat]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "pardo-01@1" in out and "agregado" in out and "152,4 (largo)" in out and "132,7 (alto)" in out
    assert main(args) == 0                                                         # lo mismo otra vez: no crea nada
    assert "no se creó nada" in capsys.readouterr().out and HorseLibrary(cat).versions("pardo-01") == [1]
    assert main(["caballo", "agregar", HORSE, "--id", "pardo-01", "--escala", "2", "--catalogo", cat]) == 2
    assert "--nueva-version" in capsys.readouterr().err
    assert main(["caballo", "agregar", HORSE, "--id", "pardo-01", "--escala", "2", "--nueva-version", "--catalogo", cat]) == 0
    capsys.readouterr()
    assert main(["caballo", "listar", "--catalogo", cat]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("pardo-01@2")                  # solo la última versión
    assert main(["caballo", "listar", "--todas-las-versiones", "--catalogo", cat]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 2
    assert main(["caballo", "verificar", "--catalogo", cat]) == 0
    assert "OK" in capsys.readouterr().out
    with open(HorseLibrary(cat).path("pardo-01@1"), "ab") as f:                    # el STL administrado cambia: verificar lo encuentra
        f.write(b"x")
    assert main(["caballo", "verificar", "pardo-01@1", "--catalogo", cat]) == 1
    assert "SHA-256" in capsys.readouterr().out
    assert main(["caballo", "verificar", "pardo-01@2", "--catalogo", cat]) == 0


def test_caballo_errores_claros(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("AFM_CATALOGO", raising=False)
    assert main(["caballo", "listar"]) == 2
    assert "AFM_CATALOGO" in capsys.readouterr().err
    nada = str(tmp_path / "nada")
    assert main(["caballo", "listar", "--catalogo", nada]) == 2
    assert "no es un catálogo" in capsys.readouterr().err and not os.path.exists(nada)   # no deja carpetas sueltas
    cat = str(tmp_path / "cat")
    shutil.copytree(ROOT, cat)
    assert main(["caballo", "agregar", str(tmp_path / "no.stl"), "--id", "x", "--catalogo", cat]) == 2
    assert "no existe el STL" in capsys.readouterr().err
    assert main(["caballo", "agregar", HORSE, "--id", "Mal Id", "--catalogo", cat]) == 2
    assert "minúsculas" in capsys.readouterr().err
    monkeypatch.setenv("AFM_CATALOGO", cat)                                           # $AFM_CATALOGO reemplaza a --catalogo
    assert main(["caballo", "listar"]) == 0


# ------------------------------------------------------------------------------------------------------------ afm armar
def test_armar_con_caballo_exporta_el_conjunto(world, tmp_path, capsys):
    out = str(tmp_path / "o")
    assert main(["armar", world["cat"], world["good"], "--out", out, "--fijar-geometria"]) == 0
    txt = capsys.readouterr().out
    assert "Montaje: OK" in txt and "Caballo tallado" in txt and "holgura pierna_izq ↔ caballo" in txt
    for f in ("muneco_ensamblada.stl", "muneco_informe.json", "muneco.receta.json", "piezas/muneco_caballo.stl", "piezas/muneco_pierna_izq.stl"):
        assert os.path.isfile(os.path.join(out, f)), f
    assert not os.path.exists(os.path.join(out, "muneco.proyecto"))                   # sin .proyecto en modo ecuestre
    info = json.load(open(os.path.join(out, "muneco_informe.json")))
    cab = info["cabalgadura"]
    assert cab["ok"] and cab["etapa"] == "ok" and cab["validacion"]["ok"] and cab["tallado"]["profundidad_max_mm"] <= 10.0
    assert info["validacion"]["ok"] and cab["archivos"]["caballo_tallado"] is True
    z0 = {n: trimesh.load(os.path.join(out, "piezas", f"muneco_{n}.stl")).bounds[0][2] for n in ("caballo", "pierna_izq", "torso")}
    whole = trimesh.load(os.path.join(out, "muneco_ensamblada.stl"))
    assert whole.bounds[0][2] == pytest.approx(0.0, abs=1e-4) and z0["caballo"] == pytest.approx(0.0, abs=1e-4)   # todo con la misma traslación
    saved = json.load(open(os.path.join(out, "muneco.receta.json")))["cabalgadura"]
    assert saved["hash_geometria"] == cab["hash_geometria"]                           # --fijar-geometria
    assert "hash_geometria" not in json.load(open(world["good"]))["cabalgadura"]       # la receta de entrada no se toca


def test_la_receta_guardada_con_el_hash_vuelve_a_dar_lo_mismo(world, tmp_path, capsys):
    out1, out2 = str(tmp_path / "a"), str(tmp_path / "b")
    assert main(["armar", world["cat"], world["good"], "--out", out1, "--fijar-geometria"]) == 0
    capsys.readouterr()
    assert main(["armar", world["cat"], os.path.join(out1, "muneco.receta.json"), "--out", out2]) == 0
    assert "no coincide" not in capsys.readouterr().out
    h = [json.load(open(os.path.join(o, "muneco_informe.json")))["cabalgadura"]["hash_geometria"] for o in (out1, out2)]
    assert h[0] == h[1]


def test_un_asiento_que_no_pasa_no_exporta_nada(world, tmp_path, capsys):
    out = str(tmp_path / "o")
    assert main(["armar", world["cat"], world["bad"], "--out", out]) == 1
    txt = capsys.readouterr().out
    assert "NO SE EXPORTÓ" in txt and "«tallado»" in txt and "del caballo (máximo 3 %)" in txt
    assert not os.path.exists(os.path.join(out, "muneco_ensamblada.stl")) and not os.path.exists(os.path.join(out, "piezas"))
    cab = json.load(open(os.path.join(out, "muneco_informe.json")))["cabalgadura"]       # el informe sí queda
    assert cab["ok"] is False and cab["etapa"] == "tallado" and cab["problemas"] and "archivos" not in cab


def _no_assemble(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no tenía que armar la figura")

    monkeypatch.setattr(R, "assemble", boom)


@pytest.mark.parametrize("flag", ["--orient", "--orient-auto", "--sin-soportes", "--rellenar-muslos"])
def test_las_opciones_incompatibles_se_rechazan_antes_de_armar(world, tmp_path, capsys, monkeypatch, flag):
    _no_assemble(monkeypatch)
    assert main(["armar", world["cat"], world["good"], "--out", str(tmp_path / "o"), flag]) == 2
    assert "ERROR (caballo)" in capsys.readouterr().err


def test_un_caballo_que_falta_se_avisa_antes_de_armar(world, tmp_path, capsys, monkeypatch):
    _no_assemble(monkeypatch)
    d = json.load(open(world["good"]))
    d["cabalgadura"]["caballo"] = "referencia@9"
    p9 = tmp_path / "r9.json"
    p9.write_text(json.dumps(d))
    assert main(["armar", world["cat"], str(p9), "--out", str(tmp_path / "o")]) == 2
    assert "no tiene la versión 9" in capsys.readouterr().err
    cat2 = str(tmp_path / "cat2")                                                           # catálogo sin la biblioteca de caballos
    shutil.copytree(ROOT, cat2)
    assert main(["armar", cat2, world["good"], "--out", str(tmp_path / "o")]) == 2
    err = capsys.readouterr().err
    assert "no está en la biblioteca" in err and "afm caballo agregar" in err
    shutil.copytree(os.path.join(world["cat"], "caballos"), os.path.join(cat2, "caballos"))
    os.remove(HorseLibrary(cat2).path("referencia@1"))                                      # el STL administrado desapareció
    assert main(["armar", cat2, world["good"], "--out", str(tmp_path / "o")]) == 2
    assert "afm caballo verificar" in capsys.readouterr().err


def test_sin_cabalgadura_el_camino_es_el_de_siempre(tmp_path, monkeypatch):
    from afm import horse_cli

    def boom(*a, **k):
        raise AssertionError("una receta sin caballo no pasa por horse_cli")

    for fn in ("open_ride_horse", "armar_ecuestre"):
        monkeypatch.setattr(horse_cli, fn, boom)
    info = cli.run_armar(RECIPE, str(tmp_path / "o"), "muneco", ROOT, progress=lambda *_: None)
    assert "cabalgadura" not in info and "cabalgadura" not in info["receta"] and info["validacion"]["ok"]
    assert os.path.isfile(str(tmp_path / "o" / "muneco.proyecto"))
