// ¿Estoy usando la última versión publicada o una vieja guardada en la caché del navegador?
//
// - build-info.js (generado por scripts/build_web.sh) dice qué versión se cargó en ESTA pestaña.
// - version.json (generado en el mismo build) dice qué versión está publicada AHORA; se pide salteando la caché.
// Si difieren, «Actualizar ahora» vuelve a bajar los archivos salteando la caché y recarga la página.

export const shortId = (commit) => (commit && commit !== "dev" ? String(commit).slice(0, 7) : "dev");

export function formatBuild(b) {
  if (!b || !b.commit) return "versión de desarrollo (sin datos de compilación)";
  const t = b.built_at ? new Date(b.built_at) : null;
  const when = t && !Number.isNaN(t.getTime()) ? t.toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" }) : "fecha desconocida";
  return `${shortId(b.commit)} · publicada ${when}`;
}

// "unknown" (no hay con qué comparar) | "current" | "outdated" | "ahead" (la copia local es más nueva que la publicada)
export function compareBuilds(local, remote) {
  if (!local || !remote || !local.commit || !remote.commit || local.commit === "dev") return "unknown";
  if (local.commit === remote.commit) return "current";
  const l = Date.parse(local.built_at), r = Date.parse(remote.built_at);
  return Number.isFinite(l) && Number.isFinite(r) && l > r ? "ahead" : "outdated";
}

// Pide version.json salteando la caché del navegador (no-store) y la de la CDN (parámetro único).
export async function fetchRemote(url, { fetchFn = fetch, now = Date.now } = {}) {
  const r = await fetchFn(`${url}?t=${now()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const v = await r.json();
  if (!v || typeof v.commit !== "string") throw new Error("version.json inválido");
  return v;
}

// Vuelve a bajar cada archivo salteando la caché HTTP (cache: "reload" también la deja actualizada).
export async function refreshFiles(files, { fetchFn = fetch, base } = {}) {
  const res = await Promise.allSettled(files.map(async (f) => {
    const r = await fetchFn(new URL(f, base).href, { cache: "reload" });
    if (!r.ok) throw new Error(`${f}: HTTP ${r.status}`);
    await r.arrayBuffer();     // consumir el cuerpo para que la entrada quede completa en la caché
  }));
  return { failed: res.map((x, i) => (x.status === "rejected" ? files[i] : null)).filter(Boolean) };
}

// Conecta el label y el botón. `el` = {label, button, status} (cualquier objeto con textContent / addEventListener).
export function initVersionUI({ el, build, versionUrl, base, fetchFn = fetch, reload = () => location.reload() }) {
  let remote = null, state = "unknown";
  const render = (status, btnText = "Buscar actualización") => { el.status.textContent = status; el.button.textContent = btnText; };
  el.label.textContent = `Versión: ${formatBuild(build)}`;

  async function check({ silent = false } = {}) {
    render("Comprobando…");
    try {
      remote = await fetchRemote(versionUrl, { fetchFn });
    } catch (e) {
      state = "error";
      render(silent ? "" : `No se pudo comprobar la versión publicada (${e.message}). ¿Sin conexión?`);
      return state;
    }
    state = compareBuilds(build, remote);
    if (state === "current") render("✓ Estás usando la última versión.");
    else if (state === "outdated") render(`⚠ Hay una versión más nueva (${formatBuild(remote)}). Estás usando una vieja guardada en la caché.`, "Actualizar ahora");
    else if (state === "ahead") render("Tu copia es más nueva que la publicada (puede que el sitio se esté actualizando todavía).");
    else render("Versión de desarrollo: no hay con qué compararla.");
    return state;
  }

  async function update() {
    render("Descargando la versión nueva…", "Actualizar ahora");
    const { failed } = await refreshFiles(["./", ...(remote?.files ?? [])], { fetchFn, base });
    if (failed.length) el.status.textContent = `No se pudieron actualizar: ${failed.join(", ")}. Se recarga igual.`;
    reload();
  }

  el.button.addEventListener("click", () => (state === "outdated" ? update() : check()));
  return { check, update, get state() { return state; } };
}
