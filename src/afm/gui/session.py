"""Modelo de documento de la interfaz: una figura cargada + su proyecto. No depende de GTK.

Marcos de coordenadas (igual que el CLI, ver `afm.cli.run`):

* ``figure``: STL reparado, normalizado a ``ref_height`` mm, Z arriba, con el frente girado a +Y.
  Sobre esta malla se elige el plano de simetría.
* ``work``: ``figure`` con el plano de simetría llevado a x = 0. En este marco se definen los planos
  de silueta, los polígonos y los recortes (así los consume ``pipeline.build``).
"""
from __future__ import annotations

import copy
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


class CatalogNeeded(FileNotFoundError):
    """El proyecto es de una figura toda de catálogo y no se encontró la carpeta del catálogo de su línea."""

    def __init__(self, line: str, last_root: str | None = None):
        self.line, self.last_root = line, last_root
        where = f" (la última vez estaba en {last_root})" if last_root else ""
        super().__init__(f"Este proyecto se armó con el catálogo de la línea «{line}»{where} y no lo encuentro: "
                         "elegí la carpeta del catálogo.")


@dataclass
class BuildOutcome:
    result: P.BuildResult
    validation: dict


def json_sig(obj) -> str:
    """Firma de texto de un dict/lista con arreglos de numpy (para saber si algo cambió)."""
    return json.dumps(obj, sort_keys=True, default=lambda o: np.asarray(o).tolist())


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
        self.catalog_origin: dict | None = None      # bloque `catalogo_solo` del .proyecto: figura toda de catálogo, sin STL
        self._catalog_joint_cache: dict | None = None  # articulaciones de las piezas de catálogo ya colocadas (ver `catalog_joints`)

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

    # ------------------------------------------------- mira del corte (cinta de calor del desplazamiento)
    def _catalog_stamp(self) -> float:
        """Cambia cuando se agrega / quita una pieza del catálogo abierto (para descartar la cinta medida con el anterior)."""
        stamp = 0.0
        try:
            for name in ("linea.json", "piezas"):
                stamp = max(stamp, os.path.getmtime(os.path.join(self.catalog_root, name)))
        except OSError:
            pass
        return stamp

    def cut_aid(self, name: str):
        """`cut_aid.CutAid` del miembro `name`: mide, sin armar la figura, cuánto del catálogo acepta el corte a cada distancia del
        plano de simetría. Se guarda mientras no cambien la malla, la silueta, el plano, los parámetros ni el catálogo (el propio
        desplazamiento NO es parte de la clave: es justo lo que se barre). Falla con ValueError si no se puede medir."""
        from ..cut_aid import CutAid, unsupported

        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        if self.work is None:
            raise ValueError("Abrí un STL")
        if self.project.symmetry is None:
            raise ValueError("Falta el plano de simetría")
        if not self.catalog_root:
            raise ValueError("Abrí un catálogo para medir el corte contra sus piezas")
        why = unsupported(spec)
        if why:
            raise ValueError(why)
        params = self.project.params
        stl = os.path.basename(self.stl_path or "") or None
        key = (id(self.work), np.asarray(spec.polygon).tobytes(), spec.plane.origin.tobytes(), spec.plane.normal.tobytes(),
               spec.thickness, spec.kind, spec.extrude, tuple(spec.contact_axis), spec.burr, params.burr, params.ref_height,
               spec.auto, tuple(spec.cap_dir), self.catalog_root, self._catalog_stamp(), stl,
               None if spec.cross is None else (np.asarray(spec.cross.polygon).tobytes(), spec.cross.plane.origin.tobytes(),
                                                spec.cross.plane.normal.tobytes(), spec.cross.thickness))
        cache = getattr(self, "_cut_aid_cache", None)
        if cache is not None and cache[0] == (name, key):
            return cache[1]
        if getattr(self, "_half_key", None) != id(self.work):
            self._half = S.to_manifold(self.work).trim_by_plane((1.0, 0.0, 0.0), 0.0)
            self._half_key = id(self.work)
        aid = CutAid(self._half, spec, self._catalog(), params, height_mm=params.ref_height, exclude_figure=stl)
        self._cut_aid_cache = ((name, key), aid)
        return aid

    # ------------------------------------------------------- mira del cuello y de la cintura
    def _joint_aid_blocked(self, lugar: str) -> str | None:
        """Por qué NO hay mira del plano del cuello («cuello») o de la cintura («cintura») (None = hay)."""
        if self.work is None:
            return "Abrí un STL"
        if not self.catalog_root:
            return "Abrí un catálogo para medir el plano contra sus piezas"
        if lugar == "cuello" and self.project.head is None:
            return "Primero definí el plano del cuello"
        if lugar == "cintura" and self.project.waist is None:
            return "Primero definí el plano de la cintura"
        return None

    def joint_aid(self, lugar: str):
        """`neck_waist_aid.AxisCutAid` del plano del cuello o de la cintura (en caché mientras no cambien la malla, el plano, los
        parámetros ni el catálogo; el desplazamiento y el pasador elegido NO son parte de la clave). ValueError si no se puede."""
        from ..neck_waist_aid import neck_cut_aid, waist_cut_aid, waist_torso

        why = self._joint_aid_blocked(lugar)
        if why:
            raise ValueError(why)
        params = self.project.params
        stl = os.path.basename(self.stl_path or "") or None
        spec = self.project.head if lugar == "cuello" else self.project.waist
        key = (lugar, id(self.work), spec.plane.origin.tobytes(), spec.plane.normal.tobytes(), spec.auto, params.ref_height,
               self.catalog_root, self._catalog_stamp(), stl,
               self._head_fit_key(spec) if lugar == "cuello" else None)
        cache = getattr(self, "_joint_aid_cache", None) or {}
        hit = cache.get(lugar)
        if hit is not None and hit[0] == key:
            return hit[1]
        body = S.to_manifold(self.work)
        cat = self._catalog()
        if lugar == "cuello":
            aid = neck_cut_aid(body, spec, cat, params, height_mm=params.ref_height, exclude_figure=stl)
        else:
            aid = waist_cut_aid(waist_torso(body, self.project), spec, cat, params, height_mm=params.ref_height,
                                exclude_figure=stl)
        cache[lugar] = (key, aid)
        self._joint_aid_cache = cache
        return aid

    def shift_plane(self, lugar: str, shift: float) -> None:
        """Desliza el plano del cuello o de la cintura `shift` mm sobre su normal respecto del que tiene (como el desplazamiento del
        corte de un miembro, descarta el pasador elegido: era de la cara anterior)."""
        spec = self.project.head if lugar == "cuello" else self.project.waist
        if spec is None:
            raise ValueError(f"Primero definí el plano {'del cuello' if lugar == 'cuello' else 'de la cintura'}")
        pl = neck_plane(spec) if lugar == "cuello" else waist_plane(spec)
        plane = Plane.from_normal(pl.origin + pl.normal * float(shift), pl.normal, pl.name)
        if lugar == "cuello":
            self._apply_head_spec(dataclasses.replace(self.project.head, plane=plane, pin_center=None))
        else:
            self.project.waist = dataclasses.replace(self.project.waist, plane=plane, pin_center=None)
            waist_plane(self.project.waist)
            self.outcome = None

    def waist_pin_preview(self):
        """Cara de la cintura (entera) y centro por omisión del pasador (`neck_waist_aid.LinePinPreview`), en caché."""
        from ..neck_waist_aid import waist_preview, waist_torso

        spec = self.project.waist
        if spec is None or self.work is None:
            raise ValueError("Primero definí el plano de la cintura")
        key = (id(self.work), spec.plane.origin.tobytes(), spec.plane.normal.tobytes(), spec.auto)
        cache = getattr(self, "_waist_pin_cache", None)
        if cache is not None and cache[0] == key:
            return cache[1]
        prev = waist_preview(waist_torso(S.to_manifold(self.work), self.project), spec)
        self._waist_pin_cache = (key, prev)
        return prev

    def set_waist_pin(self, point_world) -> None:
        """Fija el centro del pasador de la cintura en `point_world` (sobre su plano, x = 0). ValueError si el vástago no toca la
        cara; `None` vuelve al automático. Con pasador elegido el puerto «cintura» que se cataloga va en él (`waist_port_center`)."""
        spec = self.project.waist
        if spec is None:
            raise ValueError("Primero definí el plano de la cintura")
        if point_world is None:
            center = None
        else:
            prev = self.waist_pin_preview()
            c = prev.on_line(point_world)                       # en el mundo: sobre el plano de la cintura y con x = 0
            shaft = self.project.params.waist_pin.shaft_d
            if prev.fit(prev.project(c), shaft)[1] <= -shaft:
                raise ValueError("el vástago no toca la cara de la cintura: el pasador quedaría suelto")
            center = tuple(float(x) for x in c)
        self.project.waist = dataclasses.replace(spec, pin_center=center)
        self.outcome = None

    def cut_seated_blocked(self, name: str) -> str | None:
        """Por qué NO se puede medir el choque en pose sentada del corte de `name` (None = se puede). Es barato: no mide nada;
        lo usa la ventana para apagar el botón con el motivo y `cut_seated` para negarse."""
        from ..cut_aid import unsupported

        spec = self.limb(name)
        if spec is None:
            return f"no hay un miembro «{name}»"
        if self.work is None:
            return "Abrí un STL"
        if not self.catalog_root:
            return "Abrí un catálogo para medir el choque con sus piezas"
        why = unsupported(spec)
        if why:
            return why
        return None                         # con varios de la misma clase no hay problema: `cut_seated` pone a `name` primero (ver allí)

    def cut_seated(self, name: str, offset: float | None = None) -> dict:
        """Choque en la pose sentada del corte de `name` a `offset` mm (por omisión el actual), sin armar la figura: mide el corte
        con `guide.measure_cuts` (miembro de esta figura sobre los torsos del catálogo y su torso con los miembros del catálogo) y
        resume los choques (`cut_aid.seated_summary`); si hay dos miembros de la misma clase mide el pedido. Es lento (carga mallas): se pide solo para la posición elegida. Corre con
        copias del proyecto: se puede llamar desde un hilo."""
        from ..cut_aid import seated_summary
        from ..guide import measure_cuts

        spec = self.limb(name)
        if spec is None:
            raise KeyError(name)
        why = self.cut_seated_blocked(name)
        if why:
            raise ValueError(why)
        off = float(self.limb_offset(name) if offset is None else offset)
        place = "brazo" if spec.kind == "arm" else "pierna"
        # la guía mide el PRIMER miembro de cada clase: con dos brazos (o dos piernas) se pone primero el que se pide
        limbs = [dataclasses.replace(l, cut_offset=off, pin_center=None) if l.name == name else l for l in copy.deepcopy(self.project.limbs)]
        mine = next(l for l in limbs if l.name == name)
        first = next(k for k, l in enumerate(limbs) if l.kind == spec.kind)
        limbs = limbs[:first] + [mine] + [l for l in limbs[first:] if l is not mine]
        trial = dataclasses.replace(copy.deepcopy(self.project), limbs=limbs)
        stl = os.path.basename(self.stl_path or "") or None
        cuts = {m.lugar: m for m in measure_cuts(trial, S.to_manifold(self.work), self._catalog(), exclude_figure=stl)}
        m = cuts.get(place)
        if m is None or m.error or m.port is None:
            raise ValueError((m.error if m else None) or "no se pudo medir el corte en esa posición")
        return seated_summary(m, off)

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

    # ------------- articulaciones que traen las piezas de catálogo (figura armada solo con catálogo)
    def _catalog_joint_sig(self) -> str:
        """Firma de lo que decide dónde quedan las articulaciones de las piezas de catálogo: qué piezas (id y versión), de qué
        catálogo y con qué escalas de la receta. No incluye los parámetros: el lugar de cada pieza no depende de ellos."""
        origin = None if self.catalog_origin is None else self.catalog_origin.get("receta")
        return json_sig({"parts": {k: v.to_dict() for k, v in self.project.parts.items()}, "root": self.catalog_root,
                         "origin": origin})

    def _catalog_joint_state(self, host=None) -> dict:
        """Lleva cada miembro de catálogo con articulación a su lugar en el cuerpo base y guarda sus `JointSpec` ya colocadas.

        `{"sig", "joints": {nombre: JointSpec}, "placed": {miembro: PlacedPart}, "error": str}`. Se recuerda por firma (no se
        recalcula si no cambian las piezas) y también el fallo, para no reintentar en cada refresco de la ventana. `host` es un
        `SyntheticHost` ya armado (lo trae `load_catalog_body`); sin él se arma acá, que es lo caro."""
        from ..assembly import AssemblyError, place_part, placed_joints, synthesize_host

        sig = self._catalog_joint_sig()
        cached = self._catalog_joint_cache
        if host is None and cached is not None and cached["sig"] == sig:
            return cached
        state = {"sig": sig, "joints": {}, "placed": {}, "error": ""}
        try:
            probs = self.catalog_only_problems()
            if probs:
                raise ValueError("; ".join(probs))
            p = self.project.params
            if host is None:
                host = synthesize_host(self._catalog_only_choices(), p)
            own = {{"arm": "brazo", "leg": "pierna"}[l.kind]: l.name for l in self.project.limbs if l.name in self.project.parts}
            for hl in host.limbs:
                choice, face = host.parts.get(hl.name), host.faces.get(hl.name)
                if choice is None or face is None or not (choice.meta.articulaciones or []):
                    continue
                lname = own.get(hl.name, hl.name)          # nombre del miembro en el proyecto (no siempre el del huésped)
                pp = place_part(choice, hl.kind, face, p, p.ref_height)
                for j in placed_joints(pp, lname):
                    if j.kind != JOINT_KIND_BY_LIMB.get(hl.kind):
                        raise ValueError(f"«{lname}»: la articulación «{j.name}» de la pieza es «{j.kind}» y el miembro es «{hl.kind}»")
                    state["joints"][j.name] = j
                    state["placed"][lname] = pp
        except (AssemblyError, ValueError) as exc:
            state.update(joints={}, placed={}, error=str(exc))
        self._catalog_joint_cache = state
        return state

    def catalog_joints(self) -> dict[str, JointSpec]:
        """Rodilla / codo que traen las piezas de catálogo, ya colocadas en la figura: `{nombre: JointSpec}`. Solo con torso de
        catálogo (la figura sale entera del catálogo) y con el modelo cargado; vacío si no hay o no se puede armar. Son de
        solo lectura: el eje, el plano y el rango vienen de la pieza catalogada."""
        if not self.loaded or not self.catalog_only():
            return {}
        return dict(self._catalog_joint_state()["joints"])

    def catalog_joint_error(self) -> str:
        """Por qué no se pudieron colocar las articulaciones de catálogo ('' si se pudo o no corresponde)."""
        return self._catalog_joint_state()["error"] if self.loaded and self.catalog_only() else ""

    def is_catalog_joint(self, name: str) -> bool:
        return name in self.catalog_joints()

    def joints_listed(self) -> list[JointSpec]:
        """Articulaciones que ve el usuario / la vista previa: con torso de catálogo, las de las piezas elegidas (la figura se
        genera solo con ellas: las del proyecto no cuentan); si no, las del proyecto."""
        if self.catalog_only():
            return list(self.catalog_joints().values())
        return list(self.project.joints)

    def joint_any(self, name: str) -> JointSpec | None:
        """Como `joint`, pero también encuentra las articulaciones de catálogo (de solo lectura)."""
        return next((j for j in self.joints_listed() if j.name == name), None)

    # --- cadera en la vista previa: una «articulación» más por pierna (el pasador + el barrido de cadera)
    def hip_previews(self) -> list[str]:
        """Entradas de cadera de la vista previa: una por pierna del modelo. Con torso de catálogo no hay: la figura sale
        entera del catálogo y esas piernas no son las del modelo."""
        from ..joint_preview import hip_preview_name
        if self.catalog_only():
            return []
        return [hip_preview_name(l.name) for l in self.project.limbs if l.kind == "leg"]

    def preview_names(self) -> list[str]:
        """Todo lo que se puede previsualizar: las articulaciones (rodilla, codo) y la cadera de cada pierna."""
        return [j.name for j in self.joints_listed()] + self.hip_previews()

    def is_hip_preview(self, name: str) -> bool:
        return name in self.hip_previews()

    def preview_exists(self, name: str | None) -> bool:
        return name is not None and (self.joint_any(name) is not None or self.is_hip_preview(name))

    def _refuse_catalog_joint(self, name: str) -> None:
        if self.is_catalog_joint(name):
            raise ValueError(f"la articulación «{name}» viene de la pieza de catálogo: su eje, plano y rango son los de la pieza "
                             "(se cambian en el proyecto con el que se catalogó)")

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
        self._refuse_catalog_joint(name)
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
        self._refuse_catalog_joint(name)
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

    # ------------------------------------------- articulación: altura del eje y plano (vista previa)
    def _joint_normal(self, j: JointSpec) -> np.ndarray:
        n = np.asarray(j.plane.normal, float)
        n = n / np.linalg.norm(n)
        return -n if n[2] < 0 else n                          # lado proximal = arriba (figura de pie, Z arriba)

    def joint_height(self, name: str) -> float:
        """Distancia (mm) del eje de la bisagra al plano de articulación, medida a lo largo de la normal."""
        j = self.joint_any(name)
        if j is None:
            raise KeyError(name)
        return float((np.asarray(j.axis_center, float) - np.asarray(j.plane.origin, float)) @ self._joint_normal(j))

    def set_joint_height(self, name: str, h: float) -> JointSpec:
        """Sube o baja el eje en vertical (x e y no cambian) hasta que quede a `h` mm del plano."""
        self._refuse_catalog_joint(name)
        j = self.joint(name)
        if j is None:
            raise KeyError(name)
        h = float(h)
        if h < 0.5:
            raise ValueError(f"el eje tiene que quedar al menos 0,5 mm arriba del plano de articulación (pedido: {h:.2f} mm)")
        n = self._joint_normal(j)
        c = np.asarray(j.axis_center, float)
        c[2] += (h - self.joint_height(name)) / float(n[2])      # en vertical: x e y no cambian, la altura sobre el plano sí
        j.axis_center = tuple(float(x) for x in c)
        self.outcome = None
        return j

    @staticmethod
    def joint_plane_side_tilt_deg(normal) -> float:
        """Inclinación de COSTADO (vista lateral) del plano de una articulación: ángulo de su traza en YZ respecto de
        Y, positivo si z sube con y. Un plano horizontal da 0; es el par de `plane_front_tilt_deg` (vista de frente)."""
        n = np.asarray(normal, float)
        n = n / np.linalg.norm(n)
        if n[2] < 0:
            n = -n
        return float(np.degrees(np.arctan2(-n[1] * n[2], 1.0 - n[1] ** 2))) + 0.0

    def set_joint_plane_side_tilt(self, name: str, tilt_deg: float) -> JointSpec:
        """Pone la inclinación de costado del plano en `tilt_deg` (0 = «enderezarlo»), sin tocar la inclinación de frente.

        El eje no se mueve y conserva su altura sobre el plano: lo que se corre es el plano (que sigue pasando a esa
        altura bajo el eje). Con el plano más recto el hueco se lleva menos brazo, pero el corte cambia de lugar."""
        self._refuse_catalog_joint(name)
        j = self.joint(name)
        if j is None:
            raise KeyError(name)
        t = float(tilt_deg)
        if abs(t) > 60.0:
            raise ValueError("la inclinación de costado tiene que estar entre −60° y 60°")
        n = self._joint_normal(j)
        h = self.joint_height(name)
        new = np.array([n[0] / n[2], -np.tan(np.radians(t)), 1.0])     # misma pendiente de frente, nueva de costado
        new = new / np.linalg.norm(new)
        if new[2] < 0.5:
            raise ValueError("con esa inclinación el plano deja de cortar el miembro a lo largo (tiene que ser casi horizontal)")
        c = np.asarray(j.axis_center, float)
        j.plane = Plane.from_normal(c - new * h, new, j.plane.name)
        self.outcome = None
        return j

    def joint_preview_key(self, name: str) -> str:
        """Firma de todo lo que cambia el resultado de la vista previa de la articulación (si no cambia, no se recalcula)."""
        if self.is_hip_preview(name):
            from ..joint_preview import hip_limb_name
            lname = hip_limb_name(name)
            spec = self.limb(lname)
            prj = self.project
            blob = {"hip": lname, "limb": spec.to_dict() if spec is not None else None,
                    "trims": [t.to_dict() for t in prj.trims if t.target == lname], "params": prj.params.to_dict(),
                    "work": id(self.work), "part": prj.parts[lname].to_dict() if lname in prj.parts else None,
                    "catalog": self.catalog_root}
            return json_sig(blob)
        j = self.joint_any(name)
        if j is None:
            raise KeyError(name)
        if self.is_catalog_joint(name):            # articulación de una pieza de catálogo: depende de las piezas, no del modelo
            return json_sig({"joint": j.to_dict(), "catalog": self._catalog_joint_sig(), "params": self.project.params.to_dict()})
        spec = self.limb(j.limb)
        prj = self.project
        trims = [t.to_dict() for t in prj.trims if t.target == j.limb]
        blob = {"joint": j.to_dict(), "limb": spec.to_dict() if spec is not None else None, "trims": trims,
                "params": prj.params.to_dict(), "work": id(self.work),
                "part": prj.parts[j.limb].to_dict() if j.limb in prj.parts else None, "catalog": self.catalog_root}
        return json_sig(blob)

    def joint_preview_job(self, name: str):
        """Trabajo de la vista previa de la articulación `name`, apto para correr en un hilo (trabaja con copias).

        Devuelve `run(cache) -> JointPreview`. `cache` es un dict que el que llama conserva entre corridas: guarda el
        miembro ya cortado (`prepare_limb`, lo caro), que solo se rehace si cambian el miembro, su pieza de catálogo,
        sus recortes o los parámetros. Falla con ValueError si no se puede previsualizar así (piezas escaladas, figura
        armada solo con catálogo)."""
        import copy
        from ..customize import needed
        from ..joint_preview import prepare_limb, prepare_limb_from_part, preview_joint

        if self.is_hip_preview(name):
            return self._hip_preview_job(name)
        if self.is_catalog_joint(name):
            return self._catalog_joint_preview_job(name)
        j = self.joint(name)
        if j is None:
            raise KeyError(name)
        spec = self.limb(j.limb)
        if spec is None:
            raise KeyError(j.limb)
        if needed(self.project):
            raise ValueError("la figura tiene piezas escaladas: la vista previa de la articulación todavía no las contempla")
        if self.catalog_only():
            raise ValueError("la figura se arma solo con piezas de catálogo y esta articulación no es de ninguna de ellas: "
                             "las del proyecto no entran en esa figura")
        choice = (self._resolved_parts() or {}).get(j.limb)        # pieza de catálogo en ese lugar (None = miembro del modelo)
        part_sig = "" if choice is None else json_sig({"ref": self.project.parts[j.limb].to_dict(), "root": self.catalog_root})
        work = self.work
        joint = copy.deepcopy(j)
        spec = copy.deepcopy(spec)
        p = copy.deepcopy(self.project.params)
        trims = copy.deepcopy(list(self.project.trims))

        def run(cache: dict | None = None):
            cache = {} if cache is None else cache
            lk = (id(work), json_sig(spec.to_dict()), json_sig([t.to_dict() for t in trims if t.target == spec.name]),
                  json_sig(p.to_dict()), part_sig)
            if cache.get("key") != lk:
                cache["key"] = None
                body = S.to_manifold(work)
                cache["val"] = (prepare_limb(body, spec, p, trims) if choice is None
                                else prepare_limb_from_part(body, spec, choice, p, trims))
                cache["key"] = lk
            limb, face = cache["val"]
            return preview_joint(limb, face, joint, p)

        return run

    def _hip_preview_job(self, name: str):
        """Trabajo de la vista previa de la cadera de una pierna (apto para un hilo; trabaja con copias). Devuelve
        `run(cache) -> HipPreview`. El `cache` guarda la pierna ya cortada y la mitad del cuerpo (lo caro), que solo se rehacen
        si cambian la pierna, su pieza de catálogo, sus recortes o los parámetros. Falla con ValueError como la vista previa
        de las demás articulaciones (piezas escaladas, figura armada solo con catálogo)."""
        import copy
        from ..customize import needed
        from ..joint_preview import hip_limb_name, prepare_limb, prepare_limb_from_part, preview_hip

        lname = hip_limb_name(name)
        spec = self.limb(lname) if lname else None
        if spec is None:
            raise KeyError(name)
        if needed(self.project):
            raise ValueError("la figura tiene piezas escaladas: la vista previa de la cadera todavía no las contempla")
        if self.catalog_only():
            raise ValueError("la figura se arma solo con piezas de catálogo: la cadera del modelo no entra en esa figura")
        choice = (self._resolved_parts() or {}).get(lname)
        part_sig = "" if choice is None else json_sig({"ref": self.project.parts[lname].to_dict(), "root": self.catalog_root})
        work = self.work
        spec = copy.deepcopy(spec)
        p = copy.deepcopy(self.project.params)
        trims = copy.deepcopy(list(self.project.trims))

        def run(cache: dict | None = None):
            cache = {} if cache is None else cache
            lk = ("hip", id(work), json_sig(spec.to_dict()), json_sig([t.to_dict() for t in trims if t.target == spec.name]),
                  json_sig(p.to_dict()), part_sig)
            if cache.get("key") != lk:
                cache["key"] = None
                body = S.to_manifold(work)
                half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
                cache["val"] = (prepare_limb(body, spec, p, trims, half) if choice is None
                                else prepare_limb_from_part(body, spec, choice, p, trims, half)), half
                cache["key"] = lk
            (limb, face), half = cache["val"]
            return preview_hip(half, limb, face, spec, p)

        return run

    def _catalog_joint_preview_job(self, name: str):
        """Trabajo de la vista previa de una articulación que trae una pieza de catálogo (figura armada solo con catálogo).

        El miembro ya está colocado en el cuerpo base (`_catalog_joint_state`), así que no hay nada caro que preparar: solo se
        recorta alrededor del eje y se arma el hueco, con los mismos números que la figura final (salvo el paso del barrido)."""
        import copy
        from ..joint_preview import preview_joint

        j = self.catalog_joints()[name]
        pp = self._catalog_joint_state()["placed"][j.limb]
        joint, p = copy.deepcopy(j), copy.deepcopy(self.project.params)

        def run(cache: dict | None = None):
            return preview_joint(pp.mesh, pp.face, joint, p)

        return run

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
        m = hb.frame.matrix()                                     # marco local de la mano → mundo (para colocar accesorios en el puño)
        if spec.join == "giro":
            m[:3, 3] += hb.frame.axis * float(self.project.params.clearance)
        elif spec.overlap > 0:
            m[:3, 3] -= hb.frame.axis * float(spec.overlap)
        return {"figure": S.to_trimesh(S.difference(full, hb.original)), "hand": S.to_trimesh(hb.hand), "info": hb.info,
                "matrix": m}

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

    def catalog_name(self) -> str:
        return self._catalog().name

    def cut_guide(self) -> list:
        """«Guía de cortes»: mide la cara de cada corte de la figura abierta (hombro, cadera, cuello) sin generar nada y, si hay
        catálogo abierto, cuánto de él acepta cada corte (`guide.measure_cuts`). Devuelve `[CutMeasure]`; un corte que todavía
        no se puede medir (falta la silueta, el plano…) trae su `error` en vez de interrumpir a los demás."""
        from ..guide import measure_cuts

        if self.work is None:
            raise ValueError("Primero abrí una figura.")
        cat = self._catalog() if self.catalog_root else None
        name = os.path.basename(self.stl_path or "") or None
        return measure_cuts(self.project, S.to_manifold(self.work), cat, exclude_figure=name)

    def cut_fit(self) -> list:
        """«Ajustar al estándar»: qué cambiaría cada corte para caer en un talle del contrato de la línea (`guide.fit_cut`),
        sin tocar el proyecto. `[FitResult]`."""
        from ..guide import contract_of, fit_cut

        if self.work is None:
            raise ValueError("Primero abrí una figura.")
        if not self.catalog_root:
            raise ValueError("Abrí el catálogo de la línea: el contrato de interfaz vive ahí.")
        contract = contract_of(self._catalog())
        if contract is None:
            raise ValueError("La línea no tiene contrato de interfaz (`afm catalogo contrato … --calibrar --guardar`).")
        return fit_cut(self.project, S.to_manifold(self.work), contract)

    def apply_cut_fit(self, results) -> int:
        """Aplica al proyecto los ajustes que dejan el corte en contrato (`guide.apply_fit`). Devuelve cuántos cortes cambió."""
        from ..guide import apply_fit

        return apply_fit(self.project, results)

    def waist_wrist_options(self) -> dict:
        """Piezas del catálogo abierto que pueden hacer de patrón para el ajuste de cintura y muñeca: {«pelvis»: [ref], «mano»: [ref]}."""
        if not self.catalog_root:
            return {"pelvis": [], "mano": []}
        cat = self._catalog()
        return {t: [m.ref for m in cat.list(t)] for t in ("pelvis", "mano")}

    def waist_wrist_fit(self, pelvis: str | None = None, mano: str | None = None) -> list:
        """«Ajustar cintura y muñeca al patrón»: cuánto mover el plano de la cintura y el de la muñeca para que sus caras tengan el
        radio de la pelvis y de la mano del catálogo (`ajuste.ajustar`), sin tocar el proyecto. `[AjusteResult]`."""
        from ..ajuste import ajustar

        if self.work is None:
            raise ValueError("Primero abrí una figura.")
        if not self.catalog_root:
            raise ValueError("Abrí el catálogo de la línea: la pelvis y la mano del patrón viven ahí.")
        return ajustar(self.project, S.to_manifold(self.work), self._catalog(), pelvis, mano)

    def apply_waist_wrist_fit(self, results, escalar_pelvis: bool = False) -> int:
        """Mueve los planos de cintura y muñeca según `results` y, con `escalar_pelvis`, escribe la escala de la pelvis del patrón
        (`ajuste.aplicar`). Devuelve cuántos cambios hizo."""
        from ..ajuste import aplicar

        n = aplicar(self.project, results, escalar_pelvis)
        if n:
            self.outcome = None
            self._part_preview = None
        return n

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

    def proportions_snapshot(self, figura: str, progress=None) -> dict:
        """Lo que necesita el comparador de proporciones (`afm.proportions`): las piezas de la figura abierta, ya divididas y escaladas
        (`model`), el cuerpo de la figura patrón `figura` del catálogo (`ref`) y la escala actual de cada pieza (`scales`, con los
        miembros como «brazo» / «pierna»). Si la figura todavía no está dividida en piezas la divide (escalas ×1)."""
        from .. import proportions as PR

        if self.work is None:
            raise ValueError("Primero abrí una figura.")
        if self.catalog_only():
            raise ValueError("Con un torso de catálogo la figura sale entera del catálogo: no hay piezas del modelo para comparar")
        ref = PR.reference_meshes(self._catalog(), figura)
        items = self.part_preview_items() if self.project.part_scales else {}
        if not items:
            items = self.divide_model() if not self.project.part_scales else self.preview_parts(progress)
        model = PR.model_meshes(items, self.project)
        if not model:
            raise ValueError("No se pudieron armar las piezas del modelo (revisá planos y siluetas).")
        kind_place = {"arm": "brazo", "leg": "pierna"}
        scales = {kind_place.get(self._limb_kind(p), p): self.part_scale(p) for p in self.scale_places()}
        model, ref, note = PR.harmonize(model, ref)
        return {"figura": figura, "model": model, "ref": ref, "scales": scales, "nota": note}

    def _limb_kind(self, place: str):
        spec = self.limb(place)
        return None if spec is None else spec.kind

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
        `cabeza`, `mano`). En una figura sin STL (`catalog_origin`) cada pieza lleva la escala que traía la receta."""
        resolved = self._resolved_parts() or {}
        scales = self._origin_scales()
        out = {}
        for place, choice in resolved.items():
            if place == "torso" and choice.meta.tipo == "torso_sup":
                key = "torso_sup"
            elif place in TORSO_PLACES or place in ("cabeza", "mano"):
                key = place
            else:
                spec = self.limb(place)
                key = {"arm": "brazo", "leg": "pierna"}[spec.kind]
            if place in scales:
                from ..customize import scale_choice
                choice = scale_choice(choice, scales[place])
            out[key] = choice
        return out

    def _origin_scales(self) -> dict:
        """Escalas por pieza de la receta con la que se armó la figura sin STL (vacío si la figura viene de un STL)."""
        if self.catalog_origin is None:
            return {}
        from ..recipe import Recipe

        try:
            return Recipe.from_dict(self.catalog_origin["receta"]).scales()
        except (KeyError, ValueError, TypeError):
            return {}

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
        # La firma es la del estado con el que arranca la revisión: la revisión corre en un hilo y, si mientras tanto se
        # cambia algo (la cabeza, una pieza, un parámetro), el resultado queda viejo y no se dibuja como si fuera vigente.
        sig = self._preview_signature()
        from ..customize import needed, preview_model
        if needed(self.project):
            items = preview_model(S.to_manifold(self.work), self.project, self._resolved_parts())
            self._part_preview = (sig, items)
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
            self._part_preview = (sig, items)
            return items
        if self.project.symmetry is None:
            raise ValueError("Falta el plano de simetría")
        parts = self._resolved_parts()
        items = _preview(self.project, parts, S.to_manifold(self.work), progress)
        self._part_preview = (sig, items)
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
        only_head = bool(places) and set(places) <= {"cabeza"}
        if only_head:                       # la cabeza solo necesita el plano del cuello: el resto de la figura puede estar sin terminar
            if self.project.head is None:
                raise ValueError("Primero definí el plano del cuello (la cabeza que se cataloga).")
        else:
            probs = self.problems()
            if probs:
                raise ValueError("; ".join(probs))
        cat = self._catalog()
        name = os.path.basename(self.stl_path or "figura")
        invert_wrist = False
        hand = self.project.hand
        if wrist_points is None and hand is not None and len(hand.points) == 3:
            # la muñeca ya marcada en la figura (pestaña Mano) va con el brazo: sin ella la pieza «brazo» queda sin toma de muñeca
            # y la mano de catálogo no se puede acoplar a ese brazo (pipeline: «necesita el plano de muñeca del modelo»)
            wrist_points = np.asarray(hand.points, float)
            if wrist_points[:, 0].mean() < 0:
                wrist_points = wrist_points * np.array([-1.0, 1.0, 1.0])            # el brazo de trabajo es el +X
            invert_wrist = bool(hand.invert)
        try:
            res = extract_all(S.to_manifold(self.work), self.project, prefix, places, wrist_points, invert_wrist, figure=name)
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
        dup = [e.meta.id for e in res.pieces if cat.versions(e.meta.id)]
        if dup and not new_version:         # antes de guardar nada: no dejar el catálogo a medio llenar
            raise ValueError(f"ya están en el catálogo: {', '.join(dup)}. Las piezas son inmutables: tildá «guardar como versión "
                             "nueva» o elegí solo los lugares que faltan.")
        metas, avisos = [], list(res.warnings)
        for e in res.pieces:
            e.meta.etiquetas = list(labels)
            meta, warns = cat.add_piece(e.mesh, e.meta, new_version)
            metas.append(meta)
            avisos += warns
        return {"piezas": metas, "avisos": avisos}

    CATALOG_ADD_PLACES = ("cabeza", "torso", "brazo", "pierna")

    def catalog_prefix(self) -> str:
        """Prefijo de los ids de las piezas que salen de esta figura (`cabeza-<prefijo>-01`): el nombre del STL."""
        import re

        base = os.path.splitext(os.path.basename(self.stl_path or "figura"))[0].lower()
        return re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "figura"

    def catalog_add_options(self) -> dict:
        """Lo que ofrece «Catalogar esta figura…»: {"prefijo", "lugares": [{"lugar", "disponible", "nota", "existentes"}],
        "cabeza_reemplazo": nombre del STL o None}. `existentes` son las piezas de ese lugar que ya salieron de esta figura
        (mismo prefijo): guardarlas otra vez pide «versión nueva»."""
        prefix = self.catalog_prefix()
        try:
            have = [m for m in self._catalog().list(all_versions=True) if m.id.endswith(f"-{prefix}-01")] if self.catalog_root else []
        except ValueError:
            have = []
        place_of = {"cabeza": "cabeza", "torso": "torso", "torso_sup": "torso", "pelvis": "torso", "brazo": "brazo", "pierna": "pierna"}
        kinds = {l.kind for l in self.project.limbs}
        head = self.project.head
        rep = os.path.basename(head.replacement_path) if head is not None and head.replacement_path else None
        avail = {"cabeza": (head is not None, f"cabeza de reemplazo «{rep}»" if rep else
                            ("" if head is not None else "falta el plano del cuello")),
                 "torso": (True, ""),
                 "brazo": ("arm" in kinds, "" if "arm" in kinds else "el proyecto no define un brazo"),
                 "pierna": ("leg" in kinds, "" if "leg" in kinds else "el proyecto no define una pierna")}
        rows = [{"lugar": lugar, "disponible": avail[lugar][0], "nota": avail[lugar][1],
                 "existentes": sorted(m.ref for m in have if place_of.get(m.tipo) == lugar)} for lugar in self.CATALOG_ADD_PLACES]
        return {"prefijo": prefix, "lugares": rows, "cabeza_reemplazo": rep}

    # ------------------------------------------------------------- gestor del catálogo (ver y eliminar piezas)
    def catalog_open(self):
        """El `Catalog` abierto (nuevo en cada llamada: no se comparte con un hilo), o None si no hay catálogo."""
        return self._catalog() if self.catalog_root else None

    def catalog_pieces(self) -> list:
        """Todas las versiones de todas las piezas del catálogo abierto, ordenadas por tipo, id y versión."""
        return sorted(self._catalog().list(all_versions=True), key=lambda m: (m.tipo, m.id, m.version))

    def catalog_load(self, ref: str):
        """(meta, malla) de la pieza `id@versión` del catálogo abierto, para la vista previa (sin verificar el hash)."""
        from ..catalog import CatalogError

        try:
            return self._catalog().load(ref, check_hash=False)
        except CatalogError as exc:
            raise ValueError(str(exc)) from exc

    def catalog_wrist_plan(self, ref: str, points, invert: bool = False, hand_id: str | None = None):
        """Corte de muñeca de un brazo del catálogo abierto, calculado y SIN guardar (`Catalog.plan_wrist_cut`)."""
        from ..catalog import CatalogError

        try:
            return self._catalog().plan_wrist_cut(ref, points, invert, hand_id)
        except CatalogError as exc:
            raise ValueError(str(exc)) from exc

    def catalog_wrist_cut(self, ref: str, points, invert: bool = False, hand_id: str | None = None,
                          new_hand_version: bool = False):
        """Corta y guarda (`Catalog.cut_wrist`): versión nueva del brazo sin mano + pieza `mano`. Devuelve el `WristCut`."""
        from ..catalog import CatalogError

        try:
            return self._catalog().cut_wrist(ref, points, invert, hand_id, new_hand_version)
        except CatalogError as exc:
            raise ValueError(str(exc)) from exc

    def catalog_joint_params(self):
        """Parámetros de la línea del catálogo abierto: con los que se arma la articulación de una pieza al reajustarla."""
        return self._catalog().params

    def catalog_retune_joint(self, ref: str, joint, note: str = ""):
        """Guarda la articulación reajustada como versión nueva de la pieza (`Catalog.retune_joint`): (meta, avisos)."""
        from ..catalog import CatalogError

        try:
            return self._catalog().retune_joint(ref, joint, note)
        except CatalogError as exc:
            raise ValueError(str(exc)) from exc

    def _parts_using(self, cat, ref: str) -> list[str]:
        """Lugares de ESTA figura que tienen elegida la pieza `ref` (una `PartRef` sin versión = la última del id)."""
        from ..catalog import parse_ref

        pid, ver = parse_ref(ref)
        latest = max(cat.versions(pid), default=None)
        return [place for place, pr in self.project.parts.items()
                if pr.id == pid and pr.linea in (None, cat.name)
                and (ver is None or (pr.version == ver if pr.version is not None else ver == latest))]

    def catalog_usages(self, ref: str) -> list[str]:
        """Dónde se usa `ref` (texto para mostrar): recetas del catálogo y lugares de la figura abierta. No mira otros
        `.proyecto` del disco (para eso `afm catalogo usos --buscar`)."""
        cat = self._catalog()
        out = [f"receta «{os.path.basename(u['archivo'])}» (lugar «{u['lugar']}»)" for u in cat.usages(ref)]
        out += [f"la figura abierta (lugar «{place}»)" for place in self._parts_using(cat, ref)]
        return out

    def catalog_delete(self, refs) -> dict:
        """Elimina del catálogo las versiones `id@n` y deja la figura abierta coherente: los lugares que usaban una pieza
        eliminada vuelven a la del modelo. Devuelve {"eliminadas": [ref], "quitadas": [lugar], "errores": [str]}."""
        from ..catalog import CatalogError

        cat = self._catalog()
        try:
            res = cat.delete_pieces(list(refs))
        except CatalogError as exc:
            raise ValueError(str(exc)) from exc
        gone = set(res.refs)
        left = {m.id for m in cat.list(all_versions=True)}
        cleared = []
        for place, pr in list(self.project.parts.items()):
            if pr.linea not in (None, cat.name):
                continue
            hit = f"{pr.id}@{pr.version}" in gone if pr.version is not None else (pr.id not in left and any(r.startswith(pr.id + "@") for r in gone))
            if hit and self.clear_part(place):
                cleared.append(place)
        if cleared:
            self._part_preview = None
        return {"eliminadas": res.refs, "quitadas": cleared, "errores": res.errors}

    def catalog_set_state(self, refs, state: str) -> list:
        """Pone el estado (borrador / validada / impresa) a las versiones `id@n` (`Catalog.set_states`)."""
        from ..catalog import CatalogError

        try:
            return self._catalog().set_states(list(refs), state)
        except CatalogError as exc:
            raise ValueError(str(exc)) from exc

    def catalog_trash(self) -> list[dict]:
        """Lotes recuperables de la papelera del catálogo abierto (el más nuevo primero)."""
        return self._catalog().trash_batches() if self.catalog_root else []

    def catalog_undo_delete(self) -> tuple[list, list]:
        """Recupera el último lote eliminado: (metas recuperadas, errores)."""
        from ..catalog import CatalogError

        try:
            return self._catalog().restore_deleted()
        except CatalogError as exc:
            raise ValueError(str(exc)) from exc

    def catalog_rename(self, ref: str, nombre: str) -> list:
        """Cambia el nombre para mostrar de la pieza `ref` (todas sus versiones; `Catalog.rename_piece`). Devuelve las metas."""
        from ..catalog import CatalogError

        try:
            return self._catalog().rename_piece(ref, nombre)
        except CatalogError as exc:
            raise ValueError(str(exc)) from exc

    def _resolved_parts(self):
        """`Project.parts` -> piezas cargadas del catálogo abierto (o de la ruta por defecto de su línea)."""
        if not self.project.parts:
            return None
        from ..assembly import AssemblyError, resolve_parts

        try:
            return resolve_parts(self.project.parts, self.catalog_root)
        except AssemblyError as exc:
            raise ValueError(str(exc)) from exc

    def progress_plan(self, task: str, orient: bool = False, flat: bool = False) -> list | None:
        """Pasos que se esperan de `build` | `preview` | `export` con el estado actual (barra de progreso). Es una
        estimación barata, sin sintetizar nada: los mensajes que no estén en el plan no la rompen. None = sin plan."""
        from .. import progress as PR

        p = self.project.params
        if task == "export":
            if self.outcome is None:
                return None
            st = [PR.Stage("Aplanando la base", 4)] if flat else []
            cap = p.export_max_tris
            if cap and cap > 0 and any(m.num_tri() > cap for m in self.outcome.result.pieces.values()):
                st.append(PR.Stage("Reduciendo triángulos de las piezas", 4))      # `pipeline.export` lo anuncia solo si alguna supera el tope
            st += [PR.Stage("Calculando la orientación de impresión", 5)] if orient else []
            st += [PR.Stage(f"Escribiendo «{n}»", 2) for n in self.outcome.result.pieces]
            return st + [PR.Stage("Escribiendo la figura ensamblada", 3), PR.Stage("Escribiendo la lista de piezas", 1),
                         PR.Stage("Guardando el informe y el proyecto", 1)]
        if task == "preview":
            from ..customize import needed

            if needed(self.project) or self.catalog_only() or not self.project.parts:
                return None                                   # corre sin mensajes por lugar: barra sin porcentaje
            return PR.preview_plan(list(self._resolved_parts() or {}))
        if task != "build":
            return None
        from ..customize import needed

        if not needed(self.project) and self.catalog_only():
            return PR.catalog_plan(self._catalog_only_choices(), p, self.project.hand if (
                self.project.hand is not None and not self.project.hand.path) else None, epilogue=[PR.VALIDATE])
        prj = self.project
        return PR.build_plan(prj.limbs, p, parts=self._resolved_parts(), head=prj.head, waist=prj.waist, hand=prj.hand,
                             joints=prj.joints, prologue=[PR.BASE_BODY] if needed(prj) else None, epilogue=[PR.VALIDATE])

    def build(self, progress=None) -> BuildOutcome:
        probs = self.problems()
        if probs:
            raise ValueError("; ".join(probs))
        from ..customize import needed, build_model
        if needed(self.project):
            res, limbs = build_model(S.to_manifold(self.work), self.project, self._resolved_parts(), progress)
            if progress:
                progress("Validando la figura…")
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
        if progress:
            progress("Validando la figura…")
        val = P.validate(res, self.project.params, self.project.limbs, joints=self.project.joints)
        self.outcome = BuildOutcome(res, val)
        return self.outcome

    def _build_catalog_only(self, progress=None) -> BuildOutcome:
        """Figura armada solo con piezas de catálogo (torso incluido): lo mismo que `afm armar` (recipe.assemble)."""
        from ..assembly import AssemblyError, synthesize_host

        p = self.project.params
        try:
            if progress:
                progress("Armando el cuerpo base…")
            host = synthesize_host(self._catalog_only_choices(), p)
            hand = self.project.hand if (self.project.hand is not None and not self.project.hand.path) else None   # mano de catálogo: giro y unión
            res = P.build(host.body, host.limbs, p, None, True, progress, None, host.head, None, host.waist, hand,
                          host.parts, host_faces=host.faces)
        except AssemblyError as exc:
            raise ValueError(str(exc)) from exc
        if progress:
            progress("Validando la figura…")
        val = P.validate(res, p, host.limbs, joints=res.joint_specs)
        self.outcome = BuildOutcome(res, val)
        return self.outcome

    def export(self, outdir: str, name: str | None = None, orient: bool = False, max_angle: float = 45.0,
               flat: bool = False, progress=None) -> dict:
        if self.outcome is None:
            raise ValueError("Primero generá la figura")
        name = name or (os.path.splitext(os.path.basename(self.stl_path or "figura"))[0])
        os.makedirs(outdir, exist_ok=True)
        from ..accessories import finish_export, load_project_items

        items = load_project_items(self.project, progress=progress)              # las armas viajan con la figura
        files = P.export(self.outcome.result, outdir, name, orient=orient, max_angle=max_angle,
                         flat=self.project.params.flat_depth if flat else None, params=self.project.params,
                         progress=progress, extras=[i["mesh"] for i in items])
        acc_info = finish_export(items, outdir, name, progress)
        if acc_info:
            files["accesorios"] = [a["archivo"] for a in acc_info]
        if progress:
            progress("Guardando el informe y el proyecto…")
        info = {"validacion": self.outcome.validation, "construccion": self.outcome.result.report, "archivos": files}
        if acc_info:
            info["accesorios"] = acc_info
        with open(os.path.join(outdir, f"{name}_informe.json"), "w") as f:
            json.dump(info, f, indent=2, default=float)
        self.save_project(os.path.join(outdir, f"{name}.proyecto"))
        return info

    # ------------------------------------------------------------- proyecto
    def save_project(self, path: str) -> None:
        d = self.project.to_dict()
        d["source"] = os.path.abspath(self.stl_path) if self.stl_path else None
        if self.catalog_origin is not None and not self.stl_path:      # sin esto el proyecto no se podría reabrir sin STL
            d["catalogo_solo"] = {**self.catalog_origin, **({"ruta": os.path.abspath(self.catalog_root)} if self.catalog_root else {})}
        with open(path, "w") as f:
            json.dump(d, f, indent=2)

    @classmethod
    def open_project(cls, path: str, stl_path: str | None = None, catalog_root: str | None = None) -> "Session":
        """Abre un `.proyecto`. Con STL (`stl_path` o el `source` del archivo) carga la figura; un proyecto de «Armar y
        exportar» (`catalogo_solo`, `source: null`) se abre SIN STL: el cuerpo base sale del catálogo de su línea, en
        `catalog_root`, o en la carpeta donde se armó."""
        from ..catalog_project import origin

        with open(path) as f:
            d = json.load(f)
        src = stl_path or d.get("source")
        if src and os.path.exists(src):
            sess = cls(Project.from_dict(d))
            sess.load(src, sess.project.crown_z)
            return sess
        org = origin(d)
        if org is not None and not stl_path:
            return cls.from_catalog_project(d, org, catalog_root)
        raise FileNotFoundError(f"No se encuentra el STL del proyecto: {src!r}. Indicá su ubicación.")

    @classmethod
    def from_catalog_project(cls, d: dict, org: dict, catalog_root: str | None = None) -> "Session":
        """Figura toda de catálogo sin STL: abre el catálogo y arma el cuerpo base (torso, pelvis y cabeza) con `synthesize_host`."""
        roots = [r for r in (catalog_root, org.get("ruta")) if r]
        root = next((r for r in roots if os.path.isfile(os.path.join(r, "linea.json"))), None)
        if root is None:
            raise CatalogNeeded(org.get("linea") or "?", roots[-1] if roots else None)
        sess = cls(Project.from_dict(d))
        sess.catalog_origin = dict(org)
        sess.open_catalog(root)
        sess.load_catalog_body()
        return sess

    def load_catalog_body(self) -> None:
        """Reemplaza el STL: el modelo de trabajo es el cuerpo que arma `synthesize_host` con las piezas de catálogo del proyecto."""
        from ..assembly import AssemblyError, synthesize_host

        probs = self.catalog_only_problems()
        if probs:
            raise ValueError("; ".join(probs))
        try:
            host = synthesize_host(self._catalog_only_choices(), self.project.params)
        except AssemblyError as exc:
            raise ValueError(str(exc)) from exc
        mesh = S.to_trimesh(host.body)
        ext = mesh.bounds[1] - mesh.bounds[0]
        self.stl_path, self.base = None, mesh
        self._catalog_joint_state(host)        # el huésped ya está armado: de acá salen las articulaciones de las piezas
        self.report = meshio.LoadReport(original_height=float(ext[2]), was_watertight=True)
        self._zmin_file = float(mesh.bounds[0][2])
        self.outcome = None
        self._part_preview = None
        self._rebuild_frames()
