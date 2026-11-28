"""Oferta de tipos de articulación en la guía de cortes (plan de plugins §3.5, Fase 2 corte 2): `CutMeasure.tipos`, el texto de
`afm guia` y de la ventana «Guía de cortes» (mismo texto), y la regla de cara de cada tipo."""
import dataclasses
import os

import pytest

from afm import guide as G
from afm import joint_types as JT
from afm.cli import main
from afm.extract import load_body
from afm.joint_types.mushroom import Mushroom
from afm.joint_types.offer import offer_one
from afm.params import Params
from afm.project import demo_project
from afm.testfigure import make_doll


@pytest.fixture(scope="module")
def doll(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("doll")
    stl = str(tmp / "muneco.stl")
    make_doll().export(stl)
    proj = demo_project()
    proj_path = str(tmp / "m.proyecto")
    proj.save(proj_path)
    body, _rep = load_body(stl, proj)
    return stl, proj_path, proj, body


@pytest.fixture(scope="module")
def cuts(doll):
    _stl, _pp, proj, body = doll
    return {m.lugar: m for m in G.measure_cuts(proj, body)}


def test_each_limb_cut_lists_the_types_of_its_slot_with_the_depth_measured(cuts):
    for lugar in ("brazo", "pierna"):
        m = cuts[lugar]
        assert {o.type_id for o in m.tipos} == {"mushroom", "detent", "ball"}   # los de ese lugar, y no swivel ni bisagra
        assert m.profundidad_mm is not None and all(o.margen_profundidad_mm is not None for o in m.tipos)
    assert cuts["cabeza"].tipos == []                                          # el cuello es hongo fijo: no hay nada que elegir


def test_the_face_rule_for_mushroom_and_detent_is_the_shaft_not_the_head(cuts):
    """La cabeza queda escondida en el torso: lo que tiene que caber en la cara es el vástago, igual que el margen del pasador de la guía."""
    m = cuts["brazo"]
    pin = Params().shoulder_pin
    assert m.margen_pasador == pytest.approx(0.57, abs=0.05)
    for o in (o for o in m.tipos if o.type_id in ("mushroom", "detent")):
        assert o.margen_pared_mm == pytest.approx(m.margen_pasador, abs=0.05)
        assert o.nivel == "aviso" and any("vástago" in x for x in o.motivos)    # 0,57 mm < pared mínima, como el AVISO de la guía
    assert pin.head_d / 2 > m.r_inscrito > pin.shaft_d / 2                     # la cabeza no entraría en la cara; el hongo se arma igual


def test_the_depth_matches_the_capacity_of_the_socket(doll, cuts):
    """Misma cuenta que la capacidad que guarda el catálogo para una toma (`capacity.measure`)."""
    from afm import capacity as C
    from afm import solids as S

    _stl, _pp, proj, body = doll
    half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    m = cuts["pierna"]
    cap = C.measure(S.to_trimesh(half), m.socket_port(), proj.params)
    assert cap is not None and m.profundidad_mm == pytest.approx(cap["profundidad_mm"], abs=1e-6)
    need = Mushroom().requires(proj.params.hip_pin, proj.params, "cadera")
    for o in (o for o in m.tipos if o.type_id in ("mushroom", "detent")):
        assert o.margen_profundidad_mm == pytest.approx(m.profundidad_mm - need.profundidad_mm - need.pared_min_mm, abs=1e-3)
    ball = next(o for o in m.tipos if o.type_id == "ball")                      # la rótula pide lo suyo: cuello + bola + holgura
    bneed = JT.get("ball").requires(JT.get("ball").default_params(proj.params, "cadera"), proj.params, "cadera")
    assert ball.margen_profundidad_mm == pytest.approx(m.profundidad_mm - bneed.profundidad_mm - bneed.pared_min_mm, abs=1e-3)


def test_ball_rule_on_the_face_is_the_cone_of_the_neck_and_the_doll_cuts_are_too_small_for_it(doll, cuts):
    """La rótula con sus medidas por defecto (bola Ø7, cono de 20°) no entra en el muñeco: el cono del cuello no cabe en la cara con pared
    (brazo y pierna) y la pierna tampoco tiene profundidad para la bola. La oferta lo dice con el motivo."""
    _stl, _pp, proj, _body = doll
    for lugar in ("brazo", "pierna"):
        o = next(o for o in cuts[lugar].tipos if o.type_id == "ball")
        assert o.nivel == "no" and any("el cono del cuello" in x for x in o.motivos)
        assert o.margen_pared_mm < proj.params.min_wall
    pierna = next(o for o in cuts["pierna"].tipos if o.type_id == "ball")
    assert pierna.margen_profundidad_mm < 0 and any("detrás de la cara" in x for x in pierna.motivos)
    assert cuts["brazo"].tipos[-1].type_id == "ball"                              # «no» va al final de la oferta
