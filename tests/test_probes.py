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
