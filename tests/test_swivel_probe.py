"""Probeta de giro (swivel): grilla holgura × Ø de vástago armada con el código real del tipo (PLAN_plugins_articulacion.md §4.1, H1)."""
import json
import zipfile
import io

import numpy as np
import pytest

from afm import probes as P
from afm import profile as PF
from afm import solids as S
from afm.cli import main
from afm.params import Params


@pytest.fixture(scope="module")
def cells():
    return P.swivel_probe_cells(Params())


def _members(cell, p=None):
    """(bloque con su plaqueta, miembro suelto con su mango) de una celda, separados por componentes conexas."""
    m = S.to_trimesh(P._swivel_cell(cell, p or Params(), number=1))
    parts = m.split(only_watertight=False)
    assert len(parts) == 2, "la celda debe ser dos piezas sueltas: el bloque y el miembro"
    parts = sorted(parts, key=lambda t: t.bounds[1][2])
    return S.to_manifold(parts[0]), S.to_manifold(parts[1])


def test_default_grid_is_clearance_by_shaft(cells):
    assert len(cells) == 12 and cells[0].label == "0.3/2.8" and cells[-1].label == "0.6/3.2"
    assert {c.clearance for c in cells} == set(P.DEFAULT_CLEARANCES)
    assert {c.pin.shaft_d for c in cells} == {2.8, 3.0, 3.2}
    txt = P.swivel_labels_text(cells).splitlines()
    assert txt[0] == "#1: holgura 0.3 mm, vástago Ø2.8 mm" and len(txt) == 12


@pytest.mark.parametrize("i", [0, 5, 11])
def test_a_cell_is_a_block_and_a_free_member_that_turns_without_touching(cells, i):
    block, member = _members(cells[i])
    p = Params()
    for ang in range(0, 360, 45):
        turned = S.rotate_about_axis(member, (0, 0, 0), (0, 0, 1), float(ang))
        assert float(S.intersection(block, turned).volume()) < 1e-3, f"roza a {ang}°"


@pytest.mark.parametrize("i", [0, 11])
def test_the_member_cannot_be_pulled_out(cells, i):
    """Retención: subir el miembro 1 mm choca con el collar que sujeta la cabeza (como la prueba del kit para el tipo)."""
    block, member = _members(cells[i])
    assert float(S.intersection(block, member.translate((0, 0, 1.0))).volume()) > 1e-3
    assert float(S.intersection(block, member.translate((0, 0, 0.1))).volume()) < 1e-3     # y con menos que la holgura todavía no


def test_the_cell_sits_on_the_bed_and_the_proximal_half_is_below(cells):
    block, member = _members(cells[4])
    assert S.to_trimesh(block).bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    assert S.to_trimesh(member).bounds[1][2] > S.to_trimesh(block).bounds[1][2]             # el mango queda arriba


def test_probe_files_giro_has_stl_3mf_table_and_manifest():
    files, text = P.probe_files("giro")
    assert set(files) == {"probetas.stl", "probetas.3mf", "probetas.txt", "probetas.json"} and text.startswith("#1:")
    man = json.loads(files["probetas.json"])
    assert man["tipo"] == "giro" and len(man["celdas"]) == 12 and man["celdas"][0] == {"n": 1, "holgura": 0.3, "vastago": 2.8}
    names = zipfile.ZipFile(io.BytesIO(files["probetas.3mf"])).read("3D/3dmodel.model").decode()
    assert names.count("<object ") == 12
    assert S.to_trimesh(S.union(*[P._swivel_cell(c, Params()) for c in P.swivel_probe_cells(Params())[:1]])).is_watertight


def test_probe_files_giro_accepts_custom_values():
    files, _ = P.probe_files("giro", clearances=[0.4], deltas=[0.0, 0.1])
    assert [c["vastago"] for c in json.loads(files["probetas.json"])["celdas"]] == [3.0, 3.1]


def test_the_chosen_cell_brings_back_the_clearance_to_the_profile():
    files, _ = P.probe_files("giro", clearances=[0.4, 0.5], deltas=[0.0])
    man = json.loads(files["probetas.json"])
    assert PF.probe_cell_changes(man, 2) == {"clearance": 0.5}
    new, resumen = PF.apply_probe_cell(Params(), man, 2)
    assert new.clearance == 0.5 and resumen == "holgura 0.5 mm, vástago Ø3 mm (giro)"


def test_cli_probe_giro_and_result(tmp_path, capsys):
    out = tmp_path / "pr"
    out.mkdir()
    assert main(["probe", "--tipo", "giro", "--out", str(out), "--clearances", "0.4,0.5", "--deltas", "0"]) == 0
    text = capsys.readouterr().out
    assert "#2: holgura 0.5 mm" in text and "probe-resultado" in text
    assert sorted(p.name for p in out.iterdir()) == ["probetas.3mf", "probetas.json", "probetas.stl", "probetas.txt"]
