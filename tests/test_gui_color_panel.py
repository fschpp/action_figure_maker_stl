"""T25 del plan de color: lógica del panel «Color» (Controller + Session), sin GTK.

El bloque de widgets (`steps/parametros.py`, `window._refresh_color`) solo muestra lo que estas pruebas ejercitan.
"""
import json
import os
import shutil
from types import SimpleNamespace

import pytest

from afm import pipeline as P
from afm.gui.controller import Controller
from afm.gui.session import ColorSourceError, Session, color_rows
from afm.project import ColorSpec, Project, demo_project
from afm.testfigure import make_doll
from color_helpers import write_doll_fbx, write_sphere_fbx


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("gui_color")
    stl = str(d / "muneco.stl")
    make_doll().export(stl)
    return {"dir": d, "stl": stl, "fbx": write_doll_fbx(d / "muneco.fbx"), "otra": write_sphere_fbx(d / "esfera.fbx")}


def _ctrl_with_stl(files) -> Controller:
    c = Controller()
    c.open_stl(files["stl"])
    assert c.loaded
    return c


def _fake_outcome(s: Session) -> None:
    s.outcome = SimpleNamespace(result=SimpleNamespace(report={}), validation={"ok": True})


# ------------------------------------------------------------------------------------------ proyecto
def test_controller_color_spec_roundtrip(files, tmp_path):
    c = _ctrl_with_stl(files)
    assert c.color_spec() is None and "Sin color" in c.color_summary()
    assert c.set_color(files["fbx"], texel_mm=0.4, eps=0.2, fill="#C8C8C8", symmetric=True)
    spec = c.color_spec()
    assert spec.texel_mm == 0.4 and spec.eps == 0.2 and spec.fill == "#C8C8C8" and spec.symmetric and len(spec.source_sha256) == 64
    assert "muneco.fbx" in c.color_summary() and "simétrico" in c.color_summary()
    path = str(tmp_path / "muneco.proyecto")
    c.save_project(path)
    assert Project.load(path).color == spec                                  # ida y vuelta por el .proyecto
    assert json.load(open(path))["color"]["fill"] == "#C8C8C8"


def test_update_color_validates(files):
    c = _ctrl_with_stl(files)
    assert not c.update_color(eps=0.1) and "FBX" in c.message               # sin color activo no hay qué cambiar
    c.set_color(files["fbx"])
    before = c.color_spec().to_dict()
    assert not c.update_color(texel_mm=5.0) and not c.update_color(eps=0.0)
    assert not c.update_color(fill="rojo") and not c.update_color(fill="#12345") and not c.update_color(velocidad=1)
    assert c.color_spec().to_dict() == before                                # lo inválido no cambia nada
    assert c.update_color(fill="#aabbcc", texel_mm=0.25) and c.color_spec().fill == "#aabbcc"
    assert c.update_color(fill="extend") and c.color_spec().fill == "extend"


def test_set_color_rejects_missing_file(files, tmp_path):
    c = _ctrl_with_stl(files)
    assert not c.set_color(str(tmp_path / "no_existe.fbx")) and c.color_spec() is None
    assert not c.set_color() and c.color_spec() is None                      # sin ruta y sin FBX abierto (modo B)


def test_color_off_when_removed(files):
    c = _ctrl_with_stl(files)
    assert not c.remove_color()                                              # nada que quitar
    c.set_color(files["fbx"])
    c.color_report = {"piezas": {"torso": {"pct_origen": 90.0, "estado": "identidad"}}}
    assert c.remove_color() and c.color_spec() is None and c.color_report_rows() == []
    assert "sin color" in c.message and os.path.exists(files["fbx"])         # el FBX no se borra
    assert "color" not in c.session.project.to_dict()


def test_mode_a_opens_with_color_active(files):
    c = Controller()
    c.open_stl(files["fbx"])
    spec = c.color_spec()
    assert spec is not None and os.path.samefile(spec.source_path, files["fbx"]) and spec.source_sha256
    assert c.set_color()                                                     # sin ruta: el FBX abierto


# ---------------------------------------------------------------------------------------- relocalizar
def test_relocate_checks_sha256(files, tmp_path):
    c = _ctrl_with_stl(files)
    c.set_color(files["fbx"])
    copia = str(tmp_path / "otra_carpeta" / "renombrado.fbx")
    os.makedirs(os.path.dirname(copia))
    shutil.copy(files["fbx"], copia)
    assert c.relocate_color(copia) and c.color_spec().source_path == os.path.abspath(copia)    # mismo contenido: se acepta
    before = c.color_spec().source_path
    assert not c.relocate_color(files["otra"]) and "no es el mismo" in c.message               # otro FBX: se rechaza
    assert c.color_spec().source_path == before


def test_missing_fbx_blocks_export_before_starting(files, tmp_path):
    c = _ctrl_with_stl(files)
    c.set_color(files["fbx"])
    gone = str(tmp_path / "se_fue.fbx")
    shutil.copy(files["fbx"], gone)
    c.relocate_color(gone)
    os.remove(gone)
    _fake_outcome(c.session)
    assert "se_fue.fbx" in c.color_source_problem()
    assert c.begin_export(str(tmp_path / "out")) is None and not c.busy and "se_fue.fbx" in c.message
    with pytest.raises(ColorSourceError):
        c.session.resolve_color_source()


def test_resolve_finds_relative_path_next_to_project(files, tmp_path):
    s = Session()
    s.load(files["stl"])
    shutil.copy(files["fbx"], tmp_path / "rel.fbx")
    s.project.color = ColorSpec(source_path="rel.fbx", source_sha256=ColorSpec.from_file(files["fbx"]).source_sha256)
    s.project_path = str(tmp_path / "x.proyecto")
    assert os.path.samefile(s.resolve_color_source(), tmp_path / "rel.fbx")
    s.project.color.source_sha256 = "0" * 64                                  # mismo nombre, otro contenido
    with pytest.raises(ColorSourceError, match="hash"):
        s.resolve_color_source()


# -------------------------------------------------------------------------------------------- informe
def test_controller_report_rows(files):
    c = _ctrl_with_stl(files)
    info = {"archivos": {"piezas": ["torso", "brazo_izq"], "color": {"piezas": {
        "torso": {"pct_origen": 72.7, "estado": "identidad"}, "brazo_izq": {"pct_origen": 84.4, "estado": "semilla"}}}},
        "color": {"omitido": None}}
    c.finish_export(info, "/tmp/x")
    assert c.color_report_rows() == [("brazo_izq", 84.4, "semilla"), ("torso", 72.7, "identidad")]    # ordenadas por pieza
    assert "Color: 2 piezas" in c.message
    c.finish_export({"archivos": {"piezas": ["torso"]}, "color": {"omitido": "otra_figura"}}, "/tmp/x")
    assert c.color_report_rows() == [] and "otra_figura" in c.message
    assert color_rows(None) == [] and color_rows({}) == []


# ---------------------------------------------------------------------------------- Session.export
def test_session_export_passes_colorize_only_with_color(files, tmp_path, monkeypatch):
    seen = []

    def fake_export(res, outdir, name, **kw):
        seen.append(kw)
        return {"piezas": []}

    monkeypatch.setattr(P, "export", fake_export)
    s = Session()
    s.load(files["stl"])
    _fake_outcome(s)
    s.export(str(tmp_path / "a"), "f")
    assert "colorize" not in seen[0]                                          # sin color: la llamada queda como siempre
    s.project.color = ColorSpec.from_file(files["fbx"])
    info = s.export(str(tmp_path / "b"), "f")
    assert seen[1]["colorize"] is not None and info["color"]["fuente"].endswith("muneco.fbx")
    assert info["color"]["alineacion"]["ok"]
    s.project.color = None
    s.export(str(tmp_path / "c"), "f")
    assert "colorize" not in seen[2]


def test_session_export_with_other_figure_skips_color(files, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "export", lambda res, outdir, name, **kw: {"piezas": [], "_kw": sorted(kw)})
    s = Session()
    s.load(files["stl"])
    _fake_outcome(s)
    s.project.color = ColorSpec.from_file(files["otra"])
    info = s.export(str(tmp_path / "d"), "f")
    assert info["color"]["omitido"] == "otra_figura" and "colorize" not in info["archivos"]["_kw"]


@pytest.fixture(scope="module")
def built(files):
    s = Session()
    s.load(files["stl"])
    s.auto_symmetry()
    for l in demo_project().limbs:
        s.add_limb(l.name, l.kind, l.plane, l.thickness, auto=False)
        s.set_limb_polygon(l.name, l.polygon)
    out = s.build()
    assert out.validation["ok"]
    return s


def test_real_export_with_color_writes_report(files, built, tmp_path):
    built.project.color = ColorSpec.from_file(files["fbx"], texel_mm=0.4)
    try:
        c = Controller(built)
        info = built.export(str(tmp_path), "muneco")
        c.finish_export(info, str(tmp_path))
        rows = dict((n, (p, e)) for n, p, e in c.color_report_rows())
        assert {"torso", "brazo_izq", "pierna_izq"} <= set(rows) and rows["torso"][0] > 50
        assert os.path.exists(tmp_path / "muneco_ensamblada.glb") and (tmp_path / "piezas_color").is_dir()
        saved = json.load(open(tmp_path / "muneco.proyecto"))
        assert saved["color"]["texel_mm"] == 0.4 and saved["color"]["source_sha256"] == built.project.color.source_sha256
        assert "color" in json.load(open(tmp_path / "muneco_informe.json"))
    finally:
        built.project.color = None


def test_real_export_without_color_has_no_color_outputs(built, tmp_path):
    assert built.project.color is None
    info = built.export(str(tmp_path), "muneco")
    assert "color" not in info["archivos"] and "color" not in info and not (tmp_path / "piezas_color").exists()
