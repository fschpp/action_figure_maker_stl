// Visor three.js (M2): cámara ORTOGRÁFICA, Z arriba, selección de puntos por Raycaster con ajuste al vértice más cercano.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

const SNAP_PX = 14;   // radio de ajuste al vértice, en píxeles de pantalla

export function createViewer(canvas, { onPick = () => {} } = {}) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x15181c);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x333840, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.2); scene.add(sun);

  const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, -10000, 10000);
  cam.up.set(0, 0, 1);
  const controls = new OrbitControls(cam, canvas);
  controls.enableDamping = false;

  let mesh = null, markers = [], planeMesh = null, picking = null, bboxSize = 100;
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();

  function resize() {
    const w = canvas.clientWidth || 640, h = canvas.clientHeight || 480;
    renderer.setSize(w, h, false);
    const half = bboxSize * 0.65, a = w / h;
    cam.left = -half * a; cam.right = half * a; cam.top = half; cam.bottom = -half;
    cam.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(canvas);
  const frame = () => { sun.position.copy(cam.position); renderer.render(scene, cam); requestAnimationFrame(frame); };
  requestAnimationFrame(frame);

  function setView(name) {   // vistas ortogonales a los ejes; el modelo mira a ±Y según el proyecto
    if (!mesh) return;
    const c = new THREE.Box3().setFromObject(mesh).getCenter(new THREE.Vector3());
    const d = { front: [0, -1, 0], back: [0, 1, 0], right: [1, 0, 0], left: [-1, 0, 0], top: [0, 0, 1] }[name];
    cam.position.copy(c).addScaledVector(new THREE.Vector3(...d), bboxSize * 3);
    controls.target.copy(c); cam.lookAt(c); controls.update();
  }

  function loadStl(bytes) {
    const geo = new STLLoader().parse(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); }
    clearMarkers();
    mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0xc8ccd2, roughness: 0.8, metalness: 0, side: THREE.DoubleSide }));
    scene.add(mesh);
    geo.computeBoundingBox();
    const s = geo.boundingBox.getSize(new THREE.Vector3());
    bboxSize = Math.max(s.x, s.y, s.z);
    resize(); setView("front");
    return { min: geo.boundingBox.min.toArray(), max: geo.boundingBox.max.toArray(), triangles: geo.attributes.position.count / 3 };
  }

  function clearMarkers() { for (const m of markers) scene.remove(m); markers = []; }
  function addMarker(p) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(bboxSize * 0.008, 12, 8), new THREE.MeshBasicMaterial({ color: 0xff5533, depthTest: false }));
    m.renderOrder = 10; m.position.fromArray(p); scene.add(m); markers.push(m);
  }

  // Punto bajo el puntero ajustado al vértice más cercano (de la cara tocada) si está a ≤ SNAP_PX píxeles.
  function pickAt(ev) {
    if (!mesh) return null;
    const r = canvas.getBoundingClientRect();
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, cam);
    const hit = ray.intersectObject(mesh, false)[0];
    if (!hit) return null;
    const pos = mesh.geometry.attributes.position;
    let best = hit.point.clone(), bd = Infinity;
    for (const i of [hit.face.a, hit.face.b, hit.face.c]) {
      const v = new THREE.Vector3().fromBufferAttribute(pos, i);
      const s = v.clone().project(cam);
      const px = Math.hypot((s.x - ndc.x) * r.width / 2, (s.y - ndc.y) * r.height / 2);
      if (px < bd) { bd = px; if (px <= SNAP_PX) best = v; }
    }
    return best.toArray();
  }

  // Modo de selección: n clics → onDone(puntos). Un arrastre (órbita) no cuenta como clic.
  function startPicking(n, onDone) { picking = { n, pts: [], onDone }; clearMarkers(); canvas.style.cursor = "crosshair"; }
  function cancelPicking() { picking = null; canvas.style.cursor = ""; }
  let down = null;
  canvas.addEventListener("pointerdown", (e) => { down = [e.clientX, e.clientY]; });
  canvas.addEventListener("pointerup", (e) => {
    if (!picking || !down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
    const p = pickAt(e); if (!p) return;
    picking.pts.push(p); addMarker(p); onPick(p, picking.pts.length, picking.n);
    if (picking.pts.length === picking.n) { const { pts, onDone } = picking; cancelPicking(); onDone(pts); }
  });

  // Plano translucente (cuadrado de lado ~ tamaño del modelo) a partir de {origin, normal}.
  function showPlane(pl, color = 0x3fa9f5) {
    if (planeMesh) { scene.remove(planeMesh); planeMesh.geometry.dispose(); planeMesh = null; }
    if (!pl) return;
    planeMesh = new THREE.Mesh(new THREE.PlaneGeometry(bboxSize * 1.2, bboxSize * 1.2),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.25, side: THREE.DoubleSide, depthWrite: false }));
    planeMesh.position.fromArray(pl.origin);
    planeMesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), new THREE.Vector3(...pl.normal));
    scene.add(planeMesh);
  }

  return { loadStl, setView, startPicking, cancelPicking, showPlane, clearMarkers, addMarker };
}
