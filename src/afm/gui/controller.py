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
from ..head import HEAD_NAME, MIN_BASE_IOU, REPLACEMENT_UPS, neck_plane
from ..pipeline import body_base_z, body_ref
from ..waist import PELVIS_NAME, WAIST_NAME
from ..limbs import silhouette_on_plane
from ..orient import OrientResult, overhang_mask, pose_orientation
from ..planes import Plane
from ..trims import Trim
from .camera import OrbitCamera
from .picking import Picker, PickResult
from .polygon_editor import EDGE_PX, VERTEX_PX, PolygonDraft, ray_plane_uv
from .session import Session

SYMMETRY_ID = "simetria"
_KIND_BASE = {"arm": "brazo", "leg": "pierna"}


HOVER_RADIUS_PX = 12.0       # distancia en pantalla a la que un vértice se marca en rojo al pasar el puntero


class Mode(Enum):
    VIEW = "ver"
    CROWN = "coronilla"
    SYMMETRY = "simetria"
    LIMB_PLANE = "plano_miembro"
    NECK_PLANE = "plano_cuello"
    HEAD_CUT_PLANE = "plano_corte_cabeza"   # 3 clics sobre la cabeza de reemplazo (naranja): plano que corta su base
    HAND_EXTRACT = "extraer_mano"   # 3 clics en la muñeca izquierda: corta la mano y la deja lista para guardar como mano estándar
    HAND_PLACE = "pegar_mano"       # 3 clics en la muñeca de ESTE modelo: se descarta su mano y se pega la mano estándar (STL) elegida
    WAIST_PLANE = "plano_cintura"   # 3 clics: corta el torso en pecho y pelvis con un pasador robusto (giro a los costados)
    POLYGON = "silueta"
    PIN = "pasador"
    JOINT_PLANE = "plano_articulacion"   # 3 clics: plano que parte el miembro en proximal y distal (rodilla / codo)
    JOINT_LINES = "lineas_articulacion"   # 4 clics de frente: línea del brazo (arriba→codo) y del antebrazo (codo→abajo)
    JOINT_SIDE = "guias_costado"          # 4 clics de costado: corrigen (y, z) de las mismas 4 guías y recalculan la articulación
    JOINT_AXIS = "eje_articulacion"      # un clic sobre la figura fija (y, z) del eje de la bisagra
    TRIM_PICK = "recorte_clic"      # un clic sobre la figura pone el centro de un recorte esfera / caja / cilindro
    HEAD_PIN = "pasador_cabeza"     # vista de costado: un clic (o flechas) pone el pasador del cuello adelante / atrás sobre la cara
    FLAT_PLANE = "plano_sin_soportes"   # figura generada sentada, vista Derecha: un clic (o flechas) fija la altura del corte plano

EXPORT_ONLY_PARAMS = frozenset({"flat_depth"})   # parámetros que no cambian las piezas generadas


GUIDE_ARM_COLOR = (0.2, 0.9, 1.0, 1.0)         # cian: línea del brazo (o del muslo)
GUIDE_FOREARM_COLOR = (1.0, 0.35, 0.9, 1.0)    # magenta: línea del antebrazo (o de la pantorrilla)
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
    kind: str                # "symmetry" | "arm" | "leg" | "head" | "joint" | "waist"
    visible: bool = True


_MIRROR_COLOR = (0.70, 0.78, 0.92, 1.0)      # el espejo se ve con un tinte azul para no confundirlo con el lado real
_REPLACEMENT_HEAD_COLOR = (0.95, 0.62, 0.25, 1.0)      # cabeza de reemplazo en la vista 3D (naranja)
_EXTRACTED_HAND_COLOR = (0.25, 0.85, 0.55, 1.0)         # mano extraída (aún sin guardar) en la vista 3D (verde)
_REPLACEMENT_HAND_COLOR = (0.25, 0.75, 0.85, 1.0)       # mano estándar pegada en el modelo (turquesa: no se confunde con la extraída)
FLAT_NUDGE_MM = 0.1            # paso de las flechas al ajustar el plano de corte «sin soportes» (Mayús = 10 veces más)
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
    ("flat_depth", "Sin soportes: corte plano sobre la cola (mm)", 0.2, 6.0, 0.1, 1),
    ("torso_mirror", "Torso espejado (1 = sí, 0 = conserva el diseño asimétrico del STL)", 0.0, 1.0, 1.0, 0),
    ("hip_margin", "Margen del límite de cadera (°)", 0.0, 30.0, 1.0, 0),
    ("waist_pin.shaft_d", "Cintura: Ø vástago (mm)", 3.0, 14.0, 0.1, 1),
    ("waist_pin.shaft_len", "Cintura: largo vástago (mm; pared tras la cabeza = largo − 2·holgura)", 1.5, 15.0, 0.1, 1),
    ("waist_pin.head_d", "Cintura: Ø cabeza (mm)", 5.0, 20.0, 0.1, 1),
    ("waist_pin.head_h", "Cintura: alto cabeza (mm)", 1.0, 8.0, 0.05, 2),
    ("waist_twist", "Cintura: giro libre a cada lado a validar (°)", 5.0, 180.0, 5.0, 0),
    ("shoulder_pin.shaft_d", "Hombro: Ø vástago (mm)", 2.0, 12.0, 0.1, 1),
    ("shoulder_pin.shaft_len", "Hombro: largo vástago (mm)", 0.5, 15.0, 0.1, 1),
    ("shoulder_pin.head_d", "Hombro: Ø cabeza (mm)", 3.0, 16.0, 0.1, 1),
    ("shoulder_pin.head_h", "Hombro: alto cabeza (mm)", 0.5, 8.0, 0.05, 2),
    ("hip_pin.shaft_d", "Cadera: Ø vástago (mm)", 2.0, 12.0, 0.1, 1),
    ("hip_pin.shaft_len", "Cadera: largo vástago (mm)", 0.5, 15.0, 0.1, 1),
    ("hip_pin.head_d", "Cadera: Ø cabeza (mm)", 3.0, 16.0, 0.1, 1),
    ("hip_pin.head_h", "Cadera: alto cabeza (mm)", 0.5, 8.0, 0.05, 2),
    ("shoulder_detent", "Hombro: mecanismo de clic (1 = sí, 0 = solo hongo)", 0.0, 1.0, 1.0, 0),
    ("hip_detent", "Cadera: mecanismo de clic (1 = sí, 0 = solo hongo)", 0.0, 1.0, 1.0, 0),
    ("detent.n_notches", "Clic: cantidad de muescas", 2.0, 8.0, 1.0, 0),
    ("detent.disc_d", "Clic: Ø del disco (mm)", 5.0, 14.0, 0.1, 1),
    ("detent.disc_h", "Clic: alto del disco (mm)", 1.0, 5.0, 0.1, 1),
    ("detent.notch_depth", "Clic: profundidad de la muesca (mm)", 0.3, 2.0, 0.05, 2),
    ("detent.flank_deg", "Clic: ángulo del flanco (°)", 10.0, 60.0, 1.0, 0),
    ("detent.tip_r", "Clic: radio de la punta (mm)", 0.3, 2.0, 0.05, 2),
    ("detent.detent_gap", "Clic: holgura punta/muesca (mm)", 0.1, 0.8, 0.05, 2),
    ("detent.slit_w", "Clic: ranura de la lengüeta (mm)", 0.2, 1.5, 0.05, 2),
    ("detent.flex_gap", "Clic: ranura superior = flexión + ranura (mm)", 0.3, 3.0, 0.05, 2),
    ("detent.finger_t", "Clic: espesor de la lengüeta (mm)", 0.6, 3.0, 0.1, 1),
    ("detent.finger_len", "Clic: largo de la lengüeta (mm)", 4.0, 20.0, 0.5, 1),
    ("detent.finger_w", "Clic: ancho de la lengüeta (mm)", 2.0, 10.0, 0.5, 1),
    ("detent.print_angle", "Clic: ángulo del brazo al imprimir (°; fase de los clics)", 0.0, 90.0, 1.0, 0),
    ("hinge_pin.pin_d", "Rodilla/codo: Ø bulón (mm)", 2.0, 6.0, 0.1, 1),
    ("hinge_pin.pin_len", "Rodilla/codo: largo bulón (mm)", 0.5, 4.0, 0.1, 1),
    ("hinge_pin.tongue_t", "Rodilla/codo: espesor lengüeta (mm)", 1.0, 6.0, 0.1, 1),
    ("hinge_pin.end_r", "Rodilla/codo: radio lengüeta (mm)", 2.0, 8.0, 0.1, 1),
    ("hinge_pin.neck", "Rodilla/codo: lengüeta con cuello recto (1 = sí, 0 = disco)", 0.0, 1.0, 1.0, 0),
    ("hinge_pin.rod", "Rodilla/codo: bastago pasante (1 = sí, 0 = dos bulones)", 0.0, 1.0, 1.0, 0),
    ("hinge_pin.rod_wall", "Rodilla/codo: pared tras el bastago (mm; 0 = pasante)", 0.0, 4.0, 0.1, 1),
)


class Controller:
    def __init__(self, session: Session | None = None):
        self.session = session or Session()
        self.camera = OrbitCamera()
        self.picker: Picker | None = None
        self.mode = Mode.VIEW
        self.pending: list[np.ndarray] = []
        self.hover_vertex: np.ndarray | None = None  # vértice bajo el puntero (se pinta de rojo; un clic lo elige)
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
        self.head_pin_prev = None                   # HeadPinPreview mientras se coloca el pasador del cuello (HEAD_PIN)
        self.joint_name: str | None = None          # articulación cuyo eje se está colocando (JOINT_AXIS)
        self.joint_guides: dict[str, tuple] = {}    # articulación -> (línea del brazo 2×3, línea del antebrazo 2×3) dibujadas
        self.pin_limb: str | None = None            # miembro cuyo pasador se está colocando (modo PIN)
        self.pin_prev = None                         # PinPreview de ese miembro (cara de contacto)
        self.mirror_live = False                     # con «un solo lado»: mostrar el espejo del lado de trabajo en vivo
        self.pose_view = False                       # vista previa de la figura generada con las piernas en la pose sentada
        self.pose_angle: float | None = None         # ángulo de cadera de la vista previa (None = el de la pose sentada)
        self.pose_report: dict | None = None         # resultado de `pose_check` para ese ángulo
        self._pose_cache: tuple | None = None        # (outcome, ángulo, estiradas, {pieza: sólido}) de la pose mostrada
        self.flat_print = False                      # casilla «sin soportes»: la vista de pose muestra las piernas estiradas
        self.trim_cfg: dict | None = None            # recorte en edición: target / op / distance / symmetric / shape / size
        self.edit_trim: int | None = None            # índice en project.trims del polígono de recorte en edición
        self._guide_cache: tuple | None = None       # (work, bounds, plano) -> contornos de la figura en el plano
        self._overhang_cache: tuple | None = None    # (work, bounds, ángulo, máscara, OrientResult)
        self.hand_extract: dict | None = None        # mano extraída sin guardar: hand (local), split, points, invert
        self._hand_mesh = None                       # (split, malla de la mano en el mundo) para dibujarla en verde
        self._hand_path_pending: str | None = None   # STL de la mano estándar elegida, a la espera de los 3 clics de la muñeca
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
        self.joint_guides = {}
        self.hidden.clear()
        self.hand_extract = self._hand_mesh = None
        rep = self.session.load(path, crown_z)
        self._after_load()
        msg = f"Cargado {path.split('/')[-1]}"
        if rep.warnings:
            msg += " — " + " ".join(rep.warnings)
        self._changed(msg)

    def open_project(self, path: str, stl_path: str | None = None) -> None:
        self.session = Session.open_project(path, stl_path)
        self.joint_guides = {}
        self.hidden.clear()
        self.hand_extract = self._hand_mesh = None
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

    def start_waist_plane(self) -> None:
        if not self._need_loaded():
            return
        if self.session.project.symmetry is None:
            self._changed("Primero definí el plano de simetría.", scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.WAIST_PLANE
        self._changed("Plano de la cintura: elegí 3 puntos a la altura de la cintura (2 a cada lado y 1 adelante o atrás). "
                      "Lo de arriba (pecho, brazos, cabeza) gira sobre un pasador robusto que sale de la pelvis.")

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

    def start_joint_lines(self, limb: str) -> None:
        """Codo (brazo) o rodilla (pierna) siguiendo el miembro: 2 líneas sobre la vista frontal, una arriba y otra abajo
        de la articulación; el corte sigue la bisectriz y el eje queda perpendicular a ella (`Session.plane_from_lines`)."""
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
        self.mode, self.joint_limb = Mode.JOINT_LINES, limb
        up, down = self._line_names(spec.kind)
        joint = "la rodilla" if spec.kind == "leg" else "el codo"
        self._changed(f"{joint.capitalize()} con 2 líneas (conviene la vista «Frente»): línea del {up}, punto 1/2: clic "
                      f"arriba, en el centro del {up}. Después {joint} y la línea del {down}. Backspace deshace; Esc cancela.")

    def start_joint_lines_for_kind(self, kind: str) -> None:
        """Como `start_joint_plane_for_kind` pero definiendo el corte con dos líneas."""
        if kind not in _KIND_BASE:
            raise ValueError(kind)
        limb = next((l.name for l in self.session.project.limbs
                     if l.kind == kind and self.session.joint_of_limb(l.name) is None), None)
        if limb is None:
            self._changed(f"No hay un {_KIND_BASE[kind]} sin articulación: agregá primero su plano y silueta "
                          "(o quitá la articulación que ya tiene).", scene=False)
            return
        self.start_joint_lines(limb)

    @staticmethod
    def _line_names(kind: str) -> tuple[str, str]:
        return ("muslo", "pantorrilla") if kind == "leg" else ("brazo", "antebrazo")

    def _joint_lines_hint(self) -> str:
        spec = self.session.limb(self.joint_limb)
        up, down = self._line_names(spec.kind if spec else "arm")
        joint = "la rodilla" if spec is not None and spec.kind == "leg" else "el codo"
        n = len(self.pending)
        if n == 1:
            text = (f"Línea del {up} (cian): punto 1/2 listo. Ahora clic en {joint}, en el centro del miembro, "
                    "donde se dobla.")
        elif n == 2:
            ang = Session.line_opening_deg(self.pending[0], self.pending[1])
            if ang is None:
                text = (f"La línea del {up} no sirve (muy corta o casi horizontal vista de frente): Backspace y elegí "
                        f"otro punto en {joint}.")
            else:
                text = (f"Línea del {up} lista (cian): se abre {ang:.1f}° al bajar. Ahora la del {down}: clic en "
                        f"{joint} otra vez (en el mismo lugar).")
        else:
            text = (f"Línea del {down} (magenta): punto 1/2 listo. Ahora clic abajo, en el centro del {down}.")
        return text + " Backspace deshace el último; Esc cancela."

    def guide_overlays(self) -> list[tuple]:
        """Guías de las 2 líneas como (tipo, clave, puntos, color) con tipo «lines» | «points»: las que se están eligiendo
        (puntos cian del brazo y magenta del antebrazo, y la línea del brazo apenas hay 2 puntos) y las de cada
        articulación creada con 2 líneas (las 2 líneas y un punto blanco en el codo). `sync` las dibuja sin prueba de
        profundidad, para que se vean aunque la línea atraviese la figura."""
        out: list[tuple] = []
        if self.mode == Mode.JOINT_LINES and self.pending:
            pts = np.array(self.pending, float)
            out.append(("points", "guia:pend:brazo", pts[:2], GUIDE_ARM_COLOR))
            if len(pts) >= 2:
                out.append(("lines", "guia:pend:brazo:l", pts[:2], GUIDE_ARM_COLOR))
            if len(pts) > 2:
                out.append(("points", "guia:pend:ante", pts[2:], GUIDE_FOREARM_COLOR))
        for name, (up, low) in self.joint_guides.items():
            if self.session.joint(name) is None:
                continue
            up, low = np.asarray(up, float), np.asarray(low, float)
            out.append(("lines", f"guia:{name}:brazo", up, GUIDE_ARM_COLOR))
            out.append(("lines", f"guia:{name}:antebrazo", low, GUIDE_FOREARM_COLOR))
            out.append(("points", f"guia:{name}:codo", ((up[1] + low[0]) / 2.0)[None], (1.0, 1.0, 1.0, 1.0)))
        return out

    def _centered_in_y(self, pt) -> np.ndarray:
        """Lleva un punto elegido sobre la superficie (vista de frente: cara delantera del miembro) al centro del
        miembro en profundidad: dos rayos a lo largo de Y, uno desde cada lado, por (x, z) del punto; y = la mitad
        entre las dos primeras caras. Si algún rayo no toca la figura o el espesor no es creíble (> 40 mm: el rayo
        atravesó el torso, por ejemplo) se deja el punto como está; «Ajustar guías de costado» corrige a mano."""
        pt = np.asarray(pt, float).copy()
        if self.picker is None:
            return pt
        from .picking import ray_first_hit
        tris, big = self.picker._tris, 1e4
        ys = []
        for sgn in (1.0, -1.0):
            r = ray_first_hit(tris, (pt[0], sgn * big, pt[2]), (0.0, -sgn, 0.0))
            if r is None:
                return pt
            ys.append(sgn * big - sgn * r[1])
        if abs(ys[0] - ys[1]) > 40.0:
            return pt
        pt[1] = (ys[0] + ys[1]) / 2.0
        return pt

    def _finish_joint_lines(self) -> None:
        pts, self.pending = self.pending, []
        limb = self.joint_limb
        pts = [self._centered_in_y(p) for p in pts]          # de frente se clickea la cara delantera: centrar en profundidad
        try:
            plane, elbow, a_up, a_down, lift = Session.plane_from_lines((pts[0], pts[1]), (pts[2], pts[3]))
            j = self.session.add_joint(limb, plane, anchor=elbow, lift_dir=lift)
        except (ValueError, KeyError) as e:
            self._changed(f"No sirve: {e}. Dibujá las 2 líneas de nuevo.")
            return
        self.mode, self.joint_limb = Mode.VIEW, None
        self.joint_guides[j.name] = (np.array(pts[:2], float), np.array(pts[2:], float))
        up, down = self._line_names(self.session.limb(limb).kind)
        self._changed(f"{j.name.capitalize()} definida siguiendo el miembro: el {up} se abre {a_up:.1f}° y el {down} "
                      f"{a_down:.1f}° (hacia +X al bajar); el corte sigue la bisectriz y el eje quedó inclinado "
                      f"{self.joint_tilt(j.name):.1f}°. Rango {self.joint_range_text(j.name)}. Las guías se centraron en "
                      "profundidad: revisalas de costado y, si hace falta, corregilas con «Ajustar guías de costado».")

    def start_joint_side(self, name: str) -> None:
        """Corrige las 4 guías de una articulación hecha con 2 líneas desde la vista de costado (conviene «Costado»):
        los mismos 4 puntos en el mismo orden (arriba del brazo, codo, codo, abajo del antebrazo). De cada clic se toman
        y y z; la x se conserva de la guía de frente. Con eso se recalculan el corte y el eje."""
        if not self._need_loaded():
            return
        if self.session.joint(name) is None:
            self._changed(f"No hay una articulación «{name}».", scene=False)
            return
        if name not in self.joint_guides:
            self._changed(f"«{name}» no se hizo con 2 líneas: quitala (−) y rehacela con «codo siguiendo el brazo».",
                          scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.joint_name = Mode.JOINT_SIDE, name
        up, down = self._line_names(self.session.limb(self.session.joint(name).limb).kind)
        self._changed(f"Guías de {name} de costado (vista «Costado»): clic 1/4 arriba, en el centro del {up}; 2/4 en la "
                      f"articulación; 3/4 otra vez en la articulación; 4/4 abajo, en el centro del {down}. Se toman la "
                      "altura y la profundidad de cada clic. Backspace deshace; Esc cancela.")

    def _finish_joint_side(self) -> None:
        name, pts = self.joint_name, self.pending
        self.pending = []
        up_old, low_old = self.joint_guides[name]
        old = np.vstack([up_old, low_old])
        new = np.array([[old[i][0], p[1], p[2]] for i, p in enumerate(pts)], float)
        try:
            self.session.retarget_joint_lines(name, (new[0], new[1]), (new[2], new[3]))
        except (ValueError, KeyError) as e:
            self._changed(f"No sirve: {e}. Hacé los 4 clics de costado de nuevo.")
            self.mode = Mode.JOINT_SIDE
            return
        self.joint_guides[name] = (new[:2].copy(), new[2:].copy())
        self.mode, self.joint_name = Mode.VIEW, None
        self._changed(f"Guías de {name} corregidas de costado: el corte y el eje se recalcularon "
                      f"(inclinación {self.joint_tilt(name):.1f}°). Hay que generar de nuevo.")

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
        self.joint_guides.pop(j.name, None)                  # sin líneas guía: se definió con 3 puntos
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
        self._hand_path_pending = None
        if was == Mode.HEAD_CUT_PLANE:
            self._rebuild_picker()           # durante el corte el picker apuntaba a la cabeza de reemplazo
        self.clear_hover()
        self.draft, self.edit_limb, self.edit_cross = None, None, False
        self.pin_limb, self.pin_prev = None, None
        self.head_pin_prev = None
        self.joint_limb, self.joint_name = None, None
        was_trim = self.trim_cfg is not None
        self.trim_cfg, self.edit_trim = None, None
        if was == Mode.FLAT_PLANE and self.pose_view and not self.flat_print:
            self.pose_report = None          # vuelven las rodillas a la flexión de la pose impresa: se recalculan las interferencias
        if not quiet:
            self._changed("Edición del recorte terminada." if was_trim
                          else "Edición de silueta terminada." if was == Mode.POLYGON
                          else "Colocación del pasador terminada." if was in (Mode.PIN, Mode.HEAD_PIN)
                          else "Ajuste del plano de corte terminado." if was == Mode.FLAT_PLANE else "Cancelado.")

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
        if key in EXPORT_ONLY_PARAMS:          # solo se usa al exportar: la figura generada sigue valiendo
            self._changed(f"{label}: {value:g}.")
            return True
        self.session.outcome = None
        self._changed(f"{label}: {value:g}. Hay que generar de nuevo.", scene=False)
        return True

    def set_ref_height(self, mm: float) -> bool:
        """Cambia la altura de la figura (pies → coronilla, mm). Reescala la malla y, si ya había planos, siluetas,
        recortes y ejes, los escala por el mismo factor. Las medidas absolutas (pasadores, holgura, clic) no se
        tocan. Devuelve False (con el motivo en `message`) si el valor no vale."""
        try:
            k = self.session.set_ref_height(mm)
        except ValueError as e:
            self._changed(str(e), scene=False)
            return False
        if k == 1.0:
            return True
        if self.loaded:
            self._rebuild_picker()
            self.camera.frame(self.session.work.bounds)
        pr = self.session.project
        has_geom = pr.symmetry is not None or pr.limbs or pr.trims or pr.joints or pr.head is not None or pr.waist is not None
        msg = f"Altura de la figura: {pr.params.ref_height:g} mm (×{k:.4g})."
        if has_geom:
            msg += (" Planos, siluetas y recortes se escalaron con la figura; revisalos. Los pasadores, la holgura y el"
                    " clic son medidas absolutas y no cambian: ajustalos en Parámetros si hace falta. Hay que generar de nuevo.")
        elif self.loaded:
            msg += " Los pasadores, la holgura y el clic son medidas absolutas: revisalos en Parámetros."
        self.joint_guides = {n: (up * k, low * k) for n, (up, low) in self.joint_guides.items()}
        self.cancel(quiet=True)             # sale de cualquier edición a medias (estaba en la escala anterior)
        self._changed(msg)
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
        params.ref_height = self.session.project.params.ref_height   # la altura es de la figura abierta, no del perfil
        self.session.project.params = params
        self.session.outcome = None
        self._changed(f"Perfil «{name}» cargado (holgura {params.clearance:g} mm). Hay que generar de nuevo.",
                      scene=False)
        return True

    def begin_export_probes(self, outdir: str, which: str = "hip", clearances=None, shaft_deltas=None):
        """Valida y devuelve el trabajo pesado ``job(progress=None) -> dict`` que escribe la placa de probetas
        (`probetas.stl` + `probetas.txt`), apto para un hilo; después hay que llamar `finish_export_probes`.
        `which`: «hip» (cadera), «shoulder» (hombro) o «hinge» (bisagra de rodilla/codo) a variar."""
        if self.busy:
            return None
        self.busy = True
        self._changed("Generando las probetas… puede tardar.", scene=False)
        p = self.session.project.params

        def job(progress=None) -> dict:
            import os
            from .. import probes, solids as S
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
            return {"stl": stl, "tabla": txt, "celdas": len(cells)}

        return job

    def finish_export_probes(self, info: dict | None, outdir: str, which: str = "hip",
                             error: Exception | None = None) -> dict | None:
        self.busy = False
        if error is not None:
            self._changed(f"Falló la generación de probetas: {error}", scene=False)
            return None
        kind = {"hip": "cadera", "shoulder": "hombro", "hinge": "bisagra de rodilla/codo"}.get(which, which)
        self._changed(f"Probetas ({info['celdas']} articulaciones, pasador de {kind}) "
                      f"en {outdir}. Imprimilas, cargá en «Parámetros» la combinación elegida y guardala en el perfil.",
                      scene=False)
        return info

    def export_probes(self, outdir: str, which: str = "hip", clearances=None, shaft_deltas=None) -> dict | None:
        """Escribe la placa de probetas con los parámetros actuales (sin hilos: pruebas y uso programático)."""
        job = self.begin_export_probes(outdir, which, clearances, shaft_deltas)
        if job is None:
            return None
        try:
            info = job()
        except Exception:
            self.busy = False
            raise
        return self.finish_export_probes(info, outdir, which)

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

    # ------------------------------------------------- pasador del cuello (vista de costado)
    def start_head_pin(self) -> None:
        """Elegir dónde va el pasador de giro de la cabeza mirando la figura de costado: el plano del cuello se ve
        de canto y el pasador se desliza adelante / atrás sobre él (siempre en x = 0, como el cuello)."""
        if not self._need_loaded():
            return
        if self.session.project.head is None:
            self._changed("Primero definí el plano del cuello (Planos → Cabeza).", scene=False)
            return
        try:
            prev = self.session.head_pin_preview()
        except ValueError as e:
            self._changed(f"No se puede colocar el pasador del cuello: {e}.", scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.head_pin_prev = Mode.HEAD_PIN, prev
        self.camera.look_along((1.0, 0.0, 0.0), self._head_pin_center(), 0.35 * self.session.project.params.ref_height)
        self._changed("Pasador del cuello (vista de costado): " + self._head_pin_status()
                      + " Clic = mover; flechas ←/↓ atrás y →/↑ adelante 0,1 mm (Mayús 1 mm); Esc termina.")

    def _head_pin_center(self) -> np.ndarray:
        """Centro del pasador que se usaría ahora: el elegido, o el automático."""
        spec, prev = self.session.project.head, self.head_pin_prev
        return prev.default_center if spec.pin_center is None else np.asarray(spec.pin_center, float)

    def _head_pin_status(self) -> str:
        spec, prev = self.session.project.head, self.head_pin_prev
        c, shaft = self._head_pin_center(), self.session.project.params.neck_pin.shaft_d
        inside, margin = prev.fit(c, shaft)
        off = float((c - prev.default_center) @ prev.along)
        txt = "posición automática" if spec.pin_center is None else (
            f"posición elegida ({abs(off):.1f} mm {'adelante' if off > 0 else 'atrás'} de la automática)"
            if abs(off) >= 0.05 else "posición elegida (igual a la automática)")
        if not inside:
            return txt + f"; centro fuera de la cara (vástago Ø{shaft:g} apoya {max(margin + shaft / 2, 0):.1f} mm)."
        if margin < 0:
            return txt + f"; el vástago sobresale {-margin:.1f} mm del borde."
        if margin < self.session.project.params.min_wall:
            return txt + f"; margen al borde {margin:.1f} mm (< {self.session.project.params.min_wall:g})."
        return txt + f"; margen al borde {margin:.1f} mm."

    def _place_head_pin(self, point, what: str = "Pasador del cuello movido") -> bool:
        try:
            self.session.set_head_pin(point)
        except ValueError as e:
            self._changed(f"No se movió: {e}.", scene=False)
            return False
        self._changed(f"{what}: " + self._head_pin_status() + " Hay que generar de nuevo.")
        return True

    def _click_head_pin(self, px: float, py: float, width: float, height: float) -> None:
        """El clic se lleva al plano de simetría (x = 0), donde está el pasador, y de ahí al plano del cuello."""
        origin, direction = self.camera.ray(px, py, width, height)
        if abs(direction[0]) < 1e-6:
            self._changed("Esta vista mira de canto el plano de simetría: usá «Derecha» o «Izquierda» (vista de costado).",
                          scene=False)
            return
        self._place_head_pin(origin - origin[0] / direction[0] * direction)

    def nudge_head_pin(self, du: float, dv: float, big: bool = False) -> None:
        """Mueve el pasador del cuello `du + dv` pasos sobre la cara (+ = adelante)."""
        if self.mode != Mode.HEAD_PIN or self.head_pin_prev is None:
            return
        step = NUDGE_MM * (10.0 if big else 1.0)
        c = self._head_pin_center() + self.head_pin_prev.along * (du + dv) * step
        self._place_head_pin(c, "Pasador del cuello ajustado")

    def reset_head_pin(self) -> None:
        """Descarta la posición elegida del pasador del cuello y vuelve a la automática."""
        if self.session.project.head is None or self.session.project.head.pin_center is None:
            self._changed("El pasador del cuello ya está en la posición automática.", scene=False)
            return
        self.session.set_head_pin(None)
        if self.mode != Mode.HEAD_PIN:
            self.start_head_pin()                  # vuelve a la vista de costado para ver y reposicionar el pasador
            self._changed("Pasador del cuello restablecido: " + self._head_pin_status()
                          + " Clic = mover; flechas ←/↓ atrás y →/↑ adelante; Esc termina. Hay que generar de nuevo.")
            return
        self._changed("Pasador del cuello restablecido: " + self._head_pin_status() + " Hay que generar de nuevo.")

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

    def legs_straight(self) -> bool:
        """True si la vista de pose muestra las piernas estiradas (rodilla a 0°, muslo y pantorrilla horizontales):
        con la casilla «sin soportes» activa o mientras se ajusta la altura del corte."""
        return self.flat_print or self.mode == Mode.FLAT_PLANE

    def _posed_pieces(self, angle: float) -> dict:
        """Miembros de la figura generada con las piernas giradas `angle` grados (izquierdos y sus espejos).
        Con `legs_straight()` la rodilla va a 0° en vez de la flexión de la pose impresa."""
        out = self.session.outcome
        straight = self.legs_straight()
        c = self._pose_cache
        if c is not None and c[0] is out and abs(c[1] - angle) < 1e-9 and c[2] == straight:
            return c[3]
        from ..pipeline import posed_pieces, straight_leg_flex       # cadena muslo/pantorrilla, brazo/antebrazo; la cabeza no participa
        pieces = posed_pieces(out.result, angle, straight_leg_flex(out.result) if straight else None)
        self._pose_cache = (out, angle, straight, pieces)
        return pieces

    def flat_click_advice(self) -> dict:
        """Brazos con clic que no apoyan en el plano del corte actual (ver `pipeline.flat_click_advice`). {} sin figura."""
        out = self.session.outcome
        if out is None:
            return {}
        from ..pipeline import flat_click_advice
        return flat_click_advice(out.result, float(self.session.project.params.flat_depth))

    def apply_flat_click_angle(self) -> bool:
        """Pone «Clic: ángulo del brazo al imprimir» en el ángulo en que el brazo apoya en el plano del corte «sin
        soportes». Cambia la fase de los clics: invalida la figura y hay que generar de nuevo."""
        adv = {n: a for n, a in self.flat_click_advice().items() if a["angulo_apoyo"] is not None}
        if not adv:
            self._changed("Ningún brazo con clic necesita otro ángulo para apoyar en el plano del corte.", scene=False)
            return False
        angle = max(a["angulo_apoyo"] for a in adv.values())            # hoy hay un solo brazo con clic: el parámetro es global
        return self.set_param("detent.print_angle", round(angle))

    def flat_click_note(self) -> str:
        adv = self.flat_click_advice()
        if not adv:
            return ""
        a = next(iter(adv.values()))
        if a["angulo_apoyo"] is None:
            return f" El brazo con clic cuelga {a['cuelga_mm']:.1f} mm bajo el plano y no llega a apoyar."
        return (f" El brazo con clic cuelga {a['cuelga_mm']:.1f} mm bajo el plano: para que apoye, el clic tiene que quedar a "
                f"{a['angulo_apoyo']:.0f}° (ahora {a['angulo_actual']:.0f}°): usá «Ajustar el clic del brazo» y generá de nuevo.")

    def set_flat_print(self, on: bool) -> None:
        """Casilla «Modificar para imprimir sin soportes»: al activarla la figura generada se muestra sentada y con las
        piernas estiradas (es como se exporta); al apagarla vuelve la rodilla a la flexión de la pose impresa."""
        on = bool(on)
        if on == self.flat_print:
            return
        self.flat_print = on
        self._pose_cache, self.pose_report = None, None
        if self.session.outcome is None:
            return
        if not self.pose_view:
            if not on:
                return
            self.set_pose_view(True)                 # ya calcula el informe con las piernas estiradas
        else:
            self._pose_update()
        if on and (note := self.flat_click_note()):
            self._changed(self.message + note, scene=False)

    def pose_check(self, angle: float | None = None) -> dict | None:
        """Interferencias de la figura generada con las piernas giradas `angle` grados (None = el de la vista previa).

        Devuelve ``{"angle", "limit", "torso": {pieza: mm³}, "pairs": {(a, b): mm³}, "ok"}``; solo figuran las
        interferencias mayores que la tolerancia de `pipeline.validate`. None si todavía no se generó la figura."""
        out = self.session.outcome
        if out is None:
            return None
        angle = self._pose_angle() if angle is None else float(angle)
        pieces = self._posed_pieces(angle)
        torso = body_ref(out.result)         # con cintura: pecho + pelvis
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
                "ok": not torso_hits and not pairs, "estiradas": self.legs_straight()}

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
        if r.get("estiradas"):
            txt += " (piernas estiradas, como se imprimen sin soportes)"
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

    # ------------------------------------------------ plano de corte «sin soportes» (piernas y cola)
    def flat_base_z(self) -> float | None:
        """Altura del punto más bajo del torso generado (la cola): desde ahí se mide `flat_depth`. None sin figura."""
        out = self.session.outcome
        return None if out is None else body_base_z(out.result.pieces)

    def flat_cut_z(self) -> float | None:
        base = self.flat_base_z()
        return None if base is None else base + float(self.session.project.params.flat_depth)

    def start_flat_plane(self) -> None:
        """Muestra la figura generada sentada (como «ver la figura en la pose de cadera»), de costado (vista Derecha),
        con el plano horizontal del corte «sin soportes»; un clic o las flechas ↑↓ fijan su altura."""
        if not self._need_loaded():
            return
        if self.session.outcome is None:
            self._changed("Primero generá la figura: el plano de corte se ajusta sobre la figura generada y sentada.",
                          scene=False)
            return
        self.set_pose_view(True)
        self.mode = Mode.FLAT_PLANE
        self.pose_report = self.pose_check()         # piernas estiradas mientras se ajusta el corte
        self.camera.frame(self.session.work.bounds)
        self.camera.set_view("derecha")
        self._changed(self.flat_message(self.session.project.params.flat_depth) +
                      " Hacé clic a la altura que querés (o ↑ ↓, Mayús = 1 mm); Esc termina.")

    def flat_message(self, depth: float) -> str:
        return f"Corte sin soportes: {depth:.1f} mm sobre la cola (z = {self.flat_base_z() + depth:.1f} mm)."

    def set_flat_depth(self, depth: float) -> float | None:
        """Fija la altura del corte (mm sobre la cola), dentro del rango de `Parámetros`. No invalida la figura."""
        spec = next(f for f in PARAM_FIELDS if f[0] == "flat_depth")
        lo, hi = spec[2], spec[3]
        wanted = float(depth)
        d = float(np.clip(wanted, lo, hi))
        self.session.project.params.flat_depth = round(d, 3)
        note = ""
        if abs(d - wanted) > 1e-9:
            note = f" (el rango es {lo:g}–{hi:g} mm)"
        self._changed(self.flat_message(d) + note)
        return d

    def nudge_flat(self, direction: int, big: bool = False) -> None:
        """↑ sube el plano, ↓ lo baja `FLAT_NUDGE_MM` (Mayús = 10 veces más)."""
        if self.mode != Mode.FLAT_PLANE or self.session.outcome is None:
            return
        step = FLAT_NUDGE_MM * (10.0 if big else 1.0) * (1 if direction > 0 else -1)
        self.set_flat_depth(self.session.project.params.flat_depth + step)

    def _click_flat_plane(self, px: float, py: float, width: float, height: float) -> None:
        """La altura es la z del clic sobre el plano medio (x = 0), que de costado es justo donde se hizo clic."""
        base = self.flat_base_z()
        if base is None:
            return
        origin, direction = self.camera.ray(px, py, width, height)
        if abs(direction[0]) < 0.2:
            self._changed("Mirá la figura de costado (botón Derecha o Izquierda) para fijar la altura con un clic.",
                          scene=False)
            return
        t = (0.0 - origin[0]) / direction[0]
        z = float(origin[2] + t * direction[2])
        self.set_flat_depth(z - base)

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
        """Máscara de caras voladizas de ``session.work`` y la orientación usada.

        Es una referencia sobre la figura entera tal como se ve (cabeza arriba, sin girarla: es la orientación en que
        se exportan las piezas, ver `pose_orientation`); no sobre las piezas ya cortadas. Con la figura generada, los
        voladizos se marcan sobre la vista de la pose de cadera (`_pose_overhangs`). La rotación conserva el orden de
        las caras, así que la máscara sirve tal cual para dibujar ``work``.
        """
        work = self.session.work
        if work is None:
            return None
        key_bounds = work.bounds.tobytes()
        c = self._overhang_cache
        if c is None or c[0] is not work or c[1] != key_bounds or c[2] != self.overhang_angle:
            res = pose_orientation(work, self.overhang_angle)
            oriented = work.copy()
            oriented.apply_transform(res.rotation)
            c = self._overhang_cache = (work, key_bounds, self.overhang_angle,
                                        overhang_mask(oriented, self.overhang_angle), res)
        return c[3], c[4]

    def _pose_overhangs(self, meshes: dict) -> dict:
        """Voladizos de las piezas de la vista de pose: {nombre: máscara de caras}. Todas comparten la altura de la
        cama (el mínimo del conjunto), como al imprimirlas juntas en la pose sentada."""
        zmin = min(m.bounds[0][2] for m in meshes.values())
        return {n: overhang_mask(m, self.overhang_angle, zmin=zmin) for n, m in meshes.items()}

    def _overhang_message(self) -> str:
        found = self.overhang_faces()
        if found is None:
            return "Primero abrí un STL."
        _mask, res = found
        if self.pose_active():
            area = sum(float(m.area_faces[mk].sum()) for m, mk in self._pose_overhang_cache())
            return (f"Voladizos en rojo (pared a más de {self.overhang_angle:.0f}° de la vertical) sobre la figura "
                    f"generada en la pose de cadera ({self._pose_angle():.0f}°), cabeza arriba, como se exporta: "
                    f"{area:.0f} mm².")
        return (f"Voladizos en rojo (pared a más de {self.overhang_angle:.0f}° de la vertical): "
                f"{res.overhang_area:.0f} mm² con la figura vista de frente, cabeza arriba. Referencia sobre la figura "
                "entera; generala y mirala en la pose de cadera para ver los de las piezas.")

    @staticmethod
    def _torso_meshes(out) -> dict:
        """Mallas del torso generado: una sola, o pecho («torso») y pelvis si hay cintura."""
        return {n: S.to_trimesh(out.result.pieces[n]) for n in ("torso", PELVIS_NAME) if n in out.result.pieces}

    def _pose_overhang_cache(self) -> list:
        """[(malla, máscara)] de las piezas de la pose mostrada (torso + miembros)."""
        out = self.session.outcome
        meshes = self._torso_meshes(out)
        for name, mf in self._posed_pieces(self._pose_angle()).items():
            meshes[name] = S.to_trimesh(mf)
        masks = self._pose_overhangs(meshes)
        return [(meshes[n], masks[n]) for n in meshes]

    def set_show_overhangs(self, on: bool) -> None:
        self.show_overhangs = bool(on)
        if self.show_overhangs and self.session.outcome is not None and not self.pose_view:
            self.set_pose_view(True)          # con la figura generada, los voladizos se ven en la pose de cadera
        self._changed(self._overhang_message() if self.show_overhangs and self.loaded else None)

    def set_overhang_angle(self, angle: float) -> None:
        self.overhang_angle = float(np.clip(angle, 10.0, 80.0))
        self._changed(self._overhang_message() if self.show_overhangs and self.loaded else None)

    # ----------------------------------------------------------------- vértice bajo el puntero
    def hover_active(self) -> bool:
        """True en los modos en que se eligen puntos sobre la figura (planos de 3 puntos, guías de 4)."""
        return (self.picker is not None and not self.busy and
                self.mode in (Mode.SYMMETRY, Mode.LIMB_PLANE, Mode.NECK_PLANE, Mode.HEAD_CUT_PLANE, Mode.WAIST_PLANE, Mode.HAND_EXTRACT,
                              Mode.HAND_PLACE, Mode.JOINT_PLANE, Mode.JOINT_LINES, Mode.JOINT_SIDE))

    def update_hover(self, px: float, py: float, width: float, height: float) -> bool:
        """Marca (en rojo) el vértice de la figura que el puntero tiene cerca; lo suelta al alejarse. Devuelve True si
        cambió lo que hay que dibujar."""
        if not self.hover_active():
            return self.clear_hover()
        found = self.picker.nearest_vertex(self.camera, px, py, width, height, HOVER_RADIUS_PX)
        new = None if found is None else found[1]
        old = self.hover_vertex
        if (new is None) == (old is None) and (new is None or np.allclose(new, old)):
            return False
        self.hover_vertex = new
        self.scene_dirty = True
        return True

    def clear_hover(self) -> bool:
        if self.hover_vertex is None:
            return False
        self.hover_vertex = None
        self.scene_dirty = True
        return True

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
        if self.mode == Mode.HEAD_PIN and self.head_pin_prev is not None:
            self._click_head_pin(px, py, width, height)
            return None
        if self.mode == Mode.FLAT_PLANE:
            self._click_flat_plane(px, py, width, height)
            return None
        if self.picker is None or self.mode == Mode.VIEW:
            return None
        hit = self.picker.pick_pixel(self.camera, px, py, width, height)
        if self.hover_active() and (v := self.picker.nearest_vertex(self.camera, px, py, width, height, HOVER_RADIUS_PX)) is not None:
            base = hit if hit is not None else self.picker.pick_pixel(self.camera, px, py, width, height, 1e9)
            hit = PickResult(v[1], base.hit.copy(), base.face, base.normal.copy(), True)    # el vértice en rojo es el punto elegido
            self.clear_hover()
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
            need = 4 if self.mode in (Mode.JOINT_LINES, Mode.JOINT_SIDE) else 3
            if len(self.pending) < need:
                if self.mode == Mode.JOINT_LINES:
                    self._changed(self._joint_lines_hint())
                elif self.mode == Mode.JOINT_SIDE:
                    self._changed(f"Guía de costado: punto {len(self.pending)}/4. Backspace deshace el último; Esc cancela.")
                else:
                    self._changed(f"Punto {len(self.pending)}/3. Backspace deshace el último; Esc cancela.")
            elif self.mode == Mode.JOINT_LINES:
                self._finish_joint_lines()
            elif self.mode == Mode.JOINT_SIDE:
                self._finish_joint_side()
            elif self.mode == Mode.SYMMETRY:
                self._finish_symmetry()
            elif self.mode == Mode.NECK_PLANE:
                self._finish_neck_plane()
            elif self.mode == Mode.HEAD_CUT_PLANE:
                self._finish_head_cut_plane()
            elif self.mode == Mode.WAIST_PLANE:
                self._finish_waist_plane()
            elif self.mode == Mode.HAND_EXTRACT:
                self._finish_hand_extract()
            elif self.mode == Mode.HAND_PLACE:
                self._finish_hand_place()
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
        self._changed(f"Coronilla marcada a {z:.1f} mm: la figura se reescaló a {self.session.project.params.ref_height:g} mm.")

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

    def _finish_waist_plane(self) -> None:
        pts, self.pending = self.pending, []
        try:
            self.session.set_waist(Session.plane_from_points(pts, WAIST_NAME))
        except ValueError as e:
            self._changed(f"No sirve: {e}. Elegí 3 puntos de nuevo.")
            return
        self.mode = Mode.VIEW
        self._changed("Plano de la cintura definido: el torso se corta en pecho y pelvis, unidos por un pasador robusto; "
                      "la figura gira a los costados sobre la cintura.")

    # ------------------------------------------------------------ mano estándar (extraer)
    def start_hand_extract(self) -> None:
        if not self._need_loaded():
            return
        if self.session.project.symmetry is None:
            self._changed("Primero definí el plano de simetría.", scene=False)
            return
        self.cancel(quiet=True)
        self.hand_extract = self._hand_mesh = None
        self.mode = Mode.HAND_EXTRACT
        self._changed("Mano estándar: elegí 3 puntos en la muñeca IZQUIERDA (+X). Marcá el punto 1 siempre en el mismo "
                      "lugar (p. ej. el lado del pulgar) y los otros dos a ambos lados de la muñeca: así la mano queda "
                      "orientada igual en todos los modelos. Lo de la mano se corta y se guarda aparte.")

    def _finish_hand_extract(self) -> None:
        pts, self.pending = self.pending, []
        self._run_hand_extract([tuple(map(float, q)) for q in pts], invert=False)

    def _run_hand_extract(self, points, invert: bool) -> bool:
        try:
            hand, split = self.session.extract_hand(points, invert=invert)
        except ValueError as e:
            self.mode = Mode.VIEW
            self.hand_extract = self._hand_mesh = None
            self._changed(f"No sirve: {e}. Probá de nuevo con «Extraer mano estándar».")
            return False
        self.hand_extract = {"hand": hand, "split": split, "points": points, "invert": invert}
        self._hand_mesh = None
        self.mode = Mode.VIEW
        side = "izquierda" if split.frame.center[0] > 0 else "derecha"
        self._changed(f"Mano {side} cortada (verde): muñeca Ø{2 * split.face_radius:.1f} mm, "
                      f"{split.hand_volume:.0f} mm³. Si no es la mano, usá «Invertir lado»; si está bien, «Guardar mano estándar…».")
        return True

    def invert_hand_extract(self) -> None:
        """Vuelve a cortar con los mismos 3 puntos tomando el otro lado del plano como mano."""
        if self.hand_extract is None:
            self._changed("Primero extraé una mano.", scene=False)
            return
        self._run_hand_extract(self.hand_extract["points"], invert=not self.hand_extract["invert"])

    def discard_hand_extract(self) -> None:
        self.hand_extract = self._hand_mesh = None
        self._changed("Mano extraída descartada.")

    def save_hand_extract(self, path: str) -> dict | None:
        """Guarda la mano extraída como mano estándar (STL + `.mano.json` al lado)."""
        if self.hand_extract is None:
            self._changed("Primero extraé una mano.", scene=False)
            return None
        meta = self.session.save_hand(path, self.hand_extract["hand"], self.hand_extract["split"])
        msg = f"Mano estándar guardada: {path.split('/')[-1]} (figura a {meta['altura_figura_mm']:g} mm)."
        from ..hand import LINE_HEIGHT_MM
        if abs(float(meta["altura_figura_mm"]) - LINE_HEIGHT_MM) > 0.5:
            msg += f" AVISO: la línea usa figuras de {LINE_HEIGHT_MM:g} mm."
        self._changed(msg, scene=False)
        return meta

    def hand_preview(self):
        """Malla de la mano extraída en el marco de trabajo (para dibujarla), o None."""
        if self.hand_extract is None:
            return None
        split = self.hand_extract["split"]
        if self._hand_mesh is None or self._hand_mesh[0] is not split:
            self._hand_mesh = (split, S.to_trimesh(split.hand))
        return self._hand_mesh[1]

    # ------------------------------------------------------------ mano estándar (pegar en este modelo)
    def start_hand_place(self, path: str) -> None:
        """Mano de reemplazo: con el STL de la mano estándar elegido, pide los 3 puntos de la muñeca de este modelo."""
        if not self._need_loaded():
            return
        if self.session.project.symmetry is None:
            self._changed("Primero definí el plano de simetría.", scene=False)
            return
        from ..hand import load_standard_hand
        try:
            load_standard_hand(path)
        except Exception as e:                                  # archivo ilegible, no es una malla, JSON de otra versión…
            self._changed(f"No se pudo leer esa mano estándar: {e}", scene=False)
            return
        self.cancel(quiet=True)
        self._hand_path_pending = path
        self.mode = Mode.HAND_PLACE
        self._changed("Mano de reemplazo: elegí 3 puntos en la muñeca de este modelo (vale cualquiera de los dos brazos). "
                      "Marcá el punto 1 en el mismo lugar que al extraer la mano (p. ej. el lado del pulgar) y los otros dos a "
                      "ambos lados de la muñeca. Se descarta la mano del modelo y se pega la estándar, sin escalarla.")

    def _finish_hand_place(self) -> None:
        pts, self.pending = self.pending, []
        path, self._hand_path_pending = self._hand_path_pending, None
        self.mode = Mode.VIEW
        try:
            info = self.session.set_hand(path, [tuple(map(float, q)) for q in pts])
        except ValueError as e:
            self._changed(f"No sirve: {e}. Probá de nuevo con «Mano de reemplazo (STL)…».")
            return
        self._changed(self._hand_fit_message(info))

    @staticmethod
    def _hand_fit_message(info: dict) -> str:
        msg = (f"Mano estándar pegada (turquesa): muñeca del modelo Ø{2 * info['radio_brazo_mm']:.1f} mm, de la mano "
               f"Ø{2 * info['radio_mano_mm']:.1f} mm. Si no cae bien usá «Invertir lado» o el giro.")
        for a in info["avisos"]:
            msg += f" AVISO: {a}."
        return msg

    def set_hand_adjust(self, yaw: float | None = None, invert: bool | None = None) -> bool:
        """Cambia el giro (°) y/o el lado de la mano de reemplazo ya pegada. Si no sirve, no cambia nada."""
        if not self._need_loaded():
            return False
        try:
            info = self.session.adjust_hand(yaw, invert)
        except ValueError as e:
            self._changed(f"No sirve: {e}", scene=False)
            return False
        self._changed(self._hand_fit_message(info))
        return True

    def invert_hand_place(self) -> bool:
        cur = self.session.project.hand
        return self.set_hand_adjust(invert=None if cur is None else not cur.invert)

    def clear_hand(self) -> None:
        self._changed("Se vuelve a usar la mano original del modelo." if self.session.clear_hand()
                      else "No había mano de reemplazo.")

    def hand_fit(self):
        """{"figure", "hand", "info"} de la mano de reemplazo para dibujarla, o None si no hay."""
        return self.session.hand_replacement_preview() if self.loaded else None

    def set_head_replacement(self, path: str, yaw: float | None = None, up: str | None = None,
                             rx: float | None = None, ry: float | None = None,
                             scale: float | None = None, cut_points="keep") -> bool:
        """Cabeza de reemplazo (STL): descarta la original, sin simetría, base escalada a la del cuello.

        Controles (None = conservar el valor actual): `up` (eje «arriba»), `rx`/`ry`/`yaw` (giros en °) y `scale`
        (factor manual sobre la escala automática). `cut_points` es el plano de corte de la base (3 puntos en
        coordenadas del STL): «keep» conserva el actual, None lo quita."""
        if not self._need_loaded():
            return False
        cur = self.session.project.head

        def keep(new, attr, default):
            return (getattr(cur, attr) if cur else default) if new is None else new

        yaw, up = keep(yaw, "replacement_yaw", 0.0), keep(up, "replacement_up", "+Z")
        rx, ry = keep(rx, "replacement_rx", 0.0), keep(ry, "replacement_ry", 0.0)
        scale = keep(scale, "replacement_scale", 1.0)
        if isinstance(cut_points, str) and cut_points == "keep":
            cut_points = cur.replacement_cut_points if cur else None
        try:
            info = self.session.set_head_replacement(path, yaw, up, rx, ry, scale, cut_points)
        except Exception as e:                     # archivo ilegible, malla irreparable, plano sin cabeza…
            self._changed(f"No se pudo usar esa cabeza: {e}", scene=False)
            return False
        self._changed(self._head_fit_message(info))
        return True

    @staticmethod
    def _head_fit_message(info: dict) -> str:
        msg = (f"Cabeza de reemplazo lista (se ve en naranja, apoyada en el cuello): escala ×{info['escala']:.3f} "
               f"(uniforme), alto {info['alto_mm']:.1f} mm, base coincide {100 * info['coincidencia_base']:.0f} %.")
        if info["corte_plano"]:
            msg += " La base se cortó con el plano de 3 puntos."
        elif info["base_cortada"]:
            msg += " No traía base plana: se cortó una fracción baja para apoyarla."
        if info["coincidencia_base"] < MIN_BASE_IOU:
            msg += " AVISO: la base se parece poco a la del cuello; probá otro giro, eje «arriba», escala o plano de corte (Planos → Cabeza)."
        return msg

    def set_head_adjust(self, up: str | None = None, yaw: float | None = None, rx: float | None = None,
                        ry: float | None = None, scale: float | None = None) -> bool:
        """Cambia orientación (`up`, `rx`, `ry`, `yaw`) y/o escala manual de la cabeza de reemplazo ya cargada y la
        vuelve a ajustar (el plano de corte de la base, si hay, acompaña a la malla). Si el valor no vale, no cambia
        nada y deja el motivo en `message`."""
        cur = self.session.project.head
        if cur is None or not cur.replacement_path:
            self._changed("Primero cargá una cabeza de reemplazo (STL).", scene=False)
            return False
        return self.set_head_replacement(cur.replacement_path, yaw, up, rx, ry, scale)

    # ---- corte de la base de la cabeza de reemplazo con un plano de 3 puntos
    def start_head_cut_plane(self) -> None:
        """Pide 3 puntos sobre la cabeza de reemplazo (la naranja) que definen el plano que corta su base."""
        prev = self.head_preview()
        if prev is None:
            self._changed("Primero cargá una cabeza de reemplazo (STL).", scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.HEAD_CUT_PLANE
        self.picker = Picker(prev[0])                # los clics caen sobre la cabeza nueva, no sobre la figura
        self._changed("Corte de la base: elegí 3 puntos sobre la cabeza naranja (2 a los lados y 1 al frente, a la altura donde "
                      "querés cortar). Se descarta lo de abajo del plano; Retroceso deshace el último punto, Esc cancela.")

    def _finish_head_cut_plane(self) -> None:
        pts, self.pending = self.pending, []
        try:
            info = self.session.set_head_cut_plane(pts)
        except Exception as e:
            self._changed(f"No sirve: {e}. Elegí 3 puntos de nuevo.")
            return
        self.mode = Mode.VIEW
        self._rebuild_picker()
        self._changed(self._head_fit_message(info))

    def clear_head_cut_plane(self) -> None:
        self._changed("Se quitó el plano de corte de la base de la cabeza." if self.session.clear_head_cut_plane()
                      else "La cabeza no tenía plano de corte.")

    def set_head_orientation(self, up: str | None = None, yaw: float | None = None) -> bool:
        """Cambia el eje «arriba» y/o el giro de la cabeza de reemplazo ya cargada y la vuelve a ajustar."""
        return self.set_head_adjust(up=up, yaw=yaw)

    def head_preview(self):
        """(malla, info) de la cabeza de reemplazo para dibujarla, o None si no hay."""
        return self.session.head_replacement_preview() if self.loaded else None

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
        waist = self.session.project.waist
        if waist is not None:
            out.append(PlaneEntry(WAIST_NAME, "Cintura (giro)", waist.plane, "waist", WAIST_NAME not in self.hidden))
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
            self.session.project.waist = None
            self._rebuild_picker()
            self.hidden.clear()
            self._changed("Simetría quitada (también los miembros: dependen de ella).")
            return
        if plane_id.startswith(JOINT_PREFIX):
            self.cancel(quiet=True)
            self.joint_guides.pop(plane_id[len(JOINT_PREFIX):], None)
            self.session.remove_joint(plane_id[len(JOINT_PREFIX):])
        elif plane_id == HEAD_NAME:
            self.session.remove_head()
        elif plane_id == WAIST_NAME:
            self.session.remove_waist()
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

    def begin_export(self, outdir: str, orient: bool = True, flat: bool = False):
        """Como `begin_generate`: valida y devuelve el trabajo pesado ``job(progress=None) -> dict`` (apto para un hilo).
        Al terminar hay que llamar `finish_export` desde el hilo de la interfaz. None (con el motivo en `message`) si no se puede."""
        if self.busy:
            return None
        if self.session.outcome is None:
            self._changed("Primero generá la figura (los cambios posteriores invalidan el resultado).", scene=False)
            return None
        self.busy = True
        self._changed("Exportando las piezas… puede tardar.", scene=False)
        sess, angle = self.session, self.overhang_angle
        return lambda progress=None: sess.export(outdir, orient=orient, max_angle=angle, flat=flat)

    def finish_export(self, info: dict | None, outdir: str, error: Exception | None = None) -> dict | None:
        self.busy = False
        if error is not None:
            self._changed(f"Falló la exportación: {error}", scene=False)
            return None
        files = info["archivos"]
        msg = f"Exportado en {outdir}: ensamblada + {len(files['piezas'])} piezas."
        ori = files.get("orientacion")
        if ori:
            msg += f" Orientación: {ori['etiqueta']}, voladizo {ori['voladizo_mm2']} mm², base {ori['base_mm2']} mm²."
        ss = files.get("sin_soportes")
        if ss:
            giro = ss["giro_brazos_grados"]
            msg += (f" Sin soportes: corte {ss['corte_sobre_cola_mm']:g} mm sobre la cola, brazos girados "
                    + (", ".join(f"{a:.0f}°" for a in giro.values()) or "0°") + " hacia adelante.")
            if ss["avisos"]:
                msg += " AVISO: " + "; ".join(ss["avisos"])
        self._changed(msg, scene=False)
        return info

    def export(self, outdir: str, orient: bool = True, flat: bool = False) -> dict | None:
        """Escribe las piezas, el ensamblado, el informe y el proyecto en `outdir` (sin hilos: pruebas y uso programático)."""
        job = self.begin_export(outdir, orient=orient, flat=flat)
        if job is None:
            return None
        try:
            info = job()
        except Exception:
            self.busy = False
            raise
        return self.finish_export(info, outdir)

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
        cplane = pin_plane = self._pin_clip_plane()
        hprev = self.head_preview()
        if cplane is None and hprev is not None:                     # la cabeza original se oculta: va la de reemplazo
            n = neck_plane(self.session.project.head).normal
            cplane = (float(n[0]), float(n[1]), float(n[2]),
                      float(n @ self.session.project.head.plane.origin) + 1e-3)
        extra = {} if cplane is None else {"clip_plane": cplane}      # modo PIN o cabeza de reemplazo
        found = self.overhang_faces() if self.show_overhangs else None
        hfit = self.hand_fit() if not (found is not None and found[0].any()) else None   # con voladizos se ve la figura tal cual
        base = sess.work if hfit is None else hfit["figure"]          # con mano de reemplazo, sin la mano original
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
            scene.set_mesh("figura", base, clip_x=clip, **extra)
            scene.remove("voladizos")
            drawn = [("figura", base, _MIRROR_COLOR)]
        if hfit is not None:
            scene.set_mesh("mano:reemplazo", hfit["hand"], color=_REPLACEMENT_HAND_COLOR, clip_x=clip)
            drawn.append(("mano:reemplazo", hfit["hand"], _MIRROR_COLOR))
        else:
            scene.remove("mano:reemplazo")
        if hprev is not None:
            scene.set_mesh("cabeza:reemplazo", hprev[0], color=_REPLACEMENT_HEAD_COLOR, clip_x=clip)
            if pin_plane is None:                      # el espejo no lleva la cabeza original (la nueva no se simetriza)
                drawn = [("figura", self._work_without_head(), _MIRROR_COLOR)] + [d for d in drawn if d[0] == "mano:reemplazo"]
        else:
            scene.remove("cabeza:reemplazo")
        hand = self.hand_preview()
        if hand is not None:
            scene.set_mesh("mano:extraida", hand, color=_EXTRACTED_HAND_COLOR, clip_x=clip)
        else:
            scene.remove("mano:extraida")
        hs = self.plane_half_size
        palette = {"symmetry": (1.0, 0.55, 0.2, 0.18), "arm": (0.3, 0.6, 1.0, 0.22), "leg": (0.4, 0.85, 0.5, 0.22),
                   "head": (0.9, 0.5, 0.9, 0.22), "joint": (1.0, 0.85, 0.2, 0.22),
                   "waist": (0.95, 0.35, 0.35, 0.22)}
        wanted = set()
        for e in self.plane_entries():
            key = f"plano:{e.id}"
            wanted.add(key)
            scene.set_plane(key, e.plane, hs, center=sess.work.bounds.mean(axis=0), color=palette[e.kind])
            scene.show(key, e.visible)
        for key in [k for k in scene.items if k.startswith("plano:") and not k.endswith(":borde")]:
            if key not in wanted:
                scene.remove(key)
        if self.hover_vertex is not None and self.hover_active():
            scene.set_points("hover:vertice", self.hover_vertex[None], (1.0, 0.0, 0.0, 1.0), 18.0, on_top=True)
        else:
            scene.remove("hover:vertice")
        if self.pending and self.mode != Mode.JOINT_LINES:
            pts = np.array(self.pending)
            scene.set_points("pendientes", pts, (1.0, 0.25, 0.25, 1.0), 12.0)
            scene.set_lines("pendientes:lineas", pts, (1.0, 0.25, 0.25, 1.0), closed=len(pts) == 3)
        else:
            scene.remove("pendientes")
            scene.remove("pendientes:lineas")
        wanted_guides = set()
        for kind, key, gpts, color in self.guide_overlays():
            wanted_guides.add(key)
            if kind == "lines":
                scene.set_lines(key, gpts, color, on_top=True)
            else:
                scene.set_points(key, gpts, color, 12.0, on_top=True)
        for key in [k for k in scene.items if k.startswith("guia:") and k not in wanted_guides]:
            scene.remove(key)
        self._sync_polygons(scene)
        self._sync_pins(scene)
        self._sync_head_pin(scene)
        self._sync_joints(scene)
        self._sync_trims(scene)
        self._sync_mirror(scene, drawn, pin_plane)

    def _work_without_head(self) -> trimesh.Trimesh:
        """La malla de trabajo sin la cabeza original (lado del cuello hacia arriba), en caché: solo para el espejo."""
        head = self.session.project.head
        key = (id(self.session.work), head.plane.origin.tobytes(), head.plane.normal.tobytes())
        cache = getattr(self, "_headless_cache", None)
        if cache is None or cache[0] != key:
            n = neck_plane(head).normal
            mesh = trimesh.intersections.slice_mesh_plane(self.session.work, plane_normal=-n,
                                                          plane_origin=head.plane.origin - n * 1e-3, cap=False)
            cache = self._headless_cache = (key, mesh)
        return cache[1]

    def _sync_pose(self, scene) -> None:
        """Vista previa de la pose: torso generado + miembros girados al ángulo elegido (rojo = interfiere)."""
        scene.clear()
        out = self.session.outcome
        if self.pose_report is None:
            self.pose_report = self.pose_check()
        rep = self.pose_report
        bad = set(rep["torso"]) | {n for pair in rep["pairs"] for n in pair}
        meshes = self._torso_meshes(out)
        colors = {n: (0.62, 0.68, 0.78, 1.0) for n in meshes}
        for name, mf in self._posed_pieces(rep["angle"]).items():
            meshes[name] = S.to_trimesh(mf)
            # con los voladizos en rojo, la interferencia se marca en magenta para no confundirlos
            hit = (0.85, 0.3, 0.85, 1.0) if self.show_overhangs else (0.95, 0.28, 0.28, 1.0)
            colors[name] = hit if name in bad else (0.85, 0.80, 0.68, 1.0)
        masks = self._pose_overhangs(meshes) if self.show_overhangs else {}
        for name, mesh in meshes.items():
            mk = masks.get(name)
            if mk is not None and mk.any():
                v, f = mesh.vertices, mesh.faces
                scene.set_mesh(f"pose:{name}", trimesh.Trimesh(v, f[~mk], process=False), color=colors[name])
                scene.set_mesh(f"pose:{name}:voladizo", trimesh.Trimesh(v, f[mk], process=False),
                               color=(0.95, 0.25, 0.25, 1.0))
            else:
                scene.set_mesh(f"pose:{name}", mesh, color=colors[name])
        if self.mode == Mode.FLAT_PLANE:
            self._sync_flat_plane(scene, meshes)

    def _sync_flat_plane(self, scene, meshes: dict) -> None:
        """Plano horizontal del corte «sin soportes»: cuadro translúcido + línea gruesa delante (de costado se ve de canto)."""
        z = self.flat_cut_z()
        lo = np.min([m.bounds[0] for m in meshes.values()], axis=0)
        hi = np.max([m.bounds[1] for m in meshes.values()], axis=0)
        center = (lo + hi) / 2
        hs = float(np.linalg.norm(hi - lo)) * 0.5
        plane = Plane.from_normal((center[0], center[1], z), (0.0, 0.0, 1.0), "corte sin soportes")
        scene.set_plane("plano_sin_soportes", plane, hs, center=(center[0], center[1], z), color=(1.0, 0.55, 0.1, 0.25))
        x = float(hi[0]) + 1.0
        line = np.array([[x, lo[1] - 0.15 * hs, z], [x, hi[1] + 0.15 * hs, z]])
        scene.set_lines("plano_sin_soportes:linea", line, (1.0, 0.45, 0.0, 1.0), on_top=True)

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

    def _sync_head_pin(self, scene) -> None:
        """Pasador del cuello: un punto si se eligió a mano y, en el modo HEAD_PIN, la cara de canto, el contorno de
        costado del pasador (vástago + cabeza), el centro y el punto automático."""
        head = self.session.project.head
        keys = ("pasador_cabeza:cara", "pasador_cabeza:perfil", "pasador_cabeza:centro", "pasador_cabeza:auto",
                "pasador_cabeza:elegido")
        prev = self.head_pin_prev if self.mode == Mode.HEAD_PIN else None
        if head is None or prev is None:
            for k in keys[:4]:
                scene.remove(k)
            if head is not None and head.pin_center is not None:
                scene.set_points(keys[4], np.asarray(head.pin_center, float)[None], (1.0, 0.55, 0.1, 1.0), 10.0)
            else:
                scene.remove(keys[4])
            return
        scene.remove(keys[4])
        c = self._head_pin_center()
        scene.set_lines(keys[0], prev.trace(), (1.0, 1.0, 1.0, 1.0), closed=False, on_top=True)
        scene.set_lines(keys[1], prev.profile(c, self.session.project.params.neck_pin), (1.0, 0.55, 0.1, 1.0),
                        closed=True, on_top=True)
        scene.set_points(keys[2], c[None], (1.0, 0.25, 0.25, 1.0), 9.0, on_top=True)
        if head.pin_center is not None:
            scene.set_points(keys[3], prev.default_center[None], (0.6, 0.9, 0.6, 1.0), 7.0, on_top=True)
        else:
            scene.remove(keys[3])

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
