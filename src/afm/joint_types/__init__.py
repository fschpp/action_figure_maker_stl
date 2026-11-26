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

for _t in (Mushroom(), Detent(), Hinge()):
    register(_t)

__all__ = ["API_VERSION", "REGISTRY", "FaceRequirements", "Finding", "JointResult", "JointSite", "JointType", "Motion",
           "RevoluteMotion", "get", "ids", "register", "rotation_axis", "rotation_matrix", "sweep_angles"]
