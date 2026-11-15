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
        merged = mesh.copy()
        merged.merge_vertices()
        return {"triangulos": int(len(mesh.faces)),
                "alto_mm": float(mesh.bounds[1][2] - mesh.bounds[0][2]),
                "estanca": bool(merged.is_watertight),
                "avisos": []}

    # ------------------------------------------------------------------ salida
    def build(self, project: str | dict, progress_cb: ProgressCb | None = None, name: str = "figura",
              orient: bool | str = False) -> dict:
        """Articula el STL cargado con el proyecto (texto JSON o dict). Devuelve el ZIP con todos los archivos."""
        from . import cli
        from .project import Project

        if self._stl_path is None:
            raise RuntimeError("primero hay que cargar un STL (load_stl)")
        data = json.loads(project) if isinstance(project, (str, bytes)) else project
        return self._run(self._stl_path, Project.from_dict(data), name, progress_cb, orient, cli)

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
