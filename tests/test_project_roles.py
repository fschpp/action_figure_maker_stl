"""T44 · Figura base con color por roles y cambios de la paleta solo para esa figura (`ColorSpec.roles` / `paleta_override`).

Sobre el muñeco, con FBX sintético (sin figuras reales): el color de la figura sale de un paquete de roles (rojo / azul) y de la paleta de la
línea abierta. Los STL no cambian nunca por el color: se comparan por bytes.
"""
import hashlib
import json
import os
import shutil

import numpy as np
import pytest

from afm import cli
from afm.catalog import Catalog
from afm.colorpiece import PaletteRolesError
from afm.colorroles import build_package, propose
from afm.gui.session import ColorSourceError, Session
from afm.palette import Palette, PaletteError, Role
from afm.project import ColorSpec, Project, demo_project
from afm.testfigure import make_doll
from color_helpers import doll_params, doll_textured, write_doll_fbx

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "..", "catalogo", "ejemplo")
FIG = "muneco"
FLAT = (200, 200, 200)                                        # #C8C8C8: el plano de `colorbake`
RED, BLUE, GREEN = (200, 30, 30), (30, 60, 200), (0, 255, 0)  # #C81E1E, #1E3CC8, #00FF00


def palette() -> Palette:
    return Palette([Role("rojo", "Rojo", "#C81E1E", ["#C81E1E"]), Role("azul", "Azul", "#1E3CC8", ["#1E3CC8"])])


def _project(color: ColorSpec | None = None) -> Project:
    pr = demo_project()
    pr.params = doll_params()
    pr.color = color
    return pr


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def stl_hashes(out) -> dict:
    res = {}
    for base, _dirs, names in os.walk(out):
        for n in names:
            if n.endswith(".stl"):
                res[os.path.relpath(os.path.join(base, n), out)] = _sha(os.path.join(base, n))
    return res


def atlas_colors(keep: dict) -> set:
    """Colores únicos de los texels horneados (solo los cubiertos) de todas las piezas de la corrida."""
    out: set = set()
    for _unw, bk, _reg, _info in keep["colorizer"].pieces.values():
        out |= {tuple(int(x) for x in c) for c in np.unique(bk.atlas[bk.mask].reshape(-1, 3), axis=0)}
    return out


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("proyecto_roles")
    stl = str(d / "muneco.stl")
    make_doll().export(stl)
    pal = palette()
    tm = doll_textured()
    prop = propose(tm, pal, k=10)
    assign = {i: ("rojo" if c[0] > c[2] else "azul") for i, c in enumerate(prop.centers_rgb)}
    npz = build_package(tm, prop, assign, origen={"nombre": "muneco.fbx", "sha256": "ab" * 32}).save(str(d / "muneco.color.npz"))
    line = str(d / "linea")
    shutil.copytree(EXAMPLES, line, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    Catalog.open(line).set_palette(pal)
    no_blue = str(d / "linea_sin_azul")                       # una línea a la que le falta un rol del paquete
    shutil.copytree(line, no_blue)
    Catalog.open(no_blue).set_palette(Palette([pal.roles[0]]))
    no_pal = str(d / "linea_sin_paleta")
    shutil.copytree(EXAMPLES, no_pal, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    return {"dir": d, "stl": stl, "fbx": write_doll_fbx(d / "muneco_fbx.fbx"), "npz": npz, "line": line, "no_blue": no_blue, "no_pal": no_pal}


def _run(files, name, project=None, **kw):
    keep: dict = {}
    out = str(files["dir"] / name)
    kw.setdefault("catalog_root", files["line"])
    src = kw.pop("src", files["stl"])
    info = cli.run(src, project or _project(), out, FIG, progress=lambda *_: None, keep=keep, **kw)
    return info, keep, out


@pytest.fixture(scope="module")
def plain(files):
    """Roles pedidos con el diccionario `color` (como los flags del CLI), sin cambios: los colores de la línea."""
    return _run(files, "out_plain", color={"roles": files["npz"], "texel_mm": 0.4})


@pytest.fixture(scope="module")
def by_project(files):
    """Roles que recuerda el proyecto (ruta relativa a `project_dir`) con un cambio de color solo de esta figura."""
    spec = ColorSpec(roles="muneco.color.npz", texel_mm=0.4, paleta_override={"rojo": "#00ff00"})
    return _run(files, "out_project", _project(spec), project_dir=str(files["dir"]))


# ------------------------------------------------------------------------------------------ proyecto (sin motor)
def test_project_without_roles_roundtrips_byte_identical(tmp_path):
    spec = ColorSpec(source_path="guemes.fbx", source_sha256="ab" * 32, eps=0.3, texel_mm=0.15, fill="#C8C8C8", symmetric=True)
    assert spec.roles == "" and spec.paleta_override == {}
    assert spec.to_dict() == {"source_path": "guemes.fbx", "source_sha256": "ab" * 32, "eps": 0.3, "texel_mm": 0.15, "fill": "#C8C8C8",
                              "symmetric": True}                  # ni `roles` ni `paleta_override`: el mismo diccionario que antes de T44
    pr = demo_project()
    pr.color = spec
    p = tmp_path / "a.proyecto"
    pr.save(str(p))
    assert p.read_text() == json.dumps({**pr._to_dict_base(), "color": spec.to_dict()}, indent=2)
    again = Project.load(str(p))
    assert again.color == spec
    out = tmp_path / "b.proyecto"
    again.save(str(out))
    assert out.read_bytes() == p.read_bytes()


def test_project_with_roles_and_override_roundtrip(tmp_path):
    pr = demo_project()
    pr.color = ColorSpec(roles="muneco.color.npz", paleta_override={"rojo": "#00FF00"}, texel_mm=0.3)
    p = tmp_path / "r.proyecto"
    pr.save(str(p))
    d = json.load(open(p))
    assert d["color"]["roles"] == "muneco.color.npz" and d["color"]["paleta_override"] == {"rojo": "#00FF00"} and d["version"] == 1
    back = Project.load(str(p))
    assert back.color == pr.color and back.to_dict() == pr.to_dict()
    low = Project.from_dict({**pr.to_dict(), "color": {**d["color"], "paleta_override": {"rojo": "#00ff0a"}}})
    assert low.color.paleta_override == {"rojo": "#00FF0A"}       # el hex se guarda en mayúscula, como en la paleta
    for bad in ({"rojo": "verde"}, {"rojo": "#12345"}, ["rojo"]):
        with pytest.raises(PaletteError):
            Project.from_dict({**pr.to_dict(), "color": {**d["color"], "paleta_override": bad}})


def test_old_projects_load_and_save_without_roles(tmp_path):
    import glob

    fs = glob.glob(os.path.join(HERE, "..", "examples", "*.proyecto")) + glob.glob(os.path.join(HERE, "..", "catalogo", "**", "*.proyecto"), recursive=True)
    assert fs
    for f in fs:
        pr = Project.load(f)
        assert pr.color is None or (pr.color.roles == "" and pr.color.paleta_override == {}), f
        out = tmp_path / "re.proyecto"
        pr.save(str(out))
        assert Project.load(str(out)).to_dict() == pr.to_dict(), f
        text = json.dumps(pr.to_dict())
        assert "paleta_override" not in text and '"roles"' not in text, f


# ------------------------------------------------------------------------------------------ motor
def test_base_figure_with_roles_has_only_palette_colors(plain, files):
    info, keep, out = plain
    col = info["color"]
    pal = palette()
    assert col["modo"] == "roles" and sorted(col["roles"]) == ["azul", "rojo"]
    assert col["fuente"].endswith("muneco.color.npz") and col["alineacion"]["ok"] and col["salida"]["sin_origen"] == []
    assert col["paleta"]["hash"] == pal.digest() and col["paleta"]["roles"] == {"rojo": "#C81E1E", "azul": "#1E3CC8"}
    assert "cambios" not in col["paleta"]
    colors = atlas_colors(keep)
    assert colors <= {RED, BLUE, FLAT}, colors                    # solo la paleta (y el plano de relleno)
    assert RED in colors and BLUE in colors
    rep = json.load(open(os.path.join(out, col["salida"]["informe"])))
    assert rep["paleta"] == col["paleta"]                         # la paleta usada queda en el informe de color
    json.dumps(info, default=float)                               # R5: serializable


def test_project_roles_resolve_relative_and_apply_override(by_project, plain, files):
    info, keep, out = by_project
    col = info["color"]
    assert col["modo"] == "roles" and col["fuente"].endswith("muneco.color.npz")
    assert col["paleta"]["cambios"] == {"rojo": "#00FF00"} and col["paleta"]["roles"]["rojo"] == "#00FF00"
    assert col["paleta"]["hash"] == palette().with_overrides({"rojo": "#00FF00"}).digest() != plain[0]["color"]["paleta"]["hash"]
    colors = atlas_colors(keep)
    assert GREEN in colors and RED not in colors and BLUE in colors and colors <= {GREEN, BLUE, FLAT}
    saved = Project.load(os.path.join(out, f"{FIG}.proyecto"))    # `run` guarda el proyecto con lo que recuerda
    assert saved.color.roles == "muneco.color.npz" and saved.color.paleta_override == {"rojo": "#00FF00"}


def test_override_changes_only_that_figure(plain, by_project, files):
    line_json = os.path.join(files["line"], "paleta.json")
    before = _sha(line_json)
    third, keep3, out3 = _run(files, "out_third", color={"roles": files["npz"], "texel_mm": 0.4})    # otra figura de la misma línea, sin cambios
    assert _sha(line_json) == before                              # la paleta de la línea no se tocó (ni por una ni por otra)
    assert RED in atlas_colors(keep3) and GREEN not in atlas_colors(keep3)
    assert third["color"]["paleta"] == plain[0]["color"]["paleta"]
    assert Palette.from_dict(json.load(open(line_json))).hex_of("rojo") == "#C81E1E"
    assert stl_hashes(plain[2]) == stl_hashes(by_project[2]) == stl_hashes(out3)     # los STL no cambian nunca por el color
    assert stl_hashes(plain[2])


def test_cli_override_wins_over_project_override(files):
    spec = ColorSpec(roles="muneco.color.npz", texel_mm=0.4, paleta_override={"rojo": "#00FF00", "azul": "#FFFF00"})
    from afm.cli import _with_roles

    c = _with_roles({"paleta_override": {"rojo": "#112233"}}, _project(spec), files["line"], str(files["dir"]))
    assert c["paleta_override"] == {"rojo": "#112233", "azul": "#FFFF00"}    # lo pedido a mano gana, el resto sigue del proyecto
    assert c["roles"] == os.path.join(str(files["dir"]), "muneco.color.npz") and c["linea"] and c["palette"].hex_of("rojo") == "#C81E1E"
    assert spec.paleta_override == {"rojo": "#00FF00", "azul": "#FFFF00"}    # el proyecto no se toca


def test_without_roles_color_dict_is_returned_untouched(files):
    from afm.cli import _with_roles

    c = {"fbx": "x.fbx", "texel_mm": 0.3}
    assert _with_roles(c, _project(), None) is c and _with_roles(None, _project(), None) is None
    assert _with_roles(c, _project(ColorSpec(roles="muneco.color.npz")), files["line"]) is c          # un --color-desde explícito gana sobre los roles
    with pytest.raises(PaletteRolesError, match="roles"):
        _with_roles({"paleta_override": {"rojo": "#112233"}}, _project(), files["line"])         # cambios sin roles: se avisa, no se ignoran


def test_mode_a_with_roles(files):
    info, keep, _out = _run(files, "out_modo_a", _project(ColorSpec(roles=files["npz"], texel_mm=0.4)), src=files["fbx"])
    col = info["color"]
    assert col["alineacion"] == {"modo": "A", "ok": True} and col["modo"] == "roles" and col["salida"]["sin_origen"] == []
    assert atlas_colors(keep) <= {RED, BLUE, FLAT} and RED in atlas_colors(keep)


def test_mode_a_and_mode_b_still_work_with_rgb(files):
    ia, keep_a, _ = _run(files, "out_rgb_a", src=files["fbx"], color={"texel_mm": 0.4}, catalog_root=None)
    ib, keep_b, _ = _run(files, "out_rgb_b", color={"fbx": files["fbx"], "texel_mm": 0.4}, catalog_root=None)
    assert ia["color"]["alineacion"] == {"modo": "A", "ok": True} and ib["color"]["alineacion"]["ok"]
    for i, k in ((ia, keep_a), (ib, keep_b)):
        assert "paleta" not in i["color"] and "modo" not in i["color"] and "paleta" not in i["color"]["salida"]
        assert k["colorizer"].palette_report is None and i["color"]["salida"]["sin_origen"] == []
        assert len(atlas_colors(k)) > 3                           # colores del FBX, no los de una paleta


def test_sin_color_ignores_roles(files):
    spec = ColorSpec(roles="muneco.color.npz")
    info, _keep, out = _run(files, "out_sin_color", _project(spec), project_dir=str(files["dir"]), sin_color=True)
    assert "color" not in info and not os.path.exists(os.path.join(out, "piezas_color"))


# ------------------------------------------------------------------------------------------ errores claros, antes de construir
def test_missing_package_is_a_clear_error_like_missing_fbx(files, capsys):
    spec = ColorSpec(roles="no_esta.color.npz")
    out = files["dir"] / "out_falta"
    with pytest.raises(PaletteRolesError, match="no_esta.color.npz"):
        cli.run(files["stl"], _project(spec), str(out), FIG, progress=lambda *_: None, catalog_root=files["line"], project_dir=str(files["dir"]))
    assert not out.exists()                                       # no se construyó ni se exportó nada
    argv = ["build", files["stl"], str(files["dir"] / "p.proyecto"), "--out", str(files["dir"] / "out_falta_cli"), "--catalogo", files["line"]]
    _project().save(argv[2])
    with pytest.raises(SystemExit) as ex:                         # como `--color-desde` con un archivo que no existe
        cli.main(argv + ["--color-roles", str(files["dir"] / "falta.color.npz")])
    assert ex.value.code == 2 and "falta.color.npz" in capsys.readouterr().err
    bad = files["dir"] / "roto.color.npz"
    bad.write_bytes(b"no es un npz")
    with pytest.raises(PaletteRolesError, match="roto.color.npz"):
        cli.run(files["stl"], _project(ColorSpec(roles=str(bad))), str(out), FIG, progress=lambda *_: None, catalog_root=files["line"])
    assert not out.exists()


def test_missing_roles_in_line_palette_list_them_and_stop(files):
    out = files["dir"] / "out_faltan"
    spec = ColorSpec(roles=files["npz"])
    with pytest.raises(PaletteRolesError) as ex:
        cli.run(files["stl"], _project(spec), str(out), FIG, progress=lambda *_: None, catalog_root=files["no_blue"])
    assert "roles que usa la figura: azul." in str(ex.value)         # lista solo los que faltan (rojo sí está)
    assert not out.exists()


def test_no_line_or_no_palette_is_a_clear_error(files, monkeypatch):
    monkeypatch.delenv("AFM_CATALOGO", raising=False)
    spec = ColorSpec(roles=files["npz"])
    for root, texto in ((None, "--catalogo"), (files["no_pal"], "paleta.json"), (str(files["dir"] / "no_hay"), "paleta")):
        out = files["dir"] / "out_sin_linea"
        with pytest.raises(PaletteRolesError, match=texto):
            cli.run(files["stl"], _project(spec), str(out), FIG, progress=lambda *_: None, catalog_root=root)
        assert not out.exists()


def test_override_of_a_role_the_palette_lacks_is_an_error(files):
    spec = ColorSpec(roles=files["npz"], paleta_override={"verde": "#00FF00"})
    with pytest.raises(PaletteRolesError, match="verde"):
        cli.run(files["stl"], _project(spec), str(files["dir"] / "out_override_malo"), FIG, progress=lambda *_: None, catalog_root=files["line"])


def test_cli_flags_are_validated(files, capsys):
    base = ["build", files["stl"], str(files["dir"] / "p2.proyecto"), "--out", str(files["dir"] / "out_flags"), "--catalogo", files["line"]]
    _project().save(base[2])
    for extra, texto in ((["--color-roles", files["npz"], "--color-desde", files["fbx"]], "--color-desde"),
                         (["--color-roles", files["npz"], "--color-cambio", "rojo=verde"], "#RRGGBB"),
                         (["--color-roles", files["npz"], "--color-cambio", "rojo"], "ROL=#RRGGBB")):
        with pytest.raises(SystemExit) as ex:
            cli.main(base + extra)
        assert ex.value.code == 2 and texto in capsys.readouterr().err, extra
    assert cli.main(base + ["--color-cambio", "rojo=#00FF00"]) == 2                  # cambios sin roles en ningún lado
    assert "ERROR (color por roles)" in capsys.readouterr().err
    assert cli.main(base[:-1] + [files["no_blue"], "--color-roles", files["npz"]]) == 2   # le falta un rol a la paleta
    assert "azul" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------ sesión (sin GTK)
def _session(files, spec) -> Session:
    s = Session(_project(spec))
    s.load(files["stl"])
    return s


def test_session_exports_with_roles_and_the_open_line_palette(files):
    spec = ColorSpec(roles=files["npz"], texel_mm=0.4, paleta_override={"azul": "#FFFF00"})
    s = _session(files, spec)
    s.open_catalog(files["line"])
    assert s.resolve_color_source() == files["npz"] == s.resolve_roles_package()      # el panel de color no pide un FBX que no hace falta
    cz, info = s.make_colorizer()
    assert cz is not None and info["modo"] == "roles" and info["paleta"]["cambios"] == {"azul": "#FFFF00"}
    assert cz.palette_report == info["paleta"] and info["paleta"]["roles"]["azul"] == "#FFFF00"
    assert Palette.from_dict(json.load(open(os.path.join(files["line"], "paleta.json")))).hex_of("azul") == "#1E3CC8"


def test_session_errors_are_clear(files):
    s = _session(files, ColorSpec(roles=files["npz"]))
    with pytest.raises(PaletteRolesError, match="--catalogo"):                      # sin línea abierta
        s.make_colorizer()
    s.open_catalog(files["no_blue"])
    with pytest.raises(PaletteRolesError, match="azul"):
        s.make_colorizer()
    miss = _session(files, ColorSpec(roles="falta.color.npz"))
    with pytest.raises(ColorSourceError, match="falta.color.npz"):
        miss.resolve_roles_package()
    with pytest.raises(ColorSourceError, match="falta.color.npz"):
        miss.resolve_color_source()
    with pytest.raises(ColorSourceError):                                           # sin roles no hay paquete que resolver
        _session(files, ColorSpec(source_path=files["fbx"])).resolve_roles_package()


def test_session_relative_package_is_found_next_to_the_project(files):
    s = _session(files, ColorSpec(roles="muneco.color.npz"))
    s.project_path = str(files["dir"] / "figura.proyecto")
    assert s.resolve_roles_package() == os.path.join(str(files["dir"]), "muneco.color.npz")


def test_session_preview_with_roles_says_it_is_not_available_yet(files):
    s = _session(files, ColorSpec(roles=files["npz"]))
    with pytest.raises(ColorSourceError, match="roles"):
        s.preview_texture()


def test_session_rgb_color_unchanged(files):
    s = _session(files, ColorSpec.from_file(files["fbx"], texel_mm=0.4))
    assert s.resolve_color_source() == files["fbx"]
    cz, info = s.make_colorizer()
    assert cz is not None and "modo" not in info and "paleta" not in info and cz.palette_report is None
