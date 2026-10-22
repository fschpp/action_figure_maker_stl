"""Fase 5 del plan de piezas intercambiables: `Project.parts`, `afm build --catalogo --pieza` y la sección `piezas` de
`validate`, con el muñeco sintético y `catalogo/ejemplo`."""
import json
import os

import pytest

from afm import solids as S
from afm.assembly import AssemblyError, PartChoice, resolve_parts
from afm.catalog import Catalog
from afm.cli import main
from afm.pipeline import build, validate
from afm.project import PartRef, Project, demo_project
from afm.testfigure import make_doll

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EJEMPLO = os.path.join(ROOT, "catalogo", "ejemplo")


def test_project_parts_round_trip_and_old_projects_load(tmp_path):
    pr = demo_project()
    pr.parts["pierna"] = PartRef("pierna-muneco-01", 1, "ejemplo-100")
    path = str(tmp_path / "p.proyecto")
    pr.save(path)
    back = Project.load(path)
    assert back.parts == {"pierna": PartRef("pierna-muneco-01", 1, "ejemplo-100")}
    assert back.parts["pierna"].ref == "pierna-muneco-01@1"
    old = pr.to_dict()
    del old["parts"]                                   # un .proyecto anterior a la Fase 5
    assert Project.from_dict(old).parts == {}
    assert PartRef("x").ref == "x"


def test_resolve_parts_checks_line_and_piece():
    ok = resolve_parts({"pierna": PartRef("pierna-muneco-01", 1, "ejemplo-100")}, EJEMPLO)
    assert isinstance(ok["pierna"], PartChoice) and ok["pierna"].meta.ref == "pierna-muneco-01@1"
    assert resolve_parts({"pierna": PartRef("pierna-muneco-01")}, EJEMPLO)["pierna"].meta.version == 1   # None = la última
    with pytest.raises(AssemblyError, match="línea"):
        resolve_parts({"pierna": PartRef("pierna-muneco-01", 1, "otra-linea")}, EJEMPLO)
    with pytest.raises(AssemblyError, match="no hay ninguna pieza"):
        resolve_parts({"pierna": PartRef("pierna-nada-99")}, EJEMPLO)
    with pytest.raises(AssemblyError, match="línea"):
        resolve_parts({"pierna": PartRef("pierna-muneco-01")})                  # sin línea ni catálogo


def test_validate_reports_the_parts_section():
    doll = S.to_manifold(make_doll())
    pr = demo_project()
    parts = resolve_parts({"pierna": PartRef("pierna-muneco-01", 1, "ejemplo-100")}, EJEMPLO)
    res = build(doll, pr.limbs, pr.params, head=pr.head, parts=parts)
    val = validate(res, pr.params, pr.limbs)
    assert val["piezas"]["pierna"]["pieza"] == "pierna-muneco-01@1"
    assert val["piezas"]["pierna"]["nivel"] in ("ok", "aviso")
    assert val["ok"], val["problems"]
    # una compatibilidad «no» es un problema de la validación
    res.report["piezas"]["pierna"]["compat"] = {"nivel": "no", "iou": 0.1, "escalon_mm": 3.0, "margenes_mm": {},
                                                "motivos": ["la cara no coincide"], "avisos": []}
    bad = validate(res, pr.params, pr.limbs)
    assert any("no es compatible" in x and "la cara no coincide" in x for x in bad["problems"])
    assert not bad["ok"]


def test_cli_build_with_catalog_piece_end_to_end(tmp_path, capsys):
    stl, proj, out = str(tmp_path / "muneco.stl"), str(tmp_path / "muneco.proyecto"), str(tmp_path / "out")
    make_doll().export(stl)
    demo_project().save(proj)
    # errores de uso: lugar inexistente (argparse → SystemExit 2), pieza inexistente (return 2), falta el catálogo
    with pytest.raises(SystemExit):
        main(["build", stl, proj, "--out", out, "--catalogo", EJEMPLO, "--pieza", "cola=pierna-muneco-01"])
    assert main(["build", stl, proj, "--out", out, "--catalogo", EJEMPLO, "--pieza", "pierna=pierna-nada-99"]) == 2
    with pytest.raises(SystemExit):
        main(["build", stl, proj, "--out", out, "--pieza", "pierna=pierna-muneco-01"])
    capsys.readouterr()
    code = main(["build", stl, proj, "--out", out, "--catalogo", EJEMPLO, "--pieza", "pierna=pierna-muneco-01",
                 "--pieza", "brazo=brazo-muneco-01@1"])
    text = capsys.readouterr().out
    assert code == 0, text
    assert "Pieza de catálogo en pierna: pierna-muneco-01@1" in text
    saved = Project.load(os.path.join(out, "muneco.proyecto"))                  # la elección queda en el .proyecto con la versión fija
    assert saved.parts == {"pierna": PartRef("pierna-muneco-01", 1, "ejemplo-100"),
                           "brazo": PartRef("brazo-muneco-01", 1, "ejemplo-100")}
    info = json.load(open(os.path.join(out, "muneco_informe.json")))
    assert set(info["construccion"]["piezas"]) == {"pierna", "brazo"}
    assert info["validacion"]["piezas"]["brazo"]["pieza"] == "brazo-muneco-01@1"
    # el .proyecto guardado se vuelve a construir sin repetir --pieza (solo hace falta el catálogo)
    assert main(["build", stl, os.path.join(out, "muneco.proyecto"), "--out", str(tmp_path / "out2"), "--catalogo", EJEMPLO]) == 0
