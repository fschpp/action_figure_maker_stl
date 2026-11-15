// Web Worker: carga Pyodide y atiende mensajes {id, type, ...}.
// Protocolo: init, load_stl, frame, new_limb, new_head, new_waist, build, demo → progress / result / error.
import { PYODIDE_BASE, PYODIDE_PACKAGES, MICROPIP_PACKAGES, WHEELS_MANIFEST } from "./config.js";
import { createEngine } from "./engine.js";

// Worker de tipo "module" (ver app.js): Pyodide se importa con su build ESM.
const { loadPyodide } = await import(PYODIDE_BASE + "pyodide.mjs");
let engine = null;

const post = (msg, transfer = []) => self.postMessage(msg, transfer);
const progress = (stage, pct) => post({ type: "progress", stage, pct });

async function init() {
  const man = await (await fetch(new URL(WHEELS_MANIFEST, import.meta.url))).json();
  const wheelUrls = [...(man.extra ?? []), man.engine].map((f) => new URL(`./wheels/${f}`, import.meta.url).href);
  engine = await createEngine({
    loadPyodide, indexURL: PYODIDE_BASE, pyodidePackages: PYODIDE_PACKAGES, pypiPackages: MICROPIP_PACKAGES,
    wheelUrls, progress,
  });
}

// Convierte el resultado Python (objeto JS con PyProxy) en algo transferible y libera los proxies.
function unpack(r) {
  const zip = r.zip.slice();        // copia a un ArrayBuffer propio, transferible
  const out = { zip, archivos: r.archivos.toJs ? r.archivos.toJs() : r.archivos, informe: JSON.parse(r.informe) };
  return out;
}

self.onmessage = async (ev) => {
  const { id, type } = ev.data;
  try {
    if (type === "init") { await init(); post({ id, type: "result", value: "ok" }); }
    else if (type === "load_stl") { post({ id, type: "result", value: engine.loadStl(ev.data.bytes) }); }
    else if (type === "frame") { post({ id, type: "result", value: engine.figureFrame(ev.data.crown_z, ev.data.ref_height) }); }
    else if (type === "new_limb") { post({ id, type: "result", value: engine.newLimb(ev.data.name, ev.data.kind, ev.data.points) }); }
    else if (type === "new_head") { post({ id, type: "result", value: engine.newHead(ev.data.points) }); }
    else if (type === "new_waist") { post({ id, type: "result", value: engine.newWaist(ev.data.points) }); }
    else if (type === "build" || type === "demo") {
      const onMessage = (m) => post({ type: "progress", stage: String(m), pct: null });
      const r = type === "demo" ? engine.demo({ onMessage }) : engine.build(ev.data.project, { onMessage, name: ev.data.name });
      const v = unpack(r);
      post({ id, type: "result", value: v }, [v.zip.buffer]);
    } else throw new Error(`mensaje desconocido: ${type}`);
  } catch (e) {
    post({ id, type: "error", message: String((e && e.message) || e) });
  }
};
