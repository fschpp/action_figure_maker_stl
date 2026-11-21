"""M06: las monturas y «imprimir sin soportes» (`flat_for_print`): el plano se mide sin la montura y la montura se vuelve a unir."""
import dataclasses

import pytest

from afm import pipeline as P
from afm import props as PR
from afm import solids as S
from afm.mount import MountSpec
from afm.project import demo_project
from afm.testfigure import make_doll


def _spec(z):
    return MountSpec(arma="espada@1", lugar="pelvis", ancla=(23.0, 0.0, z), direccion=(0.0, 0.0, 1.0))


def _sword():
    return PR.make_prop(PR.GripSpec(), 10.0)


@pytest.fixture(scope="module")
def base():
    proj = demo_project()
    p = dataclasses.replace(proj.params, decimate=0)
    body = S.to_manifold(make_doll())
    res = P.build(body, proj.limbs, p, proj.symmetry)
    return proj, p, body, res, P.flat_for_print(res, 1.0, p)


def _con(base, z):
    proj, p, body, _, _ = base
    return P.build(body, proj.limbs, p, proj.symmetry, mounts=[(_spec(z), _sword())])


def test_host_bare_es_el_huesped_sin_la_montura(base):
    res = _con(base, 38.0)
    sin = base[3]
    assert sin.host_bare == {}
    assert set(res.host_bare) == {"torso"}
    assert res.host_bare["torso"].volume() == pytest.approx(sin.pieces["torso"].volume(), abs=1e-6)
    assert res.pieces["torso"].volume() > res.host_bare["torso"].volume() + 5.0


def test_con_flat_la_montura_sigue_completa_y_el_plano_es_el_de_siempre(base):
    res = _con(base, 38.0)
    pieces, info = P.flat_for_print(res, 1.0, base[1])
    pf, inf0 = base[4]
    (name, mb), = res.mounts.items()
    assert info["altura_corte_z"] == pytest.approx(inf0["altura_corte_z"])
    assert info["monturas"][name]["unida"] is True and "cortada_mm" not in info["monturas"][name]
    assert info["avisos"] == inf0["avisos"]                                  # nada nuevo que avisar
    t = pieces["torso"]
    assert len(t.decompose()) == 1
    assert t.volume() > pf["torso"].volume() + 5.0                           # el puente sigue ahí
    assert float((mb.lazo.weapon ^ t).volume()) < 1e-3                       # y el hueco sigue abierto: la espada entra
    for n, m in pf.items():                                                  # lo demás no se toca
        if n != "torso":
            assert pieces[n].volume() == pytest.approx(m.volume(), abs=1e-6)


def test_el_plano_se_mide_sin_la_montura_y_si_la_corta_avisa(base):
    res = _con(base, 35.0)                       # el puente baja a z = 33,5 < la cola (34,0)
    (name, mb), = res.mounts.items()
    assert res.pieces["torso"].bounding_box()[2] < res.host_bare["torso"].bounding_box()[2] - 0.4
    pieces, info = P.flat_for_print(res, 1.0, base[1])
    assert info["altura_corte_z"] == pytest.approx(base[4][1]["altura_corte_z"])       # no bajó con la montura
    rec = info["monturas"][name]
    assert rec["unida"] is True and rec["cortada_mm"] == pytest.approx(1.5, abs=0.05)
    assert any("queda cortada por el plano" in a for a in info["avisos"])
    assert pieces["torso"].bounding_box()[2] >= info["altura_corte_z"] - 1e-3          # base plana intacta
    assert len(pieces["torso"].decompose()) == 1


def test_sin_monturas_flat_no_cambia(base):
    pf, info = base[4]
    assert "monturas" not in info


def test_con_relleno_de_cadera_la_montura_se_vuelve_a_unir(base):
    proj, p, body, _, _ = base
    pfill = dataclasses.replace(p, flat_fill_hip=True)
    ref_pieces, ref_info = P.flat_for_print(base[3], 1.0, pfill)
    res = _con(base, 38.0)
    pieces, info = P.flat_for_print(res, 1.0, pfill)
    (name, _), = res.mounts.items()
    assert info["altura_corte_z"] == pytest.approx(ref_info["altura_corte_z"])
    assert info["monturas"][name]["unida"] is True
    assert len(pieces["torso"].decompose()) == 1
    assert pieces["torso"].volume() > ref_pieces["torso"].volume() + 5.0
