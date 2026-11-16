// Prueba del puente web/engine.js bajo Pyodide en Node: node web/tests/engine_node.mjs <carpeta-pyodide> <figura.stl>
// Verifica: instalación del motor con callKwargs, paso de bytes JS→Python (load_stl) y conversión del ZIP Python→JS (_pack).
// `demo`/`build` necesitan manifold3d: sin su wheel de Pyodide se espera el fallo de import (se informa, no se oculta).
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createEngine } from "../engine.js";

const [dir, stlPath, zipOut] = process.argv.slice(2);
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

try {
  const t0 = performance.now();
  const laps = []; let last = performance.now(), cur = "inicio";
  const d = eng.demo({ onMessage: (m) => { const n = performance.now(); laps.push([cur, (n - last) / 1000]); cur = String(m); last = n; } });
  laps.push([cur, (performance.now() - last) / 1000]);
  console.log("  etapas (s):"); laps.sort((a, b) => b[1] - a[1]).slice(0, 8).forEach(([k, v]) => console.log(`    ${v.toFixed(1).padStart(6)}  ${k}`));
  if (zipOut) writeFileSync(zipOut, d.zip);
  const seg = ((performance.now() - t0) / 1000).toFixed(1);
  check(true, `demo: terminó en ${seg} s con manifold3d → ${d.archivos.length} archivos, zip ${d.zip.length} bytes`);
  const inf = JSON.parse(d.informe);
  check(d.archivos.some((a) => a.endsWith(".stl")), "demo: el ZIP trae STL");
  console.log("  informe (claves):", Object.keys(inf).join(", "));
  // preview_pose: arrays tipados desde el resultado en memoria de la demo (slider de cadera de la página)
  const t1 = performance.now();
  const p90 = eng.previewPose(90, true), p0 = eng.previewPose(0, false);
  const f = p90.piezas.pierna_izq;
  check(f?.v instanceof Float32Array && f.i instanceof Uint32Array && f.v.length % 3 === 0 && f.i.length % 3 === 0 && Math.max(...f.i.subarray(0, 3000)) < f.v.length / 3,
    `preview_pose: pierna_izq llega como Float32Array(${f?.v.length}) y Uint32Array(${f?.i.length}) con índices válidos (${((performance.now() - t1) / 1000).toFixed(1)} s para 2 poses)`);
  check(Object.keys(p90.colisiones).length === 0 && p90.angulo === 90 && eng.previewPose(500, false).angulo === 95, "preview_pose: sin choques a 90° y el ángulo se acota a 95°");
  const mean = (a) => { let s = 0; for (let k = 1; k < a.length; k += 3) s += a[k]; return s / (a.length / 3); };
  check(Math.abs(mean(p0.piezas.pierna_izq.v) - mean(p90.piezas.pierna_izq.v)) > 5, "preview_pose: la pierna se mueve entre 0° y 90°");
} catch (e) { check(/manifold3d/.test(String(e)), `demo: falla esperada sin manifold3d → ${String(e.message || e).split("\n").filter(Boolean).pop()}`); }
process.exit(fails ? 1 : 0);
