import pytest


@pytest.fixture(autouse=True)
def _gui_config_aislada(tmp_path_factory, monkeypatch):
    """Que ningún test lea ni escriba las preferencias reales de la GUI (`~/.config/afm/gui.json`)."""
    monkeypatch.setenv("AFM_GUI_CONFIG", str(tmp_path_factory.mktemp("cfg") / "gui.json"))
