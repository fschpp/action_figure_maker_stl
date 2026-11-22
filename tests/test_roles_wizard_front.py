"""O09 · El asistente de roles muestra el resultado antes de guardar y no guarda una cabeza con la cara al revés sin confirmación explícita
(`docs/PLAN_orientacion_color_cabeza.md`). Lógica sin GTK (la ventana no se prueba acá, R9): `RolesWizard.check_piece` / `save_piece(confirm_front=)`,
`Catalog.add_piece(alignment=, front_confirmed=)`.

La alineación real de la cabeza de Bolívar está probada en `test_cut_piece_orientation.py`; acá se reemplaza `colorpiece.align_roles_package` por una
pose con el informe de frente que se quiere probar (cara hacia atrás, ambigua, bien) para no pagar los ≈10 s de la alineación en cada caso."""
import json
import os

import numpy as np
import pytest

from afm.catalog import Catalog, CatalogError
from afm import colorpiece
from afm.frame import FigureFrame
from afm.gui import roles_wizard as RW

from test_gui_roles_wizard import HEAD_ID, decide_by_color, head_mesh, line, make_ctrl, two_color_tm  # noqa: F401  (fixtures)

REF = f"{HEAD_ID}@1"
BACK = {"ok": False, "media_mm": 0.3, "frente_ok": False, "frente": {"fbx": [0, -1, 0], "pieza": [0, 1, 0], "angulo_deg": 163.0}, "ambigua": False}
FORWARD = {"ok": True, "media_mm": 0.05, "frente_ok": True, "frente": {"fbx": [0, 1, 0], "pieza": [0, 1, 0], "angulo_deg": 5.0}, "ambigua": False}
TURNED = {**FORWARD, "yaw180": {"media_alt_mm": 0.31, "margen_mm": 0.11, "girada": True}}      # la cara quedó bien, pero el motor tuvo que girar la pose 180° para lograrlo
AMBIGUOUS = {"ok": False, "media_mm": 0.05, "frente_ok": None, "frente": {}, "ambigua": True, "yaw180": {"margen_mm": 0.02}}


@pytest.fixture()
def wiz(line, head_mesh):  # noqa: F811
    """Asistente con el FBX sintético de la cabeza del catálogo de ejemplo, todo decidido y confirmado."""
    w = RW.RolesWizard(make_ctrl(line))
    w.load_textured(two_color_tm(head_mesh), {"nombre": "cabeza.fbx", "sha256": "ab" * 32})
    decide_by_color(w)
    w.confirm_suggestions()
    assert w.can_save
    return w


@pytest.fixture()
def fake_align(monkeypatch):
    """`align_roles_package` que calcula la pose real una vez y le pone el informe de frente que se pida. Cuenta las llamadas."""
    real = colorpiece.align_roles_package
    state = {"calls": 0, "report": FORWARD, "frame": None}

    def fake(meta, piece_stl, pkg, palette, *, require_ok=True):
        state["calls"] += 1
        if state["frame"] is None:
            state["frame"] = real(meta, piece_stl, pkg, palette)
        fr = FigureFrame(np.array(state["frame"].matrix), state["frame"].scale, {**state["frame"].report, **state["report"]})
        if colorpiece.front_problem(fr.report) and require_ok:
            raise colorpiece.PieceFrontError("la cara quedó mirando hacia atrás", fr)
        return fr

    monkeypatch.setattr(colorpiece, "align_roles_package", fake)
    return state


def versions(root):
    return Catalog.open(root).versions(HEAD_ID)


def test_check_piece_shows_where_the_face_ended_up_without_saving(wiz, line, fake_align):
    fake_align["report"] = BACK
    n = len(versions(line))
    chk = wiz.check_piece(REF)
    assert chk.frente_ok is False and chk.needs_confirmation and chk.angulo_deg == 163.0
    assert "atrás" in chk.message and "163" in chk.message
    assert np.asarray(chk.matrix).shape == (4, 4)
    assert len(versions(line)) == n and Catalog.open(line).palette() is None              # nada se escribió (ni la pieza ni la paleta)
    fake_align["report"] = FORWARD
    wiz.last_check = None
    ok = wiz.check_piece(REF)
    assert ok.frente_ok is True and not ok.needs_confirmation and "adelante" in ok.message


def test_check_piece_builds_the_piece_with_its_real_color(wiz, fake_align):
    chk = wiz.check_piece(REF, preview=True)
    assert chk.parts and all(len(p) == 3 and len(p[2]) == 4 for p in chk.parts)          # (nombre, malla, rgba)
    assert {tuple(round(c * 255) for c in p[2][:3]) for p in chk.parts}                    # al menos un color
    assert wiz.check_piece(REF, preview=True) is chk and fake_align["calls"] == 1          # misma prueba: no se vuelve a alinear


def test_a_check_goes_stale_when_decisions_change(wiz, fake_align):
    chk = wiz.check_piece(REF, preview=True)
    assert wiz.fresh_check(REF) is chk and wiz.fresh_check(f"{HEAD_ID}@9") is None
    first = next(iter(wiz.choices))
    wiz.set_none(first)
    assert wiz.fresh_check(REF) is None                                                   # la vista previa ya no vale
    again = wiz.check_piece(REF, preview=True)                                            # probar de nuevo con lo decidido ahora da otra prueba
    assert again is not chk and wiz.fresh_check(REF) is again


def test_saving_a_head_with_the_face_at_the_back_needs_explicit_confirmation(wiz, line, fake_align):
    fake_align["report"] = BACK
    n = len(versions(line))
    with pytest.raises(RW.RolesWizardError, match="No se guardó"):
        wiz.save_piece(REF)
    assert len(versions(line)) == n and Catalog.open(line).palette() is None               # ni la pieza ni la paleta
    new = wiz.save_piece(REF, confirm_front=True)
    assert new.version == n + 1 and new.color["alineacion"]["frente_confirmado"] is True
    assert new.color["alineacion"]["frente_ok"] is False                                   # queda anotado que se guardó con la cara al revés a propósito


def test_an_ambiguous_front_also_needs_confirmation(wiz, fake_align):
    fake_align["report"] = AMBIGUOUS
    chk = wiz.check_piece(REF)
    assert chk.ambigua and chk.needs_confirmation and "no se puede decidir" in chk.message.lower()
    with pytest.raises(RW.RolesWizardError):
        wiz.save_piece(REF)
    assert wiz.save_piece(REF, confirm_front=True).color["alineacion"]["frente_confirmado"] is True


def test_a_good_front_saves_without_confirmation_and_does_not_align_twice(wiz, line, fake_align):
    n = len(versions(line))
    wiz.check_piece(REF, preview=True)
    new = wiz.save_piece(REF)
    assert new.version == n + 1 and "frente_confirmado" not in new.color["alineacion"]
    assert fake_align["calls"] == 1                                                         # la alineación que se mostró es la que se guarda
    assert wiz.last_check is None                                                          # tras guardar, la prueba ya no vale


def test_confirm_front_is_ignored_when_the_front_is_fine(wiz, fake_align):
    new = wiz.save_piece(REF, confirm_front=True)
    assert "frente_confirmado" not in new.color["alineacion"]


def test_similarity_from_the_caller_measures_the_face_with_it(wiz, fake_align, monkeypatch):
    """Con una 4×4 FBX → figura no se alinea: se mide hacia dónde queda la cara con esa matriz (solo en una cabeza)."""
    class Hint:
        def angle(self, M):
            return 171.0, np.array([0.0, -1.0, 0.0])

    monkeypatch.setattr(colorpiece, "front_hint", lambda *a, **k: Hint())
    sim = np.eye(4).tolist()
    chk = wiz.check_piece(REF, sim)
    assert fake_align["calls"] == 0 and chk.frame is None
    assert chk.frente_ok is False and chk.needs_confirmation and chk.angulo_deg == 171.0
    with pytest.raises(RW.RolesWizardError, match="No se guardó"):
        wiz.save_piece(REF, sim)
    with pytest.raises(RW.RolesWizardError):                                              # matriz inválida
        wiz.check_piece(REF, [[1, 2], [3, 4]])


def test_add_piece_refuses_a_backwards_alignment_unless_confirmed(wiz, line, fake_align):
    """La compuerta también está en el catálogo (no solo en el asistente): una alineación con la cara hacia atrás no se guarda sin `front_confirmed`."""
    fake_align["report"] = BACK
    chk = wiz.check_piece(REF)
    cat, pkg = Catalog.open(line), wiz.package()
    meta, mesh = cat.load(REF)
    from afm import parts as P

    def go(**kw):
        return cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_roles=pkg, **kw)

    cat.set_palette(wiz.final_palette())                                                   # la paleta que el asistente escribe antes de guardar la pieza
    with pytest.raises(CatalogError, match="atrás"):
        go(alignment=chk.frame)
    new, _w = go(alignment=chk.frame, front_confirmed=True)
    assert new.color["alineacion"]["frente_confirmado"] is True
    chk.frame.report.update(ok=False, frente_ok=True, ambigua=False, media_mm=3.0)         # no calza: la confirmación del frente no lo arregla
    with pytest.raises(CatalogError, match="no coincide"):
        go(alignment=chk.frame, front_confirmed=True)


def test_a_fbx_that_does_not_fit_the_piece_is_an_error_not_a_confirmation(wiz, monkeypatch):
    def boom(*a, **k):
        raise colorpiece.PieceColorError("el FBX no coincide con el STL de la pieza: distancia media 9.000 mm")

    monkeypatch.setattr(colorpiece, "align_roles_package", boom)
    with pytest.raises(RW.RolesWizardError, match="no coincide con la pieza"):
        wiz.check_piece(REF)
    with pytest.raises(RW.RolesWizardError, match="similarity"):                           # el mensaje conserva la pista de la similitud (la ventana la completa)
        wiz.save_piece(REF)


def test_front_problem_helper():
    assert colorpiece.front_problem({"frente_ok": False}) and colorpiece.front_problem({"ambigua": True})
    assert not colorpiece.front_problem({"frente_ok": True}) and not colorpiece.front_problem({"frente_ok": None}) and not colorpiece.front_problem({})


def test_a_pose_the_program_had_to_turn_is_always_asked_about(wiz, line, fake_align):
    """«Siempre preguntar»: si el calce prefería la cabeza con la cara atrás y el motor la giró 180° por su cuenta, no se guarda sin confirmación, aunque la cara
    haya quedado de frente (`frente_ok`)."""
    fake_align["report"] = TURNED
    n = len(versions(line))
    chk = wiz.check_piece(REF)
    assert chk.frente_ok is True and chk.girada and chk.needs_confirmation and chk.margen_mm == pytest.approx(0.11)
    assert "giró 180" in chk.message and "0.11" in chk.message.replace(",", ".") and "5" in chk.message
    with pytest.raises(RW.RolesWizardError, match="No se guardó"):
        wiz.save_piece(REF)
    assert len(versions(line)) == n
    new = wiz.save_piece(REF, confirm_front=True)
    assert new.version == n + 1
    meta, _ = Catalog.open(line).load(f"{HEAD_ID}@{new.version}")
    assert meta.color["alineacion"].get("giro_confirmado") is True and meta.color["alineacion"]["yaw180"]["girada"] is True


def test_a_pose_that_needed_no_turn_saves_without_asking(wiz, line, fake_align):
    fake_align["report"] = {**FORWARD, "yaw180": {"media_alt_mm": 0.5, "margen_mm": 0.2, "girada": False}}
    chk = wiz.check_piece(REF)
    assert not chk.girada and not chk.needs_confirmation
    assert wiz.save_piece(REF).version == len(versions(line))
