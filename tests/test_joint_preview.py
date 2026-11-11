"""Vista previa de la articulación (rodilla / codo) sin generar la figura: motor, sesión y controlador (sin GTK)."""
import dataclasses
import json
import os
import shutil
import types

import numpy as np
import pytest

from afm import catalog_project as CP
from afm import solids as S
from afm.assembly import synthesize_host
from afm.recipe import Recipe, load_choices, recipe_params
from afm.gui.controller import Controller
from afm.gui.session import Session
from afm.joint_preview import prepare_limb, preview_joint
from afm.joints import JointSpec
from afm.params import HingeSpec
from afm.planes import Plane
from afm.project import demo_project
from afm.testfigure import make_doll


def elbow(h: float = 3.0, **kw) -> JointSpec:
    # brazo del muñeco: eje transversal (X) a `h` mm sobre el plano de articulación z = 59
    d = dict(name="codo", limb="brazo", kind="elbow", plane=Plane.from_normal((18.5, 0, 59.0), (0, 0, 1), "articulación"),
             axis_center=(18.5, 0.0, 59.0 + h), hinge=HingeSpec(pin_d=2.0, pin_len=0.6, tongue_t=2.0, end_r=3.0))
    d.update(kw)
    return JointSpec(**d)


@pytest.fixture(scope="module")
def params():
    return dataclasses.replace(demo_project().params, decimate=0)


@pytest.fixture(scope="module")
def arm(params):
    body = S.to_manifold(make_doll())
    spec = next(l for l in demo_project().limbs if l.name == "brazo")
    return prepare_limb(body, spec, params)


class FakeScene:
    def __init__(self):
        self.items, self.visible = {}, {}

    def set_mesh(self, name, mesh, color=None, clip_x=-1e9, clip_plane=None):
        self.items[name] = mesh
        self.visible[name] = True

    def show(self, name, visible):
        self.visible[name] = visible

    def remove(self, name):
        self.items.pop(name, None)
        self.visible.pop(name, None)

    def clear(self):
        self.items.clear()

    def __getattr__(self, name):          # set_plane / set_lines / set_points…: no interesan acá
        if name.startswith("set_"):
            return lambda key, *a, **k: self.items.setdefault(key, None)
        raise AttributeError(name)


# ------------------------------------------------------------------ motor
def test_preview_gives_carved_proximal_and_posed_distal(arm, params):
    limb, face = arm
    pv = preview_joint(limb, face, elbow(), params)
    assert pv.proximal_mesh().is_watertight and len(pv.proximal_mesh().faces) > 100
    assert pv.carved_mm3 > 1.0 and 0.0 < pv.carved_pct < 100.0
    assert pv.h == pytest.approx(3.0)
    # el distal girado 0° y 60° es el mismo sólido movido: mismo volumen, otra posición
    a, b = pv.distal_mesh(0.0), pv.distal_mesh(60.0)
    assert abs(a.volume - b.volume) < 1e-3 * a.volume
    assert np.linalg.norm(a.centroid - b.centroid) > 1.0
    assert "Hueco" in pv.summary()


def test_axis_closer_to_the_plane_carves_less(arm, params):
    limb, face = arm
    near, far = (preview_joint(limb, face, elbow(h), params) for h in (2.5, 4.5))
    assert near.carved_mm3 < far.carved_mm3


def test_shorter_sweep_carves_less(arm, params):
    limb, face = arm
    full = preview_joint(limb, face, elbow(), params)
    short = preview_joint(limb, face, elbow(flex_range=(0.0, 45.0)), params)
    assert short.carved_mm3 < full.carved_mm3
    assert short.flex_range == (0.0, 45.0)


def test_preview_rejects_axis_below_the_plane(arm, params):
    limb, face = arm
    with pytest.raises(ValueError, match="0,5 mm"):
        preview_joint(limb, face, elbow(h=0.2), params)


def test_build_joint_defaults_unchanged(arm, params):
    """Los parámetros de resolución son solo de la vista previa: con los valores por omisión sale lo de siempre."""
    from afm.joints import build_joint

    limb, face = arm
    a = build_joint(limb, elbow(), face.center, params)
    b = build_joint(limb, elbow(), face.center, params, 12, 0.25)
    assert a.proximal.volume() == pytest.approx(b.proximal.volume())


# ----------------------------------------------------------------- sesión
@pytest.fixture()
def ctrl(tmp_path):
    path = str(tmp_path / "muneco.stl")
    make_doll().export(path)
    c = Controller()
    c.open_stl(path)
    c.auto_symmetry()
    c.session.project.limbs = demo_project().limbs
    c.session.project.params = dataclasses.replace(c.session.project.params, decimate=0)
    # plano inclinado 27° de costado, como un codo cortado en diagonal
    c.session.add_joint("brazo", Plane.from_normal((18.5, 0, 59.0), (0, 0.5, 1.0), "articulación"), anchor=(18.5, 0, 59.0))
    return c


def test_straightening_the_plane_keeps_axis_and_height(ctrl):
    s = ctrl.session
    j = s.joint("codo")
    axis0, h0 = np.array(j.axis_center), s.joint_height("codo")
    assert abs(Session.joint_plane_side_tilt_deg(j.plane.normal)) > 20
    s.set_joint_plane_side_tilt("codo", 0.0)
    assert Session.joint_plane_side_tilt_deg(j.plane.normal) == pytest.approx(0.0, abs=1e-9)
    assert np.allclose(j.axis_center, axis0)                          # el eje no se movió
    assert s.joint_height("codo") == pytest.approx(h0)                # y quedó a la misma altura del plano
    with pytest.raises(ValueError):
        s.set_joint_plane_side_tilt("codo", 75.0)


def test_set_joint_height_moves_axis_vertically(ctrl):
    s = ctrl.session
    x, y, _z = s.joint("codo").axis_center
    s.set_joint_height("codo", 2.0)
    assert s.joint_height("codo") == pytest.approx(2.0)
    assert s.joint("codo").axis_center[:2] == pytest.approx((x, y))
    with pytest.raises(ValueError, match="0,5 mm"):
        s.set_joint_height("codo", 0.2)


def test_preview_key_changes_with_every_input(ctrl):
    s = ctrl.session
    k = s.joint_preview_key("codo")
    s.set_joint_height("codo", 2.0)
    k2 = s.joint_preview_key("codo")
    s.set_joint_plane_side_tilt("codo", 0.0)
    k3 = s.joint_preview_key("codo")
    s.set_joint_range("codo", 0.0, 45.0)
    k4 = s.joint_preview_key("codo")
    assert len({k, k2, k3, k4}) == 4
    assert s.joint_preview_key("codo") == k4                           # estable si nada cambia


# ------------------------------------------------------------- controlador
def test_controller_preview_shows_pieces_and_hides_the_figure(ctrl):
    scene = FakeScene()
    ctrl.set_joint_preview("codo", True)
    ctrl.sync(scene)                                                   # sin ventana: el cálculo es síncrono
    assert "junta:prox" in scene.items and "junta:dist" in scene.items
    assert scene.visible["figura"] is False
    assert "Hueco" in ctrl.joint_preview_status()
    before = ctrl._jp["pv"]
    ctrl.set_joint_preview_flex(45.0)                                  # solo recoloca el distal: no recalcula
    ctrl.sync(scene)
    assert ctrl._jp["pv"] is before
    ctrl.set_joint_height("codo", 2.0)                                 # un cambio real sí recalcula
    ctrl.sync(scene)
    assert ctrl._jp["pv"] is not before and ctrl._jp["pv"].h == pytest.approx(2.0)
    ctrl.set_joint_preview("codo", False)
    ctrl.sync(scene)
    assert "junta:prox" not in scene.items and scene.visible["figura"] is True


def test_controller_preview_reports_errors_instead_of_failing(ctrl):
    ctrl.session.joint("codo").axis_center = tuple(np.add(ctrl.session.joint("codo").axis_center, (0, 0, -3.5)))
    ctrl.set_joint_preview("codo", True)
    ctrl.sync(FakeScene())
    assert ctrl.joint_preview_status().startswith("No se puede previsualizar")


def test_preview_needs_a_joint(ctrl):
    ctrl.session.remove_joint("codo")
    ctrl.session.project.limbs = [l for l in ctrl.session.project.limbs if l.kind != "leg"]    # sin pierna tampoco hay cadera
    ctrl.set_joint_preview(None, True)
    assert not ctrl.show_joint_preview and "No hay ninguna articulación" in ctrl.message


def test_preview_without_joints_falls_back_to_the_hip(ctrl):
    """Sin rodilla ni codo, la vista previa ofrece la cadera de la pierna (antes no había nada que mostrar)."""
    ctrl.session.remove_joint("codo")
    assert ctrl.set_joint_preview(None, True) and ctrl.show_joint_preview
    assert ctrl.is_hip_preview(ctrl._jp["name"])


# ------------------------------------------------------ miembro que es una pieza de catálogo
@pytest.fixture(scope="module")
def arm_choice(params):
    from afm.assembly import PartChoice
    from afm.extract import extract_all

    project = demo_project()
    body = S.to_manifold(make_doll())
    ext = {e.meta.tipo: e for e in extract_all(body, project, "muneco", figure="doll").pieces}
    return PartChoice(ext["brazo"].meta, ext["brazo"].mesh)


def test_catalog_arm_in_its_own_port_previews_like_the_model_arm(arm, arm_choice, params):
    """La pieza extraída del muñeco, puesta en su propio lugar, es el mismo brazo: la vista previa da lo mismo."""
    from afm.joint_preview import prepare_limb_from_part

    limb, face = arm
    spec = next(l for l in demo_project().limbs if l.name == "brazo")
    plimb, pface = prepare_limb_from_part(S.to_manifold(make_doll()), spec, arm_choice, params)
    assert float(plimb.volume()) == pytest.approx(float(limb.volume()), rel=0.01)
    assert np.allclose(pface.center, face.center, atol=1e-3)
    a, b = preview_joint(limb, face, elbow(), params), preview_joint(plimb, pface, elbow(), params)
    assert b.carved_mm3 == pytest.approx(a.carved_mm3, rel=0.05)


def test_controller_previews_a_joint_on_a_catalog_part(ctrl, arm_choice, monkeypatch):
    """Belgrano: el brazo es una pieza de catálogo y el codo es el del proyecto; antes se negaba («usa una pieza de catálogo»)."""
    from afm.project import PartRef

    s = ctrl.session
    s.project.parts = {"brazo": PartRef(id="brazo-demo", linea="demo")}
    monkeypatch.setattr(s, "_resolved_parts", lambda: {"brazo": arm_choice})
    scene = FakeScene()
    ctrl.set_joint_preview("codo", True)
    ctrl.sync(scene)
    assert ctrl.joint_preview_status().startswith("Hueco"), ctrl.joint_preview_status()
    assert "junta:prox" in scene.items and scene.visible["figura"] is False
    k = s.joint_preview_key("codo")
    s.project.parts = {}
    assert s.joint_preview_key("codo") != k                            # cambiar la pieza invalida el cálculo


def test_preview_frames_the_camera_on_the_joint_once(ctrl):
    scene = FakeScene()
    far = ctrl.camera.distance
    ctrl.set_joint_preview("codo", True)
    ctrl.sync(scene)
    near = ctrl.camera.distance
    assert near < far / 2 and np.allclose(ctrl.camera.target, ctrl._jp["pv"].build.center, atol=15.0)
    ctrl.camera.distance = near * 1.7                                  # el usuario aleja la cámara: no se le vuelve a encuadrar
    ctrl.set_joint_height("codo", 2.0)
    ctrl.sync(scene)
    assert ctrl.camera.distance == pytest.approx(near * 1.7)


# ------------------------------------------- figura armada desde el catálogo («Armar y exportar», sin STL)
EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


def _catalog_project(base, with_elbow: bool) -> str:
    """`.proyecto` de «Armar y exportar» del muñeco de ejemplo (sin generar la figura); con `with_elbow` el brazo catalogado
    trae su codo (`meta.articulaciones`), como el brazo de Belgrano."""
    root = str(base / "ejemplo")
    shutil.copytree(EJEMPLO, root)
    if with_elbow:
        pj = os.path.join(root, "piezas", "brazo", "brazo-muneco-01@1.pieza.json")
        d = json.load(open(pj))
        d["articulaciones"] = [elbow().to_dict()]
        json.dump(d, open(pj, "w"))
    recipe = Recipe.load(os.path.join(root, "recetas", "muneco.receta.json"))
    cat, choices = load_choices(recipe, root)
    p = recipe_params(recipe, cat)
    host = synthesize_host(choices, p)
    path = str(base / "muneco.proyecto")
    CP.save(types.SimpleNamespace(recipe=recipe, catalog=cat, host=host, params=p), path)      # lo mismo que escribe `afm armar`
    return path


@pytest.fixture(scope="module")
def catalog_elbow_path(tmp_path_factory):
    return _catalog_project(tmp_path_factory.mktemp("codo_catalogo"), with_elbow=True)


@pytest.fixture()
def cctrl(catalog_elbow_path):
    c = Controller()
    c.open_project(catalog_elbow_path)
    return c


def test_catalog_project_lists_the_joint_its_piece_brings(cctrl):
    s = cctrl.session
    assert s.stl_path is None and s.catalog_only() and s.project.joints == []       # el proyecto no trae articulaciones propias
    assert [j.name for j in s.joints_listed()] == ["codo"]
    assert s.is_catalog_joint("codo") and not cctrl.joint_editable("codo")
    j = s.joint_any("codo")
    assert j.limb == "brazo" and j.kind == "elbow"
    assert np.allclose(j.axis_center, (18.5, 0.0, 62.0), atol=1e-6)                 # la pieza está en su lugar: no se movió
    assert s.joint_height("codo") == pytest.approx(3.0)
    assert cctrl.joint_plane_side_tilt("codo") == pytest.approx(0.0, abs=1e-6)


def test_catalog_project_previews_the_joint_and_hides_the_figure(cctrl):
    scene = FakeScene()
    cctrl.set_joint_preview(None, True)                  # sin nombre: la primera de la lista
    assert cctrl.show_joint_preview and "pieza de catálogo" in cctrl.message
    cctrl.sync(scene)                                    # sin ventana: el cálculo es síncrono
    status = cctrl.joint_preview_status()
    assert status.startswith("Hueco"), status
    pv = cctrl._jp["pv"]
    assert pv.carved_mm3 > 1.0 and 0.0 < pv.carved_pct < 100.0 and pv.h == pytest.approx(3.0)
    assert pv.proximal_mesh().is_watertight
    assert "junta:prox" in scene.items and "junta:dist" in scene.items and scene.visible["figura"] is False
    cctrl.set_joint_preview_flex(60.0)                   # solo recoloca el distal: no recalcula
    cctrl.sync(scene)
    assert cctrl._jp["pv"] is pv
    cctrl.set_joint_preview("codo", False)
    cctrl.sync(scene)
    assert "junta:prox" not in scene.items and scene.visible["figura"] is True


def test_catalog_joint_matches_the_same_joint_on_the_model_arm(cctrl, arm, params):
    """La pieza de catálogo del muñeco es el brazo del muñeco: el hueco que se ve es el mismo que con el brazo del modelo."""
    limb, face = arm
    expected = preview_joint(limb, face, elbow(), params)
    cctrl.set_joint_preview("codo", True)
    cctrl.sync(FakeScene())
    got = cctrl._jp["pv"]
    assert got.proximal_mm3 > 0 and got.carved_mm3 == pytest.approx(expected.carved_mm3, rel=0.15)


def test_catalog_joint_is_read_only(cctrl):
    s = cctrl.session
    key = s.joint_preview_key("codo")
    for call in (lambda: s.set_joint_height("codo", 2.0), lambda: s.set_joint_plane_side_tilt("codo", 0.0),
                 lambda: s.set_joint_range("codo", 0.0, 45.0), lambda: s.set_joint_axis("codo", None, 5.0)):
        with pytest.raises(ValueError, match="pieza de catálogo"):
            call()
    assert cctrl.set_joint_height("codo", 2.0) is False and "pieza de catálogo" in cctrl.message
    assert s.joint_preview_key("codo") == key and s.joint_height("codo") == pytest.approx(3.0)
    lo, hi = cctrl.joint_limits("codo")                  # el rango de la pieza se puede leer aunque no se pueda cambiar
    assert lo < hi


def test_preview_key_changes_with_the_pieces_and_the_params(cctrl):
    s = cctrl.session
    k = s.joint_preview_key("codo")
    assert s.joint_preview_key("codo") == k                                          # estable si nada cambia
    s.project.params = dataclasses.replace(s.project.params, clearance=s.project.params.clearance + 0.1)
    assert s.joint_preview_key("codo") != k                                          # los parámetros sí lo cambian


def test_removing_the_piece_drops_its_joint_from_the_preview(cctrl):
    s = cctrl.session
    scene = FakeScene()
    cctrl.set_joint_preview("codo", True)
    cctrl.sync(scene)
    assert "junta:prox" in scene.items
    s.project.parts.pop("brazo")                         # sin el brazo de catálogo ya no hay codo
    assert s.joints_listed() == [] and s.joint_any("codo") is None
    cctrl.sync(scene)
    assert not cctrl.show_joint_preview and "junta:prox" not in scene.items


def test_catalog_project_without_joints_in_its_pieces_says_so(tmp_path):
    c = Controller()
    c.open_project(_catalog_project(tmp_path, with_elbow=False))
    assert c.session.catalog_only() and c.session.joints_listed() == []
    c.set_joint_preview(None, True)
    assert not c.show_joint_preview and "no traen rodilla ni codo" in c.message


def test_catalog_joint_failure_is_reported_not_raised(tmp_path):
    """Un codo mal puesto en la pieza (eje bajo el plano) se muestra en el estado, como con el codo del modelo."""
    path = _catalog_project(tmp_path, with_elbow=True)
    pj = os.path.join(str(tmp_path / "ejemplo"), "piezas", "brazo", "brazo-muneco-01@1.pieza.json")
    d = json.load(open(pj))
    d["articulaciones"] = [elbow(h=0.2).to_dict()]
    json.dump(d, open(pj, "w"))
    c = Controller()
    c.open_project(path)
    c.set_joint_preview("codo", True)
    c.sync(FakeScene())
    assert c.joint_preview_status().startswith("No se puede previsualizar") and "0,5 mm" in c.joint_preview_status()


def test_model_joints_are_listed_as_before(ctrl):
    s = ctrl.session
    assert not s.catalog_only() and [j.name for j in s.joints_listed()] == ["codo"]
    assert s.joint_any("codo") is s.joint("codo") and not s.is_catalog_joint("codo") and ctrl.joint_editable("codo")
