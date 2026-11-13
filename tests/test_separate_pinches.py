"""Mallas con láminas que se tocan en una arista (4 caras): `meshio.separate_pinches` y su uso al catalogar (`extract._clean`).

Caso real: una figura reparada en línea (rulos de pelo que casi se tocan) daba «la malla … no es estanca» al catalogar."""
import numpy as np
import pytest
import trimesh

from afm import extract as X
from afm import meshio


def two_boxes_touching_at_an_edge() -> trimesh.Trimesh:
    a = trimesh.creation.box(extents=(20, 20, 20))      # mm: el tamaño de una pieza real (la separación es de 0,01 mm)
    b = trimesh.creation.box(extents=(20, 20, 20))
    b.apply_translation((20, 20, 0))                    # comparte con `a` la arista x=10, y=10
    m = trimesh.util.concatenate([a, b])
    m.merge_vertices()
    return m


def test_the_fixture_is_really_pinched():
    m = two_boxes_touching_at_an_edge()
    _u, cnt = np.unique(m.edges_sorted, axis=0, return_counts=True)
    assert not m.is_watertight and sorted(set(cnt.tolist())) == [2, 4]


def test_separate_pinches_makes_it_watertight_even_after_welding_again():
    m = two_boxes_touching_at_an_edge()
    fixed = meshio.separate_pinches(m)
    assert fixed is not None and fixed.is_watertight and fixed.is_winding_consistent
    assert fixed.volume == pytest.approx(16000.0, rel=5e-4)       # dos cajas de 8000 mm³: casi no pierde material
    welded = fixed.copy()
    welded.merge_vertices()                                        # lo que hace el laminador al leer el STL
    assert welded.is_watertight
    assert len(fixed.vertices) > len(m.vertices)                   # los vértices pellizcados se duplicaron


def test_separate_pinches_only_touches_pinched_edges():
    clean = trimesh.creation.box(extents=(1, 1, 1))
    assert meshio.separate_pinches(clean) is None                 # nada que arreglar
    holed = clean.copy()
    holed.update_faces(np.arange(len(holed.faces)) != 0)           # un hueco (aristas de 1 cara): lo cura otro método
    assert meshio.separate_pinches(holed) is None
    pinched = two_boxes_touching_at_an_edge()
    assert meshio.separate_pinches(pinched, max_edges=0) is None   # tope de aristas


def test_clean_heals_a_pinched_piece_and_says_so(monkeypatch):
    pinched = two_boxes_touching_at_an_edge()
    monkeypatch.setattr(X.S, "to_printable_trimesh", lambda mf: pinched)
    warnings = []
    tm = X._clean(object(), "cabeza", warnings)
    assert tm.is_watertight
    assert any("láminas" in w and "cabeza" in w for w in warnings)


def test_clean_still_reports_what_it_cannot_heal(monkeypatch):
    holed = trimesh.creation.box(extents=(1, 1, 1))
    holed.update_faces(np.arange(len(holed.faces)) != 0)
    monkeypatch.setattr(X.S, "to_printable_trimesh", lambda mf: holed)
    monkeypatch.setattr(X.meshio, "_meshfix", lambda tm: None)
    warnings = []
    tm = X._clean(object(), "brazo", warnings)
    assert not tm.is_watertight and any("no se pudo curar" in w for w in warnings)


def test_fill_small_loops_closes_a_polygonal_hole_without_new_nonmanifold_edges():
    """Tras separar un pellizco pueden quedar lazos de borde de más de 4 aristas (`fill_holes` no los cierra): se cierran con un
    abanico que no repite aristas de la malla."""
    import numpy as np
    import trimesh
    from afm import meshio

    ball = trimesh.creation.icosphere(subdivisions=2)
    vol = float(ball.volume)
    keep = ~np.isin(np.arange(len(ball.faces)), np.where((ball.faces == 7).any(axis=1))[0])   # se quitan las caras que rodean al vértice 7
    holed = trimesh.Trimesh(ball.vertices, ball.faces[keep], process=False)
    assert not holed.is_watertight
    out = meshio._fill_small_loops(holed)
    assert out.is_watertight and out.is_winding_consistent
    assert abs(float(out.volume) - vol) / vol < 0.02
