"""Mira asistida del pasador: lectura de cobertura según dónde se ponga el centro (sin GTK, sin armar la figura)."""
import dataclasses
import shutil
import os

import numpy as np
import pytest
from shapely import affinity

from afm import guide as G
from afm import pin_aid as PA
from afm.catalog import Catalog
from afm.gui.controller import Controller, Mode
from afm.pin_aid import AimReading, PinAid
from afm.ports import Port, PortFace
from afm.project import demo_project
from afm.testfigure import make_doll

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


def clone(cat, ref, new_id, k=1.0, figura="otra.stl"):
    """Copia de una pieza con las caras de sus puertos escaladas por `k` (otra figura, otro tamaño)."""
    meta, mesh = cat.load(ref)
    ports = {}
    for name, p in meta.puertos.items():
        face = p.face
        if face is not None:
            face = PortFace.from_shape(affinity.scale(face.shape(), k, k, origin=(0, 0)))
        ports[name] = Port(p.clase, p.rol, p.frame, face, p.sweep_deg)
    cat.add_piece(mesh, dataclasses.replace(meta, id=new_id, version=1, puertos=ports, origen={"figura": figura},
                                            stl="", sha256=""))


@pytest.fixture()
def cat(tmp_path):
    root = tmp_path / "linea"
    shutil.copytree(EJEMPLO, root)
    c = Catalog.open(str(root))
    for k, i in ((1.0, "a"), (0.5, "c"), (0.3, "d")):
        clone(c, "torso-muneco-01", f"torso-otra-{i}", k)
    return c


@pytest.fixture(scope="module")
def prev_arm(tmp_path_factory):
    """La cara de contacto del brazo del muñeco de prueba, como la arma el modo PIN."""
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path_factory.mktemp("aid") / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    for l in demo_project().limbs:
        c.session.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
    spec = next(l for l in demo_project().limbs if l.name == "brazo")
    c.session.set_limb_polygon("brazo", spec.polygon)
    c.start_pin("brazo")
    assert c.mode == Mode.PIN
    return c.pin_prev


def _cov(niveles, clase="hombro", rol="enchufe"):
    """Un `Coverage` con piezas falsas de los niveles dados (solo cuenta `nivel`)."""
    class _Fit:
        def __init__(self, nivel):
            self.nivel, self.ref, self.puerto = nivel, "x@1", "p"

        def motivo(self):
            return "" if self.nivel == "ok" else "escalón"
    return G.Coverage(clase, rol, [_Fit(n) for n in niveles])


def _reading(plug=None, socket=None, dentro=True, margen=3.0):
    return AimReading((0.0, 0.0), "hombro", dentro, margen, 3.0, 1.2, plug, socket, ("torsos", "brazos"))


# ---------------------------------------------------------------------------------------------------- niveles
def test_nivel_thresholds():
    assert _reading(_cov(["ok"] * 4 + ["aviso"])).nivel == "ok"             # 5/5 aceptan (≥ GREEN_MIN)
    assert _reading(_cov(["ok", "aviso", "no", "no"])).nivel == "aviso"     # 2/4
    assert _reading(_cov(["no", "no"])).nivel == "no"
    assert _reading(None, None).nivel == "sin_catalogo"
    assert _reading(_cov(["ok"]), dentro=False).nivel == "fuera"            # fuera pesa más que la cobertura


def test_cobertura_is_the_worst_direction_and_empty_side_does_not_vote():
    r = _reading(_cov(["ok", "ok"]), _cov(["no", "no"]))
    assert r.cobertura == 0.0 and r.nivel == "no"
    r = _reading(_cov(["ok", "ok"]), _cov([]))                              # un lado sin universo no opina
    assert r.cobertura == 1.0 and r.nivel == "ok"


def test_texto_and_contador():
    r = _reading(_cov(["ok", "aviso", "no"]), _cov(["ok"] * 4))
    assert r.contador() == "torsos 2/3 · brazos 4/4"
    assert r.texto().endswith("pared 3.0 mm") and r.texto().startswith("torsos 2/3")
    assert _reading().texto().startswith("sin catálogo para comparar")
    assert "sobresale 1.5 mm" in _reading(margen=-1.5).pared()
    assert "< 1.2" in _reading(margen=0.5).pared()
    assert "fuera de la cara" in _reading(dentro=False).pared()


def test_peor_motivo_and_clave_order():
    r = _reading(_cov(["ok", "aviso", "no"]))
    assert r.peor_motivo() == "escalón"
    assert _reading(_cov(["ok"])).peor_motivo() is None
    mejor, peor = _reading(_cov(["ok", "ok"])), _reading(_cov(["aviso", "aviso"]))
    assert mejor.clave() > peor.clave()                                      # misma cobertura, más «ok» pleno gana


def test_colors_cover_every_level():
    assert set(PA.LEVEL_COLORS) == set(PA.LEVELS)


# ---------------------------------------------------------------------------------------------------- sobre una cara real
def test_wrong_kind_is_refused(prev_arm, cat):
    with pytest.raises(ValueError):
        PinAid(prev_arm, "head", cat)


def test_evaluate_on_the_default_center_reads_both_directions(prev_arm, cat):
    aid = PinAid(prev_arm, "arm", cat)
    r = aid.evaluate(prev_arm.project(prev_arm.default_center))
    assert r.dentro and r.clase == "hombro"
    assert r.plug is not None and r.plug.universo >= 4                       # el torso del ejemplo + 3 clones
    assert r.socket is not None and r.socket.universo >= 1                   # el brazo del ejemplo
    assert r.nivel in ("ok", "aviso", "no")
    assert "torsos" in r.contador()


def test_point_outside_the_face_is_fuera_and_skips_the_catalog(prev_arm, cat, monkeypatch):
    calls = []
    monkeypatch.setattr(G, "coverage_of_port", lambda *a, **k: calls.append(1))
    aid = PinAid(prev_arm, "arm", cat)
    far = prev_arm.project(prev_arm.face.center) + np.array([60.0, 0.0])
    r = aid.evaluate(far)
    assert r.nivel == "fuera" and r.plug is None and not calls


def test_cache_groups_points_in_the_same_cell(prev_arm, cat, monkeypatch):
    calls = []
    real = G.coverage_of_port
    monkeypatch.setattr(G, "coverage_of_port", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    aid = PinAid(prev_arm, "arm", cat)
    uv = prev_arm.project(prev_arm.default_center)
    a = aid.evaluate(uv)
    n = len(calls)
    b = aid.evaluate(uv + np.array([PA.CELL_MM * 0.1, 0.0]))                 # misma celda: no recalcula
    assert len(calls) == n and b.uv != a.uv and b.nivel == a.nivel
    aid.evaluate(uv + np.array([PA.CELL_MM * 3, 0.0]))                       # otra celda: sí
    assert len(calls) > n


def test_exclude_figure_removes_the_own_pieces(prev_arm, cat):
    uv = prev_arm.project(prev_arm.default_center)
    todo = PinAid(prev_arm, "arm", cat).evaluate(uv)
    sin_propia = PinAid(prev_arm, "arm", cat, exclude_figure="muneco.stl").evaluate(uv)
    assert sin_propia.plug.universo == todo.plug.universo - 1                # el torso del muñeco
    assert sin_propia.socket.universo == todo.socket.universo - 1            # y su brazo


def test_best_near_never_loses_to_the_start_and_stays_in_radius(prev_arm, cat):
    aid = PinAid(prev_arm, "arm", cat)
    uv = prev_arm.project(prev_arm.default_center) + np.array([0.0, -4.0])
    start = aid.evaluate(uv)
    best = aid.best_near(uv)
    assert best is not None and best.dentro
    assert best.clave() >= start.clave()
    assert np.hypot(*(np.asarray(best.uv) - uv)) <= PA.NEAR_RADIUS_MM + PA.NEAR_STEP_MM


def test_best_near_is_none_far_from_the_face(prev_arm, cat):
    aid = PinAid(prev_arm, "arm", cat)
    assert aid.best_near(prev_arm.project(prev_arm.face.center) + np.array([80.0, 0.0])) is None


def test_reference_faces_and_own_face(prev_arm, cat):
    aid = PinAid(prev_arm, "arm", cat)
    r = aid.evaluate(prev_arm.project(prev_arm.default_center))
    refs = aid.reference_faces(r)
    assert len(refs) == r.plug.universo and all(not s.is_empty for _ref, _n, s in refs)
    assert aid.own_face(r).area > 0
    assert aid.reference_faces(_reading()) == []


# ---------------------------------------------------------------------------------------------------- en el controlador
W, H = 600, 800


@pytest.fixture()
def ctrl(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    for l in demo_project().limbs:
        c.session.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
    c.session.set_limb_polygon("brazo", next(l for l in demo_project().limbs if l.name == "brazo").polygon)
    return c


def _px(c, uv):
    return tuple(c.camera.project(c.pin_prev.plane.to_world(np.asarray(uv, float)[None])[0], W, H)[0, :2])


def test_without_catalog_there_is_no_aim(ctrl):
    ctrl.start_pin("brazo")
    assert ctrl.pin_aid is None
    assert ctrl.update_hover(*_px(ctrl, ctrl.pin_prev.project(ctrl.pin_prev.default_center)), W, H) is False
    ctrl.snap_pin()
    assert "catálogo" in ctrl.message


def test_hover_reads_the_aim_and_draws_the_ghost(ctrl, cat):
    ctrl.session.open_catalog(cat.root)
    ctrl.start_pin("brazo")
    assert ctrl.pin_aid is not None
    uv = ctrl.pin_prev.project(ctrl.pin_prev.default_center)
    assert ctrl.update_hover(*_px(ctrl, uv), W, H) is True
    assert ctrl.pin_aim is not None and ctrl.message.startswith("Mira: ")
    from test_gui_phases35 import FakeScene
    sc = FakeScene()
    ctrl.sync(sc)
    assert "pasador:mira" in sc.items and "pasador:mira_cabeza" in sc.items
    ctrl.cancel()
    assert ctrl.pin_aid is None and ctrl.pin_aim is None


def test_click_message_carries_the_catalog_count(ctrl, cat):
    ctrl.session.open_catalog(cat.root)
    ctrl.start_pin("brazo")
    uv = ctrl.pin_prev.project(ctrl.pin_prev.default_center)
    ctrl.click(*_px(ctrl, uv), W, H)
    assert "Catálogo: torsos" in ctrl.message


def test_snap_places_the_pin_on_a_reading_at_least_as_good(ctrl, cat):
    ctrl.session.open_catalog(cat.root)
    ctrl.start_pin("brazo")
    start = ctrl.pin_prev.project(ctrl.pin_prev.default_center) + np.array([0.0, -3.0])
    before = ctrl.pin_aid.evaluate(start)
    ctrl.click(*_px(ctrl, start), W, H)
    ctrl.snap_pin()
    assert ctrl.message.startswith("Imán")
    after = ctrl.pin_aid.evaluate(ctrl.pin_prev.project(ctrl.session.limb("brazo").pin_center))
    assert after.clave() >= before.clave()
