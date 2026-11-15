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
PY
