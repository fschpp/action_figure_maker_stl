import { planeFromPoints, mergeProject } from "./planes.js";
const $ = (s) => document.querySelector(s);
const log = (t) => { $("#log").textContent += t + "\n"; };

// Worker de tipo module (import dinámico de pyodide.mjs); sin bundler, ver plan §9.
const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
let nextId = 1;
const pending = new Map();
let ready = null;          // promesa de init (se inicia con el primer uso)
let stlBytes = null, projText = null;

worker.onmessage = ({ data }) => {
  if (data.type === "progress") {
    $("#bar").hidden = false;
    if (data.pct != null) $("#bar").value = data.pct;
    $("#stage").textContent = data.stage;
    return;
  }
  const p = pending.get(data.id);
  if (!p) return;
  pending.delete(data.id);
  data.type === "error" ? p.reject(new Error(data.message)) : p.resolve(data.value);
};
worker.onerror = (e) => log("Error del worker: " + e.message);

const call = (type, extra = {}, transfer = []) => new Promise((resolve, reject) => {
  const id = nextId++;
  pending.set(id, { resolve, reject });
  worker.postMessage({ id, type, ...extra }, transfer);
});
const init = () => (ready ??= call("init"));

function busy(on) { for (const b of document.querySelectorAll("button")) b.disabled = on || (b.id === "build" && !(stlBytes && projText)); }
function sync() { $("#build").disabled = !(stlBytes && projText); }

function showResult(r) {
  const url = URL.createObjectURL(new Blob([r.zip], { type: "application/zip" }));
  $("#download").href = url;
  $("#download").textContent = `Descargar ZIP (${r.archivos.length} archivos, ${(r.zip.byteLength / 1048576).toFixed(1)} MB)`;
  $("#result").hidden = false;
  const v = r.informe.validacion ?? {};
  $("#reph").hidden = $("#report").hidden = false;
  $("#report").textContent = (v.ok ? "✓ validación correcta\n" : "✗ la validación encontró problemas\n") + JSON.stringify(v, null, 2);
}

async function run(fn) {
  busy(true); $("#result").hidden = true; $("#log").textContent = "";
  try { const t0 = performance.now(); await init(); await fn(); log(`Listo en ${((performance.now() - t0) / 1000).toFixed(1)} s`); }
  catch (e) { log("Error: " + e.message); }
  finally { busy(false); sync(); }
}

$("#demo").addEventListener("click", () => run(async () => showResult(await call("demo"))));

$("#stl").addEventListener("change", async (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  stlBytes = new Uint8Array(await f.arrayBuffer()); $("#stlinfo").textContent = "";
  showInViewer(stlBytes);
  await run(async () => {
    const info = await call("load_stl", { bytes: stlBytes.slice() });   // copia: la original queda disponible en la página
    $("#stlinfo").textContent = `${info.triangulos} triángulos, ${info.alto_mm.toFixed(1)} mm de alto, ${info.estanca ? "estanca" : "NO estanca"}`;
  });
});
$("#proj").addEventListener("change", async (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  projText = await f.text();
  try { JSON.parse(projText); } catch { projText = null; log("El proyecto no es un JSON válido."); }
  sync();
});
$("#build").addEventListener("click", () => run(async () => showResult(await call("build", { project: mergeProject(projText, edits), name: "figura" }))));

// --- Visor (M2): se carga solo al abrir un STL, para que un fallo de three.js no impida la demo ---
const edits = {};          // lo elegido en el visor: symmetry, crown_z, front
let viewer = null;
async function showInViewer(bytes) {
  try {
    if (!viewer) {
      const { createViewer } = await import("./viewer.js");
      $("#vista").hidden = false;
      viewer = createViewer($("#canvas"), { onPick: (p, i, n) => { $("#pickinfo").textContent = `Punto ${i} de ${n}: (${p.map((x) => x.toFixed(2)).join(", ")})`; } });
    }
    const info = viewer.loadStl(bytes);
    $("#pickinfo").textContent = `Visor: ${info.triangles} triángulos.`;
  } catch (e) { log("No se pudo abrir el visor 3D: " + e.message); }
}
document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => viewer?.setView(b.dataset.view)));
$("#pick-sym").addEventListener("click", () => {
  if (!viewer) return;
  $("#pickinfo").textContent = "Hacé clic en 3 puntos de la línea central (2 en la espalda y 1 en el pecho).";
  viewer.startPicking(3, (pts) => {
    try { edits.symmetry = planeFromPoints(...pts, "simetría"); viewer.showPlane(edits.symmetry); $("#pickinfo").textContent = "Plano de simetría definido."; }
    catch (e) { $("#pickinfo").textContent = e.message; }
  });
});
$("#pick-crown").addEventListener("click", () => {
  if (!viewer) return;
  $("#pickinfo").textContent = "Hacé clic en la coronilla (sin sombrero ni orejas).";
  viewer.startPicking(1, ([p]) => { edits.crown_z = p[2]; $("#pickinfo").textContent = `Coronilla en Z = ${p[2].toFixed(2)}.`; });
});
$("#front").addEventListener("change", (e) => { edits.front = e.target.value; });
sync();
