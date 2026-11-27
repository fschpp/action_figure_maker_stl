"""Tipos de articulación: el punto de extensión que decide qué se pone en un corte (plan de plugins de articulación, Fase 0).

`REGISTRY` guarda los tipos por `id`. Los internos se registran al importar el paquete; los de terceros (nivel N2, Fase 5)
llegarán más adelante y no se cargan sin consentimiento explícito.
"""
from __future__ import annotations

from .base import (API_VERSION, FaceRequirements, Finding, JointResult, JointSite, JointType, Motion, RevoluteMotion,
                   rotation_axis, rotation_matrix, sweep_angles)

REGISTRY: dict[str, JointType] = {}


def register(t: JointType) -> None:
    """Registra un tipo. Un `id` repetido o una versión de API distinta de la del motor es un error claro, no un pisado."""
    if not getattr(t, "id", None):
        raise ValueError("un tipo de articulación necesita un `id`")
    if t.id in REGISTRY:
        raise ValueError(f"el tipo de articulación «{t.id}» ya está registrado")
    if getattr(t, "api_version", None) != API_VERSION:
        raise ValueError(f"el tipo «{t.id}» usa la API {getattr(t, 'api_version', None)}; el motor habla la {API_VERSION}")
    REGISTRY[t.id] = t


def get(type_id: str) -> JointType:
    try:
        return REGISTRY[type_id]
    except KeyError:
        raise ValueError(f"tipo de articulación desconocido: «{type_id}» (registrados: {', '.join(sorted(REGISTRY)) or 'ninguno'})") from None


def ids() -> list[str]:
    return sorted(REGISTRY)


from .detent import Detent  # noqa: E402  (los internos se registran al importar el paquete)
from .hinge import Hinge  # noqa: E402
from .mushroom import Mushroom  # noqa: E402
from .offer import Offer, offer, offer_one  # noqa: E402,F401
from .swivel import Swivel  # noqa: E402

BUILTIN: frozenset[str] = frozenset({"mushroom", "detent", "hinge", "swivel"})   # los que viajan con el motor: su versión es la del motor


def type_version(t: JointType) -> int:
    """Versión del tipo (atributo opcional `version`, 1 si no lo trae). Subirla invalida la caché de compatibilidad."""
    return int(getattr(t, "version", 1) or 1)


def used_type_ids(params: Params) -> set[str]:
    """Tipos que usa una línea: los del perfil `Params.joints` y los que fija por clase de puerto."""
    from .resolve import PIN_ATTR_OF_CLASS, class_type_id
    ids = {class_type_id(c, params) for c in PIN_ATTR_OF_CLASS}
    ids |= {e["type"] for e in (params.joints or {}).values() if isinstance(e, dict) and e.get("type")}
    return {i for i in ids if i}


def cache_key_info(params: Params) -> dict:
    """`{id: {"version": n, "sha256": h}}` de los tipos de la línea que NO son internos (plugins): actualizar un plugin sin tocar
    `Params` cambia la geometría, así que entra en `compat.recipe_key`. Vacío con tipos internos: la clave de siempre no cambia."""
    import hashlib
    import inspect
    out: dict = {}
    for tid in sorted(used_type_ids(params) - BUILTIN):
        t = REGISTRY.get(tid)
        if t is None:
            out[tid] = {"version": 0}                  # usado y no instalado: la clave cambia cuando se instale
            continue
        info = {"version": type_version(t)}
        try:
            info["sha256"] = hashlib.sha256(open(inspect.getsourcefile(type(t)), "rb").read()).hexdigest()
        except (TypeError, OSError):
            pass
        out[tid] = info
    return out


for _t in (Mushroom(), Detent(), Hinge(), Swivel()):
    register(_t)

from . import resolve  # noqa: E402,F401  (qué tipo y qué medidas le tocan a cada miembro/articulación)

__all__ = ["API_VERSION", "REGISTRY", "FaceRequirements", "Finding", "JointResult", "JointSite", "JointType", "Motion",
           "RevoluteMotion", "get", "ids", "register", "rotation_axis", "rotation_matrix", "sweep_angles"]
