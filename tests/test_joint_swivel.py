"""Plugins de articulación, Fase 2 (corte 1): tipo `swivel` y `offer()` (oferta de tipos por corte)."""
import dataclasses

import numpy as np
import pytest
from shapely.geometry import Point

from afm import joint_types as JT
from afm import solids as S
from afm.joint_types import testing as K
from afm.joint_types.swivel import DEFAULT_PIN, Swivel
from afm.limbs import ContactFace
from afm.params import Params, PinSpec
from afm.planes import Plane


def _site(**kw):
    site, _ = Swivel().test_site()
    return dataclasses.replace(site, **kw) if kw else site


def _vol(m):
    return float(m.volume())


def test_swivel_is_registered_builtin_and_offers_its_slots():
    t = JT.get("swivel")
    assert t.slots == frozenset({"biceps", "muslo", "canilla"}) and "swivel" in JT.BUILTIN
    assert set(t.labels) == {"es", "en", "pt"}
    assert JT.cache_key_info(Params(joints={})) == {}          # un tipo interno no cambia la clave de caché


def test_swivel_passes_the_conformance_kit():
    assert not K.failures(K.check_type(Swivel())), "el kit de conformidad no pasa"


def test_free_turn_and_axis_is_the_section_centre_along_the_normal():
    r = Swivel().build(_site())
    m = r.motion
    assert np.allclose(m.axis, [0, 0, 1]) and np.allclose(m.center[:2], [0, 0], atol=1e-2) and abs(m.center[2] + 20.0) < 1e-6
    assert tuple(m.range) == (0.0, 360.0)
    worst = max(_vol(S.intersection(m.limb.transform(T[:3, :]), r.pieces["prox"])) for T in m.poses(15.0))
    assert worst < 1e-4


def test_normal_is_oriented_towards_the_contact_face():
    site = _site(plane=Plane.from_normal((0.0, 0.0, -20.0), (0.0, 0.0, -1.0), "giro"))   # normal dada al revés
    r = Swivel().build(site)
    assert np.allclose(r.motion.axis, [0, 0, 1])
    assert _vol(r.pieces["prox"]) < _vol(r.pieces["dist"]) + 1.0 and r.pieces["prox"].bounding_box()[2] > -20.0   # lo proximal queda arriba


def test_retention_does_not_come_off_when_pulled_one_mm():
    r = Swivel().build(_site())
    n = np.asarray(r.motion.axis, float)
    for d in (1.0, 2.0):                                        # tirar de la mitad distal alejándola de la proximal
        T = np.eye(4)[:3, :]
        T[:3, 3] = -n * d
        assert _vol(S.intersection(r.motion.limb.transform(T), r.pieces["prox"])) > 1e-3, f"se soltó con {d} mm"
    T = np.eye(4)[:3, :]
    T[:3, 3] = n * 0.2                                           # acercarlas la luz que hay: no se tocan
    assert _vol(S.intersection(r.motion.limb.transform(T), r.pieces["prox"])) < 1e-4


def test_gap_and_wall_with_the_default_pin():
    p = Params()
    r = Swivel().build(_site())
    assert r.report["collar_mm"] >= p.min_wall and not r.warnings
    zmin = r.pieces["prox"].bounding_box()[2]
    assert abs(zmin - (-20.0 + p.clearance)) < 1e-6              # luz axial = holgura
    assert _vol(r.pieces["prox"]) < _vol(S.Manifold.cylinder(20.0 - p.clearance, 6.0, 6.0, 48))   # la cavidad resta algo


def test_thin_pin_and_thin_collar_warn():
    r = Swivel().build(_site(jp=PinSpec(2.0, 2.0, 4.0, 1.0)))
    assert any("collar" in w for w in r.warnings) and any("vástago" in w for w in r.warnings)


def test_plane_that_misses_the_limb_and_missing_plane_are_errors():
    with pytest.raises(ValueError, match="no toca"):
        Swivel().build(_site(plane=Plane.from_normal((0.0, 0.0, -80.0), (0.0, 0.0, 1.0), "x")))
    with pytest.raises(ValueError, match="plano"):
        Swivel().build(_site(plane=None))


def test_requires_counts_gap_pin_and_head():
    p = Params()
    d = Swivel().requires(DEFAULT_PIN, p, "biceps").to_dict()
    assert d["profundidad_mm"] == pytest.approx(2 * p.clearance + DEFAULT_PIN.shaft_len + DEFAULT_PIN.head_h)
    assert d["pared_min_mm"] == p.min_wall and d["huella_radio_mm"] == DEFAULT_PIN.head_d / 2 and "separacion_min_mm" not in d


# ------------------------------------------------------------------------------------------------ offer
def _disc(r):
    class F:                       # una cara circular de radio r con el pasador en el centro
        def shape(self):
            return Point(0, 0).buffer(r, 64)
    return F()


def test_offer_lists_only_types_of_the_slot_and_defaults_to_ok():
    p = Params()
    got = {o.type_id: o for o in JT.offer(p, "canilla")}
    assert set(got) == {"swivel"} and got["swivel"].nivel == "ok"
    assert {o.type_id for o in JT.offer(p, "rodilla")} == {"hinge"}
    assert {"mushroom", "detent"} <= {o.type_id for o in JT.offer(p, "hombro")}
    assert JT.offer(p, "no-existe") == []


def test_offer_levels_by_face_and_depth():
    p = Params()
    need = Swivel().requires(DEFAULT_PIN, p).profundidad_mm + p.min_wall
    ok = JT.offer(p, "muslo", _disc(8.0), need + 1.0)[0]
    assert ok.nivel == "ok" and ok.margen_pared_mm == pytest.approx(8.0 - 2.5, abs=1e-2) and ok.margen_profundidad_mm == pytest.approx(1.0)
    tight_face = JT.offer(p, "muslo", _disc(2.5 + 0.5))[0]                       # sobran 0,5 mm < pared mínima
    assert tight_face.nivel == "aviso" and "poca pared" in tight_face.motivos[0]
    no_face = JT.offer(p, "muslo", _disc(2.0))[0]
    assert no_face.nivel == "no" and "no entra" in no_face.motivos[0]
    shallow = JT.offer(p, "muslo", None, need - 0.1)[0]
    assert shallow.nivel == "no" and "detrás de la cara" in shallow.motivos[0]
    edge = JT.offer(p, "muslo", None, need + 0.2)[0]
    assert edge.nivel == "aviso" and "poca profundidad" in edge.motivos[0]


def test_offer_sorts_best_first_and_serialises():
    class Weak(Swivel):
        id = "weak_t"
        def requires(self, jp, params, slot=None):
            r = super().requires(jp, params, slot)
            return dataclasses.replace(r, profundidad_mm=r.profundidad_mm + 50.0)
    JT.REGISTRY["weak_t"] = Weak()
    try:
        got = JT.offer(Params(), "canilla", None, 12.0)
        assert [o.type_id for o in got] == ["swivel", "weak_t"] and [o.nivel for o in got] == ["ok", "no"]
        assert got[1].to_dict()["tipo"] == "weak_t" and got[1].to_dict()["motivos"]
    finally:
        del JT.REGISTRY["weak_t"]
