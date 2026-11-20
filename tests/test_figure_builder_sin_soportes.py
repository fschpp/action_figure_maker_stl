"""«Imprimir sin soportes» en el armado por catálogo: `FigureBuilder` lo pasa a `run_armar` y lo cuenta en la geometría armada."""
import os

from afm import cli
from afm.gui import figure_builder as FB

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


def _capture(monkeypatch):
    seen = {}

    def fake(recipe_path, outdir, name, root, **kw):
        seen.update(kw)
        return {"archivos": {"ensamblada": "x.stl"}, "validacion": {"ok": True}}
    monkeypatch.setattr(FB, "run_armar", fake, raising=False)
    monkeypatch.setattr(cli, "run_armar", fake)
    monkeypatch.setattr(FB.FigureBuilder, "problems", lambda self: [])
    monkeypatch.setattr(FB.FigureBuilder, "color_missing", lambda self, cat=None: {})
    monkeypatch.setattr(FB.FigureBuilder, "recipe", lambda self: type("R", (), {"save": lambda s, p: None, "to_dict": lambda s: {"personaje": "x"}})())
    return seen


def test_default_is_not_flat(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    FB.FigureBuilder(ROOT).generate(str(tmp_path), progress=lambda *_: None)
    assert seen["flat"] is None and seen["fill_hip"] is False


def test_flat_depth_and_fill_hip_reach_run_armar(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    b = FB.FigureBuilder(ROOT)
    b.flat, b.flat_depth, b.fill_hip = True, 1.5, True
    b.generate(str(tmp_path), progress=lambda *_: None)
    assert seen["flat"] == 1.5 and seen["fill_hip"] is True
    b.flat_depth = None                                  # sin altura: la de la línea (-1)
    b.generate(str(tmp_path), progress=lambda *_: None)
    assert seen["flat"] < 0


def test_fill_hip_needs_flat(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    b = FB.FigureBuilder(ROOT)
    b.fill_hip = True
    b.generate(str(tmp_path), progress=lambda *_: None)
    assert seen["fill_hip"] is False


def test_geometry_key_changes_with_flat(monkeypatch):
    _capture(monkeypatch)
    b = FB.FigureBuilder(ROOT)
    k0 = b._geometry_key()
    b.flat = True
    assert b._geometry_key() != k0
