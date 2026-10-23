"""Preferencias de la GUI que viven fuera del proyecto: dónde está el catálogo de cada línea y cuál fue el último.

El `.proyecto` solo guarda `id`, versión y NOMBRE de la línea de cada pieza (así se puede mover de computadora); la
carpeta del catálogo es de cada máquina. Acá se recuerda `línea -> carpeta` para reabrirlo solo al abrir un proyecto con
piezas de catálogo, y la última carpeta usada para reabrirla al arrancar.

Archivo: `$AFM_GUI_CONFIG`, o `$XDG_CONFIG_HOME/afm/gui.json`, o `~/.config/afm/gui.json`. Si no se puede leer o escribir,
no pasa nada: es una comodidad, nunca un requisito.
"""
from __future__ import annotations

import json
import os
import tempfile

FORMAT = "afm-gui-1"


def config_path() -> str:
    env = os.environ.get("AFM_GUI_CONFIG")
    if env:
        return env
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "afm", "gui.json")


def load() -> dict:
    try:
        with open(config_path(), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, json.JSONDecodeError):
        d = {}
    if not isinstance(d, dict) or d.get("formato") != FORMAT:
        d = {"formato": FORMAT}
    if not isinstance(d.get("catalogos"), dict):
        d["catalogos"] = {}
    return d


def save(d: dict) -> bool:
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def remember_catalog(line: str, root: str) -> bool:
    """Recuerda que el catálogo de la línea `line` está en `root` y que fue el último usado."""
    d = load()
    root = os.path.abspath(root)
    d["catalogos"][line] = root
    d["ultimo_catalogo"] = root
    return save(d)


def catalog_for_line(line: str | None) -> str | None:
    """Carpeta recordada del catálogo de la línea `line` (solo si todavía existe)."""
    if not line:
        return None
    root = load()["catalogos"].get(line)
    return root if root and os.path.isfile(os.path.join(root, "linea.json")) else None


def last_catalog() -> str | None:
    """Última carpeta de catálogo usada (solo si todavía existe)."""
    root = load().get("ultimo_catalogo")
    return root if root and os.path.isfile(os.path.join(root, "linea.json")) else None


def forget_catalog(line: str | None = None) -> bool:
    """Olvida la carpeta de `line` (o todo lo recordado si es None)."""
    d = load()
    if line is None:
        d["catalogos"], d["ultimo_catalogo"] = {}, None
    else:
        root = d["catalogos"].pop(line, None)
        if root is not None and d.get("ultimo_catalogo") == root:
            d["ultimo_catalogo"] = None
    return save(d)
