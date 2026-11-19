"""T22 · `Session.load` acepta un `.fbx` (modo A): guarda la fuente y un resumen. Sin GTK."""
import numpy as np
import pytest

from afm import fbxio
from afm.frame import FigureFrame
from afm.gui.controller import Controller
from afm.gui.session import Session, fbx_summary, uv_island_count
from afm.testfigure import make_doll
from color_helpers import doll_textured, write_doll_fbx
from fbx_synth import write_fbx


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("gui_fbx")
    stl = str(d / "muneco.stl")
    make_doll().export(stl)
    tm = doll_textured()
    n = len(tm.faces)
    plain = str(d / "sin_textura.fbx")
    write_fbx(plain, tm.vertices, tm.faces.tolist(), np.arange(3 * n), np.asarray(tm.corner_uv, float).reshape(-1, 2), texture=None)
    return {"fbx": write_doll_fbx(d / "muneco.fbx"), "stl": stl, "sin_textura": plain}


def test_session_loads_fbx_and_summary(files):
    s = Session()
    rep = s.load(files["fbx"])
    sm = s.fbx_summary
    assert s.loaded and rep.color_source_path and s.color_source is not None
    assert sm["triangulos"] == len(doll_textured().faces) and sm["islas_uv"] >= 1
    assert sm["texturas"] == ["base_color"] and sm["materiales"] == 1 and sm["avisos"] == []
    assert "triángulos" in s.fbx_summary_text() and "base_color" in s.fbx_summary_text()
    c = Controller()                                              # el controlador muestra el resumen en el mensaje de estado
    c.open_stl(files["fbx"])
    assert c.loaded and "islas UV" in c.message


def test_session_warns_missing_base_texture(files):
    s = Session()
    s.load(files["sin_textura"])
    assert s.loaded and any("textura" in a for a in s.fbx_summary["avisos"])
    assert s.fbx_summary["texturas"] == []
    tm = fbxio.load_textured(files["fbx"])
    tm.info = {**tm.info, "n_materiales": 3}
    assert any("3" in a for a in fbx_summary(tm)["avisos"])      # más de un material


def test_session_loads_stl_as_before(files):
    s = Session()
    s.load(files["fbx"])
    rep = s.load(files["stl"])                                    # un STL después de un FBX limpia la fuente
    assert s.color_source is None and s.fbx_summary is None and rep.color_source_path == ""
    assert s.fbx_summary_text() == "" and s.loaded


def test_session_fbx_planes_use_figure_frame(files):
    """Un punto del FBX, llevado con `report.to_figure` (como se guardan los planos), cae donde `FigureFrame.from_load` lo pone."""
    s = Session()
    rep = s.load(files["fbx"])
    tm = s.color_source
    R = np.asarray(rep.orient_matrix, float).reshape(3, 3)
    pts = tm.vertices[::40]
    a = rep.to_figure(pts @ R.T)
    frame = FigureFrame.from_load(rep)
    assert np.allclose(a, frame.apply(pts), atol=1e-6)
    allv = frame.apply(tm.vertices)                                # toda la fuente cae sobre la figura cargada
    assert np.allclose(allv.min(axis=0), s.base.bounds[0], atol=0.05) and np.allclose(allv.max(axis=0), s.base.bounds[1], atol=0.05)


def test_uv_island_count_splits_on_uv_seams():
    tm = doll_textured()
    one_island = uv_island_count(tm)
    assert one_island >= 1
    tm2 = doll_textured()
    tm2.corner_uv = tm2.corner_uv.copy()
    tm2.corner_uv[:, :, 0] += (np.arange(len(tm2.faces)) * 0.01)[:, None]       # cada triángulo con UV propios: una isla por triángulo
    assert uv_island_count(tm2) == len(tm2.faces) > one_island
