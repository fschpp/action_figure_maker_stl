import { planeFromPoints, mergeProject, toFigure } from "./planes.js";
const $ = (s) => document.querySelector(s);
const log = (t) => { $("#log").textContent += t + "\n"; };

// Worker de tipo module (import dinámico de pyodide.mjs); sin bundler, ver plan §9.
const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
let nextId = 1;
const pending = new Map();
let ready = null;          // promesa de init (se inicia con el primer uso)
let stlBytes = null, projText = null;
let runT0 = null;          // inicio de la acción en curso (tras cargar Pyodide), para cronometrar etapas

worker.onmessage = ({ data }) => {
  if (data.type === "progress") {
    $("#bar").hidden = false;
    if (data.pct != null) $("#bar").value = data.pct;
    $("#stage").textContent = data.stage;
    // Mensajes del motor (pct == null): se anota el instante de cada etapa para ver dónde se va el tiempo (G3).
    if (data.pct == null && runT0 != null) log(`+${((performance.now() - runT0) / 1000).toFixed(1)} s  ${data.stage}`);
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
  try {
    const t0 = performance.now(); await init();
    runT0 = performance.now();      // las etapas se miden desde aquí; «Listo en» sigue incluyendo la carga de Pyodide
    await fn(); log(`Listo en ${((performance.now() - t0) / 1000).toFixed(1)} s`);
  }
  catch (e) { log("Error: " + e.message); }
  finally { runT0 = null; busy(false); sync(); }
}

$("#demo").addEventListener("click", () => run(async () => showResult(await call("demo"))));

$("#stl").addEventListener("change", async (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  stlBytes = new Uint8Array(await f.arrayBuffer()); $("#stlinfo").textContent = "";
  showInViewer(stlBytes);
  await run(async () => {
    const info = stlInfo = await call("load_stl", { bytes: stlBytes.slice() });   // copia: la original queda disponible en la página
    for (const a of info.avisos ?? []) log("Aviso: " + a);
    $("#stlinfo").textContent = `${info.triangulos} triángulos, ${info.alto_mm.toFixed(1)} mm de alto, ${info.estanca ? "estanca" : "NO estanca"}`;
  });
});
$("#proj").addEventListener("change", async (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  projText = await f.text();
  try { JSON.parse(projText); } catch { projText = null; log("El proyecto no es un JSON válido."); }
  sync();
});
$("#build").addEventListener("click", () => run(async () => showResult(await call("build", { project: await projectWithPicks(), name: "figura" }))));

// Lo elegido en el visor está en coordenadas del STL crudo; los planos del proyecto, en el marco de la figura
// normalizada (escala, pies en z=0, centrada). El motor da la transformación (frame) y el miembro por defecto (new_limb).
async function projectWithPicks() {
  const base = JSON.parse(projText);
  const picked = edits.symPts || edits.limbs.size || edits.headPts || edits.waistPts;
  if (!picked && edits.crown_z == null && !edits.front) return projText;
  if (stlInfo && stlInfo.z_arriba === false) throw new Error("El STL no tiene Z arriba: los puntos del visor no se pueden usar.");
  const crown = edits.crown_z ?? base.crown_z ?? null;
  const fr = await call("frame", { crown_z: crown, ref_height: base.params?.ref_height ?? 100 });
  const out = { crown_z: crown };
  if (edits.front) out.front = edits.front;
  if (edits.symPts) out.symmetry = planeFromPoints(...toFigure(edits.symPts, fr), "simetría");
  const limbs = (base.limbs ?? []).filter((l) => !edits.limbs.has(l.name));
  for (const [name, { kind, pts }] of edits.limbs) limbs.push(await call("new_limb", { name, kind, points: toFigure(pts, fr) }));
  const merged = JSON.parse(mergeProject(base, out));
  if (edits.limbs.size) merged.limbs = limbs;
  // Cuello y cintura: se conserva lo que ya tenía el proyecto (pasador, cabeza de reemplazo…) y solo cambia el plano.
  if (edits.headPts) { const h = await call("new_head", { points: toFigure(edits.headPts, fr) }); merged.head = { ...(base.head ?? h), plane: h.plane, pin_center: null }; }
  if (edits.waistPts) { const w = await call("new_waist", { points: toFigure(edits.waistPts, fr) }); merged.waist = { ...(base.waist ?? w), plane: w.plane, pin_center: null }; }
  return JSON.stringify(merged);
}

// --- Visor (M2): se carga solo al abrir un STL, para que un fallo de three.js no impida la demo ---
const edits = { symPts: null, crown_z: null, front: null, limbs: new Map(), headPts: null, waistPts: null };   // puntos crudos elegidos en el visor
let stlInfo = null;
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
    try { viewer.showPlane(planeFromPoints(...pts, "simetría"), 0x3fa9f5, "sim"); edits.symPts = pts; applyHalf(); $("#pickinfo").textContent = "Plano de simetría definido."; }
    catch (e) { $("#pickinfo").textContent = e.message; }
  });
});
$("#pick-crown").addEventListener("click", () => {
  if (!viewer) return;
  $("#pickinfo").textContent = "Hacé clic en la coronilla (sin sombrero ni orejas).";
  viewer.startPicking(1, ([p]) => { edits.crown_z = p[2]; $("#pickinfo").textContent = `Coronilla en Z = ${p[2].toFixed(2)}.`; });
});
for (const [id, kind, name] of [["pick-arm", "arm", "brazo"], ["pick-leg", "leg", "pierna"]]) {
  $("#" + id).addEventListener("click", () => {
    if (!viewer) return;
    $("#pickinfo").textContent = kind === "arm" ? "3 clics no alineados sobre la cara chata del hombro (donde se une el brazo)."
      : "3 clics no alineados sobre un plano que atraviese la pierna y la cadera (p. ej. el plano medio frente/espalda).";
    viewer.startPicking(3, (pts) => {
      try { viewer.showPlane(planeFromPoints(...pts, name), 0xf5a83f, name); edits.limbs.set(name, { kind, pts }); $("#pickinfo").textContent = `Plano del ${name} definido (la silueta la propone el motor al generar).`; }
      catch (e) { $("#pickinfo").textContent = e.message; }
    });
  });
}
for (const [id, key, label, hint, color] of [
  ["pick-head", "headPts", "cuello", "3 clics: 2 en la nuca y 1 bajo el mentón.", 0x6fb7ff],
  ["pick-waist", "waistPts", "cintura", "3 clics a una misma altura alrededor de la cintura (2 a los costados y 1 adelante o atrás).", 0x7bd88f]]) {
  $("#" + id).addEventListener("click", () => {
    if (!viewer) return;
    $("#pickinfo").textContent = hint;
    viewer.startPicking(3, (pts) => {
      try { viewer.showPlane(planeFromPoints(...pts, label), color, key); edits[key] = pts; $("#pickinfo").textContent = `Plano del ${label} definido (se valida al generar).`; }
      catch (e) { $("#pickinfo").textContent = e.message; }
    });
  });
}
// Atenuar la mitad opuesta al plano de simetría: no → «+» → «−» → no. Solo es la vista; el motor espeja solo.
let half = null;
function applyHalf() {
  if (!viewer) return;
  if (half && !edits.symPts) { half = null; $("#pickinfo").textContent = "Primero definí el plano de simetría."; }
  viewer.setHalf(edits.symPts ? planeFromPoints(...edits.symPts, "simetría") : null, half);
  $("#half").textContent = "Atenuar una mitad: " + (half === "+" ? "lado +" : half === "-" ? "lado −" : "no");
}
$("#half").addEventListener("click", () => { half = half === null ? "+" : half === "+" ? "-" : null; applyHalf(); });
$("#front").addEventListener("change", (e) => { edits.front = e.target.value; });
sync();
