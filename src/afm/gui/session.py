"""Modelo de documento de la interfaz: una figura cargada + su proyecto. No depende de GTK.

Marcos de coordenadas (igual que el CLI, ver `afm.cli.run`):

* ``figure``: STL reparado, normalizado a ``ref_height`` mm, Z arriba, con el frente girado a +Y.
  Sobre esta malla se elige el plano de simetría.
* ``work``: ``figure`` con el plano de simetría llevado a x = 0. En este marco se definen los planos
  de silueta, los polígonos y los recortes (así los consume ``pipeline.build``).
"""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass

import numpy as np
import trimesh

from .. import meshio, pipeline as P, solids as S
from ..head import HeadPinPreview, HeadSpec, head_pin_preview, neck_plane, split_head
from ..waist import WaistSpec, waist_plane
from ..joints import JOINT_KIND_BY_LIMB, JointSpec, min_shoulder
from ..limbs import CROSS_THICKNESS_MM, LimbCut, LimbSpec, PinPreview, check_limb_plane, contact_preview, effective_cut_offset, plane_is_lateral, plane_on_symmetry
from ..params import PinSpec

from ..planes import Plane
from ..project import Project

ARM_SLAB_MM = 12 * 1.653      # losa del brazo: 12 mm del tutorial a la escala final (plan §5.1)
from ..trims import Trim

FLIP_Z = np.array([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float)


def _raise_top(poly: np.ndarray, top: float | None, tol: float = 0.35) -> np.ndarray:
    """Lleva a la cota `top` (si es más alta) los vértices que forman el borde superior del polígono."""
    if top is None:
        return poly
    poly = np.array(poly, float)
    zmax = poly[:, 2].max()
    if top <= zmax + 1e-6:
        return poly
    poly[poly[:, 2] >= zmax - tol, 2] = top
    return poly


TORSO_PLACES = ("torso", "pelvis")        # lugares de catálogo que no son un miembro del modelo: el torso y la pelvis


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
    def load(self, path: str, crown_z: float | None = None, progress=None) -> meshio.LoadReport:
        """Carga y repara el STL; ``crown_z`` en unidades del archivo (None = punto más alto)."""
        mesh, rep = meshio.load_figure(path, self.project.params.ref_height, crown_z, progress=progress)
        self._zmin_file = rep.zmin_file
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

    REF_HEIGHT_RANGE = (20.0, 400.0)

    def set_ref_height(self, mm: float) -> float:
        """Cambia la altura de referencia (pies → coronilla, mm) y reescala todo en consecuencia.

        La malla se reescala por k = nueva / actual (la cota de la coronilla, en unidades del archivo, no cambia).
        Los planos, siluetas, recortes, centros de pasador y ejes de articulación del proyecto están en mm de la
        figura: se multiplican por el mismo k para que sigan sobre las mismas zonas. Las medidas absolutas de
        impresión (pasadores, holgura, rebaba, clic, bisagras) NO se escalan. Invalida la figura generada.
        Devuelve k (1.0 si no cambió nada)."""
        mm = float(mm)
        lo, hi = self.REF_HEIGHT_RANGE
        if not np.isfinite(mm) or not (lo <= mm <= hi):
            raise ValueError(f"La altura tiene que estar entre {lo:g} y {hi:g} mm")
        old = float(self.project.params.ref_height)
        k = mm / old
        if abs(k - 1.0) < 1e-9:
            return 1.0
        self.project.params.ref_height = mm
        if self.base is not None:
            self.base = self.base.copy()
            self.base.apply_scale(k)
            self.report.scale *= k
        self.project.scale_geometry(k)
        self.outcome = None
        self._rebuild_frames()
        return k

    def set_front(self, front: str) -> None:
        """'+Y' o '-Y' según hacia dónde mira la figura en el archivo. Invalida simetría, miembros y recortes."""
        if front not in ("+Y", "-Y"):
            raise ValueError("front debe ser '+Y' o '-Y'")
        if front == self.project.front:
            return
        self.project.front = front
        self.project.symmetry = None
        self.project.limbs, self.project.trims, self.project.head, self.project.waist = [], [], None, None
        self.project.part_scales.clear()
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

    def line_cut_x(self, place: str) -> float | None:
        """Distancia al plano de simetría (mm) a la que corta la pieza de catálogo elegida en `place`: es la x del centro
        de su puerto (los puertos de hombro y cadera son laterales). None si no hay pieza, no hay catálogo o el puerto de
        la pieza no es lateral (su corte no se puede reproducir con un desplazamiento)."""
        ref = self.project.parts.get(place)
        spec = self.limb(place)
        if ref is None or spec is None or not self.catalog_root:
            return None
        from ..assembly import LIMB_PART

        try:
            port = self._catalog().meta(ref.ref).puertos.get(LIMB_PART[spec.kind][1])
        except Exception:                           # noqa: BLE001 — la consulta es informativa
            return None
        if port is None or abs(float(port.frame.axis[0])) < 0.95:
            return None
        return abs(float(port.frame.center[0]))

    def align_cut_to_line(self, place: str) -> float:
        """«Alinear el corte a la línea»: mueve el corte de `place` a la misma distancia del plano de simetría que el de la
        pieza de catálogo elegida, para que las caras de unión coincidan. Devuelve el desplazamiento nuevo (mm)."""
        spec = self.limb(place)
        if spec is None:
            raise ValueError(f"«{place}» no es un miembro del modelo")
        if place not in self.project.parts:
            raise ValueError(f"«{place}» usa la pieza del modelo: elegí una pieza de catálogo para alinear a su corte")
        x = self.line_cut_x(place)
        if x is None:
            raise ValueError(f"el corte de la pieza de «{place}» no es lateral: no se reproduce con un desplazamiento "
                             "(hay que alinear el plano a mano)")
        self.set_limb_offset(place, x)            # también valida que el plano esté sobre la simetría
        return x

    def fix_limb_extrude(self, name: str) -> bool:
        """Si el plano de `name` está de costado pero la extrusión es simétrica (proyectos guardados antes
        de este cambio), pasa a «outward» y descarta el pasador elegido (estaba sobre una cara que no era la
        de corte). Devuelve True si cambió algo."""
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        if spec.kind == "leg" and spec.cross is not None and spec.cross.valid() \
                and not plane_is_lateral(spec.cross.plane, spec.contact_axis):
            return False        # pierna con 2.º corte de frente: el principal DEBE atravesar el cuerpo (ver set_limb_cross)
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

    # ------------------------------------------------- segundo corte (de frente / de costado)
    def cross_plane(self, name: str) -> Plane:
        """Plano donde se dibuja el segundo corte de `name`: el que ya tiene, o el perpendicular al principal.
        Si el principal es de costado (el habitual, 3 puntos en el hombro/cadera) es el plano frontal, el que
        divide la figura en atrás/adelante; si el principal es frontal, el segundo es de costado."""
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        if spec.cross is not None:
            return spec.cross.plane
        c = self.work.bounds.mean(axis=0) if self.work is not None else np.zeros(3)
        if plane_is_lateral(spec.plane, spec.contact_axis):
            return Plane.from_normal((0.0, c[1], 0.0), (0.0, 1.0, 0.0), "frente")
        return Plane.from_normal((0.0, c[1], c[2]), (1.0, 0.0, 0.0), "costado")

    def set_limb_cross(self, name: str, polygon_world) -> None:
        """Guarda el polígono del segundo corte de `name` (sobre `cross_plane`). El miembro pasa a ser lo que
        queda dentro de los dos prismas. En una pierna con plano de costado, la cara de la cadera la define
        el borde del polígono de frente (escalón columna + cadera) y no el plano de costado: el principal pasa
        a atravesar todo el cuerpo, si no la columna del muslo (x menor que el plano) se quedaría en el torso."""
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        poly = np.asarray(polygon_world, float)
        if poly.ndim != 2 or poly.shape[1] != 3:
            raise ValueError("El polígono debe ser Nx3")
        spec.cross = LimbCut(self.cross_plane(name), poly)
        if (spec.kind == "leg" and spec.extrude == "outward" and plane_is_lateral(spec.plane, spec.contact_axis)
                and not plane_is_lateral(spec.cross.plane, spec.contact_axis)):
            spec.extrude, spec.thickness, spec.pin_center = "symmetric", CROSS_THICKNESS_MM, None
        self.outcome = None

    @staticmethod
    def _other_top(spec: LimbSpec, cross: bool) -> float | None:
        """Cota z más alta de la otra silueta del miembro (la de costado si `cross`, la del 2.º corte si no)."""
        other = (spec.polygon if cross else None if spec.cross is None else spec.cross.polygon)
        other = np.asarray(other if other is not None else [], float)
        return float(other[:, 2].max()) if other.ndim == 2 and len(other) >= 3 else None

    def raise_cross_top(self, name: str) -> float | None:
        """Sube el borde superior del 2.º corte hasta el de la silueta principal y devuelve la nueva cota
        (None si no hay nada que subir). Así el escalón de la cadera no limita la altura de la cara de
        contacto, y con ella la del hueco y la del pasador: el recorte de frente solo debe sacar la columna
        del muslo y la rebaba, la altura la marca la silueta de costado."""
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        if spec.cross is None or not spec.cross.valid():
            return None
        top = self._other_top(spec, cross=True)
        if top is None:
            return None
        new = _raise_top(np.asarray(spec.cross.polygon, float), top)
        if np.allclose(new, spec.cross.polygon):
            return None
        spec.cross = LimbCut(spec.cross.plane, new, spec.cross.thickness)
        self.outcome = None
        return top

    def clear_limb_cross(self, name: str) -> bool:
        """Quita el segundo corte. Devuelve True si había uno."""
        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        if spec.cross is None:
            return False
        spec.cross = None
        if spec.kind == "leg" and spec.extrude == "symmetric" and plane_is_lateral(spec.plane, spec.contact_axis):
            spec.extrude, spec.thickness, spec.pin_center = "outward", ARM_SLAB_MM, None   # como `add_limb`
        self.outcome = None
        return True

    def _proposal(self, name: str, crotch_z: float | None):
        """Región (x–z, polígono Nx2) que propone `autosil` para el miembro."""
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
        return p2

    def propose_silhouette(self, name: str, crotch_z: float | None = None, cross: bool = False) -> np.ndarray:
        """Propone la silueta del miembro ``name`` desde cortes horizontales de ``work`` (`afm.autosil`) y la
        guarda en su plano (o, con ``cross``, en el del segundo corte). ``crotch_z`` (solo piernas) fuerza la
        altura de la entrepierna. Falla con ValueError si no se encuentra el miembro en la malla.

        La propuesta es una región de frente (x–z). En el plano frontal se usa tal cual (con su escalón de
        cadera); en cualquier otro se proyecta el contorno del cuerpo dentro de la región (`outline_on_plane`)."""
        from .. import autosil

        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        p2 = self._proposal(name, crotch_z)
        plane = self.cross_plane(name) if cross else spec.plane
        if abs(float(plane.normal[1])) > 0.999:             # frontal: la región de frente es la silueta
            poly = np.array([[x, plane.origin[1], z] for x, z in p2], float)
            if cross and spec.kind == "leg":
                # El escalón de cadera no debe limitar la altura de la cara de contacto: sube hasta el borde
                # superior de la silueta de costado (que es la que manda), igual que `raise_cross_top`.
                poly = _raise_top(poly, self._other_top(spec, cross=True))
        else:
            poly = autosil.outline_on_plane(self.work, p2, plane)
        if cross:
            self.set_limb_cross(name, poly)
        else:
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
               spec.plane.normal.tobytes(), spec.thickness,
               None if spec.cross is None else (np.asarray(spec.cross.polygon).tobytes(), spec.cross.plane.origin.tobytes(),
                                                spec.cross.plane.normal.tobytes(), spec.cross.thickness), spec.extrude, spec.cut_offset, tuple(spec.contact_axis), spec.burr,
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

    # --------------------------------------------------- articulaciones (rodilla / codo)
    def joint(self, name: str) -> JointSpec | None:
        return next((j for j in self.project.joints if j.name == name), None)

    def joint_of_limb(self, limb: str) -> JointSpec | None:
        return next((j for j in self.project.joints if j.limb == limb), None)

    @staticmethod
    def _tilt_axis(tilt_deg: float) -> tuple:
        """Eje visto de frente: gira alrededor de Y (0 = a lo largo de X; positivo = z sube con x), como la
        rodilla (≈ 14°) y el codo (≈ 9,5°) de la figura de referencia."""
        t = np.radians(float(tilt_deg))
        return (float(np.cos(t)), 0.0, float(np.sin(t)))

    @staticmethod
    def plane_from_lines(upper, lower):
        """Plano de una articulación a partir de dos líneas dibujadas sobre la vista frontal.

        `upper = (P1, P2)` va de arriba hasta el codo (o la rodilla) siguiendo el centro del brazo (o del muslo);
        `lower = (P3, P4)` va del codo hacia abajo siguiendo el antebrazo (o la pantorrilla). Solo cuentan x y z de cada
        punto (vista de frente). El corte pasa por el codo (punto medio de P2 y P3) y sigue la bisectriz de las dos
        direcciones, de modo que las dos piezas encajan a ras cuando el miembro está estirado, y el eje de la bisagra
        queda perpendicular a esa bisectriz (misma inclinación que el corte).

        Devuelve `(plano, codo, ángulo_línea_superior, ángulo_línea_inferior, subida)`: los ángulos son los grados que
        se abre cada línea hacia +X al bajar (negativos hacia −X) y `subida` es la dirección de la línea superior hacia
        arriba (por donde `add_joint` lleva el eje desde el codo)."""
        def direction(p, q, what):
            p, q = np.asarray(p, float), np.asarray(q, float)
            d = np.array([q[0] - p[0], 0.0, q[2] - p[2]])
            length = float(np.linalg.norm(d))
            if length < 2.0:
                raise ValueError(f"la línea del {what} es demasiado corta vista de frente (menos de 2 mm)")
            d /= length
            if d[2] > 0:
                d = -d                                    # siempre de arriba hacia abajo
            if -d[2] < 0.5:
                raise ValueError(f"la línea del {what} tiene que ir de arriba hacia abajo (a menos de 60° de la vertical)")
            return d

        u = direction(*upper, "brazo / muslo")
        v = direction(*lower, "antebrazo / pantorrilla")
        w = u + v
        w /= np.linalg.norm(w)
        opening = lambda d: float(np.degrees(np.arctan2(d[0], -d[2])))
        elbow = (np.asarray(upper[1], float) + np.asarray(lower[0], float)) / 2.0
        plane = Plane.from_normal(elbow, -w, "articulación")                # normal hacia arriba (lado proximal)
        return plane, elbow, opening(u), opening(v), -u

    @staticmethod
    def line_opening_deg(p, q) -> float | None:
        """Grados que se abre hacia +X al bajar la línea P→Q vista de frente, o None si no sirve para una articulación
        (menos de 2 mm de largo o a más de 60° de la vertical)."""
        dx, dz = float(q[0] - p[0]), float(q[2] - p[2])
        if dz > 0:
            dx, dz = -dx, -dz
        length = float(np.hypot(dx, dz))
        if length < 2.0 or -dz / length < 0.5:
            return None
        return float(np.degrees(np.arctan2(dx, -dz)))

    @staticmethod
    def plane_front_tilt_deg(normal) -> float:
        """Inclinación (vista de frente) del plano de una articulación: ángulo de su traza en XZ respecto de X, con el
        mismo signo que `_tilt_axis` (z sube con x). Un plano horizontal da 0; se limita a ±45° como el control."""
        n = np.asarray(normal, float)
        n = n / np.linalg.norm(n)
        if n[2] < 0:
            n = -n
        t = float(np.degrees(np.arctan2(-n[0] * n[2], 1.0 - n[0] ** 2)))
        return float(np.clip(t, -45.0, 45.0)) + 0.0

    @staticmethod
    def _axis_over_plane(n, base, h: float, lift_dir=None) -> np.ndarray:
        """Centro del eje: `h` mm sobre el plano (a lo largo de la normal `n`), subiendo desde `base` por `lift_dir`."""
        lift = np.array([0.0, 0.0, 1.0]) if lift_dir is None else np.asarray(lift_dir, float) / np.linalg.norm(lift_dir)
        if lift @ n < 0.5:
            raise ValueError("la dirección del miembro es casi paralela al plano de la articulación")
        return np.asarray(base, float) + lift * (h / float(lift @ n))

    def retarget_joint_lines(self, name: str, upper, lower) -> JointSpec:
        """Rehace el corte y el eje de una articulación creada con 2 líneas a partir de las líneas corregidas (por
        ejemplo desde el costado): conserva el rango, la bisagra y los nombres; el eje vuelve a calcularse como al
        crearla (sobre el codo, subiendo por la línea del brazo; (y, z) salen de los puntos corregidos)."""
        j = self.joint(name)
        if j is None:
            raise KeyError(name)
        plane, elbow, _a_up, _a_down, lift = self.plane_from_lines(upper, lower)
        n = np.asarray(plane.normal, float)
        n = n / np.linalg.norm(n)
        if n[2] < 0:
            n = -n
        if abs(n[2]) < 0.5:
            raise ValueError("el plano de la articulación tiene que ser casi horizontal (corta el miembro a lo largo)")
        h = min_shoulder(self.project.params.hinge_pin, self.project.params)
        base = elbow - n * float((elbow - plane.origin) @ n)
        j.plane = plane
        j.axis_center = tuple(float(x) for x in self._axis_over_plane(n, base, h, lift))
        j.axis_dir = self._tilt_axis(self.plane_front_tilt_deg(n))
        self.outcome = None
        return j

    def add_joint(self, limb: str, plane: Plane, anchor=None, tilt_deg: float | None = None,
                  lift_dir=None) -> JointSpec:
        """Parte `limb` por `plane` en proximal y distal (rodilla de una pierna, codo de un brazo).

        El eje de la bisagra arranca en `anchor` (por defecto el origen del plano) llevado hacia el lado proximal
        (arriba) lo justo para que quede pared alrededor del bulón (`joints.min_shoulder`); ajustalo con
        `set_joint_axis`. `tilt_deg` gira el eje vista de frente, alrededor de Y (0 = a lo largo de X, positivo = z
        sube con x). Sin `tilt_deg` el eje sigue la inclinación del plano (un brazo que cuelga en diagonal se corta
        con un plano perpendicular a él, y la bisagra queda perpendicular al brazo: no hace falta clavar los 3 clics
        a la misma altura). `lift_dir` (vector que apunta hacia el lado proximal; por defecto vertical) es la
        dirección por la que el eje sube desde `anchor`: con las 2 líneas es la línea del brazo, así el eje queda sobre
        el trayecto del miembro y no en vertical sobre el codo."""
        spec = self.limb(limb)
        if spec is None:
            raise KeyError(limb)
        if self.joint_of_limb(limb) is not None:
            raise ValueError(f"«{limb}» ya tiene una articulación: quitala primero")
        kind = JOINT_KIND_BY_LIMB[spec.kind]
        n = np.asarray(plane.normal, float)
        n = n / np.linalg.norm(n)
        if n[2] < 0:
            n = -n                                            # lado proximal = arriba (figura de pie, Z arriba)
        if abs(n[2]) < 0.5:
            raise ValueError("el plano de la articulación tiene que ser casi horizontal (corta el miembro a lo largo)")
        p = self.project.params
        h = min_shoulder(p.hinge_pin, p)
        base = np.asarray(plane.origin if anchor is None else anchor, float)
        base = base - n * float((base - plane.origin) @ n)    # sobre el plano
        # El eje queda h por encima del plano (medido a lo largo de la normal) pero verticalmente sobre `anchor`: con
        # el plano inclinado, subir por la normal corría el eje en X y «Colocar eje» no puede corregir la X.
        center = self._axis_over_plane(n, base, h, lift_dir)
        name = "rodilla" if kind == "knee" else "codo"
        if tilt_deg is None:
            tilt_deg = self.plane_front_tilt_deg(n)
        j = JointSpec(name=name, limb=limb, kind=kind, plane=plane,
                      axis_center=tuple(float(x) for x in center), axis_dir=self._tilt_axis(tilt_deg))
        self.project.joints.append(j)
        self.outcome = None
        return j

    def set_joint_axis(self, name: str, point_world=None, tilt_deg: float | None = None) -> JointSpec:
        """Mueve el eje de la bisagra: `point_world` fija (y, z) del eje (la x se conserva: el eje atraviesa el
        miembro), `tilt_deg` lo inclina. Falla con ValueError si queda a menos de 0,5 mm del lado proximal del plano."""
        j = self.joint(name)
        if j is None:
            raise KeyError(name)
        c = np.asarray(j.axis_center, float)
        if point_world is not None:
            pt = np.asarray(point_world, float)
            c = np.array([c[0], pt[1], pt[2]])
        n = np.asarray(j.plane.normal, float)
        n = n / np.linalg.norm(n)
        if n[2] < 0:
            n = -n
        h = float((c - np.asarray(j.plane.origin, float)) @ n)
        if h < 0.5:
            raise ValueError(f"el eje tiene que quedar al menos 0,5 mm arriba del plano de articulación (queda a {h:.2f} mm)")
        j.axis_center = tuple(float(x) for x in c)
        if tilt_deg is not None:
            j.axis_dir = self._tilt_axis(tilt_deg)
        self.outcome = None
        return j

    def set_joint_range(self, name: str, lo: float | None, hi: float | None) -> JointSpec:
        """Rango de flexión (°, hiperextensión negativa). `None, None` vuelve al de los parámetros."""
        j = self.joint(name)
        if j is None:
            raise KeyError(name)
        if lo is None and hi is None:
            j.flex_range = None
        else:
            lo, hi = float(lo), float(hi)
            if not (-45.0 <= lo < hi <= 150.0):
                raise ValueError("el rango tiene que cumplir −45° ≤ mínimo < máximo ≤ 150°")
            j.flex_range = (lo, hi)
        self.outcome = None
        return j

    def remove_joint(self, name: str) -> None:
        self.project.joints = [j for j in self.project.joints if j.name != name]
        self.outcome = None

    def remove_limb(self, name: str) -> None:
        self.project.joints = [j for j in self.project.joints if j.limb != name]
        self.project.limbs = [l for l in self.project.limbs if l.name != name]
        self.project.part_scales.pop(name, None)
        self.project.trims = [t for t in self.project.trims if t.target != name]
        self.outcome = None

    # ----------------------------------------------------------------- cabeza
    def set_head(self, plane: Plane) -> HeadSpec:
        """Define (o reemplaza) la cabeza articulada con el plano del cuello (3 puntos). Rechaza un plano casi
        vertical o girado de costado; el pasador se propone solo (círculo inscrito de la cara del cuello)."""
        old = self.project.head
        spec = HeadSpec(plane) if old is None else dataclasses.replace(old, plane=plane, pin_center=None)
        neck_plane(spec)                      # valida
        self.project.head = spec
        self.outcome = None
        return spec

    def set_head_replacement(self, path: str, yaw: float = 0.0, up: str = "+Z", rx: float = 0.0, ry: float = 0.0,
                             scale: float = 1.0, cut_points=None) -> dict:
        """Usa otra cabeza (STL) en lugar de la original. Se descarta la original, no se simetriza nada y la nueva se
        escala (uniforme) para que su base iguale la del cuello. Controles: eje «arriba» + giros `rx`, `ry`, `yaw` (°),
        `scale` (factor manual sobre la escala automática) y `cut_points` (3 puntos, en coordenadas del STL, del plano
        que corta la base). Valida ajustándola ya sobre el cuerpo actual y devuelve los datos del ajuste (escala,
        coincidencia de base, alto…). Si falla no cambia nada."""
        head = self.project.head
        if head is None:
            raise ValueError("Primero definí el plano del cuello (la cabeza que se reemplaza)")
        if self.work is None:
            raise ValueError("Abrí un STL")
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise ValueError(f"No existe el archivo «{path}»")
        spec = dataclasses.replace(head, replacement_path=path, replacement_yaw=float(yaw), replacement_up=up,
                                   replacement_rx=float(rx), replacement_ry=float(ry), replacement_scale=float(scale),
                                   replacement_cut_points=None if cut_points is None else
                                   tuple(tuple(map(float, q)) for q in cut_points))
        return self._apply_head_spec(spec)

    def _apply_head_spec(self, spec: HeadSpec) -> dict:
        split = split_head(S.to_manifold(self.work), spec, self.project.params)     # si falla no cambia nada
        self.project.head = spec
        self._head_fit = (self._head_fit_key(spec), S.to_trimesh(split.head), split.replacement)
        self.outcome = None
        return split.replacement

    def set_head_cut_plane(self, world_points) -> dict:
        """Corta la base de la cabeza de reemplazo con el plano de 3 puntos elegidos sobre ella (coordenadas del
        mundo, es decir sobre la cabeza ya apoyada en el cuello). Se guardan en coordenadas del STL importado, así el
        plano acompaña a la malla si después se cambia la orientación o la escala. Si no sirve no cambia nada."""
        head = self.project.head
        prev = self.head_replacement_preview()
        if head is None or prev is None:
            raise ValueError("Primero cargá una cabeza de reemplazo (STL)")
        pts = np.asarray(world_points, float)
        if pts.shape != (3, 3):
            raise ValueError("Se necesitan 3 puntos")
        inv = np.linalg.inv(np.asarray(prev[1]["raw_a_mundo"], float))     # mundo -> coordenadas del STL
        raw = (inv[:3, :3] @ pts.T).T + inv[:3, 3]
        return self._apply_head_spec(dataclasses.replace(head, replacement_cut_points=tuple(map(tuple, raw))))

    def clear_head_cut_plane(self) -> bool:
        head = self.project.head
        if head is None or head.replacement_cut_points is None:
            return False
        self._apply_head_spec(dataclasses.replace(head, replacement_cut_points=None))
        return True

    def head_pin_preview(self) -> HeadPinPreview:
        """Cara del cuello y centro automático del pasador de la cabeza (en caché hasta que cambie el plano)."""
        head = self.project.head
        if head is None or self.work is None:
            raise ValueError("Primero definí el plano del cuello")
        key = (self._head_fit_key(head), head.auto)
        cache = getattr(self, "_head_pin_cache", None)
        if cache is not None and cache[0] == key:
            return cache[1]
        prev = head_pin_preview(split_head(S.to_manifold(self.work), head, self.project.params), head)
        self._head_pin_cache = (key, prev)
        return prev

    def set_head_pin(self, point_world) -> None:
        """Fija el centro del pasador de la cabeza en `point_world` (se proyecta sobre el plano del cuello, x = 0).
        Falla con ValueError si el vástago no toca la cara (quedaría suelto); `None` vuelve al automático."""
        head = self.project.head
        if head is None:
            raise ValueError("Primero definí el plano del cuello")
        if point_world is None:
            center = None
        else:
            prev = self.head_pin_preview()
            c = prev.project(point_world)
            shaft = self.project.params.neck_pin.shaft_d
            if prev.fit(c, shaft)[1] <= -shaft:
                raise ValueError("el vástago no toca la cara del cuello: el pasador quedaría suelto")
            center = tuple(float(x) for x in c)
        self.project.head = dataclasses.replace(head, pin_center=center)
        self.outcome = None

    def _head_fit_key(self, spec: HeadSpec):
        return (id(self.work), spec.replacement_path, spec.replacement_yaw, spec.replacement_up,
                spec.replacement_rx, spec.replacement_ry, spec.replacement_scale, spec.replacement_cut_points,
                spec.plane.origin.tobytes(), spec.plane.normal.tobytes())

    # ------------------------------------------------------------ mano estándar
    def extract_hand(self, points, invert: bool = False):
        """Corta la mano de la figura de trabajo por el plano de muñeca (3 puntos del marco de trabajo).

        Devuelve (mano en el marco local de la mano estándar, `WristSplit`). No cambia el proyecto."""
        from ..hand import extract_standard_hand

        if self.work is None:
            raise ValueError("Primero abrí una figura.")
        return extract_standard_hand(S.to_manifold(self.work), points, invert=invert)

    def save_hand(self, path: str, hand_local, split) -> dict:
        """Guarda la mano estándar (STL + `.mano.json`) con la altura de figura y el nombre del STL de origen."""
        import os

        from ..hand import hand_meta, save_standard_hand

        meta = hand_meta(split, height_mm=self.project.params.ref_height,
                         source=os.path.basename(self.stl_path) if self.stl_path else "")
        save_standard_hand(path, hand_local, meta)
        return meta

    # ------------------------------------------------- mano estándar (pegarla en este modelo)
    def set_hand(self, path: str, points, yaw: float = 0.0, invert: bool = False, join: str | None = None) -> dict:
        """Reemplaza la mano de este modelo por la mano estándar (STL) con el plano de muñeca de 3 puntos (marco de
        trabajo; vale cualquiera de los dos brazos). `join`: «solape» (soldada al brazo) o «giro» (pieza aparte con pasador,
        gira libre); None conserva la actual (o «solape»). Valida pegándola ya sobre la figura y devuelve el informe del
        pegado (radios de muñeca, escalón, avisos, pasador). Si falla (ValueError) no cambia nada."""
        from ..hand import DEFAULT_OVERLAP_MM, HandSpec

        if self.work is None:
            raise ValueError("Primero abrí una figura.")
        if self.project.symmetry is None:
            raise ValueError("Primero definí el plano de simetría: la mano se pega en un brazo y el otro sale por espejo.")
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise ValueError(f"No existe el archivo «{path}»")
        old = self.project.hand
        spec = HandSpec(path=path, points=tuple(tuple(float(x) for x in q) for q in points), yaw=float(yaw),
                        invert=bool(invert), overlap=old.overlap if old else DEFAULT_OVERLAP_MM,
                        join=join or (old.join if old else "solape"))
        return self._apply_hand_spec(spec)

    def adjust_hand(self, yaw: float | None = None, invert: bool | None = None, join: str | None = None) -> dict:
        """Cambia el giro (°) alrededor de la muñeca, el lado de la mano y/o la unión («solape» / «giro») de la mano de
        reemplazo ya elegida."""
        cur = self.project.hand
        if cur is None:
            raise ValueError("Primero elegí una mano de reemplazo (STL).")
        return self._apply_hand_spec(dataclasses.replace(
            cur, yaw=cur.yaw if yaw is None else float(yaw), invert=cur.invert if invert is None else bool(invert),
            join=cur.join if join is None else join))

    def clear_hand(self) -> bool:
        if self.project.hand is None:
            return False
        self.project.hand = None
        self._hand_fit = None
        self.outcome = None
        return True

    def _apply_hand_spec(self, spec) -> dict:
        fit = self._fit_hand(spec)                         # si falla no cambia nada
        self.project.hand = spec
        self._hand_fit = (self._hand_fit_key(spec), fit)
        self.outcome = None
        return fit["info"]

    def _fit_hand(self, spec) -> dict:
        """Pega la mano en la figura de trabajo (como hace `pipeline.build` sobre el lado +X) y arma lo que se dibuja:
        la figura sin la mano original, la mano nueva y el informe."""
        from ..hand import apply_hand

        full = S.to_manifold(self.work)
        cache = self.__dict__.setdefault("_hand_pin_cache", {})        # el giro de la mano no cambia el ajuste del pasador
        hb = apply_hand(full.trim_by_plane((1.0, 0.0, 0.0), 0.0), spec, self.project.params.ref_height,
                        self.project.params, cache)
        return {"figure": S.to_trimesh(S.difference(full, hb.original)), "hand": S.to_trimesh(hb.hand), "info": hb.info}

    def _hand_fit_key(self, spec):
        p = self.project.params
        return (id(self.work), spec.path, tuple(spec.points), spec.yaw, spec.invert, spec.overlap, spec.join,
                p.ref_height, p.clearance, p.min_wall, tuple(p.hand_pin.__dict__.values()))

    def hand_replacement_preview(self):
        """{"figure": figura sin la mano original, "hand": mano estándar puesta, "info": informe} o None.

        Es lo que se dibuja en la vista 3D: la figura generada usa exactamente esta mano. Va en caché mientras no
        cambien la malla, el STL, los puntos, el giro ni el lado."""
        spec = self.project.hand
        if spec is None or self.work is None:
            return None
        key = self._hand_fit_key(spec)
        cache = getattr(self, "_hand_fit", None)
        if cache is None or cache[0] != key:
            try:
                cache = self._hand_fit = (key, self._fit_hand(spec))
            except Exception:                              # archivo borrado o plano movido a un lugar sin mano
                self._hand_fit = None
                return None
        return cache[1]

    def head_replacement_preview(self):
        """(malla de la cabeza de reemplazo ya escalada y apoyada en el cuello, info del ajuste) o None.

        Es lo que se dibuja en la vista 3D: la figura generada usa exactamente esta cabeza. Se guarda en caché
        mientras no cambien la malla, el plano del cuello, el STL, el giro ni el eje «arriba»."""
        head = self.project.head
        if head is None or not head.replacement_path or self.work is None:
            return None
        key = self._head_fit_key(head)
        cache = getattr(self, "_head_fit", None)
        if cache is None or cache[0] != key:
            try:
                split = split_head(S.to_manifold(self.work), head, self.project.params)
            except Exception:                      # archivo borrado o plano movido a un lugar sin cabeza
                self._head_fit = None
                return None
            cache = self._head_fit = (key, S.to_trimesh(split.head), split.replacement)
        return cache[1], cache[2]

    def clear_head_replacement(self) -> bool:
        head = self.project.head
        if head is None or head.replacement_path is None:
            return False
        self.project.head = dataclasses.replace(head, replacement_path=None, replacement_yaw=0.0, replacement_up="+Z",
                                                replacement_rx=0.0, replacement_ry=0.0, replacement_scale=1.0,
                                                replacement_cut_points=None)
        self.outcome = None
        self._head_fit = None
        return True

    def remove_head(self) -> None:
        self.project.head = None
        self.project.trims = [t for t in self.project.trims if t.target != "cabeza"]
        self.outcome = None

    # ----------------------------------------------------------------- cintura
    def set_waist(self, plane: Plane) -> WaistSpec:
        """Define (o reemplaza) la cintura con un plano de 3 puntos. Rechaza un plano casi vertical o girado de
        costado; el pasador se propone solo (círculo inscrito de la cara del corte)."""
        old = self.project.waist
        spec = WaistSpec(plane) if old is None else dataclasses.replace(old, plane=plane, pin_center=None)
        waist_plane(spec)                     # valida
        self.project.waist = spec
        self.outcome = None
        return spec

    def remove_waist(self) -> None:
        self.project.waist = None
        self.project.part_scales.pop("pelvis", None)
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
        if self.catalog_only():
            return self.catalog_only_problems()          # la figura sale del catálogo: no hace falta el modelo
        if self.project.symmetry is None:
            out.append("Falta el plano de simetría")
        if not self.project.limbs and self.project.head is None and self.project.waist is None:
            out.append("Definí al menos un miembro (brazo o pierna), la cabeza o la cintura")
        for l in self.project.limbs:
            if len(l.polygon) < 3:
                out.append(f"{l.name}: dibujá la silueta (mínimo 3 puntos)")
        names = {l.name for l in self.project.limbs}
        for j in self.project.joints:
            if j.limb not in names:
                out.append(f"{j.name}: su miembro «{j.limb}» ya no existe")
        for place in self.project.parts:
            if place not in self.part_places():
                out.append(f"la pieza de catálogo de «{place}» apunta a un lugar que el modelo ya no tiene: quitala")
        if "pelvis" in self.project.parts and self.project.waist is None:
            out.append("la pelvis de catálogo necesita el plano de cintura del modelo (Planos → Cintura): define dónde se corta")
        hand = self.project.hand
        if hand is not None and not os.path.isfile(hand.path):
            out.append(f"la mano estándar «{hand.path}» ya no existe: elegí otra o quitá la mano de reemplazo")
        return out

    # ------------------------------------------------------------- catálogo de piezas (plan de piezas intercambiables, Fase 8)
    catalog_root: str | None = None          # carpeta del catálogo de la línea (no va en el .proyecto: ahí solo id y versión)

    def open_catalog(self, root: str):
        """Abre el catálogo de la línea en `root` y lo usa para elegir y armar piezas. Avisos en `catalog_warnings()`."""
        from ..catalog import Catalog, CatalogError

        try:
            cat = Catalog.open(root)
        except (CatalogError, OSError) as exc:
            raise ValueError(f"No se pudo abrir el catálogo «{root}»: {exc}") from exc
        self.catalog_root = cat.root
        self.outcome = None
        return cat

    def create_catalog(self, root: str, name: str | None = None, height_mm: float | None = None):
        """Equivalente de `afm catalogo crear RUTA --linea NOMBRE --altura MM`: crea el catálogo de una línea en la carpeta
        `root` (nueva o vacía) y lo deja abierto. Sin `name` usa el de la carpeta; sin `height_mm` la altura de la figura
        del proyecto; la boquilla y el perfil de parámetros son los de `afm catalogo crear` para la boquilla del proyecto."""
        from ..catalog import Catalog, CatalogError
        from ..profile import default_for_nozzle

        root = os.path.abspath(os.path.expanduser(root or ""))
        if not root or root == os.path.sep:
            raise ValueError("Elegí una carpeta para el catálogo.")
        if os.path.isdir(root) and os.listdir(root) and not os.path.isfile(os.path.join(root, "linea.json")):
            raise ValueError(f"La carpeta «{root}» no está vacía: elegí una nueva o vacía.")
        name = (name or os.path.basename(root)).strip()
        height = float(height_mm if height_mm is not None else self.project.params.ref_height)
        nozzle = self.project.params.nozzle
        try:
            cat = Catalog.create(root, name, height, nozzle, default_for_nozzle(nozzle))
        except (CatalogError, OSError) as exc:
            raise ValueError(f"No se pudo crear el catálogo «{root}»: {exc}") from exc
        self.catalog_root = cat.root
        self.outcome = None
        return cat

    def _catalog(self):
        if not self.catalog_root:
            raise ValueError("Primero abrí un catálogo de piezas.")
        from ..catalog import Catalog, CatalogError

        try:
            return Catalog.open(self.catalog_root)
        except (CatalogError, OSError) as exc:
            raise ValueError(f"No se pudo abrir el catálogo «{self.catalog_root}»: {exc}") from exc

    def catalog_warnings(self) -> list[str]:
        """Diferencias entre la línea del catálogo y esta figura que conviene mirar antes de elegir piezas."""
        if not self.catalog_root:
            return []
        cat, out = self._catalog(), []
        p, q = self.project.params, cat.params
        if abs(p.ref_height - float(cat.line.get("altura_mm", p.ref_height))) > 0.5:
            out.append(f"la figura mide {p.ref_height:g} mm y la línea «{cat.name}» {cat.line.get('altura_mm')} mm")
        if abs(p.clearance - q.clearance) > 1e-9:
            out.append(f"la holgura del proyecto ({p.clearance:g} mm) difiere de la de la línea ({q.clearance:g} mm): "
                       "al armar con piezas manda la del proyecto en «Generar» y la de la línea en «afm armar»")
        return out

    def part_places(self) -> list[str]:
        """Lugares donde se puede poner una pieza de catálogo: cada miembro del modelo, la cabeza (si tiene plano de cuello)
        y la mano."""
        return (list(TORSO_PLACES) + [l.name for l in self.project.limbs]
                + (["cabeza"] if self.project.head is not None else []) + ["mano"])

    def _place_types(self, place: str) -> tuple[str, ...]:
        """Tipos de pieza de catálogo que sirven en ese lugar. «torso»: entero (`torso`) o la parte de arriba
        (`torso_sup`, que va con una `pelvis`); «pelvis»: `pelvis`."""
        from ..assembly import LIMB_PART

        if place == "torso":
            return ("torso", "torso_sup")
        if place == "pelvis":
            return ("pelvis",)
        for l in self.project.limbs:
            if l.name == place:
                return (LIMB_PART[l.kind][0],)
        if place in ("cabeza", "mano"):
            return (place,)
        raise ValueError(f"«{place}» no es un lugar del modelo (hay {', '.join(self.part_places())})")

    def _place_type(self, place: str) -> str:
        return self._place_types(place)[0]

    def catalog_only(self) -> bool:
        """True si hay torso de catálogo elegido: entonces la figura sale ENTERA del catálogo (como `afm armar`) y el cuerpo
        del STL abierto solo sirve de referencia. Con un torso de catálogo no hay figura de origen a la que acoplar.
        La pelvis sola NO lo activa: reemplaza la del modelo y el torso de arriba sigue siendo el del modelo (hace falta su
        plano de cintura)."""
        return "torso" in self.project.parts

    def scale_places(self) -> list[str]:
        """Lugares cuya pieza se puede escalar por eje: torso, pelvis (con cintura), cabeza (con cuello) y los miembros."""
        return (["torso"] + (["pelvis"] if self.project.waist is not None else [])
                + (["cabeza"] if self.project.head is not None else []) + [l.name for l in self.project.limbs])

    def part_scale(self, place: str) -> tuple[float, float, float]:
        """Escala (sx, sy, sz) de la pieza de ese lugar; (1, 1, 1) si no se tocó."""
        from ..customize import scale_vector
        return tuple(float(x) for x in scale_vector(self.project.part_scales.get(place, 1.0)))

    def set_part_scale(self, place: str, factor) -> None:
        """Escala de la pieza de `place`. `factor`: un número (uniforme) o (sx, sy, sz) en X, Y, Z del modelo. Vale para la
        pieza que ocupa el lugar, sea la del modelo o la elegida del catálogo. Con un torso de catálogo toda la figura sale del
        catálogo y no hay piezas del modelo para escalar."""
        from ..assembly import AssemblyError
        from ..customize import normalize_scale
        if place not in self.scale_places():
            raise ValueError(f"«{place}»: definí su corte antes de escalar la pieza")
        if self.catalog_only():
            raise ValueError("Con un torso de catálogo la figura sale entera del catálogo: quitalo para escalar piezas del modelo")
        try:
            k = normalize_scale(factor)
        except AssemblyError as exc:
            raise ValueError(f"«{place}»: {exc}") from exc
        self.project.part_scales[place] = k       # también ×1: marca la figura como dividida en piezas
        self.outcome = None
        self._part_preview = None

    def divide_model(self) -> dict:
        probs = self.problems()
        if probs:
            raise ValueError("; ".join(probs))
        old = dict(self.project.part_scales)
        try:
            for place in self.scale_places():
                self.set_part_scale(place, old.get(place, 1.0))
            return self.preview_parts()
        except Exception:
            self.project.part_scales = old
            raise

    def catalog_choices(self, place: str) -> list:
        """Piezas del catálogo (última versión de cada una) que sirven en ese lugar."""
        cat = self._catalog()
        return [m for t in self._place_types(place) for m in cat.list(t)]

    def catalog_only_problems(self) -> list[str]:
        """Lo que impide armar la figura solo con piezas de catálogo (torso y pelvis incluidos)."""
        out = []
        parts = self.project.parts
        if not self.catalog_only():
            return out
        if not self.catalog_root:
            out.append("el torso de catálogo necesita el catálogo abierto")
            return out
        cat = self._catalog()
        tipo = {}
        for place in TORSO_PLACES:
            ref = parts.get(place)
            if ref is not None:
                try:
                    tipo[place] = cat.meta(ref.ref).tipo
                except Exception as exc:        # noqa: BLE001 — se informa como problema
                    out.append(f"«{place}»: {exc}")
        t, pel = tipo.get("torso"), tipo.get("pelvis")
        if t == "torso_sup" and "pelvis" not in parts:
            out.append("el torso elegido es solo la parte de arriba (torso_sup): elegí también una «pelvis»")
        elif t == "torso" and "pelvis" in parts:
            out.append("el torso elegido es entero: con «pelvis» hace falta un torso de arriba (torso_sup), o quitá la pelvis")
        if "pelvis" in parts and pel not in (None, "pelvis"):
            out.append(f"«pelvis» pide una pieza «pelvis» y se eligió una «{pel}»")
        seen: dict[str, str] = {}
        for l in self.project.limbs:
            if l.name in parts:
                host = {"arm": "brazo", "leg": "pierna"}[l.kind]
                if host in seen:
                    out.append(f"«{seen[host]}» y «{l.name}» son los dos {host}s de catálogo: con torso de catálogo se pone un "
                               f"{host} y se espeja; dejá la pieza del modelo en uno")
                seen[host] = l.name
        return out

    def _catalog_only_choices(self) -> dict:
        """`Project.parts` -> las claves que espera `synthesize_host` (`torso` | `torso_sup`, `pelvis`, `brazo`, `pierna`,
        `cabeza`, `mano`)."""
        resolved = self._resolved_parts() or {}
        out = {}
        for place, choice in resolved.items():
            if place == "torso" and choice.meta.tipo == "torso_sup":
                out["torso_sup"] = choice
            elif place in TORSO_PLACES or place in ("cabeza", "mano"):
                out[place] = choice
            else:
                spec = self.limb(place)
                out[{"arm": "brazo", "leg": "pierna"}[spec.kind]] = choice
        return out

    def set_part(self, place: str, ref: str):
        """Pone la pieza `ref` (`id` o `id@versión`) en ese lugar; guarda en el proyecto la versión elegida."""
        from ..catalog import CatalogError
        from ..project import PartRef

        tipos = self._place_types(place)
        cat = self._catalog()
        try:
            meta = cat.meta(ref)
        except CatalogError as exc:
            raise ValueError(str(exc)) from exc
        if meta.tipo not in tipos:
            raise ValueError(f"«{meta.ref}» es una pieza «{meta.tipo}» y «{place}» pide «{' o '.join(tipos)}»")
        self.project.parts[place] = PartRef(id=meta.id, version=meta.version, linea=cat.name)
        self.outcome = None
        return self.project.parts[place]

    def clear_part(self, place: str) -> bool:
        if self.project.parts.pop(place, None) is None:
            return False
        self.outcome = None
        return True

    def part_rows(self) -> list[dict]:
        """Una fila por lugar: la pieza de catálogo elegida (o None = la del modelo) y, si ya se generó la figura, el
        resultado de la compatibilidad del acople (`ok` / `aviso` / `no`, IoU, escalón)."""
        built = {}
        if self.outcome is not None:
            built = self.outcome.result.report.get("piezas", {})
        pre = self.part_preview_items()          # revisión previa vigente (sin generar), o {}
        rows = []
        for place in self.part_places():
            ref = self.project.parts.get(place)
            info = built.get(place) or {}
            row = {"lugar": place, "pieza": None if ref is None else ref.ref, "compat": info.get("compat"),
                   "avisos": info.get("avisos", []), "previa": False}
            pv = pre.get(place)
            if not info and pv is not None and ref is not None and pv.ref == ref.ref:
                row["compat"] = None if pv.compat is None else pv.compat.to_dict()
                row["avisos"] = pv.problems()
                row["previa"] = True
                row["nivel"] = pv.nivel
                row["conflictos"] = list(pv.conflicts)
                row["error"] = pv.error
            rows.append(row)
        return rows

    # ------------------------------------------------------------- revisión previa de las piezas (sin generar)
    _part_preview: tuple | None = None       # (firma, {lugar: preflight.PartPreview})

    def _preview_signature(self) -> str:
        """Todo lo que cambia el resultado de la revisión: el proyecto, la malla, el catálogo y las piezas elegidas."""
        import hashlib

        b = self.work.bounds if self.work is not None else None
        blob = json.dumps({"p": self.project.to_dict(), "n": None if self.work is None else len(self.work.faces),
                           "b": None if b is None else np.round(b, 3).tolist(), "c": self.catalog_root},
                          sort_keys=True, default=float, separators=(",", ":"))
        return hashlib.sha1(blob.encode()).hexdigest()

    def part_preview_items(self) -> dict:
        """Revisión previa vigente (vacía si cambió algo desde que se hizo)."""
        if self._part_preview is None or self.work is None:
            return {}
        sig, items = self._part_preview
        return items if sig == self._preview_signature() else {}

    def part_preview_fresh(self) -> bool:
        """True si ya se revisaron las piezas elegidas con el estado actual (o si no hay piezas que revisar)."""
        if not self.project.parts and not self.project.part_scales:
            return True
        items = self.part_preview_items()
        return bool(items) and set(self.project.parts).issubset(items)

    def preview_parts(self, progress=None) -> dict:
        """Revisa las piezas de catálogo elegidas SIN generar: la compatibilidad, el conflicto de rodilla/codo y la
        pieza ya colocada para dibujarla. Devuelve {lugar: PartPreview} y la deja guardada (`part_preview_items`)."""
        from ..preflight import preview_parts as _preview

        if self.work is None:
            raise ValueError("Primero abrí una figura.")
        from ..customize import needed, preview_model
        if needed(self.project):
            items = preview_model(S.to_manifold(self.work), self.project, self._resolved_parts())
            self._part_preview = (self._preview_signature(), items)
            return items
        if not self.project.parts:
            self._part_preview = None
            return {}
        if self.catalog_only():
            from ..preflight import preview_catalog_only

            probs = self.catalog_only_problems()
            if probs:
                raise ValueError("; ".join(probs))
            choices = self._catalog_only_choices()
            kinds = {"arm": "brazo", "leg": "pierna"}
            place_of = {kinds[l.kind]: l.name for l in self.project.limbs if l.name in self.project.parts}
            place_of.update({"torso_sup": "torso", "torso": "torso", "pelvis": "pelvis", "cabeza": "cabeza", "mano": "mano"})
            items = preview_catalog_only(self.project.params, choices, place_of)
            self._part_preview = (self._preview_signature(), items)
            return items
        if self.project.symmetry is None:
            raise ValueError("Falta el plano de simetría")
        parts = self._resolved_parts()
        items = _preview(self.project, parts, S.to_manifold(self.work), progress)
        self._part_preview = (self._preview_signature(), items)
        return items

    def resolve_joint_conflict(self, place: str, keep: str = "part") -> str:
        """Conflicto de rodilla/codo en `place`. `keep="part"`: se queda con la articulación de la pieza y se quita la del
        modelo; `keep="model"`: se queda con la del modelo y se vuelve a la pieza del modelo en ese lugar."""
        if keep not in ("part", "model"):
            raise ValueError(keep)
        if keep == "model":
            if not self.clear_part(place):
                raise ValueError(f"«{place}» ya usaba la pieza del modelo")
            return f"«{place}» vuelve a usar la pieza del modelo (queda la articulación del modelo)."
        js = [j for j in self.project.joints if j.limb == place]
        if not js:
            raise ValueError(f"«{place}» no tiene una articulación propia del modelo que quitar")
        for j in js:
            self.remove_joint(j.name)
        return f"Se quitó «{', '.join(j.name for j in js)}» del modelo: «{place}» usa la articulación de la pieza."

    def line_height(self) -> float | None:
        """Altura de figura (mm) de la línea del catálogo abierto, o None."""
        if not self.catalog_root:
            return None
        try:
            return float(self._catalog().line.get("altura_mm"))
        except (TypeError, ValueError):
            return None

    def catalog_add(self, prefix: str, places=None, wrist_points=None, labels=(), new_version: bool = False) -> dict:
        """«Catalogar esta figura»: saca las piezas crudas con puertos de la figura abierta (con los planos, siluetas y
        articulaciones ya definidos) y las guarda en el catálogo. Devuelve {"piezas": [PieceMeta], "avisos": [str]}."""
        from ..extract import ExtractError, extract_all

        if self.work is None:
            raise ValueError("Primero abrí una figura.")
        probs = self.problems()
        if probs:
            raise ValueError("; ".join(probs))
        cat = self._catalog()
        name = os.path.basename(self.stl_path or "figura")
        try:
            res = extract_all(S.to_manifold(self.work), self.project, prefix, places, wrist_points, False, figure=name)
        except ExtractError as exc:
            raise ValueError(str(exc)) from exc
        if not res.pieces:
            raise ValueError("No se extrajo ninguna pieza.")
        if self.project.part_scales:
            from ..assembly import PartChoice
            from ..customize import scale_choice
            places_by_type = {"torso": "torso", "torso_sup": "torso", "pelvis": "pelvis", "cabeza": "cabeza"}
            places_by_type.update({{"arm": "brazo", "leg": "pierna"}[l.kind]: l.name for l in self.project.limbs})
            for piece in res.pieces:
                place = places_by_type.get(piece.meta.tipo)
                k = self.project.part_scales.get(place, 1.0)
                adjusted = scale_choice(PartChoice(piece.meta, piece.mesh), k)
                piece.meta, piece.mesh = adjusted.meta, adjusted.mesh
        metas, avisos = [], list(res.warnings)
        for e in res.pieces:
            e.meta.etiquetas = list(labels)
            meta, warns = cat.add_piece(e.mesh, e.meta, new_version)
            metas.append(meta)
            avisos += warns
        return {"piezas": metas, "avisos": avisos}

    def _resolved_parts(self):
        """`Project.parts` -> piezas cargadas del catálogo abierto (o de la ruta por defecto de su línea)."""
        if not self.project.parts:
            return None
        from ..assembly import AssemblyError, resolve_parts

        try:
            return resolve_parts(self.project.parts, self.catalog_root)
        except AssemblyError as exc:
            raise ValueError(str(exc)) from exc

    def build(self, progress=None) -> BuildOutcome:
        probs = self.problems()
        if probs:
            raise ValueError("; ".join(probs))
        from ..customize import needed, build_model
        if needed(self.project):
            res, limbs = build_model(S.to_manifold(self.work), self.project, self._resolved_parts(), progress)
            val = P.validate(res, self.project.params, limbs, joints=res.joint_specs)
            self.outcome = BuildOutcome(res, val)
            return self.outcome
        if self.catalog_only():
            return self._build_catalog_only(progress)
        parts = self._resolved_parts()
        body = S.to_manifold(self.work)
        res = P.build(body, self.project.limbs, self.project.params, self.project.symmetry,
                      progress=progress, trims=self.project.trims, head=self.project.head,
                      joints=self.project.joints, waist=self.project.waist, hand=self.project.hand, parts=parts)
        val = P.validate(res, self.project.params, self.project.limbs, joints=self.project.joints)
        self.outcome = BuildOutcome(res, val)
        return self.outcome

    def _build_catalog_only(self, progress=None) -> BuildOutcome:
        """Figura armada solo con piezas de catálogo (torso incluido): lo mismo que `afm armar` (recipe.assemble)."""
        from ..assembly import AssemblyError, synthesize_host

        p = self.project.params
        try:
            host = synthesize_host(self._catalog_only_choices(), p)
            res = P.build(host.body, host.limbs, p, None, True, progress, None, host.head, None, host.waist, None,
                          host.parts, host_faces=host.faces)
        except AssemblyError as exc:
            raise ValueError(str(exc)) from exc
        val = P.validate(res, p, host.limbs, joints=res.joint_specs)
        self.outcome = BuildOutcome(res, val)
        return self.outcome

    def export(self, outdir: str, name: str | None = None, orient: bool = False, max_angle: float = 45.0,
               flat: bool = False) -> dict:
        if self.outcome is None:
            raise ValueError("Primero generá la figura")
        name = name or (os.path.splitext(os.path.basename(self.stl_path or "figura"))[0])
        os.makedirs(outdir, exist_ok=True)
        files = P.export(self.outcome.result, outdir, name, orient=orient, max_angle=max_angle,
                         flat=self.project.params.flat_depth if flat else None, params=self.project.params)
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
