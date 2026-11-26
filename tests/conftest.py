import pytest


@pytest.fixture(autouse=True)
def _gui_config_aislada(tmp_path_factory, monkeypatch):
    """Que ningún test lea ni escriba las preferencias reales de la GUI (`~/.config/afm/gui.json`)."""
    monkeypatch.setenv("AFM_GUI_CONFIG", str(tmp_path_factory.mktemp("cfg") / "gui.json"))


@pytest.fixture(autouse=True)
def _defectos_aislados(tmp_path_factory, monkeypatch):
    """Que ningún test lea ni escriba los valores por defecto propios reales (`~/.config/afm/defecto.json`)."""
    monkeypatch.setenv("AFM_DEFAULTS", str(tmp_path_factory.mktemp("def") / "defecto.json"))
