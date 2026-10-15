"""Carga, reparación y normalización de STL (plan §8.1)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import trimesh

from . import solids as S

log = logging.getLogger(__name__)


@dataclass
class LoadReport:
    original_height: float = 0.0
    scale: float = 1.0
    removed_components: int = 0
    was_watertight: bool = False
    repaired: bool = False
    remeshed: bool = False
    warnings: list[str] = field(default_factory=list)


def _clean(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    mesh = mesh.copy()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    return mesh


def _drop_loose(mesh: trimesh.Trimesh, report: LoadReport) -> trimesh.Trimesh:
    """Quita componentes sueltos (p. ej. triángulos huérfanos)."""
    parts = mesh.split(only_watertight=False)
    if len(parts) > 1:
        big = max(len(p.faces) for p in parts)
        keep = [p for p in parts if len(p.faces) >= 0.01 * big]
        report.removed_components = len(parts) - len(keep)
        mesh = trimesh.util.concatenate(keep)
        mesh.merge_vertices()
    return mesh


def _closed_manifold(mesh: trimesh.Trimesh) -> bool:
    """¿Lo acepta manifold3d (tras cerrar agujeros chicos)? Modifica `mesh` in situ."""
    if not mesh.is_watertight:
        trimesh.repair.fix_inversion(mesh)
        trimesh.repair.fix_normals(mesh)
        trimesh.repair.fill_holes(mesh)
    try:
        S.to_manifold(mesh)
        return True
    except S.MeshNotManifold:
        return False


def repair(mesh: trimesh.Trimesh, report: LoadReport | None = None) -> trimesh.Trimesh:
    """Repara lo básico; si sigue sin ser variedad, remalla por voxelización.

    Se prueban dos variantes y se queda la primera que acepta manifold3d: (1) limpiando triángulos
    degenerados/duplicados, (2) sin limpiarlos. Hay STL (Belgrano, Azurduy) cuyos triángulos «degenerados»
    sostienen la topología: limpiarlos abre la malla y llevaba a un remallado por voxelización
    innecesario (más de un millón de triángulos).
    """
    report = report or LoadReport()
    mesh = mesh.copy()
    mesh.merge_vertices()
    raw = mesh.copy()
    cleaned = _clean(mesh)
    report.was_watertight = cleaned.is_watertight or raw.is_watertight
    for cand in (cleaned, raw):
        cand = _drop_loose(cand, report)
        was_wt = cand.is_watertight
        if _closed_manifold(cand):
            report.repaired = not was_wt
            return cand
    mesh = _drop_loose(cleaned, report)
    pitch = max(mesh.extents.max() / 700.0, 1e-6)
    vox = mesh.voxelized(pitch).fill()
    mesh = vox.marching_cubes
    mesh.apply_transform(vox.transform)
    mesh = mesh.copy()
    mesh.merge_vertices()
    trimesh.repair.fix_normals(mesh)
    report.repaired = True
    report.remeshed = True
    report.warnings.append(
        f"La malla no era estanca ni reparable: se remalló por voxelización (paso {pitch:.4g})."
    )
    S.to_manifold(mesh)  # si falla acá, que explote con mensaje claro
    return mesh


def orient_z_up(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Si la figura viene con otro eje vertical (p. ej. Y arriba), la gira para que Z quede arriba.

    Heurística: el eje más largo de la caja envolvente es la altura.
    """
    ext = mesh.extents
    ax = int(np.argmax(ext))
    if ax == 2:
        return mesh
    mesh = mesh.copy()
    R = np.eye(4)
    if ax == 1:  # Y arriba -> Z arriba (rotación +90° sobre X)
        R[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    else:  # X arriba -> Z arriba
        R[:3, :3] = [[0, 0, 1], [0, 1, 0], [-1, 0, 0]]
    mesh.apply_transform(R)
    return mesh


def normalize(mesh: trimesh.Trimesh, ref_height: float = 100.0, crown_z: float | None = None,
              report: LoadReport | None = None) -> trimesh.Trimesh:
    """Escala a `ref_height` mm entre los pies (z mínimo) y la coronilla.

    `crown_z` es la cota de la coronilla en unidades del archivo original (tras orientar
    Z arriba). Si no se indica, se usa el punto más alto (incluye sombrero) y se avisa.
    Apoya los pies en z=0 y centra X en el plano medio (punto medio de la caja) e Y en
    el centro de la caja.
    """
    report = report or LoadReport()
    mesh = mesh.copy()
    zmin = mesh.bounds[0, 2]
    top = mesh.bounds[1, 2] if crown_z is None else crown_z
    if crown_z is None:
        report.warnings.append("Coronilla no indicada: se usó el punto más alto (incluye sombrero/orejas).")
    height = top - zmin
    if height <= 0:
        raise ValueError("Altura de referencia inválida")
    report.original_height = float(height)
    report.scale = float(ref_height / height)
    mesh.apply_translation(-np.array([0.0, 0.0, zmin]))
    mesh.apply_scale(report.scale)
    c = mesh.bounds.mean(axis=0)
    mesh.apply_translation([-c[0], -c[1], 0.0])
    return mesh


def load_figure(path: str, ref_height: float = 100.0, crown_z: float | None = None
                ) -> tuple[trimesh.Trimesh, LoadReport]:
    report = LoadReport()
    mesh = trimesh.load(path, force="mesh")
    mesh = orient_z_up(mesh)
    # Las tolerancias de la reparación son absolutas: se escala a mm ANTES de reparar, para que un STL
    # en otras unidades (p. ej. Belgrano, ~1,16 de alto) no pierda triángulos finos y termine remallado.
    top = mesh.bounds[1, 2] if crown_z is None else crown_z
    pre = ref_height / (top - mesh.bounds[0, 2]) if top > mesh.bounds[0, 2] else 1.0
    mesh = mesh.copy()
    mesh.apply_scale(pre)
    mesh = repair(mesh, report)
    mesh = normalize(mesh, ref_height, None if crown_z is None else crown_z * pre, report)
    report.original_height = float(report.original_height / pre)   # en unidades del archivo
    report.scale = float(report.scale * pre)
    return mesh, report


def save_stl(mesh: trimesh.Trimesh, path: str) -> None:
    mesh.export(path)
