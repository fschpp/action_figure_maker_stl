"""T48 · «Armar figura desde catálogo»: paleta de la línea, cambios de color solo de esta figura y recolorear (`FigureBuilder`, sin GTK).

La ventana (`figure_window.py`) no se prueba acá (no hay GTK en el entorno de pruebas, R9): todo lo que decide está en `FigureBuilder`.
Sobre el muñeco del catálogo de ejemplo con la paleta rojo/azul y la cabeza `@2` con roles (los helpers vienen de T43).
"""
import hashlib
import json
import os
import shutil

import pytest

from afm import cli
from afm.catalog import Catalog
from afm.colorpiece import PaletteRolesError
from afm.gui import figure_builder as FB
from afm.palette import Palette, Role

from test_color_catalog_palette import HEAD_ID, ROOT, base_line, line, palette, png_colors  # noqa: F401  (fixtures y ayudas de T43)

HEAD = f"{HEAD_ID}@2"
NEW = "#112233"
NEW_RGB = (0x11, 0x22, 0x33)
FLAT = (200, 200, 200)
PIECES = (("torso", "torso-muneco-01"), ("cabeza", HEAD), ("brazo", "brazo-muneco-01"), ("pierna", "pierna-muneco-01"), ("mano", "mano-muneco-01"))


def sha(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def stls(out) -> dict:
    res = {}
    for base, _d, files in os.walk(out):
        for f in files:
            if f.endswith(".stl"):
                res[os.path.relpath(os.path.join(base, f), out)] = sha(os.path.join(base, f))
    return res


def make_builder(root, name="muneco-color") -> FB.FigureBuilder:
    b = FB.FigureBuilder(root, name)
    for place, ref in PIECES:
        b.set_choice(place, ref)
    return b


def informe(out, name="muneco-color"):
    with open(os.path.join(out, f"{name}_color_informe.json"), encoding="utf-8") as fh:
        return json.load(fh)


def head_png_colors(out, name="muneco-color"):
    return png_colors(out, informe(out, name)["piezas"]["cabeza"]["png"])


@pytest.fixture(scope="module")
def built(tmp_path_factory, base_line):
    """Un armado completo con color por roles (sin cambios), para las pruebas que no lo modifican."""
    d = str(tmp_path_factory.mktemp("fig") / "linea")
    shutil.copytree(base_line, d)
    b = make_builder(d)
    out = str(tmp_path_factory.mktemp("out") / "salida")
    info = b.generate(out, progress=lambda *_: None)
    return b, d, out, info


# ------------------------------------------------------------------ pruebas de la ficha de T48
def test_override_does_not_touch_line_palette(line):
    """Un cambio de color es de la figura: `paleta.json` queda byte a byte igual y la receta lleva el cambio."""
    b = make_builder(line)
    before = sha(os.path.join(line, "paleta.json"))
    b.set_override("rojo", NEW)
    assert sha(os.path.join(line, "paleta.json")) == before
    assert Catalog.open(line).palette().hex_of("rojo") == "#C81E1E"
    rows = {r["id"]: r for r in b.palette_rows()}
    assert rows["rojo"]["hex"] == NEW and rows["rojo"]["hex_linea"] == "#C81E1E" and rows["rojo"]["cambiado"]
    assert not rows["azul"]["cambiado"] and rows["azul"]["hex"] == "#1E3CC8"
    assert rows["rojo"]["piezas"] == [HEAD] and rows["azul"]["piezas"] == [HEAD]            # la ficha dice qué piezas usan el rol
    assert b.recipe().to_dict()["paleta_override"] == {"rojo": NEW}
    assert b.recipe().to_dict()["ajustes"] == make_builder(line).recipe().to_dict()["ajustes"]          # el color no entra en `ajustes`
    b.set_override("rojo", "#c81e1e")                                                       # volver al de la línea (en minúscula) quita el cambio
    assert b.palette_override == {} and "paleta_override" not in b.recipe().to_dict()
    b.set_override("azul", "#abcdef")
    b.clear_override("azul")
    assert b.palette_override == {}


def test_recolor_does_not_rebuild_geometry(tmp_path, base_line, monkeypatch):
    """«Recolorear» rehace solo el color: ni `assemble` ni los STL (hash por hash) se tocan, y el cambio llega al PNG y al informe."""
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    b = make_builder(d)
    out = str(tmp_path / "salida")
    b.generate(out, progress=lambda *_: None)
    assert b.can_recolor() and not b.overrides_pending()
    assert (200, 30, 30) in head_png_colors(out) and NEW_RGB not in head_png_colors(out)
    before = stls(out)
    assert before
    b.set_override("rojo", NEW)
    assert b.overrides_pending() and b.can_recolor()                                        # cambiar el color no es cambiar la geometría
    import afm.recipe

    def no_rebuild(*a, **k):
        raise AssertionError("recolorear no vuelve a armar")

    monkeypatch.setattr(afm.recipe, "assemble", no_rebuild)
    res = b.recolor()
    assert stls(out) == before                                                              # los STL no cambiaron un byte
    got = head_png_colors(out)
    assert NEW_RGB in got and (200, 30, 30) not in got and (30, 60, 200) in got            # el rojo cambió, el azul no
    assert res["glb"] and os.path.exists(res["glb"]) and "cabeza" in res["piezas"]
    rep = informe(out)["paleta"]
    assert rep["cambios"] == {"rojo": NEW} and rep["roles"]["rojo"] == NEW and rep["roles"]["azul"] == "#1E3CC8"
    main = json.load(open(os.path.join(out, "muneco-color_informe.json"), encoding="utf-8"))
    assert main["color"]["paleta"] == rep                                                   # el informe del armado también quedó al día
    assert json.load(open(os.path.join(out, "muneco-color.receta.json"), encoding="utf-8"))["paleta_override"] == {"rojo": NEW}
    assert not b.overrides_pending()
    assert sha(os.path.join(d, "paleta.json")) == sha(os.path.join(base_line, "paleta.json"))
    b.clear_override()                                                                      # y se puede volver atrás recoloreando otra vez
    assert b.overrides_pending()
    b.recolor()
    assert (200, 30, 30) in head_png_colors(out) and NEW_RGB not in head_png_colors(out) and stls(out) == before


def test_missing_roles_listed_before_export(tmp_path, base_line, monkeypatch):
    """A la paleta le falta un rol que usa una pieza: el informe previo lo lista y el armado se frena antes de armar (no se inventa color)."""
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    cat = Catalog.open(d)
    cat.set_palette(Palette([r for r in cat.palette().roles if r.id != "azul"]))
    b = make_builder(d)
    rep = b.color_report()
    assert rep["faltan"] == {"azul": [HEAD]}
    assert set(rep["sin_color"]) == {"torso-muneco-01@1", "brazo-muneco-01@1", "pierna-muneco-01@1", "mano-muneco-01@1"}
    lines = b.color_report_lines()
    assert any(l.startswith("✗") and "azul" in l and HEAD in l for l in lines) and any("sin color propio" in l for l in lines)
    import afm.cli

    monkeypatch.setattr(afm.cli, "run_armar", lambda *a, **k: pytest.fail("no tiene que llegar a armar"))
    with pytest.raises(ValueError, match="azul") as exc:
        b.generate(str(tmp_path / "salida"), progress=lambda *_: None)
    assert HEAD in str(exc.value) and "Paleta de la línea" in str(exc.value)
    assert not os.path.exists(str(tmp_path / "salida"))                                     # no dejó nada a medias


def test_figure_without_palette_behaves_as_before(tmp_path):
    """Un catálogo sin `paleta.json` y piezas sin color: nada nuevo aparece (ni en la receta ni en el armado) y no hay qué recolorear."""
    d = str(tmp_path / "ejemplo")
    shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    assert not os.path.exists(os.path.join(d, "paleta.json"))
    b = FB.FigureBuilder(d, "muneco-plano")                                                 # el ejemplo tal cual: sin la cabeza @2 con roles
    for place, ref in PIECES:
        b.set_choice(place, "cabeza-muneco-01" if place == "cabeza" else ref)
    assert b.palette_rows() == [] and b.effective_overrides() == {}
    rep = b.color_report()
    assert rep["faltan"] == {} and rep["con_roles"] == [] and rep["rgb"] == []
    assert "paleta_override" not in b.recipe().to_dict()
    assert not b.can_recolor() and not b.overrides_pending()
    with pytest.raises(ValueError, match="armada"):
        b.recolor()
    with pytest.raises(ValueError, match="no tiene el rol"):                                # sin paleta no hay roles que cambiar
        b.set_override("rojo", NEW)
    out = str(tmp_path / "salida")
    info = b.generate(out, progress=lambda *_: None)
    assert "color" not in info and not os.path.exists(os.path.join(out, "muneco-plano_color_informe.json"))
    assert not b.can_recolor()                                                              # no hubo color: no hay `Colorizer`
    with pytest.raises(ValueError, match="armada"):
        b.recolor()


# ------------------------------------------------------------------ extras
def test_set_override_refuses_unknown_role_and_bad_hex(line):
    b = make_builder(line)
    with pytest.raises(ValueError, match="no tiene el rol «verde»"):
        b.set_override("verde", NEW)
    for bad in ("112233", "#12345", "rojo", "#GGGGGG", None):
        with pytest.raises(ValueError, match="#RRGGBB"):
            b.set_override("rojo", bad)
    assert b.palette_override == {}


def test_recipe_roundtrip_keeps_overrides_and_compat_hash_ignores_them(line, tmp_path):
    """El cambio de color viaja en la receta (`paleta_override`) pero no en `ajustes`: el hash de compatibilidad (geometría) no se mueve."""
    from afm.recipe import Recipe

    b = make_builder(line)
    b.set_override("rojo", NEW.lower())
    path = b.save_recipe()
    saved = json.load(open(path, encoding="utf-8"))
    assert saved["paleta_override"] == {"rojo": NEW} and "paleta_override" not in saved["ajustes"]
    b2 = FB.FigureBuilder(line)
    assert b2.load_recipe(path) == [] and b2.palette_override == {"rojo": NEW}
    assert Recipe.from_dict(saved).ajustes == make_builder(line).recipe().ajustes
    saved["paleta_override"] = {"rojo": "azul"}
    with pytest.raises(ValueError, match="#RRGGBB"):
        Recipe.from_dict(saved)
    saved["paleta_override"] = {"verde": NEW, "azul": "#010203"}                            # un rol que la línea ya no tiene: se descarta con aviso
    json.dump(saved, open(path, "w", encoding="utf-8"))
    b3 = FB.FigureBuilder(line)
    warns = b3.load_recipe(path)
    assert b3.palette_override == {"azul": "#010203"} and any("verde" in w for w in warns)


def test_recolor_is_refused_when_what_is_chosen_changed(built):
    b, _d, _out, _info = built
    assert b.can_recolor()
    ref = b.choices["pierna"]
    b.set_choice("pierna", None)
    try:
        assert not b.can_recolor()
        b.palette_override["rojo"] = NEW
        with pytest.raises(ValueError, match="armala de nuevo"):
            b.recolor()
    finally:
        b.set_choice("pierna", ref)
        b.palette_override.clear()
    assert b.can_recolor()


def test_recolor_equals_building_again_with_the_override(tmp_path, base_line):
    """Recolorear y armar de nuevo con el mismo cambio dan los mismos PNG, píxel por píxel."""
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    b = make_builder(d)
    out = str(tmp_path / "a")
    b.generate(out, progress=lambda *_: None)
    b.set_override("azul", "#33AA55")
    b.recolor()
    again = make_builder(d, "muneco-color")
    again.set_override("azul", "#33AA55")
    out2 = str(tmp_path / "b")
    again.generate(out2, progress=lambda *_: None)
    pngs = [v["png"] for v in informe(out)["piezas"].values()]
    assert pngs and pngs == [v["png"] for v in informe(out2)["piezas"].values()]
    for rel in pngs:
        assert sha(os.path.join(out, rel)) == sha(os.path.join(out2, rel)), rel
    assert stls(out) == stls(out2)


def test_run_armar_and_cli_color_cambio(tmp_path, line):
    """`afm armar --color-cambio ROL=#HEX`: mismo cambio que en la ventana; un rol que no existe frena el armado con el motivo."""
    b = make_builder(line, "muneco-cli")
    rec = b.save_recipe()
    a = str(tmp_path / "a")
    base = cli.run_armar(rec, a, "muneco-cli", line, progress=lambda *_: None)
    assert "cambios" not in base["color"]["paleta"]
    c = str(tmp_path / "c")
    over = cli.run_armar(rec, c, "muneco-cli", line, progress=lambda *_: None, color_overrides={"rojo": NEW.lower()})
    assert over["color"]["paleta"]["cambios"] == {"rojo": NEW} and over["color"]["paleta"]["roles"]["rojo"] == NEW
    assert over["color"]["paleta"]["hash"] != base["color"]["paleta"]["hash"]
    assert stls(a) == stls(c)                                                               # el color nunca cambia los STL
    with pytest.raises(PaletteRolesError, match="verde"):
        cli.run_armar(rec, str(tmp_path / "d"), "muneco-cli", line, progress=lambda *_: None, color_overrides={"verde": NEW})
    assert cli.main(["armar", line, rec, "--out", str(tmp_path / "e"), "--color-cambio", f"rojo={NEW}"]) in (0, 1)
    with pytest.raises(SystemExit):
        cli.main(["armar", line, rec, "--out", str(tmp_path / "f"), "--color-cambio", "rojo=azul"])
