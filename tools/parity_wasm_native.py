"""G2 del port a Pyodide: compara el ZIP que generó `afm demo` bajo Pyodide con el mismo `afm demo` nativo.

Uso: python tools/parity_wasm_native.py wasm.zip
Compara, por cada STL: volumen (tolerancia relativa 0,5 %) y caja envolvente (0,3 mm, criterio del plan §8).
Sale con 1 si algo difiere o falta alguna pieza.
"""
import io
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import trimesh

VOL_TOL, BOX_TOL = 0.005, 0.3


def load(path_or_bytes):
    f = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
    return trimesh.load(f, file_type="stl")


def main(zip_path: str) -> int:
    z = zipfile.ZipFile(zip_path)
    wasm = {n: z.read(n) for n in z.namelist() if n.lower().endswith(".stl")}
    with tempfile.TemporaryDirectory() as d:
        subprocess.run([sys.executable, "-m", "afm.cli", "demo", "--out", d], check=True, capture_output=True)
        native = {str(p.relative_to(d)): p.read_bytes() for p in Path(d).rglob("*.stl")}
    print(f"nativo: {len(native)} STL · wasm: {len(wasm)} STL")
    bad = 0
    for name in sorted(set(native) | set(wasm)):
        if name not in native or name not in wasm:
            print(f"✗ {name}: solo en {'wasm' if name in wasm else 'nativo'}"); bad += 1; continue
        a, b = load(native[name]), load(wasm[name])
        dv = abs(a.volume - b.volume) / max(abs(a.volume), 1e-9)
        db = float(np.abs(a.bounds - b.bounds).max())
        ok = dv <= VOL_TOL and db <= BOX_TOL
        print(f"{'✓' if ok else '✗'} {name}: Δvolumen {dv*100:.3f} %  Δcaja {db:.3f} mm")
        bad += not ok
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
