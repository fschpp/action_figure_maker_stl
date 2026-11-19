"""T23 · sección `color` del `.proyecto` (ColorSpec): se escribe solo si existe y los proyectos viejos no cambian."""
import glob
import hashlib
import json
import os

from afm.project import ColorSpec, Project, demo_project, sha256_of

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_project_without_color_roundtrip_unchanged(tmp_path):
    pr = demo_project()
    assert pr.to_dict() == pr._to_dict_base()                      # el mismo diccionario que antes de T23
    p = tmp_path / "a.proyecto"
    pr.save(str(p))
    assert p.read_text() == json.dumps(pr._to_dict_base(), indent=2)
    again = Project.load(str(p))
    assert again.color is None and again.to_dict() == pr.to_dict()


def test_project_with_color_roundtrip(tmp_path):
    pr = demo_project()
    pr.color = ColorSpec(source_path="guemes.fbx", source_sha256="ab" * 32, eps=0.3, texel_mm=0.15, fill="#C8C8C8", symmetric=True)
    p = tmp_path / "c.proyecto"
    pr.save(str(p))
    back = Project.load(str(p))
    assert back.color == pr.color
    assert json.load(open(p))["version"] == 1                     # la versión no cambia
    assert back.to_dict() == pr.to_dict()


def test_color_not_written_when_none():
    d = demo_project().to_dict()
    assert "color" not in d
    pr = demo_project()
    pr.color = ColorSpec()
    assert pr.to_dict()["color"] == {"source_path": "", "source_sha256": "", "eps": 0.15, "texel_mm": 0.2, "fill": "extend",
                                     "symmetric": False}
    pr.color = None
    assert "color" not in pr.to_dict()


def test_sha256_helper(tmp_path):
    f = tmp_path / "x.fbx"
    f.write_bytes(b"hola" * 600_000)                              # > 1 bloque de lectura
    want = hashlib.sha256(b"hola" * 600_000).hexdigest()
    assert sha256_of(str(f)) == want
    cs = ColorSpec.from_file(str(f), texel_mm=0.3)
    assert cs.source_path == str(f) and cs.source_sha256 == want and cs.texel_mm == 0.3 and cs.eps == 0.15
    assert cs.matches(str(f))
    g = tmp_path / "y.fbx"
    g.write_bytes(b"otro")
    assert not cs.matches(str(g)) and not ColorSpec(source_path="x").matches(str(f))      # sin hash guardado no se puede afirmar


def test_missing_fields_use_defaults():
    cs = ColorSpec.from_dict({"source_path": "a.fbx"})
    assert cs == ColorSpec(source_path="a.fbx") and cs.fill == "extend" and cs.symmetric is False
    pr = Project.from_dict({**demo_project().to_dict(), "color": {"texel_mm": 0.5}})
    assert pr.color.texel_mm == 0.5 and pr.color.eps == 0.15 and pr.color.source_path == ""


def test_old_project_loads(tmp_path):
    files = glob.glob(os.path.join(ROOT, "examples", "*.proyecto")) + glob.glob(os.path.join(ROOT, "catalogo", "**", "*.proyecto"), recursive=True)
    assert files
    for f in files:
        pr = Project.load(f)
        assert pr.color is None and "color" not in pr.to_dict(), f
        out = tmp_path / "re.proyecto"
        pr.save(str(out))
        assert Project.load(str(out)).to_dict() == pr.to_dict(), f
