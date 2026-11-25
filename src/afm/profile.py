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


PROBE_FORMAT = "afm-probetas-1"


def load_probe_manifest(path: str) -> dict:
    """Lee el `probetas.json` que escribe `probes.probe_files` (o la carpeta que lo contiene)."""
    if os.path.isdir(path):
        path = os.path.join(path, "probetas.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("formato") != PROBE_FORMAT or not isinstance(data.get("celdas"), list):
        raise ValueError(f"{path}: no es un probetas.json de afm (formato {PROBE_FORMAT})")
    return data


def probe_cell(manifest: dict, n: int, archivo: str | None = None) -> dict:
    """La celda número `n` (la que lleva ese número en relieve). Con varias placas (clic: una por holgura de punta) hay que
    decir de cuál (`archivo`)."""
    found = [c for c in manifest["celdas"] if c.get("n") == n and (archivo is None or c.get("archivo") == archivo)]
    if not found:
        raise ValueError(f"la tirada de probetas no tiene la celda {n}" + (f" en {archivo}" if archivo else ""))
    if len(found) > 1:
        raise ValueError(f"la celda {n} está en {len(found)} placas ({', '.join(c.get('archivo', '?') for c in found)}): "
                         "indicá el archivo")
    return found[0]


def probe_cell_summary(manifest: dict, cell: dict) -> str:
    """Los valores de la celda en una línea (lo que se guarda en el perfil)."""
    tipo = manifest.get("tipo")
    if tipo == "hongo":
        return f"holgura {cell['holgura']:g} mm, vástago Ø{cell['vastago']:g} mm ({manifest.get('pasador', 'hombro')})"
    if tipo == "bisagra":
        return (f"holgura radial {cell['holgura']:g} mm, axial {cell['holgura_axial']:g} mm, "
                f"bulón Ø{cell['bulon']:g} mm")
    if tipo == "clic":
        d = cell["detent"]
        return (f"muesca {d['notch_depth']:g} mm, lengüeta {d['finger_t']:g} mm, "
                f"holgura de punta {d['detent_gap']:g} mm")
    return f"holgura {cell.get('holgura', '?')} mm"


def apply_probe_cell(params: Params, manifest: dict, n: int, archivo: str | None = None) -> tuple[Params, str]:
    """Pasa a los parámetros la celda `n` de una tirada de probetas: devuelve (parámetros nuevos, resumen en una línea).

    · hongo: holgura (la general de la figura) y Ø del vástago del pasador de la tirada (cadera u hombro);
    · bisagra: holgura radial (la general), holgura axial y Ø del bulón;
    · clic: la `DetentSpec` completa de la celda.
    La montura de armas no tiene parámetro global (la holgura se pasa en cada `MountSpec`)."""
    from .params import DetentSpec

    cell = probe_cell(manifest, n, archivo)
    tipo = manifest.get("tipo")
    if tipo == "hongo":
        which = "hip" if manifest.get("pasador") == "cadera" else "shoulder"
        out = apply_probe_result(params, float(cell["holgura"]), float(cell["vastago"]), which)
    elif tipo == "bisagra":
        out = replace(params, clearance=float(cell["holgura"]), axial_clearance=float(cell["holgura_axial"]))
        out.hinge_pin = replace(params.hinge_pin, pin_d=float(cell["bulon"]))
    elif tipo == "clic":
        out = replace(params, detent=DetentSpec(**cell["detent"]))
    else:
        raise ValueError("la probeta de montura no guarda nada en el perfil: pasá la holgura elegida como `holgura` de la montura")
    return out, probe_cell_summary(manifest, cell)


def detent_profile(nozzle: float = 0.4) -> Params:
    """Perfil de línea con el mecanismo de clic en hombros y caderas (plan de clic §3.5): la lengüeta cambia con la
    boquilla (0,4: 1,2 × 10 mm; 0,8: 1,6 × 12 mm), así que hay un perfil por boquilla («clic_0.4», «clic_0.8»)."""
    from .detent import spec_for_nozzle
    p = default_for_nozzle(nozzle)
    p.detent = spec_for_nozzle(nozzle)
    p.shoulder_detent = True
    p.hip_detent = True
    return p


def save_detent_profiles(directory: str | None = None) -> list[str]:
    """Guarda los perfiles «clic_0.4» y «clic_0.8» (los 2 de la fase 1 del plan de clic)."""
    return [save_profile(f"clic_{n:g}", detent_profile(n), directory) for n in (0.4, 0.8)]
