"""T15 · gancho `colorize` en `export` y flags del CLI de color."""
import hashlib
import json
import os

import pytest

from afm import cli, pipeline as P
from afm.colorexport import COLOR_DIR, Colorizer
from afm.colorsrc import ColorSource
from afm.project import demo_project
from color_helpers import REGIONS, doll_params, doll_pieces, doll_textured, write_doll_fbx, write_sphere_fbx

FIG = "muneco"
# sha256[:16] de los STL del muñeco exportados con el código ANTERIOR a T15 (commit 53ad1ef): el gancho no puede cambiarlos
BASE_HASHES = {
    False: {"muneco_brazo_der.stl": "335bb3866f1169ff", "muneco_brazo_izq.stl": "307b039cd0c66d52",
            "muneco_pierna_der.stl": "b4becce9f5f64a01", "muneco_pierna_izq.stl": "a581c10118f186fd",
            "muneco_torso.stl": "1ad8bb4aa9db535a", "muneco_ensamblada.stl": "489f6659491c05ff"},
    True: {"muneco_brazo_der.stl": "9ba958ba04107a71", "muneco_brazo_izq.stl": "3f2c6da12e34adb5",
           "muneco_pierna_der.stl": "e6e0b58827332436", "muneco_pierna_izq.stl": "81043ce052b24e0a",
           "muneco_torso.stl": "23298945ad519097", "muneco_ensamblada.stl": "30b9c327bd2b7217"},
}


def _hashes(outdir):
    def h(p):
        return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
    out = {f: h(os.path.join(outdir, "piezas", f)) for f in sorted(os.listdir(os.path.join(outdir, "piezas")))}
    out[f"{FIG}_ensamblada.stl"] = h(os.path.join(outdir, f"{FIG}_ensamblada.stl"))
    return out


@pytest.fixture(scope="module")
def params():
    return doll_params()


@pytest.fixture(scope="module")
def res(params):
    return doll_pieces(params)


@pytest.fixture(scope="module")
def src():
    return ColorSource(doll_textured(), spacing_mm=0.1)


def test_export_without_colorize_is_byte_identical(res, params, tmp_path):
    for orient in (False, True):
        d = tmp_path / str(orient)
        info = P.export(res, str(d), FIG, orient=orient, params=params)
        assert "color" not in info
        assert not (d / COLOR_DIR).exists()
        assert _hashes(str(d)) == BASE_HASHES[orient]


def test_export_with_colorize_leaves_stl_untouched(res, params, src, tmp_path):
    for orient in (False, True):
        d = tmp_path / str(orient)
        cz = Colorizer(src, params, texel_mm=0.4)
        info = P.export(res, str(d), FIG, orient=orient, params=params, colorize=cz)
        assert _hashes(str(d)) == BASE_HASHES[orient]
        col = info["color"]
        assert sorted(col["piezas"]) == sorted(res.pieces)
        assert os.path.isfile(d / col["glb"]) and os.path.isfile(d / col["informe"])
        for p in col["piezas"].values():
            assert os.path.isfile(d / p["obj"]) and os.path.isfile(d / p["png"])
        json.dumps(info, default=float)                    # R13


def test_colorize_hook_gets_full_res_with_right(res, params, tmp_path):
    seen = {}

    class Spy:
        def __call__(self, name, mesh, r):
            seen[name] = r
            return {}

        def write(self, outdir, name, T=None, accesorios_sin_color=0):
            seen["write"] = accesorios_sin_color
            return {}

    import dataclasses                  # `res` con un lado derecho: `export` lo debe conservar al rearmar el `BuildResult`
    marker = object()
    r2 = dataclasses.replace(res, right=marker)
    P.export(r2, str(tmp_path), FIG, params=params, flat=1.0, colorize=Spy())
    assert all(seen[n].right is marker for n in res.pieces if n in seen)
    assert seen["write"] == 0


def test_cli_flags_parse(tmp_path, monkeypatch):
    seen = {}

    def fake_run(stl, project, outdir, name, **kw):
        seen.update(kw)
        return {"archivos": {}, "validacion": {"ok": True, "problems": [], "gaps": {}}, "construccion": {}}

    monkeypatch.setattr(cli, "run", fake_run)
    proj = tmp_path / "p.proyecto"
    demo_project().save(str(proj))
    stl = tmp_path / "x.stl"
    stl.write_text("solid x\nendsolid x\n")
    fbx = tmp_path / "x.fbx"
    fbx.write_bytes(b"x")
    cli.main(["build", str(stl), str(proj), "--out", str(tmp_path / "o"), "--color-desde", str(fbx)])
    assert seen["color"] == {"fbx": str(fbx), "texel_mm": 0.2, "eps": 0.15, "fill": "extend", "symmetric": False}
    cli.main(["build", str(stl), str(proj), "--out", str(tmp_path / "o"), "--color-desde", str(fbx), "--textura-mm", "0.5",
              "--color-simetrico", "--color-relleno", "#C8C8C8", "--color-tolerancia", "0.3"])
    assert seen["color"] == {"fbx": str(fbx), "texel_mm": 0.5, "eps": 0.3, "fill": "#C8C8C8", "symmetric": True}


def test_cli_rejects_bad_fill_and_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run", lambda *a, **k: pytest.fail("no debe llegar a run"))
    proj = tmp_path / "p.proyecto"
    demo_project().save(str(proj))
    stl = tmp_path / "x.stl"
    stl.write_text("solid x\nendsolid x\n")
    fbx = tmp_path / "x.fbx"
    fbx.write_bytes(b"x")
    with pytest.raises(SystemExit):
        cli.main(["build", str(stl), str(proj), "--out", str(tmp_path / "o"), "--color-desde", str(fbx), "--color-relleno", "rojo"])
    with pytest.raises(SystemExit):
        cli.main(["build", str(stl), str(proj), "--out", str(tmp_path / "o"), "--color-desde", str(tmp_path / "no_existe.fbx")])


def test_sin_color_flag_disables(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run", lambda stl, project, outdir, name, **kw: seen.update(kw) or
                        {"archivos": {}, "validacion": {"ok": True, "problems": [], "gaps": {}}, "construccion": {}})
    proj = tmp_path / "p.proyecto"
    demo_project().save(str(proj))
    stl = tmp_path / "x.stl"
    stl.write_text("solid x\nendsolid x\n")
    fbx = tmp_path / "x.fbx"
    fbx.write_bytes(b"x")
    cli.main(["build", str(stl), str(proj), "--out", str(tmp_path / "o"), "--color-desde", str(fbx), "--sin-color"])
    assert "color" not in seen


def test_main_omits_colorize_without_flag(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run", lambda stl, project, outdir, name, **kw: seen.update(kw) or
                        {"archivos": {}, "validacion": {"ok": True, "problems": [], "gaps": {}}, "construccion": {}})
    proj = tmp_path / "p.proyecto"
    demo_project().save(str(proj))
    stl = tmp_path / "x.stl"
    stl.write_text("solid x\nendsolid x\n")
    cli.main(["build", str(stl), str(proj), "--out", str(tmp_path / "o"), "--textura-mm", "0.5", "--color-simetrico"])
    assert "color" not in seen and "colorize" not in seen


@pytest.fixture(scope="module")
def doll_stl(tmp_path_factory):
    from afm.testfigure import make_doll
    d = tmp_path_factory.mktemp("cli_doll")
    stl = d / "muneco.stl"
    make_doll().export(str(stl))
    return d, str(stl)


def test_run_with_doll_fbx_writes_color_and_same_stls(doll_stl, tmp_path):
    d, stl = doll_stl
    fbx = write_doll_fbx(d / "muneco.fbx")
    base = tmp_path / "sin"
    col = tmp_path / "con"
    info0 = cli.run(stl, demo_project(), str(base), FIG, progress=lambda *_: None)
    info1 = cli.run(stl, demo_project(), str(col), FIG, progress=lambda *_: None,
                    color={"fbx": fbx, "texel_mm": 0.4, "eps": 0.15, "fill": "extend", "symmetric": False})
    assert _hashes(str(base)) == _hashes(str(col))                         # los STL no cambian ni un byte
    assert "color" not in info0
    assert info1["color"]["alineacion"]["ok"] and info1["color"]["salida"]["piezas"]
    assert os.path.isdir(col / COLOR_DIR)
    rep = json.load(open(col / f"{FIG}_informe.json"))                      # el informe es JSON válido (R13)
    assert rep["color"]["alineacion"]["media_mm"] < 0.1


def test_cli_color_desde_rejects_different_figure(doll_stl, tmp_path):
    d, stl = doll_stl
    fbx = write_sphere_fbx(d / "esfera.fbx")
    msgs = []
    out = tmp_path / "o"
    info = cli.run(stl, demo_project(), str(out), FIG, progress=msgs.append,
                   color={"fbx": fbx, "texel_mm": 0.4, "eps": 0.15, "fill": "extend", "symmetric": False})
    assert info["color"]["omitido"] == "otra_figura" and "salida" not in info["color"]
    assert any("AVISO" in m and "esfera.fbx" in m for m in msgs)
    assert not (out / COLOR_DIR).exists() and os.path.isfile(out / "piezas" / f"{FIG}_torso.stl")


def test_run_color_with_front_minus_y(tmp_path):
    """Figura que mira a −Y (como Güemes): el STL y el FBX están girados 180° sobre Z respecto del marco del motor.
    La fuente se alinea contra la figura tal como se cargó y recién después se le aplica el giro de `front` (regresión de T15)."""
    import dataclasses

    import numpy as np

    from afm.testfigure import make_doll
    from color_helpers import write_textured_fbx

    flip = np.diag([-1.0, -1.0, 1.0])
    doll = make_doll()
    doll.apply_transform(np.diag([-1.0, -1.0, 1.0, 1.0]))
    stl = tmp_path / "muneco_m.stl"
    doll.export(str(stl))
    tm = doll_textured()
    tm_m = dataclasses.replace(tm, vertices=np.asarray(tm.vertices, float) @ flip.T)
    fbx = write_textured_fbx(tmp_path / "muneco_m.fbx", tm_m)
    proj = demo_project()
    proj.front = "-Y"
    info = cli.run(str(stl), proj, str(tmp_path / "o"), FIG, progress=lambda *_: None,
                   color={"fbx": fbx, "texel_mm": 0.4, "eps": 0.15, "fill": "extend", "symmetric": False})
    al = info["color"]["alineacion"]
    assert al["ok"] and al["media_mm"] < 0.1, al
    assert info["color"]["salida"]["piezas"]
    pct = {n: p["pct_origen"] for n, p in info["color"]["salida"]["piezas"].items()}
    assert pct["brazo_izq"] > 40 and pct["torso"] > 30, pct          # el color llegó a las piezas, no es un gris de relleno
    # el doll es simétrico: la geometría no distingue un giro de 180°, el color sí (izquierdo = rojo, derecho = azul)
    from PIL import Image

    def share(piece, rgb):
        a = np.asarray(Image.open(tmp_path / "o" / info["color"]["salida"]["piezas"][piece]["png"]).convert("RGB"), float)
        return float((np.abs(a - np.array(rgb, float)).max(axis=2) < 40).mean())

    assert share("brazo_izq", REGIONS["brazo_izq"]) > 3 * share("brazo_izq", REGIONS["brazo_der"])
    assert share("brazo_der", REGIONS["brazo_der"]) > 3 * share("brazo_der", REGIONS["brazo_izq"])
