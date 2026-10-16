"""Empaquetado (Fase 5): metadatos, puntos de entrada, instalador y manual."""
import importlib
import os
import re
import subprocess

import pytest

import afm

tomllib = pytest.importorskip("tomllib", reason="hace falta Python >= 3.11 para leer pyproject.toml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pyproject() -> dict:
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)


def test_version_matches_between_package_and_pyproject():
    assert _pyproject()["project"]["version"] == afm.__version__


def test_entry_points_resolve():
    for target in _pyproject()["project"]["scripts"].values():
        module, func = target.split(":")
        assert callable(getattr(importlib.import_module(module), func)), target


def test_gui_extra_lists_the_toolkit_and_all_covers_it():
    extras = _pyproject()["project"]["optional-dependencies"]
    assert {"PyGObject", "moderngl"} <= set(extras["gui"])
    assert set(extras["gui"]) <= set(extras["all"])


def test_cli_version(capsys):
    from afm.cli import main
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0 and f"afm {afm.__version__}" in capsys.readouterr().out


def test_installer_dry_run_changes_nothing_and_mentions_the_key_steps(tmp_path):
    script = os.path.join(ROOT, "packaging", "instalar.sh")
    assert os.access(script, os.X_OK)
    dest = tmp_path / "instalacion"
    env = dict(os.environ, HOME=str(tmp_path))
    out = subprocess.run(["bash", script, "--dry-run", str(dest)], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "--system-site-packages" in out.stdout and "[gui]" in out.stdout and "afm-gui" in out.stdout
    assert ".desktop" in out.stdout
    assert not dest.exists() and not (tmp_path / ".local").exists()          # no escribió nada


def test_installer_explains_what_is_missing_without_gtk(tmp_path):
    script = os.path.join(ROOT, "packaging", "instalar.sh")
    fake_py = tmp_path / "python3"
    fake_py.write_text("#!/bin/sh\nexit 1\n")
    fake_py.chmod(0o755)
    env = dict(os.environ, HOME=str(tmp_path), AFM_PYTHON=str(fake_py))
    out = subprocess.run(["bash", script, str(tmp_path / "x")], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 1 and "gir1.2-gtk-4.0" in out.stderr and not (tmp_path / "x").exists()


def test_docs_are_linked_and_cover_the_new_tools():
    read = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()
    manual, tutorial, readme = read("docs", "manual_corto.md"), read("docs", "tutorial_gui.md"), read("README.md")
    assert "manual_corto.md" in readme and "instalar.sh" in readme and "manual_corto.md" in tutorial
    for needle in ("Espejo en vivo", "Recortes manuales", "pose de cadera"):
        assert needle in tutorial, needle
    # todos los enlaces relativos a otros .md del manual y del tutorial existen
    for text, base in ((manual, "docs"), (tutorial, "docs")):
        for link in re.findall(r"\]\((?!http)([^)#]+\.md)", text):
            assert os.path.exists(os.path.normpath(os.path.join(ROOT, base, link))), link
