"""Lógica de la ventana: modos de selección de puntos, lista de planos y qué se dibuja.

No depende de GTK (la ventana es una capa fina sobre esto), así que se prueba sin pantalla. El
renderer (`SceneRenderer`) solo se toca en `sync`, que la ventana llama con el contexto GL activo.

Marco de coordenadas: lo que se ve y se elige es siempre ``session.work`` (la figura con el plano de
simetría en x = 0), que es donde viven los planos de los miembros (ver `session`). Antes de definir la
simetría, ``work`` coincide con ``figure``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import trimesh

from .. import solids as S
from ..head import HEAD_NAME, MIN_BASE_IOU
from ..limbs import silhouette_on_plane
from ..orient import OrientResult, best_orientation, overhang_mask
from ..planes import Plane
from ..trims import Trim
from .camera import OrbitCamera
from .picking import Picker, PickResult
from .polygon_editor import EDGE_PX, VERTEX_PX, PolygonDraft, ray_plane_uv
from .session import Session

SYMMETRY_ID = "simetria"
_KIND_BASE = {"arm": "brazo", "leg": "pierna"}


class Mode(Enum):
    VIEW = "ver"
    CROWN = "coronilla"
    SYMMETRY = "simetria"
    LIMB_PLANE = "plano_miembro"
    NECK_PLANE = "plano_cuello"
    POLYGON = "silueta"
    PIN = "pasador"
    JOINT_PLANE = "plano_articulacion"   # 3 clics: plano que parte el miembro en proximal y distal (rodilla / codo)
    JOINT_AXIS = "eje_articulacion"      # un clic sobre la figura fija (y, z) del eje de la bisagra
    TRIM_PICK = "recorte_clic"      # un clic sobre la figura pone el centro de un recorte esfera / caja / cilindro


TRIM_SHAPES = ("polygon", "sphere", "box", "cylinder")
TRIM_SHAPE_LABELS = {"polygon": "polígono", "sphere": "esfera", "box": "caja", "cylinder": "cilindro"}
TRIM_OP_LABELS = {"subtract": "restar", "union": "unir"}
SYMMETRY_PLANE_NAME = "simetría"
JOINT_PREFIX = "articulacion:"      # id de plano de una articulación en la lista (`articulacion:rodilla`)
POSE_TOL_MM3 = 1e-4              # misma tolerancia de interferencia que `pipeline.validate`


def mirrored_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Reflexión en x (plano de simetría en x = 0) con el orden de los triángulos invertido, como el Mirror de Onshape."""
    return trimesh.Trimesh(np.asarray(mesh.vertices) * np.array([-1.0, 1.0, 1.0]), np.asarray(mesh.faces)[:, ::-1],
                           process=False)


def mirrored_points(pts) -> np.ndarray:
    return np.asarray(pts, float) * np.array([-1.0, 1.0, 1.0])


@dataclass
class PlaneEntry:
    id: str                  # SYMMETRY_ID o el nombre del miembro
    label: str
    plane: Plane
    kind: str                # "symmetry" | "arm" | "leg" | "head" | "joint"
    visible: bool = True


_MIRROR_COLOR = (0.70, 0.78, 0.92, 1.0)      # el espejo se ve con un tinte azul para no confundirlo con el lado real
NUDGE_MM = 0.1                 # paso de las flechas al ajustar el pasador (Mayús = 10 veces más)


def circle_world(plane: Plane, center, radius: float, n: int = 48) -> np.ndarray:
    """Circunferencia de `radius` mm centrada en `center`, sobre `plane` (Nx3, cerrada por el que dibuja)."""
    a = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    c = np.asarray(center, float)
    return c + radius * (np.cos(a)[:, None] * plane.u + np.sin(a)[:, None] * plane.v)


def validation_summary(val: dict, clearance: float) -> str:
    """Texto corto del resultado de `pipeline.validate` para la barra de estado."""
    if val["problems"]:
        return "La figura se generó pero tiene problemas: " + "; ".join(val["problems"])
    parts = [f"Figura generada y válida: {len(val['pieces'])} piezas"]
    if val["gaps"]:
        parts.append(f"holgura mínima {min(val['gaps'].values()):.3f} mm (objetivo {clearance:.3f})")
    if val["recorrido"]:
        parts.append("recorrido libre sin interferencias")
    if val.get("avisos_pasador"):
        parts.append("pasador: " + "; ".join(val["avisos_pasador"]))
    if val["avisos"]:
        parts.append(f"{len(val['avisos'])} aviso(s) de soldado (el slicer puede avisar de aristas no-variedad)")
    return "; ".join(parts) + "."


# Parámetros editables en la GUI: (clave con punto, etiqueta, mínimo, máximo, paso, decimales)
PARAM_FIELDS = (
    ("clearance", "Holgura miembro–cavidad (mm)", 0.1, 1.5, 0.05, 2),
    ("burr", "Rebaba a rebajar en la cara (mm)", 0.0, 3.0, 0.05, 2),
    ("sit_angle", "Ángulo de pose sentada (°)", 0.0, 180.0, 5.0, 0),
    ("hip_margin", "Margen del límite de cadera (°)", 0.0, 30.0, 1.0, 0),
    ("shoulder_pin.shaft_d", "Hombro: Ø vástago (mm)", 2.0, 12.0, 0.1, 1),
    ("shoulder_pin.shaft_len", "Hombro: largo vástago (mm)", 0.5, 15.0, 0.1, 1),
    ("shoulder_pin.head_d", "Hombro: Ø cabeza (mm)", 3.0, 16.0, 0.1, 1),
    ("shoulder_pin.head_h", "Hombro: alto cabeza (mm)", 0.5, 8.0, 0.05, 2),
    ("hip_pin.shaft_d", "Cadera: Ø vástago (mm)", 2.0, 12.0, 0.1, 1),
    ("hip_pin.shaft_len", "Cadera: largo vástago (mm)", 0.5, 15.0, 0.1, 1),
    ("hip_pin.head_d", "Cadera: Ø cabeza (mm)", 3.0, 16.0, 0.1, 1),
    ("hip_pin.head_h", "Cadera: alto cabeza (mm)", 0.5, 8.0, 0.05, 2),
    ("hinge_pin.pin_d", "Rodilla/codo: Ø bulón (mm)", 2.0, 6.0, 0.1, 1),
    ("hinge_pin.pin_len", "Rodilla/codo: largo bulón (mm)", 0.5, 4.0, 0.1, 1),
    ("hinge_pin.tongue_t", "Rodilla/codo: espesor lengüeta (mm)", 1.0, 6.0, 0.1, 1),
    ("hinge_pin.end_r", "Rodilla/codo: radio lengüeta (mm)", 2.0, 8.0, 0.1, 1),
)


class Controller:
    def __init__(self, session: Session | None = None):
        self.session = session or Session()
        self.camera = OrbitCamera()
        self.picker: Picker | None = None
        self.mode = Mode.VIEW
        self.pending: list[np.ndarray] = []
        self.pending_kind: str | None = None        # "arm" | "leg" mientras se elige el plano de un miembro
        self.message = "Abrí un STL para empezar."
        self.one_side = False                        # trabajar un solo lado (oculta x < 0)
        self.hidden: set[str] = set()                # ids de planos ocultos
        self.busy = False                            # generando la figura (la ventana bloquea la edición)
        self.show_overhangs = False                  # pintar de rojo los voladizos de impresión
        self.overhang_angle = 45.0                   # grados desde la vertical (como `--max-angle`)
        self.draft: PolygonDraft | None = None      # polígono de silueta en edición (modo POLYGON)
        self.edit_limb: str | None = None
        self.edit_cross = False                     # el polígono en edición es el 2.º corte (frente) del miembro
        self.joint_limb: str | None = None          # miembro al que se le está definiendo la articulación (JOINT_PLANE)
        self.joint_name: str | None = None          # articulación cuyo eje se está colocando (JOINT_AXIS)
        self.pin_limb: str | None = None            # miembro cuyo pasador se está colocando (modo PIN)
        self.pin_prev = None                         # PinPreview de ese miembro (cara de contacto)
        self.mirror_live = False                     # con «un solo lado»: mostrar el espejo del lado de trabajo en vivo
        self.pose_view = False                       # vista previa de la figura generada con las piernas en la pose sentada
        self.pose_angle: float | None = None         # ángulo de cadera de la vista previa (None = el de la pose sentada)
        self.pose_report: dict | None = None         # resultado de `pose_check` para ese ángulo
        self._pose_cache: tuple | None = None        # (outcome, ángulo, {pieza: sólido}) de la pose mostrada
        self.trim_cfg: dict | None = None            # recorte en edición: target / op / distance / symmetric / shape / size
        self.edit_trim: int | None = None            # índice en project.trims del polígono de recorte en edición
        self._guide_cache: tuple | None = None       # (work, bounds, plano) -> contornos de la figura en el plano
        self._overhang_cache: tuple | None = None    # (work, bounds, ángulo, máscara, OrientResult)
        self.scene_dirty = True
        self.on_change = None                        # callable() para refrescar la barra lateral
        if self.session.loaded:
            self._after_load()

    # --------------------------------------------------------------- utilidades
    def _changed(self, message: str | None = None, scene: bool = True) -> None:
        if message is not None:
            self.message = message
        if scene:
            self.scene_dirty = True
        if self.on_change:
            self.on_change()

    def _rebuild_picker(self) -> None:
        self.picker = Picker(self.session.work) if self.session.work is not None else None

    def _after_load(self) -> None:
        self._rebuild_picker()
        self.camera.frame(self.session.work.bounds)
        self.camera.set_view("frente")
        self.cancel(quiet=True)

    @property
    def loaded(self) -> bool:
        return self.session.loaded

    @property
    def plane_half_size(self) -> float:
        if not self.loaded:
            return 50.0
        return float(np.linalg.norm(self.session.work.extents)) * 0.5

    # ------------------------------------------------------------- archivos
    def open_stl(self, path: str, crown_z: float | None = None) -> None:
        self.session = Session()
        self.hidden.clear()
        rep = self.session.load(path, crown_z)
        self._after_load()
        msg = f"Cargado {path.split('/')[-1]}"
        if rep.warnings:
            msg += " — " + " ".join(rep.warnings)
        self._changed(msg)

    def open_project(self, path: str, stl_path: str | None = None) -> None:
        self.session = Session.open_project(path, stl_path)
        self.hidden.clear()
        self._after_load()
        self._changed(f"Proyecto abierto: {path.split('/')[-1]}")

    def save_project(self, path: str) -> None:
        self.session.save_project(path)
        self._changed(f"Proyecto guardado: {path.split('/')[-1]}", scene=False)

    # ---------------------------------------------------------------- modos
    def _need_loaded(self) -> bool:
        if not self.loaded:
            self._changed("Primero abrí un STL.", scene=False)
            return False
        return True

    def start_crown(self) -> None:
        if not self._need_loaded():
            return
        if self.session.project.symmetry is not None or self.session.project.limbs:
            self._changed("La coronilla se marca antes de definir la simetría y los miembros "
                          "(cambia la escala de la figura).", scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.CROWN
        self._changed("Hacé clic en la coronilla (el punto más alto sin sombrero ni orejas).")

    def start_symmetry(self) -> None:
        if not self._need_loaded():
            return
        self.cancel(quiet=True)
        self.mode = Mode.SYMMETRY
        self._changed("Plano de simetría: elegí 3 puntos de la línea central (2 en la espalda y 1 en el pecho).")

    def start_limb_plane(self, kind: str) -> None:
        if not self._need_loaded():
            return
        if kind not in _KIND_BASE:
            raise ValueError(kind)
        if self.session.project.symmetry is None:
            self._changed("Primero definí el plano de simetría.", scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.pending_kind = Mode.LIMB_PLANE, kind
        self._changed(f"Plano del {_KIND_BASE[kind]}: elegí 3 puntos sobre la figura.")

    def start_neck_plane(self) -> None:
        if not self._need_loaded():
            return
        if self.session.project.symmetry is None:
            self._changed("Primero definí el plano de simetría.", scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.NECK_PLANE
        self._changed("Plano del cuello: elegí 3 puntos (2 en la nuca, a cada lado, y 1 bajo el mentón). "
                      "Lo de arriba es la cabeza; el pasador va en el centro de la cara del corte.")

    # ------------------------------------------------ articulaciones (rodilla / codo)
    def start_joint_plane(self, limb: str) -> None:
        """Plano de la rodilla (pierna) o del codo (brazo): 3 puntos sobre la figura a esa altura."""
        if not self._need_loaded():
            return
        spec = self.session.limb(limb)
        if spec is None:
            self._changed(f"No hay un miembro «{limb}».", scene=False)
            return
        if self.session.joint_of_limb(limb) is not None:
            self._changed(f"«{limb}» ya tiene su articulación: quitala (−) para rehacerla.", scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.joint_limb = Mode.JOINT_PLANE, limb
        what = "de la rodilla" if spec.kind == "leg" else "del codo"
        self._changed(f"Plano {what}: elegí 3 puntos sobre la figura a la altura donde se dobla. Puede quedar inclinado "
                      "(un brazo que cuelga en diagonal) mientras no sea más de 60° de la horizontal; el eje de la "
                      "bisagra sigue esa inclinación. Lo de arriba es el muslo / brazo.")

    def start_joint_plane_for_kind(self, kind: str) -> None:
        """Rodilla («leg») o codo («arm»): toma el primer miembro de ese tipo que todavía no tiene articulación."""
        if kind not in _KIND_BASE:
            raise ValueError(kind)
        limb = next((l.name for l in self.session.project.limbs
                     if l.kind == kind and self.session.joint_of_limb(l.name) is None), None)
        if limb is None:
            self._changed(f"No hay un {_KIND_BASE[kind]} sin articulación: agregá primero su plano y silueta "
                          "(o quitá la articulación que ya tiene).", scene=False)
            return
        self.start_joint_plane(limb)

    def joint_tilt(self, name: str) -> float:
        d = self.session.joint(name).axis_dir
        return float(np.degrees(np.arctan2(d[2], d[0])))

    def _finish_joint_plane(self) -> None:
        pts, self.pending = self.pending, []
        limb = self.joint_limb
        try:
            plane = Session.plane_from_points(pts, "articulación")
            j = self.session.add_joint(limb, plane, anchor=np.mean(pts, axis=0))
        except (ValueError, KeyError) as e:
            self._changed(f"No sirve: {e}. Elegí 3 puntos de nuevo.")
            return
        self.mode, self.joint_limb = Mode.VIEW, None
        self._changed(f"{j.name.capitalize()} definida con rango {self.joint_range_text(j.name)}; el eje quedó "
                      f"inclinado {self.joint_tilt(j.name):.1f}° siguiendo el plano. Ajustá el eje con «Colocar eje» "
                      "(clic sobre el lado de la figura) y la inclinación si hace falta.")

    def start_joint_axis(self, name: str) -> None:
        if not self._need_loaded():
            return
        if self.session.joint(name) is None:
            self._changed(f"No hay una articulación «{name}».", scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.joint_name = Mode.JOINT_AXIS, name
        self._changed(f"Eje de la {name}: hacé clic sobre el costado de la figura, en el centro de la bisagra "
                      "(se toman la altura y la profundidad del clic; el eje atraviesa el miembro).")

    def _finish_joint_axis(self, hit: PickResult) -> None:
        name = self.joint_name
        try:
            self.session.set_joint_axis(name, hit.point)
        except ValueError as e:
            self._changed(f"No sirve: {e}.")
            return
        self.mode, self.joint_name = Mode.VIEW, None
        self._changed(f"Eje de la {name} movido.")

    def set_joint_tilt(self, name: str, tilt_deg: float) -> bool:
        """Inclina el eje de la bisagra vista de frente (° alrededor de Y; 0 = a lo largo de X; positivo = z sube con x).
        La figura de referencia usa ≈ 14° en la rodilla y ≈ 9,5° en el codo."""
        if not -45.0 <= float(tilt_deg) <= 45.0:
            self._changed("La inclinación tiene que estar entre −45° y 45°.", scene=False)
            return False
        try:
            self.session.set_joint_axis(name, None, tilt_deg)
        except (ValueError, KeyError) as e:
            self._changed(f"No sirve: {e}.", scene=False)
            return False
        self._changed(f"Eje de la {name} inclinado {float(tilt_deg):g}°. Hay que generar de nuevo.")
        return True

    def set_joint_range(self, name: str, lo: float | None, hi: float | None) -> bool:
        try:
            self.session.set_joint_range(name, lo, hi)
        except (ValueError, KeyError) as e:
            self._changed(f"No sirve: {e}.", scene=False)
            return False
        self._changed(f"Rango de la {name}: {self.joint_range_text(name)}. Hay que generar de nuevo.")
        return True

    def joint_limits(self, name: str) -> tuple[float, float]:
        from ..joints import flex_range
        return flex_range(self.session.joint(name), self.session.project.params)

    def set_joint_limit(self, name: str, which: str, value: float) -> bool:
        """Cambia solo el mínimo («lo») o el máximo («hi») del rango de flexión, conservando el otro."""
        lo, hi = self.joint_limits(name)
        return self.set_joint_range(name, float(value), hi) if which == "lo" else self.set_joint_range(name, lo, float(value))

    def joint_range_text(self, name: str) -> str:
        lo, hi = self.joint_limits(name)
        return f"{lo:g}° … {hi:g}° de flexión"

    def cancel(self, quiet: bool = False) -> None:
        was = self.mode
        self.mode, self.pending, self.pending_kind = Mode.VIEW, [], None
        self.draft, self.edit_limb, self.edit_cross = None, None, False
        self.pin_limb, self.pin_prev = None, None
        self.joint_limb, self.joint_name = None, None
        was_trim = self.trim_cfg is not None
        self.trim_cfg, self.edit_trim = None, None
        if not quiet:
            self._changed("Edición del recorte terminada." if was_trim
                          else "Edición de silueta terminada." if was == Mode.POLYGON
                          else "Colocación del pasador terminada." if was == Mode.PIN else "Cancelado.")

    def undo_point(self) -> None:
        if self.mode == Mode.POLYGON and self.draft is not None:
            self._polygon_backspace()
            return
        if self.pending:
            self.pending.pop()
            self._changed(f"Quedan {len(self.pending)} punto(s).")

    def auto_symmetry(self) -> None:
        if not self._need_loaded():
            return
        self.cancel(quiet=True)
        self.session.auto_symmetry()
        self._rebuild_picker()
        self._changed("Simetría automática en x = 0 (la figura ya viene centrada).")

    def set_front(self, front: str) -> None:
        if not self._need_loaded() or front == self.session.project.front:
            return
        self.cancel(quiet=True)
        self.session.set_front(front)
        self._rebuild_picker()
        self.hidden.clear()
        self._changed(f"Frente hacia {front}; se borraron simetría y miembros.")

    def set_one_side(self, on: bool) -> None:
        self.one_side = bool(on)
        if not self.one_side:
            self.mirror_live = False            # el espejo ocupa el lado que se oculta al trabajar un solo lado
        self._changed()

    # ----------------------------------------------------- parámetros y perfiles de línea
    def get_param(self, key: str) -> float:
        obj = self.session.project.params
        *path, last = key.split(".")
        for k in path:
            obj = getattr(obj, k)
        return float(getattr(obj, last))

    def set_param(self, key: str, value: float) -> bool:
        """Cambia un parámetro de `PARAM_FIELDS` (p. ej. «hip_pin.shaft_len»). Cualquier cambio invalida la figura
        generada. Devuelve False (y deja el motivo en `message`) si la clave o el valor no valen."""
        spec = next((f for f in PARAM_FIELDS if f[0] == key), None)
        if spec is None:
            self._changed(f"Parámetro desconocido: {key}.", scene=False)
            return False
        _k, label, lo, hi, _step, _dig = spec
        value = float(value)
        if not (lo - 1e-9 <= value <= hi + 1e-9):
            self._changed(f"{label}: {value:g} está fuera de rango ({lo:g}–{hi:g}).", scene=False)
            return False
        if abs(self.get_param(key) - value) < 1e-12:
            return True
        obj = self.session.project.params
        *path, last = key.split(".")
        for k in path:
            obj = getattr(obj, k)
        setattr(obj, last, value)
        self.session.outcome = None
        self._changed(f"{label}: {value:g}. Hay que generar de nuevo.", scene=False)
        return True

    def profile_names(self, directory: str | None = None) -> list[str]:
        from .. import profile
        return profile.list_profiles(directory)

    def save_profile(self, name: str, directory: str | None = None) -> str | None:
        """Guarda los parámetros actuales como perfil de línea (se reusan en la próxima figura)."""
        from .. import profile
        try:
            path = profile.save_profile(name, self.session.project.params, directory)
        except ValueError as e:
            self._changed(f"No se guardó el perfil: {e}.", scene=False)
            return None
        self._changed(f"Perfil «{name}» guardado en {path}.", scene=False)
        return path

    def load_profile(self, name: str, directory: str | None = None) -> bool:
        """Carga un perfil de línea: reemplaza los parámetros del proyecto (siluetas y planos no se tocan)."""
        from .. import profile
        try:
            params = profile.load_profile(name, directory)
        except (OSError, ValueError, TypeError) as e:
            self._changed(f"No se pudo cargar el perfil «{name}»: {e}.", scene=False)
            return False
        self.session.project.params = params
        self.session.outcome = None
        self._changed(f"Perfil «{name}» cargado (holgura {params.clearance:g} mm). Hay que generar de nuevo.",
                      scene=False)
        return True

    def export_probes(self, outdir: str, which: str = "hip", clearances=None, shaft_deltas=None) -> dict | None:
        """Escribe la placa de probetas (`probetas.stl` + `probetas.txt`) con los parámetros actuales.
        `which`: «hip» (cadera), «shoulder» (hombro) o «hinge» (bisagra de rodilla/codo) a variar."""
        import os
        from .. import probes, solids as S
        p = self.session.project.params
        kw = {}
        if clearances is not None:
            kw["clearances"] = clearances
        if which == "hinge":      # rodilla / codo: `shaft_deltas` varía el Ø del bulón
            if shaft_deltas is not None:
                kw["pin_deltas"] = shaft_deltas
            cells = probes.hinge_probe_cells(p, **kw)
            mesh, text = probes.build_hinge_probe(p, cells), probes.hinge_labels_text(cells)
        else:
            base = p.hip_pin if which == "hip" else p.shoulder_pin
            if shaft_deltas is not None:
                kw["shaft_deltas"] = shaft_deltas
            cells = probes.probe_cells(p, base=base, **kw)
            mesh, text = probes.build_probe(p, cells), probes.labels_text(cells)
        os.makedirs(outdir, exist_ok=True)
        stl = os.path.join(outdir, "probetas.stl")
        S.to_trimesh(mesh).export(stl)
        txt = os.path.join(outdir, "probetas.txt")
        with open(txt, "w") as f:
            f.write(text + "\n")
        kind = {"hip": "cadera", "shoulder": "hombro", "hinge": "bisagra de rodilla/codo"}.get(which, which)
        self._changed(f"Probetas ({len(cells)} articulaciones, pasador de {kind}) "
                      f"en {outdir}. Imprimilas, cargá en «Parámetros» la combinación elegida y guardala en el perfil.",
                      scene=False)
        return {"stl": stl, "tabla": txt, "celdas": len(cells)}

    def apply_probe_result(self, clearance: float, shaft_d: float, which: str = "both") -> None:
        """Guarda en los parámetros la combinación holgura / Ø de vástago elegida con las probetas."""
        from .. import profile
        self.session.project.params = profile.apply_probe_result(self.session.project.params, clearance, shaft_d, which)
        self.session.outcome = None
        self._changed(f"Probetas: holgura {clearance:g} mm y vástago Ø{shaft_d:g} mm. Hay que generar de nuevo.",
                      scene=False)

    # ------------------------------------------------------------ siluetas
    def start_polygon(self, name: str, cross: bool = False) -> None:
        """Entra al modo de dibujo de la silueta del miembro `name` (la que ya tenga se carga para editarla).

        Con `cross` se edita su segundo corte: la silueta sobre el plano perpendicular al principal (el frontal,
        normalmente). El miembro es lo que queda dentro de las dos siluetas, como en Onshape (un recorte de
        costado más uno de frente)."""
        if not self._need_loaded():
            return
        limb = self.session.limb(name)
        if limb is None:
            self._changed(f"No existe el plano «{name}».", scene=False)
            return
        self.cancel(quiet=True)
        if cross:
            plane = self.session.cross_plane(name)
            poly = limb.cross.polygon if limb.cross is not None else np.zeros((0, 3))
            what = f"2.º corte de «{name}» (sobre el plano «{plane.name}»)"
        else:
            plane, poly, what = limb.plane, limb.polygon, f"Silueta de «{name}»"
        if len(poly) >= 3:
            self.draft = PolygonDraft.from_world(plane, poly)
            msg = (f"{what}: clic cerca de un vértice para elegirlo y otro clic para moverlo; "
                   "clic sobre un borde agrega un vértice; Retroceso borra el elegido; Esc termina.")
        else:
            self.draft = PolygonDraft(plane)
            msg = (f"{what}: hacé clic para poner vértices sobre el plano (mirá el plano de frente, "
                   "vista Frente/Izquierda). Cerrá con un clic en el primero o con «Cerrar polígono».")
            if cross:
                msg += (" Cubrí el miembro visto de frente; en una pierna incluí la columna del muslo desde la "
                        "simetría y el escalón de la cadera.")
        self.mode, self.edit_limb, self.edit_cross = Mode.POLYGON, name, cross
        if cross:
            self.camera.look_along(plane.normal, target=plane.origin)
        self._changed(msg)

    def raise_top(self) -> None:
        """En el 2.º corte en edición: sube su borde superior hasta el de la silueta principal. Sirve para que el
        escalón de la cadera no limite la altura de la cara de contacto (ni la del hueco y el pasador)."""
        d = self.draft
        if self.mode != Mode.POLYGON or d is None or not self.edit_cross or not d.closed:
            self._changed("Abrí el 2.º corte de un miembro (botón «2.º») y cerrá su polígono.", scene=False)
            return
        spec = self.session.limb(self.edit_limb)
        top = self.session._other_top(spec, cross=True)
        if top is None:
            self._changed("Falta la silueta principal del miembro: es la que marca hasta dónde sube.", scene=False)
            return
        from .session import _raise_top
        w = d.world()
        new = _raise_top(w, top)
        if np.allclose(new, w):
            self._changed("El borde superior ya llega hasta el de la silueta principal.", scene=False)
            return
        self.draft = PolygonDraft.from_world(d.plane, new)
        self._commit_polygon(f"Borde superior subido a z = {top:.1f} mm")

    def remove_cross(self, name: str) -> None:
        """Quita el segundo corte de `name`."""
        if self.edit_limb == name and self.edit_cross:
            self.cancel(quiet=True)
        if self.session.clear_limb_cross(name):
            self._changed(f"2.º corte de «{name}» quitado.")
        else:
            self._changed(f"«{name}» no tiene 2.º corte.", scene=False)

    def _click_polygon(self, px: float, py: float, width: float, height: float) -> None:
        d = self.draft
        origin, direction = self.camera.ray(px, py, width, height)
        uv = ray_plane_uv(d.plane, origin, direction)
        if uv is None:
            self._changed("El plano se ve de canto (o queda atrás) desde esta vista: girá la cámara "
                          "hasta verlo de frente.", scene=False)
            return
        screen = self.camera.project(d.world(), width, height)[:, :2] if d.pts else np.zeros((0, 2))
        click = (px, py)
        near = d.nearest_vertex(screen, click, VERTEX_PX)
        if not d.closed:
            if near == 0 and len(d.pts) >= 3:
                self.close_polygon()
            elif near is not None:
                self._changed("Ya hay un vértice ahí.", scene=False)
            else:
                d.add(uv)
                self._changed(f"Vértice {len(d.pts)}. Retroceso deshace el último; clic en el primero cierra.")
            return
        if near is not None:
            d.selected = None if d.selected == near else near
            self._changed(f"Vértice {near + 1} elegido: clic en el plano para moverlo." if d.selected is not None
                          else "Vértice soltado.")
        elif d.selected is not None:
            d.move(d.selected, uv)
            d.selected = None
            self._commit_polygon("Vértice movido")
        else:
            edge = d.nearest_edge(screen, click, EDGE_PX)
            if edge is None:
                self._changed("Clic cerca de un vértice para elegirlo, o sobre un borde para agregar uno.", scene=False)
            else:
                d.insert(edge, uv)
                self._commit_polygon("Vértice agregado")

    def _commit_polygon(self, what: str = "Silueta guardada") -> bool:
        """Guarda el polígono cerrado en el proyecto (solo si es válido)."""
        d = self.draft
        if d is None or not d.closed:
            return False
        if self.trim_cfg is not None:
            return self._commit_trim(what)
        err = d.validity_error()
        if err:
            self._changed(f"{what}, pero la silueta {err}: corregila (no se guardó en el proyecto).")
            return False
        if self.edit_cross:
            self.session.set_limb_cross(self.edit_limb, d.world())
            self._changed(f"{what}. 2.º corte de «{self.edit_limb}»: {len(d.pts)} vértices.")
        else:
            self.session.set_limb_polygon(self.edit_limb, d.world())
            self._changed(f"{what}. Silueta de «{self.edit_limb}»: {len(d.pts)} vértices.")
        return True

    def close_polygon(self) -> None:
        d = self.draft
        if self.mode != Mode.POLYGON or d is None:
            return
        if not d.close():
            self._changed("Hacen falta al menos 3 vértices para cerrar.", scene=False)
            return
        self._commit_polygon("Polígono cerrado")

    def _polygon_backspace(self) -> None:
        d = self.draft
        if not d.closed:
            if d.pts:
                d.pts.pop()
                self._changed(f"Quedan {len(d.pts)} vértice(s).")
        elif d.selected is not None:
            if len(d.pts) <= 3:
                self._changed("Un polígono necesita al menos 3 vértices.", scene=False)
            else:
                d.delete(d.selected)
                self._commit_polygon("Vértice borrado")
        else:
            self._changed("Elegí un vértice (clic cerca) para borrarlo.", scene=False)

    def clear_polygon(self) -> None:
        """Vacía el borrador para dibujar de nuevo (la silueta guardada se reemplaza al cerrar el nuevo polígono)."""
        if self.mode == Mode.POLYGON and self.draft is not None:
            self.draft.clear()
            self._changed("Silueta vaciada: dibujá los vértices de nuevo.")

    def guide_outline(self, plane: Plane) -> list[dict]:
        """Contornos de la figura (lado de trabajo) proyectados sobre `plane`, para calcar encima."""
        work = self.session.work
        key = (work.bounds.tobytes(), plane.origin.tobytes(), plane.normal.tobytes())
        c = self._guide_cache
        if c is None or c[0] is not work or c[1] != key:
            c = self._guide_cache = (work, key, self.session.silhouette(plane, True, 0.1))
        return c[2]

    def use_outline_as_base(self) -> None:
        """Arranca el polígono desde el contorno exterior más grande de la figura sobre el plano."""
        from shapely.geometry import Polygon

        d = self.draft
        if self.mode != Mode.POLYGON or d is None:
            return
        shapes = [Polygon(g["exterior"]) for g in self.guide_outline(d.plane)]
        shapes = [g for g in shapes if g.is_valid and g.area > 1.0]
        if not shapes:
            self._changed("No hay contorno de la figura sobre este plano.", scene=False)
            return
        ring = np.asarray(max(shapes, key=lambda g: g.area).simplify(0.6).exterior.coords)[:-1]
        d.pts, d.closed, d.selected = [p.copy() for p in ring], True, None
        self._commit_polygon("Contorno de la figura usado como base; recortalo moviendo/borrando vértices")

    def propose_silhouette(self, crotch_z: float | None = None) -> None:
        """Reemplaza el borrador por la silueta que propone el programa para el miembro en edición."""
        d = self.draft
        if self.mode != Mode.POLYGON or d is None:
            return
        if self.edit_limb is None:
            self._changed("La propuesta automática es para la silueta de un miembro, no para un recorte.", scene=False)
            return
        try:
            poly = self.session.propose_silhouette(self.edit_limb, crotch_z, cross=self.edit_cross)
        except (ValueError, KeyError) as e:
            self._changed(str(e), scene=False)
            return
        self.draft = PolygonDraft.from_world(d.plane, poly)
        self._changed("Silueta propuesta: revisala y corregí los vértices que haga falta (en piernas con abrigo o "
                      "faldón largo suele convenir subir la cadera).")

    # -------------------------------------------------------------- pasador
    def start_pin(self, name: str) -> None:
        """Entra al modo de colocación del pasador del miembro `name`.

        Se calcula la cara de contacto (el plano de corte donde va el pasador) y la cámara se pone de frente
        a ella con la parte del torso recortada; el clic elige el centro del pasador sobre esa cara.
        """
        if not self._need_loaded():
            return
        if self.session.limb(name) is None:
            self._changed(f"No existe el plano «{name}».", scene=False)
            return
        self.cancel(quiet=True)
        fixed = self.session.fix_limb_extrude(name)
        try:
            prev = self.session.pin_preview(name)
        except (ValueError, KeyError) as e:
            self._changed(f"No se puede colocar el pasador de «{name}»: {e}.", scene=False)
            return
        self.mode, self.pin_limb, self.pin_prev = Mode.PIN, name, prev
        face = prev.face
        half = float(max(np.ptp(prev.plane.to_uv(r), axis=0).max() for r in prev.rings_world())) * 0.65 or 10.0
        self.camera.look_along(face.outward, face.center, max(half, 8.0))
        self.hidden.discard(name)
        note = (f"«{name}» tiene el plano de costado: ahora se extruye hacia afuera (el plano es la cara de corte) "
                "y se descartó el pasador anterior. ") if fixed else ""
        self._changed(note + f"Pasador de «{name}»: " + self._pin_status(name) + self._reach_warning(name)
                      + " Clic = mover; flechas 0,1 mm (Mayús 1 mm); Esc termina.")

    def _pin_center(self, name: str) -> np.ndarray:
        """Centro del pasador que se usaría ahora: el indicado, o el que propone el programa."""
        spec = self.session.limb(name)
        if spec.pin_center is not None:
            return np.asarray(spec.pin_center, float)
        return self.pin_prev.default_center

    def _reach_warning(self, name: str) -> str:
        """Aviso (una sola vez, al entrar al modo): la cara está tan cerca de la simetría que el pasador cruzaría
        al del espejo. No depende de dónde se ponga el pasador sobre la cara, así que no se repite en cada clic."""
        reach = self.session.pin_reach_margin(name)
        if reach is None or reach >= 0:
            return ""
        return f" Aviso: cruza con el del otro lado por {-reach:.1f} mm (acortá shaft_len / head_h o usá un plano más afuera)."

    def _pin_status(self, name: str) -> str:
        """Una línea corta: qué posición se usa y el margen al borde. Es informativa: no impide elegir ningún punto
        (salvo que el vástago no toque la cara, ver `Session.set_limb_pin`)."""
        prev = self.pin_prev
        uv = prev.project(self._pin_center(name))
        pin = self.session.pin_spec(name)
        inside, margin = prev.fit(uv, pin.shaft_d)
        user = self.session.limb(name).pin_center is not None
        txt = "posición elegida" if user else "posición automática"
        if not inside:
            return txt + f"; centro fuera de la cara (vástago Ø{pin.shaft_d:g} apoya {max(margin + pin.shaft_d / 2, 0):.1f} mm)."
        if margin < 0:
            return txt + f"; el vástago sobresale {-margin:.1f} mm del borde."
        if margin < self.session.project.params.min_wall:
            return txt + f"; margen al borde {margin:.1f} mm (< {self.session.project.params.min_wall:g})."
        return txt + f"; margen al borde {margin:.1f} mm."

    def _place_pin(self, uv, what: str = "Pasador movido") -> bool:
        name = self.pin_limb
        world = self.pin_prev.plane.to_world(np.asarray(uv, float)[None])[0]
        try:
            self.session.set_limb_pin(name, world)
        except ValueError as e:
            self._changed(f"No se movió: {e}.", scene=False)
            return False
        self._changed(f"{what}: " + self._pin_status(name))
        return True

    def _click_pin(self, px: float, py: float, width: float, height: float) -> None:
        origin, direction = self.camera.ray(px, py, width, height)
        uv = ray_plane_uv(self.pin_prev.plane, origin, direction)
        if uv is None:
            self._changed("La cara de contacto se ve de canto desde esta vista: usá «Mirar la cara de frente».",
                          scene=False)
            return
        self._place_pin(uv)

    def nudge_pin(self, du: float, dv: float, big: bool = False) -> None:
        """Mueve el pasador `du`, `dv` pasos sobre la cara (u: derecha, v: arriba, vistos de frente)."""
        if self.mode != Mode.PIN or self.pin_prev is None:
            return
        step = NUDGE_MM * (10.0 if big else 1.0)
        uv = self.pin_prev.project(self._pin_center(self.pin_limb)) + np.array([du, dv], float) * step
        self._place_pin(uv, "Pasador ajustado")

    def reset_pin(self) -> None:
        """Descarta la posición elegida y vuelve a la que propone el programa."""
        if self.mode != Mode.PIN or self.pin_prev is None:
            return
        self.session.set_limb_pin(self.pin_limb, None)
        self._changed("Pasador restablecido: " + self._pin_status(self.pin_limb))

    def face_view(self) -> None:
        """Vuelve a mirar de frente la cara de contacto (por si se orbitó la cámara)."""
        if self.mode != Mode.PIN or self.pin_prev is None:
            return
        f = self.pin_prev.face
        self.camera.look_along(f.outward, f.center)
        self._changed(scene=False)

    # --------------------------------------------------------- espejado en vivo
    def mirror_active(self) -> bool:
        """El espejo se dibuja cuando se trabaja un solo lado, hay plano de simetría y no se está en la vista de pose."""
        return bool(self.mirror_live and self.one_side and self.loaded and self.session.project.symmetry is not None
                    and not self.pose_active())

    def set_mirror_live(self, on: bool) -> None:
        """Muestra, en el lado oculto (x < 0), el espejo en vivo del lado de trabajo (x > 0): la figura, las siluetas
        (también la que se está dibujando), los recortes y los pasadores elegidos. Es lo que va a salir en la figura
        generada, que se arma con un solo lado y su espejo. Al activarlo se pasa a trabajar un solo lado."""
        if on and (not self._need_loaded() or self.session.project.symmetry is None):
            if self.loaded:
                self._changed("Primero definí el plano de simetría: el espejo se hace respecto de él.", scene=False)
            self.mirror_live = False
            return
        self.mirror_live = bool(on)
        if self.mirror_live:
            self.one_side = True
        self._changed("Espejo en vivo: lo que hacés en el lado +X se ve reflejado en el otro." if self.mirror_live
                      else "Espejo en vivo desactivado.")

    # ----------------------------------------------- pose sentada (vista previa y colisiones)
    def pose_active(self) -> bool:
        return self.pose_view and self.session.outcome is not None

    def pose_limit(self) -> float | None:
        """Mayor ángulo de cadera tallado en el torso entre las piernas generadas (límite de giro), o None."""
        out = self.session.outcome
        if out is None:
            return None
        limits = [a1 for n, (_limb, _c, _ax, (a0, a1)) in out.result.motion.items()
                  if a1 > a0 and n != HEAD_NAME]
        return max(limits) if limits else None

    def _pose_angle(self) -> float:
        return float(self.session.project.params.sit_angle if self.pose_angle is None else self.pose_angle)

    def _posed_pieces(self, angle: float) -> dict:
        """Miembros de la figura generada con las piernas giradas `angle` grados (izquierdos y sus espejos)."""
        out = self.session.outcome
        c = self._pose_cache
        if c is not None and c[0] is out and abs(c[1] - angle) < 1e-9:
            return c[2]
        from ..pipeline import posed_pieces       # cadena muslo/pantorrilla, brazo/antebrazo; la cabeza no participa
        pieces = posed_pieces(out.result, angle)
        self._pose_cache = (out, angle, pieces)
        return pieces

    def pose_check(self, angle: float | None = None) -> dict | None:
        """Interferencias de la figura generada con las piernas giradas `angle` grados (None = el de la vista previa).

        Devuelve ``{"angle", "limit", "torso": {pieza: mm³}, "pairs": {(a, b): mm³}, "ok"}``; solo figuran las
        interferencias mayores que la tolerancia de `pipeline.validate`. None si todavía no se generó la figura."""
        out = self.session.outcome
        if out is None:
            return None
        angle = self._pose_angle() if angle is None else float(angle)
        pieces = self._posed_pieces(angle)
        torso = out.result.pieces["torso"]
        hits = {n: float((m ^ torso).volume()) for n, m in pieces.items()}
        names = list(pieces)
        pairs = {}
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                v = float((pieces[a] ^ pieces[b]).volume())
                if v > POSE_TOL_MM3:
                    pairs[(a, b)] = round(v, 5)
        torso_hits = {n: round(v, 5) for n, v in hits.items() if v > POSE_TOL_MM3}
        return {"angle": angle, "limit": self.pose_limit(), "torso": torso_hits, "pairs": pairs,
                "ok": not torso_hits and not pairs}

    @staticmethod
    def pose_message(r: dict) -> str:
        if r["ok"]:
            txt = f"Pose a {r['angle']:.0f}°: sin colisiones"
        else:
            parts = []
            if r["torso"]:
                parts.append("choca con el torso: " + ", ".join(f"{n} ({v:.2f} mm³)" for n, v in r["torso"].items()))
            if r["pairs"]:
                parts.append("se cruzan " + ", ".join(f"{a} con {b} ({v:.2f} mm³)" for (a, b), v in r["pairs"].items()))
            txt = f"Pose a {r['angle']:.0f}°: " + "; ".join(parts)
        if r["limit"] is not None and r["angle"] > r["limit"] + 1e-6:
            txt += f" (pasa del límite de cadera tallado en el torso, {r['limit']:.0f}°)"
        return txt + "."

    def _pose_update(self) -> None:
        self.pose_report = self.pose_check()
        self._changed(self.pose_message(self.pose_report))

    def set_pose_view(self, on: bool) -> None:
        """Vista previa de la figura generada (torso + miembros + espejos) con las piernas en el ángulo de la pose
        sentada; las piezas que interfieren se pintan de rojo. Hace falta haber generado la figura."""
        if not on:
            was = self.pose_view
            self.pose_view, self.pose_report = False, None
            self._changed("Vista previa de la pose terminada." if was else None)
            return
        if self.session.outcome is None:
            self.pose_view = False
            self._changed("Primero generá la figura: la vista previa de la pose usa las piezas generadas.", scene=False)
            return
        self.cancel(quiet=True)
        self.pose_view = True
        self.pose_angle = float(self.session.project.params.sit_angle)
        self._pose_update()

    def set_pose_angle(self, angle: float) -> None:
        """Cambia el ángulo de cadera de la vista previa (0° = de pie, 90° = sentada)."""
        self.pose_angle = float(np.clip(angle, 0.0, 180.0))
        if self.pose_active():
            self._pose_update()

    # ------------------------------------------------------- recortes manuales (plan §8.8)
    def trim_targets(self) -> list[str]:
        return (["torso"] + [l.name for l in self.session.project.limbs]
                + ([HEAD_NAME] if self.session.project.head is not None else []))

    @staticmethod
    def _trim_plane() -> Plane:
        return Plane.from_normal((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), SYMMETRY_PLANE_NAME)

    def trim_entries(self) -> list[dict]:
        out = []
        for i, t in enumerate(self.session.project.trims):
            out.append({"index": i, "target": t.target, "shape": t.shape, "op": t.op,
                        "label": f"{i + 1}. {TRIM_OP_LABELS.get(t.op, t.op).capitalize()} {TRIM_SHAPE_LABELS.get(t.shape, t.shape)}"
                                 f" en «{t.target}»"})
        return out

    def _check_trim_args(self, target: str, op: str, length: float) -> str | None:
        """Motivo por el que los datos de un recorte no sirven, o None."""
        if self.session.project.symmetry is None:
            return "Primero definí el plano de simetría: los recortes se hacen sobre el lado de trabajo"
        if target not in self.trim_targets():
            return f"No existe la pieza «{target}» (usá «torso», «cabeza» o el nombre de un miembro)"
        if op not in TRIM_OP_LABELS:
            return "La operación debe ser «subtract» (restar) o «union» (unir)"
        if not (0.1 <= float(length) <= 500.0):
            return "La distancia de extrusión tiene que estar entre 0,1 y 500 mm"
        return None

    def start_trim(self, target: str = "torso", op: str = "subtract", distance: float = 25.0,
                   symmetric: bool = True, index: int | None = None) -> None:
        """Dibuja un recorte de polígono sobre el plano de simetría (se mira de costado): polígono → extrusión →
        restar / unir sobre `target` ("torso" o un miembro). Con `index` se edita el polígono de un recorte existente.
        Se aplica al lado de trabajo antes del espejado, así que sale igual en ambos lados."""
        if not self._need_loaded():
            return
        trims = self.session.project.trims
        if index is not None:
            if not (0 <= index < len(trims)):
                self._changed(f"No existe el recorte {index + 1}.", scene=False)
                return
            t = trims[index]
            if t.shape != "polygon":
                self._changed("Solo los recortes de polígono se editan con vértices; quitalo y colocá otro.", scene=False)
                return
            target, op, distance, symmetric = t.target, t.op, t.distance, t.symmetric
            plane, poly = t.plane, np.asarray(t.polygon, float)
        else:
            plane, poly = self._trim_plane(), np.zeros((0, 3))
        err = self._check_trim_args(target, op, distance)
        if err:
            self._changed(err + ".", scene=False)
            return
        self.cancel(quiet=True)
        self.trim_cfg = {"target": target, "op": op, "distance": float(distance), "symmetric": bool(symmetric)}
        self.edit_trim = index
        self.draft = PolygonDraft.from_world(plane, poly) if len(poly) >= 3 else PolygonDraft(plane)
        self.mode = Mode.POLYGON
        self.camera.look_along(plane.normal, target=self.session.work.bounds.mean(axis=0))
        what = f"{TRIM_OP_LABELS[op]} en «{target}»"
        self._changed(f"Recorte ({what}): " + (
            "clic en un vértice para elegirlo y otro clic para moverlo; clic sobre un borde agrega uno; Retroceso borra."
            if len(poly) >= 3 else
            "hacé clic para poner vértices sobre el plano de simetría (vista de costado); cerrá con un clic en el "
            "primero o con Intro. Se extruye " + ("hacia ambos lados" if symmetric else "hacia +X") + f" {distance:g} mm."))

    def _commit_trim(self, what: str) -> bool:
        d, cfg = self.draft, self.trim_cfg
        err = d.validity_error()
        if err:
            self._changed(f"{what}, pero el polígono {err}: corregilo (no se guardó el recorte).")
            return False
        world = d.world()
        trims = self.session.project.trims
        if self.edit_trim is None:
            self.session.add_trim(Trim(cfg["target"], d.plane, world, cfg["distance"], cfg["symmetric"], cfg["op"]))
            self.edit_trim = len(trims) - 1
            self._changed(f"{what}. Recorte {self.edit_trim + 1} guardado: {TRIM_OP_LABELS[cfg['op']]} en «{cfg['target']}».")
        else:
            trims[self.edit_trim].polygon = world
            self.session.outcome = None
            self._changed(f"{what}. Recorte {self.edit_trim + 1} actualizado.")
        return True

    def add_trim_primitive(self, target: str, shape: str, center, size, op: str = "subtract",
                           length: float = 25.0) -> int | None:
        """Agrega un recorte de forma predefinida centrado en `center` (marco ``work``): esfera de radio `size`, caja
        de arista `size` (o de 3 aristas x, y, z) o cilindro de radio `size` con el eje en X y `length` de largo.
        Devuelve su índice, o None (con el motivo en `message`) si no sirve."""
        if shape not in ("sphere", "box", "cylinder"):
            self._changed("Forma desconocida: usá esfera, caja o cilindro (o «Dibujar recorte» para un polígono).",
                          scene=False)
            return None
        err = self._check_trim_args(target, op, length)
        if err is None and not np.all(np.asarray(size, float) > 0):
            err = "El tamaño tiene que ser positivo"
        if err:
            self._changed(err + ".", scene=False)
            return None
        c, plane = np.asarray(center, float), self._trim_plane()
        if shape == "sphere":
            pts = c + float(size) * np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], float)
        elif shape == "cylinder":
            pts = circle_world(plane, c, float(size), 16)
        else:
            half = np.broadcast_to(np.asarray(size, float), (3,)) / 2.0
            pts = np.array([c - half, c + half])
        self.session.add_trim(Trim(target, plane, pts, float(length), True, op, shape))
        idx = len(self.session.project.trims) - 1
        self._changed(f"Recorte {idx + 1} agregado: {TRIM_OP_LABELS[op]} {TRIM_SHAPE_LABELS[shape]} en «{target}».")
        return idx

    def start_trim_pick(self, target: str, shape: str, size, op: str = "subtract", length: float = 25.0) -> None:
        """Un clic sobre la figura pone el centro de un recorte esfera / caja / cilindro (después se puede quitar o
        cambiar de operación en la lista)."""
        if not self._need_loaded():
            return
        if shape not in ("sphere", "box", "cylinder"):
            self._changed("Elegí esfera, caja o cilindro.", scene=False)
            return
        err = self._check_trim_args(target, op, length)
        if err:
            self._changed(err + ".", scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.TRIM_PICK
        self.trim_cfg = {"target": target, "op": op, "distance": float(length), "symmetric": True,
                         "shape": shape, "size": size}
        self._changed(f"Recorte {TRIM_SHAPE_LABELS[shape]} en «{target}»: hacé clic en la figura donde va el centro "
                      "(en el lado +X); Esc cancela.")

    def _sync_joints(self, scene) -> None:
        """Dibuja el eje de cada bisagra como una línea amarilla (sin prueba de profundidad)."""
        wanted = set()
        for j in self.session.project.joints:
            key = f"eje:{j.name}"
            wanted.add(key)
            c, d = np.asarray(j.axis_center, float), np.asarray(j.axis_dir, float)
            d = d / np.linalg.norm(d)
            scene.set_lines(key, np.array([c - d * 9.0, c + d * 9.0]), (1.0, 0.85, 0.1, 1.0), on_top=True)
        for key in [k for k in scene.items if k.startswith("eje:") and k not in wanted]:
            scene.remove(key)

    def _finish_trim_pick(self, hit: PickResult) -> None:
        cfg = self.trim_cfg
        center = np.asarray(hit.point, float).copy()
        center[0] = abs(center[0])        # el lado de trabajo es +X: lo que se elige del otro lado se espeja
        idx = self.add_trim_primitive(cfg["target"], cfg["shape"], center, cfg["size"], cfg["op"], cfg["distance"])
        if idx is not None:
            self.cancel(quiet=True)
            self._changed(self.message + " Generá de nuevo para ver el resultado.")

    def remove_trim(self, index: int) -> None:
        if not (0 <= index < len(self.session.project.trims)):
            self._changed(f"No existe el recorte {index + 1}.", scene=False)
            return
        if self.trim_cfg is not None:
            self.cancel(quiet=True)
        self.session.remove_trim(index)
        self._changed(f"Recorte {index + 1} quitado. Generá de nuevo.")

    def set_trim_op(self, index: int, op: str) -> None:
        trims = self.session.project.trims
        if not (0 <= index < len(trims)) or op not in TRIM_OP_LABELS:
            self._changed("Recorte u operación inválidos.", scene=False)
            return
        if trims[index].op != op:
            trims[index].op = op
            self.session.outcome = None
        self._changed(f"Recorte {index + 1}: {TRIM_OP_LABELS[op]}. Generá de nuevo.")

    @staticmethod
    def trim_wires(t: Trim) -> list[np.ndarray]:
        """Líneas (Nx3) que muestran un recorte en el visor: el polígono, tres círculos (esfera), dos círculos
        (cilindro) o las aristas de la caja."""
        pts = np.asarray(t.polygon, float)
        if t.shape == "polygon" or len(pts) == 0:
            return [pts]
        c = pts.mean(axis=0)
        if t.shape == "sphere":
            r = float(np.linalg.norm(pts - c, axis=1).max())
            a = np.linspace(0.0, 2 * np.pi, 48, endpoint=False)
            e = np.eye(3)
            return [c + r * (np.cos(a)[:, None] * e[i] + np.sin(a)[:, None] * e[j]) for i, j in ((0, 1), (0, 2), (1, 2))]
        if t.shape == "cylinder":
            r = float(np.linalg.norm(pts - c, axis=1).max())
            n = t.plane.normal * t.distance / 2.0
            return [circle_world(t.plane, c - n, r), circle_world(t.plane, c + n, r)]
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        return [np.array([corners[i], corners[j]]) for i in range(8) for j in range(i + 1, 8)
                if bin(i ^ j).count("1") == 1]

    # ------------------------------------------------------------- voladizos
    def overhang_faces(self) -> tuple[np.ndarray, OrientResult] | None:
        """Máscara de caras voladizas de ``session.work`` y la orientación de impresión usada.

        Es una referencia sobre la figura entera en su pose actual (no sobre las piezas ya cortadas):
        se elige la orientación de menor voladizo (`best_orientation`) y se marca, sobre la malla
        original, qué caras quedan colgando más allá de ``overhang_angle``. La rotación conserva el
        orden de las caras, así que la máscara sirve tal cual para dibujar ``work``.
        """
        work = self.session.work
        if work is None:
            return None
        key_bounds = work.bounds.tobytes()
        c = self._overhang_cache
        if c is None or c[0] is not work or c[1] != key_bounds or c[2] != self.overhang_angle:
            res = best_orientation(work, self.overhang_angle)
            oriented = work.copy()
            oriented.apply_transform(res.rotation)
            c = self._overhang_cache = (work, key_bounds, self.overhang_angle,
                                        overhang_mask(oriented, self.overhang_angle), res)
        return c[3], c[4]

    def _overhang_message(self) -> str:
        found = self.overhang_faces()
        if found is None:
            return "Primero abrí un STL."
        _mask, res = found
        return (f"Voladizos en rojo (pared a más de {self.overhang_angle:.0f}° de la vertical): "
                f"{res.overhang_area:.0f} mm² con la figura apoyada en {res.label}. "
                "Referencia sobre la figura entera, no sobre las piezas.")

    def set_show_overhangs(self, on: bool) -> None:
        self.show_overhangs = bool(on)
        self._changed(self._overhang_message() if self.show_overhangs and self.loaded else None)

    def set_overhang_angle(self, angle: float) -> None:
        self.overhang_angle = float(np.clip(angle, 10.0, 80.0))
        self._changed(self._overhang_message() if self.show_overhangs and self.loaded else None)

    # ----------------------------------------------------------------- clics
    def click(self, px: float, py: float, width: float, height: float) -> PickResult | None:
        """Clic sobre el visor (píxeles, y hacia abajo). Devuelve el punto elegido si hubo impacto."""
        if self.busy:
            return None
        if self.mode == Mode.POLYGON and self.draft is not None:
            self._click_polygon(px, py, width, height)
            return None
        if self.mode == Mode.PIN and self.pin_prev is not None:
            self._click_pin(px, py, width, height)
            return None
        if self.picker is None or self.mode == Mode.VIEW:
            return None
        hit = self.picker.pick_pixel(self.camera, px, py, width, height)
        if hit is None:
            self._changed("El clic no tocó la figura.", scene=False)
            return None
        if self.mode == Mode.CROWN:
            self._finish_crown(hit)
        elif self.mode == Mode.TRIM_PICK:
            self._finish_trim_pick(hit)
        elif self.mode == Mode.JOINT_AXIS:
            self._finish_joint_axis(hit)
        else:
            self.pending.append(hit.point.copy())
            if len(self.pending) < 3:
                self._changed(f"Punto {len(self.pending)}/3. Backspace deshace el último; Esc cancela.")
            elif self.mode == Mode.SYMMETRY:
                self._finish_symmetry()
            elif self.mode == Mode.NECK_PLANE:
                self._finish_neck_plane()
            elif self.mode == Mode.JOINT_PLANE:
                self._finish_joint_plane()
            else:
                self._finish_limb_plane()
        return hit

    def _finish_crown(self, hit: PickResult) -> None:
        z = float(hit.point[2])
        try:
            self.session.set_crown_from_mm(z)
        except ValueError as e:
            self._changed(str(e))
            return
        self._rebuild_picker()
        self.camera.frame(self.session.work.bounds)
        self.cancel(quiet=True)
        self._changed(f"Coronilla marcada a {z:.1f} mm: la figura se reescaló a 100 mm.")

    def _finish_symmetry(self) -> None:
        pts, self.pending = self.pending, []
        try:
            plane = Session.plane_from_points(pts, "simetría")
            self.session.set_symmetry(plane)
        except ValueError as e:
            self._changed(f"No sirve: {e}. Elegí 3 puntos de nuevo.")
            return
        self._rebuild_picker()
        self.mode = Mode.VIEW
        self._changed("Plano de simetría definido; la figura quedó centrada en él (x = 0).")

    def _finish_neck_plane(self) -> None:
        pts, self.pending = self.pending, []
        try:
            self.session.set_head(Session.plane_from_points(pts, HEAD_NAME))
        except ValueError as e:
            self._changed(f"No sirve: {e}. Elegí 3 puntos de nuevo.")
            return
        self.mode = Mode.VIEW
        self._changed("Plano del cuello definido: la cabeza se separa y gira libre sobre un pasador en el cuello.")

    def set_head_replacement(self, path: str, yaw: float | None = None, up: str | None = None) -> bool:
        """Cabeza de reemplazo (STL): descarta la original, sin simetría, base escalada a la del cuello."""
        if not self._need_loaded():
            return False
        cur = self.session.project.head
        yaw = (cur.replacement_yaw if cur else 0.0) if yaw is None else yaw
        up = (cur.replacement_up if cur else "+Z") if up is None else up
        try:
            info = self.session.set_head_replacement(path, yaw, up)
        except Exception as e:                     # archivo ilegible, malla irreparable, plano sin cabeza…
            self._changed(f"No se pudo usar esa cabeza: {e}", scene=False)
            return False
        msg = (f"Cabeza de reemplazo lista: escala ×{info['escala']:.3f} (uniforme), alto {info['alto_mm']:.1f} mm, "
               f"base coincide {100 * info['coincidencia_base']:.0f} %.")
        if info["base_cortada"]:
            msg += " No traía base plana: se cortó una fracción baja para apoyarla."
        if info["coincidencia_base"] < MIN_BASE_IOU:
            msg += " AVISO: la base se parece poco a la del cuello; probá otro giro o eje «arriba»."
        self._changed(msg)
        return True

    def clear_head_replacement(self) -> None:
        self._changed("Se vuelve a usar la cabeza original del modelo." if self.session.clear_head_replacement()
                      else "No había cabeza de reemplazo.")

    def _finish_limb_plane(self) -> None:
        pts, self.pending = self.pending, []
        kind = self.pending_kind or "arm"
        name = self.unique_name(kind)
        try:
            plane = Session.plane_from_points(pts, name)
            self.session.add_limb(name, kind, plane)      # rechaza planos de canto
        except ValueError as e:
            self._changed(f"No sirve: {e}. Elegí 3 puntos de nuevo.")
            return
        self.mode, self.pending_kind = Mode.VIEW, None
        self._changed(f"Plano «{name}» agregado. Falta dibujar su silueta (y, si hace falta, su 2.º corte de frente con el botón «2.º»).")

    def offset_limb(self) -> str | None:
        """Miembro activo cuyo desplazamiento de corte se puede editar (el del pasador o el de la silueta)."""
        for name in (self.pin_limb, self.edit_limb):
            if name is not None and self.session.limb_offset(name) is not None:
                return name
        sym = [l.name for l in self.session.project.limbs if self.session.limb_offset(l.name) is not None]
        return sym[0] if len(sym) == 1 else None

    def set_limb_offset(self, name: str, mm: float) -> None:
        try:
            self.session.set_limb_offset(name, mm)
        except ValueError as e:
            self._changed(f"{e}.", scene=False)
            return
        if self.mode == Mode.PIN and self.pin_limb == name:
            self.start_pin(name)             # la cara de contacto se movió: recalcular y volver a mirarla
            return
        self._changed(f"Corte de «{name}» a {mm:g} mm del plano de simetría. Revisá el pasador.")

    def unique_name(self, kind: str) -> str:
        base = _KIND_BASE[kind]
        names = {l.name for l in self.session.project.limbs}
        if base not in names:
            return base
        i = 2
        while f"{base} {i}" in names:
            i += 1
        return f"{base} {i}"

    # ---------------------------------------------------------------- planos
    def plane_entries(self) -> list[PlaneEntry]:
        out = []
        if self.session.project.symmetry is not None:
            out.append(PlaneEntry(SYMMETRY_ID, "Simetría",
                                  Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría"), "symmetry",
                                  SYMMETRY_ID not in self.hidden))
        for l in self.session.project.limbs:
            out.append(PlaneEntry(l.name, l.name.capitalize(), l.plane, l.kind, l.name not in self.hidden))
        head = self.session.project.head
        if head is not None:
            out.append(PlaneEntry(HEAD_NAME, "Cabeza (cuello)", head.plane, "head", HEAD_NAME not in self.hidden))
        for j in self.session.project.joints:
            jid = JOINT_PREFIX + j.name
            out.append(PlaneEntry(jid, j.name.capitalize(), j.plane, "joint", jid not in self.hidden))
        return out

    def toggle_plane(self, plane_id: str) -> None:
        self.hidden ^= {plane_id}
        self._changed()

    def remove_plane(self, plane_id: str) -> None:
        if (plane_id == SYMMETRY_ID or plane_id in (self.edit_limb, self.pin_limb)
                or (self.trim_cfg is not None and self.trim_cfg["target"] == plane_id)):
            self.cancel(quiet=True)
        if plane_id == SYMMETRY_ID:
            self.session.set_symmetry(None)
            self.session.project.limbs, self.session.project.trims = [], []
            self.session.project.head = None
            self._rebuild_picker()
            self.hidden.clear()
            self._changed("Simetría quitada (también los miembros: dependen de ella).")
            return
        if plane_id.startswith(JOINT_PREFIX):
            self.cancel(quiet=True)
            self.session.remove_joint(plane_id[len(JOINT_PREFIX):])
        elif plane_id == HEAD_NAME:
            self.session.remove_head()
        else:
            self.session.remove_limb(plane_id)
        self.hidden.discard(plane_id)
        self._changed(f"Plano «{plane_id}» quitado.")

    def problems(self) -> list[str]:
        return self.session.problems()

    # ---------------------------------------------------------------- generar
    def begin_generate(self):
        """Valida que se pueda generar y devuelve el trabajo pesado: ``job(progress=None) -> BuildOutcome``.

        El trabajo no toca el estado del controlador, así que la ventana puede correrlo en un hilo; al
        terminar hay que llamar `finish_generate` desde el hilo de la interfaz. Devuelve None (y deja el
        motivo en `message`) si no se puede generar.
        """
        if self.busy:
            return None
        probs = self.session.problems()
        if probs:
            self._changed("Todavía no se puede generar: " + "; ".join(probs) + ".", scene=False)
            return None
        self.cancel(quiet=True)
        self.busy = True
        self._changed("Generando la figura… puede tardar varios minutos.", scene=False)
        return lambda progress=None: self.session.build(progress)

    def report_progress(self, text: str) -> None:
        self._changed(f"Generando… {text}", scene=False)

    def finish_generate(self, outcome=None, error: Exception | None = None) -> None:
        self.busy = False
        self.pose_view, self.pose_report = False, None      # la figura nueva reemplaza a la de la vista previa
        if error is not None:
            self._changed(f"Falló la generación: {error}", scene=False)
        else:
            self._changed(validation_summary(outcome.validation, self.session.project.params.clearance),
                          scene=False)

    def generate(self, progress=None):
        """Genera sin hilos (pruebas y uso programático). Devuelve el `BuildOutcome` o None."""
        job = self.begin_generate()
        if job is None:
            return None
        try:
            outcome = job(progress)
        except Exception as e:                      # noqa: BLE001 — se informa en el estado
            self.finish_generate(error=e)
            return None
        self.finish_generate(outcome)
        return outcome

    def export(self, outdir: str, orient: bool = True) -> dict | None:
        """Escribe las piezas, el ensamblado, el informe y el proyecto en `outdir`."""
        if self.session.outcome is None:
            self._changed("Primero generá la figura (los cambios posteriores invalidan el resultado).", scene=False)
            return None
        info = self.session.export(outdir, orient=orient, max_angle=self.overhang_angle)
        files = info["archivos"]
        msg = f"Exportado en {outdir}: ensamblada + {len(files['piezas'])} piezas."
        ori = files.get("orientacion")
        if ori:
            msg += f" Orientación: {ori['etiqueta']}, voladizo {ori['voladizo_mm2']} mm²."
        self._changed(msg, scene=False)
        return info

    # ---------------------------------------------------------------- escena
    def sync(self, scene) -> None:
        """Vuelca el estado al `SceneRenderer` (llamar con el contexto GL activo)."""
        sess = self.session
        if sess.work is None:
            scene.clear()
            return
        if self.pose_active():
            self._sync_pose(scene)
            return
        for key in [k for k in scene.items if k.startswith("pose:")]:
            scene.remove(key)
        clip = 0.0 if (self.one_side and sess.project.symmetry is not None) else -1e9
        cplane = self._pin_clip_plane()
        extra = {} if cplane is None else {"clip_plane": cplane}      # solo en el modo PIN
        found = self.overhang_faces() if self.show_overhangs else None
        drawn = []                                                   # (nombre, malla, color) para dibujar su espejo
        if found is not None and found[0].any():
            mask = found[0]
            verts, faces = sess.work.vertices, sess.work.faces
            ok_mesh = trimesh.Trimesh(verts, faces[~mask], process=False)
            over_mesh = trimesh.Trimesh(verts, faces[mask], process=False)
            scene.set_mesh("figura", ok_mesh, clip_x=clip, **extra)
            scene.set_mesh("voladizos", over_mesh, color=(0.95, 0.25, 0.25, 1.0), clip_x=clip, **extra)
            drawn = [("figura", ok_mesh, _MIRROR_COLOR), ("voladizos", over_mesh, (0.95, 0.45, 0.45, 1.0))]
        else:
            scene.set_mesh("figura", sess.work, clip_x=clip, **extra)
            scene.remove("voladizos")
            drawn = [("figura", sess.work, _MIRROR_COLOR)]
        hs = self.plane_half_size
        palette = {"symmetry": (1.0, 0.55, 0.2, 0.18), "arm": (0.3, 0.6, 1.0, 0.22), "leg": (0.4, 0.85, 0.5, 0.22),
                   "head": (0.9, 0.5, 0.9, 0.22), "joint": (1.0, 0.85, 0.2, 0.22)}
        wanted = set()
        for e in self.plane_entries():
            key = f"plano:{e.id}"
            wanted.add(key)
            scene.set_plane(key, e.plane, hs, center=sess.work.bounds.mean(axis=0), color=palette[e.kind])
            scene.show(key, e.visible)
        for key in [k for k in scene.items if k.startswith("plano:") and not k.endswith(":borde")]:
            if key not in wanted:
                scene.remove(key)
        if self.pending:
            pts = np.array(self.pending)
            scene.set_points("pendientes", pts, (1.0, 0.25, 0.25, 1.0), 12.0)
            scene.set_lines("pendientes:lineas", pts, (1.0, 0.25, 0.25, 1.0), closed=len(pts) == 3)
        else:
            scene.remove("pendientes")
            scene.remove("pendientes:lineas")
        self._sync_polygons(scene)
        self._sync_pins(scene)
        self._sync_joints(scene)
        self._sync_trims(scene)
        self._sync_mirror(scene, drawn, cplane)

    def _sync_pose(self, scene) -> None:
        """Vista previa de la pose: torso generado + miembros girados al ángulo elegido (rojo = interfiere)."""
        scene.clear()
        out = self.session.outcome
        if self.pose_report is None:
            self.pose_report = self.pose_check()
        rep = self.pose_report
        bad = set(rep["torso"]) | {n for pair in rep["pairs"] for n in pair}
        scene.set_mesh("pose:torso", S.to_trimesh(out.result.pieces["torso"]), color=(0.62, 0.68, 0.78, 1.0))
        for name, mf in self._posed_pieces(rep["angle"]).items():
            scene.set_mesh(f"pose:{name}", S.to_trimesh(mf),
                           color=(0.95, 0.28, 0.28, 1.0) if name in bad else (0.85, 0.80, 0.68, 1.0))

    def _sync_trims(self, scene) -> None:
        """Recortes manuales: rojo = restar, verde = unir. El que se está dibujando lo muestra el editor de polígonos."""
        wanted = set()
        editing = self.edit_trim if self.mode == Mode.POLYGON else None
        for i, t in enumerate(self.session.project.trims):
            if i == editing:
                continue
            key = f"recorte:{i}"
            wanted.add(key)
            scene.set_lines(key, self.trim_wires(t), (0.95, 0.3, 0.3, 1.0) if t.op == "subtract" else (0.35, 0.85, 0.4, 1.0),
                            closed=True)
            scene.show(key, t.target not in self.hidden)
        for key in [k for k in scene.items if k.startswith("recorte:") and k not in wanted]:
            scene.remove(key)

    def _sync_mirror(self, scene, drawn, cplane) -> None:
        """Espejo en vivo (x → −x) del lado de trabajo: figura, siluetas (también la que se está dibujando), recortes
        y pasadores elegidos. Con los marcadores en x > 0 el espejo ocupa el lado oculto (x < 0)."""
        if not self.mirror_active() or cplane is not None:
            for key in [k for k in scene.items if k.startswith("espejo:")]:
                scene.remove(key)
            return
        wanted = set()
        for name, mesh, color in drawn:
            key = f"espejo:{name}"
            wanted.add(key)
            scene.set_mesh(key, mirrored_mesh(mesh), color=color, clip_plane=(1.0, 0.0, 0.0, 0.0))
        editing = self.draft if self.mode == Mode.POLYGON else None
        for l in self.session.project.limbs:
            hidden = l.name in self.hidden
            for key, poly, color in ((f"espejo:silueta:{l.name}", l.polygon, (1.0, 0.8, 0.2, 1.0)),
                                     (f"espejo:silueta2:{l.name}", None if l.cross is None else l.cross.polygon,
                                      (0.3, 0.85, 1.0, 1.0))):
                being_edited = editing is not None and l.name == self.edit_limb and self.edit_cross == ("silueta2" in key)
                if being_edited or poly is None or len(poly) < 3:
                    continue
                wanted.add(key)
                scene.set_lines(key, mirrored_points(poly), color, closed=True)
                scene.show(key, not hidden)
            if l.pin_center is not None:
                key = f"espejo:pasador:{l.name}"
                wanted.add(key)
                scene.set_points(key, mirrored_points(np.asarray(l.pin_center, float)[None]), (1.0, 0.55, 0.1, 1.0), 10.0)
                scene.show(key, not hidden)
        for i, t in enumerate(self.session.project.trims):
            if i == (self.edit_trim if editing is not None else None):
                continue
            key = f"espejo:recorte:{i}"
            wanted.add(key)
            scene.set_lines(key, [mirrored_points(w) for w in self.trim_wires(t)],
                            (0.95, 0.3, 0.3, 1.0) if t.op == "subtract" else (0.35, 0.85, 0.4, 1.0), closed=True)
        if editing is not None and editing.pts:
            wanted.add("espejo:poligono")
            scene.set_lines("espejo:poligono", mirrored_points(editing.world()), (1.0, 0.8, 0.2, 1.0), closed=editing.closed)
        for key in [k for k in scene.items if k.startswith("espejo:") and k not in wanted]:
            scene.remove(key)

    def _pin_clip_plane(self):
        """En el modo PIN se recorta lo que queda del lado del torso de la cara de contacto, para ver la cara."""
        if self.mode != Mode.PIN or self.pin_prev is None:
            return None
        f = self.pin_prev.face
        n = f.outward / np.linalg.norm(f.outward)
        return (float(n[0]), float(n[1]), float(n[2]), float(n @ f.center) + 1e-3)

    def _sync_pins(self, scene) -> None:
        """Marcas de pasador: un punto por cada pasador elegido a mano y, en el modo PIN, la cara de contacto,
        el vástago, la cabeza y el punto que propone el programa."""
        wanted = set()
        editing = self.pin_limb if self.mode == Mode.PIN and self.pin_prev is not None else None
        for l in self.session.project.limbs:
            if l.pin_center is None or l.name == editing:
                continue
            key = f"pasador:{l.name}"
            wanted.add(key)
            scene.set_points(key, np.asarray(l.pin_center, float)[None], (1.0, 0.55, 0.1, 1.0), 10.0)
            scene.show(key, l.name not in self.hidden)
        pin_keys = ("pasador:cara", "pasador:borde", "pasador:silueta", "pasador:vastago", "pasador:cabeza",
                    "pasador:centro", "pasador:auto")
        if editing is None:
            for key in [k for k in scene.items if k.startswith("pasador:") and k not in wanted]:
                scene.remove(key)
            return
        for key in [k for k in scene.items if k.startswith("pasador:") and k not in wanted
                    and k not in pin_keys]:
            scene.remove(key)
        prev, name = self.pin_prev, editing
        pin = self.session.pin_spec(name)
        c = self._pin_center(name)
        face_mesh = trimesh.Trimesh(*self._face_mesh(prev), process=False)
        scene.set_mesh("pasador:cara", face_mesh, color=(0.35, 0.65, 0.95, 1.0))
        scene.set_lines("pasador:borde", prev.rings_world(), (1.0, 1.0, 1.0, 1.0), closed=True, on_top=True)
        # Plano de trabajo del pasador (interno, no figura en la lista de planos): paralelo al de simetría y puesto en
        # el corte del miembro. La silueta ya dibujada se proyecta sobre él como referencia (en un plano de costado
        # es el contorno entero; en uno frontal, su sombra: la altura que cubre).
        scene.set_lines("pasador:silueta", silhouette_on_plane(self.session.limb(name), prev.plane),
                        (1.0, 0.8, 0.2, 1.0), closed=True, on_top=True)
        scene.set_lines("pasador:vastago", circle_world(prev.plane, c, pin.shaft_d / 2), (1.0, 0.55, 0.1, 1.0),
                        closed=True, on_top=True)
        scene.set_lines("pasador:cabeza", circle_world(prev.plane, c, pin.head_d / 2), (1.0, 0.85, 0.3, 1.0),
                        closed=True, on_top=True)
        scene.set_points("pasador:centro", c[None], (1.0, 0.25, 0.25, 1.0), 9.0, on_top=True)
        if self.session.limb(name).pin_center is not None:
            scene.set_points("pasador:auto", prev.default_center[None], (0.6, 0.9, 0.6, 1.0), 7.0, on_top=True)
        else:
            scene.remove("pasador:auto")

    @staticmethod
    def _face_mesh(prev):
        t = np.asarray(prev.face.triangles, float)
        return t.reshape(-1, 3), np.arange(len(t) * 3).reshape(-1, 3)

    def _sync_polygons(self, scene) -> None:
        editing = self.draft if self.mode == Mode.POLYGON else None
        wanted = set()
        for l in self.session.project.limbs:
            for key, poly, color in ((f"silueta:{l.name}", l.polygon, (1.0, 0.8, 0.2, 1.0)),
                                     (f"silueta2:{l.name}", None if l.cross is None else l.cross.polygon,
                                      (0.3, 0.85, 1.0, 1.0))):
                being_edited = editing is not None and l.name == self.edit_limb and self.edit_cross == key.startswith("silueta2")
                if being_edited or poly is None or len(poly) < 3:
                    continue
                wanted.add(key)
                scene.set_lines(key, np.asarray(poly), color, closed=True)
                scene.show(key, l.name not in self.hidden)
        for key in [k for k in scene.items if k.startswith(("silueta:", "silueta2:")) and k not in wanted]:
            scene.remove(key)
        if editing is None:
            for key in ("guia", "poligono", "poligono:vertices", "poligono:sel"):
                scene.remove(key)
            return
        guide = self.guide_outline(editing.plane)
        scene.set_lines("guia", [editing.plane.to_world(g["exterior"]) for g in guide], (0.7, 0.72, 0.8, 1.0), closed=True)
        if editing.pts:
            w3 = editing.world()
            scene.set_lines("poligono", w3, (1.0, 0.8, 0.2, 1.0), closed=editing.closed)
            scene.set_points("poligono:vertices", w3, (1.0, 0.8, 0.2, 1.0), 9.0)
            if editing.selected is not None:
                scene.set_points("poligono:sel", w3[[editing.selected]], (1.0, 0.25, 0.25, 1.0), 15.0)
            else:
                scene.remove("poligono:sel")
        else:
            for key in ("poligono", "poligono:vertices", "poligono:sel"):
                scene.remove(key)
