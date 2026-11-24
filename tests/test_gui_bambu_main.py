"""Salida para Bambu Studio en la interfaz principal (sin catálogo): `BambuChoice`, `Session.export(bambu=)`, el estado y la vista previa del
`Controller`. Con el muñeco de `test_gui_color_panel` (figura real con color por textura: las filas del selector llevan solo el HEX).

Los widgets de la ventana se ejercitan aparte (`test_gui_window_bambu.py`, bajo Xvfb); acá no hay GTK.
"""
import os
import zipfile

import numpy as np
import pytest

from afm import pipeline as P  # noqa: F401  (se importa para que falle claro si el motor no carga)
from afm.gui.bambu_choice import BambuChoice
from afm.gui.controller import Controller
from afm.gui.session import Session
from afm.project import ColorSpec, demo_project
from afm.testfigure import make_doll
from color_helpers import write_doll_fbx
from test_gui_phases35 import FakeScene

NAME = "muneco"


def materials(out) -> list:
    """Colores (#RRGGBB) de los materiales del 3MF escrito, en orden."""
    with zipfile.ZipFile(os.path.join(out, f"{NAME}_bambu.3mf")) as z:
        xml = z.read("3D/3dmodel.model").decode()
    return [c[:7] for c in xml.split('displaycolor="')[1:]]


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("bambu_main")
    stl = str(d / "muneco.stl")
    make_doll().export(stl)
    return {"dir": d, "stl": stl, "fbx": write_doll_fbx(d / "muneco.fbx")}


def _built(files) -> Session:
    s = Session()
    s.load(files["stl"])
    s.auto_symmetry()
    for lim in demo_project().limbs:
        s.add_limb(lim.name, lim.kind, lim.plane, lim.thickness, auto=False)
        s.set_limb_polygon(lim.name, lim.polygon)
    assert s.build().validation["ok"]
    return s


@pytest.fixture(scope="module")
def exported(files, tmp_path_factory):
    """Una figura generada y exportada UNA vez con color y con la salida de Bambu pedida (se comparte: las pruebas no la modifican)."""
    s = _built(files)
    s.project.color = ColorSpec.from_file(files["fbx"], texel_mm=0.4)
    out = str(tmp_path_factory.mktemp("salida") / "fig")
    info = s.export(out, NAME, bambu={"max_colors": 4, "min_area_pct": 0.5})
    return s, out, info


@pytest.fixture
def choice(exported):
    """Un `BambuChoice` nuevo sobre el `Colorizer` de ese export (cada prueba parte del modo automático)."""
    s, _out, _info = exported
    return BambuChoice(lambda: s.colorizer)


# ------------------------------------------------------------------------------------------ antes de exportar
def test_nothing_to_choose_without_an_export():
    c = BambuChoice(lambda: None)
    assert c.has_colors() is False and c.candidates() == [] and c.effective_selection() == []
    assert c.options() is None and "chosen" not in c.options(force=True)           # la casilla apagada no pide nada; la fuerza, sin selección
    for call in (c.preview, c.export):
        with pytest.raises(ValueError):
            call()
    with pytest.raises(ValueError):
        c.set_enabled("#C81E1E", True)
    with pytest.raises(ValueError):
        c.set_output_color("#C81E1E", "#000000")


def test_colorizer_without_pieces_counts_as_nothing():
    class Empty:
        pieces = {}

    assert BambuChoice(lambda: Empty()).colorizer() is None


# ------------------------------------------------------------------------------------------ Session.export(bambu=)
def test_export_with_bambu_writes_the_3mf_and_keeps_the_colorizer(exported):
    s, out, info = exported
    bam = info["archivos"]["color"]["bambu"]
    assert bam["3mf"] == f"{NAME}_bambu.3mf" and bam["obj"] == f"{NAME}_bambu.obj" and len(bam["colores"]) == 4     # el tope pedido
    assert os.path.isfile(os.path.join(out, bam["3mf"])) and os.path.isfile(os.path.join(out, bam["mtl"]))
    assert s.colorizer is not None and len(s.colorizer.pieces) >= 3                                                  # piezas ya horneadas
    assert "bambu_omitido" not in info


def test_export_without_bambu_writes_no_3mf(files, tmp_path):
    s = _built(files)
    s.project.color = ColorSpec.from_file(files["fbx"], texel_mm=0.4)
    info = s.export(str(tmp_path), NAME)
    assert "bambu" not in info["archivos"]["color"] and not (tmp_path / f"{NAME}_bambu.3mf").exists()
    assert s.colorizer is not None                                          # el selector igual puede usar este export (con la casilla apagada)


def test_export_asking_bambu_without_color_says_why(files, tmp_path):
    s = _built(files)
    info = s.export(str(tmp_path), NAME, bambu=True)
    assert info["bambu_omitido"] == "sin_color" and not (tmp_path / f"{NAME}_bambu.3mf").exists()
    assert s.colorizer is None


def test_colorizer_is_dropped_when_the_figure_changes(files, tmp_path):
    s = _built(files)
    s.project.color = ColorSpec.from_file(files["fbx"], texel_mm=0.4)
    s.export(str(tmp_path), NAME, bambu=True)
    assert s.colorizer is not None
    s.outcome = None                                                        # cualquier cambio que invalida la figura generada
    assert s.colorizer is None


def test_a_new_export_replaces_the_colorizer(files, tmp_path):
    s = _built(files)
    s.project.color = ColorSpec.from_file(files["fbx"], texel_mm=0.4)
    s.export(str(tmp_path / "a"), NAME, bambu=True)
    first = s.colorizer
    s.export(str(tmp_path / "b"), NAME, bambu=True)
    assert s.colorizer is not None and s.colorizer is not first


# ------------------------------------------------------------------------------------------ selector
def test_candidates_are_rows_with_hex_only_names(choice):
    c = choice
    cands = c.candidates()
    assert c.has_colors() and len(cands) >= 3
    assert all(x["roles"] == [] and x["nombres"] == [] for x in cands)           # sin paleta de línea (color por textura): filas solo con el HEX
    assert all(x["out_hex"] == x["hex"] and not x["cambiado"] for x in cands)
    assert [x["area_pct"] for x in cands] == sorted((x["area_pct"] for x in cands), reverse=True)        # del más al menos usado
    assert not c.is_custom()


def test_automatic_selection_follows_the_cap_and_the_percentage(choice):
    c = choice
    c.colors = 1
    on = [x for x in c.candidates() if x["enabled"]]
    assert len(on) == 1 and on[0]["hex"] == c.candidates()[0]["hex"]         # el más usado
    c.colors, c.min_pct = 16, 0.0                                           # el tope del selector es 16; la figura tiene más candidatos
    assert len([x for x in c.candidates() if x["enabled"]]) == min(16, len(c.candidates()))
    c.min_pct = 20.0                                                        # solo sobreviven los que cubren al menos 20 %
    assert {x["hex"] for x in c.candidates() if x["enabled"]} == {x["hex"] for x in c.candidates() if x["area_pct"] >= 20.0}
    assert not c.is_custom()


def test_disabling_a_color_merges_it_and_never_leaves_zero(choice):
    c = choice
    cands = c.candidates()
    n_on = sum(1 for x in cands if x["enabled"])
    gone = [x for x in cands if x["enabled"]][-1]["hex"]
    c.set_enabled(gone, False)
    assert c.is_custom() and gone not in c.effective_selection()
    parts, summary = c.preview()
    assert len(summary["colores"]) == n_on - 1 and summary["fundidos"] >= 1 and parts
    assert sum(summary["area_pct"]) == pytest.approx(100.0, abs=0.1)
    c.selection = [cands[0]["hex"]]
    with pytest.raises(ValueError):
        c.set_enabled(cands[0]["hex"], False)                               # tiene que quedar al menos uno
    with pytest.raises(ValueError):
        c.set_enabled("#010203", True)                                      # no es candidato
    with pytest.raises(ValueError):
        c.set_enabled("rojo", True)                                         # ni un HEX


def test_reset_goes_back_to_automatic(choice):
    c = choice
    c.set_enabled(c.candidates()[0]["hex"], False) if len(c.effective_selection()) > 1 else None
    c.set_output_color(c.candidates()[0]["hex"], "#123456")
    assert c.is_custom()
    c.reset()
    assert not c.is_custom() and c.selection is None and c.remap == {}


def test_output_color_remap_reaches_the_preview_and_the_file(exported, choice):
    s, out, _info = exported
    c = choice
    top = c.candidates()[0]["hex"]
    c.set_output_color(top, "#0A0B0C")
    row = c.candidates()[0]
    assert row["out_hex"] == "#0A0B0C" and row["cambiado"] and c.is_custom()
    _parts, summary = c.preview()
    assert "#0A0B0C" in summary["colores"]
    c.set_output_color(top, top)                                            # volver al mismo color lo quita
    assert c.remap == {} and not c.candidates()[0]["cambiado"]
    c.set_output_color(top, "#0A0B0C")
    rep = c.export()                                                        # «Exportar 3MF ahora»
    assert rep["3mf"] == f"{NAME}_bambu.3mf" and "#0A0B0C" in materials(out)
    c.reset()
    c.export()                                                              # automático otra vez: el color cambiado desaparece del archivo
    assert "#0A0B0C" not in materials(out)


def test_options_follow_the_checkbox_and_the_manual_choices(choice):
    c = choice
    assert c.options() is None                                              # casilla apagada
    c.enabled, c.colors, c.min_pct = True, 3, 1.5
    assert c.options() == {"max_colors": 3, "min_area_pct": 1.5}            # automático: sin `chosen`
    c.set_output_color(c.candidates()[0]["hex"], "#0A0B0C")
    opt = c.options()
    assert opt["chosen"] == c.effective_selection() and opt["remap"] == {c.candidates()[0]["hex"]: "#0A0B0C"}
    c.enabled = False
    assert c.options() is None and "chosen" in c.options(force=True)


def test_stale_selection_is_ignored_not_fatal(choice):
    c = choice
    c.selection = ["#010203", "#040506"]                                    # ya no son candidatos (otra figura)
    assert c.effective_selection() == [x["hex"] for x in c.candidates() if x["enabled"]]
    c.remap = {"#010203": "#000000"}
    assert "remap" not in c.options(force=True)                             # el cambio de un color que ya no existe no viaja
    c.preview()                                                             # y no rompe


def test_oriented_preview_differs_only_by_the_print_orientation(exported):
    s, _out, _info = exported
    cz = s.colorizer
    opt = {"max_colors": 4, "min_area_pct": 0.5}
    plain = np.concatenate([p.V for p in cz.bambu_preview(opt, oriented=False).pieces])
    posed = np.concatenate([p.V for p in cz.bambu_preview(opt).pieces])
    assert plain.shape == posed.shape                                       # misma geometría...
    T = np.asarray(cz._last_write[2], float) if cz._last_write[2] is not None else np.eye(4)
    assert np.allclose(plain @ T[:3, :3].T + T[:3, 3], posed, atol=1e-6)    # ...con o sin la orientación de impresión


# ------------------------------------------------------------------------------------------ Controller
def _ctrl(exported) -> Controller:
    s, _out, _info = exported
    c = Controller(s)
    c.bambu.reset()
    c.bambu.enabled, c.bambu.colors, c.bambu.min_pct = False, 8, 0.5
    c.show_bambu, c._bambu_view = False, None
    return c


def test_set_bambu_clamps_and_does_not_touch_the_project(exported):
    c = _ctrl(exported)
    before = c.session.project.to_dict()
    c.set_bambu(enabled=True, colors=0, min_pct=250)
    assert c.bambu.enabled and c.bambu.colors == 1 and c.bambu.min_pct == 99.9
    c.set_bambu(colors=5, min_pct=0.7)
    assert c.bambu.colors == 5 and c.bambu.min_pct == 0.7
    assert c.session.project.to_dict() == before                            # es una opción de exportación: la receta no cambia


def test_export_passes_the_bambu_options_and_reports_the_files(files, tmp_path):
    s = _built(files)
    s.project.color = ColorSpec.from_file(files["fbx"], texel_mm=0.4)
    c = Controller(s)
    c.set_bambu(enabled=True, colors=3)
    info = c.export(str(tmp_path), orient=False)
    assert info is not None and c.bambu_report["3mf"] == f"{NAME}_bambu.3mf" and len(c.bambu_report["colores"]) == 3
    assert "Bambu" in c.message and (tmp_path / f"{NAME}_bambu.3mf").exists()
    assert c.show_bambu is False and c.bambu.has_colors()                    # el selector ya tiene de dónde elegir


def test_export_with_the_checkbox_off_writes_no_bambu_files(files, tmp_path):
    s = _built(files)
    s.project.color = ColorSpec.from_file(files["fbx"], texel_mm=0.4)
    c = Controller(s)
    c.export(str(tmp_path), orient=False)
    assert c.bambu_report is None and not (tmp_path / f"{NAME}_bambu.3mf").exists()


def test_export_asking_bambu_without_color_is_explained(files, tmp_path):
    c = Controller(_built(files))
    c.set_bambu(enabled=True)
    c.export(str(tmp_path), orient=False)
    assert c.bambu_report is None and "color" in c.message.lower() and not c.bambu.has_colors()


def test_preview_view_draws_flat_colors_instead_of_the_figure(exported):
    c = _ctrl(exported)
    sc = FakeScene()
    c.sync(sc)
    assert "figura" in sc.items and not any(k.startswith("bambu:") for k in sc.items)
    assert c.set_show_bambu(True) and c.bambu_view_active()
    c.sync(sc)
    drawn = [k for k in sc.items if k.startswith("bambu:")]
    assert drawn and "figura" not in sc.items                               # la figura gris no se dibuja debajo
    assert "bambu" in c.message.lower() or "3MF" in c.message
    assert c.set_show_bambu(False) and not c.bambu_view_active()
    c.sync(sc)
    assert "figura" in sc.items and not any(k.startswith("bambu:") for k in sc.items)


def test_preview_follows_the_selector(exported):
    c = _ctrl(exported)
    assert c.set_show_bambu(True)
    n0 = len(c.bambu_view_parts())
    gone = [x for x in c.bambu.candidates() if x["enabled"]][-1]["hex"]
    c.bambu.set_enabled(gone, False)
    c.bambu_changed()
    assert c.bambu_view_parts() is None and not c.bambu_view_active()       # la vista vieja se descarta...
    assert c.set_show_bambu(True) and 0 < len(c.bambu_view_parts()) <= n0   # ...y se recalcula con la nueva selección
    assert gone not in {h for h in [x["hex"] for x in c.bambu.candidates() if x["enabled"]]}


def test_a_late_preview_does_not_overwrite_a_newer_one(exported):
    c = _ctrl(exported)
    t1, work1 = c.begin_bambu_preview()
    t2, work2 = c.begin_bambu_preview()
    assert t2 == t1 + 1
    parts, summary = work2()
    assert c.finish_bambu_preview(t2, parts, summary) is True
    parts1, summary1 = work1()
    assert c.finish_bambu_preview(t1, parts1, summary1) is False            # llegó tarde: se descarta
    assert c.bambu_view_parts() is parts


def test_preview_error_turns_the_view_off(exported):
    c = _ctrl(exported)
    c.show_bambu = True
    t, _work = c.begin_bambu_preview()
    assert c.finish_bambu_preview(t, None, None, RuntimeError("boom")) is False
    assert not c.show_bambu and c.bambu_view_parts() is None and "boom" in c.message


def test_preview_is_dropped_when_the_figure_is_invalidated(files, tmp_path):
    s = _built(files)
    s.project.color = ColorSpec.from_file(files["fbx"], texel_mm=0.4)
    c = Controller(s)
    c.export(str(tmp_path), orient=False)
    assert c.set_show_bambu(True) and c.bambu_view_active()
    s.outcome = None                                                        # p. ej. se movió un plano: las piezas ya no valen
    assert not c.bambu_view_active() and not c.bambu.has_colors()
    sc = FakeScene()
    c.sync(sc)
    assert not any(k.startswith("bambu:") for k in sc.items)                # vuelve a la figura normal sin romper
    assert c.set_show_bambu(True) is False and not c.show_bambu             # y no se puede prender sin colores


def test_export_bambu_now_rewrites_only_the_bambu_files(exported):
    s, out, _info = exported
    c = _ctrl(exported)
    glb = os.path.join(out, f"{NAME}_ensamblada.glb")
    before = os.path.getmtime(glb)
    c.bambu.set_output_color(c.bambu.candidates()[0]["hex"], "#0A0B0C")
    rep = c.export_bambu()
    assert rep is not None and c.bambu_report is rep and not c.busy
    assert "#0A0B0C" in materials(out) and os.path.getmtime(glb) == before   # el GLB, los PNG y el informe no se tocan
    c.bambu.reset()
    c.export_bambu()


def test_export_bambu_now_needs_an_export_first(files):
    c = Controller(_built(files))
    assert c.begin_export_bambu() is None and not c.busy and "exportá" in c.message.lower()
    assert c.export_bambu() is None
    c.busy = True
    assert c.begin_export_bambu() is None                                   # con una tarea en curso no arranca otra
    c.busy = False


def test_export_bambu_failure_releases_busy(exported):
    c = _ctrl(exported)
    job = c.begin_export_bambu()
    assert job is not None and c.busy
    assert c.finish_export_bambu(None, OSError("disco lleno")) is None and not c.busy and "disco lleno" in c.message
