"""Gestor del catálogo de piezas: la lógica (lista filtrable, textos, cámara 3/4, borrado). Sin GTK ni OpenGL, así se
prueba sin ventana; la ventana está en `catalog_window.py`.

Sirve para ver qué hay en el catálogo (con una vista previa 3/4 de cada pieza) y eliminar versiones de pieza, por ejemplo
las que se catalogaron por error desde un proyecto viejo."""
from __future__ import annotations

import numpy as np

from ..parts import PieceMeta
from .camera import OrbitCamera

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

    def render(self, mesh, meta: PieceMeta | None = None, yaw_off: float = 0.0, pitch_off: float = 0.0) -> np.ndarray:
        """Imagen RGBA (alto × ancho × 4, uint8) de `mesh` en vista 3/4; `yaw_off` / `pitch_off` giran la vista (°)."""
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
            cam = three_quarter_camera(view_bounds(mesh, meta), off.width / off.height)
            cam.yaw = (PREVIEW_YAW + yaw_off) % 360.0
            cam.pitch = float(np.clip(PREVIEW_PITCH + pitch_off, -89.0, 89.0))
            return off.render(cam)

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
