// Editor 2D de siluetas (plan del port, M3). Dibuja la figura proyectada sobre el plano de un miembro y deja editar el
// polígono que lo envuelve: clic = agregar un vértice, arrastrar = moverlo, doble clic o clic derecho = quitarlo.
// Coordenadas del modelo: (u, v) del plano, en mm; la pantalla tiene el eje y hacia abajo.
// La parte pura (sin DOM) se prueba en Node: web/tests/polygon_node.mjs.

export const VERTEX_R = 8;          // px: radio para agarrar un vértice
export const SNAP_R = 8;            // px: radio para imantarse a un vértice de la figura

export function bounds(points) {
  let u0 = Infinity, v0 = Infinity, u1 = -Infinity, v1 = -Infinity;
  for (const [u, v] of points) { u0 = Math.min(u0, u); v0 = Math.min(v0, v); u1 = Math.max(u1, u); v1 = Math.max(v1, v); }
  return Number.isFinite(u0) ? { u0, v0, u1, v1 } : null;
}

// Vista: pantalla = (ox + u·s, oy − v·s). `fitView` encuadra una caja con margen.
export function fitView(box, w, h, margin = 24) {
  const bw = Math.max(box.u1 - box.u0, 1e-6), bh = Math.max(box.v1 - box.v0, 1e-6);
  const s = Math.min((w - 2 * margin) / bw, (h - 2 * margin) / bh);
  return { s, ox: (w - s * (box.u0 + box.u1)) / 2, oy: (h + s * (box.v0 + box.v1)) / 2 };
}
export const toScreen = (view, [u, v]) => [view.ox + u * view.s, view.oy - v * view.s];
export const toModel = (view, [x, y]) => [(x - view.ox) / view.s, (view.oy - y) / view.s];

// Zoom con el punto bajo el cursor fijo.
export function zoomAt(view, [x, y], factor, limits = [0.2, 400]) {
  const s = Math.min(limits[1], Math.max(limits[0], view.s * factor)), k = s / view.s;
  return { s, ox: x - (x - view.ox) * k, oy: y - (y - view.oy) * k };
}

const dist2 = (a, b) => (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2;

// Índice del vértice del polígono más cercano al punto de pantalla `s` (dentro de `r` px), o -1.
export function hitVertex(poly, view, s, r = VERTEX_R) {
  let best = -1, bd = r * r;
  poly.forEach((p, i) => { const d = dist2(toScreen(view, p), s); if (d <= bd) { bd = d; best = i; } });
  return best;
}

// Arista más cercana (de i a i+1, cerrando el polígono) y el punto de ella más cercano, en el modelo.
export function nearestEdge(poly, view, s) {
  if (poly.length < 2) return null;
  const q = toModel(view, s);
  let best = null;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length], ab = [b[0] - a[0], b[1] - a[1]], len2 = ab[0] ** 2 + ab[1] ** 2;
    const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((q[0] - a[0]) * ab[0] + (q[1] - a[1]) * ab[1]) / len2));
    const p = [a[0] + t * ab[0], a[1] + t * ab[1]], d = Math.sqrt(dist2(p, q)) * view.s;
    if (!best || d < best.dist) best = { i, t, dist: d, point: p };
  }
  return best;
}

// Vértice de la silueta de fondo más cercano al punto de pantalla (para imantarse), o null.
export function snapToBackground(bg, view, s, r = SNAP_R) {
  let best = null, bd = r * r;
  for (const g of bg) for (const ring of [g.exterior, ...(g.holes ?? [])]) for (const p of ring) {
    const d = dist2(toScreen(view, p), s); if (d <= bd) { bd = d; best = p; }
  }
  return best ? [best[0], best[1]] : null;
}

export const insertVertex = (poly, after, p) => [...poly.slice(0, after + 1), [p[0], p[1]], ...poly.slice(after + 1)];
export const moveVertex = (poly, i, p) => poly.map((q, k) => (k === i ? [p[0], p[1]] : q));
export const deleteVertex = (poly, i) => (poly.length <= 3 ? poly : poly.filter((_, k) => k !== i));

export function area(poly) {
  let a = 0;
  for (let i = 0; i < poly.length; i++) { const p = poly[i], q = poly[(i + 1) % poly.length]; a += p[0] * q[1] - q[0] * p[1]; }
  return a / 2;
}

const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
function segmentsCross(p1, p2, p3, p4) {
  const d1 = cross(p3, p4, p1), d2 = cross(p3, p4, p2), d3 = cross(p1, p2, p3), d4 = cross(p1, p2, p4);
  return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
}
// ¿Dos aristas no contiguas se cruzan? (polígono no simple: el motor lo rechazaría o daría un sólido roto)
export function selfIntersects(poly) {
  const n = poly.length;
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    if (j === i + 1 || (i === 0 && j === n - 1)) continue;
    if (segmentsCross(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n])) return true;
  }
  return false;
}

// null si el polígono sirve; si no, el motivo en español.
export function validatePolygon(poly) {
  if (poly.length < 3) return "El polígono necesita al menos 3 vértices.";
  if (selfIntersects(poly)) return "El polígono se cruza consigo mismo: mové un vértice para destrabarlo.";
  if (Math.abs(area(poly)) < 1) return "El polígono es casi plano (área menor a 1 mm²).";
  return null;
}

// (u, v) → 3D sobre el plano del miembro (`origin`, `u`, `v` como los devuelve `limb_editor`).
export const uvToWorld = (plane, uv) => uv.map(([a, b]) => [0, 1, 2].map((k) => plane.origin[k] + a * plane.u[k] + b * plane.v[k]));

// ------------------------------------------------------------------------------------------------------------------
// Controlador del canvas (necesita DOM).
export function createPolygonEditor(canvas, { onChange = () => {} } = {}) {
  const ctx = canvas.getContext("2d");
  let bg = [], poly = [], view = { s: 1, ox: 0, oy: canvas.height }, drag = null, hover = -1, history = [];
  const COLORS = { bg: "#cfd6dd", bgLine: "#8892a0", poly: "#e08a00", bad: "#d33", vertex: "#fff", grid: "#00000014" };

  const pos = (ev) => { const r = canvas.getBoundingClientRect(); return [(ev.clientX - r.left) * (canvas.width / r.width), (ev.clientY - r.top) * (canvas.height / r.height)]; };
  const snap = (s, ev) => (ev.shiftKey ? null : snapToBackground(bg, view, s));
  const push = () => { history.push(poly); if (history.length > 100) history.shift(); };
  const set = (p, record = true) => { if (record) push(); poly = p; draw(); onChange(poly); };

  function path(ring) { ring.forEach((p, i) => { const [x, y] = toScreen(view, p); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.closePath(); }
  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    // regla de 10 mm
    ctx.strokeStyle = "#667"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(16, canvas.height - 16); ctx.lineTo(16 + 10 * view.s, canvas.height - 16); ctx.stroke();
    ctx.fillStyle = "#667"; ctx.font = "11px sans-serif"; ctx.fillText("10 mm", 16, canvas.height - 22);
    for (const g of bg) {
      ctx.beginPath(); path(g.exterior); for (const h of g.holes ?? []) path(h);
      ctx.fillStyle = COLORS.bg; ctx.fill("evenodd"); ctx.strokeStyle = COLORS.bgLine; ctx.lineWidth = 1; ctx.stroke();
    }
    if (poly.length) {
      const bad = poly.length >= 3 && validatePolygon(poly) !== null;
      ctx.beginPath(); path(poly); ctx.fillStyle = bad ? "#d3333322" : "#e08a0026"; if (poly.length >= 3) ctx.fill();
      ctx.strokeStyle = bad ? COLORS.bad : COLORS.poly; ctx.lineWidth = 2; poly.length >= 2 ? ctx.stroke() : 0;
      poly.forEach((p, i) => {
        const [x, y] = toScreen(view, p);
        ctx.beginPath(); ctx.arc(x, y, i === hover ? 6 : 4.5, 0, 2 * Math.PI);
        ctx.fillStyle = COLORS.vertex; ctx.fill(); ctx.strokeStyle = bad ? COLORS.bad : COLORS.poly; ctx.stroke();
      });
    }
  }
  function fit() {
    const pts = [...bg.flatMap((g) => g.exterior), ...poly];
    const box = bounds(pts);
    if (box) view = fitView(box, canvas.width, canvas.height);
    draw();
  }

  canvas.addEventListener("pointerdown", (ev) => {
    if (ev.button === 2) return;
    canvas.setPointerCapture?.(ev.pointerId);
    const s = pos(ev);
    if (ev.button === 1 || ev.shiftKey) { drag = { pan: true, s, view }; return; }
    let i = hitVertex(poly, view, s);
    if (i < 0) {
      const p = snap(s, ev) ?? toModel(view, s);
      if (poly.length < 3) { set([...poly, p]); i = poly.length - 1; }
      else { const e = nearestEdge(poly, view, s); set(insertVertex(poly, e.i, p)); i = e.i + 1; }
    } else push();
    drag = { i, moved: false };
  });
  canvas.addEventListener("pointermove", (ev) => {
    const s = pos(ev);
    if (drag?.pan) { view = { ...drag.view, ox: drag.view.ox + s[0] - drag.s[0], oy: drag.view.oy + s[1] - drag.s[1] }; draw(); return; }
    if (drag) { drag.moved = true; poly = moveVertex(poly, drag.i, snap(s, ev) ?? toModel(view, s)); draw(); onChange(poly); return; }
    const h = hitVertex(poly, view, s); if (h !== hover) { hover = h; draw(); }
  });
  const end = () => { drag = null; };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("dblclick", (ev) => {
    const i = hitVertex(poly, view, pos(ev));
    if (i >= 0 && poly.length > 3) set(deleteVertex(poly, i));
  });
  canvas.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    const i = hitVertex(poly, view, pos(ev));
    if (i >= 0 && poly.length > 3) set(deleteVertex(poly, i));
  });
  canvas.addEventListener("wheel", (ev) => { ev.preventDefault(); view = zoomAt(view, pos(ev), ev.deltaY < 0 ? 1.15 : 1 / 1.15); draw(); }, { passive: false });

  return {
    load({ silueta, polygon }) { bg = silueta ?? []; poly = (polygon ?? []).map((p) => [p[0], p[1]]); history = []; fit(); onChange(poly); },
    getPolygon: () => poly.map((p) => [p[0], p[1]]),
    setPolygon(p) { set(p.map((q) => [q[0], q[1]])); fit(); },
    clear() { set([]); },
    undo() { if (history.length) { poly = history.pop(); draw(); onChange(poly); } },
    fit,
    validate: () => validatePolygon(poly),
  };
}
