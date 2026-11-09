"""Capacidad del anfitrión: profundidad detrás de la cara y cabezas enfrentadas (plan de compatibilidad, Fase 3, §4.4)."""
import dataclasses
import os
import shutil

import numpy as np
import pytest
from shapely.geometry import Point

from afm import capacity as C
from afm import solids as S
from afm.catalog import Catalog
from afm.extract import complement, extract_all, load_body, mirror_port
from afm.params import Params
from afm.ports import Port, PortFace, PortFrame, port_compat
from afm.project import demo_project
from afm.testfigure import make_doll

M = S.Manifold
EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
P = Params()


def disc(r):
    return PortFace.from_shape(Point(0, 0).buffer(r, 64))


def socket(clase="hombro", x=10.0, z=20.0, r=5.0, cap=None):
    """Toma en la cara superior (z = `z`) de un cuerpo que ocupa z < `z`; el eje sale hacia +Z."""
    return Port(clase, "toma", PortFrame((x, 0.0, z), (0, 0, 1), (1, 0, 0)), disc(r), capacity=cap)


def plug(clase="hombro", x=10.0, z=20.0, r=5.0):
    return Port(clase, "enchufe", PortFrame((x, 0.0, z), (0, 0, -1), (1, 0, 0)), disc(r))


def body(h=20.0, r=12.0, cx=10.0):
    """Cilindro vertical de altura `h` centrado en x = `cx`: la cara de la toma es su tapa."""
    return S.to_trimesh(M.cylinder(h, r, r, 96).translate((cx, 0, 0)))


# ---------------------------------------------------------------------------------------------------- demanda
def test_demand_follows_the_line_pin_and_skips_classes_without_pin():
    d = C.demand("hombro", P)
    pin = P.shoulder_pin
    assert d["profundidad_mm"] == pytest.approx(pin.shaft_len + pin.head_h + P.clearance)
    assert d["pared_min_mm"] == P.min_wall and "separacion_min_mm" not in d    # el motor no valida cabezas enfrentadas en el hombro
    assert C.demand("cadera", P)["separacion_min_mm"] == pytest.approx(2 * P.clearance)
    assert "separacion_min_mm" not in C.demand("cuello", P)
    assert C.demand("muneca", P) is None
    bigger = dataclasses.replace(P, shoulder_pin=dataclasses.replace(pin, shaft_len=pin.shaft_len + 2.0))
    assert C.demand("hombro", bigger)["profundidad_mm"] == pytest.approx(d["profundidad_mm"] + 2.0)


def test_head_gap_is_the_engines_hip_pin_walls_formula():
    from afm.pipeline import hip_pin_walls

    pin = P.hip_pin
    for x, dx in ((13.3, 1.0), (7.7, -1.0), (7.7, 1.0), (6.0, -0.6)):
        d = np.array([dx, np.sqrt(1 - dx ** 2), 0.0])
        ref = hip_pin_walls(pin, np.array([x, 0.0, 0.0]), d, P.clearance)["separacion_cabezas_mm"]
        assert C.head_gap(pin, x, dx) == pytest.approx(ref)


# ---------------------------------------------------------------------------------------------------- medir
def test_measure_depth_through_the_body():
    cap = C.measure(body(20.0), socket(), P)
    assert cap["profundidad_mm"] == pytest.approx(20.0, abs=0.05)
    assert "x_mm" not in cap                                       # el hombro no lleva el dato de cabezas enfrentadas
    assert cap["huella_radio_mm"] == pytest.approx(P.shoulder_pin.head_d / 2, abs=1e-3)
    assert cap["rayos"] > 10


def test_measure_takes_the_thinnest_point_of_the_footprint():
    """Un hueco desde abajo deja 6 mm de cuerpo bajo una parte de la huella: la profundidad es la del punto más delgado."""
    cut = M.cube((4, 40, 16), center=True).translate((10 + 2.5, 0, 6))                # saca z de −2 a 14, en x de 10,5 a 14,5
    thin = S.to_trimesh(S.difference(M.cylinder(20, 12, 12, 96).translate((10, 0, 0)), cut))
    assert C.measure(thin, socket(r=6.0), P)["profundidad_mm"] == pytest.approx(6.0, abs=0.05)
    assert C.measure(body(20.0), socket(r=6.0), P)["profundidad_mm"] == pytest.approx(20.0, abs=0.05)


def test_measure_ignores_rays_near_the_edge_of_the_face():
    """Cuerpo que se achica hacia el borde de la cara (piel curva): los rayos pegados al borde salen por el costado y no
    cuentan; los que arrancan a más de `min_wall` del borde sí."""
    cone = S.to_trimesh(M.cylinder(20, 12, 4, 96).translate((10, 0, 0)))                # radio 12 abajo, 4 arriba (z = 20)
    face_r = 4.0
    near = C.measure(cone, socket(r=face_r), P)
    assert near["profundidad_mm"] > 15.0                                                 # el borde de la tapa se ignoró
    assert C._footprint(disc(face_r), 3.3, margin=P.min_wall).max() <= face_r - P.min_wall + 1e-6
    assert len(C._footprint(disc(1.0), 3.3, margin=P.min_wall)) == 1                      # cara más chica que la pared: queda el centro


def test_measure_ignores_plugs_classes_without_pin_and_rays_that_miss():
    assert C.measure(body(), plug(), P) is None                    # solo las tomas tienen capacidad
    assert C.measure(body(), socket("muneca"), P) is None          # la muñeca no lleva pasador
    far = Port("hombro", "toma", PortFrame((100.0, 0, 20.0), (0, 0, 1), (1, 0, 0)), disc(5.0))
    assert C.measure(body(), far, P) is None                       # ningún rayo toca el cuerpo


def test_measure_with_a_face_smaller_than_the_head_uses_the_points_that_fit():
    cap = C.measure(body(20.0), socket(r=1.0), P)                  # cara Ø2 y cabeza Ø6,6: queda al menos el centro
    assert cap is not None and cap["profundidad_mm"] == pytest.approx(20.0, abs=0.05)


# ---------------------------------------------------------------------------------------------------- comparar
def need(clase="hombro"):
    d = C.demand(clase, P)
    return d["profundidad_mm"] + d["pared_min_mm"]


def cap_of(prof, x=None, dir_x=None):
    c = {"profundidad_mm": prof, "huella_radio_mm": P.shoulder_pin.head_d / 2}
    if x is not None:
        c.update(x_mm=x, dir_x=dir_x)
    return c


def test_check_levels_for_depth():
    ok = C.check(plug(), socket(cap=cap_of(need() + 2.0)), P)
    assert ok[0] == [] and ok[1] == []
    near = C.check(plug(), socket(cap=cap_of(need() + 0.2)), P)
    assert near[0] == [] and any("poca profundidad" in a for a in near[1])
    no = C.check(plug(), socket(cap=cap_of(need() - 0.3)), P)
    assert len(no[0]) == 1 and "detrás de la cara" in no[0][0]
    assert no[2]["capacidad_profundidad"] == pytest.approx(-0.3)


def test_check_heads_facing_each_other_only_in_the_hip():
    def hip(x, dx):
        return socket("cadera", cap={"profundidad_mm": 30.0, "huella_radio_mm": P.hip_pin.head_d / 2, "x_mm": x, "dir_x": dx})

    away = C.check(plug("cadera"), hip(13.3, +1.0), P)                  # el pasador se aleja del plano: no hay cruce posible
    assert away[0] == [] and away[1] == []
    for x in np.linspace(0.5, 12.0, 200):                               # x mínimo con la separación ≥ 2·holgura
        if C.head_gap(P.hip_pin, x, -1.0) >= 2 * P.clearance:
            x_ok = x
            break
    bad = C.check(plug("cadera"), hip(x_ok - 0.3, -1.0), P)
    assert len(bad[0]) == 1 and "dos lados" in bad[0][0]
    assert C.check(plug("cadera"), hip(x_ok + 2.0, -1.0), P)[0] == []
    tight = C.check(plug("cadera"), hip(x_ok + 0.1, -1.0), P)
    assert tight[0] == [] and any("casi tocándose" in a for a in tight[1])
    sh = socket("hombro", cap={"profundidad_mm": 30.0, "huella_radio_mm": P.shoulder_pin.head_d / 2, "x_mm": 0.1, "dir_x": -1.0})
    assert C.check(plug("hombro"), sh, P)[0] == []                      # el hombro no se juzga por cabezas enfrentadas
    nocap = C.check(plug("cuello"), socket("cuello", cap={"profundidad_mm": 20.0, "huella_radio_mm": 3.3}), P)
    assert nocap == ([], [], {"capacidad_profundidad": pytest.approx(20.0 - need("cuello"))})


def test_check_warns_when_measured_with_a_smaller_head():
    small = {"profundidad_mm": 20.0, "huella_radio_mm": 2.0}
    assert any("se midió con una cabeza" in a for a in C.check(plug(), socket(cap=small), P)[1])


def test_check_is_silent_without_data_and_for_pairs_that_do_not_couple():
    assert C.check(plug(), socket(), P) == ([], [], {})            # toma vieja, sin capacidad
    assert C.missing(plug(), socket(), P) is True
    assert C.missing(plug(), socket(cap=cap_of(20.0)), P) is False
    assert C.missing(plug("muneca"), socket("muneca"), P) is False
    assert C.check(plug("cadera"), socket("hombro", cap=cap_of(0.1, 0.1)), P) == ([], [], {})      # clases distintas
    assert C.check(socket(), socket(cap=cap_of(0.1)), P) == ([], [], {})                      # dos tomas


# ---------------------------------------------------------------------------------------------------- port_compat y puertos
def test_port_compat_turns_a_lack_of_capacity_into_no_and_flags_old_pieces():
    c = port_compat(plug(), socket(cap=cap_of(need() - 0.5)), params=P)
    assert c.nivel == "no" and any("detrás de la cara" in m for m in c.motivos) and not c.sin_capacidad
    assert c.to_dict()["margenes_mm"]["capacidad_profundidad"] == pytest.approx(-0.5, abs=1e-3)
    good = port_compat(plug(), socket(cap=cap_of(20.0)), params=P)
    assert good.nivel == "ok" and "sin_capacidad" not in good.to_dict()
    old = port_compat(plug(), socket(), params=P)
    assert old.nivel == "ok" and old.sin_capacidad and old.to_dict()["sin_capacidad"] is True      # no baja de nivel
    assert not port_compat(plug("muneca"), socket("muneca"), params=P).sin_capacidad


def test_capacity_survives_the_port_round_trip_and_mirroring_but_not_complement():
    cap = {"profundidad_mm": 9.5, "x_mm": 12.0, "dir_x": -1.0, "huella_radio_mm": 3.3, "rayos": 40}
    t = socket(cap=cap)
    assert Port.from_dict(t.to_dict()).capacity == cap
    assert Port.from_dict(socket().to_dict()).capacity is None and "capacidad" not in socket().to_dict()
    assert mirror_port(t).capacity == cap
    assert complement(t).capacity is None                          # el enchufe del vecino no la hereda
    assert t.transformed(np.eye(4)).capacity == cap


# ---------------------------------------------------------------------------------------------------- extracción y catálogo
@pytest.fixture(scope="module")
def doll_res():
    return extract_all(S.to_manifold(make_doll()), demo_project(), "muneco", figure="doll")


def test_extract_measures_the_capacity_of_every_socket_with_a_pin(doll_res):
    torso = next(e for e in doll_res.pieces if e.meta.tipo == "torso")
    for name in ("hombro_izq", "hombro_der", "cadera_izq", "cadera_der", "cuello"):
        cap = torso.meta.puertos[name].capacity
        assert cap is not None and cap["profundidad_mm"] > 0, name
    hip = torso.meta.puertos["cadera_izq"]
    assert hip.capacity["x_mm"] == pytest.approx(abs(hip.frame.center[0]), abs=1e-2)
    assert "x_mm" not in torso.meta.puertos["hombro_izq"].capacity
    arm = next(e for e in doll_res.pieces if e.meta.tipo == "brazo")
    assert all(p.capacity is None for p in arm.meta.puertos.values())      # los enchufes no miden capacidad


def test_dolls_own_pieces_are_never_rejected_for_capacity(doll_res):
    """La figura con sus propias piezas se arma y valida: la capacidad medida no puede dar ningún «no» a ese par."""
    by = {e.meta.tipo: e for e in doll_res.pieces}
    torso = by["torso"]
    pairs = [(by["brazo"].meta.puertos["hombro"], torso.meta.puertos["hombro_izq"]),
             (by["pierna"].meta.puertos["cadera"], torso.meta.puertos["cadera_izq"]),
             (by["cabeza"].meta.puertos["cuello"], torso.meta.puertos["cuello"])]
    for a, b in pairs:
        motivos, _, margenes = C.check(a, b, P)
        assert motivos == [] and margenes, (a.clase, motivos)


def test_catalog_keeps_the_capacity_in_the_piece_file(tmp_path, doll_res):
    root = tmp_path / "linea"
    shutil.copytree(EJEMPLO, root)
    cat = Catalog.open(str(root))
    torso = next(e for e in doll_res.pieces if e.meta.tipo == "torso")
    meta, _ = cat.add_piece(torso.mesh, dataclasses.replace(torso.meta, id="torso-con-capacidad", version=1))
    again = Catalog.open(str(root)).meta(meta.ref)
    assert again.puertos["hombro_izq"].capacity == torso.meta.puertos["hombro_izq"].capacity
    assert Catalog.open(str(root)).verify().ok
    old = Catalog.open(str(root)).meta("torso-muneco-01")           # el de ejemplo se hizo antes: sin datos, pero se lee igual
    assert old.puertos["hombro_izq"].capacity is None


# ---------------------------------------------------------------------------------------------------- catálogo ya armado
def test_measure_capacity_on_existing_pieces_and_cli(tmp_path, capsys):
    from afm.cli import main
    from afm.parts import sha256_file

    root = tmp_path / "linea"
    shutil.copytree(EJEMPLO, root)
    cat = Catalog.open(str(root))
    stl = os.path.join(str(root), "piezas", "torso", "torso-muneco-01@1.stl")
    h0 = sha256_file(stl)
    caps = cat.measure_capacity("torso-muneco-01")
    assert set(caps) == {"cuello", "hombro_izq", "hombro_der", "cadera_izq", "cadera_der"}      # sin la cintura: el ejemplo no la tiene
    assert all(c is not None and c["profundidad_mm"] > 0 for c in caps.values())
    assert Catalog.open(str(root)).meta("torso-muneco-01").puertos["cuello"].capacity is None   # sin write no se guarda
    assert main(["catalogo", "capacidad", str(root)]) == 0
    out = capsys.readouterr().out
    assert "torso-muneco-01@1" in out and "no se guardó nada" in out and "brazo-muneco-01" not in out
    assert main(["catalogo", "capacidad", str(root), "torso-muneco-01", "--escribir"]) == 0
    assert "guardado" in capsys.readouterr().out
    again = Catalog.open(str(root))
    assert again.meta("torso-muneco-01").puertos["hombro_izq"].capacity == caps["hombro_izq"]
    assert sha256_file(stl) == h0 and again.meta("torso-muneco-01").sha256 == h0              # el STL y su hash no cambian
    assert again.verify().ok
    c = port_compat(again.meta("brazo-muneco-01").puertos["hombro"], again.meta("torso-muneco-01").puertos["hombro_izq"], 100, 100, again.params)
    assert c.margenes_mm["capacidad_profundidad"] > 0 and not c.sin_capacidad


def test_head_gap_with_click_matches_the_engine_that_shifts_the_mushroom():
    """Con clic el hongo queda `disc_h` mm más adentro (`pin_shift_mm`): la capacidad tiene que contarlo igual que `_validate_hip_pin`,
    si no el catálogo dice «ok» y la figura armada falla por cabezas a menos de 2·holgura."""
    from afm.pipeline import hip_pin_walls

    pc = dataclasses.replace(P, hip_detent=True)
    shift = C.pin_shift("cadera", pc)
    assert shift == pytest.approx(pc.detent.disc_h) and C.pin_shift("cadera", P) == 0.0 and C.pin_shift("hombro", pc) == 0.0
    d = np.array([-1.0, 0.0, 0.0])
    for x in (7.7, 9.55, 12.0):
        ref = hip_pin_walls(pc.hip_pin, np.array([x, 0.0, 0.0]) + d * shift, d, pc.clearance)["separacion_cabezas_mm"]
        assert C.head_gap(pc.hip_pin, x, -1.0, shift) == pytest.approx(ref)

    def hip(x):
        return socket("cadera", cap={"profundidad_mm": 30.0, "huella_radio_mm": pc.hip_pin.head_d / 2, "x_mm": x, "dir_x": -1.0})
    assert C.check(plug("cadera"), hip(9.55), P)[0] == []                      # sin clic cabe (separación 5,9 mm)
    bad = C.check(plug("cadera"), hip(9.55), pc)[0]                            # con clic: 0,9 mm < 1,0
    assert len(bad) == 1 and "0.90" in bad[0]
