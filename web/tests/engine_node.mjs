// Prueba del puente web/engine.js bajo Pyodide en Node: node web/tests/engine_node.mjs <carpeta-pyodide> <figura.stl>
// Verifica: instalación del motor con callKwargs, paso de bytes JS→Python (load_stl), el puente de planos (frame, new_limb,
// new_head, new_waist), la conversión del ZIP Python→JS (_pack) y la vista previa de la pose (previewPose).
// `demo`/`build` necesitan manifold3d: sin su wheel de Pyodide se espera el fallo de import (se informa, no se oculta).
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createEngine } from "../engine.js";
import { planeFromPoints, planeToJson, toFigure } from "../planes.js";

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

// Puente de planos (los usa web/app.js al generar): marco archivo→figura y miembros / cabeza / cintura por 3 puntos.
const fr = eng.figureFrame(null, 100);
check(Math.abs(fr.k - 1) < 1e-6 && fr.offset.length === 3 && fr.offset.every(Number.isFinite), `figure_frame: k = ${fr.k.toFixed(4)} (la figura ya mide 100 mm), offset = ${fr.offset.map((x) => x.toFixed(2))}`);
// El caso que importa: un STL en otras unidades y fuera del origen (÷100 y +[5, 3, 2]) → k = 100 y offset = -k·[5, 3, 2] + el centrado.
const odd = eng.py.runPython(`
from afm.testfigure import make_doll
_m = make_doll(); _m.apply_scale(0.01); _m.apply_translation([5, 3, 2])
_m.export(file_type="stl")`).toJs();
eng.loadStl(new Uint8Array(odd));
const fr2 = eng.figureFrame(null, 100);
check(Math.abs(fr2.k - 100) < 1e-6 && [-500, -300, -200].every((v, i) => Math.abs(fr2.offset[i] - v) < 1e-3), `figure_frame con un STL fuera de escala y del origen: k = ${fr2.k.toFixed(3)}, offset = ${fr2.offset.map((x) => x.toFixed(2))}`);
eng.loadStl(stl);       // vuelve a la figura normal para el resto de las pruebas
const arm = eng.newLimb("brazo", "arm", [[0, 3, 0], [1, 3, 0], [0, 3, 1]]);
const leg = eng.newLimb("pierna", "leg", [[0, 3, 0], [1, 3, 0], [0, 3, 1]]);
check(arm.extrude === "outward" && Math.abs(arm.thickness - 12 * 1.653) < 1e-9 && arm.polygon.length === 0 && arm.auto === true
  && leg.extrude === "symmetric" && leg.thickness === 100, "new_limb: brazo «outward» de 12 × 1,653 mm y pierna simétrica de 100 mm, sin polígono");
const head = eng.newHead([[-5, -10, 75], [5, -10, 75], [0, 10, 72]]);
const waist = eng.newWaist([[0, 0, 50], [10, 0, 50], [0, 10, 50]]);
check(head.name === "cabeza" && head.plane.normal[2] > 0.9 && waist.name === "cintura" && waist.plane.normal[2] === 1, "new_head / new_waist: planos válidos con la normal hacia arriba");
const rejects = (f, re) => { try { f(); return false; } catch (e) { return re.test(String(e.message || e)); } };
check(rejects(() => eng.newLimb("x", "leg", [[0, 0, 0], [1, 1, 1], [2, 2, 2]]), /colineales/), "new_limb: 3 puntos alineados → error legible en JS (colineales)");
check(rejects(() => eng.newWaist([[0, 0, 0], [1, 0, 0], [0, 0, 1]]), /casi vertical/) && rejects(() => eng.newHead([[0, 0, 0], [1, 0, 0], [0, 0, 1]]), /casi vertical/), "new_head / new_waist: plano vertical → error legible en JS");

// decimateStl (botón «Reducir el STL…» de la página): STL pesado → bytes nuevos como Uint8Array, mismo alto, menos triángulos.
const heavy = eng.py.runPython(`
from afm.testfigure import make_doll
make_doll().subdivide().subdivide().export(file_type="stl")`).toJs();
const hi = eng.loadStl(new Uint8Array(heavy));
const dec = eng.decimateStl(5000);
check(hi.pesado === true && dec.stl instanceof Uint8Array && dec.info.reducido === true && dec.info.triangulos < hi.triangulos / 2
  && Math.abs(dec.info.alto_mm - hi.alto_mm) < 0.005 * hi.alto_mm,
  `decimate_stl: ${hi.triangulos} → ${dec.info.triangulos} triángulos, STL de ${dec.stl?.length} bytes (${dec.stl?.constructor?.name}), alto ${dec.info.alto_mm?.toFixed(2)} mm`);
const noop = eng.decimateStl(10 ** 6);
check(noop.stl === null && noop.info.reducido === false, "decimate_stl: ya por debajo del objetivo → no toca nada (stl = null)");
eng.loadStl(stl);       // vuelve a la figura normal para el resto de las pruebas

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

  // Probetas de calibración: ZIP con STL + tabla, idéntico al de la CLI (probes.probe_files)
  const pc = eng.makeTestCoupons({ tipo: "hongo", clearances: [0.4], deltas: [0] });
  check(pc.zip instanceof Uint8Array && pc.zip.length > 1000 && [...pc.archivos].sort().join() === "probetas.stl,probetas.txt" && /0[.,]4/.test(JSON.parse(pc.informe).texto),
    `make_test_coupons: ZIP de ${pc.zip.length} bytes con ${[...pc.archivos].join(", ")}`);

  // Recorte manual: plano de simetría (x = 0, normal +X) y fondo, sobre la figura de la demo ya cargada
  const demoProj = eng.py.runPython("import json\nfrom afm.project import demo_project\njson.dumps(demo_project().to_dict())");
  const te = eng.trimEditor(demoProj, "simetria");
  check(te.plano === "simetria" && te.normal[0] === 1 && te.silueta.length > 0 && te.silueta[0].exterior.length >= 3,
    `trim_editor: plano de simetría (normal ${te.normal}) con ${te.silueta.length} polígono(s) de fondo`);

  // Punta a punta, como lo hace web/app.js: STL fuera de escala y del origen + planos elegidos con 3 puntos del STL crudo,
  // llevados al marco de la figura con `frame` → «Generar» debe dar las mismas piezas que la demo (planos escritos a mano).
  eng.loadStl(new Uint8Array(odd));
  const fr3 = eng.figureFrame(null, 100);
  const proj = JSON.parse(eng.py.runPython("import json\nfrom afm.project import demo_project\njson.dumps(demo_project().to_dict())"));
  proj.symmetry = planeToJson(planeFromPoints(...toFigure([[5, 0, 0], [5, 1, 0], [5, 0, 1]], fr3), "simetría"));
  const cut = planeToJson(planeFromPoints(...toFigure([[0, 3, 0], [1, 3, 0], [0, 3, 1]], fr3), "corte al medio"));
  proj.limbs.forEach((l) => { l.plane = cut; });
  const t2 = performance.now();
  const e2e = JSON.parse(eng.build(JSON.stringify(proj), {}).informe);
  const vr = inf.validacion.pieces, ve = e2e.validacion.pieces;
  const dif = Object.keys(vr).map((k) => [k, Math.abs(ve[k].volumen - vr[k].volumen) / vr[k].volumen]);
  check(dif.every(([, d]) => d < 0.01), `punta a punta: «Generar» con planos de 3 puntos crudos == demo (${((performance.now() - t2) / 1000).toFixed(1)} s; dif. de volumen por pieza: ${dif.map(([k, d]) => `${k} ${(d * 100).toFixed(2)} %`).join(", ")})`);

  // Silueta automática (`_fill_silhouettes` → `afm.gui.session`, módulo del motor sin GTK): miembros creados en el visor con
  // 3 puntos llegan sin polígono. Pierna: el motor la propone y construye; brazo del muñeco: no tiene tramo libre → error legible.
  eng.loadStl(stl);
  const noPoly = (kind) => { const q = JSON.parse(JSON.stringify(JSON.parse(eng.py.runPython("import json\nfrom afm.project import demo_project\njson.dumps(demo_project().to_dict())")))); q.limbs.forEach((l) => { if (l.kind === kind) l.polygon = []; }); return JSON.stringify(q); };
  check(rejects(() => eng.build(noPoly("arm"), {}), /no se pudo proponer la silueta de «brazo»/), "silueta automática: el brazo del muñeco no tiene tramo libre → error legible en JS («Dibujala a mano»)");
  const t3 = performance.now(), msgs3 = [];
  const sil = eng.build(noPoly("leg"), { onMessage: (m) => msgs3.push(String(m)) });
  check(msgs3.some((m) => /Proponiendo siluetas/.test(m)) && sil.archivos.some((a) => a.endsWith("pierna_izq.stl")),
    `silueta automática: pierna sin polígono → el motor la propone y construye bajo Pyodide (${((performance.now() - t3) / 1000).toFixed(1)} s, ${sil.archivos.length} archivos)`);

  // Editor de polígonos 2D (limb_editor): plano ortonormal, fondo, propuesta de la pierna y aviso legible del brazo.
  const ed = eng.limbEditor(noPoly("leg"), "pierna", true);
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  check(Math.abs(dot(ed.u, ed.v)) < 1e-9 && Math.abs(dot(ed.u, ed.normal)) < 1e-9 && ed.silueta.length > 0 && ed.silueta[0].exterior.length >= 3
    && ed.polygon.length === 0 && ed.propuesta?.length >= 3 && ed.aviso === null,
    `limb_editor (pierna sin polígono): plano ortonormal, fondo de ${ed.silueta.length} polígono(s) y propuesta de ${ed.propuesta?.length} vértices`);
  const ea = eng.limbEditor(noPoly("arm"), "brazo", true);
  check(ea.propuesta === null && /a mano/.test(ea.aviso ?? "") && ea.silueta.length > 0, "limb_editor (brazo del muñeco): sin propuesta y con el aviso «dibujala a mano»");
  check(rejects(() => eng.limbEditor(noPoly("leg"), "alas", false), /no tiene un miembro/), "limb_editor: miembro inexistente → error legible en JS");
} catch (e) { check(/manifold3d/.test(String(e)), `demo: falla esperada sin manifold3d → ${String(e.message || e).split("\n").filter(Boolean).pop()}`); }
process.exit(fails ? 1 : 0);
