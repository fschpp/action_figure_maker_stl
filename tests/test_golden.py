"""Resultados dorados (Fase 0 del plan de piezas intercambiables): las figuras ya resueltas no deben cambiar."""
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import golden  # noqa: E402

FULL = os.environ.get("AFM_GOLDEN_FULL") == "1"


def _gold(name):
    with open(golden.golden_path(name)) as f:
        return json.load(f)


def test_compare_detects_changes():
    g = _gold("muneco")
    assert golden.compare(g, g) == []
    now = copy.deepcopy(g)
    k = next(iter(now["piezas"]))
    now["piezas"][k]["volumen"] *= 1.001                      # +0,1 % > tolerancia de 0,01 %
    now["gaps"][next(iter(now["gaps"]))] += 0.01
    now["claves_validacion"].remove("cintura")
    now["ok"] = False
    diffs = golden.compare(now, g)
    assert len(diffs) == 4 and any("volumen" in d for d in diffs) and any("holgura" in d for d in diffs)
    ok = copy.deepcopy(g)
    ok["piezas"][k]["volumen"] *= 1.00005                     # dentro de la tolerancia
    assert golden.compare(ok, g) == []


def test_the_doll_golden_exists_and_is_a_valid_figure():
    g = _gold("muneco")
    assert g["ok"] is True and g["problems"] == [] and len(g["piezas"]) == 6 and all(0.4 < v < 0.6 for v in g["gaps"].values())


def test_doll_matches_golden(tmp_path):
    info = golden.build_figure("muneco", str(tmp_path))
    assert golden.compare(golden.snapshot(info), _gold("muneco")) == []


@pytest.mark.skipif(not FULL, reason="figuras reales: minutos cada una; correr con AFM_GOLDEN_FULL=1")
@pytest.mark.parametrize("name", [n for n in golden.FIGURES if n not in ("muneco", "guemes_fbx")])
def test_real_figure_matches_golden(name, tmp_path):
    if not os.path.exists(golden.golden_path(name)):
        pytest.skip(f"falta tests/golden/{name}.json (python tools/golden.py guardar {name})")
    info = golden.build_figure(name, str(tmp_path))
    assert golden.compare(golden.snapshot(info), _gold(name)) == []


# --- Güemes por modo A (plan de color, T27): `AFM_SLOW=1`, ≈ 30 s de motor una sola vez -----------------------------------------
SLOW = os.environ.get("AFM_SLOW") == "1"


def test_compare_cross_tolerates_half_percent_only():
    g = _gold("guemes")
    assert golden.compare_cross(g, g) == []
    near = copy.deepcopy(g)
    k = next(iter(near["piezas"]))
    near["piezas"][k]["volumen"] *= 1.004                      # dentro de ±0,5 %
    assert golden.compare_cross(near, g) == []
    far = copy.deepcopy(g)
    far["piezas"][k]["volumen"] *= 1.006                       # fuera
    far["piezas"].pop(next(n for n in far["piezas"] if n != k))
    assert len(golden.compare_cross(far, g)) == 2


@pytest.fixture(scope="module")
def guemes_fbx_snapshot(tmp_path_factory):
    if not SLOW:
        pytest.skip("figura real: correr con AFM_SLOW=1")
    fbx = os.path.join(golden.EXAMPLES, golden.FIGURES["guemes_fbx"][1])
    if not os.path.exists(fbx):
        pytest.skip("falta examples/guemes_segmentado_lq_texturas.fbx")
    return golden.snapshot(golden.build_figure("guemes_fbx", str(tmp_path_factory.mktemp("guemes_fbx"))))


def test_golden_guemes_fbx_matches_frozen(guemes_fbx_snapshot):
    if not os.path.exists(golden.golden_path("guemes_fbx")):
        pytest.skip("falta tests/golden/guemes_fbx.json (python tools/golden.py guardar guemes_fbx)")
    assert golden.compare(guemes_fbx_snapshot, _gold("guemes_fbx")) == []


def test_guemes_fbx_vs_stl_within_half_percent(guemes_fbx_snapshot):
    assert golden.compare_cross(guemes_fbx_snapshot, _gold("guemes")) == []
