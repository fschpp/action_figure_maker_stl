// Web Worker: carga Pyodide y atiende mensajes {id, type, ...}.
// Protocolo (plan M1): init, probe, (más adelante load_stl, build) → progress / result / error.
import { PYODIDE_BASE, PYODIDE_PACKAGES, MICROPIP_PACKAGES, NATIVE_PROBES } from "./config.js";

// Worker de tipo "module" (ver app.js): Pyodide se importa con su build ESM.
const { loadPyodide } = await import(PYODIDE_BASE + "pyodide.mjs");
let pyodide = null;

const post = (msg) => self.postMessage(msg);
const progress = (stage, pct) => post({ type: "progress", stage, pct });

async function init() {
  progress("Pyodide", 5);
  pyodide = await loadPyodide({ indexURL: PYODIDE_BASE });
  progress("Paquetes", 25);
  await pyodide.loadPackage(PYODIDE_PACKAGES);
  progress("Paquetes de PyPI", 60);
  const micropip = pyodide.pyimport("micropip");
  for (const name of MICROPIP_PACKAGES) await micropip.install(name);
  progress("Listo", 100);
}

async function probe() {
  const code = `
import importlib, json, sys, time
out = []
for name in ${JSON.stringify([...PYODIDE_PACKAGES.filter(p => p !== "micropip"), ...MICROPIP_PACKAGES, ...NATIVE_PROBES])}:
    mod = {"scikit-image": "skimage", "pillow": "PIL", "python-i18n": "i18n"}.get(name, name.replace("-", "_"))
    t = time.time()
    try:
        m = importlib.import_module(mod)
        out.append({"name": name, "ok": True, "detail": getattr(m, "__version__", "") + f" ({time.time()-t:.2f} s)"})
    except Exception as e:
        out.append({"name": name, "ok": False, "detail": f"{type(e).__name__}: {e}"})
json.dumps({"python": sys.version, "deps": out})
`;
  return JSON.parse(await pyodide.runPythonAsync(code));
}

self.onmessage = async (ev) => {
  const { id, type } = ev.data;
  try {
    if (type === "init") { await init(); post({ id, type: "result", value: "ok" }); }
    else if (type === "probe") { post({ id, type: "result", value: await probe() }); }
    else throw new Error(`mensaje desconocido: ${type}`);
  } catch (e) {
    post({ id, type: "error", message: String(e && e.message || e) });
  }
};
