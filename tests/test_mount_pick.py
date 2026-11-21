"""M14: ancla por clic y dirección por arrastre sobre la vista previa conjunta (lógica sin GTK, `gui/mount_pick.py`)."""
import math
import os
import shutil

import numpy as np
import pytest
import trimesh

from afm import props as P
from afm import solids as S
from afm.catalog import Catalog
from afm.gui import figure_builder as FB
from afm.gui import mount_pick as MP
from afm.gui.catalog_manager import PREVIEW_PITCH, PREVIEW_YAW

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
ARMA = "espada-prueba-01@1"
IMG = (840, 630)
WIDGET = (420, 400)          # el widget es más angosto que la imagen: la imagen se ve achicada y con margen arriba y abajo


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    m = trimesh.creation.box(extents=hi - lo)
    m.apply_translation((lo + hi) / 2)
    return m


@pytest.fixture()
def parts():
    """Torso (caja) con una pelvis debajo y un brazo pegado al flanco +X, como la vista conjunta (nombre, malla, color)."""
    return [("torso", _box((-12, -7, 40), (12, 7, 80)), (1, 1, 1, 1)),
            ("pelvis", _box((-11, -6, 20), (11, 6, 40)), (1, 1, 1, 1)),
            ("brazo", _box((12.5, -3, 45), (16, 3, 78)), (1, 1, 1, 1))]


def _side_camera(parts, side=+1):
    """Cámara que mira el flanco (+X o −X) de frente: yaw 0 / 180°, pitch 0."""
    return MP.group_camera(parts, *IMG, yaw_off=(0 if side > 0 else 180) - PREVIEW_YAW, pitch_off=-PREVIEW_PITCH)


def test_la_camara_del_clic_es_la_del_dibujo(parts):
    cam = MP.group_camera(parts, *IMG)
    ref = __import__("afm.gui.catalog_manager", fromlist=["preview_camera"]).preview_camera(MP.parts_bounds(parts), IMG[0] / IMG[1], 0.0, 0.0)
    assert (cam.eye() == ref.eye()).all() and (np.asarray(cam.target) == np.asarray(ref.target)).all()
    assert MP.parts_bounds(parts).tolist() == [[-12, -7, 20], [16, 7, 80]]


def test_un_clic_sobre_el_flanco_da_el_punto_y_el_lado(parts):
    cam = _side_camera(parts, +1)
    target = np.array([12.0, 0.0, 30.0])                       # flanco +X de la pelvis (el brazo cubre z 45–78)
    px, py = cam.project(target, *IMG)[0, :2]
    a = MP.pick_anchor(cam, px, py, *IMG, parts, "pelvis")
    assert a.lado == "izq" and a.pieza == "pelvis"
    assert np.allclose(a.ancla, (11.0, 0.0, 30.0), atol=0.02)    # la pelvis llega a x = 11, no a 12
    assert a.normal[0] > 0.99
    assert all(round(v, 2) == v for v in a.ancla)                # lo que va al formulario: 0,01 mm


def test_el_flanco_opuesto_es_lado_der_en_el_marco_real(parts):
    cam = _side_camera(parts, -1)
    px, py = cam.project(np.array([-11.0, 0.0, 30.0]), *IMG)[0, :2]
    a = MP.pick_anchor(cam, px, py, *IMG, parts, "pelvis")
    assert a.lado == "der" and a.ancla[0] == pytest.approx(-11.0, abs=0.02) and a.normal[0] < -0.99


def test_si_otra_pieza_tapa_el_flanco_se_avisa(parts):
    cam = _side_camera(parts, +1)
    px, py = cam.project(np.array([12.0, 0.0, 60.0]), *IMG)[0, :2]       # ahí se ve el brazo
    with pytest.raises(MP.MountPickError, match="brazo"):
        MP.pick_anchor(cam, px, py, *IMG, parts, "torso")
    # el brazo no es un lugar de montura: pedir el torso con el clic sobre el brazo no deja pasar
    with pytest.raises(MP.MountPickError, match="«brazo», no «torso»"):
        MP.pick_anchor(cam, px, py, *IMG, parts, "torso")


def test_un_clic_fuera_de_la_figura_se_avisa(parts):
    cam = _side_camera(parts, +1)
    with pytest.raises(MP.MountPickError, match="no hay figura"):
        MP.pick_anchor(cam, 2.0, 2.0, *IMG, parts, "pelvis")


def test_pelvis_sin_cintura_cae_en_el_torso():
    solo = [("torso", _box((-12, -7, 20), (12, 7, 80)), (1, 1, 1, 1))]
    cam = _side_camera(solo, +1)
    px, py = cam.project(np.array([12.0, 0.0, 50.0]), *IMG)[0, :2]
    assert MP.pick_anchor(cam, px, py, *IMG, solo, "pelvis").pieza == "torso"
    with pytest.raises(MP.MountPickError, match="no tiene la pieza"):
        MP.host_part_name("pelvis", ["cabeza"])


def test_el_clic_en_el_widget_pasa_por_el_margen_de_la_imagen(parts):
    cam = _side_camera(parts, +1)
    px, py = cam.project(np.array([12.0, 0.0, 30.0]), *IMG)[0, :2]
    k = min(WIDGET[0] / IMG[0], WIDGET[1] / IMG[1])
    x, y = px * k + (WIDGET[0] - IMG[0] * k) / 2, py * k + (WIDGET[1] - IMG[1] * k) / 2     # donde se ve ese píxel en el widget
    a = MP.pick_from_widget(x, y, WIDGET, IMG, parts, "pelvis", -PREVIEW_YAW, -PREVIEW_PITCH)
    assert np.allclose(a.ancla, (11.0, 0.0, 30.0), atol=0.05)
    with pytest.raises(MP.MountPickError, match="no hay figura"):
        MP.pick_from_widget(5.0, 1.0, WIDGET, IMG, parts, "pelvis", -PREVIEW_YAW, -PREVIEW_PITCH)       # margen de arriba


def _screen_pts(cam, anchor, direction, length=10.0):
    p = cam.project(np.array([anchor, np.asarray(anchor) + length * np.asarray(direction)]), *IMG)[:, :2]
    return tuple(p[0]), tuple(p[1])


def test_arrastrar_hacia_abajo_da_menos_z_exacto(parts):
    cam = _side_camera(parts, +1)
    p0, p1 = (400.0, 200.0), (402.0, 330.0)                      # casi vertical: dentro de los 6° de la vertical
    assert MP.direction_from_drag(cam, (11, 0, 30), p0, p1, *IMG) == (0.0, 0.0, -1.0)
    assert MP.direction_from_drag(cam, (11, 0, 30), p1, p0, *IMG) == (0.0, 0.0, 1.0)


@pytest.mark.parametrize("deg", [15, 30, 45, 60, -30])
def test_el_arrastre_recupera_una_direccion_en_el_plano_de_la_vista(parts, deg):
    cam = _side_camera(parts, +1)                                # mira desde +X: el plano de la vista es el YZ
    d = np.array([0.0, math.sin(math.radians(deg)), -math.cos(math.radians(deg))])
    p0, p1 = _screen_pts(cam, (11, 0, 30), d)
    got = MP.direction_from_drag(cam, (11, 0, 30), p0, p1, *IMG)
    assert np.allclose(got, d, atol=0.011)
    assert abs(np.linalg.norm(got) - 1.0) < 0.01 and got[0] == 0.0


def test_un_arrastre_corto_no_define_direccion(parts):
    cam = _side_camera(parts, +1)
    with pytest.raises(MP.MountPickError, match="un poco más"):
        MP.direction_from_drag(cam, (11, 0, 30), (400, 300), (403, 303), *IMG)
    with pytest.raises(MP.MountPickError, match="un poco más"):
        MP.direction_from_widget(10, 10, 12, 11, WIDGET, IMG, parts, (11, 0, 30))


def test_la_direccion_del_widget_usa_el_mismo_camino(parts):
    k = min(WIDGET[0] / IMG[0], WIDGET[1] / IMG[1])
    ox, oy = (WIDGET[0] - IMG[0] * k) / 2, (WIDGET[1] - IMG[1] * k) / 2
    cam = _side_camera(parts, +1)
    d = np.array([0.0, math.sin(math.radians(30)), -math.cos(math.radians(30))])
    p0, p1 = _screen_pts(cam, (11, 0, 30), d)
    got = MP.direction_from_widget(p0[0] * k + ox, p0[1] * k + oy, p1[0] * k + ox, p1[1] * k + oy, WIDGET, IMG, parts, (11, 0, 30),
                                   -PREVIEW_YAW, -PREVIEW_PITCH)
    assert np.allclose(got, d, atol=0.011)


def test_clic_o_giro():
    assert MP.is_click(0, 0) and MP.is_click(2, -3) and not MP.is_click(5, 5)


def test_marcas_de_las_monturas():
    class M:
        def __init__(self, a, d):
            self.ancla, self.direccion = a, d

    assert MP.mount_marks([]) is None
    pts = MP.mount_marks([M((1, 2, 3), (0, 0, -2))])
    assert pts.shape == (1 + MP.MARK_COUNT, 3) and (pts[0] == (1, 2, 3)).all()
    assert np.allclose(pts[1], (1, 2, 3 - MP.MARK_STEP_MM)) and np.allclose(pts[-1], (1, 2, 3 - MP.MARK_STEP_MM * MP.MARK_COUNT))
    assert MP.mount_marks([M((0, 0, 0), (0, 0, 0))]).shape == (1, 3)         # dirección nula: solo el ancla (MountSpec ya la rechaza)


# ---------------------------------------------------------------------- con el muñeco de ejemplo y el armado real
@pytest.fixture()
def b(tmp_path):
    dst = str(tmp_path / "ejemplo")
    shutil.copytree(EJEMPLO, dst, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    Catalog.open(dst).add_accessory(S.to_printable_trimesh(P.make_prop(P.GripSpec(), 10.0)), "espada-prueba-01",
                                    P.accessory_grip(P.GripSpec(), 10.0), descripcion="espada")
    fb = FB.FigureBuilder(dst)
    for place, ref in (("cabeza", "cabeza-muneco-01@1"), ("torso", "torso-muneco-01@1"), ("brazo", "brazo-muneco-01@1"),
                       ("pierna", "pierna-muneco-01@1"), ("mano", "mano-muneco-01@1")):
        fb.set_choice(place, ref)
    return fb


def _first_flank_click(parts, lugar, side=+1):
    """Barre la imagen de costado hasta dar con un clic que cae en la pieza que lleva la montura."""
    cam = MP.group_camera(parts, *IMG, yaw_off=(0 if side > 0 else 180) - PREVIEW_YAW, pitch_off=-PREVIEW_PITCH)
    found = []
    for py in range(20, IMG[1], 10):
        for px in range(20, IMG[0], 10):
            try:
                found.append(MP.pick_anchor(cam, px, py, *IMG, parts, lugar))
            except MP.MountPickError:
                pass
    return found


def test_el_ancla_elegida_en_la_vista_previa_se_arma_sobre_la_pieza(b, tmp_path):
    """Lo decisivo: el punto del clic está en el marco que usa `build`, o la montura diría «no toca la pieza»."""
    b.set_accessory(ARMA)
    parts, _notes = b.preview()
    hits = _first_flank_click(parts, "pelvis")
    assert hits, "ningún clic de costado cayó en la pieza que lleva la montura"
    lados = {h.lado for h in hits}
    assert lados == {"izq"}                                                        # visto desde +X solo se toca el flanco +X
    z = np.array([h.ancla[2] for h in hits])
    a = hits[int(np.argmin(np.abs(z - np.median(z))))]                             # un punto del medio de la pieza
    i = b.add_mount(ARMA, "pelvis", a.lado, a.ancla, (0, 0, -1))
    info = b.generate(str(tmp_path / "out"), progress=lambda *_: None)
    assert "monturas" in info, "la montura no entró en el armado"
    st = b.mount_status(i)
    assert st is not None and not any("no toca la pieza" in t for t in st["problemas"] + st["avisos"])
