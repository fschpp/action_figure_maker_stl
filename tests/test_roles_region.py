"""O10 · Los clusters de color salen de lo que la pieza tiene, no del busto entero (`docs/PLAN_orientacion_color_cabeza.md`).

`colorroles.propose(region=, min_share=)`, `prune_small_roles`, `colorpiece.piece_region` y `RolesWizard.load(piece_ref=)`. Con los datos reales de Bolívar
(`examples/bolivar_cabeza_lq.fbx`, `tests/data/bolivar_cabeza_pieza.npz`) se comprueba el criterio del plan: ≥ 8 clusters con ≥ 2 % de la cabeza y sin los
colores del uniforme (rojo, dorado). Lógica sin GTK."""
import os
from pathlib import Path

import numpy as np
import pytest
import trimesh

from afm import colorpiece, colorroles as CR
from afm.frame import FigureFrame
from afm.gui import roles_wizard as RW

from test_cut_piece_orientation import NECK, SAVED
from test_gui_roles_wizard import HEAD_ID, decide_by_color, head_mesh, line, make_ctrl, two_color_tm  # noqa: F401  (fixtures)

ROOT = Path(__file__).parent.parent
FBX = ROOT / "examples" / "bolivar_cabeza_lq.fbx"
DATA = Path(__file__).parent / "data"
REF = f"{HEAD_ID}@1"
UNIFORM_RED = (162, 59, 43)           # #A23B2B: el paño rojo del uniforme del busto (0,0 % de la cabeza, 7 % de lo que cae en el cuello con solo `head_region`)


def _top_half(tm, margin=0.15):
    """Triángulos enteros por encima del corte de color (con margen: los que lo cruzan traen un poco de los dos colores)."""
    z = np.asarray(tm.vertices, float)[np.asarray(tm.faces, np.int64)][:, :, 2]
    zmin, zmax = float(z.min()), float(z.max())
    return z.min(axis=1) > zmin + (0.5 + margin) * (zmax - zmin)


def _de(a, b):
    return float(np.linalg.norm(CR._lab(np.array(a, float)) - CR._lab(np.array(b, float))))


# ------------------------------------------------------------------ propose(region=)
def test_clusters_come_only_from_the_region(head_mesh):  # noqa: F811
    tm = two_color_tm(head_mesh, frame=False)
    whole = CR.propose(tm, None, k=4)
    assert whole.k == 2 and whole.region is None and whole.merged == 0
    top = _top_half(tm)
    part = CR.propose(tm, None, k=4, region=top)
    assert part.k == 1 and float(part.share[0]) == pytest.approx(100.0)                  # la mitad baja (azul) no gasta un cluster
    assert _de(part.centers_rgb[0], (200, 30, 30)) < 8
    assert part.region is not None and part.region.shape == top.shape


def test_a_region_with_no_surface_is_refused(head_mesh):  # noqa: F811
    tm = two_color_tm(head_mesh, frame=False)
    with pytest.raises(CR.RolesError, match="región"):
        CR.propose(tm, None, k=2, region=np.zeros(len(tm.faces), bool))
    with pytest.raises(CR.RolesError, match="región"):
        CR.propose(tm, None, k=2, region=np.ones(3, bool))


def test_small_clusters_are_merged_with_the_closest_one():
    lab = np.array([[10.0, 0, 0]] * 500 + [[12.0, 0, 0]] * 495 + [[40.0, 30, 30]] * 5)       # 50 % / 49,5 % / 0,5 %
    lab_of = np.array([0] * 500 + [1] * 495 + [2] * 5)
    out, merged = CR._merge_small(lab, lab_of, 3, 1.0)
    assert merged == 1 and set(out.tolist()) == {0, 1}                                   # el 2 se fundió con el más parecido…
    assert set(out[-5:].tolist()) == {1}                                                 # …el 1 (L 12 está más cerca de 40 que L 10)
    same, none = CR._merge_small(lab, lab_of, 3, 0.1)
    assert none == 0 and np.array_equal(same, lab_of)
    one, _ = CR._merge_small(lab[:5], np.zeros(5, int), 1, 99.0)                         # nunca se queda sin clusters
    assert set(one.tolist()) == {0}


def test_merging_is_off_for_the_whole_mesh(head_mesh):  # noqa: F811
    tm = two_color_tm(head_mesh, frame=False)
    assert CR.propose(tm, None, k=2).k == CR.propose(tm, None, k=2, min_share=0.0).k == 2


# ------------------------------------------------------------------ roles que sirven a la pieza
@pytest.fixture()
def pkg_two_roles(head_mesh):  # noqa: F811
    tm = two_color_tm(head_mesh, frame=False)
    prop = CR.propose(tm, None, k=2)
    red = int(np.argmin([_de(c, (200, 30, 30)) for c in prop.centers_rgb]))
    return tm, CR.build_package(tm, prop, {red: "rojo", 1 - red: "azul"})


def test_role_shares_are_measured_on_the_region(pkg_two_roles):
    tm, pkg = pkg_two_roles
    top = _top_half(tm)
    whole = CR.role_shares(pkg)
    assert set(whole) == {"rojo", "azul"} and sum(whole.values()) == pytest.approx(100.0, abs=0.5)
    onlytop = CR.role_shares(pkg, top)
    assert onlytop["rojo"] > 90 and onlytop["azul"] < 10


def test_roles_under_the_minimum_are_not_kept(pkg_two_roles):
    tm, pkg = pkg_two_roles
    top = _top_half(tm)
    kept, dropped = CR.prune_small_roles(pkg, top, min_pct=CR.ROLE_MIN_PCT * 20)         # azul < 10 %
    assert dropped == ["azul"] and kept.roles == ["rojo"]
    assert set(np.unique(kept.labels).tolist()) <= {0, CR.NO_ROLE} and (kept.labels == CR.NO_ROLE).any()
    same, none = CR.prune_small_roles(pkg, None)                                           # toda la malla: nada < 0,5 %
    assert none == [] and same is pkg
    keep_all, none2 = CR.prune_small_roles(pkg, top, min_pct=101.0)                        # no se deja una pieza sin ningún rol
    assert none2 == [] and keep_all is pkg


# ------------------------------------------------------------------ el asistente
@pytest.fixture()
def identity_align(monkeypatch):
    """`align_fbx_to_piece` con la pose identidad (el FBX sintético ya está en el marco de la pieza): no se paga la alineación real."""
    calls = []

    def fake(tm, piece_stl, **kw):
        calls.append(kw)
        return FigureFrame(np.eye(4), 1.0, {"ok": True, "media_mm": 0.0})

    monkeypatch.setattr(colorpiece, "align_fbx_to_piece", fake)
    return calls


def test_wizard_aligns_first_and_clusters_only_the_piece(line, head_mesh, identity_align):  # noqa: F811
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(two_color_tm(head_mesh, frame=False), {"nombre": "cabeza.fbx"}, piece_ref=REF)
    assert identity_align and identity_align[0].get("require_ok") is False                # alinea sin exigir el frente: la región no depende del giro de 180°
    assert w.region is not None and w.region_ref == REF and w.proposal.region is w.region
    assert w.proposal.k <= RW.REGION_K and w.proposal.merged >= 0
    assert w.region.any()                                                                 # el FBX sintético ES la pieza: toda su superficie está en la región


def test_wizard_default_k_depends_on_the_target(line, head_mesh, identity_align):  # noqa: F811
    tm = two_color_tm(head_mesh, frame=False)
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(tm)
    assert w.region is None and w.proposal.k == 2 and RW.FIGURE_K == 10 and RW.REGION_K == 8       # sin pieza: la malla entera, K = 10 como siempre
    assert w.dropped_roles == []


def test_package_of_a_piece_uses_the_wider_filter_and_drops_small_roles(line, head_mesh, identity_align, monkeypatch):  # noqa: F811
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(two_color_tm(head_mesh, frame=False), piece_ref=REF)
    decide_by_color(w)
    seen = {}
    real = CR.build_package

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(CR, "build_package", spy)
    w.confirm_suggestions()
    pkg = w.package()
    assert seen["filter_px"] == CR.PIECE_FILTER_PX == 7
    assert pkg.roles and w.dropped_roles == [r for r in w.dropped_roles if r not in pkg.roles]
    w.region = None
    w.package()
    assert seen["filter_px"] == CR.FILTER_PX == 5                                         # una figura entera conserva el filtro de siempre


def test_a_piece_that_is_not_a_head_clusters_the_whole_fbx_and_says_so(line, head_mesh, identity_align):  # noqa: F811
    from afm.catalog import Catalog

    cat = Catalog.open(line)
    other = next((m.ref for m in cat._metas() if m.tipo != "cabeza"), None)
    if other is None:
        pytest.skip("el catálogo de ejemplo no tiene otra pieza que no sea una cabeza")
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(two_color_tm(head_mesh, frame=False), piece_ref=other)
    assert w.region is None and w.proposal.k == 2


# ------------------------------------------------------------------ datos reales de Bolívar
@pytest.fixture(scope="module")
def bolivar():
    if not FBX.exists():
        pytest.skip("falta examples/bolivar_cabeza_lq.fbx")
    from afm.fbxio import load_textured

    tm = load_textured(str(FBX))
    z = np.load(DATA / "bolivar_cabeza_pieza.npz")
    piece = trimesh.Trimesh(z["vertices"].astype(float), z["faces"], process=False)
    V = np.asarray(tm.vertices, float) @ SAVED[:3, :3].T + SAVED[:3, 3]                    # el calce guardado: la región casi no cambia con el giro de 180°
    pf = colorpiece.piece_front(NECK, piece)
    return tm, piece, V, pf


def test_piece_region_leaves_out_the_uniform(bolivar):
    tm, piece, V, pf = bolivar
    wide = colorpiece.head_region(V, tm.faces, pf, np.asarray(piece.vertices))
    near = colorpiece.piece_region(V, tm.faces, pf, piece)
    assert near.sum() < wide.sum() and (near & ~wide).sum() == 0                            # es un subconjunto de la región de O04
    assert near.sum() > 0.3 * wide.sum()                                                     # y sigue siendo la cabeza
    assert np.array_equal(near, colorpiece.piece_region(V, tm.faces, pf, piece))            # determinista


def test_bolivar_clusters_serve_the_head_not_the_bust(bolivar):
    """Criterio del plan §6 (O10): clusters que sirven a la cabeza (ninguno bajo el 1 %, casi todos sobre el 2 %) y ningún rojo del uniforme. Con el calce guardado
    (no el mejor) la región cambia unas décimas: el cluster más chico queda en 1,8 %; con el calce alineado, en 3 %."""
    tm, piece, V, pf = bolivar
    region = colorpiece.piece_region(V, tm.faces, pf, piece)
    whole = CR.propose(tm, None, k=10)
    head = CR.propose(tm, None, k=CR.REGION_K, region=region, min_share=CR.MERGE_MIN_PCT)
    assert head.k >= 7 and float(head.share.min()) >= CR.MERGE_MIN_PCT and int((head.share >= 2.0).sum()) >= 6
    assert min(_de(c, UNIFORM_RED) for c in head.centers_rgb) > 12
    assert min(_de(c, UNIFORM_RED) for c in whole.centers_rgb) < 6                          # el busto entero sí gasta un cluster en el paño rojo
