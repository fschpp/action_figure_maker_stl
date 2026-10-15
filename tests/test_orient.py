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
