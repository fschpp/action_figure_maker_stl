"""Similitud FBX → marco de la figura, calculada desde la GUI (asistente «Roles de color») o desde `tools/similitud_figura.py`.

Cuando la pieza del catálogo es un trozo recortado de una figura (brazo, pierna, torso…), el FBX es el de la figura ENTERA y no calza con
el STL de la pieza. La matriz que hace falta es la misma que usa `afm build --color-desde`: se alinea el FBX con la figura tal como se
carga (STL + `.proyecto`) y se le suma lo que el motor hace después (`FigureFrame.engine_post`). Sin GTK; `trimesh` se importa al usarla.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class FigureSimilarityError(ValueError):
    """No se pudo calcular la similitud (archivo ilegible, o el FBX no es el de esa figura). El mensaje está listo para mostrarse."""


@dataclass
class FigureSimilarity:
    matrix: list              # 4×4 como lista de listas (lo que guarda el JSON y el catálogo)
    media_mm: float
    ok: bool
    report: dict


def compute_figure_similarity(stl_path: str, project_path: str, fbx_path: str, *, tm=None) -> FigureSimilarity:
    """Calcula la 4×4 FBX → figura. `tm`: `TexturedMesh` ya leído (evita leer el FBX otra vez). No levanta por «no calza»: devuelve `ok=False`
    y quien llama decide (la GUI lo muestra y no usa la matriz)."""
    from . import meshio
    from .fbxio import FbxError, load_textured
    from .frame import FigureFrame
    from .project import Project

    for what, p in (("STL", stl_path), (".proyecto", project_path), ("FBX", fbx_path)):
        if not p or not os.path.isfile(p):
            raise FigureSimilarityError(f"no se encuentra el archivo {what}: {p or '(vacío)'}")
    try:
        project = Project.load(project_path)
        mesh, _rep = meshio.load_figure(stl_path, project.params.ref_height, project.crown_z)   # la figura tal como se carga, antes de girar/alinear
        h, ref = float(mesh.bounds[1, 2] - mesh.bounds[0, 2]), float(project.params.ref_height)
        if not 0.5 * ref <= h <= 2.0 * ref:                  # el STL no es de ese proyecto (su coronilla/escala no lo dejan en la altura de referencia)
            raise FigureSimilarityError(f"el STL «{os.path.basename(stl_path)}» no corresponde al proyecto «{os.path.basename(project_path)}»: "
                                        f"cargado mide {h:.1f} mm de alto y el proyecto espera ≈{ref:g} mm")
        tm = tm if tm is not None else load_textured(fbx_path)
        frame = FigureFrame.from_similarity(tm, mesh).then(FigureFrame.engine_post(project.front, project.symmetry))
    except FigureSimilarityError:
        raise
    except FbxError as exc:
        raise FigureSimilarityError(f"{fbx_path}: {exc}") from exc
    except (OSError, ValueError, KeyError) as exc:
        raise FigureSimilarityError(f"no se pudo calcular la similitud: {exc}") from exc
    media = float(frame.report.get("media_mm", 0.0))
    return FigureSimilarity(frame.matrix.tolist(), media, bool(frame.report.get("ok", True)), dict(frame.report))
