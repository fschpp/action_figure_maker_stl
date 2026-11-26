"""Qué tipo de articulación y qué medidas le toca a cada miembro y a cada articulación (plan de plugins §3.6).

Precedencia, de más a menos específica:
  tipo:    `LimbSpec.mechanism` / `JointSpec.type`  →  `Params.joints[lugar]["type"]`  →  lo de siempre (clic si `shoulder_detent`/`hip_detent`, si no hongo; bisagra)
  medidas: `LimbSpec.mechanism_params` / `JointSpec.type_params`  →  `Params.joints[lugar]["params"]` (solo si es del mismo tipo)  →  las de `Params`
Sin nada de eso en el proyecto, todo da lo mismo que antes.
"""
from __future__ import annotations

import dataclasses

SLOT_OF_KIND = {"arm": "hombro", "leg": "cadera", "knee": "rodilla", "elbow": "codo"}


def limb_slot(spec) -> str:
    return SLOT_OF_KIND.get(spec.kind, spec.kind)


def joint_slot(joint) -> str:
    return SLOT_OF_KIND.get(joint.kind, joint.kind)


def profile_entry(p, slot: str) -> dict | None:
    e = (getattr(p, "joints", None) or {}).get(slot)
    return e if isinstance(e, dict) and e.get("type") else None


def replace_params(obj, overrides: dict, who: str):
    """`dataclasses.replace(obj, **overrides)` con un error claro si una clave no es un campo."""
    names = {f.name for f in dataclasses.fields(obj)}
    bad = sorted(set(overrides) - names)
    if bad:
        raise ValueError(f"«{who}»: parámetro desconocido {', '.join(map(repr, bad))} para {type(obj).__name__} (válidos: {', '.join(sorted(names))})")
    return dataclasses.replace(obj, **overrides)


def _check_type(type_id: str, slot: str, who: str) -> str:
    from . import REGISTRY
    t = REGISTRY.get(type_id)
    if t is None or slot not in t.slots:
        valid = " | ".join(i for i, x in REGISTRY.items() if slot in x.slots)      # en orden de registro: mushroom | detent
        raise ValueError(f"«{who}»: mecanismo desconocido {type_id!r} ({valid})")
    return type_id


def limb_type_id(spec, p) -> str:
    """Id del tipo de articulación del miembro con el torso (hombro/cadera)."""
    slot = limb_slot(spec)
    mech = spec.mechanism
    if mech is None and (e := profile_entry(p, slot)):
        mech = e["type"]
    if mech is None:
        mech = "detent" if (p.hip_detent if spec.kind == "leg" else p.shoulder_detent) else "mushroom"
    return _check_type(mech, slot, spec.name)


def limb_overrides(spec, p, type_id: str) -> dict:
    out: dict = {}
    e = profile_entry(p, limb_slot(spec))
    if e and e["type"] == type_id:
        out.update(e.get("params") or {})
    out.update(getattr(spec, "mechanism_params", None) or {})
    return out


def joint_type_id(joint, p) -> str:
    """Id del tipo de la articulación (rodilla/codo): `JointSpec.type` si no es el de siempre; si es `hinge`, el del perfil de línea."""
    t = joint.type
    if t == "hinge" and (e := profile_entry(p, joint_slot(joint))):
        t = e["type"]
    return _check_type(t, joint_slot(joint), joint.name)


def joint_overrides(joint, p) -> dict:
    out: dict = {}
    e = profile_entry(p, joint_slot(joint))
    if e and e["type"] == joint_type_id(joint, p):
        out.update(e.get("params") or {})
    out.update(getattr(joint, "type_params", None) or {})
    return out
