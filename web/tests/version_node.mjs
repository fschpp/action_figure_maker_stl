import assert from "node:assert/strict";
import { compareBuilds, formatBuild, shortId, fetchRemote, refreshFiles, initVersionUI } from "../version.js";

const A = { commit: "a".repeat(40), built_at: "2026-11-15T10:00:00+00:00" };
const B = { commit: "b".repeat(40), built_at: "2026-11-15T12:00:00+00:00" };
assert.equal(compareBuilds(A, A), "current");
assert.equal(compareBuilds(A, B), "outdated");
assert.equal(compareBuilds(B, A), "ahead");
assert.equal(compareBuilds(null, B), "unknown");
assert.equal(compareBuilds({ commit: "dev" }, B), "unknown");
assert.equal(shortId(A.commit), "aaaaaaa");
assert.match(formatBuild(A), /^aaaaaaa · publicada /);
assert.match(formatBuild(null), /desarrollo/);

// fetchRemote: pide con no-store y parámetro único; rechaza HTTP != 200 y JSON inválido
const calls = [];
const okFetch = async (u, o) => { calls.push([u, o]); return { ok: true, json: async () => ({ ...B, files: ["app.js"] }) }; };
const v = await fetchRemote("https://x/version.json", { fetchFn: okFetch, now: () => 123 });
assert.equal(v.commit, B.commit); assert.deepEqual(calls[0], ["https://x/version.json?t=123", { cache: "no-store" }]);
await assert.rejects(fetchRemote("u", { fetchFn: async () => ({ ok: false, status: 404 }) }), /HTTP 404/);
await assert.rejects(fetchRemote("u", { fetchFn: async () => ({ ok: true, json: async () => ({}) }) }), /inválido/);

// refreshFiles: cache "reload" en cada archivo, devuelve los que fallaron
const seen = [];
const rf = await refreshFiles(["./", "app.js", "wheels/x.whl"], { base: "https://s.io/p/app.js", fetchFn: async (u, o) => {
  seen.push([u, o.cache]); return u.endsWith("x.whl") ? { ok: false, status: 500 } : { ok: true, arrayBuffer: async () => new ArrayBuffer(1) }; } });
assert.deepEqual(seen.map((s) => s[0]), ["https://s.io/p/", "https://s.io/p/app.js", "https://s.io/p/wheels/x.whl"]);
assert.ok(seen.every((s) => s[1] === "reload")); assert.deepEqual(rf.failed, ["wheels/x.whl"]);

// UI con elementos falsos
const mk = () => { const h = {}; return { textContent: "", addEventListener: (_, f) => { h.click = f; }, click: () => h.click() }; };
const setup = (build, remote, extra = {}) => {
  const el = { label: mk(), button: mk(), status: mk() }; let reloaded = 0; const fetched = [];
  const fetchFn = async (u, o) => { fetched.push([u, o.cache]); if (u.includes("version.json")) { if (remote === "down") throw new Error("offline"); return { ok: true, json: async () => remote }; } return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) }; };
  const ui = initVersionUI({ el, build, versionUrl: "https://s.io/p/version.json", base: "https://s.io/p/app.js", fetchFn, reload: () => { reloaded++; }, ...extra });
  return { el, ui, fetched, get reloaded() { return reloaded; } };
};

let t = setup(A, { ...A, files: [] });
assert.match(t.el.label.textContent, /^Versión: aaaaaaa/);
assert.equal(await t.ui.check(), "current"); assert.match(t.el.status.textContent, /última versión/); assert.equal(t.el.button.textContent, "Buscar actualización");

t = setup(A, { ...B, files: ["app.js", "wheels/motor.whl"] });
assert.equal(await t.ui.check(), "outdated");
assert.match(t.el.status.textContent, /más nueva.*vieja/); assert.equal(t.el.button.textContent, "Actualizar ahora");
await t.el.button.click();                                   // en «outdated» el botón actualiza
await new Promise((r) => setTimeout(r, 10));
assert.equal(t.reloaded, 1);
assert.deepEqual(t.fetched.filter(([u]) => !u.includes("version.json")).map(([u, c]) => `${u} ${c}`), ["https://s.io/p/ reload", "https://s.io/p/app.js reload", "https://s.io/p/wheels/motor.whl reload"]);

t = setup(B, { ...A, files: [] }); assert.equal(await t.ui.check(), "ahead"); assert.match(t.el.status.textContent, /más nueva que la publicada/);
t = setup(null, B); assert.equal(await t.ui.check(), "unknown"); assert.match(t.el.label.textContent, /desarrollo/);
t = setup(A, "down"); assert.equal(await t.ui.check({ silent: true }), "error"); assert.equal(t.el.status.textContent, "");   // silencioso al cargar
assert.equal(await t.ui.check(), "error"); assert.match(t.el.status.textContent, /No se pudo comprobar/);                    // manual: avisa
// beforeReload: si la página no quiere recargar (acción en curso), la versión nueva queda descargada y no se recarga
t = setup(A, { ...B, files: ["app.js"] }, { beforeReload: () => false });
await t.ui.check(); await t.el.button.click(); await new Promise((r) => setTimeout(r, 10));
assert.equal(t.reloaded, 0); assert.match(t.el.status.textContent, /se aplica al recargar/); assert.equal(t.el.button.textContent, "Actualizar ahora");
t = setup(A, { ...B, files: ["app.js"] }, { beforeReload: () => true });
await t.ui.check(); await t.el.button.click(); await new Promise((r) => setTimeout(r, 10));
assert.equal(t.reloaded, 1);
console.log("version_node: OK");
