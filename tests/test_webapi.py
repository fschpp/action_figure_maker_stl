"""EngineSession: la API de bytes que usa el navegador (Pyodide). Corre con la figura sintética."""
import io
import json
import zipfile

import pytest

from afm.testfigure import make_doll
from afm.webapi import EngineSession


def test_load_stl_reports_the_mesh_and_rejects_garbage():
    s = EngineSession()
    buf = io.BytesIO()
    make_doll().export(buf, file_type="stl")
    r = s.load_stl(buf.getvalue())
    assert r["triangulos"] > 100 and 90 < r["alto_mm"] < 110
    with pytest.raises(ValueError):
        s.load_stl(b"esto no es un stl")
    s.close()


def test_load_stl_warns_about_heavy_meshes(monkeypatch):
    import afm.webapi as webapi
    buf = io.BytesIO()
    make_doll().export(buf, file_type="stl")
    data = buf.getvalue()
    s = EngineSession()
    assert not any("triángulos" in a for a in s.load_stl(data)["avisos"])
    monkeypatch.setattr(webapi, "HEAVY_STL_TRIANGLES", 100)      # la muñeca supera este umbral
    avisos = s.load_stl(data)["avisos"]
    assert any("decimarlo" in a for a in avisos)
    s.close()


def test_decimate_loaded_reduces_keeping_height_and_requires_stl():
    pytest.importorskip("fast_simplification")
    import trimesh
    with pytest.raises(RuntimeError):
        EngineSession().decimate_loaded(500)
    heavy = make_doll().subdivide().subdivide()                 # ~16x los triángulos
    buf = io.BytesIO()
    heavy.export(buf, file_type="stl")
    s = EngineSession()
    info = s.load_stl(buf.getvalue())
    assert info["pesado"] is (info["triangulos"] > 15_000)
    r = s.decimate_loaded(2000)
    assert r["reducido"] and r["stl"] and r["triangulos"] < r["triangulos_antes"]
    assert abs(r["alto_mm"] - info["alto_mm"]) < 0.005 * info["alto_mm"]
    assert len(trimesh.load(io.BytesIO(r["stl"]), file_type="stl").faces) == r["triangulos"]
    assert s.decimate_loaded(10**6)["reducido"] is False          # ya es más chico que el objetivo
    with pytest.raises(ValueError):
        s.decimate_loaded(5)
    s.close()


def test_decimate_loaded_handles_open_meshes_keeping_height():
    """Figura real del dueño: no estanca. Con agujeros sueltos la decimación reduce, no rompe y mantiene el alto."""
    pytest.importorskip("fast_simplification")
    import numpy as np
    import trimesh
    heavy = make_doll().subdivide().subdivide()
    keep = np.ones(len(heavy.faces), bool)
    keep[np.random.default_rng(0).choice(len(heavy.faces), 60, replace=False)] = False
    buf = io.BytesIO()
    trimesh.Trimesh(heavy.vertices, heavy.faces[keep], process=False).export(buf, file_type="stl")
    s = EngineSession()
    info = s.load_stl(buf.getvalue())
    assert info["estanca"] is False and info["pesado"]
    r = s.decimate_loaded(10_000)
    assert r["reducido"] and r["triangulos"] < 0.5 * r["triangulos_antes"]
    assert abs(r["alto_mm"] - info["alto_mm"]) < 0.005 * info["alto_mm"]
    assert r["triangulos"] < 1.5 * 10_000                         # llega a ~el objetivo (medido: ~+20 %)
    s.close()


def test_build_requires_a_loaded_stl():
    with pytest.raises(RuntimeError):
        EngineSession().build("{}")


def test_demo_returns_a_zip_with_the_parts_and_the_report():
    s = EngineSession()
    msgs = []
    r = s.demo(progress_cb=msgs.append)
    assert msgs, "el progreso debería informar etapas"
    z = zipfile.ZipFile(io.BytesIO(r["zip"]))
    assert sorted(z.namelist()) == r["archivos"]
    assert any(n.endswith(".stl") for n in r["archivos"]) and "muneco_informe.json" in r["archivos"]
    assert json.loads(z.read("muneco_informe.json"))["validacion"]["ok"] is True
    assert r["informe"]["validacion"]["ok"] is True
    s.close()


# --- M2: puntos elegidos sobre el STL crudo → marco de la figura; miembros desde 3 puntos --------------------------
def _odd_doll(tmp_path):
    """Muñeco en otras unidades (÷100) y fuera del origen: el caso en que el marco del archivo ≠ el de la figura."""
    from afm.testfigure import make_doll

    m = make_doll()
    m.apply_scale(0.01)
    m.apply_translation([5, 3, 2])
    p = tmp_path / "odd.stl"
    m.export(str(p))
    return p, m


def test_figure_frame_maps_file_points_into_the_normalized_figure(tmp_path):
    import numpy as np

    from afm.webapi import EngineSession

    p, m = _odd_doll(tmp_path)
    s = EngineSession()
    info = s.load_stl(p.read_bytes())
    assert info["z_arriba"] and not info["avisos"]
    fr = s.figure_frame()
    q = np.asarray(fr["k"]) * m.bounds + np.asarray(fr["offset"])
    # la caja del archivo cae sobre la de la figura normalizada: pies en z=0, coronilla en 100, centrada en X e Y
    assert np.allclose(q[:, 2], [0.0, 100.0], atol=1e-6)
    assert np.allclose(q[:, :2].sum(axis=0), 0.0, atol=1e-6)


def test_figure_frame_refuses_a_file_that_is_not_z_up(tmp_path):
    import numpy as np
    import pytest

    from afm.testfigure import make_doll
    from afm.webapi import EngineSession

    m = make_doll()
    m.apply_transform(np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float))   # Y arriba
    p = tmp_path / "yup.stl"
    m.export(str(p))
    s = EngineSession()
    info = s.load_stl(p.read_bytes())
    assert info["z_arriba"] is False and info["avisos"]
    with pytest.raises(ValueError, match="Z arriba"):
        s.figure_frame()


def test_new_limb_defaults_and_collinear_points():
    import pytest

    from afm.limbs import LimbSpec
    from afm.webapi import EngineSession

    arm = EngineSession.new_limb("brazo", "arm", [0, 3, 0], [1, 3, 0], [0, 3, 1])
    leg = EngineSession.new_limb("pierna", "leg", [0, 3, 0], [1, 3, 0], [0, 3, 1])
    assert arm["extrude"] == "outward" and abs(arm["thickness"] - 12 * 1.653) < 1e-9
    assert leg["extrude"] == "symmetric" and leg["thickness"] == 100.0       # plano frontal: prisma simétrico
    assert arm["polygon"] == [] and arm["auto"] is True
    LimbSpec.from_dict(arm)                                                  # el JSON vuelve a leerse como LimbSpec
    side = EngineSession.new_limb("pierna", "leg", [7, 0, 0], [7, 1, 0], [7, 0, 1])
    assert side["extrude"] == "outward"                                       # plano de costado = la cara de contacto
    with pytest.raises(ValueError, match="colineales"):
        EngineSession.new_limb("x", "leg", [0, 0, 0], [1, 1, 1], [2, 2, 2])
    with pytest.raises(ValueError, match="arm"):
        EngineSession.new_limb("x", "head", [0, 0, 0], [1, 0, 0], [0, 1, 0])


def test_new_head_and_new_waist_from_three_points():
    import pytest

    from afm.head import HeadSpec
    from afm.waist import WaistSpec
    from afm.webapi import EngineSession

    # cuello: 2 puntos en la nuca y 1 bajo el mentón (plano casi horizontal, algo inclinado)
    head = EngineSession.new_head([-5, -5, 80], [5, -5, 80], [0, 5, 82])
    assert head["auto"] is True and head["pin_center"] is None and head["replacement_path"] is None
    assert HeadSpec.from_dict(head).plane.normal[2] > 0.9
    waist = EngineSession.new_waist([-8, 0, 50], [8, 0, 50], [0, 6, 50])
    assert WaistSpec.from_dict(waist).plane.normal[2] > 0.99 and waist["pin_on"] == "lower"
    # los mismos rechazos que la GUI de escritorio: plano vertical, de costado o con puntos colineales
    with pytest.raises(ValueError, match="vertical"):
        EngineSession.new_head([0, 0, 0], [1, 0, 0], [0, 0, 1])
    with pytest.raises(ValueError, match="costado"):
        EngineSession.new_waist([0, 0, 50], [0, 1, 50], [1, 0, 53])
    with pytest.raises(ValueError, match="colineales"):
        EngineSession.new_waist([0, 0, 0], [1, 1, 1], [2, 2, 2])


# --- preview_pose: figura generada con la cadera en otro ángulo (slider de la página) -----------------------------
def test_preview_pose_rotates_the_legs_and_keeps_the_body():
    import numpy as np
    import pytest

    from afm.webapi import EngineSession

    s = EngineSession()
    with pytest.raises(RuntimeError, match="generar"):
        s.preview_pose(0)
    s.demo()

    def verts(pose, name):
        return np.frombuffer(pose["piezas"][name]["v"], np.float32).reshape(-1, 3)

    p0, p90, p_big = s.preview_pose(0), s.preview_pose(90), s.preview_pose(500, collisions=False)
    assert {"torso", "pierna_izq", "pierna_der"} <= set(p90["piezas"])
    assert p_big["angulo"] == 95.0                                    # sit_angle 90° + margen 5°
    assert p_big["colisiones"] == {}                                  # sin `collisions` no se calcula
    for pose in (p0, p90):
        i = np.frombuffer(pose["piezas"]["pierna_izq"]["i"], np.uint32)
        assert i.size % 3 == 0 and i.max() < len(verts(pose, "pierna_izq"))
    assert np.allclose(verts(p0, "torso"), verts(p90, "torso"))       # el torso no se mueve
    a, b = verts(p0, "pierna_izq"), verts(p90, "pierna_izq")
    assert abs(a[:, 1].mean() - b[:, 1].mean()) > 5 or abs(a[:, 2].mean() - b[:, 2].mean()) > 5   # la pierna giró
    assert p0["colisiones"] == {} and p90["colisiones"] == {}         # el recorrido libre de la demo no choca
    assert np.allclose(verts(p90, "pierna_der")[:, 0].mean(), -verts(p90, "pierna_izq")[:, 0].mean(), atol=1e-3)  # espejo


# --- M3: datos del editor de polígonos 2D ------------------------------------------------------------------------
def _doll_session():
    s = EngineSession()
    buf = io.BytesIO()
    make_doll().export(buf, file_type="stl")
    s.load_stl(buf.getvalue())
    return s


def test_limb_editor_gives_plane_background_polygon_and_proposal():
    import numpy as np

    from afm.project import demo_project

    s = _doll_session()
    d = demo_project().to_dict()
    for l in d["limbs"]:
        if l["kind"] == "leg":
            l["polygon"] = []
    r = s.limb_editor(d, "pierna")
    u, v, n = (np.array(r[k]) for k in ("u", "v", "normal"))
    assert abs(u @ v) < 1e-9 and abs(np.linalg.norm(np.cross(u, v)) - 1) < 1e-9 and abs(abs(n @ np.cross(u, v)) - 1) < 1e-9
    assert r["silueta"] and len(r["silueta"][0]["exterior"]) >= 3 and r["polygon"] == []
    assert r["propuesta"] and len(r["propuesta"]) >= 3 and r["aviso"] is None
    # el polígono propuesto (u, v) vuelve a 3D sobre el plano: la misma pasada que hace la página
    world = np.array(r["origin"]) + np.outer(np.array(r["propuesta"])[:, 0], u) + np.outer(np.array(r["propuesta"])[:, 1], v)
    assert np.allclose((world - np.array(r["origin"])) @ n, 0, atol=1e-9)
    s.close()


def test_limb_editor_returns_the_existing_polygon_and_explains_when_it_cannot_propose():
    from afm.project import demo_project

    s = _doll_session()
    d = demo_project().to_dict()
    r = s.limb_editor(d, "brazo")                 # el brazo del muñeco: con polígono; no hay tramo libre para proponer
    assert len(r["polygon"]) == 4 and r["propuesta"] is None and "a mano" in r["aviso"]
    assert s.limb_editor(d, "brazo", propose=False)["aviso"] is None
    with pytest.raises(ValueError, match="no tiene un miembro"):
        s.limb_editor(d, "alas")
    with pytest.raises(RuntimeError):
        EngineSession().limb_editor(d, "brazo")
    s.close()
