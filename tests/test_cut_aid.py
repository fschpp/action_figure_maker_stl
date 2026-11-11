"""Cinta de calor del corte (sin GTK): cuánto del catálogo acepta el corte según el desplazamiento del plano."""
import dataclasses
import shutil
import time

import numpy as np
import pytest

from afm import autosil
from afm import cut_aid as CA
from afm import guide as G
from afm import solids as S
from afm.catalog import Catalog
from afm.cut_aid import CutAid
from afm.extract import load_body
from afm.gui.session import ARM_SLAB_MM
from afm.limbs import LimbSpec
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll
from test_guide import EJEMPLO, clone


@pytest.fixture()
def cat(tmp_path):
    root = tmp_path / "linea"
    shutil.copytree(EJEMPLO, root)
    c = Catalog.open(str(root))
    for k, i in ((1.0, "a"), (0.9, "b"), (0.5, "c"), (0.3, "d")):
        clone(c, "torso-muneco-01", f"torso-otra-{i}", k)
    return c


@pytest.fixture(scope="module")
def doll(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("doll")
    stl = str(tmp / "muneco.stl")
    make_doll().export(stl)
    proj = demo_project()
    body, _rep = load_body(stl, proj)
    return proj, body


@pytest.fixture(scope="module")
def doll_outward(doll):
    """El muñeco con el brazo sobre el plano x = 15 y extrusión «outward» (como en `test_guide`)."""
    proj, body = doll
    arm0 = next(l for l in proj.limbs if l.kind == "arm")
    lat = Plane.from_points(np.array([15.0, 0, 60.0]), np.array([15.0, 10, 60.0]), np.array([15.0, 0, 75.0]), "hombro")
    poly = autosil.outline_on_plane(S.to_trimesh(body), np.asarray(arm0.polygon, float)[:, [0, 2]], lat)
    arm = LimbSpec("brazo", "arm", lat, poly, ARM_SLAB_MM, extrude="outward", auto=True)
    return dataclasses.replace(proj, limbs=[arm if l.kind == "arm" else l for l in proj.limbs]), body


def test_not_applicable_with_symmetric_extrusion(doll, cat):
    proj, body = doll
    aid = CutAid(proj, body, "brazo", cat)
    assert not aid.aplicable and "outward" in aid.motivo
    assert aid.ribbon() == [] and aid.best() is None
    r = aid.evaluate(10.0)
    assert r.nivel == "fuera" and r.plug is None
    assert not CutAid(dataclasses.replace(proj, limbs=[]), body, "brazo", cat).aplicable


def test_ribbon_covers_the_range_and_matches_the_scan(doll_outward, cat):
    proj, body = doll_outward
    aid = CutAid(proj, body, "brazo", cat)
    assert aid.aplicable
    t0 = time.time()
    rib = aid.ribbon(rango=1.0, paso=0.5)
    print(f"cinta de {len(rib)} puntos: {time.time() - t0:.1f} s")
    assert [round(off - aid.base, 6) for off, _ in rib] == [-1.0, -0.5, 0.0, 0.5, 1.0]
    scan = G.scan_cut_offset(proj, body, cat, "brazo", rango=1.0, paso=0.5)
    for (off, r), st in zip(rib, scan.steps):                      # mismo corte y mismo universo que el barrido de la guía
        assert off == pytest.approx(st.offset_mm)
        assert r.margen_mm == pytest.approx(st.margen_pasador, abs=1e-6)
        if r.plug is not None and r.socket is not None:
            assert r.plug.ok + r.plug.aviso + r.socket.ok + r.socket.aviso <= st.universo
    assert aid.ribbon() is rib                                     # una sola vez
    assert rib[0][1].nivel == "fuera"                              # a −1 mm el vástago sobresale (ver test_guide)


def test_evaluate_caches_by_cell_and_best_prefers_the_current_on_a_tie(doll_outward, cat, monkeypatch):
    proj, body = doll_outward
    aid = CutAid(proj, body, "brazo", cat)
    a = aid.evaluate(aid.base)
    monkeypatch.setattr(aid, "port_at", lambda *_: pytest.fail("recalculó"))
    b = aid.evaluate(aid.base + CA.CELL_MM * 0.3)
    assert b.nivel == a.nivel and b.uv[0] == pytest.approx(aid.base + CA.CELL_MM * 0.3)
