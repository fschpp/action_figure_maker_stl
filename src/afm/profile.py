"""Perfil de línea (plan §8.12): parámetros comunes a una línea de figuras.

Se guardan en ~/.config/afm/perfiles/<nombre>.json (o en el directorio indicado).
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import asdict, replace

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
    own = app_defaults()
    if abs(own.nozzle - nozzle) < 1e-9 and app_defaults_changes():
        return own                       # valores por defecto propios (p. ej. de las probetas) para esta boquilla: mandan
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
    if tipo == "giro":
        return f"holgura {cell['holgura']:g} mm, vástago Ø{cell['vastago']:g} mm (giro)"
    if tipo == "rotula":
        return f"holgura {cell['holgura']:g} mm, bola Ø{cell['bola']:g} mm, labio {cell['labio']:g} mm (rótula)"
    if tipo == "bisagra":
        return (f"holgura radial {cell['holgura']:g} mm, axial {cell['holgura_axial']:g} mm, "
                f"bulón Ø{cell['bulon']:g} mm")
    if tipo == "clic":
        d = cell["detent"]
        return (f"muesca {d['notch_depth']:g} mm, lengüeta {d['finger_t']:g} mm, "
                f"holgura de punta {d['detent_gap']:g} mm")
    if tipo == "agarre":
        return (f"mano {cell['mano']} (taladro Ø{cell['taladro']:g} mm) + mango {cell['mango']} (dentado Ø{cell['dentado']:g} mm), "
                f"interferencia {cell['interferencia']:.2f} mm/lado, ε {cell['deformacion_pct']:.2f} %")
    return f"holgura {cell.get('holgura', '?')} mm"


def probe_pair(manifest: dict, mano: int, mango: int) -> dict:
    """La celda de la probeta de agarre para el par «mano N + mango M» (los números en relieve de cada pieza)."""
    if manifest.get("tipo") != "agarre":
        raise ValueError("--mano y --mango son de la probeta de agarre (afm probe --tipo agarre)")
    found = [c for c in manifest["celdas"] if c.get("mano") == mano and c.get("mango") == mango]
    if not found:
        raise ValueError(f"la tirada no tiene el par mano {mano} + mango {mango} "
                         f"(manos: {[m['n'] for m in manifest.get('manos', [])]}, mangos: {[m['n'] for m in manifest.get('mangos', [])]})")
    return found[0]


def grip_from_cell(manifest: dict, cell: dict, base=None):
    """`GripSpec` del par elegido: taladro de la mano, dentado del mango y el vástago, la ranura y la lengüeta con que se imprimió."""
    from dataclasses import replace as _rep

    from .props import GripSpec

    return _rep(base or GripSpec(), bore_d=float(cell["taladro"]), barb_d=float(cell["dentado"]), shaft_d=float(cell["vastago"]),
                slit_w=float(cell["ranura"]), finger_len=float(cell["largo_lengueta"]))


def grip_command(cell: dict) -> str:
    """Línea de `afm agarre` con los valores del par elegido (la mano estándar va en MANO.stl)."""
    return (f"afm agarre MANO.stl --taladro {cell['taladro']:g} --vastago {cell['vastago']:g} --ranura {cell['ranura']:g} "
            f"--largo-lengueta {cell['largo_lengueta']:g} --dentados {cell['dentado']:g}")


def probe_cell_changes(manifest: dict, n: int, archivo: str | None = None) -> dict:
    """Los valores que fija la celda `n`, como `{ruta con puntos: valor}` (p. ej. `{"clearance": 0.4, "hip_pin.shaft_d": 4.1}`).

    Es lo único que hay que propagar (perfil, línea del catálogo, valores por defecto): el resto de los parámetros de cada
    destino no se toca.
      · hongo: `clearance` y el Ø de vástago del pasador de la tirada (cadera u hombro);
      · giro (swivel): solo `clearance`: el Ø de vástago del giro todavía no tiene lugar en `Params` (`Params.joints` admite hombro, cadera,
        rodilla y codo; llega con la Fase 3), así que se anota en el resumen;
      · rótula (ball): solo `clearance`: el Ø de la bola no tiene lugar en `Params` (como el vástago del giro), así que se anota en el resumen;
      · bisagra: `clearance`, `axial_clearance` y `hinge_pin.pin_d`;
      · clic: la `DetentSpec` completa de la celda."""
    from .params import DetentSpec

    cell = probe_cell(manifest, n, archivo)
    tipo = manifest.get("tipo")
    if tipo == "hongo":
        pin = "hip_pin" if manifest.get("pasador") == "cadera" else "shoulder_pin"
        return {"clearance": float(cell["holgura"]), f"{pin}.shaft_d": float(cell["vastago"])}
    if tipo in ("giro", "rotula"):
        return {"clearance": float(cell["holgura"])}
    if tipo == "bisagra":
        return {"clearance": float(cell["holgura"]), "axial_clearance": float(cell["holgura_axial"]),
                "hinge_pin.pin_d": float(cell["bulon"])}
    if tipo == "clic":
        return {"detent": asdict(DetentSpec(**cell["detent"]))}
    if tipo == "agarre":
        raise ValueError("el agarre no se guarda en el perfil: usá los valores del par con `afm agarre` (o «Usar esta celda» en la ventana)")
    raise ValueError("la probeta de montura no guarda nada en el perfil: pasá la holgura elegida como `holgura` de la montura")


def apply_changes(params: Params, changes: dict) -> Params:
    """Copia de `params` con `changes` (`{ruta con puntos: valor}`) aplicados; no modifica el original."""
    d = copy.deepcopy(params.to_dict())
    for path, value in changes.items():
        *head, last = path.split(".")
        obj = d
        for k in head:
            obj = obj[k]
        if last not in obj:
            raise KeyError(f"parámetro desconocido: {path}")
        obj[last] = copy.deepcopy(value)
    return Params.from_dict(d)


def format_changes(changes: dict) -> list[str]:
    """Una línea por valor (`clearance = 0.4`); una `DetentSpec` se muestra campo por campo en una sola línea."""
    out = []
    for k, v in changes.items():
        if isinstance(v, dict):
            v = ", ".join(f"{a}={b:g}" if isinstance(b, (int, float)) and not isinstance(b, bool) else f"{a}={b}" for a, b in v.items())
        out.append(f"{k} = {v:g}" if isinstance(v, float) else f"{k} = {v}")
    return out


def update_profile(name: str, changes: dict, base: Params, directory: str | None = None) -> tuple[str, bool]:
    """Pone `changes` en el perfil `name`. Si el perfil ya existe se le aplican solo esos valores (conserva el resto de lo que
    tenía); si no existe se crea a partir de `base` (los parámetros de la figura abierta, que ya los traen). Devuelve (ruta, existía)."""
    path = _path(name, directory)
    existed = os.path.isfile(path)
    params = apply_changes(load_profile(name, directory), changes) if existed else base
    return save_profile(name, params, directory), existed


def apply_probe_cell(params: Params, manifest: dict, n: int, archivo: str | None = None) -> tuple[Params, str]:
    """Pasa a los parámetros la celda `n` de una tirada de probetas: devuelve (parámetros nuevos, resumen en una línea).

    Los valores que fija cada tipo están en `probe_cell_changes`. La montura de armas no tiene parámetro global (la holgura
    se pasa en cada `MountSpec`)."""
    cell = probe_cell(manifest, n, archivo)
    return apply_changes(params, probe_cell_changes(manifest, n, archivo)), probe_cell_summary(manifest, cell)


# ---------------------------------------------------------------- valores por defecto de la aplicación
DEFAULTS_FORMAT = "afm-defecto-1"


def app_defaults_path() -> str:
    """`$AFM_DEFAULTS`, o `$XDG_CONFIG_HOME/afm/defecto.json`, o `~/.config/afm/defecto.json`."""
    env = os.environ.get("AFM_DEFAULTS")
    if env:
        return env
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "afm", "defecto.json")


def app_defaults_changes() -> dict:
    """Los valores que el usuario puso como por defecto (`{ruta con puntos: valor}`); vacío si no hay o el archivo no sirve."""
    try:
        with open(app_defaults_path(), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(d, dict) or d.get("formato") != DEFAULTS_FORMAT or not isinstance(d.get("cambios"), dict):
        return {}
    return d["cambios"]


def app_defaults() -> Params:
    """Parámetros de una figura nueva: los de fábrica con los valores por defecto propios encima. Si el archivo trae algo que
    ya no existe (otra versión), se usan los de fábrica: nunca falla."""
    try:
        return apply_changes(Params(), app_defaults_changes())
    except (KeyError, TypeError, ValueError, AttributeError):
        return Params()


def save_app_defaults(changes: dict) -> str:
    """Suma `changes` a los valores por defecto propios (lo que ya había y no se menciona se conserva). Devuelve la ruta."""
    apply_changes(Params(), changes)                   # valida las rutas y los valores antes de escribir
    merged = {**app_defaults_changes(), **changes}
    path = app_defaults_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"formato": DEFAULTS_FORMAT, "cambios": merged}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def clear_app_defaults() -> bool:
    """Vuelve a los valores de fábrica. True si había algo que borrar."""
    try:
        os.remove(app_defaults_path())
    except FileNotFoundError:
        return False
    return True


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
