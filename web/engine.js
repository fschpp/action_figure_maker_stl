// Puente JS ↔ motor Python (afm.webapi.EngineSession). Lo usan el Worker y la prueba en Node.
// No toca el DOM ni `self`: recibe `loadPyodide` y las URLs por parámetro.

export async function createEngine({ loadPyodide, indexURL, pyodidePackages, pypiPackages, wheelUrls, progress = () => {}, prepare = null }) {
  progress("Pyodide", 5);
  const py = await loadPyodide({ indexURL });
  progress("Paquetes", 25);
  await py.loadPackage(pyodidePackages);
  progress("Paquetes de PyPI", 55);
  const micropip = py.pyimport("micropip");
  if (pypiPackages.length) await micropip.install(pypiPackages);
  progress("Motor", 80);
  if (prepare) await prepare(py);   // gancho (pruebas en Node): copiar wheels locales al FS virtual
  // Desde JS los kwargs de Python se pasan con callKwargs (un objeto como 2.º argumento NO se convierte).
  for (const url of wheelUrls) await micropip.install.callKwargs(url, { deps: false });
  py.runPython(`
import json
from pyodide.ffi import to_js
from afm.webapi import EngineSession
_s = EngineSession()
def _pack(r):
    return to_js({"zip": to_js(r["zip"]), "archivos": r["archivos"], "informe": json.dumps(r["informe"])},
                 dict_converter=__import__("js").Object.fromEntries)
`);
  progress("Listo", 100);

  const g = py.globals;
  const withProgress = (cb) => { g.set("_pcb", cb ?? (() => {})); };
  return {
    py,
    loadStl(bytes) {
      g.set("_data", bytes);
      return JSON.parse(py.runPython("json.dumps(_s.load_stl(_data.to_py().tobytes()))"));
    },
    build(projectText, { onMessage, name = "figura" } = {}) {
      g.set("_proj", projectText); g.set("_name", name); withProgress(onMessage);
      return py.runPython("_pack(_s.build(_proj, progress_cb=_pcb, name=_name))");
    },
    // Transformación del STL crudo al marco de la figura: figura = k·archivo + offset (afm.webapi.figure_frame).
    figureFrame(crownZ, refHeight) {
      g.set("_arg", JSON.stringify({ crown_z: crownZ ?? null, ref_height: refHeight ?? 100 }));
      return JSON.parse(py.runPython("json.dumps(_s.figure_frame(**json.loads(_arg)))"));
    },
    // Miembro (brazo/pierna) con el plano por 3 puntos YA en el marco de la figura (afm.webapi.new_limb).
    newLimb(name, kind, points) {
      g.set("_arg", JSON.stringify({ name, kind, p0: points[0], p1: points[1], p2: points[2] }));
      return JSON.parse(py.runPython("json.dumps(_s.new_limb(**json.loads(_arg)))"));
    },
    demo({ onMessage } = {}) {
      withProgress(onMessage);
      return py.runPython("_pack(_s.demo(progress_cb=_pcb))");
    },
  };
}
