"""M07: verificaciones de la montura (extracción libre, holguras a miembros, retención, imprimible, mano libre).

Primero el motor puro sobre piezas sintéticas (caja como flanco, `make_prop` como espada); después `pipeline._validate_mount`
y `validate` sobre la figura demo, incluidos los casos que tienen que fallar."""
import dataclasses

import numpy as np
import pytest

from afm import pipeline as P
from afm import props as PR
from afm import solids as S
from afm.head import HEAD_NAME
from afm.mount import (CONTACT_TOL_MM3, EXTRACT_STEP, MountError, MountSpec, attach_lazo, build_lazo, extraction_distance,
                       extraction_scan, gravity_seat_cos, handle_direction, mount_solid, tilt_from_vertical_deg)
from afm.project import demo_project
from afm.testfigure import make_doll

MIN_WALL = 1.2


def _sword():
    return PR.make_prop(PR.GripSpec(), 10.0)


def _flank():
    """Caja de 20 × 20 × 60 con la cara del flanco en x = 10 (normal +X)."""
    return S.Manifold.cube((20.0, 20.0, 60.0)).translate((-10.0, -10.0, 0.0))


def _spec(**over):
    d = dict(arma="espada@1", lugar="pelvis", ancla=(10.0, 0.0, 40.0), direccion=(0.0, 0.0, -1.0))
    d.update(over)
    return MountSpec(**d)


def _lazo(**over):
    return build_lazo(_spec(**over), _sword(), MIN_WALL, normal=(1.0, 0.0, 0.0))


def _box(x0, x1, y0, y1, z0, z1):
    return S.Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


# ---------------------------------------------------------------- motor puro
def test_direccion_y_recorrido_de_extraccion():
    lz = _lazo()
    assert np.allclose(handle_direction(lz), (0.0, 0.0, 1.0), atol=1e-6)        # empuñadura arriba: se saca hacia +Z
    # largo 36 − (guarda 2 + juego 0,2 + medio puente 1,5 − medio puente 1,5 = 2,2 hasta la cara del puente) + 0,5 de margen
    assert extraction_distance(lz) == pytest.approx(36.0 - 2.2 + 0.5, abs=0.01)
    assert extraction_distance(lz, margin=0.0) == pytest.approx(33.8, abs=0.01)


def test_la_extraccion_pasa_por_el_hueco_sin_tocar_el_cuerpo():
    lz = _lazo()
    host = attach_lazo(_flank(), lz)
    scan = extraction_scan(lz, {"flanco": host})
    assert scan["choques"] == {}
    assert scan["pasos"] == int(np.ceil(scan["distancia_mm"] / EXTRACT_STEP)) + 1


def test_un_obstaculo_en_el_camino_se_detecta_en_el_recorrido_correcto():
    lz = _lazo()
    top = lz.weapon.bounding_box()[5]
    en_el_camino = _box(10.5, 30.0, -6.0, 6.0, top + 8.0, top + 30.0)           # arriba de la empuñadura, a 8 mm
    al_costado = _box(-30.0, 5.0, -6.0, 6.0, top + 8.0, top + 30.0)             # del otro lado del cuerpo
    por_detras = _box(10.5, 30.0, -6.0, 6.0, 0.0, 5.0)                          # del lado de la punta: el arma se aleja
    scan = extraction_scan(lz, {"arriba": en_el_camino, "costado": al_costado, "punta": por_detras})
    assert set(scan["choques"]) == {"arriba"}
    h = scan["choques"]["arriba"]
    assert 8.0 < h["desde_mm"] <= 8.0 + EXTRACT_STEP + 1e-6
    assert h["max_mm3"] > CONTACT_TOL_MM3


def test_extraccion_paso_invalido():
    with pytest.raises(MountError, match="paso"):
        extraction_scan(_lazo(), {}, step=0.0)


def test_inclinacion_y_gravedad():
    assert tilt_from_vertical_deg((0, 0, -1)) == pytest.approx(0.0)
    assert tilt_from_vertical_deg((1, 0, -1)) == pytest.approx(45.0)
    assert tilt_from_vertical_deg((0, 3, 0)) == pytest.approx(90.0)
    assert gravity_seat_cos(_lazo()) == pytest.approx(1.0, abs=1e-6)             # punta hacia abajo: la gravedad apoya la guarda
    assert gravity_seat_cos(_lazo(direccion=(0.0, 0.0, 1.0))) == pytest.approx(-1.0, abs=1e-6)
    assert gravity_seat_cos(_lazo(direccion=(0.0, 1.0, 0.0))) == pytest.approx(0.0, abs=1e-6)


def test_el_puente_sin_pie_deja_pasar_el_arma():
    lz = _lazo()
    visible = mount_solid(lz)
    assert 0 < visible.volume() < lz.outer.volume()
    assert (visible ^ lz.weapon).volume() < 1e-3


def test_info_del_lazo_trae_largo_y_fin_de_guarda():
    lz = _lazo()
    assert lz.info["largo_arma_mm"] == pytest.approx(36.0, abs=0.01)
    assert lz.info["guarda_fin_mm"] == pytest.approx(2.0, abs=0.01)


# ---------------------------------------------------------------- figura demo
@pytest.fixture(scope="module")
def fig():
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0)
    body = S.to_manifold(make_doll())
    return proj, p, body


def _mounted(fig, **over):
    proj, p, body = fig
    d = dict(arma="espada@1", lugar="pelvis", ancla=(23.0, 0.0, 38.0), direccion=(0.0, 0.0, -1.0))
    d.update(over)
    return P.build(body, proj.limbs, p, proj.symmetry, mounts=[(MountSpec(**d), _sword())])


def _check(res, fig, p=None, **kw):
    out = {"problems": [], "avisos": []}
    info = P._validate_mount(res, p or fig[1], out, fig[0].limbs, **kw)
    return out, info


@pytest.fixture(scope="module")
def colgada(fig):
    """Empuñadura arriba, punta hacia abajo, al costado de la pelvis: el caso bueno."""
    return _mounted(fig)


def test_figura_demo_con_la_espada_colgada_no_tiene_problemas(fig, colgada):
    out, info = _check(colgada, fig)
    assert out["problems"] == [] and out["avisos"] == []
    (name, rec), = info.items()
    assert name == "montura_1_pelvis_izq" and rec["huesped"] == "torso"
    assert rec["componentes_huesped"] == 1 and rec["mano_libre"] is True
    assert rec["extraccion"]["choques"] == {} and rec["extraccion"]["distancia_mm"] == pytest.approx(34.3, abs=0.05)
    assert rec["inclinacion_grados"] == pytest.approx(0.0) and rec["gravedad_cos"] == pytest.approx(1.0)
    assert any(k.startswith("pierna_izq@de pie") for k in rec["holguras_mm"])          # midió contra la pierna del mismo lado
    assert all(g >= 0.9 * fig[1].clearance for g in rec["holguras_mm"].values())
    assert not any(k.startswith("pierna_der") for k in rec["holguras_mm"])             # y no contra la del otro lado


def test_punta_hacia_arriba_avisa_que_la_gravedad_la_saca(fig):
    out, info = _check(_mounted(fig, direccion=(0.0, 0.0, 1.0)), fig)
    assert any("la punta no mira hacia abajo" in a for a in out["avisos"])
    assert next(iter(info.values()))["gravedad_cos"] == pytest.approx(-1.0)


def test_otra_pieza_en_el_camino_es_un_problema(fig, colgada):
    top = next(iter(colgada.mounts.values())).lazo.weapon.bounding_box()[5]
    tapa = _box(15.0, 30.0, -8.0, 8.0, top + 6.0, top + 20.0)
    res = dataclasses.replace(colgada, pieces={**colgada.pieces, HEAD_NAME: tapa})
    out, info = _check(res, fig)
    assert any(f"choca con {HEAD_NAME}" in x and "no sale por su eje" in x for x in out["problems"])
    assert HEAD_NAME in next(iter(info.values()))["extraccion"]["choques"]


def test_arma_inclinada_hacia_el_cuerpo_choca_con_la_pierna(fig):
    res = _mounted(fig, direccion=(-0.5, 0.0, -1.0))
    out, _ = _check(res, fig)
    assert any("pierna_izq" in x and "no sale por su eje" in x for x in out["problems"])
    assert any("el arma enfundada toca pierna_izq (de pie" in x for x in out["problems"])


def test_inclinada_mas_de_45_grados_avisa_de_soportes(fig):
    out, info = _check(_mounted(fig, direccion=(2.0, 0.0, -1.0)), fig)
    assert next(iter(info.values()))["inclinacion_grados"] > 45.0
    assert any("de la vertical" in a and "soportes" in a for a in out["avisos"])


def test_holgura_exigida_mayor_que_la_real_es_un_problema(fig, colgada):
    p = dataclasses.replace(fig[1], clearance=5.0)                  # exige 4,5 mm: la pierna queda a ≈ 2
    out, _ = _check(colgada, fig, p=p)
    assert any("el puente queda a" in x and "pierna_izq" in x for x in out["problems"])


def test_pared_menor_que_la_minima_es_un_problema(fig, colgada):
    p = dataclasses.replace(fig[1], min_wall=2.0)                   # el puente se construyó con 1,2 mm
    out, _ = _check(colgada, fig, p=p)
    assert any("la pared del puente mide 1.20 mm < 2 mm" in x for x in out["problems"])


def test_la_cadera_se_mide_en_todo_el_giro(fig, colgada):
    _, info = _check(colgada, fig, hip_step=30.0)
    keys = next(iter(info.values()))["holguras_mm"]
    assert "pierna_izq@cadera pierna" in keys and "pierna_izq@de pie" in keys


def test_validate_incluye_la_montura_solo_si_hay(fig, colgada):
    proj, p, body = fig
    out = P.validate(colgada, p, proj.limbs)
    assert "montura" in out and "montura_1_pelvis_izq" in out["montura"]
    assert not any(x.startswith("montura_1") for x in out["problems"])
    sin = P.validate(P.build(body, proj.limbs, p, proj.symmetry), p, proj.limbs)
    assert "montura" not in sin
