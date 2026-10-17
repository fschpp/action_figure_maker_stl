"""Herramienta de medición (tools/medir_articulaciones.py) sobre una pieza sintética con un agujero inclinado."""
import importlib.util
import pathlib

import numpy as np
import pytest

from afm import solids as S

_spec = importlib.util.spec_from_file_location(
    "medir", pathlib.Path(__file__).resolve().parents[1] / "tools" / "medir_articulaciones.py")
medir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(medir)


@pytest.fixture(scope="module")
def pieza():
    a = np.radians(12.0)
    block = S.Manifold.cube((16, 14, 14), center=True).translate((10, 0, 30))
    hole = S.cylinder_along((10, -3.0, 30), (np.cos(a), 0, np.sin(a)), 1.3, 40, 48, start=-20)
    return S.to_trimesh(S.difference(block, hole))


def test_axis_tilt_and_diameter(pieza):
    e = medir.eje_inclinado(pieza, (-3.0, 30.0), 6.0)
    assert e["alfa_deg"] == pytest.approx(12.0, abs=0.7)
    assert e["diametro_agujero_mm"] == pytest.approx(2.6, abs=0.1)
    assert e["punto"][1] == pytest.approx(-3.0, abs=0.05)


def test_axial_rays_give_block_thickness(pieza):
    e = medir.eje_inclinado(pieza, (-3.0, 30.0), 6.0)
    r = medir.rayos_axiales(pieza, e, rho=2.0)
    # a lo largo del eje inclinado el bloque (16 mm en X) mide 16/cos(12°) en la dirección del eje
    largos = [t[0][1] - t[0][0] for t in r.values() if len(t) == 1]
    assert largos and all(15.0 < l < 17.5 for l in largos)


def test_tramos_two_segments():
    a = S.Manifold.cube((2, 4, 4), center=True).translate((-3, 0, 0))
    b = S.Manifold.cube((2, 4, 4), center=True).translate((3, 0, 0))
    m = S.to_trimesh(S.union(a, b))
    # dos bloques separados: un solo `union` de manifold los deja como dos componentes
    t = medir.tramos(m, (-10, 0, 0), (1, 0, 0), (0, 20))
    assert [(round(x, 1), round(y, 1)) for x, y in t] == [(6.0, 8.0), (12.0, 14.0)]
