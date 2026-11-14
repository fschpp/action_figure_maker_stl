"""Estado de cada paso del riel lateral (UX 5b): resume lo que el controlador ya sabe, sin ventana."""
import numpy as np
import pytest

from afm.gui import step_state as SS
from afm.gui.controller import Controller
from afm.planes import Plane
from afm.testfigure import make_doll


@pytest.fixture(scope="module")
def stl_path(tmp_path_factory):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path_factory.mktemp("rail") / "muneco.stl")
    m.export(path)
    return path


def _st(c):
    return {k: v.state for k, v in SS.step_states(c).items()}


def test_empty_controller_asks_to_open_an_stl():
    st = SS.step_states(Controller())
    assert set(st) == set(SS.ORDER) and list(SS.FLOW) + list(SS.OPTIONAL) == list(SS.ORDER)
    assert st["figura"].state == "todo" and st["figura"].detail == "Abrí un STL"
    assert st["generar"].state == "todo" and st["catalogo"].state == "info" and "sin catálogo" in st["catalogo"].detail
    assert [st[k].title for k in SS.ORDER][:2] == ["Figura", "Planos"] and st["figura"].glyph == "○"


def test_flow_states_follow_the_work(stl_path):
    c = Controller()
    c.open_stl(stl_path)
    st = _st(c)
    assert st["figura"] == "ok" and st["planos"] == "todo" and st["generar"] == "todo"
    assert SS.step_states(c)["planos"].detail == "Falta el plano de simetría"
    c.auto_symmetry()
    assert _st(c)["planos"] == "warn"                                       # simetría sí, pero ningún miembro / cabeza / cintura
    sym = c.session.project.symmetry
    c.session.add_limb("brazo", "arm", Plane.from_normal(sym.origin, sym.normal, "brazo"), auto=True)
    s = SS.step_states(c)
    assert s["planos"].state == "ok" and "1 plano" in s["planos"].detail
    assert s["miembros"].state == "warn" and "Falta la silueta: brazo" == s["miembros"].detail
    assert s["generar"].state == "todo" and s["generar"].detail.startswith("Falta: brazo")
    poly = sym.to_world(np.array([[-40, 0], [40, 0], [40, 30], [-40, 30]], float))
    c.session.set_limb_polygon("brazo", poly)
    s = SS.step_states(c)
    assert s["miembros"].state == "ok" and s["miembros"].detail == "1 miembro con silueta"
    assert s["generar"].state == "ready"                                    # sin pendientes y sin generar
    c.session.outcome = object()                                            # una figura generada vigente
    c.generated_once = True
    assert SS.step_states(c)["generar"].state == "ok"
    c.session.outcome = None                                                # un cambio la invalidó
    assert SS.step_states(c)["generar"].state == "stale" and "Desactualizada" in SS.step_states(c)["generar"].detail


def test_parameters_and_trims_summaries(stl_path):
    c = Controller()
    c.open_stl(stl_path)
    assert SS.step_states(c)["parametros"].detail == "valores por defecto" and SS.step_states(c)["recortes"].detail == "ninguno"
    c.set_param("hip_pin.shaft_len", c.get_param("hip_pin.shaft_len") + 1.0)
    assert SS.step_states(c)["parametros"].detail == "1 modificados"
    assert SS.GLYPHS["ok"] == "✓" and set(SS.GLYPHS) == {"todo", "ok", "warn", "stale", "ready", "info"}


def test_next_pending_skips_done_steps_and_wraps():
    mk = lambda **kw: {k: SS.StepState(k, kw.get(k, "ok"), "") for k in SS.ORDER}   # noqa: E731
    st = mk(planos="warn", generar="ready", parametros="info", recortes="info")
    assert SS.next_pending(st, None) == "planos"
    assert SS.next_pending(st, "figura") == "planos"
    assert SS.next_pending(st, "planos") == "generar"                       # salta lo listo y lo opcional («info»)
    assert SS.next_pending(st, "generar") == "planos"                       # da la vuelta
    assert SS.next_pending(mk(), "figura") is None                          # todo listo
    assert SS.next_pending(mk(planos="stale"), "catalogo") == "planos"      # el catálogo (opcional) no es del flujo
