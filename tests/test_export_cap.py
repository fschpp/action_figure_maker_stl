"""Tope de triángulos por pieza al exportar (`Params.export_max_tris`, `pipeline.cap_export_triangles`)."""
import dataclasses
import os

import numpy as np
import trimesh

from afm import pipeline as P
from afm import solids as S
from afm.params import Params


def _dense_sphere(sub=6, r=10.0, center=(0.0, 0.0, 0.0)):
    m = trimesh.creation.icosphere(subdivisions=sub, radius=r)
    m.apply_translation(center)
    return S.to_manifold(m)


def _pieces():
    big = _dense_sphere()                                       # 81 920 triángulos
    small = S.Manifold.cube((4, 4, 4), center=True).translate((14.5, 0, 0))   # a ~0,5 mm de la esfera
    return {"torso": big, "brazo": small}


def test_only_pieces_over_the_cap_are_touched():
    pieces = _pieces()
    out, rep = P.cap_export_triangles(pieces, 20000)
    assert set(rep) == {"torso"}
    n0, n1 = rep["torso"]
    assert n0 == pieces["torso"].num_tri() and n1 < n0 and n1 <= 20000
    assert out["brazo"] is pieces["brazo"]
    assert abs(out["torso"].volume() - pieces["torso"].volume()) <= 0.002 * pieces["torso"].volume()


def test_clearance_to_other_pieces_is_kept():
    pieces = _pieces()
    g0 = pieces["brazo"].min_gap(pieces["torso"], 5.0)
    out, _ = P.cap_export_triangles(pieces, 20000)
    assert out["brazo"].min_gap(out["torso"], 5.0) >= g0 - 0.02


def test_cap_zero_or_big_does_nothing():
    pieces = _pieces()
    assert P.cap_export_triangles(pieces, 0)[1] == {}
    out, rep = P.cap_export_triangles(pieces, 10_000_000)
    assert rep == {} and out["torso"] is pieces["torso"]


def test_export_writes_smaller_stl(tmp_path):
    pieces = _pieces()
    res = P.BuildResult(pieces=pieces)
    p_off = dataclasses.replace(Params(), export_max_tris=0)
    p_on = dataclasses.replace(Params(), export_max_tris=20000)
    a, b = tmp_path / "a", tmp_path / "b"
    info_off = P.export(res, str(a), "f", params=p_off)
    info_on = P.export(res, str(b), "f", params=p_on)
    assert "triangulos_reducidos" not in info_off
    assert info_on["triangulos_reducidos"]["torso"][1] <= 20000
    size = lambda d: os.path.getsize(d / "f_ensamblada.stl")
    assert size(b) < 0.4 * size(a)
    m = trimesh.load(b / "piezas" / "f_torso.stl")
    assert m.is_watertight and len(m.faces) <= 20000
