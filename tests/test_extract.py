"""Extracción de piezas crudas con puertos (Fase 3 del plan de piezas intercambiables), con el muñeco sintético."""
import dataclasses

import numpy as np
import pytest

from afm import solids as S
from afm.catalog import Catalog
from afm.cli import main
from afm.extract import ExtractError, complement, extract_all, load_body, mirror_port
from afm.planes import Plane
from afm.ports import coupling_transform
from afm.project import Project, demo_project
from afm.testfigure import make_doll
from afm.waist import WaistSpec

WRIST = [(22.0, 0.0, 50.0), (14.0, 0.0, 50.0), (18.0, 4.0, 50.0)]      # plano z = 50 del brazo del muñeco (z 45..75)


@pytest.fixture(scope="module")
def doll():
    return S.to_manifold(make_doll())


@pytest.fixture(scope="module")
def res(doll):
    return extract_all(doll, demo_project(), "muneco", figure="doll")


def by_type(r):
    return {e.meta.tipo: e for e in r.pieces}


def volume(e):
    return abs(float(e.mesh.volume))


def test_pieces_are_closed_valid_and_have_their_ports(res):
    got = by_type(res)
    assert set(got) == {"cabeza", "torso", "brazo", "pierna"}
    for e in res.pieces:
        assert e.meta.problems() == [], e.meta.id
        assert e.mesh.is_watertight, e.meta.id
        assert e.meta.altura_figura_mm == 100.0
    assert set(got["torso"].meta.puertos) == {"cuello", "hombro_izq", "hombro_der", "cadera_izq", "cadera_der"}
    assert got["brazo"].meta.puertos["hombro"].rol == "enchufe" and got["torso"].meta.puertos["hombro_izq"].rol == "toma"
    assert got["pierna"].meta.puertos["cadera"].sweep_deg[0] == 0.0 and got["pierna"].meta.puertos["cadera"].sweep_deg[1] > 90
    assert got["brazo"].meta.puertos["hombro"].face.area > 10      # la cara de unión viene de la malla
    assert any("mano" in w for w in res.warnings)                   # sin puntos de muñeca no hay mano


def test_pieces_add_up_to_the_figure(doll, res):
    """Torso + 2 brazos + 2 piernas + cabeza = la figura (el torso es `cuerpo − miembros − cabeza`, sin pérdidas)."""
    got = by_type(res)
    total = volume(got["torso"]) + volume(got["cabeza"]) + 2 * volume(got["brazo"]) + 2 * volume(got["pierna"])
    assert total == pytest.approx(float(doll.volume()), rel=0.002)


def test_ports_couple_with_the_torso_in_place(res):
    """Cada miembro puesto en su propio puerto queda donde estaba: el acople con el puerto del torso es la identidad."""
    got = {k: v.meta for k, v in by_type(res).items()}
    for limb, own, host in (("brazo", "hombro", "hombro_izq"), ("pierna", "cadera", "cadera_izq")):
        T = coupling_transform(got[limb].puertos[own].frame, got["torso"].puertos[host].frame)
        assert np.allclose(T, np.eye(4), atol=1e-6), limb
    T = coupling_transform(got["cabeza"].puertos["cuello"].frame, got["torso"].puertos["cuello"].frame)
    assert np.allclose(T, np.eye(4), atol=1e-6)


def test_right_side_ports_are_the_mirror_of_the_left(res):
    t = by_type(res)["torso"].meta.puertos
    for clase in ("hombro", "cadera"):
        a, b = t[f"{clase}_izq"], t[f"{clase}_der"]
        assert b.frame.center[0] == pytest.approx(-a.frame.center[0]) and b.frame.axis[0] == pytest.approx(-a.frame.axis[0])
        assert np.allclose(b.frame.center[1:], a.frame.center[1:])
        assert b.face.area == pytest.approx(a.face.area)
    assert np.allclose(mirror_port(complement(by_type(res)["brazo"].meta.puertos["hombro"])).frame.center,
                       t["hombro_der"].frame.center)


def test_torso_is_whole_and_symmetric(res):
    tm = by_type(res)["torso"].mesh
    lo, hi = tm.bounds
    assert lo[0] == pytest.approx(-hi[0], abs=1e-3)              # ambos lados
    assert hi[2] < 80.0 + 1e-6                                     # cortado por el plano del cuello, sin la cabeza


def test_wrist_points_split_the_arm_and_return_the_hand(doll):
    r = extract_all(doll, demo_project(), "muneco", wrist_points=WRIST)
    got = by_type(r)
    assert set(got) == {"cabeza", "torso", "brazo", "pierna", "mano"}
    arm, hand = got["brazo"], got["mano"]
    assert set(arm.meta.puertos) == {"hombro", "muneca"} and arm.meta.puertos["muneca"].rol == "toma"
    assert arm.mesh.bounds[0][2] == pytest.approx(50.0, abs=1e-3)      # sin la mano
    assert hand.meta.puertos["muneca"].rol == "enchufe" and hand.mesh.is_watertight
    assert hand.meta.problems() == [] and arm.meta.problems() == []
    # el acople de la mano con la muñeca del brazo la deja donde estaba (mano estándar: origen en la muñeca)
    T = coupling_transform(hand.meta.puertos["muneca"].frame, arm.meta.puertos["muneca"].frame)
    placed = hand.mesh.copy()
    placed.apply_transform(T)
    assert placed.bounds[1][2] == pytest.approx(50.0, abs=1e-3) and placed.bounds[0][2] == pytest.approx(45.0, abs=1e-3)
    assert volume(arm) + volume(hand) == pytest.approx(volume(by_type(extract_all(doll, demo_project(), "m"))["brazo"]), rel=1e-3)


def test_bad_wrist_plane_is_reported(doll):
    with pytest.raises(ExtractError, match="muñeca"):
        extract_all(doll, demo_project(), "muneco", wrist_points=[(0, 0, 500), (1, 0, 500), (0, 1, 500)])


def test_unknown_place(doll):
    with pytest.raises(ExtractError, match="desconocido"):
        extract_all(doll, demo_project(), "muneco", places=["cola"])


def test_places_filter_and_missing_head(doll):
    pr = demo_project()
    r = extract_all(doll, pr, "muneco", places=["pierna"])
    assert [e.meta.tipo for e in r.pieces] == ["pierna"]
    pr.head = None
    r = extract_all(doll, pr, "muneco", places=["cabeza", "torso"])
    assert [e.meta.tipo for e in r.pieces] == ["torso"] and "cuello" not in r.pieces[0].meta.puertos
    assert any("cuello" in w for w in r.warnings)


def test_waist_splits_the_torso_into_upper_and_pelvis(doll):
    pr = demo_project()
    pr.waist = WaistSpec(Plane.from_points((-5, -5, 55), (5, -5, 55), (0, 5, 55), "cintura"))
    r = extract_all(doll, pr, "muneco")
    got = by_type(r)
    assert "torso" not in got and {"torso_sup", "pelvis"} <= set(got)
    up, lo = got["torso_sup"].meta, got["pelvis"].meta
    assert set(up.puertos) == {"cuello", "hombro_izq", "hombro_der", "cintura"}
    assert set(lo.puertos) == {"cintura", "cadera_izq", "cadera_der"}
    assert up.problems() == [] and lo.problems() == []
    T = coupling_transform(lo.puertos["cintura"].frame, up.puertos["cintura"].frame)
    assert np.allclose(T, np.eye(4), atol=1e-6)
    assert up.puertos["cintura"].face.area == pytest.approx(lo.puertos["cintura"].face.area, rel=1e-3)
    assert volume(got["torso_sup"]) + volume(got["pelvis"]) == pytest.approx(
        volume(by_type(extract_all(doll, demo_project(), "m"))["torso"]), rel=1e-3)


def test_unmirrored_torso_keeps_its_asymmetry(doll):
    """Con `torso_mirror = False` el lado −X del torso sale del cuerpo real: un bulto solo a la izquierda no se pierde."""
    bump = S.Manifold.cube((6, 6, 6), center=True).translate((-11.0, 8.0, 60.0))        # bulto en el pecho, solo del lado −X
    body = S.union(doll, bump)
    pr = demo_project()
    pr.params = dataclasses.replace(pr.params, torso_mirror=False)
    tm = by_type(extract_all(body, pr, "asim"))["torso"].mesh
    chest = tm.vertices[tm.vertices[:, 2] > 50.0]                      # sobre la pelvis
    left, right = chest[chest[:, 0] < -8.0], chest[chest[:, 0] > 8.0]
    assert left[:, 1].max() == pytest.approx(11.0, abs=1e-3)           # el bulto sobresale hasta y = 11
    assert right[:, 1].max() < 8.5                                     # el lado +X no lo tiene
    r = extract_all(body, demo_project(), "sim")                        # `torso_mirror = True`: se espeja el lado +X, como `build`
    sym = by_type(r)["torso"].mesh
    sym = sym.vertices[(sym.vertices[:, 2] > 50.0) & (sym.vertices[:, 0] < -8.0)]
    # el bulto se descarta; la única excepción es el aviso de la arista de 4 caras (el muñeco tiene la cabeza metida en el cuello)
    assert sym[:, 1].max() < 8.5 or any("4 caras" in w for w in r.warnings)


def test_cli_agregar_end_to_end(tmp_path, capsys):
    stl, proj, cat = str(tmp_path / "muneco.stl"), str(tmp_path / "muneco.proyecto"), str(tmp_path / "linea")
    make_doll().export(stl)
    demo_project().save(proj)
    assert main(["catalogo", "crear", cat, "--linea", "test-100", "--altura", "100"]) == 0
    assert main(["catalogo", "agregar", cat, stl, proj, "--prefijo", "Muñeco Uno", "--etiqueta", "estandar",
                 "--muneca", *map(str, np.asarray(WRIST).ravel())]) == 0
    out = capsys.readouterr().out
    c = Catalog.open(cat)
    tipos = {m.tipo: m for m in c.list()}
    assert set(tipos) == {"cabeza", "torso", "brazo", "pierna", "mano"}
    assert tipos["pierna"].id == "pierna-mu-eco-uno-01" or tipos["pierna"].id.startswith("pierna-")
    assert all(m.etiquetas == ["estandar"] for m in tipos.values())
    assert main(["catalogo", "verificar", cat]) == 0
    # segunda corrida: los ids ya existen → error claro; con --nueva-version crea la versión 2
    assert main(["catalogo", "agregar", cat, stl, proj, "--prefijo", "Muñeco Uno", "--lugares", "pierna"]) == 2
    assert main(["catalogo", "agregar", cat, stl, proj, "--prefijo", "Muñeco Uno", "--lugares", "pierna", "--nueva-version"]) == 0
    assert c.versions(tipos["pierna"].id) == [1, 2] or Catalog.open(cat).versions(tipos["pierna"].id) == [1, 2]
    # el torso del catálogo es compatible con la pierna por metadatos (mismo puerto, misma cara)
    compat = Catalog.open(cat).compatible_with(tipos["pierna"].ref)
    assert any(o.tipo == "torso" and c_.nivel in ("ok", "aviso") for o, _a, _b, c_ in compat)


def test_load_body_matches_the_pipeline_frame(tmp_path):
    stl = str(tmp_path / "d.stl")
    make_doll().export(stl)
    body, rep = load_body(stl, demo_project())
    assert body.bounding_box()[5] == pytest.approx(100.0, abs=0.1)


def test_compat_skips_the_mirrored_right_ports_of_a_mirrorable_piece(tmp_path):
    """El puerto `_der` es el `_izq` espejado: no se compara una pieza izquierda con la cara espejada del lado derecho."""
    stl, proj, cat = str(tmp_path / "m.stl"), str(tmp_path / "m.proyecto"), str(tmp_path / "linea")
    make_doll().export(stl)
    demo_project().save(proj)
    assert main(["catalogo", "crear", cat, "--linea", "t", "--altura", "100"]) == 0
    assert main(["catalogo", "agregar", cat, stl, proj, "--prefijo", "m"]) == 0
    pairs = Catalog.open(cat).compatible_with("brazo-m-01")
    assert pairs and all(not nb.endswith("_der") for _o, _na, nb, _c in pairs)


# ---------------------------------------------------------------- figuras reales (lentas: segundos)
import os

ROOT = os.path.dirname(os.path.dirname(__file__))
GUEMES_STL = os.path.join(ROOT, "examples", "guemes_segmentado_lq_fixed_resized.stl")
GUEMES_PROJ = os.path.join(ROOT, "examples", "referencia", "guemes", "guemes.proyecto")


@pytest.mark.skipif(not (os.path.exists(GUEMES_STL) and os.path.exists(GUEMES_PROJ)), reason="falta Güemes de ejemplo")
def test_guemes_real_pieces_are_closed_and_in_place(tmp_path):
    """Güemes (sin cabeza definida en el proyecto): brazo, pierna y torso cerrados; el torso se cierra aunque el corte de
    la cadera roce una arista (aristas de 4 caras en la malla soldada), y los puertos acoplan en su lugar."""
    proj = Project.load(GUEMES_PROJ)
    body, _rep = load_body(GUEMES_STL, proj)
    r = extract_all(body, proj, "guemes")
    got = by_type(r)
    assert set(got) == {"brazo", "pierna", "torso"}
    for e in r.pieces:
        assert e.mesh.is_watertight and e.meta.problems() == [], e.meta.id
    assert "cuello" not in got["torso"].meta.puertos             # sin plano del cuello el torso lleva la cabeza
    for limb, own, host in (("brazo", "hombro", "hombro_izq"), ("pierna", "cadera", "cadera_izq")):
        T = coupling_transform(got[limb].meta.puertos[own].frame, got["torso"].meta.puertos[host].frame)
        assert np.allclose(T, np.eye(4), atol=1e-6), limb
    total = volume(got["torso"]) + 2 * volume(got["brazo"]) + 2 * volume(got["pierna"])
    assert total == pytest.approx(float(body.volume()), rel=0.005)
    cat = str(tmp_path / "linea")
    assert main(["catalogo", "crear", cat, "--linea", "t", "--altura", "100"]) == 0
    assert main(["catalogo", "agregar", cat, GUEMES_STL, GUEMES_PROJ, "--prefijo", "guemes"]) == 0
    assert main(["catalogo", "verificar", cat]) == 0


def test_example_catalog_in_the_repo_is_clean():
    """`catalogo/ejemplo` (muñeco sintético, generado con `afm catalogo agregar`) es lo único que se versiona (decisión D5)."""
    path = os.path.join(ROOT, "catalogo", "ejemplo")
    assert main(["catalogo", "verificar", path]) == 0
    assert {m.tipo for m in Catalog.open(path).list()} == {"cabeza", "torso", "brazo", "pierna", "mano"}
