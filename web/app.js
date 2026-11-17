import { planeFromPoints, mergeProject, toFigure } from "./planes.js";
import { initVersionUI } from "./version.js";
import { cleanName, extractProfile, applyProfile, loadProfiles, storeProfiles, exportProfiles, importProfiles } from "./profiles.js";
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
// Si el worker muere (no se pudo cargar el script o Pyodide, excepción sin capturar), las promesas pendientes no se
// resolverían nunca: `running` quedaba en true y la página «ocupada» para siempre, con el error solo en la consola.
// Se rechaza todo lo pendiente, se muestra el motivo en un aviso fijo (el log lo limpia cada acción) y las acciones
// siguientes fallan al instante. Un worker muerto no se recupera: hay que recargar la página.
let workerFatal = null;
function failWorker(detail) {
  workerFatal ??= `El motor dejó de responder: ${detail}. Recargá la página; si se repite, pegá este mensaje en el reporte.`;
  const err = new Error(workerFatal);
  for (const p of pending.values()) p.reject(err);
  pending.clear();
  const box = $("#workererror");
  box.textContent = "⚠ " + workerFatal; box.hidden = false;
  $("#bar").hidden = true; $("#stage").textContent = "";
}
worker.onerror = (e) => { e.preventDefault?.(); failWorker(e.message || "no se pudo cargar el worker"); };
worker.onmessageerror = () => failWorker("el worker mandó un mensaje que la página no pudo leer");

const call = (type, extra = {}, transfer = []) => new Promise((resolve, reject) => {
  if (workerFatal) return reject(new Error(workerFatal));
  const id = nextId++;
  pending.set(id, { resolve, reject });
  worker.postMessage({ id, type, ...extra }, transfer);
});
// Un `init` que falla con un error normal (p. ej. se cortó la red al bajar un paquete) no se memoriza: el próximo intento reintenta.
const init = () => (ready ??= call("init").catch((e) => { ready = null; throw e; }));

// Una sola acción a la vez (carga del STL, reducir, generar). «Generar» depende SOLO de los datos cargados y de si hay una
// acción en curso, y dice por qué está deshabilitado. Los selectores de archivo no se bloquean (el proyecto solo guarda datos);
// el STL, que dispara una acción, se ignora con un aviso si hay otra en curso. Antes `sync()` ignoraba la acción en curso:
// cargar el proyecto con el STL procesándose habilitaba «Generar», corrían dos acciones y se pisaban `runT0` (sin marcas de etapa).
let running = false;
// «Buscar actualización» NUNCA se deshabilita: una generación dura minutos y es justo cuando se quiere actualizar; con el botón
// apagado la única salida era Shift+F5, que además borra los archivos elegidos. Además Firefox conserva el estado `disabled`
// de los botones al recargar: si se recargaba con una acción en curso, la página nueva arrancaba con los botones apagados y
// `busy(false)` nunca llegaba a rehabilitarlos. Por eso `applyBusy()` también corre al cargar y en `pageshow`.
const ALWAYS_ON = new Set(["check-update"]);
function applyBusy() {
  for (const b of document.querySelectorAll("button")) b.disabled = ALWAYS_ON.has(b.id) ? false : running;
  sync();
}
function busy(on) { running = on; applyBusy(); }
window.addEventListener("pageshow", applyBusy);
function sync() {
  const falta = !stlBytes ? "falta cargar el STL" : !projText ? "falta cargar el proyecto" : running ? "hay una acción en curso" : "";
  $("#build").disabled = !!falta;
  $("#buildhint").textContent = falta ? `(${falta})` : "";
  renderProfiles();
}

// --- Perfiles de línea (plan §8.12): los `params` del proyecto, con nombre, en localStorage + exportar/importar ---
let store = null;
try { store = window.localStorage; } catch { /* sin almacenamiento (p. ej. cookies bloqueadas): los perfiles viven solo en esta sesión */ }
let profiles = store ? loadProfiles(store) : {};
const pfInfo = (t) => { $("#pf-info").textContent = t; };
function renderProfiles() {
  const sel = $("#pf-list"), keep = sel.value, names = Object.keys(profiles).sort((a, b) => a.localeCompare(b, "es"));
  if (sel.options.length !== names.length || names.some((n, i) => sel.options[i].value !== n)) {
    sel.replaceChildren(...names.map((n) => new Option(n, n)));
    sel.value = names.includes(keep) ? keep : (names[0] ?? "");
  }
  sel.disabled = !names.length;
  $("#pf-save").disabled = running || !projText;
  $("#pf-apply").disabled = running || !projText || !names.length;
  $("#pf-del").disabled = running || !names.length;
  $("#pf-export").disabled = running || !names.length;
}
function persistProfiles() {
  renderProfiles();
  if (store && !storeProfiles(store, profiles)) pfInfo("Este navegador no dejó guardar el perfil: queda solo hasta cerrar la página. Exportalo para conservarlo.");
}
function pfAction(fn) { try { fn(); } catch (e) { pfInfo(e.message); } }
$("#pf-save").addEventListener("click", () => pfAction(() => {
  const name = cleanName($("#pf-name").value), existed = name in profiles;
  profiles[name] = extractProfile(projText);
  persistProfiles(); $("#pf-list").value = name;
  if (!$("#pf-info").textContent.startsWith("Este navegador")) pfInfo(`Perfil «${name}» ${existed ? "actualizado" : "guardado"} (${Object.keys(profiles[name]).length} parámetros).`);
}));
$("#pf-apply").addEventListener("click", () => pfAction(() => {
  const name = $("#pf-list").value;
  projText = applyProfile(projText, profiles[name]);
  pfInfo(`Perfil «${name}» aplicado al proyecto cargado: «Generar» usa sus parámetros${profiles[name].ref_height ? ` (altura de referencia ${profiles[name].ref_height} mm)` : ""}. El archivo del proyecto no se modifica.`);
}));
$("#pf-del").addEventListener("click", () => pfAction(() => {
  const name = $("#pf-list").value;
  delete profiles[name]; persistProfiles(); pfInfo(`Perfil «${name}» eliminado.`);
}));
$("#pf-export").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([exportProfiles(profiles)], { type: "application/json" }));
  a.download = "perfiles-de-linea.json"; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});
$("#pf-import").addEventListener("change", async (ev) => {
  const f = ev.target.files[0]; ev.target.value = "";
  if (!f) return;
  try {
    const r = importProfiles(await f.text(), profiles);
    profiles = r.profiles; persistProfiles();
    if (!$("#pf-info").textContent.startsWith("Este navegador")) pfInfo(`Importados: ${r.added} nuevos, ${r.replaced} reemplazados${r.skipped ? `, ${r.skipped} omitidos por estar vacíos o mal formados` : ""}.`);
  } catch (e) { pfInfo(e.message); }
});
applyBusy();   // estado inicial de todos los botones (y de los de perfiles, que dependen de sync())

// --- Pose (preview_pose): el slider mueve la cadera de la figura ya generada; las colisiones se miden al soltar ---
let poseViewer = null, poseBusy = false, poseNext = null;
async function requestPose(angle, collisions) {
  if (poseBusy) { poseNext = [angle, collisions]; return; }     // si el motor está ocupado, solo cuenta el último valor
  poseBusy = true;
  try {
    const r = await call("preview_pose", { angle, collisions });
    const bad = Object.keys(r.colisiones);
    poseViewer.showPieces(r.piezas, bad);
    $("#angle-out").textContent = `${r.angulo.toFixed(0)}°`;
    if (collisions) $("#poseinfo").textContent = bad.length ? `Choque con el torso a ${r.angulo.toFixed(0)}°: ${bad.map((n) => `${n} (${r.colisiones[n]} mm³)`).join(", ")}` : `Sin choques a ${r.angulo.toFixed(0)}°.`;
  } catch (e) { $("#poseinfo").textContent = "No se pudo mostrar la pose: " + e.message; }
  finally { poseBusy = false; if (poseNext) { const n = poseNext; poseNext = null; requestPose(...n); } }
}
async function showPose() {
  try {
    if (!poseViewer) {
      const { createViewer } = await import("./viewer.js");
      $("#pose").hidden = false;
      poseViewer = createViewer($("#pose-canvas"));
      $("#angle").addEventListener("input", (e) => requestPose(+e.target.value, false));
      $("#angle").addEventListener("change", (e) => requestPose(+e.target.value, true));
      document.querySelectorAll("[data-pview]").forEach((b) => b.addEventListener("click", () => poseViewer.setView(b.dataset.pview)));
    }
    await requestPose(+$("#angle").value, true);
  } catch (e) { log("No se pudo abrir la vista de la pose: " + e.message); }
}

function showResult(r) {
  const url = URL.createObjectURL(new Blob([r.zip], { type: "application/zip" }));
  $("#download").href = url;
  $("#download").textContent = `Descargar ZIP (${r.archivos.length} archivos, ${(r.zip.byteLength / 1048576).toFixed(1)} MB)`;
  $("#result").hidden = false;
  const v = r.informe.validacion ?? {};
  $("#reph").hidden = $("#report").hidden = false;
  $("#report").textContent = (v.ok ? "✓ validación correcta\n" : "✗ la validación encontró problemas\n") + JSON.stringify(v, null, 2);
  showPose();
}

async function run(fn) {
  if (running) return;                 // defensa: no se superponen acciones (ver `busy`)
  busy(true); $("#result").hidden = true; $("#log").textContent = "";
  try {
    const t0 = performance.now(); await init();
    log(`Motor cargado en ${((performance.now() - t0) / 1000).toFixed(1)} s (Pyodide, paquetes y wheels; se paga una vez por pestaña)`);
    runT0 = performance.now();      // las etapas se miden desde aquí; «Listo en» sigue incluyendo la carga de Pyodide
    await fn(); log(`Listo en ${((performance.now() - t0) / 1000).toFixed(1)} s`);
  }
  catch (e) { log("Error: " + e.message); }
  finally { runT0 = null; busy(false); sync(); }
}

$("#demo").addEventListener("click", () => run(async () => showResult(await call("demo"))));

$("#stl").addEventListener("change", async (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  if (running) { log("Hay otra acción en curso: esperá a que termine y volvé a elegir el STL."); ev.target.value = ""; return; }
  stlBytes = new Uint8Array(await f.arrayBuffer()); $("#stlinfo").textContent = "";
  showInViewer(stlBytes);
  await run(async () => {
    const info = stlInfo = await call("load_stl", { bytes: stlBytes.slice() });   // copia: la original queda disponible en la página
    for (const a of info.avisos ?? []) log("Aviso: " + a);
    // Muchos STL vienen en otras unidades (p. ej. ~1,1 de alto): no son mm, el motor los escala a 100 mm pies→coronilla.
    const h = info.alto_mm, alto = (h < 20 || h > 500) ? `${h.toPrecision(3)} unidades de alto (se escala a 100 mm)` : `${h.toFixed(1)} mm de alto`;
    $("#stlinfo").textContent = `${info.triangulos} triángulos, ${alto}, ${info.estanca ? "estanca" : "NO estanca"}`;
    $("#decimate").hidden = !info.pesado;     // STL pesado: se ofrece reducirlo antes de generar
  });
});
$("#decimate").addEventListener("click", () => run(async () => {
  const { info, stl } = await call("decimate_stl", { target: 10000 });
  for (const a of info.avisos ?? []) log("Aviso: " + a);
  stlInfo = info;
  if (stl) { stlBytes = stl; showInViewer(stlBytes); }
  const h = info.alto_mm, alto = (h < 20 || h > 500) ? `${h.toPrecision(3)} unidades de alto (se escala a 100 mm)` : `${h.toFixed(1)} mm de alto`;
  $("#stlinfo").textContent = `${info.triangulos} triángulos${info.reducido ? ` (reducido de ${info.triangulos_antes})` : ""}, ${alto}, ${info.estanca ? "estanca" : "NO estanca"}`;
  $("#decimate").hidden = true;
}));
async function loadProjectFile(f) {
  projText = await f.text();
  try { JSON.parse(projText); } catch { projText = null; log("El proyecto no es un JSON válido."); }
  sync();
}
$("#proj").addEventListener("change", (ev) => { const f = ev.target.files[0]; if (f) loadProjectFile(f); });
// Al recargar la página (p. ej. tras actualizar la versión) Firefox restaura los NOMBRES de los archivos elegidos en los
// <input type=file> sin disparar `change`: el nombre se ve pero la página no tiene el contenido y «Generar» quedaba
// deshabilitado sin motivo aparente. Se toman esos archivos restaurados (el proyecto es un dato; el STL, una acción normal).
function restoreFiles() {
  const pf = $("#proj").files?.[0];
  if (pf && !projText) loadProjectFile(pf);
  if ($("#stl").files?.[0] && !stlBytes && !running) $("#stl").dispatchEvent(new Event("change"));
}
window.addEventListener("pageshow", restoreFiles);
if (document.readyState === "complete") restoreFiles(); else window.addEventListener("load", restoreFiles);
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

// --- Versión: ¿es la última publicada o una vieja en la caché? (web/version.js) ---
// build-info.js / version.json los genera scripts/build_web.sh; en desarrollo local pueden no existir.
(async () => {
  const build = await import("./build-info.js").then((m) => m.BUILD, () => null);
  const ui = initVersionUI({
    el: { label: $("#version-label"), button: $("#check-update"), status: $("#update-status") },
    build, versionUrl: new URL("./version.json", import.meta.url).href, base: import.meta.url,
    beforeReload: () => !running || confirm("Hay una acción en curso y recargar la página la corta. ¿Actualizar igual?"),
  });
  ui.check({ silent: true });
})();
