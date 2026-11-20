"""T46 · Lógica de la ventana «Paleta» de la línea (`gui/palette_editor.py`), sin GTK ni OpenGL.

La ventana (`palette_window.py`) no se prueba acá (no hay GTK en el entorno de pruebas, R9): todo lo que decide está en `PaletteEditor`."""
import hashlib
import json
import os
import shutil

import numpy as np
import pytest

from afm import parts as P
from afm.catalog import Catalog
from afm.gui import palette_editor as PE
from afm.gui.controller import Controller
from afm.palette import Palette, Role

from test_catalog_roles import HEAD_ID, ROOT, new_meta, odd_frame, package_of, textured_from, two_color_palette

PIECE = f"{HEAD_ID}@2"


def digest(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def tree_hashes(root, skip=()) -> dict:
    out = {}
    for base, _d, files in os.walk(root):
        for f in files:
            if f in skip:
                continue
            p = os.path.join(base, f)
            out[os.path.relpath(p, root)] = digest(p)
    return out


@pytest.fixture()
def line(tmp_path):
    """Copia del catálogo de ejemplo con paleta rojo/azul (+ `verde` sin usar) y una pieza con color por roles (`cabeza-muneco-01@2`)."""
    d = str(tmp_path / "linea")
    shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    cat = Catalog.open(d)
    cat.set_palette(two_color_palette(extra=("verde",)))
    meta, mesh = cat.load(HEAD_ID)
    tm = textured_from(mesh, odd_frame())
    pkg = package_of(tm, two_color_palette())
    cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg)
    return d


@pytest.fixture()
def ctrl(line):
    c = Controller()
    assert c.open_catalog(line)
    return c


@pytest.fixture()
def ed(ctrl):
    return PE.PaletteEditor(ctrl)


def pal_path(line):
    return os.path.join(line, "paleta.json")


def test_palette_edit_apply_writes_file_only_on_apply(ed, line):
    before = digest(pal_path(line))
    others = tree_hashes(line, skip=("paleta.json",))
    assert not ed.dirty and ed.apply() is False
    assert ed.set_hex("rojo", "#00ff00") == "#00FF00"                       # normaliza a mayúscula
    ed.rename("azul", "Azul marino")
    ed.add("dorado", "#D4AF37")
    assert ed.dirty and digest(pal_path(line)) == before                    # solo el borrador cambió
    assert [r["id"] for r in ed.rows() if r["cambiado"]] == ["rojo", "azul", "dorado"]
    assert ed.apply() is True
    assert digest(pal_path(line)) != before and not ed.dirty
    pal = Catalog.open(line).palette()
    assert pal.hex_of("rojo") == "#00FF00" and pal.get("azul").nombre == "Azul marino" and "dorado" in pal.ids()
    assert pal.get("rojo").anclas == two_color_palette().get("rojo").anclas  # las anclas no cambian al recolorear
    assert tree_hashes(line, skip=("paleta.json",)) == others               # ni fichas, ni STL, ni paquetes
    assert ed.apply() is False


def test_revert_and_invalid_edits_leave_the_draft_alone(ed, line):
    ed.set_hex("rojo", "#123456")
    ed.revert()
    assert not ed.dirty and ed.draft.hex_of("rojo") == "#C81E1E"
    for bad in ("rojo", "#12345", "#GG0000", ""):
        with pytest.raises(PE.PaletteEditError):
            ed.set_hex("rojo", bad)
    with pytest.raises(PE.PaletteEditError):
        ed.set_hex("no_existe", "#000000")
    with pytest.raises(PE.PaletteEditError, match="vac"):
        ed.rename("rojo", "   ")
    with pytest.raises(PE.PaletteEditError, match="ya existe"):
        ed.add("rojo", "#000000")
    with pytest.raises(PE.PaletteEditError):
        ed.add("Id Malo", "#000000")
    assert not ed.dirty


def test_remove_role_in_use_is_blocked_with_pieces(ed):
    with pytest.raises(PE.PaletteEditError) as exc:
        ed.remove("rojo")
    assert PIECE in str(exc.value) and "rojo" in str(exc.value) and "rojo" in ed.draft.ids()
    ed.remove("verde")                                                       # sin uso: se quita del borrador
    assert "verde" not in ed.draft.ids() and ed.dirty
    ed.add("nuevo", "#101010")
    ed.remove("nuevo")                                                       # recién agregado y sin aplicar: se quita sin más
    with pytest.raises(PE.PaletteEditError):
        ed.remove("nuevo")


def test_apply_refuses_when_a_piece_started_using_a_removed_role(ed, ctrl, line):
    """Otra ventana catalogó una pieza con `verde` después de que el borrador lo quitó: aplicar se niega y no escribe."""
    ed.remove("verde")
    before = digest(pal_path(line))
    cat = Catalog.open(line)
    meta, mesh = cat.load(HEAD_ID)
    tm = textured_from(mesh, odd_frame())
    pal = two_color_palette(extra=("verde",))
    pkg = package_of(tm, pal)
    pkg.labels = np.where(pkg.labels == pkg.roles.index("azul"), pkg.roles.index("rojo"), pkg.labels).astype(np.uint8)  # un solo rol: rojo
    pkg.roles = ["verde" if r == "rojo" else r for r in pkg.roles]
    cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg)
    with pytest.raises(PE.PaletteEditError, match="verde"):
        ed.apply()
    assert digest(pal_path(line)) == before


def test_usage_counts_match_catalog(ed, ctrl, line):
    cat = Catalog.open(line)
    usage = ed.usage_map()
    assert usage["rojo"] == [PIECE] and usage["azul"] == [PIECE] and "verde" not in usage
    for row in ed.rows():
        assert row["piezas"] == cat.palette_usage(row["id"]) and row["n_piezas"] == len(row["piezas"])
    assert ed.rows()[2]["n_piezas"] == 0
    assert PIECE in ed.usage_notice("rojo") and "1" in ed.usage_notice("rojo")
    assert "verde" in ed.usage_notice("verde")                               # «ninguna pieza usa…»
    assert ed.preview_refs() == [PIECE]


def test_preview_lut_matches_palette(ed, line):
    pkg, _sim = ed._package(PIECE)
    lut = ed.preview_lut(PIECE)
    assert lut.shape == (256, 3) and lut.dtype == np.uint8
    for i, rid in enumerate(pkg.roles):
        assert tuple(lut[i]) == tuple(int(ed.draft.hex_of(rid)[k:k + 2], 16) for k in (1, 3, 5))
    assert tuple(lut[255]) == (0xC8, 0xC8, 0xC8)                             # «sin rol» sale plano
    atlas = ed.preview_atlas(PIECE)
    assert atlas.shape == pkg.labels.shape + (3,) and np.array_equal(atlas, lut[pkg.labels])
    shown = {tuple(c) for c in np.unique(atlas.reshape(-1, 3), axis=0)}
    assert shown <= {tuple(r) for r in lut}                                  # solo colores de la paleta
    # cambiar un HEX repinta con el borrador, sin guardar
    before = digest(pal_path(line))
    ed.set_hex("rojo", "#00FF00")
    new = ed.preview_atlas(PIECE)
    i = pkg.roles.index("rojo")
    assert tuple(ed.preview_lut(PIECE)[i]) == (0, 255, 0)
    assert np.array_equal(new[pkg.labels == i], np.tile([0, 255, 0], ((pkg.labels == i).sum(), 1)))
    assert np.array_equal(new[pkg.labels != i], atlas[pkg.labels != i])      # los otros roles no se tocan
    assert digest(pal_path(line)) == before
    ed.revert()
    assert np.array_equal(ed.preview_atlas(PIECE), atlas)


def test_preview_parts_are_textured_and_in_the_piece_frame(ed, line):
    from afm.gui.render import TexturedPart

    parts = ed.preview_parts(PIECE)
    assert len(parts) == 1 and isinstance(parts[0], TexturedPart)
    part = parts[0]
    assert np.array_equal(part.image, ed.preview_atlas(PIECE))
    assert part.corner_uv.shape == (len(part.mesh.faces), 3, 2)
    stl = Catalog.open(line).load(PIECE)[1]                                  # está en el marco del STL de la pieza (no el del FBX, escala ×1,3)
    assert np.allclose(part.mesh.bounds, stl.bounds, atol=0.5)
    assert float(part.mesh.volume) > 0                                       # normales hacia afuera


def test_preview_missing_role_and_piece_without_roles_are_clear_errors(ed):
    ed.set_hex("azul", "#0000FF")
    ed.draft = Palette([r for r in ed.draft.roles if r.id != "azul"])        # al borrador le falta un rol que usa la pieza
    with pytest.raises(PE.PaletteEditError, match="azul"):
        ed.preview_lut(PIECE)
    with pytest.raises(PE.PaletteEditError):
        ed.preview_lut(f"{HEAD_ID}@1")                                       # sin color por roles


def test_import_rejects_bad_json_without_changing_palette(ed, line, tmp_path):
    before = digest(pal_path(line))
    bad = tmp_path / "mala.json"
    bad.write_text("{no es json", encoding="utf-8")
    with pytest.raises(PE.PaletteEditError):
        ed.import_json(str(bad))
    wrong = tmp_path / "formato.json"
    wrong.write_text(json.dumps({"formato": "otra-cosa", "roles": []}), encoding="utf-8")
    with pytest.raises(PE.PaletteEditError):
        ed.import_json(str(wrong))
    with pytest.raises(PE.PaletteEditError):
        ed.import_json(str(tmp_path / "no_existe.json"))
    lacking = tmp_path / "sin_rojo.json"                                     # válida, pero le falta un rol en uso
    lacking.write_text(json.dumps(Palette([Role("azul", "Azul", "#1E3CC8")]).to_dict()), encoding="utf-8")
    with pytest.raises(PE.PaletteEditError) as exc:
        ed.import_json(str(lacking))
    assert "rojo" in str(exc.value) and PIECE in str(exc.value)
    assert digest(pal_path(line)) == before and not ed.dirty and ed.draft.ids() == ["rojo", "azul", "verde"]


def test_import_export_roundtrip_and_refuses_with_unapplied_changes(ed, line, tmp_path):
    out = str(tmp_path / "salida.json")
    ed.set_hex("verde", "#228B22")
    ed.export_json(out)                                                      # exporta lo que se ve (el borrador)
    exported = Palette.from_dict(json.load(open(out, encoding="utf-8")))
    assert exported.hex_of("verde") == "#228B22"
    with pytest.raises(PE.PaletteEditError, match="sin aplicar"):
        ed.import_json(out)                                                  # importar descartaría el borrador
    ed.revert()
    ed.import_json(out)
    assert Catalog.open(line).palette().hex_of("verde") == "#228B22" and not ed.dirty
    assert ed.rows()[2]["hex"] == "#228B22" and ed.rows()[2]["hex_guardado"] == "#228B22"


def test_export_of_an_empty_palette_is_refused(ctrl, tmp_path):
    root = str(tmp_path / "vacia")
    Catalog.create(root, "vacia", 100.0, 0.4)
    c = Controller()
    assert c.open_catalog(root)
    e = PE.PaletteEditor(c)
    assert not e.exists and e.rows() == [] and e.preview_refs() == [] and not e.dirty
    with pytest.raises(PE.PaletteEditError):
        e.export_json(str(tmp_path / "x.json"))
    e.add("piel", "#D8A47F", "Piel")                                         # la primera edición crea la paleta al aplicar
    assert e.dirty and not os.path.exists(os.path.join(root, "paleta.json"))
    assert e.apply() is True and Catalog.open(root).palette().ids() == ["piel"]


def test_editor_without_catalog_is_inert_and_says_so():
    e = PE.PaletteEditor(Controller())
    assert not e.has_catalog and e.rows() == [] and e.preview_refs() == [] and not e.dirty
    with pytest.raises(PE.PaletteEditError):
        e.import_json("x.json")


def test_window_module_has_no_stray_syntax_and_uses_existing_keys():
    """La ventana no se puede importar sin GTK: se comprueba que parsea y que sus claves i18n existen en los tres idiomas."""
    import ast
    import re

    from afm import i18n

    src = os.path.join(os.path.dirname(PE.__file__), "palette_window.py")
    text = open(src, encoding="utf-8").read()
    ast.parse(text)
    keys = set(re.findall(r'tr\("(palette_panel\.[a-z_]+)"', text + open(PE.__file__, encoding="utf-8").read()))
    assert keys
    for lang in ("es", "en", "pt"):
        i18n.set_language(lang)
        for k in keys:
            assert i18n.tr(k, rol="r", n=1, piezas="p", ruta="x", ref="y") != k, (lang, k)
    i18n.set_language("es")
