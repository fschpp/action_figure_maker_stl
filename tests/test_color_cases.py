"""T19 · Casos completos del color sobre el muñeco texturizado: `--sin-soportes` (base plana), figura asimétrica (`limbs_right`) y
piezas de catálogo, que no tienen origen en la fuente.

Medidas al escribir las pruebas (muñeco, texel 0,3 mm, fuente a 0,1 mm):
  · sin aplanar: piernas 73,4 % con origen · brazos 68,6 % / 69,3 % · torso 38,2 %; con `flat=1.0` las piernas bajan a 54,6 % (el corte
    quita 2116 mm³ de cada una) y brazos y cabeza no cambian;
  · brazo derecho con otra silueta (corte desde z = 52): 1080 mm³ contra 1381 mm³ del izquierdo; con `symmetric=False` es azul en 59 % de
    sus texeles y con `symmetric=True` rojo en 59 %.
"""
import dataclasses
import json
import os

import numpy as np
import pytest

from afm import pipeline as P, solids as S
from afm.colorexport import Colorizer
from afm.colorreg import MIRROR_X, register_piece, seeds_for_piece
from afm.colorsrc import ColorSource
from afm.parts import load_mesh
from afm.project import demo_project
from afm.testfigure import make_doll
from color_helpers import doll_params, doll_textured, piece_trimeshes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_HAND = os.path.join(ROOT, "catalogo", "ejemplo", "piezas", "mano", "mano-muneco-01@1.stl")
RED, BLUE, GREEN, LIME = (220, 30, 30), (30, 30, 220), (30, 200, 60), (160, 210, 40)     # color_helpers.REGIONS
FLAT_GRAY = (200, 200, 200)                                                               # `#C8C8C8`


def _share(cz, name, rgb, tol=12) -> float:
    """Fracción de los texeles propios del atlas de `name` que son de color `rgb` (± `tol` por canal)."""
    _unw, bk, _reg, _info = cz.pieces[name]
    px = bk.atlas[bk.mask].astype(int)
    return float(np.mean(np.abs(px - np.array(rgb)).max(axis=1) <= tol))


def _colorize(src, params, meshes, res, names, **kw) -> Colorizer:
    cz = Colorizer(src, params, texel_mm=0.3, **kw)
    for n in names:
        cz(n, meshes[n], res)
    return cz


@pytest.fixture(scope="module")
def params():
    return doll_params()


@pytest.fixture(scope="module")
def src():
    return ColorSource(doll_textured(), spacing_mm=0.1)


@pytest.fixture(scope="module")
def built(params):
    pr = demo_project()
    return P.build(S.to_manifold(make_doll()), pr.limbs, params, pr.symmetry, head=pr.head)


@pytest.fixture(scope="module")
def meshes(built):
    return piece_trimeshes(built)


@pytest.fixture(scope="module")
def plain(src, params, built, meshes):
    """El muñeco sin aplanar, coloreado: la referencia de `pct_origen`."""
    return _colorize(src, params, meshes, built, sorted(built.pieces))


@pytest.fixture(scope="module")
def flat_run(src, params, built, tmp_path_factory):
    """`export(flat=1.0)`: el motor aplana la base y gira los brazos antes de escribir; el gancho de color ve esas mallas."""
    cz = Colorizer(src, params, texel_mm=0.3)
    out = tmp_path_factory.mktemp("plana")
    info = P.export(built, str(out), "f", orient=True, flat=1.0, params=params, colorize=cz, progress=lambda *_: None)
    return cz, info


@pytest.fixture(scope="module")
def asym(params):
    """Brazo derecho con otra silueta (corte desde z = 52 en vez de 43): una figura asimétrica de verdad."""
    pr = demo_project()
    right = []
    for lim in pr.limbs:
        m = lim.mirrored()
        if lim.kind == "arm":
            poly = np.asarray(m.polygon, float).copy()
            poly[:, 2] = np.where(poly[:, 2] < 50.0, 52.0, poly[:, 2])
            m = dataclasses.replace(m, polygon=poly)
        right.append(m)
    return P.build(S.to_manifold(make_doll()), pr.limbs, params, pr.symmetry, head=pr.head, limbs_right=right)


@pytest.fixture(scope="module")
def asym_meshes(asym):
    return piece_trimeshes(asym)


@pytest.fixture(scope="module")
def asym_colored(src, params, asym, asym_meshes):
    return _colorize(src, params, asym_meshes, asym, ("brazo_izq", "brazo_der", "pierna_izq", "pierna_der"))


# ------------------------------------------------------------------ 1. --sin-soportes
def test_flat_print_pieces_still_colored(flat_run):
    cz, info = flat_run
    piezas = info["color"]["piezas"]
    assert set(piezas) == {"brazo_izq", "brazo_der", "pierna_izq", "pierna_der", "torso", "cabeza"}
    assert info["color"]["sin_origen"] == []
    assert all(i["estado"] != "sin_origen" for i in piezas.values())
    assert piezas["pierna_izq"]["estado"] == "semilla" and piezas["pierna_izq"]["angulo"] == -90.0
    assert piezas["pierna_der"]["estado"] == "semilla" and piezas["pierna_der"]["angulo"] == 90.0
    assert _share(cz, "brazo_izq", RED) >= 0.5 and _share(cz, "brazo_der", BLUE) >= 0.5      # cada brazo conserva su color
    assert _share(cz, "pierna_izq", GREEN) >= 0.4 and _share(cz, "pierna_der", LIME) >= 0.4


def test_flat_print_reports_lower_origin_where_cut(flat_run, plain):
    """El aplanado recorta la base: ahí la pieza ya no coincide con la fuente y el `pct_origen` baja (y se informa)."""
    _cz, info = flat_run
    assert info["sin_soportes"]["volumen_quitado_mm3"]["pierna_izq"] > 1000.0
    for n in ("pierna_izq", "pierna_der"):
        antes = plain.pieces[n][3]["pct_origen"]
        despues = info["color"]["piezas"][n]["pct_origen"]
        assert despues <= antes - 10.0, (n, antes, despues)                                   # medido 73,4 → 54,6
    for n in ("brazo_izq", "brazo_der", "cabeza"):                                            # lo que no se corta no cambia
        assert abs(info["color"]["piezas"][n]["pct_origen"] - plain.pieces[n][3]["pct_origen"]) <= 1.0, n


# ------------------------------------------------------------------ 2. limbs_right
def test_asymmetric_right_arm_keeps_right_color(asym, asym_meshes, asym_colored):
    assert asym.right is not None
    assert asym_meshes["brazo_der"].volume < 0.9 * asym_meshes["brazo_izq"].volume           # silueta distinta (medido 1080 vs 1381 mm³)
    info = asym_colored.pieces["brazo_der"][3]
    assert info["estado"] == "identidad" and info["pct_origen"] >= 50.0, info
    assert _share(asym_colored, "brazo_der", BLUE) >= 0.4                                    # sigue azul, aunque no sea el espejo del izquierdo
    assert _share(asym_colored, "brazo_der", RED) <= 0.02
    assert _share(asym_colored, "brazo_izq", RED) >= 0.4 and _share(asym_colored, "brazo_izq", BLUE) <= 0.02


def test_symmetric_option_overrides_asymmetry(src, params, asym, asym_meshes, asym_colored):
    sym = _colorize(src, params, asym_meshes, asym, ("brazo_izq", "brazo_der", "pierna_der"), symmetric=True)
    assert _share(sym, "brazo_der", RED) >= 0.4 and _share(sym, "brazo_der", BLUE) <= 0.02   # copia el color del lado izquierdo
    assert _share(sym, "pierna_der", GREEN) >= 0.4 and _share(sym, "pierna_der", LIME) <= 0.02
    # el lado izquierdo no cambia con la opción
    a, b = asym_colored.pieces["brazo_izq"][1], sym.pieces["brazo_izq"][1]
    assert np.array_equal(a.atlas, b.atlas) and np.array_equal(a.mask, b.mask)


def test_right_pieces_use_right_motion(asym, asym_meshes, src, params):
    """Las piezas `_der` se registran con el `motion` de `res.right` (cuerpo espejado), reflejado; no con el del lado izquierdo."""
    seeds = seeds_for_piece("pierna_der", asym, params)
    c, ax, th = seeds[0]
    c_r, ax_r = asym.right.motion["pierna"][1], asym.right.motion["pierna"][2]
    assert np.allclose(c, MIRROR_X @ np.asarray(c_r, float)) and np.allclose(ax, MIRROR_X @ np.asarray(ax_r, float))
    assert th == -90.0
    # si el lado derecho tuviera otro pasador, la semilla lo seguiría y la del izquierdo no
    moved = dataclasses.replace(asym.right, motion={**asym.right.motion, "pierna": (
        asym.right.motion["pierna"][0], np.asarray(c_r, float) + np.array([0.0, 0.0, 1.5]), *asym.right.motion["pierna"][2:])})
    res2 = dataclasses.replace(asym, right=moved)
    c2 = np.asarray(seeds_for_piece("pierna_der", res2, params)[0][0], float)
    assert np.allclose(c2 - np.asarray(c, float), [0.0, 0.0, 1.5])
    assert np.allclose(seeds_for_piece("pierna_izq", res2, params)[0][0], seeds_for_piece("pierna_izq", asym, params)[0][0])
    # registro real de la pierna derecha: semilla exacta, con el centro de `res.right` reflejado
    r = register_piece("pierna_der", asym_meshes["pierna_der"], src, asym, params)
    assert r.status == "semilla" and r.score >= 0.4 and r.seed == seeds[0], (r.status, r.score)
    assert np.allclose(r.center, MIRROR_X @ np.asarray(c_r, float))


# ------------------------------------------------------------------ 3. piezas de catálogo
@pytest.mark.skipif(not os.path.isfile(CATALOG_HAND), reason="falta la mano de catálogo de ejemplo")
def test_catalog_piece_is_sin_origen_and_gray(src, params, built):
    hand = load_mesh(CATALOG_HAND)                    # una mano estándar importada: no sale de esta figura
    cz = Colorizer(src, params, texel_mm=0.3)
    info = cz("mano_izq", hand, built)
    assert info["estado"] == "sin_origen" and info["pct_origen"] < 5.0, info
    _unw, bk, _reg, _i = cz.pieces["mano_izq"]
    px = bk.atlas[bk.mask].astype(int)
    assert len(px) > 0 and np.all(px == np.array(FLAT_GRAY))                                 # `#C8C8C8` en todos sus texeles
    rojo = Colorizer(src, params, texel_mm=0.3, fill="#FF0000")                              # con un relleno plano, ese color
    rojo("mano_izq", hand, built)
    assert np.all(rojo.pieces["mano_izq"][1].atlas[rojo.pieces["mano_izq"][1].mask] == np.array((255, 0, 0)))


@pytest.mark.skipif(not os.path.isfile(CATALOG_HAND), reason="falta la mano de catálogo de ejemplo")
def test_report_lists_sin_origen_pieces(src, params, built, meshes, tmp_path):
    cz = Colorizer(src, params, texel_mm=0.3)
    cz("brazo_izq", meshes["brazo_izq"], built)
    cz("mano_izq", load_mesh(CATALOG_HAND), built)
    out = cz.write(str(tmp_path), "f")
    assert out["sin_origen"] == ["mano_izq"]
    assert out["piezas"]["mano_izq"]["estado"] == "sin_origen" and out["piezas"]["brazo_izq"]["estado"] == "identidad"
    with open(tmp_path / out["informe"], encoding="utf-8") as fh:
        assert json.load(fh)["sin_origen"] == ["mano_izq"]                                   # también en el informe de disco
    assert (tmp_path / "piezas_color" / "f_mano_izq_color.png").is_file()                    # la pieza sin origen igual se escribe
