"""Renderer del visor 3D con moderngl: mallas sombreadas, líneas, puntos y planos translúcidos.

No depende de GTK: se usa dentro de un ``Gtk.GLArea`` (contexto adjunto) o fuera de pantalla
(``OffscreenRenderer``, útil para pruebas y capturas).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh

from ..planes import Plane
from .camera import OrbitCamera

_MESH_VS = """
#version 330
uniform mat4 u_mvp;
uniform mat3 u_view_rot;
in vec3 in_pos;
in vec3 in_nrm;
out vec3 v_nrm;
out vec3 v_pos;
#ifdef TEXTURED
in vec2 in_uv;
out vec2 v_uv;
#endif
void main() {
    v_pos = in_pos;
    v_nrm = u_view_rot * in_nrm;
#ifdef TEXTURED
    v_uv = in_uv;
#endif
    gl_Position = u_mvp * vec4(in_pos, 1.0);
}
"""
_MESH_FS = """
#version 330
uniform vec4 u_color;
uniform float u_clip_x;
uniform vec3 u_clip_n;      // plano de recorte genérico: se descarta lo que cumple dot(n, p) > d
uniform float u_clip_d;
in vec3 v_nrm;
in vec3 v_pos;
#ifdef TEXTURED
uniform sampler2D u_tex;
uniform int u_use_tex;      // 1: el color sale de la textura; 0: color plano `u_color` (el botón «Textura» apagado)
in vec2 v_uv;
#endif
out vec4 f_color;
void main() {
    if (v_pos.x < u_clip_x) discard;
    if (dot(u_clip_n, v_pos) > u_clip_d) discard;
    vec3 n = normalize(v_nrm);
    if (!gl_FrontFacing) n = -n;
    float key = max(dot(n, normalize(vec3(0.35, 0.45, 0.85))), 0.0);
    float fill = max(dot(n, normalize(vec3(-0.6, -0.2, 0.5))), 0.0);
    float l = 0.30 + 0.62 * key + 0.18 * fill;
    vec3 base = u_color.rgb;
#ifdef TEXTURED
    if (u_use_tex == 1) base = texture(u_tex, v_uv).rgb;
#endif
    f_color = vec4(min(base * l, vec3(1.0)), u_color.a);
}
"""
_FLAT_VS = """
#version 330
uniform mat4 u_mvp;
uniform float u_point_size;
uniform float u_bias;
in vec3 in_pos;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    gl_Position.z -= u_bias * gl_Position.w;
    gl_PointSize = u_point_size;
}
"""
_FLAT_FS = """
#version 330
uniform vec4 u_color;
uniform int u_round;
out vec4 f_color;
void main() {
    if (u_round == 1) {
        vec2 c = gl_PointCoord - vec2(0.5);
        if (dot(c, c) > 0.25) discard;
    }
    f_color = u_color;
}
"""
_THICK_VS = """
#version 330
uniform mat4 u_mvp;
uniform float u_bias;
uniform float u_width;      // grosor del trazo en píxeles
uniform vec2 u_viewport;    // tamaño del visor en píxeles
in vec3 in_pos;             // extremo propio del vértice
in vec3 in_other;           // el otro extremo del segmento
in float in_side;           // -1 / +1: de qué lado del trazo está el vértice
in float in_end;            // 0 en el primer extremo, 1 en el segundo
void main() {
    vec4 cs = u_mvp * vec4(in_pos, 1.0);
    vec4 co = u_mvp * vec4(in_other, 1.0);
    vec2 ps = (cs.xy / cs.w) * 0.5 * u_viewport;
    vec2 po = (co.xy / co.w) * 0.5 * u_viewport;
    vec2 d = (in_end < 0.5) ? (po - ps) : (ps - po);       // siempre del primer extremo al segundo
    float len = length(d);
    d = (len > 1e-6) ? d / len : vec2(1.0, 0.0);
    vec2 n = vec2(-d.y, d.x);
    float along = (in_end < 0.5) ? -1.0 : 1.0;              // prolonga medio grosor: los vértices de una polilínea se empalman sin muescas
    vec2 off_px = (n * in_side + d * along) * (0.5 * u_width);
    gl_Position = cs;
    gl_Position.xy += off_px * 2.0 / u_viewport * cs.w;
    gl_Position.z -= u_bias * gl_Position.w;
}
"""


def _with_define(src: str, name: str) -> str:
    """Fuente GLSL con `#define name` justo después de `#version` (las variantes de un mismo shader)."""
    return src.replace("#version 330", f"#version 330\n#define {name} 1", 1)


@dataclass
class Item:
    kind: str                       # "mesh" | "lines" | "points" | "quad"
    vao: object = None
    vbo: object = None
    count: int = 0
    color: tuple = (0.8, 0.8, 0.8, 1.0)
    size: float = 8.0
    visible: bool = True
    clip_x: float = -1e9
    clip_plane: tuple | None = None   # (nx, ny, nz, d): se descarta lo que cumple dot(n, p) > d
    bias: float = 0.0
    on_top: bool = False              # se dibuja sin prueba de profundidad (marcadores que no deben quedar tapados)
    ghost: bool = False               # malla translúcida (usa el alfa del color): se pinta después de las opacas, sin escribir profundidad
    cull: bool = False                # descarta las caras de atrás (una malla translúcida se ve limpia: solo su frente)
    restore: tuple | None = None      # (color, ghost, cull) de antes de `set_translucent`, para volver a pintarla opaca
    width: float = 1.0                # grosor en píxeles de un trazo («lines»); > 1 se dibuja como cinta (GL_LINES solo hace 1 px)
    tex: object = None                # textura de color (moderngl.Texture) de una malla con UV; None = malla de color plano
    tex_key: object = None            # identidad de la malla+textura subidas: con la misma clave no se vuelve a subir (atlas de 4096²)


def _flat_vertices(mesh: trimesh.Trimesh) -> np.ndarray:
    tris = np.asarray(mesh.triangles, np.float32)
    nrm = np.repeat(np.asarray(mesh.face_normals, np.float32)[:, None, :], 3, axis=1)
    return np.concatenate([tris, nrm], axis=2).reshape(-1, 6).astype("f4")


def _flat_vertices_uv(mesh: trimesh.Trimesh, corner_uv) -> np.ndarray:
    """Como `_flat_vertices` pero con el UV de cada esquina: 8 floats por vértice (xyz, normal, uv), 3 vértices por triángulo sin
    indexar (así cada esquina lleva su propio UV, también en las costuras). `corner_uv`: (F, 3, 2) con v hacia arriba."""
    uv = np.asarray(corner_uv, np.float32)
    if uv.shape != (len(mesh.faces), 3, 2):
        raise ValueError(f"corner_uv debe ser (F,3,2) = {(len(mesh.faces), 3, 2)}, no {uv.shape}")
    base = _flat_vertices(mesh).reshape(-1, 3, 6)
    return np.concatenate([base, uv], axis=2).reshape(-1, 8).astype("f4")


@dataclass
class TexturedPart:
    """Una malla con color por textura lista para el visor: `corner_uv` (F,3,2) con v hacia arriba (convención FBX / trimesh) e
    `image` uint8 (H, W, 3) con la fila 0 ARRIBA (como un PNG). Sin imagen, se dibuja de color plano."""
    name: str
    mesh: trimesh.Trimesh
    corner_uv: np.ndarray | None = None
    image: np.ndarray | None = None


def _rgb_array(img) -> np.ndarray:
    """PIL.Image / arreglo → uint8 (H, W, 3)."""
    from PIL import Image

    if not isinstance(img, Image.Image):
        img = Image.fromarray(np.asarray(img))
    return np.ascontiguousarray(np.asarray(img.convert("RGB"), np.uint8))


def load_glb_parts(path: str) -> list[TexturedPart]:
    """Lee un GLB (p. ej. `<fig>_ensamblada.glb` de `Colorizer.write`): una `TexturedPart` por malla, con las transformaciones de
    nodo aplicadas. Las mallas sin textura de color base salen con `image=None`. Sin GL: se prueba sin contexto."""
    scene = trimesh.load(path, process=False)
    if isinstance(scene, trimesh.Trimesh):
        scene = trimesh.Scene(scene)
    parts: list[TexturedPart] = []
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph[node]
        geom = scene.geometry[gname].copy()
        geom.apply_transform(T)
        vis = geom.visual
        uv = getattr(vis, "uv", None)
        tex = getattr(getattr(vis, "material", None), "baseColorTexture", None)
        if uv is None or tex is None or len(uv) != len(geom.vertices):
            parts.append(TexturedPart(str(node), geom))
            continue
        parts.append(TexturedPart(str(node), geom, np.asarray(uv, np.float32)[np.asarray(geom.faces)], _rgb_array(tex)))
    return parts


def part_from_textured_mesh(tm, frame=None, name: str = "fuente") -> TexturedPart:
    """La fuente de color (`texmesh.TexturedMesh`, p. ej. `Session.color_source`) como `TexturedPart` con su textura base. `frame`
    (un `FigureFrame`) la lleva al marco de la figura; sin él queda en las unidades y ejes del archivo."""
    mesh = tm.to_trimesh()
    uv = np.asarray(tm.corner_uv, np.float32)
    if frame is not None:
        mesh.vertices = frame.apply(np.asarray(mesh.vertices, float))
        if frame.det() < 0:                                        # un espejo invierte las caras: se invierte también el orden de las esquinas del UV
            mesh.invert()
            uv = np.ascontiguousarray(uv[:, ::-1])
    img = tm.image("base_color") if "base_color" in tm.images else None
    return TexturedPart(name, mesh, uv if img is not None else None, img)


def plane_quad(plane: Plane, half_size: float, center=None) -> np.ndarray:
    c = plane.origin if center is None else np.asarray(center, float)
    c = c - ((c - plane.origin) @ plane.normal) * plane.normal          # proyectado sobre el plano
    u, v = plane.u * half_size, plane.v * half_size
    p = np.array([c - u - v, c + u - v, c + u + v, c - u + v])
    return p


class SceneRenderer:
    """Mantiene los objetos de la escena por nombre y los dibuja con una `OrbitCamera`."""

    def __init__(self, ctx):
        import moderngl

        self.mgl = moderngl
        self.ctx = ctx
        self.mesh_prog = ctx.program(vertex_shader=_MESH_VS, fragment_shader=_MESH_FS)
        # la misma fuente con `TEXTURED`: las mallas con UV usan este programa; las de color plano siguen en `mesh_prog`, sin cambios
        self.tex_prog = ctx.program(vertex_shader=_with_define(_MESH_VS, "TEXTURED"), fragment_shader=_with_define(_MESH_FS, "TEXTURED"))
        self.flat_prog = ctx.program(vertex_shader=_FLAT_VS, fragment_shader=_FLAT_FS)
        self.thick_prog = ctx.program(vertex_shader=_THICK_VS, fragment_shader=_FLAT_FS)
        self.items: dict[str, Item] = {}
        self.background = (0.13, 0.14, 0.16, 1.0)
        self.textures_on = True            # el botón «Textura»: apagado, las mallas con UV se dibujan de su color plano

    # ------------------------------------------------------------- objetos
    def _put(self, name: str, item: Item) -> Item:
        old = self.items.pop(name, None)
        if old is not None:
            self._free(old)
        self.items[name] = item
        return item

    @staticmethod
    def _free(item: Item) -> None:
        for o in (item.vao, item.vbo, item.tex):
            if o is not None:
                o.release()

    def set_mesh(self, name: str, mesh: trimesh.Trimesh | None, color=(0.78, 0.80, 0.84, 1.0), clip_x: float = -1e9,
                 clip_plane=None, ghost: bool = False):
        if mesh is None or len(mesh.faces) == 0:
            return self.remove(name)
        data = _flat_vertices(mesh)
        vbo = self.ctx.buffer(data.tobytes())
        vao = self.ctx.vertex_array(self.mesh_prog, [(vbo, "3f 3f", "in_pos", "in_nrm")])
        return self._put(name, Item("mesh", vao, vbo, len(data), color, clip_x=clip_x, clip_plane=clip_plane, ghost=ghost))

    def set_textured_mesh(self, name: str, mesh: trimesh.Trimesh | None, corner_uv, image, color=(0.78, 0.80, 0.84, 1.0),
                          clip_x: float = -1e9, clip_plane=None, ghost: bool = False, key=None):
        """Malla con color por textura. `corner_uv` (F,3,2) con v hacia arriba (convención FBX / trimesh) e `image` uint8 (H, W, 3)
        con la fila 0 ARRIBA (como un PNG): se sube invertida para que `v` hacia arriba muestree bien. `color` es el plano que se ve
        con las texturas apagadas (`set_textures(False)`)."""
        if mesh is None or len(mesh.faces) == 0:
            return self.remove(name)
        old = self.items.get(name)
        if key is not None and old is not None and old.tex is not None and old.tex_key == key:
            old.color, old.clip_x, old.clip_plane, old.ghost = color, clip_x, clip_plane, ghost     # misma malla y textura: solo cambia el recorte
            return old
        data = _flat_vertices_uv(mesh, corner_uv)
        img = _rgb_array(image)
        h, w = img.shape[:2]
        tex = self.ctx.texture((w, h), 3, np.ascontiguousarray(img[::-1]).tobytes(), alignment=1)
        tex.repeat_x = tex.repeat_y = False                  # los bordes de isla llevan relleno: no se repite el atlas
        tex.filter = (self.mgl.LINEAR_MIPMAP_LINEAR, self.mgl.LINEAR)
        tex.build_mipmaps()
        try:
            tex.anisotropy = 4.0
        except Exception:                                    # noqa: BLE001  (sin anisotropía en este GL: queda trilineal)
            pass
        vbo = self.ctx.buffer(data.tobytes())
        vao = self.ctx.vertex_array(self.tex_prog, [(vbo, "3f 3f 2f", "in_pos", "in_nrm", "in_uv")])
        return self._put(name, Item("mesh", vao, vbo, len(data), color, clip_x=clip_x, clip_plane=clip_plane, ghost=ghost, tex=tex, tex_key=key))

    def set_textures(self, on: bool) -> None:
        """Enciende o apaga el color por textura de todas las mallas que lo tienen (las demás no cambian)."""
        self.textures_on = bool(on)

    def has_textures(self) -> bool:
        return any(it.tex is not None for it in self.items.values())

    def set_translucent(self, name: str, alpha: float | None, cull: bool = True) -> None:
        """Pinta la malla `name` translúcida con ese alfa (o la vuelve a opaca con `alpha=None`), sin volver a subirla. Translúcida no escribe
        profundidad, así que lo que queda detrás o adentro (un fantasma) se ve; con `cull` solo dibuja su frente, sin las caras de atrás."""
        it = self.items.get(name)
        if it is None or it.kind != "mesh":
            return
        if alpha is None:
            if it.restore is not None:
                it.color, it.ghost, it.cull = it.restore
                it.restore = None
            return
        if it.restore is None:
            it.restore = (it.color, it.ghost, it.cull)
        it.color = (*it.color[:3], float(alpha))
        it.ghost, it.cull = True, cull

    def set_lines(self, name: str, polylines, color=(1.0, 0.8, 0.2, 1.0), closed: bool = False,
                  on_top: bool = False, width: float = 1.0):
        """`polylines`: lista de arreglos Nx3 (o un único Nx3). `width`: grosor en píxeles (1 = línea fina de GL)."""
        if isinstance(polylines, np.ndarray) and polylines.ndim == 2:
            polylines = [polylines]
        segs = []
        for pl in polylines:
            pl = np.asarray(pl, np.float32)
            if len(pl) < 2:
                continue
            a, b = pl[:-1], pl[1:]
            if closed and len(pl) > 2:
                a, b = np.vstack([a, pl[-1:]]), np.vstack([b, pl[:1]])
            segs.append(np.stack([a, b], axis=1).reshape(-1, 3))
        if not segs:
            return self.remove(name)
        data = np.concatenate(segs).astype("f4")
        if width > 1.0:
            return self._put(name, self._thick_item(data, color, on_top, float(width)))
        vbo = self.ctx.buffer(data.tobytes())
        vao = self.ctx.vertex_array(self.flat_prog, [(vbo, "3f", "in_pos")])
        return self._put(name, Item("lines", vao, vbo, len(data), color, bias=2e-4, on_top=on_top))

    def _thick_item(self, seg: np.ndarray, color, on_top: bool, width: float) -> Item:
        """Cada segmento (a, b) se vuelve un rectángulo de 2 triángulos que el shader ensancha en pantalla."""
        a, b = seg[0::2], seg[1::2]
        n = len(a)
        rows = np.zeros((n, 6, 8), "f4")
        for k, (own, other, side, end) in enumerate(((a, b, 1, 0), (a, b, -1, 0), (b, a, 1, 1),
                                                     (b, a, 1, 1), (a, b, -1, 0), (b, a, -1, 1))):
            rows[:, k, 0:3], rows[:, k, 3:6], rows[:, k, 6], rows[:, k, 7] = own, other, side, end
        data = rows.reshape(-1, 8)
        vbo = self.ctx.buffer(data.tobytes())
        vao = self.ctx.vertex_array(self.thick_prog, [(vbo, "3f 3f 1f 1f", "in_pos", "in_other", "in_side", "in_end")])
        return Item("lines", vao, vbo, len(data), color, bias=2e-4, on_top=on_top, width=width)

    def set_points(self, name: str, pts, color=(1.0, 0.3, 0.3, 1.0), size: float = 10.0, on_top: bool = False):
        pts = np.asarray(pts, np.float32).reshape(-1, 3)
        if len(pts) == 0:
            return self.remove(name)
        vbo = self.ctx.buffer(pts.astype("f4").tobytes())
        vao = self.ctx.vertex_array(self.flat_prog, [(vbo, "3f", "in_pos")])
        return self._put(name, Item("points", vao, vbo, len(pts), color, size=size, bias=1.5e-3, on_top=on_top))

    def set_plane(self, name: str, plane: Plane | None, half_size: float, center=None, color=(0.3, 0.6, 1.0, 0.22)):
        if plane is None:
            return self.remove(name)
        q = plane_quad(plane, half_size, center).astype("f4")
        tri = q[[0, 1, 2, 0, 2, 3]]
        vbo = self.ctx.buffer(tri.tobytes())
        vao = self.ctx.vertex_array(self.flat_prog, [(vbo, "3f", "in_pos")])
        self._put(name, Item("quad", vao, vbo, 6, color))
        # contorno del plano
        outline = np.vstack([q, q[:1]])
        self.set_lines(name + ":borde", outline, (color[0], color[1], color[2], 1.0))

    def remove(self, name: str) -> None:
        it = self.items.pop(name, None)
        if it is not None:
            self._free(it)
        edge = self.items.pop(name + ":borde", None)
        if edge is not None:
            self._free(edge)

    def show(self, name: str, visible: bool) -> None:
        if name in self.items:
            self.items[name].visible = visible
        if name + ":borde" in self.items:
            self.items[name + ":borde"].visible = visible

    def clear(self) -> None:
        for name in list(self.items):
            self.remove(name)

    # --------------------------------------------------------------- dibujo
    def draw(self, camera: OrbitCamera, width: int, height: int, fbo=None) -> None:
        ctx, mgl = self.ctx, self.mgl
        if fbo is not None:
            fbo.use()
        ctx.viewport = (0, 0, width, height)
        ctx.clear(*self.background)
        mvp = camera.mvp(width / max(height, 1)).astype("f4")
        view_rot = camera.view()[:3, :3].astype("f4")
        ctx.enable(mgl.DEPTH_TEST | mgl.PROGRAM_POINT_SIZE)
        ctx.disable(mgl.BLEND)
        def draw_mesh(it: Item) -> None:
            prog = self.tex_prog if it.tex is not None else self.mesh_prog
            prog["u_mvp"].write(mvp.T.tobytes())
            prog["u_view_rot"].write(view_rot.T.tobytes())
            prog["u_color"].value = it.color
            prog["u_clip_x"].value = it.clip_x
            cp = it.clip_plane if it.clip_plane is not None else (0.0, 0.0, 0.0, 1e9)
            prog["u_clip_n"].value = tuple(float(x) for x in cp[:3])
            prog["u_clip_d"].value = float(cp[3])
            if it.tex is not None:
                it.tex.use(location=0)
                prog["u_tex"].value = 0
                prog["u_use_tex"].value = 1 if self.textures_on else 0
            it.vao.render(mgl.TRIANGLES, vertices=it.count)

        for it in self.items.values():
            if it.kind == "mesh" and it.visible and not it.ghost:
                draw_mesh(it)
        ctx.enable(mgl.BLEND)
        ctx.blend_func = mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA
        ctx.depth_mask = False
        for it in self.items.values():                      # fantasmas: translúcidos, con prueba de profundidad pero sin escribirla
            if it.kind == "mesh" and it.visible and it.ghost:
                if it.cull:
                    ctx.enable(mgl.CULL_FACE)
                draw_mesh(it)
                if it.cull:
                    ctx.disable(mgl.CULL_FACE)
        for it in self.items.values():
            if it.kind == "quad" and it.visible:
                self._flat(it, mvp, mgl.TRIANGLES)
        ctx.depth_mask = True
        for on_top in (False, True):
            if on_top:
                ctx.disable(mgl.DEPTH_TEST)
            for it in self.items.values():
                if not it.visible or it.on_top != on_top:
                    continue
                if it.kind == "lines" and it.width > 1.0:
                    self._thick(it, mvp, width, height)
                elif it.kind == "lines":
                    self._flat(it, mvp, mgl.LINES)
                elif it.kind == "points":
                    self._flat(it, mvp, mgl.POINTS, round_=True)
        ctx.enable(mgl.DEPTH_TEST)
        ctx.disable(mgl.BLEND)

    def _thick(self, it: Item, mvp, width: int, height: int) -> None:
        p = self.thick_prog
        p["u_mvp"].write(mvp.T.tobytes())
        p["u_color"].value = it.color
        p["u_bias"].value = it.bias
        p["u_width"].value = it.width
        p["u_viewport"].value = (float(width), float(max(height, 1)))
        p["u_round"].value = 0
        it.vao.render(self.mgl.TRIANGLES, vertices=it.count)

    def _flat(self, it: Item, mvp, mode, round_: bool = False) -> None:
        p = self.flat_prog
        p["u_mvp"].write(mvp.T.tobytes())
        p["u_color"].value = it.color
        p["u_point_size"].value = it.size
        p["u_bias"].value = it.bias
        p["u_round"].value = 1 if round_ else 0
        it.vao.render(mode, vertices=it.count)


class OffscreenRenderer:
    """Contexto EGL independiente + framebuffer: dibuja una escena a un arreglo RGBA (pruebas / capturas)."""

    def __init__(self, width: int = 640, height: int = 480):
        import moderngl

        self.ctx = moderngl.create_standalone_context(backend="egl")
        self.scene = SceneRenderer(self.ctx)
        self.resize(width, height)

    def resize(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.fbo = self.ctx.simple_framebuffer((width, height), components=4)

    def render(self, camera: OrbitCamera) -> np.ndarray:
        self.scene.draw(camera, self.width, self.height, self.fbo)
        raw = self.fbo.read(components=4)
        return np.frombuffer(raw, np.uint8).reshape(self.height, self.width, 4)[::-1].copy()

    def save_png(self, camera: OrbitCamera, path: str) -> None:
        from PIL import Image

        Image.fromarray(self.render(camera), "RGBA").save(path)
