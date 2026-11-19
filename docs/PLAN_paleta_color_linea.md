# Plan: paleta de colores por línea (roles de color) para piezas de catálogo con FBX

**Versión:** 0.1 (borrador) — 2026-11-19
**Repo:** `fschpp/action_figure_maker_stl`, sobre `main` @ `b33e425` (ubicar en `docs/PLAN_paleta_color_linea.md`)
**Continúa:** `docs/PLAN_fbx_textura_color.md` (T01–T30) y `docs/PLAN_color_catalogo_cabeza.md` (T31–T37). Las tareas de este plan son **T38–T51**.
**Reemplaza** la propuesta `PLAN_png_paletas_compartidas` (la de ChatGPT, T38–T63): no está en el repo; el §2 dice qué se tomó y qué no.
**Marcas:** **[C]** dicho por el cliente · **[V]** verificado leyendo el código de `main @ b33e425` · **[M]** medido en esta revisión sobre los FBX de `examples/` · **[I]** inferido, a validar · **[P]** pendiente de decidir.

---

## 1. Qué se quiere [C]

1. Que en el catálogo convivan **geometría y color**, usando la información UV de los FBX que ya existen.
2. Que las piezas de una línea **compartan colores** (mismo color de piel, de uniforme, de botas): se guardan como **roles** («piel», «uniforme», «botas»), no como RGB pegado en cada pieza.
3. Que cambiar un color desde la UI (p. ej. «uniforme» de blanco a azul) **recoloree todas las piezas que usan ese rol**, sin tocar la geometría ni los STL.

## 2. Qué se toma del plan de ChatGPT y qué cambia

| Idea del plan de ChatGPT | Decisión | Por qué |
|---|---|---|
| Identidad semántica estable del color (`swatch_id`) distinta del HEX | **Se toma** (el «rol») | Es lo que permite recolorear |
| El índice del PNG no es el id semántico: hay un mapeo explícito | **Se toma**, dentro del paquete de la pieza | — |
| Textura categórica: muestreo por vecino más cercano, nunca interpolado | **Se toma** | Mezclar índices inventa colores en los bordes |
| El usuario aporta un PNG indexado | **Cambia:** las etiquetas se **derivan del FBX** (T40) | El usuario ya tiene FBX; la textura es sombreada (§4), no un PNG de colores planos |
| Almacén global de paletas con versiones inmutables, `seguir_ultima`, SHA | **Cambia:** una `paleta.json` **por línea**, editable en el lugar | Es una herramienta de un solo usuario; las líneas ya son catálogos separados. La reproducibilidad la da el informe (§5.6). El intercambio entre líneas es importar/exportar JSON |
| Llevar la etiqueta categórica por `ColorSource`, `bake_piece`, `dilate` y el informe (7 tareas) | **Cambia:** el color se materializa con una tabla (LUT) al armar la fuente; el bake no se toca. Llevar etiquetas al bake queda como T45, opcional | Menos superficie de cambio en el código más delicado |
| Cuantización RGB → paleta como extra | **Es el paso central** (T40) | Es como se obtienen los roles |
| Paleta por pieza (override) | **Cambia** a paleta por **figura** (T44) | Es lo que se necesita al armar |
| Administrador de paletas, selector por catálogo, panel de color | **Se toma** (T46–T48) | — |
| CLI `afm paleta …` | **Se toma**, como `afm catalogo paleta` (T39) | — |

**Lo que el plan de ChatGPT no tenía:** *anclas* por rol (para proponer roles al importar la figura siguiente de la línea) y el hallazgo del §5.7 (el color de una pieza recortada de una figura no se alinea como el de una cabeza completa).

## 3. Estado verificado en `main @ b33e425` [V]

- **T31–T36 están hechas** (commits `606ad9d`, `16c7e78`, `462353b`, `ffeb0ba`, `42eb10d`) y **T37 parcial** (`b33e425`, `e4a4786`). `PROGRESS.md` está desactualizado: su última entrada es T33; T34–T37 no tienen bitácora. El estado real sale de `git log` y de `tests/test_color_catalog_head.py`.
- `colorpiece.py`: `align_fbx_to_piece`, `piece_color_source(tm, piece_stl, to_host, similarity=…)`, `head_color_provider`, `catalog_color_sources(placed, catalog, scaled)`. Cada pieza con color aporta un **proveedor** `callable(res)` en `Colorizer.sources[clave]` (la clave es el nombre sin `_izq/_der`); la fuente propia se usa antes que la de la figura (`Colorizer.source_for`).
- `PieceMeta.color` (opcional): `{fuente, sha256, similitud (4×4 FBX→STL), texturas, estado, alineacion}`. `Catalog.add_piece(mesh, meta, new_version, color_fbx=)` copia el **FBX entero** junto al STL como `<id>@<v>.fbx` (26–36 MB cada uno en los ejemplos). Una versión nueva **no hereda** el color. `Catalog.color_file(ref)` valida el hash; `Catalog.verify` revisa que el FBX exista y coincida.
- **No hay entrada de CLI ni de GUI para catalogar con FBX**: solo la API de Python (`add_piece(color_fbx=)`). Tampoco hay paleta, roles ni pestaña de color en la ventana de Catálogo.
- `TexturedMesh.sample` es **bilineal** y `image()` convierte todo a RGB (`convert("RGB")`). `ColorSource` precalcula `sample_rgb` una vez y libera la imagen. `bake_piece`/`dilate` copian el color del vecino más cercano (no promedian).
- Las piezas extraídas de una figura (`extract.py`) salen en **el marco de la figura de origen** (mm reales, Z arriba, +Y al frente, simetría en x = 0). Los STL de las piezas del catálogo se arman con `PlacedPart.T`.
- `gui/catalog_manager.py` ya tiene `PreviewRenderer.render_textured(parts, textures_on)` y `render.TexturedPart`: sirven de vista previa de roles.
- Dependencias del repo: `numpy, scipy, scikit-image, pillow, trimesh`; **`scikit-learn` no está** (usar `scipy.cluster.vq` para el k-means).

## 4. Medidas [M]

Prueba de concepto sobre `guemes_segmentado_lq_texturas.fbx`, `guemes_cabeza_textura.fbx` y `egon_lq_textura.fbx` (muestras de superficie ponderadas por área, k-means en Lab; los números salen de `sklearn.KMeans`, el código final usará `scipy` y pueden moverse un poco). Los scripts quedaron fuera del repo.

| Qué se midió | Resultado | Consecuencia |
|---|---|---|
| Aspecto de la textura | Es **sombreada y con la luz horneada** (estilo texturas generadas): los claros y las sombras de la misma tela son colores distintos. Entre islas hay relleno borroso | Hay que muestrear sobre la superficie 3D (ya lo hace `ColorSource`), no sobre el atlas |
| k-means K = 8 sobre Güemes | Los 3 clusters mayores son **tres grises del mismo uniforme** (`#BFBAAD` 38 %, `#9A9587` 10 %, `#6E6A5D` 8 %); negro 25 %; verde 6 %; dorado 5 %; dos tonos de piel/marrón 8 % | **Los clusters no son roles**: son tonos. Hay que agruparlos a mano (T40/T47) |
| Costo de agrupar en roles (Güemes, 5 roles: uniforme, negro, verde, dorado, piel) | ΔE medio 8,0 / p95 22,0 contra 4,3 / 9,9 con los 8 tonos. Distancia mínima entre roles ΔE 15,3 | Se pierde el sombreado horneado: queda color plano por rol. Para imprimir en multicolor es lo que se busca; para la vista previa se ve «chato» |
| Asignar a la **cabeza** (`guemes_cabeza_textura.fbx`) los roles del cuerpo por «ancla más cercana» | 28 % de la superficie (el pelo marrón oscuro) cayó en el rol **verde** (el cuello de la casaca); solo 0,2 % queda a más de ΔE 18 de alguna ancla | **Un umbral de distancia no detecta el error.** La sugerencia automática es solo una sugerencia: el usuario confirma y puede crear roles nuevos (T40, T47) |
| Peso del paquete de roles de Güemes | Etiquetas 2048² indexadas: **43–54 KB** · geometría + UV comprimida: **147 KB** · el FBX: **26,6 MB** | El catálogo pasa de ≈ 26 MB por pieza a ≈ 0,2 MB (−99 %) |
| Recolorear el atlas de etiquetas con una LUT nueva | **45 ms** en 2048² | Vista previa de «cambio un HEX» en tiempo real, sin horneado |
| Filtro de mayoría 5×5 sobre las etiquetas | cambia 0,45 % de los píxeles (en la prueba se usó mediana con 3 roles); el atlas filtrado coincide 99,4 % con la clasificación directa de las muestras | Sirve para quitar el «sal y pimienta» (94 % de los vecinos con la misma etiqueta a 0,08 mm) |
| `align_fbx_to_piece` (T35) con piezas **recortadas** del STL de Güemes contra su FBX entero | Figura entera: media **0,016 mm** (ok). Torso: **0,58 mm**. Pierna: **1,42 mm**. Cabeza recortada por altura: **0,95 mm**. Umbral de aceptación: 0,1 mm | La alineación por momentos de T35 sirve para una pieza **completa** (la cabeza con su FBX), no para un trozo de una figura. Ver §5.7 |

## 5. Diseño

### 5.1 Roles y paleta de línea
- Un **rol** es un identificador estable (`piel`, `uniforme`, `botas`) con un color actual (`hex`, editable) y una lista de **anclas** (colores de referencia en que se vio ese rol; sirven para sugerir el rol en la próxima figura y **no cambian** cuando se recolorea).
- La **paleta de línea** vive en el catálogo: `paleta.json` junto a `linea.json`. Se edita en el lugar (escritura atómica) y **no** versiona las piezas: las piezas guardan ids de rol, nunca RGB.
- Máximo 64 roles (una etiqueta cabe en 8 bits y queda libre el 255 para «sin rol»).

### 5.2 Paquete de roles de una pieza
Lo que hoy es un FBX de 26 MB se reemplaza por un **paquete** `<id>@<v>.color.npz` (≈ 0,2 MB): la geometría y los UV por esquina del FBX, más un **atlas de etiquetas** (`uint8`, ≤ 2048²) en el que cada píxel dice a qué rol pertenece. Es la «textura categórica» del plan de ChatGPT, pero derivada del FBX. El FBX original no se guarda en el catálogo: la ficha anota su nombre y su hash (`origen`).
Cada pieza lleva **su propia copia** del paquete de la figura de la que sale (≈ 0,2 MB × 8 piezas): así `write_piece`, la papelera y `verify` siguen igual que con el FBX de T35. [I]

### 5.3 De dónde sale el color de cada pieza al armar (prioridad)
1. La pieza trae paquete de roles → etiquetas + **paleta de la línea** (+ cambios de la figura, T44).
2. La pieza trae su FBX en RGB (T35, formato viejo) → se usa como hoy.
3. La figura base tiene color (`project.color`, RGB o roles) → se usa como hoy / con la paleta.
4. Nada de lo anterior → `sin_origen`, color plano `#C8C8C8` (como hoy).

Un rol que la paleta no tiene **frena el armado** con un mensaje que lista los roles que faltan (no se inventa color). Los píxeles sin rol (255) salen del color plano. Un rol en uso no se puede borrar de la paleta (T39/T46).

### 5.4 Cómo entra al motor sin tocar el bake
`TexturedMesh` gana `labels`/`role_ids` opcionales. `sample()` con etiquetas devuelve `LUT[etiqueta]` por **vecino más cercano** (misma convención de píxel del plan base), así que `ColorSource`, `register_piece`, `unwrap` y `bake_piece` reciben exactamente lo que reciben hoy: una fuente RGB cuyos únicos colores son los de la paleta. `ColorSource` además guarda `sample_label` para recolorear sin reconstruir el `cKDTree`. Sin etiquetas, todo el camino viejo queda idéntico (R3).

### 5.5 Recolorear
- **Vista previa en la ventana de Catálogo:** LUT sobre el atlas de la pieza (45 ms) → `render_textured`. Sin horneado.
- **Figura armada:** rehacer el color de todas las piezas (registro + bake: decenas de segundos, no se reconstruye la geometría). Con T45 (opcional) se recolorea lo ya horneado en < 1 s.

### 5.6 Reproducibilidad
La paleta es editable, así que cada exportación con color escribe en `<fig>_color_informe.json` la **paleta usada** (cada rol con su `hex` y el hash SHA-256 de la paleta) y, por pieza, la fuente (`roles` / `rgb` / `figura` / `plano`) y los roles que usó. Con eso se puede repetir una exportación aunque la paleta haya cambiado después.

### 5.7 Piezas recortadas de una figura (hallazgo [M])
`align_fbx_to_piece` alinea por momentos de la superficie de ambas mallas: sirve cuando el STL de la pieza es **todo** el FBX (la cabeza con su FBX). Un torso, brazo o pierna extraído de una figura no tiene un FBX propio: su color sale del **FBX de la figura entera**, y esa alineación sí funciona (0,016 mm). Como `extract.py` deja las piezas en el marco de la figura, la similitud de cada pieza es **la misma matriz FBX → figura** (la de `FigureFrame` del modo A/B) y luego rige `S = T · A` de T36. Por eso T42 guarda esa matriz sin recalcular la alineación por momentos. [I] a confirmar con una figura real en T42/T50 (en particular la mano, que `extract.py` deja en un marco estándar propio).

## 6. Formatos

**`paleta.json`** (`afm-paleta-1`):
```json
{
  "formato": "afm-paleta-1",
  "roles": [
    {"id": "piel",     "nombre": "Piel",     "hex": "#D8A47F", "anclas": ["#A98265", "#89684C"]},
    {"id": "uniforme", "nombre": "Uniforme", "hex": "#F2F0E6", "anclas": ["#BFBAAD", "#9A9587", "#6E6A5D"]},
    {"id": "botas",    "nombre": "Botas",    "hex": "#201F1C", "anclas": ["#201F1C"]}
  ]
}
```
Reglas: `id` = `^[a-z][a-z0-9_]{0,31}$` y único; `hex` = `#RRGGBB` (se acepta minúscula, se guarda en mayúscula); ≤ 64 roles; `anclas` opcional, ≤ 8 por rol. Sin rutas absolutas. Una línea sin `paleta.json` abre igual que hoy.

**`<id>@<v>.color.npz`** (`afm-color-roles-1`), leído con `np.load(..., allow_pickle=False)`:
`meta` (bytes UTF-8 de un JSON: `formato`, `roles` = ids en el orden de las etiquetas, `atlas` [ancho, alto], `origen` {nombre, sha256}) · `vertices` float32 (V,3), en unidades del FBX tras `Lcl Scaling` · `faces` int32 (F,3) · `corner_uv` float32 (F,3,2) · `labels` uint8 (alto, ancho); el valor `i` es `roles[i]` y 255 es «sin rol».

**Bloque `color` de la ficha de la pieza** (aditivo; sin `modo` = `"rgb"`, el de T35):
```json
"color": {"modo": "roles", "fuente": "torso-guemes@1.color.npz", "sha256": "…",
          "similitud": [[…4×4 FBX→marco de la pieza…]], "roles": ["piel", "uniforme", "botas"],
          "estado": "alineada", "origen": {"nombre": "guemes.fbx", "sha256": "…"}}
```
`roles` en la ficha permite `verify`, el conteo de uso y el borrado seguro sin abrir el `.npz`.

**Proyecto** (T44, aditivo, solo si existe): `ColorSpec.roles` = ruta relativa del paquete de la figura base; `ColorSpec.paleta_override` = `{rol: "#RRGGBB"}`.

## 7. Contratos de interfaz (únicos)

```python
# palette.py (T38) — sin PIL ni GTK
class PaletteError(ValueError): ...
@dataclass
class Role: id: str; nombre: str; hex: str; anclas: list[str]
@dataclass
class Palette:
    roles: list[Role]
    def to_dict(self) -> dict;  @classmethod def from_dict(cls, d) -> "Palette"       # valida y normaliza
    def get(self, role_id) -> Role;  def hex_of(self, role_id) -> str
    def lut(self, role_ids: list[str], flat: str = "#C8C8C8") -> np.ndarray            # (256,3) uint8; 255 = flat
    def with_overrides(self, overrides: dict) -> "Palette"                              # copia con otros hex
    def digest(self) -> str                                                             # SHA-256 del contenido normalizado
    def nearest(self, rgb, max_delta_e: float | None = None) -> tuple[str, float] | None  # rol de la ancla más cercana (Lab)

# catalog.py (T39, T42)
Catalog.palette() -> Palette | None;  Catalog.set_palette(p: Palette) -> None          # escritura atómica
Catalog.palette_usage(role_id) -> list[str]                                              # refs de piezas cuyo color.roles lo usan
Catalog.add_piece(mesh, meta, new_version=False, color_fbx=None, color_roles=None)      # color_roles: RolesPackage

# colorroles.py (T40, T41)
@dataclass
class Proposal: centers_rgb: np.ndarray; share: np.ndarray; labels_atlas: np.ndarray; suggested: list  # clusters, % de área, mapa, rol sugerido o None
def propose(tm: TexturedMesh, palette: Palette | None, k: int = 10, seed: int = 0, n_samples: int = 300_000) -> Proposal
@dataclass
class RolesPackage:
    vertices; faces; corner_uv; labels: np.ndarray; roles: list[str]; origen: dict
    def save(self, path) -> str;  @classmethod def load(cls, path) -> "RolesPackage"
    def to_textured(self, palette: Palette, overrides: dict | None = None) -> TexturedMesh   # con labels + LUT
def build_package(tm: TexturedMesh, proposal: Proposal, assign: dict[int, str | None], *, filter_px: int = 5, max_atlas: int = 2048) -> RolesPackage
```
`TexturedMesh.labels` (H,W uint8 | None), `TexturedMesh.lut` ((256,3) uint8 | None); `sample(face, bary, "base_color")` usa el vecino más cercano cuando hay `labels`.

## 8. Reglas de ejecución (heredan las del plan base y las precisan)

- **R1 · Una tarea por turno**, con su ficha, sus pruebas nombradas y su bitácora. Si un **[V]** del §3 no se cumple en el repo, la tarea se detiene y lo informa (no improvisa).
- **R2 · Salidas existentes intactas.** Sin paleta ni paquete de roles el comportamiento y los bytes de salida no cambian; los STL no cambian **nunca** por el color. Ninguna prueba existente se modifica salvo que la ficha lo diga.
- **R3 · Importaciones perezosas** de `PIL`/`trimesh`/`skimage`; `palette.py` no importa ninguna.
- **R4 · i18n.** Todo texto visible nuevo va a `src/afm/locales/{es,en,pt}.json` en la misma tarea (las pruebas de `tests/test_i18n.py` lo exigen).
- **R5 · Informes serializables.** Todo campo nuevo de un informe es número, `str`, `bool`, lista o diccionario de eso; nunca un objeto ni un arreglo de NumPy.
- **R6 · No relajar umbrales.** Si un criterio no se cumple, se informa la medida real y se detiene.
- **R7 · Regresión** en cada tarea: `python -I -m pytest tests/test_texmesh.py tests/test_colorsrc.py tests/test_colorbake.py tests/test_colorexport.py tests/test_color_catalog_head.py tests/test_catalog.py tests/test_i18n.py -q` (los nombres exactos se buscan en el repo) más las pruebas de la ficha. Las lentas llevan `AFM_SLOW=1`.
- **R8 · Bitácora.** Al terminar, entrada **al principio** de `PROGRESS.md` con el formato del archivo (`## Current Focus — Paleta de línea: T## (fecha, sobre main <hash>)`, **Objetivo / Hecho / Medido / No verificado**, `### Todo List`, `### Latest Blockers/Discoveries`). La primera tarea también anota que faltan las entradas de T34–T37.
- **R9 · GUI.** No hay GTK en el entorno de pruebas: la lógica va en el controlador/sesión y se prueba sin GTK; lo que no se pueda abrir se marca **no verificado**.
- **R10 · Determinismo.** Semillas fijas (`np.random.default_rng(0)`).

## 9. Grafo de dependencias

```
T38 Paleta ─► T39 Paleta en el catálogo (+CLI)
T38 ─► T40 Roles desde un FBX ─► T41 Paquete + TexturedMesh con etiquetas
T39 + T41 ─► T42 Catalogar con roles ─► T43 Armado desde catálogo con paleta
T41 + T39 ─► T44 Figura base con roles + cambios por figura
T43 (o T44) ─► T45 Recolor rápido (opcional)
T39 ─► T46 Pestaña «Paleta»            T40 + T42 ─► T47 Asistente «Roles de color»
T43 + T44 + T46 ─► T48 Armar desde catálogo: paleta y recolor
T46–T48 ─► T49 i18n y compatibilidad ─► T50 Figuras reales y medidas ─► T51 Documentación
```
Arrancables hoy: **T38**. T40 y T39 pueden ir en paralelo después de T38.

---

# Fichas de tarea

## Fase A — Datos y motor (sin GUI)

### T38 · Modelo de paleta
- **Objetivo:** `Role`, `Palette` y su validación, puros (sin disco ni PIL).
- **Bloqueantes:** ninguno.
- **Leer:** §5.1, §6, §7; `src/afm/project.py` (estilo de `to_dict/from_dict`), `src/afm/colorbake.py` (`parse_fill`, `DEFAULT_FLAT`).
- **Crear:** `src/afm/palette.py`, `tests/test_palette.py`.
- **Hacer:** el contrato del §7. `from_dict` valida `formato`, `id`, `hex`, duplicados, ≤ 64 roles, ≤ 8 anclas; los errores son `PaletteError` con el motivo legible. `lut()` devuelve 256 filas: las de los roles pedidos en el orden dado, el resto y el 255 en `flat`. `nearest()` convierte a Lab con `skimage.color.rgb2lab` (importado dentro de la función) y devuelve `(id, ΔE)` del rol de la ancla más cercana; con `max_delta_e` devuelve `None` si ninguna llega. `digest()` es el SHA-256 del JSON normalizado (orden de roles y hex en mayúscula).
- **Pruebas:** `test_roundtrip_dict` · `test_hex_normalized_to_upper` · `test_rejects_bad_hex_duplicate_id_bad_id_too_many_roles` · `test_lut_shape_order_and_flat_255` · `test_with_overrides_leaves_original_untouched` · `test_digest_changes_with_hex_but_not_with_dict_order` · `test_nearest_picks_closest_anchor_and_respects_limit` · `test_module_imports_without_pillow_or_skimage` (comprueba `sys.modules` tras importar).
- **Aceptación:** pasan; el módulo no importa nada pesado al cargarse.
- **No hacer:** disco, catálogo, GUI.

### T39 · Paleta en el catálogo y CLI
- **Objetivo:** que una línea tenga su `paleta.json` y se pueda ver y editar desde la terminal.
- **Bloqueantes:** T38.
- **Leer:** `src/afm/catalog.py` (`Catalog.open/create`, `_write_line`, `verify`, `usages`), `src/afm/catalog_cli.py`, `tests/test_catalog.py` (fixtures).
- **Tocar:** `catalog.py`, `catalog_cli.py`, `locales/*`; **crear** `tests/test_catalog_palette.py`.
- **Hacer:** `Catalog.palette()` (None si no existe `paleta.json`), `set_palette(p)` (archivo temporal + `os.replace`; no toca `linea.json` ni las piezas), `palette_usage(role_id)` (recorre las fichas: `color.roles`), `delete_role` que se niega si hay uso (error con la lista de piezas). `verify()` suma: `paleta.json` ilegible = problema; una pieza con `color.roles` que la paleta no tiene = problema. CLI: `afm catalogo paleta ver|agregar|color|renombrar|quitar|importar|exportar RUTA …` (`color RUTA ROL "#RRGGBB"`; `importar/exportar` leen y escriben un JSON `afm-paleta-1`). Un catálogo sin `paleta.json` sigue abriendo y verificando como hoy.
- **Pruebas:** `test_catalog_without_palette_opens_and_verifies_as_before` · `test_set_palette_is_atomic_and_leaves_pieces_untouched` (hash de las fichas y STL antes/después) · `test_delete_role_in_use_is_refused_with_piece_list` · `test_verify_flags_piece_role_missing_from_palette` · `test_cli_palette_roundtrip_export_import` · `test_cli_color_changes_only_that_role`.
- **Aceptación:** pasan; el catálogo `catalogo/ejemplo` verifica igual que antes.
- **No hacer:** paquetes de roles (T41/T42), GUI.

### T40 · Proponer y asignar roles a partir de un FBX
- **Objetivo:** convertir la textura de un `TexturedMesh` en un mapa de roles confirmado por el usuario.
- **Bloqueantes:** T38.
- **Leer:** §4, §5.2; `src/afm/texmesh.py`, `src/afm/colorsrc.py` (cómo se muestrea la superficie por área), `tests/color_helpers.py` (`doll_textured`), `tests/fbx_synth.py`.
- **Crear:** `src/afm/colorroles.py` (parte 1), `tests/test_colorroles.py`.
- **Hacer:** `propose(tm, palette, k, seed, n_samples)`: (1) muestras de superficie ponderadas por área con `tm.sample`; (2) sRGB→Lab (`skimage`) y k-means con `scipy.cluster.vq.kmeans2(minit="++")`; (3) `centers_rgb` y `share` (% de área) por cluster; (4) sugerencia de rol por cluster con `palette.nearest` (o `None`); (5) **atlas de etiquetas de clusters** a ≤ 2048²: se reduce la textura con promedio por bloques, cada píxel se clasifica por el centro más cercano (por bloques de filas) y se filtra por **mayoría 5×5** (votos por etiqueta con `uniform_filter` + `argmax`, por bloques; no mediana). `build_package(tm, proposal, assign, …)` toma `assign: {cluster: rol | None}` (varios clusters → el mismo rol; `None` → 255 «sin rol») y devuelve el `RolesPackage` con las etiquetas ya en ids de rol. **No asigna roles por su cuenta**: sin `assign` completo levanta error.
- **Pruebas:** `test_clusters_of_flat_regions_recovered` (muñeco: 6–8 colores planos → ΔE entre centros y colores reales < 3) · `test_shades_of_one_role_can_share_label` (dos clusters → un rol) · `test_suggestion_uses_anchors_and_none_when_far` · `test_build_requires_complete_assignment` · `test_mode_filter_removes_isolated_pixels_and_keeps_edges` · `test_atlas_never_exceeds_2048` · `test_deterministic_with_seed` · `test_real_guemes_roles_in_under_30s` (`AFM_SLOW`; K = 10; anotar el tiempo).
- **Aceptación:** pasan; con Güemes real el filtro cambia < 2 % de los píxeles (medido: 0,45 % con mediana) [I].
- **No hacer:** archivos `.npz` (T41), GUI, tocar `ColorSource`.

### T41 · Paquete de roles y `TexturedMesh` con etiquetas
- **Objetivo:** guardar/leer el paquete y hacer que un `TexturedMesh` con etiquetas se comporte como fuente RGB.
- **Bloqueantes:** T40, T38.
- **Leer:** §5.4, §6; `src/afm/texmesh.py` (`sample`, `sample_image`, `transformed`), `src/afm/colorsrc.py`, `tests/test_texmesh.py`, `tests/test_colorsrc.py`.
- **Tocar:** `texmesh.py`, `colorsrc.py`, `colorroles.py`; **crear** `tests/test_roles_package.py`.
- **Hacer:** (1) `RolesPackage.save/load` (`np.savez_compressed`, `allow_pickle=False`; valida `formato`, formas y que `labels` use solo ids < `len(roles)` o 255). (2) `to_textured(palette, overrides)`: arma un `TexturedMesh` con `labels`, `lut = palette.with_overrides(...).lut(roles)`, `info["roles"]`; falla con `PaletteError` listando los roles ausentes. (3) `TexturedMesh.sample(..., "base_color")` con `labels`: **vecino más cercano** (columna = ⌊u·ancho⌋, fila = ⌊(1−v)·alto⌋, con tope en los bordes) y `lut[etiqueta]`; `transformed()` conserva `labels` y `lut`. Sin `labels`, el código viejo no cambia. (4) `ColorSource` guarda `sample_label` cuando hay etiquetas y suma `recolor(lut)` (recalcula `sample_rgb = lut[sample_label]` sin tocar el árbol).
- **Pruebas:** `test_package_roundtrip_exact` · `test_load_rejects_pickle_bad_shapes_unknown_labels` · `test_nearest_sampling_returns_only_palette_colors` (un atlas 4×4 con 3 etiquetas: ningún color intermedio) · `test_uv_convention_matches_bilinear_at_texel_centers` (mismo texel que `sample_image`) · `test_missing_role_raises_with_list` · `test_colorsource_from_roles_has_only_palette_colors` · `test_recolor_changes_rgb_without_rebuilding_tree` · `test_legacy_sampling_unchanged` (comparar con la salida previa guardada) · `test_package_is_much_smaller_than_fbx` (muñeco: < 5 % del FBX sintético).
- **Aceptación:** pasan; `test_texmesh`, `test_colorsrc`, `test_colorbake` sin cambios.
- **No hacer:** `bake_piece`, `dilate` (T45).

## Fase B — Catálogo y armado

### T42 · Catalogar una pieza con roles
- **Objetivo:** que `add_piece` guarde el paquete de roles de una pieza y `verify`/`color_file` lo conozcan.
- **Bloqueantes:** T39, T41.
- **Leer:** §5.2, §5.7, §6; `src/afm/parts.py` (`color_problems`, `color_block`, `write_piece`, `color_name`), `src/afm/catalog.py` (`add_piece`, `color_file`, `verify`, `_piece_files`, `delete_pieces`), `src/afm/extract.py` (marco de las piezas), `tests/test_color_catalog_head.py` (fixtures `line`, `head_fbx`).
- **Tocar:** `parts.py`, `catalog.py`, `catalog_cli.py`, `locales/*`; **crear** `tests/test_catalog_roles.py`.
- **Hacer:** (1) **primero medir** si el color de un torso/brazo/pierna extraído de una figura se obtiene con la matriz `FigureFrame` de la figura (§5.7) y anotar el resultado en `PROGRESS.md`; si no calza, detenerse (R1). (2) `PieceMeta.color` con `modo` (`"rgb"` por defecto), `roles`, `origen`; `color_problems` valida los dos modos; la ficha de una pieza sin color no cambia. (3) `add_piece(..., color_roles=RolesPackage, similarity=A)`: guarda `<id>@<v>.color.npz` junto al STL, `fuente`, `sha256`, `roles` (los usados, sin el 255) y `similitud`; la similitud puede venir del caller (figura entera) o, para una pieza completa, de `align_fbx_to_piece`. Falla antes de escribir si algún rol no está en la paleta de la línea. `color_file` y `verify` aceptan ambos modos; `_piece_files`/papelera incluyen el `.npz`. Una versión nueva sigue sin heredar el color. (4) CLI: `afm catalogo color RUTA REF --fbx ARCHIVO.fbx --roles ASIGNACION.json` (el JSON es `{cluster: rol}`; la GUI de T47 lo arma sola).
- **Pruebas:** `test_piece_ficha_without_color_unchanged` · `test_add_piece_with_roles_writes_npz_and_ficha` · `test_roles_missing_from_palette_writes_nothing` · `test_verify_detects_modified_or_missing_npz` · `test_delete_and_restore_move_the_npz_with_the_piece` · `test_new_version_does_not_inherit_roles` · `test_legacy_rgb_piece_still_loads_and_verifies` · `test_color_file_works_for_both_modes` · `test_cut_piece_uses_figure_similarity_not_moments` (torso del muñeco recortado de una figura con FBX).
- **Aceptación:** pasan; `catalogo/ejemplo` sigue verificando; el `.npz` de una pieza pesa < 1 MB con Güemes real (`AFM_SLOW`).
- **No hacer:** armado (T43), GUI.

### T43 · Armado desde el catálogo con la paleta de la línea
- **Objetivo:** que `afm armar` y «Armar desde catálogo» coloreen con roles + paleta, y lo informen.
- **Bloqueantes:** T42.
- **Leer:** §5.3, §5.6; `src/afm/colorpiece.py` (`catalog_color_sources`), `src/afm/cli.py` (líneas del armado, ≈ 185–220), `src/afm/colorexport.py` (`write`, informe), `tests/test_color_catalog_head.py` (fixture `armed`).
- **Tocar:** `colorpiece.py`, `colorexport.py`, `cli.py`, `locales/*`; **crear** `tests/test_color_catalog_palette.py`.
- **Hacer:** en `catalog_color_sources`, si `meta.color["modo"] == "roles"` el proveedor carga el `.npz`, arma `to_textured(catalog.palette(), overrides)` **al colorear** (así un cambio de paleta se toma sin reconstruir nada) y sigue por `piece_color_source(... similarity=A, to_host=T)`; un rol ausente → error claro antes de registrar. Las piezas `rgb` siguen igual. El informe suma `paleta` (`{hash, roles: {id: hex}}`) y, por pieza, `roles` usados; `fuente` sigue siendo `pieza`/`figura`/`plano`.
- **Pruebas:** `test_armed_figure_pieces_have_only_palette_colors` (colores únicos del atlas ⊆ paleta ∪ plano) · `test_changing_palette_hex_changes_atlas_color_and_not_stl_bytes` · `test_missing_role_stops_before_registration_with_list` · `test_mixed_rgb_and_roles_pieces` · `test_report_has_palette_digest_and_roles` · `test_mirrored_right_piece_uses_same_roles` · `test_figure_without_roles_armed_exactly_as_before`.
- **Aceptación:** pasan; los STL del armado son idénticos con y sin color.
- **No hacer:** figura base (T44), GUI.

### T44 · Figura base con roles y cambios por figura
- **Objetivo:** que la figura que se está convirtiendo (la del FBX de `project.color`) también use roles, y que una figura pueda cambiar colores de la paleta sin tocar la línea.
- **Bloqueantes:** T39, T41.
- **Leer:** `src/afm/project.py` (`ColorSpec`, `Project.to_dict`), `src/afm/cli.py` (`make_colorizer`, `run`), `src/afm/gui/session.py` (`make_colorizer`, `resolve_color_source`), `tests/test_project_color.py`, `tests/test_mode_a.py`.
- **Tocar:** `project.py`, `cli.py`, `gui/session.py`, `catalog_project.py` si hace falta, `locales/*`; **crear** `tests/test_project_roles.py`.
- **Hacer:** `ColorSpec.roles` (ruta relativa del paquete) y `ColorSpec.paleta_override` (`{rol: hex}`), ambos aditivos y fuera del JSON si no existen. `make_colorizer`: con `roles`, la fuente de la figura sale de `RolesPackage.to_textured(paleta, override)` y la paleta es la de la línea abierta (`--catalogo` o la de la sesión) [P: ver §12.2]. El informe lleva la paleta usada.
- **Pruebas:** `test_project_without_roles_roundtrips_byte_identical` · `test_project_with_roles_and_override_roundtrip` · `test_base_figure_with_roles_has_only_palette_colors` · `test_override_changes_only_that_figure` (la `paleta.json` de la línea no cambia) · `test_missing_package_is_a_clear_error_like_missing_fbx` · `test_mode_a_and_mode_b_still_work_with_rgb`.
- **Aceptación:** pasan; proyectos de `examples/` y `catalogo/` cargan y guardan sin cambios.
- **No hacer:** GUI.

### T45 · Recolor rápido de lo ya horneado (opcional)
- **Objetivo:** cambiar un color y ver la figura armada recoloreada en < 1 s, sin registrar ni hornear de nuevo.
- **Bloqueantes:** T43 o T44. Solo se hace si en T50 el rehorneo completo resulta incómodo.
- **Leer:** `src/afm/colorbake.py` (`BakeResult`, `dilate`, `bake_piece`), `src/afm/colorexport.py` (`Colorizer`, `write`).
- **Tocar:** `colorbake.py`, `colorexport.py`; **crear** `tests/test_colorexport_recolor.py`.
- **Hacer:** `BakeResult.labels` (atlas `uint8` en paralelo al RGB cuando la fuente trae etiquetas; `dilate` copia la etiqueta del vecino; el relleno plano usa la etiqueta 255) y `Colorizer.recolor(palette)`: rehace `atlas = lut[labels]` por pieza y reescribe OBJ/MTL/PNG/GLB. Sin etiquetas, `BakeResult` y las salidas quedan idénticas.
- **Pruebas:** `test_recolor_equals_full_rebake_pixel_for_pixel` · `test_dilate_copies_label_not_blend` · `test_flat_fill_stays_flat_after_recolor` · `test_legacy_bake_unchanged` · `test_recolor_under_one_second` (muñeco).
- **Aceptación:** pasan.
- **No hacer:** tocar `register_piece` ni `unwrap`.

## Fase C — GUI (lógica en el controlador/sesión, sin GTK en las pruebas)

### T46 · Pestaña «Paleta» en la ventana de Catálogo
- **Objetivo:** ver y editar los roles y colores de la línea.
- **Bloqueantes:** T39.
- **Leer:** `src/afm/gui/catalog_window.py`, `src/afm/gui/catalog_manager.py` (`PreviewRenderer`), `src/afm/gui/controller.py`, `src/afm/gui/render.py` (`TexturedPart`), `tests/test_catalog_manager.py`.
- **Tocar:** archivos de la GUI, `locales/*`; **crear** `tests/test_gui_palette_panel.py`.
- **Hacer:** lista de roles con muestra de color, nombre, id y **piezas que lo usan**; selector de color (el de GTK4 que ya use el repo) y campo HEX; agregar, renombrar y quitar rol (quitar se bloquea si está en uso, con la lista); importar/exportar JSON; guardar escribe `paleta.json`. **Vista previa:** al elegir una pieza con roles se la muestra con `render_textured`, y cambiar un HEX la repinta con la LUT sin guardar hasta «Aplicar». Aviso de cuántas piezas usa el rol que se está cambiando.
- **Pruebas (controlador):** `test_palette_edit_apply_writes_file_only_on_apply` · `test_remove_role_in_use_is_blocked_with_pieces` · `test_usage_counts_match_catalog` · `test_preview_lut_matches_palette` · `test_import_rejects_bad_json_without_changing_palette`.
- **Aceptación:** pasan; la pestaña **no verificada en pantalla** si no hay GTK (R9).

### T47 · Asistente «Roles de color»
- **Objetivo:** pasar de un FBX a roles asignados y guardarlos en el catálogo o en la figura base.
- **Bloqueantes:** T40, T42 (y T44 para la figura base).
- **Leer:** `src/afm/gui/catalog_import.py` (cómo se arman los diálogos), `src/afm/gui/catalog_window.py`, `src/afm/colorroles.py`.
- **Tocar:** GUI, `locales/*`; **crear** `tests/test_gui_roles_wizard.py`.
- **Hacer:** (1) elegir el FBX y la pieza o figura; (2) mostrar los clusters como fichas (color, % del área) y la **vista previa** de la figura con cada cluster pintado de su color; (3) por cada ficha un desplegable con los roles de la paleta, «rol nuevo…» (pide nombre y crea el rol con ese color) y «sin rol»; los clusters que **proponen** un rol vienen preseleccionados pero marcados como sugerencia; (4) no se puede guardar con fichas sin decidir; (5) al guardar se agregan como **anclas** del rol los colores de los clusters asignados (hasta 8) y se llama a `add_piece(color_roles=…)` o se escribe el paquete de la figura base.
- **Pruebas (controlador):** `test_wizard_blocks_saving_with_undecided_clusters` · `test_new_role_is_created_with_cluster_color_and_anchor` · `test_anchors_capped_at_eight` · `test_second_figure_gets_suggestions_from_first` (muñeco A → anclas → muñeco B) · `test_saved_piece_reloads_with_roles`.
- **Aceptación:** pasan; el flujo **no verificado en pantalla** si no hay GTK (R9).

### T48 · Armar desde el catálogo: paleta, cambios por figura y recolor
- **Objetivo:** que la ventana de armado muestre la paleta, permita cambios solo para esa figura y recolorear.
- **Bloqueantes:** T43, T44, T46 (T45 si se hizo).
- **Leer:** `src/afm/gui/figure_builder.py`, `src/afm/gui/figure_window.py` (≈ l. 540–570: carga del GLB y botón «Textura»), `src/afm/gui/session.py`.
- **Tocar:** GUI, `locales/*`; **crear** `tests/test_gui_figure_palette.py`.
- **Hacer:** bloque «Colores» con los roles de la línea y su color; cambiar uno guarda un `paleta_override` de la figura (no toca la línea); botón «Recolorear» (con T45: `Colorizer.recolor`; sin T45: vuelve a correr solo el color, sin reconstruir la geometría); el informe previo lista roles faltantes y piezas sin color.
- **Pruebas (sesión/controlador):** `test_override_does_not_touch_line_palette` · `test_recolor_does_not_rebuild_geometry` (hash de los STL) · `test_missing_roles_listed_before_export` · `test_figure_without_palette_behaves_as_before`.
- **Aceptación:** pasan; GUI **no verificada en pantalla** si no hay GTK.

## Fase D — Cierre

### T49 · Auditoría de textos y compatibilidad hacia atrás
- **Objetivo:** que nada de lo nuevo rompa datos viejos ni falte en un idioma.
- **Bloqueantes:** T46–T48.
- **Hacer:** `grep` de literales visibles fuera de `tr()`/`lz()` en lo nuevo; claves `paleta.*`, `roles.*` en es/en/pt (traducciones reales, no copias); cargar todos los `.proyecto` de `examples/` y `catalogo/`, todas las fichas de `catalogo/ejemplo` y un catálogo con fichas `modo: "rgb"` (T35) y verificar que **no se reescribe ningún archivo por abrirlo**.
- **Pruebas:** `test_palette_keys_present_in_three_languages` · `test_old_projects_pieces_and_catalogs_load_and_are_not_rewritten` · `test_legacy_rgb_piece_still_colors_an_armed_figure` · `python -I -m pytest tests/test_i18n.py -q`.
- **Aceptación:** pasan sin modificar pruebas existentes.

### T50 · Figuras reales y medidas
- **Objetivo:** comprobar el flujo con archivos reales y anotar medidas.
- **Bloqueantes:** T49.
- **Casos:** (1) Güemes completo (`guemes_segmentado_lq_texturas.fbx`): roles con K = 10, agrupados en 5–6 roles; (2) la cabeza de Güemes (`guemes_cabeza_textura.fbx`) con **los mismos roles** (¿aparece el rol «pelo»? ¿cuántos clusters hay que asignar a mano?); (3) una segunda figura de la línea; (4) armar una figura con piezas de dos figuras y cambiar «uniforme» en la paleta: el cambio llega a todas las piezas y los **STL no cambian un byte**.
- **Medir y anotar:** tiempo de `propose`, peso del `.npz` por pieza, tiempo de armado con color contra el de T36, tiempo del recolor (T45 si existe), `pct_origen` por pieza, colores únicos por atlas (⊆ paleta ∪ plano), cuántos clusters sugirió bien el sistema y cuántos corrigió el usuario.
- **Criterios:** no se relaja ningún umbral de registro; si `align`/registro de las piezas recortadas (§5.7) no calza, se informa (R6).
- **Aceptación:** medidas en `PROGRESS.md`; lo no probado en pantalla, marcado **no verificado**.

### T51 · Documentación
- **Hacer:** `docs/PLAN_fbx_textura_color.md` §7.8 y `docs/PLAN_color_catalogo_cabeza.md` §4.1: el FBX completo por pieza queda para el modo `rgb`; el modo `roles` es el recomendado y resuelve la pregunta abierta del tamaño (≈ 26 MB → ≈ 0,2 MB). `docs/tutorial_gui.md` y `docs/manual_corto.md`: paleta de línea, asistente de roles, recolorear. `PROGRESS.md`: entradas T34–T37 que faltan y las de este plan.
- **Aceptación:** la documentación coincide con el código y no marca como verificado nada que no se probó en la GUI real.

---

## 11. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R-1 | La sugerencia automática de rol se equivoca (el pelo oscuro cayó en el verde del cuello, §4) | Nunca se asigna sin confirmar; las sugerencias van marcadas; «rol nuevo» y «sin rol» siempre disponibles |
| R-2 | Las texturas tienen la luz horneada: un rol agrupa tonos y el recoloreado pierde sombreado | Medido (ΔE 8,0 contra 4,3). Es lo buscado para impresión multicolor; mejora posible fuera de alcance (§13) |
| R-3 | El color de piezas recortadas de una figura no calza con la alineación por momentos (medido 0,58–1,42 mm) | §5.7: se usa la matriz de la figura entera; T42 lo mide antes de escribir nada |
| R-4 | Cada pieza arma una `ColorSource` de la figura entera (≈ 3 s y 1,6 M de muestras) | Medir en T50; si pesa, recortar el portador a las caras cercanas a la pieza al catalogar |
| R-5 | La paleta es editable: una exportación vieja no se puede repetir | Informe con la paleta usada y su hash (§5.6) |
| R-6 | Borrar un rol deja piezas sin color | Borrado bloqueado si está en uso; `verify` avisa |
| R-7 | El vecino más cercano muestrea etiquetas del relleno borroso entre islas, en los bordes de isla | Igual que el RGB de hoy: `shrink_to_interior` y `dilate` ya lo tratan; probar en T41/T50 |
| R-8 | Copias del paquete por pieza se desincronizan si se vuelve a clasificar | Volver a clasificar crea versión nueva de cada pieza (como hoy el color: no se hereda) |
| R-9 | Sin GTK en el entorno de desarrollo no se prueba la pantalla | Lógica en controlador/sesión; lo visual se marca **no verificado** |

## 12. Preguntas abiertas

1. **[P] Paleta compartida entre líneas.** ¿Querés una sola paleta para varias líneas? Este plan arranca con una por línea e intercambio por JSON. Un almacén global (con el versionado de la propuesta de ChatGPT) se agrega después si hace falta.
2. **[P] Paleta de la figura base.** Una figura que no sale solo de catálogo no tiene «su» línea: ¿se usa la línea abierta en la sesión, o se elige al convertirla? (T44)
3. **[P] ¿Se conserva el sombreado?** Hoy un rol es un color plano. Si para la vista previa se quiere conservar la luz horneada (color del rol × luminancia original), es una opción aparte; para imprimir multicolor no hace falta.
4. **[P] Cantidad de roles por línea.** El tope es 64; ¿cuántos esperás usar? Con impresión CMYWK en Bambu, lo habitual serán pocos (6–10).
5. **[P] Mano.** `extract.py` deja la mano en un marco estándar propio: ¿su color sale también de la figura (con otra similitud) o se deja sin color en la v1?
6. **[P] Piezas con su FBX propio (T35).** Las piezas `rgb` existentes siguen funcionando; ¿hay alguna que valga la pena convertir a roles?

## 13. Fuera de alcance

- Un almacén global de paletas con versiones y `seguir_ultima` (ver pregunta 1).
- PNG indexado aportado por el usuario como textura de entrada (se puede agregar después reutilizando `RolesPackage`).
- Sombreado horneado conservado como modulación de luminancia (pregunta 3).
- Edición de etiquetas pintando sobre el atlas.
- Impresión multicolor 3MF/filamentos (T30 del plan base), aplicación web/Pyodide, cambios de geometría, de registro o de `unwrap`.
