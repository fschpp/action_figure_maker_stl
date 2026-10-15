"""Propuesta automática de siluetas (afm.autosil)."""
import importlib.util
import os

import numpy as np
import pytest

from afm import autosil, limbs as L, solids as S
from afm.gui.controller import Controller, Mode
from afm.gui.session import Session
from afm.limbs import LimbSpec
from afm.planes import Plane
from afm.solids import Manifold

HERE = os.path.dirname(__file__)
EX = os.path.join(HERE, "..", "examples")


def box(x0, x1, z0, z1, y=10.0):
    return Manifold.cube((x1 - x0, 2 * y, z1 - z0)).translate((x0, -y, z0))


def make_figure():
    """Tronco z 40–80 (x ±10), hombros hasta x = 18 (z 70–76), brazos x 14–18 z 40–76 separados del tronco
    bajo z = 70, piernas x 1–9 (z 0–40) con hueco en x = 0 bajo la entrepierna (z = 40)."""
    parts = [box(-10, 10, 40, 80), box(-18, 18, 70, 76), box(14, 18, 40, 76)]
    for s in (1, -1):
        parts.append(box(1, 9, 0, 40) if s > 0 else box(-9, -1, 0, 40))
    return S.to_trimesh(S.union(*parts))


@pytest.fixture(scope="module")
def fig():
    return make_figure()


def test_slice_separates_arm_and_leg_components(fig):
    s = autosil.slice_at(fig, 55.0)
    assert s.inner == pytest.approx((0.0, 10.0), abs=1e-6)
    assert len(s.outer) == 1 and s.outer[0][0] == pytest.approx(14.0, abs=1e-6)
    low = autosil.slice_at(fig, 20.0)
    assert low.inner[0] == pytest.approx(1.0, abs=1e-6) and low.outer == []


def test_propose_arm_covers_arm_not_torso(fig):
    p = autosil.propose_arm(fig)
    assert p is not None
    from shapely.geometry import Polygon
    pg = Polygon(p)
    assert pg.is_valid
    zmin, zmax = pg.bounds[1], pg.bounds[3]
    assert 38.0 < zmin < 41.0 and 76.0 < zmax < 78.0           # de la mano al tope del hombro (+1 mm de margen)
    # en la parte separada el borde cae en el hueco entre tronco (x=10) y brazo (x=14)
    from shapely.geometry import box as sbox
    band = pg.intersection(sbox(-50, 50, 50, 60))
    assert 10.0 < band.bounds[0] < 14.0
    # y el brazo entero queda adentro
    assert pg.contains(sbox(14.0, 41, 18.0, 75))


def test_propose_arm_none_when_always_fused():
    m = S.to_trimesh(S.union(box(-10, 10, 40, 80), box(-18, 18, 60, 76)))
    assert autosil.propose_arm(m) is None


def test_propose_leg_uses_crotch_and_hip_chunk(fig):
    p = autosil.propose_leg(fig)
    from shapely.geometry import Polygon, box as sbox
    pg = Polygon(p)
    assert pg.is_valid
    assert pg.bounds[3] == pytest.approx(50.0, abs=0.6)          # entrepierna (40) + 10 mm de cadera
    assert pg.contains(sbox(1.0, 1, 9.0, 39))                      # columna completa
    assert not pg.intersects(sbox(0.1, 41, 6.5, 49))               # arriba de la entrepierna solo x > 7
    assert pg.intersects(sbox(8, 41, 9, 49))
    q = autosil.propose_leg(fig, crotch_z=30.0)
    assert Polygon(q).bounds[3] == pytest.approx(40.0, abs=0.6)


def test_lift_to_plane_and_edge_on_plane():
    xz = np.array([[1.0, 2.0], [3.0, 2.0], [3.0, 5.0]])
    fp = Plane.from_normal((0, 4.0, 0), (0, 1, 0))
    assert np.allclose(autosil.lift_to_plane(xz, fp)[:, 1], 4.0)
    tilt = Plane.from_normal((0, 4.0, 0), (0, 1, 1))              # inclinado: los puntos caen sobre el plano
    q = autosil.lift_to_plane(xz, tilt)
    assert np.allclose((q - tilt.origin) @ tilt.normal, 0.0, atol=1e-9)
    with pytest.raises(ValueError):
        autosil.lift_to_plane(xz, Plane.from_normal((0, 0, 0), (1, 0, 0)))   # de canto


def _session(fig, tmp_path):
    path = str(tmp_path / "fig.stl")
    fig.export(path)
    s = Session()
    s.load(path, crown_z=80.0)
    return s


def test_session_propose_silhouette_saves_polygon(fig, tmp_path):
    s = _session(fig, tmp_path)
    fp = Plane.from_normal((0, 0, 0), (0, 1, 0), "frontal")
    s.add_limb("brazo", "arm", fp)
    poly = s.propose_silhouette("brazo")
    assert len(poly) >= 4 and np.allclose(s.limb("brazo").polygon, poly)
    s.add_limb("pierna", "leg", fp)
    assert len(s.propose_silhouette("pierna", crotch_z=40.0)) >= 4
    with pytest.raises(KeyError):
        s.propose_silhouette("nada")


def test_session_propose_fails_with_clear_message(tmp_path):
    fused = S.to_trimesh(S.union(box(-10, 10, 40, 80), box(-18, 18, 60, 76)))
    s = _session(fused, tmp_path)
    s.add_limb("brazo", "arm", Plane.from_normal((0, 0, 0), (0, 1, 0)))
    with pytest.raises(ValueError, match="tramo"):
        s.propose_silhouette("brazo")


def test_controller_propose_replaces_draft(fig, tmp_path):
    path = str(tmp_path / "fig.stl")
    fig.export(path)
    c = Controller()
    c.open_stl(path)
    c.session.add_limb("brazo", "arm", Plane.from_normal((0, 0, 0), (0, 1, 0)))
    c.propose_silhouette()                       # fuera del modo silueta: no hace nada
    assert c.draft is None
    c.start_polygon("brazo")
    c.propose_silhouette()
    assert c.mode == Mode.POLYGON and c.draft.closed and len(c.draft.pts) >= 4
    assert len(c.session.limb("brazo").polygon) == len(c.draft.pts)


# ---- contra los polígonos hechos a mano de los personajes reales (más lento) -------------------------------

def _example(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(EX, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _xor_pct(body, lm, poly_xz):
    auto = LimbSpec(lm.name, lm.kind, lm.plane, autosil.lift_to_plane(poly_xz, lm.plane), lm.thickness,
                    extrude=lm.extrude)
    a = S.largest_component(S.intersection(body, L.limb_prism(auto)))
    b = S.largest_component(S.intersection(body, L.limb_prism(lm)))
    vi = S.intersection(a, b).volume()
    return (a.volume() + b.volume() - 2 * vi) / b.volume() * 100


@pytest.mark.parametrize("name,stl,crotch,arm_tol,leg_tol", [
    ("guemes", "guemes_segmentado_lq_fixed_resized.stl", None, 8.0, 3.0),
    ("belgrano", "belgrano_lq_segmentado.stl", 38.0, 8.0, 2.0),
    ("azurduy", "azurduy_lq_segmentado.stl", 42.0, 8.0, 2.0),
])
def test_matches_hand_drawn_silhouettes(name, stl, crotch, arm_tol, leg_tol):
    m = _example(name)
    s = Session(m.proj)
    s.load(os.path.join(EX, stl), m.proj.crown_z)
    sl = autosil.scan(s.work)
    body = S.to_manifold(s.work)
    arm, leg = m.proj.limbs
    assert _xor_pct(body, arm, autosil.propose_arm(s.work, sl)) < arm_tol
    assert _xor_pct(body, leg, autosil.propose_leg(s.work, sl, crotch_z=crotch)) < leg_tol
