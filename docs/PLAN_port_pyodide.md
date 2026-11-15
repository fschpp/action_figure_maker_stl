# Plan v2: portar `action_figure_maker_stl` a Pyodide (GitHub Pages)

Repo origen: https://github.com/fschpp/action_figure_maker_stl
Objetivo: que cualquiera abra una URL de GitHub Pages, cargue un STL y descargue la figura articulada (print-in-place) sin instalar nada y sin servidor. El STL nunca sale del navegador.

**Versión:** 2.0 — 2026-11-15
**Marcas** (mismo criterio que el `PLAN_herramienta_stl_articulable.md` del repo):
**[V]** verificado en una fuente consultada (listada en §14) · **[I]** inferido, a validar · **[P]** pendiente de medir o decidir.

> **Qué cambió respecto de la v1.** (1) Se leyó `pyproject.toml` y el plan de diseño del repo, así que ahora el port se apoya en los algoritmos reales de la app. (2) Se verificó el estado de Pyodide a noviembre de 2026 (Pyodide 314, PEP 783). (3) Se confirmó que `manifold3d` no tiene wheel para Pyodide, y se agregó una puerta de decisión con plazo. (4) Se agregó un hito mínimo (MVP) que evita escribir la interfaz completa antes de saber si el motor corre.
>
> **Límite del análisis.** GitHub bloqueó la lectura de `src/afm/` y de `docs/PROGRESS.md`. Todo lo que dependa de qué módulo llama a qué librería queda como **[P]** hasta la Fase 0.

---

## 1. Resumen ejecutivo y decisiones

| # | Decisión | Elección | Estado |
|---|---|---|---|
| D1 | Qué se porta | Solo el **motor** (`afm` sin `gui`) corre en Pyodide. La GUI GTK4 se reescribe en HTML/JS | [V] GTK y moderngl no existen en Pyodide (§3) |
| D2 | Versión de Pyodide | **314.0.x** (Python 3.14), última estable 314.0.8 | [V] |
| D3 | Dónde corre el motor | Web Worker con una **sesión con estado** (la malla queda en memoria Python entre pasos) | [I] |
| D4 | Cómo se distribuye el motor | Un **wheel puro Python** (`afm-*-py3-none-any.whl`) generado en CI e instalado con `micropip` (`deps=False`) | [I] |
| D5 | Punto crítico | **`manifold3d`**: no hay wheel Pyodide en PyPI. Plazo de 5 días hábiles para resolverlo (Plan A) antes de pasar al Plan B | [V] sobre la ausencia, [I] sobre el plazo |
| D6 | Visor y selección de puntos | **three.js** con `Raycaster` (reemplaza a moderngl y a trimesh/embree) | [I] |
| D7 | Hosting | GitHub Pages con Pyodide desde CDN fijado a versión; autohospedado solo si hace falta modo offline | [I] |
| D8 | Orden de trabajo | Primero el motor en el navegador (**M1**), después el visor (**M2**), al final el editor de recortes y las probetas (**M3**) | [I] |

---

## 2. Qué hace la app y qué implica para el port

Según el plan de diseño del repo:

- Entrada: **un STL de una sola pieza**, figura de unos 10 cm, ≈10 000 triángulos (≈10 700 en las figuras nuevas). **[V]**
- Salida: ensamblado sentado + un STL por parte (torso soldado en una pieza, 2 brazos, 2 piernas), en un mismo sistema de coordenadas. **[V]**
- Núcleo geométrico: separación por **intersección con sólidos auxiliares**, cavidades con holgura (**offset 3D**), pasadores tipo hongo, espejado, giro de piernas a 90°, y recorte del límite de cadera. **[V]**
- El plan del repo prevé el offset 3D con **campo de distancia + marching cubes** (scikit-image) a 0,05–0,1 mm, o con la suma de Minkowski de manifold3d. Lo marca como riesgo R1 (lento o inexacto). **[V]**
- La CLI ya existe: `afm demo --out ...` y `afm build figura.stl proyecto.json --out ...`. **[V]** Eso significa que el motor **ya se puede invocar sin interfaz**, y es la base del MVP.

### Mapa de componentes: escritorio → web

| Escritorio (plan del repo) | Web | Nota |
|---|---|---|
| Ventana GTK4 + panel de parámetros | HTML/CSS/JS | Formulario generado a partir de los mismos parámetros y valores por defecto (§5.2 del plan del repo) |
| Visor `Gtk.GLArea` + moderngl | three.js (`STLLoader`, `OrbitControls`) | Con ~10 k triángulos alcanza el `Raycaster` simple; `three-mesh-bvh` solo si hiciera falta |
| Selección de 3 puntos (ray-casting con trimesh/embree) | `Raycaster` en JS | El punto se envía al motor como coordenadas; no hay ray-casting en Python |
| Editor de polígonos (siluetas, recortes) | Canvas/SVG 2D sobre el plano | La pieza más laboriosa de la UI |
| Vista previa de la pose (slider de cadera + colisión) | Slider JS que llama a `preview_pose(angle)` | Devuelve arrays tipados, no STL |
| Proyecto `.proyecto` (JSON) | Descargar / abrir archivo JSON | Sin servidor: el usuario guarda el archivo |
| Perfil de línea (§8.12) | `localStorage`/IndexedDB + exportar/importar JSON | Para ≈20 personajes |
| Generador de probetas (§8.11) | Función del motor + botón de descarga | Se puede dejar para M3 |
| Informe de validación (§9) | Panel con la tabla de chequeos | Los mismos criterios sirven como tests de aceptación (§8) |
| Empaquetado pip/Flatpak | URL de GitHub Pages | Desaparece el empaquetado |

---

## 3. Compatibilidad de dependencias (verificada a nov. 2026)

Dependencias duras de `pyproject.toml`: `numpy, scipy, trimesh, manifold3d>=3.0, shapely, mapbox_earcut, networkx, scikit-image, fast-simplification, pymeshfix, python-i18n`. Extra `gui`: `PyGObject, moderngl, pycairo, pillow`. Extra `dev`: `pytest, rtree`. `requires-python >= 3.10`. **[V]**

Pyodide 314.0.8 incluye: numpy 2.4.6, scipy 1.18.0, shapely 2.1.2, scikit-image 0.26.0, networkx 3.6.1, Pillow 12.2.0, pytest 9.0.2 (y `pyclipper`, `h5py`, etc.). **[V]**

| Dependencia | En Pyodide 314.0.8 | Cómo se resuelve | Estado |
|---|---|---|---|
| numpy, scipy, shapely, scikit-image, networkx, pillow | Incluidas | `loadPackage` | [V] |
| trimesh | No está en la lista | Python puro → `micropip` desde PyPI | [I] (confirmar en Fase 1) |
| python-i18n | No está en la lista | Python puro → `micropip` | [I] |
| **manifold3d** | **No está**. PyPI (3.5.5, 13-nov-2026) publica wheels de macOS, Linux y Windows; no aparece ninguno Emscripten | Ver §4.1 | [V] |
| **mapbox_earcut** | No está. Extensión C++ | Ver §4.2 | [V] sobre la ausencia, [P] sobre alternativas |
| **fast-simplification** | No está. Extensión compilada | Ver §4.3 | [V] sobre la ausencia |
| **pymeshfix** | No está. Extensión compilada | Ver §4.4 | [V] sobre la ausencia |
| rtree | No está | Solo se usa en `tools/` y tests; no entra al motor web | [V] según el comentario del `pyproject` |
| PyGObject, pycairo, moderngl | No aplican | Se descartan; la GUI se reescribe | [V] |

**Cambio importante del ecosistema [V]:** desde PEP 783 (Pyodide 314, junio de 2026), los wheels para Pyodide se pueden publicar en PyPI con la etiqueta `pyemscripten_2026_0_wasm32` y `micropip` los instala. `cibuildwheel` ya soporta la plataforma Pyodide con `pyodide-build` como frontend por defecto. Esto abarata mucho el Plan A de `manifold3d`.

---

## 4. Estrategia por dependencia nativa

### 4.1 manifold3d (riesgo alto: probable corazón del print-in-place)

**Datos [V]:** manifold3d tiene licencia Apache-2.0; su build WASM oficial (paquete npm `manifold-3d`, bindings JS) es **serial**, y la documentación recomienda no activar el paralelismo en WASM por riesgo de corrupción de memoria. Los bindings Python usan nanobind.

**Plan A — wheel propio para Pyodide (preferido).**
1. Clonar `elalish/manifold`, compilar con `cibuildwheel --platform pyodide` (o `pyodide build`), con `MANIFOLD_PAR=OFF` y bindings Python activos. **[I]** (que el build con nanobind pase en Emscripten no está verificado)
2. Publicar el wheel `…-pyemscripten_2026_0_wasm32.whl` en GitHub Releases o en `web/wheels/` y cargarlo con `micropip.install("<url>")`.
3. Opcional: proponer el job al repo upstream para que `micropip.install("manifold3d")` funcione sin URLs.
- **Plazo máximo: 5 días hábiles.** Si no hay `import manifold3d` + test de unión/diferencia/intersección en el navegador al llegar a ese plazo, se pasa al Plan B.

**Plan B — usar `manifold-3d` (npm/WASM) desde Python.**
- Cargar el módulo JS en el worker y exponerlo a Python con `import js`.
- Escribir `afm/compat/manifold_js.py`, un adaptador con **solo las operaciones que `afm` usa** (el inventario de §7, Fase 0, lo define): constructores desde malla, union/difference/intersection, transformaciones, conversión a arrays NumPy.
- Costo: mantener dos backends (nativo en escritorio, JS en web) detrás de una interfaz común. Se recomienda definirla ya en la Fase 2.

**Plan C — reemplazo.** Último recurso; implica reescribir partes del motor. No se detalla hasta que A y B fallen.

### 4.2 mapbox_earcut
- Es una extensión C++ pequeña: candidata a wheel propio con el mismo flujo del Plan A.
- Alternativas Python puras en PyPI (`earcut`, `earcut-py`) **[V]** que existen; su rendimiento y robustez frente a `mapbox_earcut` **[P]**.
- `trimesh.creation.triangulate_polygon` permite elegir otro motor de triangulación (incluido uno basado en manifold); **[P]** confirmar en la versión de trimesh que se instale. Si funciona, `mapbox_earcut` deja de ser necesaria cuando `manifold3d` esté disponible.

### 4.3 fast-simplification
- Se usa, probablemente, para decimar mallas. **[P]** confirmar dónde y si es imprescindible.
- Alternativas: simplificación de manifold3d (si la versión la expone), decimación en JS (meshoptimizer WASM) o wheel propio.

### 4.4 pymeshfix
- Probablemente repara mallas de entrada no estancas (el plan del repo menciona que Azurduy no es estanca). **[P]**
- Alternativas: `trimesh.repair` (huecos, normales, vértices duplicados) + los chequeos de manifold3d. Si la reparación no alcanza, el flujo debe **avisar** y no fallar en silencio.

### 4.5 Tabla de puertas de decisión

| Puerta | Cuándo | Criterio de paso | Si no pasa |
|---|---|---|---|
| **G0** | Fin de Fase 0 | El motor pasa `pytest` en Python 3.14 con las versiones de §3 (numpy 2.x, scipy 1.18, shapely 2.1, scikit-image 0.26) | Fijar/ajustar el motor para esas versiones **antes** de tocar WASM |
| **G1** | Fin de Fase 1 (≤ 5 días hábiles) | `manifold3d` importa y opera en el navegador | Plan B |
| **G2** | Fin de Fase 3 | `afm demo` produce el mismo resultado que en escritorio dentro de tolerancia (§8) | Revisar dependencias restantes o mallas de prueba |
| **G3** | Fin de M1 | `afm build` sobre una figura real ≤ 60 s y ≤ 1,5 GB de memoria pico en Chrome de escritorio **[P]** (objetivo a calibrar con la línea base de Fase 0) | Reducir resolución del offset, optimizar o limitar tamaño de malla |

---

## 5. Rendimiento y memoria

Riesgos concretos del plan del repo que el port agrava:

- **Offset 3D por campo de distancia (R1).** A 0,05 mm, una pieza de 20 × 40 × 15 mm son ≈96 millones de celdas; en `float32` son unos 380 MB solo para el campo. A 0,1 mm bajan 8 veces. **[I]** (cálculo propio)
- **WASM de 32 bits:** el techo de memoria es de unos pocos GB y varía según navegador y dispositivo. **[I]**
- **Manifold en WASM es serial** (§4.1); no hay paralelismo.

Mitigaciones:

1. **Medir antes de portar:** en la Fase 0, perfilar `afm build` nativo y registrar tiempo y memoria por etapa. Eso da el presupuesto real para G3.
2. **Offset con banda angosta:** calcular el campo de distancia solo en el bounding box de cada pieza más el margen, no sobre toda la figura; procesar por piezas y liberar memoria entre piezas.
3. **Resolución adaptativa:** tamaño de celda como parámetro; valor por defecto elegido para que quepa en el presupuesto, con modo "alta calidad" opcional.
4. **Evaluar alternativas dentro de manifold:** la suma de Minkowski y la función `level set` (documentadas por manifold) pueden reemplazar parte del campo de distancia. **[P]** ensayar nativo primero.
5. **Límite de entrada:** advertir si el STL supera un número de triángulos (por ejemplo, 50 000) y ofrecer decimar.
6. **Cancelar de verdad:** un cálculo largo no se interrumpe limpiamente en WASM; el botón "Cancelar" termina el worker y lo recrea (se pierde el estado; recargar la sesión).
7. **Escritorio primero:** móvil queda como "mejor esfuerzo". Los navegadores móviles limitan la memoria más que los de escritorio. **[I]**

---

## 6. Arquitectura objetivo

```
GitHub Pages (estático)
├── index.html, app.js, styles.css      UI
├── viewer.js                           three.js: visor, selección de puntos, preview de pose
├── polygon-editor.js                   editor 2D de siluetas y recortes (M3)
├── worker.js                           Web Worker: carga Pyodide y atiende mensajes
├── wheels/
│   ├── afm-<ver>-py3-none-any.whl      motor (generado en CI)
│   └── manifold3d-…-pyemscripten_2026_0_wasm32.whl   (Plan A)
├── locales/*.json                      i18n compartido con el motor
└── examples/                           figura sintética de demostración
```

### 6.1 Sesión del motor (API propuesta)

La GUI de escritorio llama al motor varias veces (planos, vista previa, generación). En web conviene **mantener la malla en memoria dentro del worker** y no reenviar bytes en cada paso:

```
init(wheels)                         → ok
load_stl(bytes)                      → {triangulos, altura, estanca, avisos}   # repara y normaliza (§8.1)
set_plane(tipo, p1, p2, p3)          → {normal, origen}
suggest_outline(tipo)                → polígono propuesto (M2)
set_params(dict)                     → ok
preview_pose(angulo_deg)             → {vertices: Float32Array, indices: Uint32Array, colisiones}
build(project_json, progress_cb)     → {archivos: {nombre: bytes}, informe}
make_test_coupons(params)            → bytes                                    # M3
```

- `build` devuelve STL por pieza + ensamblado + informe de validación. El ZIP se arma en Python con `zipfile` (biblioteca estándar).
- Las vistas previas usan **arrays tipados** transferibles (`postMessage` con `transfer`), no STL.
- El `progress_cb` publica `{etapa, porcentaje}` para la barra de progreso.

### 6.2 Reglas de diseño

- **Separar el motor de la GUI y de la CLI** en el repo origen: `afm.engine` no importa `gi`, `moderngl` ni `cairo`. Si hoy hay imports cruzados, se corrigen en la Fase 2.
- **Sin disco:** entradas y salidas son `bytes` / `io.BytesIO`; las rutas del sistema se reemplazan por recursos empaquetados.
- **Instalación del motor:** `loadPackage([...])` para lo incluido; luego `micropip.install("afm-….whl", deps=False)`, porque las dependencias duras del `pyproject` incluyen paquetes que no se pueden resolver (§3). Alternativa a evaluar: dejar en `dependencies` solo lo mínimo y mover lo nativo a un extra.
- **Datos de i18n:** `locales/*.json` van dentro del wheel (`package-data`) y se leen con `importlib.resources`; el JS reutiliza los mismos JSON.
- **Sin `SharedArrayBuffer`:** GitHub Pages no permite configurar cabeceras COOP/COEP, y no hace falta porque WASM de manifold es serial. Si algún día hiciera falta, se usa `coi-serviceworker`.

---

## 7. Fases e hitos

### Fase 0 — Línea base y inventario (1–2 días)
- [ ] Clonar el repo; correr `pytest`, `afm demo` y `afm build` con los ejemplos de `examples/`; guardar salidas como **referencia dorada** (mallas + informe de validación).
- [ ] Perfilar `afm build` por etapa (tiempo y memoria). Anotar la etapa más pesada.
- [ ] Inventario de imports: `grep -rn "^import\|^from" src/afm`; una tabla módulo → dependencias. Separar `engine` / `cli` / `gui`.
- [ ] Listar **qué funciones** de manifold3d, earcut, fast-simplification y pymeshfix se usan realmente (nombre + archivo).
- [ ] **G0:** crear un entorno nativo con Python 3.14 y las versiones de §3; correr `pytest`. Corregir lo que falle por numpy 2.x / shapely 2.x / scikit-image 0.26.
- [ ] Resolver la licencia (§11).

### Fase 1 — Prueba de humo en el navegador (1–3 días)
- [ ] Página mínima con Pyodide 314.0.x en un Worker.
- [ ] `loadPackage` de numpy, scipy, shapely, scikit-image, networkx, pillow; `micropip` de trimesh y python-i18n.
- [ ] Probar `import` de manifold3d, mapbox_earcut, fast_simplification, pymeshfix; registrar qué falla.
- [ ] Iniciar el intento de **wheel propio de manifold3d** (Plan A) en CI. **G1** a los 5 días hábiles.

### Fase 2 — Aislar el motor (1–2 días)
- [ ] Mover imports de GTK/moderngl fuera del camino del motor.
- [ ] Crear la API de §6.1 (al menos `load_stl`, `build`).
- [ ] Definir la **interfaz de backend de booleanos** (nativo vs JS) para que el Plan B no obligue a reescribir.
- [ ] Reemplazar escrituras a disco por buffers.
- [ ] Los tests de escritorio siguen verdes.

### Fase 3 — Dependencias nativas (2–7 días; la de mayor incertidumbre)
- [ ] manifold3d: Plan A o B según G1. Test: unión/diferencia/intersección de primitivas con el mismo resultado numérico que en escritorio.
- [ ] earcut, simplificación y reparación (§4.2–4.4).
- [ ] Wheels propios publicados en Releases / `web/wheels/`.
- [ ] **G2:** `afm demo` en navegador vs. referencia dorada.

### **Hito M1 — "afm build en el navegador" (MVP)**
Página con: botón "Demo", carga de un STL **y de un `proyecto.json`** (sin selección de puntos todavía), botón "Generar", barra de progreso, descarga del ZIP, informe de validación en texto.
- [ ] Worker + protocolo de mensajes (`init`, `load_stl`, `build`, `progress`, `result`, `error`).
- [ ] Transferir buffers (`transfer`) para evitar copias.
- [ ] **G3** (§4.5) con una figura real.
- Esto valida todo el riesgo técnico **antes** de invertir en visor y editores.

### **Hito M2 — Visor y planos (3–5 días)**
- [ ] three.js: carga y visualización del STL, órbita, zoom.
- [ ] Selección de 3 puntos por `Raycaster` y generación de planos (brazo, pierna, corte al medio, simetría), con edición y rehacer.
- [ ] Trabajo en un lado: atenuar la mitad opuesta; el motor espeja.
- [ ] Generación automática de `proyecto.json`; guardar y abrir el proyecto.
- [ ] Parámetros en formulario (valores por defecto del plan del repo: holgura 0,50 mm, pasadores Ø6,6 de cabeza, pose 90°, margen 5°).
- [ ] Vista previa de pose con slider (`preview_pose`).

### **Hito M3 — Editores y complementos (4–7 días)**
- [ ] Editor de polígonos 2D para siluetas y para la **herramienta genérica de recorte manual** (§8.8 del plan del repo).
- [ ] Generador de probetas de articulación (§8.11) con descarga.
- [ ] Perfiles de línea (§8.12): guardar, listar, exportar/importar.
- [ ] i18n completo.

### Fase final — Despliegue, validación y pulido (2–4 días)
- [ ] CI de Pages (§9) y habilitar Pages (Settings → Pages → Source: GitHub Actions).
- [ ] Probar Chrome, Firefox y Safari de escritorio; Chrome/Safari móviles como "mejor esfuerzo".
- [ ] Mallas grandes: fijar límites y mensajes de error claros.
- [ ] Documentar la reconstrucción de wheels y el procedimiento de actualización de Pyodide.

### Estimación **[I]**
| Alcance | Duración |
|---|---|
| M1 (motor en el navegador) | 1,5–3 semanas, sobre todo por Fase 3 |
| + M2 (visor y planos) | +1 semana |
| + M3 (editores y complementos) | +1–1,5 semanas |
| + despliegue y pulido | +0,5 semana |
| **Total** | **≈4–6 semanas**, con M1 como punto de control |

La incertidumbre principal es la Fase 3. Si G1 pasa rápido, M1 baja al piso del rango.

---

## 8. Pruebas y criterios de aceptación

- **Tests existentes bajo Pyodide:** `pytest` viene en Pyodide **[V]**; correr la suite del repo en el runtime de Pyodide sobre Node en CI. Es la forma más barata de detectar diferencias.
- **Paridad con escritorio** (referencia dorada de la Fase 0). Reusar los criterios del plan del repo:
  - distancia máxima entre mallas ≤ 0,3 mm respecto de la referencia; **[V]** criterio de la Fase 0 del plan del repo
  - holgura típica ≈ 0,5 mm y mínima ≈ 0,164 mm en el hueco de cadera; **[V]**
  - mismo número de piezas, volumen y bounding box dentro de tolerancia. **[I]**
- **Chequeos de §9 del plan del repo** como tests automáticos: piezas estancas sin aristas non-manifold, torso unido en una sola pieza estanca, distancia mínima miembro–torso ≥ holgura − tolerancia, miembros sin colisión de 0° a 95°, espesor de pared ≥ 1,2 mm, pasadores ≥ 3 mm.
- **Figuras de prueba:** la figura sintética (`afm demo`) y, localmente, las 4 figuras reales del cliente (una de ellas no estanca, útil para probar la reparación). **No** se publican en el sitio modelos con propiedad intelectual de terceros (§11).
- **Pruebas manuales de impresión** (fuera del alcance del código): una impresión de la salida web comparada con una de escritorio.

---

## 9. Despliegue en GitHub Pages

- **Pyodide:** cargar desde jsDelivr con la versión fijada (`.../pyodide/v314.0.8/full/`); no usar `latest`. Autohospedar solo si se pide modo offline (el sitio de Pages admite hasta 1 GB, pero habría que copiar solo los paquetes necesarios y sus dependencias transitivas).
- **Peso de la primera carga:** scipy, scikit-image y numpy suman decenas de MB **[P]** (medir); se cachean por HTTP después de la primera visita. Mostrar etapas de carga ("Pyodide", "Paquetes", "Motor").
- **Versiones fijas:** Pyodide, wheels y three.js con versión exacta, para builds reproducibles.
- **Sin bundler al principio:** módulos ES + `importmap` para three.js. Se agrega Vite solo si la UI crece.

Esquema de workflow (actualizar a las versiones vigentes de cada acción):

```yaml
name: pages
on:
  push: { branches: [main] }
  workflow_dispatch: {}
permissions: { contents: read, pages: write, id-token: write }
concurrency: { group: pages, cancel-in-progress: true }
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.14" }
      - run: pip install build && python -m build --wheel --outdir web/wheels .
      - run: cp -r locales web/ 2>/dev/null || true   # si los locales se sirven aparte
      - uses: actions/upload-pages-artifact@v3
        with: { path: web }
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: { name: github-pages, url: "${{ steps.d.outputs.page_url }}" }
    steps:
      - id: d
        uses: actions/deploy-pages@v4
```

Job adicional (Plan A): `cibuildwheel` para el wheel `pyemscripten` de manifold3d y publicación en Releases.

---

## 10. Persistencia de datos (sitio estático)

- **Proyecto:** archivo `.json` que el usuario descarga y vuelve a abrir. Nada se envía a ningún servidor.
- **Perfiles de línea y preferencias:** `localStorage`/IndexedDB, más exportar/importar archivo (para pasar entre equipos).
- **Privacidad:** los STL se procesan localmente; conviene decirlo en la página.

---

## 11. Licencias y contenido de terceros

- **Inconsistencia de licencia en el repo [V]:** GitHub muestra GPL-2.0, y `pyproject.toml` declara `GPL-3.0-or-later`. Resolver antes de publicar; el sitio distribuye el código.
- **Apache-2.0 y GPL-2.0:** manifold3d es Apache-2.0. Esa licencia es compatible con GPL-3.0, pero no con GPL-2.0-only; si la intención es "solo 2.0", hay un conflicto al distribuir un wheel de manifold junto al motor. **[I]** revisar con la licencia final.
- **Verificar licencias** de cada wheel propio y de las librerías JS (three.js, etc.) y listarlas en un `THIRD_PARTY.md`.
- **Modelos de ejemplo:** el plan del repo menciona figuras de personajes de terceros como casos de estudio (por ejemplo, Mickey). No incluirlas en el sitio público; usar la figura sintética.

---

## 12. Riesgos

| # | Riesgo | Prob. | Impacto | Mitigación / disparador |
|---|---|---|---|---|
| R1 | manifold3d no compila para Pyodide | Media | Alto | Plazo de 5 días; Plan B (npm/WASM + adaptador) |
| R2 | Offset 3D lento o sin memoria en WASM | Media | Alto | Banda angosta, resolución adaptativa, límite de triángulos, perfilar en Fase 0 |
| R3 | Motor depende de numpy/scipy/shapely más viejos que los de Pyodide | Media | Medio | G0: probar nativo con las mismas versiones antes de WASM |
| R4 | Reparación de mallas (pymeshfix) sin reemplazo suficiente | Media | Medio | `trimesh.repair` + aviso explícito al usuario |
| R5 | Imports cruzados motor ↔ GUI | Media | Medio | Fase 2: aislar `engine` |
| R6 | Primera carga pesada | Alta | Bajo/Medio | Etapas visibles, caché HTTP, medir y reportar |
| R7 | Safari/móvil con poca memoria | Media | Bajo | Escritorio primero; advertencia en móvil |
| R8 | Diferencias numéricas entre nativo y WASM | Baja | Medio | Pruebas de paridad con tolerancia (§8) |
| R9 | Conflicto de licencias | Baja | Alto | Resolver en Fase 0 (§11) |
| R10 | Editor de polígonos 2D más laborioso de lo previsto | Media | Medio | Postergado a M3; M1 funciona con `proyecto.json` |

---

## 13. Preguntas abiertas

1. **[P]** ¿Qué módulos de `src/afm` llaman a manifold3d, earcut, fast-simplification y pymeshfix, y con qué funciones? (Fase 0)
2. **[P]** ¿Cuál es la etapa más pesada en tiempo y memoria del motor nativo? (Fase 0)
3. **[P]** ¿El formato de `proyecto.json` está documentado/estable? Es la interfaz del MVP.
4. **[P]** ¿Se quiere modo offline (PWA)? Cambia el esquema de hosting de Pyodide.
5. **[P]** ¿Licencia objetivo: GPL-2.0 o GPL-3.0+?
6. **[P]** ¿El alcance web incluye todo el flujo del cliente (≈20 personajes) o solo una demo pública?

---

## 14. Fuentes consultadas

- Repo y README: https://github.com/fschpp/action_figure_maker_stl
- `pyproject.toml` del repo (dependencias y extras).
- `docs/PLAN_herramienta_stl_articulable.md` del repo (algoritmos, riesgos y criterios de aceptación).
- Paquetes incluidos en Pyodide 314.0.8: https://pyodide.org/en/stable/usage/packages-in-pyodide.html
- Pyodide 314.0 (PEP 783, Python 3.14.2, Emscripten 5.0.3): https://blog.pyodide.org/posts/314-release/
- Plataforma PyEmscripten: https://pyodide.org/en/latest/development/abi.html
- Wheels Pyodide en PyPI (nota de Simon Willison, 13-jun-2026): https://simonwillison.net/2026/Jun/13/publishing-wasm-wheels/
- manifold3d 3.5.5 en PyPI (wheels, WASM serial, licencia): https://pypi.org/project/manifold3d/
- mapbox-earcut en PyPI: https://pypi.org/project/mapbox-earcut/2.1.0/

**No consultado (bloqueado o sin acceso):** el código de `src/afm/`, `docs/PROGRESS.md`, y las páginas de PyPI de `fast-simplification` y `pymeshfix`. Esa ausencia se refleja en las marcas [P].

---

## 15. Próximo paso inmediato

Ejecutar la **Fase 0** (línea base, perfilado, inventario de imports, G0) y arrancar en paralelo la **Fase 1** con el intento de wheel de manifold3d. Al cumplirse G1 se confirma el camino (A o B) y se afina la estimación.
