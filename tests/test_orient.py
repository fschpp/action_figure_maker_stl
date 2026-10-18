import trimesh
from afm import orient


def test_box_is_flat_on_bed():
    m = trimesh.creation.box((10, 10, 10))
    r = orient.best_orientation(m)
    assert r.overhang_area == 0 and r.base_area > 99


def test_cone_prefers_point_up():
    m = trimesh.creation.cone(radius=10, height=20)   # base en z=0, punta arriba
    m.apply_transform(trimesh.transformations.rotation_matrix(3.14159, (1, 0, 0)))  # punta abajo
    r = orient.best_orientation(m)
    assert r.base_area > 300          # apoya sobre la base circular (~314 mm²)


def test_export_with_orientation(tmp_path):
    """Con orient=True todas las piezas comparten la transformación y quedan apoyadas en z = 0."""
    import trimesh
    from afm import cli
    assert cli.main(["demo", "--out", str(tmp_path), "--orient"]) == 0
    import json
    info = json.load(open(tmp_path / "muneco_informe.json"))
    ori = info["archivos"]["orientacion"]
    assert ori["base_mm2"] >= 0 and ori["voladizo_mm2"] >= 0
    asm = trimesh.load(tmp_path / "muneco_ensamblada.stl")
    assert abs(asm.bounds[0][2]) < 1e-3
    for f in (tmp_path / "piezas").iterdir():
        assert trimesh.load(f).bounds[0][2] >= -1e-3      # ninguna pieza queda bajo la cama


def _t_shape():
    post = trimesh.creation.box((4, 4, 20))
    post.apply_translation((0, 0, 10))
    top = trimesh.creation.box((30, 30, 4))
    top.apply_translation((0, 0, 22))
    return trimesh.boolean.union([post, top], engine="manifold")


def _cone_tip_down(radius, height):
    import numpy as np
    c = trimesh.creation.cone(radius=radius, height=height)
    c.apply_transform(trimesh.transformations.rotation_matrix(np.pi, (1, 0, 0)))
    c.apply_translation((0, 0, -c.bounds[0][2]))
    return c


def test_horizontal_ceiling_is_overhang():
    """Regresión: el techo horizontal es el peor voladizo (antes contaba 0 mm²)."""
    import numpy as np
    m = _t_shape()
    area = orient.overhang_area(m)
    ring = 30 * 30 - 4 * 4                      # cara inferior del techo, descontado el poste
    assert abs(area - ring) < 1.0
    mask = orient.overhang_mask(m)
    assert mask.sum() > 0 and np.all(m.face_normals[mask][:, 2] < -0.99)


def test_gentle_slope_is_not_overhang_but_steep_is():
    gentle = _cone_tip_down(10, 20)             # pared a 26,6° de la vertical: imprimible
    assert orient.overhang_area(gentle) == 0
    steep = _cone_tip_down(35, 20)              # pared a 60°: necesita soporte
    import numpy as np
    lateral = np.pi * 35 * np.hypot(35, 20)     # el cono es un polígono de 32 lados: ~0,5 % menos
    assert abs(orient.overhang_area(steep) - lateral) < 0.02 * lateral


def test_max_angle_parameter():
    gentle = _cone_tip_down(10, 20)             # 26,6° de la vertical
    assert orient.overhang_area(gentle, max_angle=20.0) > 600
    assert orient.overhang_area(gentle, max_angle=30.0) == 0


def test_best_orientation_avoids_ceiling():
    """La T se imprime con el techo hacia arriba (o de costado), no colgando sobre el poste."""
    m = _t_shape()
    m.apply_transform(trimesh.transformations.rotation_matrix(3.14159265, (1, 0, 0)))   # T al revés
    r = orient.best_orientation(m)
    assert r.overhang_area < 1.0


def test_pose_orientation_never_flips_the_figure():
    """`pose_orientation` solo baja la figura a la cama: aunque cabeza abajo tendría menos voladizo, no la da vuelta."""
    import numpy as np
    post = trimesh.creation.box((4, 4, 60))
    post.apply_translation((0, 0, 30))
    top = trimesh.creation.box((40, 40, 10))
    top.apply_translation((0, 0, 65))
    t = trimesh.util.concatenate([post, top])             # «T»: el techo ancho arriba es todo voladizo
    r = orient.pose_orientation(t, 45.0)
    assert np.allclose(r.rotation[:3, :3], np.eye(3)) and r.rotation[2, 3] == -t.bounds[0][2]
    assert r.overhang_area > 1000                         # el techo de la «T» (≈ 1600 − 16 mm²) sigue ahí
    best = orient.best_orientation(t, 45.0)
    assert not np.allclose(best.rotation[:3, :3], np.eye(3)) and best.overhang_area < r.overhang_area   # lo da vuelta


def test_overhang_mask_zmin_is_the_shared_bed():
    box = trimesh.creation.box((10, 10, 10))
    box.apply_translation((0, 0, 5 + 7.0))                 # flota 7 mm sobre la cama del conjunto
    assert orient.overhang_mask(box, 45.0).sum() == 0      # sola, su base cuenta como apoyo
    assert orient.overhang_mask(box, 45.0, zmin=0.0).sum() == 2   # con la cama más abajo, su fondo (2 triángulos) es voladizo


def test_export_default_keeps_demo_head_up_and_auto_still_available(tmp_path):
    import json
    from afm import cli
    assert cli.main(["demo", "--out", str(tmp_path / "a"), "--orient"]) == 0
    info = json.load(open(tmp_path / "a" / "muneco_informe.json"))
    assert info["archivos"]["orientacion"]["etiqueta"] == "pose de cadera, cabeza arriba"
    assert cli.main(["demo", "--out", str(tmp_path / "b"), "--orient-auto"]) == 0
    info = json.load(open(tmp_path / "b" / "muneco_informe.json"))
    assert info["archivos"]["orientacion"]["etiqueta"].endswith("arriba") and "pose" not in info["archivos"]["orientacion"]["etiqueta"]
