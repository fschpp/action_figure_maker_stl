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
        assert {o.type_id for o in m.tipos} == {"mushroom", "detent"}           # los de ese lugar, y no swivel ni bisagra
        assert m.profundidad_mm is not None and all(o.margen_profundidad_mm is not None for o in m.tipos)
    assert cuts["cabeza"].tipos == []                                          # el cuello es hongo fijo: no hay nada que elegir


def test_the_face_rule_for_mushroom_and_detent_is_the_shaft_not_the_head(cuts):
    """La cabeza queda escondida en el torso: lo que tiene que caber en la cara es el vástago, igual que el margen del pasador de la guía."""
    m = cuts["brazo"]
    pin = Params().shoulder_pin
    assert m.margen_pasador == pytest.approx(0.57, abs=0.05)
    for o in m.tipos:
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
    for o in m.tipos:
        assert o.margen_profundidad_mm == pytest.approx(m.profundidad_mm - need.profundidad_mm - need.pared_min_mm, abs=1e-3)


def test_swivel_keeps_the_head_rule_inside_a_member_cut():
    """En un corte que parte el miembro la cabeza queda en la misma sección: ahí sí cuenta su radio (comportamiento del corte 1)."""
    from afm.joint_types.swivel import DEFAULT_PIN, Swivel
    from shapely.geometry import Point
    from afm.ports import PortFace

    p = Params()
    face = PortFace.from_shape(Point(0, 0).buffer(2.0, 64))
    o = offer_one(Swivel(), p, "muslo", face)
    assert o.nivel == "no" and "la cabeza" in o.motivos[0] and DEFAULT_PIN.head_d / 2 > 2.0


def test_a_type_without_face_radius_falls_back_to_the_head():
    class Plain:
        id = "plain_t"
        labels = {"es": "x", "en": "x", "pt": "x"}
        slots = frozenset({"hombro"})
        params_cls = Params

        def default_params(self, params, slot):
            return params.shoulder_pin

        def requires(self, jp, params, slot=None):
            return Mushroom().requires(jp, params, slot)

    from shapely.geometry import Point
    from afm.ports import PortFace

    p = Params()
    face = PortFace.from_shape(Point(0, 0).buffer(p.shoulder_pin.head_d / 2 - 0.2, 64))
    assert offer_one(Plain(), p, "hombro", face).nivel == "no"


def test_format_measure_prints_the_offer_and_nothing_when_empty(cuts):
    text = "\n".join(G.format_measure(cuts["pierna"]))
    assert "tipos de articulación que admite este corte:" in text
    assert "mushroom" in text and "Hongo" in text and "detent" in text and "Clic" in text
    assert "cuerpo detrás de la cara:" in text and "profundidad" in text
    assert "tipos de articulación" not in "\n".join(G.format_measure(cuts["cabeza"]))
    bare = G.CutMeasure("brazo", "hombro", r_eq=3.4, r_inscrito=3.0, x_mm=9.0)            # sin oferta: el texto de antes
    assert G.format_types(bare) == [] and "tipos de articulación" not in "\n".join(G.format_measure(bare))


def test_an_installed_type_shows_up_without_touching_anything_else(doll):
    """Un tipo registrado para ese lugar aparece solo en la guía (plan §3.5, punto 3)."""
    class Extra(Mushroom):
        id = "extra_t"
        labels = {"es": "Extra", "en": "Extra", "pt": "Extra"}

    JT.REGISTRY["extra_t"] = Extra()
    try:
        _stl, _pp, proj, body = doll
        brazo = {m.lugar: m for m in G.measure_cuts(proj, body)}["brazo"]
        assert "extra_t" in {o.type_id for o in brazo.tipos}
        assert "Extra" in "\n".join(G.format_measure(brazo))
    finally:
        del JT.REGISTRY["extra_t"]


def test_the_cut_without_a_measurable_body_keeps_the_face_offer(doll, monkeypatch):
    """Si no se puede medir la profundidad, la oferta sigue con la cara y no se rompe la guía."""
    _stl, _pp, proj, body = doll
    monkeypatch.setattr("afm.capacity.measure", lambda *a, **k: None)
    brazo = {m.lugar: m for m in G.measure_cuts(proj, body)}["brazo"]
    assert brazo.profundidad_mm is None and brazo.tipos and all(o.margen_profundidad_mm is None for o in brazo.tipos)
    assert "cuerpo detrás de la cara" not in "\n".join(G.format_measure(brazo))


def test_cli_guia_prints_the_offer(doll, capsys):
    stl, pp, _proj, _body = doll
    assert main(["guia", stl, pp]) == 0
    out = capsys.readouterr().out
    assert "brazo (hombro):" in out and "tipos de articulación que admite este corte:" in out


def test_controller_guide_text_includes_the_offer(tmp_path):
    from test_gui_phases35 import fill_limbs, make_ctrl

    c = make_ctrl(tmp_path)
    fill_limbs(c)
    assert "tipos de articulación que admite este corte:" in "\n".join(c.cut_guide_lines())


# ------------------------------------------------------------------------------------------------ `afm catalogo explicar`
EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture()
def cat_dir(tmp_path):
    import shutil
    dst = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, dst, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    return dst


def test_explain_lists_the_types_each_limb_or_socket_port_admits(cat_dir):
    from afm import catalog_ops as OPS
    from afm.catalog import Catalog

    cat = Catalog.open(cat_dir)
    lines = OPS.explain_lines(cat, "brazo-muneco-01")
    text = "\n".join(lines)
    assert lines[0].startswith("brazo-muneco-01@1")
    assert "tipos de articulación que admite este corte:" in text and "mushroom" in text and "detent" in text
    torso = "\n".join(OPS.explain_lines(cat, "torso-muneco-01"))
    assert "hombro_izq (hombro/toma)" in torso and "cuerpo detrás de la cara:" not in torso      # el ejemplo no trae la capacidad medida
    cat.measure_capacity("torso-muneco-01", write=True)
    torso = "\n".join(OPS.explain_lines(Catalog.open(cat_dir), "torso-muneco-01"))
    assert "cuerpo detrás de la cara:" in torso and "profundidad" in torso                         # con la capacidad guardada se usa
    cabeza = "\n".join(OPS.explain_lines(cat, "cabeza-muneco-01"))
    assert "tipos de articulación" not in cabeza                                   # el cuello es hongo fijo: nada que elegir


def test_cli_catalogo_explicar_prints_the_offer(cat_dir, capsys):
    assert main(["catalogo", "explicar", cat_dir, "brazo-muneco-01@1"]) == 0
    out = capsys.readouterr().out
    assert "tipos de articulación que admite este corte:" in out and "IoU" in out
