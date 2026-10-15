"""Modelo de documento de la interfaz: una figura cargada + su proyecto. No depende de GTK.

Marcos de coordenadas (igual que el CLI, ver `afm.cli.run`):

* ``figure``: STL reparado, normalizado a ``ref_height`` mm, Z arriba, con el frente girado a +Y.
  Sobre esta malla se elige el plano de simetría.
* ``work``: ``figure`` con el plano de simetría llevado a x = 0. En este marco se definen los planos
  de silueta, los polígonos y los recortes (así los consume ``pipeline.build``).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np
import trimesh

from .. import meshio, pipeline as P, solids as S
from ..limbs import LimbSpec
from ..planes import Plane
from ..project import Project
from ..trims import Trim

FLIP_Z = np.array([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float)


@dataclass
class BuildOutcome:
    result: P.BuildResult
    validation: dict


class Session:
    def __init__(self, project: Project | None = None):
        self.project = project or Project()
        self.stl_path: str | None = None
        self.report: meshio.LoadReport | None = None
        self.base: trimesh.Trimesh | None = None      # normalizada, frente tal como viene en el archivo
        self.figure: trimesh.Trimesh | None = None
        self.work: trimesh.Trimesh | None = None
        self.outcome: BuildOutcome | None = None
        self._zmin_file = 0.0

    # ------------------------------------------------------------------ carga
    def load(self, path: str, crown_z: float | None = None) -> meshio.LoadReport:
        """Carga y repara el STL; ``crown_z`` en unidades del archivo (None = punto más alto)."""
        mesh, rep = meshio.load_figure(path, self.project.params.ref_height, crown_z)
        raw = meshio.orient_z_up(trimesh.load(path, force="mesh"))
        self._zmin_file = float(raw.bounds[0, 2])
        self.stl_path, self.report, self.base = path, rep, mesh
        self.project.crown_z = crown_z
        self.outcome = None
        self._rebuild_frames()
        return rep

    @property
    def loaded(self) -> bool:
        return self.base is not None

    def set_crown_from_mm(self, z_mm: float) -> None:
        """Declara que la altura ``z_mm`` (medida sobre la malla actual) es la coronilla: reescala para que
        mida ``ref_height`` y guarda la cota equivalente en unidades del archivo."""
        if not self.loaded or z_mm <= 1.0:
            raise ValueError("Cota de coronilla inválida")
        ref = self.project.params.ref_height
        k = ref / z_mm
        self.base = self.base.copy()
        self.base.apply_scale(k)
        self.report.scale *= k
        self.project.crown_z = float(self._zmin_file + z_mm / (self.report.scale / k))
        self.outcome = None
        self._rebuild_frames()

    def set_front(self, front: str) -> None:
        """'+Y' o '-Y' según hacia dónde mira la figura en el archivo. Invalida simetría, miembros y recortes."""
        if front not in ("+Y", "-Y"):
            raise ValueError("front debe ser '+Y' o '-Y'")
        if front == self.project.front:
            return
        self.project.front = front
        self.project.symmetry = None
        self.project.limbs, self.project.trims = [], []
        self.outcome = None
        self._rebuild_frames()

    def _rebuild_frames(self) -> None:
        if self.base is None:
            self.figure = self.work = None
            return
        fig = self.base.copy()
        if self.project.front == "-Y":
            fig.apply_transform(FLIP_Z)
        self.figure = fig
        self.work = P.align_to_symmetry(fig, self.project.symmetry)

    # -------------------------------------------------------------- simetría
    @staticmethod
    def plane_from_points(points, name: str = "") -> Plane:
        pts = np.asarray(points, float)
        if pts.shape != (3, 3):
            raise ValueError("Se necesitan 3 puntos")
        return Plane.from_points(*pts, name=name)

    def set_symmetry(self, plane: Plane | None) -> None:
        """Plano de simetría (en el marco ``figure``). None = ninguno (se usa la figura tal cual)."""
        if plane is not None and abs(plane.normal[2]) > 0.05:
            raise ValueError("El plano de simetría debe ser vertical (la normal no puede apuntar arriba/abajo)")
        self.project.symmetry = plane
        self.outcome = None
        self._rebuild_frames()

    def auto_symmetry(self) -> Plane:
        """Plano x = 0 del marco normalizado (la normalización centra la figura en X)."""
        plane = Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría")
        self.set_symmetry(plane)
        return plane

    # --------------------------------------------------------------- miembros
    def limb(self, name: str) -> LimbSpec | None:
        return next((l for l in self.project.limbs if l.name == name), None)

    def add_limb(self, name: str, kind: str, plane: Plane, thickness: float = 100.0, auto: bool = True) -> LimbSpec:
        if kind not in ("arm", "leg"):
            raise ValueError("kind debe ser 'arm' o 'leg'")
        if self.limb(name):
            raise ValueError(f"Ya existe un miembro llamado {name!r}")
        spec = LimbSpec(name, kind, plane, np.zeros((0, 3)), thickness, auto=auto)
        self.project.limbs.append(spec)
        self.outcome = None
        return spec

    def set_limb_polygon(self, name: str, polygon_world) -> None:
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        poly = np.asarray(polygon_world, float)
        if poly.ndim != 2 or poly.shape[1] != 3:
            raise ValueError("El polígono debe ser Nx3")
        spec.polygon = poly
        self.outcome = None

    def remove_limb(self, name: str) -> None:
        self.project.limbs = [l for l in self.project.limbs if l.name != name]
        self.project.trims = [t for t in self.project.trims if t.target != name]
        self.outcome = None

    def add_trim(self, trim: Trim) -> None:
        self.project.trims.append(trim)
        self.outcome = None

    def remove_trim(self, index: int) -> None:
        del self.project.trims[index]
        self.outcome = None

    # ------------------------------------------------------------- siluetas
    def silhouette(self, plane: Plane, positive_x_only: bool = True, simplify: float = 0.05):
        """Silueta de ``work`` proyectada sobre ``plane``, en coordenadas (u, v) del plano.

        Devuelve una lista de polígonos, cada uno ``{"exterior": Nx2, "holes": [Mx2, ...]}``. Con
        ``positive_x_only`` solo cuenta el lado de trabajo (x >= 0), el que se edita.
        """
        import shapely

        if self.work is None:
            return []
        tris = self.work.triangles
        if positive_x_only:
            tris = tris[tris[:, :, 0].mean(axis=1) >= -1e-6]
        uv = (tris.reshape(-1, 3) - plane.origin) @ np.stack([plane.u, plane.v], axis=1)
        uv = uv.reshape(-1, 3, 2)
        a, b = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
        area = 0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
        uv = uv[area > 1e-9]
        if len(uv) == 0:
            return []
        union = shapely.union_all(shapely.polygons(uv))
        if simplify > 0:
            union = union.simplify(simplify)
        geoms = list(union.geoms) if hasattr(union, "geoms") else [union]
        out = []
        for g in geoms:
            if g.is_empty or g.geom_type != "Polygon":
                continue
            out.append({"exterior": np.asarray(g.exterior.coords), "holes": [np.asarray(h.coords) for h in g.interiors]})
        return out

    # ---------------------------------------------------------------- build
    def problems(self) -> list[str]:
        """Motivos por los que todavía no se puede generar (vacío = listo)."""
        out = []
        if not self.loaded:
            out.append("Abrí un STL")
            return out
        if self.project.symmetry is None:
            out.append("Falta el plano de simetría")
        if not self.project.limbs:
            out.append("Definí al menos un miembro (brazo o pierna)")
        for l in self.project.limbs:
            if len(l.polygon) < 3:
                out.append(f"{l.name}: dibujá la silueta (mínimo 3 puntos)")
        return out

    def build(self, progress=None) -> BuildOutcome:
        probs = self.problems()
        if probs:
            raise ValueError("; ".join(probs))
        body = S.to_manifold(self.work)
        res = P.build(body, self.project.limbs, self.project.params, self.project.symmetry,
                      progress=progress, trims=self.project.trims)
        val = P.validate(res, self.project.params, self.project.limbs)
        self.outcome = BuildOutcome(res, val)
        return self.outcome

    def export(self, outdir: str, name: str | None = None, orient: bool = False, max_angle: float = 45.0) -> dict:
        if self.outcome is None:
            raise ValueError("Primero generá la figura")
        name = name or (os.path.splitext(os.path.basename(self.stl_path or "figura"))[0])
        os.makedirs(outdir, exist_ok=True)
        files = P.export(self.outcome.result, outdir, name, orient=orient, max_angle=max_angle)
        info = {"validacion": self.outcome.validation, "construccion": self.outcome.result.report, "archivos": files}
        with open(os.path.join(outdir, f"{name}_informe.json"), "w") as f:
            json.dump(info, f, indent=2, default=float)
        self.save_project(os.path.join(outdir, f"{name}.proyecto"))
        return info

    # ------------------------------------------------------------- proyecto
    def save_project(self, path: str) -> None:
        d = self.project.to_dict()
        d["source"] = os.path.abspath(self.stl_path) if self.stl_path else None
        with open(path, "w") as f:
            json.dump(d, f, indent=2)

    @classmethod
    def open_project(cls, path: str, stl_path: str | None = None) -> "Session":
        with open(path) as f:
            d = json.load(f)
        src = stl_path or d.get("source")
        if not src or not os.path.exists(src):
            raise FileNotFoundError(f"No se encuentra el STL del proyecto: {src!r}. Indicá su ubicación.")
        sess = cls(Project.from_dict(d))
        sess.load(src, sess.project.crown_z)
        return sess
