"""Lógica de la ventana: modos de selección de puntos, lista de planos y qué se dibuja.

No depende de GTK (la ventana es una capa fina sobre esto), así que se prueba sin pantalla. El
renderer (`SceneRenderer`) solo se toca en `sync`, que la ventana llama con el contexto GL activo.

Marco de coordenadas: lo que se ve y se elige es siempre ``session.work`` (la figura con el plano de
simetría en x = 0), que es donde viven los planos de los miembros (ver `session`). Antes de definir la
simetría, ``work`` coincide con ``figure``.
"""
from __future__ import annotations
from ..i18n import tr, lz, loc

import os
import re
from dataclasses import dataclass
from enum import Enum

import numpy as np
import trimesh

from .. import solids as S
from . import settings as GUI_SETTINGS
from ..head import HEAD_NAME, MIN_BASE_IOU, REPLACEMENT_UPS, neck_plane
from ..pipeline import body_base_z, body_ref
from ..waist import PELVIS_NAME, WAIST_NAME
from .. import cut_ref as CR
from ..limbs import cut_plane, silhouette_on_plane
from ..pin_ghost import MIN_SHAFT_D, PinProposal, ghost_meshes, propose_smaller_pin, shrunk_dims, widest_point
from ..orient import OrientResult, overhang_mask, pose_orientation
from ..planes import Plane
from ..trims import Trim
from .camera import OrbitCamera
from .picking import Picker, PickResult
from .polygon_editor import EDGE_PX, VERTEX_PX, PolygonDraft, ray_plane_uv
from ..progress import TaskCancelled
from .history import History
from ..fbxio import FbxError
from .bambu_choice import BambuChoice
from .session import ColorSourceError, Session, color_rows, json_sig
from ..messages import fill_hip_message   # noqa: F401  (se mantiene importable desde aquí; vive fuera de gui/ para que la CLI y el motor no dependan de la GUI)

SYMMETRY_ID = "simetria"
_KIND_BASE = {"arm": "brazo", "leg": "pierna"}


HOVER_RADIUS_PX = 12.0       # distancia en pantalla a la que un vértice se marca en rojo al pasar el puntero


def _pin_aid_colors() -> dict:
    from ..pin_aid import LEVEL_COLORS

    return LEVEL_COLORS


class Mode(Enum):
    VIEW = "ver"
    CROWN = "coronilla"
    SYMMETRY = "simetria"
    LIMB_PLANE = "plano_miembro"
    NECK_PLANE = "plano_cuello"
    WEAPON_CUT_PLANE = "plano_corte_arma"   # 3 clics sobre el arma importada: plano que le corta el mango original
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
    WAIST_PIN = "pasador_cintura"   # vista de costado: un clic (o flechas) pone el pasador de la cintura adelante / atrás sobre la cara
    FLOOR_CUT = "corte_paralelo_piso"   # plano paralelo al piso (cabeza o cintura): un clic sobre la figura (o flechas) fija su altura
    FLAT_PLANE = "plano_sin_soportes"   # figura generada sentada, vista Derecha: un clic (o flechas) fija la altura del corte plano

DRAFT_PX = 6.0                               # grosor en pantalla (px) del borrador de silueta (el que se propone o se está dibujando)
DRAFT_COLOR = (1.0, 0.0, 0.85, 1.0)          # magenta fosforescente: contrasta con la figura gris y con los amarillos/celestes de las siluetas ya guardadas
DRAFT_SELECTED_COLOR = (0.1, 1.0, 0.2, 1.0)  # vértice elegido: verde flúor (el rojo se confundía con el magenta)
GUIDE_PX = 4.0                               # grosor (px) de la guía: el contorno de la figura sobre el plano, de donde sale la propuesta
GUIDE_COLOR = (1.0, 0.45, 0.0, 1.0)          # naranja flúor: se ve sobre la figura gris y no se confunde con el magenta del borrador
SILHOUETTE_PX = 3.0                          # grosor en pantalla (px) del trazo de las siluetas: 1 px no se distingue sobre la figura
EXPORT_ONLY_PARAMS = frozenset({"flat_depth"})   # parámetros que no cambian las piezas generadas


GUIDE_ARM_COLOR = (0.2, 0.9, 1.0, 1.0)         # cian: línea del brazo (o del muslo)
GUIDE_FOREARM_COLOR = (1.0, 0.35, 0.9, 1.0)    # magenta: línea del antebrazo (o de la pantorrilla)
TRIM_SHAPES = ("polygon", "sphere", "box", "cylinder")
TRIM_SHAPE_LABELS = {"polygon": lz("controller.trim_polygon"), "sphere": lz("controller.trim_sphere"), "box": lz("controller.trim_box"), "cylinder": lz("controller.trim_cylinder")}
TRIM_OP_LABELS = {"subtract": lz("controller.trim_subtract"), "union": lz("controller.trim_union")}
TRIM_SIZE_LABELS = {"sphere": lz("common.radio_de_la_esfera_mm"), "box": lz("controller.arista_de_la_caja_mm"), "cylinder": lz("controller.radio_del_cilindro_mm")}


def trim_size_label(shape: str) -> str | None:
    """Etiqueta del tamaño de un recorte según su forma; None si no aplica (el polígono se dibuja, no tiene radio)."""
    return TRIM_SIZE_LABELS.get(shape)
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
_PART_PREVIEW_COLORS = {"ok": (0.30, 0.78, 0.45, 1.0), "aviso": (0.95, 0.75, 0.20, 1.0),
                        "no": (0.90, 0.30, 0.30, 1.0)}   # pieza de catálogo todavía sin generar, por nivel de acople
_PLANE_STATUS_COLORS = {"no": (0.95, 0.12, 0.12, 0.45), "aviso": (1.0, 0.85, 0.1, 0.38),
                        "ok": (0.2, 0.9, 0.35, 0.30)}       # plano que falló / girado respecto de la pieza elegida / coincide
_CUT_GHOST_MODEL_ALPHA = 0.65                           # con el fantasma, el STL original se pinta un poco translúcido para no taparlo
_CUT_GHOST_COLOR = (0.15, 0.85, 1.0, 0.30)              # fantasma de la referencia: el mismo celeste que su guía, translúcido
_CUT_REF_COLOR = (0.15, 0.85, 1.0, 0.18)                # guía: dónde corta la figura de referencia (celeste: no se confunde con el blanco del corte)
_CUT_PLANE_COLOR = (1.0, 1.0, 1.0, 0.16)               # plano donde SE CORTA el miembro (el de la silueta, corrido «Corte a» mm): blanco, para no confundirlo
_REPLACEMENT_HEAD_COLOR = (0.95, 0.62, 0.25, 1.0)      # cabeza de reemplazo en la vista 3D (naranja)
_EXTRACTED_HAND_COLOR = (0.25, 0.85, 0.55, 1.0)         # mano extraída (aún sin guardar) en la vista 3D (verde)
_REPLACEMENT_HAND_COLOR = (0.25, 0.75, 0.85, 1.0)       # mano estándar pegada en el modelo (turquesa: no se confunde con la extraída)
_HAND_COLOR = (0.94, 0.74, 0.60, 1.0)                   # la mano estándar sobre el mango del arma (color piel)
_ACCESSORY_COLOR = (0.82, 0.82, 0.88, 1.0)             # espada de prueba puesta en el puño de la mano pegada (gris claro)
SHOW_GROUPS = ("planes", "pins", "contours")     # lo que se puede mostrar / ocultar de una vez desde la lista de planos
FLAT_NUDGE_MM = 0.1            # paso de las flechas al ajustar el plano de corte «sin soportes» (Mayús = 10 veces más)
NUDGE_MM = 0.1                 # paso de las flechas al ajustar el pasador (Mayús = 10 veces más)
FLOOR_CUT_NUDGE_MM = 0.1        # paso de las flechas al trasladar el plano paralelo al piso (Mayús = 10 veces más)
FLOOR_CUT_PLACES = ("head", "waist")


def circle_world(plane: Plane, center, radius: float, n: int = 48) -> np.ndarray:
    """Circunferencia de `radius` mm centrada en `center`, sobre `plane` (Nx3, cerrada por el que dibuja)."""
    a = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    c = np.asarray(center, float)
    return c + radius * (np.cos(a)[:, None] * plane.u + np.sin(a)[:, None] * plane.v)


def validation_summary(val: dict, clearance: float) -> str:
    """Texto corto del resultado de `pipeline.validate` para la barra de estado."""
    if val["problems"]:
        return (tr("controller.la_figura_se_genero_pero_tiene") + ' ') + "; ".join(val["problems"])
    parts = [tr("controller.figura_generada_y_valida_n_piezas", n=len(val['pieces']))]
    if val["gaps"]:
        parts.append(tr("controller.holgura_minima_values_mm_objetivo_clearanc", values=f"{min(val['gaps'].values()):.3f}", clearance=f"{clearance:.3f}"))
    if val["recorrido"]:
        parts.append(tr("controller.recorrido_libre_sin_interferencias"))
    if val.get("avisos_pasador"):
        parts.append("pasador: " + "; ".join(val["avisos_pasador"]))
    if val["avisos"]:
        parts.append(tr("controller.n_aviso_s_de_soldado_el", n=len(val['avisos'])))
    return "; ".join(parts) + "."


# Parámetros editables en la GUI: (clave con punto, etiqueta, mínimo, máximo, paso, decimales)
# Agarre para accesorios (`afm.props`): (clave de GripSpec, rótulo, mínimo, máximo, paso, decimales)
GRIP_FIELDS = (
    ("bore_d", lz("controller.del_taladro_en_el_puno_mm"), 1.5, 3.5, 0.1, 1),
    ("shaft_d", lz("controller.de_la_empunadura_mm"), 1.2, 3.0, 0.1, 1),
    ("slit_w", lz("controller.ranura_entre_lenguetas_mm"), 0.3, 1.5, 0.1, 1),
    ("finger_len", lz("common.largo_de_la_lengueta_mm"), 3.0, 14.0, 0.5, 1),
)


# Elección del eje de agarre (`props.compute_grip`): (atributo, rótulo, mínimo, máximo, paso, decimales)
GRIP_AXIS_FIELDS = (
    ("grip_channel", lz("controller.canal_de_agarre"), 1, 4, 1, 0),
    ("grip_tilt", lz("controller.inclinacion_fina_del_eje"), -15.0, 15.0, 1.0, 0),
    ("grip_azimuth", lz("controller.hacia_que_lado_se_inclina"), 0.0, 359.0, 15.0, 0),
)


PARAM_FIELDS = (
    ("clearance", lz("controller.holgura_miembrocavidad_mm"), 0.1, 1.5, 0.05, 2),
    ("burr", lz("controller.rebaba_a_rebajar_en_la_cara"), 0.0, 3.0, 0.05, 2),
    ("sit_angle", lz("controller.angulo_de_pose_sentada"), 0.0, 180.0, 5.0, 0),
    ("flat_depth", lz("controller.corte_con_la_cama_de_impresion"), 0.2, 6.0, 0.1, 1),
    ("torso_mirror", lz("controller.torso_espejado_1_si_0_conserva"), 0.0, 1.0, 1.0, 0),
    ("hip_margin", lz("controller.margen_del_limite_de_cadera"), 0.0, 30.0, 1.0, 0),
    ("waist_pin.shaft_d", lz("controller.cintura_vastago_mm"), 3.0, 14.0, 0.1, 1),
    ("waist_pin.shaft_len", lz("controller.cintura_largo_vastago_mm_pared_tras"), 1.5, 15.0, 0.1, 1),
    ("waist_pin.head_d", lz("controller.cintura_cabeza_mm"), 5.0, 20.0, 0.1, 1),
    ("waist_pin.head_h", lz("controller.cintura_alto_cabeza_mm"), 1.0, 8.0, 0.05, 2),
    ("waist_twist", lz("controller.cintura_giro_libre_a_cada_lado"), 5.0, 180.0, 5.0, 0),
    ("shoulder_pin.shaft_d", lz("controller.hombro_vastago_mm"), 2.0, 12.0, 0.1, 1),
    ("shoulder_pin.shaft_len", lz("controller.hombro_largo_vastago_mm"), 0.5, 15.0, 0.1, 1),
    ("shoulder_pin.head_d", lz("controller.hombro_cabeza_mm"), 3.0, 16.0, 0.1, 1),
    ("shoulder_pin.head_h", lz("controller.hombro_alto_cabeza_mm"), 0.5, 8.0, 0.05, 2),
    ("hand_pin.shaft_d", lz("controller.mano_articulada_vastago_mm_se_achica"), 1.5, 8.0, 0.1, 1),
    ("hand_pin.shaft_len", lz("controller.mano_articulada_largo_vastago_mm_labio"), 1.0, 10.0, 0.1, 1),
    ("hand_pin.head_d", lz("controller.mano_articulada_cabeza_mm"), 2.5, 12.0, 0.1, 1),
    ("hand_pin.head_h", lz("controller.mano_articulada_alto_cabeza_mm"), 0.5, 5.0, 0.05, 2),
    ("hip_pin.shaft_d", lz("controller.cadera_vastago_mm"), 2.0, 12.0, 0.1, 1),
    ("hip_pin.shaft_len", lz("controller.cadera_largo_vastago_mm"), 0.5, 15.0, 0.1, 1),
    ("hip_pin.head_d", lz("controller.cadera_cabeza_mm"), 3.0, 16.0, 0.1, 1),
    ("hip_pin.head_h", lz("controller.cadera_alto_cabeza_mm"), 0.5, 8.0, 0.05, 2),
    ("shoulder_detent", lz("controller.hombro_mecanismo_de_clic_1_si"), 0.0, 1.0, 1.0, 0),
    ("hip_detent", lz("controller.cadera_mecanismo_de_clic_1_si"), 0.0, 1.0, 1.0, 0),
    ("detent.n_notches", lz("controller.clic_cantidad_de_muescas"), 2.0, 8.0, 1.0, 0),
    ("detent.disc_d", lz("controller.clic_del_disco_mm"), 5.0, 14.0, 0.1, 1),
    ("detent.disc_h", lz("controller.clic_alto_del_disco_mm"), 1.0, 5.0, 0.1, 1),
    ("detent.notch_depth", lz("controller.clic_profundidad_de_la_muesca_mm"), 0.3, 2.0, 0.05, 2),
    ("detent.flank_deg", lz("controller.clic_angulo_del_flanco"), 10.0, 60.0, 1.0, 0),
    ("detent.tip_r", lz("controller.clic_radio_de_la_punta_mm"), 0.3, 2.0, 0.05, 2),
    ("detent.detent_gap", lz("controller.clic_holgura_punta_muesca_mm"), 0.1, 0.8, 0.05, 2),
    ("detent.slit_w", lz("controller.clic_ranura_de_la_lengueta_mm"), 0.2, 1.5, 0.05, 2),
    ("detent.flex_gap", lz("controller.clic_ranura_superior_flexion_ranura_mm"), 0.3, 3.0, 0.05, 2),
    ("detent.finger_t", lz("controller.clic_espesor_de_la_lengueta_mm"), 0.6, 3.0, 0.1, 1),
    ("detent.finger_len", lz("controller.clic_largo_de_la_lengueta_mm"), 4.0, 20.0, 0.5, 1),
    ("detent.finger_w", lz("controller.clic_ancho_de_la_lengueta_mm"), 2.0, 10.0, 0.5, 1),
    ("detent.print_angle", lz("controller.clic_angulo_del_brazo_al_imprimir"), 0.0, 90.0, 1.0, 0),
    ("hinge_pin.pin_d", lz("controller.rodilla_codo_bulon_mm"), 2.0, 6.0, 0.1, 1),
    ("hinge_pin.pin_len", lz("controller.rodilla_codo_largo_bulon_mm"), 0.5, 4.0, 0.1, 1),
    ("hinge_pin.tongue_t", lz("controller.rodilla_codo_espesor_lengueta_mm"), 1.0, 6.0, 0.1, 1),
    ("hinge_pin.end_r", lz("controller.rodilla_codo_radio_lengueta_mm"), 2.0, 8.0, 0.1, 1),
    ("hinge_pin.neck", lz("controller.rodilla_codo_lengueta_con_cuello_recto"), 0.0, 1.0, 1.0, 0),
    ("hinge_pin.rod", lz("controller.rodilla_codo_bastago_pasante_1_si"), 0.0, 1.0, 1.0, 0),
    ("hinge_pin.rod_wall", lz("controller.rodilla_codo_pared_tras_el_bastago"), 0.0, 4.0, 0.1, 1),
)


# Rango (°) del selector «Flexión mostrada» de la barra del visor; la simulación al costado se coloca midiendo todo este barrido.
JOINT_PREVIEW_FLEX_MIN, JOINT_PREVIEW_FLEX_MAX = -45.0, 150.0


class Controller:
    def __init__(self, session: Session | None = None):
        self.session = session or Session()
        self.camera = OrbitCamera()
        self.picker: Picker | None = None
        self.mode = Mode.VIEW
        self.pending: list[np.ndarray] = []
        self.hover_vertex: np.ndarray | None = None  # vértice bajo el puntero (se pinta de rojo; un clic lo elige)
        self.pending_kind: str | None = None        # "arm" | "leg" mientras se elige el plano de un miembro
        self.message = tr("controller.abri_un_stl_para_empezar")
        self.one_side = False                        # trabajar un solo lado (oculta x < 0)
        self.show_texture = False                    # el visor dibuja la figura con la textura del FBX (vista previa; no cambia la geometría)
        self.hidden: set[str] = set()                # ids de planos ocultos
        self.show_groups: dict[str, bool] = {g: False for g in SHOW_GROUPS}   # interruptores generales (planos / pines / contornos)
        self.busy = False                            # generando la figura (la ventana bloquea la edición)
        self.generated_once = False                  # ya se generó una figura en esta sesión (para avisar cuando deja de valer)
        self._saved_text: str | None = None          # estado del proyecto al abrir / guardar por última vez (para el «*» de cambios sin guardar)
        self.history = History()                     # deshacer / rehacer global por instantáneas del proyecto (ver history.py)
        self.defer_history = False                   # la ventana lo activa y registra con `record_history` tras una pausa; sin ventana se registra en cada cambio
        self.plane_faults: dict[str, tuple[str, bytes]] = {}   # id de plano -> (por qué su corte falló, firma del plano): se pinta de rojo hasta que el plano cambie
        self.progress_stages: list | None = None      # pasos esperados de la tarea en curso (`progress.Stage`); None = sin plan
        self.progress_prefix = tr("controller.generando")           # encabezado de los mensajes de progreso de la tarea en curso
        self.show_overhangs = False                  # pintar de rojo los voladizos de impresión
        self.overhang_angle = 45.0                   # grados desde la vertical (como `--max-angle`)
        self.draft: PolygonDraft | None = None      # polígono de silueta en edición (modo POLYGON)
        self.edit_limb: str | None = None
        self._vertex_drag: dict | None = None       # arrastre de un vértice de la silueta en curso: índice, posición previa y si ya se movió
        self._last_offset_limb: str | None = None   # miembro al que apunta «Corte a» cuando ninguno está en edición
        self.edit_cross = False                     # el polígono en edición es la 2.ª silueta (frente) del miembro
        self.edit_cross2 = False                    # el polígono en edición es la 3.ª silueta (desde arriba) del miembro
        self.joint_limb: str | None = None          # miembro al que se le está definiendo la articulación (JOINT_PLANE)
        self.head_pin_prev = None                   # HeadPinPreview mientras se coloca el pasador del cuello (HEAD_PIN)
        self.waist_pin_prev = None                  # LinePinPreview mientras se coloca el pasador de la cintura (WAIST_PIN)
        self.joint_prev = None                      # LinePinPreview que lee la mira en HEAD_PIN / WAIST_PIN (con `pin_aid` = LinePinAid)
        self.joint_focus: str | None = None         # «cuello» | «cintura»: cuyo plano muestra la cinta de color (sección abierta de la ventana)
        self.joint_name: str | None = None          # articulación cuyo eje se está colocando (JOINT_AXIS)
        self.joint_guides: dict[str, tuple] = {}    # articulación -> (línea del brazo 2×3, línea del antebrazo 2×3) dibujadas
        self.pin_limb: str | None = None            # miembro cuyo pasador se está colocando (modo PIN)
        self.pin_prev = None                         # PinPreview de ese miembro (cara de contacto)
        self.pin_aid = None                          # PinAid (mira asistida) del modo PIN; None sin catálogo abierto
        self.heat_visible = True                     # mapa de calor de la mira (se puede ocultar)
        self.cut_aim_visible = True                  # mira del corte: cinta de color y fantasma de la sección (se puede apagar)
        self.cut_ref_name: str | None = None         # figura del catálogo que se toma de referencia para el corte (None = la propuesta)
        self.cut_ghost_visible = False               # fantasma: el torso / pelvis y el miembro cortados de la referencia, translúcidos sobre la figura
        self.cut_ref_visible = True                  # guía de la referencia: línea / plano a la distancia que corta esa figura
        self.cut_seated: dict | None = None          # último choque en pose sentada medido (`cut_aid.seated_summary` + su clave)
        self.pin_aim = None                          # última lectura de la mira bajo el puntero (AimReading)
        self.pin_hover = None                        # (u, v) de la cara bajo el puntero en el modo PIN: ahí se dibuja el pasador fantasma
        self.pin_proposal: PinProposal | None = None   # pasador más chico que entraría, a la espera del OK del usuario (no cambia nada solo)
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
        from ..props import DEFAULT_BARBS, GripSpec
        self.grip_spec = GripSpec()                  # agarre para accesorios: valores a usar (se editan en la sección «Agarre»)
        self.grip_barbs: tuple[float, ...] = DEFAULT_BARBS
        self.grip_preview_on = False                 # previsualizar el agarre elegido en la mano pegada, sin taladrar ni guardar
        self._grip_preview: dict | None = None       # última previsualización: key, mano taladrada (local), ajuste, nº de canales, error
        self.grip_preview_catalog: tuple | None = None   # (raíz, ref) de la mano del catálogo a previsualizar cuando no hay mano pegada
        self._grip_cat_mesh: tuple | None = None         # (raíz, ref, malla) de esa mano sin taladrar, para mostrarla mientras se calcula
        self._grip_channels: tuple | None = None     # (clave de la mano, canales candidatos): se buscan una vez por mano
        self.grip_channel = 1                        # canal candidato de la mano (1 = el más robusto, el de siempre)
        self.grip_tilt = 0.0                         # inclinación fina del eje (°, ±15) y hacia qué lado (°)
        self.grip_azimuth = 0.0
        self.grip = None                             # props.GripResult de la última mano taladrada (None = ninguna)
        self.grip_show = False                       # dibujar una espada de prueba en la mano pegada de la figura
        self.weapon = None                           # weapon_session.WeaponSession del arma que se está importando (None = ninguna)
        self.weapon_active = False                   # el visor muestra el arma en lugar de la figura
        self.weapon_show_hand = True                 # ... con la mano estándar de la línea cerrada sobre el mango
        self.weapon_catalog = ""                     # carpeta del catálogo de la línea para armas y accesorios del proyecto
        self.color_report: dict | None = None        # `files["color"]` del último export con color (None = sin color o sin exportar)
        self.bambu = BambuChoice(lambda: self.session.colorizer, self._bambu_role_names)   # salida para Bambu Studio (casilla, tope, selector); sin catálogo
        self.show_bambu = False                      # el visor dibuja los colores planos que saldrán en el 3MF (vista previa; no cambia la geometría)
        self._bambu_view: tuple | None = None        # (Colorizer, [(nombre, malla, rgba)]) de esa vista; solo vale mientras el Colorizer sea el de la sesión
        self._bambu_token = 0                        # un cálculo de la vista más nuevo invalida al anterior (hilo de la ventana)
        self.bambu_report: dict | None = None        # informe de Bambu del último export / «Exportar 3MF ahora» (None = no se escribió)
        self._grip_fit_cache: tuple = (None, None)   # ((ruta, mtime), GripFit | None) de la mano pegada
        self._grip_prop_cache: tuple = (None, None)  # (clave, malla en el mundo)
        self._grip_prop_error = ""
        self.show_cut_numbers = True                 # rotular cada corte con su número en el visor
        self.show_part_preview = True                # dibujar las piezas de catálogo elegidas antes de generar
        self.show_joint_preview = False              # vista previa de la articulación (rodilla / codo) en lugar de la figura
        self.call_in_main = None                     # callable(f, *args): la ventana lo pone para que los cálculos corran en un hilo
        self._jp: dict = {"name": None, "key": None, "pv": None, "flex": 0.0, "busy": False, "error": "",
                          "cache": {}, "drawn": None}      # estado de la vista previa de la articulación
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
        if not self.defer_history:                  # también con scene=False: set_param y otros cambian el proyecto sin repintar
            self.record_history()
        if self.on_change:
            self.on_change()

    # ------------------------------------------------------ deshacer / rehacer
    def _project_text(self) -> str:
        with self.session.committed():             # el estado real aunque se esté editando el lado derecho (vista espejada)
            return json_sig(self.session.project.to_dict())

    @property
    def dirty(self) -> bool:
        """Hay cambios en el proyecto desde que se abrió o guardó. Usa el último estado registrado por el historial
        (no vuelve a serializar), así que sigue lo hecho con la misma pausa que el deshacer."""
        cur = self.history.current
        return self.loaded and cur is not None and self._saved_text is not None and cur.text != self._saved_text

    def record_history(self) -> None:
        """Registra el estado del proyecto como un paso de deshacer si cambió desde el último registrado."""
        if not self.loaded or self.busy:
            return
        self.history.observe(self._project_text(), (self.message or "").strip()[:90])

    @property
    def can_undo(self) -> bool:
        return self.loaded and not self.busy and self.history.can_undo

    @property
    def can_redo(self) -> bool:
        return self.loaded and not self.busy and self.history.can_redo

    def undo_blocked(self) -> str | None:
        """Por qué no se puede deshacer / rehacer ahora (None = se puede)."""
        if not self.loaded:
            return tr("controller.primero_abri_un_stl_pestana_figura")
        if self.busy:
            return self._WAIT
        if self.session.catalog_origin is not None and not self.session.stl_path:
            return tr("controller.en_una_figura_armada_solo_con")
        return None

    def undo(self) -> bool:
        """Ctrl+Z. Mientras se dibuja, deshace el último punto; si no, el último cambio del proyecto."""
        if (self.mode == Mode.POLYGON and self.draft is not None) or self.pending:
            self.undo_point()
            return True
        return self._step("undo")

    def redo(self) -> bool:
        return self._step("redo")

    def _step(self, which: str) -> bool:
        why = self.undo_blocked()
        if why is not None:
            self._changed(why, scene=False)
            return False
        snap = getattr(self.history, which)(self._project_text())
        if snap is None:
            self._changed((tr("controller.no_hay_nada_para") + ' ') + ("deshacer." if which == "undo" else "rehacer."), scene=False)
            return False
        label = snap.label
        self._restore_project(snap.text)
        self.history.settle(self._project_text())
        verb = tr("controller.deshecho") if which == "undo" else tr("controller.rehecho")
        # al deshacer se vuelve al estado anterior: el cartel nombra lo que se quitó; al rehacer, lo que se volvió a poner
        shown = self.history.redo_label if which == "undo" else label
        self._changed(f"{verb}: {shown}" if shown else f"{verb}.")
        return True

    def _restore_project(self, text: str) -> None:
        import dataclasses
        import json

        from ..project import Project

        sess = self.session
        sess._leave_right()                                  # el texto es el estado real: se vuelve al lado izquierdo antes de aplicarlo
        cur = sess.project
        new = Project.from_dict(json.loads(text))
        reload = sess.stl_path is not None and (new.crown_z != cur.crown_z
                                                or abs(new.params.ref_height - cur.params.ref_height) > 1e-9)
        self.cancel(quiet=True)
        for f in dataclasses.fields(Project):               # en el mismo objeto: nadie queda con una referencia vieja
            setattr(cur, f.name, getattr(new, f.name))
        for name in [k for k in vars(sess) if k.endswith("_cache")]:
            if name == "_catalog_joint_cache":               # se declara en __init__ y se lee directo
                sess._catalog_joint_cache = None
            else:                                           # el resto se crea al usarse: borrarlo es lo seguro
                del sess.__dict__[name]
        sess.outcome = None
        if reload:                                           # cambió la escala de la malla: se relee con la altura y la coronilla del paso
            sess.load(sess.stl_path, cur.crown_z)
        else:
            sess._rebuild_frames()
        self._rebuild_picker()
        self.cut_seated = None

    def _rebuild_picker(self) -> None:
        self.picker = Picker(self.session.work) if self.session.work is not None else None

    def _after_load(self) -> None:
        self.generated_once = False
        self._last_offset_limb = None               # el miembro de la figura anterior no vale para esta
        self.history.reset(self._project_text())
        self._saved_text = self.history.current.text
        self.weapon_active = False                  # se abrió una figura: el visor vuelve a ella
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
        """Abre un STL sin hilos (CLI, pruebas y uso programático); la ventana usa `begin_open_stl`."""
        job = self.begin_open_stl(path, crown_z)
        try:
            session = job()
        except Exception:
            self.finish_open_stl(path, error=True)
            raise
        self.finish_open_stl(path, session)

    def begin_open_stl(self, path: str, crown_z: float | None = None):
        """Devuelve el trabajo pesado ``job(progress=None) -> Session`` (leer, reparar, normalizar).

        No toca el estado del controlador: la ventana lo corre en un hilo y después llama a `finish_open_stl`
        desde el hilo de la interfaz. Devuelve None si hay otra tarea en curso."""
        if self.busy:
            return None
        self.busy = True
        self.progress_prefix = tr("controller.abriendo")
        from ..progress import Stage

        self.progress_stages = [Stage(tr("controller.leyendo_el_stl"), 3), Stage(tr("controller.reparando_la_malla"), 4)]
        self._changed(tr("controller.abriendo_v", v=path.split('/')[-1]), scene=False)

        def job(progress=None) -> Session:
            session = Session()
            session.load(path, crown_z, progress=progress)
            return session
        return job

    def finish_open_stl(self, path: str, session: Session | None = None, error=None) -> None:
        self.busy = False
        self.progress_prefix = tr("controller.generando")
        if error or session is None:
            if isinstance(error, BaseException):
                self._changed(tr("controller.no_se_pudo_abrir_v_error", v=path.split('/')[-1], error=error), scene=False)
            return
        self.session = session
        self.joint_guides = {}
        self.hidden.clear()
        self.hand_extract = self._hand_mesh = None
        self._after_load()
        msg = tr("controller.cargado_v", v=path.split('/')[-1])
        msg += self._auto_catalog()
        if session.report.warnings:
            msg += " — " + " ".join(session.report.warnings)
        if session.fbx_summary:                      # modo A: resumen del FBX y avisos (sin textura base, varios materiales)
            msg += " — " + session.fbx_summary_text()
        self._changed(msg)

    def new_project(self) -> bool:
        """«Nuevo»: descarta la figura cargada y el proyecto (se pierden los cambios sin guardar) y vuelve a los valores de
        inicio: sin STL, parámetros por defecto, cámara de frente, historial vacío. Conserva lo que la ventana engancha
        (refresco, hilos, historial diferido). Devuelve False si hay una tarea en curso."""
        if self.busy:
            self._changed(tr("controller.hay_una_tarea_en_curso_espera"), scene=False)
            return False
        keep = {k: getattr(self, k) for k in ("on_change", "call_in_main", "defer_history")
                if hasattr(self, k)}
        self.__init__()
        for k, v in keep.items():
            setattr(self, k, v)
        self.scene_dirty = True
        self._changed(self.message)
        return True

    def open_project(self, path: str, stl_path: str | None = None, catalog_root: str | None = None) -> None:
        """Abre un `.proyecto`. El de una figura toda de catálogo («Armar y exportar») se abre sin STL: el cuerpo base sale
        del catálogo de su línea (`catalog_root`, el recordado para esa línea o donde se armó); si no está, levanta
        `session.CatalogNeeded` y la ventana pide la carpeta."""
        from ..catalog_project import peek

        org = None if stl_path else peek(path)
        if org is not None and catalog_root is None:
            catalog_root = GUI_SETTINGS.catalog_for_line(org.get("linea"))
        self.session = Session.open_project(path, stl_path, catalog_root)
        self.joint_guides = {}
        self.hidden.clear()
        self.hand_extract = self._hand_mesh = None
        if org is not None and self.session.catalog_root:
            GUI_SETTINGS.remember_catalog(self.session.catalog_name(), self.session.catalog_root)
        self._after_load()
        what = (' ' + tr("controller.figura_armada_con_piezas_de_catalogo")) if self.session.catalog_origin is not None else ""
        self._changed(tr("controller.proyecto_abierto_v_what", v=path.split('/')[-1], what=what) + self._auto_catalog())

    def save_project(self, path: str) -> None:
        self.record_history()                       # lo último hecho entra al historial antes de marcarlo como guardado
        self.session.save_project(path)
        self.session.project_path = os.path.abspath(path)      # los próximos «Guardar» sobrescriben este archivo
        if self.history.current is not None:
            self._saved_text = self.history.current.text
        self._changed(tr("controller.proyecto_guardado_v", v=path.split('/')[-1]), scene=False)

    # ---------------------------------------------------------------- modos
    def _busy_for(self, what: str) -> bool:
        """True (y avisa) si hay una tarea en un hilo (revisar, generar…): lo que ella lee no se puede cambiar a mitad."""
        if not self.busy:
            return False
        self._changed(tr("controller.hay_una_tarea_en_curso_revisando", what=what), scene=False)
        return True

    def _need_loaded(self) -> bool:
        if not self.loaded:
            self._changed(tr("controller.primero_abri_un_stl"), scene=False)
            return False
        return True

    def start_crown(self) -> None:
        if not self._need_loaded():
            return
        if self.session.project.symmetry is not None or self.session.project.limbs:
            self._changed(tr("controller.la_coronilla_se_marca_antes_de"), scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.CROWN
        self._changed(tr("controller.hace_clic_en_la_coronilla_el"))

    def start_symmetry(self) -> None:
        if not self._need_loaded():
            return
        self.cancel(quiet=True)
        self.mode = Mode.SYMMETRY
        self._changed(tr("controller.plano_de_simetria_elegi_3_puntos"))

    def start_limb_plane(self, kind: str) -> None:
        if not self._need_loaded():
            return
        if kind not in _KIND_BASE:
            raise ValueError(kind)
        if self.session.project.symmetry is None:
            self._changed(tr("controller.primero_defini_el_plano_de_simetria_3"), scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.pending_kind = Mode.LIMB_PLANE, kind
        self._changed(tr("controller.plano_del_kind_base_elegi_3", kind_base=_KIND_BASE[kind]))

    def start_waist_plane(self) -> None:
        if not self._need_loaded():
            return
        if self.session.project.symmetry is None:
            self._changed(tr("controller.primero_defini_el_plano_de_simetria_3"), scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.WAIST_PLANE
        self._changed(tr("controller.plano_de_la_cintura_elegi_3"))

    def start_neck_plane(self) -> None:
        if not self._need_loaded():
            return
        if self.session.project.symmetry is None:
            self._changed(tr("controller.primero_defini_el_plano_de_simetria_3"), scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.NECK_PLANE
        self._changed(tr("controller.plano_del_cuello_elegi_3_puntos"))

    # ------------------------------------------------ articulaciones (rodilla / codo)
    def start_joint_plane(self, limb: str) -> None:
        """Plano de la rodilla (pierna) o del codo (brazo): 3 puntos sobre la figura a esa altura."""
        if not self._need_loaded():
            return
        spec = self.session.limb(limb)
        if spec is None:
            self._changed(tr("controller.no_hay_un_miembro_limb", limb=limb), scene=False)
            return
        if self.session.joint_of_limb(limb) is not None:
            self._changed(tr("controller.limb_ya_tiene_su_articulacion_quitala", limb=limb), scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.joint_limb = Mode.JOINT_PLANE, limb
        what = tr("controller.de_la_rodilla") if spec.kind == "leg" else tr("controller.del_codo")
        self._changed(tr("controller.plano_what_elegi_3_puntos_sobre", what=what))

    def start_joint_lines(self, limb: str) -> None:
        """Codo (brazo) o rodilla (pierna) siguiendo el miembro: 2 líneas sobre la vista frontal, una arriba y otra abajo
        de la articulación; el plano de separación sigue la bisectriz y el eje queda perpendicular a ella (`Session.plane_from_lines`)."""
        if not self._need_loaded():
            return
        spec = self.session.limb(limb)
        if spec is None:
            self._changed(tr("controller.no_hay_un_miembro_limb", limb=limb), scene=False)
            return
        if self.session.joint_of_limb(limb) is not None:
            self._changed(tr("controller.limb_ya_tiene_su_articulacion_quitala", limb=limb), scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.joint_limb = Mode.JOINT_LINES, limb
        up, down = self._line_names(spec.kind)
        joint = tr("controller.la_rodilla") if spec.kind == "leg" else tr("controller.el_codo")
        self._changed(tr("controller.capitalize_con_2_lineas_conviene_la", capitalize=joint.capitalize(), up=up, up2=up, joint=joint, down=down))

    def start_joint_lines_for_kind(self, kind: str) -> None:
        """Como `start_joint_plane_for_kind` pero definiendo el corte con dos líneas."""
        if kind not in _KIND_BASE:
            raise ValueError(kind)
        limb = next((l.name for l in self.session.project.limbs
                     if l.kind == kind and self.session.joint_of_limb(l.name) is None), None)
        if limb is None:
            self._changed(tr("controller.no_hay_un_kind_base_sin", kind_base=_KIND_BASE[kind]), scene=False)
            return
        self.start_joint_lines(limb)

    @staticmethod
    def _line_names(kind: str) -> tuple[str, str]:
        return ("muslo", "pantorrilla") if kind == "leg" else ("brazo", "antebrazo")

    def _joint_lines_hint(self) -> str:
        spec = self.session.limb(self.joint_limb)
        up, down = self._line_names(spec.kind if spec else "arm")
        joint = tr("controller.la_rodilla") if spec is not None and spec.kind == "leg" else tr("controller.el_codo")
        n = len(self.pending)
        if n == 1:
            text = (tr("controller.linea_del_up_cian_punto_1", up=up, joint=joint))
        elif n == 2:
            ang = Session.line_opening_deg(self.pending[0], self.pending[1])
            if ang is None:
                text = (tr("controller.la_linea_del_up_no_sirve", up=up, joint=joint))
            else:
                text = (tr("controller.linea_del_up_lista_cian_se", up=up, ang=f"{ang:.1f}", down=down, joint=joint))
        else:
            text = (tr("controller.linea_del_down_magenta_punto_1", down=down, down2=down))
        return text + (' ' + tr("controller.backspace_deshace_el_ultimo_esc_cancela"))

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
            self._changed(tr("controller.no_sirve_e_dibuja_las_2", e=e))
            return
        self.mode, self.joint_limb = Mode.VIEW, None
        self.joint_guides[j.name] = (np.array(pts[:2], float), np.array(pts[2:], float))
        up, down = self._line_names(self.session.limb(limb).kind)
        self._changed(tr("controller.capitalize_definida_siguiendo_el_miembro_e", capitalize=j.name.capitalize(), up=up, a_up=f"{a_up:.1f}", down=down, a_down=f"{a_down:.1f}", joint_tilt=f"{self.joint_tilt(j.name):.1f}", joint_range_text=self.joint_range_text(j.name)))

    def start_joint_side(self, name: str) -> None:
        """Corrige las 4 guías de una articulación hecha con 2 líneas desde la vista de costado (conviene «Costado»):
        los mismos 4 puntos en el mismo orden (arriba del brazo, codo, codo, abajo del antebrazo). De cada clic se toman
        y y z; la x se conserva de la guía de frente. Con eso se recalculan el plano de separación y el eje."""
        if not self._need_loaded():
            return
        if self.session.joint(name) is None:
            self._changed(tr("controller.no_hay_una_articulacion_name", name=name), scene=False)
            return
        if name not in self.joint_guides:
            self._changed(tr("controller.name_no_se_hizo_con_2", name=name),
                          scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.joint_name = Mode.JOINT_SIDE, name
        up, down = self._line_names(self.session.limb(self.session.joint(name).limb).kind)
        self._changed(tr("controller.guias_de_name_de_costado_vista", name=name, up=up, down=down))

    def _finish_joint_side(self) -> None:
        name, pts = self.joint_name, self.pending
        self.pending = []
        up_old, low_old = self.joint_guides[name]
        old = np.vstack([up_old, low_old])
        new = np.array([[old[i][0], p[1], p[2]] for i, p in enumerate(pts)], float)
        try:
            self.session.retarget_joint_lines(name, (new[0], new[1]), (new[2], new[3]))
        except (ValueError, KeyError) as e:
            self._changed(tr("controller.no_sirve_e_hace_los_4", e=e))
            self.mode = Mode.JOINT_SIDE
            return
        self.joint_guides[name] = (new[:2].copy(), new[2:].copy())
        self.mode, self.joint_name = Mode.VIEW, None
        self._changed(tr("controller.guias_de_name_corregidas_de_costado", name=name, joint_tilt=f"{self.joint_tilt(name):.1f}"))

    def start_joint_plane_for_kind(self, kind: str) -> None:
        """Rodilla («leg») o codo («arm»): toma el primer miembro de ese tipo que todavía no tiene articulación."""
        if kind not in _KIND_BASE:
            raise ValueError(kind)
        limb = next((l.name for l in self.session.project.limbs
                     if l.kind == kind and self.session.joint_of_limb(l.name) is None), None)
        if limb is None:
            self._changed(tr("controller.no_hay_un_kind_base_sin", kind_base=_KIND_BASE[kind]), scene=False)
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
            self._changed(tr("controller.no_sirve_e_elegi_3_puntos", e=e))
            return
        self.joint_guides.pop(j.name, None)                  # sin líneas guía: se definió con 3 puntos
        self.mode, self.joint_limb = Mode.VIEW, None
        self._changed(tr("controller.capitalize_definida_con_rango_joint_range", capitalize=j.name.capitalize(), joint_range_text=self.joint_range_text(j.name), joint_tilt=f"{self.joint_tilt(j.name):.1f}"))

    def start_joint_axis(self, name: str) -> None:
        if not self._need_loaded():
            return
        if self.session.joint(name) is None:
            self._changed(tr("controller.no_hay_una_articulacion_name", name=name), scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.joint_name = Mode.JOINT_AXIS, name
        self._changed(tr("controller.eje_de_la_name_hace_clic", name=name))

    def _finish_joint_axis(self, hit: PickResult) -> None:
        name = self.joint_name
        try:
            self.session.set_joint_axis(name, hit.point)
        except ValueError as e:
            self._changed(tr("controller.no_sirve_e", e=e))
            return
        if self.show_joint_preview and self._jp["name"] == name:
            # con la simulación al costado se sigue ajustando: cada clic mueve el eje y la simulación se recalcula
            self._changed(tr("controller.eje_de_la_name_movido_la", name=name))
            return
        self.mode, self.joint_name = Mode.VIEW, None
        self._changed(tr("controller.eje_de_la_name_movido", name=name))

    def set_joint_tilt(self, name: str, tilt_deg: float) -> bool:
        """Inclina el eje de la bisagra vista de frente (° alrededor de Y; 0 = a lo largo de X; positivo = z sube con x).
        La figura de referencia usa ≈ 14° en la rodilla y ≈ 9,5° en el codo."""
        if not -45.0 <= float(tilt_deg) <= 45.0:
            self._changed(tr("controller.la_inclinacion_tiene_que_estar_entre"), scene=False)
            return False
        try:
            self.session.set_joint_axis(name, None, tilt_deg)
        except (ValueError, KeyError) as e:
            self._changed(tr("controller.no_sirve_e", e=e), scene=False)
            return False
        self._changed(tr("controller.eje_de_la_name_inclinado_tilt", name=name, tilt_deg=f"{float(tilt_deg):g}"))
        return True

    def set_joint_range(self, name: str, lo: float | None, hi: float | None) -> bool:
        try:
            self.session.set_joint_range(name, lo, hi)
        except (ValueError, KeyError) as e:
            self._changed(tr("controller.no_sirve_e", e=e), scene=False)
            return False
        self._changed(tr("controller.rango_de_la_name_joint_range", name=name, joint_range_text=self.joint_range_text(name)))
        return True

    # ------------------------------------- vista previa de la articulación (sin generar la figura)
    def joint_height(self, name: str) -> float:
        return self.session.joint_height(name)

    def set_joint_height(self, name: str, h: float) -> bool:
        """Sube o baja el eje de la articulación (altura sobre el plano, en mm)."""
        try:
            self.session.set_joint_height(name, h)
        except (ValueError, KeyError) as e:
            self._changed(tr("controller.no_sirve_e", e=e), scene=False)
            return False
        self._changed(tr("controller.eje_de_la_name_a_joint", name=name, joint_height=f"{self.session.joint_height(name):.2f}"))
        return True

    def joint_plane_side_tilt(self, name: str) -> float:
        return Session.joint_plane_side_tilt_deg(self.session.joint_any(name).plane.normal)

    def joint_editable(self, name: str) -> bool:
        """False para la articulación que trae una pieza de catálogo: se ve en la vista previa pero no se ajusta acá."""
        return not self.session.is_catalog_joint(name) and not self.session.is_pivot_preview(name)

    def is_hip_preview(self, name: str | None) -> bool:
        """True si la entrada de la vista previa es la cadera de una pierna o el hombro de un brazo (no se ajusta acá: se
        ve girar el miembro alrededor del pasador)."""
        return name is not None and self.session.is_pivot_preview(name)

    def no_joint_text(self) -> str:
        """Qué decir cuando no hay articulación para previsualizar."""
        s = self.session
        if s.catalog_only():
            err = s.catalog_joint_error()
            if err:
                return tr("controller.no_se_puede_previsualizar_err", err=err)
            return tr("controller.las_piezas_de_catalogo_elegidas_no")
        return tr("controller.no_hay_ninguna_articulacion_para_previsual")

    def set_joint_plane_side_tilt(self, name: str, tilt_deg: float) -> bool:
        """Inclina (o endereza, con 0) de costado el plano de la articulación; el eje no se mueve."""
        try:
            self.session.set_joint_plane_side_tilt(name, tilt_deg)
        except (ValueError, KeyError) as e:
            self._changed(tr("controller.no_sirve_e", e=e), scene=False)
            return False
        self._changed(tr("controller.plano_de_la_name_joint_plane", name=name, joint_plane_side_tilt=f"{self.joint_plane_side_tilt(name):.1f}"))
        return True

    def set_joint_preview(self, name: str | None, on: bool) -> bool:
        """Enciende o apaga la vista previa de una articulación (por omisión, la primera del proyecto)."""
        if not on:
            self.show_joint_preview = False
            self._jp["framed_beside"] = None          # al volver a encenderla al costado se encuadra de nuevo
            self._changed(tr("controller.vista_previa_de_la_articulacion_apagada"))
            return True
        listed = self.session.preview_names()
        if name is None and listed:
            name = listed[0]
        if not self.session.preview_exists(name):
            self.show_joint_preview = False
            self._changed(self.no_joint_text())
            return False
        jp = self._jp
        if jp["name"] != name:
            jp.update(name=name, key=None, pv=None, error="", cache={}, drawn=None)
            if self.is_hip_preview(name):
                jp["flex"] = 0.0                      # la cadera arranca de pie; el giro lo elige quien mira
        jp["framed"] = None                          # al encender, la cámara se vuelve a encuadrar sobre la articulación
        self.show_joint_preview = True
        if self.session.is_shoulder_preview(name):
            self._changed(tr("controller.vista_previa_del_hombro", name=name))
        elif self.is_hip_preview(name):
            hi = self.session.project.params.sit_angle
            self._changed(tr("controller.vista_previa_de_la_name_el", name=name, hi=f"{hi:g}"))
        elif self.joint_editable(name):
            self._changed(tr("controller.vista_previa_de_la_name_move", name=name))
        else:
            self._changed(tr("controller.vista_previa_de_la_name_de", name=name))
        return True

    JOINT_TOOL_MODES = (Mode.JOINT_PLANE, Mode.JOINT_LINES, Mode.JOINT_SIDE, Mode.JOINT_AXIS)

    def joint_preview_beside(self) -> bool:
        """True si la simulación de la articulación se dibuja AL COSTADO de la figura (que sigue a la vista para elegir
        puntos sobre ella): vista previa encendida y alguna herramienta de articulación activa. Fuera de ellas la
        simulación reemplaza a la figura, como siempre."""
        if not self.show_joint_preview:
            return False
        tool = self.tool_pivot_name()
        return self.mode in self.JOINT_TOOL_MODES or (tool is not None and tool == self._jp["name"])

    def polygon_hip_name(self) -> str | None:
        """Entrada de la vista previa del pivote (la cadera de una pierna o el hombro de un brazo) del miembro cuya silueta se
        está dibujando, o None si no aplica: con torso de catálogo no hay pivote del modelo que mostrar. Sirve para ver cómo
        queda el hueco y el pasador mientras se ajusta la silueta (principal o 2.ª) del miembro."""
        if self.mode != Mode.POLYGON or self.edit_limb is None or self.trim_cfg is not None:
            return None
        return self._pivot_name_of(self.edit_limb)

    def pin_pivot_name(self) -> str | None:
        """Entrada de la vista previa del pivote del miembro cuyo pasador se está colocando (hombro o cadera), o None."""
        if self.mode != Mode.PIN or self.pin_limb is None:
            return None
        return self._pivot_name_of(self.pin_limb)

    def tool_pivot_name(self) -> str | None:
        """Pivote que acompaña a la herramienta activa (silueta o pasador de un miembro), o None."""
        return self.polygon_hip_name() or self.pin_pivot_name()

    def _pivot_name_of(self, limb_name: str) -> str | None:
        spec = self.session.limb(limb_name)
        if spec is None:
            return None
        from ..joint_preview import pivot_preview_name
        name = pivot_preview_name(spec)
        return name if self.session.is_pivot_preview(name) else None

    def preview_here_available(self) -> bool:
        """¿Hay una simulación que mostrar con la herramienta activa? (la barra del visor decide si ofrece el botón)"""
        if self.mode == Mode.POLYGON:
            return self.polygon_hip_name() is not None
        if self.mode == Mode.PIN:
            return self.pin_pivot_name() is not None
        return True

    def toggle_joint_preview_here(self) -> None:
        """Botón de la barra del visor: enciende o apaga la simulación de la articulación que se está ajustando
        (`joint_name`; en la silueta de una pierna, su cadera), sin ir a la pestaña Parámetros."""
        name = self.joint_name or self.tool_pivot_name()
        if name is None or not self.session.preview_exists(name):
            self._changed(self.no_joint_text(), scene=False)
            return
        self.set_joint_preview(name, not self.show_joint_preview)

    def _beside_axis(self) -> tuple[int, float]:
        """(eje, signo) del corrimiento de la simulación: +X como siempre, salvo que la cámara mire casi a lo largo de X
        (vista de costado, la que se usa para dibujar la silueta de una pierna): ahí +X iría hacia el ojo y la simulación
        taparía la figura, así que se corre sobre Y hacia el lado derecho de la pantalla."""
        right = self.camera.view()[0, :3]
        if abs(right[0]) >= 0.5:
            return 0, 1.0
        return 1, (1.0 if right[1] >= 0 else -1.0)

    def _joint_beside_layout(self, pv) -> tuple[np.ndarray, np.ndarray]:
        """(corrimiento, caja a encuadrar) para dejar la simulación al costado de la figura, a la altura de la articulación y
        con aire entre ambas. Se mide sobre la franja de la figura a esa altura (no sobre toda ella), para que quede cerca de
        lo que se está ajustando."""
        jp = self._jp
        axis, sign = jp.get("shift_axis") or (0, 1.0)
        key = (id(pv), axis, sign)                  # NO depende de la flexión mostrada: la simulación queda quieta mientras el brazo / la pierna gira
        if jp.get("layout_key") == key:
            return jp["layout"]
        # Se mide sobre todo el barrido (de «flex_range», más la flexión 0), no sobre la flexión actual: si no, al girar el brazo
        # en el plano del corrimiento (p. ej. Y-Z mirando a lo largo de X) cambiaban los límites y la figura saltaba de lugar.
        r0, r1 = (float(x) for x in (getattr(pv, "flex_range", None) or (0.0, 0.0)))
        lo_f, hi_f = min(r0, r1, JOINT_PREVIEW_FLEX_MIN), max(r0, r1, JOINT_PREVIEW_FLEX_MAX)      # todo lo que puede mostrar el selector
        flexes = sorted({0.0, *np.linspace(lo_f, hi_f, 14).tolist()})
        pb = np.vstack([pv.proximal_mesh().bounds] + [pv.distal_mesh(f).bounds for f in flexes])
        lo, hi = pb.min(axis=0), pb.max(axis=0)
        v = np.asarray(self.session.work.vertices)
        slab = v[(v[:, 2] >= lo[2] - 5.0) & (v[:, 2] <= hi[2] + 5.0)]
        if len(slab) == 0:
            slab = v
        gap = max(8.0, 0.5 * float(hi[axis] - lo[axis]))
        d = float(slab[:, axis].max() + gap - lo[axis]) if sign > 0 else float(slab[:, axis].min() - gap - hi[axis])
        shift = np.zeros(3)
        shift[axis] = d
        pad = float(np.max(hi - lo))
        moved_lo, moved_hi = lo + shift, hi + shift
        box = np.array([[min(slab[:, 0].min(), moved_lo[0]), min(v[:, 1].min(), moved_lo[1]), lo[2] - pad],
                        [max(slab[:, 0].max(), moved_hi[0]), max(v[:, 1].max(), moved_hi[1]), hi[2] + pad]])
        jp["layout_key"], jp["layout"] = key, (shift, box)
        return shift, box

    def set_joint_preview_flex(self, flex: float) -> None:
        """Flexión a la que se muestra la pieza distal (solo la recoloca: no recalcula el hueco)."""
        self._jp["flex"] = float(flex)
        self._changed()

    def joint_preview_status(self) -> str:
        jp = self._jp
        if not self.show_joint_preview:
            return ""
        if jp["error"]:
            return tr("controller.no_se_puede_previsualizar_error", error=jp['error'])
        if jp["pv"] is None:
            return tr("common.calculando_la_articulacion")
        pv = jp["pv"]
        if getattr(pv, "kind", None) == "arm":          # hombro: además se dice si el brazo gira libre a la flexión mostrada
            ck = (id(pv), round(float(jp["flex"]), 3))
            if jp.get("coll_key") != ck:
                jp["coll_key"], jp["coll_txt"] = ck, pv.summary(float(jp["flex"]))
            text = jp["coll_txt"]
        else:
            text = pv.summary()
        return text + (('\n  ' + tr("common.actualizando")) if jp["busy"] else "")

    def _request_joint_preview(self, name: str) -> None:
        """Lanza el cálculo si cambió algo desde el último (en un hilo si la ventana puso `call_in_main`)."""
        jp = self._jp
        try:
            key = self.session.joint_preview_key(name)
        except KeyError:
            return
        if jp["busy"] or key == jp["key"]:
            return
        try:
            run = self.session.joint_preview_job(name)
        except (ValueError, KeyError) as e:
            self._joint_preview_done(key, None, e)
            return
        jp["busy"] = True

        def work():
            try:
                return run(jp["cache"]), None
            except Exception as e:                   # cualquier falla del motor se muestra en el estado, no tira la ventana
                return None, e

        if self.call_in_main is None:
            self._joint_preview_done(key, *work())
            return

        def threaded():
            res, err = work()
            self.call_in_main(self._joint_preview_done, key, res, err)

        import threading
        threading.Thread(target=threaded, daemon=True, name="afm-joint-preview").start()

    def _joint_preview_done(self, key: str, pv, err) -> None:
        jp = self._jp
        jp["busy"], jp["key"] = False, key
        jp["pv"] = None if err is not None else pv
        jp["error"] = "" if err is None else (str(err) if isinstance(err, (ValueError, KeyError)) else f"{type(err).__name__}: {err}")
        jp["drawn"] = None
        if pv is not None and jp.get("framed") != jp["name"] and self.show_joint_preview:
            jp["framed"] = jp["name"]                # la primera vez: acercar la cámara a la articulación (si no, se ve diminuta)
            b = np.vstack([pv.proximal_mesh().bounds, pv.distal_mesh(jp["flex"]).bounds])
            self.camera.frame(np.array([b.min(axis=0), b.max(axis=0)]))
        self.scene_dirty = True                      # si algo cambió mientras se calculaba, el próximo sync recalcula
        if self.on_change:
            self.on_change()

    _JOINT_PREVIEW_HIDE = ("figura", "voladizos", "mano:", "cabeza:reemplazo", "parte:", "accesorio:")

    def _sync_joint_preview(self, scene) -> None:
        """Dibuja SOLO la articulación (proximal tallado + distal girado) y oculta la figura (la escena no dibuja
        mallas translúcidas: no se puede ver una a través de la otra)."""
        jp = self._jp
        name = jp["name"]
        if self.show_joint_preview and not self.session.preview_exists(name):
            self.show_joint_preview = False
        if jp.get("was_beside") and not self.joint_preview_beside():
            self.show_joint_preview = False          # la herramienta terminó por otro camino (p. ej. al completar los puntos)
        jp["was_beside"] = self.joint_preview_beside()
        if not self.show_joint_preview:
            for key in [k for k in scene.items if k.startswith("junta:")]:
                scene.remove(key)
            jp["drawn"] = None
            return
        self._request_joint_preview(name)
        pv = jp["pv"]
        if pv is None:                               # calculando o con error: la figura se queda a la vista
            for key in [k for k in scene.items if k.startswith("junta:")]:
                scene.remove(key)
            jp["drawn"] = None
            return
        beside = self.joint_preview_beside()
        if not beside:
            for key in list(scene.items):
                if key.startswith(self._JOINT_PREVIEW_HIDE) or (key.startswith("espejo:") and "silueta" not in key):
                    scene.show(key, False)
        if beside and jp.get("framed_beside") != jp["name"]:
            jp["shift_axis"] = self._beside_axis()          # al entrar: se elige el lado según desde dónde se mira
        shift, box = self._joint_beside_layout(pv) if beside else (np.zeros(3), None)
        sig = (id(pv), round(jp["flex"], 6), beside, tuple(np.round(shift, 3)))
        if jp["drawn"] != sig or "junta:prox" not in scene.items:
            prox, dist = pv.proximal_mesh(), pv.distal_mesh(jp["flex"])
            if beside:
                prox, dist = prox.copy(), dist.copy()
                prox.apply_translation(shift)
                dist.apply_translation(shift)
            scene.set_mesh("junta:prox", prox, color=(0.85, 0.80, 0.68, 1.0))
            scene.set_mesh("junta:dist", dist, color=(0.55, 0.75, 0.95, 1.0))
            b = pv.build
            ends = np.array([b.center - b.axis * b.tips[0], b.center + b.axis * b.tips[1]]) + shift
            scene.set_lines("junta:eje", ends, (1.0, 0.3, 0.3, 1.0), on_top=True)
            jp["drawn"] = sig
        if beside and jp.get("framed_beside") != jp["name"]:
            jp["framed_beside"] = jp["name"]                 # una vez al entrar: figura (a esa altura) y simulación juntas en pantalla
            self.camera.frame(box)
        elif not beside:
            jp["framed_beside"] = None

    def joint_limits(self, name: str) -> tuple[float, float]:
        from ..joints import flex_range
        return flex_range(self.session.joint_any(name), self.session.project.params)

    def set_joint_limit(self, name: str, which: str, value: float) -> bool:
        """Cambia solo el mínimo («lo») o el máximo («hi») del rango de flexión, conservando el otro."""
        lo, hi = self.joint_limits(name)
        return self.set_joint_range(name, float(value), hi) if which == "lo" else self.set_joint_range(name, lo, float(value))

    def joint_range_text(self, name: str) -> str:
        lo, hi = self.joint_limits(name)
        return tr("controller.lo_hi_de_flexion", lo=f"{lo:g}", hi=f"{hi:g}")

    def cancel(self, quiet: bool = False) -> None:
        was = self.mode
        if self.show_joint_preview and (was in self.JOINT_TOOL_MODES or self.tool_pivot_name() is not None):
            self.show_joint_preview = False          # al terminar la herramienta vuelve la figura (la simulación solo acompaña al ajuste)
        self.mode, self.pending, self.pending_kind = Mode.VIEW, [], None
        self._hand_path_pending = None
        if was in (Mode.HEAD_CUT_PLANE, Mode.WEAPON_CUT_PLANE):
            self._rebuild_picker()           # durante el corte el picker apuntaba a la cabeza de reemplazo / al arma
        self.clear_hover()
        self._vertex_drag = None
        self.draft, self.edit_limb, self.edit_cross, self.edit_cross2 = None, None, False, False
        self.pin_limb, self.pin_prev = None, None
        self.pin_aid, self.pin_aim, self.pin_hover = None, None, None
        self.pin_proposal = None
        self.head_pin_prev, self.waist_pin_prev, self.joint_prev = None, None, None
        self.joint_limb, self.joint_name = None, None
        was_trim = self.trim_cfg is not None
        self.trim_cfg, self.edit_trim = None, None
        if was == Mode.FLAT_PLANE and self.pose_view and not self.flat_print:
            self.pose_report = None          # vuelven las rodillas a la flexión de la pose impresa: se recalculan las interferencias
        if not quiet:
            self._changed(tr("controller.edicion_del_recorte_terminada") if was_trim
                          else tr("controller.edicion_de_silueta_terminada") if was == Mode.POLYGON
                          else tr("controller.colocacion_del_pasador_terminada") if was in (Mode.PIN, Mode.HEAD_PIN, Mode.WAIST_PIN)
                          else tr("controller.ajuste_del_corte_con_la_cama") if was == Mode.FLAT_PLANE
                          else tr("controller.corte_paralelo_al_piso_terminado") if was == Mode.FLOOR_CUT else tr("common.cancelado_2"))

    def undo_point(self) -> None:
        if self.mode == Mode.POLYGON and self.draft is not None:
            self._polygon_backspace()
            return
        if self.pending:
            self.pending.pop()
            self._changed(tr("controller.quedan_n_punto_s", n=len(self.pending)))

    def auto_symmetry(self) -> None:
        if not self._need_loaded():
            return
        self.cancel(quiet=True)
        self.session.auto_symmetry()
        self._rebuild_picker()
        self._changed(tr("controller.simetria_automatica_en_x_0_la"))

    def front_change_loss(self) -> list[str]:
        """Qué trabajo se borraría al cambiar «Frente hacia» (lista vacía = nada que perder, no hace falta confirmar)."""
        if self.session.base is None:
            return []
        pr = self.session.project
        loss = []
        if pr.symmetry is not None:
            loss.append(tr("controller.el_plano_de_simetria"))
        if pr.limbs:
            loss.append(tr("controller.n_miembro_s", n=len(pr.limbs)))
        if pr.trims:
            loss.append(tr("controller.n_recorte_s", n=len(pr.trims)))
        if pr.head is not None:
            loss.append(tr("controller.la_cabeza"))
        if pr.waist is not None:
            loss.append(tr("controller.la_cintura"))
        return loss

    def set_front(self, front: str) -> None:
        if not self._need_loaded() or front == self.session.project.front:
            return
        self.cancel(quiet=True)
        self.session.set_front(front)
        self._rebuild_picker()
        self.hidden.clear()
        self._changed(tr("controller.frente_hacia_front_se_borraron_simetria", front=front))

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

    def param_default(self, key: str) -> float:
        """Valor por defecto del parámetro (el de `Params()`)."""
        cache = self.__dict__.get("_param_defaults")
        if cache is None:                          # una sola vez: se consulta en cada refresco de la ventana
            from ..params import Params

            cache = {}
            for k, *_ in PARAM_FIELDS:
                obj = Params()
                *path, last = k.split(".")
                for p in path:
                    obj = getattr(obj, p)
                cache[k] = float(getattr(obj, last))
            self._param_defaults = cache
        return cache[key]

    def param_modified(self, key: str) -> bool:
        return abs(self.get_param(key) - self.param_default(key)) > 1e-9

    def modified_params(self) -> list[str]:
        return [f[0] for f in PARAM_FIELDS if self.param_modified(f[0])]

    def reset_param(self, key: str) -> bool:
        """Vuelve un parámetro a su valor por defecto."""
        return self.set_param(key, self.param_default(key))

    def reset_params(self) -> int:
        """Vuelve todos los parámetros de `PARAM_FIELDS` a su valor por defecto (la altura no está entre ellos). Un solo paso de deshacer."""
        keys = self.modified_params()
        if not keys:
            self._changed(tr("controller.todos_los_parametros_ya_tienen_su"), scene=False)
            return 0
        was = self.defer_history
        self.defer_history = True                 # un solo paso de deshacer para todo el lote
        try:
            for k in keys:
                self.reset_param(k)
        finally:
            self.defer_history = was
        self._changed(tr("controller.se_restauraron_n_parametro_s_a", n=len(keys)), scene=False)
        if not was:
            self.record_history()
        return len(keys)

    def set_param(self, key: str, value: float) -> bool:
        """Cambia un parámetro de `PARAM_FIELDS` (p. ej. «hip_pin.shaft_len»). Cualquier cambio invalida la figura
        generada. Devuelve False (y deja el motivo en `message`) si la clave o el valor no valen."""
        spec = next((f for f in PARAM_FIELDS if f[0] == key), None)
        if spec is None:
            self._changed(tr("controller.parametro_desconocido_key", param=key), scene=False)
            return False
        _k, label, lo, hi, _step, _dig = spec
        value = float(value)
        if not (lo - 1e-9 <= value <= hi + 1e-9):
            self._changed(tr("controller.label_value_esta_fuera_de_rango", label=label, value=f"{value:g}", lo=f"{lo:g}", hi=f"{hi:g}"), scene=False)
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
        self._changed(tr("controller.label_value_hay_que_generar_de", label=label, value=f"{value:g}"), scene=False)
        return True

    # ------------------------------------------------- figura asimétrica (cortes propios para cada lado)
    def set_asymmetric(self, on: bool) -> bool:
        """Activa o desactiva «Cortes distintos para cada lado». Devuelve False (con el motivo en `message`) si no se pudo."""
        try:
            self.cancel(quiet=True)
            self.session.set_asymmetric(on)
        except ValueError as e:
            self._changed(str(e), scene=False)
            return False
        self._after_side_change()
        self._changed(tr("controller.cortes_asimetricos_activados" if on else "controller.cortes_asimetricos_desactivados"))
        return True

    def set_edit_side(self, side: str) -> bool:
        """Cambia el lado que se edita («left» / «right»). El derecho se muestra espejado (ver `Session.set_edit_side`)."""
        if side == self.session.edit_side:
            return True
        try:
            self.cancel(quiet=True)
            self.session.set_edit_side(side)
        except ValueError as e:
            self._changed(str(e), scene=False)
            return False
        self._after_side_change()
        self._changed(tr("controller.lado_derecho_activo" if side == "right" else "controller.lado_izquierdo_activo"))
        return True

    def _after_side_change(self) -> None:
        """La malla de trabajo cambió (espejada o no): se rehace el selector de clics y se reencuadra la cámara."""
        if self.loaded:
            self._rebuild_picker()
            self.camera.frame(self.session.work.bounds)
        self.cut_seated = None

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
        msg = tr("controller.altura_de_la_figura_ref_height", ref_height=f"{pr.params.ref_height:g}", k=f"{k:.4g}")
        if has_geom:
            msg += ((' ' + tr("controller.planos_siluetas_y_recortes_se_escalaron")))
        elif self.loaded:
            msg += (' ' + tr("controller.los_pasadores_la_holgura_y_el"))
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
            self._changed(tr("controller.no_se_guardo_el_perfil_e", e=e), scene=False)
            return None
        self._changed(tr("controller.perfil_name_guardado_en_path", name=name, path=path), scene=False)
        return path

    def load_profile(self, name: str, directory: str | None = None) -> bool:
        """Carga un perfil de línea: reemplaza los parámetros del proyecto (siluetas y planos no se tocan)."""
        from .. import profile
        try:
            params = profile.load_profile(name, directory)
        except (OSError, ValueError, TypeError) as e:
            self._changed(tr("controller.no_se_pudo_cargar_el_perfil", name=name, e=e), scene=False)
            return False
        params.ref_height = self.session.project.params.ref_height   # la altura es de la figura abierta, no del perfil
        self.session.project.params = params
        self.session.outcome = None
        self._changed(tr("controller.perfil_name_cargado_holgura_clearance_mm", name=name, clearance=f"{params.clearance:g}"),
                      scene=False)
        return True

    def begin_export_probes(self, outdir: str, which: str = "hip", clearances=None, shaft_deltas=None):
        """Valida y devuelve el trabajo pesado ``job(progress=None) -> dict`` que escribe la placa de probetas
        (`probetas.stl` + `probetas.txt`), apto para un hilo; después hay que llamar `finish_export_probes`.
        `which`: «hip» (cadera), «shoulder» (hombro) o «hinge» (bisagra de rodilla/codo) a variar."""
        if self.busy:
            return None
        self.busy = True
        self._changed(tr("controller.generando_las_probetas_puede_tardar"), scene=False)
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
            self._changed(tr("controller.fallo_la_generacion_de_probetas_error", error=error), scene=False)
            return None
        kind = {"hip": "cadera", "shoulder": "hombro", "hinge": tr("controller.bisagra_de_rodilla_codo")}.get(which, which)
        self._changed(tr("controller.probetas_celdas_articulaciones_pasador_de", celdas=info['celdas'], kind=kind, outdir=outdir),
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
        self._changed(tr("controller.probetas_holgura_clearance_mm_y_vastago", clearance=f"{clearance:g}", shaft_d=f"{shaft_d:g}"),
                      scene=False)

    # ------------------------------------------------------------ siluetas
    def start_polygon(self, name: str, cross: bool = False, cross2: bool = False) -> None:
        """Entra al modo de dibujo de la silueta del miembro `name` (la que ya tenga se carga para editarla).

        Con `cross` se edita su 2.ª silueta: la silueta sobre el plano perpendicular al principal (el frontal,
        normalmente). El miembro es lo que queda dentro de las dos siluetas, como en Onshape (un recorte de
        costado más uno de frente). Con `cross2` se edita la 3.ª silueta, la vista desde arriba (plano horizontal), para sacar
        lo que se solapa con el miembro en las otras dos vistas."""
        if not self._need_loaded():
            return
        limb = self.session.limb(name)
        if limb is None:
            self._changed(tr("controller.no_existe_el_plano_name", name=name), scene=False)
            return
        keep_sim = self.show_joint_preview and self.polygon_hip_name() is not None     # de la silueta a la 2.ª (y al revés) sigue la simulación
        self.cancel(quiet=True)
        if cross2:
            plane = self.session.cross2_plane(name)
            poly = limb.cross2.polygon if limb.cross2 is not None else np.zeros((0, 3))
            what = tr("controller.3_a_silueta_de_name_sobre", name=name, name2=plane.name)
        elif cross:
            plane = self.session.cross_plane(name)
            poly = limb.cross.polygon if limb.cross is not None else np.zeros((0, 3))
            what = tr("controller.2_a_silueta_de_name_sobre", name=name, name2=plane.name)
        else:
            plane, poly, what = limb.plane, limb.polygon, tr("controller.silueta_de_name", name=name)
        if len(poly) >= 3:
            self.draft = PolygonDraft.from_world(plane, poly)
            msg = (tr("controller.what_clic_cerca_de_un_vertice", what=what))
        else:
            self.draft = PolygonDraft(plane)
            msg = (tr("controller.what_hace_clic_para_poner_vertices", what=what))
            if cross2:
                msg += ' ' + tr("controller.cubri_el_miembro_visto_desde_arriba")
            elif cross:
                msg += ((' ' + tr("controller.cubri_el_miembro_visto_de_frente")))
        self.mode, self.edit_limb, self.edit_cross, self.edit_cross2 = Mode.POLYGON, name, cross, cross2
        if keep_sim and self.polygon_hip_name() == self._jp["name"]:
            self.show_joint_preview = True
        if cross or cross2:
            self.camera.look_along(plane.normal, target=plane.origin)
        self._changed(msg)

    def raise_top(self) -> None:
        """En la 2.ª silueta en edición: sube su borde superior hasta el de la silueta principal. Sirve para que el
        escalón de la cadera no limite la altura de la cara de contacto (ni la del hueco y el pasador)."""
        d = self.draft
        if self.mode != Mode.POLYGON or d is None or not self.edit_cross or not d.closed:
            self._changed(tr("controller.abri_la_2_a_silueta_de"), scene=False)
            return
        spec = self.session.limb(self.edit_limb)
        top = self.session._other_top(spec, cross=True)
        if top is None:
            self._changed(tr("controller.falta_la_silueta_principal_del_miembro"), scene=False)
            return
        from .session import _raise_top
        w = d.world()
        new = _raise_top(w, top)
        if np.allclose(new, w):
            self._changed(tr("controller.el_borde_superior_ya_llega_hasta"), scene=False)
            return
        self.draft = PolygonDraft.from_world(d.plane, new)
        self._commit_polygon(tr("controller.borde_superior_subido_a_z_top", top=f"{top:.1f}"))

    def remove_silhouette(self, name: str) -> None:
        """Quita la silueta principal de `name` (el miembro vuelve a figurar como pendiente de silueta)."""
        if self.edit_limb == name and not self.edit_cross and not self.edit_cross2:
            self.cancel(quiet=True)
        if self.session.clear_limb_polygon(name):
            self._changed(tr("controller.silueta_de_name_quitada_falta_dibujarla", name=name))
        else:
            self._changed(tr("controller.name_no_tiene_silueta", name=name), scene=False)

    def remove_cross(self, name: str) -> None:
        """Quita la 2.ª silueta de `name`."""
        if self.edit_limb == name and self.edit_cross:
            self.cancel(quiet=True)
        if self.session.clear_limb_cross(name):
            self._changed(tr("controller.2_a_silueta_de_name_quitado", name=name))
        else:
            self._changed(tr("controller.name_no_tiene_2_a_silueta", name=name), scene=False)

    def remove_cross2(self, name: str) -> None:
        """Quita la 3.ª silueta (la de arriba) de `name`."""
        if self.edit_limb == name and self.edit_cross2:
            self.cancel(quiet=True)
        if self.session.clear_limb_cross2(name):
            self._changed(tr("controller.3_a_silueta_de_name_quitado", name=name))
        else:
            self._changed(tr("controller.name_no_tiene_3_a_silueta", name=name), scene=False)

    def _click_polygon(self, px: float, py: float, width: float, height: float) -> None:
        d = self.draft
        origin, direction = self.camera.ray(px, py, width, height)
        uv = ray_plane_uv(d.plane, origin, direction)
        if uv is None:
            self._changed(tr("controller.el_plano_se_ve_de_canto"), scene=False)
            return
        screen = self.camera.project(d.world(), width, height)[:, :2] if d.pts else np.zeros((0, 2))
        click = (px, py)
        near = d.nearest_vertex(screen, click, VERTEX_PX)
        if not d.closed:
            if near == 0 and len(d.pts) >= 3:
                self.close_polygon()
            elif near is not None:
                self._changed(tr("controller.ya_hay_un_vertice_ahi"), scene=False)
            else:
                d.add(uv)
                self._changed(tr("controller.vertice_n_retroceso_deshace_el_ultimo", n=len(d.pts)))
            return
        if near is not None:
            d.selected = None if d.selected == near else near
            self._changed(tr("controller.vertice_v_elegido_clic_en_el", v=near + 1) if d.selected is not None
                          else tr("controller.vertice_soltado"))
        elif d.selected is not None:
            d.move(d.selected, uv)
            d.selected = None
            self._commit_polygon(tr("controller.vertice_movido"))
        else:
            edge = d.nearest_edge(screen, click, EDGE_PX)
            if edge is None:
                self._changed(tr("controller.clic_cerca_de_un_vertice_para"), scene=False)
            else:
                d.insert(edge, uv)
                self._commit_polygon(tr("controller.vertice_agregado"))

    # ---------------------------------------------- arrastre de vértices (ajuste fino)
    def begin_vertex_drag(self, px: float, py: float, width: float, height: float) -> bool:
        """Empieza a arrastrar el vértice de la silueta bajo el puntero (píxeles). Devuelve True si había uno; en ese caso la
        ventana manda el movimiento con `drag_vertex` y lo termina con `end_vertex_drag`. No hace nada fuera del modo silueta."""
        d = self.draft
        self._vertex_drag = None
        if self.busy or self.mode != Mode.POLYGON or d is None or not d.pts:
            return False
        screen = self.camera.project(d.world(), width, height)[:, :2]
        near = d.nearest_vertex(screen, (px, py), VERTEX_PX)
        if near is None:
            return False
        self._vertex_drag = {"index": near, "before": d.pts[near].copy(), "moved": False}
        return True

    def drag_vertex(self, px: float, py: float, width: float, height: float) -> None:
        """Lleva el vértice arrastrado bajo el puntero (proyectado sobre el plano de la silueta). Solo repinta: el cambio
        se guarda, como un único paso de deshacer, al soltar (`end_vertex_drag`)."""
        st, d = self._vertex_drag, self.draft
        if st is None or d is None or st["index"] >= len(d.pts):
            return
        origin, direction = self.camera.ray(px, py, width, height)
        uv = ray_plane_uv(d.plane, origin, direction)
        if uv is None:                                  # el plano se ve de canto: el vértice se queda donde estaba
            return
        d.move(st["index"], uv)
        d.selected = st["index"]
        st["moved"] = True
        self.scene_dirty = True

    def end_vertex_drag(self) -> bool:
        """Suelta el vértice: guarda la silueta (si ya está cerrada y es válida). Devuelve True si el vértice se movió."""
        st, d = self._vertex_drag, self.draft
        self._vertex_drag = None
        if st is None or d is None or not st["moved"]:
            return False
        d.selected = None
        if d.closed:
            self._commit_polygon(tr("controller.vertice_movido"))
        else:
            self._changed(tr("controller.vertice_movido"))
        return True

    def cancel_vertex_drag(self) -> bool:
        """Esc en pleno arrastre: el vértice vuelve a donde estaba y la silueta queda sin cambios."""
        st, d = self._vertex_drag, self.draft
        self._vertex_drag = None
        if st is None or d is None:
            return False
        if st["index"] < len(d.pts):
            d.pts[st["index"]] = st["before"].copy()
        d.selected = None
        self._changed(tr("controller.arrastre_cancelado"))
        return True

    def _commit_polygon(self, what: str = "Silueta guardada") -> bool:
        """Guarda el polígono cerrado en el proyecto (solo si es válido)."""
        d = self.draft
        if d is None or not d.closed:
            return False
        if self.trim_cfg is not None:
            return self._commit_trim(what)
        err = d.validity_error()
        if err:
            self._changed(tr("controller.what_pero_la_silueta_err_corregila", what=what, err=err))
            return False
        if self.edit_cross2:
            self.session.set_limb_cross2(self.edit_limb, d.world())
            self._changed(tr("controller.what_3_a_silueta_de_edit", what=what, edit_limb=self.edit_limb, n=len(d.pts)))
        elif self.edit_cross:
            self.session.set_limb_cross(self.edit_limb, d.world())
            self._changed(tr("controller.what_2_a_silueta_de_edit", what=what, edit_limb=self.edit_limb, n=len(d.pts)))
        else:
            self.session.set_limb_polygon(self.edit_limb, d.world())
            self._changed(tr("controller.what_silueta_de_edit_limb_n", what=what, edit_limb=self.edit_limb, n=len(d.pts)))
        return True

    def close_polygon(self) -> None:
        d = self.draft
        if self.mode != Mode.POLYGON or d is None:
            return
        if not d.close():
            self._changed(tr("controller.hacen_falta_al_menos_3_vertices"), scene=False)
            return
        self._commit_polygon(tr("controller.poligono_cerrado"))

    def _polygon_backspace(self) -> None:
        d = self.draft
        if not d.closed:
            if d.pts:
                d.pts.pop()
                self._changed(tr("controller.quedan_n_vertice_s", n=len(d.pts)))
        elif d.selected is not None:
            if len(d.pts) <= 3:
                self._changed(tr("controller.un_poligono_necesita_al_menos_3"), scene=False)
            else:
                d.delete(d.selected)
                self._commit_polygon(tr("controller.vertice_borrado"))
        else:
            self._changed(tr("controller.elegi_un_vertice_clic_cerca_para"), scene=False)

    def clear_polygon(self) -> None:
        """Vacía el borrador para dibujar de nuevo (la silueta guardada se reemplaza al cerrar el nuevo polígono)."""
        if self.mode == Mode.POLYGON and self.draft is not None:
            self.draft.clear()
            self._changed(tr("controller.silueta_vaciada_dibuja_los_vertices_de"))

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
            self._changed(tr("controller.no_hay_contorno_de_la_figura"), scene=False)
            return
        ring = np.asarray(max(shapes, key=lambda g: g.area).simplify(0.6).exterior.coords)[:-1]
        d.pts, d.closed, d.selected = [p.copy() for p in ring], True, None
        self._commit_polygon(tr("controller.contorno_de_la_figura_usado_como"))

    def propose_silhouette(self, crotch_z: float | None = None) -> None:
        """Reemplaza el borrador por la silueta que propone el programa para el miembro en edición."""
        d = self.draft
        if self.mode != Mode.POLYGON or d is None:
            return
        if self.edit_limb is None:
            self._changed(tr("controller.la_propuesta_automatica_es_para_la"), scene=False)
            return
        if self.edit_cross2:
            self._changed(tr("controller.la_propuesta_no_es_para_la_3_a"), scene=False)
            return
        try:
            poly = self.session.propose_silhouette(self.edit_limb, crotch_z, cross=self.edit_cross)
        except (ValueError, KeyError) as e:
            self._changed(str(e), scene=False)
            return
        self.draft = PolygonDraft.from_world(d.plane, poly)
        self._changed(tr("controller.silueta_propuesta_revisala_y_corregi_los"))

    # -------------------------------------------------------------- pasador
    def start_pin(self, name: str) -> None:
        """Entra al modo de colocación del pasador del miembro `name`.

        Se calcula la cara de contacto (el plano de corte donde va el pasador) y la cámara se pone de frente
        a ella con la parte del torso recortada; el clic elige el centro del pasador sobre esa cara.
        """
        if not self._need_loaded():
            return
        if self.session.limb(name) is None:
            self._changed(tr("controller.no_existe_el_plano_name", name=name), scene=False)
            return
        keep_sim = self.show_joint_preview and self.pin_pivot_name() == self._jp["name"] and self._pivot_name_of(name) == self._jp["name"]
        self.cancel(quiet=True)
        fixed = self.session.fix_limb_extrude(name)
        try:
            prev = self.session.pin_preview(name, allow_small=True)      # una cara chica NO impide entrar: se ve el fantasma en rojo
        except (ValueError, KeyError) as e:
            self._changed(tr("controller.no_se_puede_colocar_el_pasador", name=name, e=e), scene=False)
            return
        self.mode, self.pin_limb, self.pin_prev = Mode.PIN, name, prev
        if keep_sim and self.pin_pivot_name() == self._jp["name"]:
            self.show_joint_preview = True          # el pasador se movió o cambió la cara: la simulación al costado sigue
        self.pin_aid, self.pin_aim, self.pin_hover = self._make_pin_aid(name, prev), None, None
        face = prev.face
        half = float(max(np.ptp(prev.plane.to_uv(r), axis=0).max() for r in prev.rings_world())) * 0.65 or 10.0
        self.camera.look_along(face.outward, face.center, max(half, 8.0))
        self.hidden.discard(name)
        note = ((tr("controller.name_tiene_el_plano_de_costado", name=name) + ' ')) if fixed else ""
        small = ''
        shaft = self.session.pin_spec(name).shaft_d
        if prev.face.area < np.pi * (shaft / 2) ** 2:      # la cara es menor que el vástago: se explica y se ofrece qué hacer
            small = ' ' + tr("controller.pin_cara_chica", area=f"{prev.face.area:.1f}", shaft=f"{shaft:g}") + self.session.pin_miss_hint(name, prev.project(self._pin_center(name)))
        self.pin_proposal = None
        offer = self._offer_pin_fix(name, prev.project(self._pin_center(name)))
        self._changed(note + (tr("controller.pasador_de_name", name=name) + ' ') + self._pin_status(name) + self._reach_warning(name) + small
                      + offer + (' ' + tr("controller.clic_mover_flechas_0_1_mm")))

    def _make_pin_aid(self, name: str, prev):
        """La mira asistida del pasador (`pin_aid.PinAid`) contra el catálogo abierto. None si no hay catálogo, no se puede abrir
        o el miembro no tiene puerto de catálogo: el modo PIN anda igual, solo sin la mira."""
        if not self.session.catalog_root:
            return None
        from ..pin_aid import PinAid

        try:
            params = self.session.project.params
            return PinAid(prev, self.session.limb(name).kind, self.session._catalog(), params, height_mm=params.ref_height,
                          exclude_figure=os.path.basename(self.session.stl_path or "") or None)
        except (ValueError, KeyError, OSError):
            return None

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
        return (' ' + tr("controller.aviso_cruza_con_el_del_otro", v=f"{-reach:.1f}"))

    def _pin_status(self, name: str) -> str:
        """Una línea corta: qué posición se usa y el margen al borde. Es informativa: no impide elegir ningún punto
        (salvo que el vástago no toque la cara, ver `Session.set_limb_pin`)."""
        prev = self.pin_prev
        uv = prev.project(self._pin_center(name))
        pin = self.session.pin_spec(name)
        inside, margin = prev.fit(uv, pin.shaft_d)
        user = self.session.limb(name).pin_center is not None
        txt = tr("controller.posicion_elegida") if user else tr("controller.posicion_automatica")
        if not inside:
            return txt + tr("controller.centro_fuera_de_la_cara_vastago", shaft_d=f"{pin.shaft_d:g}", v=f"{max(margin + pin.shaft_d / 2, 0):.1f}")
        if margin < 0:
            return (txt + tr("controller.el_vastago_sobresale_v_mm_del", v=f"{-margin:.1f}") + self._cabe_note(name, uv)
                    + self._aim_note(uv))
        if margin < self.session.project.params.min_wall:
            return txt + tr("controller.margen_al_borde_margin_mm_min", margin=f"{margin:.1f}", min_wall=f"{self.session.project.params.min_wall:g}") + self._aim_note(uv)
        return txt + tr("controller.margen_al_borde_margin_mm", margin=f"{margin:.1f}") + self._aim_note(uv)

    def _cabe_note(self, name: str, uv) -> str:
        """« Acá cabe Ø 2.6 mm; el punto más ancho de la cara admite Ø 3.1 mm.» cuando el pasador no entra (para decidir si achicarlo)."""
        try:
            fit = self.session.pin_fit_at(name, uv)
            _wuv, wd = widest_point(self.pin_prev.shape)
            return ' ' + tr("controller.pin_cabe", here=f"{fit.max_shaft_d:.1f}", widest=f"{wd:.1f}")
        except Exception:      # noqa: BLE001 — es solo ayuda
            return ''

    def _aim_note(self, uv) -> str:
        """« Catálogo: torsos 3/4 · brazos 17/21.» con la mira asistida, o nada si no hay catálogo contra el cual medir."""
        if self.pin_aid is None:
            return ""
        c = self.pin_aid.evaluate(uv).contador()
        return (' ' + tr("controller.catalogo_c", c=c)) if c else ""

    def _joint_mode(self) -> str | None:
        """«cuello» / «cintura» mientras se coloca su pasador de costado (HEAD_PIN / WAIST_PIN), si no None."""
        return {Mode.HEAD_PIN: "cuello", Mode.WAIST_PIN: "cintura"}.get(self.mode)

    def _joint_active(self) -> bool:
        return self._joint_mode() is not None and self.joint_prev is not None and self.pin_aid is not None and not self.busy

    def _pin_aim_active(self) -> bool:
        if self._joint_mode() is not None:
            return self._joint_active()
        return self.mode == Mode.PIN and self.pin_prev is not None and self.pin_aid is not None and not self.busy

    def _aim_plane(self):
        """El plano de la cara que lee la mira: el del miembro (PIN) o el del cuello / la cintura (de costado)."""
        return self.joint_prev.plane if self._joint_mode() else self.pin_prev.plane

    def _aim_uv(self, px: float, py: float, width: float, height: float):
        """(u, v) del punto de la cara bajo el puntero. De costado el pasador está en x = 0: el rayo se lleva a ese plano."""
        origin, direction = self.camera.ray(px, py, width, height)
        if self._joint_mode() is None:
            return ray_plane_uv(self.pin_prev.plane, origin, direction)
        if abs(direction[0]) < 1e-6:
            return None
        return self.joint_prev.plane.to_uv(origin - origin[0] / direction[0] * direction)[0]

    def _update_pin_aim(self, px: float, py: float, width: float, height: float) -> bool:
        """Lee la mira en el punto de la cara bajo el puntero (sin clic) y deja el fantasma y la barra de estado al día."""
        uv = self._aim_uv(px, py, width, height)
        if uv is None:
            return self._clear_pin_aim()
        r = self.pin_aid.evaluate(uv)
        old = self.pin_aim
        self.pin_hover = (float(uv[0]), float(uv[1]))
        if old is not None and old.nivel == r.nivel and np.allclose(old.uv, r.uv, atol=0.05):
            return False
        self.pin_aim = r
        motivo = r.peor_motivo() if r.nivel != "ok" else None
        self.message = (tr("controller.mira") + ' ') + r.texto() + (f" · {motivo}" if motivo else "")
        self.scene_dirty = True                      # la ventana refresca solo la barra de estado y el recuadro (no toda la interfaz)
        return True

    def aim_active(self) -> bool:
        """True si hay que leer la mira cuando el puntero se mueve (modo PIN con catálogo abierto)."""
        return self._pin_aim_active()

    def _clear_pin_aim(self) -> bool:
        if self.pin_aim is None and self.pin_hover is None:
            return False
        self.pin_aim = None
        self.pin_hover = None
        self.scene_dirty = True
        return True

    # ------------------------------------------- pasador fantasma (siempre, con o sin catálogo)
    def ghost_active(self) -> bool:
        """True si en el modo PIN hay que seguir el puntero para dibujar el pasador / hueco fantasma sobre la cara."""
        return self.mode == Mode.PIN and self.pin_prev is not None and not self.busy

    def _update_pin_hover(self, px: float, py: float, width: float, height: float) -> bool:
        """Sin catálogo (sin mira): el fantasma sigue al puntero y la barra de estado dice si el pasador entra ahí."""
        uv = ray_plane_uv(self.pin_prev.plane, *self.camera.ray(px, py, width, height))
        if uv is None:
            return self._clear_pin_aim()
        uv = (float(uv[0]), float(uv[1]))
        if self.pin_hover is not None and np.allclose(self.pin_hover, uv, atol=0.05):
            return False
        self.pin_hover = uv
        self.message = tr("controller.pin_fantasma", txt=self.ghost_text(uv))
        self.scene_dirty = True
        return True

    def ghost_text(self, uv=None) -> str:
        """Frase del fantasma: si el pasador entra en `uv` (o bajo el puntero / en el centro actual) y cuánto margen queda."""
        if self.mode != Mode.PIN or self.pin_prev is None:
            return ''
        if uv is None:
            uv = self.pin_hover if self.pin_hover is not None else self.pin_prev.project(self._pin_center(self.pin_limb))
        fit = self.session.pin_fit_at(self.pin_limb, uv)
        return fit.text(self.session.project.params.min_wall)

    def pin_widest(self) -> None:
        """Lleva el pasador al punto de la cara donde más ancho cabe un vástago (el centro del mayor círculo inscripto)."""
        if self.mode != Mode.PIN or self.pin_prev is None:
            return
        uv, d = widest_point(self.pin_prev.shape)
        if d <= 0:
            self._changed(tr("controller.pin_sin_lugar", min=f"{MIN_SHAFT_D:g}"), scene=False)
            return
        self._place_pin(uv, tr("controller.pin_al_punto_mas_ancho", d=f"{d:.1f}"))

    def pin_shrink_to_fit(self) -> None:
        """Achica el pasador (vástago y cabeza, en proporción) hasta que entre en la cara con la pared mínima de borde. Si el
        centro está fuera o en un lugar angosto, lo lleva antes al punto más ancho. Cambia el parámetro del hombro / la cadera."""
        if self.mode != Mode.PIN or self.pin_prev is None:
            return
        name = self.pin_limb
        spec = self.session.limb(name)
        pre = "hip_pin" if spec.kind == "leg" else "shoulder_pin"
        pin, prm = self.session.pin_spec(name), self.session.project.params
        uv = self.pin_prev.project(self._pin_center(name))
        fit = self.session.pin_fit_at(name, uv)
        wuv, wd = widest_point(self.pin_prev.shape)
        if wd > fit.max_shaft_d:                       # el punto más ancho admite más que el actual: se trabaja ahí
            uv, room = wuv, wd
        else:
            room = fit.max_shaft_d
        dims = shrunk_dims(pin, room, prm.min_wall)
        if dims is None:
            self._changed(tr("controller.pin_sin_lugar", min=f"{MIN_SHAFT_D + 2 * prm.min_wall:g}"), scene=False)
            return
        if dims != (pin.shaft_d, pin.head_d):
            shaft, head = dims
            lo_s, lo_h = self._pin_lower_bounds(pre)
            if shaft < lo_s - 1e-9:
                self._changed(tr("controller.pin_minimo_vastago", shaft=f"{shaft:g}", lo=f"{lo_s:g}", room=f"{room:.1f}"), scene=False)
                return
            if not (self.set_param(f"{pre}.shaft_d", shaft) and self.set_param(f"{pre}.head_d", max(head, lo_h))):
                return
            self.pin_aid = self._make_pin_aid(name, self.pin_prev)           # la mira midió con el pasador anterior
        self.pin_proposal = None
        self._place_pin(uv, tr("controller.pin_achicado", shaft=f"{self.session.pin_spec(name).shaft_d:g}",
                               head=f"{self.session.pin_spec(name).head_d:g}", d=f"{room:.1f}"))

    # ------------------------------------------- «no entra»: proponer un pasador más chico y preguntar
    def _pin_lower_bounds(self, pre: str) -> tuple[float, float]:
        """Mínimos (Ø vástago, Ø cabeza) que admite la herramienta para el pasador `pre` («shoulder_pin» / «hip_pin»)."""
        lo_s = next(f[2] for f in PARAM_FIELDS if f[0] == f"{pre}.shaft_d")
        lo_h = next(f[2] for f in PARAM_FIELDS if f[0] == f"{pre}.head_d")
        return lo_s, lo_h

    def _offer_pin_fix(self, name: str, uv) -> str:
        """Si el pasador NO entra en `uv`, arma la propuesta (queda en `pin_proposal`, sin cambiar nada) y devuelve la frase corta para
        el estado; con propuesta, la barra del visor pregunta «¿achicarlo?». Si achicar no alcanza (queda bajo el mínimo de la herramienta)
        lo dice en vez de ofrecer. '' si el pasador entra o no hay nada que proponer.

        No se achica en silencio: el Ø del vástago es parte de la interfaz con el catálogo (la compatibilidad con otros torsos y
        miembros), así que cambiarlo es una decisión del usuario."""
        self.pin_proposal = None
        try:
            fit = self.session.pin_fit_at(name, uv)
            if fit.level != "no":
                return ""
            spec, prm = self.session.limb(name), self.session.project.params
            pre = "hip_pin" if spec.kind == "leg" else "shoulder_pin"
            lo_s, lo_h = self._pin_lower_bounds(pre)
            res = propose_smaller_pin(self.pin_prev.shape, uv, self.session.pin_spec(name), prm.min_wall, lo_s, lo_h)
        except Exception:      # noqa: BLE001 — es solo ayuda: nunca debe romper el clic
            return ""
        if res.proposal is not None:
            self.pin_proposal = res.proposal
            if abs(res.proposal.shaft_d - res.proposal.old_shaft_d) < 1e-9:       # entra entero en otro punto de la cara: solo moverlo
                return ' ' + tr("controller.pin_oferta_corta_mover")
            return ' ' + tr("controller.pin_oferta_corta", shaft=f"{res.proposal.shaft_d:g}", head=f"{res.proposal.head_d:g}")
        if res.reason == "bajo_minimo":
            return ' ' + tr("controller.pin_oferta_bajo_minimo", lo=f"{lo_s:g}")
        return ""

    def pin_proposal_text(self) -> str:
        """La pregunta de la barra del visor: qué cambiaría aceptar (vacía si no hay propuesta)."""
        pr = self.pin_proposal
        if pr is None or self.mode != Mode.PIN:
            return ""
        move = tr("controller.pin_oferta_mueve", mm=f"{pr.moved_mm:.1f}") if pr.moved_mm > 0.05 else ""
        if abs(pr.shaft_d - pr.old_shaft_d) < 1e-9:
            return tr("controller.pin_oferta_solo_mover", mm=f"{pr.moved_mm:.1f}", room=f"{pr.room:.1f}")
        return tr("controller.pin_oferta", shaft=f"{pr.shaft_d:g}", head=f"{pr.head_d:g}", old_shaft=f"{pr.old_shaft_d:g}",
                  old_head=f"{pr.old_head_d:g}", move=move)

    def accept_pin_fix(self) -> None:
        """El usuario dijo que sí: aplica el pasador achicado propuesto (cambia el parámetro del hombro / la cadera) y lo coloca."""
        pr, name = self.pin_proposal, self.pin_limb
        if self.mode != Mode.PIN or self.pin_prev is None or pr is None or name is None:
            return
        pre = "hip_pin" if self.session.limb(name).kind == "leg" else "shoulder_pin"
        if abs(pr.shaft_d - pr.old_shaft_d) > 1e-9 or abs(pr.head_d - pr.old_head_d) > 1e-9:
            if not (self.set_param(f"{pre}.shaft_d", pr.shaft_d) and self.set_param(f"{pre}.head_d", pr.head_d)):
                return
            self.pin_aid = self._make_pin_aid(name, self.pin_prev)           # la mira midió con el pasador anterior
        self.pin_proposal = None
        pin = self.session.pin_spec(name)
        self._place_pin(pr.uv, tr("controller.pin_achicado", shaft=f"{pin.shaft_d:g}", head=f"{pin.head_d:g}", d=f"{pr.room:.1f}"))

    def reject_pin_fix(self) -> None:
        """El usuario dijo que no: el pasador queda como está (el fantasma sigue en rojo) y no se vuelve a preguntar hasta moverlo."""
        if self.pin_proposal is None:
            return
        self.pin_proposal = None
        self._changed(tr("controller.pin_oferta_rechazada"), scene=False)

    def set_pin_size(self, key: str, value: float) -> bool:
        """Cambia una medida del pasador / hueco (vástago, cabeza, largo, alto de cabeza, holgura) viendo el fantasma al instante."""
        if not self.set_param(key, value):
            return False
        if self.mode == Mode.PIN and self.pin_prev is not None:
            name = self.pin_limb
            self.pin_aid = self._make_pin_aid(name, self.pin_prev)
            self.pin_aim = None
            label = next((f[1] for f in PARAM_FIELDS if f[0] == key), key)
            offer = self._offer_pin_fix(name, self.pin_prev.project(self._pin_center(name)))
            self._changed(f"{loc(label)}: {float(value):g}. " + self._pin_status(name) + offer)
        return True

    def pin_simulate_pivot(self, on: bool = True) -> None:
        """Atajo desde el panel del pasador: enciende / apaga la simulación del giro del hombro (o cadera) de este miembro. Se ve
        AL COSTADO de la figura (que sigue a la vista para colocar el pasador), igual que la cadera al dibujar la silueta."""
        name = self.pin_limb
        if name is None or self.session.limb(name) is None:
            return
        from ..joint_preview import pivot_preview_name

        self.set_joint_preview(pivot_preview_name(self.session.limb(name)), on)

    def snap_pin(self) -> None:
        """El «imán»: mueve el pasador a la mejor posición (más piezas del catálogo que lo aceptan) dentro de ±2 mm."""
        joint = self._joint_mode()
        if joint is not None and self.joint_prev is not None:
            self._snap_joint_pin(joint)
            return
        if self.mode != Mode.PIN or self.pin_prev is None:
            return
        if self.pin_aid is None:
            self._changed(tr("controller.el_iman_necesita_un_catalogo_abierto"), scene=False)
            return
        best = self.pin_aid.best_near(self.pin_prev.project(self._pin_center(self.pin_limb)))
        if best is None:
            self._changed(tr("controller.el_iman_no_encontro_ningun_punto"), scene=False)
            return
        self._place_pin(best.uv, tr("common.iman"))

    def _place_pin(self, uv, what: str = "Pasador movido") -> bool:
        name = self.pin_limb
        world = self.pin_prev.plane.to_world(np.asarray(uv, float)[None])[0]
        try:
            self.session.set_limb_pin(name, world)
        except ValueError as e:
            self._changed(tr("controller.no_se_movio_e", e=e), scene=False)
            return False
        offer = self._offer_pin_fix(name, uv)          # si no entra: propone achicarlo y pregunta (no cambia nada solo)
        self._changed(f"{what}: " + self._pin_status(name) + offer)
        return True

    def _click_pin(self, px: float, py: float, width: float, height: float) -> None:
        origin, direction = self.camera.ray(px, py, width, height)
        uv = ray_plane_uv(self.pin_prev.plane, origin, direction)
        if uv is None:
            self._changed(tr("controller.la_cara_de_contacto_se_ve"),
                          scene=False)
            return
        self._place_pin(uv)

    def nudge_pin(self, du: float, dv: float, big: bool = False) -> None:
        """Mueve el pasador `du`, `dv` pasos sobre la cara (u: derecha, v: arriba, vistos de frente)."""
        if self.mode != Mode.PIN or self.pin_prev is None:
            return
        step = NUDGE_MM * (10.0 if big else 1.0)
        uv = self.pin_prev.project(self._pin_center(self.pin_limb)) + np.array([du, dv], float) * step
        self._place_pin(uv, tr("controller.pasador_ajustado"))

    def reset_pin(self) -> None:
        """Descarta la posición elegida y vuelve a la que propone el programa."""
        if self.mode != Mode.PIN or self.pin_prev is None:
            return
        self.session.set_limb_pin(self.pin_limb, None)
        offer = self._offer_pin_fix(self.pin_limb, self.pin_prev.project(self._pin_center(self.pin_limb)))
        self._changed((tr("controller.pasador_restablecido") + ' ') + self._pin_status(self.pin_limb) + offer)

    # ------------------------------------------------- pasador del cuello (vista de costado)
    def start_head_pin(self) -> None:
        """Elegir dónde va el pasador de giro de la cabeza mirando la figura de costado: el plano del cuello se ve
        de canto y el pasador se desliza adelante / atrás sobre él (siempre en x = 0, como el cuello)."""
        if not self._need_loaded():
            return
        if self.session.project.head is None:
            self._changed(tr("controller.primero_defini_el_plano_del_cuello"), scene=False)
            return
        try:
            prev = self.session.head_pin_preview()
        except ValueError as e:
            self._changed(tr("controller.no_se_puede_colocar_el_pasador_2", e=e), scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.head_pin_prev = Mode.HEAD_PIN, prev
        self.pin_aid, self.joint_prev = self._joint_pin_aid("cuello")
        self.pin_aim = None
        self.camera.look_along((1.0, 0.0, 0.0), self._head_pin_center(), 0.35 * self.session.project.params.ref_height)
        self._changed((tr("controller.pasador_del_cuello_vista_de_costado") + ' ') + self._head_pin_status()
                      + (' ' + tr("controller.clic_mover_flechas_atras_y_adelante")))

    def _head_pin_center(self) -> np.ndarray:
        """Centro del pasador que se usaría ahora: el elegido, o el automático."""
        spec, prev = self.session.project.head, self.head_pin_prev
        return prev.default_center if spec.pin_center is None else np.asarray(spec.pin_center, float)

    def _head_pin_status(self) -> str:
        spec, prev = self.session.project.head, self.head_pin_prev
        c, shaft = self._head_pin_center(), self.session.project.params.neck_pin.shaft_d
        inside, margin = prev.fit(c, shaft)
        off = float((c - prev.default_center) @ prev.along)
        txt = tr("controller.posicion_automatica") if spec.pin_center is None else (
            tr("controller.posicion_elegida_off_mm_v_de", off=f"{abs(off):.1f}", v='adelante' if off > 0 else 'atrás')
            if abs(off) >= 0.05 else tr("controller.posicion_elegida_igual_a_la_automatica"))
        if not inside:
            return txt + tr("controller.centro_fuera_de_la_cara_vastago_2", shaft=f"{shaft:g}", v=f"{max(margin + shaft / 2, 0):.1f}")
        if margin < 0:
            return txt + tr("controller.el_vastago_sobresale_v_mm_del", v=f"{-margin:.1f}")
        if margin < self.session.project.params.min_wall:
            return txt + tr("controller.margen_al_borde_margin_mm_min", margin=f"{margin:.1f}", min_wall=f"{self.session.project.params.min_wall:g}")
        return txt + tr("controller.margen_al_borde_margin_mm", margin=f"{margin:.1f}") + self._joint_note(self._head_pin_center())

    def _place_head_pin(self, point, what: str | None = None) -> bool:
        what = what or tr("controller.pasador_del_cuello_movido")
        try:
            self.session.set_head_pin(point)
        except ValueError as e:
            self._changed(tr("controller.no_se_movio_e", e=e), scene=False)
            return False
        self._changed(f"{what}: " + self._head_pin_status() + (' ' + tr("controller.hay_que_generar_de_nuevo")))
        return True

    def _click_head_pin(self, px: float, py: float, width: float, height: float) -> None:
        """El clic se lleva al plano de simetría (x = 0), donde está el pasador, y de ahí al plano del cuello."""
        origin, direction = self.camera.ray(px, py, width, height)
        if abs(direction[0]) < 1e-6:
            self._changed(tr("controller.esta_vista_mira_de_canto_el"),
                          scene=False)
            return
        self._place_head_pin(origin - origin[0] / direction[0] * direction)

    def nudge_head_pin(self, du: float, dv: float, big: bool = False) -> None:
        """Mueve el pasador del cuello `du + dv` pasos sobre la cara (+ = adelante)."""
        if self.mode != Mode.HEAD_PIN or self.head_pin_prev is None:
            return
        step = NUDGE_MM * (10.0 if big else 1.0)
        c = self._head_pin_center() + self.head_pin_prev.along * (du + dv) * step
        self._place_head_pin(c, tr("controller.pasador_del_cuello_ajustado"))

    def reset_head_pin(self) -> None:
        """Descarta la posición elegida del pasador del cuello y vuelve a la automática."""
        if self.session.project.head is None or self.session.project.head.pin_center is None:
            self._changed(tr("controller.el_pasador_del_cuello_ya_esta"), scene=False)
            return
        self.session.set_head_pin(None)
        if self.mode != Mode.HEAD_PIN:
            self.start_head_pin()                  # vuelve a la vista de costado para ver y reposicionar el pasador
            self._changed((tr("controller.pasador_del_cuello_restablecido") + ' ') + self._head_pin_status()
                          + (' ' + tr("controller.clic_mover_flechas_atras_y_adelante_3")))
            return
        self._changed((tr("controller.pasador_del_cuello_restablecido") + ' ') + self._head_pin_status() + (' ' + tr("controller.hay_que_generar_de_nuevo")))

    # ------------------------------------------------- mira del pasador del cuello y de la cintura (de costado)
    def _joint_pin_aid(self, lugar: str):
        """`(LinePinAid, LinePinPreview)` que lee la mira del pasador de `lugar` sobre su plano actual, o `(None, None)` sin catálogo
        (o si ahí no hay cara). Nunca levanta: la ayuda no debe romper la colocación del pasador."""
        try:
            aid = self.session.joint_aid(lugar)
            r = aid.reading_at(0.0)
            if r.prev is None:
                return None, None
            line = getattr(aid, "_line_pin", None)
            if line is None or line.prev is not r.prev:
                line = aid._pin_aid(r.prev)
                aid._line_pin = line
            return line, r.prev
        except (ValueError, KeyError):
            return None, None

    def _joint_note(self, center) -> str:
        """« Catálogo: torsos 3/4 · cabezas 1/1.» de la posición `center` (mundo) del pasador, o nada sin mira."""
        if self.pin_aid is None or self.joint_prev is None or self._joint_mode() is None:
            return ""
        c = self.pin_aid.evaluate(self.joint_prev.project(center)).contador()
        return (' ' + tr("controller.catalogo_c", c=c)) if c else ""

    def _snap_joint_pin(self, lugar: str) -> None:
        if self.pin_aid is None:
            self._changed(tr("controller.el_iman_necesita_un_catalogo_abierto"), scene=False)
            return
        c = self._head_pin_center() if lugar == "cuello" else self._waist_pin_center()
        best = self.pin_aid.best_near(self.joint_prev.project(c))
        if best is None:
            self._changed(tr("controller.el_iman_no_encontro_ningun_punto"), scene=False)
            return
        world = self.joint_prev.plane.to_world(np.asarray(best.uv, float)[None])[0]
        if lugar == "cuello":
            self._place_head_pin(world, tr("common.iman"))
        else:
            self._place_waist_pin(world, tr("common.iman"))

    def start_waist_pin(self) -> None:
        """Elegir dónde va el pasador de la cintura mirando la figura de costado (como el del cuello): siempre en x = 0."""
        if not self._need_loaded():
            return
        if self.session.project.waist is None:
            self._changed(tr("controller.primero_defini_el_plano_de_la"), scene=False)
            return
        try:
            prev = self.session.waist_pin_preview()
        except ValueError as e:
            self._changed(tr("controller.no_se_puede_colocar_el_pasador_3", e=e), scene=False)
            return
        self.cancel(quiet=True)
        self.mode, self.waist_pin_prev = Mode.WAIST_PIN, prev
        self.pin_aid, self.joint_prev = self._joint_pin_aid("cintura")
        self.pin_aim = None
        self.camera.look_along((1.0, 0.0, 0.0), self._waist_pin_center(), 0.35 * self.session.project.params.ref_height)
        self._changed((tr("controller.pasador_de_la_cintura_vista_de") + ' ') + self._waist_pin_status()
                      + (' ' + tr("controller.clic_mover_flechas_atras_y_adelante_2")))

    def _waist_pin_center(self) -> np.ndarray:
        spec, prev = self.session.project.waist, self.waist_pin_prev
        return prev.default_center if spec.pin_center is None else np.asarray(spec.pin_center, float)

    def _waist_pin_status(self) -> str:
        spec, prev = self.session.project.waist, self.waist_pin_prev
        c, shaft = self._waist_pin_center(), self.session.project.params.waist_pin.shaft_d
        inside, margin = prev.fit(prev.project(c), shaft)
        off = float((c - prev.default_center) @ prev.along)
        txt = tr("controller.posicion_automatica") if spec.pin_center is None else (
            tr("controller.posicion_elegida_off_mm_v_de", off=f"{abs(off):.1f}", v='adelante' if off > 0 else 'atrás')
            if abs(off) >= 0.05 else tr("controller.posicion_elegida_igual_a_la_automatica"))
        if not inside:
            return txt + tr("controller.centro_fuera_de_la_cara_vastago_2", shaft=f"{shaft:g}", v=f"{max(margin + shaft / 2, 0):.1f}")
        if margin < 0:
            txt += tr("controller.el_vastago_sobresale_v_mm_del", v=f"{-margin:.1f}")
        elif margin < self.session.project.params.min_wall:
            txt += tr("controller.margen_al_borde_margin_mm_min", margin=f"{margin:.1f}", min_wall=f"{self.session.project.params.min_wall:g}")
        else:
            txt += tr("controller.margen_al_borde_margin_mm", margin=f"{margin:.1f}")
        return txt + self._joint_note(c)

    def _place_waist_pin(self, point, what: str | None = None) -> bool:
        what = what or tr("controller.pasador_de_la_cintura_movido")
        try:
            self.session.set_waist_pin(point)
        except ValueError as e:
            self._changed(tr("controller.no_se_movio_e", e=e), scene=False)
            return False
        self._changed(f"{what}: " + self._waist_pin_status() + (' ' + tr("controller.hay_que_generar_de_nuevo")))
        return True

    def _click_waist_pin(self, px: float, py: float, width: float, height: float) -> None:
        origin, direction = self.camera.ray(px, py, width, height)
        if abs(direction[0]) < 1e-6:
            self._changed(tr("controller.esta_vista_mira_de_canto_el"),
                          scene=False)
            return
        self._place_waist_pin(origin - origin[0] / direction[0] * direction)

    def nudge_waist_pin(self, du: float, dv: float, big: bool = False) -> None:
        if self.mode != Mode.WAIST_PIN or self.waist_pin_prev is None:
            return
        step = NUDGE_MM * (10.0 if big else 1.0)
        self._place_waist_pin(self._waist_pin_center() + self.waist_pin_prev.along * (du + dv) * step,
                              tr("controller.pasador_de_la_cintura_ajustado"))

    def reset_waist_pin(self) -> None:
        """Descarta la posición elegida del pasador de la cintura (el puerto «cintura» vuelve al centroide de la cara)."""
        if self.session.project.waist is None or self.session.project.waist.pin_center is None:
            self._changed(tr("controller.el_pasador_de_la_cintura_ya"), scene=False)
            return
        self.session.set_waist_pin(None)
        if self.mode != Mode.WAIST_PIN:
            self.start_waist_pin()
            return
        self._changed((tr("controller.pasador_de_la_cintura_restablecido") + ' ') + self._waist_pin_status() + (' ' + tr("controller.hay_que_generar_de_nuevo")))

    def aim_box(self) -> dict | None:
        """Datos del recuadro de la mira (`pin_aid_view.aim_box_data`) del pasador que se coloca (miembro, cuello o cintura)."""
        from ..pin_aid_view import aim_box_data

        if not self._pin_aim_active():
            return None
        try:
            if self._joint_mode():
                c = self._head_pin_center() if self.mode == Mode.HEAD_PIN else self._waist_pin_center()
                uv = self.joint_prev.project(c)
            else:
                uv = self.pin_prev.project(self._pin_center(self.pin_limb))
            return aim_box_data(self.pin_aid, self.pin_aim or self.pin_aid.evaluate(uv))
        except Exception:  # noqa: BLE001 — el recuadro nunca debe romper la edición
            return None

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
                self._changed(tr("controller.primero_defini_el_plano_de_simetria_4"), scene=False)
            self.mirror_live = False
            return
        self.mirror_live = bool(on)
        if self.mirror_live:
            self.one_side = True
        self._changed(tr("controller.espejo_en_vivo_lo_que_haces") if self.mirror_live
                      else tr("controller.espejo_en_vivo_desactivado"))

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
        """Brazos con clic que no apoyan en el corte con la cama de impresión actual (ver `pipeline.flat_click_advice`). {} sin figura."""
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
            self._changed(tr("controller.ningun_brazo_con_clic_necesita_otro"), scene=False)
            return False
        angle = max(a["angulo_apoyo"] for a in adv.values())            # hoy hay un solo brazo con clic: el parámetro es global
        return self.set_param("detent.print_angle", round(angle))

    def flat_click_note(self) -> str:
        adv = self.flat_click_advice()
        if not adv:
            return ""
        a = next(iter(adv.values()))
        if a["angulo_apoyo"] is None:
            return (' ' + tr("controller.el_brazo_con_clic_cuelga_cuelga_2", cuelga_mm=f"{a['cuelga_mm']:.1f}"))
        return ((' ' + tr("controller.el_brazo_con_clic_cuelga_cuelga", cuelga_mm=f"{a['cuelga_mm']:.1f}", angulo_apoyo=f"{a['angulo_apoyo']:.0f}", angulo_actual=f"{a['angulo_actual']:.0f}")))

    def set_flat_fill_hip(self, on: bool) -> None:
        """Casilla «Rellenar los muslos hasta la pelvis recortada»: solo cambia la exportación sin soportes (no invalida la figura)."""
        self.session.project.params.flat_fill_hip = bool(on)
        self._changed(tr("controller.muslos_rellenos_hasta_la_pelvis_recortada") if on
                      else tr("controller.los_muslos_se_exportan_sin_relleno"), scene=False)

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
            txt = tr("controller.pose_a_angle_sin_colisiones", angle=f"{r['angle']:.0f}")
        else:
            parts = []
            if r["torso"]:
                parts.append((tr("controller.choca_con_el_torso") + ' ') + ", ".join(f"{n} ({v:.2f} mm³)" for n, v in r["torso"].items()))
            if r["pairs"]:
                parts.append((tr("controller.se_cruzan") + ' ') + ", ".join(tr("controller.a_con_b_v_mm3", a=a, b=b, v=f"{v:.2f}") for (a, b), v in r["pairs"].items()))
            txt = (tr("controller.pose_a_angle", angle=f"{r['angle']:.0f}") + ' ') + "; ".join(parts)
        if r["limit"] is not None and r["angle"] > r["limit"] + 1e-6:
            txt += (' ' + tr("controller.pasa_del_limite_de_cadera_tallado", limit=f"{r['limit']:.0f}"))
        if r.get("estiradas"):
            txt += (' ' + tr("controller.piernas_estiradas_como_se_imprimen_sin"))
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
            self._changed(tr("controller.vista_previa_de_la_pose_terminada") if was else None)
            return
        if self.session.outcome is None:
            self.pose_view = False
            self._changed(tr("controller.primero_genera_la_figura_la_vista"), scene=False)
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
            self._changed(tr("controller.primero_genera_la_figura_el_corte"),
                          scene=False)
            return
        self.set_pose_view(True)
        self.mode = Mode.FLAT_PLANE
        self.pose_report = self.pose_check()         # piernas estiradas mientras se ajusta el corte
        self.camera.frame(self.session.work.bounds)
        self.camera.set_view("derecha")
        self._changed(self.flat_message(self.session.project.params.flat_depth) +
                      (' ' + tr("controller.hace_clic_a_la_altura_que")))

    def flat_message(self, depth: float) -> str:
        return tr("controller.corte_con_la_cama_de_impresion_2", depth=f"{depth:.1f}", v=f"{self.flat_base_z() + depth:.1f}")

    def set_flat_depth(self, depth: float) -> float | None:
        """Fija la altura del corte (mm sobre la cola), dentro del rango de `Parámetros`. No invalida la figura."""
        spec = next(f for f in PARAM_FIELDS if f[0] == "flat_depth")
        lo, hi = spec[2], spec[3]
        wanted = float(depth)
        d = float(np.clip(wanted, lo, hi))
        self.session.project.params.flat_depth = round(d, 3)
        note = ""
        if abs(d - wanted) > 1e-9:
            note = (' ' + tr("controller.el_rango_es_lo_hi_mm", lo=f"{lo:g}", hi=f"{hi:g}"))
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
            self._changed(tr("controller.mira_la_figura_de_costado_boton"),
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
                        "label": tr("controller.trim_entry_label", n=i + 1, op=loc(TRIM_OP_LABELS.get(t.op, t.op)).capitalize(),
                                    shape=loc(TRIM_SHAPE_LABELS.get(t.shape, t.shape)), target=t.target)})
        return out

    def _check_trim_args(self, target: str, op: str, length: float) -> str | None:
        """Motivo por el que los datos de un recorte no sirven, o None."""
        if self.session.project.symmetry is None:
            return tr("controller.primero_defini_el_plano_de_simetria")
        if target not in self.trim_targets():
            return tr("controller.no_existe_la_pieza_target_usa", target=target)
        if op not in TRIM_OP_LABELS:
            return tr("controller.la_operacion_debe_ser_subtract_restar")
        if not (0.1 <= float(length) <= 500.0):
            return tr("controller.la_distancia_de_extrusion_tiene_que")
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
                self._changed(tr("controller.no_existe_el_recorte_v", v=index + 1), scene=False)
                return
            t = trims[index]
            if t.shape != "polygon":
                self._changed(tr("controller.solo_los_recortes_de_poligono_se"), scene=False)
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
        what = tr("controller.op_en_target", op=TRIM_OP_LABELS[op], target=target)
        self._changed((tr("controller.recorte_what", what=what) + ' ') + (
            tr("controller.clic_en_un_vertice_para_elegirlo")
            if len(poly) >= 3 else
            (tr("controller.hace_clic_para_poner_vertices_sobre") + ' ') + (tr("controller.hacia_ambos_lados") if symmetric else tr("controller.hacia_x")) + f" {distance:g} mm."))

    def _commit_trim(self, what: str) -> bool:
        d, cfg = self.draft, self.trim_cfg
        err = d.validity_error()
        if err:
            self._changed(tr("controller.what_pero_el_poligono_err_corregilo", what=what, err=err))
            return False
        world = d.world()
        trims = self.session.project.trims
        if self.edit_trim is None:
            self.session.add_trim(Trim(cfg["target"], d.plane, world, cfg["distance"], cfg["symmetric"], cfg["op"]))
            self.edit_trim = len(trims) - 1
            self._changed(tr("controller.what_recorte_v_guardado_trim_op", what=what, v=self.edit_trim + 1, trim_op_labels=TRIM_OP_LABELS[cfg['op']], target=cfg['target']))
        else:
            trims[self.edit_trim].polygon = world
            self.session.outcome = None
            self._changed(tr("controller.what_recorte_v_actualizado", what=what, v=self.edit_trim + 1))
        return True

    def add_trim_primitive(self, target: str, shape: str, center, size, op: str = "subtract",
                           length: float = 25.0) -> int | None:
        """Agrega un recorte de forma predefinida centrado en `center` (marco ``work``): esfera de radio `size`, caja
        de arista `size` (o de 3 aristas x, y, z) o cilindro de radio `size` con el eje en X y `length` de largo.
        Devuelve su índice, o None (con el motivo en `message`) si no sirve."""
        if shape not in ("sphere", "box", "cylinder"):
            self._changed(tr("controller.forma_desconocida_usa_esfera_caja_o"),
                          scene=False)
            return None
        err = self._check_trim_args(target, op, length)
        if err is None and not np.all(np.asarray(size, float) > 0):
            err = tr("controller.el_tamano_tiene_que_ser_positivo")
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
        self._changed(tr("controller.recorte_v_agregado_trim_op_labels", v=idx + 1, trim_op_labels=TRIM_OP_LABELS[op], trim_shape_labels=TRIM_SHAPE_LABELS[shape], target=target))
        return idx

    def start_trim_pick(self, target: str, shape: str, size, op: str = "subtract", length: float = 25.0) -> None:
        """Un clic sobre la figura pone el centro de un recorte esfera / caja / cilindro (después se puede quitar o
        cambiar de operación en la lista)."""
        if not self._need_loaded():
            return
        if shape not in ("sphere", "box", "cylinder"):
            self._changed(tr("controller.elegi_esfera_caja_o_cilindro"), scene=False)
            return
        err = self._check_trim_args(target, op, length)
        if err:
            self._changed(err + ".", scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.TRIM_PICK
        self.trim_cfg = {"target": target, "op": op, "distance": float(length), "symmetric": True,
                         "shape": shape, "size": size}
        self._changed(tr("controller.recorte_trim_shape_labels_en_target", trim_shape_labels=TRIM_SHAPE_LABELS[shape], target=target))

    def _sync_joints(self, scene) -> None:
        """Dibuja el eje de cada bisagra como una línea amarilla (sin prueba de profundidad)."""
        wanted = set()
        for j in self.session.project.joints:
            key = f"eje:{j.name}"
            wanted.add(key)
            c, d = np.asarray(j.axis_center, float), np.asarray(j.axis_dir, float)
            d = d / np.linalg.norm(d)
            scene.set_lines(key, np.array([c - d * 9.0, c + d * 9.0]), (1.0, 0.85, 0.1, 1.0), on_top=True)
            scene.show(key, self.group_visible("pins"))
        for key in [k for k in scene.items if k.startswith("eje:") and k not in wanted]:
            scene.remove(key)

    def _finish_trim_pick(self, hit: PickResult) -> None:
        cfg = self.trim_cfg
        center = np.asarray(hit.point, float).copy()
        center[0] = abs(center[0])        # el lado de trabajo es +X: lo que se elige del otro lado se espeja
        idx = self.add_trim_primitive(cfg["target"], cfg["shape"], center, cfg["size"], cfg["op"], cfg["distance"])
        if idx is not None:
            self.cancel(quiet=True)
            self._changed(self.message + (' ' + tr("controller.genera_de_nuevo_para_ver_el")))

    def remove_trim(self, index: int) -> None:
        if not (0 <= index < len(self.session.project.trims)):
            self._changed(tr("controller.no_existe_el_recorte_v", v=index + 1), scene=False)
            return
        if self.trim_cfg is not None:
            self.cancel(quiet=True)
        self.session.remove_trim(index)
        self._changed(tr("controller.recorte_v_quitado_genera_de_nuevo", v=index + 1))

    def set_trim_op(self, index: int, op: str) -> None:
        trims = self.session.project.trims
        if not (0 <= index < len(trims)) or op not in TRIM_OP_LABELS:
            self._changed(tr("controller.recorte_u_operacion_invalidos"), scene=False)
            return
        if trims[index].op != op:
            trims[index].op = op
            self.session.outcome = None
        self._changed(tr("controller.recorte_v_trim_op_labels_genera", v=index + 1, trim_op_labels=TRIM_OP_LABELS[op]))

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
            return tr("controller.primero_abri_un_stl")
        _mask, res = found
        if self.pose_active():
            area = sum(float(m.area_faces[mk].sum()) for m, mk in self._pose_overhang_cache())
            return (tr("controller.voladizos_en_rojo_pared_a_mas_2", overhang_angle=f"{self.overhang_angle:.0f}", pose_angle=f"{self._pose_angle():.0f}", area=f"{area:.0f}"))
        return (tr("controller.voladizos_en_rojo_pared_a_mas", overhang_angle=f"{self.overhang_angle:.0f}", overhang_area=f"{res.overhang_area:.0f}"))

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
                self.mode in (Mode.SYMMETRY, Mode.LIMB_PLANE, Mode.NECK_PLANE, Mode.HEAD_CUT_PLANE, Mode.WEAPON_CUT_PLANE, Mode.WAIST_PLANE, Mode.HAND_EXTRACT,
                              Mode.HAND_PLACE, Mode.JOINT_PLANE, Mode.JOINT_LINES, Mode.JOINT_SIDE, Mode.JOINT_AXIS))

    def update_hover(self, px: float, py: float, width: float, height: float) -> bool:
        """Marca (en rojo) el vértice de la figura que el puntero tiene cerca; lo suelta al alejarse. Devuelve True si
        cambió lo que hay que dibujar."""
        if self._pin_aim_active():                   # modo PIN con catálogo: la mira lee la cara bajo el puntero
            return self._update_pin_aim(px, py, width, height)
        if self.ghost_active():                      # modo PIN sin catálogo: el fantasma sigue al puntero
            return self._update_pin_hover(px, py, width, height)
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
        aim = self._clear_pin_aim()
        if self.hover_vertex is None:
            return aim
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
        if self.mode == Mode.WAIST_PIN and self.waist_pin_prev is not None:
            self._click_waist_pin(px, py, width, height)
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
            self._changed(tr("controller.el_clic_no_toco_la_figura"), scene=False)
            return None
        if self.mode == Mode.CROWN:
            self._finish_crown(hit)
        elif self.mode == Mode.TRIM_PICK:
            self._finish_trim_pick(hit)
        elif self.mode == Mode.JOINT_AXIS:
            self._finish_joint_axis(hit)
        elif self.mode == Mode.FLOOR_CUT:
            self._finish_floor_cut_pick(hit)
        else:
            self.pending.append(hit.point.copy())
            need = 4 if self.mode in (Mode.JOINT_LINES, Mode.JOINT_SIDE) else 3
            if len(self.pending) < need:
                if self.mode == Mode.JOINT_LINES:
                    self._changed(self._joint_lines_hint())
                elif self.mode == Mode.JOINT_SIDE:
                    self._changed(tr("controller.guia_de_costado_punto_n_4", n=len(self.pending)))
                else:
                    self._changed(tr("controller.punto_n_3_backspace_deshace_el", n=len(self.pending)))
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
            elif self.mode == Mode.WEAPON_CUT_PLANE:
                self._finish_weapon_cut_plane()
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
        self._changed(tr("controller.coronilla_marcada_a_z_mm_la", z=f"{z:.1f}", ref_height=f"{self.session.project.params.ref_height:g}"))

    def _finish_symmetry(self) -> None:
        pts, self.pending = self.pending, []
        try:
            plane = Session.plane_from_points(pts, "simetría")
            self.session.set_symmetry(plane)
        except ValueError as e:
            self._changed(tr("controller.no_sirve_e_elegi_3_puntos", e=e))
            return
        self._rebuild_picker()
        self.mode = Mode.VIEW
        self._changed(tr("controller.plano_de_simetria_definido_la_figura"))

    def _finish_neck_plane(self) -> None:
        pts, self.pending = self.pending, []
        try:
            self.session.set_head(Session.plane_from_points(pts, HEAD_NAME))
        except ValueError as e:
            self._changed(tr("controller.no_sirve_e_elegi_3_puntos", e=e))
            return
        self.mode = Mode.VIEW
        self._changed(tr("controller.plano_del_cuello_definido_la_cabeza"))

    def _finish_waist_plane(self) -> None:
        pts, self.pending = self.pending, []
        try:
            self.session.set_waist(Session.plane_from_points(pts, WAIST_NAME))
        except ValueError as e:
            self._changed(tr("controller.no_sirve_e_elegi_3_puntos", e=e))
            return
        self.mode = Mode.VIEW
        self._changed(tr("controller.plano_de_la_cintura_definido_el"))

    # ------------------------------------------- corte paralelo al piso (cabeza / cintura)
    def start_floor_cut(self, place: str) -> None:
        """Corta la cabeza (`place` = «head») o la cintura («waist») con un plano paralelo al piso de la figura. Es lo mismo
        que el plano de simetría trasladado («Corte a») para brazos y piernas, pero tomando el piso: el plano nace a una
        altura razonable (o conserva la que ya tenía) y se traslada en paralelo con «Altura sobre el piso», con un clic sobre
        la figura o con ↑ ↓. Si ya había un plano de 3 puntos inclinado, lo reemplaza."""
        if place not in FLOOR_CUT_PLACES:
            raise ValueError(place)
        if not self._need_loaded():
            return
        if self.session.project.symmetry is None:
            self._changed(tr("controller.primero_defini_el_plano_de_simetria_3"), scene=False)
            return
        self.cancel(quiet=True)
        try:
            if self.session.floor_cut_height(place) is None:
                self.session.set_floor_cut(place, self.session.default_floor_cut_height(place))
        except ValueError as e:
            self._changed(tr("controller.no_sirve_error", error=e))
            return
        self.mode, self.pending_kind = Mode.FLOOR_CUT, place
        self._changed(self.floor_cut_message(place) + " " + tr("controller.hace_clic_sobre_la_figura_a_la_altura"))

    def floor_cut_message(self, place: str) -> str:
        h = self.session.floor_cut_height(place)
        if place == "head":
            return tr("controller.corte_de_la_cabeza_cuello_plano_paralelo", h=f"{h:.1f}")
        return tr("controller.corte_de_la_cintura_plano_paralelo_al", h=f"{h:.1f}")

    def set_floor_cut_height(self, place: str, mm: float) -> None:
        """Traslada en paralelo el plano de corte de `place` a `mm` mm sobre el piso (la cara del corte se mueve: el pasador vuelve a su posición automática)."""
        try:
            self.session.set_floor_cut(place, mm)
        except ValueError as e:
            self._changed(f"{e}.", scene=False)
            return
        self._changed(self.floor_cut_message(place) + " " + tr("controller.revisa_el_pasador"))

    def nudge_floor_cut(self, direction: int, big: bool = False) -> None:
        """↑ sube el plano, ↓ lo baja `FLOOR_CUT_NUDGE_MM` (Mayús = 10 veces más), sin salirse del rango de la figura."""
        if self.mode != Mode.FLOOR_CUT or self.pending_kind is None:
            return
        h = self.session.floor_cut_height(self.pending_kind)
        if h is None:
            return
        lo, hi = self.session.floor_cut_range()
        step = FLOOR_CUT_NUDGE_MM * (10.0 if big else 1.0) * (1 if direction > 0 else -1)
        self.set_floor_cut_height(self.pending_kind, float(np.clip(h + step, lo, hi)))

    def _finish_floor_cut_pick(self, hit: PickResult) -> None:
        """La altura es la z del punto tocado sobre la figura (desde cualquier vista). El modo sigue activo para reajustar."""
        if self.pending_kind is not None:
            lo, hi = self.session.floor_cut_range()
            self.set_floor_cut_height(self.pending_kind, float(np.clip(hit.point[2], lo, hi)))

    # ------------------------------------------------------------ mano estándar (extraer)
    def start_hand_extract(self) -> None:
        if not self._need_loaded():
            return
        if self.session.project.symmetry is None:
            self._changed(tr("controller.primero_defini_el_plano_de_simetria_3"), scene=False)
            return
        self.cancel(quiet=True)
        self.hand_extract = self._hand_mesh = None
        self.mode = Mode.HAND_EXTRACT
        self._changed(tr("controller.mano_estandar_elegi_3_puntos_en"))

    def _finish_hand_extract(self) -> None:
        pts, self.pending = self.pending, []
        self._run_hand_extract([tuple(map(float, q)) for q in pts], invert=False)

    def _run_hand_extract(self, points, invert: bool) -> bool:
        try:
            hand, split = self.session.extract_hand(points, invert=invert)
        except ValueError as e:
            self.mode = Mode.VIEW
            self.hand_extract = self._hand_mesh = None
            self._changed(tr("controller.no_sirve_e_proba_de_nuevo", e=e))
            return False
        self.hand_extract = {"hand": hand, "split": split, "points": points, "invert": invert}
        self._hand_mesh = None
        self.mode = Mode.VIEW
        side = "izquierda" if split.frame.center[0] > 0 else "derecha"
        self._changed(tr("controller.mano_side_cortada_verde_muneca_v", side=side, v=f"{2 * split.face_radius:.1f}", hand_volume=f"{split.hand_volume:.0f}"))
        return True

    def invert_hand_extract(self) -> None:
        """Vuelve a cortar con los mismos 3 puntos tomando el otro lado del plano como mano."""
        if self.hand_extract is None:
            self._changed(tr("controller.primero_extrae_una_mano"), scene=False)
            return
        self._run_hand_extract(self.hand_extract["points"], invert=not self.hand_extract["invert"])

    def discard_hand_extract(self) -> None:
        self.hand_extract = self._hand_mesh = None
        self._changed(tr("controller.mano_extraida_descartada"))

    def save_hand_extract(self, path: str) -> dict | None:
        """Guarda la mano extraída como mano estándar (STL + `.mano.json` al lado)."""
        if self.hand_extract is None:
            self._changed(tr("controller.primero_extrae_una_mano"), scene=False)
            return None
        meta = self.session.save_hand(path, self.hand_extract["hand"], self.hand_extract["split"])
        msg = tr("controller.mano_estandar_guardada_v_figura_a", v=path.split('/')[-1], altura_figura_mm=f"{meta['altura_figura_mm']:g}")
        from ..hand import LINE_HEIGHT_MM
        if abs(float(meta["altura_figura_mm"]) - LINE_HEIGHT_MM) > 0.5:
            msg += (' ' + tr("controller.aviso_la_linea_usa_figuras_de", line_height_mm=f"{LINE_HEIGHT_MM:g}"))
        self._changed(msg, scene=False)
        return meta

    # ------------------------------------------------------------------ agarre para accesorios (afm.props)
    def set_grip_param(self, key: str, value: float) -> bool:
        from dataclasses import replace

        spec = next((f for f in GRIP_FIELDS if f[0] == key), None)
        if spec is None or not (spec[2] <= value <= spec[3]):
            return False
        self.grip_spec = replace(self.grip_spec, **{key: float(value)})
        self._changed(scene=False)
        return True

    def set_grip_axis(self, key: str, value: float) -> bool:
        """Canal (1–4), inclinación fina (°) o lado de la inclinación (°) del agarre; se aplican al volver a taladrar."""
        spec = next((f for f in GRIP_AXIS_FIELDS if f[0] == key), None)
        if spec is None or not (spec[2] <= value <= spec[3]):
            return False
        setattr(self, key, int(round(value)) if key == "grip_channel" else float(value))
        self._changed(scene=False)
        return True

    @property
    def grip_axis_stale(self) -> bool:
        """La elección de canal o inclinación pedida ya no es la de la mano taladrada."""
        if self.grip is None:
            return False
        ch = (self.grip.meta.get("agarre") or {}).get("eleccion") or {"canal": 1, "inclinar_grados": 0.0, "azimut_grados": 0.0}
        return (int(ch["canal"]) != self.grip_channel or abs(float(ch["inclinar_grados"]) - self.grip_tilt) > 1e-9
                or (self.grip_tilt != 0.0 and abs(float(ch["azimut_grados"]) - self.grip_azimuth) > 1e-9))

    def set_grip_barbs(self, text: str) -> bool:
        from ..props import parse_barbs

        try:
            self.grip_barbs = parse_barbs(text)
        except ValueError as e:
            self._changed(tr("controller.dentados_e", e=e), scene=False)
            return False
        self._changed(scene=False)
        return True

    @property
    def grip_stale(self) -> bool:
        """El Ø del taladro pedido ya no es el de la mano taladrada: hay que volver a taladrar para guardarla."""
        return self.grip is not None and abs(self.grip.spec.bore_d - self.grip_spec.bore_d) > 1e-9

    def begin_grip(self, path: str):
        """Valida y devuelve el trabajo pesado ``job(progress=None) -> GripResult`` (busca el canal y taladra la mano estándar
        de `path`), apto para un hilo; después hay que llamar `finish_grip`."""
        if self.busy:
            return None
        self.busy = True
        self.progress_prefix = tr("controller.taladrando_la_mano")
        self._changed(tr("controller.taladrando_la_mano_estandar_busca_el"), scene=False)
        spec = self.grip_spec
        canal, inclinar, azimut = self.grip_channel, self.grip_tilt, self.grip_azimuth

        def job(progress=None):
            from ..props import compute_grip

            return compute_grip(path, spec, progress, canal=canal, inclinar=inclinar, azimut=azimut)

        return job

    def finish_grip(self, result, error: Exception | None = None) -> bool:
        self.busy = False
        if error is not None:
            self._changed(tr("controller.no_se_pudo_taladrar_la_mano", error=error), scene=False)
            return False
        self.grip = result
        self._changed(self.grip_summary(), scene=False)
        return True

    def run_grip(self, path: str):
        """Taladra la mano con los valores actuales sin hilos (pruebas y uso programático)."""
        job = self.begin_grip(path)
        if job is None:
            return None
        try:
            res = job()
        except Exception as e:
            self.finish_grip(None, e)
            return None
        self.finish_grip(res)
        return res

    def grip_summary(self) -> str:
        """Texto para la sección «Agarre»: lo medido en la mano taladrada, los dentados y los avisos."""
        from ..props import grip_variants

        g = self.grip
        if g is None:
            return tr("controller.no_se_puede_dibujar_la_espada", grip_prop_error=self._grip_prop_error) if self.grip_show and self._grip_prop_error else ""
        f = g.fit
        lines = [tr("controller.taladro_bore_d_mm_canal_natural", bore_d=f"{f.bore_d:g}", free_d=f"{g.channel.free_d:.1f}", length=f"{f.length:.1f}", wall_min=f"{f.wall_min:.1f}", support_deg=f"{f.support_deg:.0f}")]
        lines += [tr("controller.dentado_dentado_mm_interferencia_interfere", dentado_mm=f"{v['dentado_mm']:g}", interferencia_mm=f"{v['interferencia_mm']:.2f}", deformacion_pct=f"{v['deformacion_pct']:.2f}") for v in grip_variants(g.spec, self.grip_barbs)]
        if f.support_deg < 270:
            lines.append(tr("controller.aviso_el_hombro_apoya_solo_en"))
        ch = (g.meta.get("agarre") or {}).get("eleccion")
        if ch:
            lines.append(tr("controller.canal_n_inclinado_hacia", canal=ch["canal"], inclinar=f"{ch['inclinar_grados']:g}", azimut=f"{ch['azimut_grados']:g}"))
        if self.grip_stale:
            lines.append(tr("controller.el_del_taladro_cambio_volve_a"))
        if self.grip_axis_stale:
            lines.append(tr("controller.el_canal_o_la_inclinacion_cambio"))
        if self.grip_show and self._grip_prop_error:
            lines.append(tr("controller.no_se_puede_dibujar_la_espada", grip_prop_error=self._grip_prop_error))
        return "\n".join(lines)

    def _hand_grip_fit(self):
        """Ajuste del agarre de la mano pegada (clave «agarre» de su .mano.json) o None si no tiene; cacheado por archivo."""
        import os

        spec = self.session.project.hand
        if spec is None:
            return None
        try:
            key = (spec.path, os.stat(spec.path).st_mtime_ns)
        except OSError:
            return None
        if self._grip_fit_cache[0] != key:
            from ..hand import load_standard_hand
            from ..props import GripFit

            fit = None
            try:
                ag = (load_standard_hand(spec.path)[1] or {}).get("agarre")
                fit = GripFit.from_dict(ag) if ag else None
            except Exception:                       # noqa: BLE001 — una mano ilegible simplemente no tiene agarre
                fit = None
            self._grip_fit_cache = (key, fit)
        return self._grip_fit_cache[1]

    def set_grip_show(self, on: bool) -> bool:
        """Muestra u oculta una espada de prueba en el puño de la mano pegada (necesita una mano con agarre)."""
        if on and self._hand_grip_fit() is None:
            self.grip_show = False
            self._changed(tr("controller.la_mano_pegada_no_tiene_agarre"), scene=False)
            return False
        self.grip_show = bool(on)
        self._grip_prop_error = ""
        self._changed()
        return True

    # ------------------------------------------------------------------ previsualizar el agarre antes de taladrar
    def _grip_preview_source(self):
        """(tipo, id1, id2) de la mano a previsualizar: la pegada a la figura, o si no hay, la del catálogo elegida; None si no hay ninguna."""
        import os

        spec = self.session.project.hand if self.loaded else None
        if spec is not None:
            try:
                return ("pegada", spec.path, os.stat(spec.path).st_mtime_ns)
            except OSError:
                return None
        if self.grip_preview_catalog is not None:
            return ("catalogo",) + tuple(self.grip_preview_catalog)
        return None

    def _grip_preview_key(self):
        """Clave de lo que se pidió previsualizar (mano + elección + Ø), o None sin mano."""
        src = self._grip_preview_source()
        if src is None:
            return None
        return src + (self.grip_channel, round(self.grip_tilt, 6), round(self.grip_azimuth, 6), self.grip_spec.bore_d)

    def grip_preview_catalog_hand(self):
        """(raíz, ref) de la mano más reciente del catálogo abierto, o None si no hay catálogo o no tiene manos."""
        from ..catalog import Catalog, CatalogError

        root = self.weapon_catalog or self.session.catalog_root or GUI_SETTINGS.last_catalog()
        if not root:
            return None
        try:
            hands = Catalog.open(root).list(tipo="mano")
        except (CatalogError, OSError):
            return None
        return (root, hands[-1].ref) if hands else None

    @property
    def grip_preview_standalone(self) -> bool:
        """La previsualización va con la mano del catálogo, sola en el visor (no hay mano pegada)."""
        return self.grip_preview_on and self._grip_preview_source() is not None and self._grip_preview_source()[0] == "catalogo"

    @property
    def grip_preview_stale(self) -> bool:
        """Hay que recalcular la previsualización: se pidió y no coincide con lo elegido ahora."""
        if not self.grip_preview_on or self._grip_preview_key() is None:
            return False
        pv = self._grip_preview
        return pv is None or pv["key"] != self._grip_preview_key()

    def set_grip_preview(self, on: bool, catalog_hand: tuple | None = None) -> bool:
        """Activa o apaga la previsualización del agarre (la calcula `begin_grip_preview`, en un hilo). Sin mano pegada hace falta
        `catalog_hand` = (raíz, ref) de una mano del catálogo (ver `grip_preview_catalog_hand`)."""
        if on and self.loaded and self.session.project.hand is not None:
            catalog_hand = None
        elif on and catalog_hand is None:
            self.grip_preview_on = False
            self._changed(tr("controller.para_previsualizar_el_agarre_pega_una_mano"), scene=False)
            return False
        self.grip_preview_catalog = tuple(catalog_hand) if (on and catalog_hand) else self.grip_preview_catalog
        self.grip_preview_on = bool(on)
        self._changed()
        return True

    def begin_grip_preview(self):
        """Trabajo pesado ``job(progress=None) -> dict`` (canales de la mano, taladro simulado en memoria y ajuste de la
        elección actual); None si no hace falta o hay otra tarea. Después hay que llamar `finish_grip_preview`."""
        key = self._grip_preview_key()
        if self.busy or key is None or not self.grip_preview_on:
            return None
        self.busy = True
        self.progress_prefix = tr("controller.previsualizando_el_agarre")
        self._changed(tr("controller.buscando_canales_y_calculando_agarre"), scene=False)
        spec, canal, tilt, az = self.grip_spec, self.grip_channel, self.grip_tilt, self.grip_azimuth
        barbs = self.grip_barbs
        cached = self._grip_channels
        hand_key = key[:3] + (spec.bore_d,)

        def job(progress=None):
            from .. import hand as H
            from ..props import GripError, carve_grip, channel_along, find_channel_options, tilt_axis
            from ..hand import HandError

            say = progress or (lambda text: None)
            if key[0] == "pegada":
                hand, _ = H.load_standard_hand(key[1])
            else:
                from ..catalog import Catalog

                _meta, mesh = Catalog.open(key[1]).load(key[2])
                hand = S.to_manifold(mesh)
            opts = cached[1] if cached and cached[0] == hand_key else None
            out = {"key": key, "hand_key": hand_key, "error": ""}
            try:
                if opts is None:
                    say(tr("controller.previsualizando_el_agarre"))
                    opts = find_channel_options(hand, spec.bore_d)
                out["channels"] = opts
                if canal > len(opts):
                    raise GripError(f"la mano solo tiene {len(opts)} canales candidatos (se pidió el {canal})")
                ch = opts[canal - 1]
                if tilt:
                    ch = channel_along(hand, tilt_axis(ch.axis, tilt, az), spec.bore_d)
                carved, fit = carve_grip(hand, spec, ch)
                out["hand"], out["fit"] = S.to_trimesh(carved), fit
                out["prop_error"] = ""
                try:                                          # la espada de prueba puede no caber con estos vástago/lengüeta/largo
                    from dataclasses import replace

                    from ..props import make_prop

                    make_prop(replace(spec, bore_d=fit.bore_d, barb_d=barbs[len(barbs) // 2]), fit.length)
                except GripError as exc:
                    out["prop_error"] = str(exc)
            except (GripError, HandError) as exc:
                out["error"] = str(exc)
            return out

        return job

    def finish_grip_preview(self, result, error: Exception | None = None) -> bool:
        self.busy = False
        if error is not None:                                # con la clave actual: así no se reintenta en bucle lo que falla
            self._grip_preview = {"key": self._grip_preview_key(), "error": str(error)}
            self._changed(tr("controller.no_se_puede_previsualizar_el_agarre", error=error), scene=True)
            return False
        if "channels" in result:
            self._grip_channels = (result["hand_key"], result["channels"])
        self._grip_preview = result
        if result["error"]:
            self._changed(tr("controller.no_se_puede_previsualizar_el_agarre", error=result["error"]), scene=True)
            return False
        f = result["fit"]
        self._changed(tr("controller.previsualizacion_agarre_lista", canal=self.grip_channel, n=len(result["channels"]),
                         inclinar=f"{self.grip_tilt:g}", azimut=f"{self.grip_azimuth:g}", wall=f"{f.wall_min:.2f}", length=f"{f.length:.1f}")
                      + (" " + tr("controller.no_se_puede_dibujar_la_espada", grip_prop_error=result["prop_error"]) if result["prop_error"] else ""))
        return True

    def grip_preview_meshes(self, hfit):
        """(mano taladrada, espada de prueba) en el mundo para la previsualización vigente, o None si no hay una válida
        para la elección actual. Es lo que se dibuja en lugar de la mano pegada."""
        pv = self._grip_preview
        solo = self.grip_preview_standalone
        if not self.grip_preview_on or pv is None or pv["error"] or (not solo and (hfit is None or "matrix" not in hfit)):
            return None
        if pv["key"] != self._grip_preview_key():
            return None
        from dataclasses import replace

        from ..props import GripError, make_prop, prop_to_hand_matrix

        fit, m = pv["fit"], (np.eye(4) if solo else hfit["matrix"])
        hand = pv["hand"].copy()
        hand.apply_transform(m)
        spec = replace(self.grip_spec, bore_d=fit.bore_d, barb_d=self.grip_barbs[len(self.grip_barbs) // 2])
        try:
            prop = S.to_trimesh(make_prop(spec, fit.length))
        except GripError:
            return hand, None
        prop.apply_transform(m @ prop_to_hand_matrix(fit))
        return hand, prop

    def grip_prop_preview(self, hfit):
        """Malla (mundo) de la espada de prueba en el puño de la mano pegada, o None. `hfit` es `hand_fit()`: trae la matriz
        mano → mundo con el giro y la unión ya aplicados. Usa el dentado del medio de la lista y el vástago/ranura/lengüeta
        actuales; el Ø del taladro es el de la mano."""
        if not self.grip_show or hfit is None or "matrix" not in hfit:
            return None
        fit = self._hand_grip_fit()
        if fit is None:
            return None
        from dataclasses import asdict, replace

        from ..props import GripError, make_prop, prop_to_hand_matrix

        spec = replace(self.grip_spec, bore_d=fit.bore_d, barb_d=self.grip_barbs[len(self.grip_barbs) // 2])
        key = (tuple(asdict(spec).values()), round(fit.length, 6), hfit["matrix"].tobytes())
        if self._grip_prop_cache[0] == key:
            return self._grip_prop_cache[1]
        try:
            prop = make_prop(spec, fit.length)
        except GripError as e:
            self._grip_prop_error = str(e)
            return None
        self._grip_prop_error = ""
        mesh = S.to_trimesh(prop)
        mesh.apply_transform(hfit["matrix"] @ prop_to_hand_matrix(fit))
        self._grip_prop_cache = (key, mesh)
        return mesh

    # ------------------------------------------------------------------ armas: importar, escalar, cortar el mango, guardar en el catálogo
    def set_weapon_catalog(self, root: str) -> bool:
        """Elige el catálogo de la línea: de su mano con agarre sale la empuñadura de las armas, y de él los accesorios del proyecto."""
        from ..weapon import WeaponError
        from .weapon_session import WeaponSession

        ws = self.weapon or WeaponSession()
        try:
            ws.set_catalog(root)
        except WeaponError as exc:
            self._changed(str(exc), scene=False)
            return False
        self.weapon, self.weapon_catalog = ws, root
        self._changed(tr("controller.catalogo_name_las_armas_llevan_la", name=ws.catalog.name, ref=ws.hand.ref), scene=bool(self.weapon_active))
        return True

    def open_weapon(self, path: str) -> bool:
        """Carga un STL cualquiera como arma: lo muestra a escala (0,40 × la altura de la línea) para elegir el plano de corte."""
        from ..weapon import WeaponError
        from .weapon_session import WeaponSession

        if self._busy_for(tr("controller.el_arma")):
            return False
        ws = self.weapon or WeaponSession()
        root = self.weapon_catalog or self.session.catalog_root or GUI_SETTINGS.last_catalog()
        if ws.catalog is None and root:                          # el catálogo ya abierto (o el último usado) da la empuñadura de la línea
            try:
                ws.set_catalog(root)
                self.weapon_catalog = root
            except WeaponError:
                pass
        try:
            ws.load(path)
        except WeaponError as exc:
            self._changed(tr("controller.no_se_pudo_cargar_el_arma", exc=exc), scene=False)
            return False
        self.cancel(quiet=True)
        self.weapon, self.weapon_active = ws, True
        self.camera.frame(ws.scaled.bounds)
        self.camera.set_view("frente")
        self._changed(tr("controller.arma_cargada_v_ajusta_la_escala", v=ws.summary().splitlines()[0]))
        return True

    def close_weapon(self) -> None:
        """Vuelve el visor a la figura."""
        self.cancel(quiet=True)
        self.weapon_active = False
        if self.session.work is not None:
            self.camera.frame(self.session.work.bounds)
        self._changed(tr("controller.se_volvio_a_la_figura"))

    def set_weapon_scale(self, factor: float | None = None, length_mm: float | None = None) -> bool:
        from ..weapon import WeaponError

        if self.weapon is None or not self.weapon.loaded:
            self._changed(tr("controller.primero_carga_un_arma_stl"), scene=False)
            return False
        try:
            if factor is not None:
                self.weapon.set_scale(factor)
            elif length_mm is not None:
                self.weapon.set_length(length_mm)
        except WeaponError as exc:
            self._changed(str(exc), scene=False)
            return False
        self._changed(self.weapon.summary().splitlines()[0])
        return True

    def start_weapon_cut_plane(self) -> None:
        """Pide 3 puntos sobre el arma que definen el plano que le corta el mango original."""
        from .picking import Picker

        if self.weapon is None or not self.weapon.loaded:
            self._changed(tr("controller.primero_carga_un_arma_stl"), scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.WEAPON_CUT_PLANE
        self.picker = Picker(self.weapon.scaled)                  # los clics caen sobre el arma, no sobre la figura
        self._changed(tr("controller.corte_del_mango_elegi_3_puntos"))

    def _finish_weapon_cut_plane(self) -> None:
        pts, self.pending = self.pending, []
        try:
            self.weapon.set_cut(pts)
        except Exception as e:                                    # noqa: BLE001
            self._changed(tr("controller.no_sirve_e_elegi_3_puntos", e=e))
            return
        self.mode = Mode.VIEW
        self.picker = None
        self._changed(self.weapon.summary().replace("\n", " · "))

    def flip_weapon_cut(self) -> None:
        if self.weapon is None or not self.weapon.loaded or not self.weapon.spec.cut_points:
            self._changed(tr("controller.primero_elegi_el_plano_de_corte"), scene=False)
            return
        self.weapon.flip()
        self._changed(self.weapon.summary().replace("\n", " · "))

    def set_weapon_roll(self, degrees: float | None) -> None:
        """Giro del arma sobre el eje de la empuñadura; None = el que la deja más baja (acostada)."""
        if self.weapon is None or not self.weapon.loaded:
            return
        self.weapon.set_roll(degrees)
        self._changed(self.weapon.summary().replace("\n", " · "))

    def set_weapon_show_hand(self, on: bool) -> None:
        """Muestra u oculta la mano estándar sobre el mango del arma en el visor."""
        self.weapon_show_hand = bool(on)
        self._changed(scene=bool(self.weapon_active))

    def weapon_summary(self) -> str:
        return self.weapon.summary() if self.weapon is not None else ""

    def save_weapon(self, piece_id: str | None = None, new_version: bool = False) -> bool:
        """Guarda el arma preparada en el catálogo de la línea como pieza `accesorio`."""
        from ..catalog import CatalogError
        from ..weapon import WeaponError

        if self.weapon is None or not self.weapon.loaded:
            self._changed(tr("controller.primero_carga_un_arma_stl"), scene=False)
            return False
        try:
            meta, warns = self.weapon.save(piece_id or None, new_version)
        except (WeaponError, CatalogError, ValueError) as exc:
            self._changed(tr("controller.no_se_pudo_guardar_el_arma", exc=exc), scene=False)
            return False
        self._changed(tr("controller.arma_guardada_en_el_catalogo_como", ref=meta.ref) + "".join((' ' + tr("common.aviso_w", w=w)) for w in warns), scene=False)
        return True

    def _sync_weapon(self, scene) -> None:
        """El visor muestra el arma: el original a escala (translúcido si ya hay resultado), el resultado con la empuñadura
        nueva encima, el plano de corte y los puntos elegidos."""
        ws = self.weapon
        scene.clear()
        res = ws.result_mesh()
        scene.set_mesh("arma:original", ws.scaled, color=(0.72, 0.74, 0.80, 0.30 if res is not None else 1.0))
        if res is not None:
            scene.set_mesh("arma:resultado", res, color=_ACCESSORY_COLOR)
            hand = ws.hand_in_weapon() if self.weapon_show_hand else None
            if hand is not None:
                scene.set_mesh("arma:mano", hand, color=_HAND_COLOR)
        pl = ws.plane()
        if pl is not None:
            hs = float(np.max(ws.scaled.extents)) * 0.35
            scene.set_plane("arma:plano", pl, hs, center=pl.origin, color=(1.0, 0.55, 0.1, 0.25))
        if self.pending:
            pts = np.array(self.pending)
            scene.set_points("pendientes", pts, (1.0, 0.25, 0.25, 1.0), 14.0, on_top=True)
            scene.set_lines("pendientes:lineas", pts, (1.0, 0.25, 0.25, 1.0), closed=len(pts) == 3, on_top=True)
        if self.hover_vertex is not None and self.hover_active():
            scene.set_points("hover:vertice", self.hover_vertex[None], (1.0, 0.0, 0.0, 1.0), 18.0, on_top=True)

    # ------------------------------------------------------------------ color (FBX con textura, plan de color T25)
    COLOR_TEXEL_RANGE = (0.05, 1.0)                  # mm por texel del atlas de cada pieza
    COLOR_EPS_RANGE = (0.02, 1.0)                    # mm: distancia para decir que un punto «viene» de la figura original

    def color_spec(self):
        """`ColorSpec` del proyecto (None = la figura sale sin color)."""
        return self.session.project.color

    def _valid_color_fill(self, fill: str) -> bool:
        return fill == "extend" or bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", fill))

    def set_color(self, path: str | None = None, **kw) -> bool:
        """Activa el color con el FBX `path` (por defecto, el FBX abierto en modo A). `kw`: `texel_mm`, `eps`, `fill`, `symmetric`.
        No invalida la figura generada: el color no cambia la geometría."""
        from ..project import ColorSpec

        path = path or (self.session.report.color_source_path if self.session.report is not None else "")
        if not path or not os.path.isfile(path):
            self._changed(tr("controller.color_elegi_un_fbx_existente"), scene=False)
            return False
        base = self.color_spec()
        spec = ColorSpec.from_file(os.path.abspath(path), **({"eps": base.eps, "texel_mm": base.texel_mm, "fill": base.fill,
                                                              "symmetric": base.symmetric} if base else {}))
        self.session.project.color = spec
        if kw and not self.update_color(**kw):
            return False
        self.color_report = None
        self._changed(tr("controller.color_activado_con_ruta", ruta=os.path.basename(path)), scene=False)
        return True

    def update_color(self, **kw) -> bool:
        """Cambia `texel_mm`, `eps`, `fill` («extend» o «#RRGGBB») o `symmetric` del color. False (y el motivo en `message`) si no vale."""
        spec = self.color_spec()
        if spec is None:
            self._changed(tr("controller.color_primero_elegi_un_fbx"), scene=False)
            return False
        unknown = set(kw) - {"texel_mm", "eps", "fill", "symmetric"}
        if unknown:
            self._changed(tr("controller.color_campo_desconocido", campo=", ".join(sorted(unknown))), scene=False)
            return False
        for key, rng, label in (("texel_mm", self.COLOR_TEXEL_RANGE, tr("controller.color_texel")), ("eps", self.COLOR_EPS_RANGE, tr("controller.color_tolerancia"))):
            if key in kw and not (rng[0] - 1e-9 <= float(kw[key]) <= rng[1] + 1e-9):
                self._changed(tr("controller.label_value_esta_fuera_de_rango", label=label, value=f"{float(kw[key]):g}", lo=f"{rng[0]:g}", hi=f"{rng[1]:g}"), scene=False)
                return False
        if "fill" in kw and not self._valid_color_fill(str(kw["fill"])):
            self._changed(tr("controller.color_relleno_invalido", valor=kw["fill"]), scene=False)
            return False
        for key, value in kw.items():
            setattr(spec, key, bool(value) if key == "symmetric" else (str(value) if key == "fill" else float(value)))
        self._changed(tr("controller.color_actualizado"), scene=False)
        return True

    def set_show_texture(self, on: bool) -> bool:
        """Enciende o apaga la vista previa de la textura del FBX en el visor principal. Al encender alinea el FBX con la figura (en el
        modo B puede tardar unos segundos). False, y el motivo en `message`, si no se puede; no toca el proyecto ni lo que se exporta."""
        if not on:
            self.show_texture = False
            self._changed(tr("controller.textura_apagada"))
            return True
        try:
            self.session.preview_texture()
        except (ColorSourceError, FbxError, OSError, ValueError) as exc:
            self.show_texture = False
            self._changed(tr("controller.textura_no_disponible", motivo=str(exc)))
            return False
        self.show_texture = True
        self._changed(tr("controller.textura_activada"))
        return True

    def _texture_for_scene(self):
        """`TexturedPart` para el visor si la vista previa está encendida y la figura se dibuja tal cual; None = color plano. Un fallo
        (otro STL, FBX cambiado) apaga la vista previa y lo dice, sin romper el repintado."""
        if not self.show_texture:
            return None
        try:
            return self.session.preview_texture()
        except (ColorSourceError, FbxError, OSError, ValueError) as exc:
            self.show_texture = False
            self.message = tr("controller.textura_no_disponible", motivo=str(exc))
            return None

    def remove_color(self) -> bool:
        """«Quitar color»: la figura se exporta sin color (los STL no cambian). El FBX no se borra."""
        if self.color_spec() is None:
            return False
        self.show_texture = False
        self.session.project.color = None
        self.color_report = None
        self._changed(tr("controller.color_quitado"), scene=False)
        return True

    def relocate_color(self, path: str) -> bool:
        """El FBX de la fuente cambió de lugar: se acepta `path` solo si es el mismo archivo (hash guardado en el proyecto)."""
        spec = self.color_spec()
        if spec is None or not os.path.isfile(path):
            self._changed(tr("controller.color_elegi_un_fbx_existente"), scene=False)
            return False
        if not spec.matches(path):
            self._changed(tr("controller.color_no_es_el_mismo_fbx", ruta=os.path.basename(path)), scene=False)
            return False
        spec.source_path = os.path.abspath(path)
        self._changed(tr("controller.color_fbx_relocalizado", ruta=os.path.basename(path)), scene=False)
        return True

    def color_source_problem(self) -> str | None:
        """Por qué el color no se puede usar al exportar (FBX perdido o cambiado); None si está bien o no hay color."""
        if self.color_spec() is None:
            return None
        try:
            self.session.resolve_color_source()
        except ColorSourceError as exc:
            return str(exc)
        return None

    def color_report_rows(self) -> list[tuple[str, float, str]]:
        """(pieza, % con origen, estado) del último export con color, para la tabla del panel «Color»."""
        return color_rows(self.color_report)

    def color_summary(self) -> str:
        spec = self.color_spec()
        if spec is None:
            return tr("controller.color_sin_color")
        fill = tr("controller.color_relleno_extender") if spec.fill == "extend" else spec.fill
        side = tr("controller.color_lado_simetrico") if spec.symmetric else tr("controller.color_lado_original")
        return tr("controller.color_resumen", fbx=os.path.basename(spec.source_path), texel=f"{spec.texel_mm:g}", eps=f"{spec.eps:g}", relleno=fill, lado=side)

    # ------------------------------------------------------------------ accesorios del proyecto (viajan con la figura)
    def _accessory_catalog(self):
        """Catálogo de donde salen los accesorios: el elegido para armas, el abierto en la ventana o el último usado (None si no hay)."""
        from ..catalog import Catalog, CatalogError

        root = self.weapon_catalog or self.session.catalog_root or GUI_SETTINGS.last_catalog()
        if not root:
            return None
        try:
            return Catalog.open(root)
        except (CatalogError, OSError):
            return None

    def accessory_pieces(self) -> list:
        """Piezas «accesorio» del catálogo (todas las versiones)."""
        cat = self._accessory_catalog()
        if cat is None:
            return []
        return sorted((m for m in cat.list(all_versions=True) if m.tipo == "accesorio"), key=lambda m: (m.id, m.version))

    def project_accessory_labels(self) -> dict[str, str]:
        """{ref: «ok» | «aviso» | «no se sujeta» | …} de cada accesorio respecto de la mano pegada en este proyecto."""
        from ..accessories import project_accessory_fit
        from .figure_builder import FIT_TEXT

        return {m.ref: loc(FIT_TEXT.get(project_accessory_fit(self.session.project, m)["nivel"], "")) for m in self.accessory_pieces()}

    def set_project_accessory(self, ref: str, on: bool = True) -> bool:
        """Agrega o quita un accesorio del proyecto: sale con la figura al exportar y queda guardado en el `.proyecto`.
        Una sola versión por id. No invalida la figura generada: los accesorios no cambian las piezas."""
        import os

        from ..accessories import AccessoryRef

        if on:
            cat = self._accessory_catalog()
            if cat is None:
                self._changed(tr("controller.primero_abri_o_elegi_el_catalogo"), scene=False)
                return False
            try:
                meta = cat.meta(ref)
            except ValueError as exc:
                self._changed(str(exc), scene=False)
                return False
            if meta.tipo != "accesorio":
                self._changed(tr("controller.ref_es_una_pieza_tipo_no", ref=meta.ref, tipo=meta.tipo), scene=False)
                return False
            keep = [a for a in self.session.project.accesorios if a.pieza.split("@")[0] != meta.id]
            self.session.project.accesorios = keep + [AccessoryRef(os.path.abspath(cat.root), meta.ref, meta.sha256)]
            self._changed(tr("controller.ref_viaja_con_la_figura_sale", ref=meta.ref), scene=False)
        else:
            pid = ref.split("@")[0]
            self.session.project.accesorios = [a for a in self.session.project.accesorios if a.pieza.split("@")[0] != pid]
            self._changed(tr("controller.pid_ya_no_viaja_con_la", pid=pid), scene=False)
        return True

    def save_grip_hand(self, path: str) -> bool:
        from ..props import write_grip_hand

        if self.grip is None:
            self._changed(tr("controller.primero_taladra_una_mano_estandar"), scene=False)
            return False
        if self.grip_stale:
            self._changed(tr("controller.el_del_taladro_cambio_desde_que"), scene=False)
            return False
        write_grip_hand(self.grip, path)
        self._changed(tr("controller.mano_con_agarre_guardada_v_eje", v=path.split('/')[-1]), scene=False)
        return True

    def save_grip_plate(self, path: str) -> bool:
        from ..props import GripError, write_grip_plate

        if self.grip is None:
            self._changed(tr("controller.primero_taladra_una_mano_estandar"), scene=False)
            return False
        try:
            write_grip_plate(self.grip, path, self.grip_spec, self.grip_barbs)
        except GripError as e:
            self._changed(tr("controller.no_se_pudo_generar_la_placa", e=e), scene=False)
            return False
        self._changed(tr("controller.placa_de_espadas_de_prueba_guardada", v=path.split('/')[-1], n=len(self.grip_barbs)), scene=False)
        return True

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
            self._changed(tr("controller.primero_defini_el_plano_de_simetria_3"), scene=False)
            return
        from ..hand import load_standard_hand
        try:
            load_standard_hand(path)
        except Exception as e:                                  # archivo ilegible, no es una malla, JSON de otra versión…
            self._changed(tr("controller.no_se_pudo_leer_esa_mano", e=e), scene=False)
            return
        self.cancel(quiet=True)
        self._hand_path_pending = path
        self.mode = Mode.HAND_PLACE
        self._changed(tr("controller.mano_de_reemplazo_elegi_3_puntos"))

    def _finish_hand_place(self) -> None:
        pts, self.pending = self.pending, []
        path, self._hand_path_pending = self._hand_path_pending, None
        self.mode = Mode.VIEW
        try:
            info = self.session.set_hand(path, [tuple(map(float, q)) for q in pts])
        except ValueError as e:
            self._changed(tr("controller.no_sirve_e_proba_de_nuevo_2", e=e))
            return
        self._changed(self._hand_fit_message(info))

    @staticmethod
    def _hand_fit_message(info: dict) -> str:
        msg = (tr("controller.mano_estandar_pegada_turquesa_muneca_del", v=f"{2 * info['radio_brazo_mm']:.1f}", v2=f"{2 * info['radio_mano_mm']:.1f}"))
        pin = info.get("pasador")
        if pin:
            msg += ((' ' + tr("controller.articulada_pieza_aparte_con_pasador_vastag", vastago_mm=f"{pin['vastago_mm']:.1f}", cabeza_mm=f"{pin['cabeza_mm']:.1f}"))
                    + (f", ×{pin['escala']:.2f}" if pin["escala"] < 1 else "") + tr("controller.que_gira_libre_dentro_del_brazo"))
        for a in info["avisos"]:
            msg += (' ' + tr("controller.aviso_a", a=a))
        return msg

    def set_hand_adjust(self, yaw: float | None = None, invert: bool | None = None, join: str | None = None) -> bool:
        """Cambia el giro (°), el lado y/o la unión («solape» / «giro») de la mano de reemplazo ya pegada. Si no sirve, no
        cambia nada."""
        if not self._need_loaded():
            return False
        try:
            info = self.session.adjust_hand(yaw, invert, join)
        except ValueError as e:
            self._changed(tr("controller.no_sirve_e_2", e=e), scene=False)
            return False
        self._changed(self._hand_fit_message(info))
        return True

    def set_hand_swivel(self, on: bool) -> bool:
        """Mano articulada: pieza aparte con pasador que gira libre dentro del brazo (False = soldada al brazo)."""
        return self.set_hand_adjust(join="giro" if on else "solape")

    def invert_hand_place(self) -> bool:
        cur = self.session.project.hand
        return self.set_hand_adjust(invert=None if cur is None else not cur.invert)

    # ------------------------------------------------------------- catálogo de piezas (Fase 8)
    def open_catalog(self, path: str) -> bool:
        """Abre el catálogo de la línea; deja en el mensaje cuántas piezas trae y lo que difiere de esta figura."""
        try:
            cat = self.session.open_catalog(path)
        except ValueError as e:
            self._changed(str(e), scene=False)
            return False
        GUI_SETTINGS.remember_catalog(cat.name, cat.root)
        n = len(cat.list())
        warns = self.session.catalog_warnings()
        self._changed(tr("controller.catalogo_name_n_pieza_s", name=cat.name, n=n) + "".join((' ' + tr("controller.aviso_w", w=w)) for w in warns), scene=False)
        return True

    def default_catalog_line(self) -> tuple[str, float]:
        """Nombre y altura (mm) que propone «Crear catálogo…»: `proceres-<altura>` y la altura de la figura abierta."""
        h = float(self.session.project.params.ref_height)
        return f"proceres-{h:g}", h

    def create_catalog(self, path: str, name: str | None = None, height_mm: float | None = None) -> bool:
        """«Crear catálogo…» (= `afm catalogo crear RUTA --linea NOMBRE --altura MM`): lo crea en `path` y lo deja abierto."""
        try:
            cat = self.session.create_catalog(path, name, height_mm)
        except ValueError as e:
            self._changed(str(e), scene=False)
            return False
        GUI_SETTINGS.remember_catalog(cat.name, cat.root)
        warns = self.session.catalog_warnings()
        self._changed(tr("controller.catalogo_name_creado_en_root_altura", name=cat.name, root=cat.root, height_mm=f"{cat.height_mm:g}") + "".join((' ' + tr("controller.aviso_w", w=w)) for w in warns), scene=False)
        return True

    def _auto_catalog(self) -> str:
        """Reabre solo el catálogo al cargar una figura: el de la línea de las piezas que ya trae el proyecto (carpeta
        recordada por línea) o, si no trae piezas, el último que se usó. Devuelve un texto para el mensaje ("" si no abrió
        nada). Nunca falla: si la carpeta ya no sirve, simplemente no se abre."""
        sess = self.session
        if sess.catalog_root:
            return ""
        lines = sorted({r.linea for r in sess.project.parts.values() if r.linea})
        root = next((r for r in (GUI_SETTINGS.catalog_for_line(l) for l in lines) if r), None)
        if root is None and not sess.project.parts:
            root = GUI_SETTINGS.last_catalog()
        if root is None:
            return ("" if not lines else
                    (' ' + tr("controller.aviso_el_proyecto_usa_piezas_de", join=', '.join(lines))))
        try:
            cat = sess.open_catalog(root)
        except ValueError:
            return ""
        return (' ' + tr("controller.catalogo_name_reabierto_root", name=cat.name, root=root))

    def forget_catalog(self) -> None:
        """«Olvidar catálogo»: cierra el de esta sesión y borra lo recordado (no toca la carpeta ni el proyecto)."""
        GUI_SETTINGS.forget_catalog()
        self.session.catalog_root = None
        self.session._part_preview = None
        self._changed(tr("controller.catalogo_olvidado_la_proxima_vez_hay"), scene=False)

    # ---- revisión previa de las piezas (sin generar)
    def begin_preview_parts(self):
        """Devuelve el trabajo ``job(progress=None) -> dict`` que revisa las piezas elegidas sin generar (apto para un
        hilo; después `finish_preview_parts` en el hilo de la interfaz). None si no hay nada que revisar."""
        if self.busy:
            return None
        if not self._need_loaded():
            return None
        if not self.session.project.parts and not self.session.project.part_scales:
            self._changed(tr("controller.no_hay_piezas_elegidas_ni_divididas"), scene=False)
            return None
        self.busy = True
        self.progress_prefix = tr("controller.revisando")
        self.progress_stages = self.session.progress_plan("preview")
        self._changed(tr("controller.revisando_como_acoplan_las_piezas_elegidas"), scene=False)
        return lambda progress=None: self.session.preview_parts(progress)

    def finish_preview_parts(self, items: dict | None = None, error: Exception | None = None) -> None:
        self.busy = False
        self.progress_prefix = tr("controller.generando")
        if error is not None or items is None:
            self._register_plane_fault(error)
            self._changed(tr("controller.no_se_pudo_revisar_error_fault", error=error, fault_hint=self._fault_hint()))
            return
        self.plane_faults = {}
        self._changed(self.preview_message(items))

    def preview_parts(self) -> dict | None:
        """Revisión previa sin hilos (pruebas y uso programático). Devuelve {lugar: PartPreview} o None."""
        job = self.begin_preview_parts()
        if job is None:
            return None
        try:
            items = job()
        except Exception as e:                      # noqa: BLE001 — se informa en el estado
            self.finish_preview_parts(error=e)
            return None
        self.finish_preview_parts(items)
        return items

    @staticmethod
    def preview_message(items: dict) -> str:
        bad = [pv for pv in items.values() if pv.nivel == "no"]
        warn = [pv for pv in items.values() if pv.nivel == "aviso"]
        head = (tr("controller.revisadas_n_pieza_s_sin_generar", n=len(items), v=len(items) - len(bad) - len(warn), n2=len(warn), n3=len(bad)))
        detail = "".join(f" {pv.place}: {pv.problems()[0]}." for pv in bad + warn if pv.problems())
        return head + detail

    def needs_review_on_open(self) -> bool:
        """True si el proyecto abierto trae piezas de catálogo (o la figura dividida) y todavía no se dibujaron: la ventana
        las revisa sola al abrirlo, así el visor muestra las piezas reemplazadas como ya hace con la cabeza y la mano.
        Con piezas elegidas hace falta el catálogo abierto (si no, no se pueden leer; el mensaje de apertura ya avisa)."""
        s = self.session
        if self.busy or not self.loaded:
            return False
        if not (s.project.parts or s.project.part_scales):
            return False
        if s.project.parts and not s.catalog_root:
            return False
        return not s.part_preview_fresh()

    def _replacement_head_shown(self) -> bool:
        """True si se dibuja la cabeza de reemplazo (STL) en vez de la cabeza del modelo; salvo que la cabeza sea una pieza
        elegida del catálogo, la «cabeza» de la revisión es la original del modelo y no se dibuja encima."""
        head = self.session.project.head
        return head is not None and bool(head.replacement_path) and "cabeza" not in self.session.project.parts

    def parts_replace_figure(self) -> bool:
        """True si las piezas revisadas cubren toda la figura (torso incluido): en el visor reemplazan al modelo, que deja
        de dibujarse (igual que la cabeza o la mano de reemplazo ocultan las originales)."""
        if not (self.show_part_preview and self.outcome_free()):
            return False
        torso = self.session.part_preview_items().get("torso")
        return torso is not None and torso.mesh is not None

    def part_preview(self, place: str):
        """`PartPreview` vigente del lugar (o None si todavía no se revisó o cambió algo desde entonces)."""
        return self.session.part_preview_items().get(place)

    def set_show_part_preview(self, on: bool) -> None:
        if self.show_part_preview != bool(on):
            self.show_part_preview = bool(on)
            self._changed(scene=True)

    def fit_diagnosis(self, place: str) -> dict | None:
        """Todo lo que la ventana necesita para mostrar POR QUÉ la pieza combina o no con el lugar: las dos caras
        superpuestas (polígonos en (u, v) del puerto de la pieza) y las explicaciones en lenguaje llano. None si no hay
        revisión vigente de ese lugar."""
        pv = self.part_preview(place)
        if pv is None:
            return None
        from ..preflight import advice, overlay_polygons

        return {"lugar": place, "pieza": pv.ref, "nivel": pv.nivel, "resumen": pv.summary(),
                "caras": overlay_polygons(pv), "lineas": advice(pv, self.session), "escala": pv.scale_hint}

    def resolve_joint_conflict(self, place: str, keep: str = "part") -> bool:
        """Resuelve el conflicto de rodilla/codo de `place` (ver `Session.resolve_joint_conflict`)."""
        if not self._need_loaded():
            return False
        try:
            msg = self.session.resolve_joint_conflict(place, keep)
        except ValueError as e:
            self._changed(str(e), scene=False)
            return False
        self._changed(msg + (' ' + tr("controller.volve_a_revisar_las_piezas")))
        return True

    # ---- escalar la figura a la altura de la línea del catálogo
    def line_scale(self) -> float | None:
        """Factor que lleva esta figura a la altura de la línea del catálogo abierto (None si no hay catálogo)."""
        h = self.session.line_height()
        if h is None or not self.loaded:
            return None
        return h / float(self.session.project.params.ref_height)

    def scale_to_line(self) -> bool:
        """«Escalar a la altura de la línea»: la figura pasa a medir lo que la línea del catálogo (ver `set_ref_height`)."""
        h = self.session.line_height()
        if h is None:
            self._changed(tr("controller.primero_abri_el_catalogo_de_la"), scene=False)
            return False
        return self.set_ref_height(h)

    def proportion_figures(self) -> list[str]:
        """Figuras patrón que ofrece el comparador de proporciones: las del catálogo abierto con cuerpo (cabeza, torso, miembros)."""
        if not self.session.catalog_root:
            return []
        from .. import proportions as PR

        try:
            figs = PR.catalog_figures(self.session._catalog())
        except ValueError:
            return []
        return sorted(f for f, parts in figs.items() if {"torso", "brazo", "pierna"} <= set(parts))

    def proportions_job(self, figura: str):
        """Trabajo `job(progress) -> dict` del comparador (apto para un hilo): ver `Session.proportions_snapshot`. None si no se puede."""
        if self.busy or not self._need_loaded():
            return None
        if not self.session.catalog_root:
            self._changed(tr("controller.primero_abri_el_catalogo_de_la_2"), scene=False)
            return None
        self.busy = True
        self._changed(tr("controller.armando_la_comparacion_de_proporciones"), scene=False)
        return lambda progress=None: self.session.proportions_snapshot(figura, progress)

    def finish_proportions(self, error: Exception | None = None) -> None:
        self.busy = False
        if error is not None:
            self._changed(tr("controller.no_se_pudo_armar_la_comparacion", error=error), scene=False)
        else:
            self._changed(tr("controller.comparacion_de_proporciones_lista"), scene=False)

    def divide_model(self) -> bool:
        if self.busy or not self._need_loaded():
            return False
        try:
            self.session.divide_model()
        except ValueError as exc:
            self._changed(tr("controller.no_se_pudo_dividir_exc", exc=exc), scene=False)
            return False
        self._changed(tr("controller.figura_dividida_ajusta_la_escala_de"))
        return True

    def set_part_scale(self, place: str, factor) -> bool:
        """Escala de la pieza de `place`: un número (uniforme) o (sx, sy, sz) en X, Y, Z del modelo."""
        if self.busy or not self._need_loaded():
            return False
        try:
            self.session.set_part_scale(place, factor)
        except ValueError as exc:
            self._changed(str(exc), scene=False)
            return False
        sx, sy, sz = self.session.part_scale(place)
        txt = f"×{sx:.3f}" if sx == sy == sz else f"X ×{sx:.3f}, Y ×{sy:.3f}, Z ×{sz:.3f}"
        self._changed(tr("controller.place_escala_txt_volve_a_revisar", place=place, txt=txt))
        return True

    def reset_part_scales(self) -> None:
        if self.busy:
            return
        self.session.project.part_scales.clear()
        self.session._part_preview = None
        self.session.outcome = None
        self._changed(tr("controller.escalas_por_pieza_restablecidas_se_usa"))

    def host_piece_for(self, place: str) -> str:
        """Pieza del MODELO que lleva la unión de `place` y que se escala para que la cara encaje: las piernas van en la pelvis
        (si hay cintura); los brazos, la cintura y el cuello, en el torso."""
        spec = next((l for l in self.session.project.limbs if l.name == place), None)
        return "pelvis" if spec is not None and spec.kind == "leg" and self.session.project.waist else "torso"

    def scale_host_for_part(self, place: str) -> bool:
        """Ajusta, de forma uniforme, sólo la pieza del modelo que recibe la unión de `place` (torso o pelvis)."""
        pv = self.part_preview(place)
        if pv is None or pv.scale_hint is None:
            self._changed(tr("controller.primero_revisa_las_piezas_para_medir"), scene=False)
            return False
        target = self.host_piece_for(place)
        k = self.session.part_scale(target)[0] * pv.scale_hint
        return self.set_part_scale(target, k)

    def fit_host_xyz(self, place: str) -> bool:
        """Como `scale_host_for_part` pero por eje: iguala ancho y alto de la cara del modelo con los de la pieza elegida,
        sin tocar el espesor ni las demás piezas."""
        pv = self.part_preview(place)
        if pv is None or pv.scale_xyz is None:
            self._changed(tr("controller.primero_revisa_las_piezas_para_medir"), scene=False)
            return False
        target = self.host_piece_for(place)
        k = np.asarray(self.session.part_scale(target)) * np.asarray(pv.scale_xyz)
        return self.set_part_scale(target, tuple(float(x) for x in k))

    def scale_to_fit_part(self, place: str) -> bool:
        """Escala TODA la figura por el factor que iguala la cara de corte de ese lugar con la de la pieza (`scale_hint`)."""
        pv = self.part_preview(place)
        if pv is None or pv.scale_hint is None:
            self._changed(tr("controller.primero_revisa_las_piezas_no_hay", place=place), scene=False)
            return False
        return self.set_ref_height(self.session.project.params.ref_height * pv.scale_hint)

    # ---- avisar antes de generar
    def pregenerate_issues(self) -> dict:
        """Lo que hay que mirar ANTES de generar, sin generar: {"stale": bool, "bloqueantes": [...], "avisos": [...]}.
        `stale` = hay piezas de catálogo elegidas y todavía no se revisaron con el estado actual (la ventana las revisa
        antes de preguntar). Los bloqueantes son lo que `build` va a rechazar o dejar mal; los avisos, lo que conviene leer."""
        sess = self.session
        out = {"stale": False, "bloqueantes": [], "avisos": []}
        if not self.loaded:
            return out
        for w in sess.catalog_warnings():
            out["avisos"].append(w)
        if not sess.project.parts and not sess.project.part_scales:
            return out
        if not sess.part_preview_fresh():
            out["stale"] = True
            return out
        for place, pv in sess.part_preview_items().items():
            if pv.error or pv.conflicts or (pv.compat is not None and pv.compat.motivos):
                out["bloqueantes"] += [f"{place}: {t}" for t in pv.problems()[:3]]
            elif pv.compat is not None and pv.compat.avisos:
                out["avisos"] += [f"{place}: {t}" for t in pv.compat.avisos]
            out["avisos"] += [f"{place}: {t}" for t in pv.notes]
        return out

    def catalog_choices(self, place: str) -> list:
        """Piezas del catálogo que sirven en ese lugar (vacío si no hay catálogo abierto o el lugar no existe)."""
        try:
            return self.session.catalog_choices(place)
        except ValueError:
            return []

    def catalog_set_part(self, place: str, ref: str | None) -> bool:
        """Pone la pieza `ref` en ese lugar (None o vacío = vuelve a la del modelo). Hay que volver a generar la figura."""
        if not self._need_loaded():
            return False
        if not ref:
            self._changed(tr("controller.place_vuelve_a_usar_la_pieza", place=place) if self.session.clear_part(place)
                          else tr("controller.place_ya_usaba_la_pieza_del", place=place), scene=False)
            return True
        try:
            pr = self.session.set_part(place, ref)
        except ValueError as e:
            self._changed(tr("controller.no_sirve_e_2", e=e), scene=False)
            return False
        self._changed(tr("controller.place_pieza_ref_genera_la_figura", place=place, ref=pr.ref), scene=False)
        return True

    def catalog_rows(self) -> list[dict]:
        """Una fila por lugar (miembros, cabeza, mano): pieza elegida y, tras generar, la etiqueta de compatibilidad."""
        return self.session.part_rows() if self.loaded else []

    @staticmethod
    def catalog_row_state(row: dict) -> tuple[str, str]:
        """(estado, ícono) de una fila de lugar: «modelo» (· la del modelo), «pendiente» (○ elegida y sin revisar),
        «ok» (✓), «aviso» (⚠) o «no» (✗; también si hay error o conflicto)."""
        if row["pieza"] is None:
            return "modelo", "·"
        nivel = row.get("nivel") or (row.get("compat") or {}).get("nivel")
        if nivel is None:
            return "pendiente", "○"
        return nivel, {"ok": "✓", "aviso": "⚠"}.get(nivel, "✗")

    @staticmethod
    def catalog_row_text(row: dict, labels: dict | None = None) -> str:
        """Texto de una fila para la lista: «brazo: original» o «pierna: pierna-01@2 — aviso (IoU 0,71, escalón 1,07 mm)».
        `labels` = {ref: nombre para mostrar} (ver `catalog_manager.piece_label`): si la pieza tiene nombre, se muestra."""
        if row["pieza"] is None:
            return tr("controller.lugar_la_del_modelo", lugar=row['lugar'])
        shown = (labels or {}).get(row["pieza"], row["pieza"])
        c = row.get("compat")
        if not c:
            return f"{row['lugar']}: {shown}"
        extra = []
        if c.get("iou") is not None:
            extra.append(tr("controller.iou_iou", iou=f"{c['iou']:.2f}").replace(".", ","))
        if c.get("escalon_mm") is not None:
            extra.append(tr("controller.escalon_escalon_mm_mm", escalon_mm=f"{c['escalon_mm']:.2f}").replace(".", ","))
        return f"{row['lugar']}: {shown} — {c.get('nivel', '?')}" + (f" ({', '.join(extra)})" if extra else "")

    def catalog_add_done(self, res: dict | None, err: Exception | None) -> bool:
        """Cierra «Catalogar esta figura»: mensaje con las piezas guardadas (o el error)."""
        if err is not None or res is None:
            self._changed(tr("controller.no_se_pudo_catalogar_err", err=err), scene=False)
            return False
        refs = ", ".join(m.ref for m in res["piezas"])
        self._changed(tr("controller.catalogadas_n_pieza_s_refs", n=len(res['piezas']), refs=refs) + "".join((' ' + tr("common.aviso_w", w=w)) for w in res["avisos"]),
                      scene=False)
        return True

    def catalog_add_options(self) -> dict | None:
        """Datos para el diálogo de «Catalogar esta figura…» (ver `Session.catalog_add_options`); None si no se puede."""
        if not self._need_loaded():
            return None
        if not self.session.catalog_root:
            self._changed(tr("common.primero_abri_un_catalogo_de_piezas"), scene=False)
            return None
        return self.session.catalog_add_options()

    def catalog_add_current(self, prefix: str, places=None, wrist_points=None, labels=(), new_version: bool = False) -> bool:
        """«Catalogar esta figura…»: guarda sus piezas crudas con puertos en el catálogo abierto."""
        if not self._need_loaded():
            return False
        try:
            res = self.session.catalog_add(prefix, places, wrist_points, labels, new_version)
        except ValueError as e:
            self._changed(tr("controller.no_se_pudo_catalogar_e", e=e), scene=False)
            return False
        return self.catalog_add_done(res, None)

    def cut_guide(self) -> list | None:
        """«Guía de cortes»: `[CutMeasure]` de la figura abierta (ver `Session.cut_guide`) o None si no se pudo medir."""
        if not self._need_loaded():
            return None
        try:
            return self.session.cut_guide()
        except ValueError as e:
            self._changed(tr("controller.no_se_pudo_medir_los_cortes", e=e), scene=False)
            return None

    def cut_guide_lines(self, all_fits: bool = False) -> list | None:
        """El texto de la guía (el mismo de `afm guia`), línea por línea; None si no se pudo medir."""
        from ..guide import format_measure

        cuts = self.cut_guide()
        if cuts is None:
            return None
        if not cuts:
            return [tr("controller.el_proyecto_no_define_miembros_ni")]
        lines = [] if self.session.catalog_root else [tr("controller.sin_catalogo_abierto_solo_se_miden")]
        for m in cuts:
            lines += format_measure(m, all_fits) + [""]
        return lines[:-1]

    def cut_fit_preview(self) -> list | None:
        """«Ajustar al estándar» sin aplicar: `[FitResult]` (ver `Session.cut_fit`) o None si no se pudo."""
        if not self._need_loaded():
            return None
        try:
            return self.session.cut_fit()
        except ValueError as e:
            self._changed(tr("controller.no_se_pudo_ajustar_al_estandar", e=e), scene=False)
            return None

    def cut_fit_lines(self) -> list | None:
        """El texto del ajuste (el mismo de `afm guia --estandar`), línea por línea; None si no se pudo."""
        from ..guide import format_fit_cut

        res = self.cut_fit_preview()
        if res is None:
            return None
        if not res:
            return [tr("controller.el_proyecto_no_define_brazos_ni")]
        return [l for r in res for l in format_fit_cut(r)]

    def apply_cut_fit(self) -> bool:
        """Aplica «Ajustar al estándar» a los cortes que quedan en contrato. Deja un punto de deshacer (`undo_snapshot` si existe)."""
        res = self.cut_fit_preview()
        if res is None:
            return False
        try:
            n = self.session.apply_cut_fit(res)
        except ValueError as e:
            self._changed(tr("controller.no_se_aplico_el_ajuste_e", e=e), scene=False)
            return False
        if not n:
            self._changed(tr("controller.ningun_corte_necesita_cambios_para_entrar"), scene=False)
            return False
        self._changed(tr("controller.ajustado_al_estandar_n_corte_s", n=n))
        return True

    def new_from_pattern_job(self, patron: str, stl: str, out: str, corona: float | None = None, entrepierna: float | None = None):
        """Trabajo pesado de «Nuevo héroe desde el patrón» (para correr en un hilo, sin tocar el estado de la ventana):
        `job(progress)` -> `(proyecto, imagen, avisos)`. Valida las rutas antes."""
        from ..patron import crear_desde_patron

        for label, path in ((tr("controller.el_proyecto_del_patron"), patron), (tr("controller.el_stl_del_heroe"), stl)):
            if not path or not os.path.isfile(path):
                raise ValueError(tr("controller.no_existe_label_path", label=label, path=path))
        if not out:
            raise ValueError(tr("common.elegi_donde_guardar_el_proyecto_del"))
        return lambda progress: crear_desde_patron(patron, stl, out, corona, None, entrepierna, None, progress=progress)

    def finish_new_from_pattern(self, res, err, stl: str) -> str | None:
        """Cierra «Nuevo héroe desde el patrón»: abre el proyecto creado (con su STL) y devuelve la imagen de revisión, o None si falló."""
        self.busy = False
        if err is not None or res is None:
            self._changed(tr("controller.no_se_pudo_crear_el_heroe", err=err), scene=False)
            return None
        out, image, notes = res
        try:
            self.open_project(out, stl)
        except Exception as e:  # noqa: BLE001
            self._changed(tr("controller.el_proyecto_se_guardo_en_out", out=out, e=e), scene=False)
            return image
        extra = ((' ' + tr("common.avisos") + ' ') + "; ".join(notes)) if notes else ""
        self._changed(tr("controller.heroe_creado_desde_el_patron_basename", basename=os.path.basename(out), extra=extra), scene=False)
        return image

    def waist_wrist_options(self) -> dict:
        """Pelvis y manos del catálogo abierto que sirven de patrón (`Session.waist_wrist_options`); vacío si no se pudo leer."""
        try:
            return self.session.waist_wrist_options()
        except ValueError:
            return {"pelvis": [], "mano": []}

    def waist_wrist_fit_preview(self, pelvis: str | None = None, mano: str | None = None) -> list | None:
        """«Ajustar cintura y muñeca al patrón» sin aplicar: `[AjusteResult]` o None si no se pudo."""
        if not self._need_loaded():
            return None
        try:
            return self.session.waist_wrist_fit(pelvis, mano)
        except ValueError as e:
            self._changed(tr("controller.no_se_pudo_ajustar_cintura_y", e=e), scene=False)
            return None

    def waist_wrist_fit_lines(self, pelvis: str | None = None, mano: str | None = None) -> list | None:
        """El texto del ajuste (el mismo de `afm ajustar-cintura-muneca`), línea por línea; None si no se pudo."""
        from ..ajuste import format_ajuste

        res = self.waist_wrist_fit_preview(pelvis, mano)
        if res is None:
            return None
        return [l for r in res for l in format_ajuste(r)]

    def apply_waist_wrist_fit(self, pelvis: str | None = None, mano: str | None = None, escalar_pelvis: bool = False) -> bool:
        """Mueve el plano de la cintura y el de la muñeca al ajuste calculado y, con `escalar_pelvis`, escala la pelvis del patrón.
        Devuelve True si cambió algo."""
        res = self.waist_wrist_fit_preview(pelvis, mano)
        if res is None:
            return False
        try:
            n = self.session.apply_waist_wrist_fit(res, escalar_pelvis)
        except ValueError as e:
            self._changed(tr("controller.no_se_aplico_el_ajuste_e", e=e), scene=False)
            return False
        if not n:
            self._changed(tr("controller.ni_la_cintura_ni_la_muneca"), scene=False)
            return False
        self.hand_extract = self._hand_mesh = None
        self._changed(tr("controller.ajustado_al_patron_n_cambio_s", n=n))
        return True

    # ---- gestor del catálogo: ver y eliminar piezas
    def catalog_pieces(self) -> list:
        """Todas las versiones de las piezas del catálogo abierto (vacío si no hay catálogo o no se pudo leer)."""
        try:
            return self.session.catalog_pieces() if self.session.catalog_root else []
        except ValueError:
            return []

    def catalog_usages(self, ref: str) -> list[str]:
        try:
            return self.session.catalog_usages(ref)
        except ValueError:
            return []

    def catalog_handle(self, required: bool = False):
        """`Catalog` del catálogo abierto para los informes de `catalog_ops`. None si no hay (con `required`, levanta `ValueError`)."""
        try:
            cat = self.session.catalog_open()
        except ValueError:
            if required:
                raise
            return None
        if cat is None and required:
            raise ValueError(tr("common.no_hay_un_catalogo_abierto"))
        return cat

    def begin_catalog_task(self, label: str, run):
        """Tarea larga de catálogo (certificar, compatibilidad, informe): marca `busy` y devuelve `job(progress=None)` para un hilo,
        o None si no se puede (hay otra tarea, o no hay catálogo). `run(root, progress)` abre su propio `Catalog` desde `root`.
        Después hay que llamar `finish_catalog_task`."""
        if self.busy:
            self._changed(tr("common.espera_a_que_termine_la_tarea"), scene=False)
            return None
        root = self.session.catalog_root
        if not root:
            self._changed(tr("common.no_hay_un_catalogo_abierto"), scene=False)
            return None
        self.busy = True
        self.progress_prefix = label
        self.progress_stages = None
        self._changed(tr("controller.label_puede_tardar_minutos_por_pieza", label=label), scene=False)
        return lambda progress=None: run(root, progress)

    def finish_catalog_task(self, label: str, error: Exception | None = None, cancelled: bool = False) -> None:
        self.busy = False
        self.progress_prefix = tr("controller.generando")
        if cancelled:
            self._changed(tr("controller.label_cancelado_lo_ya_terminado_quedo", label=label), scene=False)
        elif error is not None:
            self._changed(tr("controller.label_fallo_error", label=label, error=error), scene=False)
        else:
            self._changed(tr("controller.label_terminado", label=label), scene=False)

    def catalog_delete(self, refs) -> bool:
        """Elimina del catálogo abierto esas versiones (`id@n`). Los lugares de la figura que las usaban vuelven a la pieza
        del modelo. No se puede deshacer: la ventana pide confirmación antes."""
        refs = list(refs)
        if self.busy:
            self._changed(tr("controller.espera_a_que_termine_la_tarea"), scene=False)
            return False
        if not refs:
            self._changed(tr("controller.no_hay_ninguna_pieza_elegida_para"), scene=False)
            return False
        try:
            res = self.session.catalog_delete(refs)
        except ValueError as e:
            self._changed(tr("controller.no_se_elimino_nada_e", e=e), scene=False)
            return False
        msg = tr("controller.eliminadas_n_pieza_s_join", n=len(res['eliminadas']), join=', '.join(res['eliminadas']))
        if res["quitadas"]:
            msg += (' ' + tr("controller.vuelven_a_la_pieza_del_modelo", join=', '.join(res['quitadas'])))
        if res["errores"]:
            msg += (' ' + tr("controller.errores") + ' ') + "; ".join(res["errores"])
        self._changed(msg, scene=bool(res["quitadas"]))
        return not res["errores"]

    def catalog_set_state(self, refs, state: str) -> bool:
        """Cambia el estado de las versiones `refs` (`id@n`) del catálogo abierto."""
        refs = list(refs)
        if self.busy:
            self._changed(tr("controller.espera_a_que_termine_la_tarea_2"), scene=False)
            return False
        try:
            metas = self.session.catalog_set_state(refs, state)
        except ValueError as e:
            self._changed(tr("controller.no_se_cambio_el_estado_e", e=e), scene=False)
            return False
        self._changed(tr("controller.estado_state_join", state=state, join=', '.join(m.ref for m in metas)), scene=False)
        return True

    def catalog_trash_count(self) -> int:
        """Cuántas piezas hay en el último lote de la papelera (0 = nada que recuperar)."""
        try:
            batches = self.session.catalog_trash()
        except ValueError:
            return 0
        return len(batches[0]["refs"]) if batches else 0

    def catalog_undo_delete(self) -> bool:
        """«Recuperar lo eliminado»: devuelve al catálogo el último lote de piezas eliminadas. Los lugares de la figura que las
        usaban ya habían vuelto a la pieza del modelo: hay que elegirlas de nuevo."""
        if self.busy:
            self._changed(tr("controller.espera_a_que_termine_la_tarea_3"), scene=False)
            return False
        try:
            restored, errors = self.session.catalog_undo_delete()
        except ValueError as e:
            self._changed(tr("controller.no_se_recupero_nada_e", e=e), scene=False)
            return False
        msg = tr("controller.recuperadas_n_pieza_s_v", n=len(restored), v=', '.join(m.ref for m in restored) or '—')
        if errors:
            msg += (' ' + tr("controller.no_se_recuperaron") + ' ') + "; ".join(errors)
        self._changed(msg, scene=False)
        return bool(restored) and not errors

    def catalog_rename(self, ref: str, nombre: str) -> bool:
        """Renombra (nombre para mostrar) la pieza `ref` del catálogo abierto. El `id` y las referencias no cambian."""
        if self.busy:
            self._changed(tr("controller.espera_a_que_termine_la_tarea_4"), scene=False)
            return False
        try:
            metas = self.session.catalog_rename(ref, nombre)
        except ValueError as e:
            self._changed(tr("controller.no_se_renombro_e", e=e), scene=False)
            return False
        name = metas[0].display_name
        self._changed(tr("controller.id_ahora_se_muestra_como_name", id=metas[0].id, name=name) if metas[0].nombre else tr("controller.id_vuelve_a_mostrarse_con_su", id=metas[0].id),
                      scene=False)
        return True

    def catalog_cut_wrist(self, ref: str, points, invert: bool = False, hand_id: str | None = None, new_hand_version: bool = False):
        """«Cortar la mano…» del gestor del catálogo: guarda el brazo `ref` sin mano (versión nueva) y la mano como pieza aparte.
        Devuelve el `WristCut` o None si no se pudo (el motivo queda en el mensaje). No toca la figura abierta."""
        if self.busy:
            self._changed(tr("controller.espera_a_que_termine_la_tarea_5"), scene=False)
            return None
        try:
            res = self.session.catalog_wrist_cut(ref, points, invert, hand_id, new_hand_version)
        except ValueError as e:
            self._changed(tr("controller.no_se_corto_la_mano_e", e=e), scene=False)
            return None
        msg = tr("controller.brazo_sin_mano_ref_mano_ref2", ref=res.arm.ref, ref2=res.hand.ref, v=f"{2 * res.wrist_radius_mm:.1f}", from_ref=res.from_ref)
        if res.warnings:
            msg += (' ' + tr("common.avisos") + ' ') + "; ".join(res.warnings)
        self._changed(msg, scene=False)
        return res

    def catalog_save_joint(self, ref: str, joint):
        """«Reajustar articulación…» del gestor del catálogo: guarda la articulación `joint` de la pieza `ref` como versión nueva.
        Devuelve la meta nueva o None (el motivo queda en el mensaje). La versión original y la figura abierta no se tocan."""
        if self.busy:
            self._changed(tr("controller.espera_a_que_termine_la_tarea_6"), scene=False)
            return None
        try:
            meta, warns = self.session.catalog_retune_joint(ref, joint)
        except ValueError as e:
            self._changed(tr("controller.no_se_guardo_la_articulacion_e", e=e), scene=False)
            return None
        msg = tr("controller.articulacion_name_reajustada_version_nueva", name=joint.name, ref=meta.ref, ref2=ref)
        if warns:
            msg += (' ' + tr("common.avisos") + ' ') + "; ".join(warns)
        self._changed(msg, scene=False)
        return meta

    def clear_hand(self) -> None:
        self._changed(tr("controller.se_vuelve_a_usar_la_mano") if self.session.clear_hand()
                      else tr("controller.no_habia_mano_de_reemplazo"))

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
        if self._busy_for(tr("controller.la_cabeza")) or not self._need_loaded():
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
            self._changed(tr("controller.no_se_pudo_usar_esa_cabeza", e=e), scene=False)
            return False
        self._changed(self._head_fit_message(info))
        return True

    @staticmethod
    def _head_fit_message(info: dict) -> str:
        msg = (tr("controller.cabeza_de_reemplazo_lista_se_ve", escala=f"{info['escala']:.4g}", alto_mm=f"{info['alto_mm']:.1f}", v=f"{100 * info['coincidencia_base']:.0f}"))
        if info["corte_plano"]:
            msg += (' ' + tr("controller.la_base_se_corto_con_el"))
        elif info["base_cortada"]:
            msg += (' ' + tr("controller.no_traia_base_plana_se_corto"))
        if info.get("cavidad_rellenada"):
            msg += (' ' + tr("controller.la_cabeza_era_hueca_cascara_abierta"))
        if info["coincidencia_base"] < MIN_BASE_IOU:
            msg += (' ' + tr("controller.aviso_la_base_se_parece_poco"))
        return msg

    def set_head_adjust(self, up: str | None = None, yaw: float | None = None, rx: float | None = None,
                        ry: float | None = None, scale: float | None = None) -> bool:
        """Cambia orientación (`up`, `rx`, `ry`, `yaw`) y/o escala manual de la cabeza de reemplazo ya cargada y la
        vuelve a ajustar (el plano de corte de la base, si hay, acompaña a la malla). Si el valor no vale, no cambia
        nada y deja el motivo en `message`."""
        cur = self.session.project.head
        if cur is None or not cur.replacement_path:
            self._changed(tr("controller.primero_carga_una_cabeza_de_reemplazo"), scene=False)
            return False
        return self.set_head_replacement(cur.replacement_path, yaw, up, rx, ry, scale)

    # ---- corte de la base de la cabeza de reemplazo con un plano de 3 puntos
    def start_head_cut_plane(self) -> None:
        """Pide 3 puntos sobre la cabeza de reemplazo (la naranja) que definen el plano que corta su base."""
        prev = self.head_preview()
        if prev is None:
            self._changed(tr("controller.primero_carga_una_cabeza_de_reemplazo"), scene=False)
            return
        self.cancel(quiet=True)
        self.mode = Mode.HEAD_CUT_PLANE
        self.picker = Picker(prev[0])                # los clics caen sobre la cabeza nueva, no sobre la figura
        self._changed(tr("controller.corte_de_la_base_elegi_3"))

    def _finish_head_cut_plane(self) -> None:
        pts, self.pending = self.pending, []
        try:
            info = self.session.set_head_cut_plane(pts)
        except Exception as e:
            self._changed(tr("controller.no_sirve_e_elegi_3_puntos", e=e))
            return
        self.mode = Mode.VIEW
        self._rebuild_picker()
        self._changed(self._head_fit_message(info))

    def clear_head_cut_plane(self) -> None:
        if self._busy_for(tr("controller.la_cabeza")):
            return
        self._changed(tr("controller.se_quito_el_plano_de_corte") if self.session.clear_head_cut_plane()
                      else tr("controller.la_cabeza_no_tenia_plano_de"))

    def set_head_orientation(self, up: str | None = None, yaw: float | None = None) -> bool:
        """Cambia el eje «arriba» y/o el giro de la cabeza de reemplazo ya cargada y la vuelve a ajustar."""
        return self.set_head_adjust(up=up, yaw=yaw)

    def head_preview(self):
        """(malla, info) de la cabeza de reemplazo para dibujarla, o None si no hay."""
        return self.session.head_replacement_preview() if self.loaded else None

    def clear_head_replacement(self) -> None:
        if self._busy_for(tr("controller.la_cabeza")):
            return
        self._changed(tr("controller.se_vuelve_a_usar_la_cabeza") if self.session.clear_head_replacement()
                      else tr("controller.no_habia_cabeza_de_reemplazo"))

    def _finish_limb_plane(self) -> None:
        pts, self.pending = self.pending, []
        kind = self.pending_kind or "arm"
        name = self.unique_name(kind)
        try:
            plane = Session.plane_from_points(pts, name)
            self.session.add_limb(name, kind, plane)      # rechaza planos de canto
        except ValueError as e:
            self._changed(tr("controller.no_sirve_e_elegi_3_puntos", e=e))
            return
        self.mode, self.pending_kind = Mode.VIEW, None
        if self.session.limb_offset(name) is not None:
            self._last_offset_limb = name            # el plano nuevo es el que se está trabajando: «Corte a» lo mueve a él
        self._changed(tr("controller.plano_name_agregado_falta_dibujar_su", name=name))

    def offset_limb(self) -> str | None:
        """Miembro activo cuyo desplazamiento de corte se puede editar (el del pasador o el de la silueta)."""
        for name in (self.pin_limb, self.edit_limb):
            if name is not None and self.session.limb_offset(name) is not None:
                self._last_offset_limb = name
                return name
        sym = [l.name for l in self.session.project.limbs if self.session.limb_offset(l.name) is not None]
        if len(sym) == 1:
            return sym[0]
        last = self._last_offset_limb                           # varios sobre la simetría y ninguno activo: el último que se editó
        return last if last in sym else None

    def set_limb_offset(self, name: str, mm: float) -> None:
        try:
            self.session.set_limb_offset(name, mm)
        except ValueError as e:
            self._changed(f"{e}.", scene=False)
            return
        if self.mode == Mode.PIN and self.pin_limb == name:
            self.start_pin(name)             # la cara de contacto se movió: recalcular y volver a mirarla
            return
        self._changed(tr("controller.corte_de_name_a_mm_mm", name=name, mm=f"{mm:g}"))

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
    # ---- diagnóstico visual de planos (qué plano falla y cuánto está girado)
    @staticmethod
    def plane_id_for_place(place: str | None) -> str | None:
        """Id del plano (el de `plane_entries`) al que pertenece el lugar de un error de extracción. La muñeca no
        tiene plano propio en la lista (se define con 3 puntos de la mano): devuelve None."""
        if place is None or place == "muneca":
            return None
        return {"cuello": HEAD_NAME, "cintura": WAIST_NAME}.get(place, place)

    def _register_plane_fault(self, error: Exception | None) -> None:
        """Si el error trae el lugar cuyo plano falló, lo recuerda para pintar ese plano de rojo en el visor."""
        self.plane_faults = {}
        pid = self.plane_id_for_place(getattr(error, "place", None))
        if pid is not None and any(e.id == pid for e in self.plane_entries()):
            self.plane_faults[pid] = (str(error), self._plane_sig(pid))

    def _plane_sig(self, pid: str) -> bytes:
        """Firma del plano y del desplazamiento de su corte: si cambia, el fallo registrado ya no vale."""
        e = next((x for x in self.plane_entries() if x.id == pid), None)
        if e is None:
            return b""
        spec = self.session.limb(pid)
        off = b"" if spec is None else repr(getattr(spec, "cut_offset", None)).encode()
        return np.round(np.asarray(e.plane.origin, float), 6).tobytes() + np.round(np.asarray(e.plane.normal, float), 6).tobytes() + off

    def _fault_hint(self) -> str:
        if not self.plane_faults:
            return ""
        names = {e.id: e.label for e in self.plane_entries()}
        return (' ' + tr("controller.el_plano")) + "», «".join(names.get(k, k) for k in self.plane_faults) + tr("controller.esta_pintado_de_rojo_en_el")

    def plane_tilt_deg(self, place: str) -> float | None:
        """Cuánto está girado (grados) el plano lateral de un miembro respecto de la cara de la pieza de catálogo elegida
        en `place`, cuyo puerto es lateral (paralelo al plano de simetría). None si no hay pieza de catálogo con puerto
        lateral o si el plano del miembro no es lateral (su corte no tiene que ser paralelo a la simetría)."""
        from ..limbs import plane_is_lateral

        spec = self.session.limb(place)
        if spec is None or place not in self.session.project.parts or self.session.line_cut_x(place) is None:
            return None
        if not plane_is_lateral(spec.plane, spec.contact_axis):
            return None
        ax = np.asarray(spec.contact_axis, float)
        n = np.asarray(spec.plane.normal, float)
        c = abs(float(n @ ax)) / (np.linalg.norm(n) * np.linalg.norm(ax))
        return float(np.degrees(np.arccos(min(1.0, c))))

    def plane_status(self) -> dict[str, tuple[str, str]]:
        """Estado de cada plano para pintarlo: {id: (nivel, texto)} con nivel «no» (rojo: su corte falló en la última
        revisión), «aviso» (amarillo: está girado más de `TILT_OK_DEG` respecto de la cara de la pieza elegida) o «ok».
        Los planos que no son de miembros ni fallaron no aparecen."""
        from ..ports import TILT_OK_DEG

        out: dict[str, tuple[str, str]] = {}
        for e in self.plane_entries():
            fault = self.plane_faults.get(e.id)
            if fault is not None and fault[1] == self._plane_sig(e.id):
                out[e.id] = ("no", fault[0])
                continue
            if e.kind in ("arm", "leg"):
                t = self.plane_tilt_deg(e.id)
                if t is None:
                    continue
                if t >= TILT_OK_DEG:
                    out[e.id] = ("aviso", tr("controller.girado_t_respecto_del_corte_de", t=f"{t:.1f}").replace(".", ","))
                else:
                    out[e.id] = ("ok", tr("controller.coincide_con_el_corte_de_la"))
        return out

    def cut_plane_to_draw(self, e: "PlaneEntry") -> Plane | None:
        """Plano de corte desplazado de un miembro sobre el plano de simetría (silueta en x ≈ 0, corte a «Corte a» mm), o None si
        no hay nada que mostrar: no es un brazo / pierna, su plano no está sobre la simetría, la extrusión no usa el
        desplazamiento o el corte coincide con el plano de la silueta. Solo se dibuja: el plano de la silueta (donde se hacen los
        clics) no se mueve."""
        if e.kind not in ("arm", "leg"):
            return None
        try:
            off = self.session.limb_offset(e.id)
            spec = self.session.limb(e.id)
            if off is None or spec is None or abs(off) < 0.05:
                return None
            return cut_plane(spec, off)
        except (ValueError, KeyError):
            return None

    def plane_entries(self) -> list[PlaneEntry]:
        out = []
        if self.session.project.symmetry is not None:
            out.append(PlaneEntry(SYMMETRY_ID, tr("controller.simetria"),
                                  Plane.from_normal((0, 0, 0), (1, 0, 0), "simetría"), "symmetry",
                                  SYMMETRY_ID not in self.hidden))
        for l in self.session.project.limbs:
            out.append(PlaneEntry(l.name, l.name.capitalize(), l.plane, l.kind, l.name not in self.hidden))
        head = self.session.project.head
        if head is not None:
            out.append(PlaneEntry(HEAD_NAME, tr("controller.cabeza_cuello"), head.plane, "head", HEAD_NAME not in self.hidden))
        waist = self.session.project.waist
        if waist is not None:
            out.append(PlaneEntry(WAIST_NAME, tr("controller.cintura_giro"), waist.plane, "waist", WAIST_NAME not in self.hidden))
        for j in self.session.project.joints:
            jid = JOINT_PREFIX + j.name
            out.append(PlaneEntry(jid, j.name.capitalize(), j.plane, "joint", jid not in self.hidden))
        return out

    def cut_rows(self) -> list[dict]:
        """Cortes NUMERADOS en el orden de la lista de planos (el mismo número que se dibuja en el visor): n, id, etiqueta,
        tipo, desplazamiento del corte (mm, solo miembros sobre el plano de simetría) y, si la pieza de catálogo elegida
        corta a otra distancia, la de la línea (`linea_mm`) y la diferencia (`dif_mm`)."""
        out = []
        status = self.plane_status()
        for n, e in enumerate(self.plane_entries(), start=1):
            cur = line = None
            if e.kind in ("arm", "leg"):
                cur = self.session.limb_offset(e.id)
                line = self.session.line_cut_x(e.id) if cur is not None else None
            st = status.get(e.id)
            out.append({"n": n, "id": e.id, "etiqueta": e.label, "tipo": e.kind, "corte_mm": cur, "linea_mm": line,
                        "dif_mm": None if (cur is None or line is None) else line - cur,
                        "nivel": None if st is None else st[0], "estado": None if st is None else st[1]})
        return out

    @staticmethod
    def cut_row_text(row: dict) -> str:
        """«2 · Pierna — corte a 7,00 mm (la línea corta a 7,66: +0,66)»."""
        t = f"{row['n']} · {row['etiqueta']}"
        if row.get("nivel") == "no":
            t += (' ' + tr("controller.fallo") + ' ') + row["estado"].split("— ")[-1]
        elif row.get("nivel") == "aviso":
            t += " — ⚠ " + row["estado"]
        if row["corte_mm"] is None:
            return t
        t += (' ' + tr("controller.corte_a_corte_mm_mm", corte_mm=f"{row['corte_mm']:.2f}")).replace(".", ",")
        if row["linea_mm"] is not None:
            d = row["dif_mm"]
            t += ((' ' + tr("controller.la_linea_corta_a_linea_mm", linea_mm=f"{row['linea_mm']:.2f}", v='alineado' if abs(d) < 0.05 else f'{d:+.2f}'))).replace(".", ",")
        return t

    def align_cut_to_line(self, place: str) -> bool:
        """«Alinear a la línea» en un miembro: su corte pasa a la distancia de la pieza de catálogo elegida."""
        if not self._need_loaded():
            return False
        try:
            x = self.session.align_cut_to_line(place)
        except (ValueError, KeyError) as e:
            self._changed(tr("controller.no_se_pudo_alinear_place_e", place=place, e=e), scene=False)
            return False
        self._changed(tr("controller.corte_de_place_alineado_a_la", place=place, x=f"{x:.2f}"))
        return True

    def align_cuts_to_line(self) -> int:
        """«Alinear todos los cortes a la línea»: cada miembro con pieza de catálogo y corte lateral. Devuelve cuántos movió."""
        if not self._need_loaded():
            return 0
        moved, skipped = [], []
        for r in self.cut_rows():
            if r["id"] in self.session.project.parts and r["tipo"] in ("arm", "leg"):
                if r["dif_mm"] is not None and abs(r["dif_mm"]) < 0.05:
                    continue
                try:
                    self.session.align_cut_to_line(r["id"])
                    moved.append(r["id"])
                except (ValueError, KeyError) as e:
                    skipped.append(f"{r['id']} ({e})")
        msg = (tr("controller.alineados_a_la_linea_join", join=', '.join(moved)) if moved else tr("controller.no_habia_cortes_para_alinear"))
        if skipped:
            msg += (' ' + tr("controller.sin_alinear") + ' ') + "; ".join(skipped) + "."
        self._changed(msg + ((' ' + tr("controller.volve_a_revisar_las_piezas")) if moved else ""))
        return len(moved)

    def set_show_cut_numbers(self, on: bool) -> None:
        if self.show_cut_numbers != bool(on):
            self.show_cut_numbers = bool(on)
            self._changed()

    def _sync_cut_numbers(self, scene) -> None:
        """Dibuja el número de cada corte (el de `cut_rows`) en el visor, mirando a la cámara y sin prueba de profundidad."""
        from .glyphs import camera_axes, number_paths

        wanted = set()
        if self.show_cut_numbers and self.group_visible("planes"):
            right, up = camera_axes(self.camera.eye(), self.camera.target)
            h = max(self.plane_half_size * 0.16, 1.0)
            for n, e in enumerate(self.plane_entries(), start=1):
                if not e.visible:
                    continue
                org = np.asarray(e.plane.origin, float)
                for i, path in enumerate(number_paths(n, org, right, up, h)):
                    key = f"corte:{e.id}:n{i}"
                    scene.set_lines(key, path, (1.0, 1.0, 1.0, 1.0), on_top=True)
                    wanted.add(key)
        for key in [k for k in scene.items if k.startswith("corte:") and re.search(r":n\d+$", k) and k not in wanted]:
            scene.remove(key)                       # solo los números: la mira del corte también pinta con «corte:…» (mira_, cinta_, mejor…)

    def group_visible(self, group: str) -> bool:
        """Interruptor general: «planes» (todos los planos), «pins» (marcas de pasadores y ejes de articulación) o
        «contours» (siluetas y recortes). Se combina con el de cada plano: algo se ve si ambos lo permiten."""
        if group not in SHOW_GROUPS:
            raise ValueError(group)
        return self.show_groups[group]

    def set_group_visible(self, group: str, on: bool) -> None:
        if group not in SHOW_GROUPS:
            raise ValueError(group)
        on = bool(on)
        if self.show_groups[group] != on:
            self.show_groups[group] = on
            self._changed()

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
            self._changed(tr("controller.simetria_quitada_tambien_los_miembros_depe"))
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
        self._changed(tr("controller.plano_plane_id_quitado", plane_id=plane_id))

    def problems(self) -> list[str]:
        return self.session.problems()

    # ------------------------------------------------- motivos de botón apagado
    _WAIT = lz("common.espera_a_que_termine_la_tarea")

    def blocked(self, what: str) -> str | None:
        """Por qué está apagado el control `what` (None = se puede usar); lo muestran los tooltips de la ventana.

        Es la única fuente de verdad: la ventana usa el mismo valor para apagar el botón y para explicarlo, así que el
        motivo nunca se desvía de la condición real. Las claves son grupos de controles con la misma condición.
        """
        proj = self.session.project
        loaded = self.loaded
        if what == "open":
            return self._WAIT if self.busy else None
        if what in ("save", "generate"):
            return self._why_stl() or (self._WAIT if self.busy else None)
        if what == "stl":
            return self._why_stl()
        if what == "sym":
            return self._why_sym()
        if what == "limbs":
            return self._why_sym() or (None if proj.limbs else tr("controller.agrega_primero_un_brazo_o_una"))
        if what == "head":
            return self._why_stl() or (None if proj.head is not None else tr("controller.agrega_primero_el_plano_del_cuello"))
        if what == "head_pin_reset":
            return self.blocked("head") or (None if proj.head.pin_center is not None else tr("controller.el_pasador_del_cuello_ya_esta_2"))
        if what == "head_replacement":
            return self.blocked("head") or (None if proj.head.replacement_path is not None else tr("controller.primero_elegi_una_cabeza_de_reemplazo"))
        if what == "head_replacement_cut":
            return self.blocked("head_replacement") or (None if proj.head.replacement_cut_points is not None else tr("controller.la_cabeza_de_reemplazo_no_tiene"))
        if what in ("floor_cut_head", "floor_cut_waist"):
            place = what.split("_")[-1]
            return self._why_sym() or (None if self.session.floor_cut_height(place) is not None else
                                       tr("controller.primero_agrega_el_corte_paralelo_al_piso"))
        if what == "waist":
            return self._why_stl() or (None if proj.waist is not None else tr("controller.agrega_primero_la_cintura_pestana_planos"))
        if what == "waist_pin_reset":
            return self.blocked("waist") or (None if proj.waist.pin_center is not None else tr("controller.el_pasador_de_la_cintura_ya_2"))
        if what == "hand_extract":
            return self._why_stl() or (None if self.hand_extract is not None else tr("controller.primero_extrae_la_mano_estandar_3"))
        if what == "hand_placed":
            return self._why_stl() or (None if proj.hand is not None else tr("common.primero_elegi_una_mano_de_reemplazo"))
        if what == "outcome":
            if self.busy:
                return self._WAIT
            if self.session.outcome is not None:
                return None
            return (tr("controller.la_figura_generada_quedo_desactualizada_ge")
                    if self.generated_once else tr("controller.genera_la_figura_primero_pestana_generar"))
        if what == "grip":
            return self._WAIT if self.busy else None
        if what == "grip_plate":
            return None if self.grip is not None else tr("controller.primero_taladra_una_mano_estandar_stl")
        if what == "grip_hand":
            if self.grip is None:
                return tr("controller.primero_taladra_una_mano_estandar_stl")
            return tr("controller.el_agarre_quedo_desactualizado_volve_a") if self.grip_stale else None
        if what == "drawing":
            return None if self.mode == Mode.POLYGON else tr("controller.se_activa_mientras_dibujas_una_silueta")
        if what == "raise":
            return self.blocked("drawing") or (None if self.edit_cross else tr("controller.solo_vale_para_la_2_a"))
        if what == "pin":
            return None if self.mode == Mode.PIN else tr("controller.se_activa_mientras_colocas_un_pasador")
        if what == "pin_any":
            ok = self.mode in (Mode.PIN, Mode.HEAD_PIN, Mode.WAIST_PIN)
            return None if ok else tr("controller.se_activa_mientras_colocas_un_pasador_2")
        if what == "weapon":
            ws = self.weapon
            if ws is None or not ws.loaded:
                return tr("controller.primero_abri_un_arma_stl")
            return self._WAIT if self.busy else None
        raise KeyError(what)

    @property
    def outcome_stale(self) -> bool:
        """Había una figura generada y un cambio posterior la invalidó (parámetros, planos, deshacer…)."""
        return self.generated_once and self.loaded and not self.busy and self.session.outcome is None

    def _why_stl(self) -> str | None:
        return None if self.loaded else tr("controller.primero_abri_un_stl_pestana_figura")

    def _why_sym(self) -> str | None:
        if not self.loaded:
            return self._why_stl()
        return None if self.session.project.symmetry is not None else tr("controller.primero_defini_el_plano_de_simetria_2")

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
            self._changed((tr("controller.todavia_no_se_puede_generar") + ' ') + "; ".join(probs) + ".", scene=False)
            return None
        self.cancel(quiet=True)
        self.busy = True
        self._changed(tr("controller.generando_la_figura_puede_tardar_varios"), scene=False)
        self.progress_stages = self.session.progress_plan("build")
        return lambda progress=None: self.session.build(progress)

    def report_progress(self, text: str) -> None:
        self._changed(f"{self.progress_prefix}… {text}", scene=False)

    def finish_generate(self, outcome=None, error: Exception | None = None) -> None:
        self.busy = False
        self.pose_view, self.pose_report = False, None      # la figura nueva reemplaza a la de la vista previa
        if isinstance(error, TaskCancelled):                 # no es una falla de la figura: no se marca ningún plano
            self._changed(tr("controller.generacion_cancelada_la_figura_anterior_qu"))
            return
        if error is not None:
            self._register_plane_fault(error)
            self._changed(tr("controller.fallo_la_generacion_error_fault_hint", error=error, fault_hint=self._fault_hint()))
        else:
            self.plane_faults = {}
            self.generated_once = True
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
            self._changed(tr("controller.primero_genera_la_figura_los_cambios"), scene=False)
            return None
        problem = self.color_source_problem()          # un FBX perdido o cambiado se sabe antes de empezar, no a mitad de la exportación
        if problem:
            self._changed(problem, scene=False)
            return None
        self.busy = True
        self._changed(tr("controller.exportando_las_piezas_puede_tardar"), scene=False)
        sess, angle = self.session, self.overhang_angle
        self.progress_stages = self.session.progress_plan("export", orient=orient, flat=flat)
        bambu = self.bambu.options()                      # None con la casilla apagada; con ella, el tope, el % y lo elegido a mano
        return lambda progress=None: sess.export(outdir, orient=orient, max_angle=angle, flat=flat, progress=progress, bambu=bambu)

    def finish_export(self, info: dict | None, outdir: str, error: Exception | None = None) -> dict | None:
        self.busy = False
        if error is not None:
            self._changed(tr("controller.fallo_la_exportacion_error", error=error), scene=False)
            return None
        files = info["archivos"]
        self.color_report = files.get("color")
        self.bambu_report = (self.color_report or {}).get("bambu")
        was_viewing = self.show_bambu
        self.show_bambu, self._bambu_view = False, None       # piezas nuevas: la vista del 3MF se calcula de nuevo
        msg = tr("controller.exportado_en_outdir_ensamblada_n_piezas", outdir=outdir, n=len(files['piezas']))
        ori = files.get("orientacion")
        if ori:
            msg += (' ' + tr("controller.orientacion_etiqueta_voladizo_voladizo_mm2", etiqueta=ori['etiqueta'], voladizo_mm2=ori['voladizo_mm2'], base_mm2=ori['base_mm2']))
        ss = files.get("sin_soportes")
        if ss:
            giro = ss["giro_brazos_grados"]
            msg += ((' ' + tr("controller.corte_con_la_cama_de_impresion_4", corte_sobre_cola_mm=f"{ss['corte_sobre_cola_mm']:g}") + ' ')
                    + (", ".join(f"{a:.0f}°" for a in giro.values()) or "0°") + (' ' + tr("controller.hacia_adelante")))
            msg += fill_hip_message(ss, self.session.project.params.flat_fill_hip)
            if ss["avisos"]:
                msg += (' ' + tr("controller.aviso") + ' ') + "; ".join(ss["avisos"])
        omitted = (info.get("color") or {}).get("omitido")
        if self.color_report:
            msg += ' ' + tr("controller.color_exportado_n_piezas", n=len(self.color_report.get("piezas", {})))
        elif omitted:
            msg += ' ' + tr("controller.color_omitido_motivo", motivo=omitted)
        if self.bambu_report:
            msg += ' ' + tr("controller.bambu_exportado", archivos=f"{self.bambu_report['3mf']}, {self.bambu_report['obj']}", n=len(self.bambu_report["colores"]))
        elif info.get("bambu_omitido"):
            msg += ' ' + tr("controller.bambu_sin_color")
        self._changed(msg, scene=was_viewing)                 # solo se repinta si el visor mostraba la vista del 3MF
        return info

    # ---------------------------------------------------------------- salida para Bambu Studio (3MF / OBJ de color plano)
    def _bambu_role_names(self) -> dict:
        """`{id de rol: nombre}` de la paleta de la línea abierta (las filas del selector); falla sin línea o sin paleta (`BambuChoice` lo tolera)."""
        from ..colorpiece import line_palette

        pal, _linea = line_palette(self.session.catalog_root)
        return {r.id: (r.nombre or r.id) for r in pal.roles} if pal is not None else {}

    def set_bambu(self, enabled: bool | None = None, colors: int | None = None, min_pct: float | None = None) -> None:
        """Casilla, tope de colores y % mínimo de la salida para Bambu Studio (no tocan el proyecto, la receta ni la geometría)."""
        b = self.bambu
        if enabled is not None:
            b.enabled = bool(enabled)
        if colors is not None:
            b.colors = max(1, int(colors))
        if min_pct is not None:
            b.min_pct = max(0.0, min(99.9, float(min_pct)))
        self._bambu_view = None                                  # el tope y el % mueven la selección automática: la vista se calcula de nuevo
        self._changed(scene=False)

    def bambu_view_parts(self):
        """`[(nombre, malla, rgba)]` de la vista previa del 3MF si es de las piezas de ahora; None si no hay o quedó vieja."""
        cz, v = self.session.colorizer, self._bambu_view
        return v[1] if (v is not None and cz is not None and v[0] is cz) else None

    def bambu_view_active(self) -> bool:
        return bool(self.show_bambu and self.bambu_view_parts())

    def begin_bambu_preview(self):
        """Prepara el cálculo de la vista previa (puede tardar en una figura grande) y devuelve ``(token, job)``: ``job()`` da ``(partes, resumen)`` y es
        apto para un hilo; con lo que dé hay que llamar `finish_bambu_preview(token, ...)` desde el hilo de la interfaz. None si no hay colores."""
        cz = self.session.colorizer
        if cz is None or not self.bambu.has_colors():
            self._changed(tr("bambu_choice.sin_figura"), scene=False)
            return None
        self._bambu_token += 1
        opt = self.bambu.options(force=True)                     # una copia: el hilo no lee lo que la ventana siga cambiando
        return self._bambu_token, (lambda: self.bambu.preview(opt))

    def finish_bambu_preview(self, token: int, parts, summary, error=None) -> bool:
        if token != self._bambu_token:                           # mientras tanto se pidió otra vista
            return False
        cz = self.session.colorizer
        if error is not None or cz is None:
            self.show_bambu, self._bambu_view = False, None
            self._changed(tr("figure_window.bambu_vista_error", exc=error if error is not None else tr("bambu_choice.sin_figura")))
            return False
        self._bambu_view = (cz, parts)
        text = tr("figure_window.bambu_vista_info", n=len(summary["colores"]), tri=summary["triangulos"])
        if summary["fundidos"]:
            text += " " + tr("figure_window.bambu_vista_fundidos", n=summary["fundidos"])
        self._changed(text)
        return True

    def set_show_bambu(self, on: bool) -> bool:
        """Enciende o apaga la vista previa de los colores del 3MF en el visor principal (sin hilos: la ventana usa `begin_bambu_preview`)."""
        if not on:
            self.show_bambu = False
            self._changed(tr("controller.bambu_vista_apagada"))
            return True
        job = self.begin_bambu_preview()
        if job is None:
            self.show_bambu = False
            return False
        token, work = job
        try:
            parts, summary = work()
        except Exception as exc:                                 # noqa: BLE001 — se informa en el estado
            self.show_bambu = False
            self.finish_bambu_preview(token, None, None, exc)
            return False
        self.show_bambu = True
        return self.finish_bambu_preview(token, parts, summary)

    def bambu_changed(self) -> None:
        """Cambió el selector: la vista (si está prendida) se recalcula; si no, se descarta para calcularla con lo de ese momento."""
        self._bambu_view = None
        self._changed(scene=True)

    def begin_export_bambu(self):
        """«Exportar 3MF ahora»: devuelve el trabajo ``job(progress=None) -> dict`` (apto para un hilo) que reescribe solo `<fig>_bambu.3mf / .obj / .mtl`
        del último export con los colores de ahora; después hay que llamar `finish_export_bambu`. None (con el motivo en `message`) si no se puede."""
        if self.busy:
            return None
        if self.session.colorizer is None or not self.bambu.has_colors():
            self._changed(tr("bambu_choice.sin_figura"), scene=False)
            return None
        self.busy = True
        self._changed(tr("figure_window.bambu_exportando"), scene=False)
        b = self.bambu
        opt = b.options(force=True)                              # una copia: el hilo no lee lo que la ventana siga cambiando
        return lambda progress=None: b.export(opt)

    def finish_export_bambu(self, rep: dict | None, error: Exception | None = None) -> dict | None:
        self.busy = False
        if error is not None:
            self._changed(tr("figure_window.bambu_exportar_error", exc=error), scene=False)
            return None
        self.bambu_report = rep
        self._changed(tr("figure_window.bambu_exportado", archivos=f"{rep['3mf']}, {rep['obj']}", n=len(rep["colores"])), scene=False)
        return rep

    def export_bambu(self) -> dict | None:
        """Sin hilos: pruebas y uso programático."""
        job = self.begin_export_bambu()
        if job is None:
            return None
        try:
            rep = job()
        except Exception:
            self.busy = False
            raise
        return self.finish_export_bambu(rep)

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
    def _sync_grip_standalone(self, scene) -> bool:
        """Previsualización del agarre con la mano del catálogo, sola en el visor (sin mano pegada). False si no corresponde."""
        if not self.grip_preview_standalone:
            return False
        scene.clear()
        pv = self.grip_preview_meshes(None)
        if pv is not None:
            scene.set_mesh("agarre:mano", pv[0], color=_HAND_COLOR)
            if pv[1] is not None:
                scene.set_mesh("agarre:espada", pv[1], color=_ACCESSORY_COLOR)
            return True
        root, ref = self.grip_preview_catalog
        if self._grip_cat_mesh is None or self._grip_cat_mesh[:2] != (root, ref):      # mientras se calcula, la mano tal cual está
            try:
                from ..catalog import Catalog

                self._grip_cat_mesh = (root, ref, Catalog.open(root).load(ref)[1])
            except Exception:                                    # noqa: BLE001 — sin la malla simplemente no se dibuja nada
                self._grip_cat_mesh = None
        if self._grip_cat_mesh is not None:
            scene.set_mesh("agarre:mano", self._grip_cat_mesh[2], color=_HAND_COLOR)
        return True

    def sync(self, scene) -> None:
        """Vuelca el estado al `SceneRenderer` (llamar con el contexto GL activo)."""
        sess = self.session
        if self._sync_grip_standalone(scene):
            return
        if self.weapon_active and self.weapon is not None and self.weapon.loaded:
            self._sync_weapon(scene)
            return
        if sess.work is None:
            scene.clear()
            return
        parts = self.bambu_view_parts() if self.show_bambu else None
        if parts:                                                    # vista previa del 3MF: colores planos en lugar de la figura gris
            self._sync_bambu(scene, parts)
            return
        for key in [k for k in scene.items if k.startswith("bambu:")]:
            scene.remove(key)
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
        parts_fig = pin_plane is None and hfit is None and self.parts_replace_figure()
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
        elif parts_fig:
            # las piezas de catálogo reemplazan a la figura: se ven ellas (con su espejo) y no el modelo original
            scene.remove("figura")
            scene.remove("voladizos")
            drawn = [(f"parte:{pl}", pv.mesh, _MIRROR_COLOR) for pl, pv in self.session.part_preview_items().items()
                     if pv.mesh is not None and pv.kind not in ("arm", "leg")       # brazos y piernas ya llevan su espejo
                     and not (pl == "cabeza" and self._replacement_head_shown())]
        else:
            tpart = self._texture_for_scene() if (pin_plane is None and hfit is None and hprev is None) else None
            if tpart is not None:                                   # vista previa: el FBX con su textura en lugar de la malla gris
                scene.set_textured_mesh("figura", tpart.mesh, tpart.corner_uv, tpart.image, clip_x=clip, key=id(tpart), **extra)
            else:
                scene.set_mesh("figura", base, clip_x=clip, **extra)
            scene.remove("voladizos")
            drawn = [("figura", base, _MIRROR_COLOR)]
        if hfit is not None:
            pv = self.grip_preview_meshes(hfit)              # previsualización del agarre (sin taladrar): mano taladrada en memoria + espada
            shown = hfit["hand"] if pv is None else pv[0]
            scene.set_mesh("mano:reemplazo", shown, color=_REPLACEMENT_HAND_COLOR, clip_x=clip)
            drawn.append(("mano:reemplazo", shown, _MIRROR_COLOR))
            acc = self.grip_prop_preview(hfit) if pv is None else pv[1]             # sin espejo: la espada va en una sola mano
            if acc is not None:
                scene.set_mesh("accesorio:agarre", acc, color=_ACCESSORY_COLOR, clip_x=clip)
            else:
                scene.remove("accesorio:agarre")
        else:
            scene.remove("mano:reemplazo")
            scene.remove("accesorio:agarre")
        if hprev is not None:
            scene.set_mesh("cabeza:reemplazo", hprev[0], color=_REPLACEMENT_HEAD_COLOR, clip_x=clip)
            if pin_plane is None and not parts_fig:    # el espejo no lleva la cabeza original (la nueva no se simetriza)
                drawn = [("figura", self._work_without_head(), _MIRROR_COLOR)] + [d for d in drawn if d[0] == "mano:reemplazo"]
        else:
            scene.remove("cabeza:reemplazo")
        hand = self.hand_preview()
        if hand is not None:
            scene.set_mesh("mano:extraida", hand, color=_EXTRACTED_HAND_COLOR, clip_x=clip)
        else:
            scene.remove("mano:extraida")
        self._sync_part_previews(scene, clip)
        self._sync_cut_numbers(scene)
        hs = self.plane_half_size
        palette = {"symmetry": (1.0, 0.55, 0.2, 0.18), "arm": (0.3, 0.6, 1.0, 0.22), "leg": (0.4, 0.85, 0.5, 0.22),
                   "head": (0.9, 0.5, 0.9, 0.22), "joint": (1.0, 0.85, 0.2, 0.22),
                   "waist": (0.95, 0.35, 0.35, 0.22)}
        wanted = set()
        status = self.plane_status()
        for e in self.plane_entries():
            key = f"plano:{e.id}"
            wanted.add(key)
            color = _PLANE_STATUS_COLORS.get((status.get(e.id) or ("",))[0], palette[e.kind])
            scene.set_plane(key, e.plane, hs, center=sess.work.bounds.mean(axis=0), color=color)
            scene.show(key, e.visible and self.group_visible("planes"))
            cut = self.cut_plane_to_draw(e)                     # feedback de «Corte a»: dónde corta de verdad, aparte del plano de la silueta
            if cut is not None:
                ckey = f"plano:{e.id}:corte"
                wanted.add(ckey)
                scene.set_plane(ckey, cut, hs, center=sess.work.bounds.mean(axis=0), color=_CUT_PLANE_COLOR)
                scene.show(ckey, e.visible and self.group_visible("planes"))
        for key in [k for k in scene.items if k.startswith("plano:") and not k.endswith(":borde")]:
            if key not in wanted:
                scene.remove(key)
        if self.hover_vertex is not None and self.hover_active():
            scene.set_points("hover:vertice", self.hover_vertex[None], (1.0, 0.0, 0.0, 1.0), 18.0, on_top=True)
        else:
            scene.remove("hover:vertice")
        if self.pending and self.mode != Mode.JOINT_LINES:
            pts = np.array(self.pending)
            scene.set_points("pendientes", pts, (1.0, 0.25, 0.25, 1.0), 14.0, on_top=True)     # sin prueba de profundidad: no quedan tapados
            scene.set_lines("pendientes:lineas", pts, (1.0, 0.25, 0.25, 1.0), closed=len(pts) == 3, on_top=True)
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
        self._sync_waist_pin(scene)
        self._sync_joint_aim(scene)
        self._sync_cut_aim(scene)
        self._sync_cut_ref(scene)
        self._sync_cut_ghost(scene)
        self._sync_joint_strip(scene)
        self._sync_joints(scene)
        self._sync_trims(scene)
        self._sync_mirror(scene, drawn, pin_plane)
        self._sync_joint_preview(scene)

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

    def _sync_part_previews(self, scene, clip: float) -> None:
        """Dibuja las piezas de catálogo elegidas YA colocadas (vista previa de la revisión, sin generar), pintadas por
        nivel de acople: verde = ok, ámbar = aviso, rojo = no. Brazos y piernas también del lado −X (espejo)."""
        wanted = set()
        if self.show_part_preview and self.outcome_free():
            for place, pv in self.session.part_preview_items().items():
                if pv.mesh is None or (place == "cabeza" and self._replacement_head_shown()):
                    continue
                color = _PART_PREVIEW_COLORS.get(pv.nivel, _PART_PREVIEW_COLORS["aviso"])
                key = f"parte:{place}"
                scene.set_mesh(key, pv.mesh, color=color, clip_x=clip)
                wanted.add(key)
                if pv.kind in ("arm", "leg"):
                    m = pv.mesh.copy()
                    m.vertices = m.vertices * np.array([-1.0, 1.0, 1.0])
                    m.invert()
                    scene.set_mesh(key + ":espejo", m, color=color, clip_x=-1e9 if clip < 0 else clip)
                    wanted.add(key + ":espejo")
        for key in [k for k in scene.items if k.startswith("parte:") and k not in wanted]:
            scene.remove(key)

    def outcome_free(self) -> bool:
        """True mientras no hay una figura generada con estas piezas (después se ve la generada, no la previa)."""
        return self.session.outcome is None

    def _sync_bambu(self, scene, parts) -> None:
        """Lo que saldrá en el 3MF: la figura entera (sin recorte ni espejo), un color liso por malla."""
        scene.clear()
        for name, mesh, rgba in parts:
            scene.set_mesh(f"bambu:{name}", mesh, color=tuple(rgba))

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
        plane = Plane.from_normal((center[0], center[1], z), (0.0, 0.0, 1.0), tr("controller.corte_con_la_cama_de_impresion_3"))
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
            scene.show(key, t.target not in self.hidden and self.group_visible("contours"))
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
                                      (0.3, 0.85, 1.0, 1.0)),
                                     (f"espejo:silueta3:{l.name}", None if l.cross2 is None else l.cross2.polygon,
                                      (0.75, 0.55, 1.0, 1.0))):
                being_edited = editing is not None and l.name == self.edit_limb and self._edit_kind() == key.split(":")[1]
                if being_edited or poly is None or len(poly) < 3:
                    continue
                wanted.add(key)
                scene.set_lines(key, mirrored_points(poly), color, closed=True, on_top=True, width=SILHOUETTE_PX)     # la silueta vive dentro de la figura: sin prueba de profundidad no se vería
                scene.show(key, not hidden and self.group_visible("contours"))
            if l.pin_center is not None:
                key = f"espejo:pasador:{l.name}"
                wanted.add(key)
                scene.set_points(key, mirrored_points(np.asarray(l.pin_center, float)[None]), (1.0, 0.55, 0.1, 1.0), 10.0)
                scene.show(key, not hidden and self.group_visible("pins"))
        for i, t in enumerate(self.session.project.trims):
            if i == (self.edit_trim if editing is not None else None):
                continue
            key = f"espejo:recorte:{i}"
            wanted.add(key)
            scene.set_lines(key, [mirrored_points(w) for w in self.trim_wires(t)],
                            (0.95, 0.3, 0.3, 1.0) if t.op == "subtract" else (0.35, 0.85, 0.4, 1.0), closed=True)
            scene.show(key, self.group_visible("contours"))
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
            scene.show(key, l.name not in self.hidden and self.group_visible("pins"))
        pin_keys = ("pasador:cara", "pasador:borde", "pasador:silueta", "pasador:vastago", "pasador:cabeza",
                    "pasador:centro", "pasador:auto", "pasador:mira", "pasador:mira_cabeza",
                    "pasador:calor_ok", "pasador:calor_aviso", "pasador:calor_no",
                    "pasador:fantasma", "pasador:hueco", "pasador:hueco_borde", "pasador:sobra")
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
        self._sync_pin_heat(scene)
        self._sync_pin_ghost(scene, prev, name, pin)
        aim = self.pin_aim if self._pin_aim_active() else None
        if aim is None:                                 # el fantasma de la mira: vástago y cabeza con el color del nivel
            scene.remove("pasador:mira")
            scene.remove("pasador:mira_cabeza")
        else:
            g = self.pin_aid.center_world(aim.uv)
            scene.set_lines("pasador:mira", circle_world(prev.plane, g, pin.shaft_d / 2), aim.color, closed=True, on_top=True)
            scene.set_lines("pasador:mira_cabeza", circle_world(prev.plane, g, pin.head_d / 2), aim.color, closed=True,
                            on_top=True)

    def _sync_pin_ghost(self, scene, prev, name: str, pin) -> None:
        """El pasador y su hueco como fantasma translúcido sobre la cara, donde está el puntero (o, si no hay puntero, en el centro
        actual). Verde: entra; amarillo: entra justo (borde menor que la pared mínima); rojo: no entra, y en rojo fuerte la parte
        del vástago que sobresale de la cara. El aro más grande es el hueco que se vacía en el torso (vástago + holgura)."""
        keys = ("pasador:fantasma", "pasador:hueco", "pasador:hueco_borde", "pasador:sobra")
        try:
            uv = self.pin_hover if self.pin_hover is not None else prev.project(self._pin_center(name))
            fit = self.session.pin_fit_at(name, uv)
            r, g, b, _a = _pin_aid_colors()[fit.level]
            clearance = self.session.project.params.clearance
            center = prev.plane.to_world(np.asarray(uv, float)[None])[0]
            meshes = ghost_meshes(center, prev.face.outward, pin, clearance)
        except Exception:      # noqa: BLE001 — el fantasma es solo ayuda: nunca debe romper el modo PIN
            for key in keys:
                scene.remove(key)
            return
        scene.set_mesh("pasador:fantasma", meshes["pasador"], color=(r, g, b, 0.55), ghost=True)
        scene.set_mesh("pasador:hueco", meshes["hueco"], color=(r, g, b, 0.18), ghost=True)
        scene.set_lines("pasador:hueco_borde", circle_world(prev.plane, center, pin.shaft_d / 2 + clearance), (r, g, b, 1.0),
                        closed=True, on_top=True)
        rings = []
        if fit.overflow is not None:
            geoms = list(fit.overflow.geoms) if hasattr(fit.overflow, "geoms") else [fit.overflow]
            rings = [prev.plane.to_world(np.asarray(gm.exterior.coords)) for gm in geoms if gm.geom_type == "Polygon" and not gm.is_empty]
        if rings:
            scene.set_lines("pasador:sobra", rings, (1.0, 0.1, 0.1, 1.0), closed=True, on_top=True)
        else:
            scene.remove("pasador:sobra")

    # ------------------------------------------------ mira del corte (cinta de calor + fantasma de la sección)
    CUT_KEYS = ("ok", "aviso", "no", "fuera")

    def toggle_cut_aim(self) -> None:
        self.cut_aim_visible = not self.cut_aim_visible
        self._changed((tr("controller.mira_del_corte") + ' ') + ("visible." if self.cut_aim_visible else "oculta."))

    def cut_aim(self) -> dict | None:
        """Lo que la mira del corte muestra para el miembro cuyo desplazamiento se edita: `{limb, reading, strip, best, improves}`,
        o `{limb, note}` con el motivo si no se puede medir (sin catálogo, extrusión «symmetric»…). None si no hay un miembro
        con desplazamiento, o se está en otro modo. Nunca levanta: la ayuda no debe romper la edición."""
        if not self.session.loaded or self.mode == Mode.PIN or self.busy:
            return None
        name = self.offset_limb()
        if name is None:
            return None
        try:
            aid = self.session.cut_aid(name)
            cur = self.session.limb_offset(name)
            key = (id(aid), round(float(cur), 3))
            last = getattr(self, "_cut_aim_cache", None)
            if last is not None and last[0] == key:
                return last[1]
            strip = aid.strip(cur)
            best, improves = aid.best(cur)
            out = {"limb": name, "reading": aid.reading_at(cur), "strip": strip, "best": best, "improves": improves}
        except (ValueError, KeyError, OSError) as e:
            out = {"limb": name, "note": str(e)}
        except Exception as e:  # noqa: BLE001
            out = {"limb": name, "note": tr("controller.no_se_pudo_medir_el_corte", e=e)}
        if "note" not in out:
            self._cut_aim_cache = (key, out)
        return out

    def cut_aim_text(self) -> str:
        """Línea para la ventana: «corte a 12,50 mm · torsos 3/4 · brazos 17/21 · pared 1,4 mm — mejor: 13,25 mm (…)»."""
        if not self.cut_aim_visible:
            return ""
        aim = self.cut_aim()
        if aim is None:
            return ""
        if "note" in aim:
            return (tr("controller.mira_del_corte_2") + ' ') + aim["note"]
        txt = aim["reading"].texto()
        best = aim["best"]
        if best is not None and aim["improves"]:
            txt += (' ' + tr("controller.mejor_cerca_offset_mm_mm_contador", offset_mm=f"{best.offset_mm:.2f}", contador=best.contador())).replace(".", ",")
        elif best is not None:
            txt += (' ' + tr("controller.es_la_mejor_posicion_de_la"))
        cs = self._cut_seated_now(aim)
        if cs is not None:
            txt += ((' ' + tr("controller.choca_sentado") + ' ') + tr("controller.n_de_total_piezas", n=len(cs['choques']), total=cs['total']) if cs["choca"]
                    else (' ' + tr("controller.sentado_sin_choque_total_piezas", total=cs['total'])))
        return txt

    def _sync_cut_aim(self, scene) -> None:
        """El fantasma de la sección del corte (contorno pintado con el nivel) y la cinta: un punto verde / amarillo / rojo cada
        `STEP_MM` a lo largo de la distancia al plano de simetría, desde la cara de contacto actual; el blanco es la mejor posición."""
        keys = [f"corte:mira_{i}" for i in range(6)] + [f"corte:cinta_{k}" for k in self.CUT_KEYS] + ["corte:mejor", "corte:sentado"]
        aim = self.cut_aim() if self.cut_aim_visible else None
        if aim is None or "note" in aim:
            for k in keys:
                scene.remove(k)
            return
        cur = aim["reading"]
        rings = cur.rings_world()
        for i in range(6):
            if i < len(rings):
                scene.set_lines(f"corte:mira_{i}", rings[i], cur.color, closed=True, on_top=True)
            else:
                scene.remove(f"corte:mira_{i}")
        spec = self.session.limb(aim["limb"])
        axis = np.asarray(spec.contact_axis, float)
        axis = axis / np.linalg.norm(axis)
        base = next((r.prev.plane.origin for r in [cur] + list(aim["strip"]) if r.prev is not None), None)
        groups: dict[str, list] = {k: [] for k in self.CUT_KEYS}
        if base is not None:
            for r in aim["strip"]:
                groups.setdefault(r.nivel, []).append(np.asarray(base, float) + axis * (r.offset_mm - cur.offset_mm))
        for nivel in self.CUT_KEYS:
            key = f"corte:cinta_{nivel}"
            if not groups.get(nivel):
                scene.remove(key)
                continue
            r_, g_, b_, _a = _pin_aid_colors()[nivel]
            scene.set_points(key, np.array(groups[nivel]), (r_, g_, b_, 0.85), 8.0, on_top=True)
        best = aim["best"]
        if base is not None and best is not None:
            scene.set_points("corte:mejor", (np.asarray(base, float) + axis * (best.offset_mm - cur.offset_mm))[None],
                             (1.0, 1.0, 1.0, 1.0), 12.0, on_top=True)
        else:
            scene.remove("corte:mejor")
        cs = self._cut_seated_now(aim)                       # el tercer color: violeta donde se midió y chocan en la pose sentada
        if base is not None and cs is not None and cs["choca"]:
            scene.set_points("corte:sentado", (np.asarray(base, float) + axis * (cs["offset"] - cur.offset_mm))[None],
                             (0.72, 0.35, 0.95, 1.0), 16.0, on_top=True)
        else:
            scene.remove("corte:sentado")

    # ---------------------------------------------------------------- referencia del corte (figura ya cortada)
    def _cut_refs(self) -> dict[str, dict]:
        """`{figura: {kind: RefCut}}` del catálogo abierto, con caché mientras no cambie su índice (CATALOGO.md se reescribe en cada
        cambio): la guía se redibuja en cada cuadro y no debe releer las fichas."""
        root = self.session.catalog_root
        if not root:
            return {}
        try:
            stamp = os.stat(os.path.join(root, "CATALOGO.md")).st_mtime_ns
        except OSError:
            stamp = 0
        last = getattr(self, "_cut_ref_cache", None)
        if last is not None and last[0] == (root, stamp):
            return last[1]
        try:
            refs = self.session.catalog_cut_refs()
        except Exception:                                      # noqa: BLE001 — la guía es informativa
            refs = {}
        self._cut_ref_cache = ((root, stamp), refs)
        return refs

    def cut_ref_figures(self) -> list[str]:
        return sorted(self._cut_refs())

    def cut_ref_figure(self) -> str | None:
        """La figura de referencia en uso: la elegida si sigue en el catálogo, si no la propuesta (Güemes si está)."""
        figs = self.cut_ref_figures()
        return self.cut_ref_name if self.cut_ref_name in figs else CR.default_reference(figs)

    def set_cut_ref_figure(self, figura: str | None) -> None:
        if figura == self.cut_ref_name:
            return
        self.cut_ref_name = figura
        self._changed(tr("controller.referencia_del_corte_v", v=self.cut_ref_figure() or 'ninguna'))

    def toggle_cut_ref(self) -> None:
        self.cut_ref_visible = not self.cut_ref_visible
        self._changed((tr("controller.guia_de_la_referencia") + ' ') + ("visible." if self.cut_ref_visible else "oculta."))

    def cut_ref_rows(self) -> list[dict]:
        """Un renglón por brazo / pierna sobre el plano de simetría que tenga corte de referencia: `{id, kind, label, corte_mm, ref,
        lectura, texto}`. Vacío sin catálogo, sin figura de referencia o con el modelo sin cargar."""
        if not self.session.loaded:
            return []
        figura = self.cut_ref_figure()
        cuts = self._cut_refs().get(figura or "", {})
        height = self.session.project.params.ref_height
        out = []
        for spec in self.session.project.limbs:
            if spec.kind not in CR.KINDS or spec.kind not in cuts:
                continue
            try:
                cur = self.session.limb_offset(spec.name)
            except KeyError:
                continue
            if cur is None:
                continue
            ref = cuts[spec.kind]
            out.append({"id": spec.name, "kind": spec.kind, "label": spec.name, "corte_mm": cur, "ref": ref,
                        "lectura": CR.reading(cur, ref, height), "texto": CR.readout_text(spec.name.capitalize(), cur, ref, height)})
        return out

    def cut_ref_text(self) -> str:
        """Texto de la ventana bajo «Corte a»: una línea por brazo / pierna sobre la simetría («Brazo: tu corte a 6,20 mm · Güemes corta
        a 7,70 mm → mové +1,50 mm»); con varios, el que se está editando lleva «▶». Se ve aunque no haya un miembro activo (sirve para
        planear los cortes); si falta algo para mostrarlo, dice qué."""
        if not self.session.loaded:
            return ""
        try:
            if not any(self.session.limb_offset(l.name) is not None for l in self.session.project.limbs if l.kind in CR.KINDS):
                return ""
        except KeyError:
            return ""
        if not self.session.catalog_root:
            return tr("controller.referencia_del_corte_abri_un_catalogo")
        if not self.cut_ref_figures():
            return tr("controller.referencia_del_corte_el_catalogo_no")
        rows = self.cut_ref_rows()
        if not rows:
            return tr("controller.referencia_del_corte_la_figura_elegida")
        active = self.offset_limb()
        return "\n".join((("▶ " if r["id"] == active else "") if len(rows) > 1 else "") + r["texto"] for r in rows)

    def match_cut_to_ref(self, name: str | None = None) -> bool:
        """«Igualar a la referencia»: lleva el corte de `name` (por defecto el miembro activo) a la distancia que corta la figura de
        referencia. Es el mismo movimiento que «Corte a», así que el pasador se reinicia."""
        name = name or self.offset_limb()
        row = next((r for r in self.cut_ref_rows() if r["id"] == name), None)
        if row is None:
            self._changed(tr("controller.no_hay_una_referencia_de_corte"),
                          scene=False)
            return False
        self.set_limb_offset(name, row["ref"].mm)
        return True

    def _sync_cut_ref(self, scene) -> None:
        """La guía en el visor: un plano celeste a la distancia a la que corta la referencia, aparte del plano blanco del corte actual
        (el de la silueta no se mueve). Se ve de canto como una línea en la vista de frente. Es solo la del miembro que se está
        editando (el mismo que mueve «Corte a»): al pasar de brazo a pierna la guía del brazo se retira. Sigue la casilla de
        visibilidad de ese plano en la lista, como el plano blanco del corte."""
        wanted = set()
        active = self.offset_limb() if (self.cut_ref_visible and self.session.loaded and not self.busy) else None
        if active is not None:
            sess = self.session
            visible = next((e.visible for e in self.plane_entries() if e.id == active), True)
            for r in self.cut_ref_rows():
                if r["id"] != active:
                    continue
                try:
                    plane = cut_plane(sess.limb(r["id"]), r["ref"].mm)
                except (ValueError, KeyError):
                    continue
                if plane is None:
                    continue
                key = f"corteref:{r['id']}"
                wanted.add(key)
                scene.set_plane(key, plane, self.plane_half_size, center=sess.work.bounds.mean(axis=0), color=_CUT_REF_COLOR)
                scene.show(key, visible and self.group_visible("planes"))
        for key in [k for k in scene.items if k.startswith("corteref:") and not k.endswith(":borde") and k not in wanted]:
            scene.remove(key)

    def _cut_ghost_meshes(self) -> dict:
        """Las mallas de la figura de referencia ya llevadas a la figura abierta (`cut_ref.place_ghost`), con caché mientras no cambien el
        catálogo, la referencia ni las medidas del modelo: cargarlas del disco y moverlas es lo caro."""
        figura = self.cut_ref_figure()
        sess = self.session
        if not (figura and sess.loaded and sess.catalog_root):
            return {}
        try:
            stamp = os.stat(os.path.join(sess.catalog_root, "CATALOGO.md")).st_mtime_ns
        except OSError:
            stamp = 0
        bounds = np.asarray(sess.work.bounds, float)
        key = (sess.catalog_root, stamp, figura, tuple(np.round(bounds.ravel(), 3)))
        last = getattr(self, "_cut_ghost_cache", None)
        if last is not None and last[0] == key:
            return last[1]
        try:
            raw = sess.catalog_reference_meshes(figura)
            self._cut_ghost_raw = raw
            placed = CR.place_ghost(raw, bounds)
        except Exception:                                      # noqa: BLE001 — el fantasma es informativo
            placed = {}
        self._cut_ghost_cache = (key, placed)
        return placed

    def toggle_cut_ghost(self) -> None:
        """Muestra / oculta el fantasma. Al mostrarlo avisa si la altura de la referencia y la de la figura difieren (el fantasma no
        se escala: usa los mm de la referencia, igual que la guía)."""
        self.cut_ghost_visible = not self.cut_ghost_visible
        if not self.cut_ghost_visible:
            self._changed(tr("controller.fantasma_de_la_referencia_oculto"))
            return
        msg = tr("controller.fantasma_de_la_referencia_v_visible", v=self.cut_ref_figure() or 'ninguna')
        placed = self._cut_ghost_meshes()
        if not placed:
            msg += (' ' + tr("controller.no_hay_piezas_de_esa_figura"))
        else:
            ref_h, my_h = CR.body_height(placed), float(self.session.work.bounds[1][2] - self.session.work.bounds[0][2])
            if my_h > 0 and abs(ref_h - my_h) / my_h > 0.03:
                msg += (' ' + tr("controller.ojo_la_referencia_mide_ref_h", ref_h=f"{ref_h:.0f}", my_h=f"{my_h:.0f}"))
        self._changed(msg)

    def _sync_cut_ghost(self, scene) -> None:
        """El fantasma en el visor: las piezas de la referencia que rodean al miembro que se edita (torso + brazo, o pelvis + pierna),
        translúcidas y en su lugar. El corte de la referencia queda a la distancia de la guía celeste."""
        wanted = []
        active = self.offset_limb() if (self.cut_ghost_visible and self.session.loaded and not self.busy) else None
        if active is not None:
            placed = self._cut_ghost_meshes()
            try:
                kind = self.session.limb(active).kind
            except KeyError:
                kind = None
            if kind in CR.KINDS:
                wanted = [(f"corteghost:{p}", placed[p]) for p in CR.ghost_places(kind, placed)]
        if wanted:                                          # el STL original un poco translúcido: así el fantasma que queda adentro se ve
            for key in ("figura", "espejo:figura"):         # (sin fantasma no hace falta restaurar: la figura se sube opaca en cada sync)
                scene.set_translucent(key, _CUT_GHOST_MODEL_ALPHA)
        sig = tuple((k, id(m)) for k, m in wanted)
        if sig and sig == getattr(self, "_cut_ghost_sig", None) and all(k in scene.items for k, _m in wanted):
            return
        keep = {k for k, _m in wanted}
        for key in [k for k in scene.items if k.startswith("corteghost:") and k not in keep]:
            scene.remove(key)
        for key, mesh in wanted:
            scene.set_mesh(key, mesh, color=_CUT_GHOST_COLOR, ghost=True)
        self._cut_ghost_sig = sig

    def snap_cut(self, quiet: bool = False) -> bool:
        """El «imán» del corte: lleva el plano del miembro a la mejor posición de la cinta (±3 mm; solo cambia la distancia al plano
        de simetría, la silueta no se toca). `quiet`: sin mensaje cuando no hay nada que hacer (la tecla M no debe molestar).
        Devuelve si movió el corte."""
        aim = self.cut_aim() if self.cut_aim_visible else None
        if aim is None or "note" in aim:
            if not quiet:
                self._changed(tr("controller.el_iman_del_corte_necesita_la"), scene=False)
            return False
        best = aim["best"]
        if best is None or not aim["improves"]:
            if not quiet:
                self._changed(tr("controller.el_corte_ya_esta_en_la"), scene=False)
            return False
        name, mm = aim["limb"], round(float(best.offset_mm), 2)
        self.set_limb_offset(name, mm)
        if abs((self.session.limb_offset(name) or 0.0) - mm) > 1e-6:                     # `set_limb_offset` lo rechazó: ya dejó el motivo
            return False
        self._changed(tr("controller.iman_del_corte_name_a_mm", name=name, mm=f"{mm:.2f}", contador=best.contador()).replace(".", ",", 1))
        return True

    def cut_aim_box(self) -> dict | None:
        """Datos del recuadro de la sección (`pin_aid_view.draw_aim_box`) para el corte actual, o None si la mira no está disponible."""
        aim = self.cut_aim() if self.cut_aim_visible else None
        if aim is None or "note" in aim:
            return None
        try:
            key = (id(self.session.cut_aid(aim["limb"])), round(float(aim["reading"].offset_mm), 3))
            last = getattr(self, "_cut_box_cache", None)
            if last is None or last[0] != key:
                last = (key, self.session.cut_aid(aim["limb"]).box_data(aim["reading"].offset_mm))
                self._cut_box_cache = last
            return last[1]
        except Exception:  # noqa: BLE001 — el recuadro nunca debe romper la edición
            return None

    def _cut_seated_key(self, aim: dict) -> tuple:
        return (aim["limb"], id(self.session.cut_aid(aim["limb"])), round(float(aim["reading"].offset_mm), 3))

    def cut_seated_blocked(self) -> str | None:
        """Por qué no se puede medir el choque sentado del corte que se mira (None = se puede); lo usa el botón de la ventana."""
        name = self.offset_limb()
        return tr("controller.elegi_un_miembro_con_corte_sobre") if name is None else self.session.cut_seated_blocked(name)

    def cut_seated_job(self):
        """Prepara el choque en pose sentada del corte actual: `(clave, trabajo)`; `trabajo(progreso)` va en un hilo y `finish_cut_seated`
        guarda el resultado. Es la parte lenta de la mira (mallas de todo el catálogo), por eso se pide sobre una posición elegida."""
        aim = self.cut_aim() if self.cut_aim_visible else None
        if aim is None or "note" in aim:
            self._changed(tr("controller.el_choque_en_pose_sentada_necesita"), scene=False)
            return None
        key, name, off = self._cut_seated_key(aim), aim["limb"], float(aim["reading"].offset_mm)
        return key, (lambda _progress: self.session.cut_seated(name, off))

    def finish_cut_seated(self, key, res, err) -> None:
        if err is not None:
            self.cut_seated = None
            self._changed(tr("controller.no_se_pudo_medir_el_choque", err=err), scene=False)
            return
        self.cut_seated = dict(res, key=key)
        self._changed(res["texto"])

    def _cut_seated_now(self, aim: dict) -> dict | None:
        """El choque medido, si es del corte que se está mirando (si se movió el plano, el resultado ya no vale)."""
        cs = self.cut_seated
        try:
            return cs if cs is not None and cs["key"] == self._cut_seated_key(aim) else None
        except (ValueError, KeyError):
            return None

    def toggle_heat(self) -> None:
        self.heat_visible = not self.heat_visible
        self._changed((tr("controller.mapa_de_calor_del_pasador") + ' ') + ("visible." if self.heat_visible else "oculto."))

    HEAT_KEYS = ("ok", "aviso", "no")

    def _sync_pin_heat(self, scene) -> None:
        """El mapa de calor de la mira: puntos verde / amarillo / rojo sobre la cara, donde cabe el pasador. Se barre UNA vez
        (la primera vez que se dibuja en el modo PIN) y queda guardado en la `PinAid`; fuera del modo se saca de la escena."""
        groups = {}
        if self._pin_aim_active() and self.heat_visible:
            try:
                groups = self.pin_aid.heat_points()
            except Exception:                           # la ayuda nunca debe romper el modo PIN
                groups = {}
        for nivel in self.HEAT_KEYS:
            key = f"pasador:calor_{nivel}"
            pts = groups.get(nivel)
            if pts is None or len(pts) == 0:
                scene.remove(key)
                continue
            r, g, b, _a = _pin_aid_colors()[nivel]
            scene.set_points(key, pts, (r, g, b, 0.55), 5.0, on_top=True)

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
                scene.show(keys[4], self.group_visible("pins"))
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

    def _sync_waist_pin(self, scene) -> None:
        """Pasador de la cintura: un punto si se eligió a mano y, en WAIST_PIN, la cara de canto, el contorno de costado del
        pasador, el centro y el punto automático (como el del cuello)."""
        waist = self.session.project.waist
        keys = ("pasador_cintura:cara", "pasador_cintura:perfil", "pasador_cintura:centro", "pasador_cintura:auto",
                "pasador_cintura:elegido")
        prev = self.waist_pin_prev if self.mode == Mode.WAIST_PIN else None
        if waist is None or prev is None:
            for k in keys[:4]:
                scene.remove(k)
            if waist is not None and waist.pin_center is not None:
                scene.set_points(keys[4], np.asarray(waist.pin_center, float)[None], (1.0, 0.55, 0.1, 1.0), 10.0)
                scene.show(keys[4], self.group_visible("pins"))
            else:
                scene.remove(keys[4])
            return
        scene.remove(keys[4])
        c = self._waist_pin_center()
        scene.set_lines(keys[0], prev.trace(), (1.0, 1.0, 1.0, 1.0), closed=False, on_top=True)
        scene.set_lines(keys[1], prev.profile(c, self.session.project.params.waist_pin), (1.0, 0.55, 0.1, 1.0),
                        closed=True, on_top=True)
        scene.set_points(keys[2], c[None], (1.0, 0.25, 0.25, 1.0), 9.0, on_top=True)
        if waist.pin_center is not None:
            scene.set_points(keys[3], prev.default_center[None], (0.6, 0.9, 0.6, 1.0), 7.0, on_top=True)
        else:
            scene.remove(keys[3])

    JOINT_AIM_KEYS = tuple(f"pasador_union:calor_{n}" for n in ("ok", "aviso", "no")) + ("pasador_union:mira",)

    def _sync_joint_aim(self, scene) -> None:
        """La mira del pasador del cuello / la cintura: el mapa de calor sobre la recta de la cara (verde / amarillo / rojo, barrida
        UNA vez) y el fantasma de costado del pasador bajo el puntero con el color de su nivel."""
        if not self._joint_active():
            for k in self.JOINT_AIM_KEYS:
                scene.remove(k)
            return
        groups = {}
        if self.heat_visible:
            try:
                groups = self.pin_aid.heat_points()
            except Exception:                           # la ayuda nunca debe romper la colocación
                groups = {}
        for nivel in self.HEAT_KEYS:
            key = f"pasador_union:calor_{nivel}"
            pts = groups.get(nivel)
            if pts is None or len(pts) == 0:
                scene.remove(key)
                continue
            r, g, b, _a = _pin_aid_colors()[nivel]
            scene.set_points(key, pts, (r, g, b, 0.55), 5.0, on_top=True)
        aim = self.pin_aim
        if aim is None:
            scene.remove("pasador_union:mira")
            return
        pin = self.session.project.params.neck_pin if self.mode == Mode.HEAD_PIN else self.session.project.params.waist_pin
        scene.set_lines("pasador_union:mira", self.joint_prev.profile(self.pin_aid.center_world(aim.uv), pin), aim.color,
                        closed=True, on_top=True)

    # ------------------------------------------------ mira del plano del cuello / la cintura (cinta de calor y fantasma de la sección)
    JOINT_STRIP_KEYS = tuple(f"plano_union:cinta_{k}" for k in CUT_KEYS) + ("plano_union:mejor",) + tuple(
        f"plano_union:mira_{i}" for i in range(6))

    def set_joint_focus(self, lugar: str | None) -> None:
        """Qué plano muestra su cinta de color («cuello», «cintura» o None): lo fija la ventana según la sección abierta."""
        if lugar != self.joint_focus:
            self.joint_focus = lugar
            self.scene_dirty = True

    def joint_aim(self) -> dict | None:
        """Lo que muestra la mira del plano del cuello / de la cintura en foco: `{lugar, reading, strip, best, improves}`, o
        `{lugar, note}` con el motivo si no se puede medir. None sin foco o en un modo de colocación. Nunca levanta."""
        lugar = self.joint_focus
        if (lugar is None or not self.session.loaded or self.busy or self.mode in (Mode.PIN, Mode.HEAD_PIN, Mode.WAIST_PIN)):
            return None
        try:
            aid = self.session.joint_aid(lugar)
        except ValueError as e:
            return {"lugar": lugar, "note": str(e)}
        try:
            last = getattr(self, "_joint_aim_cache", None)
            if last is not None and last[0] == id(aid):
                return last[1]
            cur = aid.reading_at(0.0)
            strip = aid.strip(0.0)
            best, improves = aid.best(0.0)
            out = {"lugar": lugar, "reading": cur, "strip": strip, "best": best, "improves": improves, "aid": aid}
            self._joint_aim_cache = (id(aid), out)
            return out
        except Exception as e:  # noqa: BLE001 — la ayuda nunca debe romper la edición
            return {"lugar": lugar, "note": tr("controller.no_se_pudo_medir_e", e=e)}

    def joint_aim_text(self) -> str:
        """Una línea para la sección del cuello / la cintura: el nivel de hoy y la mejor posición de la cinta."""
        if not self.cut_aim_visible:
            return ""
        aim = self.joint_aim()
        if aim is None:
            return ""
        if "note" in aim:
            return aim["note"]
        cur, best = aim["reading"], aim["best"]
        txt = cur.texto()
        if best is not None and aim["improves"]:
            txt += (' ' + tr("controller.mejor_offset_mm_mm_contador", offset_mm=f"{best.offset_mm:+.1f}", contador=best.contador())).replace(".", ",")
        elif best is not None:
            txt += (' ' + tr("controller.ya_esta_en_la_mejor_posicion"))
        return txt

    def joint_aim_box(self) -> dict | None:
        aim = self.joint_aim() if self.cut_aim_visible else None
        if aim is None or "note" in aim:
            return None
        try:
            return aim["aid"].box_data(0.0)
        except Exception:  # noqa: BLE001
            return None

    def shift_joint_plane(self, lugar: str, delta: float) -> bool:
        """Desliza el plano del cuello / la cintura `delta` mm sobre su normal (se descarta el pasador elegido: era de la cara anterior)."""
        if self.busy:
            return False
        had_pin = self._joint_pin_chosen(lugar)
        try:
            self.session.shift_plane(lugar, delta)
        except ValueError as e:
            self._changed(tr("controller.no_se_movio_el_plano_e", e=e), scene=False)
            return False
        if self.mode in (Mode.HEAD_PIN, Mode.WAIST_PIN):
            self.cancel(quiet=True)
        self._changed(tr("controller.plano_v_corrido_delta_mm", v=tr("controller.del_cuello") if lugar == "cuello" else tr("controller.de_la_cintura"), delta=f"{delta:+.2f}").replace(".", ",")
                      + ((' ' + tr("controller.se_descarto_el_pasador_elegido")) if had_pin else ".") + (' ' + tr("controller.hay_que_generar_de_nuevo")))
        return True

    def _joint_pin_chosen(self, lugar: str) -> bool:
        spec = self.session.project.head if lugar == "cuello" else self.session.project.waist
        return spec is not None and spec.pin_center is not None

    def snap_joint(self, quiet: bool = False) -> bool:
        """El «imán» del plano en foco: lo desliza a la mejor posición de la cinta (±4 mm). Devuelve si lo movió."""
        aim = self.joint_aim() if self.cut_aim_visible else None
        if aim is None or "note" in aim:
            if not quiet:
                self._changed(tr("controller.el_iman_del_plano_necesita_la"),
                              scene=False)
            return False
        best = aim["best"]
        if best is None or not aim["improves"]:
            if not quiet:
                self._changed(tr("controller.el_plano_ya_esta_en_la"), scene=False)
            return False
        return self.shift_joint_plane(aim["lugar"], float(best.offset_mm) - float(aim["reading"].offset_mm))

    def _sync_joint_strip(self, scene) -> None:
        """La sección del plano en foco con el color de su nivel y la cinta: un punto por paso a lo largo de la normal del plano
        (blanco = la mejor posición)."""
        aim = self.joint_aim() if self.cut_aim_visible else None
        if aim is None or "note" in aim:
            for k in self.JOINT_STRIP_KEYS:
                scene.remove(k)
            return
        cur = aim["reading"]
        rings = cur.rings_world()
        for i in range(6):
            if i < len(rings):
                scene.set_lines(f"plano_union:mira_{i}", rings[i], cur.color, closed=True, on_top=True)
            else:
                scene.remove(f"plano_union:mira_{i}")
        normal = np.asarray(aim["aid"].base.normal, float)
        base = next((r.prev.face.center for r in [cur] + list(aim["strip"]) if r.prev is not None), None)
        groups: dict[str, list] = {k: [] for k in self.CUT_KEYS}
        if base is not None:
            for r in aim["strip"]:
                groups.setdefault(r.nivel, []).append(np.asarray(base, float) + normal * (r.offset_mm - cur.offset_mm))
        for nivel in self.CUT_KEYS:
            key = f"plano_union:cinta_{nivel}"
            if not groups.get(nivel):
                scene.remove(key)
                continue
            r_, g_, b_, _a = _pin_aid_colors()[nivel]
            scene.set_points(key, np.array(groups[nivel]), (r_, g_, b_, 0.85), 8.0, on_top=True)
        best = aim["best"]
        if base is not None and best is not None:
            scene.set_points("plano_union:mejor", (np.asarray(base, float) + normal * (best.offset_mm - cur.offset_mm))[None],
                             (1.0, 1.0, 1.0, 1.0), 12.0, on_top=True)
        else:
            scene.remove("plano_union:mejor")

    @staticmethod
    def _face_mesh(prev):
        t = np.asarray(prev.face.triangles, float)
        return t.reshape(-1, 3), np.arange(len(t) * 3).reshape(-1, 3)

    def _edit_kind(self) -> str:
        """Qué silueta del miembro se está editando: «silueta» (la principal), «silueta2» (de frente) o «silueta3» (desde arriba)."""
        return "silueta3" if self.edit_cross2 else "silueta2" if self.edit_cross else "silueta"

    def _sync_polygons(self, scene) -> None:
        editing = self.draft if self.mode == Mode.POLYGON else None
        wanted = set()
        for l in self.session.project.limbs:
            for key, poly, color in ((f"silueta:{l.name}", l.polygon, (1.0, 0.8, 0.2, 1.0)),
                                     (f"silueta2:{l.name}", None if l.cross is None else l.cross.polygon,
                                      (0.3, 0.85, 1.0, 1.0)),
                                     (f"silueta3:{l.name}", None if l.cross2 is None else l.cross2.polygon,
                                      (0.75, 0.55, 1.0, 1.0))):
                being_edited = editing is not None and l.name == self.edit_limb and self._edit_kind() == key.split(":")[0]
                if being_edited or poly is None or len(poly) < 3:
                    continue
                wanted.add(key)
                scene.set_lines(key, np.asarray(poly), color, closed=True, on_top=True, width=SILHOUETTE_PX)          # la silueta vive dentro de la figura: sin prueba de profundidad no se vería
                scene.show(key, l.name not in self.hidden and self.group_visible("contours"))
        for key in [k for k in scene.items if k.startswith(("silueta:", "silueta2:", "silueta3:")) and k not in wanted]:
            scene.remove(key)
        if editing is None:
            for key in ("guia", "poligono", "poligono:vertices", "poligono:sel"):
                scene.remove(key)
            return
        guide = self.guide_outline(editing.plane)
        scene.set_lines("guia", [editing.plane.to_world(g["exterior"]) for g in guide], GUIDE_COLOR, closed=True, on_top=True, width=GUIDE_PX)
        if editing.pts:
            w3 = editing.world()
            scene.set_lines("poligono", w3, DRAFT_COLOR, closed=editing.closed, on_top=True, width=DRAFT_PX)
            scene.set_points("poligono:vertices", w3, DRAFT_COLOR, 11.0, on_top=True)
            if editing.selected is not None:
                scene.set_points("poligono:sel", w3[[editing.selected]], DRAFT_SELECTED_COLOR, 17.0, on_top=True)
            else:
                scene.remove("poligono:sel")
        else:
            for key in ("poligono", "poligono:vertices", "poligono:sel"):
                scene.remove(key)
