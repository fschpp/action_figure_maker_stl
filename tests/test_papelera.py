"""Papelera del sistema (freedesktop): la carpeta va a Trash/files con su .trashinfo y nunca se borra definitivamente."""
import os

import pytest

from afm import papelera as P


def test_mueve_carpeta_y_escribe_la_ficha(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    src = tmp_path / "mi catalogo"
    (src / "piezas").mkdir(parents=True)
    (src / "piezas" / "a.stl").write_text("x")
    dest = P.move_to_trash(str(src))
    assert not src.exists() and os.path.isfile(os.path.join(dest, "piezas", "a.stl"))
    info = (tmp_path / "share" / "Trash" / "info" / "mi catalogo.trashinfo").read_text()
    assert info.startswith("[Trash Info]\n") and "Path=" + str(src).replace(" ", "%20") in info and "DeletionDate=" in info


def test_nombre_repetido_no_pisa_lo_que_ya_hay(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    destinos = []
    for n in range(3):
        d = tmp_path / f"v{n}" / "cat"
        d.mkdir(parents=True)
        (d / "n.txt").write_text(str(n))
        destinos.append(P.move_to_trash(str(d)))
    assert len(set(destinos)) == 3
    assert sorted((open(os.path.join(d, "n.txt")).read() for d in destinos)) == ["0", "1", "2"]


def test_si_no_existe_levanta_error_sin_tocar_nada(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    with pytest.raises(P.PapeleraError):
        P.move_to_trash(str(tmp_path / "no-esta"))


def test_si_falla_el_movimiento_queda_el_original_y_sin_ficha(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    src = tmp_path / "cat"
    src.mkdir()
    (src / "f").write_text("x")

    def boom(*a, **k):
        raise OSError("disco lleno")
    monkeypatch.setattr(P.os, "rename", boom)
    monkeypatch.setattr(P.shutil, "move", boom)
    with pytest.raises(P.PapeleraError):
        P.move_to_trash(str(src))
    assert (src / "f").is_file()
    assert os.listdir(tmp_path / "share" / "Trash" / "info") == []
