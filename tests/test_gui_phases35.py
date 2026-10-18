"""GUI Fases 3–5: espejado en vivo, vista previa de la pose sentada con colisiones y recortes manuales."""
import numpy as np
import pytest

from afm.gui.controller import Controller, Mode
from afm.gui.picking import PickResult
from afm.project import demo_project
from afm.testfigure import make_doll
from afm.trims import Trim

W = H = 600


class FakeScene:
    """Registra lo que `Controller.sync` pide dibujar, sin OpenGL."""

    def __init__(self):
        self.items = {}

    def set_mesh(self, name, mesh, color=None, clip_x=-1e9, clip_plane=None):
        self.items[name] = ("mesh", mesh, clip_x, clip_plane)

    def set_plane(self, name, plane, hs, center=None, color=None):
        self.items[name] = ("quad", plane)
        self.items[name + ":borde"] = ("lines", None)

    def set_lines(self, name, pl, color=None, closed=False, on_top=False):
        self.items[name] = ("lines", pl)

    def set_points(self, name, pts, color=None, size=10.0, on_top=False):
        self.items[name] = ("points", np.asarray(pts))

    def show(self, name, visible):
        pass

    def remove(self, name):
        self.items.pop(name, None)
        self.items.pop(name + ":borde", None)

    def clear(self):
        self.items.clear()


def make_ctrl(folder) -> Controller:
    m = make_doll()
    m.apply_scale(0.05)                       # el STL "viene" en otras unidades
    path = str(folder / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    return c


def fill_limbs(c: Controller) -> None:
    c.auto_symmetry()
    for l in demo_project().limbs:
        c.session.add_limb(l.name, l.kind, l.plane, l.thickness, auto=False)
        c.session.set_limb_polygon(l.name, l.polygon)


def click_uv(c: Controller, uv) -> None:
    world = c.draft.plane.to_world(np.array([uv], float))[0]
    px, py, _ = c.camera.project(world, W, H)[0]
    c.click(px, py, W, H)


@pytest.fixture()
def ctrl(tmp_path):
    return make_ctrl(tmp_path)


@pytest.fixture()
def limbs(ctrl):
    fill_limbs(ctrl)
    return ctrl


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Muñeco ya generado (una sola vez para todo el módulo: generar tarda). Los tests no deben editarlo."""
    c = make_ctrl(tmp_path_factory.mktemp("muneco"))
    fill_limbs(c)
    out = c.generate()
    assert out is not None and out.validation["ok"], out and out.validation["problems"]
    return c


# ------------------------------------------------------------------ espejado en vivo
def test_mirror_needs_symmetry_then_forces_one_side(ctrl):
    ctrl.set_mirror_live(True)
    assert not ctrl.mirror_live and "simetría" in ctrl.message
    ctrl.auto_symmetry()
    ctrl.set_mirror_live(True)
    assert ctrl.mirror_live and ctrl.one_side and ctrl.mirror_active()
    ctrl.set_one_side(False)                      # volver a ver los dos lados apaga el espejo
    assert not ctrl.mirror_live and not ctrl.mirror_active()


def test_mirror_sync_reflects_figure_silhouettes_pins_and_draft(limbs):
    c, sc = limbs, FakeScene()
    c.session.limb("brazo").pin_center = (16.0, 1.0, 60.0)
    c.sync(sc)
    assert not [k for k in sc.items if k.startswith("espejo:")]
    c.set_mirror_live(True)
    c.sync(sc)
    work = c.session.work
    kind, mesh, clip_x, clip_plane = sc.items["espejo:figura"]
    assert np.allclose(mesh.bounds[:, 0], [-work.bounds[1, 0], -work.bounds[0, 0]])
    assert np.allclose(mesh.bounds[:, 1:], work.bounds[:, 1:])
    assert clip_plane == (1.0, 0.0, 0.0, 0.0)                   # del espejo solo se ve x <= 0
    assert sc.items["figura"][2] == 0.0                         # y del lado real, x >= 0
    real = np.asarray(c.session.limb("brazo").polygon)
    assert np.allclose(sc.items["espejo:silueta:brazo"][1], real * [-1, 1, 1])
    assert np.allclose(sc.items["espejo:pasador:brazo"][1], [[-16.0, 1.0, 60.0]])
    # el polígono que se está dibujando también se refleja en vivo
    c.start_polygon("pierna")
    c.draft.clear()
    click_uv(c, (3, 5))
    click_uv(c, (20, 5))
    c.sync(sc)
    assert np.allclose(sc.items["espejo:poligono"][1], c.draft.world() * [-1, 1, 1])
    assert "espejo:silueta:pierna" not in sc.items              # la que se edita la muestra el editor
    c.set_mirror_live(False)
    c.sync(sc)
    assert not [k for k in sc.items if k.startswith("espejo:")]


def test_mirror_hidden_while_placing_a_pin(limbs):
    c, sc = limbs, FakeScene()
    c.set_mirror_live(True)
    c.start_pin("brazo")
    c.sync(sc)
    assert "espejo:figura" not in sc.items                      # el plano de recorte del modo pasador ya ocupa ese lugar


# ------------------------------------------------------------------ pose sentada
def test_pose_view_needs_a_generated_figure(limbs):
    limbs.set_pose_view(True)
    assert not limbs.pose_view and "Primero generá" in limbs.message
    assert limbs.pose_check() is None and limbs.pose_limit() is None


def test_pose_sitting_angle_is_free_and_going_past_the_limit_collides(built):
    c = built
    try:
        c.set_pose_view(True)
        assert c.pose_active() and c.pose_angle == c.session.project.params.sit_angle
        rep = c.pose_report
        assert rep["ok"] and rep["torso"] == {} and rep["pairs"] == {}, rep
        assert "sin colisiones" in c.message and "90°" in c.message
        limit = c.pose_limit()
        assert limit == pytest.approx(c.session.project.params.sit_angle + c.session.project.params.hip_margin)
        c.set_pose_angle(0)
        assert c.pose_report["ok"] and "0°" in c.message
        c.set_pose_angle(180)                                    # la pierna se mete en el torso
        bad = c.pose_report
        assert not bad["ok"] and bad["torso"], bad
        assert "choca con el torso" in c.message and "pasa del límite" in c.message
        assert all(n.startswith("pierna_") for n in bad["torso"])
        c.set_pose_angle(500)                                    # se acota a 0–180
        assert c.pose_angle == 180.0
    finally:
        c.set_pose_view(False)


def test_pose_sync_draws_generated_pieces_and_marks_collisions(built):
    c, sc = built, FakeScene()
    try:
        c.sync(sc)
        assert "figura" in sc.items and not [k for k in sc.items if k.startswith("pose:")]
        c.set_pose_view(True)
        c.sync(sc)
        assert {"pose:torso", "pose:brazo_izq", "pose:brazo_der", "pose:pierna_izq", "pose:pierna_der"} <= set(sc.items)
        assert "figura" not in sc.items and "plano:simetria" not in sc.items
        izq, der = sc.items["pose:pierna_izq"][1], sc.items["pose:pierna_der"][1]
        assert np.allclose(izq.bounds[:, 0], [-der.bounds[1, 0], -der.bounds[0, 0]], atol=1e-4)   # la derecha es el espejo
        # a 90° la pierna quedó adelante (y < 0 es atrás en este muñeco: el frente es +Y)
        assert izq.bounds[1, 1] > 20.0
        c.set_pose_angle(0)
        c.sync(sc)
        assert sc.items["pose:pierna_izq"][1].bounds[1, 1] < 20.0
        c.set_pose_view(False)
        c.sync(sc)
        assert "figura" in sc.items and not [k for k in sc.items if k.startswith("pose:")]
    finally:
        c.set_pose_view(False)


def test_pose_view_ends_when_the_figure_is_invalidated(tmp_path):
    c = make_ctrl(tmp_path)
    fill_limbs(c)
    assert c.generate() is not None
    c.set_pose_view(True)
    assert c.pose_active()
    c.session.set_limb_polygon("brazo", c.session.limb("brazo").polygon)     # editar invalida la figura generada
    assert not c.pose_active() and c.pose_check() is None
    c.generate()
    assert not c.pose_view                                     # la figura nueva reemplaza a la de la vista previa


# ------------------------------------------------------------------ recortes manuales
def test_draw_polygon_trim_on_symmetry_plane(limbs):
    c = limbs
    c.start_trim("torso", op="subtract", distance=20.0)
    assert c.mode == Mode.POLYGON and c.trim_cfg["target"] == "torso" and c.edit_limb is None
    assert np.allclose(c.draft.plane.normal, [1, 0, 0]) and np.allclose(c.camera.yaw % 360, 0.0)
    for uv in ((-4, 50), (4, 50), (4, 60), (-4, 60)):
        click_uv(c, uv)
    assert c.session.project.trims == []                       # todavía abierto
    c.close_polygon()
    (t,) = c.session.project.trims
    assert (t.target, t.op, t.shape, t.distance, t.symmetric) == ("torso", "subtract", "polygon", 20.0, True)
    assert np.allclose(t.polygon[:, 0], 0.0) and c.edit_trim == 0 and c.session.outcome is None
    assert "Recorte 1 guardado" in c.message
    # mover un vértice cambia el mismo recorte (no agrega otro)
    click_uv(c, (4, 60))
    click_uv(c, (6, 62))
    assert len(c.session.project.trims) == 1
    assert np.allclose(c.session.project.trims[0].polygon[2], c.draft.world()[2])
    assert np.allclose(c.session.project.trims[0].polygon[2][1:], [6, 62], atol=0.05)
    c.cancel()
    assert c.mode == Mode.VIEW and c.trim_cfg is None and c.edit_trim is None and "recorte" in c.message


def test_invalid_trim_polygon_is_not_saved(limbs):
    c = limbs
    c.start_trim("torso")
    for uv in ((0, 50), (8, 60), (8, 50), (0, 60)):            # se cruza consigo mismo
        click_uv(c, uv)
    c.close_polygon()
    assert c.session.project.trims == [] and "se cruza" in c.message


def test_edit_existing_polygon_trim_by_index(limbs):
    c = limbs
    c.start_trim("pierna", op="union", distance=10.0, symmetric=False)
    for uv in ((1, 10), (9, 10), (9, 20)):
        click_uv(c, uv)
    c.close_polygon()
    c.cancel()
    c.start_trim(index=0)
    assert c.draft.closed and len(c.draft.pts) == 3
    assert c.trim_cfg == {"target": "pierna", "op": "union", "distance": 10.0, "symmetric": False}
    c.cancel()
    c.add_trim_primitive("torso", "sphere", (5, 0, 60), 3)
    c.start_trim(index=1)                                      # una esfera no se edita con vértices
    assert c.mode == Mode.VIEW and "polígono" in c.message
    c.start_trim(index=7)
    assert "No existe" in c.message


def test_primitive_trims_build_the_expected_tools(limbs):
    c = limbs
    s = c.add_trim_primitive("torso", "sphere", (5, 0, 60), 4.0)
    b = c.add_trim_primitive("torso", "box", (5, 0, 50), (6, 4, 2))
    y = c.add_trim_primitive("pierna", "cylinder", (8, 0, 20), 3.0, length=12.0)
    assert (s, b, y) == (0, 1, 2)
    t_s, t_b, t_y = c.session.project.trims
    assert t_s.tool().volume() == pytest.approx(4 / 3 * np.pi * 4.0 ** 3, rel=0.03)
    assert t_b.tool().volume() == pytest.approx(6 * 4 * 2, rel=1e-4)
    assert t_y.tool().volume() == pytest.approx(np.pi * 3.0 ** 2 * 12.0, rel=0.03)
    bb = t_y.tool().bounding_box()                              # eje en X, centrado en el punto elegido
    assert np.allclose([bb[0], bb[3]], [8 - 6, 8 + 6], atol=1e-4)
    assert np.allclose([(bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2], [0, 20], atol=1e-4)
    assert [e["label"] for e in c.trim_entries()] == ["1. Restar esfera en «torso»", "2. Restar caja en «torso»",
                                                      "3. Restar cilindro en «pierna»"]
    # sobrevive al guardar/abrir el proyecto
    back = Trim.from_dict(t_b.to_dict())
    assert back.shape == "box" and back.tool().volume() == pytest.approx(48, rel=1e-4)


def test_invalid_trim_requests_give_a_reason_and_change_nothing(ctrl):
    assert ctrl.add_trim_primitive("torso", "sphere", (5, 0, 60), 3) is None and "simetría" in ctrl.message
    ctrl.auto_symmetry()
    assert ctrl.add_trim_primitive("nadie", "sphere", (5, 0, 60), 3) is None and "No existe la pieza" in ctrl.message
    assert ctrl.add_trim_primitive("torso", "cono", (5, 0, 60), 3) is None and "Forma desconocida" in ctrl.message
    assert ctrl.add_trim_primitive("torso", "sphere", (5, 0, 60), -1) is None and "positivo" in ctrl.message
    assert ctrl.add_trim_primitive("torso", "sphere", (5, 0, 60), 3, op="xor") is None and "operación" in ctrl.message
    assert ctrl.add_trim_primitive("torso", "sphere", (5, 0, 60), 3, length=0.0) is None and "distancia" in ctrl.message
    ctrl.start_trim("nadie")
    assert ctrl.mode == Mode.VIEW and "No existe la pieza" in ctrl.message
    assert ctrl.session.project.trims == []


def test_pick_a_click_to_place_a_primitive_trim(limbs):
    c = limbs
    c.start_trim_pick("torso", "sphere", 3.0)
    assert c.mode == Mode.TRIM_PICK
    c.picker.pick_pixel = lambda *a, **k: PickResult(np.array([-6.0, 1.0, 58.0]), np.zeros(3), 0, np.array([0.0, 1.0, 0.0]), True)
    c.click(10, 10, 100, 100)
    assert c.mode == Mode.VIEW and "Generá de nuevo" in c.message
    (t,) = c.session.project.trims
    assert t.shape == "sphere" and np.allclose(t.polygon.mean(axis=0), [6.0, 1.0, 58.0])    # se pasa al lado de trabajo (+X)
    c.start_trim_pick("torso", "sphere", 3.0)
    c.cancel()
    assert c.mode == Mode.VIEW and c.trim_cfg is None


def test_change_operation_and_remove_trims(limbs):
    c = limbs
    c.add_trim_primitive("torso", "sphere", (5, 0, 60), 3)
    c.add_trim_primitive("torso", "box", (5, 0, 50), 2)
    c.session.outcome = object()
    c.set_trim_op(0, "union")
    assert c.session.project.trims[0].op == "union" and c.session.outcome is None
    c.set_trim_op(0, "xor")
    assert c.session.project.trims[0].op == "union" and "inválidos" in c.message
    c.remove_trim(0)
    assert [t.shape for t in c.session.project.trims] == ["box"]
    c.remove_trim(5)
    assert "No existe" in c.message
    c.add_trim_primitive("pierna", "sphere", (8, 0, 20), 2)
    assert [t.target for t in c.session.project.trims] == ["torso", "pierna"]
    c.remove_plane("pierna")                                   # quitar un miembro también quita sus recortes
    assert [t.target for t in c.session.project.trims] == ["torso"]


def test_removing_the_target_limb_cancels_the_trim_editor(limbs):
    c = limbs
    c.start_trim("pierna")
    c.remove_plane("pierna")
    assert c.mode == Mode.VIEW and c.trim_cfg is None and c.draft is None


def test_sync_draws_trims_and_their_mirror(limbs):
    c, sc = limbs, FakeScene()
    c.add_trim_primitive("torso", "sphere", (5, 0, 60), 3)
    c.add_trim_primitive("torso", "box", (5, 0, 50), 2)
    c.sync(sc)
    assert {"recorte:0", "recorte:1"} <= set(sc.items) and not [k for k in sc.items if k.startswith("espejo:")]
    assert len(sc.items["recorte:0"][1]) == 3                   # tres círculos
    assert len(sc.items["recorte:1"][1]) == 12                  # doce aristas
    c.set_mirror_live(True)
    c.sync(sc)
    assert np.allclose(sc.items["espejo:recorte:0"][1][0], sc.items["recorte:0"][1][0] * [-1, 1, 1])
    c.remove_trim(0)
    c.sync(sc)
    assert "recorte:1" not in sc.items and "recorte:0" in sc.items     # quedó una sola (la caja, ahora la 0)
    assert len(sc.items["recorte:0"][1]) == 12 and "espejo:recorte:1" not in sc.items


def test_a_subtract_trim_removes_material_from_the_generated_torso(built, tmp_path):
    from afm import solids as S
    base = built.session.outcome.result.pieces["torso"]
    c = make_ctrl(tmp_path)
    fill_limbs(c)
    # esfera que rompe la superficie del pecho (la «esfera panza» del tutorial): y = 9,2 es el borde del torso en x = 6
    c.add_trim_primitive("torso", "sphere", (6, 9.2, 58), 4.0)
    tool = c.session.project.trims[0].tool()
    expected = float((base ^ S.union(tool, S.mirror_x(tool))).volume())       # una esfera por cada lado (espejo)
    assert expected > 50
    out = c.generate()
    assert out is not None and out.validation["ok"], out and out.validation["problems"]
    lost = float(base.volume() - out.result.pieces["torso"].volume())
    assert lost == pytest.approx(expected, rel=0.06)


def test_a_cut_that_stays_inside_the_solid_is_dropped_known_limit(built, tmp_path):
    """Un recorte que no llega a la superficie deja un hueco cerrado; `drop_small_components` lo descarta (límite documentado)."""
    c = make_ctrl(tmp_path)
    fill_limbs(c)
    c.add_trim_primitive("torso", "sphere", (6, 0, 58), 4.0)
    out = c.generate()
    assert out.result.pieces["torso"].volume() == pytest.approx(built.session.outcome.result.pieces["torso"].volume(), abs=0.5)


# ------------------------------------------------------------------ voladizos y exportación en la pose de cadera
def test_overhangs_with_a_generated_figure_are_drawn_on_the_hip_pose(built):
    c, sc = built, FakeScene()
    try:
        c.set_overhang_angle(10.0)                        # muñeco con curvas: seguro hay caras que cuelgan
        c.set_show_overhangs(True)
        assert c.pose_active(), "con la figura generada, los voladizos se ven en la pose de cadera"
        assert "pose de cadera" in c.message and "cabeza arriba" in c.message
        c.sync(sc)
        over = [k for k in sc.items if k.startswith("pose:") and k.endswith(":voladizo")]
        assert over, "alguna pieza de la pose tiene voladizos"
        for k in over:                                    # cada pieza se parte en sana + voladiza: no se pierde ninguna cara
            name = k[: -len(":voladizo")]
            n_faces = len(sc.items[name][1].faces) + len(sc.items[k][1].faces)
            full = [m for m, _ in c._pose_overhang_cache()]
            assert n_faces in {len(m.faces) for m in full}
        # la figura se ve cabeza arriba: el torso llega más alto que las piernas sentadas
        assert sc.items["pose:torso"][1].bounds[1, 2] > sc.items["pose:pierna_izq"][1].bounds[1, 2]
        c.set_show_overhangs(False)
        c.sync(sc)
        assert not [k for k in sc.items if k.endswith(":voladizo")]
    finally:
        c.set_show_overhangs(False)
        c.set_pose_view(False)


def test_pose_overhang_masks_share_one_bed_height(built):
    from afm.orient import overhang_mask
    c = built
    try:
        c.set_pose_view(True)
        pairs = c._pose_overhang_cache()
        assert pairs and all(mask.shape == (len(m.faces),) for m, mask in pairs)
        zmin = min(m.bounds[0][2] for m, _ in pairs)          # la cama es la del conjunto, no la de cada pieza
        for m, mask in pairs:
            assert (mask == overhang_mask(m, c.overhang_angle, zmin=zmin)).all()
    finally:
        c.set_pose_view(False)


def test_export_keeps_the_hip_pose_head_up(built, tmp_path):
    import os
    import trimesh
    from afm import solids as S
    c = built
    base = os.path.splitext(os.path.basename(c.session.stl_path))[0]
    info = c.export(str(tmp_path / "out"), orient=True)
    assert info["archivos"]["orientacion"]["etiqueta"] == "pose de cadera, cabeza arriba"
    out = tmp_path / "out" / "piezas"
    res = c.session.outcome.result
    drop = None
    for part, mf in res.pieces.items():
        orig = S.to_printable_trimesh(mf)
        exported = trimesh.load(str(out / f"{base}_{part}.stl"), process=False)
        d = exported.bounds - orig.bounds                     # solo se baja: ni giro ni espejo
        assert np.allclose(d[:, :2], 0.0, atol=1e-3) and np.allclose(d[0, 2], d[1, 2], atol=1e-3), part
        drop = d[0, 2] if drop is None else drop
        assert d[0, 2] == pytest.approx(drop, abs=1e-3)       # todas bajan lo mismo: siguen ensambladas
    asm = trimesh.util.concatenate([trimesh.load(str(p), process=False) for p in out.glob("*.stl")])
    assert asm.bounds[0][2] == pytest.approx(0.0, abs=1e-3)   # apoyada en la cama
    zc = {p.stem: trimesh.load(str(p), process=False).centroid[2] for p in out.glob("*.stl")}
    torso = next(v for k, v in zc.items() if k.endswith("torso"))
    legs = [v for k, v in zc.items() if "pierna" in k]
    assert legs and torso > max(legs)                         # el torso arriba y las piernas más abajo: no patas para arriba
