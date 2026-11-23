"""«Calcular similitud…» del asistente de roles: la 4×4 FBX → figura se calcula sin salir de la GUI (lógica sin GTK, `RolesWizard.compute_similarity`)."""
import os

import numpy as np
import pytest

from afm.figsim import FigureSimilarityError, compute_figure_similarity
from afm.fbxio import load_textured
from afm.gui import roles_wizard as RW
from afm.catalog import Catalog

from test_gui_roles_wizard import HEAD_ID, line, make_ctrl, two_color_tm, head_mesh  # noqa: F401  (fixtures)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples")
STL = os.path.join(EX, "guemes_segmentado_lq_fixed_resized.stl")
PROJ = os.path.join(EX, "referencia", "guemes", "guemes.proyecto")
FBX = os.path.join(EX, "guemes_segmentado_lq_texturas.fbx")


@pytest.fixture(scope="module")
def tm():
    return load_textured(FBX)


def test_compute_figure_similarity_guemes(tm):
    sim = compute_figure_similarity(STL, PROJ, FBX, tm=tm)
    assert sim.ok and sim.media_mm < 0.1
    assert np.asarray(sim.matrix).shape == (4, 4)


def test_compute_figure_similarity_missing_file():
    with pytest.raises(FigureSimilarityError, match="STL"):
        compute_figure_similarity("/no/existe.stl", PROJ, FBX)


def test_wizard_compute_similarity_is_used_when_saving(line, head_mesh, tm, monkeypatch):  # noqa: F811
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(two_color_tm(head_mesh), {"nombre": "g.fbx", "sha256": "ab" * 32})
    with pytest.raises(RW.RolesWizardError):                                 # sin FBX cargado por ruta no se sabe cuál alinear
        w.compute_similarity(STL, PROJ)
    w.fbx_path, w.tm = FBX, tm
    info = w.compute_similarity(STL, PROJ)
    assert info["ok"] and w.similarity is not None
    seen = {}
    real = Catalog.add_piece

    def spy(self, *a, **k):
        seen["sim"] = k.get("similarity")
        return real(self, *a, **k)

    monkeypatch.setattr(Catalog, "add_piece", spy)
    for c in range(w.proposal.k):
        w.set_none(c)
    try:
        w.save_piece(f"{HEAD_ID}@1")
    except Exception:                                                        # el resultado del guardado no importa: solo qué similitud se pasó
        pass
    assert seen["sim"] == w.similarity
    w.clear_similarity()
    assert w.similarity is None


def test_wizard_rejects_similarity_of_other_figure(line, head_mesh, tm):  # noqa: F811
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(two_color_tm(head_mesh))
    w.fbx_path, w.tm = FBX, tm
    other = os.path.join(EX, "belgrano_lq_segmentado.stl")
    with pytest.raises(RW.RolesWizardError, match="no corresponde al proyecto"):   # STL de otra figura con el .proyecto de Güemes: no calza en escala
        w.compute_similarity(other, PROJ)
    assert w.similarity is None


def test_wizard_compute_similarity_with_chosen_fbx_without_analyzing(line, head_mesh, tm):  # noqa: F811
    """La similitud se calcula con el FBX elegido en la ventana, sin haberlo analizado antes (el asistente puede tener otro FBX cargado)."""
    w = RW.RolesWizard(make_ctrl(line))
    assert w.fbx_path is None or not w.tm                                    # nada analizado
    info = w.compute_similarity(STL, PROJ, FBX)
    assert info["ok"] and w.similarity is not None
    w.clear_similarity()
    w.load_textured(two_color_tm(head_mesh))                                 # otro FBX ya cargado: su `tm` no debe usarse para el FBX elegido
    w.fbx_path, w.tm = "/otro/fbx.fbx", w.tm
    assert w.compute_similarity(STL, PROJ, FBX)["ok"]


def test_roles_wizard_locale_keys_ux():
    import json
    keys = ("intro", "colores_k_ayuda", "origen", "origen_pieza", "origen_pieza_ayuda", "origen_figura", "origen_figura_ayuda", "elegir_similitud_ayuda",
            "lista_vacia", "vista_vacia", "sim_falta", "sim_falta_fbx", "sim_archivo", "sim_calculada")
    for lg in ("es", "en", "pt"):
        d = json.load(open(os.path.join(ROOT, "src", "afm", "locales", f"{lg}.json"), encoding="utf-8"))
        assert all(k in d["roles_wizard"] for k in keys), lg
        for sec in ("common", "catalog_window"):                             # los botones de menú del catálogo no llevan la flecha ▾ en el texto
            assert not [k for k, v in d[sec].items() if isinstance(v, str) and "▾" in v], (lg, sec)
