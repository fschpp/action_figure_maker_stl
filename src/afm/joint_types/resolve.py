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


# ---------------------------------------------------------------------------------------------------- por clase de puerto
# Nombre del campo de `Params` con el pasador de cada clase de puerto. Es un DATO guardado (`interface.ClassContract.to_dict` escribe
# `"pasador": "hip_pin"`): no se renombra. Cuello y cintura llevan hongo fijo (no intercambiable por ahora: plan §7.5).
PIN_ATTR_OF_CLASS = {"cuello": "neck_pin", "hombro": "shoulder_pin", "cadera": "hip_pin", "cintura": "waist_pin"}
SWAPPABLE_CLASSES = ("hombro", "cadera")


def class_type_id(clase: str, params) -> str | None:
    """Tipo de articulación que la LÍNEA fija para una clase de puerto (el contrato es por clase): perfil `Params.joints`, si no
    clic o hongo según `shoulder_detent`/`hip_detent`. None si la clase no lleva pasador (muñeca, rodilla, codo)."""
    if clase not in PIN_ATTR_OF_CLASS:
        return None
    if clase not in SWAPPABLE_CLASSES:
        return "mushroom"
    if e := profile_entry(params, clase):
        return _check_type(e["type"], clase, clase)
    return "detent" if (params.hip_detent if clase == "cadera" else params.shoulder_detent) else "mushroom"


def class_pin(clase: str, params):
    """Pasador (PinSpec) de la clase en esta línea, o None si no lleva: el de `Params` con encima las medidas del perfil de línea
    cuando el tipo del lugar es `mushroom` (con clic el hongo queda el de `Params`, como en `pipeline._pin_spec`). Con una rótula es el
    pasador equivalente (`ball.equivalent_pin`: vástago = cono del cuello sobre la cara, cabeza = bola) para que la guía, la capacidad y
    el contrato de interfaz midan lo que de verdad atraviesa la cara."""
    attr = PIN_ATTR_OF_CLASS.get(clase)
    if attr is None:
        return None
    base = getattr(params, attr)
    e = profile_entry(params, clase) if clase in SWAPPABLE_CLASSES else None
    if e and e["type"] == "mushroom" and e.get("params"):
        return replace_params(base, e["params"], clase)
    if e and e["type"] == "ball":
        from .ball import equivalent_pin
        return equivalent_pin(class_ball(clase, params), float(params.clearance))
    return base


def class_detent(clase: str, params):
    """Medidas del clic de la clase: `Params.detent` con encima las del perfil si el tipo del lugar es `detent`."""
    e = profile_entry(params, clase) if clase in SWAPPABLE_CLASSES else None
    if e and e["type"] == "detent" and e.get("params"):
        return replace_params(params.detent, e["params"], clase)
    return params.detent


def class_ball(clase: str, params):
    """Medidas de la rótula de la clase: las de siempre (`BallSpec()`) con encima las del perfil si el tipo del lugar es `ball`."""
    from .ball import BallSpec
    e = profile_entry(params, clase) if clase in SWAPPABLE_CLASSES else None
    if e and e["type"] == "ball" and e.get("params"):
        return replace_params(BallSpec(), e["params"], clase)
    return BallSpec()


def _class_jp(clase: str, params, type_id: str):
    if type_id == "detent":
        return class_detent(clase, params)
    if type_id == "ball":
        return class_ball(clase, params)
    return class_pin(clase, params)


def class_requirements(clase: str, params):
    """Lo que el tipo de la línea le pide al anfitrión en esa clase (`FaceRequirements`), o None si no lleva pasador."""
    tid = class_type_id(clase, params)
    if tid is None:
        return None
    from . import get
    return get(tid).requires(_class_jp(clase, params, tid), params, clase)


def class_pin_shift(clase: str, params) -> float:
    """Cuánto queda más adentro el hongo por el tipo del lugar (el clic: `disc_h`). Solo en las clases que se enfrentan a su espejo
    (la cadera), que son las que validan cabezas enfrentadas; igual que `pipeline` (`pin_shift_mm`)."""
    req = class_requirements(clase, params)
    if req is None or req.separacion_min_mm is None:
        return 0.0
    from . import get
    tid = class_type_id(clase, params)
    return float(get(tid).pin_shift(_class_jp(clase, params, tid), params, clase))


# ---------------------------------------------------------------------------------------------------- firma de un puerto
def params_hash(type_id: str, jp) -> str:
    """Huella corta (8 hex) de las medidas de acople de un tipo: los campos del `params_cls` redondeados a 0,001 mm.
    Dos piezas del mismo tipo con huellas distintas no acoplan igual (aviso en `ports.port_compat`)."""
    import hashlib
    if dataclasses.is_dataclass(jp):
        items = [(f.name, getattr(jp, f.name)) for f in dataclasses.fields(jp)]
    else:
        items = []
    txt = type_id + "|" + ";".join(f"{k}={round(v, 3) if isinstance(v, float) else v!r}" for k, v in sorted(items))
    return hashlib.sha256(txt.encode()).hexdigest()[:8]


def class_signature(clase: str, params) -> tuple[str, str] | None:
    """(id del tipo, huella de sus medidas) que la línea fija para una clase de puerto; None si la clase no lleva pasador."""
    tid = class_type_id(clase, params)
    if tid is None:
        return None
    return tid, params_hash(tid, _class_jp(clase, params, tid))
