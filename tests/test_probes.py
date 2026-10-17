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
    assert len(mf.decompose()) >= 2 * len(cells)  # bloque y miembro separados por celda


# --- probeta de bisagra (rodilla / codo) -------------------------------------------------------------
import numpy as np


def _one_hinge(**kw):
    p = Params()
    cells = probes.hinge_probe_cells(p, clearances=(0.4,), pin_deltas=(0.0,), **kw)
    return p, cells, probes.build_hinge_probe(p, cells, columns=1)


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
