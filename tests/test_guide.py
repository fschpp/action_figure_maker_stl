"""Guía de cortes: cobertura contra el catálogo y medición de los cortes (plan de compatibilidad, Fases 2 y 3)."""
import dataclasses
import json
import os
import shutil

import pytest
from shapely import affinity

from afm import guide as G
from afm.catalog import Catalog
from afm.cli import main
from afm.extract import load_body
from afm.ports import Port, PortFace, port_compat
from afm.project import demo_project
from afm.testfigure import make_doll

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RANK = {"ok": 0, "aviso": 1, "no": 2}


def clone(cat, ref, new_id, k=1.0, figura="otra.stl", only=None):
    """Copia de una pieza del catálogo con las caras de sus puertos escaladas por `k` (otra figura, otro tamaño)."""
    meta, mesh = cat.load(ref)
    ports = {}
    for name, p in meta.puertos.items():
        face = p.face
        kk = k if (only is None or name in only) else 1.0
        if face is not None:
            face = PortFace.from_shape(affinity.scale(face.shape(), kk, kk, origin=(0, 0)))
        ports[name] = Port(p.clase, p.rol, p.frame, face, p.sweep_deg)
    new = dataclasses.replace(meta, id=new_id, version=1, puertos=ports, origen={"figura": figura}, stl="", sha256="")
    cat.add_piece(mesh, new)
    return new


@pytest.fixture()
def cat(tmp_path):
    """Copia del catálogo de ejemplo (el muñeco) más 4 torsos «de otra figura» con caras de 1,0 / 0,9 / 0,5 / 0,3 del tamaño."""
    root = tmp_path / "linea"
    shutil.copytree(EJEMPLO, root)
    c = Catalog.open(str(root))
    for k, i in ((1.0, "a"), (0.9, "b"), (0.5, "c"), (0.3, "d")):
        clone(c, "torso-muneco-01", f"torso-otra-{i}", k)
    return c


def leg_port(cat):
    m = cat.meta("pierna-muneco-01")
    return m.puertos["cadera"], m.altura_figura_mm


# ------------------------------------------------------------------------------------------------ estadística
def test_wilson_lower_known_values():
    assert G.wilson_lower(0, 0) == 0.0
    assert G.wilson_lower(30, 30) == pytest.approx(0.886, abs=0.002)        # 30 de 30 no es «100 %»: con n chico se descuenta
    assert G.wilson_lower(300, 300) > G.wilson_lower(30, 30) > G.wilson_lower(3, 3)
    assert G.wilson_lower(0, 10) == 0.0 and 0.0 < G.wilson_lower(5, 10) < 0.5


def test_load_calibration_and_expected(tmp_path):
    assert G.load_calibration(str(tmp_path)) is None                          # sin archivo
    (tmp_path / G.CALIB_FILE).write_text("no es json")
    assert G.load_calibration(str(tmp_path)) is None                          # roto
    (tmp_path / G.CALIB_FILE).write_text(json.dumps({"formato": "otro", "clases": {}}))
    assert G.load_calibration(str(tmp_path)) is None                          # formato desconocido
    calib = {"cadera": {"ok": [29, 30], "aviso": [12, 20], "no": [0, 5]}}
    (tmp_path / G.CALIB_FILE).write_text(json.dumps({"formato": G.CALIB_FORMAT, "clases": calib}))
    assert G.load_calibration(str(tmp_path)) == calib


# ------------------------------------------------------------------------------------------------ cobertura
def test_coverage_counts_levels_and_order(cat):
    port, h = leg_port(cat)
    cov = G.coverage_of_port(port, h, cat, exclude_figure="muneco.stl")
    assert (cov.clase, cov.rol, cov.universo) == ("cadera", "enchufe", 4)
    assert (cov.ok, cov.aviso, cov.no) == (1, 1, 2)
    assert cov.cobertura == 0.5 and cov.cobertura_plena == 0.25
    assert [f.ref for f in cov.fits] == ["torso-otra-a@1", "torso-otra-b@1", "torso-otra-c@1", "torso-otra-d@1"]
    assert [f.nivel for f in cov.fits] == ["ok", "aviso", "no", "no"]          # ok, aviso, no
    assert cov.summary() == "compatible con 2 de 4 piezas (50 %) · ok pleno 1"
    assert "no entra en ninguna posición" in cov.fits[3].motivo()
    assert all(f.puerto == "cadera_izq" for f in cov.fits)                     # pieza espejable: solo el lado izquierdo


def test_coverage_excludes_own_figure_and_ids(cat):
    port, h = leg_port(cat)
    own = G.coverage_of_port(port, h, cat)                                      # sin excluir: también el torso del muñeco
    assert own.universo == 5 and "torso-muneco-01@1" in [f.ref for f in own.fits]
    assert G.coverage_of_port(port, h, cat, exclude_ids=("torso-otra-a",), exclude_figure="muneco.stl").universo == 3
    empty = G.coverage_of_port(port, h, cat, exclude_ids=[m.id for m in cat.list()])
    assert empty.universo == 0 and empty.cobertura == 0.0
    assert empty.summary().startswith("sin piezas con puerto «cadera»")


def test_coverage_matches_compatible_with(cat):
    """Mismo criterio de espejo y mismo `port_compat` que `Catalog.compatible_with`: los números coinciden en todas partes."""
    for m in cat.list():
        fig = m.origen.get("figura")
        old = {}
        for other, na, _nb, c in cat.compatible_with(m.ref):
            if other.origen.get("figura") != fig:
                old.setdefault(na, {})[other.id] = c.nivel
        for name, cov in G.piece_coverage(cat, m.ref).items():
            new = {f.ref.split("@")[0]: f.nivel for f in cov.fits if f.nivel != "no"}
            assert new == old.get(name, {}), (m.ref, name)


def test_non_mirrorable_port_takes_the_worst_side(cat):
    clone(cat, "torso-muneco-01", "torso-asim", 0.3, only=("cadera_der",))      # solo el lado derecho tiene la cara chica
    port, h = leg_port(cat)
    izq = {f.ref: f for f in G.coverage_of_port(port, h, cat, exclude_figure="muneco.stl", mirrorable=True).fits}
    ambos = {f.ref: f for f in G.coverage_of_port(port, h, cat, exclude_figure="muneco.stl", mirrorable=False).fits}
    assert izq["torso-asim@1"].nivel == "ok" and izq["torso-asim@1"].puerto == "cadera_izq"
    assert ambos["torso-asim@1"].nivel == "no" and ambos["torso-asim@1"].puerto == "cadera_der"


def test_piece_coverage_hides_der_without_universe(cat):
    clone(cat, "brazo-muneco-01", "brazo-otro", 1.0)
    cov = G.piece_coverage(cat, "torso-muneco-01")
    assert "hombro_izq" in cov and cov["hombro_izq"].universo == 1              # el brazo espejable de otra figura
    assert "hombro_der" not in cov and "cadera_der" not in cov                  # nada contra qué medirlos: ya se midió el _izq
    assert cov["cuello"].universo == 0                                          # no hay otras cabezas: queda, sin piezas


def test_calibrated_expected_in_summary(cat):
    port, h = leg_port(cat)
    calib = {"cadera": {"ok": [29, 30], "aviso": [12, 20], "no": [0, 5]}}
    cov = G.coverage_of_port(port, h, cat, exclude_figure="muneco.stl", calib=calib)
    manual = (G.wilson_lower(29, 30) + G.wilson_lower(12, 20) + 0 + 0) / 4
    assert cov.esperada == pytest.approx(manual) and cov.n_calibracion == 55
    assert "esperada ≈ " in cov.summary() and "(n = 55)" in cov.summary()
    falta = {"cadera": {"ok": [29, 30]}}                                        # falta el dato de «aviso»: no se inventa
    cov = G.coverage_of_port(port, h, cat, exclude_figure="muneco.stl", calib=falta)
    assert cov.esperada is None and "esperada" not in cov.summary()
    # la calibración se lee sola del catálogo
    with open(os.path.join(cat.root, G.CALIB_FILE), "w") as fh:
        json.dump({"formato": G.CALIB_FORMAT, "clases": calib}, fh)
    assert G.piece_coverage(cat, "pierna-muneco-01")["cadera"].universo == 4
    clone(cat, "pierna-muneco-01", "pierna-otra", 1.0)
    assert G.piece_coverage(cat, "pierna-otra")["cadera"].esperada is not None


# ------------------------------------------------------------------------------------------------ medición de cortes
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


def test_measure_cuts_on_the_doll(doll):
    _stl, _pp, proj, body = doll
    cuts = {m.lugar: m for m in G.measure_cuts(proj, body)}
    assert set(cuts) == {"cabeza", "brazo", "pierna"} and all(m.error is None for m in cuts.values())
    # mismos números que `docs/MEDIDAS_interfaces.md` (radio equivalente, x del puerto, margen del pasador)
    assert cuts["brazo"].clase == "hombro" and cuts["brazo"].r_eq == pytest.approx(7.07, abs=0.05)
    assert cuts["brazo"].x_mm == pytest.approx(15.66, abs=0.05) and cuts["brazo"].margen_pasador == pytest.approx(0.57, abs=0.05)
    assert cuts["pierna"].clase == "cadera" and cuts["pierna"].r_eq == pytest.approx(6.96, abs=0.05)
    assert cuts["cabeza"].clase == "cuello" and cuts["cabeza"].r_eq == pytest.approx(6.26, abs=0.05)
    # la cara es más angosta que el círculo de igual área: el círculo inscrito (el calibre) es el que manda
    assert 0 < cuts["brazo"].r_inscrito < cuts["brazo"].r_eq
    assert any("pared" in a for a in cuts["brazo"].avisos)                       # 0,57 mm < 1,2 mm
    assert cuts["brazo"].as_plug is None and cuts["brazo"].as_socket is None      # sin catálogo no hay cobertura


def test_measure_cuts_against_catalog(doll, cat):
    _stl, _pp, proj, body = doll
    own = {m.lugar: m for m in G.measure_cuts(proj, body, cat)}                    # sin excluir: encaja con el torso del muñeco
    assert own["brazo"].as_plug.universo == 5 and own["cabeza"].as_socket.universo == 1
    assert own["cabeza"].as_socket.fits[0].nivel == "ok"                           # la cabeza del muñeco en su propio cuello
    other = {m.lugar: m for m in G.measure_cuts(proj, body, cat, exclude_figure="muneco.stl")}
    assert other["pierna"].as_plug.universo == 4 and (other["pierna"].as_plug.ok, other["pierna"].as_plug.no) == (1, 2)
    assert other["pierna"].as_socket.universo == 0                                 # nadie más trae piernas
    # el puerto medido es el mismo que el que guarda `afm catalogo agregar` (misma cara, mismo centro)
    meta = cat.meta("pierna-muneco-01")
    assert other["pierna"].port.face.area == pytest.approx(meta.puertos["cadera"].face.area, rel=1e-3)


def test_format_measure_and_errors():
    m = G.CutMeasure("brazo", "hombro", error="no hay cara de contacto")
    assert G.format_measure(m) == ["brazo (hombro): no se pudo medir — no hay cara de contacto"]
    small = G.CutMeasure("brazo", "hombro", r_eq=3.4, r_inscrito=3.0, x_mm=9.0, margen_pasador=-0.3, cara_chica=(6.0, 6.5),
                         avisos=["el vástago sobresale"])
    text = "\n".join(G.format_measure(small))
    assert "radio eq 3,40 mm" in text and "inscrito 3,00 mm" in text and "no entra en ninguna posición" in text


# ------------------------------------------------------------------------------------------------ CLI
def test_cli_cobertura(cat, capsys):
    assert main(["catalogo", "cobertura", cat.root, "pierna-muneco-01"]) == 0
    out = capsys.readouterr().out
    assert "cadera (enchufe): sin piezas" not in out                             # hay torsos de otra figura... pero el muñeco se excluye a sí mismo
    assert "cadera (enchufe): compatible con 2 de 4 piezas (50 %) · ok pleno 1" in out
    assert "torso-otra-c@1" in out and "torso-otra-a@1" not in out               # solo lo que no es ok, salvo --todas
    assert main(["catalogo", "cobertura", cat.root, "pierna-muneco-01", "--todas"]) == 0
    assert "torso-otra-a@1" in capsys.readouterr().out
    assert main(["catalogo", "cobertura", cat.root, "no-existe"]) == 2


def test_cli_guia(doll, cat, capsys):
    stl, proj, _p, _b = doll
    assert main(["guia", stl, proj, "--catalogo", cat.root, "--excluir-figura", "muneco.stl"]) == 0
    out = capsys.readouterr().out
    assert "brazo (hombro): radio eq 7,07 mm" in out and "pierna (cadera):" in out and "cabeza (cuello):" in out
    assert "como enchufe (la pieza de esta figura en otras):" in out and "compatible con 2 de 4 piezas (50 %)" in out
    assert main(["guia", stl, proj]) == 0                                          # sin catálogo (y sin $AFM_CATALOGO): solo mide
    out = capsys.readouterr().out
    assert "sin catálogo" in out and "como enchufe" not in out
    assert main(["guia", stl, proj, "--catalogo", cat.root + "-no-existe"]) == 2


def test_cli_agregar_prints_coverage(doll, cat, capsys):
    stl, proj, _p, _b = doll
    assert main(["catalogo", "agregar", cat.root, stl, proj, "--prefijo", "nuevo", "--lugares", "brazo,pierna"]) == 0
    out = capsys.readouterr().out
    assert "pierna-nuevo-01@1" in out and "cadera (enchufe): compatible con" in out


# ------------------------------------------------------------------------------------------------ interfaz (sin GTK)
def test_controller_cut_guide(tmp_path, cat):
    from afm.gui.controller import Controller
    from test_gui_phases35 import fill_limbs, make_ctrl

    empty = Controller()
    assert empty.cut_guide() is None and "abrí un STL" in empty.message           # sin figura abierta
    c = make_ctrl(tmp_path)
    assert c.cut_guide_lines() == ["El proyecto no define miembros ni cuello: no hay cortes que medir."]
    fill_limbs(c)
    lines = c.cut_guide_lines()
    assert lines[0].startswith("Sin catálogo abierto") and any(x.startswith("brazo (hombro)") for x in lines)
    assert not any("como enchufe" in x for x in lines)
    c.session.open_catalog(cat.root)                                                # el catálogo de ejemplo + 4 torsos de otra figura
    text = "\n".join(c.cut_guide_lines())
    assert not text.startswith("Sin catálogo") and "pierna (cadera):" in text
    assert "como enchufe (la pieza de esta figura en otras):" in text and "compatible con 2 de 4 piezas (50 %)" in text
