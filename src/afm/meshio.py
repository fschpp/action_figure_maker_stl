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
    zmin_file: float = 0.0          # z mínimo de la malla orientada, en unidades del archivo
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


VOXEL_FALLBACK_RES = 400       # 700 pedía varios GB de RAM; 400 (≈0,3 mm en una figura de 127 mm) alcanza como último recurso


def _meshfix(mesh: trimesh.Trimesh) -> trimesh.Trimesh | None:
    """Repara con pymeshfix (cierra agujeros de cualquier forma y arregla aristas no-variedad).

    Devuelve None si pymeshfix no está instalado, si falla o si el resultado cambia el volumen de forma sospechosa
    (más de 2 %: señal de que «reparó» borrando o inventando partes).
    """
    try:
        import pymeshfix
    except ImportError:
        return None
    try:
        v, f = pymeshfix.clean_from_arrays(np.ascontiguousarray(mesh.vertices, dtype=np.float64),
                                           np.ascontiguousarray(mesh.faces, dtype=np.int32))
    except Exception:          # noqa: BLE001 — cualquier falla de pymeshfix = probar el siguiente método
        return None
    if len(f) == 0:
        return None
    fixed = trimesh.Trimesh(v, f, process=True)
    if not fixed.is_watertight:
        return None
    ref = abs(mesh.volume) if mesh.is_volume else 0.0
    if ref > 0 and abs(abs(fixed.volume) - ref) / ref > 0.02:
        return None
    return fixed


PINCH_SEPARATION_MM = 0.01       # cuánto se corre cada lámina hacia su propio interior (muy por debajo de la resolución de impresión)
PINCH_MAX_EDGES = 500


PINCH_MAX_LOOP = 16              # lazos de borde más largos no se cierran con un abanico: los cura `_meshfix`


def _fill_small_loops(mesh: trimesh.Trimesh, max_len: int = PINCH_MAX_LOOP) -> trimesh.Trimesh:
    """Cierra con un abanico de triángulos los lazos de borde chicos (de hasta `max_len` aristas) que quedan al separar un
    pellizco cuyos abanicos de los dos extremos no se corresponden (`trimesh.repair.fill_holes` solo cierra 3 o 4 aristas).
    Devuelve la malla (la misma si ya estaba cerrada o si algún lazo no se puede cerrar)."""
    faces = np.asarray(mesh.faces)
    directed = {}
    for f in faces:
        for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            directed[(int(a), int(b))] = True
    nxt: dict[int, list[int]] = {}
    for (a, b) in directed:
        if (b, a) not in directed:                       # arista de borde: la cara nueva debe recorrerla al revés
            nxt.setdefault(a, []).append(b)
    if not nxt or any(len(v) != 1 for v in nxt.values()):
        return mesh                                      # un vértice con dos salidas: lazo no simple
    new, seen = [], set()
    for start in list(nxt):
        if start in seen:
            continue
        loop, v = [], start
        while v not in seen:
            seen.add(v)
            loop.append(v)
            v = nxt[v][0]
            if v not in nxt:
                return mesh
        if v != start or not 3 <= len(loop) <= max_len:
            return mesh
        for r in range(len(loop)):                       # un abanico cuyas diagonales ya sean aristas de la malla crearía otra arista de 4 caras
            lp = loop[r:] + loop[:r]
            if len(lp) == 3 or not any((lp[0], lp[k]) in directed or (lp[k], lp[0]) in directed for k in range(2, len(lp) - 1)):
                break
        else:
            return mesh
        new += [(lp[0], lp[k + 1], lp[k]) for k in range(1, len(lp) - 1)]
    out = trimesh.Trimesh(np.asarray(mesh.vertices), np.vstack([faces, np.asarray(new, dtype=faces.dtype)]), process=False)
    return out


def separate_pinches(mesh: trimesh.Trimesh, eps: float = PINCH_SEPARATION_MM, max_edges: int = PINCH_MAX_EDGES) -> trimesh.Trimesh | None:
    """Arregla las aristas de 4 caras («pellizcos»): dos láminas de la superficie que se tocan en una arista.

    Pasa con mallas sin huecos que casi se tocan (rulos de pelo, pliegues, dedos pegados; también lo que dejan los servicios
    de reparación en línea): cada lámina es una superficie sana, pero al soldar los vértices coincidentes —lo que hace el
    laminador, y `write_piece`— quedan 4 caras en una arista y la malla ya no es estanca. Acá cada abanico de caras que
    rodea un vértice pellizcado se separa del otro (el vértice se duplica) y se corre `eps` mm contra su normal, es decir
    hacia el interior del sólido: las dos láminas quedan a `2·eps` y dejan de soldarse. El volumen cambia en el orden de
    `eps × el largo del pellizco`.

    Devuelve la malla nueva, o None si el defecto no es solo de aristas con exactamente 4 caras (huecos, aristas de 3 o de
    más de 4 caras: eso lo cura `_meshfix`), si hay más de `max_edges` aristas o si el resultado no queda estanco."""
    from collections import defaultdict

    edges = mesh.edges_sorted
    uniq, cnt = np.unique(edges, axis=0, return_counts=True)
    bad = uniq[cnt != 2]
    if len(bad) == 0 or len(bad) > max_edges or (cnt[cnt != 2] != 4).any():
        return None
    orig = np.asarray(mesh.faces)                        # los abanicos se miden siempre sobre la malla original
    faces, verts, normals = orig.copy(), [v for v in mesh.vertices], mesh.face_normals
    around: dict[int, list[int]] = defaultdict(list)
    for fi, f in enumerate(orig):
        for v in f:
            around[int(v)].append(fi)
    bad_edges = {(int(a), int(b)) for a, b in bad}
    plan: list[tuple[int, list[list[int]]]] = []         # (vértice, abanicos de caras)
    for v in sorted({int(x) for e in bad for x in e}):
        fan = {f: f for f in around[v]}                  # abanicos: caras unidas por una arista sana (2 caras) que contiene v

        def root(f: int) -> int:
            while fan[f] != f:
                fan[f] = fan[fan[f]]
                f = fan[f]
            return f

        by_edge: dict[tuple, list[int]] = defaultdict(list)
        for f in around[v]:
            for a, b in ((0, 1), (1, 2), (2, 0)):
                e = tuple(sorted((int(orig[f][a]), int(orig[f][b]))))
                if v in e and e not in bad_edges:
                    by_edge[e].append(f)
        for fl in by_edge.values():
            for f in fl[1:]:
                fan[root(f)] = root(fl[0])
        groups: dict[int, list[int]] = defaultdict(list)
        for f in around[v]:
            groups[root(f)].append(f)
        if len(groups) < 2:
            return None                                  # el pellizco no pasa por un vértice de dos abanicos: no es el caso que se sabe curar
        plan.append((v, list(groups.values())))
    for v, groups in plan:
        pos = np.asarray(mesh.vertices[v], float)
        for k, group in enumerate(groups):
            n = normals[group].sum(axis=0)
            n /= np.linalg.norm(n) or 1.0
            if k == 0:
                verts[v] = pos - n * eps
            else:
                verts.append(pos - n * eps)
                for f in group:
                    faces[f][orig[f] == v] = len(verts) - 1
    fixed = trimesh.Trimesh(np.asarray(verts), faces, process=False)
    if not fixed.is_watertight:
        # si los abanicos de los dos extremos de la arista no se corresponden (la lámina se retuerce), tras separarlos queda un
        # lazo de 4 aristas de ~2·eps: se cierra con un cuadrilátero mínimo (no inventa volumen)
        trimesh.repair.fill_holes(fixed)
    if not fixed.is_watertight:
        fixed = _fill_small_loops(fixed)
    return fixed if fixed.is_watertight and fixed.is_winding_consistent else None


def repair(mesh: trimesh.Trimesh, report: LoadReport | None = None, voxel_res: int = VOXEL_FALLBACK_RES,
           progress=None) -> trimesh.Trimesh:
    """Repara lo básico; si sigue sin ser variedad, prueba pymeshfix y, como último recurso, remalla por voxelización.

    `progress(texto)` (opcional) informa qué paso se está haciendo.

    Se prueban dos variantes y se queda la primera que acepta manifold3d: (1) limpiando triángulos
    degenerados/duplicados, (2) sin limpiarlos. Hay STL (Belgrano, Azurduy) cuyos triángulos «degenerados»
    sostienen la topología: limpiarlos abre la malla y llevaba a un remallado por voxelización
    innecesario (más de un millón de triángulos).
    """
    report = report or LoadReport()
    say = progress or (lambda _t: None)
    say("reparando la malla…")
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
    say("la malla no es estanca: cerrando agujeros (pymeshfix)…")
    for cand in (mesh, _drop_loose(raw, report)):
        fixed = _meshfix(cand)
        if fixed is not None and _closed_manifold(fixed):
            report.repaired = True
            report.warnings.append("La malla tenía agujeros o aristas no-variedad: se reparó con pymeshfix.")
            return fixed
    say(f"no se pudo reparar: remallando por voxelización (resolución {voxel_res}; puede tardar y usar mucha memoria)…")
    pitch = max(mesh.extents.max() / float(voxel_res), 1e-6)
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


def load_figure(path: str, ref_height: float = 100.0, crown_z: float | None = None, progress=None
                ) -> tuple[trimesh.Trimesh, LoadReport]:
    report = LoadReport()
    if progress:
        progress("leyendo el STL…")
    mesh = trimesh.load(path, force="mesh")
    mesh = orient_z_up(mesh)
    report.zmin_file = float(mesh.bounds[0, 2])
    # Las tolerancias de la reparación son absolutas: se escala a mm ANTES de reparar, para que un STL
    # en otras unidades (p. ej. Belgrano, ~1,16 de alto) no pierda triángulos finos y termine remallado.
    top = mesh.bounds[1, 2] if crown_z is None else crown_z
    pre = ref_height / (top - mesh.bounds[0, 2]) if top > mesh.bounds[0, 2] else 1.0
    mesh = mesh.copy()
    mesh.apply_scale(pre)
    mesh = repair(mesh, report, progress=progress)
    mesh = normalize(mesh, ref_height, None if crown_z is None else crown_z * pre, report)
    report.original_height = float(report.original_height / pre)   # en unidades del archivo
    report.scale = float(report.scale * pre)
    return mesh, report


def save_stl(mesh: trimesh.Trimesh, path: str) -> None:
    mesh.export(path)
