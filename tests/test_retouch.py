"""R01/R02 · Retoque manual por pinceladas (`retouch.py`, `ColorSource.paint`, `RolesPackage.with_roles`).

Sobre un cubo de 10 mm con un único rol («piel»): sin figuras reales ni GTK."""
import json
import sys

import numpy as np
import pytest
import trimesh

from afm.colorroles import RolesPackage
from afm.colorsrc import ColorSource
from afm.palette import Palette, Role
from afm.retouch import MAX_STROKES, Retouch, RetouchError, Stroke, transform_strokes

PIEL, AZUL, ORO = "#EABF97", "#000080", "#F5D020"


def palette():
    return Palette([Role("piel", "Piel", PIEL), Role("azul", "Azul", AZUL), Role("oro", "Oro", ORO)])


def cube_package() -> RolesPackage:
    """Cubo de lado 10 centrado en el origen; todo el atlas es el rol «piel»."""
    box = trimesh.creation.box((10.0, 10.0, 10.0))
    V, F = np.asarray(box.vertices, np.float32), np.asarray(box.faces, np.int32)
    uv = np.full((len(F), 3, 2), 0.5, np.float32)
    return RolesPackage(V, F, uv, np.zeros((4, 4), np.uint8), ["piel"], {})


def source(extra=("azul", "oro")) -> ColorSource:
    tm = cube_package().with_roles(extra).to_textured(palette())
    return ColorSource(tm, spacing_mm=0.4)


def rgb_of(hex_):
    return np.array([int(hex_[i:i + 2], 16) for i in (1, 3, 5)], np.uint8)


# ------------------------------------------------------------------ R01 · modelo
def test_stroke_roundtrip_and_normal_is_unit():
    s = Stroke((1, 2, 3), 1.5, "azul", (0, 0, 2))
    assert s.n == (0.0, 0.0, 1.0)
    assert Stroke.from_dict(s.to_dict()) == s


@pytest.mark.parametrize("kw", [
    dict(c=(0, 0), r=1, rol="azul"), dict(c=(0, 0, float("nan")), r=1, rol="azul"),
    dict(c=(0, 0, 0), r=0.0, rol="azul"), dict(c=(0, 0, 0), r=99, rol="azul"),
    dict(c=(0, 0, 0), r=1, rol="Azul"), dict(c=(0, 0, 0), r=1, rol="azul", n=(0, 0, 0)),
])
def test_stroke_rejects_bad_values(kw):
    with pytest.raises(RetouchError):
        Stroke(**kw)


def test_eraser_has_no_role_and_is_not_counted():
    r = Retouch([Stroke((0, 0, 0), 1, "azul"), Stroke((0, 0, 0), 1, None), Stroke((0, 0, 0), 1, "oro"), Stroke((0, 0, 0), 1, "azul")])
    assert r.roles_used() == ["azul", "oro"]


def test_retouch_file_roundtrip_atomic_and_digest(tmp_path):
    r = Retouch([Stroke((1, 2, 3), 1.0, "azul", (0, 0, 1)), Stroke((4, 5, 6), 2.0, None)])
    p = tmp_path / "x.retoque.json"
    r.save(p)
    assert not (tmp_path / "x.retoque.json.tmp").exists()
    back = Retouch.load(p)
    assert back == r and back.digest() == r.digest()
    assert Retouch([r.strokes[1], r.strokes[0]]).digest() != r.digest()            # el orden cuenta


def test_load_rejects_wrong_format_and_garbage(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"formato": "otro", "pinceladas": []}))
    with pytest.raises(RetouchError):
        Retouch.load(p)
    p.write_text("{no es json")
    with pytest.raises(RetouchError):
        Retouch.load(p)
    with pytest.raises(RetouchError):
        Retouch.load(tmp_path / "no_existe.json")


def test_too_many_strokes_rejected():
    with pytest.raises(RetouchError):
        Retouch([Stroke((0, 0, 0), 1, "azul")] * (MAX_STROKES + 1))


def test_module_imports_without_heavy_dependencies():
    for m in [k for k in sys.modules if k == "afm.retouch"]:
        del sys.modules[m]
    before = {k for k in ("PIL", "scipy", "skimage") if k in sys.modules}
    import afm.retouch  # noqa: F401
    assert {k for k in ("PIL", "scipy", "skimage") if k in sys.modules} == before


# ------------------------------------------------------------------ R02 · with_roles
def test_with_roles_appends_without_touching_labels():
    pkg = cube_package()
    out = pkg.with_roles(["azul", "piel", "azul", "oro"])
    assert out.roles == ["piel", "azul", "oro"]
    assert np.array_equal(out.labels, pkg.labels)
    assert pkg.with_roles(["piel"]) is pkg and pkg.roles == ["piel"]


# ------------------------------------------------------------------ R02 · paint
def test_paint_inside_radius_only():
    src = source()
    assert (src.sample_rgb == rgb_of(PIEL)).all()
    rep = src.paint([Stroke((5, 0, 0), 2.0, "azul")])                          # centro de la cara +X
    assert rep["pinceladas"] == 1 and rep["muestras"] > 0
    d = np.linalg.norm(src.sample_xyz - np.array([5.0, 0, 0]), axis=1)
    blue = (src.sample_rgb == rgb_of(AZUL)).all(axis=1)
    assert blue[d < 1.9].all() and not blue[d > 2.1].any()
    assert (src.sample_rgb[~blue] == rgb_of(PIEL)).all()                       # nada más cambió de color


def test_normal_gate_does_not_paint_the_back_face():
    src = source()
    # pincelada grande en el centro del cubo, con la normal de la cara +X: solo debe pintar esa cara
    src.paint([Stroke((0, 0, 0), 15.0, "azul", (1, 0, 0))])
    blue = (src.sample_rgb == rgb_of(AZUL)).all(axis=1)
    nx = src.face_normals[src.sample_face][:, 0]
    assert blue.any() and (nx[blue] > 0.3).all()                               # solo caras compatibles con la normal pedida
    assert blue[nx > 0.99].all()                                               # toda la cara +X
    assert not blue[nx < -0.99].any()                                          # la cara de atrás no se pinta
    # sin normal pinta toda la esfera
    src2 = source()
    src2.paint([Stroke((0, 0, 0), 15.0, "azul")])
    assert (src2.sample_rgb == rgb_of(AZUL)).all()


def test_last_stroke_wins_and_eraser_restores_original():
    src = source()
    src.paint([Stroke((5, 0, 0), 3.0, "azul"), Stroke((5, 0, 0), 1.5, "oro")])
    d = np.linalg.norm(src.sample_xyz - np.array([5.0, 0, 0]), axis=1)
    assert (src.sample_rgb[d < 1.4] == rgb_of(ORO)).all()
    ring = (d > 1.7) & (d < 2.8)
    assert (src.sample_rgb[ring] == rgb_of(AZUL)).all()
    src.paint([Stroke((5, 0, 0), 15.0, None)])                                  # borrador: todo vuelve a «piel»
    assert (src.sample_rgb == rgb_of(PIEL)).all()
    assert (src.sample_label == 0).all()


def test_paint_unknown_role_lists_it():
    src = source(extra=("azul",))
    with pytest.raises(ValueError, match="oro"):
        src.paint([Stroke((5, 0, 0), 2.0, "oro")])


def test_paint_needs_a_labelled_source():
    tm = cube_package().to_textured(palette())
    tm.labels = None
    src = ColorSource(tm, spacing_mm=1.0)
    with pytest.raises(ValueError):
        src.paint([Stroke((5, 0, 0), 2.0, "piel")])


def test_empty_paint_changes_nothing_and_recolor_still_works():
    src = source()
    assert src.paint([]) == {"pinceladas": 0, "muestras": 0}
    src.paint([Stroke((5, 0, 0), 2.0, "azul")])
    lut = np.asarray(src._lut).copy()
    lut[1] = (255, 0, 0)                                                         # «azul» pasa a rojo en la paleta
    src.recolor(lut)
    red = (src.sample_rgb == np.array([255, 0, 0], np.uint8)).all(axis=1)
    assert red.any() and ((src.sample_label == 1) == red).all()                  # lo retocado sigue al rol


# ------------------------------------------------------------------ R02 · transformación
def test_transform_strokes_scale_rotation_and_mirror():
    s = Stroke((1, 2, 3), 2.0, "azul", (1, 0, 0))
    M = np.diag([-2.0, 2.0, 2.0, 1.0])                                           # espejo en X con escala ×2
    M[:3, 3] = [10, 0, 0]
    (t,) = transform_strokes([s], M)
    assert t.c == pytest.approx((8.0, 4.0, 6.0))
    assert t.r == pytest.approx(4.0)
    assert t.n == pytest.approx((-1.0, 0.0, 0.0))
    with pytest.raises(RetouchError):
        transform_strokes([s], np.zeros((4, 4)))


def test_painting_matches_after_moving_source_and_strokes_together():
    """La fuente y las pinceladas llevadas por la misma 4×4 dan el mismo resultado que en el marco original."""
    M = np.eye(4)
    M[:3, :3] = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float) * 1.5
    M[:3, 3] = [3, -2, 7]
    st = [Stroke((5, 0, 0), 2.0, "azul", (1, 0, 0))]
    a = source()
    a.paint(st)
    tm = cube_package().with_roles(("azul", "oro")).to_textured(palette()).transformed(M)
    b = ColorSource(tm, spacing_mm=0.6)
    b.paint(transform_strokes(st, M))
    fa = (a.sample_rgb == rgb_of(AZUL)).all(axis=1).mean()
    fb = (b.sample_rgb == rgb_of(AZUL)).all(axis=1).mean()
    assert fb > 0 and abs(fa - fb) < 0.02
