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
@pytest.mark.parametrize("name", [n for n in golden.FIGURES if n != "muneco"])
def test_real_figure_matches_golden(name, tmp_path):
    if not os.path.exists(golden.golden_path(name)):
        pytest.skip(f"falta tests/golden/{name}.json (python tools/golden.py guardar {name})")
    info = golden.build_figure(name, str(tmp_path))
    assert golden.compare(golden.snapshot(info), _gold(name)) == []
