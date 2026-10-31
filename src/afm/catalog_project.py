"""`.proyecto` de una figura armada solo con piezas de catálogo, abrible en la ventana principal SIN el STL de origen.

«Armar y exportar» (`afm armar`, ventana «Armar desde catálogo») escribe junto a los STL un `<figura>.proyecto`: las piezas
elegidas (`Project.parts`: id, versión y línea, nunca rutas), los parámetros de la línea con las opciones de la receta, la
mano, el plano de cuello / cintura y los miembros que salen del torso, más el bloque `catalogo_solo` con la receta, la línea
y la carpeta del catálogo donde se armó. Con `source: null` y ese bloque, `Session.open_project` arma el cuerpo base desde
el catálogo (`synthesize_host`) en vez de leer un STL.
"""
from __future__ import annotations

import os

from .project import PartRef, Project

FORMAT = "afm-catalogo-solo-1"
KEY = "catalogo_solo"


def project_from_assembled(asm) -> Project:
    """`Project` equivalente a lo armado: el huésped sintetizado (`asm.host`) da miembros, cuello y cintura."""
    from .recipe import hand_spec

    recipe, cat, host = asm.recipe, asm.catalog, asm.host
    if host is None or cat is None:
        raise ValueError("lo armado no trae el huésped ni el catálogo: no se puede escribir el proyecto")
    parts = {}
    for lugar, ref in recipe.lugares.items():
        if ref:
            meta = cat.meta(ref)
            parts[lugar] = PartRef(id=meta.id, version=meta.version, linea=cat.name)
    from .accessories import AccessoryRef

    accs = []
    for ref in recipe.accesorios:
        meta = cat.meta(ref)
        accs.append(AccessoryRef(catalogo=os.path.abspath(cat.root), pieza=meta.ref, sha256=meta.sha256))
    return Project(params=asm.params, symmetry=None, limbs=list(host.limbs), head=host.head, waist=host.waist,
                   hand=hand_spec(recipe), parts=parts, accesorios=accs)


def to_dict(asm) -> dict:
    d = project_from_assembled(asm).to_dict()
    d["source"] = None
    d[KEY] = {"formato": FORMAT, "linea": asm.catalog.name, "ruta": os.path.abspath(asm.catalog.root),
              "receta": asm.recipe.to_dict()}
    return d


def save(asm, path: str) -> str:
    import json

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(to_dict(asm), fh, ensure_ascii=False, indent=2)
    return path


def origin(d: dict) -> dict | None:
    """Bloque `catalogo_solo` de un `.proyecto` ya leído (None si no es de una figura toda de catálogo)."""
    o = d.get(KEY)
    return o if isinstance(o, dict) and o.get("formato") == FORMAT else None


def peek(path: str) -> dict | None:
    """Lee solo lo necesario para saber si `path` es un proyecto de catálogo sin STL: `{linea, ruta, receta}` o None."""
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    o = origin(d) if isinstance(d, dict) else None
    return None if o is None or d.get("source") else o
