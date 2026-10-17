"""Rodilla y codo: bisagra de lengüeta en ranura con dos bulones (plan de articulaciones §3–§5)."""
import dataclasses

import numpy as np

import pytest

from afm import pipeline as P, solids as S
from afm.joints import JointSpec, build_joint, flex_range
from afm.params import HingeSpec, Params
from afm.planes import Plane
from afm.project import Project, demo_project
from afm.testfigure import make_doll


def knee(**kw):
    # pierna del muñeco: cilindro Ø14 centrado en x = 9; eje a 3,4 mm sobre el plano de articulación
    d = dict(name="rodilla", limb="pierna", kind="knee", plane=Plane.from_normal((0, 0, 14.0), (0, 0, 1), "art"),
             axis_center=(9.0, 0.0, 17.4))
    d.update(kw)
    return JointSpec(**d)


def elbow(**kw):
    d = dict(name="codo", limb="brazo", kind="elbow", plane=Plane.from_normal((0, 0, 59.0), (0, 0, 1), "art"),
             axis_center=(18.5, 0.0, 62.0), hinge=HingeSpec(pin_d=2.0, pin_len=0.6, tongue_t=2.0, end_r=3.0))
    d.update(kw)
    return JointSpec(**d)


@pytest.fixture(scope="module")
def params():
    return dataclasses.replace(demo_project().params, decimate=0)


@pytest.fixture(scope="module")
def built(params):
    body = S.to_manifold(make_doll())
    proj = demo_project()
    joints = [knee(), elbow()]
    res = P.build(body, proj.limbs, params, proj.symmetry, head=proj.head, joints=joints)
    return res, P.validate(res, params, proj.limbs, joints=joints), joints


def test_pieces_split_and_mirrored(built):
    res, _val, _j = built
    for n in ("muslo", "pantorrilla", "brazo", "antebrazo"):
        assert f"{n}_izq" in res.pieces and f"{n}_der" in res.pieces
    assert "pierna_izq" not in res.pieces and "brazo_izq" in res.pieces      # «brazo» es ahora la pieza proximal
    assert set(res.joints) == {"rodilla", "codo"}


# El brazo del muñeco queda pegado al torso (x 11,5–22 dentro del torso, que llega a x = 23) y el motor no talla un
# barrido de brazo en el torso (limitación conocida), así que el antebrazo choca al flexionar el codo: es un hallazgo
# real de la validación de cadena, no de los demás chequeos.
DOLL_ELBOW_VS_TORSO = "antebrazo_izq choca con torso"


def test_validation_ok(built):
    _res, val, _j = built
    other = [x for x in val["problems"] if DOLL_ELBOW_VS_TORSO not in x]
    assert not other, other
    for rec in val["articulaciones"].values():
        assert rec["recorrido_mm3"] <= 1e-4
        assert rec["tope_flexion_mm3"] > 1e-4 and rec["tope_extension_mm3"] > 1e-4
        assert rec["retencion_libre"] == []
        assert rec["holgura_mm"] >= 0.9 * 0.35


def test_every_piece_watertight_and_torso_single(built):
    _res, val, _j = built
    for name, info in val["pieces"].items():
        assert info["variedad_ok"], name
        assert info["componentes"] == 1, name


def test_knee_free_in_range_and_stops_outside(params):
    body = S.to_manifold(make_doll())
    proj = demo_project()
    res = P.build(body, proj.limbs, params, proj.symmetry, joints=[knee()])
    jb = res.joints["rodilla"]
    assert jb.flex == (0.0, 90.0)
    for f in (0, 30, 60, 90):
        assert (jb.rotate_distal(f) ^ jb.proximal).volume() <= 1e-4
    assert (jb.rotate_distal(100) ^ jb.proximal).volume() > 1e-3      # tope de flexión
    assert (jb.rotate_distal(-10) ^ jb.proximal).volume() > 1e-4      # sin hiperextensión


def test_knee_flexes_backward_elbow_forward(params):
    """Rodilla: la pantorrilla va hacia atrás (−Y); codo: el antebrazo hacia adelante (+Y)."""
    body = S.to_manifold(make_doll())
    proj = demo_project()
    res = P.build(body, proj.limbs, params, proj.symmetry, joints=[knee(), elbow()])
    kn, el = res.joints["rodilla"], res.joints["codo"]
    ky = lambda mf: mf.bounding_box()
    assert ky(kn.rotate_distal(60))[1] < ky(kn.distal)[1] - 3          # y mínimo baja
    assert ky(el.rotate_distal(60))[4] > ky(el.distal)[4] + 3          # y máximo sube


def test_print_pose_knee_bent_85(built, params):
    """En la pose impresa la pantorrilla está flexionada `knee_print_angle` respecto de la de pie y sentada."""
    res, _v, _j = built
    jb = res.joints["rodilla"]
    zmin = lambda mf: mf.bounding_box()[2]
    assert zmin(res.pieces["pantorrilla_izq"]) < zmin(res.pieces["muslo_izq"]) - 5     # sentada: la pantorrilla cuelga
    assert res.report["joints"]["rodilla"]["print_flex"] == params.knee_print_angle


def test_hyperextension_widens_range(params):
    p = dataclasses.replace(params, elbow_hyperext=10.0)
    assert flex_range(elbow(), p) == (-10.0, 90.0)
    assert flex_range(knee(), p) == (0.0, 90.0)
    assert flex_range(knee(flex_range=(-5, 80)), p) == (-5.0, 80.0)


def test_axis_below_plane_rejected(params):
    body = S.to_manifold(make_doll())
    proj = demo_project()
    with pytest.raises(ValueError, match="lado proximal"):
        P.build(body, proj.limbs, params, proj.symmetry, joints=[knee(axis_center=(9.0, 0.0, 13.0))])


def test_one_joint_per_limb_and_unknown_limb(params):
    body = S.to_manifold(make_doll())
    proj = demo_project()
    with pytest.raises(ValueError, match="una sola articulación"):
        P.build(body, proj.limbs, params, proj.symmetry, joints=[knee(), knee(name="otra")])
    with pytest.raises(ValueError, match="no existe"):
        P.build(body, proj.limbs, params, proj.symmetry, joints=[knee(limb="cola")])


def test_thin_shoulder_warns(params):
    body = S.to_manifold(make_doll())
    proj = demo_project()
    res = P.build(body, proj.limbs, params, proj.symmetry, joints=[knee(axis_center=(9.0, 0.0, 15.5))])
    assert any("poca pared" in w for w in res.joints["rodilla"].warnings)


def test_project_roundtrip_and_old_projects(tmp_path):
    proj = demo_project()
    proj.joints = [knee(), elbow(flex_range=(-10, 100), flex_sign=-1)]
    proj.params.knee_range = (0.0, 85.0)
    f = tmp_path / "x.proyecto"
    proj.save(str(f))
    q = Project.load(str(f))
    assert [j.name for j in q.joints] == ["rodilla", "codo"]
    assert q.joints[1].flex_range == (-10, 100) and q.joints[1].sign() == -1
    assert q.joints[0].hinge is None and q.joints[1].hinge.pin_d == 2.0
    assert q.params.knee_range == (0.0, 85.0) and q.params.hinge_pin == proj.params.hinge_pin
    d = proj.to_dict()
    d.pop("joints")
    d["params"] = {k: v for k, v in d["params"].items() if k not in ("hinge_pin", "knee_range")}
    assert Project.from_dict(d).joints == []                          # un .proyecto viejo carga igual


def test_tilted_hinge_axis_like_reference_knee(params):
    """Eje inclinado sobre Y como el de la figura de referencia (rodilla ≈ 14,5°, intencional): mismo comportamiento.

    Con el eje inclinado el miembro flexionado se desplaza hacia el centro (≈ L·sen α·cos α·(1 − cos θ)): con las
    piernas del muñeco, muy juntas, la pose sentada a 85° hace chocar las pantorrillas izquierda y derecha, y la
    validación lo informa; con una flexión de impresión de 40° (≈ 1 mm de desplazamiento) todo queda en orden."""
    a = np.radians(14.5)
    kn = knee(axis_dir=(np.cos(a), 0.0, np.sin(a)))
    body = S.to_manifold(make_doll())
    proj = demo_project()
    p40 = dataclasses.replace(params, knee_print_angle=40.0)
    res = P.build(body, proj.limbs, p40, proj.symmetry, joints=[kn])
    val = P.validate(res, p40, proj.limbs, joints=[kn])
    assert val["ok"], val["problems"]
    rec = val["articulaciones"]["rodilla"]
    assert rec["tope_flexion_mm3"] > 1e-4 and rec["tope_extension_mm3"] > 1e-4
    assert rec["retencion_libre"] == []
    jb = res.joints["rodilla"]
    assert jb.axis[2] == pytest.approx(np.sin(a), abs=1e-6)
    ymin = lambda mf: mf.bounding_box()[1]
    assert ymin(jb.rotate_distal(60)) < ymin(jb.distal) - 3          # sigue flexionando hacia atrás (−Y)
    # a 85° (valor por defecto) las pantorrillas de las dos piernas se cruzan en el muñeco
    res85 = P.build(body, proj.limbs, params, proj.symmetry, joints=[kn])
    assert any("pantorrilla_izq y pantorrilla_der se interpenetran" in x
               for x in P.validate(res85, params, proj.limbs, joints=[kn])["problems"])


def test_wall_around_bolt_measured_and_flagged(built, params):
    res, val, joints = built
    limbs = demo_project().limbs
    for name, rec in val["articulaciones"].items():
        assert rec["pared_mm"] is not None and rec["pared_mm"] >= 2 * params.nozzle, (name, rec)
    thick = dataclasses.replace(params, min_wall=500.0)             # imposible: solo avisa (se imprime igual)
    v2 = P.validate(res, thick, limbs, joints=joints)
    assert any("pared del cuerpo proximal" in x for x in v2["avisos"])
    coarse = dataclasses.replace(params, nozzle=500.0)              # 2 líneas imposibles: bloquea
    v3 = P.validate(res, coarse, limbs, joints=joints)
    assert any("no se imprime" in x for x in v3["problems"])


def test_posed_pieces_matches_print_pose(built, params):
    res, _val, _j = built
    sit = P.posed_pieces(res, params.sit_angle)
    for nm in ("muslo_izq", "pantorrilla_izq", "brazo_izq", "antebrazo_izq", "pantorrilla_der"):
        assert nm in sit
        assert abs(sit[nm].volume() - res.pieces[nm].volume()) < 1e-3
        assert (sit[nm] ^ res.pieces[nm]).volume() > sit[nm].volume() - 1e-3      # mismo sólido (coincide)
    assert "cabeza" not in sit and "torso" not in sit


def test_chain_check_finds_elbow_against_torso_and_clears_knee(built):
    _res, val, _j = built
    chain = val["cadena"]
    assert chain["rodilla"] == {}
    assert "torso" in chain["codo"] and chain["codo"]["torso"]["mm3"] > 1.0
    assert any(DOLL_ELBOW_VS_TORSO in x for x in val["problems"])
