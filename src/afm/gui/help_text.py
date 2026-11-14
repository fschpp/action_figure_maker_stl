"""Ayuda de los paneles en dos niveles (UX criterio: ningún bloque fijo pasa de ~200 caracteres).

`split_help` corta un texto largo en una parte corta (oraciones completas hasta el límite) y el resto, que la ventana
muestra con «Más ▾». No usa GTK para poder probarse sin pantalla.
"""
from __future__ import annotations

import re

LIMIT = 200
_SENT = re.compile(r"(?<=[.;:?!])\s+(?=[A-ZÁÉÍÓÚÑ«¿¡(])")


def split_help(text: str, limit: int = LIMIT) -> tuple[str, str]:
    """(corto, resto). Si el texto entra en `limit`, el resto es ''. Corta en límites de oración; si la primera ya pasa
    del límite, corta en el último espacio y agrega «…» (el resto empieza donde se cortó)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text, ""
    short = ""
    parts = _SENT.split(text)
    for part in parts:
        cand = (short + " " + part).strip()
        if len(cand) > limit:
            break
        short = cand
    if short:
        return short, text[len(short):].strip()
    cut = text.rfind(" ", 0, limit - 1)
    cut = cut if cut > 0 else limit - 1
    return text[:cut].rstrip(" ,;:") + "…", text[cut:].strip()
