"""GUI (sin GTK): extraer la mano estándar con 3 clics en la muñeca, invertir el lado, guardarla y verla en verde."""
import os

import numpy as np
import pytest

from afm import solids as S
from afm.gui.controller import Controller, Mode
from afm.gui.picking import PickResult
from afm.hand import load_standard_hand, meta_path
from afm.testfigure import make_doll

# el brazo izquierdo del muñeco es un cilindro vertical en x = 18 (r = 4, z 45..75): el plano x = 19 le corta una lasca chica
WRIST = [(19.0, 0.0, 70.0), (19.0, -2.0, 60.0), (19.0, 2.0, 60.0)]


@pytest.fixture()
def ctrl(tmp_path):
    path = str(tmp_path / "muneco.stl")
    make_doll().export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    return c


def feed(c, pts):
    it = iter(pts)
    c.picker.pick_pixel = lambda *a, **k: PickResult(*(np.asarray(p, float) for p in (next(it),) * 2), 0,
                                                     np.array([0.0, 1.0, 0.0]), True)
    for _ in pts:
        c.click(10, 10, 100, 100)


def test_hand_extract_needs_symmetry(tmp_path):
    path = str(tmp_path / "m.stl")
    make_doll().export(path)
    c = Controller()
    c.open_stl(path)
    c.start_hand_extract()
    assert c.mode == Mode.VIEW and "simetría" in c.message


def test_three_clicks_extract_the_hand(ctrl):
    ctrl.start_hand_extract()
    assert ctrl.mode == Mode.HAND_EXTRACT and ctrl.hover_active()
    assert "punto 1" in ctrl.message and "IZQUIERDA" in ctrl.message
    feed(ctrl, WRIST)
    assert ctrl.mode == Mode.VIEW and ctrl.hand_extract is not None
    assert "Mano izquierda" in ctrl.message
    sp = ctrl.hand_extract["split"]
    assert 0 < sp.hand_volume < 0.05 * sp.rest_volume
    assert ctrl.session.project.limbs == [] and ctrl.session.project.waist is None      # no toca el proyecto


def test_invert_takes_the_other_side_and_back(ctrl):
    ctrl.start_hand_extract()
    feed(ctrl, WRIST)
    small = ctrl.hand_extract["split"].hand_volume
    ctrl.invert_hand_extract()
    assert ctrl.hand_extract["split"].hand_volume > 5 * small and ctrl.hand_extract["invert"]
    ctrl.invert_hand_extract()
    assert ctrl.hand_extract["split"].hand_volume == pytest.approx(small, rel=1e-6)


def test_bad_plane_is_reported_and_nothing_is_kept(ctrl):
    ctrl.start_hand_extract()
    feed(ctrl, [(0.0, 0.0, 70.0), (0.0, -2.0, 60.0), (0.0, 2.0, 60.0)])       # plano de simetría: mitades iguales
    assert ctrl.hand_extract is None and ctrl.mode == Mode.VIEW and "No sirve" in ctrl.message


def test_save_hand_writes_stl_and_metadata(ctrl, tmp_path):
    ctrl.start_hand_extract()
    feed(ctrl, WRIST)
    path = str(tmp_path / "linea" / "mano.stl")
    meta = ctrl.save_hand_extract(path)
    assert os.path.exists(path) and os.path.exists(meta_path(path))
    hand, m2 = load_standard_hand(path)
    assert m2["altura_figura_mm"] == meta["altura_figura_mm"] == 100.0 and m2["lado_origen"] == "izquierda"
    assert hand.bounding_box()[2] == pytest.approx(0.0, abs=1e-2)
    assert "AVISO" in ctrl.message and "130" in ctrl.message                 # la figura está a 100 mm, la línea a 130


def test_save_without_extraction_is_harmless(ctrl, tmp_path):
    assert ctrl.save_hand_extract(str(tmp_path / "x.stl")) is None
    assert not os.path.exists(tmp_path / "x.stl")


def test_discard_and_reload_clear_the_extraction(ctrl, tmp_path):
    ctrl.start_hand_extract()
    feed(ctrl, WRIST)
    ctrl.discard_hand_extract()
    assert ctrl.hand_extract is None and ctrl.hand_preview() is None
    ctrl.start_hand_extract()
    feed(ctrl, WRIST)
    path = str(tmp_path / "otro.stl")
    make_doll().export(path)
    ctrl.open_stl(path)
    assert ctrl.hand_extract is None


def test_extracted_hand_is_drawn_in_green_and_removed(ctrl):
    from test_gui_phases35 import FakeScene
    sc = FakeScene()
    ctrl.sync(sc)
    assert "mano:extraida" not in sc.items
    ctrl.start_hand_extract()
    feed(ctrl, WRIST)
    ctrl.sync(sc)
    assert "mano:extraida" in sc.items
    ctrl.discard_hand_extract()
    ctrl.sync(sc)
    assert "mano:extraida" not in sc.items
