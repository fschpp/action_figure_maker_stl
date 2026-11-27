# Plan: catálogos compartidos por internet (explorar, instalar y publicar piezas)

**Versión:** 0.3 — 2026-11-23
**Repo:** `fschpp/action_figure_maker_stl`, sobre `main` @ `1fc6d05` (motor 0.2.1) — ubicar en `docs/PLAN_catalogos_online.md`
**Continúa:** `docs/PLAN_piezas_intercambiables_catalogo.md` (catálogo local), `docs/PLAN_paleta_color_linea.md` (roles de color) y `docs/PLAN_port_pyodide.md` (versión web). Las tareas de este plan son **D00–D34** (las letras R, T, E, M y O ya las usan otros planes; D31–D34 se agregaron en la v0.3 y se numeran a continuación para no mover las anteriores).
**Inspiración:** la guía [Publishing a Mod](https://github.com/bryanthaboi/gen1recomp/wiki/Guide-Publishing) de gen1recomp y su [índice de mods](https://github.com/bryanthaboi/gen1recomp-mod-index) (estructura, CI, feed). §4 dice qué se toma y qué no.
**Marcas** (mismo criterio que los otros planes): **[C]** dicho por el cliente · **[V]** verificado leyendo el código o los docs de `main @ 1fc6d05` · **[I]** inferido, a validar · **[P]** pendiente de medir o decidir · **[G]** verificado leyendo el repo de gen1recomp o de su índice de mods el 2026-11-23 (no es código nuestro).

---

## 0. Qué cambió

### De la v0.2 a la v0.3 (esta versión)

La v0.3 incorpora lo que se pudo leer del **índice de mods de gen1recomp** (`bryanthaboi/gen1recomp-mod-index`: README, esquemas y lista de scripts) y de un índice personal basado en él (`ShaneMcGovernIE/my-mod-index`) [G]. La guía *Publishing* del wiki **no se pudo abrir** desde esta sesión: de ella se conserva lo que ya traía la v0.2 y el resumen que pasó el cliente [C]. Las tareas D00–D30 **no se renumeran**; lo nuevo es D31–D34 y retoques a D00, D02, D09–D13, D18, D21, D24, D26–D29.

| # | Cambio | Por qué |
|---|---|---|
| 1 | El índice guarda **una carpeta por paquete** `paquetes/<Autor>@<id>/` con `meta.json`, `descripcion.md` y `portada` (2 MB máx.) y **nada más**; antes era un `paquetes/<id>.json` con solo `{id, github}` (§5.5). | La alta se revisa como un cambio chico y legible; `meta.json` se valida contra un esquema; la carpeta se llama igual que el paquete instalado; `Autor@id` evita que dos autores se pisen el id y ayuda contra la suplantación. [G] |
| 2 | **`meta.json` es casi una copia de `paquete.json`** y lo genera `afm catalogo paquete enviar`; el bot lo compara con el manifiesto del Release (IX104). | Quien publica no escribe el mismo dato dos veces. [G] (el control de deriva es agregado nuestro) |
| 3 | **Publicar una versión nueva no pide PR**: se etiqueta un Release y el índice lo toma solo (en cada push y cada noche). El PR solo se repite si cambia el listado (descripción, categorías, portada, repo). El bot elige `<id>-<version>.zip` y, si no está, cualquier `.zip`; el autor puede seguir el último Release o **fijar** uno (`release_fijo`). | Un autor solo toca su propio repo. [G] Que el bot ignore borradores y deje los prerelease fuera de `latest` es decisión nuestra **[I]**. |
| 4 | **Feed en `data/index.json` de Pages**, nunca desde un clon ni `raw.githubusercontent`; cada entrada trae `latest` (versión, tag, prerelease, fecha, zip con nombre, URL, tamaño y `sha256`), `update_check` (`ok` · `off` · `sin release instalable` · `inaccesible` · `error: …`) y `descargas`. Una entrada con el upstream caído **lo dice, no desaparece**. | Portadas y descripciones solo existen en el sitio publicado; el cliente lee un solo archivo. [G] |
| 5 | **Evolución aditiva** del índice y de los formatos: solo se agregan claves, `schema_version` sube únicamente si se rompe algo existente y el cliente lee lo que conoce sin rechazar el índice entero (§5.1 punto 8). | El lector de gen1recomp compara `schema_version` por igualdad y rechaza el archivo completo ante cualquier diferencia; por eso agregaron `carts` sin subir la versión: una subida habría dejado sin pestaña de mods a quien no actualiza. [G] |
| 6 | **CI del índice con los mismos pasos que gen1recomp**, en Python y reutilizando `afm`: validar alta · comprobar enlaces · construir índice `--releases` · **puerta de releases** · salud · comprobar bloqueados; estado en `.salud/` (§5.5, D12, D31). | La puerta de releases escanea lo que el índice adopta **sin PR**; sin ella, un Release nuevo entraría sin control. [G] |
| 7 | **Salud con tolerancia (strikes)**: un enlace caído suma un strike y recién tras N controles fallidos (N en D-13) pasa a `inaccesible`: se oculta en «Explorar», no se borra, y vuelve solo si se recupera. | gen1recomp anota strikes y retira solo lo que siguió caído, con un job cada seis horas. [G] El umbral N es nuestro **[P]**. |
| 8 | **Descargas** como dato generado, nunca enviado por el autor: `total`, `reciente` (ventana de 30 días), `ventana_dias` real y `as_of`; **`null`, no 0**, cuando no hay contador; el estado guarda el **máximo visto por tag**. | El contador de GitHub se reinicia si se borra y vuelve a subir un asset y la API pagina: sin historial el total puede bajar. Sirve para ordenar «Explorar» sin telemetría. [G] |
| 9 | **Índices propios**: «Agregar índice» acepta `owner/repo` o la URL de Pages; hay `ejemplos/` y el repo del índice sirve de plantilla (D33). | Equipos, escuelas o colecciones privadas sin depender del oficial; es la forma descentralizada de gen1recomp (se pega `owner/repo` o la URL de Pages). [G] |
| 10 | **Colecciones** (`tipo: coleccion`): lista de paquetes fijados a versión y `sha256` exactos, sin activos propios (§5.12, D34). | Equivale a los *custom carts* de gen1recomp y es lo más barato para decirle a quien no diseña «instalá esto y listo». [G] |
| 11 | **Fuente externa fija** (`fuente.tipo = "url"`) para quien no usa GitHub Releases: sin seguimiento, con `sha256` en la alta y host en la lista permitida (§5.5). | gen1recomp admite un `downloadURL` directo cuando no hay `github`. Acá la integridad la da el `sha256` del índice. [G] |
| 12 | **Ayudante de alta en Pages** que arma `meta.json` y abre GitHub con el archivo precargado, sin OAuth en v1 (D32). | Bajar la barrera para quien no usa git. gen1recomp lo resuelve con un formulario que abre el PR (y un worker opcional para «Sign in with GitHub»). [G] |
| 13 | **Regla de contenido más estricta**: solo activos propios, con permiso, de dominio público, o **recetas de transformación** (`origen: referencia`); **D26 pasa de opcional a recomendada**. | Es la regla de gen1recomp tal como la resume el cliente [C] (originales o recetas de transformación; nada extraído) y lo único que resuelve §5.8 para personajes de terceros. |
| 14 | **`bloqueados.json` con motivo y fecha**, que bloquea por id, autor **o `sha256`**; el CI rechaza altas bloqueadas y la app compara en local los `sha256` de lo instalado. | gen1recomp lleva un `blocklist.json` con razón y fecha que el CI consulta. [G] Bloquear por `sha256` y comparar en la app es agregado nuestro **[I]**: un reclamo no se esquiva volviendo a subir lo mismo con otro nombre. |
| 15 | **«Listar no es verificar»**: el índice dice que alguien pidió listar el paquete, no que lo haya revisado una persona; la confirmación de instalación lo repite. | Mismo aviso que el README de gen1recomp. [G] |
| 16 | Se corrigió una inconsistencia: el campo del rango de motor se llama **`motor`** en todo el plan (en §4 y D02 figuraba como `afm`). | El ejemplo de `paquete.json` ya usaba `motor`. |

### De la v0.1 a la v0.2 (historial)

La v0.1 se escribió sin poder leer el repo (GitHub bloquea la lectura de `docs/` desde la web). Esta versión se hizo con un clon de `main`: se leyeron `README.md`, `THIRD_PARTY.md`, `PROGRESS.md` (parcial), `docs/PENDIENTES.md`, `docs/WEB_mantenimiento.md`, los planes de catálogo, color, caballos y Pyodide, y el código de `catalog.py`, `parts.py`, `colorroles.py`, `catalog_project.py`, `catalog_cli.py`, `gui/catalog_import.py`, `web/profiles.js` y los workflows.

| # | Cambio | Por qué |
|---|---|---|
| 1 | El paquete **no inventa metadatos**: reutiliza `afm-linea-1`, `afm-pieza-1`, `afm-paleta-1` y `afm-receta-1`; el manifiesto nuevo (`afm-paquete-1`) solo agrega lo que falta para compartir. | La v0.1 proponía un `manifest.json` con su propia lista de piezas, hashes y puertos: duplicaba lo que ya guarda cada `.pieza.json`. [V] |
| 2 | **Hoy el catálogo es una carpeta por línea; no existe exportar ni importar `.zip`.** El `catalogo.zip` / `proceres-127.zip` que se pasa a mano es una carpeta comprimida a mano. | `grep zipfile` solo encuentra `colorroles.py` (`.npz`), `bambu.py` (3MF) y `webapi.py` (resultados). La v0.1 daba por hecho «Importar .zip». [V] |
| 3 | La **unidad de compartir es la línea** (y sus piezas, recetas y paleta), no «una pieza suelta para cualquier catálogo». | Las piezas solo se acoplan dentro de una línea: altura de figura ±0,5 mm, perfil `Params`, contrato de interfaz (talles). [V] |
| 4 | **El color se comparte por roles** (`.color.npz` + `paleta.json`), no como FBX. | Un FBX pesa 26–36 MB por pieza; el paquete de roles, ≈0,2 MB (−99 %), y permite recolorear. [V] |
| 5 | Los **derivados locales no se comparten** (`compat.json`, `COMPATIBILIDAD.md`, `calibracion.json`, certificaciones, `.papelera/`, `CATALOGO.md`). | Son resultados generados; su clave incluye piezas, `Params`, contrato y versión del motor, así que se recalculan en la máquina de quien instala. [V] |
| 6 | Hay una **contradicción de política** que conviene resolver antes de abrir un índice público (§5.8 y D00). | `THIRD_PARTY.md` dice que las figuras de personajes de terceros no se distribuyen con el repo, pero `examples/` versiona `mickey*.stl`, `egon_*` y `kenner-real_ghostbusters-egon.proyecto`. [V] |
| 7 | La **versión web existe, pero no tiene catálogo**: `web/` solo lee el `params` de un `linea.json` (`profiles.js`) y `webapi.py` no expone nada del catálogo. La fase web se reduce a lo que sí es barato. | La v0.1 suponía que el catálogo ya corría en Pyodide. [V] |
| 8 | Se aprovecha lo que el repo ya hace: `pages.yml` baja wheels de **Releases** con `gh` y los sirve desde **Pages**; la GUI sigue el patrón `FORMS` + `run` en hilo (`catalog_import.py`); los textos van en es/en/pt con test. | Menos infraestructura nueva. [V] |
| 9 | Tareas con **fichas** (depende de / paralelo con / leer / hacer / pruebas / listo cuando) y patch `git am` por tarea, como `PLAN_caballos_compatibles.md`. | Convención del repo. [V] |
| 10 | Se descartó la CLI separada `afm pack …`: todo cuelga de `afm catalogo …`. | Ya hay más de 20 subcomandos bajo `afm catalogo` y es donde se busca. [V] |

---

## 1. Qué se quiere [C]

1. Que una persona **que no sabe diseñar** pueda, desde la app, **buscar** piezas y héroes publicados por otros, **descargarlos** e **instalarlos** en su catálogo, y armar juguetes con «Armar desde catálogo…» sin pasos manuales.
2. Que quien **sí diseña** pueda **publicar** su catálogo (o parte) por internet, sin montar un servidor.
3. Tomar como modelo cómo gen1recomp publica sus mods: paquete en `.zip`, manifiesto con versión, checklist de publicación, Releases de GitHub, instalación desde un gestor dentro de la app.

**No se quiere en esta versión:** tienda ni pagos, cuentas de usuario, comentarios o calificaciones dentro de la app, ejecución de código de terceros, ni cambiar piezas ya impresas (decisión D1 del plan de piezas intercambiables: el intercambio es **en el diseño**).

---

## 2. Estado verificado en `main @ 1fc6d05` [V]

| Tema | Qué hay hoy |
|---|---|
| Catálogo en disco | Una carpeta por línea: `linea.json` (`afm-linea-1`: nombre, `altura_mm`, `boquilla`, `params`, `lugares`, `motor`), `paleta.json` (`afm-paleta-1`, opcional), `piezas/<tipo>/<id>@<n>.{stl,pieza.json,fbx,color.npz,…}`, `figuras/<personaje>.receta.json` (`afm-receta-1`), y generados: `compat.json`, `COMPATIBILIDAD.md`, `CATALOGO.md`, `.papelera/`. Ruta por defecto `~/.local/share/afm/catalogos/<linea>` o `AFM_CATALOGO` (**una sola ruta para todas las líneas**). |
| Piezas | Inmutables: `add_piece` nunca sobrescribe y la versión nueva es la última + 1; cada ficha guarda `sha256` del STL, `bbox`, puertos con su cara, `origen` y, si hay color, un bloque `color` (`modo` `rgb` o `roles`). Ids con el patrón `[a-z0-9]+(-[a-z0-9]+)*` y referencia `id@n`. |
| Verificación | `Catalog.verify` / `afm catalogo verificar`: formato, tipo vs carpeta, nombre vs `id@versión`, STL presente, hash, malla estanca, puertos, caras, altura, paleta y existencia/hash del FBX. |
| Compatibilidad | Barata (metadatos: `ports.port_compat`, `afm catalogo cobertura/explicar`), completa (`build`+`validate`, caché `compat.json`), contrato de interfaz por línea (`afm catalogo contrato`) y certificación (`certify.py`). `LINE_HEIGHT_TOL_MM = 0.5`. |
| Color | FBX entero (`modo: rgb`) o paquete de roles `<id>@<v>.color.npz` (`modo: roles`), leído con `np.load(..., allow_pickle=False)`; todos los roles de una pieza tienen que existir en la `paleta.json` de la línea o `add_piece` no escribe nada. `afm catalogo paleta importar/exportar` ya existe. |
| Figuras y proyectos | Receta con ids `id@n` y `accesorios`; los `.proyecto` de catálogo guardan la **ruta absoluta** del catálogo (la ventana pide dónde está si cambió) y `AccessoryRef(catalogo, pieza, sha256)`. |
| Otros activos | Caballos en `<catalogo>/caballos/<id>/v<N>/{caballo.json,modelo.stl}` (`afm-caballo-1`), **fuera** del catálogo de piezas; accesorios/armas como piezas `accesorio` con `mecanismo.agarre`; monturas horneadas en torso/pelvis. |
| GUI | GTK4 (Linux). Lógica sin GTK + ventanas finas; `Administrar catálogo` tiene barra de menús por función; `gui/catalog_import.py` (`FORMS`, `parse`, `run` en hilo); `progress.py` para tareas largas. Textos en `src/afm/locales/{es,en,pt}.json` con `tests/test_i18n.py`. |
| Web | Pyodide 314 + three.js en GitHub Pages; sin catálogo. `pages.yml` descarga wheels de Releases en CI y publica `web/`. Límites de STL: 75 MB / 1,5 M de triángulos. |
| Licencia | App: GPL-3.0-or-later. `THIRD_PARTY.md` lista dependencias y declara que las figuras de terceros no se distribuyen. |
| Red | Hoy el motor y la GUI no hacen ninguna petición de red. |

---

## 3. Qué se comparte y qué no

| Activo | ¿Se comparte? | Notas |
|---|---|---|
| **Línea** (`linea.json` + `paleta.json`) | Sí | Define con qué es compatible todo lo demás. Una «línea base» puede publicarse sin piezas. |
| **Pieza** (`.stl`, `.pieza.json`, `.color.npz`, retoque) | Sí | Por defecto sin `.fbx`. |
| **Receta / héroe** (`figuras/*.receta.json`) | Sí | Con la lista de piezas que necesita. Es lo que más le sirve a quien no diseña. |
| **Accesorio / arma** | Sí | Mismo cuidado de licencias que cualquier pieza (§5.8). |
| **Paleta** | Sí | Dentro de la línea o suelta (ya hay importar/exportar). |
| **Colección** (lista de paquetes fijada a versiones exactas) | Sí | Solo metadatos: no trae activos (§5.12). |
| **FBX de color** | **No** por defecto | 26–36 MB, y suele ser el archivo con más problemas de derechos. Opción `--incluir-fbx` bajo un tope. Una pieza `modo: rgb` sin FBX se exporta **sin color** (con aviso); la ficha exportada saca el bloque `color` para que `verify` no lo marque como roto. |
| **Caballos** (`caballos/`) | Fase posterior | Biblioteca aparte con otro formato; no entra en v1 (D-10 en §8). |
| **`.proyecto`** | No | Guarda rutas absolutas. Lo que viaja es la receta. |
| `compat.json`, `COMPATIBILIDAD.md`, `calibracion.json`, `CATALOGO.md`, `.papelera/`, certificaciones | **Nunca** | Se regeneran. |

---

## 4. Qué se toma de gen1recomp (y qué no)

| gen1recomp | Aquí |
|---|---|
| Mod = carpeta con `manifest.json`; se distribuye como `.zip` con los archivos en la **raíz**; el launcher solo reconoce el nombre `.zip` | **Paquete** `.zip` con `paquete.json` y el **mismo árbol que el catálogo** (instalar = copiar y verificar). Siempre extensión `.zip`. |
| «Manifest hygiene»: `version` semver, `description`, perfil honesto, `games` solo si se probó, `experimental` | `version` semver, `descripcion`, `linea` (con la que **se probó**), `experimental`, `motor` (rango de motor). |
| `modkit validate --strict`, `lint`, `pack`; `pack` repite los gates y rechaza ante cualquier hallazgo | `afm catalogo paquete validar [--estricto]` y `afm catalogo exportar`, que repite los gates en estricto. |
| `set-github` + `add-release-workflow`: cada push publica un Release `mod-version.zip` | `afm catalogo paquete init` deja un workflow que valida y publica `<id>-<version>.zip` en un Release. |
| Dependencias con pista de repo (`dep@^1.0.0#owner/repo`) y alcance por juego | `requiere: ["id@^1.0.0#owner/repo"]`, con alcance por línea. |
| «Content rule»: solo activos originales o recetas de transformación; nada extraído de la ROM; el lint lo rechaza [C] | Regla de contenido (§5.8): licencia obligatoria, declaración de origen (incluye `referencia`), sin FBX/archivos no declarados; política sobre personajes de terceros. |
| `required_imports` (archivos que aporta la persona, verificados por hash) | **Piezas por referencia** (D26, opcional): el paquete trae solo la articulación y la persona aporta el STL original. |
| Versionado: patch/minor/major, migraciones, `conflicts` | Igual; además las piezas ya son inmutables, así que **actualizar solo agrega** `id@n` nuevos. |
| «Play-testing the failure paths» y «Check and recover» | Checklist de publicación y de instalación (§7), con instalación atómica y reversión. |
| Carril A (contenido, `validate` + checklist de pulido) y carril B (motor/API, con RFC) | Carril A: paquetes en el índice. Carril B: cambios al formato o a la app (RFC corto + compatibilidad hacia atrás + tests). |
| Gestor de mods dentro del juego: import/drag-and-drop, Update, Versions, errores por mod | «Explorar», «Paquetes instalados», arrastrar y soltar, Actualizar y Versiones. |
| **Índice** `gen1recomp-mod-index` [G]: carpeta `<Author>@<id>` con `meta.json`, `description.md` y `thumbnail` (2 MB); nada más; solo metadatos, sin código ni activos | Carpeta `<Autor>@<id>` con `meta.json`, `descripcion.md` y `portada`; nada más; sin zips ni mallas (§5.5). |
| `github` en el manifiesto activa el seguimiento de versiones en el índice y en el launcher; no hay PR por versión; `automatic_version_check` / `fixed_release_tag` [G] | Igual en el índice (`seguimiento`, `release_fijo`). **La app no consulta la API de GitHub**: lee el índice, que ya trae `latest` y un `sha256` independiente del servidor del autor (§5.5). |
| Feed `data/index.json` en Pages, generado en cada push y cada noche; `schema_version` y claves solo aditivas [G] | Igual (§5.5; regla en §5.1 punto 8). |
| Scripts `validate`, `check-links`, `build-index --releases`, `gate-releases`, `health --record --prune`, `check-blocklist`; estado en `.health/` [G] | Mismos pasos en Python con `afm` (D12, D31); estado en `.salud/`. |
| Índice personal: se pega `owner/repo` o la URL de Pages en «Find mods» [G] | «Agregar índice…» (D33). |
| *Custom carts*: mods fijados a build exacto con `sha256`, orden de carga y sello [G] | **Colecciones** (§5.12, D34); sin sello en v1. |
| `downloadURL` directo para un listado fijo (p. ej. GameBanana) [G] | `fuente.tipo = "url"` con `sha256` (§5.5). |
| «Listing is not vetting» [G] | La confirmación de instalación y el índice lo dicen (§5.5, §5.8). |
| **No aplica:** código Lua, hooks, `modData`, migraciones de saves, parity oracle, link play | Un paquete **no tiene código**. |

---

## 5. Diseño

### 5.1 Principios

1. **Solo datos.** El paquete no ejecuta nada (el único formato binario propio, `.color.npz`, ya se lee con `allow_pickle=False`).
2. **Nunca se sobrescribe.** Las piezas ya son inmutables; instalar solo agrega. Una colisión (`id@n` igual con otro hash) detiene la instalación.
3. **Instalación atómica y reversible:** staging dentro del catálogo → verificación → movimiento a su lugar; si algo falla, el catálogo queda como estaba.
4. **Offline primero.** Todo lo instalado funciona sin red; la red solo se usa al explorar, instalar o actualizar. Sin telemetría.
5. **Nada se instala sin confirmación** que muestre autor, licencia, tamaño, qué agrega, qué línea toca y qué conflictos hay.
6. **Descentralizado.** El proyecto mantiene un **índice liviano** que apunta a repos; los archivos viven en el repo/Release de cada autor. Cualquiera puede copiar la estructura del índice y alojar el suyo (D33).
7. **Un catálogo sin red ni paquetes se comporta como hoy.** Los catálogos actuales y los `.proyecto` existentes no cambian.
8. **Evolución aditiva.** El formato del paquete y el del índice solo **agregan** claves: no se reutilizan ni cambian de significado. `afm-paquete-1` y `schema_version` solo suben ante un cambio que rompe lo existente, y cada lector ignora lo que no conoce (con aviso en un paquete, en silencio en el índice). Una subida de versión no puede dejar sin «Explorar» a quien no actualizó la app. [G]
9. **Listar no es verificar.** Estar en el índice significa que alguien pidió listar el paquete y que pasó los controles automáticos de §6, no que una persona haya revisado su contenido. [G]

### 5.2 El paquete `afm-paquete-1`

Un `.zip` con los archivos **en la raíz**, con el árbol del catálogo:

```text
paquete.json                      # afm-paquete-1 (nuevo)
README.md  LICENSE  CREDITOS.md   # obligatorios los dos primeros; CREDITOS si hay más de un autor o licencia
linea.json                        # opcional: si el paquete trae/define la línea
paleta.json                       # opcional
piezas/<tipo>/<id>@<n>.stl
piezas/<tipo>/<id>@<n>.pieza.json
piezas/<tipo>/<id>@<n>.color.npz  # opcional (modo roles)
figuras/<personaje>.receta.json   # opcional
previews/portada.png              # 1280×720
previews/<id>@<n>.png             # miniatura por pieza
```

`paquete.json` (propuesta; los nombres de campos se fijan en D02):

```json
{
  "formato": "afm-paquete-1",
  "id": "proceres-127-guemes",
  "nombre": "Próceres 127 — Güemes",
  "version": "1.2.0",
  "descripcion": "Héroe completo de Güemes para la línea proceres-127.",
  "tipo": "heroe",
  "autores": [{"nombre": "…", "github": "owner"}],
  "licencia": "CC-BY-4.0",
  "origen": "propia",
  "github": "owner/repo",
  "linea": {"nombre": "proceres-127", "altura_mm": 127.0},
  "motor": ">=0.2.1",
  "contenido": {"piezas": ["cabeza-guemes-01@1"], "recetas": ["guemes"], "paleta": true},
  "requiere": ["proceres-127-base@^1.0.0#owner/repo"],
  "conflicts": [],
  "experimental": false,
  "categorias": ["heroes"],
  "etiquetas": ["prócer", "histórico"],
  "archivos": {"piezas/cabeza/cabeza-guemes-01@1.stl": "<sha256>"}
}
```

- `tipo`: `linea` (perfil + paleta, con o sin piezas), `piezas` (piezas sueltas de una línea), `heroe` (receta + todas las piezas que necesita) o `coleccion` (solo pines a otros paquetes, §5.12). Es una etiqueta para el explorador, no una restricción.
- `categorias`: de 1 a 4 valores de una **lista cerrada** (propuesta en D-14) para filtrar en «Explorar»; `etiquetas` queda libre. [G]
- `origen`: `propia` · `derivada` · `dominio-publico` · `con-permiso` · `referencia` (el paquete trae solo la receta de articulación y la persona aporta su propio archivo, §5.11). Se muestra en la confirmación.
- `licencia`: identificador SPDX (`CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, `CC-BY-NC-4.0`, …) o `todos-los-derechos-reservados` con permiso de distribución (§5.8).
- `archivos`: `sha256` de **cada** archivo del zip (además del `sha256` del STL que ya lleva cada ficha de pieza).
- Reglas de higiene al estilo gen1recomp: `version` semver real, descripción presentable, `linea` solo con la que **se armó y validó**, `experimental: true` ⇒ no queda activo hasta que la persona confirme.
- Lectura tolerante: una clave desconocida en `paquete.json` **no** invalida el paquete (aviso); solo un `formato` de otra versión mayor se rechaza (§5.3 punto 1).

### 5.3 Reglas al instalar (compatibilidad)

Todo sale de reglas que el catálogo ya aplica [V]:

1. **Formatos conocidos.** `formato` de cada ficha en `afm-pieza-1`; un formato más nuevo que el que entiende la app ⇒ rechazo con mensaje claro («este paquete requiere una versión más reciente de afm»). Agregar claves opcionales **no** cambia el `formato` (§5.1 punto 8): las claves desconocidas se ignoran con aviso.
2. **Línea de destino.** `|altura_mm paquete − altura_mm catálogo| ≤ LINE_HEIGHT_TOL_MM (0,5)` y `ref_height` igual; si no, no entra en ese catálogo y se ofrece **instalar como línea nueva**.
3. **Perfil.** Si el paquete trae `linea.json`, se comparan los `params`: diferencias de pasadores u holgura son **aviso** (las piezas son crudas y el perfil de la línea local gana, como en el plan de piezas); `ref_height` distinto es **error**.
4. **Paleta.** Cada pieza `modo: roles` exige que sus roles existan en la paleta de destino; si faltan, el instalador propone agregarlos desde la `paleta.json` del paquete (con el hex del paquete) y, si el id ya existe con otro hex, **gana el local** y se avisa.
5. **Colisiones.** `id@n` ya instalado con el mismo `sha256` ⇒ se omite; con otro `sha256` ⇒ **conflicto**: se detiene y se ofrece instalar como línea nueva. (Renombrar con prefijo queda para más adelante: hay que reescribir ficha, nombres de archivo y recetas.)
6. **Numeración.** `add_piece` asigna siempre «última + 1», pero un paquete puede traer `pierna-x-01@3` sin el `@1`: el instalador escribe con `parts.write_piece` **conservando el número** y debe comprobar que `Catalog.versions` tolera huecos **[P]** (D05).
7. **Después de instalar:** correr `verificar` sobre lo agregado y mostrar `cobertura` de las piezas nuevas contra el catálogo (compatibilidad barata). Las certificaciones de `compat.json` cuya clave incluía piezas/`Params` quedan «vencidas» por construcción.
8. **Motor.** Si `pieza.origen.motor` o `paquete.motor` es más nuevo que el motor local ⇒ aviso.

### 5.4 Registro local de lo instalado

`<catálogo>/paquetes.json` (estado local, **no se comparte**): por paquete, `id`, `version`, `github`, **índice de origen** (URL), `sha256` del zip, fecha, `refs` instaladas (piezas, recetas, roles de paleta agregados). Sirve para **Actualizar**, **Versiones**, **Quitar** (solo lo que el paquete agregó y que ninguna receta de otro paquete use; reutiliza `usos` y la papelera) y para saber el origen de una pieza. Que `verify` tolere un archivo desconocido en la raíz **[P]** (D05).

### 5.5 Índice

Repo aparte (`afm-catalogo-indice`; nombre y cuenta a decidir en D00) con GitHub Pages. **Solo metadatos**: ni zips, ni mallas, ni imágenes de autores sin reencodar. La estructura sigue la de `gen1recomp-mod-index` [G]:

```text
paquetes/<Autor>@<id>/
    meta.json          # obligatorio: la entrada (casi una copia de paquete.json) + campos de listado
    descripcion.md     # obligatorio: texto largo en markdown
    portada.png        # opcional (o .jpg), 2 MB máx.
ejemplos/TuNombre@paquete-ejemplo/   # carpeta modelo para copiar
esquemas/              # JSON Schema de meta.json, index.json y fichas: fuente de verdad del CI y del sitio
scripts/               # validar_alta · comprobar_enlaces · construir_indice · puerta_releases · salud · comprobar_bloqueados
bloqueados.json        # lo que el CI se niega a listar, con motivo y fecha
.salud/                # estado del bot: strikes, descargas acumuladas, última versión que pasó la puerta
sitio/                 # galería y ayudante de alta (Pages)
```

Lo que se **publica** en Pages, y lo único que lee la app:

```text
data/index.json
data/fichas/<Autor>@<id>-<ver>.json      # fichas de pieza sin mallas (compatibilidad antes de descargar)
data/paquetes/<Autor>@<id>/portada.png   # miniatura reencodada por el bot
data/paquetes/<Autor>@<id>/descripcion.md
```

- **Nombre de carpeta.** La mitad `<id>` es el `id` de `paquete.json` y la mitad `<Autor>` el dueño de `github` (o `autor` si hay `fuente` fija): lo listado y lo instalado se llaman igual. Dentro de la carpeta **no puede haber nada más** (IX103). [G]
- **`meta.json`:** `id`, `nombre`, `autor`, `version` (la del Release que describe), `categorias`, `github` (recomendado: activa el seguimiento) **o** `fuente`, `descripcion` corta, `etiquetas`, `licencia`, `origen`, `tipo`, `linea`, `motor`, `requiere`, `conflicts`, `experimental` (copiados de `paquete.json` para avisar **antes** de bajar nada) y `seguimiento`: `ultimo` (por defecto) o `fijo` con `release_fijo`. Lo genera `afm catalogo paquete enviar`. [G]
- **Alta una sola vez** (PR o ayudante web, D32). **Las versiones nuevas no piden PR**: el autor etiqueta un Release en su repo y el índice lo toma. Se vuelve a abrir un PR solo si cambia el listado (descripción, categorías, portada, repo). [G]
- **Modelo pull.** En cada push y cada noche, el bot lee con la API los Releases de cada entrada con `github`, elige el asset `<id>-<version>.zip` (si no está, cualquier `.zip`), ignora borradores y deja los prerelease fuera de `latest` **[I]**, baja el zip, corre `afm catalogo paquete validar --estricto` (la **puerta**, que también se aplica a lo que se adopta sin PR), calcula `sha256` y `tamano`, genera las fichas, extrae y reencoda la portada y regenera `index.json`. Lo que falla no entra: la entrada conserva su última versión buena y su `update_check` dice `error: …`. El despliegue a Pages se hace solo si la corrida entera terminó bien. Esto es lo que gen1recomp llama «gallery lane A». [G]
- **Fuente fija (`fuente`)** para quien no usa GitHub Releases: `{"tipo": "url", "url": "https://…", "sha256": "…"}`. Es un listado **sin seguimiento**: una versión nueva es un PR nuevo; el host debe estar en la lista permitida y el `sha256` va en la alta (IX108). [G]/[I]
- **Entrada de `index.json`:** `folder`, `id`, `nombre`, todos los campos de `meta.json`, `portada` y `descripcion_url` (rutas bajo `data/`), `latest` (`version`, `tag`, `prerelease`, `publicado`, `zip`: `nombre`, `url`, `tamano`, `sha256`), `versiones`, `retiradas`, `update_check` (`ok` · `off` · `sin release instalable` · `inaccesible` · `error: …`), `descargas` y el resumen de piezas (id, tipo, radios de puertos). Encabezado: `schema_version`, `generado`, `cantidad`, `categorias`. [G] Clave **aditiva** `bloqueados` (D16, la escribe el bot desde `bloqueados.json`): `[{id | autor | sha256, motivo, fecha}]` con exactamente una de las tres claves; el cliente no instala ni muestra lo bloqueado y avisa de lo ya instalado (`afm catalogo remoto auditar`). **[I]**
- **`descargas`:** generado, nunca enviado por el autor ni guardado dentro de `paquetes/`. `total` (suma de los assets `.zip` de todos los Releases vistos), `reciente` (lo ganado desde la muestra más vieja de al menos 30 días), `ventana_dias` (cuánto duró de verdad esa ventana: al principio el historial es corto) y `as_of`. Es **`null`, no 0**, cuando no hay contador (fuente fija, zips autogenerados del código) y esas entradas se ordenan al final, no como «poco populares». El estado guarda **el máximo visto por tag** en `.salud/descargas.json`: GitHub reinicia el contador si se borra y vuelve a subir un asset y la API pagina, así que sin ese historial el total puede bajar. Cuesta una petición extra: sale de la misma respuesta de Releases que ya lee el bot. [G]
- **Fichas:** un JSON con todas las `.pieza.json` del paquete (sin mallas) permite calcular la compatibilidad **antes de descargar** los STL y mostrar cuántas piezas encajan en el catálogo propio.
- **Salud (cada 6 horas).** Comprueba que el repo, el Release y el zip respondan. Un fallo suma un strike en `.salud/estado.json`; tras N controles fallidos (N en D-13) la entrada pasa a `inaccesible`: se oculta en «Explorar» pero **no se borra**, y vuelve sola si se recupera. Quitar una carpeta del índice es siempre una decisión humana (PR). [G]
- **Se consume por HTTP desde Pages**, no desde un clon ni desde `raw.githubusercontent`: portadas y descripciones solo existen en el sitio publicado y la copia del repo es un resultado de compilación. [G]
- **Varios índices.** La app acepta una lista de índices (por defecto el oficial). «Agregar índice…» acepta `owner/repo` (se resuelve a la URL de Pages del repo **[I]**) o una URL; cualquiera puede copiar la estructura y alojar el suyo en Pages (D33). [G]
- **`retiradas`** (yank): versiones que no deben instalarse; se avisa si ya están instaladas, pero **no se borra nada solo**.
- **`bloqueados.json`:** entradas `{"id" | "autor" | "sha256", "motivo", "fecha"}`; el CI rechaza altas que coincidan y retira las ya listadas. [G]/[I]
- **La app no usa la API de GitHub**: lee archivos estáticos (la API sin autenticación tiene un tope bajo de consultas por hora **[I]**) y el `sha256` sale del índice, no del servidor del autor. Solo el bot usa la API. gen1recomp, en cambio, consulta los Releases desde el launcher para los mods instalados [G]; acá se prefiere el índice por el tope de la API y por tener un `sha256` independiente.
- **Listar no es verificar** (§5.1 punto 9): se muestra en la ficha de «Explorar» y en la confirmación de instalación. [G]

Los códigos de lint del índice (`IX…`) están en §6.

### 5.6 Cliente, CLI y GUI

**Módulos nuevos** (nombres propuestos; sin GTK ni `manifold3d` fuera de `solids.py`, como pide `test_engine_portability.py`):

| Módulo | Qué hace |
|---|---|
| `src/afm/paquete.py` | `Paquete` (`afm-paquete-1`): carga, validación, lint (códigos `CK…`), construcción desde un catálogo. |
| `src/afm/instalar.py` | `plan_install(zip, catálogo) → InstallPlan` (sin escribir) y `apply` atómico; registro `paquetes.json`. |
| `src/afm/remoto.py` | Índice y descargas con `urllib` (sin dependencias nuevas): caché con ETag, lista de hosts permitidos, `sha256` obligatorio, modo sin red, **lectura tolerante** (solo `schema_version` y las entradas; claves desconocidas se ignoran) y varios índices cada uno con su host. |
| `src/afm/paquete_cli.py` | Subcomandos, para no engordar `catalog_cli.py`; se enchufa desde `add_parsers`. |
| `src/afm/gui/catalog_online.py` | Lógica de la GUI sin GTK (estado, filtros, instalación en hilo) y sus `FORMS`. |
| `src/afm/gui/catalog_online_window.py` | Ventana «Explorar catálogos en línea…» (fina). |

**CLI** (todo bajo `afm catalogo`):

```text
afm catalogo exportar RUTA -o paquete.zip [--piezas A,B | --receta guemes | --linea-completa] [--incluir-fbx]
afm catalogo paquete init DIR --id ID [--perfil heroe|piezas|linea|coleccion]  # README, LICENSE, workflow de Release
afm catalogo paquete github OWNER/REPO [DIR]                              # fija "github" y agrega el workflow (como set-github + add-release-workflow)
afm catalogo paquete validar DIR|zip [--estricto]                         # lint de contenido y pulido
afm catalogo instalar paquete.zip [--en RUTA | --como-linea] [--simular]  # --simular = solo el plan
afm catalogo remoto buscar [texto] [--tipo T] [--linea L] [--licencia SPDX]
afm catalogo remoto ver ID      |  instalar ID[@ver]  |  actualizar [ID]  |  versiones ID
afm catalogo remoto indice agregar owner/repo|URL  |  quitar URL  |  listar
afm catalogo paquete enviar DIR                                           # genera paquetes/<Autor>@<id>/ (meta.json, descripcion.md, portada) y abre el PR
```

**GUI.** En la pestaña Catálogo: botón «Explorar catálogos en línea…» (ventana propia, como «Administrar catálogo…») y, dentro de «Administrar catálogo», un menú **Compartir** con «Importar paquete .zip…», «Exportar paquete…», «Paquetes instalados» y «Publicar…». Arrastrar y soltar un `.zip` sobre la ventana abre la misma confirmación. Esquema:

```text
┌─ Explorar catálogos en línea ────────────────────────────────────────────────┐
│ [buscar…            ]  Tipo ▾  Línea ▾  Licencia ▾   ☑ Solo compatibles con mi catálogo │
├───────────────────────────────┬──────────────────────────────────────────────┤
│ ▣ Güemes (héroe)              │ Próceres 127 — Güemes            v1.2.0      │
│   proceres-127 · CC BY · 18 MB│ por … · CC BY 4.0 · 18 MB                    │
│ ▣ Armas de época (piezas)     │ [portada / miniaturas]                       │
│ ▣ Línea proceres-127 (base)   │ Trae: 6 piezas, 1 receta, paleta (5 roles)   │
│                               │ Encaja en tu catálogo: 6/6 ✔  Requiere: base │
│ Sin red: índice guardado el … │ [Instalar]  [Ver en GitHub]                  │
└───────────────────────────────┴──────────────────────────────────────────────┘
```

Estados de cada tarjeta: no instalado · instalado · actualización disponible · versión retirada · experimental · **inaccesible** · incompatible con tu motor o tu línea. La tarjeta muestra las descargas (orden «Más descargados»; las entradas sin contador van al final), las categorías y el aviso «listado no verificado»; la ventana tiene «Agregar índice…» (D33). Si se abre una receta o un proyecto con piezas que faltan, la app ofrece **buscarlas en el índice** por `id@n`.

### 5.7 Seguridad

| Riesgo | Mitigación |
|---|---|
| Zip malicioso | Validar cada ruta (sin `..`, absolutas, ni caracteres de control; sin enlaces simbólicos, cifrados, zips anidados ni duplicados por mayúsculas); tope de archivos, de tamaño por archivo y **descomprimido total**; extracción solo a staging. |
| Ejecución de código | No hay. Lista cerrada de extensiones: `.json .md .txt .stl .npz .png .jpg` (y `.fbx` solo con `--incluir-fbx`); lint rechaza el resto. `.npz` solo con `allow_pickle=False` (ya es así). |
| Archivo alterado o cambiado en tránsito | HTTPS obligatorio; `sha256` del zip tomado del **índice**, no del propio servidor; `sha256` por archivo en `paquete.json` y el de cada STL en su ficha. |
| Mallas que cuelgan el motor | Antes de instalar: `verify` (estanca, puertos, caras) con tope de triángulos por pieza y de tiempo; imágenes con tope de píxeles (Pillow). |
| Hosts inesperados | Lista de permitidos: el host del índice, `github.com`, `raw.githubusercontent.com` y los hosts a los que GitHub redirige los assets (`objects.githubusercontent.com`, `release-assets.githubusercontent.com` **[I]**); redirecciones fuera de la lista se rechazan. |
| Índice de terceros malicioso o con datos falsos | Al agregarlo se muestra una advertencia («listar no es verificar»); solo puede apuntar a la lista de hosts permitidos y a su propio host; nada de lo que trae se ejecuta; la confirmación de instalación se muestra igual. |
| Zip cambiado después de indexarlo | El `sha256` del índice no coincide ⇒ se rechaza con «el paquete cambió desde que se indexó; probá más tarde»; el bot lo detecta en la corrida siguiente. |
| Imágenes de autores (rastreadores, bombas de píxeles) | La app no carga imágenes del repo del autor: solo la miniatura que el bot extrajo del zip y reencodó (tope de píxeles y de 2 MB), servida desde Pages. |
| Índice que cambia de formato | Lectura tolerante (§5.1 punto 8): dentro de la misma versión mayor se ignoran las claves desconocidas; solo una `schema_version` mayor se rechaza, con aviso de actualizar `afm`. |
| Instalación a medias | Staging + movimiento atómico + `paquetes.json` escrito al final; falla ⇒ se limpia el staging y el catálogo no cambia. |
| Suplantación (mismo `id`, otro repo) | El índice liga `id ↔ github`; si un paquete instalado cambia de origen, se pide confirmación explícita. |
| Privacidad | Sin telemetría; solo se piden `index.json`, fichas, imágenes y los zips elegidos. |

### 5.8 Licencias y contenido

- **Licencia obligatoria** en cada paquete (SPDX) y **declaración de origen** (`propia`/`derivada`/`dominio-publico`/`con-permiso`). Sin ellas, `validar` falla.
- **La app es GPL-3.0-or-later; los activos tienen la licencia de su autor.** La app no incrusta ni redistribuye activos de terceros; se deja escrito en la documentación (no es asesoramiento legal).
- **Licencia efectiva del armado:** al armar con piezas de varios paquetes, la app calcula la restricción más fuerte (una `CC-BY-NC` ⇒ resultado no comercial; una `CC-BY-SA` ⇒ compartir igual), la muestra antes de exportar y escribe un `CREDITOS.txt` junto al STL, ampliando la `lista_de_piezas` que `export` ya genera (D23).
- **Regla de contenido.** Un paquete trae solo activos **propios**, **con permiso**, de **dominio público**, o **recetas de transformación** (articulación y referencia sin el archivo original); nunca activos extraídos o escaneados de terceros sin permiso. Es la regla de gen1recomp tal como la resume el cliente [C]; el lint la hace cumplir con `origen`, la lista cerrada de extensiones y CK304.
- **Personajes de terceros** (franquicias, películas, videojuegos): el índice oficial **no los aloja ni los lista**; se publican solo con permiso del titular, o como **pieza por referencia** (D26, `origen: referencia`, **recomendada**), donde el autor comparte la articulación y la persona aporta su propio STL verificado por hash.
- **Retiro:** `bloqueados.json` en el índice (con **motivo y fecha** [G]) + proceso documentado de reclamos (abrir un issue ⇒ se retira del índice ⇒ la app avisa en los equipos que lo tengan instalado). Bloquea por `id`, por autor o por `sha256` **[I]**, y la app compara en local los `sha256` de las piezas instaladas con esa lista (sin enviar nada) para avisar aunque el paquete se haya importado a mano.
- **Listar no es verificar.** El índice oficial no revisa el contenido de cada paquete a mano; lo dice en la ficha y en la confirmación. [G]
- **Política del propio repo [V]:** `THIRD_PARTY.md` afirma que las figuras de terceros usadas como casos de estudio no se distribuyen con el repo; `examples/` versiona `mickey*.stl`, `egon_fixed.stl`, `egon_lq_textura.fbx` y `kenner-real_ghostbusters-egon.proyecto`. Hay que decidir (D00) si se declaran con su origen y permiso, se sacan del repo (y de la historia, que es otra operación) o se corrige el texto. El índice público hereda la política que se fije acá.

### 5.9 Versionado, actualización y retiro

- **Paquete:** semver. *patch* = correcciones de fichas o previews; *minor* = piezas o recetas nuevas; *major* = se quitan piezas o cambia la línea.
- **Pieza:** conserva `id@n`; un cambio de geometría ya es una `@n` nueva. Por eso **actualizar un paquete es aditivo**: agrega archivos nuevos y no toca las recetas locales que apuntan a versiones anteriores. Lo que ya estaba en uso no se rompe.
- **Manifiesto:** campo `formato` (`afm-paquete-1`); un formato desconocido se rechaza con mensaje claro; los cambios del formato siguen el carril B (§4).
- **Dependencias:** rangos semver simples, una versión por `id`, sin ciclos; conflicto ⇒ no se instala nada.
- **Retiradas:** el índice marca versiones con problema; la app avisa, no borra.

### 5.10 Versión web (Pyodide / Pages)

Hoy la web no tiene catálogo ni «armar desde catálogo» [V]; llevarlo a Pyodide es un plan propio (memoria, tiempos, almacenamiento). Para este plan:

1. **Galería estática en Pages** generada desde `index.json`: la gente la ve sin instalar nada y obtiene el enlace de descarga y el botón «Abrir en la app» (D27). La misma página puede ordenar por descargas y alojar el ayudante de alta (D32).
2. **El índice y las fichas se sirven desde Pages** (CORS abierto **[I]**); los `.zip` también se **espejan en Pages** en el deploy, como `pages.yml` ya hace con los wheels [V], porque descargar assets de Releases desde un navegador puede estar bloqueado por CORS **[P]**: D01 lo mide y decide si hace falta el campo `espejo` en el índice.
3. Instalar paquetes **dentro** de la web queda fuera de v1 (D-11 en §8).

### 5.11 Piezas por referencia (recomendada, D26)

Para trabajos derivados de modelos que no se pueden redistribuir: `paquete.json` declara `referencias_requeridas` (`id`, nombre, `sha256` aceptados, tamaño máximo); el paquete trae solo planos, puertos, pasadores y roles. La persona importa el STL con un control «Importar archivo requerido»; la app verifica el hash y lo guarda en una carpeta de estado local, **nunca dentro del paquete** (el lint lo rechaza). Hasta que el STL esté, la pieza aparece «incompleta» y no se puede armar. Es el equivalente de las *asset transformation recipes* y de `required_imports` (archivos que aporta la persona, verificados por hash) de gen1recomp [C].

### 5.12 Colecciones (D34)

Una **colección** es un paquete `tipo: coleccion` que no trae piezas: solo **fija** otros paquetes a versión y `sha256` exactos, en el orden en que se instalan. Sirve para decirle a quien no diseña «instalá este héroe con todo lo que necesita y la línea base», y para que dos personas armen exactamente lo mismo. Es el equivalente de los *custom carts* de gen1recomp (mods fijados a builds exactos con `sha256`, que se publican por el mismo CI y el mismo feed) [G].

```json
"contenido": {"pines": [
  {"id": "proceres-127-base",   "github": "owner/repo", "version": "1.0.0", "sha256": "…"},
  {"id": "proceres-127-guemes", "github": "owner/repo", "version": "1.2.0", "sha256": "…"}
]}
```

- No trae STL, fichas ni `.color.npz` (CK403); su `linea` se toma de los pines, que deben coincidir entre sí.
- **Instalar una colección** arma un **único plan** con el plan de cada pin (conflictos y avisos agregados, una sola confirmación) y es todo o nada; cada pin pasa por la misma instalación que un `.zip` suelto.
- El CI del índice comprueba que cada pin **exista y que su `sha256` coincida** con el zip del Release (CK402); un pin retirado o bloqueado deja a la colección fuera.
- Una colección nueva es una versión nueva del paquete: actualizar es aditivo como cualquier paquete (§5.9). Sin «sello» ni opciones congeladas en v1.

---

## 6. Códigos de lint (`CK…`, propuesta)

| Código | Regla | Severidad |
|---|---|---|
| CK101 | Falta `licencia` o no es SPDX/valor permitido | error |
| CK102 | Falta README, descripción o `origen` | error |
| CK103 | `version` no es semver | error |
| CK104 | Falta `linea` o su altura no coincide con las de las piezas (±0,5 mm) | error |
| CK105 | Falta portada o miniatura de alguna pieza | aviso (error con `--estricto`) |
| CK106 | `categorias` fuera de la lista cerrada o fuera de 1–4 | error |
| CK107 | `origen: referencia` sin `referencias_requeridas`, o al revés | error |
| CK201 | `sha256` de `archivos` no coincide con el archivo | error |
| CK202 | Archivo en el zip que no está declarado, o declarado y ausente | error |
| CK203 | `Catalog.verify` falla sobre el contenido (malla no estanca, puertos, caras, tipo/carpeta, nombre ≠ `id@versión`) | error |
| CK204 | Rol de color sin definir en la paleta del paquete | error |
| CK301 | Extensión no permitida | error |
| CK302 | Ruta con `..`, absoluta, enlace simbólico o carácter de control | error |
| CK303 | Tamaño por archivo, total o cantidad de archivos sobre el tope | error |
| CK304 | Archivo de una referencia requerida empaquetado | error |
| CK305 | Piezas con licencias distintas sin `CREDITOS.md` | error |
| CK306 | `requiere` sin pista de repo y fuera del índice | aviso |
| CK307 | Ficha con bloque `color` que apunta a un FBX no incluido | error (el exportador lo corrige solo) |
| CK402 | Pin de una colección que no resuelve o cuyo `sha256` no coincide | error |
| CK403 | Colección que trae archivos de piezas, fichas o color | error |

### Códigos del índice (`IX…`, CI del repo del índice)

| Código | Regla | Severidad |
|---|---|---|
| IX101 | `meta.json` no valida contra el esquema, o falta `descripcion.md` | error |
| IX102 | `<Autor>@<id>` no coincide con el `id` ni con el dueño de `github` (o `autor` si hay `fuente`) | error |
| IX103 | La carpeta tiene un archivo que no es `meta.json`, `descripcion.md` o `portada.png/.jpg`, o tiene subcarpetas | error |
| IX104 | `meta.json` difiere del `paquete.json` del último Release (deriva) | aviso; error si cambian `licencia`, `origen` o `linea` |
| IX105 | El repo, el Release o el zip no resuelven, o el asset no es `<id>-<version>.zip` ni un `.zip` | error en la alta; strike en la corrida programada (D31) |
| IX106 | `id`, autor o `sha256` presentes en `bloqueados.json` | error |
| IX107 | Portada de más de 2 MB o sobre el tope de píxeles | error |
| IX108 | `fuente.url` con un host fuera de la lista permitida, o sin `sha256` | error |

Además, todo Release que el bot adopta pasa `afm catalogo paquete validar --estricto` (la puerta de releases); si falla, la entrada conserva su última versión buena.

---

## 7. Pruebas

**Automáticas** (`tests/test_paquete.py`, `test_instalar.py`, `test_remoto.py`, `test_catalog_online.py`; fixtures con `catalogo/ejemplo`, el muñeco sintético del repo):

- Ida y vuelta: exportar → instalar en un catálogo vacío ⇒ `verificar` limpio y mismos `sha256`.
- Un fixture por código CK; zips maliciosos (zip-slip, bombas, enlaces, extensiones, duplicados).
- Instalación atómica: excepción simulada en cada paso ⇒ catálogo idéntico al anterior.
- Colisiones (mismo `id@n` con distinto hash), huecos de versión, roles de color faltantes, línea de otra altura, `formato` futuro, motor más nuevo.
- Cliente: servidor `http.server` local (índice, ETag/304, hash que no coincide, redirección fuera de la lista, timeout, sin red con caché).
- GUI sin GTK con `FakeScene` y `tests/test_i18n.py` verde con es/en/pt.
- Actualización aditiva: instalar `1.0.0` y luego `1.1.0` no toca lo ya usado por una receta.
- Repo del índice: una alta válida; un fixture por código `IX`; Release nuevo **sin PR** ⇒ entra tras la corrida; Release inválido ⇒ la entrada conserva su última versión buena; si falla la corrida entera, no se despliega.
- Compatibilidad hacia atrás del feed: un `index.json` con claves nuevas lo lee el cliente actual; uno con `schema_version` mayor se rechaza con mensaje claro.
- Salud y descargas: repo caído 1 vez, N veces y recuperado; contador que baja por un asset re-subido ⇒ el total no retrocede; `null` y 0 se distinguen y se ordenan distinto.
- Colecciones: pin que no resuelve, `sha256` distinto, pin bloqueado, instalación todo o nada.

**Checklist de publicación** (a lo «play-testing the failure paths»):

1. Instalar el paquete en un catálogo **vacío** y armar una figura con «Armar desde catálogo…».
2. Instalarlo en un catálogo que ya tiene piezas de otro paquete de la misma línea y correr `cobertura`.
3. Instalar una versión nueva encima de una vieja con una receta ya armada.
4. Cortar la red a mitad de la descarga y reintentar.
5. Quitar el paquete y comprobar que no quedan archivos huérfanos ni recetas rotas.
6. Probar en GTK real (no se puede en el entorno de desarrollo): ventana «Explorar», diálogo de confirmación, arrastrar y soltar.
7. Etiquetar un Release nuevo **sin abrir un PR** y comprobar que el índice lo toma en menos de un día.
8. Dejar inaccesible el repo de prueba y comprobar que la entrada pasa a `inaccesible` tras los strikes sin desaparecer, y que vuelve al recuperarse.
9. Agregar un índice propio por `owner/repo` en una app que nunca vio el oficial, instalar de ahí y quitarlo sin perder lo instalado.

---

## 8. Riesgos y decisiones abiertas

| # | Tema | Riesgo / pregunta | Propuesta |
|---|---|---|---|
| D-1 | Política de contenido de terceros | §5.8: contradicción entre `THIRD_PARTY.md` y `examples/` | D00, antes de publicar nada |
| D-2 | Licencia por defecto y `NC` | ¿Se acepta `CC-BY-NC` en el índice oficial? ¿Cuál se sugiere? | `CC-BY-4.0` sugerida; `NC` permitida pero filtrable |
| D-3 | Ids repetidos entre autores | En el **índice** `Autor@id` ya los separa; en el **catálogo** dos autores pueden crear `pierna-guemes-01@1` distintas | v1: conflicto ⇒ línea nueva; luego, renombrado con prefijo |
| D-4 | Una sola `AFM_CATALOGO` | Con esa variable, `default_root` devuelve la misma ruta para todas las líneas | El instalador usa rutas explícitas `…/catalogos/<linea>` y avisa si la variable está puesta |
| D-5 | Tamaño | Sin medir un catálogo real (`proceres-127`), los topes son supuestos | D01 mide peso por pieza y por línea |
| D-6 | CORS en la web | Releases desde el navegador **[P]** | Espejo en Pages (D01) |
| D-7 | CI del índice | El bot necesita instalar `afm` con sus dependencias (scipy, manifold3d…) | `pip install` desde el repo; probar en D12 |
| D-8 | Mantenimiento | Un solo mantenedor | Todo automatizado; alta por PR con checklist |
| D-9 | Huecos de versión y archivo extra en el catálogo | **[P]** si `Catalog.versions` y `verify` los toleran | D05 lo comprueba y, si no, ajusta |
| D-10 | Caballos, monturas y accesorios horneados | Formatos y dependencias propios | Fuera de v1; accesorios sí entran como piezas |
| D-11 | Web con catálogo | Plan aparte | Fuera de v1 |
| D-12 | Instalar directo desde un repo sin índice | Sin índice no hay `sha256` independiente del servidor del autor | Fuera de v1: el camino es un índice propio (D33) o el `.zip` local |
| D-13 | Strikes de salud | ¿Cuántos controles fallidos antes de `inaccesible` y cada cuánto se controla? | Cada 6 h (como gen1recomp [G]); N = 4 (≈ un día) **[P]** |
| D-14 | Lista cerrada de `categorias` | Hace falta para filtrar en «Explorar»; gen1recomp usa una lista de 12 y pide de 1 a 4 [G] | `heroes`, `cuerpos`, `cabezas`, `manos`, `armas-accesorios`, `ropa`, `paletas`, `lineas`, `colecciones`, `otros` **[P]** |
| D-15 | Fuente `url` además de GitHub | Hosts arbitrarios y listados sin seguimiento ni contador | Lista de hosts aprobada en el índice oficial; si el cliente no la quiere, solo GitHub Releases |
| D-16 | Ayudante de alta sin OAuth | La URL de «archivo nuevo» con contenido precargado puede tener un tope de largo **[I]** | D32 lo prueba; plan B: formulario de *issue* + workflow que abre el PR |

**Decisiones que necesito del cliente:** (1) D-1; (2) licencia por defecto y si entra `NC` (D-2); (3) si el índice vive en un repo propio de la cuenta (`afm-catalogo-indice`) o dentro de este; (4) topes iniciales de tamaño (se proponen con la medición de D01); (5) si «Explorar» va como ventana propia (propuesto) o como pestaña dentro del panel de Catálogo; (6) la lista de categorías (D-14) y el umbral de strikes (D-13); (7) si se admite `fuente` por URL además de GitHub (D-15).

---

## 9. Tareas (D00–D34)

### Cómo leer las fichas
**Depende de** = tiene que estar hecha. **Paralelo con** = se puede hacer en otro turno sin esperarla. Cada tarea cierra con un patch `git am` y sus pruebas; las nuevas van en `tests/test_paquete*.py`, `test_instalar.py`, `test_remoto.py`, `test_catalog_online.py` salvo que se indique otra cosa.

**Estado (2026-11-26):** hechas además **D16** (`dependencias.py`), **D17** (`gui/catalog_online.py`: la lógica de «Explorar» sin GTK) y **D09** (`autor.py`: `paquete init | github | empaquetar` y el workflow de Release); **Estado (2026-11-25):** hechas además **D11** (`indice.py` y esquemas), **D13**, **D14** y **D15** (`compat_remota.py`); antes, **D02** (`paquete.py`), **D03** (`exportar`), **D04** y **D08** (`validar.py`, `ziplector.py`), **D05** (`instalar.py`) y **D06** (`afm catalogo instalar`) y **D07** (zips heredados; falta probarlo con el `proceres-127.zip` real). Siguen CK305 y los topes definitivos de `ziplector`; D00 y D01 esperan al cliente.

### Orden y paralelismo

```text
D00 ─► G0 ──────────────────────────────────────────────────────► D29
D01 ─► (topes, espejo)
D02 ─┬─► D03 ─► D04 ─► D05 ─► D06 ─► D07 ─► D08
     └──────────────► D09 ─► D10                       (autor)
D04+D08 ─► D11 ─► D12 ─► D13 ─► D14 ─► D15 ─► D16     (índice y cliente)
D05+D13 ─► D17 ─► D18 ─► D19 ─► D20 ─► D21 ─► D22 ─► D23 (GUI)
D16 ─► D24 (─► D25)     D12 ─► D27     D04+D05 ─► D26 (recomendada)
D12 ─► D31 (salud, descargas)   D11+D12 ─► D32 (alta web)   D13 ─► D33 (índices propios)   D14+D16 ─► D34 (colecciones)
todo ─► D28 ─► D29 ─► D30
```

**Hito mínimo útil (MVP):** D02–D08 (exportar e instalar `.zip` por CLI, con confirmación y reversión) + D17–D19 (menú «Importar paquete .zip…» con diálogo). Con eso ya se comparte un catálogo por pendrive o chat. El índice y la exploración vienen después.

**Hito 2 (índice vivo):** D09–D15 + D31: un autor publica con un tag, el índice lo toma solo, lo valida y la app lo ve con su compatibilidad. Después D32–D34 y la GUI en línea.

### Fase 0 — Decisiones y mediciones

**D00 · Política de contenido y decisiones del cliente (G0).** *Depende de:* nada. *Bloqueo:* decisión humana.
- Hacer: resolver §5.8 (`examples/` vs `THIRD_PARTY.md`), licencia por defecto, `NC`, dónde vive el índice y la lista del §8. Dejarlo escrito en `docs/POLITICA_catalogos_online.md` (una página).
- Listo cuando: las 7 decisiones del §8 están tomadas; sin G0 no se abre el índice (D12) ni se publica la semilla (D29); el resto puede avanzar.

**D01 · Medición de tamaños y de hosting.** *Paralelo con:* D00, D02.
- Leer: `proceres-127.zip` del cliente (o un catálogo real), `docs/WEB_mantenimiento.md`, `pages.yml`.
- Hacer: `tools/medir_catalogo_red.py`: peso y triángulos por pieza, por tipo y por línea (con y sin `.color.npz`/`.fbx`); prueba de descarga con `urllib` de un asset de Release y de un archivo de Pages (redirecciones, hosts reales); prueba en un navegador (CORS) de Pages, `raw.githubusercontent.com` y Releases.
- Listo cuando: hay una tabla con los pesos reales, los topes propuestos (por archivo, total, por pieza) y la decisión sobre el campo `espejo` (§5.10).

### Fase 1 — Paquete (motor puro, sin red)

**D02 · `paquete.py`: manifiesto `afm-paquete-1`.** *Paralelo con:* D01.
- Leer: `parts.PieceMeta` (estilo de serialización), `catalog.py` (`LINE_FORMAT`, `REF_RE`), `palette.py`.
- Hacer: dataclass con `to_dict/from_dict`, validación de campos, SPDX, semver, rangos `requiere`/`motor`; `categorias` (lista cerrada, CK106), `origen` con `referencia`, `tipo: coleccion` con `contenido.pines`; lectura tolerante de claves desconocidas; JSON Schema en `src/afm/schemas/`.
- Pruebas: ida y vuelta, campos faltantes, `formato` futuro, semver inválido.
- Listo cuando: `Paquete.from_dict(p.to_dict()) == p` y el esquema valida los ejemplos.

**D03 · `afm catalogo exportar`.** *Depende de:* D02.
- Leer: `catalog.py` (`Catalog.open`, `meta`, `versions`), `catalog_ops.py`, `parts.read_piece`.
- Hacer: armar el zip desde el árbol del catálogo con las piezas/recetas/paleta elegidas (más las dependencias internas: una receta arrastra sus piezas; un brazo con mano, su mano si es pieza aparte); excluir derivados y `.fbx`; sacar el bloque `color` de la ficha cuando el FBX no va (CK307); `sha256` por archivo; salida determinista (orden y fechas fijas).
- Pruebas: con `catalogo/ejemplo`; mismo catálogo ⇒ mismo zip; el zip pasa `Catalog.verify` al descomprimirlo.
- Listo cuando: el zip exportado se instala en un catálogo vacío (D06) con `verificar` limpio.

**D04 · `afm catalogo paquete validar` y lint CK.** *Depende de:* D02. *Paralelo con:* D03.
- Hacer: los códigos del §6, con archivo y causa; `--estricto` convierte avisos en errores; reutiliza `Catalog.verify` sobre una copia temporal.
- Pruebas: un fixture por código.
- Listo cuando: cada hallazgo nombra archivo y qué corregir.

**D05 · `instalar.py`: plan y aplicación atómica.** *Depende de:* D02, D04.
- Leer: `catalog.add_piece/write_piece`, `delete_pieces` y la papelera, `verify`, `colorroles` (roles y paleta), `palette.py`.
- Hacer: `plan_install` (reglas del §5.3, sin escribir) y `apply` (staging dentro del catálogo → verificación → movimiento → `paquetes.json`); fusión de paleta; conservar el número de versión con `write_piece`; **comprobar** que `versions`/`verify` toleran huecos de versión y un archivo extra en la raíz **[P]**.
- Pruebas: colisión con distinto hash; mismo hash (omite); paleta faltante; altura distinta; excepción en cada paso.
- Listo cuando: ante cualquier fallo el árbol del catálogo es idéntico al de antes (comparación de hashes).

**D06 · `afm catalogo instalar` (CLI).** *Depende de:* D05.
- Hacer: `--en RUTA`, `--como-linea`, `--simular` (imprime el plan), confirmación en consola, rutas explícitas por línea (D-4).
- Listo cuando: instala el zip de D03 y `afm catalogo listar` muestra las piezas.

**D07 · Importar `.zip` heredados (sin `paquete.json`).** *Depende de:* D05.
- Hacer: detectar `linea.json` en la raíz o a un nivel (los `catalogo.zip` que ya circulan), generar un `paquete.json` mínimo en memoria y pedir licencia y origen antes de instalar.
- Pruebas: zip de `catalogo/ejemplo` sin manifiesto, con y sin carpeta contenedora.
- Listo cuando: el `proceres-127.zip` real del cliente se instala sin perder nada.

**D08 · Endurecimiento del zip.** *Depende de:* D04. *Paralelo con:* D05–D07.
- Hacer: validaciones de §5.7 en un único lector de zips usado por D04, D05 y el cliente; topes de D01.
- Pruebas: zip-slip, bomba, enlaces, cifrado, anidado, duplicados por mayúsculas, extensión no permitida.
- Listo cuando: ninguno de los casos llega a escribir fuera del staging.

### Fase 2 — Herramientas de autor

**D09 · `afm catalogo paquete init`.** *Depende de:* D02.
- Hacer: carpeta con `paquete.json`, `README.md`, `LICENSE` (con selector SPDX), `CREDITOS.md` y `.github/workflows/publicar-paquete.yml` (valida en estricto, arma el zip y publica un Release `<id>-<version>.zip` en cada push a `main`; versión del manifiesto o `[release X.Y.Z]` en el commit); el comando `afm catalogo paquete github OWNER/REPO`, que fija `github` y agrega el workflow (equivale a `set-github` + `add-release-workflow` de modkit [C]); el asset se llama siempre `<id>-<version>.zip`, que es el nombre que el índice busca primero [G].
- Pruebas: la carpeta generada, completada, pasa `validar`.
- Listo cuando: en un repo de prueba un push publica el zip y el índice (D12) puede leerlo.

**D10 · `afm catalogo paquete enviar`.** *Depende de:* D09, D12.
- Hacer: genera `paquetes/<Autor>@<id>/` con `meta.json` (copiado de `paquete.json`), `descripcion.md` (del README) y `portada` (de `previews/portada.png`, tope de 2 MB) y, si `gh` está instalado, abre el Pull Request; si no, deja la carpeta lista para copiar o para el ayudante web (D32).
- Listo cuando: un alta de prueba entra al índice tras la corrida del bot.

### Fase 3 — Índice y cliente remoto

**D11 · Esquema del índice y de las fichas.** *Depende de:* D02, D04. *Paralelo con:* D09, D10.
- Hacer: JSON Schema de `meta.json` (con `fuente`, `seguimiento` y `release_fijo`), de `index.json` (con `schema_version`, `latest`, `update_check`, `descargas`) y de las fichas por paquete (`data/fichas/<Autor>@<id>-<ver>.json`), fijando la regla aditiva del §5.1 punto 8; `afm catalogo paquete fichas ZIP -o FICHAS.json`, que junta las `.pieza.json` de un paquete sin mallas (lo usa el bot del índice y el cliente en D15).
- Pruebas: el índice y las fichas generadas desde `catalogo/ejemplo` validan contra el esquema.
- Listo cuando: dado un zip se obtiene su entrada de índice y su archivo de fichas sin leer ningún STL.

**D12 · Repo del índice.** *Depende de:* D11, D08; **G0 (D00)**.
- Hacer: repo con la estructura del §5.5 (`esquemas/`, `ejemplos/`, `scripts/` en Python que reutilizan `afm`: `validar_alta`, `comprobar_enlaces`, `construir_indice [--releases]`, `puerta_releases`, `comprobar_bloqueados`); `validate-pr.yml` (valida el alta con los códigos `IX`); `build-index.yml` (en cada push y cada noche: último Release → puerta → `sha256`/`tamano` → portada reencodada → `index.json` y `data/fichas/`; despliega a Pages solo si la corrida entera terminó bien); `bloqueados.json`; `README` y `CONTRIBUTING` con el carril A; el primer PR de alta queda enlazado como ejemplo para copiar su forma [G].
- Listo cuando: el alta de un paquete de prueba aparece en `index.json` tras correr el bot y uno inválido no entra.

**D13 · `remoto.py`: cliente.** *Depende de:* D12. *Paralelo con:* D14.
- Hacer: `urllib`, caché en `~/.cache/afm/` con ETag, lista de hosts permitidos y redirecciones controladas, timeouts y reintentos, `sha256` obligatorio, índices múltiples (`~/.config/afm/remoto.json`), modo sin red con el índice guardado; lectura tolerante (solo `schema_version` y las entradas); `latest.zip.url` entra por el **mismo camino** que un `.zip` local (`plan_install`, D05); si el `sha256` no coincide: «el paquete cambió desde que se indexó».
- Pruebas: servidor local (304, hash incorrecto, redirección no permitida, timeout, sin red, claves nuevas en el índice, `schema_version` mayor).
- Listo cuando: baja y verifica un paquete del servidor local sin escribir nada fuera de la caché.

**D14 · `afm catalogo remoto …`.** *Depende de:* D13, D06.
- Hacer: `buscar`, `ver`, `instalar`, `actualizar`, `versiones`.
- Listo cuando: ciclo completo contra el servidor local de pruebas.

**D15 · Compatibilidad antes de descargar.** *Depende de:* D11, D13.
- Leer: `ports.port_compat`, `guide.py`, `afm catalogo cobertura`.
- Hacer: con las **fichas** del paquete calcular, contra el catálogo local, cuántas piezas encajan (nivel `ok`/`aviso`/`no` y motivos) sin bajar los STL.
- Listo cuando: el resultado coincide con el de `cobertura` tras instalar.

**D16 · Dependencias, actualización, retiradas y bloqueados.** *Depende de:* D14.
- Hacer: resolución semver simple (una versión por `id`, sin ciclos), `conflicts`, actualización aditiva, aviso de versiones retiradas y de paquetes bloqueados.
- Pruebas: ciclo, conflicto, falta de dependencia, instalar `1.0.0` y luego `1.1.0` con una receta ya armada.
- Listo cuando: ningún caso deja el catálogo a medias.

**D31 · Salud del índice y descargas.** *Depende de:* D12. *Paralelo con:* D13–D16.
- Leer: README de `gen1recomp-mod-index` (`health`, `build-index --releases`, `gate-releases`, `.health/`) [G].
- Hacer: `scripts/salud.py` (cada 6 h: repo, Release y zip responden; strikes en `.salud/estado.json`; tras N seguidos ⇒ `update_check: "inaccesible"`, oculto en «Explorar» y **sin borrar** la carpeta; se recupera solo); `puerta_releases.py` (valida lo que el bot adopta sin PR y anota en `.salud/` la última versión que pasó); recuento de `descargas` desde la misma respuesta de Releases que ya lee el bot, con historial por tag y máximo visto (`null` si no hay contador).
- Pruebas: servidor falso con el repo caído 1 vez, N veces y recuperado; contador que baja por un asset re-subido y tags que salen de la página ⇒ el total no baja; fuente fija ⇒ `descargas: null`.
- Listo cuando: un repo de prueba caído pasa a `inaccesible` tras N controles y vuelve solo; ningún total retrocede; el índice nunca borra una carpeta por su cuenta.

**D32 · Ayudante de alta en Pages.** *Depende de:* D11, D12. *Paralelo con:* D13.
- Hacer: página estática en `sitio/` que arma `meta.json` y `descripcion.md` según el esquema, valida en el navegador y abre GitHub con el archivo nuevo precargado para que el PR se arme ahí; sin OAuth ni servidor en v1 (gen1recomp usa un formulario que abre el PR y un worker opcional para «Sign in with GitHub» [G]). Si la URL de «archivo nuevo» con contenido precargado no alcanza **[I]**, plan B: formulario de *issue* + workflow que abre el PR (D-16).
- Pruebas: el JSON generado valida contra el esquema de D11; campos faltantes.
- Listo cuando: una persona sin experiencia con git da de alta un paquete de prueba desde el navegador y el bot lo lista.

**D33 · Índices propios.** *Depende de:* D13. *Paralelo con:* D14–D16.
- Hacer: `afm catalogo remoto indice agregar|quitar|listar` (acepta `owner/repo`, que se resuelve a la URL de Pages del repo **[I]**, o una URL completa); advertencia de confianza al agregar; límite de hosts por índice (§5.7); marcar el repo del índice como **plantilla** de GitHub con `ejemplos/`; botón «Agregar índice…» en «Explorar» (con D18).
- Pruebas: dos servidores locales (oficial y propio); un índice que apunta a un host no permitido; quitar un índice no borra lo ya instalado ni su registro en `paquetes.json`.
- Listo cuando: un índice de prueba se agrega por `owner/repo` y por URL, se explora, se instala de él y se quita sin dejar rastros salvo lo instalado.

**D34 · Colecciones.** *Depende de:* D14, D16.
- Leer: §5.12, `instalar.py` (D05), `catalog_project.py`.
- Hacer: `tipo: coleccion` con `contenido.pines` (D02) y lint CK402/CK403; `afm catalogo paquete init --perfil coleccion`; `plan_install` agregado para varios pines (una confirmación, todo o nada); resolución de pines contra el índice con comprobación del `sha256`; la misma comprobación en el CI del índice.
- Pruebas: pin retirado, bloqueado o con `sha256` distinto; fallo a mitad ⇒ catálogo intacto.
- Listo cuando: instalar la colección de `proceres-127` en un catálogo vacío deja lo mismo que instalar a mano cada pin.

### Fase 4 — GUI

**D17 · Lógica de la GUI sin GTK.** *Depende de:* D05, D13.
- Leer: `gui/catalog_import.py` (`FORMS`, `parse`, `run`), `gui/controller.py`, `gui/session.py`, `progress.py`, `gui/settings.py`.
- Hacer: `gui/catalog_online.py` con estado (índice, filtros, selección, plan de instalación) y ejecución en hilo con progreso; claves nuevas en `es/en/pt.json`.
- Pruebas: con `FakeScene`; `test_i18n.py` verde.
- Listo cuando: todo el flujo se puede conducir sin ventana.

**D18 · Ventana «Explorar catálogos en línea…».** *Depende de:* D17, D15.
- Hacer: lista con filtros (tipo, categoría, línea, licencia, solo compatibles) y orden (descargas, recientes), tarjeta de detalle con portada/miniaturas, compatibilidad con el catálogo propio, estados (incluido `inaccesible`), aviso «listado no verificado», «Agregar índice…» (D33), modo sin red; botón en la pestaña Catálogo.
- Listo cuando: se explora con el índice guardado sin conexión (probar en GTK real).

**D19 · Diálogo de confirmación e instalación.** *Depende de:* D17.
- Hacer: muestra autor, licencia, origen, tamaño, piezas/recetas que agrega, roles de paleta que se agregan, dependencias, línea destino (o «crear línea nueva»), conflictos y avisos; barra de progreso; resultado con `verificar` y `cobertura`.
- Listo cuando: sin confirmar no se escribe nada; un fallo deja el catálogo intacto.

**D20 · «Paquetes instalados».** *Depende de:* D19, D16.
- Hacer: lista con versión y origen, Actualizar, Versiones, Quitar (con las dependencias y `usos`), errores atribuidos; al abrir una receta o proyecto con piezas faltantes, ofrecer buscarlas por `id@n`.
- Listo cuando: quitar un paquete no deja huérfanos ni recetas rotas sin aviso.

**D21 · «Publicar…».** *Depende de:* D03, D04, D09.
- Hacer: asistente (piezas/receta → nombre, descripción, licencia con explicación en español, origen, créditos → validar con resultados legibles → exportar zip y/o preparar la carpeta del repo y/o abrir el alta en el índice con D10/D32).
- Listo cuando: alguien publica un paquete de prueba sin usar la terminal.

**D22 · Importar y arrastrar y soltar.** *Depende de:* D19.
- Hacer: «Importar paquete .zip…» en el menú **Compartir** de «Administrar catálogo» y arrastrar y soltar sobre la ventana principal, con la misma confirmación; soporte de `.zip` heredados (D07).
- Listo cuando: ambos caminos llegan al mismo diálogo.

**D23 · Licencia efectiva y créditos.** *Depende de:* D05. *Paralelo con:* D18–D22.
- Leer: `recipe.py`, `export` y la lista de piezas (`<figura>_lista_de_piezas.{json,md}`).
- Hacer: calcular la licencia más restrictiva del armado, mostrarla en «Armar desde catálogo…» y escribir `CREDITOS.txt` con autor, licencia y enlace de cada pieza.
- Pruebas: matriz de combinaciones de licencias; el archivo aparece junto al STL.
- Listo cuando: una figura con una pieza `NC` lo dice antes de exportar.

### Fase 5 — Seguridad y moderación

**D24 · Bloqueo y retiro.** *Depende de:* D16.
- Hacer: `bloqueados.json` con `id` / autor / `sha256`, `motivo` y `fecha`; `comprobar_bloqueados` en el CI del índice (rechaza altas y retira lo ya listado); en la app, comparación local de los `sha256` de las piezas instaladas con la lista y aviso; documento de reclamos (`docs/RECLAMOS.md`).
- Listo cuando: un paquete bloqueado desaparece de «Explorar» y avisa si ya estaba instalado.

**D25 · (Opcional) Firma de `index.json`.** *Depende de:* D24.
- Hacer: firma con una clave del mantenedor y verificación en el cliente.
- Listo cuando: un índice con firma inválida se rechaza.

**D26 · Piezas por referencia (recomendada).** *Depende de:* D04, D05. *Paralelo con:* D24. Es el camino para todo lo que no se puede redistribuir (§5.8); conviene hacerla antes de abrir el índice a terceros.
- Hacer: §5.11: `referencias_requeridas`, CK304, control «Importar archivo requerido», carpeta de estado local, pieza «incompleta» hasta tener el archivo.
- Listo cuando: un paquete sin el archivo no se arma y con el correcto sí.

### Fase 6 — Web

**D27 · Galería estática en Pages.** *Depende de:* D12.
- Leer: `pages.yml`, `scripts/build_web.sh`.
- Hacer: página generada desde `index.json` (búsqueda, filtros, detalle, descarga, «Abrir en la app»); orden por descargas; espejo de los zips si D01 lo pide.
- Listo cuando: se navega el índice desde el sitio sin instalar nada.

### Fase 7 — Documentación y lanzamiento

**D28 · Documentación.** *Depende de:* D21.
- Hacer: `docs/guia_publicar_paquetes.md` (primer paquete local → instalación en limpio → checklist → regla de contenido → versionado → pruebas de fallo → recuperación, al estilo de la guía de gen1recomp), `docs/guia_instalar_paquetes.md`, `docs/guia_indice_propio.md` (copiar el repo plantilla, dar de alta paquetes, publicar en Pages, agregarlo desde la app), secciones en `README.md` y `docs/tutorial_gui.md`, y una entrada en `PROGRESS.md`.
- Listo cuando: una persona ajena publica un paquete siguiendo solo la guía.

**D29 · Paquetes semilla.** *Depende de:* D00 (G0), D12, D21.
- Hacer: publicar la línea `proceres-127` (línea base, piezas y héroes propios) como primeros paquetes del índice, solo con lo que D00 permita, y una colección que los fija si D34 ya está; el PR de alta de la semilla queda como ejemplo en el README del índice [G].
- Listo cuando: el índice tiene al menos tres paquetes reales y la app los instala de punta a punta.

**D30 · Prueba con una persona que no diseña.** *Depende de:* D29.
- Hacer: que alguien sin experiencia instale un héroe y arme una figura con «Armar desde catálogo…», sin ayuda, y registrar dónde se traba.
- Listo cuando: lo logra sin pasar por la terminal; lo que falle entra a `PENDIENTES.md`.

---

## 10. Referencias

- gen1recomp — [Guide Publishing](https://github.com/bryanthaboi/gen1recomp/wiki/Guide-Publishing): checklist, empaquetado con `modkit`, Releases de GitHub, regla de contenido, `required_imports`, versionado, pruebas de fallo. Relacionadas: Modkit, Reference Manifest, Concepts Compatibility.
- gen1recomp — [Mod index](https://github.com/bryanthaboi/gen1recomp-mod-index) [G]: carpeta `<Author>@<id>` con `meta.json`, `description.md` y `thumbnail`; feed `data/index.json`; `build-index --releases`, `gate-releases`, `health --record --prune`, `blocklist.json`; recuento de descargas; *carts*; ayudante de alta; regla de claves aditivas. Leído el 2026-11-23.
- [my-mod-index](https://github.com/ShaneMcGovernIE/my-mod-index) [G]: índice personal basado en el oficial; se agrega en el launcher pegando `owner/repo` o la URL de Pages.
- Repo: `docs/PLAN_piezas_intercambiables_catalogo.md`, `docs/PLAN_paleta_color_linea.md`, `docs/PLAN_port_pyodide.md`, `docs/WEB_mantenimiento.md`, `docs/PENDIENTES.md`, `THIRD_PARTY.md`; código: `src/afm/catalog.py`, `parts.py`, `colorroles.py`, `catalog_project.py`, `catalog_cli.py`, `gui/catalog_import.py`, `web/profiles.js`, `.github/workflows/pages.yml`.
