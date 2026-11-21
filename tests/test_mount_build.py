"""M05: las monturas se suman al final de `pipeline.build` (figura demo, espada de prueba)."""
import dataclasses

import numpy as np
import pytest

from afm import pipeline as P
from afm import props as PR
from afm import solids as S
from afm.mount import MountError, MountSpec, apply_mounts, resolve_host
from afm.project import demo_project
from afm.testfigure import make_doll


def _sword():
    return PR.make_prop(PR.GripSpec(), 10.0)


def _spec(**over):
    d = dict(arma="espada@1", lugar="pelvis", ancla=(23.0, 0.0, 38.0), direccion=(0.0, 0.0, 1.0))
    d.update(over)
    return MountSpec(**d)


@pytest.fixture(scope="module")
def base():
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0)
    body = S.to_manifold(make_doll())
    return proj, p, body, P.build(body, proj.limbs, p, proj.symmetry)


def _build(base, mounts):
    proj, p, body, _ = base
    return P.build(body, proj.limbs, p, proj.symmetry, mounts=mounts)


def test_sin_monturas_el_resultado_es_el_de_siempre(base):
    res = base[3]
    again = _build(base, None)
    assert res.mounts == {} and "monturas" not in res.report
    for n, m in res.pieces.items():
        assert again.pieces[n].num_tri() == m.num_tri()
        assert abs(again.pieces[n].volume() - m.volume()) < 1e-6


def test_la_montura_suma_al_torso_y_deja_una_sola_pieza(base):
    res = _build(base, [(_spec(), _sword())])
    (name, mb), = res.mounts.items()
    assert name == "montura_1_pelvis_izq" and mb.host == "torso"
    t0, t1 = base[3].pieces["torso"], res.pieces["torso"]
    assert len(t1.decompose()) == 1
    assert t1.volume() > t0.volume() + 5.0                      # el puente agrega material
    assert t1.bounding_box()[3] > t0.bounding_box()[3] + 1.0    # y sobresale del flanco
    assert res.report["monturas"][name]["retiene"] is True
    for n, m in base[3].pieces.items():                         # lo demás no se toca
        if n != "torso":
            assert res.pieces[n].volume() == pytest.approx(m.volume(), abs=1e-6)


def test_la_espada_enfundada_no_toca_el_torso_y_el_hueco_la_deja_pasar(base):
    res = _build(base, [(_spec(), _sword())])
    mb = next(iter(res.mounts.values()))
    torso, weapon = res.pieces["torso"], mb.lazo.weapon
    assert S.intersection(torso, weapon).volume() < 1e-3
    gap = float(torso.min_gap(weapon, 5.0))
    assert 0.1 <= gap <= 0.6                                    # la holgura de la hoja (0,25) más el juego de la guarda


def test_montura_derecha_es_el_espejo_de_la_izquierda(base):
    left = _build(base, [(_spec(), _sword())])
    right = _build(base, [(_spec(lado="der", ancla=(-23.0, 0.0, 38.0)), _sword())])
    mr = next(iter(right.mounts.values()))
    assert mr.spec.lado == "der"
    delta = right.pieces["torso"].volume() - base[3].pieces["torso"].volume()
    deltal = left.pieces["torso"].volume() - base[3].pieces["torso"].volume()
    assert delta == pytest.approx(deltal, rel=0.02)
    assert right.pieces["torso"].bounding_box()[0] < base[3].pieces["torso"].bounding_box()[0] - 1.0
    assert S.intersection(right.pieces["torso"], mr.lazo.weapon).volume() < 1e-3


def test_ancla_en_el_aire_falla_con_mensaje(base):
    with pytest.raises(ValueError, match="montura_1_pelvis_izq"):
        _build(base, [(_spec(ancla=(60.0, 0.0, 38.0)), _sword())])


def test_lugar_no_soportado(base):
    with pytest.raises(ValueError, match="por ahora"):
        _build(base, [(_spec(lugar="brazo"), _sword())])


def test_resolve_host_usa_la_pelvis_si_hay_cintura():
    from afm.waist import PELVIS_NAME

    box = S.Manifold.cube((1.0, 1.0, 1.0))
    assert resolve_host(_spec(), {"torso": box}) == "torso"
    assert resolve_host(_spec(), {"torso": box, PELVIS_NAME: box}) == PELVIS_NAME
    assert resolve_host(_spec(lugar="torso"), {"torso": box, PELVIS_NAME: box}) == "torso"
    with pytest.raises(MountError):
        resolve_host(_spec(), {})


def test_apply_mounts_no_modifica_el_diccionario_de_entrada(base):
    pieces = dict(base[3].pieces)
    out, built = apply_mounts(pieces, [(_spec(), _sword())], 1.2)
    assert pieces["torso"] is base[3].pieces["torso"] and out["torso"] is not pieces["torso"] and len(built) == 1
