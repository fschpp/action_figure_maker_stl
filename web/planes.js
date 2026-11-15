// Plano por 3 puntos: misma matemática que afm.planes.Plane.from_points (sin DOM ni three.js, para probarlo en Node).
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => Math.hypot(a[0], a[1], a[2]);

export function planeFromPoints(p0, p1, p2, name = "") {
  const n = cross(sub(p1, p0), sub(p2, p0));
  const len = norm(n);
  if (len < 1e-9) throw new Error("Los 3 puntos son colineales: no definen un plano");
  return { name, origin: [...p0], normal: n.map((x) => x / len) };
}

// Formato de `Plane` en el .proyecto (project.py: {"name", "origin", "normal"}).
export const planeToJson = (pl) => ({ name: pl.name, origin: pl.origin, normal: pl.normal });

// Fusiona lo elegido en el visor con un proyecto base (objeto o texto) y devuelve el texto del proyecto.
// `edits`: {symmetry?: plano, crown_z?: número, front?: "+Y"|"-Y"}. Los miembros (siluetas) vienen del proyecto base.
export function mergeProject(base, edits) {
  const p = typeof base === "string" ? JSON.parse(base) : structuredClone(base);
  if (edits.symmetry) p.symmetry = planeToJson(edits.symmetry);
  if (edits.crown_z != null) p.crown_z = edits.crown_z;
  if (edits.front) p.front = edits.front;
  return JSON.stringify(p, null, 2);
}

// Lleva puntos del STL crudo al marco de la figura: figura = k·archivo + offset (`figure_frame` del motor).
// Los planos del .proyecto están en ese marco; los puntos que se eligen en el visor, en el del archivo.
export const toFigure = (pts, { k, offset }) => pts.map((p) => p.map((x, i) => k * x + offset[i]));
