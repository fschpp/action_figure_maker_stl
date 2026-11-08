#!/usr/bin/env python3
"""Prueba de punta a punta «dejar uno afuera» (plan de compatibilidad, Fase 7, criterio 6 de §6).

Para cada figura real del catálogo: se la trata como nueva. Se copia el catálogo, se le sacan las piezas de esa figura, se
calibra el contrato con las demás (`interface.propose`), se ajusta el corte de la figura al contrato (`guide.fit_cut`), se
vuelve a extraer y catalogar, y se arman (`compat.check`: build + validate) las combinaciones cruzadas de a una pieza
(brazo y pierna de la figura en las otras y al revés) que caen en el **mismo talle**. Informe: cuántas pasan sin problemas nuevos.

    python tools/dejar_uno_afuera.py CATALOGO --figuras DIR [--metrica equivalente|inscrito] [--tolerancia MM]
                                     [--solo nombre,…] [--out docs/DEJAR_UNO_AFUERA.md]

`DIR` trae, por figura, `<figura>.stl` y `<figura>.proyecto` (el nombre del STL es el que figura en `origen.figura` de las
piezas del catálogo). No toca el catálogo original: trabaja en copias temporales.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import calibrar_compat as CC  # noqa: E402
from afm import guide as G  # noqa: E402
from afm import interface as I  # noqa: E402
from afm.catalog import Catalog, CatalogError  # noqa: E402
from afm.extract import ExtractError, extract_all, load_body  # noqa: E402
from afm.project import Project  # noqa: E402

PASS_GOAL = 0.90


def talle_of(contract, meta, clase: str):
    """Talle de la pieza en el contrato (el del puerto de esa clase), o None si cae fuera o la clase no tiene talles."""
    for lab in I.label_piece(contract, meta, only_plugs=False).values():
        if lab.clase == clase:
            return lab.talle
    return None


def leave_one_out(src_catalog: str, figure: str, stl: str, project_path: str, metrica: str, tol: float, workdir: str, log=print) -> dict:
    """Una vuelta: `figure` (nombre del STL en `origen.figura`) como figura nueva. Devuelve un dict con el detalle."""
    root = os.path.join(workdir, os.path.splitext(figure)[0])
    shutil.copytree(src_catalog, root, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md", CC.CALIB_FILE))
    cat = Catalog.open(root)
    mine = [m.ref for m in cat.list() if (m.origen or {}).get("figura") == figure]
    out = {"figura": figure, "quitadas": mine, "errores": []}
    if not mine:
        out["errores"].append(f"el catálogo no tiene piezas con origen.figura = «{figure}»")
        return out
    cat.delete_pieces(mine)
    cat = Catalog.open(root)
    try:
        prop = I.propose(cat, tol, metrica)
        cat.set_contract(prop.contract)
    except (I.ContractError, CatalogError, ValueError) as exc:
        out["errores"].append(f"no se pudo calibrar el contrato sin «{figure}»: {exc}")
        return out
    contract = prop.contract
    out["contrato"] = I.format_contract(contract) if hasattr(I, "format_contract") else ""
    project = Project.load(project_path)
    body, _rep = load_body(stl, project)
    fits = G.fit_cut(project, body, contract)
    out["ajuste"] = [l for r in fits for l in G.format_fit_cut(r)]
    out["ajustados"] = G.apply_fit(project, fits)
    try:
        res = extract_all(body, project, CC.fig_name(figure), None, None, False, figure=figure, project_name=os.path.basename(project_path))
    except ExtractError as exc:
        out["errores"].append(f"no se pudo extraer la figura: {exc}")
        return out
    for e in res.pieces:
        cat.add_piece(e.mesh, e.meta, True)
    cat = Catalog.open(root)
    figs = CC.figures_of(cat)
    key = next((f for f in figs if f == figure), None)
    if key is None:
        out["errores"].append("la figura nueva no quedó completa (hacen falta torso, brazo y pierna)")
        return out
    others = [f for f in figs if f != key]
    rows = []
    rows += CC.calibrate(cat, hosts=others, donors=[key], log=log)["filas"]       # la pieza nueva en los demás torsos
    rows += CC.calibrate(cat, hosts=[key], donors=others, log=log)["filas"]       # las piezas de los demás en el torso nuevo
    same = []
    for r in rows:
        host_t, donor_t = figs[r["anfitrion"]]["torso"], figs[r["donante"]][r["lugar"]]
        a = talle_of(contract, donor_t, r["clase"])
        b = talle_of(contract, host_t, r["clase"])
        r["talle_donante"], r["talle_anfitrion"] = a, b
        if a is not None and a == b:
            same.append(r)
    out["filas"] = rows
    out["mismo_talle"] = same
    out["pasan"] = sum(1 for r in same if r["pasa"])
    return out


def report(results: list, metrica: str) -> str:
    L = [f"# Prueba «dejar uno afuera» (métrica {metrica})", ""]
    tot = ok = 0
    for r in results:
        L.append(f"## {r['figura']}")
        for e in r["errores"]:
            L.append(f"- **Error:** {e}")
        for l in r.get("ajuste", []):
            L.append(f"- {l}")
        if "mismo_talle" in r:
            n, k = len(r["mismo_talle"]), r["pasan"]
            tot, ok = tot + n, ok + k
            L.append(f"- Combinaciones cruzadas: {len(r['filas'])}; del mismo talle: {n}; pasan: {k}" + (f" ({100 * k / n:.0f} %)" if n else ""))
            for x in r["mismo_talle"]:
                if not x["pasa"]:
                    L.append(f"  - FALLA {x['anfitrion']} ← {x['donante']} ({x['lugar']}, talle {x['talle_donante']}): {'; '.join(x['problemas_nuevos'])[:160]}")
        L.append("")
    rate = ok / tot if tot else 0.0
    L += ["## Resultado", "", f"{ok} de {tot} combinaciones del mismo talle pasan ({100 * rate:.0f} %). Meta del plan: ≥ {100 * PASS_GOAL:.0f} %: "
          + ("**cumple**." if tot and rate >= PASS_GOAL else "**no cumple**." if tot else "sin combinaciones del mismo talle para medir."), ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("catalogo")
    ap.add_argument("--figuras", required=True, metavar="DIR", help="carpeta con <figura>.stl y <figura>.proyecto")
    ap.add_argument("--metrica", default=I.DEFAULT_METRIC, choices=I.METRICS)
    ap.add_argument("--tolerancia", type=float, default=I.DEFAULT_RADIUS_TOL_MM)
    ap.add_argument("--solo", default=None, help="solo estas figuras (nombre sin extensión, separadas por comas)")
    ap.add_argument("--out", default=None, metavar="ARCHIVO")
    args = ap.parse_args(argv)
    want = {s.strip() for s in args.solo.split(",")} if args.solo else None
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for fname in sorted(os.listdir(args.figuras)):
            base, ext = os.path.splitext(fname)
            if ext.lower() != ".stl" or (want and base not in want):
                continue
            proj = os.path.join(args.figuras, base + ".proyecto")
            if not os.path.exists(proj):
                print(f"AVISO: {fname} no tiene {base}.proyecto; se salta")
                continue
            print(f"== {fname} como figura nueva ==")
            results.append(leave_one_out(args.catalogo, fname, os.path.join(args.figuras, fname), proj, args.metrica, args.tolerancia, tmp))
    text = report(results, args.metrica)
    print("\n" + text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
