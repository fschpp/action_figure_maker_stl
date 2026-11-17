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


# Por encima de esto el navegador tarda mucho (figura real de 19 532 tri: >1950 s; los STL de ejemplo tienen ~10 000).
HEAVY_STL_TRIANGLES = 15_000


class EngineSession:
    def __init__(self) -> None:
        self._dir = tempfile.mkdtemp(prefix="afm_")
        self._stl_path: str | None = None
        self._z_up = True
        self._last: dict | None = None

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
        avisos = [] if self._z_up else ["el eje más largo del STL no es Z: el motor lo gira, pero los puntos "
                                         "elegidos en el visor no se pueden llevar a la figura"]
        if len(mesh.faces) > HEAVY_STL_TRIANGLES:
            avisos.append(f"el STL tiene {len(mesh.faces)} triángulos (más de {HEAVY_STL_TRIANGLES}): en el navegador "
                          "generar puede tardar decenas de minutos; conviene decimarlo a ~10 000 antes de cargarlo")
        return {"triangulos": int(len(mesh.faces)),
                "alto_mm": float(mesh.bounds[1][2] - mesh.bounds[0][2]),
                "estanca": bool(merged.is_watertight),
                "z_arriba": self._z_up,
                "pesado": len(mesh.faces) > HEAVY_STL_TRIANGLES,
                "avisos": avisos}

    def decimate_loaded(self, target: int = 10_000) -> dict:
        """Reduce el STL cargado a ~`target` triángulos (decimación cuadrática) y lo deja como el STL de la sesión.

        No cambia el sistema de coordenadas (solo hay menos caras), así que los puntos elegidos en el visor siguen
        valiendo. Devuelve el resumen de `load_stl` más `stl` (bytes del STL reducido) y `reducido`; si ya tiene
        `target` triángulos o menos, no toca nada (`stl` = None)."""
        import numpy as np
        import trimesh

        if self._stl_path is None:
            raise RuntimeError("primero hay que cargar un STL")
        target = int(target)
        if target < 100:
            raise ValueError("el objetivo de triángulos es demasiado chico")
        mesh = trimesh.load(self._stl_path, file_type="stl", process=False)
        mesh.merge_vertices()
        n0 = len(mesh.faces)
        if n0 <= target:
            with open(self._stl_path, "rb") as f:
                info = self.load_stl(f.read())
            return {**info, "stl": None, "reducido": False, "triangulos_antes": n0}
        try:
            import fast_simplification as fs
        except ImportError as exc:
            raise RuntimeError("falta fast_simplification: no se puede decimar") from exc
        v, f = fs.simplify(mesh.vertices.astype(np.float32), mesh.faces.astype(np.int32),
                           target_reduction=1.0 - target / n0, agg=5)
        out = trimesh.Trimesh(v, f, process=False)
        data = bytes(out.export(file_type="stl"))
        info = self.load_stl(data)
        # el alto no debe moverse más de 0,5 %: si se mueve, la decimación se comió el modelo
        h0 = float(mesh.bounds[1][2] - mesh.bounds[0][2])
        if h0 > 0 and abs(info["alto_mm"] - h0) > 0.005 * h0:
            with open(self._stl_path, "wb") as fh:      # no se aplica: se vuelve al original
                trimesh.Trimesh(mesh.vertices, mesh.faces, process=False).export(fh, file_type="stl")
            info = self.load_stl(open(self._stl_path, "rb").read())
            return {**info, "stl": None, "reducido": False, "triangulos_antes": n0,
                    "avisos": info["avisos"] + ["la decimación cambiaba el alto de la figura: se dejó el STL original"]}
        return {**info, "stl": data, "reducido": True, "triangulos_antes": n0}

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

    def limb_editor(self, project: str | dict, name: str, propose: bool = True) -> dict:
        """Datos para el editor de polígonos 2D de la página (M3) del miembro ``name`` del proyecto.

        Devuelve el plano del miembro (``origin``, ``u``, ``v``, ``normal``: con ellos la página pasa de (u, v) a 3D),
        el fondo (``silueta``: polígonos ``{exterior, holes}`` en (u, v) de la figura proyectada sobre ese plano, el lado
        de trabajo x ≥ 0 como en la GUI de escritorio), el polígono actual en (u, v) (``polygon``, vacío si el miembro
        no tiene) y, con ``propose``, la propuesta de `autosil` (``propuesta``; ``None`` y ``aviso`` si no hay tramo
        donde el miembro quede separado del tronco, p. ej. el brazo pegado al cuerpo: hay que dibujarlo).
        """
        import numpy as np

        from .gui.session import Session
        from .project import Project

        if self._stl_path is None:
            raise RuntimeError("primero hay que cargar un STL (load_stl)")
        data = json.loads(project) if isinstance(project, (str, bytes)) else project
        proj = Project.from_dict(data)
        sess = Session(proj)
        sess.load(self._stl_path, crown_z=proj.crown_z)
        spec = sess.limb(name)
        if spec is None:
            raise ValueError(f"el proyecto no tiene un miembro «{name}»")
        plane = spec.plane
        out: dict = {"nombre": name, "tipo": spec.kind,
                     "origin": plane.origin.tolist(), "u": plane.u.tolist(), "v": plane.v.tolist(), "normal": plane.normal.tolist(),
                     "silueta": [{"exterior": g["exterior"].tolist(), "holes": [h.tolist() for h in g["holes"]]}
                                 for g in sess.silhouette(plane)],
                     "polygon": plane.to_uv(spec.polygon).tolist() if len(spec.polygon) >= 3 else [],
                     "propuesta": None, "aviso": None}
        if propose:
            try:
                poly = sess.propose_silhouette(name)
                out["propuesta"] = plane.to_uv(np.asarray(poly, float)).tolist()
            except ValueError as exc:
                out["aviso"] = str(exc)
        return out

    def trim_editor(self, project: str | dict, plane: str = "simetria") -> dict:
        """Datos para dibujar un recorte manual (§8.8): plano de dibujo y fondo, como `limb_editor` pero sin polígono.

        ``plane`` = ``"simetria"`` (x = 0 del marco de trabajo, normal +X: el plano por defecto del plan) o el nombre de un
        miembro del proyecto (se dibuja sobre el plano de ese miembro). El polígono que dibuje la página, llevado a 3D
        con ``origin``/``u``/``v``, viaja en ``project.trims`` (``Trim.to_dict``) y lo aplica el build.
        """
        from .gui.session import Session
        from .planes import Plane
        from .project import Project

        if self._stl_path is None:
            raise RuntimeError("primero hay que cargar un STL (load_stl)")
        data = json.loads(project) if isinstance(project, (str, bytes)) else project
        sess = Session(Project.from_dict(data))
        sess.load(self._stl_path, crown_z=sess.project.crown_z)
        if plane == "simetria":
            pl = Plane.from_normal((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), "simetría")
        else:
            spec = sess.limb(plane)
            if spec is None:
                raise ValueError(f"el proyecto no tiene un miembro «{plane}»")
            pl = spec.plane
        return {"plano": plane, "name": pl.name, "origin": pl.origin.tolist(), "u": pl.u.tolist(), "v": pl.v.tolist(),
                "normal": pl.normal.tolist(),
                "silueta": [{"exterior": g["exterior"].tolist(), "holes": [h.tolist() for h in g["holes"]]}
                            for g in sess.silhouette(pl)]}

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
        keep: dict = {}
        info = cli.run(stl_path, project, out, name, progress=_progress(progress_cb), orient=orient, keep=keep)
        self._last = keep or None          # resultado en memoria para `preview_pose`
        _progress(progress_cb)("Comprimiendo el ZIP…")
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

    def preview_pose(self, angle: float, collisions: bool = True) -> dict:
        """Figura generada con la cadera girada `angle` grados (0 = de pie, `sit_angle` = sentada), para el slider.

        Usa `pipeline.posed_pieces`, la misma composición de giros que la pose sentada del build, sobre el resultado
        en memoria de la última generación. Devuelve por pieza los vértices (float32, xyz) y los índices (uint32)
        en bytes, y con `collisions` el volumen (mm³) en que cada miembro se mete en el torso (> tolerancia = choque).
        """
        import numpy as np

        from . import pipeline as P
        from . import solids as S
        from .head import HEAD_NAME
        from .waist import PELVIS_NAME

        if not self._last:
            raise RuntimeError("primero hay que generar la figura (build) para ver su pose")
        res, params = self._last["res"], self._last["params"]
        angle = float(np.clip(angle, 0.0, params.sit_angle + params.hip_margin))
        posed = P.posed_pieces(res, angle, p=params)
        fixed = {n: res.pieces[n] for n in ("torso", PELVIS_NAME, HEAD_NAME) if n in res.pieces}
        pieces = {**fixed, **posed}
        out: dict = {"angulo": angle, "piezas": {}, "colisiones": {}}
        for name, mf in pieces.items():
            m = S.to_trimesh(mf)
            out["piezas"][name] = {"v": np.asarray(m.vertices, np.float32).tobytes(),
                                   "i": np.asarray(m.faces, np.uint32).tobytes()}
        if collisions:
            body = P.body_ref(res)
            for name, mf in posed.items():
                v = float((mf ^ body).volume())
                if v > P.JOINT_TOL_MM3:
                    out["colisiones"][name] = round(v, 4)
        return out

    def make_test_coupons(self, tipo: str = "hongo", clearances=None, deltas=None, axiales=None,
                          boquilla: float = 0.4, gaps=None, espesores=None) -> dict:
        """Probetas de calibración (§8.11) en un ZIP: mismos archivos que `afm probe` (`probes.probe_files`)."""
        from . import probes

        files, text = probes.probe_files(tipo, clearances=clearances, deltas=deltas, axiales=axiales,
                                         boquilla=boquilla, gaps=gaps, espesores=espesores)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(files):
                z.writestr(name, files[name])
        return {"zip": buf.getvalue(), "archivos": sorted(files), "texto": text}

    def close(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)
