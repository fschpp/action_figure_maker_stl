"""Compatibilidad completa y trazabilidad del catálogo (plan de piezas intercambiables, §4.4 y Fase 7).

La compatibilidad **barata** (`ports.port_compat`) sale de los metadatos de las piezas. La **completa** es `build` +
`validate` de una receta: tarda minutos, así que su resultado se guarda en `<catálogo>/compat.json` con la clave

    sha256( piezas elegidas con su hash + Params de la línea + opciones de la receta + ajustes + versión del motor )

Cualquier cambio en una pieza (nueva versión, otro hash), en los parámetros o en el motor da otra clave: no hace falta
invalidar nada a mano. La completa se hace solo sobre las figuras que se imprimen (las recetas), no sobre todas las
combinaciones; `matrix_markdown` arma el informe legible con la matriz barata de todo el catálogo y las completas ya
calculadas.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import tempfile
import time

from . import __version__
from .catalog import Catalog, CatalogError
from .recipe import Recipe, RecipeError

COMPAT_FORMAT = "afm-compat-1"
COMPAT_FILE = "compat.json"
LEVELS = ("ok", "aviso", "no")


# ---------------------------------------------------------------------------------------------------- clave
def recipe_key(recipe: Recipe, cat: Catalog) -> tuple[str, dict]:
    """(clave, piezas) de una receta. `piezas` = lugar -> {ref `id@n`, sha256}: las versiones sin número se resuelven a
    la última, así que la clave cambia cuando aparece una versión nueva."""
    from .recipe import recipe_params

    piezas = {}
    for lugar, ref in sorted(recipe.lugares.items()):
        if not ref:
            continue
        m = cat.meta(ref)
        piezas[lugar] = {"ref": m.ref, "sha256": m.sha256}
    payload = {"piezas": piezas, "params": recipe_params(recipe, cat).to_dict(), "opciones": recipe.options(),
               "ajustes": recipe.ajustes, "motor": __version__}
    blob = json.dumps(payload, sort_keys=True, default=float, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest(), piezas


# ---------------------------------------------------------------------------------------------------- caché
class CompatCache:
    """`compat.json` de un catálogo. Se lee y se escribe entero (es chico); la escritura es atómica."""

    def __init__(self, root: str):
        self.path = os.path.join(root, COMPAT_FILE)

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {"formato": COMPAT_FORMAT, "entradas": {}}
        if data.get("formato") != COMPAT_FORMAT or not isinstance(data.get("entradas"), dict):
            return {"formato": COMPAT_FORMAT, "entradas": {}}
        return data

    def get(self, key: str) -> dict | None:
        return self.load()["entradas"].get(key)

    def put(self, key: str, entry: dict) -> None:
        data = self.load()
        data["entradas"][key] = entry
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2, default=float)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def prune(self, cat: Catalog) -> int:
        """Quita las entradas cuyas piezas ya no existen (o cambiaron de hash). Devuelve cuántas quitó."""
        data = self.load()
        keep = {}
        for k, e in data["entradas"].items():
            try:
                if all(cat.meta(p["ref"]).sha256 == p["sha256"] for p in e.get("piezas", {}).values()):
                    keep[k] = e
            except CatalogError:
                pass
        removed = len(data["entradas"]) - len(keep)
        if removed:
            data["entradas"] = keep
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2, default=float)
        return removed


# ---------------------------------------------------------------------------------------------------- evaluación
def entry_from_validation(recipe: Recipe, key: str, piezas: dict, val: dict, seconds: float | None = None) -> dict:
    """Entrada de `compat.json` a partir del resultado de `pipeline.validate` (la misma que usa `afm armar`)."""
    gaps = [v for v in (val.get("gaps") or {}).values() if isinstance(v, (int, float))]
    niveles = [r.get("nivel") for r in (val.get("piezas") or {}).values() if r.get("nivel")]
    peor = max(niveles, key=LEVELS.index) if niveles else None
    return {"formato": COMPAT_FORMAT, "personaje": recipe.personaje, "linea": recipe.linea, "piezas": piezas,
            "ok": bool(val.get("ok")), "problemas": list(val.get("problems") or []), "avisos": list(val.get("avisos") or []),
            "avisos_pasador": list(val.get("avisos_pasador") or []), "compat_piezas": val.get("piezas") or {},
            "nivel": peor, "holgura_min_mm": min(gaps) if gaps else None, "motor": __version__,
            "fecha": datetime.datetime.now().isoformat(timespec="seconds"),
            "segundos": None if seconds is None else round(seconds, 1)}


def record(asm, val: dict, seconds: float | None = None) -> dict | None:
    """Guarda en el caché el resultado de una figura recién armada y validada (`recipe.Assembled` + `validate`).
    Es lo que usa `afm armar`: no cuesta nada porque `build` y `validate` ya se hicieron. None si no hay catálogo."""
    cat = getattr(asm, "catalog", None)
    if cat is None:
        return None
    key, piezas = recipe_key(asm.recipe, cat)
    entry = entry_from_validation(asm.recipe, key, piezas, val, seconds)
    CompatCache(cat.root).put(key, entry)
    return entry


def check(recipe: Recipe, catalog_root: str | None = None, force: bool = False, progress=None) -> dict:
    """Compatibilidad completa de una receta: la devuelve del caché si la clave no cambió, si no arma (`assemble`),
    valida y guarda. El resultado lleva `cache` (True si salió del caché) y `clave`."""
    from . import pipeline as P
    from .recipe import assemble, load_choices

    cat, _ = load_choices(recipe, catalog_root)
    key, piezas = recipe_key(recipe, cat)
    cache = CompatCache(cat.root)
    hit = None if force else cache.get(key)
    if hit is not None:
        return {**hit, "clave": key, "cache": True}
    t0 = time.time()
    asm = assemble(recipe, cat.root, progress=progress)
    val = P.validate(asm.res, asm.params, asm.limbs, joints=asm.joints)
    entry = entry_from_validation(recipe, key, piezas, val, time.time() - t0)
    cache.put(key, entry)
    return {**entry, "clave": key, "cache": False}


def check_all(cat: Catalog, force: bool = False, progress=None) -> list[dict]:
    """`check` de cada receta de `<catálogo>/figuras/*.receta.json` (y de `recetas/`)."""
    import glob

    out = []
    for path in sorted(glob.glob(os.path.join(cat.root, "figuras", "*.receta.json"))
                       + glob.glob(os.path.join(cat.root, "recetas", "*.receta.json"))):
        try:
            res = check(Recipe.load(path), cat.root, force, progress)
        except (RecipeError, CatalogError) as exc:
            res = {"personaje": os.path.basename(path), "ok": False, "problemas": [str(exc)], "avisos": [], "cache": False,
                   "piezas": {}, "nivel": None}
        res["receta"] = path
        out.append(res)
    return out


# ---------------------------------------------------------------------------------------------------- informe
def cheap_matrix(cat: Catalog) -> dict:
    """Compatibilidad barata de todo el catálogo agrupada por tipo de acople: (tipoA, puertoA, tipoB, puertoB) ->
    {(refA, refB): Compat}. Cada par aparece una vez (A < B por tipo, luego por referencia)."""
    metas = cat.list()
    groups: dict = {}
    for a in metas:
        for other, na, nb, c in cat.compatible_with(a.ref):
            b = other
            if (a.tipo, a.ref) > (b.tipo, b.ref):
                continue                                  # el par simétrico lo cuenta `b`
            groups.setdefault((a.tipo, na, b.tipo, nb), {})[(a.ref, b.ref)] = c
    return groups


def _cell(c) -> str:
    if c is None:
        return "—"
    det = f"{c.iou:.2f}/{c.escalon_mm:.1f}" if c.iou is not None else "s/c"
    return f"{c.nivel} ({det})"


def matrix_markdown(cat: Catalog, results: list[dict] | None = None) -> str:
    """Informe `COMPATIBILIDAD.md` (generado): matriz barata por acople y compatibilidad completa de las figuras."""
    lines = [f"# Compatibilidad de la línea «{cat.name}»", "",
             f"_Generado por afm {__version__}. No editar a mano: `afm catalogo matriz`._", "",
             "## Matriz barata (metadatos de los puertos)", "",
             "Cada celda: nivel (IoU de las caras / escalón en mm). Los pares `no` no se listan.", ""]
    groups = cheap_matrix(cat)
    if not groups:
        lines.append("(no hay pares de piezas acoplables)")
    for (ta, na, tb, nb), pairs in sorted(groups.items()):
        rows = sorted({p[0] for p in pairs})
        cols = sorted({p[1] for p in pairs})
        lines += [f"### {ta}:{na} ↔ {tb}:{nb}", "", "| | " + " | ".join(f"`{c}`" for c in cols) + " |",
                  "|---|" + "---|" * len(cols)]
        for r in rows:
            lines.append(f"| `{r}` | " + " | ".join(_cell(pairs.get((r, c))) for c in cols) + " |")
        lines.append("")
    lines += ["## Compatibilidad completa (`build` + `validate`)", ""]
    if results is None:
        # el caché guarda una entrada por combinación (clave): si una receta cambió, quedan las viejas. El informe
        # muestra solo la más reciente de cada figura.
        latest: dict = {}
        for e in CompatCache(cat.root).load()["entradas"].values():
            k = (e.get("personaje"), e.get("linea"))
            if k not in latest or str(e.get("fecha") or "") >= str(latest[k].get("fecha") or ""):
                latest[k] = e
        results = list(latest.values())
    if not results:
        lines.append("(todavía no se validó ninguna figura: `afm catalogo compat RUTA receta.json`)")
    else:
        lines += ["| Figura | Resultado | Peor acople | Holgura mín. (mm) | Piezas | Fecha |", "|---|---|---|---|---|---|"]
        for e in sorted(results, key=lambda e: str(e.get("personaje"))):
            piezas = ", ".join(f"{k}=`{v['ref']}`" for k, v in sorted((e.get("piezas") or {}).items()))
            hm = e.get("holgura_min_mm")
            lines.append(f"| {e.get('personaje')} | {'ok' if e.get('ok') else 'CON PROBLEMAS'} | {e.get('nivel') or '—'} | "
                         f"{'—' if hm is None else f'{hm:.2f}'} | {piezas or '—'} | {str(e.get('fecha') or '—')[:10]} |")
        notes = [(e.get("personaje"), kind, t) for e in results for kind, key in (("problema", "problemas"), ("aviso", "avisos"))
                 for t in e.get(key, [])]
        if notes:
            lines += ["", "### Problemas y avisos", *[f"- **{p}** ({k}): {t}" for p, k, t in notes]]
    return "\n".join(lines) + "\n"


def write_matrix(cat: Catalog, path: str | None = None, results: list[dict] | None = None) -> str:
    path = path or os.path.join(cat.root, "COMPATIBILIDAD.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(matrix_markdown(cat, results))
    return path
