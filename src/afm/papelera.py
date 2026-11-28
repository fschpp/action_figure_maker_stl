"""Mover carpetas/archivos a la papelera del sistema (sin GTK ni dependencias nuevas).

Implementa la especificación freedesktop.org Trash (la que usan GNOME, KDE, XFCE…): el elemento va a
``$XDG_DATA_HOME/Trash/files`` (por defecto ``~/.local/share/Trash``) con su ``.trashinfo`` en ``Trash/info``, así que
el gestor de archivos lo muestra y lo puede restaurar a su lugar. Si algo falla **no** se borra nada: se levanta
``PapeleraError`` y quien llama decide (nunca se cae a un borrado definitivo en silencio).
"""
from __future__ import annotations

import os
import shutil
import time
from urllib.parse import quote


class PapeleraError(OSError):
    """No se pudo mover el elemento a la papelera del sistema (el original sigue donde estaba)."""


def _data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def trash_dir() -> str:
    """Carpeta `Trash` del usuario (la crea con `files/` e `info/` si no existe)."""
    base = os.path.join(_data_home(), "Trash")
    for sub in ("files", "info"):
        os.makedirs(os.path.join(base, sub), mode=0o700, exist_ok=True)
    return base


def _free_name(base: str, name: str) -> str:
    """Nombre libre en `files/` e `info/` (agrega `.2`, `.3`… como hacen los gestores de archivos)."""
    cand, n = name, 1
    while os.path.lexists(os.path.join(base, "files", cand)) or os.path.lexists(os.path.join(base, "info", cand + ".trashinfo")):
        n += 1
        cand = f"{name}.{n}"
    return cand


def move_to_trash(path: str) -> str:
    """Manda `path` (archivo o carpeta) a la papelera del sistema; devuelve su nueva ruta dentro de `Trash/files`."""
    real = os.path.abspath(path)
    if not os.path.lexists(real):
        raise PapeleraError(f"no existe: {real}")
    try:
        base = trash_dir()
        name = _free_name(base, os.path.basename(real.rstrip(os.sep)) or "elemento")
        dest = os.path.join(base, "files", name)
        info = os.path.join(base, "info", name + ".trashinfo")
        with open(info, "w", encoding="utf-8") as f:       # primero la ficha: si el movimiento falla se quita
            f.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (quote(real, safe="/"), time.strftime("%Y-%m-%dT%H:%M:%S")))
        try:
            try:
                os.rename(real, dest)
            except OSError:                                 # otro disco: copiar y recién entonces borrar el original
                shutil.move(real, dest)
        except BaseException:
            if os.path.lexists(dest) and os.path.lexists(real):
                shutil.rmtree(dest, ignore_errors=True) if os.path.isdir(dest) and not os.path.islink(dest) else os.remove(dest)
            if os.path.lexists(info):
                os.remove(info)
            raise
    except PapeleraError:
        raise
    except OSError as exc:
        raise PapeleraError(str(exc)) from exc
    return dest
