"""Gestor del catálogo de piezas: la lógica (lista filtrable, textos, cámara 3/4, borrado). Sin GTK ni OpenGL, así se
prueba sin ventana; la ventana está en `catalog_window.py`.

Sirve para ver qué hay en el catálogo (con una vista previa 3/4 de cada pieza) y eliminar versiones de pieza, por ejemplo
las que se catalogaron por error desde un proyecto viejo."""
from __future__ import annotations

import numpy as np

from ..parts import PieceMeta
from .camera import OrbitCamera
from .picking import Picker

PREVIEW_YAW = 55.0          # 3/4: desde el frente (+Y) corrido hacia la derecha de la figura (+X), mirando un poco desde arriba
PREVIEW_PITCH = 22.0
ALL_TYPES = "(todos los tipos)"


def three_quarter_camera(bounds, aspect: float = 1.0) -> OrbitCamera:
    """Cámara en vista 3/4 (adelante, a un lado y algo desde arriba) que encuadra la caja `bounds` (2x3) entera.

    `aspect` = ancho / alto del área de dibujo: si es angosta se aleja para que no se recorte a los costados."""
    cam = OrbitCamera(yaw=PREVIEW_YAW, pitch=PREVIEW_PITCH)
    cam.frame(bounds)
    cam.distance /= min(max(aspect, 0.2), 1.0)
    return cam


ZOOM_MIN, ZOOM_MAX = 0.5, 30.0
ZOOM_STEP = 1.25


def clamp_zoom(zoom: float) -> float:
    return float(np.clip(zoom, ZOOM_MIN, ZOOM_MAX))


def zoom_about(zoom: float, pan, factor: float, u: float = 0.0, v: float = 0.0) -> tuple[float, tuple[float, float]]:
    """Nuevo (zoom, pan) tras multiplicar el zoom por `factor` dejando quieto el punto bajo el cursor.

    `pan` = desplazamiento del centro (derecha, arriba) en alturas de la vista sin zoom; `u`, `v` = posición del cursor respecto
    del centro de la imagen (derecha, arriba) en alturas de imagen (0, 0 = el centro). Con (0, 0) se acerca al centro."""
    z2 = clamp_zoom(zoom * factor)
    k = 1.0 / zoom - 1.0 / z2
    return z2, (pan[0] + u * k, pan[1] + v * k)


def preview_camera(bounds, aspect: float = 1.0, yaw_off: float = 0.0, pitch_off: float = 0.0,
                   zoom: float = 1.0, pan=(0.0, 0.0)) -> OrbitCamera:
    """La cámara 3/4 de la vista previa con la vista girada `yaw_off` / `pitch_off` (°), con `zoom` (1 = encuadre entero) y
    `pan` (ver `zoom_about`). La usan el dibujo Y los clics: si no fueran la misma cámara, el punto elegido no caería donde
    se hizo clic."""
    cam = three_quarter_camera(bounds, aspect)
    cam.yaw = (PREVIEW_YAW + yaw_off) % 360.0
    cam.pitch = float(np.clip(PREVIEW_PITCH + pitch_off, -89.0, 89.0))
    if pan[0] or pan[1]:
        cam.pan(-pan[0], pan[1], 1.0)                    # con el alto en 1, `pan` está en alturas de la vista sin zoom
    cam.distance /= clamp_zoom(zoom)
    return cam


def widget_to_image(x: float, y: float, widget_w: float, widget_h: float, img_w: int, img_h: int):
    """Clic en el widget (`Gtk.Picture` con `ContentFit.CONTAIN`: la imagen se escala para entrar entera y queda centrada) →
    píxel (px, py) de la imagen, o None si cayó en el margen de afuera."""
    if min(widget_w, widget_h, img_w, img_h) <= 0:
        return None
    k = min(widget_w / img_w, widget_h / img_h)
    ox, oy = (widget_w - img_w * k) / 2.0, (widget_h - img_h * k) / 2.0
    px, py = (x - ox) / k, (y - oy) / k
    if not (0.0 <= px <= img_w and 0.0 <= py <= img_h):
        return None
    return px, py


def piece_origin(meta: PieceMeta) -> str:
    """De qué figura salió la pieza («belgrano_viejo.stl», o la nota si es una pieza suelta)."""
    return str(meta.origen.get("figura") or meta.origen.get("nota") or "")


def matches(meta: PieceMeta, text: str = "", tipo: str | None = None) -> bool:
    """¿Pasa el filtro? `text` busca (sin mayúsculas) en la referencia, el tipo, el estado, las etiquetas, la figura de
    origen, el proyecto de origen y la fecha: «belgrano» muestra todo lo catalogado desde Belgrano."""
    if tipo and meta.tipo != tipo:
        return False
    words = (text or "").lower().split()
    if not words:
        return True
    hay = " ".join([meta.ref, meta.tipo, meta.estado, " ".join(meta.etiquetas), piece_origin(meta),
                    str(meta.origen.get("proyecto") or ""), str(meta.origen.get("fecha") or ""), meta.descripcion]).lower()
    return all(w in hay for w in words)


def row_text(meta: PieceMeta) -> str:
    """Una línea de la lista: «pierna-belgrano-01@2 · pierna · borrador · belgrano_viejo.stl»."""
    parts = [meta.ref, meta.tipo, meta.estado]
    if piece_origin(meta):
        parts.append(piece_origin(meta))
    return " · ".join(parts)


def detail_text(meta: PieceMeta, uses: list[str] | None = None) -> str:
    """Ficha de una pieza para el panel de la derecha."""
    size = np.asarray(meta.bbox[1], float) - np.asarray(meta.bbox[0], float)
    lines = [f"{meta.ref}", f"Tipo: {meta.tipo} · estado: {meta.estado} · etiquetas: {', '.join(meta.etiquetas) or '—'}",
             f"Tamaño: {size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f} mm · volumen {meta.volumen_mm3:.0f} mm³".replace(".", ",")]
    if meta.altura_figura_mm:
        lines.append(f"Altura de la figura de origen: {meta.altura_figura_mm:g} mm".replace(".", ","))
    org = piece_origin(meta)
    proj = meta.origen.get("proyecto")
    lines.append(f"Origen: {org or '—'}" + (f" · proyecto {proj}" if proj else "") + (f" · {meta.origen['fecha']}" if meta.origen.get("fecha") else ""))
    if meta.puertos:
        lines.append("Puertos: " + ", ".join(f"{n} ({p.rol})" for n, p in meta.puertos.items()))
    ag = (meta.mecanismo or {}).get("agarre")
    if ag and meta.tipo == "mano" and "diametro_taladro_mm" in ag:
        lines.append(f"Agarre: taladro Ø{ag['diametro_taladro_mm']:g} mm · canal {ag.get('largo_canal_mm', 0):g} mm · el dentado apoya "
                     f"en ≈ {ag.get('apoyo_dentado_grados', 0):.0f}°".replace(".", ","))
    elif ag and meta.tipo == "accesorio" and "taladro_mm" in ag:
        lines.append(f"Agarre: para taladro Ø{ag['taladro_mm']:g} mm · vástago Ø{ag.get('vastago_mm', 0):g} · dentado Ø{ag.get('dentado_mm', 0):g} · "
                     f"lengüeta {ag.get('largo_lengueta_mm', 0):g} mm · canal {ag.get('largo_canal_mm', 0):g} mm".replace(".", ","))
    if meta.descripcion:
        lines.append(meta.descripcion)
    if uses is not None:
        lines.append("Se usa en: " + ("; ".join(uses) if uses else "ninguna figura ni receta que se conozca"))
    return "\n".join(lines)


def port_markers(meta: PieceMeta, length: float) -> tuple[np.ndarray, list[np.ndarray]]:
    """Centros de los puertos (Nx3) y un segmento por puerto en la dirección de su eje (saliendo de la pieza)."""
    pts, segs = [], []
    for p in meta.puertos.values():
        c, a = np.asarray(p.frame.center, float), np.asarray(p.frame.axis, float)
        pts.append(c)
        segs.append(np.array([c, c + a * length]))
    return (np.array(pts) if pts else np.zeros((0, 3))), segs


def view_bounds(mesh, meta: PieceMeta | None = None) -> np.ndarray:
    """Caja (2x3) que hay que encuadrar: la pieza y las flechas de sus puertos (que pueden salir por fuera de la malla)."""
    b = np.asarray(mesh.bounds, float)
    if meta is None or not meta.puertos:
        return b
    pts, segs = port_markers(meta, float(max(mesh.extents)) * 0.25)
    allp = np.vstack([b] + [np.asarray(sg) for sg in segs] + [pts])
    return np.array([allp.min(axis=0), allp.max(axis=0)])


def confirm_texts(metas: list[PieceMeta], uses: dict[str, list[str]]) -> tuple[str, str]:
    """(mensaje, detalle) del diálogo de confirmación para eliminar `metas`. `uses` = {ref: dónde se usa}."""
    n = len(metas)
    msg = "¿Eliminar esta pieza del catálogo?" if n == 1 else f"¿Eliminar estas {n} piezas del catálogo?"
    shown = [m.ref for m in metas[:12]]
    detail = "\n".join(f"• {r}" for r in shown) + (f"\n… y {n - len(shown)} más" if n > len(shown) else "")
    used = {r: u for r, u in uses.items() if u}
    if used:
        detail += "\n\nOjo, se usan en:\n" + "\n".join(f"• {r}: {'; '.join(u)}" for r, u in list(used.items())[:8])
        detail += "\nLas recetas que las nombran van a fallar al armar; en la figura abierta esos lugares vuelven a la pieza del modelo."
    detail += "\n\nSe borran del disco (el STL y su ficha) y no se puede deshacer."
    return msg, detail


class PreviewRenderer:
    """Dibuja una pieza en vista 3/4 FUERA de pantalla (contexto EGL propio, independiente del visor de la ventana
    principal) y devuelve la imagen RGBA. Si no hay EGL / moderngl, `available` es False y `error` dice por qué: el resto del
    gestor (lista, borrado) sigue andando sin vista previa."""

    PIECE_COLOR = (0.80, 0.82, 0.86, 1.0)
    PORT_COLOR = (1.0, 0.62, 0.15, 1.0)
    MARK_COLOR = (1.0, 0.2, 0.2, 1.0)

    def __init__(self, width: int = 840, height: int = 630):
        self.width, self.height = width, height
        self.error: str | None = None
        self._off = None
        self._mesh = None            # la malla que ya está en la GPU (arrastrar para girar no la vuelve a subir)

    def _ensure(self):
        if self._off is None and self.error is None:
            try:
                from .render import OffscreenRenderer

                self._off = OffscreenRenderer(self.width, self.height)
            except Exception as exc:         # noqa: BLE001  (sin moderngl, sin EGL, sin GL 3.3…)
                self.error = f"{type(exc).__name__}: {exc}"
        return self._off

    @property
    def available(self) -> bool:
        return self._ensure() is not None

    def _marks(self, off, marks) -> None:
        """Puntos elegidos con clic (rojos, siempre visibles); sin puntos, se quitan."""
        if marks is not None and len(marks):
            off.scene.set_points("marcas", np.asarray(marks, float), self.MARK_COLOR, size=14.0, on_top=True)
        else:
            off.scene.remove("marcas")

    def render(self, mesh, meta: PieceMeta | None = None, yaw_off: float = 0.0, pitch_off: float = 0.0, marks=None,
               zoom: float = 1.0, pan=(0.0, 0.0)) -> np.ndarray:
        """Imagen RGBA (alto × ancho × 4, uint8) de `mesh` en vista 3/4; `yaw_off` / `pitch_off` giran la vista (°).
        `marks`: puntos (Nx3) a marcar encima, p. ej. los de la muñeca."""
        off = self._ensure()
        if off is None:
            raise RuntimeError(self.error or "sin vista previa")
        with off.ctx:                        # vuelve a poner este contexto como el actual (GTK tiene el suyo)
            if self._mesh is not mesh:
                off.scene.clear()
                off.scene.set_mesh("pieza", mesh, self.PIECE_COLOR)
                if meta is not None and meta.puertos:
                    pts, segs = port_markers(meta, float(max(mesh.extents)) * 0.25)
                    off.scene.set_points("puertos", pts, self.PORT_COLOR, size=11.0, on_top=True)
                    off.scene.set_lines("ejes", segs, self.PORT_COLOR, on_top=True)
                self._mesh = mesh
            self._marks(off, marks)
            return off.render(preview_camera(view_bounds(mesh, meta), off.width / off.height, yaw_off, pitch_off, zoom, pan))

    def render_parts(self, parts, yaw_off: float = 0.0, pitch_off: float = 0.0, marks=None, bounds=None,
                     zoom: float = 1.0, pan=(0.0, 0.0)) -> np.ndarray:
        """Imagen RGBA de varias piezas juntas, cada una con su color: `parts` = [(nombre, malla, color RGBA)]. `bounds` (2x3) fija el
        encuadre (por defecto, el de las piezas juntas). Si `parts` es el
        mismo objeto de la llamada anterior no se vuelve a subir a la GPU (arrastrar para girar solo mueve la cámara)."""
        off = self._ensure()
        if off is None:
            raise RuntimeError(self.error or "sin vista previa")
        if not parts:
            raise ValueError("no hay piezas para dibujar")
        with off.ctx:
            if self._mesh is not parts:
                off.scene.clear()
                for name, mesh, color in parts:
                    off.scene.set_mesh(name, mesh, color)
                self._mesh = parts
            if bounds is None:
                lo = np.min([np.asarray(m.bounds, float)[0] for _n, m, _c in parts], axis=0)
                hi = np.max([np.asarray(m.bounds, float)[1] for _n, m, _c in parts], axis=0)
                bounds = np.array([lo, hi])
            self._marks(off, marks)
            return off.render(preview_camera(bounds, off.width / off.height, yaw_off, pitch_off, zoom, pan))

    def clear(self) -> None:
        """Suelta la malla de la GPU (cuando ya no hay nada elegido)."""
        if self._off is not None and self._mesh is not None:
            with self._off.ctx:
                self._off.scene.clear()
        self._mesh = None

    def close(self) -> None:
        if self._off is not None:
            try:
                with self._off.ctx:
                    self._off.scene.clear()
                self._off.ctx.release()
            except Exception:                # noqa: BLE001
                pass
        self._off, self._mesh = None, None


class CatalogManager:
    """Estado del gestor sobre un `Controller`: lista filtrada, selección, malla de la vista previa y borrado."""

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self._mesh_cache: dict[str, tuple[str, object]] = {}      # ref -> (hash, malla): solo las últimas

    # ---- lista
    def pieces(self, text: str = "", tipo: str | None = None) -> list[PieceMeta]:
        """Todas las versiones del catálogo abierto que pasan el filtro."""
        tipo = None if tipo in (None, "", ALL_TYPES) else tipo
        return [m for m in self.ctrl.catalog_pieces() if matches(m, text, tipo)]

    def types(self) -> list[str]:
        return sorted({m.tipo for m in self.ctrl.catalog_pieces()})

    def uses(self, ref: str) -> list[str]:
        return self.ctrl.catalog_usages(ref)

    # ---- vista previa
    def mesh(self, ref: str):
        """Malla de la pieza (con un caché chico: elegir una pieza ya vista no vuelve a leer el STL)."""
        hit = self._mesh_cache.get(ref)
        meta_hash = next((m.sha256 for m in self.ctrl.catalog_pieces() if m.ref == ref), "")
        if hit is not None and hit[0] == meta_hash:
            return hit[1]
        _meta, mesh = self.ctrl.session.catalog_load(ref)
        if len(self._mesh_cache) >= 6:
            self._mesh_cache.pop(next(iter(self._mesh_cache)))
        self._mesh_cache[ref] = (meta_hash, mesh)
        return mesh

    def camera_for(self, ref: str, aspect: float = 1.0) -> OrbitCamera:
        return three_quarter_camera(self.mesh(ref).bounds, aspect)

    # ---- borrado
    def confirm_for(self, refs: list[str]) -> tuple[str, str]:
        by_ref = {m.ref: m for m in self.ctrl.catalog_pieces()}
        metas = [by_ref[r] for r in refs if r in by_ref]
        return confirm_texts(metas, {m.ref: self.uses(m.ref) for m in metas})

    def delete(self, refs: list[str]) -> bool:
        ok = self.ctrl.catalog_delete(refs)
        for r in refs:
            self._mesh_cache.pop(r, None)
        return ok


# ------------------------------------------------------------------ cortar la mano de un brazo del catálogo
HAND_COLOR = (0.30, 0.78, 0.40, 1.0)          # la mano cortada, en verde (como en «Extraer mano estándar»)
ARM_COLOR = PreviewRenderer.PIECE_COLOR
WRIST_POINTS = 3
WRIST_HINTS = ("Punto 1 de 3: tocá el lado del PULGAR de la muñeca (siempre en el mismo lugar: fija cómo queda girada la mano).",
               "Punto 2 de 3: tocá un lado de la muñeca, del otro costado del brazo.",
               "Punto 3 de 3: tocá el tercer punto de la muñeca (los 3 tienen que formar un triángulo, no una línea).")


def can_cut_wrist(meta: PieceMeta | None) -> tuple[bool, str]:
    """¿Se le puede cortar la mano a esta pieza? (sí/no, motivo cuando no)."""
    if meta is None:
        return False, "Elegí un brazo de la lista."
    if meta.tipo != "brazo":
        return False, f"«{meta.ref}» es una pieza «{meta.tipo}»: solo se le corta la mano a un brazo."
    if "muneca" in meta.puertos:
        return False, f"«{meta.ref}» ya está sin mano (tiene el puerto «muneca»): elegí la versión con la mano pegada."
    return True, ""


class WristCutter:
    """Modo «cortar la mano» del gestor: 3 clics sobre la vista previa del brazo → corte calculado (sin guardar) → guardar.

    Sin GTK: la ventana solo le pasa los clics (en píxeles del widget) y dibuja lo que pide `marks()` / `preview_parts()`."""

    def __init__(self, ctrl, meta: PieceMeta, mesh):
        self.ctrl, self.meta, self.mesh = ctrl, meta, mesh
        self.points: list[np.ndarray] = []
        self.invert = False
        self.plan = None
        self.error = ""
        self.hand_id = ""                      # vacío = el de siempre (mano-<resto del id del brazo>)
        self._picker: Picker | None = None
        self._parts = None                     # lo que devuelve `preview_parts` (el mismo objeto mientras el corte no cambie)

    # ---- estado
    @property
    def complete(self) -> bool:
        return len(self.points) >= WRIST_POINTS

    def marks(self) -> np.ndarray:
        return np.array(self.points) if self.points else np.zeros((0, 3))

    def message(self) -> str:
        if not self.complete:
            return WRIST_HINTS[len(self.points)]
        if self.plan is None:
            return f"No sirve: {self.error}. Usá «Deshacer el último punto» y probá de nuevo."
        p = self.plan
        txt = (f"Mano cortada (verde): muñeca Ø{2 * p.wrist_radius_mm:.1f} mm, {p.hand_volume_mm3:.0f} mm³. Si lo verde no es la mano, "
               "«Invertir lado»; si está bien, revisá el id de la mano y «Guardar».")
        return txt + ("  Avisos: " + "; ".join(p.warnings) if p.warnings else "")

    # ---- clics
    def pick_at(self, x: float, y: float, widget_size, image_size, yaw_off: float = 0.0, pitch_off: float = 0.0,
                zoom: float = 1.0, pan=(0.0, 0.0)) -> str:
        """Clic en (x, y) del widget; `widget_size` e `image_size` = (ancho, alto). Devuelve el mensaje para mostrar."""
        if self.complete:
            return "Ya están los 3 puntos: «Deshacer el último punto» para cambiar uno."
        img_w, img_h = image_size
        hit = widget_to_image(x, y, widget_size[0], widget_size[1], img_w, img_h)
        if hit is None:
            return "Ese clic cayó fuera de la imagen. " + self.message()
        cam = preview_camera(view_bounds(self.mesh, self.meta), img_w / img_h, yaw_off, pitch_off, zoom, pan)
        origin, direction = cam.ray(hit[0], hit[1], img_w, img_h)
        if self._picker is None:
            self._picker = Picker(self.mesh)
        res = self._picker.pick(origin, direction)
        if res is None:
            return "Ahí no hay pieza: hacé clic sobre el brazo. " + self.message()
        self.points.append(np.asarray(res.point, float))
        if self.complete:
            self._recompute()
        return self.message()

    def undo(self) -> str:
        if self.points:
            self.points.pop()
        self.plan, self.error, self._parts = None, "", None
        return self.message()

    def reset(self) -> str:
        self.points.clear()
        self.plan, self.error, self.invert, self._parts = None, "", False, None
        return self.message()

    def toggle_invert(self) -> str:
        self.invert = not self.invert
        if self.complete:
            self._recompute()
        return self.message()

    def set_hand_id(self, text: str) -> None:
        self.hand_id = (text or "").strip()

    # ---- corte
    def _recompute(self) -> None:
        self._parts = None
        try:
            self.plan = self.ctrl.session.catalog_wrist_plan(self.meta.ref, self.points, self.invert, self.hand_id or None)
            self.error = ""
        except ValueError as exc:
            self.plan, self.error = None, str(exc)

    def set_hand_id_and_recompute(self, text: str) -> None:
        self.set_hand_id(text)
        if self.complete:
            self._recompute()

    def preview_parts(self):
        """[(nombre, malla, color)] para `PreviewRenderer.render_parts`: el brazo sin mano en gris y la mano donde estaba, en verde."""
        if self.plan is None:
            return None
        if self._parts is None:               # el mismo objeto en cada dibujo: `PreviewRenderer.render_parts` no resube la malla al girar
            self._parts = [("brazo", self.plan.arm_mesh, ARM_COLOR), ("mano", self.plan.hand_world, HAND_COLOR)]
        return self._parts

    def default_hand_id(self) -> str:
        from ..catalog import Catalog

        return Catalog.default_hand_id(self.meta.id)

    def save(self, new_hand_version: bool = False):
        """Guarda el corte. Devuelve el `WristCut` o None (el motivo queda en `ctrl.message`)."""
        if not self.complete or self.plan is None:
            self.ctrl._changed("Primero elegí los 3 puntos de la muñeca y revisá el corte.", scene=False)
            return None
        return self.ctrl.catalog_cut_wrist(self.meta.ref, self.points, self.invert, self.hand_id or None, new_hand_version)
