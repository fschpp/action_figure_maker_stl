"""R06 + R03b · Controlador del pincel (sin GTK) y `afm catalogo retoque`."""
import json
import os

import numpy as np
import pytest

from afm.catalog import Catalog
from afm.cli import main
from afm.gui.retouch_session import ERASE, PAINT, RetouchSession
from afm.retouch import MAX_RADIUS_MM, MAX_STROKES, MIN_RADIUS_MM, Retouch, RetouchError, Stroke
from test_catalog_retouch import REF, base_line, line, top_stroke  # noqa: F401  (fixtures y ayudas del módulo hermano)

PAL = ["rojo", "azul", "oro"]


def sess(**kw):
    return RetouchSession(kw.pop("retouch", None), PAL, **kw)


# ------------------------------------------------------------------ herramienta
def test_defaults_and_tool_state():
    s = sess()
    assert s.mode == PAINT and s.role == "rojo" and s.can_paint and not s.dirty and len(s) == 0
    s.set_role("oro")
    s.set_mode(ERASE)
    assert s.mode == ERASE and s.can_paint
    s.set_role("azul")                                  # elegir un rol vuelve a pintar
    assert s.mode == PAINT
    with pytest.raises(RetouchError):
        s.set_role("verde")
    with pytest.raises(RetouchError):
        s.set_mode("rociar")


def test_radius_is_clamped_and_nan_rejected():
    s = sess()
    assert s.set_radius(1e6) == MAX_RADIUS_MM and s.set_radius(0) == MIN_RADIUS_MM and s.set_radius(2.5) == 2.5
    with pytest.raises(RetouchError):
        s.set_radius(float("nan"))


def test_empty_palette_cannot_paint_but_can_erase():
    s = RetouchSession(None, [])
    assert s.role is None and not s.can_paint
    with pytest.raises(RetouchError):
        s.begin_stroke((0, 0, 0))
    s.set_mode(ERASE)
    assert s.begin_stroke((0, 0, 0)) == 1


# ------------------------------------------------------------------ trazos
def test_stroke_spacing_is_half_the_radius_and_fills_gaps():
    s = sess(radius=2.0)                                # paso = 1 mm
    assert s.begin_stroke((0, 0, 0)) == 1
    assert s.add_point((0.4, 0, 0)) == 0                # menos de un paso: nada
    assert s.add_point((0.9, 0, 0)) == 0
    assert s.add_point((3.0, 0, 0)) == 3                # el mouse saltó 3 mm desde (0,0,0): rellena a 1, 2 y 3
    s.end_stroke()
    xs = [round(st.c[0], 6) for st in s.retouch().strokes]
    assert xs == [0.0, 1.0, 2.0, 3.0] and all(st.rol == "rojo" and st.r == 2.0 for st in s.retouch().strokes)


def test_erase_mode_writes_eraser_strokes_and_normal_is_kept():
    s = sess()
    s.set_mode(ERASE)
    s.begin_stroke((1, 2, 3), (0, 0, 2))
    s.end_stroke()
    st = s.retouch().strokes[0]
    assert st.rol is None and st.n == (0.0, 0.0, 1.0)


def test_points_need_an_open_stroke_and_begin_closes_the_previous_one():
    s = sess()
    with pytest.raises(RetouchError):
        s.add_point((0, 0, 0))
    s.begin_stroke((0, 0, 0))
    s.begin_stroke((10, 0, 0))                          # cierra el anterior: dos entradas de deshacer
    s.end_stroke()
    assert s.undo() and s.undo() and not s.undo() and len(s) == 0


def test_bad_point_is_rejected_without_touching_the_stroke():
    s = sess()
    s.begin_stroke((0, 0, 0))
    with pytest.raises(RetouchError):
        s.add_point((float("nan"), 0, 0))
    assert len(s) == 1


def test_changing_radius_mid_stroke_applies_to_new_points():
    s = sess(radius=1.0)
    s.begin_stroke((0, 0, 0))
    s.set_radius(4.0)
    s.add_point((10, 0, 0))
    s.end_stroke()
    assert [st.r for st in s.retouch().strokes][0] == 1.0 and s.retouch().strokes[-1].r == 4.0


# ------------------------------------------------------------------ deshacer / rehacer
def drag(s, x0, x1):
    s.begin_stroke((x0, 0, 0))
    s.add_point((x1, 0, 0))
    return s.end_stroke()


def test_one_stroke_is_one_undo_entry_and_redo_restores_it():
    s = sess(radius=2.0)
    n1, n2 = drag(s, 0, 4), drag(s, 20, 24)
    assert len(s) == n1 + n2 and s.can_undo and not s.can_redo
    before = s.retouch().to_dict()
    assert s.undo() and len(s) == n1 and s.can_redo
    assert s.redo() and s.retouch().to_dict() == before and not s.can_redo


def test_new_stroke_clears_redo():
    s = sess(radius=2.0)
    drag(s, 0, 4)
    s.undo()
    drag(s, 50, 54)
    assert not s.can_redo and not s.redo()


def test_undo_while_stroking_closes_it_first():
    s = sess(radius=2.0)
    s.begin_stroke((0, 0, 0))
    s.add_point((4, 0, 0))
    assert s.undo() and len(s) == 0 and not s.stroking


def test_discard_goes_back_to_saved_and_clears_stacks():
    base = Retouch([Stroke((0, 0, 0), 1.0, "azul")])
    s = sess(retouch=base)
    assert not s.dirty and not s.discard()
    drag(s, 10, 20)
    s.undo()
    assert s.dirty is False                             # deshacer todo lo nuevo = igual a lo guardado
    drag(s, 10, 20)
    assert s.dirty and s.discard() and len(s) == 1 and not s.dirty and not s.can_undo and not s.can_redo


def test_undo_never_removes_saved_strokes():
    s = sess(retouch=Retouch([Stroke((0, 0, 0), 1.0, "azul")]))
    assert not s.undo() and len(s) == 1


def test_max_strokes_truncates_the_stroke_and_flags_it():
    s = sess(retouch=Retouch([Stroke((0, 0, 0), 1.0, "azul")] * (MAX_STROKES - 2)), radius=1.0)
    s.begin_stroke((100, 0, 0))
    added = s.add_point((110, 0, 0))                    # pediría ~20 pinceladas
    s.end_stroke()
    assert len(s) == MAX_STROKES and added == 1 and s.truncated
    assert s.undo() and len(s) == MAX_STROKES - 2       # el trazo cortado se deshace entero


# ------------------------------------------------------------------ catálogo
def test_apply_writes_to_the_catalog_and_marks_clean(line):
    cat = Catalog.open(line)
    s = RetouchSession(cat.retouch(REF), cat.palette().ids(), radius=3.0)
    assert "oro" in s.palette_ids
    top = top_stroke(cat).c
    s.set_role("oro")
    s.begin_stroke(top)
    s.end_stroke()
    assert s.dirty
    s.apply(cat, REF)
    assert not s.dirty and cat.retouch(REF).roles_used() == ["oro"] and len(cat.retouch(REF)) == 1
    s.set_mode(ERASE)
    drag(s, 0, 0)                                       # un borrador más
    s.apply(cat, REF)
    assert len(cat.retouch(REF)) == 2


def test_apply_with_nothing_removes_the_retouch(line):
    cat = Catalog.open(line)
    cat.set_retouch(REF, Retouch([top_stroke(cat)]))
    s = RetouchSession(cat.retouch(REF), cat.palette().ids())
    s.undo()                                            # no hay nada para deshacer: lo guardado es intocable
    s.discard()
    assert len(s) == 1
    s2 = RetouchSession(None, cat.palette().ids())
    s2.apply(cat, REF)                                  # vacío = quitar
    assert cat.retouch(REF) is None


def test_failed_apply_keeps_session_dirty(line):
    cat = Catalog.open(line)
    s = RetouchSession(None, ["rojo", "fantasma"])      # un rol que la paleta de la línea no tiene
    s.set_role("fantasma")
    s.begin_stroke((0, 0, 0))
    s.end_stroke()
    with pytest.raises(Exception):
        s.apply(cat, REF)
    assert s.dirty and cat.retouch(REF) is None


# ------------------------------------------------------------------ R03b · CLI
def run(line, *a, capsys):
    code = main(["catalogo", "retoque", line, *a])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_cli_show_export_import_delete(line, tmp_path, capsys):
    cat = Catalog.open(line)
    code, out, _ = run(line, REF, capsys=capsys)
    assert code == 0 and "no tiene retoque" in out
    code, _, err = run(line, REF, "--exportar", str(tmp_path / "x.json"), capsys=capsys)
    assert code == 2 and "no tiene retoque" in err and not (tmp_path / "x.json").exists()

    cat.set_retouch(REF, Retouch([top_stroke(cat), Stroke((0, 0, 0), 1.0, None)]))
    code, out, _ = run(line, REF, capsys=capsys)
    assert code == 0 and "2 pincelada" in out and "oro" in out and "1 de borrador" in out
    ex = str(tmp_path / "x.json")
    code, out, _ = run(line, REF, "--exportar", ex, capsys=capsys)
    assert code == 0 and Retouch.load(ex) == cat.retouch(REF)

    code, out, _ = run(line, REF, "--borrar", capsys=capsys)
    assert code == 0 and "quitado" in out and Catalog.open(line).retouch(REF) is None
    code, out, _ = run(line, REF, "--importar", ex, capsys=capsys)
    assert code == 0 and "importado" in out and len(Catalog.open(line).retouch(REF)) == 2


def test_cli_import_rejects_bad_file_and_unknown_role(line, tmp_path, capsys):
    bad = tmp_path / "mal.json"
    bad.write_text(json.dumps({"formato": "otro"}))
    code, _, err = run(line, REF, "--importar", str(bad), capsys=capsys)
    assert code == 2 and "ERROR" in err
    ghost = Retouch([Stroke((0, 0, 0), 1.0, "fantasma")]).save(tmp_path / "f.json")
    code, _, err = run(line, REF, "--importar", ghost, capsys=capsys)
    assert code == 2 and "fantasma" in err and Catalog.open(line).retouch(REF) is None


def test_cli_options_are_mutually_exclusive(line):
    with pytest.raises(SystemExit):
        main(["catalogo", "retoque", line, REF, "--borrar", "--exportar", "x.json"])


# ------------------------------------------------------------------ rellenar región e importar
def test_fill_mode_is_one_click_one_stroke_with_the_reach():
    from afm.gui.retouch_session import DEFAULT_REACH_MM, FILL

    s = sess()
    s.set_mode(FILL)
    s.set_role("oro")
    assert s.mode == FILL and s.reach == DEFAULT_REACH_MM
    s.set_reach(3.0)
    assert s.begin_stroke((1, 2, 3), (0, 0, 1)) == 1
    assert s.add_point((9, 9, 9)) == 0                      # arrastrar no agrega nada
    assert s.end_stroke() == 1 and len(s) == 1
    st = s.retouch().strokes[0]
    assert st.relleno and st.r == 3.0 and st.rol == "oro" and st.c == (1.0, 2.0, 3.0)
    assert s.undo() and len(s) == 0 and s.redo() and len(s) == 1
    assert s.set_reach(1e6) == MAX_RADIUS_MM and s.set_reach(0) == MIN_RADIUS_MM
    with pytest.raises(RetouchError):
        s.set_reach(float("nan"))


def test_fill_mode_keeps_filling_when_the_role_changes_and_erase_goes_back_to_paint():
    from afm.gui.retouch_session import FILL

    s = sess()
    s.set_mode(FILL)
    s.set_role("azul")
    assert s.mode == FILL                                    # cambiar de rol no saca del relleno
    s.set_mode(ERASE)
    s.set_role("azul")
    assert s.mode == PAINT


def test_fill_respects_the_stroke_cap_and_reports_truncation():
    from afm.gui.retouch_session import FILL

    s = sess(retouch=Retouch([Stroke((0, 0, 0), 1, "rojo")] * MAX_STROKES))
    s.set_mode(FILL)
    assert s.begin_stroke((0, 0, 0)) == 0 and s.truncated


def test_import_strokes_is_one_undo_step_and_validates_before_changing_anything():
    s = sess()
    new = [Stroke((i, 0, 0), 1.0, "azul") for i in range(5)] + [Stroke((0, 0, 0), 4.0, "oro", relleno=True)]
    assert s.import_strokes(new) == 6 and len(s) == 6 and s.dirty
    assert s.undo() and len(s) == 0 and s.redo() and len(s) == 6
    with pytest.raises(RetouchError):                        # rol fuera de la paleta: no cambia nada
        s.import_strokes([Stroke((0, 0, 0), 1, "verde")])
    assert len(s) == 6
    big = sess(retouch=Retouch([Stroke((0, 0, 0), 1, "rojo")] * (MAX_STROKES - 2)))
    with pytest.raises(RetouchError):
        big.import_strokes([Stroke((0, 0, 0), 1, "azul")] * 3)
    assert len(big) == MAX_STROKES - 2 and big.import_strokes([]) == 0


# ------------------------------------------------------------------ CLI: --copiar-de, --espejar y estado
def test_cli_copy_from_another_piece_and_mirror(line, tmp_path, capsys):
    from test_color_catalog_palette import ARM_ID, with_roles

    cat = Catalog.open(line)
    top = top_stroke(cat)
    cat.set_retouch(REF, Retouch([Stroke(top.c, 2.0, "oro", top.n), Stroke(top.c, 5.0, "rojo", top.n, relleno=True)]))
    arm_ref = with_roles(cat, ARM_ID).ref                         # una segunda pieza con color por roles
    code, out, _ = run(line, arm_ref, "--copiar-de", REF, "--espejar", capsys=capsys)
    assert code == 0 and "copiado" in out and "espejado" in out
    got, src = cat.retouch(arm_ref), cat.retouch(REF)
    assert got == src.mirrored() and got.strokes[0].c[0] == pytest.approx(-top.c[0], abs=1e-3) and got.strokes[1].relleno
    assert cat.retouch(REF) == src                                  # el origen no se toca
    # sin --espejar es una copia exacta (p. ej. de la versión anterior a la nueva)
    assert run(line, arm_ref, "--borrar", capsys=capsys)[0] == 0 and cat.retouch(arm_ref) is None
    assert run(line, arm_ref, "--copiar-de", REF, capsys=capsys)[0] == 0 and cat.retouch(arm_ref) == src
    # exportar un retoque con rellenos escribe el formato 2, que se vuelve a importar
    path = str(tmp_path / "r.json")
    assert run(line, REF, "--exportar", path, capsys=capsys)[0] == 0 and json.load(open(path))["formato"] == "afm-retoque-2"
    assert run(line, arm_ref, "--importar", path, "--espejar", capsys=capsys)[0] == 0 and cat.retouch(arm_ref) == src.mirrored()


def test_cli_espejar_needs_importar_or_copiar_de(line, capsys):
    code, _, err = run(line, REF, "--espejar", capsys=capsys)
    assert code != 0 and "--espejar" in err


def test_cli_copiar_de_without_retouch_fails_clearly(line, capsys):
    code, _, err = run(line, REF, "--copiar-de", REF, capsys=capsys)
    assert code != 0 and "no tiene retoque" in err


def test_retouching_does_not_change_the_piece_state(line):
    """Decisión del cliente: un retoque NO invalida `validada`/`impresa`."""
    cat = Catalog.open(line)
    for state in ("validada", "impresa"):
        cat.set_state(REF, state)
        cat.set_retouch(REF, Retouch([top_stroke(cat)]))
        assert cat.meta(REF).estado == state
        cat.set_retouch(REF, None)
        assert cat.meta(REF).estado == state
