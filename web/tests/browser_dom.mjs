// Prueba de navegador (Chrome headless + playwright-core) del DOM, el visor three.js, los clics y el slider de pose.
// El motor Python NO se carga: `worker.js` se reemplaza por un stub con el mismo protocolo (init, load_stl, frame, new_limb,
// new_head, new_waist, limb_editor, preview_pose, build, demo). Pyodide real ya lo cubre web/tests/engine_node.mjs; aquí se prueba
// todo lo que hace la página salvo el motor.
//
// Uso:   node web/tests/browser_dom.mjs
// Env:   PW_DIR     carpeta con node_modules/playwright-core (por defecto, la raíz del repo)
//        THREE_DIR  carpeta de `three` (node_modules/three): si está, las peticiones al CDN se sirven de ahí (sin red)
//        CHROME     ejecutable de Chrome (por defecto, el canal «chrome» de Playwright o /usr/bin/google-chrome)
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import zlib from "node:zlib";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { bounds, fitView, toScreen } from "../polygon-editor.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(here, "..");
const pwDir = process.env.PW_DIR ?? path.resolve(webDir, "..");
const { chromium } = createRequire(path.join(pwDir, "x.js"))("playwright-core");

// ---------- Archivos de prueba ----------
// Caja 40 × 20 × 100 mm (STL ASCII, 12 triángulos), con Z arriba y el frente hacia −Y.
function boxStl(x = 20, y = 10, z = 50) {
  const v = (sx, sy, sz) => [sx * x, sy * y, sz * z + z];
  const quads = [[[-1, -1, -1], [1, -1, -1], [1, -1, 1], [-1, -1, 1]], [[-1, 1, -1], [-1, 1, 1], [1, 1, 1], [1, 1, -1]],
    [[-1, -1, -1], [-1, -1, 1], [-1, 1, 1], [-1, 1, -1]], [[1, -1, -1], [1, 1, -1], [1, 1, 1], [1, -1, 1]],
    [[-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], [[-1, -1, -1], [-1, 1, -1], [1, 1, -1], [1, -1, -1]]];
  let s = "solid caja\n";
  for (const q of quads) for (const t of [[0, 1, 2], [0, 2, 3]]) {
    s += "facet normal 0 0 0\nouter loop\n" + t.map((i) => `vertex ${v(...q[i]).join(" ")}\n`).join("") + "endloop\nendfacet\n";
  }
  return Buffer.from(s + "endsolid caja\n");
}
const PROJECT = JSON.stringify({ version: 1, symmetry: null, crown_z: null, front: "+Y", params: { ref_height: 100 }, limbs: [] });

// Worker de mentira: misma interfaz que web/worker.js. `build` devuelve el proyecto recibido dentro del informe.
const STUB_WORKER = `
self.onmessage = (ev) => {
  const { id, type } = ev.data;
  const post = (value, transfer = []) => self.postMessage({ id, type: "result", value }, transfer);
  if (type === "init") return post("ok");
  if (type === "load_stl") {   // el STL «pesado» tarda 600 ms, como un STL real: da tiempo a intentar otras acciones mientras carga
    const heavy = ev.data.bytes.length > 5000;
    return setTimeout(() => post({ triangulos: heavy ? 20000 : 12, alto_mm: 100, estanca: true, avisos: [], z_arriba: true, pesado: heavy }), heavy ? 600 : 0);
  }
  if (type === "decimate_stl") return post({ info: { triangulos: 10000, triangulos_antes: 20000, reducido: true, alto_mm: 100, estanca: true, avisos: [], z_arriba: true, pesado: false }, stl: new TextEncoder().encode("solid t\\nfacet normal 0 0 0\\nouter loop\\nvertex 0 0 0\\nvertex 10 0 0\\nvertex 0 0 10\\nendloop\\nendfacet\\nendsolid t\\n") });
  if (type === "make_test_coupons") return post({ zip: new Uint8Array([80, 75, 5, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]), archivos: ["probetas.stl", "probetas.txt"], informe: { texto: "tipo=" + ev.data.opts.tipo + " b=" + ev.data.opts.boquilla } });
  if (type === "frame") return post({ k: 1, offset: [0, 0, 0], ref_height: 100 });
  if (type === "new_limb") return post({ name: ev.data.name, kind: ev.data.kind, stub: true, points: ev.data.points });
  if (type === "new_head" || type === "new_waist") return post({ plane: { name: type, origin: ev.data.points[0], normal: [0, 0, 1] } });
  if (type === "limb_editor") return post({ nombre: ev.data.name, tipo: "arm", origin: [0, 0, 0], u: [1, 0, 0], v: [0, 0, 1], normal: [0, -1, 0],
    silueta: [{ exterior: [[-5, -5], [30, -5], [30, 70], [-5, 70]], holes: [] }], polygon: [], propuesta: [[0, 0], [20, 0], [20, 60], [0, 60]], aviso: null });
  if (type === "preview_pose") {
    const a = ev.data.angle;
    const mk = (dx) => ({ v: new Float32Array([dx, 0, 0, dx, 10, 0, dx, 0, 30])   /* plano x = dx: se ve desde «Costado» */, i: new Uint32Array([0, 1, 2]) });
    const piezas = { torso: mk(0), pierna_izq: mk(15) };
    return post({ angulo: a, colisiones: a > 92 ? { pierna_izq: 1.5 } : {}, piezas },
      Object.values(piezas).flatMap((p) => [p.v.buffer, p.i.buffer]));
  }
  if (type === "build" || type === "demo") {
    self.postMessage({ type: "progress", stage: "Separando la cabeza…", pct: null });
    const zip = new Uint8Array([80, 75, 5, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
    return post({ zip, archivos: ["torso.stl", "brazo_izq.stl"], informe: { validacion: { ok: true, proyecto: ev.data.project ?? null } } }, [zip.buffer]);
  }
  self.postMessage({ id, type: "error", message: "mensaje desconocido: " + type });
};`;

// ---------- Servidor estático de web/ ----------
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json" };
const server = http.createServer((req, res) => {
  const p = new URL(req.url, "http://x").pathname;
  const f = path.join(webDir, p === "/" ? "index.html" : p);
  if (!f.startsWith(webDir) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); return res.end("no"); }
  res.writeHead(200, { "content-type": MIME[path.extname(f)] ?? "application/octet-stream", "cache-control": "no-store" });
  fs.createReadStream(f).pipe(res);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}/`;

const exe = process.env.CHROME ?? (fs.existsSync("/usr/bin/google-chrome") ? "/usr/bin/google-chrome" : undefined);
const browser = await chromium.launch({
  headless: true, ...(exe ? { executablePath: exe } : { channel: "chrome" }),
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--no-sandbox",
    "--disable-background-networking", "--proxy-server=direct://", "--proxy-bypass-list=*"],   // sin tráfico de fondo de Chrome
  timeout: 30000,
});
const page = await (await browser.newContext({ viewport: { width: 1000, height: 1400 } })).newPage();
page.setDefaultTimeout(15000);
const errors = [];
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });

await page.route("**/worker.js", (r) => r.fulfill({ contentType: "text/javascript", body: STUB_WORKER }));
// Sin fallos si el CDN no responde: three se sirve de THREE_DIR cuando está (CI y sandbox sin red).
await page.route(/cdn\.jsdelivr\.net\/npm\/three@[^/]+\/(.*)$/, (r) => {
  const rel = r.request().url().match(/three@[^/]+\/(.*)$/)[1];
  const dir = process.env.THREE_DIR;
  if (!dir) return r.continue();
  const f = path.join(dir, rel);
  return fs.existsSync(f) ? r.fulfill({ contentType: "text/javascript", body: fs.readFileSync(f) }) : r.fulfill({ status: 404, body: "" });
});
await page.route(/\/(version\.json|build-info\.js)/, (r) => r.fulfill({ status: 404, body: "" }));   // sin build estampado

let passed = 0;
const step = async (name, fn) => {
  try { await fn(); passed++; console.log("  ✓ " + name); }
  catch (e) { console.error("  ✗ " + name + "\n    " + (e.stack ?? e).toString().split("\n").slice(0, 6).join("\n    ")); process.exitCode = 1;
    const log = await page.locator("#log").innerText().catch(() => "");
    if (log.trim()) console.error("    log de la página: " + log.trim().split("\n").slice(-4).join(" | "));
    if (errors.length) console.error("    errores: " + errors.slice(-3).join(" | ")); }
};
const text = (sel) => page.locator(sel).innerText();
// Cuenta los píxeles de la captura que no son el fondo del visor (0x15181c): 0 = canvas vacío. Decodifica el PNG con zlib.
function litPixels(png) {
  let o = 8, w = 0, h = 0, ct = 0; const idat = [];
  while (o < png.length) {
    const len = png.readUInt32BE(o), type = png.toString("ascii", o + 4, o + 8), d = png.subarray(o + 8, o + 8 + len);
    if (type === "IHDR") { w = d.readUInt32BE(0); h = d.readUInt32BE(4); ct = d[9]; assert.equal(d[8], 8); assert.equal(d[12], 0, "PNG entrelazado"); }
    if (type === "IDAT") idat.push(d);
    o += 12 + len;
  }
  const bpp = ct === 6 ? 4 : 3, raw = zlib.inflateSync(Buffer.concat(idat)), stride = w * bpp;
  const out = Buffer.alloc(h * stride);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)];
    for (let x = 0; x < stride; x++) {
      const v = raw[y * (stride + 1) + 1 + x], a = x >= bpp ? out[y * stride + x - bpp] : 0, b = y ? out[(y - 1) * stride + x] : 0, c = x >= bpp && y ? out[(y - 1) * stride + x - bpp] : 0;
      const pa = Math.abs(b - c), pb = Math.abs(a - c), pc = Math.abs(a + b - 2 * c);
      out[y * stride + x] = (v + (f === 0 ? 0 : f === 1 ? a : f === 2 ? b : f === 3 ? (a + b) >> 1 : (pa <= pb && pa <= pc ? a : pb <= pc ? b : c))) & 255;
    }
  }
  let n = 0;
  for (let i = 0; i < out.length; i += bpp) if (Math.abs(out[i] - 0x15) + Math.abs(out[i + 1] - 0x18) + Math.abs(out[i + 2] - 0x1c) > 12) n++;
  return n;
}
const shot = async (sel) => litPixels(await page.locator(sel).screenshot());
const canvasPoint = async (sel, fx, fy) => { const b = await page.locator(sel).boundingBox(); return { x: b.x + b.width * fx, y: b.y + b.height * fy }; };
const clickAt = async (sel, fx, fy) => { const p = await canvasPoint(sel, fx, fy); await page.mouse.click(p.x, p.y); };

console.log("browser_dom:");
try {
  await page.goto(base);

  await step("la página carga sin errores y las secciones del visor y la pose empiezan ocultas", async () => {
    assert.match(await page.title(), /Action Figure Maker/);
    assert.equal(await page.locator("#vista").isHidden(), true);
    assert.equal(await page.locator("#pose").isHidden(), true);
    assert.equal(await page.locator("#build").isDisabled(), true);
  });

  await step("al abrir un STL aparece el visor, dibuja la figura y el worker informa del STL", async () => {
    await page.locator("#stl").setInputFiles({ name: "caja.stl", mimeType: "model/stl", buffer: boxStl() });
    await page.locator("#vista").waitFor({ state: "visible" });
    await page.waitForFunction(() => document.querySelector("#pickinfo").textContent.includes("Visor: 12"));
    await page.waitForFunction(() => document.querySelector("#stlinfo").textContent.includes("12 triángulos"));
    assert.match(await text("#stlinfo"), /100\.0 mm de alto, estanca/);
    await page.waitForTimeout(300);
    assert.ok(await shot("#canvas") > 3000, "el canvas parece vacío (sin figura dibujada)");
  });

  await step("un STL pesado ofrece «Reducir»; al pulsarlo se informa la reducción y el botón desaparece", async () => {
    assert.equal(await page.locator("#decimate").isHidden(), true);
    const heavy = Buffer.concat([boxStl(), Buffer.alloc(6000, 32)]);      // el stub lo trata como de 20 000 triángulos
    await page.locator("#stl").setInputFiles({ name: "pesado.stl", mimeType: "model/stl", buffer: heavy });
    await page.locator("#decimate").waitFor({ state: "visible" });
    await page.waitForFunction(() => document.querySelector("#stlinfo").textContent.includes("20000 triángulos"));
    await page.locator("#decimate").click();
    await page.waitForFunction(() => document.querySelector("#stlinfo").textContent.includes("reducido de 20000"));
    assert.equal(await page.locator("#decimate").isHidden(), true);
    await page.locator("#stl").setInputFiles({ name: "caja.stl", mimeType: "model/stl", buffer: boxStl() });   // vuelve al estado normal
    await page.waitForFunction(() => document.querySelector("#stlinfo").textContent.includes("12 triángulos"));
    assert.equal(await page.locator("#decimate").isHidden(), true);
  });

  await step("«Generar» sigue desactivado hasta cargar el proyecto; un JSON inválido se avisa", async () => {
    assert.equal(await page.locator("#build").isDisabled(), true);
    await page.locator("#proj").setInputFiles({ name: "malo.proyecto", mimeType: "application/json", buffer: Buffer.from("{no es json") });
    await page.waitForFunction(() => document.querySelector("#log").textContent.includes("no es un JSON válido"));
    assert.equal(await page.locator("#build").isDisabled(), true);
    await page.locator("#proj").setInputFiles({ name: "fig.proyecto", mimeType: "application/json", buffer: Buffer.from(PROJECT) });
    await page.waitForFunction(() => !document.querySelector("#build").disabled);
  });

  await step("con una acción en curso «Generar» queda bloqueado y dice por qué; las marcas de etapa salen en «Generar»", async () => {
    const heavy = Buffer.concat([boxStl(), Buffer.alloc(6000, 32)]);
    await page.locator("#stl").setInputFiles({ name: "pesado.stl", mimeType: "model/stl", buffer: heavy });
    await page.waitForFunction(() => document.querySelector("#buildhint").textContent.includes("acción en curso"), null, { timeout: 2000 });
    assert.equal(await page.locator("#build").isDisabled(), true, "«Generar» no debe estar habilitado mientras carga el STL");
    assert.equal(await page.locator("#demo").isDisabled(), true, "la demo sí se bloquea durante una acción");
    assert.equal(await page.locator("#check-update").isDisabled(), false, "«Buscar actualización» nunca se deshabilita, ni con una acción en curso");
    // el proyecto se puede elegir durante la carga (solo guarda datos) pero «Generar» espera; otro STL se ignora con aviso
    await page.locator("#proj").setInputFiles({ name: "fig2.proyecto", mimeType: "application/json", buffer: Buffer.from(PROJECT) });
    await page.locator("#stl").setInputFiles({ name: "otro.stl", mimeType: "model/stl", buffer: boxStl() });
    await page.waitForFunction(() => document.querySelector("#log").textContent.includes("otra acción en curso"));
    assert.equal(await page.locator("#build").isDisabled(), true);
    await page.waitForFunction(() => !document.querySelector("#build").disabled);                          // terminó la carga
    assert.equal(await text("#buildhint"), "");
    assert.match(await text("#stlinfo"), /20000 triángulos/);
    await page.locator("#build").click();
    await page.waitForFunction(() => document.querySelector("#log").textContent.includes("Listo en"));
    const lg = await text("#log");
    assert.match(lg, /^\+[\d.]+ s {2}Separando la cabeza/m, "faltan las marcas de etapa en el log de «Generar»");
    assert.equal(lg.match(/Listo en/g).length, 1, "una sola acción en el log");
    await page.locator("#stl").setInputFiles({ name: "caja.stl", mimeType: "model/stl", buffer: boxStl() });   // vuelve al estado normal
    await page.waitForFunction(() => document.querySelector("#stlinfo").textContent.includes("12 triángulos"));
    await page.waitForFunction(() => !document.querySelector("#build").disabled);
  });

  await step("«Buscar actualización» se puede pulsar varias veces; botones que el navegador restauró apagados se rehabilitan", async () => {
    await page.waitForFunction(() => !document.querySelector("#demo").disabled);
    for (let i = 0; i < 3; i++) {
      await page.click("#check-update");
      await page.waitForFunction(() => document.querySelector("#update-status").textContent.includes("No se pudo comprobar"));
      assert.equal(await page.locator("#check-update").isDisabled(), false, `tras el clic ${i + 1}`);
      await page.evaluate(() => { document.querySelector("#update-status").textContent = ""; });
    }
    // Firefox conserva `disabled` al recargar una página que estaba ocupada: se simula y se dispara `pageshow`
    await page.evaluate(() => { for (const id of ["#check-update", "#demo", "#decimate", "#pf-export"]) document.querySelector(id).disabled = true; window.dispatchEvent(new Event("pageshow")); });
    assert.equal(await page.locator("#check-update").isDisabled(), false);
    assert.equal(await page.locator("#demo").isDisabled(), false);
    assert.equal(await page.locator("#decimate").isDisabled(), false);
  });

  await step("tras «Reducir» se puede generar, con el proyecto cargado antes o después", async () => {
    const heavy = Buffer.concat([boxStl(), Buffer.alloc(6000, 32)]);
    const reducir = async () => {
      await page.locator("#decimate").waitFor({ state: "visible" });
      await page.locator("#decimate").click();
      await page.waitForFunction(() => document.querySelector("#stlinfo").textContent.includes("reducido de"));
      await page.waitForFunction(() => !document.querySelector("#demo").disabled);
    };
    // 1) proyecto ya cargado → STL pesado → Reducir → Generar habilitado
    await page.locator("#stl").setInputFiles({ name: "pesado.stl", mimeType: "model/stl", buffer: heavy });
    await reducir();
    await page.waitForFunction(() => !document.querySelector("#build").disabled);
    assert.equal(await text("#buildhint"), "");
    await page.locator("#build").click();
    await page.waitForFunction(() => document.querySelector("#log").textContent.includes("Listo en"));
    // 2) STL pesado → Reducir → recién después el proyecto → Generar habilitado
    await page.reload();
    await page.locator("#stl").setInputFiles({ name: "pesado.stl", mimeType: "model/stl", buffer: heavy });
    await reducir();
    assert.equal(await page.locator("#build").isDisabled(), true);
    assert.match(await text("#buildhint"), /falta cargar el proyecto/);
    await page.locator("#proj").setInputFiles({ name: "fig.proyecto", mimeType: "application/json", buffer: Buffer.from(PROJECT) });
    await page.waitForFunction(() => !document.querySelector("#build").disabled);
    await page.locator("#stl").setInputFiles({ name: "caja.stl", mimeType: "model/stl", buffer: boxStl() });   // vuelve al estado normal
    await page.waitForFunction(() => document.querySelector("#stlinfo").textContent.includes("12 triángulos"));
  });

  await step("archivos que el navegador restaura al recargar (sin evento `change`) se toman igual", async () => {
    await page.reload();
    assert.match(await text("#buildhint"), /falta cargar el STL/);
    // se simula la restauración de Firefox: los archivos quedan en los <input> sin que se dispare `change`
    await page.evaluate(([stl, proj]) => {
      const set = (sel, name, body) => { const dt = new DataTransfer(); dt.items.add(new File([body], name)); document.querySelector(sel).files = dt.files; };
      set("#stl", "caja.stl", stl); set("#proj", "fig.proyecto", proj);
      window.dispatchEvent(new Event("pageshow"));
    }, [boxStl().toString("latin1"), PROJECT]);
    await page.waitForFunction(() => document.querySelector("#stlinfo").textContent.includes("12 triángulos"));
    await page.waitForFunction(() => !document.querySelector("#build").disabled);
    assert.equal(await text("#buildhint"), "");
  });

  await step("los botones de vista cambian la cámara (la imagen cambia)", async () => {
    const front = await (await page.locator("#canvas").screenshot()).toString("base64");
    await page.click('[data-view="right"]'); await page.waitForTimeout(200);
    const right = await (await page.locator("#canvas").screenshot()).toString("base64");
    assert.notEqual(front, right);
    await page.click('[data-view="front"]'); await page.waitForTimeout(200);
  });

  await step("plano de simetría: 3 clics sobre la figura marcan puntos y definen el plano", async () => {
    await page.click("#pick-sym");
    await clickAt("#canvas", 0.42, 0.3); await page.waitForFunction(() => document.querySelector("#pickinfo").textContent.startsWith("Punto 1 de 3"));
    await clickAt("#canvas", 0.58, 0.3); await page.waitForFunction(() => document.querySelector("#pickinfo").textContent.startsWith("Punto 2 de 3"));
    await clickAt("#canvas", 0.5, 0.75);
    await page.waitForFunction(() => document.querySelector("#pickinfo").textContent.includes("Plano de simetría definido"));
  });

  await step("un arrastre (órbita) no cuenta como clic", async () => {
    await page.click("#pick-crown");
    const a = await canvasPoint("#canvas", 0.4, 0.4);
    await page.mouse.move(a.x, a.y); await page.mouse.down(); await page.mouse.move(a.x + 40, a.y + 5, { steps: 4 }); await page.mouse.up();
    assert.doesNotMatch(await text("#pickinfo"), /Coronilla en Z/);
    await clickAt("#canvas", 0.5, 0.2);
    await page.waitForFunction(() => /Coronilla en Z = /.test(document.querySelector("#pickinfo").textContent));
    await page.click('[data-view="front"]'); await page.waitForTimeout(200);
  });

  await step("tres clics alineados dan un error legible y no definen el plano", async () => {
    await page.click("#pick-waist");
    for (const fy of [0.3, 0.5, 0.7]) await clickAt("#canvas", 0.5, fy);
    await page.waitForFunction(() => document.querySelector("#pickinfo").textContent.includes("colineales"));
  });

  await step("planos del brazo y de la pierna; el plano se dibuja (la imagen cambia)", async () => {
    const before = await (await page.locator("#canvas").screenshot()).toString("base64");
    await page.click("#pick-arm");
    for (const [fx, fy] of [[0.4, 0.3], [0.6, 0.3], [0.5, 0.6]]) await clickAt("#canvas", fx, fy);
    await page.waitForFunction(() => document.querySelector("#pickinfo").textContent.includes("Plano del brazo definido"));
    await page.click("#pick-leg");
    for (const [fx, fy] of [[0.4, 0.65], [0.6, 0.65], [0.5, 0.45]]) await clickAt("#canvas", fx, fy);
    await page.waitForFunction(() => document.querySelector("#pickinfo").textContent.includes("Plano del pierna definido"));
    await page.waitForTimeout(200);
    assert.notEqual(before, await (await page.locator("#canvas").screenshot()).toString("base64"));
  });

  await step("editor de siluetas: abre con la propuesta del motor, agrega un vértice, rechaza un polígono cruzado y guarda", async () => {
    assert.equal(await page.locator("#siluetas").isVisible(), true);
    await page.selectOption("#front", "-Y");     // el frente mueve el marco de la figura: se fija ANTES de guardar la silueta
    assert.deepEqual(await page.locator("#sil-limb option").allInnerTexts(), ["brazo", "pierna"]);
    await page.click("#sil-open");
    await page.locator("#sil-editor").waitFor({ state: "visible" });
    await page.waitForFunction(() => document.querySelector("#sil-info").textContent.includes("Silueta propuesta para «brazo»"));
    assert.ok(await shot("#sil-canvas") > 500, "el editor de siluetas está vacío");
    // Misma vista que el editor (encuadra la figura de fondo + el polígono): los vértices se ubican en pantalla desde acá.
    const bg = [[-5, -5], [30, -5], [30, 70], [-5, 70]], poly = [[0, 0], [20, 0], [20, 60], [0, 60]];
    const view = fitView(bounds([...bg, ...poly]), 640, 480);
    const at = (p) => { const [x, y] = toScreen(view, p); return [x / 640, y / 480]; };
    const drag = async (from, to) => {
      const a = await canvasPoint("#sil-canvas", ...at(from)), b = await canvasPoint("#sil-canvas", ...at(to));
      await page.mouse.move(a.x, a.y); await page.mouse.down(); await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2); await page.mouse.move(b.x, b.y); await page.mouse.up();
    };
    // 1) un clic sobre una arista agrega un vértice
    await clickAt("#sil-canvas", ...at([10, 1.5]));
    await page.waitForFunction(() => document.querySelector("#sil-info").textContent.includes("5 vértices"));
    await page.click("#sil-undo");
    await page.waitForFunction(() => document.querySelector("#sil-info").textContent.includes("4 vértices"));
    // 2) arrastrar el vértice (20, 0) a (10, 66) cruza dos aristas: no se puede guardar
    await drag([20, 0], [10, 66]);
    await page.waitForFunction(() => document.querySelector("#sil-info").textContent.includes("se cruza"));
    await page.click("#sil-save");
    assert.match(await text("#sil-info"), /se cruza/);
    assert.equal(await page.locator("#sil-editor").isVisible(), true, "un polígono inválido no debe cerrar el editor");
    // 3) deshacer el movimiento y guardar
    await page.click("#sil-undo");
    await page.waitForFunction(() => !document.querySelector("#sil-info").textContent.includes("se cruza"));
    await page.click("#sil-save");
    await page.locator("#sil-editor").waitFor({ state: "hidden" });
    assert.match(await text("#sil-info"), /Silueta de «brazo» guardada \(4 vértices\)/);
    assert.deepEqual(await page.locator("#sil-limb option").allInnerTexts(), ["brazo (corregida)", "pierna"]);
  });

  await step("«Atenuar una mitad» recorre no → lado + → lado − → no", async () => {
    const t = [];
    for (let i = 0; i < 3; i++) { await page.click("#half"); t.push(await text("#half")); }
    assert.deepEqual(t, ["Atenuar una mitad: lado +", "Atenuar una mitad: lado −", "Atenuar una mitad: no"]);
  });

  await step("«Generar» manda al motor el proyecto con simetría, coronilla y miembros elegidos en el visor", async () => {
    await page.click("#front");  // select
    await page.selectOption("#front", "-Y");
    await page.click("#build");
    await page.waitForFunction(() => document.querySelector("#result") && !document.querySelector("#result").hidden);
    assert.match(await text("#download"), /Descargar ZIP \(2 archivos/);
    const rep = await text("#report");
    assert.match(rep, /✓ validación correcta/);
    let proj = JSON.parse(await page.evaluate(() => document.querySelector("#report").textContent.replace(/^[^\n]*\n/, ""))).proyecto;
    assert.equal(typeof proj, "string");
    proj = JSON.parse(proj);
    assert.ok(Math.abs(proj.symmetry.normal[1]) > 0.999, "la normal del plano de simetría debía ser ±Y (3 puntos sobre la cara frontal)");
    assert.equal(proj.front, "-Y");
    assert.ok(typeof proj.crown_z === "number");
    assert.deepEqual(proj.limbs.map((l) => l.name).sort(), ["brazo", "pierna"]);
    assert.ok(proj.limbs.every((l) => l.stub));
    const arm = proj.limbs.find((l) => l.name === "brazo");
    assert.deepEqual(arm.polygon, [[0, 0, 0], [20, 0, 0], [20, 0, 60], [0, 0, 60]], "la silueta corregida llega al proyecto en 3D sobre el plano");
    assert.equal(arm.auto, false);
    assert.equal(proj.limbs.find((l) => l.name === "pierna").polygon, undefined, "la pierna no se editó: sigue sin polígono (la propone el motor)");
    assert.match(await text("#log"), /Separando la cabeza/);
  });

  await step("tras generar aparece la pose: segundo visor dibujado y slider que llama al motor", async () => {
    await page.locator("#pose").waitFor({ state: "visible" });
    await page.waitForFunction(() => document.querySelector("#poseinfo").textContent.includes("Sin choques a 90°"));
    await page.waitForTimeout(300);
    assert.ok(await shot("#pose-canvas") > 200, "el visor de la pose está vacío");
    await page.locator("#angle").fill("95");
    await page.waitForFunction(() => document.querySelector("#poseinfo").textContent.includes("Choque con el torso a 95°: pierna_izq (1.5 mm³)"));
    assert.equal(await text("#angle-out"), "95°");
    await page.locator("#angle").fill("30");
    await page.waitForFunction(() => document.querySelector("#poseinfo").textContent.includes("Sin choques a 30°"));
    for (const v of ["right", "back", "top"]) await page.click(`[data-pview="${v}"]`);
  });

  await step("una silueta guardada se descarta (con aviso) si después cambia el marco de la figura (el frente)", async () => {
    await page.selectOption("#front", "+Y");
    await page.click("#build");
    await page.waitForFunction(() => document.querySelector("#result") && !document.querySelector("#result").hidden);
    assert.match(await text("#log"), /la silueta de «brazo» se descartó/);
    const proj = JSON.parse(JSON.parse(await page.evaluate(() => document.querySelector("#report").textContent.replace(/^[^\n]*\n/, ""))).proyecto);
    assert.equal(proj.front, "+Y");
    assert.equal(proj.limbs.find((l) => l.name === "brazo").polygon, undefined, "sin la silueta descartada, el brazo vuelve a quedar sin polígono");
    assert.deepEqual(await page.locator("#sil-limb option").allInnerTexts(), ["brazo", "pierna"]);
  });

  await step("la demo funciona sin cargar ningún archivo", async () => {
    const p2 = await (await browser.newContext()).newPage();
    await p2.route("**/worker.js", (r) => r.fulfill({ contentType: "text/javascript", body: STUB_WORKER }));
    await p2.route(/\/(version\.json|build-info\.js)/, (r) => r.fulfill({ status: 404, body: "" }));
    await p2.route(/cdn\.jsdelivr\.net\/npm\/three@[^/]+\/(.*)$/, (r) => {
      const f = path.join(process.env.THREE_DIR ?? "", r.request().url().match(/three@[^/]+\/(.*)$/)[1]);
      return process.env.THREE_DIR && fs.existsSync(f) ? r.fulfill({ contentType: "text/javascript", body: fs.readFileSync(f) }) : r.continue();
    });
    await p2.goto(base);
    await p2.click("#demo");
    await p2.waitForFunction(() => !document.querySelector("#result").hidden);
    assert.match(await p2.locator("#report").innerText(), /✓ validación correcta/);
    await p2.close();
  });

  // ---- el worker falla: la página tiene que decirlo y no quedar «ocupada» para siempre ----
  const pageWith = async (workerBody) => {
    const pg = await (await browser.newContext()).newPage();
    pg.setDefaultTimeout(8000);
    await pg.route("**/worker.js", (r) => r.fulfill({ contentType: "text/javascript", body: workerBody }));
    await pg.route(/\/(version\.json|build-info\.js)/, (r) => r.fulfill({ status: 404, body: "" }));
    await pg.route(/cdn\.jsdelivr\.net\/npm\/three@[^/]+\/(.*)$/, (r) => {
      const f = path.join(process.env.THREE_DIR ?? "", r.request().url().match(/three@[^/]+\/(.*)$/)[1]);
      return process.env.THREE_DIR && fs.existsSync(f) ? r.fulfill({ contentType: "text/javascript", body: fs.readFileSync(f) }) : r.continue();
    });
    pg.errs = [];
    pg.on("pageerror", (e) => pg.errs.push("pageerror: " + e.message));
    await pg.goto(base);
    return pg;
  };
  // Espera una condición y, si no se cumple, dice qué mostraba ESA página (el log y los errores), no la principal.
  const waitOn = async (pg, fn, what) => {
    try { await pg.waitForFunction(fn); }
    catch (e) { throw new Error(`${what}: no ocurrió. log de la página: ${JSON.stringify(await pg.locator("#log").innerText())} | errores: ${JSON.stringify(pg.errs)}`); }
  };

  // ---------- Perfiles de línea (§8.12) ----------
  // Un contexto propio (localStorage limpio) que se conserva entre pasos; `goto` de nuevo = recargar la página.
  const pfCtx = await browser.newContext({ acceptDownloads: true });
  const pf = await pfCtx.newPage();
  const pfErrors = [];
  pf.on("pageerror", (e) => pfErrors.push(e.message));
  await pf.route("**/worker.js", (r) => r.fulfill({ contentType: "text/javascript", body: STUB_WORKER }));
  await pf.route(/\/(version\.json|build-info\.js)/, (r) => r.fulfill({ status: 404, body: "" }));
  await pf.route(/cdn\.jsdelivr\.net\/npm\/three@[^/]+\/(.*)$/, (r) => {
    const f = path.join(process.env.THREE_DIR ?? "", r.request().url().match(/three@[^/]+\/(.*)$/)[1]);
    return process.env.THREE_DIR && fs.existsSync(f) ? r.fulfill({ contentType: "text/javascript", body: fs.readFileSync(f) }) : r.continue();
  });
  pf.setDefaultTimeout(15000);
  const projFile = (params, name = "fig.proyecto") => ({ name, mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ version: 1, symmetry: null, crown_z: null, front: "+Y", limbs: [], ...(params ? { params } : {}) })) });
  const pfInfo = () => pf.locator("#pf-info").innerText();
  const LINEA = { ref_height: 130, gap: 0.5, pin: { head_d: 6.6 } };

  await step("perfiles: sin proyecto cargado no se puede guardar ni aplicar; sin perfiles no se exporta", async () => {
    await pf.goto(base);
    for (const id of ["#pf-save", "#pf-apply", "#pf-del", "#pf-export"]) assert.equal(await pf.locator(id).isDisabled(), true, id);
    assert.equal(await pf.locator("#pf-list option").count(), 0);
  });

  await step("perfiles: nombre vacío y proyecto sin parámetros dan un aviso legible y no guardan nada", async () => {
    await pf.locator("#proj").setInputFiles(projFile(null));
    await pf.waitForFunction(() => !document.querySelector("#pf-save").disabled);
    await pf.click("#pf-save");
    assert.match(await pfInfo(), /necesita un nombre/);
    await pf.fill("#pf-name", "Sin params"); await pf.click("#pf-save");
    assert.match(await pfInfo(), /no tiene parámetros/);
    assert.equal(await pf.locator("#pf-list option").count(), 0);
  });

  await step("perfiles: guardar los parámetros del proyecto cargado; aparece en la lista y en localStorage", async () => {
    await pf.locator("#proj").setInputFiles(projFile(LINEA, "heroes.proyecto"));
    await pf.fill("#pf-name", "  Héroes   13 cm "); await pf.click("#pf-save");
    assert.match(await pfInfo(), /Perfil «Héroes 13 cm» guardado \(3 parámetros\)/);
    assert.deepEqual(await pf.locator("#pf-list option").allInnerTexts(), ["Héroes 13 cm"]);
    const saved = JSON.parse(await pf.evaluate(() => localStorage.getItem("afm.perfiles.v1")));
    assert.deepEqual(saved["Héroes 13 cm"], LINEA);
    await pf.click("#pf-save");   // mismo nombre: se actualiza
    assert.match(await pfInfo(), /actualizado/);
  });

  await step("perfiles: aplicado a otro proyecto, «Generar» manda al motor los parámetros del perfil y el resto intacto", async () => {
    await pf.locator("#proj").setInputFiles(projFile({ ref_height: 100, gap: 0.3, margin: 5 }, "otro.proyecto"));
    await pf.locator("#stl").setInputFiles({ name: "caja.stl", mimeType: "model/stl", buffer: boxStl() });
    await pf.waitForFunction(() => document.querySelector("#stlinfo").textContent.includes("12 triángulos"));
    await pf.click("#pf-apply");
    assert.match(await pfInfo(), /Perfil «Héroes 13 cm» aplicado .*altura de referencia 130 mm/);
    await pf.waitForFunction(() => !document.querySelector("#build").disabled);
    await pf.click("#build");
    await pf.waitForFunction(() => !document.querySelector("#result").hidden);
    const proj = JSON.parse(JSON.parse(await pf.evaluate(() => document.querySelector("#report").textContent.replace(/^[^\n]*\n/, ""))).proyecto);
    assert.deepEqual(proj.params, LINEA, "los params son exactamente los del perfil (no mezclados con los del proyecto)");
    assert.equal(proj.front, "+Y");
  });

  await step("perfiles: sobreviven a recargar la página", async () => {
    await pf.goto(base);
    assert.deepEqual(await pf.locator("#pf-list option").allInnerTexts(), ["Héroes 13 cm"]);
    assert.equal(await pf.locator("#pf-export").isDisabled(), false);
    assert.equal(await pf.locator("#pf-apply").isDisabled(), true, "sin proyecto cargado no se puede aplicar");
  });

  await step("perfiles: exportar da un JSON con formato; eliminar vacía la lista; importar lo devuelve", async () => {
    const [dl] = await Promise.all([pf.waitForEvent("download"), pf.click("#pf-export")]);
    assert.equal(dl.suggestedFilename(), "perfiles-de-linea.json");
    const file = JSON.parse(fs.readFileSync(await dl.path(), "utf8"));
    assert.equal(file.format, "afm-perfiles"); assert.deepEqual(file.profiles["Héroes 13 cm"], LINEA);
    await pf.click("#pf-del");
    assert.match(await pfInfo(), /eliminado/);
    assert.equal(await pf.locator("#pf-list option").count(), 0);
    assert.equal(await pf.locator("#pf-export").isDisabled(), true);
    assert.deepEqual(JSON.parse(await pf.evaluate(() => localStorage.getItem("afm.perfiles.v1"))), {});
    await pf.locator("#pf-import").setInputFiles({ name: "perfiles.json", mimeType: "application/json", buffer: fs.readFileSync(await dl.path()) });
    await pf.waitForFunction(() => document.querySelector("#pf-list").options.length === 1);
    assert.match(await pfInfo(), /Importados: 1 nuevos, 0 reemplazados/);
    // el mismo archivo otra vez: se reemplaza, no se duplica
    await pf.locator("#pf-import").setInputFiles({ name: "perfiles.json", mimeType: "application/json", buffer: fs.readFileSync(await dl.path()) });
    await pf.waitForFunction(() => document.querySelector("#pf-info").textContent.includes("0 nuevos, 1 reemplazados"));
    assert.equal(await pf.locator("#pf-list option").count(), 1);
  });

  await step("perfiles: un archivo que no es de perfiles se rechaza con un mensaje y no toca los guardados", async () => {
    await pf.locator("#pf-import").setInputFiles({ name: "malo.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ hola: 1 })) });
    await pf.waitForFunction(() => document.querySelector("#pf-info").textContent.includes("No es un archivo de perfiles"));
    await pf.locator("#pf-import").setInputFiles({ name: "roto.json", mimeType: "application/json", buffer: Buffer.from("{no") });
    await pf.waitForFunction(() => document.querySelector("#pf-info").textContent.includes("no es un JSON válido"));
    assert.deepEqual(await pf.locator("#pf-list option").allInnerTexts(), ["Héroes 13 cm"]);
  });

  await step("perfiles: el linea.json de un catálogo de escritorio se importa como perfil (nombre y params de la línea)", async () => {
    const linea = { formato: "afm-linea-1", nombre: "proceres-127", altura_mm: 127, boquilla: 0.4, params: { ref_height: 127, nozzle: 0.4, gap: 0.5 }, lugares: ["brazo"], motor: "0.9" };
    await pf.locator("#pf-import").setInputFiles({ name: "linea.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(linea)) });
    await pf.waitForFunction(() => document.querySelector("#pf-info").textContent.includes("Importados: 1 nuevos"));
    assert.deepEqual(await pf.locator("#pf-list option").allInnerTexts(), ["Héroes 13 cm", "proceres-127"]);
    assert.deepEqual(JSON.parse(await pf.evaluate(() => localStorage.getItem("afm.perfiles.v1")))["proceres-127"], linea.params);
  });

  await step("perfiles: con localStorage bloqueado la página no se rompe y los perfiles viven en la sesión", async () => {
    const ctx = await browser.newContext();
    await ctx.addInitScript(() => { Object.defineProperty(window, "localStorage", { get() { throw new DOMException("bloqueado", "SecurityError"); } }); });
    const q = await ctx.newPage(); const qerr = [];
    q.on("pageerror", (e) => qerr.push(e.message));
    await q.route("**/worker.js", (r) => r.fulfill({ contentType: "text/javascript", body: STUB_WORKER }));
    await q.route(/\/(version\.json|build-info\.js)/, (r) => r.fulfill({ status: 404, body: "" }));
    await q.goto(base);
    await q.locator("#proj").setInputFiles(projFile(LINEA));
    await q.fill("#pf-name", "Solo sesión"); await q.click("#pf-save");
    assert.match(await q.locator("#pf-info").innerText(), /guardado/);
    assert.deepEqual(await q.locator("#pf-list option").allInnerTexts(), ["Solo sesión"]);
    assert.deepEqual(qerr, []);
    await ctx.close();
  });
  await step("perfiles: sin errores de JavaScript", async () => { assert.deepEqual(pfErrors, []); await pfCtx.close(); });

  await step("si el worker no carga, la página muestra el error y no queda ocupada", async () => {
    const pg = await pageWith('throw new Error("no se pudo cargar Pyodide (simulado)");');
    await pg.click("#demo");
    await waitOn(pg, () => { const e = document.querySelector("#workererror"); return e && !e.hidden; }, "el aviso del worker caído");
    const msg = await pg.locator("#workererror").innerText();
    assert.match(msg, /no se pudo cargar Pyodide \(simulado\)/);
    assert.match(msg, /Recarg/);                                              // dice qué hacer
    assert.equal(await pg.locator("#demo").isDisabled(), false, "«Probar con la demo» quedó bloqueado");
    await pg.click("#demo");                                                  // un segundo intento no se cuelga: falla al instante
    await waitOn(pg, () => document.querySelector("#log").textContent.includes("Error: "), "el error del segundo intento");
    assert.equal(await pg.locator("#demo").isDisabled(), false);
    assert.equal(await pg.locator("#result").isHidden(), true);
    await pg.close();
  });

  await step("un init que falla con un error normal se puede reintentar (no se memoriza el fallo)", async () => {
    const flaky = 'let intentos = 0;\n' + STUB_WORKER.replace('if (type === "init") return post("ok");',
      'if (type === "init") { if (intentos++ === 0) return self.postMessage({ id, type: "error", message: "fallo de red simulado" }); return post("ok"); }');
    assert.notEqual(flaky, 'let intentos = 0;\n' + STUB_WORKER, "el reemplazo del stub no se aplicó");
    const pg = await pageWith(flaky);
    await pg.click("#demo");
    await waitOn(pg, () => document.querySelector("#log").textContent.includes("Error: fallo de red simulado"), "el error de init en el log");
    assert.equal(await pg.locator("#demo").isDisabled(), false);
    assert.equal(await pg.locator("#workererror").isHidden(), true, "un error de init no es un worker caído");
    await pg.click("#demo");                                                  // el segundo intento sí carga el motor
    await waitOn(pg, () => !document.querySelector("#result").hidden, "el resultado del segundo intento");
    assert.match(await pg.locator("#report").innerText(), /✓ validación correcta/);
    await pg.close();
  });

  await step("si el worker muere a mitad de una acción, esa acción termina con error y la página se libera", async () => {
    const dies = STUB_WORKER.replace('if (type === "build" || type === "demo") {',
      'if (type === "demo") { self.postMessage({ type: "progress", stage: "Separando la cabeza…", pct: null }); setTimeout(() => { throw new Error("se cayó a mitad de la corrida (simulado)"); }, 80); return; }\n  if (type === "build" || type === "demo") {');
    assert.notEqual(dies, STUB_WORKER, "el reemplazo del stub no se aplicó");
    const pg = await pageWith(dies);
    await pg.click("#demo");
    await waitOn(pg, () => { const e = document.querySelector("#workererror"); return e && !e.hidden; }, "el aviso del worker caído a mitad de la corrida");
    assert.match(await pg.locator("#workererror").innerText(), /se cayó a mitad de la corrida \(simulado\)/);
    await waitOn(pg, () => document.querySelector("#log").textContent.includes("Error: El motor dejó de responder"), "el error de la acción en el log");
    assert.equal(await pg.locator("#demo").isDisabled(), false, "la página quedó ocupada");
    assert.equal(await pg.locator("#bar").isHidden(), true, "la barra de progreso quedó a medias");
    await pg.close();
  });

  await step("probetas de calibración: se elige el tipo, se generan y aparece la descarga con la tabla", async () => {
    const pg = await pageWith(STUB_WORKER);
    assert.equal(await pg.locator("#pb-boq-l").isHidden(), true);
    await pg.selectOption("#pb-tipo", "clic");
    assert.equal(await pg.locator("#pb-boq-l").isHidden(), false, "la boquilla solo aplica al tipo clic");
    await pg.fill("#pb-boq", "0.6");
    await pg.click("#pb-gen");
    await waitOn(pg, () => !document.querySelector("#pb-res").hidden, "la descarga de las probetas");
    assert.match(await pg.locator("#pb-txt").innerText(), /tipo=clic b=0\.6/);
    assert.match(await pg.locator("#pb-dl").innerText(), /2 archivos/);
    assert.match(await pg.locator("#pb-dl").getAttribute("href"), /^blob:/);
    assert.equal(await pg.locator("#pb-gen").isDisabled(), false);
    await pg.close();
  });

  await step("ningún error de JavaScript ni de consola durante toda la prueba", async () => {
    assert.deepEqual(errors.filter((e) => !/Failed to load resource.*404/.test(e)), []);
  });
} finally {
  await browser.close();
  server.close();
}
console.log(process.exitCode ? "browser_dom: FALLÓ" : `browser_dom: OK (${passed} pasos)`);
