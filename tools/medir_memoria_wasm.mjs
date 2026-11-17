// Mide el tiempo y la memoria de «cargar STL» y «reducir a ~10 000 triángulos» bajo Pyodide en Node (los mismos pasos que la página).
// Uso: node --max-old-space-size=4096 tools/medir_memoria_wasm.mjs <carpeta-pyodide> <figura.stl> [objetivo=10000]
// Corre una malla por proceso (la memoria de WASM no se devuelve nunca: hay que medir cada tamaño en un proceso nuevo).
// Requiere web/wheels/ (scripts/build_web.sh). Imprime una línea JSON por etapa con el montón de WASM y el máximo RSS del proceso.
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { readFileSync } from "node:fs";
import { createEngine } from "../web/engine.js";

const [dir, stlPath, target = "10000"] = process.argv.slice(2);
const base = resolve(dir);
const { loadPyodide } = await import(pathToFileURL(resolve(base, "pyodide.mjs")).href);
const wheelsDir = resolve(import.meta.dirname, "../web/wheels");
const man = JSON.parse(readFileSync(resolve(wheelsDir, "manifest.json")));
const files = [...man.extra, man.engine];
const eng = await createEngine({
  loadPyodide, indexURL: base + "/",
  pyodidePackages: ["numpy", "scipy", "shapely", "scikit-image", "networkx", "pillow", "micropip"],
  pypiPackages: ["trimesh", "python-i18n"],
  wheelUrls: files.map((f) => `emfs:/tmp/${f}`),
  prepare: (py) => files.forEach((f) => py.FS.writeFile(`/tmp/${f}`, readFileSync(resolve(wheelsDir, f)))),
  progress: () => {},
});
const mb = (b) => Math.round(b / 1048576);
const estado = (etapa, extra = {}) => console.log(JSON.stringify({
  etapa, ...extra, wasm_heap_mb: mb(eng.py._module.HEAP8.length), rss_max_mb: mb(process.resourceUsage().maxRSS * 1024),
}));
estado("pyodide listo");
const bytes = new Uint8Array(readFileSync(resolve(stlPath)));
let t = performance.now();
try {
  const r = eng.loadStl(bytes);
  estado("load_stl", { archivo_mb: mb(bytes.length), triangulos: r.triangulos, s: +((performance.now() - t) / 1000).toFixed(1) });
  t = performance.now();
  const d = eng.decimateStl(Number(target));
  estado("decimate_stl", { de: d.info.triangulos_antes, a: d.info.triangulos, s: +((performance.now() - t) / 1000).toFixed(1) });
} catch (e) { estado("ERROR", { mensaje: String(e.message || e).split("\n").filter(Boolean).pop().slice(0, 200) }); process.exitCode = 1; }
