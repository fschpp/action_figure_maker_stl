// Lógica pura del editor de polígonos 2D (web/polygon-editor.js): vista, agarrar/insertar/mover/quitar vértices, imán,
// validación (cruces, área) y paso (u, v) → 3D.
import assert from "node:assert/strict";
import { bounds, fitView, toScreen, toModel, zoomAt, hitVertex, nearestEdge, snapToBackground, insertVertex, moveVertex, deleteVertex,
  area, selfIntersects, validatePolygon, uvToWorld } from "../polygon-editor.js";

const close = (a, b, e = 1e-9) => assert.ok(Math.abs(a - b) < e, `${a} ≉ ${b}`);
const sq = [[0, 0], [20, 0], [20, 30], [0, 30]];

// vista: encuadra con margen, el eje v sube, ida y vuelta pantalla ↔ modelo
const box = bounds(sq);
assert.deepEqual(box, { u0: 0, v0: 0, u1: 20, v1: 30 });
assert.equal(bounds([]), null);
const view = fitView(box, 400, 300, 20);
close(view.s, 260 / 30);                                    // manda la altura: (300 − 40) / 30
const [sx, sy] = toScreen(view, [10, 15]);
close(sx, 200); close(sy, 150);                             // el centro de la caja cae en el centro del lienzo
const back = toModel(view, toScreen(view, [7.3, 21.1]));
close(back[0], 7.3); close(back[1], 21.1);
assert.ok(toScreen(view, [0, 30])[1] < toScreen(view, [0, 0])[1], "v hacia arriba en pantalla");

// zoom: el punto bajo el cursor no se mueve; respeta los límites
const z = zoomAt(view, [123, 77], 2);
const m0 = toModel(view, [123, 77]), m1 = toModel(z, [123, 77]);
close(m0[0], m1[0]); close(m0[1], m1[1]); close(z.s, view.s * 2);
assert.equal(zoomAt(view, [0, 0], 1e9).s, 400);
assert.equal(zoomAt(view, [0, 0], 1e-9).s, 0.2);

// agarrar un vértice: dentro del radio sí, fuera no; elige el más cercano
const at = (p) => toScreen(view, p);
assert.equal(hitVertex(sq, view, [at([20, 0])[0] + 5, at([20, 0])[1] - 3]), 1);
assert.equal(hitVertex(sq, view, [at([20, 0])[0] + 30, at([20, 0])[1]]), -1);
assert.equal(hitVertex([], view, [0, 0]), -1);

// arista más cercana: punto proyectado, índice, distancia en px
const e = nearestEdge(sq, view, at([10, -2]));
assert.equal(e.i, 0); close(e.point[0], 10); close(e.point[1], 0); close(e.dist, 2 * view.s, 1e-6);
assert.equal(nearestEdge(sq, view, at([-3, 15])).i, 3);    // la arista de cierre (3 → 0)
assert.equal(nearestEdge([[0, 0]], view, [0, 0]), null);

// imán a la silueta de fondo
const bg = [{ exterior: [[0, 0], [50, 0], [50, 80], [0, 80]], holes: [[[10, 10], [20, 10], [20, 20]]] }];
assert.deepEqual(snapToBackground(bg, view, [at([50, 80])[0] + 2, at([50, 80])[1] + 2]), [50, 80]);
assert.deepEqual(snapToBackground(bg, view, [at([20, 20])[0], at([20, 20])[1]]), [20, 20]);   // también vértices de agujeros
assert.equal(snapToBackground(bg, view, at([30, 40])), null);

// editar sin mutar el original
const ins = insertVertex(sq, 1, [25, 15]);
assert.deepEqual(ins, [[0, 0], [20, 0], [25, 15], [20, 30], [0, 30]]);
assert.equal(sq.length, 4);
assert.deepEqual(moveVertex(sq, 2, [22, 31])[2], [22, 31]);
assert.deepEqual(sq[2], [20, 30]);
assert.equal(deleteVertex(sq, 0).length, 3);
assert.equal(deleteVertex(deleteVertex(sq, 0), 0).length, 3, "no baja de 3 vértices");

// área y validación
close(area(sq), 600); close(area([...sq].reverse()), -600);
assert.equal(validatePolygon(sq), null);
assert.match(validatePolygon([[0, 0], [1, 0]]), /al menos 3/);
assert.match(validatePolygon([[0, 0], [10, 0], [20, 0]]), /casi plano/);
const bow = [[0, 0], [20, 20], [20, 0], [0, 20]];          // moño: dos aristas se cruzan
assert.equal(selfIntersects(bow), true);
assert.match(validatePolygon(bow), /se cruza/);
assert.equal(selfIntersects(sq), false);
assert.equal(selfIntersects(ins), false);
assert.equal(selfIntersects([[0, 0], [10, 0], [10, 10], [5, 5], [0, 10]]), false, "cóncavo pero simple");

// (u, v) → 3D: plano frontal con u = +x, v = +z, a y = 4
const plane = { origin: [1, 4, 2], u: [1, 0, 0], v: [0, 0, 1] };
assert.deepEqual(uvToWorld(plane, [[0, 0], [3, 5]]), [[1, 4, 2], [4, 4, 7]]);
// plano de costado: u = +y, v = +z
assert.deepEqual(uvToWorld({ origin: [12, 0, 0], u: [0, 1, 0], v: [0, 0, 1] }, [[2, 3]]), [[12, 2, 3]]);

console.log("polygon_node: OK");
