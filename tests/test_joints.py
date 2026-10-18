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


def test_validation_ok(built):
    _res, val, _j = built
    assert val["ok"], val["problems"]
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


# Con el eje de la rodilla inclinado como en la figura de referencia, las pantorrillas se tocan al sentar con flexión
# máxima (hallazgo real de la validación de cadena).
TILTED_KNEE_CALVES = "pantorrilla_izq choca con pantorrilla_der"


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
    # La validación de cadena (Fase 4) barre la flexión hasta el máximo con la cadera hasta `sit_angle`: con el eje
    # inclinado las pantorrillas se pisan a cadera 60° y flexión 90°. Es un hallazgo real (el desplazamiento hacia el
    # centro de la docstring), no un error de los demás chequeos: debe ser el único problema.
    other = [x for x in val["problems"] if TILTED_KNEE_CALVES not in x]
    assert not other, other
    assert val["cadena"]["rodilla"]["pantorrilla_der"]["mm3"] > 1.0
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


def test_bolts_not_swept_fewer_pinched_edges(built):
    """Los bulones son de revolución sobre el eje: barrerlos girando unía copias casi idénticas y dejaba aristas de
    4 caras en la pieza proximal (el slicer avisa de «non-manifold edge»). Con los bulones sin rotar, el brazo del
    muñeco baja de 11 a 2 aristas. Quedan aristas sobre la pared de la ranura (eje ± 1,56 mm) sin resolver, sobre
    todo en el muslo (≈ 19): este test fija solo lo que mejoró."""
    res, _val, _j = built
    assert P._nonmanifold_edges(res.pieces["brazo_izq"]) <= 4


def test_posed_pieces_matches_print_pose(built, params):
    res, _val, _j = built
    sit = P.posed_pieces(res, params.sit_angle)
    for nm in ("muslo_izq", "pantorrilla_izq", "brazo_izq", "antebrazo_izq", "pantorrilla_der"):
        assert nm in sit
        assert abs(sit[nm].volume() - res.pieces[nm].volume()) < 1e-3
        assert (sit[nm] ^ res.pieces[nm]).volume() > sit[nm].volume() - 1e-3      # mismo sólido (coincide)
    assert "cabeza" not in sit and "torso" not in sit


def test_chain_check_clears_knee_and_elbow(built):
    _res, val, _j = built
    chain = val["cadena"]
    assert chain["rodilla"] == {}
    assert chain["codo"] == {}                      # el barrido del antebrazo está tallado en el torso


def test_arm_sweep_carved_in_torso(built, params):
    """El brazo del muñeco queda pegado al torso: el antebrazo flexionado entraría en él, así que el motor talla el
    barrido de la flexión del codo en el torso (la rodilla no: la pierna se libra con el barrido de cadera)."""
    res, _val, joints = built
    rep = res.report["joints"]
    assert rep["codo"]["torso_tallado_mm3"] > 1.0
    assert "torso_tallado_mm3" not in rep["rodilla"]
    # el torso tallado no toca el antebrazo en ninguna flexión del rango
    jb = res.joints["codo"]
    torso = res.pieces["torso"]
    lo, hi = jb.flex
    for f in (lo, (lo + hi) / 2, hi):
        assert (jb.rotate_distal(float(f)) ^ torso).volume() <= 1e-4


# ------------------------------------------------------------------ bastago pasante (codo/rodilla)
@pytest.fixture(scope="module")
def rod_vs_bolts(params):
    """Codo del muñeco con bastago pasante (por defecto) y con los dos bulones de antes."""
    body = S.to_manifold(make_doll())
    proj = demo_project()
    out = {}
    for rod in (True, False):
        h = HingeSpec(pin_d=2.0, pin_len=0.6, tongue_t=2.0, end_r=3.0, rod=rod, rod_wall=1.2)
        joints = [elbow(hinge=h)]
        res = P.build(body, [l for l in proj.limbs if l.kind == "arm"], params, proj.symmetry, head=proj.head, joints=joints)
        out[rod] = (res, P.validate(res, params, [l for l in proj.limbs if l.kind == "arm"], joints=joints))
    return out


def test_rod_is_one_piece_with_the_forearm_and_passes_through_the_arm(rod_vs_bolts):
    (res, val), (res_b, _vb) = rod_vs_bolts[True], rod_vs_bolts[False]
    jb, jb_b = res.joints["codo"], res_b.joints["codo"]
    assert val["ok"] if "ok" in val else not val["problems"], val["problems"]
    assert S.to_trimesh(res.pieces["antebrazo_izq"]).body_count == 1               # el bastago está soldado: una sola pieza
    # el brazo del muñeco es fino (pegado al torso): el bastago queda corto, como mucho igual que los bulones, y avisa
    assert min(jb.tips) >= min(jb_b.tips) - 1e-9
    assert any("bastago queda corto" in w for w in jb.warnings)
    for tip in jb.tips:
        assert tip > 2.0 / 2 + 0.5                                                   # sale de la lengüeta
    # el orificio del brazo es de lado a lado menos la pared: la pared medida ≈ rod_wall
    assert val["articulaciones"]["codo"]["pared_mm"] == pytest.approx(1.2, abs=0.35)


def test_rod_is_retained_and_free_in_range(rod_vs_bolts):
    _res, val = rod_vs_bolts[True]
    rec = val["articulaciones"]["codo"]
    assert rec["recorrido_mm3"] <= 1e-4 and rec["retencion_libre"] == []
    assert rec["holgura_mm"] >= 0.9 * min(0.5, 0.35)


def test_rod_wall_zero_gives_an_open_through_hole(params):
    body = S.to_manifold(make_doll())
    proj = demo_project()
    arm = [l for l in proj.limbs if l.kind == "arm"]
    closed = HingeSpec(pin_d=2.0, pin_len=0.6, tongue_t=2.0, end_r=3.0, rod_wall=1.2)
    opened = dataclasses.replace(closed, rod_wall=0.0)
    r = {}
    for k, h in (("closed", closed), ("open", opened)):
        r[k] = P.build(body, arm, params, proj.symmetry, head=proj.head, joints=[elbow(hinge=h)]).joints["codo"]
    assert sum(r["open"].tips) > sum(r["closed"].tips) + 1.0


def test_hinge_rod_fields_roundtrip_and_old_projects_load():
    h = HingeSpec(rod=False, rod_wall=0.6)
    assert HingeSpec(**vars(h).copy()) == h
    old = {"pin_d": 2.4, "pin_len": 1.2, "tongue_t": 2.4, "end_r": 3.6}      # proyecto guardado antes del bastago
    assert HingeSpec(**old).rod is True and HingeSpec(**old).rod_wall == 1.2


def test_rod_extent_stops_where_the_arm_is_too_thin():
    from afm.joints import rod_extent
    block = S.Manifold.cube((20.0, 10.0, 10.0), center=True)          # x ∈ [−10, 10]; el disco de radio 2,5 cabe en todo
    ext = rod_extent(block, (0, 0, 0), (1, 0, 0), 2.5)
    assert ext == pytest.approx((10.0, 10.0), abs=0.3)
    ext = rod_extent(block, (4.0, 0, 0), (1, 0, 0), 2.5)
    assert ext == pytest.approx((14.0, 6.0), abs=0.3)
    thin = S.union(block, S.Manifold.cube((10.0, 3.0, 3.0), center=True).translate((15.0, 0, 0)))   # saliente de 3 × 3: no cabe
    assert rod_extent(thin, (0, 0, 0), (1, 0, 0), 2.5)[1] == pytest.approx(10.0, abs=0.3)
    assert rod_extent(block, (0, 0, 0), (1, 0, 0), 2.5, start=11.0) is None            # empezar fuera del material


def test_neck_makes_the_tongue_a_u_shape_wide_where_it_meets_the_body(params):
    """Sin cuello, la lengüeta es un disco que toca el cuerpo en una cuerda corta; con cuello llega con todo el ancho."""
    body = S.to_manifold(make_doll())
    proj = demo_project()
    arm = [l for l in proj.limbs if l.kind == "arm"]
    width = {}
    for neck in (False, True):
        h = HingeSpec(pin_d=2.0, pin_len=0.6, tongue_t=2.0, end_r=3.0, neck=neck)
        jb = P.build(body, arm, params, proj.symmetry, head=proj.head, joints=[elbow(hinge=h)]).joints["codo"]
        tri = S.to_trimesh(jb.distal)
        sec = tri.section(plane_origin=jb.center - np.array([0, 0, 0.5 * (jb.shoulder if hasattr(jb, "shoulder") else 3.0)]),
                          plane_normal=(1, 0, 0))
        pts = sec.vertices
        near = pts[np.abs(pts[:, 2] - (jb.center[2] - 1.5)) < 0.05]          # 1,5 mm bajo el eje, dentro del cuello
        width[neck] = np.ptp(near[:, 1]) if len(near) else 0.0
    assert width[True] > 2 * 3.0 * 0.95                                       # ancho completo de la lengüeta (2·radio)
    assert width[True] > width[False] + 0.3
