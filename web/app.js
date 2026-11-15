const $ = (s) => document.querySelector(s);
const log = (t) => { $("#log").textContent += t + "\n"; };

// Worker de tipo module (import dinámico de pyodide.mjs); sin bundler, ver plan §9.
const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
let nextId = 1;
const pending = new Map();

worker.onmessage = ({ data }) => {
  if (data.type === "progress") {
    $("#bar").hidden = false; $("#bar").value = data.pct; $("#stage").textContent = data.stage;
    return;
  }
  const p = pending.get(data.id);
  if (!p) return;
  pending.delete(data.id);
  data.type === "error" ? p.reject(new Error(data.message)) : p.resolve(data.value);
};
worker.onerror = (e) => log("Error del worker: " + e.message);

const call = (type, extra = {}) => new Promise((resolve, reject) => {
  const id = nextId++;
  pending.set(id, { resolve, reject });
  worker.postMessage({ id, type, ...extra });
});

$("#run").addEventListener("click", async () => {
  $("#run").disabled = true;
  try {
    const t0 = performance.now();
    await call("init");
    log(`init: ${((performance.now() - t0) / 1000).toFixed(1)} s`);
    const r = await call("probe");
    log("Python: " + r.python);
    const tb = $("#report tbody"); tb.replaceChildren();
    for (const d of r.deps) {
      const tr = document.createElement("tr");
      for (const [txt, cls] of [[d.name], [d.ok ? "OK" : "FALLA", d.ok ? "ok" : "fail"], [d.detail]]) {
        const td = document.createElement("td"); td.textContent = txt; if (cls) td.className = cls; tr.append(td);
      }
      tb.append(tr);
    }
    $("#report").hidden = false;
  } catch (e) { log("Error: " + e.message); }
  finally { $("#run").disabled = false; }
});
