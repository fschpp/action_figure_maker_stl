"""Resultados dorados (plan de piezas intercambiables, Fase 0 y P7): congelar lo que hoy produce `afm build` para poder
verificar que tocar `pipeline.py` no cambia las figuras ya resueltas.

Uso:
    python tools/golden.py guardar muneco            # genera tests/golden/muneco.json
    python tools/golden.py comparar guemes           # vuelve a construir y compara con tests/golden/guemes.json
    python tools/golden.py comparar todos [--out DIR]

Figuras: muneco (sintética, ~40 s) y guemes / belgrano / azurduy / mickey (los proyectos de `examples/`, minutos cada una).

Qué se congela por figura: volumen y componentes de cada pieza exportada, holguras mínimas (`validacion.gaps`), `validacion.ok`
y `problems`, y las CLAVES de `construccion` y `validacion` (para detectar claves que desaparecen). La comparación admite
±0,01 % en los volúmenes (criterio de aceptación 1 del plan) y ±0,005 mm en las holguras.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_DIR = os.path.join(ROOT, "tests", "golden")
EXAMPLES = os.path.join(ROOT, "examples")
FIGURES = {"muneco": None, "guemes": ("guemes.py", "guemes_segmentado_lq_fixed_resized.stl"),
           "belgrano": ("belgrano.py", "belgrano_lq_segmentado.stl"), "azurduy": ("azurduy.py", "azurduy_lq_segmentado.stl"),
           "mickey": ("mickey.py", "mickey_reescalado.stl")}
VOL_REL_TOL = 1e-4          # ±0,01 %
GAP_TOL_MM = 5e-3


def _load_example(script: str):
    spec = importlib.util.spec_from_file_location("ejemplo_" + script[:-3], os.path.join(EXAMPLES, script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.proj


def build_figure(name: str, outdir: str, progress=lambda *a: None) -> dict:
    """Construye la figura con el mismo camino que `afm build` y devuelve el `info` de `cli.run`."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from afm.cli import run
    if name == "muneco":
        from afm.project import demo_project
        from afm.testfigure import make_doll
        stl = os.path.join(outdir, "muneco.stl")
        make_doll().export(stl)
        return run(stl, demo_project(), outdir, "muneco", progress=progress)
    script, stl = FIGURES[name]
    return run(os.path.join(EXAMPLES, stl), _load_example(script), outdir, name, progress=progress)


def snapshot(info: dict) -> dict:
    """Lo que se congela de un `info` (ver docstring del módulo)."""
    val = info["validacion"]
    return {
        "ok": bool(val["ok"]), "problems": list(val["problems"]),
        "piezas": {k: {"volumen": round(float(v["volumen"]), 4), "componentes": int(v["componentes"])}
                   for k, v in sorted(val["pieces"].items())},
        "gaps": {k: round(float(v), 4) for k, v in sorted(val["gaps"].items())},
        "claves_construccion": sorted(info["construccion"].keys()),
        "claves_validacion": sorted(val.keys()),
    }


def compare(now: dict, gold: dict) -> list[str]:
    """Diferencias entre una instantánea y la dorada (lista vacía = idénticas dentro de las tolerancias)."""
    diffs = []
    if now["ok"] != gold["ok"]:
        diffs.append(f"validacion.ok: {gold['ok']} → {now['ok']}")
    if now["problems"] != gold["problems"]:
        diffs.append(f"problems: {gold['problems']} → {now['problems']}")
    for k in sorted(set(gold["piezas"]) | set(now["piezas"])):
        a, b = gold["piezas"].get(k), now["piezas"].get(k)
        if a is None or b is None:
            diffs.append(f"pieza «{k}»: {'desapareció' if b is None else 'nueva'}")
            continue
        if abs(b["volumen"] - a["volumen"]) > VOL_REL_TOL * max(abs(a["volumen"]), 1e-9):
            diffs.append(f"pieza «{k}»: volumen {a['volumen']} → {b['volumen']}")
        if b["componentes"] != a["componentes"]:
            diffs.append(f"pieza «{k}»: componentes {a['componentes']} → {b['componentes']}")
    for k in sorted(set(gold["gaps"]) | set(now["gaps"])):
        a, b = gold["gaps"].get(k), now["gaps"].get(k)
        if a is None or b is None or abs(a - b) > GAP_TOL_MM:
            diffs.append(f"holgura «{k}»: {a} → {b}")
    for key in ("claves_construccion", "claves_validacion"):
        lost = sorted(set(gold[key]) - set(now[key]))
        if lost:
            diffs.append(f"{key}: desaparecieron {lost}")
    return diffs


def golden_path(name: str) -> str:
    return os.path.join(GOLDEN_DIR, f"{name}.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("accion", choices=("guardar", "comparar"))
    ap.add_argument("figura", choices=(*FIGURES, "todos"))
    ap.add_argument("--out", default=None, help="carpeta de trabajo (por defecto una temporal)")
    args = ap.parse_args(argv)
    names = list(FIGURES) if args.figura == "todos" else [args.figura]
    bad = 0
    for name in names:
        out = os.path.join(args.out, name) if args.out else tempfile.mkdtemp(prefix=f"afm_golden_{name}_")
        os.makedirs(out, exist_ok=True)
        print(f"== {name}: construyendo en {out}", flush=True)
        snap = snapshot(build_figure(name, out))
        if args.accion == "guardar":
            os.makedirs(GOLDEN_DIR, exist_ok=True)
            with open(golden_path(name), "w") as f:
                json.dump(snap, f, indent=2, ensure_ascii=False, sort_keys=True)
            print(f"   guardado {golden_path(name)} ({len(snap['piezas'])} piezas, ok={snap['ok']})")
            continue
        with open(golden_path(name)) as f:
            diffs = compare(snap, json.load(f))
        print("   idéntica a la dorada" if not diffs else "   DIFERENCIAS:\n   " + "\n   ".join(diffs))
        bad += bool(diffs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
