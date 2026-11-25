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
def hand_stl(tmp_path):
    """Mano de juguete: toro en el plano XY con un agujero de Ø2,0 (eje Z); es mucho más grande que una mano."""
    t = trimesh.creation.torus(major_radius=4.4, minor_radius=3.4, major_sections=48, minor_sections=24)
    path = str(tmp_path / "mano_toro.stl")
    t.export(path)
    return path


# elección manual sobre el toro: 3 puntos de la cara de arriba (z = 3,4) y el centro del agujero
TORUS_PLANE = ((4.4, 0.0, 3.4), (0.0, 4.4, 3.4), (-4.4, 0.0, 3.4))
TORUS_CENTER = (0.0, 0.0, 3.4)


def _manual(c, end_center=None):
    c.grip_manual = P.ManualGrip(TORUS_PLANE, TORUS_CENTER, end_center)
    return c


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
    assert c.begin_grip(hand_stl) is None and "Primero elegí el plano" in c.message      # sin elección manual no se taladra
    _manual(c)
    job = c.begin_grip(hand_stl)
    assert job is not None and c.busy
    assert c.begin_grip(hand_stl) is None                       # no se lanzan dos tareas a la vez
    c.finish_grip(job(), None)
    assert not c.busy and c.grip is not None
    s = c.grip_summary()
    assert "Taladro Ø2.4" in s and "Dentado Ø2.9" in s and not c.grip_stale


def test_error_frees_the_controller(tmp_path):
    c = _manual(Controller())
    bad = tmp_path / "vacio.stl"
    bad.write_text("no es una malla")
    assert c.run_grip(str(bad)) is None
    assert not c.busy and c.grip is None and "No se pudo taladrar" in c.message


def test_save_hand_and_plate(hand_stl, tmp_path):
    c = _manual(Controller())
    assert c.save_grip_hand(str(tmp_path / "x.stl")) is False   # todavía no hay mano taladrada
    assert c.run_grip(hand_stl) is not None
    _short_fingers(c)
    out_hand, out_plate = str(tmp_path / "mano_agarre.stl"), str(tmp_path / "placa.stl")
    assert c.save_grip_hand(out_hand) and c.save_grip_plate(out_plate)
    meta = json.load(open(os.path.splitext(out_hand)[0] + ".mano.json"))
    assert meta["formato"] == "afm-mano-1" and meta["agarre"]["diametro_taladro_mm"] == 2.4
    assert meta["agarre"]["eleccion"]["manual"]["puntos_plano"] == [list(p) for p in TORUS_PLANE]
    assert len(trimesh.load(out_plate).split(only_watertight=False)) == len(c.grip_barbs)
    from afm.hand import load_standard_hand                      # la mano con agarre sigue siendo una mano estándar válida
    hand, m = load_standard_hand(out_hand)
    assert hand.volume() > 0 and m["agarre"]["eje"]


def test_stale_bore_blocks_saving_the_hand_but_not_the_plate(hand_stl, tmp_path):
    c = _manual(Controller())
    c.run_grip(hand_stl)
    _short_fingers(c)
    c.set_grip_param("bore_d", 2.0)
    assert c.grip_stale and "volvé a taladrar" in c.grip_summary()
    assert c.save_grip_hand(str(tmp_path / "x.stl")) is False
    assert c.save_grip_plate(str(tmp_path / "p.stl")) is True    # la placa usa el taladro ya hecho (2,4)


def test_plate_reports_bad_spec(hand_stl, tmp_path):
    c = _manual(Controller())
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


# ---------------------------------------------------------------- elección manual con clics sobre la mano pegada
@pytest.fixture()
def placed_hand(placed):
    """La mano estándar (esfera de prueba) pegada a la figura, con la previsualización del agarre encendida."""
    assert placed.set_grip_preview(True)
    return placed


def _hand_pts(c):
    """3 puntos de un plano sobre la mano pegada, en el mundo (la cara de abajo de la esfera, tocando su superficie)."""
    m = c.hand_fit()["matrix"]
    local = [(3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (-3.0, 0.0, 0.0)]
    return [(m @ np.append(p, 1.0))[:3] for p in local], m


def test_picking_needs_the_preview_on(placed):
    assert placed.start_grip_plane() is False and "Previsualizar el agarre" in placed.message


def _click_world(c, world_pt, w=800, h=600):
    """Clic real: el píxel donde la cámara ve `world_pt` (sin simular el picker)."""
    px, py = c.camera.project(np.asarray(world_pt, float), w, h)[0, :2]
    return c.click(float(px), float(py), w, h)


def _look_along_plane_normal(c, n_world):
    """Pone la cámara de frente al plano elegido (si no, el rayo puede ser casi paralelo a él)."""
    c.camera.look_along(n_world, target=c.camera.target)


def test_plane_then_center_then_end(placed_hand):
    c = placed_hand
    assert c.start_grip_center() is False and "plano" in c.message              # el orden es plano → centro → final
    assert c.start_grip_plane() and c.mode.name == "GRIP_PLANE" and c.picker is not None
    pts, m = _hand_pts(c)
    feed(c, pts)
    assert c.mode.name == "VIEW" and c.grip_manual is not None and c.grip_manual.center is None
    # los puntos se guardan en el marco LOCAL de la mano (invertida la matriz con la que se dibuja)
    assert np.allclose(np.array(c.grip_manual.points), [(3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (-3.0, 0.0, 0.0)], atol=1e-6)
    assert not c.grip_manual_ready and c.begin_grip("x.stl") is None
    assert c.start_grip_center()
    _look_along_plane_normal(c, m[:3, :3] @ np.array([0.0, 0.0, 1.0]))      # el centro es un clic sobre el plano, no sobre la malla
    _click_world(c, (m @ np.array([0.0, 0.0, 0.0, 1.0]))[:3])
    assert c.grip_manual_ready and c.grip_manual.end_center is None
    assert c.start_grip_end()
    _click_world(c, (m @ np.array([0.5, 0.0, 3.0, 1.0]))[:3])
    assert c.grip_manual.end_center is not None
    c.clear_grip_end()
    assert c.grip_manual.end_center is None
    c.clear_grip_manual()
    assert c.grip_manual is None


def test_collinear_points_are_refused(placed_hand):
    c = placed_hand
    assert c.start_grip_plane()
    m = c.hand_fit()["matrix"]
    feed(c, [(m @ np.append(p, 1.0))[:3] for p in [(0, 0, 0), (1, 0, 0), (2, 0, 0)]])
    assert c.grip_manual is None and "alineados" in c.message


def test_a_new_plane_clears_the_center(placed_hand):
    c = placed_hand
    c.grip_manual = P.ManualGrip(((3, 0, 0), (0, 3, 0), (-3, 0, 0)), (0, 0, 0), (0.5, 0, 3))
    assert c.start_grip_plane()
    pts, _ = _hand_pts(c)
    feed(c, pts)
    assert c.grip_manual.center is None and c.grip_manual.end_center is None


def test_preview_key_needs_the_manual_choice_and_sync_draws_the_cylinder(placed_hand):
    c = placed_hand
    assert c._grip_preview_key() is None and not c.grip_preview_stale             # sin elección: solo se ve la mano
    scene = FakeScene()
    c.sync(scene)
    assert "agarre:cilindro" not in scene.items
    c.grip_manual = P.ManualGrip(((3, 0, 0), (0, 3, 0), (-3, 0, 0)), (0.0, 0.0, 0.0))
    assert c._grip_preview_key() is not None and c.grip_preview_stale
    c.sync(scene)
    assert "agarre:plano" in scene.items and "agarre:cilindro" in scene.items
    assert "agarre:final" not in scene.items
    c.clear_grip_manual()
    c.sync(scene)
    assert "agarre:cilindro" not in scene.items and "agarre:plano" not in scene.items


def test_preview_job_carves_with_the_manual_choice(placed_hand):
    c = placed_hand
    c.set_grip_param("bore_d", 2.0)
    c.grip_manual = P.ManualGrip(((3, 0, 0), (0, 3, 0), (-3, 0, 0)), (0.0, 0.0, 0.0))
    job = c.begin_grip_preview()
    assert job is not None
    res = job()
    c.finish_grip_preview(res, None)
    # la esfera de prueba no tiene un anillo cerrado alrededor del agujero: se informa en vez de dibujar algo falso
    assert res["error"] == "" or "anillo" in res["error"] or "pared" in res["error"]


# ---------------------------------------------------------------- el centro es un punto del PLANO, no de la figura
def _plane_ready(c):
    """Plano elegido sobre la mano pegada y modo «centro» activo; devuelve (matriz local→mundo, normal del plano en el mundo)."""
    assert c.start_grip_plane()
    pts, m = _hand_pts(c)
    feed(c, pts)
    assert c.start_grip_center()
    n_local = np.array([0.0, 0.0, 1.0])
    return m, m[:3, :3] @ n_local


def test_center_can_be_outside_the_mesh(placed_hand):
    """Un clic sobre el plano pero FUERA de la silueta de la mano (la malla no está ahí) sí vale como centro."""
    c = placed_hand
    m, n_world = _plane_ready(c)
    mesh = c._grip_source_mesh()
    outside_local = np.array([mesh.bounds[1][0] + 5.0, 0.0, 0.0])           # 5 mm más allá de la mano, sobre el plano z = 0
    # el plano de los 3 puntos de _hand_pts pasa por z = 0 (local): el punto tiene que estar sobre ese plano
    assert not mesh.contains([outside_local])[0]
    world = (m @ np.append(outside_local, 1.0))[:3]
    assert c.picker.pick_pixel is not None
    c.picker.pick_pixel = lambda *a, **k: (_ for _ in ()).throw(AssertionError("el centro no debe pasar por la malla"))
    _look_along_plane_normal(c, n_world)
    c.click(*c.camera.project(world, 800, 600)[0, :2], 800, 600)
    assert c.grip_manual.center is not None, c.message
    assert np.allclose(c.grip_manual.center, outside_local, atol=0.05)       # quedó donde se hizo clic, en el plano
    assert c.mode.name == "VIEW"


def test_center_is_projected_exactly_on_the_plane(placed_hand):
    c = placed_hand
    m, n_world = _plane_ready(c)
    _look_along_plane_normal(c, n_world)
    c.click(400.0, 300.0, 800, 600)
    assert c.grip_manual.center is not None, c.message
    n, o = P.plane_from_points(c.grip_manual.points)
    assert abs(float((np.asarray(c.grip_manual.center) - o) @ n)) < 1e-6


def test_center_click_edge_on_to_the_plane_is_refused_with_a_message(placed_hand):
    c = placed_hand
    m, n_world = _plane_ready(c)
    # mirando el plano de canto el rayo es casi paralelo a él: no hay punto fiable
    t = np.cross(n_world, [0.0, 0.0, 1.0])
    t = t / np.linalg.norm(t) if np.linalg.norm(t) > 1e-6 else np.array([1.0, 0.0, 0.0])
    _look_along_plane_normal(c, t)
    c.click(400.0, 300.0, 800, 600)
    assert c.grip_manual.center is None and "plano" in c.message
    assert c.mode.name == "GRIP_CENTER"                                      # sigue esperando un clic válido


def test_end_center_is_a_point_of_the_end_plane_not_of_the_mesh(placed_hand):
    c = placed_hand
    m, n_world = _plane_ready(c)
    _look_along_plane_normal(c, n_world)
    c.click(400.0, 300.0, 800, 600)
    assert c.grip_manual_ready
    assert c.start_grip_end()
    mesh = c._grip_source_mesh()
    n, p_end = P.manual_end_plane(mesh, c.grip_manual)
    near_axis = p_end + np.array([0.4, 0.0, 0.0])                           # poco inclinado respecto del eje
    world = (m @ np.append(near_axis, 1.0))[:3]
    c.picker.pick_pixel = lambda *a, **k: (_ for _ in ()).throw(AssertionError("el final no debe pasar por la malla"))
    c.click(*c.camera.project(world, 800, 600)[0, :2], 800, 600)
    assert c.grip_manual.end_center is not None, c.message
    assert abs(float((np.asarray(c.grip_manual.end_center) - p_end) @ n)) < 0.05


def test_hover_does_not_snap_to_vertices_while_choosing_the_center(placed_hand):
    c = placed_hand
    _plane_ready(c)
    assert not c.hover_active()
