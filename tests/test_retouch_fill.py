"""Retoque · «rellenar región» (`Stroke.relleno`, `ColorSource._fill_region`), espejado y formato `afm-retoque-2`.

Sobre una placa de 20 × 20 mm de celdas de 1 mm con dos roles en el atlas («piel» = 0 y «azul» = 1): sin figuras reales ni GTK."""
import json

import numpy as np
import pytest

from afm.colorroles import RolesPackage
from afm.colorsrc import ColorSource
from afm.palette import Palette, Role
from afm.retouch import FORMAT, FORMAT_FILL, Retouch, RetouchError, Stroke

HEX = {"piel": "#EABF97", "azul": "#000080", "oro": "#F5D020", "rojo": "#CC0000"}
N = 20


def palette():
    return Palette([Role(k, k.title(), v) for k, v in HEX.items()])


def plate(specks=(), flap=False) -> ColorSource:
    """Placa en z=0 (normal +Z). Las celdas de `specks` ((i, j)) llevan la etiqueta 1 («azul»); el resto, 0 («piel»). Con `flap`, una segunda piel
    en z=0,6 con la normal hacia −Z (una solapa fina de dos caras)."""
    V, F, uv = [], [], []

    def skin(z, flip):
        base = len(V)
        for j in range(N + 1):
            for i in range(N + 1):
                V.append((i - N / 2, j - N / 2, z))
        for j in range(N):
            for i in range(N):
                a, b, c, d = (base + j * (N + 1) + i, base + j * (N + 1) + i + 1, base + (j + 1) * (N + 1) + i + 1, base + (j + 1) * (N + 1) + i)
                tris = [(a, b, c), (a, c, d)]
                for t in tris:
                    F.append(t[::-1] if flip else t)
                    u = 0.75 if (i, j) in specks and not flip else 0.25
                    uv.append([(u, 0.5)] * 3)

    skin(0.0, False)
    if flap:
        skin(0.6, True)
    atlas = np.array([[0, 1]], np.uint8)
    pkg = RolesPackage(np.asarray(V, np.float32), np.asarray(F, np.int32), np.asarray(uv, np.float32), atlas, ["piel", "azul"], {})
    return ColorSource(pkg.with_roles(("oro", "rojo")).to_textured(palette()), spacing_mm=0.2)


def labels_of(src, rol):
    return src.sample_label == src.label_roles.index(rol)


def test_fill_paints_only_the_connected_region_of_the_seed():
    src = plate(specks={(3, 3), (4, 3), (3, 4), (10, 3), (11, 3)})
    before_azul = labels_of(src, "azul").copy()
    rep = src.paint([Stroke((-6.5, -6.5, 0), 15.0, "oro", relleno=True)])        # semilla sobre la mancha de abajo a la izquierda
    oro = labels_of(src, "oro")
    assert rep["muestras"] == int(oro.sum()) > 0
    assert not (oro & ~before_azul).any()                                        # solo cambió lo que era «azul»
    x, y = src.sample_xyz[:, 0], src.sample_xyz[:, 1]
    near = (x < -3) & (y < -4)                                                   # la 2.ª mancha está a ~7 mm: dentro del alcance, pero desconectada
    assert (before_azul & ~near).any()
    assert oro[before_azul & near].all()                                         # toda la mancha de la semilla pasó a oro
    assert not oro[before_azul & ~near].any()                                    # la otra mancha (lejos) no se tocó
    assert not oro[~before_azul].any()                                           # nada de «piel» cambió


def test_fill_of_a_big_region_is_bounded_by_the_reach():
    src = plate()
    src.paint([Stroke((0, 0, 0), 3.0, "oro", relleno=True)])
    oro = labels_of(src, "oro")
    d = np.linalg.norm(src.sample_xyz, axis=1)
    assert oro[d < 2.9].all() and not oro[d > 3.1].any()


def test_fill_does_not_cross_to_the_back_skin_of_a_thin_flap():
    src = plate(flap=True)
    src.paint([Stroke((0, 0, 0.0), 4.0, "oro", (0, 0, 1), relleno=True)])
    nz = src.face_normals[src.sample_face][:, 2]
    oro = labels_of(src, "oro")
    assert oro[nz > 0.99].any() and not oro[nz < -0.99].any()


def test_fill_with_a_seed_far_from_any_candidate_changes_nothing():
    src = plate()
    assert src.paint([Stroke((0, 0, 5.0), 1.0, "oro", relleno=True)])["muestras"] == 0


def test_fill_order_last_wins_and_recolor_follows_the_role():
    src = plate(specks={(3, 3)})
    src.paint([Stroke((-6.5, -6.5, 0), 15.0, "oro", relleno=True), Stroke((-6.5, -6.5, 0), 15.0, "rojo", relleno=True)])
    assert labels_of(src, "rojo").any() and not labels_of(src, "oro").any()
    lut = np.asarray(src._lut).copy()
    lut[src.label_roles.index("rojo")] = (1, 2, 3)
    src.recolor(lut)
    assert (src.sample_rgb[labels_of(src, "rojo")] == (1, 2, 3)).all()


# ------------------------------------------------------------------ modelo
def test_fill_stroke_roundtrip_and_needs_a_role():
    s = Stroke((1, 2, 3), 5.0, "azul", (0, 0, 1), relleno=True)
    assert s.to_dict()["k"] == "relleno" and Stroke.from_dict(s.to_dict()) == s
    with pytest.raises(RetouchError):
        Stroke((0, 0, 0), 5.0, None, relleno=True)
    with pytest.raises(RetouchError):
        Stroke.from_dict({"c": [0, 0, 0], "r": 1, "rol": "azul", "k": "otro"})


def test_format_is_v1_without_fills_and_bytes_do_not_change(tmp_path):
    rt = Retouch([Stroke((1, 2, 3), 1.5, "azul", (0, 0, 1))])
    assert rt.to_dict()["formato"] == FORMAT and "k" not in rt.to_dict()["pinceladas"][0]
    rt2 = Retouch(list(rt.strokes) + [Stroke((0, 0, 0), 4.0, "oro", relleno=True)])
    assert rt2.to_dict()["formato"] == FORMAT_FILL
    assert Retouch.load(rt2.save(tmp_path / "a.json")) == rt2


def test_v1_file_cannot_hide_fill_strokes():
    d = Retouch([Stroke((0, 0, 0), 4.0, "oro", relleno=True)]).to_dict()
    d["formato"] = FORMAT
    with pytest.raises(RetouchError):
        Retouch.from_dict(d)


def test_mirrored_reflects_x_and_keeps_kind_and_radius():
    rt = Retouch([Stroke((5, 2, 3), 1.5, "azul", (1, 0, 0)), Stroke((4, 0, 0), 6.0, "oro", (0, 1, 0), relleno=True)])
    m = rt.mirrored()
    assert m.strokes[0].c == (-5.0, 2.0, 3.0) and m.strokes[0].n == (-1.0, 0.0, 0.0)
    assert m.strokes[1].relleno and m.strokes[1].r == 6.0 and m.strokes[1].n == (0.0, 1.0, 0.0)
    assert m.mirrored() == rt                                                    # espejar dos veces vuelve al original
    json.dumps(m.to_dict())
