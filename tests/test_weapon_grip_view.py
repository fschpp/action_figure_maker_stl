"""Dibujo de la mano agarrando el arma (`afm.gui.weapon_grip_view`): geometría sin GTK y trazado con cairo fuera de pantalla."""
import numpy as np
import pytest

from afm import props as P
from afm import solids as S
from afm import weapon_fit as WF
from afm.gui import weapon_grip_view as GV
from afm.gui.controller import Controller
from afm.gui.weapon_session import WeaponSession

from test_weapon import cat_hand, sword_stl                       # noqa: F401,E402  (fixtures)
from test_gui_hand_place import std_hand                          # noqa: F401,E402

GRIP = P.GripSpec()


def _prop(blade_len=34.0, blade_w=4.0, guard_w=10.0):
    return S.to_printable_trimesh(P.make_prop(GRIP, 10.0, blade_len=blade_len, blade_w=blade_w, guard_w=guard_w))


def _view(cat, prop=None, **kw):
    _meta, hand, fit = WF.load_hand_with_grip(cat)
    sim = WF.simulate_in_hand(prop, hand, fit, 100.0) if prop is not None else None
    return GV.build_view(hand, fit, sim, **kw), sim


def test_badge_levels_group_the_notes():
    assert GV.badge_levels([]) == {"contacto": "ok", "agarre": "ok", "tamano": "ok"}
    assert GV.badge_levels([("choca_mano", {})])["contacto"] == WF.LEVEL_NO
    assert GV.badge_levels([("guarda_chica", {}), ("larga_vs_figura", {})]) == {"contacto": "ok", "agarre": "aviso", "tamano": "aviso"}
    assert GV.badge_levels([], build_warnings=1)["agarre"] == "aviso"          # un aviso del armado también marca el agarre
    # todo código que puede dar weapon_fit para la mano está en alguna insignia
    hand_codes = set(WF.NOTE_CODES) - {"atraviesa_piso", "punta_cerca_piso", "choca_miembro"}
    assert hand_codes <= GV.CONTACT_CODES | GV.GRIP_CODES | GV.SIZE_CODES


def test_view_with_weapon_measures_what_it_draws(cat_hand):                    # noqa: F811
    v, sim = _view(cat_hand, _prop())
    assert v.has_weapon and v.levels == {"contacto": "ok", "agarre": "ok", "tamano": "ok"} and v.level == WF.LEVEL_OK
    assert v.reach_mm == pytest.approx(sim.reach_mm) and v.guard_mm == pytest.approx(10.0, abs=0.5)
    assert v.figure_ratio == pytest.approx(sim.reach_vs_figure)
    assert v.is_weapon.any() and (~v.is_weapon).any()                           # mano y arma
    assert v.tris.shape[1:] == (3, 2) and len(v.tris) == len(v.shade) == len(v.is_weapon)
    xmin, ymin, xmax, ymax = v.bounds
    assert ymax == pytest.approx(v.reach_mm, abs=0.6)                           # la punta es lo más alto: la hoja sube
    assert v.hand_bottom < 0 < v.reach_mm and v.fist_y < 0 < v.guard_y          # la mano abajo de la cara del puño, la guarda arriba


def test_blade_is_seen_flat(cat_hand):                                         # noqa: F811
    wide, _ = _view(cat_hand, _prop(blade_w=6.0, guard_w=12.0))
    blade = wide.tris[wide.is_weapon].reshape(-1, 2)
    assert np.ptp(blade[:, 0]) >= 11.0                                          # se ve la guarda de 12 mm de ancho, no de canto


def test_empty_fist_waits_for_a_weapon(cat_hand):                              # noqa: F811
    v, _ = _view(cat_hand)
    assert not v.has_weapon and v.level is None and set(v.levels.values()) == {None}
    assert not v.is_weapon.any() and len(v.tris) > 0


def test_problems_show_up_in_the_right_badge(cat_hand):                        # noqa: F811
    small_guard, _ = _view(cat_hand, _prop(guard_w=2.5, blade_w=2.0))
    assert small_guard.levels["agarre"] == "aviso" and small_guard.level == WF.LEVEL_WARN
    tiny, _ = _view(cat_hand, _prop(blade_len=6.0))
    assert tiny.levels["tamano"] == "aviso" and tiny.levels["contacto"] == "ok"
    with_warning, _ = _view(cat_hand, _prop(), build_warnings=2)
    assert with_warning.levels["agarre"] == "aviso"


def test_big_hand_triangle_count_is_capped(cat_hand):                          # noqa: F811
    import trimesh

    _meta, hand, fit = WF.load_hand_with_grip(cat_hand)
    dense = hand.subdivide().subdivide().subdivide()
    assert len(dense.faces) > GV.MAX_TRIS * 2
    assert len(GV.build_view(dense, fit).tris) <= GV.MAX_TRIS


cairo = pytest.importorskip("cairo")


def _render(view, w=300, h=250):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    GV.draw(cr, w, h, view)
    buf = np.frombuffer(surf.get_data(), np.uint8).reshape(h, surf.get_stride() // 4, 4)[:, :w]
    return buf.astype(int)                                                      # B, G, R, A


def test_draw_paints_hand_weapon_and_badges(cat_hand):                         # noqa: F811
    v, _ = _view(cat_hand, _prop())
    img = _render(v)
    bg = img[125, 3]
    assert (img != bg).any(axis=2).mean() > 0.08                                # hay dibujo, no solo fondo
    skin = (img[..., 2] > 180) & (img[..., 1] > 120) & (img[..., 0] < 170) & (img[..., 2] > img[..., 0] + 40)
    steel = (img[..., 0] > 190) & (img[..., 2] < 190) & (img[..., 0] > img[..., 2] + 30)
    assert skin.sum() > 300 and steel.sum() > 150                               # piel y acero
    badge_row = img[8:36, 8:100]
    green = (badge_row[..., 1] > 170) & (badge_row[..., 2] < 120)
    assert green.sum() > 40                                                      # las insignias en verde


def test_draw_signals_problems_and_handles_nothing(cat_hand):                  # noqa: F811
    amber = lambda im: ((im[8:36, 8:100, 2] > 220) & (im[8:36, 8:100, 1] > 160) & (im[8:36, 8:100, 0] < 80)).sum()
    ok_img = _render(_view(cat_hand, _prop())[0])
    warn_img = _render(_view(cat_hand, _prop(blade_len=6.0))[0])
    assert amber(warn_img) > 30 and amber(ok_img) == 0
    blank = _render(None)
    assert blank[125, 150].tolist() == blank[125, 3].tolist()                    # sin vista: solo el fondo
    assert _render(_view(cat_hand)[0]).any()                                      # el puño vacío también se dibuja


def test_draw_works_at_any_size_and_for_very_long_weapons(cat_hand):           # noqa: F811
    long_v, _ = _view(cat_hand, _prop(blade_len=120.0))
    for size in ((120, 90), (300, 250), (640, 400)):
        assert _render(long_v, *size).any()


# ---------------------------------------------------------------- sesión y controlador
def test_session_gives_the_empty_fist_then_the_weapon(cat_hand, sword_stl):    # noqa: F811
    path, pts = sword_stl
    ws = WeaponSession()
    assert ws.grip_view() is None                                               # sin catálogo no hay mano que dibujar
    ws.set_catalog(cat_hand.root)
    empty = ws.grip_view()
    assert empty is not None and not empty.has_weapon and ws.grip_view() is empty
    ws.load(path)
    assert not ws.grip_view().has_weapon                                        # todavía sin plano de corte
    ws.set_cut(np.asarray(pts, float) * ws.scale)
    v = ws.grip_view()
    assert v.has_weapon and v.tip and ws.grip_view() is v                       # el mismo objeto mientras nada cambie
    ws.set_length(30.0)
    v2 = ws.grip_view()
    assert v2 is not v and v2.has_weapon


def test_controller_exposes_the_view(cat_hand, sword_stl):                     # noqa: F811
    c = Controller()
    assert c.weapon_grip_view() is None
    assert c.set_weapon_catalog(cat_hand.root) is True
    assert c.weapon_grip_view() is not None and not c.weapon_grip_view().has_weapon
