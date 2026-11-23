"""Casilla «Salida para Bambu Studio» de «Armar figura desde catálogo»: lo que decide está en `FigureBuilder` (sin GTK, R9)."""
import json
import os
import shutil
import zipfile

from test_color_catalog_palette import base_line  # noqa: F401  (fixture)
from test_gui_figure_palette import NEW, make_builder


def _colors(out, name):
    with zipfile.ZipFile(os.path.join(out, f"{name}_bambu.3mf")) as z:
        xml = z.read("3D/3dmodel.model").decode()
    return {c for c in xml.split('displaycolor="')[1:] for c in [c[:7]]}


def test_bambu_options_default_off_and_follow_the_fields(tmp_path, base_line):
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    b = make_builder(d)
    assert b.bambu is False and b.bambu_options() is None
    key = b._geometry_key()
    b.bambu, b.bambu_colors, b.bambu_min_pct = True, 4, 1.5
    assert b.bambu_options() == {"max_colors": 4, "min_area_pct": 1.5}
    assert b._geometry_key() == key and "bambu" not in json.dumps(b.recipe().to_dict())      # opción de exportación: no toca receta ni geometría


def test_generate_writes_bambu_files_only_when_asked(tmp_path, base_line):
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    b = make_builder(d)
    off = str(tmp_path / "off")
    assert "bambu" not in b.generate(off, progress=lambda *_: None)["color"]["salida"]
    assert not [f for f in os.listdir(off) if "_bambu" in f]
    b.bambu, b.bambu_colors = True, 4
    on = str(tmp_path / "on")
    info = b.generate(on, progress=lambda *_: None)
    bam = info["color"]["salida"]["bambu"]
    assert os.path.isfile(os.path.join(on, bam["3mf"])) and os.path.isfile(os.path.join(on, bam["obj"]))
    assert 1 <= len(bam["colores"]) <= 4


def test_recolor_rewrites_the_bambu_3mf(tmp_path, base_line):
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    b = make_builder(d)
    b.bambu = True
    out = str(tmp_path / "salida")
    b.generate(out, progress=lambda *_: None)
    assert NEW.upper() not in _colors(out, b.name)
    b.set_override("rojo", NEW)
    b.recolor()
    assert NEW.upper() in _colors(out, b.name)
