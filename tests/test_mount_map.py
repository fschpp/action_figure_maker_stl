"""M08: mapa de espacio libre del flanco (`mount_map`, `pipeline.mount_flank_map`, `afm montura-mapa`)."""
import json
import os
import shutil

import numpy as np
import pytest
import trimesh

from afm import cli
from afm import mount_map as MM

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECETA = os.path.join(ROOT, "recetas", "muneco.receta.json")


def _box(x0, x1, y0=-10.0, y1=10.0, z0=0.0, z1=20.0):
    return trimesh.creation.box(bounds=[[x0, y0, z0], [x1, y1, z1]])


def _map(obstacles, lado="izq", need=2.95, **kw):
    return MM.flank_map(_box(-10, 10), obstacles, need, lado=lado, n_points=3000, **kw)


def test_needed_height_con_los_numeros_del_plan():
    assert MM.needed_height(0.8) == pytest.approx(0.8 + 0.5 + 1.2 + 0.45)          # ≈ 2,95 (plan §5)
    assert MM.needed_height(1.2, clearance=0.35, wall=1.5, member_clearance=0.6) == pytest.approx(1.2 + 0.7 + 1.5 + 0.54)


def test_la_altura_libre_es_la_distancia_al_obstaculo_por_la_normal():
    fm = _map([_box(13, 16)])                                  # el flanco +X está en x = 10: hueco de 3 mm
    on_side = fm.points[:, 0] > 9.9
    assert on_side.sum() > 100
    assert fm.free_mm[on_side] == pytest.approx(3.0, abs=0.45)                     # error ≤ el muestreo de los miembros
    assert fm.fits()[on_side].mean() > 0.9 and set(fm.levels()[on_side]) <= {1, 2}


def test_si_el_hueco_es_menor_que_lo_que_necesita_no_cabe():
    fm = _map([_box(12, 16)])                                  # 2 mm de hueco < 2,95
    on_side = fm.points[:, 0] > 9.9
    assert fm.free_mm[on_side].max() < 2.5 and not fm.fits()[on_side].any()
    assert set(fm.levels()[on_side]) == {0}


def test_sin_obstaculos_cabe_de_sobra():
    fm = _map([])
    assert (fm.free_mm == MM.DEFAULT_MAX_MM).all() and (fm.levels() == 2).all()


def test_un_obstaculo_fuera_del_ancho_del_puente_no_cuenta():
    lejos = _box(11, 16, y0=4.0, y1=9.0)                      # a 1 mm del flanco, pero solo en y > 4
    fm = _map([lejos], lateral=2.0)
    on_side = (fm.points[:, 0] > 9.9) & (np.abs(fm.points[:, 1]) < 1.5)
    assert on_side.sum() > 20 and (fm.free_mm[on_side] >= MM.DEFAULT_MAX_MM - 1e-9).all()
    cerca = (fm.points[:, 0] > 9.9) & (fm.points[:, 1] > 6.0)
    assert cerca.any() and fm.free_mm[cerca].max() < 2.5


def test_el_lado_der_es_el_espejo_del_izq():
    izq = _map([_box(13, 16)], "izq")
    der = _map([_box(-16, -13)], "der")
    assert (izq.points[:, 0] > 0).all() and (der.points[:, 0] < 0).all()
    assert izq.fits().mean() == pytest.approx(der.fits().mean(), abs=0.03)
    assert der.free_mm[der.points[:, 0] < -9.9] == pytest.approx(3.0, abs=0.45)


def test_el_mapa_es_determinista():
    a, b = _map([_box(13, 16)]), _map([_box(13, 16)])
    assert np.array_equal(a.points, b.points) and np.array_equal(a.free_mm, b.free_mm)


def test_resumen_por_franjas_y_nube_de_puntos():
    fm = _map([_box(13, 16, z0=10.0, z1=20.0), _box(11, 16, z0=0.0, z1=9.0)])     # abajo 1 mm de hueco, arriba 3 mm
    s = MM.summarize(fm, z_step=5.0)
    por_z = {f["z"][0]: f for f in s["franjas"]}
    assert por_z[0.0]["cabe_pct"] < 40 and por_z[15.0]["cabe_pct"] > 80
    assert s["cabe"]["z"][0] >= 9.0
    cloud = MM.to_point_cloud(fm)
    cols = {tuple(c) for c in np.asarray(cloud.colors)}
    assert MM.COLORS[0] in cols and (MM.COLORS[1] in cols or MM.COLORS[2] in cols)
    md = MM.markdown(s)
    assert "2.95" in md and "Cabe en z" in md


def test_sin_ningun_punto_donde_quepa():
    s = MM.summarize(_map([_box(11, 16)]))                    # 1 mm de hueco en todo el flanco
    assert s["cabe"] is None and s["cabe_pct"] == 0.0
    assert "No hay ningún punto" in MM.markdown(s)


def test_lado_desconocido():
    with pytest.raises(ValueError, match="lado"):
        MM.flank_points(_box(-10, 10), "centro")


def test_cli_montura_mapa_sobre_el_muneco(tmp_path, capsys):
    cat = str(tmp_path / "linea")
    shutil.copytree(ROOT, cat, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    out = str(tmp_path / "mapa")
    assert cli.main(["montura-mapa", cat, RECETA, "--out", out]) == 0
    txt = capsys.readouterr().out
    assert "necesita 2.95 mm" in txt
    s = json.load(open(os.path.join(out, "mapa.json")))
    assert s["host"] == "torso" and s["lado"] == "izq" and s["puntos"] > 200 and s["cabe"] is not None
    assert all(os.path.exists(os.path.join(out, f)) for f in ("mapa.ply", "mapa.json", "mapa.md"))
    pc = trimesh.load(os.path.join(out, "mapa.ply"))
    assert len(pc.vertices) == s["puntos"]
    # el muñeco: el flanco de la cadera está libre y, a la altura del brazo, queda menos espacio
    zb = {f["z"][0]: f for f in s["franjas"]}
    assert zb[36.0]["cabe_pct"] > 80
    assert zb[44.0]["cabe_pct"] < zb[36.0]["cabe_pct"] - 10


def test_cli_montura_mapa_con_arma_usa_el_espesor_de_su_hoja(tmp_path, capsys):
    from afm import props as P
    from afm import solids as S
    from afm.catalog import Catalog
    from afm.mount import analyze_blade

    cat = str(tmp_path / "linea")
    shutil.copytree(ROOT, cat, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    prop = P.make_prop(P.GripSpec(), 10.0)
    Catalog.open(cat).add_accessory(S.to_printable_trimesh(prop), "espada-prueba-01", P.accessory_grip(P.GripSpec(), 10.0), descripcion="espada")
    t = analyze_blade(prop).blade_thickness
    out = str(tmp_path / "mapa")
    assert cli.main(["montura-mapa", cat, RECETA, "--arma", "espada-prueba-01@1", "--out", out]) == 0
    s = json.load(open(os.path.join(out, "mapa.json")))
    assert s["necesita_mm"] == pytest.approx(MM.needed_height(t, 0.25, 1.2, 0.5), abs=0.06)   # pared y holgura de la figura de ejemplo
    assert cli.main(["montura-mapa", cat, RECETA, "--arma", "no-existe@1", "--out", out]) == 2
