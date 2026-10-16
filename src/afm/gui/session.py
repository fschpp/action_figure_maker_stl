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
from ..limbs import LimbSpec, PinPreview, check_limb_plane, contact_preview, effective_cut_offset, plane_is_lateral, plane_on_symmetry
from ..params import PinSpec

from ..planes import Plane
from ..project import Project

ARM_SLAB_MM = 12 * 1.653      # losa del brazo: 12 mm del tutorial a la escala final (plan §5.1)
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

    def add_limb(self, name: str, kind: str, plane: Plane, thickness: float | None = None, auto: bool = True,
                 extrude: str | None = None) -> LimbSpec:
        """Por defecto el brazo usa una losa «outward» de ARM_SLAB_MM (12 mm del tutorial × 1,653 de
        reescala) y la pierna un prisma simétrico de 100 mm (cubre toda la circunferencia de la cadera).
        Un plano de costado (normal ∥ X) es él mismo la cara de contacto: ahí la extrusión es siempre
        «outward», también en las piernas (un prisma simétrico atravesaría el miembro)."""
        if extrude is None:
            extrude = "outward" if (kind == "arm" or plane_is_lateral(plane)) else "symmetric"
        if thickness is None:
            thickness = ARM_SLAB_MM if extrude == "outward" else 100.0
        if kind not in ("arm", "leg"):
            raise ValueError("kind debe ser 'arm' o 'leg'")
        if self.limb(name):
            raise ValueError(f"Ya existe un miembro llamado {name!r}")
        check_limb_plane(plane)
        spec = LimbSpec(name, kind, plane, np.zeros((0, 3)), thickness, auto=auto, extrude=extrude)
        self.project.limbs.append(spec)
        self.outcome = None
        return spec

    def limb_offset(self, name: str) -> float | None:
        """Desplazamiento efectivo del corte de `name`, o None si su plano no es el de simetría."""
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        if not plane_on_symmetry(spec.plane, spec.contact_axis):
            return None
        try:
            return effective_cut_offset(spec)
        except ValueError:
            return 0.0

    def set_limb_offset(self, name: str, mm: float) -> None:
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        mm = float(mm)
        if not np.isfinite(mm) or not (0.5 <= mm <= 200.0):
            raise ValueError("el desplazamiento del corte tiene que estar entre 0,5 y 200 mm")
        if not plane_on_symmetry(spec.plane, spec.contact_axis):
            raise ValueError(f"«{name}» no está sobre el plano de simetría: el desplazamiento no aplica")
        spec.cut_offset = mm
        spec.pin_center = None          # la cara de contacto se movió
        self.outcome = None

    def fix_limb_extrude(self, name: str) -> bool:
        """Si el plano de `name` está de costado pero la extrusión es simétrica (proyectos guardados antes
        de este cambio), pasa a «outward» y descarta el pasador elegido (estaba sobre una cara que no era la
        de corte). Devuelve True si cambió algo."""
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        if spec.extrude == "symmetric" and plane_is_lateral(spec.plane, spec.contact_axis):
            spec.extrude = "outward"
            spec.pin_center = None
            self.outcome = None
            return True
        return False

    def set_limb_polygon(self, name: str, polygon_world) -> None:
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        poly = np.asarray(polygon_world, float)
        if poly.ndim != 2 or poly.shape[1] != 3:
            raise ValueError("El polígono debe ser Nx3")
        spec.polygon = poly
        self.outcome = None

    def propose_silhouette(self, name: str, crotch_z: float | None = None) -> np.ndarray:
        """Propone la silueta del miembro ``name`` desde cortes horizontales de ``work`` (`afm.autosil`) y\n        la guarda en su plano. ``crotch_z`` (solo piernas) fuerza la altura de la entrepierna.\n        Falla con ValueError si no se encuentra el miembro en la malla o el plano está de canto."""
        from .. import autosil

        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        if self.work is None:
            raise ValueError("Abrí un STL")
        key = id(self.work)
        if getattr(self, "_slices_key", None) != key:
            self._slices, self._slices_key = autosil.scan(self.work), key
        if spec.kind == "arm":
            p2 = autosil.propose_arm(self.work, self._slices)
        else:
            p2 = autosil.propose_leg(self.work, self._slices, crotch_z=crotch_z)
        if p2 is None:
            raise ValueError("No se pudo proponer la silueta: no hay un tramo donde el miembro quede separado del "
                             "tronco. Dibujala a mano.")
        poly = autosil.outline_on_plane(self.work, p2, spec.plane)
        self.set_limb_polygon(name, poly)
        return poly

    # ----------------------------------------------------------------- pasador
    def pin_spec(self, name: str) -> PinSpec:
        """Medidas del pasador del miembro (hombro para los brazos, cadera para las piernas)."""
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        p = self.project.params
        return p.hip_pin if spec.kind == "leg" else p.shoulder_pin

    def pin_preview(self, name: str) -> PinPreview:
        """Cara de contacto (plano de corte) del miembro y centro por defecto del pasador.

        Corta solo ese miembro de la mitad de trabajo (no construye la figura), con la silueta y la rebaba
        actuales. Se guarda en caché mientras no cambien la malla, la silueta ni los parámetros.
        Falla con ValueError si falta la silueta o la silueta no deja una cara de contacto plana.
        """
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        if self.work is None:
            raise ValueError("Abrí un STL")
        if self.project.symmetry is None:
            raise ValueError("Falta el plano de simetría")
        if len(spec.polygon) < 3:
            raise ValueError(f"{name}: dibujá primero la silueta (mínimo 3 puntos)")
        burr = self.project.params.burr
        key = (id(self.work), np.asarray(spec.polygon).tobytes(), spec.plane.origin.tobytes(),
               spec.plane.normal.tobytes(), spec.thickness, spec.extrude, spec.cut_offset, tuple(spec.contact_axis), spec.burr,
               burr, spec.auto, tuple(spec.cap_dir))
        cache = getattr(self, "_pin_cache", None)
        if cache is not None and cache[0] == key:
            return cache[1]
        hkey = id(self.work)
        if getattr(self, "_half_key", None) != hkey:
            self._half = S.to_manifold(self.work).trim_by_plane((1.0, 0.0, 0.0), 0.0)
            self._half_key = hkey
        prev = contact_preview(self._half, spec, burr)
        shaft = self.pin_spec(name).shaft_d
        if prev.face.area < np.pi * (shaft / 2) ** 2:
            raise ValueError(
                f"la cara de contacto encontrada es muy chica ({prev.face.area:.1f} mm², menos que el vástago de "
                f"Ø{shaft:g}): no es la cara de corte. Apoyá el plano en la cara chata donde el miembro se separa del "
                "torso (la extrusión es «hacia afuera» si el plano está de costado), o revisá la silueta")
        self._pin_cache = (key, prev)
        return prev

    def pin_reach_margin(self, name: str) -> float | None:
        """Holgura (mm) entre la punta del pasador y el plano de simetría, descontando la holgura de la cavidad
        y media pared mínima. Negativa: el pasador (o su cavidad) se cruza con el del lado opuesto, que es el
        espejo. Solo aplica si la cara de contacto es paralela al plano de simetría (pasadores enfrentados)."""
        sym = self.project.symmetry
        spec = self.limb(name)
        if sym is None or spec is None or self.work is None:
            return None
        prev = self.pin_preview(name)
        n = np.asarray(sym.normal, float) / np.linalg.norm(np.asarray(sym.normal, float))
        if abs(float(prev.face.outward @ n)) < 0.99:
            return None
        pin, prm = self.pin_spec(name), self.project.params
        dist = abs(float((prev.face.center - np.asarray(sym.origin, float)) @ n))
        return dist - (pin.shaft_len + pin.head_h) - prm.clearance - prm.min_wall / 2

    def set_limb_pin(self, name: str, point_world) -> None:
        """Fija el centro del pasador de `name` en `point_world` (se proyecta sobre el plano de la cara de
        contacto). Se puede elegir cualquier punto: el centro puede quedar fuera de la cara (se avisa en el estado);
        solo falla con ValueError si el vástago no toca la cara (quedaría suelto). `None` vuelve al automático."""
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        if point_world is None:
            spec.pin_center = None
        else:
            prev = self.pin_preview(name)
            uv = prev.project(point_world)
            _inside, margin = prev.fit(uv, self.pin_spec(name).shaft_d)
            if margin <= -self.pin_spec(name).shaft_d:        # centro a más de un diámetro del borde: ni lo roza
                raise ValueError("el vástago no toca la cara de contacto: el pasador quedaría suelto")
            spec.pin_center = tuple(float(x) for x in prev.plane.to_world(uv[None])[0])
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
