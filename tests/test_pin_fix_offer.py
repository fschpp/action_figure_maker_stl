"""«El pasador no entra»: se propone uno más chico (o moverlo) y se PREGUNTA; nunca se achica en silencio."""
import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from afm.gui.controller import Controller, Mode
from afm.params import PinSpec
from afm.pin_ghost import propose_smaller_pin, shrunk_dims
from afm.project import demo_project
from afm.testfigure import make_doll

PIN = PinSpec(4.1, 2.5, 6.6, 1.65)
WALL = 1.2


# ---------------------------------------------------------------- lógica pura
def test_shrunk_dims_keeps_a_pin_that_already_fits_and_rounds_the_shaft_down():
    assert shrunk_dims(PIN, 20.0, WALL) == (4.1, 6.6)
    shaft, head = shrunk_dims(PIN, 5.95, WALL)           # 5,95 − 2·1,2 = 3,55 → se redondea hacia abajo a 3,5 (que sobre borde, no que falte)
    assert shaft == 3.5 and head == pytest.approx(6.6 * 3.5 / 4.1, abs=0.05)
    assert shrunk_dims(PIN, 2.0, WALL) is None            # ni el vástago mínimo con borde


def test_shrinks_in_place_when_the_click_is_inside_a_narrow_face():
    res = propose_smaller_pin(box(-3, -10, 3, 10), (0.0, 0.0), PIN, WALL)
    p = res.proposal
    assert res.reason == "" and p is not None
    assert p.uv == (0.0, 0.0) and p.moved_mm == 0.0
    assert p.shaft_d < PIN.shaft_d and p.old_shaft_d == PIN.shaft_d and p.room == pytest.approx(6.0)


def test_moves_to_the_widest_point_only_when_it_buys_a_thicker_pin():
    trapezoid = Polygon([(-1.5, 0), (1.5, 0), (6, 20), (-6, 20)])
    near_narrow_end = (0.0, 3.0)
    stay = propose_smaller_pin(trapezoid, near_narrow_end, PIN, WALL, max_move=0.0).proposal
    go = propose_smaller_pin(trapezoid, near_narrow_end, PIN, WALL, max_move=100.0).proposal
    assert stay is not None and stay.moved_mm == 0.0
    assert go is not None and go.moved_mm > 1.0 and go.shaft_d > stay.shaft_d


def test_never_jumps_to_another_piece_of_the_face():
    """Cara con un trozo chico en el hombro y otro grande en la mano: el clic en el hombro se resuelve ahí."""
    shoulder, hand = box(-3, 0, 3, 10), box(-15, -80, 15, -50)
    face = MultiPolygon([shoulder, hand])
    res = propose_smaller_pin(face, (0.0, 5.0), PIN, WALL)
    assert res.proposal is not None and shoulder.contains(__import__("shapely.geometry", fromlist=["Point"]).Point(*res.proposal.uv))
    assert res.proposal.shaft_d < PIN.shaft_d              # el trozo de la mano habría dado el pasador entero, pero no es donde se pidió


def test_no_proposal_when_the_shaft_does_not_touch_any_face():
    res = propose_smaller_pin(box(-3, -10, 3, 10), (40.0, 0.0), PIN, WALL)
    assert res.proposal is None and res.reason == "no_toca"


def test_no_proposal_below_the_tool_minimum_or_without_room():
    narrow = box(-1.8, -10, 1.8, 10)                       # cabe Ø3,6: con borde quedaría un vástago de Ø1,2
    res = propose_smaller_pin(narrow, (0.0, 0.0), PIN, WALL, lo_shaft=2.0, lo_head=3.0)
    assert res.proposal is None and res.reason == "bajo_minimo" and res.room == pytest.approx(3.6)
    res = propose_smaller_pin(box(-1, -1, 1, 1), (0.0, 0.0), PIN, WALL)
    assert res.proposal is None and res.reason == "sin_lugar"


def test_head_never_goes_below_the_tool_minimum():
    p = propose_smaller_pin(box(-3, -10, 3, 10), (0.0, 0.0), PinSpec(6.0, 2.5, 8.0, 1.65), WALL, lo_shaft=2.0, lo_head=7.0).proposal
    assert p is not None and p.head_d >= 7.0


# ---------------------------------------------------------------- controlador (muñeca de test)
@pytest.fixture(scope="module")
def stl_path(tmp_path_factory):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path_factory.mktemp("pinfix") / "muneco.stl")
    m.export(path)
    return path


@pytest.fixture()
def ctrl(stl_path):
    c = Controller()
    c.open_stl(stl_path)
    c.auto_symmetry()
    for l in demo_project().limbs:
        c.session.add_limb(l.name, l.kind, l.plane, l.thickness, auto=True)
    spec = next(l for l in demo_project().limbs if l.name == "brazo")
    c.session.set_limb_polygon("brazo", spec.polygon)
    return c


def _wide_face(c, offset: float):
    """Corte hacia adentro: la cara del brazo de la muñeca mide hasta Ø3,6 (−1) / Ø4,6 (−2) / Ø5,2 (−3) mm."""
    c.session.limb("brazo").cut_offset = offset


def _big_pin(c):
    c.set_param("shoulder_pin.shaft_d", 6.0)
    c.set_param("shoulder_pin.head_d", 8.0)


def _level(c):
    return c.session.pin_fit_at("brazo", c.pin_prev.project(c._pin_center("brazo"))).level


def test_a_pin_that_does_not_fit_is_offered_a_smaller_one_without_changing_anything(ctrl):
    _wide_face(ctrl, -3.0)
    _big_pin(ctrl)
    ctrl.start_pin("brazo")
    pr = ctrl.pin_proposal
    assert ctrl.mode == Mode.PIN and pr is not None and _level(ctrl) == "no"
    assert pr.shaft_d < 6.0 and pr.old_shaft_d == 6.0
    assert ctrl.get_param("shoulder_pin.shaft_d") == 6.0 and ctrl.get_param("shoulder_pin.head_d") == 8.0     # solo pregunta
    assert "¿Achicarlo" in ctrl.message
    assert f"{pr.shaft_d:g}" in ctrl.pin_proposal_text() and "catálogo" in ctrl.pin_proposal_text()


def test_accepting_applies_the_smaller_pin_and_it_fits(ctrl):
    _wide_face(ctrl, -3.0)
    _big_pin(ctrl)
    ctrl.start_pin("brazo")
    pr = ctrl.pin_proposal
    ctrl.session.outcome = object()                                   # una figura ya generada
    ctrl.accept_pin_fix()
    assert ctrl.pin_proposal is None and ctrl.get_param("shoulder_pin.shaft_d") == pytest.approx(pr.shaft_d)
    assert ctrl.get_param("shoulder_pin.head_d") >= pr.head_d - 1e-9
    assert _level(ctrl) in ("ok", "aviso") and "achicado" in ctrl.message
    assert ctrl.session.outcome is None                               # cambió un parámetro: hay que generar de nuevo
    ctrl.accept_pin_fix()                                             # sin propuesta pendiente no hace nada
    assert ctrl.get_param("shoulder_pin.shaft_d") == pytest.approx(pr.shaft_d)


def test_rejecting_keeps_the_pin_and_asks_again_only_after_it_moves(ctrl):
    _wide_face(ctrl, -3.0)
    _big_pin(ctrl)
    ctrl.start_pin("brazo")
    ctrl.reject_pin_fix()
    assert ctrl.pin_proposal is None and "queda como está" in ctrl.message
    assert ctrl.get_param("shoulder_pin.shaft_d") == 6.0 and _level(ctrl) == "no"
    ctrl.nudge_pin(0, 1)                                              # mover el pasador es otra decisión: se vuelve a ofrecer
    assert ctrl.pin_proposal is not None and ctrl.get_param("shoulder_pin.shaft_d") == 6.0


def test_no_offer_when_the_pin_fits(ctrl):
    _wide_face(ctrl, -3.0)                                            # cara de Ø5,2: el pasador de Ø4,1 entra
    ctrl.start_pin("brazo")
    assert _level(ctrl) in ("ok", "aviso")
    assert ctrl.pin_proposal is None and "¿Achicarlo" not in ctrl.message and ctrl.pin_proposal_text() == ""


def test_says_so_when_shrinking_is_not_enough(ctrl):
    _wide_face(ctrl, -1.0)                                            # cara de Ø3,6: con borde el vástago quedaría bajo el mínimo (Ø2)
    ctrl.start_pin("brazo")
    assert _level(ctrl) == "no" and ctrl.pin_proposal is None
    assert "no alcanza" in ctrl.message


def test_changing_the_size_by_hand_recomputes_the_offer_and_cancel_clears_it(ctrl):
    _wide_face(ctrl, -3.0)
    ctrl.start_pin("brazo")
    assert ctrl.pin_proposal is None                                  # Ø4,1 entra
    assert ctrl.set_pin_size("shoulder_pin.shaft_d", 6.0)
    assert ctrl.pin_proposal is not None and "¿Achicarlo" in ctrl.message
    assert ctrl.set_pin_size("shoulder_pin.shaft_d", 4.1)
    assert ctrl.pin_proposal is None
    ctrl.set_pin_size("shoulder_pin.shaft_d", 6.0)
    assert ctrl.pin_proposal is not None
    ctrl.cancel()
    assert ctrl.pin_proposal is None and ctrl.pin_proposal_text() == ""


def test_manual_shrink_button_still_works_and_clears_the_offer(ctrl):
    _wide_face(ctrl, -3.0)
    _big_pin(ctrl)
    ctrl.start_pin("brazo")
    assert ctrl.pin_proposal is not None
    ctrl.pin_shrink_to_fit()
    assert ctrl.pin_proposal is None and ctrl.get_param("shoulder_pin.shaft_d") < 6.0 and _level(ctrl) in ("ok", "aviso")
