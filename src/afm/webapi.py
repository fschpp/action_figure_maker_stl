"""API del motor para el navegador (Pyodide): bytes de entrada y de salida, sin rutas del usuario (plan del port, §6.1).

No depende de GTK ni de la GUI. Usa el flujo de `cli.run`; los archivos intermedios van a un directorio temporal, que
en Pyodide es el sistema de archivos en memoria (nada toca el disco del usuario ni sale del navegador).

    s = EngineSession()
    s.load_stl(bytes_del_stl)                 # → {"triangulos", "alto_mm", "estanca", "avisos"}
    r = s.build(texto_del_proyecto_json)      # → {"zip": bytes, "archivos": [...], "informe": {...}}
    r = s.demo()                              # figura sintética + proyecto de ejemplo
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import zipfile
from typing import Callable

ProgressCb = Callable[[str], None]


def _progress(cb: ProgressCb | None):
    """El motor informa con `progress("texto")`; se adapta a cualquier callback de un argumento."""
    if cb is None:
        return lambda *a, **k: None
    return lambda *a, **k: cb(" ".join(str(x) for x in a))


class EngineSession:
    def __init__(self) -> None:
        self._dir = tempfile.mkdtemp(prefix="afm_")
        self._stl_path: str | None = None
        self._z_up = True

    # ------------------------------------------------------------------ entrada
    def load_stl(self, data: bytes) -> dict:
        """Guarda el STL de la sesión y devuelve un resumen. Rechaza lo que no se pueda leer como malla."""
        import trimesh

        try:
            mesh = trimesh.load(io.BytesIO(bytes(data)), file_type="stl", process=False)
        except Exception as exc:        # noqa: BLE001 — se informa tal cual al usuario
            raise ValueError(f"no se pudo leer el STL: {exc}") from exc
        if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
            raise ValueError("el archivo no contiene una malla de triángulos")
        path = os.path.join(self._dir, "figura.stl")
        with open(path, "wb") as f:
            f.write(bytes(data))
        self._stl_path = path
        self._z_up = int(mesh.extents.argmax()) == 2
        merged = mesh.copy()
        merged.merge_vertices()
        return {"triangulos": int(len(mesh.faces)),
                "alto_mm": float(mesh.bounds[1][2] - mesh.bounds[0][2]),
                "estanca": bool(merged.is_watertight),
                "z_arriba": self._z_up,
                "avisos": [] if self._z_up else ["el eje más largo del STL no es Z: el motor lo gira, pero los puntos "
                                                  "elegidos en el visor no se pueden llevar a la figura"]}

    def figure_frame(self, crown_z: float | None = None, ref_height: float = 100.0) -> dict:
        """Transformación del STL crudo (Z arriba) al marco normalizado en que se guardan los planos del proyecto:
        `figura = k·archivo + offset`. `crown_z` es la cota de la coronilla en unidades del archivo (None = punto más
        alto). Usa la misma carga y reparación que el motor (`meshio.load_figure`), así que el centro coincide."""
        from . import meshio

        if self._stl_path is None:
            raise ValueError("primero hay que cargar un STL")
        if not self._z_up:
            raise ValueError("el STL no tiene Z arriba: no se pueden llevar puntos del visor a la figura")
        _mesh, rep = meshio.load_figure(self._stl_path, float(ref_height), crown_z)
        return {"k": float(rep.scale), "offset": rep.to_figure([0.0, 0.0, 0.0])[0].tolist(),
                "ref_height": float(ref_height)}

    # ------------------------------------------------------------------ miembros
    @staticmethod
    def new_limb(name: str, kind: str, p0, p1, p2, auto: bool = True) -> dict:
        """Miembro (brazo/pierna) cuyo plano pasa por 3 puntos del STL, listo para el `.proyecto`.

        Mismos valores por defecto que `gui.session.Session.add_limb` (losa «outward» de 12 × 1,653 mm en brazos y en
        planos de costado; prisma simétrico de 100 mm en el resto). Con `auto=True` la silueta la propone el motor
        (`autosil`) al generar; el editor de polígonos (M3) la corrige después. Devuelve `LimbSpec.to_dict()`.
        """
        import numpy as np

        from .limbs import LimbSpec, check_limb_plane, plane_is_lateral
        from .planes import Plane

        if kind not in ("arm", "leg"):
            raise ValueError("el tipo de miembro debe ser «arm» o «leg»")
        plane = Plane.from_points(p0, p1, p2, name)        # ValueError si son colineales
        check_limb_plane(plane)
        outward = kind == "arm" or plane_is_lateral(plane)
        thickness = 12 * 1.653 if outward else 100.0       # ARM_SLAB_MM / prisma de cadera
        spec = LimbSpec(name, kind, plane, np.zeros((0, 3)), thickness, auto=auto,
                        extrude="outward" if outward else "symmetric")
        return spec.to_dict()

    # ------------------------------------------------------------------ cuello y cintura
    @staticmethod
    def new_head(p0, p1, p2, name: str = "cabeza cuerpo") -> dict:
        """Cabeza articulada cuyo plano del cuello pasa por 3 puntos (2 en la nuca y 1 bajo el mentón), ya en el marco
        de la figura. Misma validación que `Session.set_head` (plano casi vertical o girado de costado → ValueError);
        el pasador lo propone el motor. Devuelve `HeadSpec.to_dict()`."""
        from .head import HeadSpec, neck_plane
        from .planes import Plane

        spec = HeadSpec(Plane.from_points(p0, p1, p2, name))
        neck_plane(spec)
        return spec.to_dict()

    @staticmethod
    def new_waist(p0, p1, p2, name: str = "cintura") -> dict:
        """Cintura articulada cuyo plano de corte pasa por 3 puntos, ya en el marco de la figura. Misma validación que
        `Session.set_waist`; el pasador lo propone el motor. Devuelve `WaistSpec.to_dict()`."""
        from .planes import Plane
        from .waist import WaistSpec, waist_plane

        spec = WaistSpec(Plane.from_points(p0, p1, p2, name))
        waist_plane(spec)
        return spec.to_dict()

    # ------------------------------------------------------------------ salida
    def build(self, project: str | dict, progress_cb: ProgressCb | None = None, name: str = "figura",
              orient: bool | str = False) -> dict:
        """Articula el STL cargado con el proyecto (texto JSON o dict). Devuelve el ZIP con todos los archivos."""
        from . import cli
        from .project import Project

        if self._stl_path is None:
            raise RuntimeError("primero hay que cargar un STL (load_stl)")
        data = json.loads(project) if isinstance(project, (str, bytes)) else project
        proj = Project.from_dict(data)
        self._fill_silhouettes(proj, _progress(progress_cb))
        return self._run(self._stl_path, proj, name, progress_cb, orient, cli)

    def _fill_silhouettes(self, proj, progress) -> None:
        """Miembros sin polígono (creados en el visor con 3 puntos): la silueta la propone `autosil`, igual que
        «Proponer silueta» en la GUI de escritorio. Se corrige después con el editor de polígonos (M3)."""
        pending = [l.name for l in proj.limbs if len(l.polygon) == 0]
        if not pending:
            return
        from .gui.session import Session

        progress("Proponiendo siluetas…")
        sess = Session(proj)
        sess.load(self._stl_path, crown_z=proj.crown_z)
        for name in pending:
            try:
                sess.propose_silhouette(name)
            except ValueError as exc:
                raise ValueError(f"no se pudo proponer la silueta de «{name}»: {exc}") from exc

    def demo(self, progress_cb: ProgressCb | None = None, orient: bool | str = False) -> dict:
        """Figura sintética (`afm demo`) con su proyecto de ejemplo."""
        from . import cli
        from .project import demo_project
        from .testfigure import make_doll

        path = os.path.join(self._dir, "muneco.stl")
        make_doll().export(path)
        self._stl_path = path
        return self._run(path, demo_project(), "muneco", progress_cb, orient, cli)

    def _run(self, stl_path: str, project, name: str, progress_cb, orient, cli) -> dict:
        out = os.path.join(self._dir, "salida")
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        info = cli.run(stl_path, project, out, name, progress=_progress(progress_cb), orient=orient)
        buf = io.BytesIO()
        names: list[str] = []
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _dirs, files in os.walk(out):
                for fn in sorted(files):
                    full = os.path.join(root, fn)
                    arc = os.path.relpath(full, out)
                    z.write(full, arc)
                    names.append(arc)
        shutil.rmtree(out, ignore_errors=True)
        return {"zip": buf.getvalue(), "archivos": sorted(names), "informe": json.loads(json.dumps(info, default=float))}

    def close(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)
