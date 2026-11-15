#!/bin/sh
# Genera web/wheels/ con el wheel del motor y el manifest que lee el worker.
# Uso: scripts/build_web.sh [carpeta con wheels extra para Pyodide, p. ej. manifold3d-*-pyemscripten_2026_0_wasm32.whl]
set -eu
cd "$(dirname "$0")/.."
rm -rf web/wheels && mkdir -p web/wheels
python3 -m pip wheel -q --no-deps --no-build-isolation -w web/wheels .
extra="${1:-}"
[ -n "$extra" ] && cp "$extra"/*.whl web/wheels/ 2>/dev/null || true
python3 - <<'PY'
import glob, json, os
ws = sorted(os.path.basename(p) for p in glob.glob("web/wheels/*.whl"))
engine = next(w for w in ws if w.startswith("action_figure_maker_stl-"))
json.dump({"engine": engine, "extra": [w for w in ws if w != engine]}, open("web/wheels/manifest.json", "w"), indent=2)
print("web/wheels/manifest.json:", ws)

# Versión publicada: build-info.js (la que carga cada pestaña) y version.json (lo que consulta el botón «Buscar actualización»).
import datetime, subprocess
def git(*a):
    try: return subprocess.check_output(["git", *a], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception: return ""
commit = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD") or "dev"
build = {"commit": commit, "short": commit[:7] if commit != "dev" else "dev",
         "run": os.environ.get("GITHUB_RUN_NUMBER", ""),
         "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
open("web/build-info.js", "w").write("// Generado por scripts/build_web.sh; no se versiona.\nexport const BUILD = " + json.dumps(build) + ";\n")
# Archivos que «Actualizar ahora» vuelve a bajar: todo web/ salvo tests/, version.json y los wheels que no son el del motor
# (los otros llevan la versión en el nombre, así que no quedan viejos en la caché).
files = []
for root, _, names in os.walk("web"):
    for n in names:
        rel = os.path.relpath(os.path.join(root, n), "web").replace(os.sep, "/")
        if rel.startswith("tests/") or rel == "version.json" or (rel.startswith("wheels/") and rel.endswith(".whl") and rel != "wheels/" + engine):
            continue
        files.append(rel)
json.dump({**build, "files": sorted(files)}, open("web/version.json", "w"), indent=2)
print("web/version.json:", build["short"], build["built_at"], len(files), "archivos")
PY
