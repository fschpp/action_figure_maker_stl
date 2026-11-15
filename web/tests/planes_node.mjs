import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { planeFromPoints, mergeProject } from "../planes.js";

const cases = [[[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[1, 2, 3], [4, -1, 2], [0.5, 7, 9]], [[0, 0, 0], [0, 5, 0], [0, 0, 5]]];
const py = (c) => JSON.parse(execFileSync("python3", ["-c", `
import json,sys; sys.path.insert(0,'../src')
from afm.planes import Plane
p=Plane.from_points(*json.loads(sys.argv[1]))
print(json.dumps({'origin':p.origin.tolist(),'normal':p.normal.tolist()}))`, JSON.stringify(c)], { cwd: new URL("..", import.meta.url).pathname, env: { ...process.env, PYTHONPATH: "../src" } }));
for (const c of cases) {
  const j = planeFromPoints(...c), p = py(c);
  for (let i = 0; i < 3; i++) { assert.ok(Math.abs(j.normal[i] - p.normal[i]) < 1e-12); assert.equal(j.origin[i], p.origin[i]); }
}
assert.throws(() => planeFromPoints([0, 0, 0], [1, 1, 1], [2, 2, 2]), /colineales/);
const out = JSON.parse(mergeProject({ version: 1, symmetry: null, crown_z: null, front: "+Y", limbs: [] }, { symmetry: planeFromPoints(...cases[2], "simetría"), crown_z: 98.5, front: "-Y" }));
assert.deepEqual(out.symmetry.normal, [1, 0, 0]); assert.equal(out.crown_z, 98.5); assert.equal(out.front, "-Y");
console.log("planes_node: OK");
