import pytest

from afm import probes, solids as S
from afm.params import Params


def test_probe_cells_grid():
    cells = probes.probe_cells(Params())
    assert len(cells) == 12
    assert len({c.label for c in cells}) == 12


def test_probe_builds_manifold():
    p = Params()
    cells = probes.probe_cells(p, clearances=(0.4, 0.6), shaft_deltas=(0.0,))
    mf = probes.build_probe(p, cells, columns=2)
    assert mf.status() == S.m3.Error.NoError
    assert mf.volume() > 0
    assert len(mf.decompose()) == 2 * len(cells)  # bloque (con su plaqueta) y miembro (con su mango) separados por celda


# --- probeta de bisagra (rodilla / codo) -------------------------------------------------------------
import numpy as np


def _one_hinge(**kw):
    p = Params()
    cells = probes.hinge_probe_cells(p, clearances=(0.4,), pin_deltas=(0.0,), **kw)
    return p, cells, probes.build_hinge_probe(p, cells, columns=1, for_print=False)   # marco del eje: sin voltear ni número


def test_hinge_cells_grid():
    cells = probes.hinge_probe_cells(Params())
    assert len(cells) == 9 and len({c.label for c in cells}) == 9
    cells = probes.hinge_probe_cells(Params(), axial_clearances=(0.25, 0.45))
    assert len(cells) == 18
    assert "radial" in probes.hinge_labels_text(cells)


def test_hinge_probe_two_loose_parts_and_free_in_range():
    p, cells, mf = _one_hinge()
    assert mf.status() == S.m3.Error.NoError
    parts = sorted(mf.decompose(), key=lambda m: m.volume())
    assert len(parts) == 2
    distal, yoke = parts
    axis = np.array([1.0, 0, 0])
    for ang in (0.0, 45.0, 90.0):                       # dentro del barrido: gira sin tocar el yugo
        moved = S.rotate_about_axis(distal, (0, 0, 0), axis, ang)
        assert S.intersection(moved, yoke).volume() < 1e-4
    out = S.rotate_about_axis(distal, (0, 0, 0), axis, -60.0)   # fuera del barrido (hiperextensión): topa
    assert S.intersection(out, yoke).volume() > 0.5


def test_hinge_probe_distal_is_retained():
    p, cells, mf = _one_hinge()
    distal, yoke = sorted(mf.decompose(), key=lambda m: m.volume())
    # desplazada 1 mm en ±X, ±Y o +Z choca con el yugo (ranura + agujeros ciegos: no hay salida)
    for d in ((0, 0, 1), (0, 1, 0), (0, -1, 0), (1, 0, 0), (-1, 0, 0)):
        assert S.intersection(distal.translate(d), yoke).volume() > 1e-3
    # hacia abajo el vástago sale por la ranura, pero el disco y los bulones no pasan
    assert S.intersection(distal.translate((0, 0, -3.0)), yoke).volume() > 1e-3


def test_cli_probe_hinge(tmp_path):
    from afm.cli import main
    rc = main(["probe", "--tipo", "bisagra", "--clearances", "0.4", "--deltas", "0", "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "probetas.stl").stat().st_size > 1000
    assert "bulón" in (tmp_path / "probetas.txt").read_text()


# --- rótulos, mangos y archivos de la tirada --------------------------------------------------------------
import io
import json
import zipfile

import trimesh


def test_text_solid_digits_and_size():
    one, twelve = probes.text_solid("1"), probes.text_solid("12")
    assert one.status() == S.m3.Error.NoError and twelve.status() == S.m3.Error.NoError
    bb = S.to_trimesh(twelve).bounds
    w, h, z = bb[1] - bb[0]
    assert probes.text_width("12") - 2 * probes.LABEL_PX < w <= probes.text_width("12") + 0.02    # el «1» no usa la columna de la izquierda
    assert h == pytest.approx(7 * probes.LABEL_PX + 0.02, abs=1e-3) and z == pytest.approx(probes.LABEL_H, abs=1e-3)
    assert twelve.volume() > one.volume() > 0
    with pytest.raises(ValueError):
        probes.text_solid("a")


def test_number_plaque_is_attached_in_front_and_raised():
    pl = probes.number_plaque(7, 0.0, -7.0, 0.0, 14.0)
    bb = S.to_trimesh(pl).bounds
    assert bb[0][1] == pytest.approx(-7.0 - probes.PLAQUE_LEN) and bb[1][1] == pytest.approx(-6.5)   # 0,5 mm de solape con el bloque
    assert bb[1][2] == pytest.approx(probes.PLAQUE_T + probes.LABEL_H, abs=0.02)
    assert len(pl.decompose()) == 1
    wide = probes.number_plaque(123, 0.0, -7.0, 0.0, 6.0)             # no cabe en 6 mm: la plaqueta crece
    assert S.to_trimesh(wide).bounds[1][0] - S.to_trimesh(wide).bounds[0][0] >= probes.text_width("123") + 2.0 - 1e-6


def test_hongo_cell_has_a_handle_that_clears_the_block_and_turns():
    p = Params()
    cell = probes.probe_cells(p, clearances=(0.4,), shaft_deltas=(0.0,))[0]
    m = probes._cell(cell, number=3)
    parts = sorted(m.decompose(), key=lambda x: x.volume())
    limb, body = parts
    pin = cell.pin
    depth = pin.shaft_len + pin.head_h + 4.0
    assert S.to_trimesh(limb).bounds[1][2] > depth + 2.0 + probes.HANDLE_POST_H            # el mango sale por encima del bloque
    assert S.to_trimesh(body).bounds[0][1] == pytest.approx(-7.0 - probes.PLAQUE_LEN)       # la plaqueta del número va delante
    for ang in (0.0, 30.0, 45.0, 90.0, 135.0):                                              # gira libre alrededor del pasador
        moved = S.rotate_about_axis(limb, (0, 0, 0), (0, 0, 1), ang)
        assert S.intersection(moved, body).volume() < 1e-4


def test_hinge_print_orientation_sits_on_the_bed_with_a_number():
    p = Params()
    cells = probes.hinge_probe_cells(p, clearances=(0.4,), pin_deltas=(0.0,))
    mf = probes.build_hinge_probe(p, cells, columns=1)                     # por defecto: para imprimir
    bb = S.to_trimesh(mf).bounds
    assert bb[0][2] == pytest.approx(0.0, abs=1e-6)                         # el yugo apoya en la cama (antes colgaba 4 mm)
    assert bb[0][1] == pytest.approx(-6.0 - probes.PLAQUE_LEN)              # plaqueta con el número delante
    assert bb[1][2] > 15.0                                                  # la palanca sale hacia arriba
    parts = sorted(mf.decompose(), key=lambda m: m.volume())
    assert len(parts) == 2
    distal, yoke = parts
    assert S.to_trimesh(yoke).bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    h = cells[0].hinge
    axis_z = h.end_r + cells[0].clearance + 1.5                             # z del eje en el marco de impresión (= top)
    for ang in (0.0, -45.0, -90.0):                                         # la palanca gira hacia el frente (−Y, donde está el número) sin tocar el yugo
        moved = S.rotate_about_axis(distal, (0, 0, axis_z), np.array([1.0, 0, 0]), ang)
        assert S.intersection(moved, yoke).volume() < 1e-4
    assert S.intersection(S.rotate_about_axis(distal, (0, 0, axis_z), np.array([1.0, 0, 0]), 90.0), yoke).volume() > 1.0   # hacia atrás topa


def test_hinge_lever_clears_yoke_in_axis_frame():
    p, cells, mf = _one_hinge()
    distal, yoke = sorted(mf.decompose(), key=lambda m: m.volume())
    assert S.to_trimesh(distal).bounds[0][2] < -15.0                        # vástago + palanca + T
    for ang in range(0, 91, 10):
        moved = S.rotate_about_axis(distal, (0, 0, 0), np.array([1.0, 0, 0]), float(ang))
        assert S.intersection(moved, yoke).volume() < 1e-4


def test_probe_files_ship_stl_3mf_table_and_manifest():
    files, text = probes.probe_files("hongo", clearances=[0.4, 0.5], deltas=[0.0], pasador="cadera")
    assert sorted(files) == ["probetas.3mf", "probetas.json", "probetas.stl", "probetas.txt"]
    man = json.loads(files["probetas.json"])
    assert man["formato"] == "afm-probetas-1" and man["tipo"] == "hongo" and man["pasador"] == "cadera"
    assert [c["n"] for c in man["celdas"]] == [1, 2] and man["celdas"][0]["holgura"] == 0.4
    assert man["celdas"][0]["vastago"] == Params().hip_pin.shaft_d               # parte del pasador de la cadera
    lines = text.splitlines()
    sc = trimesh.load(io.BytesIO(files["probetas.3mf"]), file_type="3mf")
    assert sorted(sc.geometry) == sorted(lines)                                   # el objeto del 3MF se llama como su línea de la tabla
    zf = zipfile.ZipFile(io.BytesIO(files["probetas.3mf"]))
    assert "3D/3dmodel.model" in zf.namelist() and 'unit="millimeter"' in zf.read("3D/3dmodel.model").decode()
    stl = trimesh.load(io.BytesIO(files["probetas.stl"]), file_type="stl")
    assert len(stl.split(only_watertight=False)) == 4                              # 2 celdas × (bloque + miembro)
    with pytest.raises(ValueError):
        probes.probe_files("hongo", pasador="rodilla")


def test_probe_files_use_given_params():
    p = Params()
    p.shoulder_pin = type(p.shoulder_pin)(shaft_d=3.0, shaft_len=2.5, head_d=5.0, head_h=1.5)
    files, _ = probes.probe_files("hongo", clearances=[0.4], deltas=[0.0], params=p)
    assert json.loads(files["probetas.json"])["celdas"][0]["vastago"] == 3.0
