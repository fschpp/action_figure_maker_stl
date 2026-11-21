"""E14: caballo, asiento y ajustes en `FigureBuilder` (lógica sin GTK) con el caballo de referencia y el muñeco de ejemplo."""
import json
import os
import shutil

import pytest

from afm import recipe as R
from afm.catalog import Catalog
from afm.equestrian import SAFE_MAX_DEPTH
from afm.gui import figure_builder as FB
from afm.horse_library import HorseLibrary

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
HORSE = os.path.join(os.path.dirname(__file__), "..", "examples", "referencia", "caballo", "horse_baseless.stl")
pytestmark = pytest.mark.skipif(not os.path.exists(HORSE), reason="falta examples/referencia/caballo/horse_baseless.stl")
PIECES = (("cabeza", "cabeza-muneco-01@1"), ("torso", "torso-muneco-01@1"), ("brazo", "brazo-muneco-01@1"),
          ("pierna", "pierna-muneco-01@1"), ("mano", "mano-muneco-01@1"))


def make(tmp, horse=True):
    dst = str(tmp / "ejemplo")
    shutil.copytree(EJEMPLO, dst, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    fb = FB.FigureBuilder(dst)
    for place, ref in PIECES:
        fb.set_choice(place, ref)
    if horse:
        fb.add_horse(HORSE, "referencia", nombre="Referencia", escala=3.0, yaw=180.0)
    return fb


@pytest.fixture(scope="module")
def fb(tmp_path_factory):
    return make(tmp_path_factory.mktemp("e14"))


@pytest.fixture(autouse=True)
def _reset(request):
    if "fb" in request.fixturenames:
        request.getfixturevalue("fb").clear_ride()
    yield


def test_sin_caballo_todo_es_como_antes(tmp_path):
    b = make(tmp_path, horse=False)
    assert b.horse is None and b.ride_spec() is None and b.ride_problems() == [] and b.horses() == []
    assert b.recipe().cabalgadura == {} and "cabalgadura" not in b.recipe().to_dict() and b.problems() == []
    with pytest.raises(ValueError, match="Elegí un caballo"):
        b.set_seat((0, 0, 0))
    with pytest.raises(ValueError, match="Falta elegir el caballo"):
        b.check_ride()


def test_elegir_caballo_y_marcar_el_asiento(fb):
    assert [h.ref for h in fb.horses()] == ["referencia@1"]
    fb.set_horse("referencia")                                                   # sin versión: la última
    assert fb.horse == "referencia@1" and fb.ride_spec() is None                 # falta el asiento
    assert any("falta marcar el asiento" in p for p in fb.problems())
    z = fb.seat_on_back(0.0, -20.0)[2]
    assert 20 < z < 40 and fb.ride_spec().asiento == (0.0, -20.0, z)
    assert fb.problems() == [] and fb.recipe().cabalgadura["caballo"] == "referencia@1"
    fb.set_horse("referencia@1")                                                 # el mismo: no borra el asiento
    assert fb.ride["asiento"] is not None
    with pytest.raises(ValueError, match="No hay caballo bajo el punto"):
        fb.seat_on_back(500.0, 500.0)
    with pytest.raises(ValueError, match="no tiene la versión 7"):
        fb.set_horse("referencia@7")
    fb.set_horse(None)
    assert fb.horse is None and fb.recipe().cabalgadura == {}


def test_los_ajustes_invalidos_no_cambian_nada(fb):
    fb.set_horse("referencia")
    fb.set_seat((0, -20, 27.0), inclinacion=5)
    before = dict(fb.ride)
    with pytest.raises(ValueError, match="solo van con el modo"):
        fb.set_saddle(ancho=30)                                                  # la silla no va con «tallado»
    with pytest.raises(ValueError, match="máximo seguro"):
        fb.set_ride_limits(max_depth=SAFE_MAX_DEPTH + 1)
    with pytest.raises(ValueError):
        fb.set_seat((0, 0))
    with pytest.raises(ValueError, match="uno de"):
        fb.set_ride_mode("galope")
    assert fb.ride == before
    fb.set_ride_limits(max_depth=8.0)
    fb.set_ride_clearance(0.45)
    fb.set_ride_mode("ambos")
    fb.set_saddle(ancho=30.0, largo=40.0)
    fb.set_relief(skirt_drop=5.0)
    d = fb.recipe().cabalgadura
    assert d["modo"] == "ambos" and d["silla"]["ancho"] == 30.0 and d["relieve"]["skirt_drop"] == 5.0 and d["holgura"] == 0.45
    assert d["limites"]["max_depth"] == 8.0 and d["inclinacion"] == 5.0
    fb.set_ride_mode("silla")                                                    # «silla» descarta los límites del tallado
    assert fb.ride["limites"] is None and fb.ride["silla"] is not None
    fb.set_ride_mode("tallado")                                                  # y «tallado» descarta la silla
    assert fb.ride["silla"] is None and fb.ride["relieve"] is None


def test_un_caballo_con_la_figura_sin_soportes_no_se_arma(fb):
    fb.set_horse("referencia")
    fb.set_seat((0, -20, 27.0))
    fb.flat = True
    try:
        assert any("sin soportes" in p for p in fb.problems())
        with pytest.raises(ValueError, match="sin soportes"):
            fb.generate("/tmp/no-se-escribe")
    finally:
        fb.flat = False


def test_la_receta_guardada_se_vuelve_a_cargar(fb, tmp_path):
    fb.set_horse("referencia")
    fb.seat_on_back(0.0, -20.0)
    fb.set_ride_limits(max_depth=10.0)
    fb.ride_hash = "d" * 64                                                      # (lo que dejaría una validación)
    path = fb.save_recipe()
    assert json.load(open(path))["cabalgadura"]["hash_geometria"] == "d" * 64
    other = FB.FigureBuilder(fb.root)
    assert other.load_recipe(path) == []
    assert other.horse == "referencia@1" and other.ride == fb.ride and other.ride_hash == "d" * 64
    assert other.recipe().to_dict() == fb.recipe().to_dict()
    fb.set_seat((0, -19, 27.0))                                                  # tocar el asiento descarta el hash viejo
    assert fb.ride_hash is None and "hash_geometria" not in fb.recipe().cabalgadura


def test_recetas_con_caballo_que_falta_o_mal_formadas_avisan(fb, tmp_path):
    fb.set_horse("referencia")
    fb.seat_on_back(0.0, -20.0)
    path = fb.save_recipe()
    d = json.load(open(path))
    d["cabalgadura"]["asiento"] = "mal"
    bad = tmp_path / "mal.receta.json"
    bad.write_text(json.dumps(d))
    other = FB.FigureBuilder(fb.root)
    with pytest.raises(ValueError, match="cabalgadura"):                         # `Recipe.load` ya rechaza una cabalgadura mal formada
        other.load_recipe(str(bad))
    assert other.horse is None
    d["cabalgadura"]["asiento"] = [0, -20, 27]
    d["cabalgadura"]["caballo"] = "otro-01@1"
    gone = tmp_path / "falta.receta.json"
    gone.write_text(json.dumps(d))
    warns = other.load_recipe(str(gone))
    assert len([w for w in warns if "otro-01@1" in w]) == 1 and other.horse == "otro-01@1"   # se conserva: la receta fija ese caballo
    assert any("no está en la biblioteca" in p for p in other.problems())
    lib = HorseLibrary(fb.root)                                                  # el STL cambió: aviso de revalidar
    d["cabalgadura"]["caballo"] = "referencia@1"
    d["cabalgadura"]["sha256"] = "a" * 64
    chg = tmp_path / "cambio.receta.json"
    chg.write_text(json.dumps(d))
    assert any("cambió; revalidar" in w for w in other.load_recipe(str(chg))) and lib.versions("referencia") == [1]


def test_check_ride_arma_una_vez_y_guarda_el_hash(fb, monkeypatch):
    calls = []
    real = R.assemble
    monkeypatch.setattr(R, "assemble", lambda *a, **k: calls.append(1) or real(*a, **k))
    fb.set_horse("referencia")
    fb.seat_on_back(0.0, -20.0)
    fb.set_ride_limits(max_depth=10.0)
    fit = fb.check_ride()
    assert fit.ok and fit.stage == "ok" and fb.ride_hash and fb.ride_spec().hash_geometria == fb.ride_hash
    h = fb.ride_hash
    fb.set_seat((0.0, -20.0, fb.ride["asiento"][2]), inclinacion=2.0)           # otro asiento: no vuelve a armar la figura
    assert fb.ride_hash is None
    fit2 = fb.check_ride()
    assert fit2.ok and len(calls) == 1 and fb.ride_hash != h
    fb.seat_on_back(0.0, 0.0)                                                    # ahí el tallado retira demasiado
    fit3 = fb.check_ride()
    assert not fit3.ok and fit3.stage == "tallado" and fb.ride_hash is None and len(calls) == 1
    fb.set_choice("brazo", None)                                                 # cambia la figura: se arma de nuevo
    fb.set_choice("brazo", "brazo-muneco-01@1")
    fb.set_scale("pierna", 1.0)
    assert fb.check_ride().stage == "tallado" and len(calls) == 1               # misma receta: sigue en el caché


def test_generate_con_caballo_cuenta_el_resultado(fb, monkeypatch, tmp_path):
    import afm.cli as cli

    fb.set_horse("referencia")
    fb.set_seat((0, -20, 27.0))
    out = {"archivos": {}, "cabalgadura": {"ok": False}, "validacion": {"ok": True}}      # asiento rechazado: no se exportó el conjunto
    monkeypatch.setattr(cli, "run_armar", lambda *a, **k: out)
    info = fb.generate(str(tmp_path / "o"))
    assert info["ensamblada"] is None and fb.ride_hash is None
    out2 = {"archivos": {"ensamblada": "f_ensamblada.stl"}, "cabalgadura": {"ok": True, "hash_geometria": "e" * 64}}
    monkeypatch.setattr(cli, "run_armar", lambda *a, **k: out2)
    info = fb.generate(str(tmp_path / "o"))
    assert info["ensamblada"].endswith("f_ensamblada.stl") and fb.ride_hash == "e" * 64
