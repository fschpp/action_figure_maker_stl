"""T43 · Armado desde el catálogo con la paleta de la línea (`catalog_color_sources`, `Colorizer`, `afm armar`).

Sobre el muñeco y las piezas del catálogo de ejemplo, con FBX sintéticos (sin figuras reales): rojo arriba y azul abajo, convertidos a
dos roles (`rojo`, `azul`) de la paleta de la línea. Los STL no cambian nunca por el color: se comparan por bytes.
"""
import hashlib
import json
import os
import shutil

import numpy as np
import pytest
import trimesh
from scipy.spatial.transform import Rotation

from afm import cli, parts as P
from afm.catalog import Catalog
from afm.colorpiece import PaletteRolesError, catalog_color_sources
from afm.colorroles import RolesPackage, build_package, propose
from afm.palette import Palette, Role
from afm.texmesh import TexturedMesh
from color_helpers import png_bytes, write_textured_fbx

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
RECETA = os.path.join(ROOT, "recetas", "muneco.receta.json")
HEAD_ID, ARM_ID, LEG_ID = "cabeza-muneco-01", "brazo-muneco-01", "pierna-muneco-01"
TOP, BOTTOM, FLAT = (200, 30, 30), (30, 60, 200), (200, 200, 200)           # rojo y azul de la paleta; #C8C8C8 = plano de `colorbake`


def textured_from(mesh, M=None) -> TexturedMesh:
    """FBX sintético de `mesh`: rojo en la mitad alta, azul en la baja; con `M` (4×4) los vértices van a otro marco."""
    V = np.asarray(mesh.vertices, float)
    F = np.asarray(mesh.faces, np.int32)
    zc = V[F][:, :, 2].mean(1)
    mid = (V[:, 2].min() + V[:, 2].max()) / 2
    img = np.zeros((16, 16, 3), np.uint8)
    img[:8] = TOP
    img[8:] = BOTTOM
    uv = np.where((zc > mid)[:, None], [0.5, 0.75], [0.5, 0.25]).astype(np.float32)
    if M is not None:
        V = V @ np.asarray(M, float)[:3, :3].T + np.asarray(M, float)[:3, 3]
    return TexturedMesh(V, F, np.repeat(uv[:, None, :], 3, axis=1), {"base_color": png_bytes(img)}, {"avisos": []})


def odd_frame():
    """Escala ×1,3, giro de 30° sobre Z y traslado: el FBX no está en el marco del STL."""
    M = np.eye(4)
    M[:3, :3] = 1.3 * Rotation.from_euler("z", 30, degrees=True).as_matrix()
    M[:3, 3] = [7.0, -4.0, 11.0]
    return M


def palette(extra=()):
    return Palette([Role("rojo", "Rojo", "#C81E1E", ["#C81E1E"]), Role("azul", "Azul", "#1E3CC8", ["#1E3CC8"])]
                   + [Role(r, r, "#808080") for r in extra])


def package_of(tm, pal) -> RolesPackage:
    prop = propose(tm, pal, k=10)
    assign = {i: ("rojo" if c[0] > c[2] else "azul") for i, c in enumerate(prop.centers_rgb)}
    return build_package(tm, prop, assign, origen={"nombre": "origen.fbx", "sha256": "ab" * 32})


def new_meta(meta):
    return P.PieceMeta.from_dict(meta.to_dict())


def with_roles(cat, piece_id, extra_roles=(), similarity=None):
    """Guarda la versión siguiente de `piece_id` con roles rojo/azul (FBX sintético en el marco raro, o en el de la pieza si hay `similarity`)."""
    meta, mesh = cat.load(piece_id)
    tm = textured_from(mesh, None if similarity is not None else odd_frame())
    pkg = package_of(tm, cat.palette())
    if extra_roles:
        pkg = RolesPackage(pkg.vertices, pkg.faces, pkg.corner_uv, pkg.labels, list(pkg.roles) + list(extra_roles), pkg.origen)
    saved, _ = cat.add_piece(mesh, new_meta(meta), new_version=True, color_roles=pkg, similarity=similarity)
    return saved


COLORIZERS: dict = {}                                    # carpeta de salida -> el `Colorizer` que escribió ahí (para mirar los texels horneados)


def arm(line, folder, lugares=None, scales=None, name="muneco"):
    """`run_armar` de la receta del muñeco con las piezas pedidas (`lugares`: {lugar: ref}); devuelve (info, carpeta de salida).

    Guarda en `COLORIZERS[carpeta]` el `Colorizer` usado (si hubo color): el PNG entero tiene fondo vacío (0,0,0) fuera de las islas,
    y la comprobación «solo colores de la paleta» se hace sobre los texels cubiertos (`bk.mask`)."""
    from afm import colorexport

    r = json.load(open(RECETA))
    r["lugares"].update(lugares or {})
    if scales:
        r["ajustes"]["escalas"] = scales
    d = str(folder)
    os.makedirs(d, exist_ok=True)
    rec = os.path.join(d, "r.receta.json")
    json.dump(r, open(rec, "w"))
    out = os.path.join(d, "fig")
    orig = colorexport.Colorizer.write

    def spy(self, *a, **k):
        COLORIZERS[out] = self
        return orig(self, *a, **k)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(colorexport.Colorizer, "write", spy)
        info = cli.run_armar(rec, out, name, line, progress=lambda *_: None)
    return info, out


def stl_hashes(out):
    res = {}
    for base, _dirs, files in os.walk(out):
        for f in files:
            if f.endswith(".stl"):
                res[os.path.relpath(os.path.join(base, f), out)] = hashlib.sha256(open(os.path.join(base, f), "rb").read()).hexdigest()
    return res


def atlas_colors(out, name):
    """Colores únicos del atlas horneado de la pieza `name` de la figura armada en `out` (solo los texels cubiertos)."""
    _unw, bk, _reg, _info = COLORIZERS[out].pieces[name]
    return {tuple(int(x) for x in c) for c in np.unique(bk.atlas[bk.mask].reshape(-1, 3), axis=0)}


def png_colors(out, relpath):
    from PIL import Image

    arr = np.asarray(Image.open(os.path.join(out, relpath)).convert("RGB")).reshape(-1, 3)
    return {tuple(int(x) for x in c) for c in np.unique(arr, axis=0)}


@pytest.fixture(scope="module")
def base_line(tmp_path_factory):
    """Copia del catálogo de ejemplo con la paleta rojo/azul y la cabeza `@2` con roles."""
    d = str(tmp_path_factory.mktemp("linea") / "linea")
    shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    cat = Catalog.open(d)
    cat.set_palette(palette())
    with_roles(cat, HEAD_ID)
    return d


@pytest.fixture()
def line(tmp_path, base_line):
    """Copia propia de `base_line` para las pruebas que la modifican."""
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    return d


@pytest.fixture(scope="module")
def armed(tmp_path_factory, base_line):
    info, out = arm(base_line, tmp_path_factory.mktemp("armada"), {"cabeza": f"{HEAD_ID}@2"})
    return info, out


def informe(out, name="muneco"):
    return json.load(open(os.path.join(out, f"{name}_color_informe.json"), encoding="utf-8"))


# ------------------------------------------------------------------ pruebas de la ficha de T43
def test_armed_figure_pieces_have_only_palette_colors(armed):
    """El atlas de la cabeza solo tiene colores de la paleta (o el plano): el bake copia el color del vecino, no promedia."""
    info, out = armed
    col = info["color"]
    assert col["piezas"]["cabeza"]["modo"] == "roles" and not col["omitidas"]
    cab = informe(out)["piezas"]["cabeza"]
    assert cab["fuente"] == "pieza" and cab["estado"] != "sin_origen" and cab["pct_origen"] >= 85, cab
    allowed = {TOP, BOTTOM, FLAT}
    got = atlas_colors(out, "cabeza")
    assert {TOP, BOTTOM} <= got and got <= allowed, sorted(got - allowed)       # llegaron los dos roles, y nada más que la paleta y el plano
    assert png_colors(out, cab["png"]) <= allowed | {(0, 0, 0)}                  # el archivo: lo mismo, más el fondo vacío del atlas
    flat = [k for k, v in informe(out)["piezas"].items() if k != "cabeza"]
    assert flat and all(informe(out)["piezas"][k]["fuente"] == "plano" for k in flat)


def test_changing_palette_hex_changes_atlas_color_and_not_stl_bytes(tmp_path, line):
    """Cambiar el hex de un rol en la paleta se toma en el armado siguiente, sin tocar la pieza: los STL salen idénticos byte a byte."""
    lugares = {"cabeza": f"{HEAD_ID}@2"}
    info1, out1 = arm(line, tmp_path / "a", lugares)
    cat = Catalog.open(line)
    piece_before = P.sha256_file(cat.color_file(f"{HEAD_ID}@2"))
    pal = cat.palette()
    role = pal.get("rojo")
    cat.set_palette(Palette([Role("rojo", "Rojo", "#00AA00", role.anclas)] + [r for r in pal.roles if r.id != "rojo"]))
    info2, out2 = arm(line, tmp_path / "b", lugares)
    c1, c2 = atlas_colors(out1, "cabeza"), atlas_colors(out2, "cabeza")
    assert TOP in c1 and TOP not in c2 and (0, 170, 0) in c2 and BOTTOM in c2          # el rojo pasó a verde; el azul quedó igual
    assert stl_hashes(out1) == stl_hashes(out2) and stl_hashes(out1)                    # los STL no cambian un byte
    assert P.sha256_file(cat.color_file(f"{HEAD_ID}@2")) == piece_before               # y la pieza del catálogo tampoco
    assert informe(out1)["paleta"]["hash"] != informe(out2)["paleta"]["hash"]
    plain, out0 = arm(line, tmp_path / "c")                                             # sin ninguna pieza con color
    assert stl_hashes(out0) == stl_hashes(out1) and "color" not in plain               # los STL son idénticos con y sin color


def test_missing_role_stops_before_registration_with_list(tmp_path, line, monkeypatch, capsys):
    cat = Catalog.open(line)
    cat.set_palette(Palette([Role("rojo", "Rojo", "#C81E1E")]))                         # falta «azul»
    calls = []
    monkeypatch.setattr("afm.colorreg.register_piece", lambda *a, **k: calls.append(a))
    with pytest.raises(PaletteRolesError, match="azul") as exc:
        arm(line, tmp_path / "a", {"cabeza": f"{HEAD_ID}@2"})
    assert f"{HEAD_ID}@2" in str(exc.value) and "rojo (" not in str(exc.value)          # lista el rol que falta y la pieza que lo usa
    assert calls == [] and not os.path.exists(os.path.join(tmp_path, "a", "fig", "piezas_color"))
    assert not stl_hashes(os.path.join(tmp_path, "a", "fig"))                           # no se exportó nada: se frenó antes
    # por la CLI: error de armado con el mismo mensaje y código de salida 2 (no un aviso)
    r = json.load(open(RECETA))
    r["lugares"]["cabeza"] = f"{HEAD_ID}@2"
    rec = str(tmp_path / "r2.json")
    json.dump(r, open(rec, "w"))
    assert cli.main(["armar", line, rec, "--out", str(tmp_path / "b")]) == 2
    assert "azul" in capsys.readouterr().err and calls == []
    # sin paleta: también se frena, y el mensaje lo dice
    os.remove(os.path.join(line, "paleta.json"))
    with pytest.raises(PaletteRolesError, match="no tiene paleta"):
        arm(line, tmp_path / "c", {"cabeza": f"{HEAD_ID}@2"})
    # paleta ilegible: se frena con el motivo
    open(os.path.join(line, "paleta.json"), "w").write("{no es json")
    with pytest.raises(PaletteRolesError, match="paleta.json"):
        arm(line, tmp_path / "d", {"cabeza": f"{HEAD_ID}@2"})


def test_unused_declared_role_missing_from_palette_does_not_stop(tmp_path, line):
    """Un paquete que declara un rol que ningún píxel usa (la ficha solo anota los usados) no frena el armado si ese rol se borró de la paleta."""
    cat = Catalog.open(line)
    cat.set_palette(palette(extra=["sombra"]))
    saved = with_roles(cat, HEAD_ID, extra_roles=["sombra"])
    assert "sombra" not in saved.color["roles"]
    cat.delete_role("sombra")                                                           # no está en uso según la ficha: se puede borrar
    info, out = arm(line, tmp_path, {"cabeza": saved.ref})
    cab = informe(out)["piezas"]["cabeza"]
    assert cab["fuente"] == "pieza" and sorted(cab["roles"]) == ["azul", "rojo"] and not info["color"]["omitidas"]


def test_mixed_rgb_and_roles_pieces(tmp_path, line):
    """Una pieza con su FBX en RGB (T35) y otra con roles conviven: cada una con su fuente, y el informe solo lleva roles para la segunda.

    (El torso del muñeco no es una pieza «colocada» en `BuildResult.placed`: la pierna hace de pieza `rgb`.)"""
    cat = Catalog.open(line)
    meta, mesh = cat.load(LEG_ID)
    fbx = write_textured_fbx(tmp_path / "pierna.fbx", textured_from(mesh))
    rgb, _ = cat.add_piece(mesh, new_meta(meta), new_version=True, color_fbx=fbx)
    assert "modo" not in rgb.color
    info, out = arm(line, tmp_path / "m", {"cabeza": f"{HEAD_ID}@2", "pierna": rgb.ref})
    pz = info["color"]["piezas"]
    assert pz["cabeza"]["modo"] == "roles" and "fbx" not in pz["cabeza"]
    assert "modo" not in pz["pierna"] and pz["pierna"]["fbx"].endswith(".fbx")          # la ficha rgb se informa como antes
    rep = informe(out)["piezas"]
    assert rep["cabeza"]["fuente"] == "pieza" and rep["pierna_izq"]["fuente"] == "pieza"
    assert rep["cabeza"]["roles"] and "roles" not in rep["pierna_izq"]
    assert atlas_colors(out, "cabeza") <= {TOP, BOTTOM, FLAT} and {TOP, BOTTOM} <= atlas_colors(out, "cabeza")


def test_report_has_palette_digest_and_roles(armed, base_line):
    info, out = armed
    pal = Catalog.open(base_line).palette()
    for rep in (info["color"]["paleta"], informe(out)["paleta"]):
        assert rep == {"hash": pal.digest(), "roles": {"rojo": "#C81E1E", "azul": "#1E3CC8"}}
    cab = informe(out)["piezas"]["cabeza"]
    assert sorted(cab["roles"]) == ["azul", "rojo"] and cab["fuente"] == "pieza"       # `fuente` sigue siendo pieza/figura/plano
    assert info["color"]["piezas"]["cabeza"]["roles"] == cab["roles"]
    json.dumps(informe(out))                                                          # serializable (R5): sin objetos ni arreglos de NumPy


def test_mirrored_right_piece_uses_same_roles(tmp_path, line):
    """Con el lado derecho espejado, `brazo_der` se colorea con los mismos roles que `brazo_izq` (misma fuente, registrada reflejada)."""
    cat = Catalog.open(line)
    saved = with_roles(cat, ARM_ID, similarity=np.eye(4))
    info, out = arm(line, tmp_path, {"brazo": saved.ref})
    assert list(info["color"]["piezas"]) == ["brazo"]                                  # una sola fuente para los dos lados
    rep = informe(out)["piezas"]
    assert {"brazo_izq", "brazo_der"} <= set(rep)
    for side in ("brazo_izq", "brazo_der"):
        assert rep[side]["fuente"] == "pieza" and rep[side]["estado"] != "sin_origen", rep[side]
        assert sorted(rep[side]["roles"]) == ["azul", "rojo"]
        assert atlas_colors(out, side) <= {TOP, BOTTOM, FLAT} and atlas_colors(out, side) & {TOP, BOTTOM}
    # el lado derecho es el espejo del izquierdo: cubre lo mismo (medido: 69,85 % y 70,73 %; el resto son las caras que el FBX no tiene)
    assert abs(rep["brazo_der"]["pct_origen"] - rep["brazo_izq"]["pct_origen"]) < 2.0 and rep["brazo_izq"]["pct_origen"] > 50
    assert atlas_colors(out, "brazo_der") == atlas_colors(out, "brazo_izq")
    assert sorted(informe(out)["config"]["fuentes_propias"]) == ["brazo", "brazo_der"]


def test_figure_without_roles_armed_exactly_as_before(tmp_path, line, base_line):
    """Con paleta en la línea pero sin piezas por roles, el armado y el informe son los de siempre (nada de `paleta` ni `roles`)."""
    plain, out = arm(line, tmp_path / "p", {"cabeza": f"{HEAD_ID}@1"})
    assert "color" not in plain and not os.path.exists(os.path.join(out, "piezas_color"))
    # una pieza rgb (T35) en una línea con paleta: el informe de color no gana claves nuevas
    cat = Catalog.open(line)
    meta, mesh = cat.load(HEAD_ID + "@1")
    fbx = write_textured_fbx(tmp_path / "c.fbx", textured_from(mesh, odd_frame()))
    rgb, _ = cat.add_piece(mesh, new_meta(meta), new_version=True, color_fbx=fbx)
    info, out2 = arm(line, tmp_path / "q", {"cabeza": rgb.ref})
    assert "paleta" not in informe(out2) and "paleta" not in info["color"]
    assert all("roles" not in v for v in informe(out2)["piezas"].values()) and all("roles" not in v for v in info["color"]["piezas"].values())
    assert "paleta" not in informe(out2)["config"]


def test_scaled_roles_piece_is_left_without_color_even_if_its_role_is_missing(tmp_path, line):
    """Una pieza escalada no lleva color propio: no hace falta su paleta y no frena el armado (se avisa como en T36)."""
    cat = Catalog.open(line)
    cat.set_palette(Palette([Role("rojo", "Rojo", "#C81E1E")]))                         # falta «azul», pero la cabeza se escala
    info, out = arm(line, tmp_path, {"cabeza": f"{HEAD_ID}@2"}, scales={"cabeza": 1.1})
    assert "cabeza" in info.get("color", {}).get("omitidas", {}) or "color" not in info
