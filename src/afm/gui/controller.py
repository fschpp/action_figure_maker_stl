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

from ..limbs import silhouette_on_plane
from ..orient import OrientResult, best_orientation, overhang_mask
from ..planes import Plane
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
    POLYGON = "silueta"
    PIN = "pasador"


@dataclass
class PlaneEntry:
    id: str                  # SYMMETRY_ID o el nombre del miembro
    label: str
    plane: Plane
    kind: str                # "symmetry" | "arm" | "leg"
    visible: bool = True


NUDGE_MM = 0.1                  # paso de las flechas al ajustar el pasador (Mayús = 10 veces más)


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
        self.pin_limb: str | None = None            # miembro cuyo pasador se está colocando (modo PIN)
        self.pin_prev = None                         # PinPreview de ese miembro (cara de contacto)
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

    def cancel(self, quiet: bool = False) -> None:
        was = self.mode
        self.mode, self.pending, self.pending_kind = Mode.VIEW, [], None
        self.draft, self.edit_limb, self.edit_cross = None, None, False
        self.pin_limb, self.pin_prev = None, None
        if not quiet:
            self._changed("Edición de silueta terminada." if was == Mode.POLYGON
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
        `which`: pasador de «hip» (cadera) o «shoulder» (hombro) a variar."""
        import os
        from .. import probes, solids as S
        p = self.session.project.params
        base = p.hip_pin if which == "hip" else p.shoulder_pin
        kw = {}
        if clearances is not None:
            kw["clearances"] = clearances
        if shaft_deltas is not None:
            kw["shaft_deltas"] = shaft_deltas
        cells = probes.probe_cells(p, base=base, **kw)
        os.makedirs(outdir, exist_ok=True)
        stl = os.path.join(outdir, "probetas.stl")
        S.to_trimesh(probes.build_probe(p, cells)).export(stl)
        txt = os.path.join(outdir, "probetas.txt")
        with open(txt, "w") as f:
            f.write(probes.labels_text(cells) + "\n")
        self._changed(f"Probetas ({len(cells)} articulaciones, pasador de {'cadera' if which == 'hip' else 'hombro'}) "
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
        if self.mode != Mode.POLYGON or d is None or self.edit_limb is None:
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
        else:
            self.pending.append(hit.point.copy())
            if len(self.pending) < 3:
                self._changed(f"Punto {len(self.pending)}/3. Backspace deshace el último; Esc cancela.")
            elif self.mode == Mode.SYMMETRY:
                self._finish_symmetry()
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
        return out

    def toggle_plane(self, plane_id: str) -> None:
        self.hidden ^= {plane_id}
        self._changed()

    def remove_plane(self, plane_id: str) -> None:
        if plane_id == SYMMETRY_ID or plane_id in (self.edit_limb, self.pin_limb):
            self.cancel(quiet=True)
        if plane_id == SYMMETRY_ID:
            self.session.set_symmetry(None)
            self.session.project.limbs, self.session.project.trims = [], []
            self._rebuild_picker()
            self.hidden.clear()
            self._changed("Simetría quitada (también los miembros: dependen de ella).")
            return
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
        clip = 0.0 if (self.one_side and sess.project.symmetry is not None) else -1e9
        cplane = self._pin_clip_plane()
        extra = {} if cplane is None else {"clip_plane": cplane}      # solo en el modo PIN
        found = self.overhang_faces() if self.show_overhangs else None
        if found is not None and found[0].any():
            mask = found[0]
            verts, faces = sess.work.vertices, sess.work.faces
            scene.set_mesh("figura", trimesh.Trimesh(verts, faces[~mask], process=False), clip_x=clip, **extra)
            scene.set_mesh("voladizos", trimesh.Trimesh(verts, faces[mask], process=False),
                           color=(0.95, 0.25, 0.25, 1.0), clip_x=clip, **extra)
        else:
            scene.set_mesh("figura", sess.work, clip_x=clip, **extra)
            scene.remove("voladizos")
        hs = self.plane_half_size
        palette = {"symmetry": (1.0, 0.55, 0.2, 0.18), "arm": (0.3, 0.6, 1.0, 0.22), "leg": (0.4, 0.85, 0.5, 0.22)}
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
