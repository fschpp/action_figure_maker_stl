// Prueba de humo del motor bajo Pyodide, en Node (sin navegador).
// Uso: node web/tests/smoke_node.mjs <carpeta-pyodide> <wheel-del-motor> [--pypi]
//   <carpeta-pyodide>: release descomprimido de https://github.com/pyodide/pyodide/releases (contiene pyodide.mjs)
//   --pypi: permite micropip.install desde PyPI (trimesh, python-i18n); sin la opción se esperan en la carpeta.
import { pathToFileURL } from "node:url";
import { resolve, basename } from "node:path";
import { readFileSync } from "node:fs";

const [dir, wheel, ...flags] = process.argv.slice(2);
if (!dir || !wheel) { console.error("uso: smoke_node.mjs <carpeta-pyodide> <wheel> [--pypi]"); process.exit(2); }
const base = resolve(dir);
const { loadPyodide } = await import(pathToFileURL(resolve(base, "pyodide.mjs")).href);

const t0 = performance.now();
const lap = (m) => console.log(`[${((performance.now() - t0) / 1000).toFixed(1)} s] ${m}`);

const py = await loadPyodide({ indexURL: base + "/" });
lap("Pyodide listo");
await py.loadPackage(["numpy", "scipy", "shapely", "scikit-image", "networkx", "pillow", "micropip"]);
lap("paquetes nativos cargados");

const micropip = py.pyimport("micropip");
if (flags.includes("--pypi")) {
  await micropip.install(["trimesh", "python-i18n"]);
  lap("trimesh y python-i18n instalados desde PyPI");
}
const whlName = basename(wheel);   // micropip exige el nombre original del wheel
py.FS.writeFile(`/tmp/${whlName}`, readFileSync(resolve(wheel)));
// Desde JS los kwargs de Python se pasan con callKwargs (un objeto como 2.º argumento NO se convierte)
await micropip.install.callKwargs(`emfs:/tmp/${whlName}`, { deps: false });
lap("wheel del motor instalado");

const report = await py.runPythonAsync(`
import importlib, json, sys
res = []
def probe(mod):
    try:
        m = importlib.import_module(mod)
        res.append((mod, "OK", getattr(m, "__version__", "")))
    except Exception as e:
        res.append((mod, "FALLA", f"{type(e).__name__}: {e}"))
for mod in ["numpy","scipy","shapely","skimage","networkx","PIL","trimesh","i18n",
            "manifold3d","mapbox_earcut","fast_simplification","pymeshfix",
            "afm","afm.solids","afm.pipeline"]:
    probe(mod)
json.dumps({"python": sys.version.split()[0], "res": res})
`);
const r = JSON.parse(report);
console.log("Python", r.python);
let fails = 0;
for (const [m, s, d] of r.res) { console.log(`${s === "OK" ? "✓" : "✗"} ${m.padEnd(20)} ${d}`); if (s !== "OK") fails++; }
lap(`${fails} módulos con falla`);
