"""Plugins de articulación, Fase 1 tarea 3: tipo y huella en los puertos, rechazo por tipo, capacidad con el tipo medido."""
import numpy as np

from afm import capacity as C
from afm import ports as PO
from afm.joint_types import resolve as R
from afm.params import Params


def _face(r=6.0):
    t = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    ring = np.stack([r * np.cos(t), r * np.sin(t)], 1).tolist()
    return PO.PortFace([{"exterior": ring, "interiors": []}], float(np.pi * r * r), r)


def _pair(ta=None, tb=None, ha=None, hb=None):
    fa = PO.PortFrame([0, 0, 0], [0, 0, 1], [1, 0, 0])
    fb = PO.PortFrame([0, 0, 0], [0, 0, -1], [1, 0, 0])
    return (PO.Port("hombro", "enchufe", fa, _face(), joint_type=ta, joint_hash=ha),
            PO.Port("hombro", "toma", fb, _face(), joint_type=tb, joint_hash=hb))


def test_old_port_dict_is_unchanged_and_roundtrip():
    a, _ = _pair()
    d = a.to_dict()
    assert "tipo_articulacion" not in d and "hash_articulacion" not in d
    a2, _ = _pair("detent", None, "abcd1234")
    d2 = a2.to_dict()
    assert d2["tipo_articulacion"] == "detent" and d2["hash_articulacion"] == "abcd1234"
    b = PO.Port.from_dict(d2)
    assert (b.joint_type, b.joint_hash) == ("detent", "abcd1234")
    assert PO.Port.from_dict(d).joint_type is None
    assert b.transformed(np.eye(4)).joint_type == "detent"


def test_compat_rejects_different_types_with_reason():
    a, b = _pair("detent", "mushroom")
    c = PO.port_compat(a, b)
    assert c.nivel == "no" and any("«detent» contra «mushroom»" in m for m in c.motivos)


def test_compat_same_type_ok_and_unknown_not_judged():
    a, b = _pair("mushroom", "mushroom")
    assert not any("tipo" in m for m in PO.port_compat(a, b).motivos)
    a, b = _pair("detent", None)
    assert not any("tipo de articulación" in m for m in PO.port_compat(a, b).motivos)


def test_compat_warns_on_different_hash_and_on_line_type():
    a, b = _pair("mushroom", "mushroom", "aaaaaaaa", "bbbbbbbb")
    assert any("medidas de acople distintas" in x for x in PO.port_compat(a, b).avisos)
    a, b = _pair("detent", "detent")
    assert any("la línea fija «mushroom»" in x for x in PO.port_compat(a, b).avisos)


def test_params_hash_and_signature():
    p = Params()
    t, h = R.class_signature("hombro", p)
    assert t == "mushroom" and len(h) == 8
    assert R.class_signature("muneca", p) is None
    p2 = Params(joints={"hombro": {"type": "mushroom", "params": {"shaft_d": 3.3}}})
    assert R.class_signature("hombro", p2)[1] != h


def test_capacity_stale_when_line_type_changes():
    cap = {"profundidad_mm": 30.0, "huella_radio_mm": 2.0, "tipo": "mushroom"}
    p = Params()
    assert not C.stale(cap, "hombro", p)
    assert C.stale(cap, "hombro", Params(shoulder_detent=True))
    assert not C.stale({"profundidad_mm": 30.0}, "hombro", Params(shoulder_detent=True))   # medida antes de los plugins
    fa = PO.PortFrame([0, 0, 0], [0, 0, 1], [1, 0, 0])
    sock = PO.Port("hombro", "toma", PO.PortFrame([0, 0, 0], [0, 0, -1], [1, 0, 0]), _face(), capacity=cap)
    plug = PO.Port("hombro", "enchufe", fa, _face())
    assert C.missing(plug, sock, Params(shoulder_detent=True))
    assert not C.missing(plug, sock, p)
