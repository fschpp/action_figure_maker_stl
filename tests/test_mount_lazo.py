"""M03 (secciones del arma) y M04 (puente «lazo») sobre piezas sintéticas: caja como flanco, `make_prop` como espada."""
import numpy as np
import pytest

from afm import props as P
from afm import solids as S
from afm.mount import (BRIDGE_LENGTH, REST_GAP, STATION_SPACING, MountError, MountSpec, analyze_blade, attach_lazo,
                       build_lazo, mirrored_for_side, section_at, slab_extent, surface_normal)

MIN_WALL = 1.2


def _sword(**kw):
    return P.make_prop(P.GripSpec(), 10.0, **kw)


def _flank():
    """Caja de 30 × 20 × 60 con la cara del flanco en x = 10 (normal +X)."""
    return S.Manifold.cube((20.0, 20.0, 60.0)).translate((-10.0, -10.0, 0.0))


def _spec(**over):
    d = dict(arma="espada@1", lugar="pelvis", ancla=(10.0, 0.0, 40.0), direccion=(0.0, 0.0, -1.0))
    d.update(over)
    return MountSpec(**d)


def _vol(a, b):
    return (a ^ b).volume()


# ---------------------------------------------------------------- M03
def test_perfil_de_la_espada_de_prueba():
    pr = analyze_blade(_sword())
    assert pr.length == pytest.approx(36.0, abs=0.01)
    assert pr.blade_width == pytest.approx(4.0, abs=0.01)
    assert pr.blade_thickness == pytest.approx(0.8, abs=0.01)
    assert pr.has_guard and pr.guard_end == pytest.approx(2.0, abs=0.01)
    assert pr.guard_width == pytest.approx(10.0, abs=0.01)


def test_arma_sin_guarda():
    blade = S.Manifold.cube((30.0, 4.0, 0.8)).translate((-30.0, -2.0, -0.4))
    pr = analyze_blade(blade)
    assert not pr.has_guard and pr.guard_end == 0.0
    assert pr.blade_width == pytest.approx(4.0, abs=0.01)


def test_seccion_y_tramo():
    sw = _sword()
    y0, z0, y1, z1 = section_at(sw, 20.0)
    assert (y1 - y0, z1 - z0) == (pytest.approx(4.0, abs=0.01), pytest.approx(0.8, abs=0.01))
    assert section_at(sw, 50.0) is None
    ya, za, yb, zb = slab_extent(sw, 1.0, 8.0)                      # el tramo incluye la guarda: es el más ancho
    assert yb - ya == pytest.approx(10.0, abs=0.01)
    with pytest.raises(MountError):
        slab_extent(sw, 8.0, 8.0)


def test_normal_de_la_superficie():
    n = surface_normal(_flank(), (10.0, 0.0, 40.0))
    assert np.allclose(n, (1.0, 0.0, 0.0), atol=1e-6)
    n2 = surface_normal(S.to_trimesh(_flank()), (0.0, 0.0, 60.0))
    assert np.allclose(n2, (0.0, 0.0, 1.0), atol=1e-6)


# ---------------------------------------------------------------- M04
def test_el_hueco_sale_de_la_hoja_mas_la_holgura():
    lz = build_lazo(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0))
    assert lz.hole_w == pytest.approx(4.0 + 0.5, abs=0.01)
    assert lz.hole_t == pytest.approx(0.8 + 0.5, abs=0.01)
    assert lz.info["alto_sobre_superficie_mm"] == pytest.approx(1.3 + MIN_WALL, abs=0.01)     # plan §5: ≈ 2,5
    assert lz.info["ancho_total_mm"] == pytest.approx(4.5 + 2 * MIN_WALL, abs=0.01)
    assert lz.stations[0] == pytest.approx(2.0 + REST_GAP + BRIDGE_LENGTH / 2, abs=0.3)
    assert lz.info["retiene"] and lz.info["avisos"] == []


def test_la_espada_enfundada_no_toca_y_se_saca_hacia_arriba():
    host = _flank()
    lz = build_lazo(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0))
    body = attach_lazo(host, lz)
    assert len(body.decompose()) == 1
    assert _vol(lz.weapon, body) < 1e-6
    assert lz.weapon.min_gap(body, 3.0) >= 0.19                      # guarda a 0,2 mm; hoja a 0,25 mm
    up = -np.array((0.0, 0.0, -1.0))
    for k in np.arange(0.0, 41.0, 1.0):                              # extracción por el eje: nunca choca
        assert _vol(lz.weapon.translate(tuple(up * k)), body) < 1e-6, k


def test_la_guarda_frena_la_espada():
    body = attach_lazo(_flank(), build_lazo(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0)))
    lz = build_lazo(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0))
    assert _vol(lz.weapon.translate((0.0, 0.0, -0.6)), body) > 0.5     # empujarla más abajo apoya la guarda en el puente


def test_el_puente_queda_sobre_el_flanco_y_el_hueco_vacio():
    host = _flank()
    lz = build_lazo(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0))
    body = attach_lazo(host, lz)
    added = body.volume() - host.volume()
    assert 0 < added < lz.outer.volume()                              # sumó material, pero el hueco quitó parte
    zc = 40.0                                                          # el puente central cae sobre el ancla
    probe = S.Manifold.cube((0.2, 0.2, 0.2)).translate((10.0 + 0.5, -0.1, zc - 0.1))
    assert _vol(body, probe) < 1e-9                                    # dentro del hueco no hay material
    wall_probe = S.Manifold.cube((0.2, 0.2, 0.2)).translate((10.5, 2.3, zc - 0.1))     # justo al lado del hueco (y = 2,25…) sí
    assert _vol(body, wall_probe) > 1e-3


def test_dos_puentes():
    sw = _sword(blade_len=60.0)
    lz = build_lazo(_spec(estaciones=2), sw, MIN_WALL, normal=(1, 0, 0))
    assert len(lz.stations) == 2 and lz.stations[1] - lz.stations[0] == pytest.approx(STATION_SPACING)
    body = attach_lazo(_flank(), lz)
    assert len(body.decompose()) == 1 and _vol(lz.weapon, body) < 1e-6


def test_hoja_corta_para_dos_puentes():
    with pytest.raises(MountError, match="no entran"):
        build_lazo(_spec(estaciones=2), _sword(blade_len=12.0), MIN_WALL, normal=(1, 0, 0))


def test_pared_y_holgura_pedidas():
    lz = build_lazo(_spec(pared=2.0, holgura=0.4), _sword(), MIN_WALL, normal=(1, 0, 0))
    assert lz.info["pared_mm"] == 2.0 and lz.hole_t == pytest.approx(0.8 + 0.8, abs=0.01)
    assert lz.info["alto_sobre_superficie_mm"] == pytest.approx(1.6 + 2.0, abs=0.01)


def test_sin_guarda_avisa():
    blade = S.Manifold.cube((30.0, 4.0, 0.8)).translate((-30.0, -2.0, -0.4))
    lz = build_lazo(_spec(), blade, MIN_WALL, normal=(1, 0, 0))
    assert not lz.info["retiene"] and "no tiene guarda" in lz.info["avisos"][0]


def test_guarda_que_no_supera_la_abertura_avisa():
    sw = _sword(guard_w=4.6, guard_t=2.0)
    lz = build_lazo(_spec(), sw, MIN_WALL, normal=(1, 0, 0))
    assert not lz.info["retiene"] and "se desliza" in lz.info["avisos"][0]


def test_montura_flotando_falla():
    lz = build_lazo(_spec(ancla=(40.0, 0.0, 40.0)), _sword(), MIN_WALL, normal=(1, 0, 0))
    with pytest.raises(MountError, match="no toca"):
        attach_lazo(_flank(), lz)


def test_solo_lazo():
    with pytest.raises(MountError, match="lazo"):
        build_lazo(_spec(tipo="clip"), _sword(), MIN_WALL)


def test_lado_der_es_el_espejo_del_izq():
    izq = build_lazo(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0))
    der_spec = _spec(lado="der", ancla=(-10.0, 0.0, 40.0))
    der = mirrored_for_side(build_lazo(der_spec, _sword(), MIN_WALL, normal=(1, 0, 0)), der_spec)
    host = _flank()
    b_izq, b_der = attach_lazo(host, izq), attach_lazo(S.mirror_x(host), der)
    assert b_der.volume() == pytest.approx(b_izq.volume(), rel=1e-6)
    assert _vol(der.weapon, b_der) < 1e-6
    assert izq.weapon.bounding_box()[0] == pytest.approx(10.25, abs=1e-6)             # la hoja queda a 0,25 mm del flanco
    assert der.weapon.bounding_box()[3] == pytest.approx(-10.25, abs=1e-6)            # y la der es el espejo, en x < 0


def test_flanco_inclinado_hacia_atras():
    """La espada inclinada 20° hacia atrás (−y): el puente la sigue y la extracción por su eje sigue libre."""
    d = np.array((0.0, -np.sin(np.radians(20.0)), -np.cos(np.radians(20.0))))
    spec = _spec(direccion=tuple(d))
    lz = build_lazo(spec, _sword(), MIN_WALL, normal=(1, 0, 0))
    body = attach_lazo(_flank(), lz)
    assert len(body.decompose()) == 1
    for k in np.arange(0.0, 41.0, 2.0):
        assert _vol(lz.weapon.translate(tuple(-d * k)), body) < 1e-6, k


def test_el_techo_mide_la_pared():
    lz = build_lazo(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0))
    body = attach_lazo(_flank(), lz)
    zc = 40.0
    top = 10.0 + lz.hole_t + MIN_WALL                                  # cara de arriba del puente (alto sobre el flanco)

    def probe(x):
        return _vol(body, S.Manifold.cube((0.1, 0.1, 0.1)).translate((x, -0.05, zc - 0.05)))

    assert probe(10.0 + lz.hole_t + 0.1) > 0.9e-3                       # justo sobre el hueco hay techo
    assert probe(top - 0.15) > 0.9e-3                                   # y llega hasta `pared` de espesor
    assert probe(top + 0.05) < 1e-9                                     # sin material por encima


def test_las_paredes_laterales_miden_la_pared():
    lz = build_lazo(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0))
    body = attach_lazo(_flank(), lz)
    edge = lz.hole_w / 2 + MIN_WALL                                    # cara exterior de la pared lateral (|y|)

    def probe(y):
        return _vol(body, S.Manifold.cube((0.1, 0.1, 0.1)).translate((10.6, y - 0.05, 39.95)))

    assert probe(edge - 0.1) > 0.9e-3 and probe(-(edge - 0.1)) > 0.9e-3
    assert probe(edge + 0.1) < 1e-9 and probe(-(edge + 0.1)) < 1e-9
