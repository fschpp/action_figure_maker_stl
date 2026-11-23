"""«Imprimir sin soportes» + clic en los hombros: el brazo con clic se apoya en el plano rearmando con el ángulo de apoyo
(`cli._rest_click_arms_on_plane`), en vez de colgar bajo el plano con un aviso."""
import dataclasses
from types import SimpleNamespace

from afm import cli, recipe as R
from afm.params import Params


def _asm(angle=10.0):
    p = dataclasses.replace(Params(), shoulder_detent=True, detent=dataclasses.replace(Params().detent, print_angle=angle))
    return SimpleNamespace(res=SimpleNamespace(), params=p)


def test_no_click_arm_hanging_keeps_the_assembly(monkeypatch):
    asm = _asm()
    monkeypatch.setattr(cli.P, "flat_click_advice", lambda res, depth: {})
    monkeypatch.setattr(R, "assemble", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debía rearmar")))
    assert cli._rest_click_arms_on_plane(None, asm, None, 1.0, lambda *_: None, False) is asm


def test_hanging_click_arm_is_rebuilt_with_the_resting_angle(monkeypatch):
    asm, new = _asm(10.0), _asm(47.0)
    calls = []
    advice = iter([{"brazo": {"angulo_apoyo": 47.0, "angulo_actual": 10.0, "cuelga_mm": 3.0, "estado": "ok"}}, {}])
    monkeypatch.setattr(cli.P, "flat_click_advice", lambda res, depth: next(advice))
    monkeypatch.setattr(R, "assemble", lambda recipe, root, progress=None, sit=True, print_angle=None: calls.append(print_angle) or new)
    msgs = []
    out = cli._rest_click_arms_on_plane("receta", asm, None, 1.0, msgs.append, True)
    assert out is new and calls == [47.0]
    assert new.params.flat_fill_hip is True                      # el relleno de muslos se conserva al rearmar
    assert any("47°" in m for m in msgs)


def test_arm_that_cannot_rest_is_left_alone(monkeypatch):
    asm = _asm()
    monkeypatch.setattr(cli.P, "flat_click_advice", lambda res, depth: {"brazo": {"angulo_apoyo": None, "cuelga_mm": 2.0, "estado": "no_llega"}})
    monkeypatch.setattr(R, "assemble", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debía rearmar")))
    assert cli._rest_click_arms_on_plane(None, asm, None, 1.0, lambda *_: None, False) is asm


def test_assemble_print_angle_overrides_only_that_build(monkeypatch):
    seen = {}
    cat = SimpleNamespace(params=Params())
    monkeypatch.setattr(R, "load_choices", lambda recipe, root: (cat, {}))
    monkeypatch.setattr(R, "recipe_params", lambda recipe, c: c.params)

    def stop(choices, p):
        seen["angle"] = p.detent.print_angle
        raise R.AssemblyError("stop")
    monkeypatch.setattr(R, "synthesize_host", stop)
    try:
        R.assemble(None, None, print_angle=33.0)
    except R.RecipeError:
        pass
    assert seen["angle"] == 33.0 and cat.params.detent.print_angle == Params().detent.print_angle
