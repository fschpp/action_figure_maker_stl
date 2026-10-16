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
void main() {
    v_pos = in_pos;
    v_nrm = u_view_rot * in_nrm;
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
out vec4 f_color;
void main() {
    if (v_pos.x < u_clip_x) discard;
    if (dot(u_clip_n, v_pos) > u_clip_d) discard;
    vec3 n = normalize(v_nrm);
    if (!gl_FrontFacing) n = -n;
    float key = max(dot(n, normalize(vec3(0.35, 0.45, 0.85))), 0.0);
    float fill = max(dot(n, normalize(vec3(-0.6, -0.2, 0.5))), 0.0);
    float l = 0.30 + 0.62 * key + 0.18 * fill;
    f_color = vec4(u_color.rgb * l, u_color.a);
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


def _flat_vertices(mesh: trimesh.Trimesh) -> np.ndarray:
    tris = np.asarray(mesh.triangles, np.float32)
    nrm = np.repeat(np.asarray(mesh.face_normals, np.float32)[:, None, :], 3, axis=1)
    return np.concatenate([tris, nrm], axis=2).reshape(-1, 6).astype("f4")


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
        self.flat_prog = ctx.program(vertex_shader=_FLAT_VS, fragment_shader=_FLAT_FS)
        self.items: dict[str, Item] = {}
        self.background = (0.13, 0.14, 0.16, 1.0)

    # ------------------------------------------------------------- objetos
    def _put(self, name: str, item: Item) -> Item:
        old = self.items.pop(name, None)
        if old is not None:
            self._free(old)
        self.items[name] = item
        return item

    @staticmethod
    def _free(item: Item) -> None:
        for o in (item.vao, item.vbo):
            if o is not None:
                o.release()

    def set_mesh(self, name: str, mesh: trimesh.Trimesh | None, color=(0.78, 0.80, 0.84, 1.0), clip_x: float = -1e9,
                 clip_plane=None):
        if mesh is None or len(mesh.faces) == 0:
            return self.remove(name)
        data = _flat_vertices(mesh)
        vbo = self.ctx.buffer(data.tobytes())
        vao = self.ctx.vertex_array(self.mesh_prog, [(vbo, "3f 3f", "in_pos", "in_nrm")])
        return self._put(name, Item("mesh", vao, vbo, len(data), color, clip_x=clip_x, clip_plane=clip_plane))

    def set_lines(self, name: str, polylines, color=(1.0, 0.8, 0.2, 1.0), closed: bool = False,
                  on_top: bool = False):
        """`polylines`: lista de arreglos Nx3 (o un único Nx3)."""
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
        vbo = self.ctx.buffer(data.tobytes())
        vao = self.ctx.vertex_array(self.flat_prog, [(vbo, "3f", "in_pos")])
        return self._put(name, Item("lines", vao, vbo, len(data), color, bias=2e-4, on_top=on_top))

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
        for it in self.items.values():
            if it.kind != "mesh" or not it.visible:
                continue
            self.mesh_prog["u_mvp"].write(mvp.T.tobytes())
            self.mesh_prog["u_view_rot"].write(view_rot.T.tobytes())
            self.mesh_prog["u_color"].value = it.color
            self.mesh_prog["u_clip_x"].value = it.clip_x
            cp = it.clip_plane if it.clip_plane is not None else (0.0, 0.0, 0.0, 1e9)
            self.mesh_prog["u_clip_n"].value = tuple(float(x) for x in cp[:3])
            self.mesh_prog["u_clip_d"].value = float(cp[3])
            it.vao.render(mgl.TRIANGLES, vertices=it.count)
        ctx.enable(mgl.BLEND)
        ctx.blend_func = mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA
        ctx.depth_mask = False
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
                if it.kind == "lines":
                    self._flat(it, mvp, mgl.LINES)
                elif it.kind == "points":
                    self._flat(it, mvp, mgl.POINTS, round_=True)
        ctx.enable(mgl.DEPTH_TEST)
        ctx.disable(mgl.BLEND)

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
