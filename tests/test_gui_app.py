import importlib.util

import pytest

from afm.gui import app


def _has_gtk() -> bool:
    if importlib.util.find_spec("gi") is None:
        return False
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        return True
    except (ImportError, ValueError):
        return False


@pytest.mark.skipif(_has_gtk(), reason="hay GTK4: la ventana sí se puede abrir")
def test_entry_point_explains_missing_gtk(capsys):
    assert app.main([]) == 1
    err = capsys.readouterr().err
    assert "GTK4" in err and "gir1.2-gtk-4.0" in err


def test_window_module_compiles():
    import py_compile
    from pathlib import Path
    py_compile.compile(str(Path(app.__file__).with_name("window.py")), doraise=True)
