"""E02: carga del caballo (escala, guiño, hash, malla abierta, caballo real de ejemplo)."""
import numpy as np
import pytest
import trimesh

from afm.equestrian import HorseError, HorseSpec, load_horse_mesh, sha256_file
from pathlib import Path

REAL = Path(__file__).resolve().parents[1] / "examples" / "referencia" / "caballo" / "horse_baseless.stl"


def write_box(tmp_path, extents=(2.0, 4.0, 6.0), center=(0, 0, 0), name="caja.stl"):
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(center)
    p = tmp_path / name
    m.export(p)
    return p


def spec_for(p, **kw):
    return HorseSpec(archivo=p.name, sha256=sha256_file(str(p)), **kw)


def test_scale_preserves_proportions(tmp_path):
    p = write_box(tmp_path)
    h = load_horse_mesh(spec_for(p, escala=3.0), str(tmp_path))
    assert np.allclose(h.report.extents, (6, 12, 18), atol=1e-4)
    assert h.report.volume == pytest.approx(48 * 27, rel=1e-4)
    assert h.report.triangles == 12 and h.report.components == 1 and not h.report.repaired


def test_yaw_180_flips_y(tmp_path):
    p = write_box(tmp_path, center=(0, 10, 0))
    h = load_horse_mesh(spec_for(p, yaw=180.0), str(tmp_path))
    assert h.mesh.bounds.mean(axis=0)[1] == pytest.approx(-10, abs=1e-4)
    h90 = load_horse_mesh(spec_for(p, yaw=90.0), str(tmp_path))
    assert np.allclose(h90.report.extents, (4, 2, 6), atol=1e-4)       # X e Y intercambiados


def test_hash_mismatch_missing_and_empty(tmp_path):
    p = write_box(tmp_path)
    bad = HorseSpec(archivo=p.name, sha256="0" * 64)
    with pytest.raises(HorseError, match="SHA-256"):
        load_horse_mesh(bad, str(tmp_path))
    assert load_horse_mesh(bad, str(tmp_path), check_hash=False).report.triangles == 12
    with pytest.raises(HorseError, match="no existe"):
        load_horse_mesh(HorseSpec(archivo="nada.stl", sha256="0" * 64), str(tmp_path))
    e = tmp_path / "vacio.stl"
    e.write_bytes(b"")
    with pytest.raises(HorseError):
        load_horse_mesh(spec_for(e), str(tmp_path))


def test_open_mesh_fails_without_repair_and_is_flagged_with_it(tmp_path):
    m = trimesh.creation.box(extents=(2, 2, 2))
    m.update_faces(np.arange(len(m.faces)) != 0)                       # un triángulo menos: queda abierto
    p = tmp_path / "abierta.stl"
    m.export(p)
    with pytest.raises(HorseError, match="estanca"):
        load_horse_mesh(spec_for(p), str(tmp_path), repair=False)
    try:
        h = load_horse_mesh(spec_for(p), str(tmp_path))
    except HorseError as exc:                                          # reparar puede fallar: debe dar un motivo
        assert "reparar" in str(exc)
    else:
        assert h.report.repaired and h.report.warnings


def test_two_shells_are_reported(tmp_path):
    a = trimesh.creation.box(extents=(1, 1, 1))
    b = trimesh.creation.box(extents=(1, 1, 1))
    b.apply_translation((5, 0, 0))
    p = tmp_path / "dos.stl"
    trimesh.util.concatenate([a, b]).export(p)
    h = load_horse_mesh(spec_for(p), str(tmp_path))
    assert h.report.components == 2 and h.report.warnings


@pytest.mark.skipif(not REAL.exists(), reason="falta el STL de ejemplo")
def test_real_horse_at_3x_with_180_yaw():
    spec = HorseSpec(archivo=REAL.name, sha256=sha256_file(str(REAL)), escala=3.0, yaw=180.0)
    h = load_horse_mesh(spec, str(REAL.parent))
    r = h.report
    assert r.triangles == 64324 and r.components == 1 and not r.repaired
    assert np.allclose(r.extents, (41.8, 152.4, 132.7), atol=0.3)      # ≈152 × 42 × 133 mm (plan §1.4)
    assert r.volume == pytest.approx(6499.9 * 27, rel=0.01)
    ys = h.mesh.vertices[:, 1]
    top = h.mesh.vertices[h.mesh.vertices[:, 2] > h.mesh.bounds[1][2] - 3]
    assert top[:, 1].mean() > ys.mean()                                # con yaw 180° la cabeza queda hacia +Y
    assert h.light.num_tri() < r.triangles and h.light.num_tri() > 5000
