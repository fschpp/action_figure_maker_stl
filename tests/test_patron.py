"""Proyecto de un héroe nuevo desde una figura patrón."""
import json
import os

import numpy as np
import pytest

from afm.cli import main
from afm.project import Project, demo_project
from afm.testfigure import make_doll


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("patron")
    ref_stl, hero_stl, ref_proj = str(d / "ref.stl"), str(d / "heroe.stl"), str(d / "ref.proyecto")
    make_doll().export(ref_stl)
    hero = make_doll()
    hero.apply_scale([1.12, 0.95, 1.06])                       # otras proporciones: más ancho, menos profundo, más alto
    hero.apply_translation([30.0, -12.0, 5.0])                 # y en otro lugar del archivo
    hero.export(hero_stl)
    proj = demo_project()
    proj.trims = []
    with open(ref_proj, "w") as f:
        json.dump(proj.to_dict(), f)
    return ref_proj, hero_stl, str(d)


def test_desde_patron_copies_planes_and_redraws_silhouettes(files):
    from afm.patron import desde_patron
    ref_proj, hero_stl, _d = files
    ref = Project.from_dict(json.load(open(ref_proj)))
    sess, notes = desde_patron(ref_proj, hero_stl)
    p = sess.project
    assert sess.loaded and [l.name for l in p.limbs] == [l.name for l in ref.limbs]
    assert p.parts == {} and p.part_scales == {} and p.crown_z is None
    assert p.waist is None or np.allclose(p.waist.plane.origin, ref.waist.plane.origin)       # copiados tal cual
    # la pierna se volvió a dibujar sobre la malla nueva (otras proporciones); el brazo del muñeco no tiene tramo separado del
    # tronco y se queda con el del patrón, avisando
    leg_new = next(l for l in p.limbs if l.kind == "leg").polygon
    leg_ref = next(l for l in ref.limbs if l.kind == "leg").polygon
    assert len(leg_new) >= 3 and not (leg_new.shape == leg_ref.shape and np.allclose(leg_new, leg_ref))
    assert any("«brazo»: se dejó la silueta del patrón" in n for n in notes)
    assert isinstance(notes, list)


def test_cli_desde_patron_writes_project_and_image(files, capsys):
    ref_proj, hero_stl, d = files
    out = os.path.join(d, "salida.proyecto")
    assert main(["desde-patron", ref_proj, hero_stl, "--out", out]) == 0
    text = capsys.readouterr().out
    assert "Proyecto guardado" in text and os.path.exists(out)
    img = os.path.join(d, "salida_revision.png")
    assert os.path.getsize(img) > 20000
    loaded = Project.from_dict(json.load(open(out)))                     # el archivo se vuelve a abrir
    assert loaded.limbs and loaded.symmetry is not None
    assert main(["desde-patron", ref_proj, os.path.join(d, "no-existe.stl")]) == 2
