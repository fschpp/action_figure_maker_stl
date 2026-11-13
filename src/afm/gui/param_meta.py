"""Metadatos de los parámetros de la pestaña «Parámetros» (UX etapa 3): grupo, tipo, etiqueta corta y ayuda.

Se deriva de `PARAM_FIELDS` (clave, etiqueta larga, mín, máx, paso, decimales) sin cambiarle la forma: la etiqueta larga
trae la explicación entre paréntesis y acá se separa en «etiqueta corta» + «ayuda» (que va al tooltip). No depende de GTK.
"""
from __future__ import annotations

import re
import unicodedata

PARAM_GROUPS = ("Generales", "Pasador de hombro", "Pasador de cadera", "Mecanismo de clic", "Rodilla / codo", "Cintura",
                "Mano articulada")
GENERATE_GROUP = "Sin soportes"          # no va en la pestaña Parámetros: su fila vive en Generar › Impresión sin soportes

BOOLEAN_KEYS = frozenset({"torso_mirror", "shoulder_detent", "hip_detent", "hinge_pin.neck", "hinge_pin.rod"})


def is_boolean(key: str) -> bool:
    return key in BOOLEAN_KEYS


def group_of(key: str) -> str:
    if key == "flat_depth":
        return GENERATE_GROUP
    if key.startswith("shoulder_pin."):
        return PARAM_GROUPS[1]
    if key.startswith("hip_pin."):
        return PARAM_GROUPS[2]
    if key.startswith("detent.") or key.endswith("_detent"):
        return PARAM_GROUPS[3]
    if key.startswith("hinge_pin."):
        return PARAM_GROUPS[4]
    if key.startswith("waist_"):
        return PARAM_GROUPS[5]
    if key.startswith("hand_pin."):
        return PARAM_GROUPS[6]
    return PARAM_GROUPS[0]


_PAREN = re.compile(r"^(?P<base>.*?) \((?P<inner>.*)\)$")
_ONOFF = re.compile(r"^1 = (?P<on>[^,]+), 0 = (?P<off>.+)$")


def split_label(label: str) -> tuple[str, str]:
    """«Cintura: largo vástago (mm; pared tras la cabeza = …)» → («Cintura: largo vástago (mm)», «pared tras la cabeza = …»).

    La unidad se queda en la etiqueta corta; lo demás entre paréntesis pasa a la ayuda. Un «(1 = sí, 0 = X)» se reescribe
    como «Encendido: sí. Apagado: X.» (para los interruptores).
    """
    m = _PAREN.match(label)
    if not m:
        return label, ""
    base, inner = m["base"], m["inner"]
    onoff = _ONOFF.match(inner)
    if onoff:
        return base, f"Encendido: {onoff['on'].strip()}. Apagado: {onoff['off'].strip()}."
    parts = [p.strip() for p in inner.split(";")]
    if parts[0] in ("mm", "°"):
        return f"{base} ({parts[0]})", "; ".join(parts[1:])
    return base, inner


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def matches(query: str, key: str, label: str) -> bool:
    """Búsqueda sin mayúsculas ni acentos, por palabras sueltas (todas tienen que aparecer) en la etiqueta, la clave y el grupo."""
    words = _fold(query).split()
    if not words:
        return True
    hay = _fold(f"{label} {key} {group_of(key)}")
    return all(w in hay for w in words)
