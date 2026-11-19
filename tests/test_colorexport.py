"""T14 · `Colorizer` y escritura OBJ + MTL + PNG + GLB."""
import json
import os

import numpy as np
import pytest
import trimesh

from afm.colorexport import COLOR_DIR, Colorizer
from afm.colorsrc import ColorSource
from afm.frame import Z_UP_TO_Y_UP
from color_helpers import REGIONS, doll_params, doll_pieces, doll_textured, piece_trimeshes

FIG = "muneco"
RED = np.array(REGIONS["brazo_izq"])


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
def colorizer(res, meshes, params):
    cz = Colorizer(ColorSource(doll_textured(), spacing_mm=0.1), params, texel_mm=0.3)
    cz.infos = {n: cz(n, m, res) for n, m in meshes.items()}
    return cz


@pytest.fixture(scope="module")
def out(colorizer, tmp_path_factory):
    d = tmp_path_factory.mktemp("color")
    return d, colorizer.write(str(d), FIG)


def _read_obj(path):
    v, vt, f = [], [], []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            k, *rest = ln.split()
            if k == "v":
                v.append([float(x) for x in rest])
            elif k == "vt":
                vt.append([float(x) for x in rest])
            elif k == "f":
                f.append([[int(i) for i in tok.split("/")] for tok in rest])
    return np.array(v), np.array(vt), np.array(f)


def test_call_returns_piece_info(colorizer, res):
    for n, info in colorizer.infos.items():
        assert {"estado", "pct_origen", "angulo", "texel_mm", "atlas", "segundos"} <= set(info)
        json.dumps(info)                                   # R13: serializable
    assert set(colorizer.infos) == set(res.pieces)


def test_obj_mtl_png_written_and_consistent(out, colorizer):
    from PIL import Image

    d, rep = out
    for name, (unw, bk, _reg, info) in colorizer.pieces.items():
        base = f"{FIG}_{name}"
        obj, mtl, png = (os.path.join(d, COLOR_DIR, base + s) for s in (".obj", ".mtl", "_color.png"))
        assert os.path.isfile(obj) and os.path.isfile(mtl) and os.path.isfile(png)
        v, vt, f = _read_obj(obj)
        assert len(v) == len(vt) == 3 * len(unw.faces) and len(f) == len(unw.faces)
        assert vt.min() >= 0.0 and vt.max() <= 1.0
        assert (f[:, :, 0] == f[:, :, 1]).all()            # f v/vt: el mismo índice
        assert f"map_Kd {base}_color.png" in open(mtl, encoding="utf-8").read()
        with Image.open(png) as im:
            assert im.size == tuple(info["atlas"]) == (unw.size[0], unw.size[1]) and im.mode == "RGB"
        # la geometría del OBJ es la de la pieza (misma superficie)
        assert trimesh.Trimesh(v, f[:, :, 0] - 1, process=False).area == pytest.approx(float(info["area_mm2"]), rel=1e-3)
        assert rep["piezas"][name]["obj"] == f"{COLOR_DIR}/{base}.obj"


def test_glb_loads_with_all_pieces(out, colorizer):
    d, rep = out
    scene = trimesh.load(os.path.join(d, rep["glb"]))
    assert isinstance(scene, trimesh.Scene) and len(scene.geometry) == len(colorizer.pieces)
    for g in scene.geometry.values():
        tex = g.visual.material.baseColorTexture
        assert tex is not None and tex.size[0] > 0 and len(g.visual.uv) == len(g.vertices)


def test_report_has_pct_origen_per_piece(out, colorizer):
    d, rep = out
    on_disk = json.load(open(os.path.join(d, rep["informe"]), encoding="utf-8"))
    assert on_disk["piezas"].keys() == colorizer.pieces.keys()
    for name, p in on_disk["piezas"].items():
        assert 0.0 <= p["pct_origen"] <= 100.0 and p["estado"] in ("identidad", "semilla", "barrido", "sin_origen")
    assert on_disk["accesorios_sin_color"] == 0 and on_disk["config"]["texel_mm"] == 0.3 and on_disk["sin_origen"] == []


def test_accessories_are_reported_without_color(colorizer, tmp_path):
    rep = colorizer.write(str(tmp_path), FIG, accesorios_sin_color=2)
    assert rep["accesorios_sin_color"] == 2


def test_orientation_applied_to_outputs(colorizer, tmp_path):
    T = trimesh.transformations.rotation_matrix(np.radians(90.0), [1, 0, 0], [0, 0, 0])
    T[:3, 3] = (5.0, -3.0, 2.0)
    colorizer.write(str(tmp_path), FIG)
    colorizer.write(str(tmp_path / "t"), FIG, orientation=T)
    name = "brazo_izq"
    v0, vt0, _ = _read_obj(tmp_path / COLOR_DIR / f"{FIG}_{name}.obj")
    v1, vt1, _ = _read_obj(tmp_path / "t" / COLOR_DIR / f"{FIG}_{name}.obj")
    z0, z1 = v0 @ Z_UP_TO_Y_UP, v1 @ Z_UP_TO_Y_UP                           # los OBJ van en Y arriba: de vuelta al marco Z arriba
    assert np.allclose(z1, z0 @ T[:3, :3].T + T[:3, 3], atol=1e-5)
    assert np.array_equal(vt0, vt1)                                       # los UV no cambian
    scene = trimesh.load(str(tmp_path / "t" / f"{FIG}_ensamblada.glb"))
    ymax_out = max(g.bounds[1][1] for g in scene.geometry.values())          # el GLB va en Y arriba
    ymax_in = max(g.bounds[1][1] for g in trimesh.load(str(tmp_path / f"{FIG}_ensamblada.glb")).geometry.values())
    assert ymax_out != pytest.approx(ymax_in, abs=1.0)                    # el conjunto sí quedó girado


def test_obj_reload_color_at_known_point(out):
    from PIL import Image

    d, _rep = out
    v, vt, f = _read_obj(d / COLOR_DIR / f"{FIG}_brazo_izq.obj")
    img = np.asarray(Image.open(d / COLOR_DIR / f"{FIG}_brazo_izq_color.png").convert("RGB")).astype(int)
    H, W = img.shape[:2]
    tri_v = v[(f[:, :, 0] - 1)]
    cen = tri_v.mean(axis=1)
    outer = np.flatnonzero(cen[:, 0] > 20.0)                              # cara exterior del brazo izquierdo del muñeco (región roja)
    assert len(outer) > 20
    uv = vt[(f[:, :, 1] - 1)].mean(axis=1)

    def color_at(k):
        c, r = int(uv[k, 0] * W), int((1.0 - uv[k, 1]) * H)
        return img[min(r, H - 1), min(c, W - 1)]

    one = outer[len(outer) // 2]
    assert np.abs(color_at(one) - RED).max() <= 10                        # el centro de una cara: rojo ± 10/255
    hits = np.mean([np.abs(color_at(k) - RED).max() <= 10 for k in outer])
    assert hits > 0.9, hits


def test_pieces_without_registration_marked_sin_origen(res, params, tmp_path):
    far = trimesh.creation.icosphere(subdivisions=2, radius=5.0)
    far.apply_translation((500.0, 500.0, 500.0))
    cz = Colorizer(ColorSource(doll_textured(), spacing_mm=0.2), params, texel_mm=0.5)
    info = cz("pieza_ajena", far, res)
    assert info["estado"] == "sin_origen" and info["pct_origen"] == 0.0
    unw, bk, _r, _i = cz.pieces["pieza_ajena"]
    assert (bk.atlas[bk.mask] == (200, 200, 200)).all()                   # color plano #C8C8C8
    rep = cz.write(str(tmp_path), FIG)
    assert rep["sin_origen"] == ["pieza_ajena"]


def test_invalid_fill_fails_at_construction(params):
    with pytest.raises(ValueError):
        Colorizer(ColorSource(doll_textured(), spacing_mm=0.5), params, fill="azul")


def test_assembled_obj_has_every_piece_and_valid_indices(out, colorizer):
    d, rep = out
    cdir = os.path.join(d, COLOR_DIR)
    obj = os.path.join(d, rep["obj_ensamblado"])
    mtl = obj[:-4] + ".mtl"
    assert os.path.isfile(obj) and os.path.isfile(mtl)
    lines = open(obj, encoding="utf-8").read().splitlines()
    assert lines[0] == f"mtllib {os.path.basename(mtl)}"
    objs = [l[2:] for l in lines if l.startswith("o ")]
    assert objs == [f"{FIG}_{n}" for n in sorted(colorizer.pieces)]
    nv = sum(l.startswith("v ") for l in lines)
    assert nv == sum(l.startswith("vt ") for l in lines) == sum(3 * len(u.faces) for u, *_ in colorizer.pieces.values())
    idx = [int(x.split("/")[0]) for l in lines if l.startswith("f ") for x in l.split()[1:]]
    assert min(idx) == 1 and max(idx) == nv
    text = open(mtl, encoding="utf-8").read()
    for n in colorizer.pieces:
        assert f"newmtl {FIG}_{n}" in text and f"map_Kd {FIG}_{n}_color.png" in text
        assert os.path.isfile(os.path.join(cdir, f"{FIG}_{n}_color.png"))
    assert sum(l.startswith("usemtl ") for l in lines) == len(objs)


def test_obj_and_glb_are_y_up_and_come_back_to_the_piece_frame(out, colorizer):
    """OBJ y GLB van con Y arriba (como esperan Bambu Studio y los visores de glTF); multiplicando por `Z_UP_TO_Y_UP` vuelve el marco
    Z arriba de la pieza impresa, con el mismo frente. Escritos en Z arriba, Bambu los importa acostados."""
    from afm.gui.render import load_glb_parts

    d, rep = out
    for name, (unw, *_rest) in colorizer.pieces.items():
        z_up = np.asarray(unw.vertices, float)[np.asarray(unw.faces, np.int64)].reshape(-1, 3)
        v, _vt, _f = _read_obj(d / COLOR_DIR / f"{FIG}_{name}.obj")
        assert np.allclose(v @ Z_UP_TO_Y_UP, z_up, atol=1e-5)
        assert np.allclose(v[:, 1], z_up[:, 2], atol=1e-5) and np.allclose(v[:, 2], -z_up[:, 1], atol=1e-5)   # la altura (z) pasó a y; el frente cambió de signo
    whole, _vt, _f = _read_obj(d / rep["obj_ensamblado"])
    zs = np.vstack([np.asarray(u.vertices, float)[np.asarray(u.faces, np.int64)].reshape(-1, 3) for u, *_r in colorizer.pieces.values()])
    assert whole[:, 1].max() == pytest.approx(zs[:, 2].max(), abs=1e-4) and whole[:, 1].min() == pytest.approx(zs[:, 2].min(), abs=1e-4)
    parts = load_glb_parts(os.path.join(d, rep["glb"]), y_up=True)                  # la ventana de la figura lo lee así
    assert max(p.mesh.bounds[1][2] for p in parts) == pytest.approx(zs[:, 2].max(), abs=1e-4)
    assert min(p.mesh.bounds[0][2] for p in parts) == pytest.approx(zs[:, 2].min(), abs=1e-4)
