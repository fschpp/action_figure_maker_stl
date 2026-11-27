"""Salida de color para Bambu Studio: un color plano por triángulo (`afm.bambu`), 3MF + OBJ sin textura."""
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import trimesh

from afm.bambu import BambuOptions, despeckle, reduce_colors
from afm.colorexport import Colorizer
from afm.colorsrc import ColorSource
from color_helpers import doll_params, doll_pieces, doll_textured, piece_trimeshes

FIG = "muneco"
NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


# ------------------------------------------------------------------ opciones
def test_options_coerce():
    assert BambuOptions.coerce(None) is None and BambuOptions.coerce(False) is None
    assert BambuOptions.coerce(True).max_colors == 8
    assert BambuOptions.coerce(4).max_colors == 4
    assert BambuOptions.coerce({"max_colors": 3, "min_area_pct": 1.0}).min_area_pct == 1.0
    for bad in (0, {"min_area_pct": 100}, {"despeckle": -1}, "x"):
        with pytest.raises(ValueError):
            BambuOptions.coerce(bad)


# ------------------------------------------------------------------ reducir colores
def test_reduce_keeps_exact_palette_under_the_cap():
    rgb = np.array([[200, 30, 30]] * 50 + [[30, 30, 200]] * 30 + [[240, 240, 240]] * 20, np.uint8)
    centers, assign, merged = reduce_colors(rgb, np.ones(len(rgb)), 8, 0.0, exact=True)
    assert merged == 0 and len(centers) == 3
    assert [tuple(c) for c in centers] == [(200, 30, 30), (30, 30, 200), (240, 240, 240)]      # del más al menos usado
    assert np.array_equal(centers[assign], rgb)


def test_reduce_to_cap_and_min_area():
    rng = np.random.default_rng(0)
    base = np.array([[220, 40, 40], [40, 40, 220], [40, 200, 60]], float)
    rgb = np.clip(np.concatenate([b + rng.normal(0, 6, (300, 3)) for b in base]), 0, 255).astype(np.uint8)
    centers, assign, _ = reduce_colors(rgb, np.ones(len(rgb)), 3, 0.0, exact=False)
    assert len(centers) == 3
    for b in base:                                               # cada color base tiene un centro cerca
        assert np.linalg.norm(centers.astype(float) - b, axis=1).min() < 12
    tiny = np.concatenate([np.tile([[200, 30, 30]], (990, 1)), np.tile([[30, 30, 200]], (10, 1))]).astype(np.uint8)
    c2, a2, m2 = reduce_colors(tiny, np.ones(len(tiny)), 8, 5.0, exact=True)      # el azul es 1 % < 5 %: se funde con el rojo
    assert len(c2) == 1 and m2 == 1 and (a2 == 0).all()


def test_reduce_is_deterministic():
    rng = np.random.default_rng(1)
    rgb = rng.integers(0, 256, (500, 3)).astype(np.uint8)
    a = reduce_colors(rgb, np.ones(500), 5, 0.0, exact=False)
    b = reduce_colors(rgb, np.ones(500), 5, 0.0, exact=False)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


# ------------------------------------------------------------------ motas
def test_despeckle_removes_isolated_face():
    m = trimesh.creation.icosphere(subdivisions=2)
    idx = np.zeros(len(m.faces), np.int64)
    idx[10] = 1
    assert despeckle(idx, np.asarray(m.faces), 2, 2) == 1 and (idx == 0).all()
    idx2 = np.zeros(len(m.faces), np.int64)                       # una mancha de dos caras vecinas NO es una mota
    nb = int(trimesh.graph.face_adjacency(m.faces)[0][1])
    a = int(trimesh.graph.face_adjacency(m.faces)[0][0])
    idx2[[a, nb]] = 1
    despeckle(idx2, np.asarray(m.faces), 2, 2)
    assert idx2[a] == 1 and idx2[nb] == 1


# ------------------------------------------------------------------ de punta a punta con el muñeco de pruebas
@pytest.fixture(scope="module")
def params():
    return doll_params()


@pytest.fixture(scope="module")
def res(params):
    return doll_pieces(params)


@pytest.fixture(scope="module")
def meshes(res):
    return piece_trimeshes(res)


@pytest.fixture(scope="module")
def baked(res, meshes, params, tmp_path_factory):
    cz = Colorizer(ColorSource(doll_textured(), spacing_mm=0.1), params, texel_mm=0.3, bambu=3)
    for n, m in meshes.items():
        cz(n, m, res)
    d = tmp_path_factory.mktemp("bambu")
    return d, cz.write(str(d), FIG), cz


def test_files_and_report(baked):
    d, out, _ = baked
    b = out["bambu"]
    for k in ("3mf", "obj", "mtl"):
        assert os.path.isfile(d / b[k])
    assert b["3mf"] == f"{FIG}_bambu.3mf"
    assert 1 <= len(b["colores"]) <= 3
    assert abs(sum(c["area_pct"] for c in b["colores"]) - 100.0) < 0.5
    rep = json.load(open(d / out["informe"]))
    assert rep["bambu"]["triangulos"] == b["triangulos"] and b["triangulos"] > 0


def _model(d, base):
    with zipfile.ZipFile(d / f"{base}.3mf") as z:
        assert set(z.namelist()) >= {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}
        return ET.fromstring(z.read("3D/3dmodel.model"))


def test_3mf_has_n_flat_materials_and_valid_indices(baked, meshes):
    d, out, _ = baked
    root = _model(d, f"{FIG}_bambu")
    bases = root.findall(".//m:basematerials/m:base", NS)
    n = len(out["bambu"]["colores"])
    assert len(bases) == n and all(re.fullmatch(r"#[0-9A-F]{6}", b.get("displaycolor")) for b in bases)
    objs = {o.get("id"): o for o in root.findall(".//m:resources/m:object", NS)}
    meshes_xml = [o for o in objs.values() if o.find("m:mesh", NS) is not None]
    assert len(meshes_xml) == len(meshes)
    assert sorted(o.get("name") for o in meshes_xml) == sorted(meshes)
    used = set()
    tri_total = 0
    for o in meshes_xml:
        nv = len(o.findall("m:mesh/m:vertices/m:vertex", NS))
        for t in o.findall("m:mesh/m:triangles/m:triangle", NS):
            assert all(0 <= int(t.get(f"v{i}")) < nv for i in (1, 2, 3))
            assert t.get("pid") == "1" and 0 <= int(t.get("p1")) < n
            used.add(int(t.get("p1")))
            tri_total += 1
    assert tri_total == out["bambu"]["triangulos"] and used == set(range(n))
    comp = [o for o in objs.values() if o.find("m:components", NS) is not None]
    assert len(comp) == 1 and len(comp[0].findall("m:components/m:component", NS)) == len(meshes)
    assert root.find("m:build/m:item", NS).get("objectid") == comp[0].get("id")


def test_3mf_is_z_up_like_the_stl(baked, meshes):
    d, _, _ = baked
    root = _model(d, f"{FIG}_bambu")
    zs = np.array([float(v.get("z")) for v in root.findall(".//m:vertex", NS)])
    ref = trimesh.util.concatenate(list(meshes.values())).bounds
    assert zs.min() == pytest.approx(ref[0, 2], abs=1e-3) and zs.max() == pytest.approx(ref[1, 2], abs=1e-3)


def test_obj_has_flat_materials_no_texture(baked):
    d, out, _ = baked
    mtl = open(d / out["bambu"]["mtl"], encoding="utf-8").read()
    obj = open(d / out["bambu"]["obj"], encoding="utf-8").read()
    assert "map_Kd" not in mtl and "vt " not in obj
    assert len(re.findall(r"^newmtl ", mtl, re.M)) == len(out["bambu"]["colores"])
    assert set(re.findall(r"^usemtl (\S+)", obj, re.M)) <= set(re.findall(r"^newmtl (\S+)", mtl, re.M))
    ys = np.array([float(ln.split()[2]) for ln in obj.splitlines() if ln.startswith("v ")])
    assert ys.max() - ys.min() > 1.0                              # la altura de la figura quedó en Y (OBJ con Y arriba)


def test_off_by_default_and_recolor_keeps_file(params, res, meshes, tmp_path):
    cz = Colorizer(ColorSource(doll_textured(), spacing_mm=0.1), params, texel_mm=0.3)
    for n, m in meshes.items():
        cz(n, m, res)
    out = cz.write(str(tmp_path), FIG)
    assert "bambu" not in out and not list(tmp_path.glob("*_bambu*"))


# ------------------------------------------------------------------ accesorios (armas) en el 3MF
def _sword_mesh():
    """Hoja acostada de 40 × 4 × 1 mm (pose de impresión), sin color propio."""
    m = trimesh.creation.box(extents=(40.0, 4.0, 1.0))
    m.apply_translation(-m.bounds[0])
    return m


def _bake(res, meshes, params, d, extras=None):
    cz = Colorizer(ColorSource(doll_textured(), spacing_mm=0.1), params, texel_mm=0.3, bambu=3)
    for n, m in meshes.items():
        cz(n, m, res)
    return cz, cz.write(str(d), FIG, extras=extras)


def test_el_3mf_lleva_el_arma(res, meshes, params, tmp_path):
    cz, out = _bake(res, meshes, params, tmp_path, extras=[("sword-01", _sword_mesh())])
    b = out["bambu"]
    assert b["accesorios"] == ["sword-01"]
    root = _model(tmp_path, f"{FIG}_bambu")
    objs = [o for o in root.findall(".//m:resources/m:object", NS) if o.find("m:mesh", NS) is not None]
    assert sorted(o.get("name") for o in objs) == sorted(list(meshes) + ["sword-01"])
    n = len(b["colores"])
    assert n <= 3                                                   # el arma no suma un color nuevo: usa uno de la figura (tope de filamentos)
    sw = next(o for o in objs if o.get("name") == "sword-01")
    assert len({int(t.get("p1")) for t in sw.findall("m:mesh/m:triangles/m:triangle", NS)}) == 1       # un color liso
    assert b["triangulos"] == sum(len(o.findall("m:mesh/m:triangles/m:triangle", NS)) for o in objs)
    assert sum(c["area_pct"] for c in b["colores"]) == pytest.approx(100.0, abs=0.5)
    assert "sword-01" in open(tmp_path / b["obj"]).read()


def test_el_arma_queda_donde_se_la_puso(res, meshes, params, tmp_path):
    """No se vuelve a orientar: las coordenadas del 3MF son las de la malla recibida."""
    sw = _sword_mesh()
    sw.apply_translation([500.0, 0.0, 0.0])
    _cz, out = _bake(res, meshes, params, tmp_path, extras=[("sword-01", sw)])
    root = _model(tmp_path, f"{FIG}_bambu")
    o = next(o for o in root.findall(".//m:resources/m:object", NS) if o.get("name") == "sword-01")
    xs = [float(v.get("x")) for v in o.findall("m:mesh/m:vertices/m:vertex", NS)]
    assert min(xs) == pytest.approx(500.0, abs=1e-3) and max(xs) == pytest.approx(540.0, abs=1e-3)


def test_sin_armas_el_3mf_no_cambia(res, meshes, params, tmp_path):
    _cz, out = _bake(res, meshes, params, tmp_path)
    assert "accesorios" not in out["bambu"]
    root = _model(tmp_path, f"{FIG}_bambu")
    assert sorted(o.get("name") for o in root.findall(".//m:resources/m:object", NS) if o.find("m:mesh", NS) is not None) == sorted(meshes)


def test_exportar_ahora_y_la_vista_previa_llevan_el_arma(res, meshes, params, tmp_path):
    cz, out = _bake(res, meshes, params, tmp_path, extras=[("sword-01", _sword_mesh())])
    assert cz.write_bambu()["accesorios"] == ["sword-01"]
    assert "sword-01" in [p.name for p in cz.bambu_preview().pieces]


def test_export_de_la_figura_pasa_las_armas_al_3mf(res, params, tmp_path):
    """`pipeline.export` pone el arma al lado de la figura (como el STL ensamblado) y la entrega al 3MF con su nombre."""
    from afm import pipeline as P

    cz = Colorizer(ColorSource(doll_textured(), spacing_mm=0.1), params, texel_mm=0.3, bambu=3)
    info = P.export(res, str(tmp_path), FIG, params=params, extras=[_sword_mesh()], extras_names=["sword-01"], colorize=cz)
    assert info["color"]["bambu"]["accesorios"] == ["sword-01"]
    root = _model(tmp_path, f"{FIG}_bambu")
    names = [o.get("name") for o in root.findall(".//m:resources/m:object", NS) if o.find("m:mesh", NS) is not None]
    assert "sword-01" in names
    asm = trimesh.load(str(tmp_path / f"{FIG}_ensamblada.stl"))
    o = next(o for o in root.findall(".//m:resources/m:object", NS) if o.get("name") == "sword-01")
    xs = [float(v.get("x")) for v in o.findall("m:mesh/m:vertices/m:vertex", NS)]
    assert min(xs) >= asm.bounds[0][0] and max(xs) <= asm.bounds[1][0] + 1e-3        # mismo lugar que en el STL ensamblado
