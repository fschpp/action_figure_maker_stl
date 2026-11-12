"""Gestor del catálogo de piezas: la lógica (lista filtrable, textos, cámara 3/4, borrado). Sin GTK ni OpenGL, así se
prueba sin ventana; la ventana está en `catalog_window.py`.

Sirve para ver qué hay en el catálogo (con una vista previa 3/4 de cada pieza) y eliminar versiones de pieza, por ejemplo
las que se catalogaron por error desde un proyecto viejo."""
from __future__ import annotations

import glob
import os
import threading

import numpy as np

from .. import catalog_ops as OPS
from ..parts import PieceMeta
from .camera import OrbitCamera
from .picking import Picker

PREVIEW_YAW = 55.0          # 3/4: desde el frente (+Y) corrido hacia la derecha de la figura (+X), mirando un poco desde arriba
PREVIEW_PITCH = 22.0
ALL_TYPES = "(todos los tipos)"
ALL_STATES = "(todos los estados)"
ALL_TAGS = "(todas las etiquetas)"


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


def matches(meta: PieceMeta, text: str = "", tipo: str | None = None, estado: str | None = None, etiqueta: str | None = None) -> bool:
    """¿Pasa el filtro? `text` busca (sin mayúsculas) en la referencia, el tipo, el estado, las etiquetas, la figura de
    origen, el proyecto de origen y la fecha: «belgrano» muestra todo lo catalogado desde Belgrano."""
    if tipo and meta.tipo != tipo:
        return False
    if estado and meta.estado != estado:
        return False
    if etiqueta and etiqueta not in meta.etiquetas:
        return False
    words = (text or "").lower().split()
    if not words:
        return True
    hay = " ".join([meta.ref, meta.nombre, meta.tipo, meta.estado, joint_label(meta), " ".join(meta.etiquetas), piece_origin(meta),
                    str(meta.origen.get("proyecto") or ""), str(meta.origen.get("fecha") or ""), meta.descripcion]).lower()
    return all(w in hay for w in words)


JOINT_OF_TYPE = {"brazo": "codo", "pierna": "rodilla"}                     # miembros que pueden traer la articulación propia
SOCKET_OF_TYPE = {"antebrazo": "codo", "pantorrilla": "rodilla", "bota": "rodilla"}   # piezas distales: llevan la toma del otro lado


def joint_label(meta: PieceMeta) -> str:
    """Si la pieza trae pin de articulación, dicho sin ambigüedad: «● con codo» / «○ sin codo» (brazo), «● con rodilla» /
    «○ sin rodilla» (pierna), «toma de codo / rodilla» (antebrazo, pantorrilla, bota) o «—» (el resto de los tipos)."""
    name = JOINT_OF_TYPE.get(meta.tipo)
    if name:
        return f"● con {name}" if (meta.articulaciones or []) else f"○ sin {name}"
    if meta.tipo in SOCKET_OF_TYPE:
        return f"toma de {SOCKET_OF_TYPE[meta.tipo]}"
    return "—"


def has_joint_pin(meta: PieceMeta) -> bool:
    return meta.tipo in JOINT_OF_TYPE and bool(meta.articulaciones or [])


# Columnas de la lista: (clave, título, ancho en caracteres)
COLUMNS = (("nombre", "Nombre", 24), ("tipo", "Tipo", 10), ("version", "Ver.", 4), ("articulacion", "Articulación", 14),
           ("estado", "Estado", 9), ("certificacion", "Certificación", 18), ("origen", "Origen", 18))


def row_cells(meta: PieceMeta, cert: str = "") -> dict[str, str]:
    """Texto de cada columna de la lista para `meta` (`cert`: su certificación, «» si no se certifica o no es la última versión)."""
    return {"nombre": meta.display_name, "tipo": meta.tipo, "version": f"@{meta.version}", "articulacion": joint_label(meta),
            "estado": meta.estado, "certificacion": cert, "origen": piece_origin(meta)}


def sort_pieces(metas, key: str = "tipo", descending: bool = False, cert_of=None) -> list[PieceMeta]:
    """Ordena por la columna `key` (desempata por nombre y versión): ordenar por «tipo» agrupa cabezas, torsos, piernas…"""
    if key not in {k for k, _t, _w in COLUMNS}:
        raise ValueError(f"columna desconocida «{key}»")
    return sorted(metas, key=lambda m: (row_cells(m, cert_of(m) if cert_of else "")[key].lower(), m.display_name.lower(), m.version),
                  reverse=descending)


def next_row(n: int, key: str, current: int | None, selected: list[int]) -> int | None:
    """Fila a la que lleva una tecla de navegación en una lista de `n` filas: `key` = «up», «down», «home», «end», «pgup» o «pgdn».
    Parte de la fila actual (`current`, la de la última selección) o, sin ella, de lo elegido: hacia arriba desde la primera
    elegida, hacia abajo desde la última; sin nada elegido, ↓ va a la primera y ↑ a la última. Se queda en los extremos."""
    if n <= 0:
        return None
    if key == "home":
        return 0
    if key == "end":
        return n - 1
    step = {"up": -1, "down": 1, "pgup": -10, "pgdn": 10}[key]
    if current is None or not 0 <= current < n:
        current = (min(selected) if step < 0 else max(selected)) if selected else (n if step < 0 else -1)
    return max(0, min(n - 1, current + step))


def piece_label(meta: PieceMeta) -> str:
    """Cómo se nombra una pieza en menús y mensajes: «Nombre (id@n)» si tiene nombre, si no «id@n»."""
    return f"{meta.nombre} ({meta.ref})" if meta.nombre else meta.ref


def row_text(meta: PieceMeta, badge: str = "") -> str:
    """Una línea de la lista: «pierna-belgrano-01@2 · pierna · borrador · belgrano_viejo.stl» (+ «· certificada 3/3» si hay)."""
    parts = [piece_label(meta), meta.tipo, meta.estado]
    if badge:
        parts.append(badge)
    if piece_origin(meta):
        parts.append(piece_origin(meta))
    return " · ".join(parts)


def detail_text(meta: PieceMeta, uses: list[str] | None = None, cert: str = "") -> str:
    """Ficha de una pieza para el panel de la derecha."""
    size = np.asarray(meta.bbox[1], float) - np.asarray(meta.bbox[0], float)
    lines = [f"{meta.ref}" + (f" — «{meta.nombre}»" if meta.nombre else ""), f"Tipo: {meta.tipo} · estado: {meta.estado} · etiquetas: {', '.join(meta.etiquetas) or '—'}",
             f"Tamaño: {size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f} mm · volumen {meta.volumen_mm3:.0f} mm³".replace(".", ",")]
    if meta.altura_figura_mm:
        lines.append(f"Altura de la figura de origen: {meta.altura_figura_mm:g} mm".replace(".", ","))
    org = piece_origin(meta)
    proj = meta.origen.get("proyecto")
    lines.append(f"Origen: {org or '—'}" + (f" · proyecto {proj}" if proj else "") + (f" · {meta.origen['fecha']}" if meta.origen.get("fecha") else ""))
    if meta.tipo in JOINT_OF_TYPE or meta.tipo in SOCKET_OF_TYPE:
        lines.append(f"Articulación: {joint_label(meta)}" + (" (el eje se ve en azul en la vista previa)" if has_joint_pin(meta) else ""))
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
    if cert:
        lines.append(f"Certificación: {cert}")
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


PIN_LEGEND = ("Naranja: puertos / pasadores de encastre (cilindro con su dirección) · Azul: eje de la articulación (codo / rodilla), "
              "con su plano celeste")
JOINT_COLOR = (0.20, 0.50, 1.0, 1.0)
JOINT_PLANE_COLOR = (0.30, 0.75, 1.0, 0.28)


def _cyl(center, axis, radius: float, length: float):
    import trimesh

    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    z = np.array([0.0, 0.0, 1.0])
    v, c = np.cross(z, axis), float(z @ axis)
    if np.linalg.norm(v) < 1e-9:
        rot = np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    else:
        k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        rot = np.eye(3) + k + k @ k * (1.0 / (1.0 + c))
    T = np.eye(4)
    T[:3, :3] = rot
    T[:3, 3] = np.asarray(center, float)
    return trimesh.creation.cylinder(radius=radius, height=length, sections=24, transform=T)


def pin_radius(mesh_extent: float) -> float:
    """Radio con que se dibuja un pasador en la vista previa: se ve grueso aunque la pieza sea chica (≥ 0,9 mm)."""
    return max(0.9, float(mesh_extent) * 0.02)


def pin_meshes(meta: PieceMeta, mesh) -> list[tuple[str, object, tuple]]:
    """Pasadores como sólidos de verdad (no líneas de 1 px): [(nombre, malla, color)]. Cada puerto, un cilindro naranja que
    sale de la pieza en la dirección de su eje; cada articulación (codo / rodilla), un cilindro azul que atraviesa la pieza a lo
    largo del eje de la bisagra y sobresale 2 mm de cada lado, más su diámetro real (`HingeSpec.pin_d`) si es mayor."""
    from ..joints import JointSpec
    from ..params import HingeSpec

    ext = float(max(mesh.extents))
    out: list[tuple[str, object, tuple]] = []
    length = ext * 0.25
    r = pin_radius(ext)
    for name, p in meta.puertos.items():
        c, a = np.asarray(p.frame.center, float), np.asarray(p.frame.axis, float)
        out.append((f"pasador:{name}", _cyl(c + a * length / 2, a, r, length), PreviewRenderer.PORT_COLOR))
    if meta.tipo in JOINT_OF_TYPE:
        corners = np.asarray(mesh.bounds, float)
        for jd in meta.articulaciones or []:
            try:
                j = JointSpec.from_dict(jd)
            except Exception:                                        # noqa: BLE001  (ficha rara: se dibuja lo demás)
                continue
            c, a = np.asarray(j.axis_center, float), np.asarray(j.axis_dir, float)
            a = a / np.linalg.norm(a)
            box = np.array([[x, y, z] for x in corners[:, 0] for y in corners[:, 1] for z in corners[:, 2]])
            t = (box - c) @ a
            lo, hi = float(t.min()) - 2.0, float(t.max()) + 2.0
            pin_d = (j.hinge or HingeSpec()).pin_d
            out.append((f"eje:{j.name}", _cyl(c + a * (lo + hi) / 2, a, max(r, pin_d / 2), hi - lo), JOINT_COLOR))
    return out


def joint_planes(meta: PieceMeta):
    """[(nombre, Plane)] de las articulaciones de la pieza (para dibujar el plano translúcido)."""
    from ..joints import JointSpec

    out = []
    if meta.tipo in JOINT_OF_TYPE:
        for jd in meta.articulaciones or []:
            try:
                out.append((f"plano:{jd.get('name', '')}", JointSpec.from_dict(jd).plane))
            except Exception:                                        # noqa: BLE001
                continue
    return out


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
    shown = [piece_label(m) for m in metas[:12]]
    detail = "\n".join(f"• {r}" for r in shown) + (f"\n… y {n - len(shown)} más" if n > len(shown) else "")
    used = {r: u for r, u in uses.items() if u}
    if used:
        detail += "\n\nOjo, se usan en:\n" + "\n".join(f"• {r}: {'; '.join(u)}" for r, u in list(used.items())[:8])
        detail += "\nLas recetas que las nombran van a fallar al armar; en la figura abierta esos lugares vuelven a la pieza del modelo."
    detail += "\n\nSe mueven a la papelera del catálogo (carpeta .papelera): «Recuperar lo eliminado» las devuelve a su lugar."
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
        self._key = None
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
            key = (id(mesh), meta.ref if meta is not None else None, len(meta.articulaciones or []) if meta is not None else 0)
            if self._mesh is not mesh or self._key != key:
                off.scene.clear()
                off.scene.set_mesh("pieza", mesh, self.PIECE_COLOR)
                if meta is not None:
                    for name, pm, color in pin_meshes(meta, mesh):          # pasadores y ejes como sólidos: se ven aunque la pieza sea chica
                        off.scene.set_mesh(name, pm, color)
                    if meta.puertos:
                        pts, _segs = port_markers(meta, float(max(mesh.extents)) * 0.25)
                        off.scene.set_points("puertos", pts, self.PORT_COLOR, size=14.0, on_top=True)
                    for name, plane in joint_planes(meta):
                        off.scene.set_plane(name, plane, float(max(mesh.extents)) * 0.35, color=JOINT_PLANE_COLOR)
                self._mesh = mesh
                self._key = key
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
    """Estado del gestor sobre un `Controller`: lista filtrada, selección, malla de la vista previa y borrado.

    También reparte los comandos de `afm catalogo` que la ventana ofrece (`catalog_ops`): los informes rápidos devuelven
    líneas de texto y las tareas largas (`certify_job`, `compat_job`, `matrix_job`) devuelven el trabajo para un hilo."""

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self._mesh_cache: dict[str, tuple[str, object]] = {}      # ref -> (hash, malla): solo las últimas
        self._badges: tuple[tuple, dict[str, str]] = ((), {})     # (clave de lo que hay en disco, {ref: texto de certificación})
        self.cancel_event = threading.Event()
        self.proposal: OPS.ContractProposal | None = None            # última propuesta de contrato calibrada (aún sin guardar)

    # ---- lista
    def pieces(self, text: str = "", tipo: str | None = None, estado: str | None = None, etiqueta: str | None = None,
               latest_only: bool = False) -> list[PieceMeta]:
        """Las versiones del catálogo abierto que pasan el filtro (`latest_only`: solo la última de cada id)."""
        tipo = None if tipo in (None, "", ALL_TYPES) else tipo
        estado = None if estado in (None, "", ALL_STATES) else estado
        etiqueta = None if etiqueta in (None, "", ALL_TAGS) else etiqueta
        every = self.ctrl.catalog_pieces()
        last = {}
        for m in every:
            last[m.id] = max(last.get(m.id, 0), m.version)
        return [m for m in every if matches(m, text, tipo, estado, etiqueta) and (not latest_only or m.version == last[m.id])]

    def types(self) -> list[str]:
        return sorted({m.tipo for m in self.ctrl.catalog_pieces()})

    def tags(self) -> list[str]:
        return sorted({t for m in self.ctrl.catalog_pieces() for t in m.etiquetas})

    def states(self) -> list[str]:
        return sorted({m.estado for m in self.ctrl.catalog_pieces()})

    def uses(self, ref: str) -> list[str]:
        return self.ctrl.catalog_usages(ref)

    # ---- certificación (semáforo: no arma nada)
    def _disk_key(self) -> tuple:
        root = self.ctrl.session.catalog_root
        if not root:
            return ()
        stamps = []
        for path in [os.path.join(root, "compat.json"), os.path.join(root, "linea.json")] + glob.glob(os.path.join(root, "piezas", "*", "*.pieza.json")):
            try:
                stamps.append((path, os.stat(path).st_mtime_ns))
            except OSError:
                stamps.append((path, None))
        return (root, tuple(sorted(stamps, key=lambda x: x[0])))

    def cert_text(self, ref: str) -> str:
        """«certificada 3/3», «vencida…», «sin certificar» o «» si la pieza no se certifica. Se recuerda hasta que cambia el disco."""
        key = self._disk_key()
        if self._badges[0] != key:
            self._badges = (key, {})
        cache = self._badges[1]
        if ref not in cache:
            from .. import catalog_ops as OPS

            try:
                meta = next(m for m in self.ctrl.catalog_pieces() if m.ref == ref)
                cat = self.ctrl.catalog_handle()
                cache[ref] = OPS.cert_badge(cat, meta) if cat is not None else ""
            except (StopIteration, ValueError):
                cache[ref] = ""
        return cache[ref]

    def cert_summary(self) -> str:
        """Resumen del catálogo: «certificadas 3 · parciales 1 · vencidas 1 · sin certificar 2» (la última versión de cada pieza certificable)."""
        from collections import Counter

        from ..certify import PLACE_OF_TIPO

        c: Counter = Counter()
        for m in self.pieces(latest_only=True):
            if m.tipo in PLACE_OF_TIPO:
                txt = self.cert_text(m.ref)
                c["certificadas" if txt.startswith("certificada") and "falla" not in txt else
                  "parciales" if txt.startswith("certificada") else "vencidas" if txt.startswith("certificación vencida") else
                  "sin certificar"] += 1
        return " · ".join(f"{k} {v}" for k, v in c.items())

    # ---- informes rápidos (líneas de texto; no escriben nada)
    def report_verify(self) -> tuple[list[str], bool]:
        from .. import catalog_ops as OPS

        res = OPS.verify(self.ctrl.catalog_handle(required=True))
        return res.lines, res.ok

    def report_contract(self) -> list[str]:
        from .. import catalog_ops as OPS

        lines, _probs = OPS.contract_view(self.ctrl.catalog_handle(required=True))
        return lines

    def report_coverage(self, ref: str, all_fits: bool = False) -> list[str]:
        from .. import catalog_ops as OPS

        return OPS.coverage_lines(self.ctrl.catalog_handle(required=True), ref, all_fits)

    def report_explain(self, ref: str, tipo: str | None = None) -> list[str]:
        from .. import catalog_ops as OPS

        return OPS.explain_lines(self.ctrl.catalog_handle(required=True), ref, tipo)

    # ---- tareas largas (para un hilo): devuelven `job(progress) -> resultado` o None si no se pudo empezar (motivo en `ctrl.message`)
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def certify_refs(self, refs: list[str] | None = None) -> list[str]:
        """`refs` pedidas que se certifican (brazo, pierna, cabeza, pelvis, mano); sin `refs`, todas las certificables (últimas versiones)."""
        from ..certify import PLACE_OF_TIPO

        if refs is None:
            return [m.ref for m in self.pieces(latest_only=True) if m.tipo in PLACE_OF_TIPO]
        by = {m.ref: m for m in self.ctrl.catalog_pieces()}
        return [r for r in refs if r in by and by[r].tipo in PLACE_OF_TIPO]

    def certify_job(self, refs: list[str] | None, force: bool = False, status_only: bool = False, emit=None):
        from .. import catalog_ops as OPS

        todo = self.certify_refs(refs)
        if not todo:
            self.ctrl._changed("No hay piezas para certificar (se certifican brazo, pierna, cabeza, pelvis y mano).", scene=False)
            return None
        self.cancel_event.clear()
        label = "Revisando la certificación" if status_only else "Certificando"
        return self.ctrl.begin_catalog_task(
            label, lambda root, progress: OPS.certify_pieces(root, todo, force, status_only, progress, self.cancelled, emit))

    def compat_job(self, force: bool = False):
        from .. import catalog_ops as OPS

        self.cancel_event.clear()
        return self.ctrl.begin_catalog_task("Compatibilidad de las recetas", lambda root, progress: OPS.compat_all(root, force, progress, self.cancelled))

    def matrix_job(self, complete: bool = False):
        from .. import catalog_ops as OPS

        self.cancel_event.clear()
        return self.ctrl.begin_catalog_task("Informe de compatibilidad", lambda root, progress: OPS.write_matrix(root, None, complete, progress, self.cancelled))

    def capacity_refs(self, refs: list[str]) -> list[str]:
        """De `refs`, las piezas con alguna toma de pasador (las que `capacidad` mide)."""
        cat = self.ctrl.catalog_handle()
        if cat is None:
            return []
        by = {m.ref: m for m in self.ctrl.catalog_pieces()}
        return [r for r in refs if r in by and OPS.measurable(cat, by[r])]

    def capacity_job(self, refs: list[str] | None, write: bool = False, emit=None):
        """Mide la capacidad de las tomas de `refs` (sin `refs`, de todo el catálogo); con `write` la guarda en los .pieza.json."""
        todo = self.capacity_refs([m.ref for m in self.pieces(latest_only=True)] if refs is None else refs)
        if not todo:
            self.ctrl._changed("Ninguna de las piezas tiene tomas de pasador para medir.", scene=False)
            return None
        self.cancel_event.clear()
        label = "Midiendo y guardando la capacidad" if write else "Midiendo la capacidad"
        return self.ctrl.begin_catalog_task(label, lambda root, progress: OPS.measure_capacity(root, todo, write, self.cancelled, emit))

    # ---- contrato de la línea (escribe linea.json: la ventana pide confirmación antes de guardar o borrar)
    def propose_contract(self, tolerance: float = 0.4, metric: str = "equivalente") -> OPS.ContractProposal:
        """Calibra una propuesta y la recuerda en `proposal` para guardarla después; no escribe nada."""
        self.proposal = OPS.propose_contract(self.ctrl.catalog_handle(required=True), tolerance, metric)
        return self.proposal

    def can_save_proposal(self) -> bool:
        return self.proposal is not None and self.proposal.contract is not None and not self.proposal.problems

    def save_proposal(self) -> str:
        """Guarda la propuesta vigente en linea.json y devuelve su huella (`ValueError` si no hay una sin problemas)."""
        if not self.can_save_proposal():
            raise ValueError("No hay una propuesta de contrato sin problemas para guardar (calibrarla primero).")
        digest = OPS.save_contract(self.ctrl.catalog_handle(required=True), self.proposal.contract)
        self.proposal = None
        self._badges = ((), {})                              # el contrato cambia los talles: la certificación puede quedar vencida
        return digest

    def clear_contract(self) -> bool:
        ok = OPS.clear_contract(self.ctrl.catalog_handle(required=True))
        self.proposal = None
        self._badges = ((), {})
        return ok

    def finish_task(self, label: str, error) -> None:
        """Cierra una tarea larga: suelta `busy` y deja el mensaje (cancelada / falló / terminó). Los cachés de la insignia se invalidan solos."""
        from .. import catalog_ops as OPS

        self.ctrl.finish_catalog_task(label, error, cancelled=isinstance(error, OPS.Cancelled))

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

    def undo_delete(self) -> bool:
        ok = self.ctrl.catalog_undo_delete()
        self._mesh_cache.clear()
        return ok

    def set_state(self, refs: list[str], state: str) -> bool:
        return self.ctrl.catalog_set_state(refs, state)

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


# ------------------------------------------------------------------ reajustar la articulación de una pieza del catálogo
PROX_COLOR = (0.85, 0.80, 0.68, 1.0)          # como en la vista previa de la ventana principal
DIST_COLOR = (0.55, 0.75, 0.95, 1.0)


def find_piece(metas, ref: str | None) -> PieceMeta | None:
    """La pieza de `metas` que nombra `ref` (`id@n` = esa versión; `id` = la última), o None. Es como la ventana principal
    resuelve la pieza de un lugar de la figura, que puede estar guardada sin versión fija."""
    if not ref:
        return None
    pid, _, ver = ref.partition("@")
    found = [m for m in metas if m.id == pid and (not ver or m.version == int(ver))]
    return max(found, key=lambda m: m.version) if found else None


def can_retune_joint(meta: PieceMeta | None) -> tuple[bool, str]:
    """¿Se le puede reajustar la articulación a esta pieza? (sí/no, motivo cuando no)."""
    if meta is None:
        return False, "Elegí un brazo o una pierna de la lista."
    if meta.tipo not in ("brazo", "pierna"):
        return False, f"«{meta.ref}» es una pieza «{meta.tipo}»: solo un brazo o una pierna lleva rodilla / codo."
    if not (meta.articulaciones or []):
        return False, (f"«{meta.ref}» no trae articulación (se catalogó sin rodilla / codo): la articulación se define en la figura "
                       "y se catalogan juntas.")
    return True, ""


class JointEditor:
    """Modo «Reajustar articulación…» del gestor: la pieza sola (en su marco), con la articulación que trae, para mover el eje,
    inclinar el plano o cambiar el rango viendo cuánto material se lleva el hueco → guardar como versión nueva.

    Sin GTK: la ventana solo reenvía valores / clics y dibuja lo que pide `view()`. El cálculo (`job` / `done`) se puede correr
    en un hilo: `job()` no toca el estado y `done()` descarta resultados de una articulación que ya cambió."""

    def __init__(self, ctrl, meta: PieceMeta, mesh):
        from ..joints import JointSpec

        self.ctrl, self.meta, self.mesh = ctrl, meta, mesh
        self.joints = [JointSpec.from_dict(d) for d in meta.articulaciones]
        self._original = {j.name: j for j in self.joints}
        self.joint = self.joints[0]
        self.flex = 0.0
        self.pv = None                          # JointPreview vigente (de `self._pv_key`)
        self._pv_key = None
        self.error = ""
        self.busy = False
        self.pick_axis = False                  # el próximo clic sobre la pieza mueve el eje
        self._prep = None                       # (Manifold, ContactFace) de la pieza: se arma una vez
        self._picker: Picker | None = None
        self._drawn = None                      # (sig, parts) para no resubir la malla a la GPU en cada dibujo

    # ---- estado
    @property
    def names(self) -> list[str]:
        return [j.name for j in self.joints]

    def select(self, name: str) -> None:
        self.joint = next(j for j in self.joints if j.name == name)
        self.pv, self._pv_key, self.error, self._drawn = None, None, "", None

    @property
    def original(self):
        return self._original[self.joint.name]

    @property
    def changed(self) -> bool:
        from ..joint_edit import differs

        return differs(self.original, self.joint)

    def height(self) -> float:
        from ..joint_edit import height

        return height(self.joint)

    def front_tilt(self) -> float:
        from ..joint_edit import front_tilt

        return front_tilt(self.joint)

    def side_tilt(self) -> float:
        from ..joint_edit import side_tilt

        return side_tilt(self.joint)

    def limits(self) -> tuple[float, float]:
        """Rango de flexión que se usa (el de la articulación o, si no lleva, el de los parámetros de la línea)."""
        from ..joints import flex_range

        return flex_range(self.joint, self._params())

    def _params(self):
        try:
            return self.ctrl.session.catalog_joint_params()
        except (ValueError, KeyError):
            from ..params import Params
            return Params()

    # ---- cambios (cada uno deja el valor anterior si no sirve y explica por qué en `error`)
    def _apply(self, fn, *args) -> str:
        try:
            new = fn(self.joint, *args)
        except ValueError as exc:
            self.error = f"No sirve: {exc}."
            return self.error
        i = self.joints.index(self.joint)
        self.joints[i] = self.joint = new
        self.error, self._drawn = "", None
        return self.message()

    def set_height(self, h: float) -> str:
        from ..joint_edit import with_height

        return self._apply(with_height, h)

    def set_front_tilt(self, t: float) -> str:
        from ..joint_edit import with_front_tilt

        return self._apply(with_front_tilt, t)

    def set_side_tilt(self, t: float) -> str:
        from ..joint_edit import with_side_tilt

        return self._apply(with_side_tilt, t)

    def set_range(self, lo: float | None, hi: float | None) -> str:
        from ..joint_edit import with_range

        return self._apply(with_range, lo, hi)

    def set_axis_point(self, point) -> str:
        from ..joint_edit import with_axis_point

        return self._apply(with_axis_point, point)

    def reset(self) -> str:
        i = self.joints.index(self.joint)
        self.joints[i] = self.joint = self._original[self.joint.name]
        self.error, self._drawn = "", None
        return self.message()

    def set_flex(self, flex: float) -> None:
        self.flex = float(flex)
        self._drawn = None

    # ---- clic para mover el eje
    def start_pick(self) -> str:
        self.pick_axis, self._drawn = True, None
        return "Hacé clic sobre el costado de la pieza, en el centro de la bisagra (se toman la altura y la profundidad del clic)."

    def cancel_pick(self) -> None:
        self.pick_axis, self._drawn = False, None

    def pick_at(self, x: float, y: float, widget_size, image_size, yaw_off: float = 0.0, pitch_off: float = 0.0,
                zoom: float = 1.0, pan=(0.0, 0.0)) -> str:
        """Clic en (x, y) del widget sobre la pieza entera (el mismo encuadre que `view()` cuando `pick_axis`)."""
        if not self.pick_axis:
            return self.message()
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
            return "Ahí no hay pieza: hacé clic sobre el miembro. " + self.message()
        msg = self.set_axis_point(res.point)
        if not self.error:
            self.pick_axis = False
            msg = self.message()
        return msg

    # ---- cálculo de la vista previa
    def key(self) -> str:
        import json

        return json.dumps(self.joint.to_dict(), sort_keys=True, default=float)

    def needs_compute(self) -> bool:
        return not self.busy and self._pv_key != self.key()

    def job(self):
        """(clave, función) del cálculo de la vista previa de la articulación vigente. La función no toca el estado: se puede
        correr en un hilo y entregar el resultado a `done`."""
        import copy

        from ..joint_preview import prepare_piece, preview_joint

        if self._prep is None:
            self._prep = prepare_piece(self.meta, self.mesh)
        (limb, face), joint, p = self._prep, copy.deepcopy(self.joint), copy.deepcopy(self._params())
        self.busy = True
        return self.key(), (lambda: preview_joint(limb, face, joint, p))

    def done(self, key: str, pv, err) -> bool:
        """Entrega el resultado de `job`. False si ya no corresponde (la articulación cambió mientras se calculaba)."""
        self.busy = False
        if key != self.key():
            return False
        self._pv_key, self._drawn = key, None
        self.pv = None if err is not None else pv
        self.error = "" if err is None else (str(err) if isinstance(err, ValueError) else f"{type(err).__name__}: {err}")
        return True

    def compute(self) -> None:
        """Calcula la vista previa en el acto (sin hilo)."""
        try:
            key, run = self.job()
        except ValueError as exc:
            self.busy = False
            self._pv_key, self.pv, self.error = self.key(), None, str(exc)
            return
        try:
            res, err = run(), None
        except Exception as exc:               # cualquier falla del motor se muestra en el estado
            res, err = None, exc
        self.done(key, res, err)

    # ---- qué dibujar
    def view(self):
        """(partes, encuadre, marcas) para `PreviewRenderer.render_parts`. Con `pick_axis`: la pieza entera (para tocar el eje);
        si no, la articulación (proximal tallado + distal girado a la flexión mostrada); sin cálculo todavía, la pieza entera."""
        c = np.asarray(self.joint.axis_center, float)
        d = np.asarray(self.joint.axis_dir, float)
        marks = np.array([c - d * 5.0, c + d * 5.0])
        if self.pick_axis or self.pv is None:
            sig = ("pieza",)
            if self._drawn is None or self._drawn[0] != sig:
                self._drawn = (sig, [("pieza", self.mesh, ARM_COLOR)])
            return self._drawn[1], np.asarray(view_bounds(self.mesh, self.meta), float), marks
        sig = (id(self.pv), round(self.flex, 6))
        if self._drawn is None or self._drawn[0] != sig:
            parts = [("proximal", self.pv.proximal_mesh(), PROX_COLOR), ("distal", self.pv.distal_mesh(self.flex), DIST_COLOR)]
            self._drawn = (sig, parts)
        b = self.pv.build
        marks = np.array([b.center - b.axis * b.tips[0], b.center + b.axis * b.tips[1]])
        return self._drawn[1], None, marks

    def message(self) -> str:
        if self.error:
            return self.error
        if self.pick_axis:
            return "Hacé clic sobre el costado de la pieza, en el centro de la bisagra."
        if self.pv is None:
            return "Calculando la articulación…" if self.busy else "Movés el eje, el plano o el rango y la vista previa se recalcula."
        txt = self.pv.summary()
        return txt + ("\n  (actualizando…)" if self.busy else "") + ("" if self.changed else "\n  Sin cambios respecto de la versión guardada.")

    # ---- guardar
    def save(self):
        """Guarda la articulación como versión nueva de la pieza. Devuelve la meta nueva o None (el motivo queda en `ctrl.message`)."""
        if not self.changed:
            self.ctrl._changed("No cambiaste nada: no hay nada que guardar.", scene=False)
            return None
        return self.ctrl.catalog_save_joint(self.meta.ref, self.joint)
