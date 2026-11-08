"""Certificación de piezas (plan de compatibilidad, Fase 6, §4.5).

Una pieza nueva (brazo, pierna, cabeza o pelvis) se «certifica» armándola de verdad (`compat.check`: build + validate) sobre un
**panel de anfitriones**: el torso de su propia figura y los torsos de menor, mediana y mayor capacidad del catálogo
(≤ `MAX_HOSTS` armados). El resultado se guarda en `compat.json` (clave `certificaciones`) con la **clave de certificación**:
el hash de las claves de receta de todo el panel. Esa clave ya incluye cada pieza con su hash, los `Params`, las opciones, el
motor y el hash del contrato (`compat.recipe_key`), y además el propio panel: un torso nuevo, una pieza nueva, otro
contrato o motor la cambian y la certificación vieja deja de valer sin invalidar nada a mano (`status` la ve «vencida»).

Los otros lugares de cada receta salen de la figura del anfitrión (misma `origen.figura`) o, si no hay, de la primera pieza
del catálogo de ese tipo.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import statistics

from . import compat
from .catalog import Catalog, CatalogError
from .recipe import PLACES, Recipe, RecipeError

MAX_HOSTS = 4
PLACE_OF_TIPO = {"brazo": "brazo", "pierna": "pierna", "cabeza": "cabeza", "pelvis": "pelvis"}
CLASS_OF_TIPO = {"brazo": "hombro", "pierna": "cadera", "cabeza": "cuello", "pelvis": "cintura"}
TORSO_TIPOS = ("torso", "torso_sup")           # con cintura el torso del catálogo es solo la parte de arriba
CERT_KEY = "certificaciones"


class CertifyError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------- panel
def host_depth(host, clase: str) -> float | None:
    """Menor profundidad medida (mm) de las tomas de `clase` del torso `host`, o None si no tiene el dato."""
    vals = [float(p.capacity["profundidad_mm"]) for p in host.puertos.values()
            if p.clase == clase and p.rol == "toma" and p.capacity and p.capacity.get("profundidad_mm") is not None]
    return min(vals) if vals else None


def host_panel(cat: Catalog, ref: str) -> list:
    """Torsos del panel de `ref`: el de su figura y los de menor, mediana y mayor capacidad entre los que no dan «no» en la
    compatibilidad barata. Lista de `PieceMeta` (≤ MAX_HOSTS), el propio primero."""
    me = cat.meta(ref)
    if me.tipo not in PLACE_OF_TIPO:
        raise CertifyError(f"no se certifican piezas de tipo «{me.tipo}» (hay {', '.join(PLACE_OF_TIPO)})")
    clase = CLASS_OF_TIPO[me.tipo]
    ok_ids = {o.id for o, _na, _nb, _c in cat.compatible_with(ref) if o.tipo in TORSO_TIPOS}
    hosts = sorted((h for t in TORSO_TIPOS for h in cat.list(t) if h.id in ok_ids), key=lambda h: h.id)
    own = [h for h in hosts if (h.origen or {}).get("figura") and h.origen.get("figura") == (me.origen or {}).get("figura")]
    panel = own[:1]
    with_cap = sorted((h for h in hosts if host_depth(h, clase) is not None), key=lambda h: (host_depth(h, clase), h.id))
    picks = []
    if with_cap:
        mid = statistics.median_low([host_depth(h, clase) for h in with_cap])
        picks = [with_cap[0], next(h for h in with_cap if host_depth(h, clase) >= mid), with_cap[-1]]
    for h in picks if picks else hosts:                     # sin datos de capacidad se completa por orden de id
        if h.id not in {x.id for x in panel} and len(panel) < MAX_HOSTS:
            panel.append(h)
    return panel


def has_waist(host) -> bool:
    """El torso `host` se corta en la cintura (tiene una toma de cintura): la figura lleva pelvis."""
    return any(p.clase == "cintura" and p.rol == "toma" for p in host.puertos.values())


def recipe_for(cat: Catalog, ref: str, host) -> Recipe:
    """Receta que arma la pieza `ref` sobre el torso `host`: los demás lugares de la figura del anfitrión. Sobre un torso con
    cintura la receta lleva pelvis (la de `ref` si es una pelvis; si no, la de la figura del anfitrión o la primera del catálogo)."""
    me = cat.meta(ref)
    figura = (host.origen or {}).get("figura")
    lugares = {"torso": host.ref}
    waist = has_waist(host)
    for lugar in PLACES:
        if lugar in lugares or (lugar == "pelvis" and not waist):
            continue
        cands = cat.list(lugar)
        mine = [m for m in cands if figura and (m.origen or {}).get("figura") == figura]
        pick = (mine or cands or [None])[0]
        lugares[lugar] = pick.ref if pick is not None else None
    lugares[PLACE_OF_TIPO[me.tipo]] = me.ref
    return Recipe(f"certificacion-{me.id}-{host.id}", cat.name, lugares=lugares, opciones={"cintura": True} if waist else {})


def panel_keys(cat: Catalog, ref: str) -> list[tuple]:
    """[(host, receta, clave)] del panel de `ref`."""
    out = []
    for h in host_panel(cat, ref):
        r = recipe_for(cat, ref, h)
        out.append((h, r, compat.recipe_key(r, cat)[0]))
    return out


def cert_key(keys: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(keys)).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------------- certificar y estado
def certify(cat: Catalog, ref: str, force: bool = False, progress=None) -> dict:
    """Arma `ref` en cada anfitrión del panel (con el caché de `compat.check`) y guarda la certificación en `compat.json`."""
    me = cat.meta(ref)
    items = panel_keys(cat, ref)
    if not items:
        raise CertifyError(f"«{me.ref}» no tiene ningún torso del catálogo con el que sea compatible")
    hosts = []
    for h, r, key in items:
        try:
            res = compat.check(r, cat.root, force, progress)
            hosts.append({"torso": h.ref, "ok": bool(res.get("ok")), "problemas": list(res.get("problemas") or []),
                          "avisos": list(res.get("avisos") or []), "clave": key})
        except (RecipeError, CatalogError, ValueError) as exc:
            hosts.append({"torso": h.ref, "ok": False, "problemas": [f"no se pudo armar: {exc}"], "avisos": [], "clave": key})
    entry = {"pieza": me.ref, "sha256": me.sha256, "anfitriones": hosts, "ok": all(x["ok"] for x in hosts),
             "n_ok": sum(1 for x in hosts if x["ok"]), "n": len(hosts), "clave": cert_key([x["clave"] for x in hosts]),
             "fecha": datetime.datetime.now().isoformat(timespec="seconds")}
    compat.CompatCache(cat.root).put_cert(me.id, entry)
    return entry


def status(cat: Catalog, ref: str) -> dict:
    """Estado de la certificación de `ref` sin armar nada: `{"estado": "certificada" | "parcial" | "vencida" | "sin certificar", …}`.
    «vencida»: hay una certificación guardada pero la clave actual del panel es otra (pieza, torsos, `Params`, contrato o motor)."""
    me = cat.meta(ref)
    saved = compat.CompatCache(cat.root).get_cert(me.id)
    if me.tipo not in PLACE_OF_TIPO:
        return {"estado": "no aplica", "texto": ""}
    if not saved:
        return {"estado": "sin certificar", "texto": "sin certificar"}
    try:
        now = cert_key([k for _h, _r, k in panel_keys(cat, ref)])
    except (CatalogError, RecipeError, CertifyError):
        now = None
    if now != saved.get("clave"):
        return {"estado": "vencida", "texto": "certificación vencida (cambió una pieza, el contrato, los parámetros o el motor)", "guardada": saved}
    n, k = saved["n"], saved["n_ok"]
    if k == n:
        return {"estado": "certificada", "texto": f"certificada {k}/{n}", "guardada": saved}
    bad = next(x for x in saved["anfitriones"] if not x["ok"])
    why = (bad["problemas"] or ["falla"])[0]
    return {"estado": "parcial", "texto": f"certificada {k}/{n} (falla con {bad['torso']}: {why})", "guardada": saved}
