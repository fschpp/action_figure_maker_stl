"""R03–R05 · Retoque manual del color de una pieza del catálogo: archivo y ficha, `verify`, papelera, armado y vista previa.

Sobre el muñeco del catálogo de ejemplo con FBX sintéticos (rojo arriba, azul abajo) y un tercer rol «oro» (gris #808080 en la paleta de prueba),
que solo aparece si el retoque lo pinta. Los STL no cambian nunca por el color: se comparan por bytes."""
import json
import os
import shutil

import numpy as np
import pytest

from afm import parts as P
from afm.catalog import Catalog, CatalogError
from afm.colorpiece import PaletteRolesError, catalog_color_sources, roles_color_source
from afm.colorpreview import piece_color_parts
from afm.colorroles import RolesPackage
from afm.palette import Palette, Role
from afm.retouch import Retouch, Stroke
from test_color_catalog_palette import (BOTTOM, FLAT, HEAD_ID, ROOT, TOP, arm, atlas_colors, informe, palette,
                                        stl_hashes, with_roles)

GOLD = (128, 128, 128)                   # «oro» en la paleta de prueba (`palette(extra=("oro",))`: #808080)
REF = f"{HEAD_ID}@2"


@pytest.fixture(scope="module")
def base_line(tmp_path_factory):
    """Copia del catálogo de ejemplo con la paleta rojo/azul/oro y la cabeza `@2` con roles."""
    d = str(tmp_path_factory.mktemp("linea") / "linea")
    shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    cat = Catalog.open(d)
    cat.set_palette(palette(extra=("oro",)))
    with_roles(cat, HEAD_ID)
    return d


@pytest.fixture()
def line(tmp_path, base_line):
    d = str(tmp_path / "linea")
    shutil.copytree(base_line, d)
    return d


def ficha(root):
    with open(os.path.join(root, "piezas", "cabeza", f"{REF}.pieza.json"), encoding="utf-8") as f:
        return json.load(f)


def top_stroke(cat, radius=3.0):
    """Pincelada «oro» sobre el punto más alto de la cabeza (marco del STL de la pieza)."""
    _m, mesh = cat.load(REF)
    V = np.asarray(mesh.vertices, float)
    return Stroke(tuple(V[V[:, 2].argmax()]), radius, "oro")


# ------------------------------------------------------------------ R03 · catálogo
def test_piece_without_retouch_has_no_retoque_field(line):
    cat = Catalog.open(line)
    assert "retoque" not in ficha(line)["color"] and cat.retouch(REF) is None
    assert not [p for p in cat.verify().problems if "retoque" in p]


def test_set_retouch_writes_file_and_ficha_and_roundtrips(line):
    cat = Catalog.open(line)
    before = ficha(line)
    rt = Retouch([top_stroke(cat), Stroke((0, 0, 0), 1.0, None)])
    m = cat.set_retouch(REF, rt)
    path = os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@2.retoque.json")
    blk = ficha(line)["color"]["retoque"]
    assert os.path.isfile(path) and not os.path.exists(path + ".tmp")
    assert blk == {"fuente": f"{HEAD_ID}@2.retoque.json", "sha256": P.sha256_file(path), "n": 2, "roles": ["oro"]} == m.color["retoque"]
    assert {k: v for k, v in ficha(line)["color"].items() if k != "retoque"} == before["color"]      # el resto del color no se toca
    assert Catalog.open(line).retouch(REF) == Retouch.from_dict(rt.to_dict())          # el archivo guarda las coordenadas a 4 decimales
    assert not [p for p in cat.verify().problems if "retoque" in p or "rol" in p]
    assert f"{HEAD_ID}@2" in cat.palette_usage("oro") and f"{HEAD_ID}@2" in cat.palette_usage("rojo")


def test_retouch_does_not_touch_stl_or_package(line):
    cat = Catalog.open(line)
    meta = cat.meta(REF)
    stl = os.path.join(line, "piezas", "cabeza", meta.stl)
    npz = cat.color_file(REF)
    h = (P.sha256_file(stl), P.sha256_file(npz), meta.sha256)
    cat.set_retouch(REF, Retouch([top_stroke(cat)]))
    assert (P.sha256_file(stl), P.sha256_file(npz), cat.meta(REF).sha256) == h


def test_role_missing_from_palette_writes_nothing(line):
    cat = Catalog.open(line)
    before = ficha(line)
    with pytest.raises(CatalogError, match="verde"):
        cat.set_retouch(REF, Retouch([Stroke((0, 0, 0), 1.0, "verde")]))
    assert ficha(line) == before and not os.path.exists(os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@2.retoque.json"))


def test_only_pieces_with_roles_can_be_retouched(line):
    cat = Catalog.open(line)
    with pytest.raises(CatalogError, match="por roles"):
        cat.set_retouch(f"{HEAD_ID}@1", Retouch([Stroke((0, 0, 0), 1.0, "oro")]))          # la @1 no tiene color


def test_tampered_or_missing_retouch_is_detected(line):
    cat = Catalog.open(line)
    cat.set_retouch(REF, Retouch([top_stroke(cat)]))
    path = os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@2.retoque.json")
    good = open(path, encoding="utf-8").read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(good.replace('"oro"', '"azul"'))
    assert any("retoque" in p and "hash" in p for p in cat.verify().problems)
    with pytest.raises(CatalogError, match="hash"):
        cat.retouch(REF)
    os.remove(path)
    assert any("falta el retoque" in p for p in cat.verify().problems)
    with pytest.raises(CatalogError, match="falta"):
        cat.retouch(REF)


def test_verify_flags_retouch_role_removed_from_palette(line):
    cat = Catalog.open(line)
    cat.set_retouch(REF, Retouch([top_stroke(cat)]))
    cat.set_palette(Palette([r for r in cat.palette().roles if r.id != "oro"]))
    assert any("retoque" in p and "oro" in p for p in cat.verify().problems)


def test_removing_the_retouch_restores_the_original_ficha(line):
    cat = Catalog.open(line)
    before = ficha(line)
    cat.set_retouch(REF, Retouch([top_stroke(cat)]))
    cat.set_retouch(REF, None)
    assert ficha(line) == before and cat.retouch(REF) is None
    assert not os.path.exists(os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@2.retoque.json"))
    cat.set_retouch(REF, Retouch([top_stroke(cat)]))
    cat.set_retouch(REF, Retouch())                                              # vacío también lo quita
    assert ficha(line) == before


def test_delete_and_restore_move_the_retouch_with_the_piece(line):
    cat = Catalog.open(line)
    cat.set_retouch(REF, Retouch([top_stroke(cat)]))
    path = os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@2.retoque.json")
    digest = P.sha256_file(path)
    res = cat.delete_pieces([REF])
    assert not res.errors and not os.path.exists(path)
    assert os.path.isfile(os.path.join(res.trash, "piezas", "cabeza", f"{HEAD_ID}@2.retoque.json"))
    cat.restore_deleted()
    assert os.path.isfile(path) and P.sha256_file(path) == digest and cat.retouch(REF) is not None
    assert not [p for p in cat.verify().problems if "retoque" in p]


def test_ficha_validation_rejects_bad_retoque_block():
    base = {"modo": "roles", "fuente": "x.npz", "sha256": "a", "similitud": np.eye(4).tolist(), "roles": ["rojo"]}
    assert not P.color_problems(base)
    assert not P.color_problems({**base, "retoque": {"fuente": "x", "sha256": "b", "n": 3, "roles": ["oro"]}})
    for bad in ({"fuente": "x"}, {"fuente": "x", "sha256": "b", "n": -1, "roles": []}, {"fuente": "x", "sha256": "b", "n": 1, "roles": ["Oro"]}, "no"):
        assert P.color_problems({**base, "retoque": bad})
    assert P.color_problems({**{k: v for k, v in base.items() if k != "roles"}, "modo": "rgb", "retoque": {"fuente": "x"}})


# ------------------------------------------------------------------ R04 · armado
def test_armed_figure_has_the_retouched_role_and_stl_do_not_change(tmp_path, line):
    cat = Catalog.open(line)
    lugares = {"cabeza": REF}
    info0, out0 = arm(line, tmp_path / "a", lugares)
    assert GOLD not in atlas_colors(out0, "cabeza") and "retoque" not in info0["color"]["piezas"]["cabeza"]
    cat.set_retouch(REF, Retouch([top_stroke(cat)]))
    info1, out1 = arm(line, tmp_path / "b", lugares)
    got = atlas_colors(out1, "cabeza")
    assert GOLD in got and {TOP, BOTTOM} <= got and got <= {TOP, BOTTOM, FLAT, GOLD}, sorted(got)
    assert info1["color"]["piezas"]["cabeza"]["retoque"] == 1
    assert informe(out1)["piezas"]["cabeza"]["estado"] != "sin_origen"
    assert stl_hashes(out0) == stl_hashes(out1) and stl_hashes(out0)             # el retoque no mueve ni un byte de los STL


def test_retouch_role_missing_from_palette_stops_before_registration(tmp_path, line, monkeypatch):
    cat = Catalog.open(line)
    cat.set_retouch(REF, Retouch([top_stroke(cat)]))
    cat.set_palette(Palette([r for r in cat.palette().roles if r.id != "oro"]))
    calls = []
    monkeypatch.setattr("afm.colorreg.register_piece", lambda *a, **k: calls.append(a))
    with pytest.raises(PaletteRolesError, match="oro"):
        arm(line, tmp_path / "a", {"cabeza": REF})
    assert calls == []


def test_unreadable_retouch_leaves_the_piece_out_with_a_reason(line):
    """Un retoque alterado no se ignora en silencio: la pieza queda omitida del color y el informe dice por qué."""
    cat = Catalog.open(line)
    cat.set_retouch(REF, Retouch([top_stroke(cat)]))
    with open(os.path.join(line, "piezas", "cabeza", f"{HEAD_ID}@2.retoque.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    from afm.recipe import Recipe  # noqa: F401  (solo para dejar claro que se prueba por el camino de la receta)
    from afm import cli
    import afm.colorpiece as C

    class Placed:                                                                # lo mínimo que `catalog_color_sources` lee de `placed`
        def __init__(self, meta):
            self.choice = type("Ch", (), {"meta": meta})()
            self.T = np.eye(4)
    meta = Catalog.open(line).meta(REF)
    sources, info = C.catalog_color_sources({"cabeza": Placed(meta)}, Catalog.open(line))
    assert not sources and "hash" in info["omitidas"]["cabeza"]
    assert cli is not None


def test_right_side_uses_the_mirrored_strokes():
    """`roles_color_source` con `M·T` (el lado derecho): las pinceladas se espejan con la fuente."""
    import trimesh

    box = trimesh.creation.box((10.0, 10.0, 10.0))
    pkg = RolesPackage(np.asarray(box.vertices, np.float32), np.asarray(box.faces, np.int32), np.full((len(box.faces), 3, 2), 0.5, np.float32),
                       np.zeros((4, 4), np.uint8), ["piel"], {})
    pal = Palette([Role("piel", "Piel", "#EABF97"), Role("oro", "Oro", "#808080")])
    rt = Retouch([Stroke((5, 0, 0), 2.0, "oro", (1, 0, 0))])
    T = np.eye(4)
    T[:3, 3] = [20, 0, 0]                                                        # la pieza izquierda está en x = 20
    left = roles_color_source(pkg, pal, T, np.eye(4), 0.4, rt)
    M = np.diag([-1.0, 1.0, 1.0, 1.0])
    right = roles_color_source(pkg, pal, M @ T, np.eye(4), 0.4, rt)
    gl = (left.sample_rgb == np.array(GOLD, np.uint8)).all(axis=1)
    gr = (right.sample_rgb == np.array(GOLD, np.uint8)).all(axis=1)
    assert gl.any() and abs(int(gl.sum()) - int(gr.sum())) <= 0.1 * gl.sum()      # mismo tamaño salvo el ruido del muestreo
    assert left.sample_xyz[gl][:, 0].min() > 24.5 and right.sample_xyz[gr][:, 0].max() < -24.5     # una en +x, la espejada en −x


# ------------------------------------------------------------------ R05 · vista previa
def test_preview_shows_the_retouched_role_and_defaults_to_the_saved_one(line):
    cat = Catalog.open(line)
    meta, mesh = cat.load(REF)

    def has_gold(parts):
        return any(np.allclose(rgba[:3], np.array(GOLD) / 255.0, atol=0.02) for _n, _m, rgba in parts)

    assert not has_gold(piece_color_parts(cat, meta, mesh))
    rt = Retouch([top_stroke(cat)])
    assert has_gold(piece_color_parts(cat, meta, mesh, retouch=rt))              # un retoque sin guardar (el que se está pintando)
    cat.set_retouch(REF, rt)
    assert has_gold(piece_color_parts(cat, Catalog.open(line).meta(REF), mesh))  # el guardado, por defecto
    assert not has_gold(piece_color_parts(cat, Catalog.open(line).meta(REF), mesh, retouch=None))     # None lo ignora


# ------------------------------------------------------------------ «rellenar región» de punta a punta
def fill_stroke(cat, reach=15.0):
    """Relleno «oro» con la semilla en el punto más alto de la cabeza (el rol que trae ahí el paquete es el de arriba)."""
    s = top_stroke(cat)
    return Stroke(s.c, reach, "oro", relleno=True)


def test_fill_stroke_is_saved_armed_and_previewed(tmp_path, line):
    cat = Catalog.open(line)
    rt = Retouch([fill_stroke(cat)])
    cat.set_retouch(REF, rt)
    f = ficha(line)["color"]["retoque"]
    assert f["n"] == 1 and f["roles"] == ["oro"]
    assert json.load(open(os.path.join(line, "piezas", "cabeza", f["fuente"])))["formato"] == "afm-retoque-2"
    assert not any("retoque" in p for p in cat.verify().problems)
    info, out = arm(line, tmp_path / "f", {"cabeza": REF})
    got = atlas_colors(out, "cabeza")
    assert GOLD in got and info["color"]["piezas"]["cabeza"]["retoque"] == 1
    meta, mesh = cat.load(REF)
    assert any(np.allclose(rgba[:3], np.array(GOLD) / 255.0, atol=0.02) for _n, _m, rgba in piece_color_parts(cat, meta, mesh))
