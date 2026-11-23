"""R12 · `ColorSource.query` busca una muestra de normal compatible entre las K más cercanas, no solo en la más cercana.

Dos capas casi pegadas (como las superficies superpuestas del FBX de una cabeza): A en z = 0 con la normal +Z («piel») y B en z = 0,03 con la
normal −Z («azul»). Un punto entre ambas con normal +Z tiene a veces como muestra más cercana una de B, que la normal rechaza."""
import numpy as np

from afm.colorroles import RolesPackage
from afm.colorsrc import NORMAL_CANDIDATES, ColorSource
from afm.palette import Palette, Role

N = 10


def _layers(gap=0.03) -> ColorSource:
    V, F, uv = [], [], []

    def skin(z, flip, u):
        base = len(V)
        for j in range(N + 1):
            for i in range(N + 1):
                V.append((i - N / 2, j - N / 2, z))
        for j in range(N):
            for i in range(N):
                a, b, c, d = (base + j * (N + 1) + i, base + j * (N + 1) + i + 1, base + (j + 1) * (N + 1) + i + 1, base + (j + 1) * (N + 1) + i)
                for t in [(a, b, c), (a, c, d)]:
                    F.append(t[::-1] if flip else t)
                    uv.append([(u, 0.5)] * 3)

    skin(0.0, False, 0.25)           # piel, normal +Z
    skin(gap, True, 0.75)            # azul, normal −Z
    pkg = RolesPackage(np.asarray(V, np.float32), np.asarray(F, np.int32), np.asarray(uv, np.float32), np.array([[0, 1]], np.uint8), ["piel", "azul"], {})
    pal = Palette([Role("piel", "Piel", "#EABF97"), Role("azul", "Azul", "#000080")])
    return ColorSource(pkg.to_textured(pal), spacing_mm=0.2)


def _grid(z, n=400, seed=1):
    rng = np.random.default_rng(seed)
    P = np.column_stack([rng.uniform(-4, 4, n), rng.uniform(-4, 4, n), np.full(n, z)])
    return P


def _rgb(src, rol):
    return tuple(int(x) for x in src._lut[src.label_roles.index(rol)])


def test_nearest_sample_alone_misses_the_compatible_layer():
    src = _layers()
    P = _grid(0.02)
    up = np.tile([0, 0, 1.0], (len(P), 1))
    old = src.query(P, up, candidates=1)
    new = src.query(P, up)
    assert NORMAL_CANDIDATES > 1
    assert not old.has_origin.all(), "el caso de prueba tiene que fallar con la muestra más cercana sola"
    assert new.has_origin.all()                                    # la capa A (normal +Z) está a 0,02 mm: siempre hay una compatible
    assert {tuple(c) for c in new.rgb} == {_rgb(src, "piel")}       # el color sale de la muestra elegida, no de la más cercana
    assert (new.label == src.label_roles.index("piel")).all()
    assert (new.dist <= 0.15).all() and (src.face_normals[new.face] @ [0, 0, 1.0] > 0.3).all()


def test_points_that_already_had_origin_do_not_change():
    src = _layers()
    P = _grid(0.02, seed=2)
    up = np.tile([0, 0, 1.0], (len(P), 1))
    old, new = src.query(P, up, candidates=1), src.query(P, up)
    keep = old.has_origin
    assert keep.any() and not keep.all()
    assert np.array_equal(new.rgb[keep], old.rgb[keep]) and np.array_equal(new.face[keep], old.face[keep]) and np.allclose(new.dist[keep], old.dist[keep])


def test_opposite_normal_picks_the_other_layer():
    src = _layers()
    P = _grid(0.02, seed=3)
    q = src.query(P, np.tile([0, 0, -1.0], (len(P), 1)))
    assert q.has_origin.all() and {tuple(c) for c in q.rgb} == {_rgb(src, "azul")}


def test_no_compatible_sample_within_eps_stays_without_origin():
    src = _layers()
    P = _grid(0.5)                                                  # a 0,5 mm de las dos capas: más lejos que eps (0,15)
    q = src.query(P, np.tile([0, 0, 1.0], (len(P), 1)))
    assert not q.has_origin.any()
    q2 = src.query(_grid(0.0), np.tile([1.0, 0, 0], (400, 1)))      # a ras de la capa pero con una normal perpendicular a las dos
    assert not q2.has_origin.any()


def test_without_normals_nothing_changes():
    src = _layers()
    P = _grid(0.02)
    a, b = src.query(P), src.query(P, candidates=1)
    assert np.array_equal(a.rgb, b.rgb) and np.array_equal(a.has_origin, b.has_origin)


def test_orphan_brush_only_sees_points_that_remain_without_origin():
    from afm.retouch import Stroke

    src = _layers()
    src.paint([Stroke((0, 0, 0.5), 3.0, "azul", huerfana=True)])
    P = np.vstack([_grid(0.02, 50), _grid(0.5, 50)])
    q = src.query(P, np.tile([0, 0, 1.0], (len(P), 1)))
    assert q.has_origin[:50].all() and not q.assigned[:50].any()      # los recuperados por la búsqueda ya tienen origen: no se pintan
    assert q.assigned[50:][np.linalg.norm(P[50:, :2], axis=1) < 2.9].all()
