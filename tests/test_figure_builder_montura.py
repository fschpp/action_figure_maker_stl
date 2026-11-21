"""M13: armas enganchadas al cuerpo desde la ventana «Armar figura desde catálogo» (lógica sin GTK, `FigureBuilder`)."""
import json
import os
import shutil

import pytest

from afm import props as P
from afm import solids as S
from afm.catalog import Catalog
from afm.gui import figure_builder as FB

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
ARMA = "espada-prueba-01@1"


@pytest.fixture()
def b(tmp_path):
    dst = str(tmp_path / "ejemplo")
    shutil.copytree(EJEMPLO, dst, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    Catalog.open(dst).add_accessory(S.to_printable_trimesh(P.make_prop(P.GripSpec(), 10.0)), "espada-prueba-01",
                                    P.accessory_grip(P.GripSpec(), 10.0), descripcion="espada")
    fb = FB.FigureBuilder(dst)
    for place, ref in (("cabeza", "cabeza-muneco-01@1"), ("torso", "torso-muneco-01@1"), ("brazo", "brazo-muneco-01@1"),
                       ("pierna", "pierna-muneco-01@1"), ("mano", "mano-muneco-01@1")):
        fb.set_choice(place, ref)
    return fb


def test_sin_monturas_la_receta_es_la_de_siempre(b):
    assert "monturas" not in b.recipe().ajustes
    assert b.mount_weapons() == []


def test_solo_se_engancha_un_accesorio_marcado(b):
    with pytest.raises(ValueError, match="no está entre los accesorios"):
        b.add_mount(ARMA)
    b.set_accessory(ARMA)
    i = b.add_mount("espada-prueba-01", "pelvis", "izq", (23, 0, 38))           # sin versión: toma la del accesorio marcado
    assert i == 0 and b.mounts[0].arma == ARMA and b.mounts[0].ancla == (23.0, 0.0, 38.0)
    assert b.mounts[0].direccion == FB.MOUNT_DEFAULT_DIRECTION                   # punta hacia abajo por defecto
    with pytest.raises(ValueError, match="no está entre los accesorios"):
        b.add_mount("espada-prueba-01@2")


def test_la_montura_va_a_la_receta_y_la_receta_es_valida(b):
    b.set_accessory(ARMA)
    b.add_mount(ARMA, "torso", "der", (-12, 0, 50), (0, 0, -1), holgura=0.35, pared=1.5)
    r = b.recipe()
    assert r.ajustes["monturas"] == [b.mounts[0].to_dict()] and r.ajustes["monturas"][0]["lado"] == "der"
    assert r.mount_problems() == [] and b.problems() == []


def test_valores_mal_formados_se_rechazan_y_no_cambian_nada(b):
    b.set_accessory(ARMA)
    with pytest.raises(ValueError, match="lugar"):
        b.add_mount(ARMA, "cabeza")
    with pytest.raises(ValueError, match="holgura"):
        b.add_mount(ARMA, holgura=9.0)
    with pytest.raises(ValueError, match="vector nulo"):
        b.add_mount(ARMA, direccion=(0, 0, 0))
    assert b.mounts == []
    b.add_mount(ARMA, ancla=(1, 2, 3))
    with pytest.raises(ValueError, match="lado"):
        b.set_mount(0, lado="centro")
    with pytest.raises(ValueError, match="desconocido"):
        b.set_mount(0, tipo="clip")
    assert b.mounts[0].lado == "izq" and b.mounts[0].ancla == (1.0, 2.0, 3.0)
    with pytest.raises(ValueError, match="número 3"):
        b.remove_mount(3)


def test_editar_y_quitar(b):
    b.set_accessory(ARMA)
    b.add_mount(ARMA, ancla=(1, 2, 3))
    b.add_mount(ARMA, "torso", "der", (4, 5, 6))
    b.set_mount(0, ancla=(7, 8, 9), holgura=0.3)
    assert b.mounts[0].ancla == (7.0, 8.0, 9.0) and b.mounts[0].holgura == 0.3 and b.mounts[0].lugar == "pelvis"
    b.remove_mount(0)
    assert len(b.mounts) == 1 and b.mounts[0].lugar == "torso"


def test_quitar_el_accesorio_quita_sus_monturas(b):
    b.set_accessory(ARMA)
    b.add_mount(ARMA)
    b.set_accessory(ARMA, False)
    assert b.mounts == [] and "monturas" not in b.recipe().ajustes


def test_la_receta_con_montura_se_guarda_y_se_vuelve_a_abrir(b):
    b.set_accessory(ARMA)
    b.add_mount(ARMA, "pelvis", "izq", (23, 0, 38), holgura=0.3, estaciones=2)
    path = b.save_recipe()
    again = FB.FigureBuilder(b.root)
    assert again.load_recipe(path) == []
    assert [m.to_dict() for m in again.mounts] == [m.to_dict() for m in b.mounts]
    assert again.recipe().ajustes["monturas"] == b.recipe().ajustes["monturas"]       # guardar de nuevo no pierde la montura


def test_una_receta_con_montura_mal_formada_se_rechaza_nombrandola(b):
    b.set_accessory(ARMA)
    b.add_mount(ARMA)
    path = b.save_recipe()
    d = json.load(open(path))
    d["ajustes"]["monturas"] += [{"arma": ARMA, "lugar": "pelvis", "ancla": [0, 0], "direccion": [0, 0, -1]}]
    json.dump(d, open(path, "w"))
    with pytest.raises(ValueError, match="montura 2"):                       # no se abre a medias ni se pierde la montura sin avisar
        FB.FigureBuilder(b.root).load_recipe(path)


def test_una_montura_en_un_lugar_que_la_ventana_no_ofrece_se_descarta_con_aviso(b):
    b.set_accessory(ARMA)
    b.add_mount(ARMA, ancla=(23, 0, 38))
    path = b.save_recipe()
    d = json.load(open(path))
    d["ajustes"]["monturas"].append({"arma": ARMA, "lugar": "brazo", "ancla": [0, 0, 0], "direccion": [0, 0, -1]})
    json.dump(d, open(path, "w"))
    again = FB.FigureBuilder(b.root)
    warns = again.load_recipe(path)
    assert len(again.mounts) == 1 and len(warns) == 1 and "montura 2" in warns[0].lower() and "brazo" in warns[0]


def test_la_montura_cambia_la_clave_de_geometria(b):
    b.set_accessory(ARMA)
    k0 = b._geometry_key()
    b.add_mount(ARMA, ancla=(23, 0, 38))
    assert b._geometry_key() != k0


def test_armar_con_montura_deja_su_estado_y_se_borra_al_cambiarla(b, tmp_path):
    b.set_accessory(ARMA)
    b.add_mount(ARMA, "pelvis", "izq", (23.0, 0.0, 38.0), (0, 0, 1))          # punta hacia arriba: no retiene → aviso
    assert b.mount_status(0) is None and " — " in b.mount_label(0) and b.mount_label(0).count(" — ") == 1
    info = b.generate(str(tmp_path / "out"), progress=lambda *_: None)
    assert "monturas" in info
    st = b.mount_status(0)
    assert st["nivel"] == "aviso" and any("no mira hacia abajo" in a for a in st["avisos"]) and st["problemas"] == []
    assert b.mount_label(0).endswith(" — aviso")
    b.set_mount(0, direccion=(0, 0, -1))
    assert b.mount_status(0) is None                                         # ya no es de lo que se armó
