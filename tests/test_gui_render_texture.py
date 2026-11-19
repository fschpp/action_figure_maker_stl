"""T24 del plan de color: el visor dibuja mallas con textura.

La parte de datos (`_flat_vertices_uv`, `load_glb_parts`, `part_from_textured_mesh`) no necesita GL ni GTK. Las pruebas que
dibujan usan el contexto EGL fuera de pantalla, como `test_gui_render.py`, y se saltan solas si el entorno no lo tiene.
"""
import os

import numpy as np
import pytest
import trimesh

from afm.frame import FigureFrame
from afm.gui.camera import OrbitCamera
from afm.gui.render import (TexturedPart, _flat_vertices, _flat_vertices_uv, _with_define, load_glb_parts, part_from_textured_mesh,
                            _MESH_FS, _MESH_VS)

from color_helpers import doll_textured

REAL_FBX = os.path.join(os.path.dirname(__file__), "..", "examples", "guemes_segmentado_lq_texturas.fbx")
RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)


@pytest.fixture(scope="module")
def doll_tm():
    return doll_textured()


def _quad():
    """Un cuadrado de 20 × 20 en el plano XZ (de cara a la vista «frente»); v hacia arriba: z = 0 → v = 0, z = 20 → v = 1."""
    V = np.array([[-10, 0, 0], [10, 0, 0], [10, 0, 20], [-10, 0, 20]], float)
    F = np.array([[0, 1, 2], [0, 2, 3]])
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)[F]
    return trimesh.Trimesh(V, F, process=False), uv


def _top_red_bottom_green(n: int = 4) -> np.ndarray:
    img = np.zeros((n, n, 3), np.uint8)
    img[: n // 2] = RED                                   # la fila 0 es la de ARRIBA (como un PNG)
    img[n // 2:] = GREEN
    return img


# ----------------------------------------------------------------------------------------------- datos (sin GL)
def test_flat_vertices_uv_layout(doll_tm):
    mesh = doll_tm.to_trimesh()
    data = _flat_vertices_uv(mesh, doll_tm.corner_uv)
    assert data.dtype == np.float32
    assert data.shape == (len(mesh.faces) * 3, 8)                                    # 3 vértices por triángulo, 8 floats cada uno
    assert np.array_equal(data[:, :6], _flat_vertices(mesh))                         # xyz + normal: igual que antes, mismo orden
    first = np.asarray(mesh.triangles, np.float32)[0]
    assert np.array_equal(data[:3, :3], first)                                       # el triángulo 0 va primero, esquina por esquina


def test_uv_matches_corner_uv(doll_tm):
    mesh = doll_tm.to_trimesh()
    data = _flat_vertices_uv(mesh, doll_tm.corner_uv)
    assert np.array_equal(data[:, 6:8], np.asarray(doll_tm.corner_uv, np.float32).reshape(-1, 2))


def test_uv_per_corner_survives_a_seam():
    """Un vértice compartido por dos triángulos con UV distintos (costura) conserva el UV de CADA esquina."""
    mesh, _ = _quad()
    uv = np.array([[[0, 0], [1, 0], [1, 1]],                   # triángulo 0
                   [[0.5, 0.5], [1, 1], [0, 1]]], np.float32)  # triángulo 1: el vértice 0 (compartido) lleva otro UV
    data = _flat_vertices_uv(mesh, uv)
    shared = np.flatnonzero(np.all(data[:, :3] == mesh.vertices[0].astype(np.float32), axis=1))
    assert len(shared) == 2 and not np.array_equal(data[shared[0], 6:8], data[shared[1], 6:8])
    assert np.array_equal(data[shared[0], 6:8], [0, 0]) and np.array_equal(data[shared[1], 6:8], [0.5, 0.5])


def test_flat_vertices_uv_rejects_wrong_shape():
    mesh, uv = _quad()
    with pytest.raises(ValueError, match="corner_uv"):
        _flat_vertices_uv(mesh, uv[:1])
    with pytest.raises(ValueError, match="corner_uv"):
        _flat_vertices_uv(mesh, uv.reshape(-1, 2))


def test_shader_variants_share_one_source():
    vs, fs = _with_define(_MESH_VS, "TEXTURED"), _with_define(_MESH_FS, "TEXTURED")
    for src in (vs, fs):
        head = src.strip().splitlines()[:2]
        assert head == ["#version 330", "#define TEXTURED 1"]                     # `#define` justo después de `#version`
    assert "#define TEXTURED" not in _MESH_VS and "#define TEXTURED" not in _MESH_FS   # la variante sin textura no cambia


def test_load_glb_parts_roundtrip(tmp_path):
    """Un GLB como el de `Colorizer.write`: una malla con su textura por pieza; el UV (v hacia arriba) y el PNG vuelven iguales."""
    from PIL import Image

    mesh, uv = _quad()
    img = _top_red_bottom_green(8)
    mesh.visual = trimesh.visual.TextureVisuals(uv=np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float),
                                                material=trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(img)))
    other = trimesh.creation.box((5, 5, 5))                    # una pieza sin textura en la misma escena
    scene = trimesh.Scene()
    scene.add_geometry(mesh, node_name="pieza_a", geom_name="pieza_a")
    scene.add_geometry(other, node_name="sin_color", geom_name="sin_color")
    path = str(tmp_path / "fig_ensamblada.glb")
    scene.export(path)

    parts = {p.name: p for p in load_glb_parts(path)}
    assert set(parts) == {"pieza_a", "sin_color"}
    a = parts["pieza_a"]
    assert isinstance(a, TexturedPart) and a.image.shape == (8, 8, 3) and a.image.dtype == np.uint8
    assert np.array_equal(a.image, img)                                           # la fila 0 sigue arriba
    assert a.corner_uv.shape == (len(a.mesh.faces), 3, 2)
    expected = np.asarray([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)[np.asarray(a.mesh.faces)]
    assert np.allclose(a.corner_uv, expected, atol=1e-5) or _uv_matches_by_position(a, mesh, expected)
    assert parts["sin_color"].image is None and parts["sin_color"].corner_uv is None


def _uv_matches_by_position(part, ref_mesh, _expected) -> bool:
    """Si trimesh reordenó los vértices al leer, se compara por posición: cada esquina debe tener el UV que le toca (u = x, v = z)."""
    tri = np.asarray(part.mesh.triangles, float)
    u = (tri[..., 0] + 10) / 20.0
    v = tri[..., 2] / 20.0
    return bool(np.allclose(part.corner_uv[..., 0], u, atol=1e-5) and np.allclose(part.corner_uv[..., 1], v, atol=1e-5))


def test_part_from_textured_mesh_applies_frame(doll_tm):
    plain = part_from_textured_mesh(doll_tm)
    assert plain.image.shape == (256, 256, 3) and plain.corner_uv.shape == (len(plain.mesh.faces), 3, 2)
    assert np.allclose(plain.mesh.vertices, doll_tm.vertices)
    M = np.eye(4)
    M[0, 3] = 5.0
    moved = part_from_textured_mesh(doll_tm, FigureFrame(matrix=M), name="x")
    assert moved.name == "x"
    assert np.allclose(moved.mesh.vertices - plain.mesh.vertices, [5.0, 0.0, 0.0])
    assert np.array_equal(moved.corner_uv, plain.corner_uv)                       # la transformación es rígida: los UV no cambian


def test_part_from_textured_mesh_without_base_color_has_no_image(doll_tm):
    import copy

    tm = copy.copy(doll_tm)
    tm.images = {k: v for k, v in doll_tm.images.items() if k != "base_color"}
    part = part_from_textured_mesh(tm)
    assert part.image is None and part.corner_uv is None                          # se dibuja de color plano


# -------------------------------------------------------------------------------------------- dibujo (GL fuera de pantalla)
@pytest.fixture(scope="module")
def _renderer():
    pytest.importorskip("moderngl")
    from afm.gui.render import OffscreenRenderer

    try:
        return OffscreenRenderer(320, 400)
    except Exception as e:           # sin EGL/GL en este entorno
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {e}")


@pytest.fixture
def off(_renderer):
    """El renderer con SU contexto como el actual: otro test (`PreviewRenderer`) crea un segundo contexto EGL y, sin esto, el de acá
    deja de ser el vigente y moderngl falla al crear texturas."""
    with _renderer.ctx:
        yield _renderer


def _saturated(img, threshold: int) -> int:
    """Cantidad de píxeles con color (máx − mín de RGB mayor que `threshold`): un gris liso da ~0."""
    rgb = img[..., :3].astype(int)
    return int(((rgb.max(axis=2) - rgb.min(axis=2)) > threshold).sum())


def _px(off, cam, img, point):
    x, y, _ = cam.project(np.asarray(point, float), off.width, off.height)[0]
    return img[int(round(y)), int(round(x)), :3].astype(int)


def _quad_scene(off):
    off.scene.clear()
    mesh, uv = _quad()
    off.scene.set_textured_mesh("q", mesh, uv, _top_red_bottom_green(), color=(0, 0, 1, 1))
    cam = OrbitCamera()
    cam.frame(mesh.bounds)
    cam.set_view("frente")
    return cam


def test_v_axis_points_up_in_viewer(off):
    """`v` alto = fila de arriba de la imagen (convención FBX): la mitad de arriba del cuadrado sale roja y la de abajo, verde."""
    cam = _quad_scene(off)
    img = off.render(cam)
    top, bottom = _px(off, cam, img, [0, 0, 15]), _px(off, cam, img, [0, 0, 5])
    assert top[0] > 150 and top[1] < 40 and top[2] < 40, top
    assert bottom[1] > 150 and bottom[0] < 40 and bottom[2] < 40, bottom


def test_texture_toggle_flag_in_state(off):
    """El botón «Textura» vive en el renderer: encendido por defecto; apagado, la malla sale de su color plano (azul)."""
    cam = _quad_scene(off)
    assert off.scene.textures_on is True and off.scene.has_textures()
    off.scene.set_textures(False)
    assert off.scene.textures_on is False
    flat = off.render(cam)
    for z in (5, 15):
        p = _px(off, cam, flat, [0, 0, z])
        assert p[2] > 150 and p[0] < 40 and p[1] < 40, p
    off.scene.set_textures(True)
    assert off.scene.textures_on is True
    back = off.render(cam)
    assert _px(off, cam, back, [0, 0, 15])[0] > 150                              # vuelve el rojo


def test_flat_meshes_ignore_the_toggle(off):
    off.scene.clear()
    box = trimesh.creation.box((20, 20, 20))
    off.scene.set_mesh("caja", box, (0.8, 0.2, 0.2, 1.0))
    cam = OrbitCamera()
    cam.frame(box.bounds)
    cam.set_view("frente")
    off.scene.set_textures(True)
    a = off.render(cam)
    off.scene.set_textures(False)
    b = off.render(cam)
    off.scene.set_textures(True)
    assert not off.scene.has_textures()
    assert np.array_equal(a, b)


def test_removing_a_textured_mesh_drops_its_texture(off):
    _quad_scene(off)
    assert off.scene.has_textures()
    assert off.scene.items["q"].tex is not None
    off.scene.remove("q")
    assert "q" not in off.scene.items and not off.scene.has_textures()
    _quad_scene(off)                    # y se puede volver a subir con el mismo nombre
    assert off.scene.has_textures()
    off.scene.clear()
    assert not off.scene.has_textures()


def test_textured_mesh_can_be_translucent(off):
    """Una malla con textura también pasa por el camino de los fantasmas (translúcida, sin escribir profundidad)."""
    cam = _quad_scene(off)
    off.scene.set_translucent("q", 0.5, cull=False)       # el cuadrado de prueba mira a −Y: con `cull` se descartaría desde la cámara «frente»
    img = off.render(cam)
    p = _px(off, cam, img, [0, 0, 15])
    assert 90 < p[0] < 200 and p[1] < 90, p                                       # el rojo mezclado con el fondo: atenuado, no ausente ni pleno
    off.scene.set_translucent("q", None)


def test_preview_renderer_draws_textured_parts_and_toggles(doll_tm):
    pytest.importorskip("moderngl")
    from afm.gui.catalog_manager import PreviewRenderer

    pr = PreviewRenderer(320, 400)
    if not pr.available:
        pytest.skip(f"sin contexto OpenGL fuera de pantalla: {pr.error}")
    try:
        parts = [part_from_textured_mesh(doll_tm)]
        on = pr.render_textured(parts, True)
        vbo = pr._off.scene.items["fuente"].vbo
        off_img = pr.render_textured(parts, False, yaw_off=0.0)
        assert pr._off.scene.items["fuente"].vbo is vbo                           # mismo objeto de partes: no se vuelve a subir a la GPU
        assert on.shape == (400, 320, 4) and not np.array_equal(on, off_img)
        assert _saturated(on, 60) > 500 and _saturated(off_img, 60) < 50          # con textura hay rojos, azules, amarillos…; sin ella, gris
        with pytest.raises(ValueError):
            pr.render_textured([], True)
    finally:
        pr.close()


@pytest.mark.skipif(not os.path.exists(REAL_FBX), reason="falta el FBX de ejemplo")
def test_real_fbx_source_renders_with_its_colors(off):
    """El FBX real de Güemes (4096²) se sube y se dibuja: con textura hay oro, verde y piel; sin ella, un solo gris."""
    from afm.fbxio import load_textured

    tm = load_textured(REAL_FBX)
    M = np.eye(4)
    M[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]                                # Y arriba → Z arriba, como `orient_z_up`
    part = part_from_textured_mesh(tm, FigureFrame(matrix=M))
    assert part.image.shape == (4096, 4096, 3)
    off.scene.clear()
    off.scene.set_textured_mesh("fuente", part.mesh, part.corner_uv, part.image)
    cam = OrbitCamera()
    cam.frame(part.mesh.bounds)
    cam.set_view("espalda")                                                       # la figura mira a −Y: su frente da a la cámara «espalda»
    on = off.render(cam)
    off.scene.set_textures(False)
    flat = off.render(cam)
    off.scene.clear()
    assert _saturated(on, 40) > 300, _saturated(on, 40)
    assert _saturated(flat, 40) < 50, _saturated(flat, 40)
