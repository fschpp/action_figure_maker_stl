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

from ..planes import Plane
from .camera import OrbitCamera
from .picking import Picker, PickResult
from .session import Session

SYMMETRY_ID = "simetria"
_KIND_BASE = {"arm": "brazo", "leg": "pierna"}


class Mode(Enum):
    VIEW = "ver"
    CROWN = "coronilla"
    SYMMETRY = "simetria"
    LIMB_PLANE = "plano_miembro"


@dataclass
class PlaneEntry:
    id: str                  # SYMMETRY_ID o el nombre del miembro
    label: str
    plane: Plane
    kind: str                # "symmetry" | "arm" | "leg"
    visible: bool = True


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
        self.mode, self.pending, self.pending_kind = Mode.VIEW, [], None
        if not quiet:
            self._changed("Cancelado.")

    def undo_point(self) -> None:
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

    # ----------------------------------------------------------------- clics
    def click(self, px: float, py: float, width: float, height: float) -> PickResult | None:
        """Clic sobre el visor (píxeles, y hacia abajo). Devuelve el punto elegido si hubo impacto."""
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
        except ValueError as e:
            self._changed(f"No sirve: {e}. Elegí 3 puntos de nuevo.")
            return
        self.session.add_limb(name, kind, plane)
        self.mode, self.pending_kind = Mode.VIEW, None
        self._changed(f"Plano «{name}» agregado. Falta dibujar su silueta.")

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

    # ---------------------------------------------------------------- escena
    def sync(self, scene) -> None:
        """Vuelca el estado al `SceneRenderer` (llamar con el contexto GL activo)."""
        sess = self.session
        if sess.work is None:
            scene.clear()
            return
        clip = 0.0 if (self.one_side and sess.project.symmetry is not None) else -1e9
        scene.set_mesh("figura", sess.work, clip_x=clip)
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
