"""Agarre para accesorios desde el controlador (sin ventana): valores, tarea en dos pasos, guardado y avisos."""
import json
import os

import numpy as np
import pytest
import trimesh

from afm import props as P
from afm import solids as S
from afm.gui.controller import GRIP_FIELDS, Controller


@pytest.fixture()
def hand_stl(tmp_path, monkeypatch):
    """Mano de juguete (toro con agujero Ø2,4) y búsqueda de canal gruesa: el toro es mucho más grande que una mano."""
    t = trimesh.creation.torus(major_radius=4.4, minor_radius=3.2, major_sections=48, minor_sections=24)
    path = str(tmp_path / "mano_toro.stl")
    t.export(path)
    real = P.find_channel
    monkeypatch.setattr(P, "find_channel", lambda hand, bore_d=2.4, **kw: real(hand, bore_d, pitch=0.2, n_dirs=150, refine=False))
    return path


def _short_fingers(c):
    """El toro de prueba tiene un canal de ≈ 6 mm (una mano real, 10): lengüetas y dentado acordes."""
    assert c.set_grip_param("finger_len", 5.5) and c.set_grip_barbs("2.7, 2.8")


def test_fields_exist_and_defaults_are_in_range():
    c = Controller()
    for key, _label, lo, hi, _step, _dig in GRIP_FIELDS:
        assert lo <= getattr(c.grip_spec, key) <= hi, key


def test_set_grip_param_validates():
    c = Controller()
    assert c.set_grip_param("bore_d", 2.2) is True and c.grip_spec.bore_d == 2.2
    assert c.set_grip_param("bore_d", 9.0) is False and c.grip_spec.bore_d == 2.2
    assert c.set_grip_param("nope", 1.0) is False


def test_set_grip_barbs():
    c = Controller()
    assert c.set_grip_barbs("2.6, 2.8 ; 3.0") is True and c.grip_barbs == (2.6, 2.8, 3.0)
    assert c.set_grip_barbs("2.6, hola") is False and c.grip_barbs == (2.6, 2.8, 3.0)
    assert c.set_grip_barbs("") is False
    assert c.set_grip_barbs("1,2,3,4,5,6,7") is False


def test_two_step_task_and_busy_flag(hand_stl):
    c = Controller()
    job = c.begin_grip(hand_stl)
    assert job is not None and c.busy
    assert c.begin_grip(hand_stl) is None                       # no se lanzan dos tareas a la vez
    c.finish_grip(job(), None)
    assert not c.busy and c.grip is not None
    s = c.grip_summary()
    assert "Taladro Ø2.4" in s and "Dentado Ø2.9" in s and not c.grip_stale


def test_error_frees_the_controller(tmp_path):
    c = Controller()
    bad = tmp_path / "vacio.stl"
    bad.write_text("no es una malla")
    assert c.run_grip(str(bad)) is None
    assert not c.busy and c.grip is None and "No se pudo taladrar" in c.message


def test_save_hand_and_plate(hand_stl, tmp_path):
    c = Controller()
    assert c.save_grip_hand(str(tmp_path / "x.stl")) is False   # todavía no hay mano taladrada
    assert c.run_grip(hand_stl) is not None
    _short_fingers(c)
    out_hand, out_plate = str(tmp_path / "mano_agarre.stl"), str(tmp_path / "placa.stl")
    assert c.save_grip_hand(out_hand) and c.save_grip_plate(out_plate)
    meta = json.load(open(os.path.splitext(out_hand)[0] + ".mano.json"))
    assert meta["formato"] == "afm-mano-1" and meta["agarre"]["diametro_taladro_mm"] == 2.4
    assert len(trimesh.load(out_plate).split(only_watertight=False)) == len(c.grip_barbs)
    from afm.hand import load_standard_hand                      # la mano con agarre sigue siendo una mano estándar válida
    hand, m = load_standard_hand(out_hand)
    assert hand.volume() > 0 and m["agarre"]["eje"]


def test_stale_bore_blocks_saving_the_hand_but_not_the_plate(hand_stl, tmp_path):
    c = Controller()
    c.run_grip(hand_stl)
    _short_fingers(c)
    c.set_grip_param("bore_d", 2.0)
    assert c.grip_stale and "volvé a taladrar" in c.grip_summary()
    assert c.save_grip_hand(str(tmp_path / "x.stl")) is False
    assert c.save_grip_plate(str(tmp_path / "p.stl")) is True    # la placa usa el taladro ya hecho (2,4)


def test_plate_reports_bad_spec(hand_stl, tmp_path):
    c = Controller()
    c.run_grip(hand_stl)
    _short_fingers(c)
    c.set_grip_barbs("2.3")                                      # no supera el taladro: no retiene
    assert c.save_grip_plate(str(tmp_path / "p.stl")) is False and "no retiene" in c.message


# ---------------------------------------------------------------- fase 2: la espada en la mano pegada de la figura
from afm.hand import load_standard_hand, save_standard_hand          # noqa: E402

from test_gui_hand_place import WRIST, _armed, feed, std_hand         # noqa: E402,F401  (fixtures)
from test_gui_phases35 import FakeScene                              # noqa: E402

# agarre inventado sobre la mano de prueba (una esfera): para colocar la espada solo importan el eje, el centro y el largo
FAKE_GRIP = {"diametro_taladro_mm": 2.4, "centro": [0.0, 0.0, 3.0], "eje": [0.0, 0.0, 1.0], "largo_canal_mm": 6.0,
             "t_entrada_mm": -3.0, "t_salida_mm": 3.0, "pared_min_mm": 2.0, "apoyo_dentado_grados": 180.0}


@pytest.fixture()
def grip_hand(tmp_path, std_hand):
    hand, meta = load_standard_hand(std_hand)
    path = str(tmp_path / "mano_agarre.stl")
    save_standard_hand(path, hand, {**meta, "agarre": FAKE_GRIP})
    return path


@pytest.fixture()
def placed(tmp_path, grip_hand):
    stl = str(tmp_path / "modelo.stl")
    S.to_trimesh(_armed(3.5)).export(stl)
    c = Controller()
    c.open_stl(stl)
    c.auto_symmetry()
    c.start_hand_place(grip_hand)
    feed(c, WRIST)
    assert c.session.project.hand is not None
    return c


def test_grip_fit_roundtrip_and_matrix():
    fit = P.GripFit.from_dict(FAKE_GRIP)
    assert fit.length == pytest.approx(6.0) and np.allclose(fit.axis, [0, 0, 1])
    m = P.prop_to_hand_matrix(fit)
    assert np.allclose(m[:3, 3], [0, 0, 0.0])                      # centro (z=3) + eje·t_in (−3)
    assert abs(np.linalg.det(m[:3, :3]) - 1.0) < 1e-9              # giro puro, sin espejo ni escala


def test_hand_fit_matrix_puts_the_standard_hand_where_it_is_drawn(placed, grip_hand):
    fit = placed.hand_fit()
    hand_local, _ = load_standard_hand(grip_hand)
    world = hand_local.transform(fit["matrix"][:3, :].astype(np.float64))
    hand_drawn = S.to_manifold(fit["hand"])
    inside = S.intersection(world, hand_drawn).volume()
    assert inside == pytest.approx(world.volume(), rel=0.02)       # la mano local, movida con la matriz, está en la dibujada


def test_preview_needs_a_hand_with_grip(tmp_path, std_hand):
    stl = str(tmp_path / "modelo.stl")
    S.to_trimesh(_armed(3.5)).export(stl)
    c = Controller()
    c.open_stl(stl)
    c.auto_symmetry()
    c.start_hand_place(std_hand)                                   # mano sin clave «agarre»
    feed(c, WRIST)
    assert c.set_grip_show(True) is False and not c.grip_show and "no tiene agarre" in c.message
    assert c.grip_prop_preview(c.hand_fit()) is None


def test_preview_places_the_sword_at_the_fist(placed):
    _short = placed.set_grip_param("finger_len", 5.5) and placed.set_grip_barbs("2.7")
    assert _short and placed.set_grip_show(True)
    fit = placed.hand_fit()
    mesh = placed.grip_prop_preview(fit)
    assert mesh is not None and len(mesh.faces) > 100
    # la cara de la guarda cae sobre el eje del taladro, en la cara de entrada de la mano, ya llevado al mundo
    gf = P.GripFit.from_dict(FAKE_GRIP)
    expected = (fit["matrix"] @ np.append(gf.center + gf.axis * gf.t_in, 1.0))[:3]
    assert np.min(np.linalg.norm(mesh.vertices - expected, axis=1)) < 1.2
    assert placed.grip_prop_preview(fit) is mesh                   # cacheada mientras nada cambie


def test_sync_draws_and_removes_the_sword(placed):
    placed.set_grip_param("finger_len", 5.5)
    placed.set_grip_barbs("2.7")
    scene = FakeScene()
    placed.sync(scene)
    assert "accesorio:agarre" not in scene.items and "mano:reemplazo" in scene.items
    placed.set_grip_show(True)
    placed.sync(scene)
    assert "accesorio:agarre" in scene.items
    placed.set_grip_show(False)
    placed.sync(scene)
    assert "accesorio:agarre" not in scene.items


def test_bad_spec_reports_instead_of_drawing(placed):
    placed.set_grip_show(True)                                     # lengüeta de 8 mm en un canal de 6: no entra
    assert placed.grip_prop_preview(placed.hand_fit()) is None
    assert "lengüetas" in placed.grip_summary()
