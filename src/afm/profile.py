"""Perfil de línea (plan §8.12): parámetros comunes a una línea de figuras.

Se guardan en ~/.config/afm/perfiles/<nombre>.json (o en el directorio indicado).
"""
from __future__ import annotations

import json
import os
from dataclasses import replace

from .params import Params

NOZZLE_CLEARANCE = {0.4: 0.50, 0.6: 0.60, 0.8: 0.70}   # holgura por defecto según boquilla


def profiles_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "afm", "perfiles")


def _path(name: str, directory: str | None = None) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()
    if not safe:
        raise ValueError("Nombre de perfil vacío o inválido")
    return os.path.join(directory or profiles_dir(), f"{safe}.json")


def default_for_nozzle(nozzle: float) -> Params:
    p = Params()
    p.nozzle = nozzle
    p.clearance = NOZZLE_CLEARANCE.get(round(nozzle, 2), p.clearance)
    return p


def save_profile(name: str, params: Params, directory: str | None = None) -> str:
    path = _path(name, directory)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    params.save(path)
    return path


def load_profile(name: str, directory: str | None = None) -> Params:
    return Params.load(_path(name, directory))


def list_profiles(directory: str | None = None) -> list[str]:
    d = directory or profiles_dir()
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def apply_probe_result(params: Params, clearance: float, shaft_d: float, which: str = "both") -> Params:
    """Guarda en el perfil el resultado de las probetas: holgura y Ø de vástago elegidos."""
    out = replace(params, clearance=clearance)
    if which in ("both", "shoulder"):
        out.shoulder_pin = replace(params.shoulder_pin, shaft_d=shaft_d)
    if which in ("both", "hip"):
        out.hip_pin = replace(params.hip_pin, shaft_d=shaft_d)
    return out
