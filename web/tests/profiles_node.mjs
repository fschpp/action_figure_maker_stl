// Lógica pura de perfiles de línea (web/profiles.js): guardar, aplicar, exportar/importar y robustez ante almacenamiento roto.
import assert from "node:assert/strict";
import { cleanName, extractProfile, applyProfile, loadProfiles, storeProfiles, exportProfiles, importProfiles, STORAGE_KEY } from "../profiles.js";

const fake = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)), m }; };
const proj = (params, extra = {}) => JSON.stringify({ version: 1, symmetry: { normal: [1, 0, 0] }, crown_z: 98.5, limbs: [{ name: "brazo" }], ...(params ? { params } : {}), ...extra });
const P1 = { ref_height: 130, gap: 0.5, pin: { head_d: 6.6 } };

// nombres
assert.equal(cleanName("  Línea   Héroes "), "Línea Héroes");
assert.throws(() => cleanName("   "), /necesita un nombre/);
assert.throws(() => cleanName("x".repeat(61)), /demasiado largo/);

// extraer: copia profunda; sin params o JSON roto → mensaje legible
const got = extractProfile(proj(P1));
assert.deepEqual(got, P1);
got.pin.head_d = 99; assert.equal(JSON.parse(proj(P1)).params.pin.head_d, 6.6);
assert.throws(() => extractProfile(proj(null)), /no tiene parámetros/);
assert.throws(() => extractProfile(proj({})), /no tiene parámetros/);
assert.throws(() => extractProfile("{no"), /JSON válido/);

// aplicar: solo cambia `params`; el resto del proyecto queda igual
const target = proj({ ref_height: 100, gap: 0.3, margin: 5 });
const applied = JSON.parse(applyProfile(target, P1));
assert.deepEqual(applied.params, P1, "params reemplazados por completo (no mezclados)");
assert.deepEqual(applied.symmetry, { normal: [1, 0, 0] }); assert.equal(applied.crown_z, 98.5); assert.deepEqual(applied.limbs, [{ name: "brazo" }]);
assert.deepEqual(JSON.parse(applyProfile(proj(null), P1)).params, P1, "un proyecto sin params también la recibe");
assert.throws(() => applyProfile(target, null), /vacío o dañado/);
assert.throws(() => applyProfile("{no", P1), /JSON válido/);

// almacenamiento: ida y vuelta, y tolera basura / fallos
const st = fake();
assert.deepEqual(loadProfiles(st), {});
assert.equal(storeProfiles(st, { A: P1 }), true);
assert.deepEqual(loadProfiles(st), { A: P1 });
st.setItem(STORAGE_KEY, "{roto"); assert.deepEqual(loadProfiles(st), {});
st.setItem(STORAGE_KEY, JSON.stringify({ ok: { a: 1 }, mal: 5, lista: [1] })); assert.deepEqual(loadProfiles(st), { ok: { a: 1 } });
assert.deepEqual(loadProfiles({ getItem() { throw new Error("bloqueado"); } }), {});
assert.equal(storeProfiles({ setItem() { throw new Error("cuota"); } }, {}), false);

// exportar / importar
const txt = exportProfiles({ A: P1, B: { gap: 0.4 } });
assert.equal(JSON.parse(txt).format, "afm-perfiles");
let r = importProfiles(txt, {});
assert.deepEqual([r.added, r.replaced, r.skipped], [2, 0, 0]); assert.deepEqual(r.profiles.A, P1);
r = importProfiles(txt, { A: { viejo: 1 }, C: { c: 1 } });
assert.deepEqual([r.added, r.replaced], [1, 1]); assert.deepEqual(r.profiles.A, P1); assert.deepEqual(r.profiles.C, { c: 1 });
r = importProfiles(txt, { A: { viejo: 1 } }, { overwrite: false });
assert.deepEqual([r.added, r.replaced, r.skipped], [1, 0, 1]); assert.deepEqual(r.profiles.A, { viejo: 1 });
r = importProfiles(JSON.stringify({ format: "afm-perfiles", version: 1, profiles: { "": { a: 1 }, X: {}, Y: 3, Z: { z: 1 } } }), {});
assert.deepEqual([r.added, r.skipped], [1, 3]); assert.deepEqual(Object.keys(r.profiles), ["Z"]);
assert.throws(() => importProfiles("{no", {}), /JSON válido/);
assert.throws(() => importProfiles(JSON.stringify({ a: 1 }), {}), /perfiles de línea/);
assert.throws(() => importProfiles(JSON.stringify({ format: "otro", profiles: {} }), {}), /perfiles de línea/);
console.log("profiles_node: OK");
