"""Portabilidad del motor (port a Pyodide): un solo punto de importación de manifold3d y triangulación sin earcut."""
import pathlib
import re
from types import SimpleNamespace

import numpy as np
import trimesh

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "afm"


def test_manifold3d_is_imported_only_by_solids():
    pat = re.compile(r"^\s*(import manifold3d|from manifold3d)", re.M)
    offenders = [p.name for p in SRC.rglob("*.py") if p.name != "solids.py" and pat.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_port_face_triangulation_falls_back_without_earcut(monkeypatch):
    from afm import assembly

    real = trimesh.creation.triangulate_polygon

    def no_earcut(g, engine=None, **kw):
        if engine == "earcut":
            raise ImportError("mapbox_earcut no está")
        return real(g, engine=engine, **kw)

    monkeypatch.setattr(trimesh.creation, "triangulate_polygon", no_earcut)
    face = SimpleNamespace(polygons=[{"exterior": [(0, 0), (10, 0), (10, 10), (0, 10)],
                                      "interiors": [[(3, 3), (7, 3), (7, 7), (3, 7)]]}])
    frame = SimpleNamespace(axis=np.array([0.0, 0.0, 1.0]), ref=np.array([1.0, 0.0, 0.0]), center=np.zeros(3))
    tris = assembly._face_triangles(face, frame)
    area = 0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1).sum()
    assert abs(area - (100 - 16)) < 1e-6
