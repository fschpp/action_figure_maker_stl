"""T39 · Paleta de color de la línea en el catálogo (`paleta.json`) y `afm catalogo paleta …`. Sin FBX ni GUI."""
import hashlib
import json
import os

import pytest

from afm import parts as P
from afm.catalog import Catalog, CatalogError
from afm.cli import main
from afm.palette import Palette, Role
from test_catalog import arm_piece

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
IDENT = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


@pytest.fixture
def cat(tmp_path):
    return Catalog.create(str(tmp_path / "linea"), "proceres-130", 130.0, 0.4)


def digest_tree(root):
    """SHA-256 de cada archivo de `piezas/` (fichas y STL): lo que el color NUNCA debe tocar."""
    out = {}
    for d, _s, files in os.walk(os.path.join(root, "piezas")):
        for f in files:
            with open(os.path.join(d, f), "rb") as fh:
                out[os.path.relpath(os.path.join(d, f), root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def add_with_roles(cat, pid, roles):
    """Pieza cuya ficha declara `color.roles` (el bloque de T42, escrito a mano: T39 todavía no guarda paquetes)."""
    mesh, meta = arm_piece(pid)
    meta.color = {"modo": "roles", "fuente": f"{pid}@1.color.npz", "sha256": "0" * 64, "similitud": IDENT, "roles": list(roles)}
    P.write_piece(cat._dir(meta.tipo), meta, mesh)
    return meta


def sample_palette():
    return Palette([Role("piel", "Piel", "#D8A47F", ["#A98265"]), Role("uniforme", "Uniforme", "#F2F0E6", ["#BFBAAD", "#9A9587"]),
                    Role("botas", "Botas", "#201F1C")])


def test_catalog_without_palette_opens_and_verifies_as_before(cat):
    mesh, meta = arm_piece()
    cat.add_piece(mesh, meta)
    assert cat.palette() is None and not os.path.exists(cat.palette_path)
    assert cat.verify().ok
    assert Catalog.open(cat.root).palette() is None
    assert Catalog.open(ROOT).palette() is None and Catalog.open(ROOT).verify().ok      # el catálogo de ejemplo del repo sigue igual


def test_set_palette_is_atomic_and_leaves_pieces_untouched(cat):
    mesh, meta = arm_piece()
    cat.add_piece(mesh, meta)
    line_before = open(os.path.join(cat.root, "linea.json"), "rb").read()
    tree_before = digest_tree(cat.root)
    cat.set_palette(sample_palette())
    assert Catalog.open(cat.root).palette().to_dict() == sample_palette().to_dict()
    assert not os.path.exists(cat.palette_path + ".tmp")                        # el temporal se renombró, no quedó al lado
    cat.set_role_color("piel", "#aa5500")
    assert digest_tree(cat.root) == tree_before                                  # ni fichas ni STL cambiaron un byte
    assert open(os.path.join(cat.root, "linea.json"), "rb").read() == line_before

    # si falla el reemplazo atómico, el archivo anterior queda intacto
    good = open(cat.palette_path, "rb").read()
    bad = Palette([Role("piel", "Piel", "#D8A47F")])
    bad.roles[0].hex = "no-es-un-color"                                          # alguien rompió el rol después de crearlo
    with pytest.raises(CatalogError, match="no es válida"):
        cat.set_palette(bad)
    assert open(cat.palette_path, "rb").read() == good and not os.path.exists(cat.palette_path + ".tmp")


def test_delete_role_in_use_is_refused_with_piece_list(cat):
    cat.set_palette(sample_palette())
    add_with_roles(cat, "brazo-uno-01", ["piel", "uniforme"])
    add_with_roles(cat, "brazo-dos-01", ["uniforme"])
    assert cat.palette_usage("uniforme") == ["brazo-dos-01@1", "brazo-uno-01@1"] or sorted(cat.palette_usage("uniforme")) == ["brazo-dos-01@1", "brazo-uno-01@1"]
    assert cat.palette_usage("botas") == []
    with pytest.raises(CatalogError, match=r"uniforme.*brazo-dos-01@1.*brazo-uno-01@1|uniforme.*brazo-uno-01@1.*brazo-dos-01@1"):
        cat.delete_role("uniforme")
    assert "uniforme" in cat.palette().ids()                                     # no se borró nada
    cat.delete_role("botas")                                                     # sin uso: se puede
    assert cat.palette().ids() == ["piel", "uniforme"]
    with pytest.raises(CatalogError, match="no tiene el rol"):
        cat.delete_role("botas")


def test_verify_flags_piece_role_missing_from_palette(cat):
    cat.set_palette(sample_palette())
    add_with_roles(cat, "brazo-uno-01", ["piel", "dorado"])
    probs = cat.verify().problems
    assert any("brazo-uno-01@1" in p and "dorado" in p for p in probs)
    assert not any("piel" in p and "no tiene" in p for p in probs)
    assert not any("FBX" in p for p in probs)                                    # el modo «roles» no se revisa como un FBX (eso es de T42)
    cat.add_role("dorado", "#C9A227")
    assert not any("dorado" in p for p in cat.verify().problems)
    os.remove(cat.palette_path)                                                  # sin paleta, una pieza con roles también se marca
    assert any("falta paleta.json" in p for p in cat.verify().problems)
    with open(cat.palette_path, "w") as f:
        f.write("{no es json")
    assert any(p.startswith("paleta.json") for p in cat.verify().problems)
    with pytest.raises(CatalogError, match="paleta.json"):
        cat.palette()


def test_import_is_refused_when_it_would_leave_a_piece_without_role(cat, tmp_path):
    cat.set_palette(sample_palette())
    add_with_roles(cat, "brazo-uno-01", ["piel"])
    other = tmp_path / "otra.json"
    other.write_text(json.dumps(Palette([Role("botas", "Botas", "#000000")]).to_dict()))
    before = open(cat.palette_path, "rb").read()
    with pytest.raises(CatalogError, match="piel"):
        cat.import_palette(str(other))
    assert open(cat.palette_path, "rb").read() == before
    other.write_text(json.dumps({"formato": "otro"}))
    with pytest.raises(CatalogError, match="formato"):
        cat.import_palette(str(other))
    assert open(cat.palette_path, "rb").read() == before


def run(capsys, *argv):
    rc = main(["catalogo", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_cli_palette_roundtrip_export_import(cat, tmp_path, capsys):
    rc, out, _ = run(capsys, "paleta", "ver", cat.root)
    assert rc == 0 and "no tiene paleta" in out
    assert run(capsys, "paleta", "agregar", cat.root, "piel", "#d8a47f", "--nombre", "Piel", "--ancla", "#A98265")[0] == 0
    assert run(capsys, "paleta", "agregar", cat.root, "uniforme", "#F2F0E6")[0] == 0
    rc, out, err = run(capsys, "paleta", "agregar", cat.root, "piel", "#000000")
    assert rc == 2 and "ya existe" in err
    rc, out, _ = run(capsys, "paleta", "ver", cat.root)
    assert rc == 0 and "#D8A47F" in out and "«Piel»" in out and "2 rol(es)" in out and "#A98265" in out
    assert run(capsys, "paleta", "renombrar", cat.root, "uniforme", "Uniforme blanco")[0] == 0
    exported = str(tmp_path / "paleta_exportada.json")
    assert run(capsys, "paleta", "exportar", cat.root, exported)[0] == 0
    assert json.load(open(exported))["formato"] == "afm-paleta-1"
    other = Catalog.create(str(tmp_path / "otra"), "otra-130", 130.0, 0.4)
    assert run(capsys, "paleta", "importar", other.root, exported)[0] == 0
    assert other.palette().to_dict() == cat.palette().to_dict()
    assert run(capsys, "paleta", "quitar", cat.root, "uniforme")[0] == 0
    assert cat.palette().ids() == ["piel"]
    rc, _, err = run(capsys, "paleta", "importar", cat.root, str(tmp_path / "no_existe.json"))
    assert rc == 2 and "ERROR" in err


def test_cli_color_changes_only_that_role(cat, capsys):
    cat.set_palette(sample_palette())
    add_with_roles(cat, "brazo-uno-01", ["uniforme"])
    tree = digest_tree(cat.root)
    before = {r.id: r for r in cat.palette().roles}
    rc, out, _ = run(capsys, "paleta", "color", cat.root, "uniforme", "#1F3A93")
    assert rc == 0 and "#F2F0E6" in out and "#1F3A93" in out and "1 pieza(s)" in out
    after = {r.id: r for r in Catalog.open(cat.root).palette().roles}
    assert after["uniforme"].hex == "#1F3A93"
    assert after["uniforme"].anclas == before["uniforme"].anclas                  # las anclas no se mueven al recolorear
    for rid in ("piel", "botas"):
        assert after[rid].to_dict() == before[rid].to_dict()
    assert digest_tree(cat.root) == tree
    rc, _, err = run(capsys, "paleta", "color", cat.root, "uniforme", "azul")
    assert rc == 2 and "#RRGGBB" in err
    rc, _, err = run(capsys, "paleta", "color", cat.root, "fantasma", "#000000")
    assert rc == 2 and "fantasma" in err
    assert Catalog.open(cat.root).palette().hex_of("uniforme") == "#1F3A93"       # los errores no cambiaron nada


def test_cli_palette_texts_exist_in_english_and_portuguese(cat, capsys, monkeypatch):
    from afm import i18n
    cat.set_palette(sample_palette())
    try:
        for lang, word in (("en", "role(s)"), ("pt", "papel(éis)")):
            i18n.set_language(lang)
            rc, out, _ = run(capsys, "paleta", "ver", cat.root)
            assert rc == 0 and word in out
    finally:
        i18n.set_language("es")
