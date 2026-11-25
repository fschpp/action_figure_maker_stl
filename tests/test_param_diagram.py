"""Diagrama de parámetros (motor puro, sin GTK): toda clave de `PARAM_FIELDS` tiene escena y se resalta en ella."""
import pytest

from afm import i18n
from afm.gui import param_diagram as D
from afm.gui.controller import PARAM_FIELDS, Controller

RANGES = {k: (lo, hi) for k, _l, lo, hi, _s, _d in PARAM_FIELDS}


@pytest.fixture(autouse=True)
def _es():
    i18n.set_language("es")
    yield
    i18n.set_language("es")


def _get():
    return Controller().get_param


def test_every_param_key_is_highlighted_in_its_scene():
    """Cada clave aparece como cota o forma de su escena (los interruptores, en alguno de sus dos estados)."""
    v = _get()
    toggles = ("hinge_pin.rod", "hinge_pin.neck", "torso_mirror", "shoulder_detent", "hip_detent")
    for key in RANGES:
        sid = D.scene_id_for_key(key)
        found = D.build_scene(sid, v).keys()
        for t in toggles:
            for val in (0.0, 1.0):
                found |= D.build_scene(sid, lambda k, t=t, val=val: val if k == t else v(k)).keys()
        assert key in found, key


def test_all_scenes_render_in_svg_both_palettes():
    v = _get()
    ref = D.reference_values(RANGES, v)
    for sid in D.SCENE_TITLES:
        sc = D.build_scene(sid, v)
        frame = D.build_scene(sid, ref).bbox()
        for pal in (D.LIGHT, D.DARK):
            cv = D.SvgCanvas(400, 190, pal["bg"])
            D.render(cv, sc, next(iter(sorted(sc.keys()))), 400, 190, pal, frame=frame)
            assert cv.svg().startswith("<svg")


def test_scene_titles_are_translated():
    for sid in D.SCENE_TITLES:
        assert D.scene_title(sid) != D.SCENE_TITLES[sid]


def test_unlabelled_notes_do_not_count_as_keys():
    sc = D.build_scene(("pin", "shoulder_pin"), _get())
    assert "" not in sc.keys()
