"""Probeta de rótula (ball): grilla holgura × Ø de bola armada con el código real del tipo (PLAN_plugins_articulacion.md §4.3, H2)."""
import io
import json
import zipfile

import numpy as np
import pytest

from afm import probes as P
from afm import profile as PF
from afm import solids as S
from afm.cli import main
from afm.joint_types.ball import BallSpec
from afm.params import Params


@pytest.fixture(scope="module")
def cells():
    return P.ball_probe_cells(Params())


@pytest.fixture(scope="module")
def built(cells):
    """{índice: (bloque, miembro, centro de la bola)} de unas celdas, separados por componentes conexas."""
    out = {}
    for i in (0, 4, 11):
        c = cells[i]
        m = S.to_trimesh(P._ball_cell(c, Params(), number=1))
        parts = sorted(m.split(only_watertight=False), key=lambda t: t.bounds[1][2])
        assert len(parts) == 2, "la celda debe ser dos piezas sueltas: el bloque (con su plaqueta) y el miembro"
        block, member = S.to_manifold(parts[0]), S.to_manifold(parts[1])
        q = Params()
        depth = Params().min_wall + c.spec.neck_len + c.spec.ball_d + c.clearance      # `requires().profundidad_mm` + una pared
        center = np.array([0.0, 0.0, depth - (c.spec.neck_len + c.spec.ball_d / 2)])
        out[i] = (block, member, center)
    return out


def test_default_grid_is_clearance_by_ball_diameter(cells):
    assert len(cells) == 12 and cells[0].label == "0.2/6.0" and cells[-1].label == "0.5/8.0"
    assert {c.clearance for c in cells} == set(P.BALL_CLEARANCES) and {c.spec.ball_d for c in cells} == {6.0, 7.0, 8.0}
    assert all(c.lip_margin > 0 for c in cells)
    txt = P.ball_labels_text(cells).splitlines()
    assert txt[0].startswith("#1: holgura 0.2 mm, bola Ø6.0 mm (labio 0.55 mm)") and len(txt) == 12


def test_cells_that_would_not_hold_the_ball_are_dropped():
    only_wide = P.ball_probe_cells(Params(), clearances=(1.0,), diameter_deltas=(-1.0, 0.0, 1.0))
    assert [c.spec.ball_d for c in only_wide] == [8.0]                         # con 1 mm de luz solo la bola grande conserva labio
    assert P.ball_probe_cells(Params(), clearances=(1.0,), diameter_deltas=(-1.0,)) == []
    assert P.ball_probe_cells(Params(), diameter_deltas=(-4.0,)) == []         # Ø3 = el cuello: ya no es una bola


@pytest.mark.parametrize("i", [0, 4, 11])
def test_the_member_tilts_and_turns_inside_its_cone_without_touching(built, cells, i):
    block, member, center = built[i]
    half = cells[i].spec.half_angle
    for tilt in (0.0, half / 2, half):
        for az in (0.0, 90.0, 180.0, 270.0):
            a = np.radians(az)
            axis = np.array([np.cos(a), np.sin(a), 0.0])
            m = S.rotate_about_axis(member, center, axis, tilt)
            assert float(S.intersection(block, m).volume()) < 1e-3, f"roza a {tilt:g}° hacia {az:g}°"
    for ang in (45.0, 135.0, 270.0):                                            # y gira sobre sí mismo
        assert float(S.intersection(block, S.rotate_about_axis(member, center, (0, 0, 1), ang)).volume()) < 1e-3


@pytest.mark.parametrize("i", [0, 4, 11])
def test_the_ball_cannot_be_pulled_out(built, i):
    block, member, _c = built[i]
    assert float(S.intersection(block, member.translate((0, 0, 1.0))).volume()) > 1e-3     # el labio la ataja
    assert float(S.intersection(block, member.translate((0, 0, 0.05))).volume()) < 1e-3    # y con menos que la luz todavía no


def test_the_cell_sits_on_the_bed_and_the_handle_is_on_top(built):
    block, member, _c = built[4]
    assert S.to_trimesh(block).bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    assert S.to_trimesh(member).bounds[1][2] > S.to_trimesh(block).bounds[1][2] + 5.0      # el mango sobresale del bloque


def test_probe_files_rotula_has_stl_3mf_table_and_manifest():
    files, text = P.probe_files("rotula")
    assert set(files) == {"probetas.stl", "probetas.3mf", "probetas.txt", "probetas.json"} and text.startswith("#1:")
    man = json.loads(files["probetas.json"])
    assert man["tipo"] == "rotula" and len(man["celdas"]) == 12
    assert man["celdas"][0] == {"n": 1, "holgura": 0.2, "bola": 6.0, "cuello": 3.0, "semiangulo": 20.0, "labio": 0.551}
    assert zipfile.ZipFile(io.BytesIO(files["probetas.3mf"])).read("3D/3dmodel.model").decode().count("<object ") == 12
    with pytest.raises(ValueError, match="retiene"):
        P.probe_files("rotula", clearances=[1.0], deltas=[-1.0])


def test_the_chosen_cell_goes_to_the_profile_as_clearance_only():
    files, _t = P.probe_files("rotula", clearances=[0.3, 0.4], deltas=[0.0])
    man = json.loads(files["probetas.json"])
    assert PF.probe_cell_changes(man, 2) == {"clearance": 0.4}
    assert "bola Ø7" in PF.probe_cell_summary(man, PF.probe_cell(man, 2)) and "(rótula)" in PF.probe_cell_summary(man, PF.probe_cell(man, 2))


def test_cli_probe_rotula_writes_the_files(tmp_path):
    out = tmp_path / "p"
    assert main(["probe", "--tipo", "rotula", "--clearances", "0.3,0.4", "--deltas", "0", "--out", str(out)]) == 0
    assert {f.name for f in out.iterdir()} >= {"probetas.stl", "probetas.3mf", "probetas.txt", "probetas.json"}
    assert json.loads((out / "probetas.json").read_text())["tipo"] == "rotula"
