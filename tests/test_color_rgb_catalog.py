"""Modo `rgb` (el FBX entero) con entrada de interfaz: `afm catalogo color --rgb`, `RolesWizard.save_piece_rgb` y el chequeo del frente en una cabeza
(`Catalog.add_piece(color_fbx=, similarity=, front_confirmed=)`). Sin GTK (la ventana no se prueba acá)."""
import os
import shutil

import numpy as np
import pytest

from afm import cli, colorpiece, parts as P
from afm.catalog import Catalog, CatalogError, CatalogFrontError
from afm.frame import FigureFrame
from afm.gui import roles_wizard as RW

from test_color_catalog_head import HEAD_ID, ROOT, head_fbx, line  # noqa: F401  (fixtures)
from test_gui_roles_wizard import make_ctrl

REF = f"{HEAD_ID}@1"


@pytest.fixture()
def root(tmp_path):
    d = str(tmp_path / "linea")
    shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    return d


def _head_dir(root):
    return sorted(os.listdir(os.path.join(root, "piezas", "cabeza")))


def test_cli_rgb_saves_the_whole_fbx_without_palette(root, head_fbx):  # noqa: F811
    fbx, _meta, _mesh = head_fbx
    assert Catalog.open(root).palette() is None                              # rgb no pide paleta
    assert cli.main(["catalogo", "color", root, REF, "--fbx", fbx, "--rgb"]) == 0
    new = Catalog.open(root).meta(f"{HEAD_ID}@2")
    assert new.color["fuente"] == f"{HEAD_ID}@2.fbx" and new.color.get("modo", "rgb") == "rgb" and "roles" not in new.color
    assert os.path.isfile(Catalog.open(root).color_file(f"{HEAD_ID}@2"))
    assert Catalog.open(root).meta(REF).color is None                         # la versión anterior no cambió


def test_cli_rgb_does_not_mix_with_roles_options_and_a_mode_is_required(root, head_fbx, tmp_path):  # noqa: F811
    fbx, _meta, _mesh = head_fbx
    before = _head_dir(root)
    asg = tmp_path / "a.json"
    asg.write_text("{}")
    assert cli.main(["catalogo", "color", root, REF, "--fbx", fbx, "--rgb", "--roles", str(asg)]) == 2
    assert cli.main(["catalogo", "color", root, REF, "--fbx", fbx, "--rgb", "--sombreado"]) == 2
    assert cli.main(["catalogo", "color", root, REF, "--fbx", fbx, "--rgb", "--k", "5"]) == 2
    assert cli.main(["catalogo", "color", root, REF, "--fbx", fbx]) == 2         # ni --roles ni --rgb
    assert _head_dir(root) == before


def test_similarity_skips_the_alignment_and_is_recorded(root, head_fbx, monkeypatch):  # noqa: F811
    fbx, meta, mesh = head_fbx
    A = np.diag([1.0, 1.0, 1.0, 1.0])
    A[:3, 3] = (1.0, 2.0, 3.0)
    monkeypatch.setattr(colorpiece, "align_fbx_to_piece", lambda *a, **k: pytest.fail("no se alinea con similitud dada"))
    cat = Catalog.open(root)
    new, _ = cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_fbx=fbx, similarity=A.tolist())
    assert np.allclose(new.color["similitud"], A) and new.color["alineacion"] == {"origen": "figura"}
    with pytest.raises(CatalogError, match="4×4"):
        cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_fbx=fbx, similarity=np.zeros((4, 4)).tolist())


def _fake_align(monkeypatch, report):
    """`align_fbx_to_piece` que calcula la pose real una vez y le pone el informe de frente pedido (levanta `PieceFrontError` si la cara está mal)."""
    real = colorpiece.align_fbx_to_piece
    state = {}

    def fake(tm, piece, **kw):
        assert kw.get("front") is not None                                   # en una cabeza siempre se pasa la pista del frente
        if "frame" not in state:
            state["frame"] = real(tm, piece, **{**kw, "front": None})
        f = state["frame"]
        fr = FigureFrame(np.array(f.matrix), f.scale, {**f.report, **report})
        if colorpiece.front_problem(fr.report) and kw.get("require_ok", True):
            raise colorpiece.PieceFrontError("la cara quedó mirando hacia atrás", fr)
        return fr

    monkeypatch.setattr(colorpiece, "align_fbx_to_piece", fake)


BACK = {"ok": False, "frente_ok": False, "ambigua": False, "frente": {"angulo_deg": 160.0}}
FORWARD = {"ok": True, "frente_ok": True, "ambigua": False, "frente": {"angulo_deg": 4.0}}


def test_head_with_the_face_backwards_is_not_saved_without_confirmation(root, head_fbx, monkeypatch):  # noqa: F811
    fbx, meta, mesh = head_fbx
    _fake_align(monkeypatch, BACK)
    cat = Catalog.open(root)
    before = _head_dir(root)
    with pytest.raises(CatalogFrontError, match="atrás"):
        cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_fbx=fbx)
    assert _head_dir(root) == before                                          # no quedó nada escrito
    new, _ = cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_fbx=fbx, front_confirmed=True)
    assert new.color["alineacion"]["frente_confirmado"] is True


def test_head_with_the_face_forward_saves_and_notes_the_front(root, head_fbx, monkeypatch):  # noqa: F811
    fbx, meta, mesh = head_fbx
    _fake_align(monkeypatch, FORWARD)
    new, _ = Catalog.open(root).add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_fbx=fbx)
    assert new.color["alineacion"]["frente_ok"] is True and "frente_confirmado" not in new.color["alineacion"]


def test_wizard_save_piece_rgb(root, head_fbx, monkeypatch):  # noqa: F811
    fbx, _meta, _mesh = head_fbx
    w = RW.RolesWizard(make_ctrl(root))
    assert not w.loaded                                                       # sin analizar ni asignar roles
    with pytest.raises(RW.RolesWizardError):
        w.save_piece_rgb(REF, "")
    with pytest.raises(RW.RolesWizardError):
        w.save_piece_rgb(REF, os.path.join(root, "no_existe.fbx"))
    _fake_align(monkeypatch, BACK)
    before = _head_dir(root)
    with pytest.raises(RW.RolesWizardFrontError):
        w.save_piece_rgb(REF, fbx)
    assert _head_dir(root) == before
    new = w.save_piece_rgb(REF, fbx, confirm_front=True)
    assert new.ref == f"{HEAD_ID}@2" and new.color["alineacion"]["frente_confirmado"] is True
