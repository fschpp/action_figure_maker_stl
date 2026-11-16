// Prueba de navegador (Chrome headless + playwright-core) del DOM, el visor three.js, los clics y el slider de pose.
// El motor Python NO se carga: `worker.js` se reemplaza por un stub con el mismo protocolo (init, load_stl, frame, new_limb,
// new_head, new_waist, preview_pose, build, demo). Pyodide real ya lo cubre web/tests/engine_node.mjs; aquí se prueba
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
  if (type === "load_stl") return post({ triangulos: 12, alto_mm: 100, estanca: true, avisos: [], z_arriba: true });
  if (type === "frame") return post({ k: 1, offset: [0, 0, 0], ref_height: 100 });
  if (type === "new_limb") return post({ name: ev.data.name, kind: ev.data.kind, stub: true, points: ev.data.points });
  if (type === "new_head" || type === "new_waist") return post({ plane: { name: type, origin: ev.data.points[0], normal: [0, 0, 1] } });
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

  await step("«Generar» sigue desactivado hasta cargar el proyecto; un JSON inválido se avisa", async () => {
    assert.equal(await page.locator("#build").isDisabled(), true);
    await page.locator("#proj").setInputFiles({ name: "malo.proyecto", mimeType: "application/json", buffer: Buffer.from("{no es json") });
    await page.waitForFunction(() => document.querySelector("#log").textContent.includes("no es un JSON válido"));
    assert.equal(await page.locator("#build").isDisabled(), true);
    await page.locator("#proj").setInputFiles({ name: "fig.proyecto", mimeType: "application/json", buffer: Buffer.from(PROJECT) });
    await page.waitForFunction(() => !document.querySelector("#build").disabled);
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

  await step("ningún error de JavaScript ni de consola durante toda la prueba", async () => {
    assert.deepEqual(errors.filter((e) => !/Failed to load resource.*404/.test(e)), []);
  });
} finally {
  await browser.close();
  server.close();
}
console.log(process.exitCode ? "browser_dom: FALLÓ" : `browser_dom: OK (${passed} pasos)`);
