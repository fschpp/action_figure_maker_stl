// Versiones fijas (plan §9): nada de "latest".
export const PYODIDE_VERSION = "314.0.8";
export const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

// Paquetes incluidos en Pyodide (plan §3)
export const PYODIDE_PACKAGES = ["numpy", "scipy", "shapely", "scikit-image", "networkx", "pillow", "micropip"];
// Python puro desde PyPI con micropip
export const MICROPIP_PACKAGES = ["trimesh", "python-i18n"];
// Módulos nativos que la prueba intenta importar y reporta (no se espera que todos pasen)
export const NATIVE_PROBES = ["manifold3d", "mapbox_earcut", "fast_simplification", "pymeshfix"];

// Wheels propios (los genera scripts/build_web.sh y el CI). El worker lee wheels/manifest.json:
//   {"engine": "action_figure_maker_stl-<ver>-py3-none-any.whl", "extra": ["manifold3d-...-pyemscripten_2026_0_wasm32.whl"]}
export const WHEELS_MANIFEST = "./wheels/manifest.json";
