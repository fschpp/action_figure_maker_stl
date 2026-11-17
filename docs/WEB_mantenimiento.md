# Mantenimiento de la versión web (Pyodide + GitHub Pages)

Guía para quien tenga que reconstruir los wheels, actualizar Pyodide o entender por qué la página rechaza un STL. Todo lo de aquí sale de los workflows de `.github/workflows/` y de `web/config.js`; si algo difiere, mandan ellos.

## Qué se publica y de dónde sale

| Pieza | Origen | Versión fijada en |
|---|---|---|
| Pyodide (runtime, numpy, scipy, shapely, scikit-image, networkx, pillow, micropip) | CDN jsDelivr | `web/config.js` → `PYODIDE_VERSION` (hoy 314.0.8, Python 3.14) |
| `trimesh`, `python-i18n` (Python puro) | PyPI, con micropip | `web/config.js` → `MICROPIP_PACKAGES` |
| `manifold3d` para Pyodide | wheel propio, Release `manifold3d-pyodide-3.5.5` | `pages.yml`, `manifold-wheel.yml` |
| `fast-simplification` para Pyodide | wheel propio, Release `fast-simplification-pyodide-0.2.0` | `pages.yml`, `manifold-wheel.yml` (job `verify`), `fastsimp-wheel.yml` |
| Motor `afm` | wheel construido en cada deploy (`scripts/build_web.sh`) | `pyproject.toml` |

`pages.yml` corre en cada push a `main`: baja los dos wheels de sus Releases, ejecuta `scripts/build_web.sh extra` (genera `web/wheels/`, `manifest.json`, `build-info.js`, `version.json`) y publica `web/`. Los wheels propios llevan la versión en el nombre, así que la caché del navegador no los deja viejos.

## Reconstruir el wheel de manifold3d

1. Actions → **manifold-wheel** → *Run workflow*; en `manifold_version` poné la versión de PyPI (por defecto `3.5.5`).
2. El workflow baja el sdist, parchea el CMake (el upstream excluye los bindings de Python bajo Emscripten), compila con cibuildwheel (`cp314-pyodide_wasm32`, `MANIFOLD_PAR=OFF`), prueba el wheel dentro de Pyodide y lo sube al Release `manifold3d-pyodide-<versión>`.
3. El job `verify` corre el motor completo en Pyodide sobre Node (`web/tests/engine_node.mjs`) y la paridad con el motor nativo (`afm demo`). Si falla, no cambies la versión en `pages.yml`.
4. Si la versión cambió, actualizá el nombre del Release en `pages.yml` y en `manifold-wheel.yml` (job `verify`), `THIRD_PARTY.md` y esta tabla.
5. Si el parche del CMake ya no aplica (los `assert` del workflow lo dicen), el upstream cambió: leé el error y ajustá la cadena que se reemplaza.

## Reconstruir el wheel de fast-simplification

Igual, con el workflow **fastsimp-wheel** (versión por defecto `0.2.0`, Release `fast-simplification-pyodide-<versión>`). Notas del sdist que ya están resueltas en el workflow: `CIBW_ARCHS=wasm32`, `CIBW_TEST_REQUIRES=""` y `CXXFLAGS="-include cstdio"` (nanobind usa `stderr` sin incluir `<cstdio>`). Sin este wheel el torso no se decima en el navegador (120 066 triángulos en lugar de ~24 000).

## Actualizar Pyodide

1. Mirá las notas de la versión nueva y confirmá que sigue siendo Python 3.14 y la etiqueta `pyemscripten_2026_0_wasm32`. Si cambia la versión de Python o la ABI de Emscripten, hay que **recompilar los dos wheels** (cambiar `CIBW_BUILD` y, si hace falta, la etiqueta que espera `scripts/build_web.sh`).
2. Cambiá `PYODIDE_VERSION` en `web/config.js` y la URL del tarball en el job `verify` de `manifold-wheel.yml` (`pyodide-<versión>.tar.bz2`).
3. Corré `manifold-wheel` (para que `verify` pruebe contra la nueva versión) y revisá que pasen `web/tests/engine_node.mjs` y `web/tests/browser_dom.mjs` (workflow `web-browser`).
4. Versiones de numpy/scipy/shapely que trae Pyodide pueden diferir de las del escritorio: corré la paridad (G2) y mirá `THIRD_PARTY.md`.
5. Hacé el deploy y probá en el sitio: «Cargar demo», un STL propio y las probetas, en Chrome y Firefox.

## Límites de mallas grandes

- **Aviso** (`HEAVY_STL_TRIANGLES`, 15 000 triángulos): el STL carga, pero la página avisa que generar puede tardar decenas de minutos y ofrece «Reducir».
- **Rechazo** (`MAX_STL_BYTES` = 75 MB, `MAX_STL_TRIANGLES` = 1 500 000, en `src/afm/webapi.py`; el de bytes se repite en `web/app.js`): un STL binario pesa 84 B + 50 B por triángulo, así que 75 MB ≈ 1,5 M de triángulos. La memoria de WebAssembly (≤ 4 GB por pestaña) no alcanza con holgura porque el STL se copia varias veces (archivo, worker, trimesh, manifold). El mensaje pide reducirlo a ~10 000 triángulos con MeshLab o Blender.
- Si subís estos límites, medilo antes con un STL real (memoria del worker y tiempo de «Cargar»), y cambiá los dos lugares a la vez; hay un test nativo (`test_load_stl_rejects_files_over_the_browser_limits`) y uno de navegador.

## Verificación mínima antes de mergear algo web

`node --check web/*.js web/tests/*.mjs`; `pytest tests/test_webapi.py`; `node web/tests/browser_dom.mjs` (Chrome headless; necesita acceso al CDN de three.js o `THREE_DIR`); `engine_node.mjs` lo corre CI. Navegadores: se probó Chrome y Firefox de escritorio; Safari y móviles no.
