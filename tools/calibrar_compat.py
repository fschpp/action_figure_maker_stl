"""Calibración de la compatibilidad barata contra la completa (plan de compatibilidad, Fase 0).

La barata (`ports.port_compat`) sale de los metadatos de las piezas; la completa (`compat.check`) arma la figura y corre
`validate`. Esta herramienta las contrasta: toma las figuras que ya están en un catálogo y arma recetas de «una pieza
cambiada» (el brazo o la pierna de otra figura puesto en el torso de la figura anfitriona), y para cada una anota el nivel
barato (ok / aviso / no, con su IoU y escalón de las caras), el choque brazo–pierna en la pose sentada que anticipa
`guide.SeatedClash` (si choca, el nivel pasa a «no») y si la completa encontró problemas NUEVOS respecto de la receta propia del
anfitrión (la línea base: lo que ya falla sin cambiar nada no se le achaca a la pieza cambiada).

Con eso arma la matriz de confusión por clase (nivel barato × la completa pasó o no) y los límites de Wilson que usa
`guide.expected_coverage` para decir «esperada ≈ 88 % (n = 31)». Cada combinación arma una figura entera: minutos en las
figuras reales. `compat.json` guarda los resultados (la clave cambia si cambia una pieza, los parámetros o el motor), así que
volver a correrlo solo calcula lo que falta.

Uso:
    python tools/calibrar_compat.py RUTA_CATALOGO [--anfitriones a,b] [--donantes a,b] [--lugares brazo,pierna]
                                   [--max N] [--forzar] [--escribir] [--out docs/CALIBRACION_compat.md]

`--escribir` deja `<catálogo>/calibracion.json` (formato `afm-calibracion-1`), que la guía lee sola.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from afm import __version__, compat  # noqa: E402
from afm.catalog import Catalog, CatalogError  # noqa: E402
from afm.guide import CALIB_FILE, CALIB_FORMAT, SIT_CLASH_TOL, SeatedClash, wilson_lower  # noqa: E402
from afm.recipe import Recipe, RecipeError  # noqa: E402

CLASS_OF_PLACE = {"brazo": "hombro", "pierna": "cadera", "cabeza": "cuello"}     # clase del puerto que cambia al cambiar la pieza
SWAPPABLE = ("brazo", "pierna", "cabeza")
LEVELS = ("ok", "aviso", "no")


def _c(x, nd=2) -> str:
    return "—" if x is None else f"{x:.{nd}f}".replace(".", ",")


# ------------------------------------------------------------------------------------------------ figuras y recetas
def figures_of(cat: Catalog) -> dict:
    """{figura: {tipo: PieceMeta}} con la última versión de cada tipo; solo figuras con torso, brazo y pierna."""
    by_fig: dict[str, dict] = collections.defaultdict(dict)
    for m in cat.list():
        fig = m.origen.get("figura") or ""
        if fig and m.tipo in ("cabeza", "torso", "brazo", "pierna", "mano"):
            by_fig[fig][m.tipo] = m
    return {f: d for f, d in by_fig.items() if {"torso", "brazo", "pierna"} <= set(d)}


def fig_name(fig: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", os.path.splitext(fig)[0].lower()).strip("-")[:24] or "fig"


def pick(wanted, figs: dict) -> list:
    """Figuras pedidas por nombre completo, por el nombre corto (`belgrano`) o por su comienzo; sin pedido, todas."""
    if not wanted:
        return sorted(figs)
    out = []
    for f in sorted(figs):
        if any(w in (f, fig_name(f)) or f.startswith(w) or fig_name(f).startswith(w) for w in wanted):
            out.append(f)
    return out


def base_recipe(cat: Catalog, fig: str, pieces: dict) -> Recipe:
    lugares = {k: (pieces[k].ref if k in pieces else None) for k in ("cabeza", "torso", "brazo", "pierna", "mano")}
    lugares["pelvis"] = None
    return Recipe(f"cal-{fig_name(fig)}", cat.name, lugares)


def swap_recipe(cat: Catalog, host: str, hp: dict, donor: str, dp: dict, place: str) -> Recipe:
    r = base_recipe(cat, host, hp)
    r.personaje = f"cal-{fig_name(host)}-{place}-{fig_name(donor)}"
    r.lugares[place] = dp[place].ref
    if place == "brazo":                           # la mano depende del brazo: sin puerto «muneca» la mano va pegada al brazo
        if "muneca" not in dp["brazo"].puertos:
            r.lugares["mano"] = None
        elif r.lugares.get("mano") is None and "mano" in dp:
            r.lugares["mano"] = dp["mano"].ref
    return r


def run_check(recipe: Recipe, cat: Catalog, force: bool) -> dict:
    """`compat.check` con los errores de armado convertidos en resultado (una figura que no se arma no pasa)."""
    t0 = time.time()
    try:
        res = compat.check(recipe, cat.root, force=force)
        res["armado"] = True
    except (RecipeError, CatalogError, ValueError, RuntimeError) as exc:
        res = {"armado": False, "ok": False, "problemas": [f"no se pudo armar: {exc}"], "avisos": [], "compat_piezas": {},
               "cache": False}
    res.setdefault("segundos", round(time.time() - t0, 1))
    return res


# ------------------------------------------------------------------------------------------------ calibración
def calibrate(cat: Catalog, hosts=None, donors=None, places=("brazo", "pierna"), limit=None, force=False, log=print):
    figs = figures_of(cat)
    hosts = pick(hosts, figs)
    donors = pick(donors, figs)
    jobs = []
    for h in hosts:
        for pl in places:
            for d in donors:
                if d != h and pl in figs[d]:
                    jobs.append((h, d, pl))
    if limit:
        jobs = jobs[:limit]
    base: dict[str, dict] = {}
    rows = []
    sc = SeatedClash(cat)
    for i, (h, d, pl) in enumerate(jobs, 1):
        if h not in base:
            log(f"línea base de {h} …")
            base[h] = run_check(base_recipe(cat, h, figs[h]), cat, force)
        res = run_check(swap_recipe(cat, h, figs[h], d, figs[d], pl), cat, force)
        new = [p for p in res.get("problemas", []) if p not in base[h].get("problemas", [])]
        cheap = (res.get("compat_piezas") or {}).get(pl) or {}
        choque = predicted_clash(sc, figs[h], figs[d], pl)
        nivel = "no" if (choque or 0.0) > SIT_CLASH_TOL else cheap.get("nivel")
        row = {"anfitrion": h, "donante": d, "lugar": pl, "clase": CLASS_OF_PLACE[pl], "pieza": figs[d][pl].ref,
               "nivel": nivel, "nivel_cara": cheap.get("nivel"), "choque_mm3": choque,
               "iou": cheap.get("iou"), "escalon_mm": cheap.get("escalon_mm"),
               "cap_profundidad_mm": (cheap.get("margenes_mm") or {}).get("capacidad_profundidad"),      # sobra (+) o falta (−)
               "cap_cabezas_mm": (cheap.get("margenes_mm") or {}).get("capacidad_cabezas"),
               "sin_capacidad": bool(cheap.get("sin_capacidad")),
               "armado": res.get("armado", True), "pasa": bool(res.get("armado", True)) and not new, "problemas_nuevos": new,
               "avisos": [a for a in res.get("avisos", []) if a.startswith(pl)], "segundos": res.get("segundos"),
               "cache": bool(res.get("cache"))}
        rows.append(row)
        log(f"[{i}/{len(jobs)}] {h} ← {d} ({pl}): barata {row['nivel']} (IoU {_c(row['iou'])}, choque {_c(choque, 1)} mm³) → "
            f"{'pasa' if row['pasa'] else 'FALLA'}{'' if row['pasa'] else ': ' + '; '.join(new)[:120]} "
            f"[{'caché' if row['cache'] else str(row['segundos']) + ' s'}]")
    return {"filas": rows, "base": base, "figuras": sorted(figs)}


def predicted_clash(sc: SeatedClash, host: dict, donor: dict, place: str):
    """Choque en la pose sentada (mm³) del miembro del donante en el torso del anfitrión, contra el otro miembro del
    anfitrión; None si no aplica (la cabeza) o falta algo."""
    if place not in ("brazo", "pierna"):
        return None
    limb = sc.limb_of(donor[place])
    mate = sc.limb_of(host["pierna" if place == "brazo" else "brazo"])
    if limb is None or mate is None:
        return None
    return sc.limb_on_torso(limb, host["torso"], mate)


def confusion(rows: list) -> dict:
    """{clase: {nivel: [pasaron, total]}} (solo niveles con datos)."""
    out: dict[str, dict] = {}
    for r in rows:
        if r["nivel"] not in LEVELS:
            continue
        km = out.setdefault(r["clase"], {}).setdefault(r["nivel"], [0, 0])
        km[0] += int(r["pasa"])
        km[1] += 1
    return out


def calibration_json(result: dict, cat: Catalog) -> dict:
    return {"formato": CALIB_FORMAT, "linea": cat.name, "motor": __version__,
            "fecha": datetime.date.today().isoformat(), "figuras": result["figuras"],
            "n_combinaciones": len(result["filas"]), "clases": confusion(result["filas"]),
            "detalle": [{k: r.get(k) for k in ("anfitrion", "donante", "lugar", "clase", "nivel", "nivel_cara", "choque_mm3", "iou",
                                               "escalon_mm", "cap_profundidad_mm", "cap_cabezas_mm", "sin_capacidad", "pasa")}
                        for r in result["filas"]]}


def _capacity_cell(r: dict) -> str:
    """Margen de la capacidad del anfitrión (+ sobra, − falta): profundidad y, en la cadera, cabezas. «sin dato» si la toma no la trae."""
    parts = [f"{lab} {v:+.1f}" for lab, v in (("prof.", r.get("cap_profundidad_mm")), ("cabezas", r.get("cap_cabezas_mm"))) if v is not None]
    return " · ".join(parts) or ("sin dato" if r.get("sin_capacidad") else "—")


def _theme(msg: str) -> str:
    """Mensaje de `validate` sin números, para agrupar problemas parecidos."""
    return re.sub(r"\d+([.,]\d+)?", "N", msg)[:110]


def report_markdown(result: dict, cat: Catalog) -> str:
    rows, base = result["filas"], result["base"]
    L = ["# Calibración de la compatibilidad barata contra la completa", "",
         f"_{datetime.date.today().isoformat()} · motor {__version__} · línea «{cat.name}» ({cat.height_mm:g} mm) · "
         f"{len(rows)} combinaciones de una pieza cambiada, generado por `tools/calibrar_compat.py`._", "",
         "Cada fila pone el brazo, la pierna o la cabeza de una figura (**donante**) en el torso de otra (**anfitrión**) y compara "
         "el nivel que da la compatibilidad barata (`port_compat`, por metadatos) con el resultado de armar la figura entera y "
         "validarla. «Pasa» = la completa no encontró problemas **nuevos** respecto de la receta propia del anfitrión.", ""]
    L += ["## Línea base (cada figura con sus propias piezas)", "", "| Anfitrión | Armado | Problemas | Avisos |", "|---|---|---|---|"]
    for h, b in sorted(base.items()):
        L.append(f"| {h} | {'sí' if b.get('armado', True) else 'no'} | {len(b.get('problemas', []))} | {len(b.get('avisos', []))} |")
    L += ["", "## Combinaciones", "",
          "| Anfitrión | Lugar | Donante | Barata | IoU | Escalón (mm) | Choque sentada (mm³) | Capacidad (mm) | Completa | Problemas nuevos |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['anfitrion']} | {r['lugar']} | {r['donante']} | {r['nivel'] or '—'} | {_c(r['iou'])} | {_c(r['escalon_mm'])} | "
                 f"{_c(r.get('choque_mm3'), 1)} | {_capacity_cell(r)} | {'pasa' if r['pasa'] else '**falla**'} | {'; '.join(r['problemas_nuevos'])[:90] or '—'} |")
    conf = confusion(rows)
    L += ["", "## Matriz de confusión por clase", "",
          "Pasaron / total de las combinaciones con ese nivel barato, y el límite inferior de Wilson al 95 % (lo que usa la guía).", "",
          "| Clase | Nivel barato | Pasaron | Total | Wilson inf. |", "|---|---|---|---|---|"]
    for clase in sorted(conf):
        for nivel in LEVELS:
            if nivel in conf[clase]:
                k, n = conf[clase][nivel]
                L.append(f"| {clase} | {nivel} | {k} | {n} | {100 * wilson_lower(k, n):.0f} % |")
    themes = collections.Counter(_theme(p) for r in rows for p in r["problemas_nuevos"])
    if themes:
        L += ["", "## Problemas nuevos más frecuentes", "", "| Veces | Mensaje (números reemplazados por N) |", "|---|---|"]
        L += [f"| {n} | {t} |" for t, n in themes.most_common(12)]
    ok_rows = [r for r in rows if r["nivel"] == "ok"]
    no_rows = [r for r in rows if r["nivel"] == "no"]
    L += ["", "## Lectura", ""]
    if ok_rows:
        bad = sum(1 for r in ok_rows if not r["pasa"])
        L.append(f"- De {len(ok_rows)} combinaciones «ok» por la barata, {bad} fallaron en la completa ({100 * bad / len(ok_rows):.0f} %).")
    else:
        L.append("- No hay combinaciones «ok» por la barata en esta muestra: no se puede medir cuánto vale un «ok».")
    if no_rows:
        good = sum(1 for r in no_rows if r["pasa"])
        L.append(f"- De {len(no_rows)} combinaciones «no», {good} pasaron la completa ({100 * good / len(no_rows):.0f} %).")
    av = [r for r in rows if r["nivel"] == "aviso"]
    if av:
        good = sum(1 for r in av if r["pasa"])
        L.append(f"- De {len(av)} combinaciones «aviso», {good} pasaron la completa ({100 * good / len(av):.0f} %).")
    judged = [r for r in rows if r.get("choque_mm3") is not None]
    if judged:
        hit = sum(1 for r in judged if (r["choque_mm3"] > SIT_CLASH_TOL) == (not r["pasa"]))
        fp = sum(1 for r in judged if r["choque_mm3"] > SIT_CLASH_TOL and r["pasa"])
        fn = sum(1 for r in judged if r["choque_mm3"] <= SIT_CLASH_TOL and not r["pasa"])
        L.append(f"- El choque brazo–pierna en la pose sentada (`guide.SeatedClash`, sin armar la figura) anticipó {hit} de {len(judged)} "
                 f"resultados de la completa ({fp} falsas alarmas, {fn} fallas que no vio).")
    L += ["- La capacidad del anfitrión (§3 y §4.4 del plan) se mide desde la Fase 3: la columna «Capacidad» da el margen (+ sobra, − falta) y «sin dato» marca las tomas catalogadas antes (`afm catalogo capacidad --escribir`). Falta contrastarla con la completa sobre las figuras reales.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("catalogo", help="directorio del catálogo (con las piezas de varias figuras)")
    ap.add_argument("--anfitriones", default=None, help="figuras anfitrionas (por defecto todas), separadas por comas")
    ap.add_argument("--donantes", default=None, help="figuras donantes (por defecto todas)")
    ap.add_argument("--lugares", default="brazo,pierna", help=f"qué pieza se cambia: {', '.join(SWAPPABLE)}")
    ap.add_argument("--max", type=int, default=None, help="como mucho N combinaciones")
    ap.add_argument("--forzar", action="store_true", help="volver a armar aunque compat.json ya tenga el resultado")
    ap.add_argument("--escribir", action="store_true", help=f"dejar <catálogo>/{CALIB_FILE} (la guía lo lee sola)")
    ap.add_argument("--out", default=None, metavar="ARCHIVO", help="informe en markdown (p. ej. docs/CALIBRACION_compat.md)")
    a = ap.parse_args(argv)
    cat = Catalog.open(a.catalogo)
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()] if s else None      # noqa: E731
    places = tuple(split(a.lugares) or ())
    bad = [p for p in places if p not in SWAPPABLE]
    if bad:
        ap.error(f"lugar(es) no soportado(s): {', '.join(bad)} (hay {', '.join(SWAPPABLE)})")
    result = calibrate(cat, split(a.anfitriones), split(a.donantes), places, a.max, a.forzar, log=lambda m: print(m, flush=True))
    if not result["filas"]:
        print("No hay combinaciones: hacen falta al menos dos figuras con torso, brazo y pierna en el catálogo.")
        return 1
    for clase, tabla in sorted(confusion(result["filas"]).items()):
        print(f"{clase}: " + ", ".join(f"{n} {k}/{t}" for n, (k, t) in tabla.items()))
    if a.escribir:
        path = os.path.join(cat.root, CALIB_FILE)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(calibration_json(result, cat), fh, ensure_ascii=False, indent=2)
        print(f"Escrito {path}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(report_markdown(result, cat))
        print(f"Escrito {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
