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
