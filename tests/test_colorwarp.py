"""Ajuste no rígido del FBX a una pieza que es otra malla (`colorwarp`, cabeza de reemplazo con su propio FBX).

Sobre la cabeza del catálogo de ejemplo y un FBX sintético: la misma cabeza deformada con una onda suave de 0,8 mm (lo que un calce rígido no
puede resolver) y puesta en otro marco. Sin figuras reales ni GTK.
"""
import os

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from afm import colorwarp as cw
from afm import parts as P
from afm.catalog import Catalog
from afm.colorroles import RolesPackage

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
HEAD_ID = "cabeza-muneco-01"
WAVE_MM = 0.8


def fbx_frame():
    """Escala ×1,3, giro de 30° sobre Z y traslado: el FBX no está en el marco de la pieza (FBX = M · pieza)."""
    M = np.eye(4)
    M[:3, :3] = 1.3 * Rotation.from_euler("z", 30, degrees=True).as_matrix()
    M[:3, 3] = [7.0, -4.0, 11.0]
    return M


def wavy(mesh, amp=WAVE_MM):
    """Los vértices de `mesh` desplazados `amp` mm a lo largo de su normal con una onda suave (no se arruga: es de baja frecuencia)."""
    V = np.asarray(mesh.vertices, float)
    n = np.asarray(mesh.vertex_normals, float)
    span = np.ptp(V, axis=0).max()
    k = 2 * np.pi / (span * 0.7)
    return V + (amp * np.sin(k * V[:, 0] + 0.5) * np.cos(k * V[:, 2]))[:, None] * n


def package_for(mesh, deform: float):
    """(paquete de roles, A) del FBX sintético: la pieza `mesh` deformada `deform` mm y en el marco `fbx_frame()`; A = FBX → marco de la pieza."""
    M = fbx_frame()
    V = wavy(mesh, deform) if deform else np.asarray(mesh.vertices, float)
    Vf = V @ M[:3, :3].T + M[:3, 3]
    F = np.asarray(mesh.faces, np.int32)
    labels = np.zeros((4, 4), np.uint8)
    labels[2:] = 1
    pkg = RolesPackage(Vf.astype(np.float32), F, np.zeros((len(F), 3, 2), np.float32), labels, ["rojo", "azul"], {"nombre": "x.fbx"})
    return pkg, np.linalg.inv(M)


@pytest.fixture(scope="module")
def head():
    return Catalog.open(ROOT).load(HEAD_ID)[1]


def _in_piece_frame(pkg, A):
    return np.asarray(pkg.vertices, float) @ A[:3, :3].T + A[:3, 3]


def test_warp_package_closes_the_gap_a_rigid_fit_cannot(head):
    pkg, A = package_for(head, WAVE_MM)
    before = cw.coverage(head.vertices, head.faces, _in_piece_frame(pkg, A), pkg.faces)
    out, rep = cw.warp_package(pkg, A, head)
    after = cw.coverage(head.vertices, head.faces, _in_piece_frame(out, A), out.faces)
    assert rep["aplicada"] and out is not pkg
    assert after > before + 0.15 and after > 0.7, (before, after)
    assert rep["cobertura_antes"] == pytest.approx(before, abs=1e-3) and rep["cobertura_despues"] == pytest.approx(after, abs=1e-3)
    assert rep["desplazamiento_max_mm"] <= 1.5 * rep["reach_mm"] + 1e-6


def test_warp_package_keeps_everything_but_the_vertices(head):
    pkg, A = package_for(head, WAVE_MM)
    out, _ = cw.warp_package(pkg, A, head)
    assert out.vertices.dtype == np.float32 and out.vertices.shape == pkg.vertices.shape
    assert not np.allclose(out.vertices, pkg.vertices)
    assert np.array_equal(out.faces, pkg.faces) and np.array_equal(out.corner_uv, pkg.corner_uv) and np.array_equal(out.labels, pkg.labels)
    assert out.roles == pkg.roles and out.origen == pkg.origen and not out.problems()


def test_identical_mesh_is_left_alone(head):
    pkg, A = package_for(head, 0.0)
    out, rep = cw.warp_package(pkg, A, head)
    assert out is pkg and rep["aplicada"] is False and rep["cobertura_antes"] > 0.9


def test_warp_survives_a_far_away_fbx(head):
    """Un FBX que no toca la pieza no se mueve ni rompe: cobertura 0, sin ancla, paquete tal cual."""
    pkg, A = package_for(head, 0.0)
    far = RolesPackage((np.asarray(pkg.vertices, float) + 500.0).astype(np.float32), pkg.faces, pkg.corner_uv, pkg.labels, pkg.roles, {})
    out, rep = cw.warp_package(far, A, head)
    assert out is far and rep["aplicada"] is False and rep["cobertura_antes"] == 0.0


def _meta_with(align, A):
    meta = Catalog.open(ROOT).meta(HEAD_ID)
    meta = P.PieceMeta.from_dict(meta.to_dict())
    meta.color = {"modo": "roles", "similitud": np.asarray(A).tolist(), "alineacion": dict(align)}
    return meta


def test_catalog_fits_the_package_when_the_alignment_was_measured_and_loose(head):
    cat = Catalog.open(ROOT)
    pkg, A = package_for(head, WAVE_MM)
    meta, warns = _meta_with({"media_mm": 0.3, "ok": True, "recortada": True}, A), []
    out = cat._fit_roles_package(meta, head, pkg, warns)
    assert out is not pkg and not np.allclose(out.vertices, pkg.vertices)
    nr = meta.color["alineacion"]["no_rigida"]
    assert nr["aplicada"] is True and nr["cobertura_despues"] > nr["cobertura_antes"]
    assert len(warns) == 1 and "se ajustó a la pieza" in warns[0]
    assert meta.color["similitud"] == np.asarray(A).tolist()                       # la similitud no cambia: el ajuste vive en los vértices del paquete


@pytest.mark.parametrize("align", [{"media_mm": 0.01, "ok": True}, {"origen": "figura"}, {}])
def test_catalog_leaves_the_package_when_the_fbx_is_the_same_mesh_or_the_alignment_was_given(head, align):
    cat = Catalog.open(ROOT)
    pkg, A = package_for(head, WAVE_MM)
    meta, warns = _meta_with(align, A), []
    out = cat._fit_roles_package(meta, head, pkg, warns)
    assert out is pkg and warns == [] and "no_rigida" not in meta.color["alineacion"]


def test_catalog_reports_when_the_fit_does_not_help(head):
    cat = Catalog.open(ROOT)
    pkg, A = package_for(head, 0.0)
    meta, warns = _meta_with({"media_mm": 0.3, "ok": True}, A), []
    out = cat._fit_roles_package(meta, head, pkg, warns)
    assert out is pkg and meta.color["alineacion"]["no_rigida"]["aplicada"] is False
    assert len(warns) == 1 and "no mejoró" in warns[0]


# --- O07: compuerta del ajuste no rígido (una pose mala no se «arregla» deformando el FBX) ---

@pytest.fixture(scope="module")
def egg():
    """Cabeza sintética SIN simetría de revolución (frente largo hacia +Y, nuca corta): la de ejemplo del catálogo es de revolución y girarla
    180° no cambia nada, así que no sirve para probar una pose al revés."""
    import trimesh

    m = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
    V = np.asarray(m.vertices, float).copy()
    V[:, 1] *= np.where(V[:, 1] > 0, 1.5, 0.8)
    V[:, 2] *= 1.2
    return trimesh.Trimesh(V, m.faces, process=False)


def _flipped(A, mesh):
    """`A` con un giro de 180° sobre el eje vertical que pasa por el centro de `mesh` (la cara del FBX queda hacia atrás)."""
    c = np.asarray(mesh.vertices, float).mean(axis=0)
    Rz = np.eye(4)
    Rz[:3, :3] = Rotation.from_euler("z", 180, degrees=True).as_matrix()
    Rz[:3, 3] = c - Rz[:3, :3] @ c
    return Rz @ A


def test_gate_refuses_to_warp_a_pose_that_is_turned_around(egg):
    pkg, A = package_for(egg, 0.3)
    out, rep = cw.warp_package(pkg, A, egg)
    assert rep["aplicada"] is not None and "pose_sospechosa" not in rep           # la pose buena pasa la compuerta
    out, rep = cw.warp_package(pkg, _flipped(A, egg), egg)
    assert out is pkg and rep["aplicada"] is False and rep["pose_sospechosa"] is True and rep["motivo"]
    assert rep["cobertura_antes"] < cw.GATE_MIN_COVER


def test_gate_reasons_cover_each_threshold():
    ok = {"desplazamiento_medio_mm": 0.1, "desplazamiento_max_mm": 0.5}
    assert cw._gate_reasons(0.9, ok, edge=1.0, diag=40.0) == []
    assert "cobertura previa" in cw._gate_reasons(0.3, {}, 1.0, 40.0)[0]
    assert "medio" in cw._gate_reasons(0.9, {**ok, "desplazamiento_medio_mm": 0.6}, 1.0, 40.0)[0]
    assert "máximo" in cw._gate_reasons(0.9, {**ok, "desplazamiento_max_mm": 6.5}, 1.0, 40.0)[0]
    assert len(cw._gate_reasons(0.3, {"desplazamiento_medio_mm": 2.0, "desplazamiento_max_mm": 9.0}, 1.0, 40.0)) == 3


def test_gate_refuses_a_displacement_larger_than_half_an_edge(egg, monkeypatch):
    """Cobertura previa alta pero un ajuste que tendría que mover > media arista (0,42 mm aquí): no se aplica."""
    pkg, A = package_for(egg, 0.0)
    real = cw.warp_fbx_to_piece

    def big(V, F, pV, pF, **kw):
        V2, rep = real(V, F, pV, pF, **kw)
        return V2, {**rep, "desplazamiento_medio_mm": 1.0, "desplazamiento_max_mm": 2.0}

    monkeypatch.setattr(cw, "warp_fbx_to_piece", big)
    out, rep = cw.warp_package(pkg, A, egg)
    assert out is pkg and rep["aplicada"] is False and rep["pose_sospechosa"] is True and "desplazamiento medio" in rep["motivo"]
    assert rep["cobertura_antes"] > cw.GATE_MIN_COVER and "cobertura_despues" not in rep


def test_gate_off_keeps_the_old_behavior(egg):
    pkg, A = package_for(egg, 0.3)
    _, rep = cw.warp_package(pkg, _flipped(A, egg), egg, gate=False)
    assert "pose_sospechosa" not in rep and "cobertura_despues" in rep


def test_a_good_pose_passes_the_gate(head):
    pkg, A = package_for(head, WAVE_MM)
    out, rep = cw.warp_package(pkg, A, head)
    assert rep["aplicada"] is True and "pose_sospechosa" not in rep and out is not pkg


def test_catalog_warns_and_keeps_the_package_when_the_pose_is_suspicious(egg):
    cat = Catalog.open(ROOT)
    pkg, A = package_for(egg, 0.3)
    meta, warns = _meta_with({"media_mm": 0.3, "ok": True, "recortada": True}, _flipped(A, egg)), []
    out = cat._fit_roles_package(meta, egg, pkg, warns)
    nr = meta.color["alineacion"]["no_rigida"]
    assert out is pkg and nr["pose_sospechosa"] is True and nr["aplicada"] is False
    assert len(warns) == 1 and "sospechosa" in warns[0] and "Girar 180°" in warns[0]


def test_a_verified_front_lets_a_large_warp_through(egg):
    """O07: con el frente verificado (`front_ok=True`) los umbrales no frenan; quedan como aviso. Con la pose girada (cobertura baja) pasa lo mismo:
    quien verifica el frente es O05/O06, no la compuerta."""
    pkg, A = package_for(egg, 0.3)
    out, rep = cw.warp_package(pkg, _flipped(A, egg), egg, front_ok=True)
    assert "pose_sospechosa" not in rep and "cobertura_despues" in rep
    assert rep.get("desplazamiento_alto") is True and rep["motivo"]
    out, rep = cw.warp_package(pkg, _flipped(A, egg), egg, front_ok=False)           # frente mal o sin medir: la compuerta frena
    assert out is pkg and rep["pose_sospechosa"] is True


def test_catalog_passes_the_verified_front_to_the_warp(egg):
    cat = Catalog.open(ROOT)
    pkg, A = package_for(egg, 0.3)
    A180 = _flipped(A, egg)
    meta, warns = _meta_with({"media_mm": 0.3, "ok": True, "recortada": True, "frente_ok": True}, A180), []
    out = cat._fit_roles_package(meta, egg, pkg, warns)
    nr = meta.color["alineacion"]["no_rigida"]
    assert out is not pkg and nr["aplicada"] is True and "pose_sospechosa" not in nr
    assert any("se ajustó a la pieza" in w for w in warns) and any("más de lo habitual" in w for w in warns)
