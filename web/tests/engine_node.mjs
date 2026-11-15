// Prueba del puente web/engine.js bajo Pyodide en Node: node web/tests/engine_node.mjs <carpeta-pyodide> <figura.stl>
// Verifica: instalación del motor con callKwargs, paso de bytes JS→Python (load_stl) y conversión del ZIP Python→JS (_pack).
// `demo`/`build` necesitan manifold3d: sin su wheel de Pyodide se espera el fallo de import (se informa, no se oculta).
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { readFileSync, readdirSync } from "node:fs";
import { createEngine } from "../engine.js";

const [dir, stlPath] = process.argv.slice(2);
const base = resolve(dir);
const { loadPyodide } = await import(pathToFileURL(resolve(base, "pyodide.mjs")).href);
const wheelsDir = resolve(import.meta.dirname, "../wheels");
const man = JSON.parse(readFileSync(resolve(wheelsDir, "manifest.json")));
const files = [...man.extra, man.engine];
// micropip dentro de Node no lee rutas del disco anfitrión: se copian al FS virtual y se instalan con emfs:
const eng = await createEngine({
  loadPyodide, indexURL: base + "/",
  pyodidePackages: ["numpy", "scipy", "shapely", "scikit-image", "networkx", "pillow", "micropip"],
  pypiPackages: ["trimesh", "python-i18n"],
  wheelUrls: files.map((f) => `emfs:/tmp/${f}`),
  prepare: (py) => files.forEach((f) => py.FS.writeFile(`/tmp/${f}`, readFileSync(resolve(wheelsDir, f)))),
  progress: (s, p) => console.log(`  [${p}%] ${s}`),
});
let fails = 0;
const check = (ok, msg) => { console.log(`${ok ? "✓" : "✗"} ${msg}`); if (!ok) fails++; };

const stl = new Uint8Array(readFileSync(resolve(stlPath)));
const r = eng.loadStl(stl);
check(r.triangulos > 100 && r.alto_mm > 90 && r.alto_mm < 110, `load_stl: ${r.triangulos} triángulos, alto ${r.alto_mm.toFixed(1)} mm, estanca=${r.estanca}`);

const packed = eng.py.runPython(`_pack({"zip": b"PK-prueba", "archivos": ["a.stl"], "informe": {"ok": True}})`);
check(packed.zip instanceof Uint8Array && packed.zip.length === 9, `_pack: zip llega como ${packed.zip?.constructor?.name} de ${packed.zip?.length} bytes`);
check(Array.isArray(packed.archivos) && packed.archivos[0] === "a.stl" && JSON.parse(packed.informe).ok === true, "_pack: archivos e informe");

try { eng.demo({ onMessage: () => {} }); check(true, "demo: terminó (hay manifold3d)"); }
catch (e) { check(/manifold3d/.test(String(e)), `demo: falla esperada sin manifold3d → ${String(e.message || e).split("\n").filter(Boolean).pop()}`); }
process.exit(fails ? 1 : 0);
