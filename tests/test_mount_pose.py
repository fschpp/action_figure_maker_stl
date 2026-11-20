"""M02 — escala por medida real y pose de transporte del accesorio al marco de la pieza (sin cuerpo ni receta)."""
import numpy as np
import pytest

from afm import props as P
from afm import solids as S
from afm.mount import (MountError, MountSpec, construction_spec, place_accessory, scale_from_real_length,
                       transport_matrix)


def _spec(**over):
    d = dict(arma="espada-link@1", lugar="pelvis", ancla=(11.0, -3.0, 58.0), direccion=(0.0, -0.5, 1.0))
    d.update(over)
    return MountSpec(**d)


# ---------------------------------------------------------------- escala
def test_escala_por_medida_real():
    assert scale_from_real_length(0.864, 86.4) == pytest.approx(100.0)
    assert scale_from_real_length(200.0, 50.0) == pytest.approx(0.25)


@pytest.mark.parametrize("units,real", [(0, 50), (-1, 50), (1, 0), (1, -5), (float("nan"), 5), (1, float("inf")), (True, 5), ("1", 5)])
def test_escala_invalida(units, real):
    with pytest.raises(MountError):
        scale_from_real_length(units, real)


# ---------------------------------------------------------------- lado de construcción
def test_izq_se_construye_tal_cual():
    s = _spec()
    assert construction_spec(s) is s


def test_der_se_espeja_en_x_y_queda_izq():
    s = _spec(lado="der", ancla=(-11.0, -3.0, 58.0), direccion=(-0.2, -0.5, 1.0), holgura=0.3, estaciones=2)
    c = construction_spec(s)
    assert c.lado == "izq" and c.ancla == (11.0, -3.0, 58.0) and c.direccion == (0.2, -0.5, 1.0)
    assert (c.holgura, c.estaciones, c.arma, c.lugar) == (0.3, 2, s.arma, s.lugar)


def test_transport_exige_lado_de_construccion():
    with pytest.raises(MountError, match="construction_spec"):
        transport_matrix(_spec(lado="der"))


# ---------------------------------------------------------------- matriz
def test_es_un_movimiento_rigido():
    m = transport_matrix(_spec(), station=12.0)
    r = m[:3, :3]
    assert np.allclose(r.T @ r, np.eye(3), atol=1e-12)
    assert np.linalg.det(r) == pytest.approx(1.0)
    assert np.allclose(m[3], [0, 0, 0, 1])


def test_la_hoja_apunta_a_la_direccion():
    s = _spec()
    m = transport_matrix(s)
    assert np.allclose(m[:3, :3] @ [-1.0, 0, 0], s.unit_direction())      # accesorio −X (empuñadura → punta)


def test_la_estacion_cae_sobre_el_ancla():
    s = _spec()
    m = transport_matrix(s, station=12.0)
    p = m @ np.array([-12.0, 0.0, 0.0, 1.0])                              # punto de la hoja a 12 mm de la empuñadura
    assert np.allclose(p[:3], s.ancla)


def test_lift_sube_por_la_normal_y_el_espesor_va_con_ella():
    s = _spec(direccion=(0.0, 0.0, -1.0))                                   # recta hacia abajo
    m0, m1 = transport_matrix(s, normal=(1, 0, 0)), transport_matrix(s, normal=(1, 0, 0), lift=1.5)
    assert np.allclose(m1[:3, 3] - m0[:3, 3], [1.5, 0, 0])
    assert np.allclose(m1[:3, :3] @ [0, 0, 1.0], [1, 0, 0])                # espesor de la hoja a lo largo de la normal


def test_la_normal_se_proyecta_perpendicular_al_eje():
    s = _spec(direccion=(0.0, 0.0, -1.0))
    m = transport_matrix(s, normal=(1, 0, 0.7))                             # normal inclinada: se le quita la parte axial
    z = m[:3, :3] @ [0, 0, 1.0]
    assert np.allclose(z, [1, 0, 0]) and abs(z @ s.unit_direction()) < 1e-12


def test_normal_paralela_al_eje_falla():
    with pytest.raises(MountError, match="paralela"):
        transport_matrix(_spec(direccion=(0, 0, -1)), normal=(0, 0, 1))


def test_normal_mal_formada_falla():
    with pytest.raises(MountError):
        transport_matrix(_spec(), normal=(1, 0))


def test_sin_normal_usa_el_flanco_y_si_el_arma_apunta_a_x_cae_a_z():
    m = transport_matrix(_spec(direccion=(0, 0, -1)))
    assert np.allclose(m[:3, :3] @ [0, 0, 1.0], [1, 0, 0])                  # +X del lado de construcción
    m = transport_matrix(_spec(direccion=(1, 0, 0)))
    assert np.allclose(m[:3, :3] @ [0, 0, 1.0], [0, 0, 1])


# ---------------------------------------------------------------- con un arma de verdad (make_prop)
def _sword():
    return P.make_prop(P.GripSpec(), 10.0)


def test_la_espada_de_prueba_queda_sobre_el_eje_y_la_punta_donde_toca():
    blade_len, guard_t = 34.0, 2.0
    total = blade_len + guard_t                                             # de la cara de la empuñadura a la punta
    s = _spec(direccion=(0.0, 0.0, -1.0))
    station = 20.0
    placed = place_accessory(_sword(), transport_matrix(s, station=station, normal=(1, 0, 0)))
    v = np.asarray(S.to_printable_trimesh(placed).vertices)
    along = (v - np.asarray(s.ancla)) @ np.asarray(s.unit_direction())      # posición sobre el eje, con el ancla en 0
    assert along.max() == pytest.approx(total - station, abs=1e-3)           # la punta, después del ancla
    assert along.min() < -station                                           # la empuñadura, antes del ancla (la guarda está en 2…)


def test_el_arma_transformada_conserva_el_volumen():
    s = _spec()
    sword = _sword()
    placed = place_accessory(sword, transport_matrix(s, station=10.0))
    assert placed.volume() == pytest.approx(sword.volume(), rel=1e-9)
