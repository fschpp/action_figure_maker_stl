"""M15: vista previa del arma y del puente y mapa de zonas sobre la vista conjunta (lógica sin GTK, `gui/mount_preview.py`)."""
import os
import shutil

import numpy as np
import pytest

from afm import mount_map as MM
from afm import props as P
from afm import solids as S
from afm.catalog import Catalog
from afm.gui import figure_builder as FB
from afm.gui import mount_pick as MP
from afm.gui import mount_preview as PV
from afm.gui.catalog_manager import PREVIEW_PITCH, PREVIEW_YAW

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
ARMA = "espada-prueba-01@1"
IMG = (840, 630)


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
    fb.set_accessory(ARMA)
    return fb


def _flank_anchor(parts, lugar="pelvis"):
    cam = MP.group_camera(parts, *IMG, yaw_off=-PREVIEW_YAW, pitch_off=-PREVIEW_PITCH)
    hits = []
    for py in range(20, IMG[1], 10):
        for px in range(20, IMG[0], 10):
            try:
                hits.append(MP.pick_anchor(cam, px, py, *IMG, parts, lugar))
            except MP.MountPickError:
                pass
    hits = [h for h in hits if h.normal[0] > 0.9]                  # un flanco de verdad: la normal mira hacia afuera (+X)
    z = np.array([h.ancla[2] for h in hits])
    return hits[int(np.argmin(np.abs(z - np.median(z))))]


def test_una_montura_bien_puesta_muestra_arma_y_puente_de_colores(b):
    parts, _ = b.preview()
    a = _flank_anchor(parts)
    i = b.add_mount(ARMA, "pelvis", a.lado, a.ancla, (0, 0, -1))
    pv = PV.mount_preview(b, i, parts)
    assert pv.ok, pv.text
    names = [n for n, *_ in pv.parts]
    assert names == [f"montura_arma_{i}", f"montura_puente_{i}"]
    arma, puente = pv.parts[0][1], pv.parts[1][1]
    assert pv.parts[0][2] == PV.WEAPON_COLOR and pv.parts[1][2] == PV.BRIDGE_COLOR
    assert arma.volume > 0 and puente.volume > 0
    assert np.linalg.norm(puente.centroid - np.asarray(a.ancla)) < 10.0        # el puente está pegado al ancla
    assert "abertura" in pv.text


def test_una_montura_que_no_toca_la_pieza_sale_en_rojo_con_el_motivo(b):
    parts, _ = b.preview()
    i = b.add_mount(ARMA, "pelvis", "izq", (500, 0, 0), (0, 0, -1))
    pv = PV.mount_preview(b, i, parts)
    assert not pv.ok and pv.parts == [] and pv.text            # el motivo sale del mismo código del armado


def test_los_avisos_del_puente_llegan_al_texto(b, monkeypatch):
    """Si el puente genera avisos (la guarda no apoya), la vista previa los muestra junto al «sale»."""
    parts, _ = b.preview()
    a = _flank_anchor(parts)
    i = b.add_mount(ARMA, "pelvis", a.lado, a.ancla, (0, 0, -1))
    real = PV.MT.apply_mounts

    def con_aviso(pieces, mounts, min_wall):
        out, built = real(pieces, mounts, min_wall)
        for mb in built.values():
            mb.info["avisos"] = ["la espada se desliza (aviso de prueba)"]
        return out, built

    monkeypatch.setattr(PV.MT, "apply_mounts", con_aviso)
    pv = PV.mount_preview(b, i, parts)
    assert pv.ok and pv.avisos == ["la espada se desliza (aviso de prueba)"] and "- la espada se desliza" in pv.text


def test_el_mapa_de_zonas_colorea_el_flanco(b):
    parts, _ = b.preview()
    a = _flank_anchor(parts)
    i = b.add_mount(ARMA, "pelvis", a.lado, a.ancla, (0, 0, -1))
    zm = PV.zone_map(b, i, parts)
    assert zm.overlay and set(zm.overlay) <= set(PV.LEVEL_NAMES.values())
    assert sum(len(p) for p, _c in zm.overlay.values()) == len(zm.fm.points) > 100
    assert zm.fm.lado == "izq" and (zm.fm.points[:, 0] > 0).mean() > 0.7                     # sobre todo el flanco +X (hay escalones con la normal hacia +X)
    assert zm.fm.need_mm > 0.5 and "%" in zm.text and "de pie" in zm.text
    assert any(w in zm.text for w in ("cabe", "no cabe"))


def test_el_mapa_dice_si_el_ancla_cabe(b):
    parts, _ = b.preview()
    a = _flank_anchor(parts)
    i = b.add_mount(ARMA, "pelvis", a.lado, a.ancla, (0, 0, -1))
    zm = PV.zone_map(b, i, parts)
    free, dist = PV.free_at(zm.fm, a.ancla)
    assert dist < 3.0
    assert ("En el ancla hay" in zm.text) and (f"{free:.1f}" in zm.text)
    far = b.add_mount(ARMA, "pelvis", "izq", (300, 0, 0), (0, 0, -1))
    assert "no dice nada" in PV.zone_map(b, far, parts).text


def test_el_mapa_del_lado_der_usa_los_miembros_der_y_el_flanco_menos_x(b):
    parts, _ = b.preview()
    cam = MP.group_camera(parts, *IMG, yaw_off=180 - PREVIEW_YAW, pitch_off=-PREVIEW_PITCH)
    hit = next(h for h in (MP.pick_anchor(cam, px, py, *IMG, parts, "pelvis") for py in range(20, 600, 10) for px in range(20, 800, 10)
                           if _ok(cam, px, py, parts)) if h.normal[0] < -0.9)
    i = b.add_mount(ARMA, "pelvis", hit.lado, hit.ancla, (0, 0, -1))
    zm = PV.zone_map(b, i, parts)
    assert hit.lado == "der" and zm.fm.lado == "der" and (zm.fm.points[:, 0] < 0).mean() > 0.7


def _ok(cam, px, py, parts):
    try:
        MP.pick_anchor(cam, px, py, *IMG, parts, "pelvis")
        return True
    except MP.MountPickError:
        return False


def test_el_espesor_de_la_hoja_entra_en_lo_que_necesita(b):
    parts, _ = b.preview()
    i = b.add_mount(ARMA, "pelvis", "izq", (11, 0, 30), (0, 0, -1), holgura=0.25)
    j = b.add_mount(ARMA, "pelvis", "izq", (11, 0, 30), (0, 0, -1), holgura=0.45)
    assert PV._need_mm(b, b.mounts[j]) - PV._need_mm(b, b.mounts[i]) == pytest.approx(2 * 0.2)       # 2 × la diferencia de holgura
    assert MM.needed_height(0.8) > 2.0



def test_el_tamano_del_arma_enfundada_llega_al_texto(b):
    """La vista previa de la montura suma cuánto de la altura ocupa el arma y dónde queda su punta (`weapon_fit.simulate_on_body`)."""
    parts, _ = b.preview()
    a = _flank_anchor(parts)
    i = b.add_mount(ARMA, "pelvis", a.lado, a.ancla, (0, 0, -1))
    pv = PV.mount_preview(b, i, parts)
    assert pv.ok, pv.text
    assert "En el cuerpo:" in pv.text and "% de la altura" in pv.text
    # la espada de prueba mide ≈ 36 mm frente a una figura de ≈ 100 mm: es chica y el aviso sale (en rojo en la ventana)
    assert pv.size_problem == ("corta_vs_figura" in pv.text or "diminuta" in pv.text or "choca" in pv.text or "piso" in pv.text)
