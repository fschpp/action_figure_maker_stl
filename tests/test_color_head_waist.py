"""T17 · Color de la cabeza (giro libre sobre el cuello) y de las dos mitades del torso con cintura (`torso` = pecho, `pelvis`)."""
import dataclasses

import numpy as np
import pytest

from afm import pipeline as P, solids as S
from afm.colorexport import Colorizer
from afm.colorsrc import ColorSource
from afm.head import HEAD_NAME, HeadSpec
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll
from afm.waist import PELVIS_NAME, WaistSpec
from color_helpers import REGIONS, doll_params, doll_textured, piece_trimeshes

NECK_Z = 80.0
WAIST_Z = 55.0
TOL = 12                      # ± por canal para decir «es este color» (el atlas es bilineal sobre bloques de color liso)


def _neck(z=NECK_Z):
    return HeadSpec(Plane.from_points((-5, -5, z), (5, -5, z), (0, 5, z), "cabeza cuerpo"))


def _waist(z=WAIST_Z):
    return WaistSpec(Plane.from_points((-10, -6, z), (10, -6, z), (0, 8, z), "cintura"))


@pytest.fixture(scope="module")
def params():
    return doll_params()


@pytest.fixture(scope="module")
def src():
    return ColorSource(doll_textured(), spacing_mm=0.1)


@pytest.fixture(scope="module")
def body():
    return S.to_manifold(make_doll())


def _colorize(res, src, params):
    cz = Colorizer(src, params, texel_mm=0.3)
    for n, m in piece_trimeshes(res).items():
        cz(n, m, res)
    return cz


@pytest.fixture(scope="module")
def head_cz(body, src, params):
    res = P.build(body, [], params, head=_neck())
    return _colorize(res, src, params)


@pytest.fixture(scope="module")
def waist_cz(body, src, params):
    pr = demo_project()
    res = P.build(body, pr.limbs, params, pr.symmetry, head=pr.head, waist=_waist())
    return _colorize(res, src, params)


def _share(cz, piece: str, region: str) -> float:
    """Fracción de los texeles cubiertos del atlas de `piece` cuyo color es el de `region` (± TOL)."""
    _unw, bk, _reg, _info = cz.pieces[piece]
    px = bk.atlas[bk.mask].astype(int)
    return float(np.mean(np.abs(px - np.array(REGIONS[region])).max(axis=1) <= TOL))


def _count(cz, piece: str, region: str) -> int:
    _unw, bk, _reg, _info = cz.pieces[piece]
    px = bk.atlas[bk.mask].astype(int)
    return int(np.sum(np.abs(px - np.array(REGIONS[region])).max(axis=1) <= TOL))


# ------------------------------------------------------------------ cabeza
def test_head_piece_is_yellow(head_cz):
    assert HEAD_NAME in head_cz.pieces
    assert _share(head_cz, HEAD_NAME, "cabeza") >= 0.75           # medido 0,81; el resto es la franja z < 78 del cuello (gris «resto» en la fuente)
    assert _share(head_cz, HEAD_NAME, "torso") < 0.01 and _share(head_cz, HEAD_NAME, "pelvis") < 0.01


def test_head_registration_status(head_cz):
    """La cabeza gira libre sobre el cuello pero se imprime sin girar (`th = 0`): registra con la semilla exacta (ángulo 0).

    Es una pieza de revolución: todos los giros puntúan casi igual, así que sin el margen de desempate (`TIE_MARGIN`) ganaba
    el de ±90° por ruido (0,895 contra 0,894) y una cabeza con cara habría salido girada.
    """
    reg = head_cz.pieces[HEAD_NAME][2]
    assert reg.status in ("identidad", "semilla")
    assert reg.angle_deg == pytest.approx(0.0, abs=1e-6)
    assert reg.score >= 0.8
    assert head_cz.pieces["torso"][2].status == "identidad"


# ------------------------------------------------------------------ cintura
def test_waist_chest_is_white(waist_cz):
    assert waist_cz.pieces["torso"][2].status == "identidad"
    assert _share(waist_cz, "torso", "torso") >= 0.75              # medido 0,80
    assert _share(waist_cz, "torso", "pelvis") < 0.01


def test_waist_pelvis_has_orange_and_white(waist_cz):
    """La pelvis lleva el naranja de su región y el blanco del torso. El naranja del muñeco es solo la cara de abajo del bloque
    de la pelvis (381 mm² de 6 400): en el atlas de la pelvis son ≈ 0,3 % de los texeles (≈ 5 000), por eso se cuenta en texeles."""
    assert PELVIS_NAME in waist_cz.pieces
    assert waist_cz.pieces[PELVIS_NAME][2].status == "identidad"     # la pelvis queda en su pose de reposo (no hay `motion` de cintura)
    assert _count(waist_cz, PELVIS_NAME, "pelvis") >= 1000
    assert _share(waist_cz, PELVIS_NAME, "torso") >= 0.15            # medido 0,23
    # los flancos de la pelvis (|x| > 15) caen en las regiones de los brazos de la fuente (regla `region_of` del muñeco)
    assert _share(waist_cz, PELVIS_NAME, "brazo_izq") + _share(waist_cz, PELVIS_NAME, "brazo_der") > 0.5


def test_waist_surfaces_without_source_are_reported(waist_cz):
    """El corte de la cintura, el pasador y la cavidad no existían en la figura: `pct_origen < 100` en ambas mitades."""
    for n in ("torso", PELVIS_NAME):
        info = waist_cz.pieces[n][3]
        assert 0.0 < info["pct_origen"] < 100.0
    assert waist_cz.pieces[PELVIS_NAME][3]["pct_origen"] < waist_cz.pieces["torso"][3]["pct_origen"]    # la pelvis tiene más superficie nueva
