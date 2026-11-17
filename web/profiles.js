// Perfiles de línea (plan §8.12): los parámetros comunes a todos los personajes de una línea (altura de referencia,
// holgura, pasadores, pose, margen…) son el bloque `params` del proyecto. Un perfil guarda ese bloque con un nombre en el
// navegador (localStorage) y se puede exportar/importar como archivo para pasarlo entre equipos. Sin DOM: se prueba en Node.
export const STORAGE_KEY = "afm.perfiles.v1";
export const FILE_FORMAT = "afm-perfiles";

const isObj = (x) => x !== null && typeof x === "object" && !Array.isArray(x);

export function cleanName(name) {
  const n = String(name ?? "").replace(/\s+/g, " ").trim();
  if (!n) throw new Error("El perfil necesita un nombre.");
  if (n.length > 60) throw new Error("El nombre del perfil es demasiado largo (máximo 60 caracteres).");
  return n;
}

/** Bloque `params` de un proyecto (texto JSON). Falla con un mensaje legible si no tiene. */
export function extractProfile(projText) {
  let p;
  try { p = JSON.parse(projText); } catch { throw new Error("El proyecto no es un JSON válido."); }
  if (!isObj(p?.params) || Object.keys(p.params).length === 0) throw new Error("El proyecto no tiene parámetros (`params`) para guardar.");
  return structuredClone(p.params);
}

/** Proyecto con los `params` del perfil en lugar de los suyos; planos, miembros y demás quedan como estaban. */
export function applyProfile(projText, params) {
  let p;
  try { p = JSON.parse(projText); } catch { throw new Error("El proyecto no es un JSON válido."); }
  if (!isObj(params)) throw new Error("El perfil está vacío o dañado.");
  return JSON.stringify({ ...p, params: structuredClone(params) });
}

/** Perfiles guardados: { nombre: params }. Si el almacenamiento no existe, falla o trae basura, devuelve {}. */
export function loadProfiles(storage) {
  try {
    const raw = JSON.parse(storage.getItem(STORAGE_KEY) ?? "{}");
    return isObj(raw) ? Object.fromEntries(Object.entries(raw).filter(([, v]) => isObj(v))) : {};
  } catch { return {}; }
}

/** Guarda todo el conjunto. Devuelve false si el navegador no deja escribir (modo privado, cuota…). */
export function storeProfiles(storage, profiles) {
  try { storage.setItem(STORAGE_KEY, JSON.stringify(profiles)); return true; } catch { return false; }
}

export function exportProfiles(profiles) {
  return JSON.stringify({ format: FILE_FORMAT, version: 1, profiles }, null, 2) + "\n";
}

/**
 * Lee un archivo de perfiles y lo junta con los existentes. Con un nombre repetido gana el importado si `overwrite`
 * (por defecto), si no se conserva el que ya estaba. Devuelve { profiles, added, replaced, skipped }.
 */
export function importProfiles(text, existing, { overwrite = true } = {}) {
  let doc;
  try { doc = JSON.parse(text); } catch { throw new Error("El archivo no es un JSON válido."); }
  if (doc?.format !== FILE_FORMAT || !isObj(doc.profiles)) throw new Error("No es un archivo de perfiles de línea.");
  const out = { ...existing };
  let added = 0, replaced = 0, skipped = 0;
  for (const [rawName, params] of Object.entries(doc.profiles)) {
    let name;
    try { name = cleanName(rawName); } catch { skipped++; continue; }
    if (!isObj(params) || Object.keys(params).length === 0) { skipped++; continue; }
    if (name in out) { if (!overwrite) { skipped++; continue; } replaced++; } else added++;
    out[name] = structuredClone(params);
  }
  return { profiles: out, added, replaced, skipped };
}
