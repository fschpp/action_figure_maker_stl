# Plan: importar FBX con textura y conservar el color en la figura articulada

**Versión:** 0.4 (completa, 30 tareas ejecutables) — 2026-11-18
**Repo:** `fschpp/action_figure_maker_stl` → `docs/PLAN_fbx_textura_color.md`
**Contrastado con:** `main` @ `f62f19d` y con `examples/guemes_segmentado_lq_texturas.fbx`.
**Marcas:** **[C]** dato del cliente o del archivo · **[M]** medido en la prueba de concepto (v0.2) · **[M3]** medido al preparar la v0.3 · **[M4]** medido al preparar esta versión · **[I]** inferido, a validar · **[P]** pendiente de decisión del cliente · **[V]** «verificar»: dato de código que la tarea debe confirmar antes de usarlo.

---

## 0. Cómo usar este documento

§1 resume qué cambió; §2–§8 explican el diseño; §9 fija las reglas comunes; §10 es el grafo de dependencias; del §11 al §16 hay **una ficha por tarea** (T01…T30).

Una ficha está pensada para que **un solo turno** (Sonnet 5.5, esfuerzo medio, sin contexto previo) la resuelva leyendo: la ficha, las secciones que cite y los archivos que nombre. Cada ficha trae: objetivo, bloqueantes verificables, qué leer, qué crear o tocar, contrato (firmas), pasos, pruebas con nombre, verificación, aceptación y qué **no** hacer. Si un bloqueante no se cumple, la tarea **se detiene y lo informa** (R2); no improvisa el módulo que falta.

## 1. Qué cambió respecto de la v0.2 y la v0.3

| # | Antes | Ahora | Por qué |
|---|---|---|---|
| 1 | Fases con entregables grandes | **30 tareas** de un turno, con contratos de interfaz fijados en el §6 | Una fase no cabe en un turno; sin contrato cada tarea reinventa firmas |
| 2 | Despliegue UV por cartas por defecto | **Primero «una celda por triángulo» (T10)**; cartas pasan a T20 (opcional) | Corto, sin solapes por construcción; la calidad se mide en T16 y recién ahí se decide T20 |
| 3 | Convención V sin comprobar | Fila de píxel = `(1 − v)·alto − 0,5`; columna = `u·ancho − 0,5` [M3] | Con `1 − v` el desvío entre esquinas de un mismo vértice es 0,29/255; con `v` directo 5,3/255 |
| 4 | `crown_z` «≈ 101,8» | **101,769** (unidades FBX tras `Lcl Scaling`) [M3] | Escala FBX→STL 0,983079 |
| 5 | Distancia FBX→STL 0,017 mm | Sin ICP: media 0,036 · p95 0,055 · máx 0,336 mm; con ICP de similitud ≈ 0,017 [M3] | Los criterios de T04 usan ambos |
| 6 | Estructura del FBX en prosa | **Mapa de nodos medido** (§3.1) | El lector se escribe sin explorar el archivo |
| 7 | `FigureFrame` «extiende `to_figure`» | `LoadReport` guarda la matriz de `orient_z_up` (T03) | Hoy no devuelve su rotación |
| 8 | Sección `color` del proyecto dentro de la GUI | Tarea propia **T23, sin bloqueantes** | Es solo `project.py` |
| 9 | Casos completos en una fase | T17 cabeza y cintura · T18 rodillas/codos/clic · T19 sin-soportes, asimetría, catálogo | Cada una cita sus fixtures de `tests/` |
| 10 | Sin parser verificado | **T01 ya hecha** (`src/afm/fbxio.py` + `tests/test_fbxio.py`, 10 pruebas verdes, 0,03 s sobre el FBX real) [M4] | Se entrega en el parche que acompaña a este documento |
| 11 | La v0.3 quedaba cortada en §2, T02, T11 y T18 | Documento completo | — |

Se mantienen las decisiones D1–D8 de la v0.2 (§5).

## 2. Resumen

Hoy la herramienta toma un STL (geometría sola) y entrega las piezas de una figura articulada en STL. La feature agrega:

1. **Entrada:** un `.fbx` con malla + UV + texturas embebidas, como **fuente de color** junto a un STL ya trabajado (modo B, primero) o como **entrada única** (modo A, después).
2. **Proceso:** el motor (planos, siluetas, cavidades, pasadores, espejo, pose sentada, validaciones) sigue trabajando **solo con geometría**, sin cambios.
3. **Salida:** cada pieza sale **también con color**: un atlas PNG propio, horneado desde la textura original. Las superficies que no existían en la figura (cortes, cavidades, pasadores) reciben el color vecino o uno neutro. Los STL no cambian **ni un byte**.

**Idea central [I]:** no arrastrar UV por booleanos y offsets (el offset por campo de distancia + marching cubes destruye la topología). Se conserva **intacta la malla original con textura como «fuente de color»** y, al final, a cada pieza se le **hornea** el color buscando, por cada punto de su superficie, el punto más cercano de la fuente. La prueba de concepto lo confirma en Güemes [M].

**Alcance v1:** FBX binario con 1 malla y 1 material; textura de color base (las otras tres solo para vista previa); registro de piezas giradas; atlas PNG por pieza; salida OBJ+MTL+PNG por pieza y GLB ensamblado; visor con textura. **Fuera:** animación/skinning, varios materiales, imprimir en varios filamentos (T30, depende de P1), color propio de piezas de catálogo (gancho en §7.8).

## 3. Datos medidos

### 3.1 El FBX de ejemplo (`examples/guemes_segmentado_lq_texturas.fbx`, 26,6 MB)

Mapa de nodos medido con `fbxio.parse_fbx` [M4]:

| Dato | Valor |
|---|---|
| Versión / raíces | 7400; `FBXHeaderExtension, FileId, CreationTime, Creator, GlobalSettings, Documents, References, Definitions, Objects, Connections, Takes` |
| Tiempo de parseo | 0,03 s (el árbol completo, con las 4 imágenes) |
| Nombres de objetos | `props[1]` es `"nombre\x00\x01Clase"` (separador `\x00\x01`); `props[0]` es el id numérico; `props[2]` el subtipo (`"Mesh"`) |
| `Geometry` | `Vertices` 16 035 flotantes (5345 vértices) · `PolygonVertexIndex` 21 850 (último índice de cada polígono complementado: `~i`) · `Edges` 10 928 · `LayerElementNormal/Binormal/Tangent/UV/Material` · `Layer` |
| `LayerElementUV` | `Name="UVMap"`, `MappingInformationType="ByPolygonVertex"`, `ReferenceInformationType="IndexToDirect"`, `UV` 12 512 flotantes… (pares), `UVIndex` 21 850: **UV por esquina** |
| `Model` | 1; `Lcl Scaling` = (100, 100, 100); `Lcl Rotation` ≈ (−4e−6, 0, 0) (despreciable); se leen en `Properties70` como `P` con `props[0]` = nombre y valores en `props[4:7]` |
| `GlobalSettings` | `UpAxis`=1, `FrontAxis`=2, `CoordAxis`=0, `UnitScaleFactor`=1 |
| Conexiones | `OO` hijo→padre y `OP` hijo→padre con propiedad: `Model→raíz(0)`, `Geometry→Model`, `Material→Model`, `Texture→Material` por `OP` con `DiffuseColor` (color base), `NormalMap`, `ShininessExponent` (rugosidad) y `ReflectionFactor` (metálico); `Video→Texture` por `OO` |
| Texturas | 4 `Texture` (`base_color_texture`, `normalmap_texture`, `roughness_texture`, `metallic_texture`) y 4 `Video` con `Content` PNG embebido: 12 524 256 · 7 843 831 · 2 852 103 · 2 579 272 B; todas 4096² |
| Malla | 5345 vértices, 5104 cuadriláteros + 478 triángulos = 10 686 triángulos; 1 solo componente [C] |
| Escala | Caja 0,489 × 1,160 × 0,203 → 48,9 × 116,0 × 20,3 tras `Lcl Scaling` [C] |
| Densidad | Textura ≈ 38 texeles/mm sobre la figura a 100 mm (0,026 mm/texel); arista media de malla 1,5 mm [M] |
| UV | 36 islas, 0 triángulos invertidos, islas rodeadas de relleno borroso, ≈ 70 % del atlas ocupado [C] |

### 3.2 El FBX frente al STL de Güemes del repo [M]/[M3]
- FBX 5345 vértices / 10 686 triángulos; STL 5344 / 10 684 (estanco). Alto 116,01 contra 114,00; escala FBX→STL **0,983079**.
- Con la rotación de `meshio.orient_z_up` (Y→Z, `R = [[1,0,0],[0,0,-1],[0,1,0]]`) y esa escala: STL→FBX media 0,017 mm, p95 0,040, máx 0,062 (con ICP). Solo con caja y escala: media 0,036, p95 0,055, máx 0,336 [M3].
- La figura queda mirando a −Y, igual que `front="-Y"` de `examples/guemes.py`. Espejada en Y o girada 180° sobre Z la distancia media sube a 1,72 mm: el control de orientación es inequívoco.
- `crown_z` del FBX (unidades tras `Lcl Scaling`) = **101,769** para que `ref_height = 100` dé el mismo marco que el STL [M3].

### 3.3 Piezas reales de Güemes [M]
| Pieza | Triángulos | Área mm² | Con origen (d ≤ 0,6) | Transformación hallada |
|---|---|---|---|---|
| torso | 22 652 | 5298 | 72,7 % | identidad |
| brazo izq / der | 2644 / 2640 | 1526 | 84,4 / 84,2 % | identidad |
| pierna izq / der | 1982 | 2271 | 84,9 / 84,7 % | giro de −90° sobre (centro de cadera, eje X) |
| pierna sin registrar | | | **6,3 %** | — |

Distancia pieza→fuente del torso (acumulada): ≤ 0,02: 43,0 % · ≤ 0,05: 68,7 % · ≤ 0,1: 69,3 % · ≤ 0,2: 70,3 % · ≤ 0,6: 73,4 %. La meseta entre 0,05 y 0,3 mm indica que **ε = 0,1–0,2 mm separa limpio** lo original de lo nuevo.

### 3.4 Color y simetría [M]
Comparando 40 000 puntos con su espejo (x → −x): mediana 4,7/255, p90 18, p99 63; **2,3 %** difiere > 40/255 (cara, pelo, barba). Justifica «respetar el lado original» por defecto.

### 3.5 Rendimiento [M] (1 núcleo)
BVH exacto (`trimesh.proximity` + `rtree`): ≈ 25 mil consultas/s. `cKDTree` con muestras cada 0,08 mm (1,6 M): construir 3,2 s, 29 mil/s, error de color medio 3,2/255 (p95 12,5). Cada 0,05 mm (4,1 M): 8,6 s, 9 mil/s, error 2,2/255. `xatlas` 0.0.9–0.0.11: **segfault**, incluso con una esfera. 5 piezas (12 892 mm²) a texel 0,2 mm: 322 mil texeles, ≈ 12 s; a 0,1 mm: 1,29 M, ≈ 48 s.

### 3.6 Consecuencias
La textura original es 15 veces más fina que la boquilla de 0,4 mm: nadie necesita atlas de 4096². Con aristas de 1,5 mm el color por vértice no alcanza. Los UV son por esquina: la consulta usa siempre los UV **de la cara** hallada. Las piezas llegan **en pose**, por lo que el registro es esencial.

## 4. Principios de diseño
1. **Geometría y color separados.** El motor no sabe de colores. Sin FBX todo funciona como hoy.
2. **La fuente de color es inmutable** (malla original con UV y textura, sin reparar).
3. **Un único marco** (`FigureFrame`) aplicado igual a la geometría y a la fuente.
4. **Solo rotaciones propias (det = +1).** Un intercambio de ejes espeja figura y UV.
5. **Registrar antes de consultar:** cada pieza se lleva a la pose de reposo de la fuente; los UV no cambian (la transformación es rígida).
6. **No instrumentar `pipeline.py`** salvo el gancho opcional de `export` (T15). Registro por búsqueda con semillas de `BuildResult.motion`.
7. **Modo B primero.**

## 5. Decisiones

| # | Decisión | Descartado |
|---|---|---|
| D1 | Modo B primero, modo A después | FBX como entrada única desde el inicio |
| D2 | ε = 0,15 mm con autocalibración | ε fijo de 0,6 mm |
| D3 | Despliegue UV propio; `xatlas` opcional | `xatlas` obligatorio |
| D4 | `cKDTree` sobre muestras densas | BVH con `rtree` |
| D5 | Atlas por texel físico, 0,2 mm | Atlas fijo de 1024² |
| D6 | Registro por búsqueda de ángulo | Registrar cada giro en `pipeline.py` |
| D7 | Color en archivos paralelos al STL | Cambiar la salida STL |
| D8 | «Respetar el lado original» por defecto | Forzar simetría |
| D9 | Primero despliegue «celda por triángulo» (T10); cartas (T20) solo si T16 lo justifica | Cartas desde el inicio |
| D10 | Los módulos de color importan `PIL` y `trimesh` de forma perezosa; `fbxio` no los importa | — |

**Preguntas abiertas [P]:** P1 ¿para qué quiere el color (pintar a mano, varios filamentos/AMS, renders, impresión a todo color)? define T30. P2 ¿vendrán también GLB/OBJ? define T29. P3 ¿confirma respetar asimetrías? P4 ¿el FBX de servicio externo será el estándar? (medir bordes borrosos entre islas, RG12). P5 ¿la cabeza irá separada en las figuras con color? Cerrada: P6, el FBX es la malla del STL.

## 6. Contratos de interfaz (únicos; todas las tareas los respetan)

Módulos planos en `src/afm/`. `fbxio` no importa `PIL` ni `trimesh`; los demás los importan dentro de las funciones que los usan.

```python
# fbxio.py (T01 hecha; T02 amplía)
class FbxError(ValueError): ...
class Node: name: str; props: list; kids: list[Node]; find(name) -> list[Node]; first(name) -> Node | None
def fbx_version(data: bytes) -> int
def parse_fbx(data: bytes) -> Node                      # raíz sintética "root"
def load_textured(source: str | bytes) -> TexturedMesh  # ruta o bytes (T02)

# texmesh.py (T02)
@dataclass
class TexturedMesh:
    vertices: np.ndarray        # (V,3) float64, ya multiplicada por Lcl Scaling, unidades del FBX
    faces: np.ndarray           # (F,3) int32 (cuadriláteros partidos por la diagonal más corta)
    corner_uv: np.ndarray       # (F,3,2) float32, UV de cada esquina; v hacia arriba (convención FBX)
    images: dict[str, bytes]    # "base_color" | "normal" | "roughness" | "metallic" -> bytes PNG
    info: dict                  # {"avisos": [...], "up_axis": int, "scaling": [3], "n_materiales": int, ...}
    def image(self, kind: str = "base_color") -> np.ndarray     # uint8 (H,W,3), decodifica perezosa y cachea
    def sample(self, face: np.ndarray, bary: np.ndarray, kind: str = "base_color") -> np.ndarray  # (n,3) uint8, bilineal
    def to_trimesh(self) -> trimesh.Trimesh                     # solo geometría
# Convención de píxel [M3]: fila = (1 - v)*alto - 0.5 ; columna = u*ancho - 0.5 ; bilineal con borde "clamp".

# frame.py (T03)
@dataclass
class FigureFrame:
    matrix: np.ndarray          # (4,4) archivo -> marco de la figura (mm, pies en z=0, centrada, frente +Y, simetría en x=0)
    def apply(self, points) -> np.ndarray
    def det(self) -> float      # debe ser +1 salvo la escala: det(R) = +1
    @classmethod
    def from_load(cls, report: LoadReport, front: str = "+Y", symmetry: Plane | None = None) -> "FigureFrame"
    @classmethod
    def from_similarity(cls, source_vertices, target_mesh, refine: bool = True) -> "FigureFrame"   # modo B (T04)

# colorsrc.py (T07)
@dataclass
class ColorQuery: rgb: np.ndarray (n,3) uint8; dist: np.ndarray (n,) float32; has_origin: np.ndarray (n,) bool; face: np.ndarray (n,) int32
class ColorSource:
    def __init__(self, tm: TexturedMesh, frame: FigureFrame | None = None, spacing_mm: float = 0.08, seed: int = 0)
    def query(self, points, normals=None, eps: float = 0.15, min_dot: float = 0.3, workers: int = 1) -> ColorQuery
    def distance(self, points) -> np.ndarray        # versión gruesa, para puntuar registros
    mesh: trimesh.Trimesh                           # geometría de la fuente en el marco de la figura

# colorreg.py (T08, T18)
Seed = tuple | list      # (centro(3), eje(3), ángulo_deg) o lista de esos pasos, aplicados en orden
@dataclass
class Registration:
    status: str             # "identidad" | "semilla" | "barrido" | "sin_origen"
    matrix: np.ndarray      # (4,4): pieza en pose -> pose de reposo de la fuente
    angle_deg: float; score: float; seed: object
def seeds_for_piece(piece_name: str, motion: dict, params, report: dict | None = None) -> list[Seed]
def register_piece(piece_name: str, mesh, src: ColorSource, motion: dict, params, report: dict | None = None,
                   n_points: int = 1500, thresh_mm: float = 0.3, min_score: float = 0.4) -> Registration

# unwrap.py (T10, T20)
@dataclass
class Unwrapped:
    vertices: np.ndarray    # (n,3) float64, pieza (posiblemente subdividida; misma geometría)
    faces: np.ndarray       # (m,3) int32
    src_face: np.ndarray    # (m,) int32, cara de la pieza original de cada cara
    tri_px: np.ndarray      # (m,3,2) float32, coordenadas continuas de píxel (x der, y abajo); centro del píxel (fila r, col c) = (c+0.5, r+0.5)
    uv: np.ndarray          # (m,3,2) float32 = (x/ancho, 1 - y/alto)
    size: tuple[int, int]   # (ancho, alto)
    texel_mm: float         # efectivo
def unwrap_triangles(mesh, texel_mm: float = 0.2, margin_px: int = 2, max_cell_px: int = 64,
                     max_atlas: int = 4096, max_edge_mm: float | None = None) -> Unwrapped
def check_no_overlap(unw: Unwrapped) -> int

# colorbake.py (T11–T13)
@dataclass
class TexelGrid: rows, cols: (n,) int32; face: (n,) int32; bary: (n,3) float32; points: (n,3) float64; normals: (n,3) float64; size: tuple
def texel_points(unw: Unwrapped) -> TexelGrid
@dataclass
class BakeResult: atlas: np.ndarray (alto,ancho,3) uint8; mask: np.ndarray (alto,ancho) bool; stats: dict  # pct_origen, area_mm2, n_texeles, segundos
def bake_piece(mesh, unw: Unwrapped, src: ColorSource, reg: Registration, *, eps=0.15, fill="extend"|"#RRGGBB",
               symmetric=False, dilate_px=6, workers=1) -> BakeResult

# colorexport.py (T14, T15)
class Colorizer:        # el «gancho» que recibe pipeline.export(colorize=...)
    def __init__(self, src: ColorSource, params, *, texel_mm=0.2, eps=0.15, fill="extend", symmetric=False, workers=1)
    def __call__(self, name: str, mesh, motion: dict, report: dict | None = None) -> dict   # devuelve info y guarda el atlas internamente
    def write(self, outdir: str, fig_name: str, orientation=None) -> dict    # OBJ+MTL+PNG por pieza, GLB ensamblado, informe JSON
```

**Nombres de pieza y `motion` [V]:** `res.motion` tiene una entrada por **miembro** (`"brazo"`, `"pierna"`, claves = `LimbSpec.name`) y una por `HEAD_NAME = "cabeza"` (`head.py:39`, giro 0–90°); **no** hay entrada para la cintura. Las piezas de miembro se llaman `<miembro>_izq` / `<miembro>_der` [V: imprimir `sorted(res.pieces)` en T08]. Las de articulación son `{nombre}_izq/_der` con `JointSpec.names()`; su informe está en `res.report["joints"][nombre]` con `center, axis, print_flex, print_hip, piezas, limb`. Parámetros útiles de `Params`: `sit_angle=90`, `hip_margin=5`, `print_angle=10` (hombro/clic), `knee_print_angle=85`, `elbow_print_angle=0`, `ref_height=100`, `clearance=0.50`, `export_max_tris=150000`.

## 7. Algoritmos

### 7.1 Lectura del FBX
Localizar `Geometry` (tercera propiedad `"Mesh"`), `Model`, `Material`, `Texture`, `Video`. Polígonos desde `PolygonVertexIndex` (último índice `~i`). Triangular en abanico; cuadriláteros por la diagonal más corta. `LayerElementUV` con `UVIndex` por esquina. `Lcl Scaling` aplicado. Imagen de color por la cadena `Material ←OP(DiffuseColor)— Texture ←OO— Video.Content` (no por nombre de archivo). Normales: se recalculan.

### 7.2 Marco de la figura
El motor aplica: `orient_z_up` → `pre`-escala → `repair` → `normalize` (escala, pies en z=0, centrado en x,y) → giro de 180° sobre Z si `front == "-Y"` → `align_to_symmetry` (rotación sobre Z + traslado en x). `FigureFrame` compone lo mismo en 4×4 para la fuente. **Modo A:** el FBX es la entrada; `crown_z` = 101,769 en unidades del FBX tras `Lcl Scaling`. **Modo B:** similitud (rotación de `orient_z_up`, escala y traslado por caja, ICP de similitud); se acepta si la distancia media ≤ 0,1 mm, si no: «el FBX no es la misma figura». Si `LoadReport.remeshed`, ε sube a `max(ε, 0,3)` y se avisa.

### 7.3 Consulta de color
Punto más cercano `q` de la fuente, cara `t`, distancia `d`. Hay origen si `d ≤ ε` (0,15 mm) y `dot(normal_t, normal_pieza) > 0,3` [I]; entonces se muestrea la textura con las baricéntricas de `q` y los UV **de las esquinas de `t`**. Camino por defecto: muestras densas (0,08 mm) con cara y baricéntricas en un `cKDTree`.

### 7.4 Registro pieza→fuente
Cada pieza es la superficie original recortada y rotada rígidamente (más espejo en `_der`); basta una rotación de **un grado de libertad** sobre el eje del miembro. (1) Semillas: identidad y ±`sit_angle`, ±`print_angle`, ±`print_flex` sobre `(centro, eje)` de `motion`; para `_der` se refleja el centro (x → −x) y, si el eje es X, el ángulo no cambia. (2) Puntuación: fracción de ≈ 1500 puntos con `d ≤ 0,3 mm`. (3) Si ninguna supera 40 %: barrido 0–360° cada 15° y refinado ±7,5°. (4) Si el mejor no llega a 40 %: **«sin origen»** y color neutro. En Güemes: identidad en torso y brazos, −90° en piernas [M].

### 7.5 Despliegue y horneado
Despliegue (T10: una celda por triángulo; T20: cartas). Tamaño por texel físico (0,2 mm). Rasterizado → punto 3D por texel → consulta. Dilatación 4–8 texeles (`scipy.ndimage`). PNG por pieza y UV en el OBJ.

### 7.6 Espejo y asimetría
Se consulta en las **posiciones reales** de la pieza: la derecha toma el color del lado derecho (por defecto). Opción «simétrico»: las piezas con x < 0 consultan con x reflejada.

### 7.7 Superficies sin origen
(1) **Extender** (defecto): color del punto más cercano sin importar `d`. (2) **Color plano** (`#C8C8C8`). (3) **Máscara** e informe por pieza.

### 7.8 Piezas de catálogo
Marcadas «sin origen», color plano. Gancho: `PieceMeta.color` opcional (no se implementa en v1).

## 8. Interfaz

**CLI** (flags en español): `afm build guemes.stl proyecto.json --color-desde guemes.fbx` (modo B) · `afm build guemes.fbx proyecto.json` (modo A) · `--sin-color`, `--textura-mm 0.2`, `--color-simetrico`, `--color-relleno "#C8C8C8"`, `--color-tolerancia 0.15`.
**GUI:** abrir `.fbx` con resumen; botón «Textura» en el visor; bloque «Color» en parámetros; informe de color antes de guardar; el `.proyecto` guarda ruta y hash de la fuente.
**Web (Pyodide):** lector FBX, `cKDTree`, `Pillow` y despliegue propio son compatibles; `xatlas` y `rtree` no.
**Salidas:** `piezas/*.stl` y `*_ensamblada.stl` sin cambios (byte a byte) · `piezas_color/<fig>_<pieza>.obj|.mtl|_color.png` · `<fig>_ensamblada.glb` · `<fig>_color_informe.json`.

## 9. Reglas comunes de ejecución

- **R1 · Entorno.** `pip install -e ".[dev]"` ya hecho; `src/afm/` es plano; Python ≥ 3.10. Con `python -I` el `PYTHONPATH` se ignora: el paquete debe estar instalado en modo editable. Sin red para datos: el FBX está en `examples/`.
- **R2 · Bloqueante incumplido ⇒ detenerse.** Informar cuál bloqueante falló (el comando de verificación de la ficha lo dice) y no crear el módulo que falta.
- **R3 · No cambiar salidas existentes.** Sin `colorize`, `export` produce los mismos bytes. Ningún test existente se modifica salvo que la ficha lo diga.
- **R4 · Importaciones perezosas** de `PIL`/`trimesh` en los módulos de color; `fbxio` no los importa.
- **R5 · i18n.** Todo texto visible al usuario (CLI, GUI, avisos) va a `src/afm/locales/{es,en,pt}.json`; los avisos de motor en español, como el resto.
- **R6 · Pruebas con nombre.** Cada ficha lista los nombres exactos de las pruebas; se crean en el archivo indicado. Las pruebas que usan el FBX real llevan `skipif` si falta el archivo.
- **R7 · Bitácora.** Al terminar, agregar al **principio** de `PROGRESS.md` una sección «Color FBX — T## (fecha)» con: qué se hizo, medidas reales obtenidas, qué no se verificó. No reescribir secciones anteriores.
- **R8 · No relajar umbrales.** Si un criterio de aceptación no se cumple, informar la medida real y detenerse; no cambiar el umbral para que pase.
- **R9 · Regresión.** Además de las pruebas de la ficha: `python -I -m pytest tests/test_packaging.py tests/test_pipeline.py tests/test_export_clean.py -q` (≈ minutos; si `test_pipeline.py` tarda demasiado, `-x`). Ninguna ficha debe romperlas.
- **R10 · Tiempo.** Una tarea que necesite generar Güemes completo (26 s) o el muñeco (≈ 40 s) usa `scope="module"` en los fixtures y lo hace una vez.
- **R11 · Entrega.** Cada tarea deja el árbol de trabajo con los archivos nuevos/tocados y, si el turno termina antes, un parche `git diff > tarea_T##.patch` con lo hecho y la lista de lo que falta.
- **R12 · Determinismo.** Semillas fijas (`np.random.default_rng(0)`); ninguna prueba depende del orden.

## 10. Grafo de dependencias

```
Fase 0:  T01(hecha) ─► T02 ─► T04 ─► T05
                  T03 ──────┘ (T03 sin bloqueantes; T04 necesita T02+T03)
Fase 1:  T06 (sin bloqueantes: muñeco)   T07 ◄─ T02,T03   T08 ◄─ T07   T09 ◄─ T07,T08 (Güemes)
Fase 2:  T10 (libre) ─► T11 ─► T12 ◄─ T07,T08 ─► T13 ─► T14 ─► T15 ─► T16 (◄ T04,T09)
Fase 3:  T17, T18, T19 ◄─ T15 (+T06)      (paralelas entre sí)
Fase 4:  T20 ◄─ T16 (opcional)   T21 ◄─ T04,T15   T22 ◄─ T21   T23 (libre)   T24 ◄─ T15,T22   T25 ◄─ T23,T24   T26 ◄─ T25   T27 ◄─ T21
Fase 5-7: T28 ◄─ T15   T29 ◄─ T02   T30 ◄─ T15 y P1 [P]
```
Tareas **sin bloqueantes** (arrancables hoy): T03, T06, T10, T23. T01 ya está hecha.

---

# Fichas de tarea

## 11. Fase 0 — Lector y marco

### T01 · Parser binario de FBX — **HECHA** (está en el parche)
- **Objetivo:** leer cualquier FBX binario v7100–v7700 a un árbol de nodos.
- **Estado:** `src/afm/fbxio.py` y `tests/test_fbxio.py` existen; `pillow` pasó a `dependencies` en `pyproject.toml`. 10 pruebas verdes; parsea el FBX real en 0,03 s [M4].
- **Verificación:** `python -I -m pytest tests/test_fbxio.py -q`.
- **Si la rehacés:** header de 23 bytes `b"Kaydara FBX Binary  \x00\x1a\x00"` + `uint32` de versión (offset 23); nodos desde el offset 27; versión ≥ 7500 usa registros de 25 bytes (`uint64`), menor de 13 (`uint32`); `end_offset == 0` cierra la lista. Propiedades `Y C I F D L`, arreglos `f d l i b` (`longitud, codificación, largo_comprimido`; 1 = zlib), `S` (UTF‑8, `errors="replace"`), `R`. Errores siempre `FbxError`.

### T02 · `TexturedMesh` y `load_textured`
- **Objetivo:** convertir el árbol del FBX en una malla triangulada con UV por esquina y las imágenes embebidas.
- **Bloqueantes:** T01. *Verificación:* `python -I -c "import afm.fbxio as f; f.parse_fbx"`.
- **Leer:** §3.1, §6 (`TexturedMesh`, convención de píxel), §7.1, `src/afm/fbxio.py`.
- **Crear:** `src/afm/texmesh.py`, `tests/test_texmesh.py`. **Ampliar:** `src/afm/fbxio.py` con `load_textured` y `polygons_from_pvi(pvi) -> list[list[int]]`.
- **Contrato:** el del §6.
- **Pasos:**
  1. `Geometry` = primer nodo `Geometry` con `props[2] == "Mesh"`. `Vertices` → `(V,3)` float64. Multiplicar por `Lcl Scaling` del `Model` conectado (`Geometry→Model` por `OO`); si no hay, 1. `Lcl Rotation` con algún valor > 1e‑3° → aviso en `info["avisos"]`, no se aplica (en el ejemplo vale −4e‑6: no avisa).
  2. `polygons_from_pvi`: recorrer `PolygonVertexIndex`; un índice negativo cierra el polígono y se recupera con `~i`.
  3. Triangular: triángulos tal cual; cuadriláteros por la diagonal más corta; n > 4 en abanico. Guardar, para cada triángulo, las posiciones de esquina (0..n‑1 del polígono) para mapear UV.
  4. UV: con `ByPolygonVertex` + `IndexToDirect`, `UVIndex[k]` da el UV de la esquina `k` del arreglo de polígonos aplanado; con `ByPolygonVertex` + `Direct`, `UV[k]`. Otras combinaciones → `FbxError`. `corner_uv` queda `(F,3,2)` float32.
  5. Imágenes: de cada `Texture` conectada a un `Material` por `OP` con la propiedad `DiffuseColor`/`NormalMap`/`ShininessExponent`/`ReflectionFactor` → `base_color/normal/roughness/metallic`; los bytes salen del `Content` del `Video` conectado a esa `Texture` (`OO`, hijo = `Video`). Sin `Content` → aviso y sin imagen. `info["n_materiales"]` = cantidad de `Material`; más de uno → aviso.
  6. `image(kind)`: decodifica con `PIL` (import perezoso) a `uint8 (H,W,3)`; las de un solo canal se replican; cachea.
  7. `sample(face, bary, kind)`: interpola `corner_uv` con las baricéntricas y toma **bilineal** con fila `= (1 − v)·alto − 0,5`, columna `= u·ancho − 0,5`, borde *clamp*.
  8. `to_trimesh()`: `trimesh.Trimesh(vertices, faces, process=False)`.
- **Pruebas (`tests/test_texmesh.py`):** `test_polygons_from_pvi_quads_and_tris` (con `[0,1,2,~3]`, `[4,5,~6]`) · `test_quad_split_uses_shortest_diagonal` · `test_real_file_counts` (5345 vértices, 10 686 triángulos, `images` con 4 claves) · `test_real_file_scaling_applied` (caja ≈ 48,9 × 116,0 × 20,3 ± 0,1) · `test_corner_uv_shape_and_range` (UV dentro de [0,1] ± 0,01) · `test_base_color_image_is_4096_rgb` · `test_sample_hits_known_texel` (con un FBX sintético o con `TexturedMesh` armada a mano: una cara cuyas esquinas caen en el centro de un texel conocido devuelve su color exacto) · `test_v_axis_convention` (textura sintética 2×2 con la mitad superior roja: `v` alto ⇒ rojo) · `test_missing_texture_warns`.
- **Verificación:** `python -I -m pytest tests/test_texmesh.py -q` y R9.
- **Aceptación:** pasan; cargar el FBX real tarda < 10 s (anotarlo).
- **No hacer:** marco de la figura (T03), ICP (T04), muestreo denso (T07).

### T03 · `FigureFrame` y matriz de `orient_z_up`
- **Objetivo:** una matriz 4×4 única archivo → marco de la figura, reconstruida a partir de lo que hace `load_figure`.
- **Bloqueantes:** ninguno.
- **Leer:** §6 (`FigureFrame`), §7.2, `src/afm/meshio.py` (`LoadReport`, `orient_z_up`, `normalize`, `load_figure`), `src/afm/pipeline.py` líneas 55–72 (`align_to_symmetry`), `src/afm/cli.py` líneas 17–30.
- **Crear:** `src/afm/frame.py`, `tests/test_frame.py`. **Tocar:** `src/afm/meshio.py`.
- **Pasos:**
  1. En `LoadReport` agregar `orient_matrix: tuple = ()` (la 3×3 aplicada por `orient_z_up`, como 9 números; vacío = identidad). Para obtenerla sin cambiar la firma pública: crear `orient_z_up_matrix(mesh) -> np.ndarray(3,3)` con la misma heurística (eje más largo) y que `orient_z_up` la use; `load_figure` la guarda en `report.orient_matrix`. **No** cambiar lo que devuelve `load_figure`.
  2. `FigureFrame.from_load(report, front, symmetry)`: `M = A · F · N`, con `N = to_figure ∘ orient` (rotación `orient_matrix`, luego `scale`, resta de `zmin_file` en z y de `center_xy` en x,y; igual a `LoadReport.to_figure`), `F` = giro de 180° sobre Z si `front == "-Y"` (`diag(-1,-1,1)`), `A` = la de `align_to_symmetry`: obtenerla aplicando `align_to_symmetry` a un tetraedro de 4 puntos y ajustando la transformación rígida (Kabsch). Sin `symmetry`, `A = I`.
  3. `apply(points)` y `det()` (determinante de la parte 3×3 dividido por `scale³`; debe dar +1).
- **Pruebas:** `test_orient_matrix_recorded_for_y_up` (malla alta en Y ⇒ matriz `[[1,0,0],[0,0,-1],[0,1,0]]`) · `test_load_figure_output_unchanged` (mismos vértices que antes para el muñeco guardado como STL) · `test_frame_matches_to_figure_for_points` · `test_frame_front_minus_y_flips_xy` · `test_frame_matches_cli_pipeline` (carga el muñeco con `front="-Y"` y `demo_project().symmetry`, aplica el marco a los vértices del archivo y compara con la malla que arma `cli.run`: distancia máx < 1e‑6) · `test_det_is_plus_one`.
- **Verificación:** `python -I -m pytest tests/test_frame.py -q` y R9.
- **Aceptación:** pasan sin tocar tests existentes.
- **No hacer:** similitud/ICP (T04); tocar `cli.py`.

### T04 · Modo B: alinear el FBX con el STL (similitud + ICP)
- **Objetivo:** `FigureFrame.from_similarity`: llevar los vértices del FBX al marco de la geometría del STL y aceptar o rechazar.
- **Bloqueantes:** T02, T03. *Verificación:* `python -I -c "import afm.frame, afm.texmesh"`.
- **Leer:** §3.2, §7.2, `src/afm/frame.py`, `src/afm/texmesh.py`.
- **Tocar:** `src/afm/frame.py`; **crear** `tests/test_frame_similarity.py`.
- **Contrato:** `from_similarity(source_vertices, target_mesh, refine=True)` devuelve un `FigureFrame` con atributo `report: dict {"media_mm","p95_mm","max_mm","ok","iteraciones"}`; `ok = media ≤ 0,1`.
- **Pasos:** (1) rotación de `orient_z_up_matrix` (T03) aplicada a la fuente; (2) escala = alto del STL / alto de la fuente y traslado que iguala las cajas; (3) si `refine`, ICP de similitud (Umeyama sobre pares de `cKDTree`, ≤ 20 iteraciones, corte si la media baja < 1e‑4); (4) medir la distancia de los vértices del STL a la superficie de la fuente (`trimesh.proximity` si hay `rtree`, si no `cKDTree` sobre muestras) y cargar `report`. Mantener det(R) = +1.
- **Pruebas:** `test_recovers_known_similarity` (muñeco, transformada conocida aplicada ⇒ error medio < 0,01 mm) · `test_rejects_different_figure` (muñeco contra una esfera ⇒ `ok=False`) · `test_real_guemes_without_icp` (`refine=False`: media ≤ 0,05, máx ≤ 0,4 mm; esperado 0,036/0,336 [M3]) · `test_real_guemes_with_icp` (media ≤ 0,03 mm; esperado 0,017) · `test_real_guemes_crown_z` (`crown_z` de la fuente tras la escala ≈ 101,769 ± 0,05 cuando el STL mide 114,0 con `crown_z = 100`) · `test_front_minus_y_confirmed` (la fuente girada 180° sobre Z da media > 1 mm: el control de orientación detecta el error).
- **Verificación:** `python -I -m pytest tests/test_frame_similarity.py -q` y R9. Las pruebas reales usan `examples/guemes_segmentado_lq_fixed_resized.stl` y el FBX.
- **Aceptación:** los números de arriba. Si el real da fuera de rango, informar el valor medido (R8).
- **No hacer:** modo A (T21).

### T05 · `tools/fbx_a_glb.py`
- **Objetivo:** convertir un FBX en un GLB con color, de pie y mirando al frente, para verlo.
- **Bloqueantes:** T02, T03. *Verificación:* `python -I -c "from afm.texmesh import TexturedMesh; from afm.frame import FigureFrame"`.
- **Leer:** §6, `src/afm/texmesh.py`, `src/afm/frame.py`.
- **Crear:** `tools/fbx_a_glb.py`, `tests/test_tool_fbx_a_glb.py`.
- **Pasos:** CLI `python tools/fbx_a_glb.py in.fbx out.glb [--front -Y]`: cargar, aplicar la matriz de `orient_z_up` y la escala a 100 mm (sin ICP), construir `trimesh.Trimesh` con `visual=trimesh.visual.TextureVisuals(uv=…, image=PIL.Image)`. **UV por vértice requiere desdoblar por esquina:** usar 3 vértices por triángulo (sin indexar) y `uv = corner_uv.reshape(-1,2)`. Exportar con `trimesh.Scene(...).export(glb)`.
- **Pruebas:** `test_glb_exists_and_loads` (recarga con `trimesh.load`; 32 058 vértices = 10 686 × 3) · `test_glb_height_and_upright` (alto ≈ 100 ± 0,5 si `--crown-z` o ≈ 100 hasta el punto más alto) · `test_glb_has_texture` (`mesh.visual.material.baseColorTexture` no es `None`).
- **Verificación:** `python -I -m pytest tests/test_tool_fbx_a_glb.py -q`.
- **Aceptación:** pasan; renderizar no es necesario.
- **No hacer:** tocar `src/afm/`.

## 12. Fase 1 — Fuente de color y registro

### T06 · Muñeco texturizado sintético
- **Objetivo:** una `TexturedMesh` del muñeco (`testfigure.make_doll`) con color distinto por región, fuente de verdad para las pruebas de las fases 1 a 3.
- **Bloqueantes:** ninguno (T02 solo si se quiere usar `TexturedMesh`; ver *Pasos*).
- **Leer:** `src/afm/testfigure.py` (`make_doll`, `ARM_X0`, `LEG_X0`), §6 (`TexturedMesh`).
- **Crear:** `tests/color_helpers.py` (parte 1), `tests/test_color_helpers.py`.
- **Pasos:** (1) `doll_textured() -> TexturedMesh` en el marco identidad: tomar `make_doll()`, y asignar a **cada triángulo** un color por región según el centroide: brazo izquierdo (x > 15) rojo `(220,30,30)`, brazo derecho (x < −15) azul `(30,30,220)`, pierna izquierda (x > 2, z < 38, x < 16) verde, pierna derecha amarilla-verde distinta, cabeza (z > 78) amarillo `(240,220,30)`, pelvis (34 ≤ z < 42) naranja `(240,130,20)`, torso (42 ≤ z ≤ 75) blanco `(245,245,245)`, resto gris. (2) Atlas de 256 × 256: un bloque de 8 × 8 px por color y UV de cada cara apuntando al centro del bloque (los UV del tri son constantes dentro de la región; sirve para probar la consulta); `corner_uv` constante por cara. (3) `REGIONS: dict[str, tuple]` exportado con los colores. Si T02 no está hecha, armar `TexturedMesh` con `dataclass` mínima local y marcar el `skip` de las pruebas que dependen de `image()`.
- **Pruebas:** `test_doll_regions_have_expected_colors` (muestreo del atlas por cara = color esperado) · `test_left_and_right_arm_differ` · `test_all_faces_have_uv_inside_atlas` · `test_doll_textured_is_deterministic`.
- **Verificación:** `python -I -m pytest tests/test_color_helpers.py -q`.
- **Aceptación:** pasan; ≤ 3 s.
- **No hacer:** piezas de la figura (T14).

### T07 · `ColorSource`
- **Objetivo:** color de la fuente para puntos 3D, con distancia y marca de origen.
- **Bloqueantes:** T02, T03. *Verificación:* `python -I -c "from afm.texmesh import TexturedMesh; from afm.frame import FigureFrame"`.
- **Leer:** §3.5, §6 (`ColorSource`, `ColorQuery`), §7.3, `src/afm/texmesh.py`.
- **Crear:** `src/afm/colorsrc.py`, `tests/test_colorsrc.py`.
- **Pasos:**
  1. Constructor: aplicar `frame` (o identidad) a `vertices`; `mesh` = trimesh en el marco. Muestras: por cara, `n = ceil(área / spacing²)` puntos con baricéntricas aleatorias (`rng = default_rng(seed)`); guardar `sample_xyz`, `sample_face`, `sample_bary` y `sample_rgb` (calculado de una vez con `TexturedMesh.sample`; **liberar** la imagen decodificada después). Tope de memoria: si `n_total > 6 M`, subir `spacing`. `cKDTree(sample_xyz)`.
  2. `query(points, normals, eps, min_dot, workers)`: `tree.query(points, workers=workers)`; `dist` = distancia al **punto de superficie más cercano estimado**: para no sobreestimar por el espaciado, refinar con la distancia exacta punto-triángulo a la cara de la muestra más cercana (`trimesh.triangles.closest_point` vectorizado). `rgb` = `sample_rgb` de la muestra más cercana. `has_origin = dist ≤ eps` y, si hay `normals`, `dot(normal_cara_fuente, normal) > min_dot`.
  3. `distance(points)`: versión gruesa usando solo una muestra cada 4 (un `cKDTree` más chico), para el registro.
- **Pruebas:** `test_reproduces_own_colors` (muñeco: puntos de superficie de la propia fuente ⇒ error medio de color ≤ 4/255 y p95 ≤ 12/255) · `test_points_far_have_no_origin` · `test_normal_filter_rejects_opposite_face` (punto a 0,05 mm de una cara con normal opuesta ⇒ `has_origin=False`) · `test_seam_points_have_no_color_jump` (con el FBX real, puntos sobre aristas de costura: desvío medio entre muestras vecinas ≤ 3/255) · `test_real_file_build_time_under_20s` · `test_workers_gives_same_result` · `test_memory_cap_raises_spacing`.
- **Verificación:** `python -I -m pytest tests/test_colorsrc.py -q` y R9.
- **Aceptación:** pasan sin cambiar los umbrales.
- **No hacer:** registro (T08); BVH exacto (opcional más adelante).

### T08 · Registro pieza → fuente (semillas simples)
- **Objetivo:** hallar la rotación de un grado de libertad que lleva una pieza en pose a la pose de reposo de la fuente.
- **Bloqueantes:** T07. *Verificación:* `python -I -c "from afm.colorsrc import ColorSource"`.
- **Leer:** §6 (`Registration`, `Seed`, `seeds_for_piece`, nombres de pieza), §7.4, `src/afm/pipeline.py` líneas 286–310 y 1330–1370 (uso de `motion`), `src/afm/params.py`.
- **Crear:** `src/afm/colorreg.py`, `tests/test_colorreg.py`.
- **Pasos:** (1) **Verificar los nombres [V]:** construir el muñeco (`P.build` como en `tests/test_head.py`, fixture `built`) y anotar `sorted(res.pieces)` y `res.motion.keys()` en `PROGRESS.md`. (2) `seeds_for_piece(name, motion, params, report=None)`: clave = nombre sin sufijo `_izq/_der`; si no está en `motion`, solo identidad. Semillas: `identidad`, y para `(limb, centro, eje, (a0,a1))`: `±params.sit_angle`, `±params.print_angle`, `±(a1)` (`a1` = tope del recorrido). Para `_der`: centro con x → −x; si el eje es ±X el ángulo no cambia, si no se invierte. (3) `register_piece`: muestrear `n_points` de la superficie de la pieza (`seed 0`), para cada semilla aplicar la rotación (Rodrigues sobre `centro`, `eje`) y puntuar `score = mean(src.distance(p) ≤ thresh_mm)`; tomar la mejor; si `< min_score`, barrido de 0–360° cada 15° sobre el eje del miembro y refinado ±7,5° en pasos de 1°; si aún `< min_score` ⇒ `status="sin_origen"`, `matrix = I`. `matrix` lleva **pieza → reposo** (la inversa de la rotación del motor). Estados: `identidad` si la mejor es la identidad, `semilla`, `barrido`.
- **Pruebas:** `test_identity_for_unrotated_piece` · `test_recovers_synthetic_rotation` (rotar el brazo del muñeco 90° sobre su eje ⇒ ángulo recuperado con error ≤ 1°) · `test_right_side_mirrors_center` · `test_unknown_piece_returns_sin_origen` (malla de una esfera lejos de la figura) · `test_sweep_finds_angle_not_in_seeds` (rotación de 37°) · `test_seeds_without_motion_entry_is_identity_only` · `test_registration_is_fast` (< 3 s por pieza).
- **Verificación:** `python -I -m pytest tests/test_colorreg.py -q` y R9.
- **Aceptación:** pasan; los nombres de pieza reales quedan en `PROGRESS.md`.
- **No hacer:** cadenas de giros (T18); tocar `pipeline.py`.

### T09 · Registro con Güemes real (verificación de cobertura)
- **Objetivo:** reproducir con código del repo la tabla del §3.3.
- **Bloqueantes:** T07, T08, T04. *Verificación:* `python -I -c "from afm.colorreg import register_piece; from afm.frame import FigureFrame"`.
- **Leer:** §3.3, `examples/guemes.py`, `src/afm/cli.py` (`run`, parámetro `keep`).
- **Crear:** `tests/test_color_guemes_registration.py` (marca `@pytest.mark.slow` si el repo define `slow`; si no, `skipif` por variable de entorno `AFM_SLOW=1`).
- **Pasos:** generar Güemes una vez (fixture de módulo, ≈ 26 s) con `run(..., keep={})` en un directorio temporal; construir `ColorSource` del FBX con el `FigureFrame.from_similarity` contra el STL; registrar las 5 piezas desde `res.pieces` (convertidas con `S.to_printable_trimesh`).
- **Pruebas:** `test_torso_and_arms_identity` · `test_legs_rotation_is_minus_90_about_x` (± 2°) · `test_coverage_per_piece` (torso ≥ 70 %, brazos y piernas ≥ 80 %, con `d ≤ 0,6 mm` como en [M]) · `test_unregistered_leg_coverage_is_low` (< 15 %).
- **Verificación:** `AFM_SLOW=1 python -I -m pytest tests/test_color_guemes_registration.py -q`.
- **Aceptación:** los números del §3.3; si no, informar (R8).
- **No hacer:** hornear.

## 13. Fase 2 — Horneado, exportación y motor (modo B, sin GUI)

### T10 · Despliegue base «una celda por triángulo»
- **Objetivo:** dar a cada triángulo de una pieza un lugar propio en un atlas, sin solapes por construcción.
- **Bloqueantes:** ninguno (solo necesita una malla `trimesh`).
- **Leer:** §6 (`Unwrapped`), §7.5, §3.1 (convención UV). `trimesh.remesh.subdivide_to_size` (firma: `(vertices, faces, max_edge, max_iter=10, return_index=False)`).
- **Crear:** `src/afm/unwrap.py`, `tests/test_unwrap.py`.
- **Contrato:** §6 (`Unwrapped`, `unwrap_triangles`) y `check_no_overlap(unw) -> int` (píxeles cubiertos por más de una celda; 0 = bien).
- **Pasos:**
  1. Si `max_edge_mm is None`, usar `0,9 · max_cell_px · texel_mm`. Subdividir con `subdivide_to_size(..., return_index=True)` (el índice da `src_face`; sin subdivisión, `arange`). La geometría es idéntica a la de la pieza.
  2. Por triángulo, aplanar **isométricamente**: `v0` en el origen, `v1` sobre +x a distancia `|e01|`, `v2` con `x = dot(e02, e01)/|e01|` e `y = 2·área/|e01|`; la celda es la caja del triángulo plano, en píxeles: `ceil(lado_mm / texel) · s + 2·margin_px`, con `s = min(1, max_cell_px / lado_mayor_px)`.
  3. Empaquetar por **estantes**: celdas ordenadas por alto decreciente; ancho del atlas = potencia de 2 ≥ `sqrt(Σ áreas / 0,7)`, tope `max_atlas`; alto = múltiplo de 4. Si no entra, multiplicar `texel_mm` por `1,15 · sqrt(área_usada / área_máx)` y reintentar (hasta 6 veces; luego `ValueError`). Guardar el `texel_mm` efectivo.
  4. Coordenadas continuas de píxel (x derecha, y abajo; centro del píxel `(fila r, columna c)` = `(c + 0,5, r + 0,5)`) en `tri_px`; `uv = (x / ancho, 1 − y / alto)`.
  5. `check_no_overlap`: pintar el rectángulo de cada celda en un `uint8` y contar > 1.
- **Pruebas:** `test_cells_never_overlap` (esfera de 3000 caras y una malla con triángulos muy desparejos) · `test_uv_in_unit_square_and_inside_atlas` · `test_isometric_area_preserved` (con `s = 1`, Σ área px² × texel² = área 3D ± 1 %) · `test_big_triangle_is_subdivided_and_mapped_back` (cuadrado de 40 mm: `src_face` válido, área total igual) · `test_oversized_piece_grows_texel` (`max_atlas=256` ⇒ `texel_mm` efectivo mayor que el pedido) · `test_deterministic` · `test_torso_like_mesh_under_10_seconds` (22 000 triángulos).
- **Verificación:** `python -I -m pytest tests/test_unwrap.py -q` y R9.
- **Aceptación:** 0 solapes; torso de 22 000 triángulos a 0,2 mm cabe en ≤ 4096² y se despliega en < 10 s (anotar el tamaño real en `PROGRESS.md`: es la medida que decide T20).
- **No hacer:** cartas, proyecciones, `xatlas`.

### T11 · Rasterizado: de texel a punto 3D
- **Objetivo:** para un `Unwrapped`, listar los texeles cubiertos con su cara, baricéntricas, punto 3D y normal.
- **Bloqueantes:** T10. *Verificación:* `python -I -c "from afm.unwrap import unwrap_triangles, Unwrapped"`.
- **Leer:** §6 (`TexelGrid`), §7.5, `src/afm/unwrap.py`.
- **Crear:** `src/afm/colorbake.py` (solo `texel_points` y `TexelGrid`), `tests/test_colorbake_grid.py`.
- **Pasos:** (1) por celda, los centros de píxel de su caja (vectorizar con `np.repeat`/`arange`, sin bucle por píxel); (2) baricéntricas contra `tri_px`, conservar `min(bary) ≥ −1e‑6`; (3) si un triángulo queda sin ningún texel, agregar el píxel que contiene su baricentro; (4) `points = Σ bary·vértices`; `normals` = normal plana de la cara.
- **Pruebas:** `test_every_face_has_at_least_one_texel` · `test_points_lie_on_the_mesh` (distancia a la malla ≤ 1e‑6) · `test_bary_sum_is_one_and_nonneg` · `test_texel_count_close_to_area_over_texel2` (± 25 %) · `test_no_python_loop_per_pixel_speed` (22 000 caras < 5 s) · `test_rows_cols_inside_atlas`.
- **Verificación:** `python -I -m pytest tests/test_colorbake_grid.py -q` y R9.
- **Aceptación:** pasan.
- **No hacer:** consultar color (T12).

### T12 · Horneado: consulta de color por texel
- **Objetivo:** color de cada texel a partir de la fuente y del registro.
- **Bloqueantes:** T07, T08, T11. *Verificación:* `python -I -c "from afm.colorbake import texel_points; from afm.colorreg import register_piece; from afm.colorsrc import ColorSource"`.
- **Leer:** §6 (`bake_piece`, `BakeResult`), §7.3, §7.6, `src/afm/colorbake.py`.
- **Tocar:** `src/afm/colorbake.py` (agregar `bake_piece` sin dilatación ni políticas finas); **crear** `tests/test_colorbake_query.py`.
- **Pasos:** (1) `grid = texel_points(unw)`; (2) llevar `points` y `normals` a la pose de reposo con `reg.matrix` (rotar normales sin trasladar); (3) con `symmetric=True`, para piezas con `x_medio < 0` reflejar x antes de consultar; (4) `src.query(...)` en bloques de 200 000 puntos; (5) llenar el atlas `uint8` con el color de los texeles con origen, `mask` = texeles consultados; los sin origen se resuelven en T13 (aquí solo `extend`: color de la muestra más cercana sin importar `d`); (6) `stats`: `pct_origen`, `n_texeles`, `area_mm2`, `segundos`.
- **Pruebas:** `test_arm_piece_colors_match_region` (muñeco: brazo izquierdo mayormente rojo, derecho mayormente azul) · `test_rotated_piece_uses_registration` (la pierna girada 90° recupera el verde) · `test_symmetric_option_copies_left_color_to_right` · `test_stats_pct_origen_in_0_100` · `test_chunking_gives_same_result` · `test_bake_time_scales` (muñeco, 0,4 mm: < 15 s).
- **Verificación:** `python -I -m pytest tests/test_colorbake_query.py -q` y R9.
- **Aceptación:** pasan.
- **No hacer:** dilatación y color plano (T13).

### T13 · Dilatación y políticas de superficies sin origen
- **Objetivo:** cerrar el atlas: rellenar bordes de isla y aplicar `extend` / color plano.
- **Bloqueantes:** T12.
- **Leer:** §7.5 (4), §7.7, RG12 del §17, `src/afm/colorbake.py`.
- **Tocar:** `src/afm/colorbake.py`; **crear** `tests/test_colorbake_fill.py`.
- **Pasos:** (1) `dilate(atlas, mask, px)`: copiar iterativamente el color del vecino cubierto (`scipy.ndimage.distance_transform_edt(~mask, return_indices=True)`), limitado a `dilate_px`; (2) política `fill="extend"` (usa el color del vecino más cercano de la fuente sin `eps`) o `fill="#RRGGBB"` (los texeles sin origen quedan de ese color); (3) contraer el muestreo 1–2 texeles hacia el interior de la cara para evitar el borde borroso (RG12): si el texel cae a < 1 texel de la arista, sustituir la baricéntrica por la del punto movido hacia el baricentro; (4) `BakeResult.mask` final = islas + dilatación.
- **Pruebas:** `test_dilation_fills_up_to_px` · `test_dilation_never_overwrites_covered_texels` · `test_fill_hex_color_applies_to_no_origin_only` · `test_extend_policy_has_no_gray` · `test_invalid_fill_raises` · `test_edge_shrink_changes_edge_texels_only`.
- **Verificación:** `python -I -m pytest tests/test_colorbake_fill.py -q` y R9.
- **Aceptación:** pasan.
- **No hacer:** exportar archivos.

### T14 · `Colorizer` y escritura OBJ + MTL + PNG + GLB
- **Objetivo:** el objeto que recibe `export(colorize=...)` y escribe los archivos de color.
- **Bloqueantes:** T10, T13. *Verificación:* `python -I -c "from afm.colorbake import bake_piece; from afm.unwrap import unwrap_triangles"`.
- **Leer:** §6 (`Colorizer`), §8 (salidas), `src/afm/pipeline.py` (`export`, líneas 1614–1670), `tests/test_head.py` (fixtures).
- **Crear:** `src/afm/colorexport.py`, `tests/color_helpers.py` (parte 2: `doll_pieces()` y `piece_trimeshes()`), `tests/test_colorexport.py`.
- **Pasos:** (1) `doll_pieces()` construye el muñeco con `P.build(body, pr.limbs, params, pr.symmetry)` como `test_head.py` y devuelve `res`; `piece_trimeshes(res)` → `{nombre: S.to_printable_trimesh(mf)}`. (2) `Colorizer.__call__(name, mesh, motion, report)`: `register_piece` → `unwrap_triangles` → `bake_piece`; guarda `(mesh_subdividida, Unwrapped, BakeResult, Registration)` y devuelve `{"estado", "pct_origen", "angulo", "texel_mm", "atlas": [w,h], "segundos"}`. (3) `write(outdir, fig_name, orientation)`: `piezas_color/<fig>_<pieza>.obj` (`v`, `vt` desde `Unwrapped.uv`, `f v/vt`), `.mtl` (`map_Kd <fig>_<pieza>_color.png`) y el PNG (`PIL`); `<fig>_ensamblada.glb` con todas las piezas y su textura (cada una con su material); `<fig>_color_informe.json` con las `info` y los tiempos. Aplicar la matriz `orientation` (la `T` de `print_orientation`) a las mallas de salida si se la pasa. Un vértice por esquina de cara para que el `vt` sea correcto.
- **Pruebas:** `test_obj_mtl_png_written_and_consistent` (cada `vt` en [0,1]; el PNG existe y su tamaño = `atlas`) · `test_glb_loads_with_all_pieces` · `test_report_has_pct_origen_per_piece` · `test_orientation_applied_to_outputs` · `test_obj_reload_color_at_known_point` (recargar OBJ+PNG y muestrear el centro de una cara del brazo izquierdo del muñeco ⇒ rojo ± 10/255) · `test_pieces_without_registration_marked_sin_origen`.
- **Verificación:** `python -I -m pytest tests/test_colorexport.py -q` y R9.
- **Aceptación:** pasan; `tests/color_helpers.py` queda importable desde otras pruebas.
- **No hacer:** tocar `pipeline.py` (T15).

### T15 · Gancho `colorize` en `export` y flags del CLI
- **Objetivo:** conectar el color al motor sin cambiar la salida STL.
- **Bloqueantes:** T14. *Verificación:* `python -I -c "from afm.colorexport import Colorizer"`.
- **Leer:** §8, §6, `src/afm/pipeline.py` `export` (líneas 1614–1670), `src/afm/cli.py` (`run`, parser `b`), `src/afm/locales/es.json`.
- **Tocar:** `src/afm/pipeline.py` (solo `export`), `src/afm/cli.py`, `src/afm/locales/{es,en,pt}.json`; **crear** `tests/test_color_cli.py`.
- **Pasos:** (1) `export(..., colorize=None)`: dentro del bucle de piezas, **antes** de `apply_transform(T)`, `if colorize: colorize(part, tm, res.motion, res.report)` (pasa una copia de `tm`); al final, `if colorize: info["color"] = colorize.write(outdir, name, T)`. Sin `colorize` el comportamiento y los bytes no cambian. (2) CLI: flags `--color-desde FBX`, `--sin-color`, `--textura-mm`, `--color-simetrico`, `--color-relleno`, `--color-tolerancia` en el parser `b`; en `run()`, si hay `--color-desde`, cargar `TexturedMesh`, `FigureFrame.from_similarity` contra la malla del motor (marco de la figura), si no `ok` abortar con aviso; crear `ColorSource` y `Colorizer` y pasarlos a `export`. (3) i18n en los tres idiomas para cada `help`.
- **Pruebas:** `test_export_without_colorize_is_byte_identical` (muñeco: dos exportaciones, una con el código antes y otra después, comparar hashes de los STL; guardar el hash esperado en el test) · `test_export_with_colorize_leaves_stl_untouched` · `test_cli_flags_parse` · `test_cli_color_desde_rejects_different_figure` (FBX del muñeco de otra figura ⇒ aviso y salida sin color) · `test_sin_color_flag_disables`.
- **Verificación:** `python -I -m pytest tests/test_color_cli.py -q` y R9.
- **Aceptación:** STL idénticos byte a byte con y sin color.
- **No hacer:** modo A (T21); GUI.

### T16 · Integración con Güemes y medida de calidad del despliegue base
- **Objetivo:** correr Güemes con color de punta a punta, medir y decidir T20.
- **Bloqueantes:** T04, T09, T15.
- **Leer:** `examples/guemes.py`, §12 (fase 2: 60 s, 0 solapes), `src/afm/cli.py`.
- **Crear:** `tests/test_color_guemes_e2e.py` (misma marca `AFM_SLOW`), `tools/render_color_compare.py`.
- **Pasos:** generar Güemes con color (texel 0,2 mm); medir tiempo adicional, atlas por pieza, `pct_origen`. Colores en puntos conocidos (charretera dorada, puño verde, pelo negro, guerrera blanca) comparados contra la propia fuente en el mismo punto. `render_color_compare.py`: 3 vistas (frente, lado, espalda) de la fuente y del GLB ensamblado, lado a lado, PNG.
- **Pruebas:** `test_guemes_color_time_under_60s` · `test_guemes_no_overlapping_texels` · `test_guemes_stl_identical_to_run_without_color` · `test_guemes_color_at_known_points` (distancia de color ≤ 40/255 en 5 puntos que la prueba elige por coordenada en el marco de la figura; documentar las coordenadas) · `test_guemes_atlas_sizes_recorded`.
- **Verificación:** `AFM_SLOW=1 python -I -m pytest tests/test_color_guemes_e2e.py -q`.
- **Aceptación:** los criterios de §12 fase 2. **Decisión para T20:** si el torso supera 4096² o el atlas ocupa < 35 % útil, anotar «hacer T20» en `PROGRESS.md`; si no, «no hace falta T20».
- **No hacer:** cambiar el despliegue.

## 14. Fase 3 — Casos completos (sobre el muñeco texturizado)

Las tres tareas parten de `tests/color_helpers.py` (T14: `doll_pieces()`, `piece_trimeshes()`), usan el muñeco texturizado de T06 como fuente (marco identidad) y el `Colorizer` de T14 / `export(colorize=…)` de T15. Se pueden hacer en paralelo. Hechos del motor verificados en `pipeline.py`: `res.motion` tiene una entrada por **miembro** y una por `HEAD_NAME` (`"cabeza"`, giro 0–90°); **no** hay entrada para la cintura.

### T17 · Cabeza y cintura
- **Objetivo:** que la cabeza (giro libre sobre el cuello) y las dos mitades del torso con cintura (`pecho` = `torso`, y `pelvis`) salgan con su color.
- **Bloqueantes:** T15. *Verificación:* `python -I -c "from afm.pipeline import export; import inspect; assert 'colorize' in inspect.signature(export).parameters"`.
- **Leer:** `tests/test_head.py` (fixtures `body`, `built`, `_neck`), `tests/test_waist.py` líneas 1–40 (`_waist`, `WAIST_Z = 55`), `src/afm/colorreg.py` (`seeds_for_piece`).
- **Tocar:** `src/afm/colorreg.py` solo si hace falta; **crear** `tests/test_color_head_waist.py`.
- **Pasos:** (1) cabeza: construir como `tests/test_head.py` (`P.build(body, [], params, head=_neck())`), colorizar y comprobar estado de registro (`"identidad"` o `"semilla"` con ángulo ≈ 0 o el de la pose impresa de la cabeza), color dominante amarillo (T06); (2) cintura: `P.build(doll, pr.limbs, params, pr.symmetry, head=pr.head, waist=_waist())`; `torso` blanco, `pelvis` con naranja **y** blanco; (3) si una pieza no registra, ampliar `seeds_for_piece` (p. ej. semillas de giro de la cintura sobre el eje Z del `WaistSpec`) y justificarlo en `PROGRESS.md`.
- **Pruebas:** `test_head_piece_is_yellow` · `test_head_registration_status` · `test_waist_chest_is_white` · `test_waist_pelvis_has_orange_and_white` · `test_waist_surfaces_without_source_are_reported` (el corte de la cintura aparece en `pct_origen < 100`).
- **Verificación:** `python -I -m pytest tests/test_color_head_waist.py -q` y R9.
- **Aceptación:** pasan sin relajar umbrales (R8).
- **No hacer:** cambiar la construcción de cabeza ni cintura.

### T18 · Rodillas, codos y clic (cadenas de giros)
- **Objetivo:** registrar piezas que pasan por **dos** giros (cadera + rodilla) y brazos con clic.
- **Bloqueantes:** T15.
- **Leer:** `tests/test_flat_print.py` líneas 126–150 (fixture `knee_doll`: `JointSpec(name="rodilla", limb="pierna", kind="knee", plane=Plane.from_normal((0,0,14),(0,0,1)), axis_center=(9,0,17.4))`), `tests/test_detent.py` líneas 185–200 (fixture `doll_arm` con `shoulder_detent=True`), `src/afm/pipeline.py` líneas 308–330 (cómo se giran `prox`/`dist`: `jb.rotate_distal(print_flex)` y luego el giro `th` sobre el pasador).
- **Tocar:** `src/afm/colorreg.py` (aceptar **semillas encadenadas**); **crear** `tests/test_color_joints.py`.
- **Contrato:** una semilla puede ser `(centro, eje, ángulo)` o una **lista** de esos pasos aplicados en orden. `seeds_for_piece(piece_name, motion, params, report=None)` recibe además `res.report` y, para cada pieza de articulación, genera la cadena inversa de la pose impresa: primero deshacer el giro de la cadera/hombro (`print_hip` sobre el centro y eje del miembro), luego deshacer la flexión (`print_flex` sobre `center`/`axis` de la articulación), probando las 4 combinaciones de signo. Las piezas proximales llevan solo el primer paso.
- **Pasos:** (1) extender `register_piece` para aceptar listas de pasos (composición de matrices 4×4); (2) generar la cadena desde `res.report["joints"][nombre]`; (3) si ninguna cadena supera 40 %, barrido en **dos ángulos** (RG10): 24 × 24 pasos de 15° en los dos ejes, refinado local ±7,5°; (4) pieza proximal y distal de rodilla/codo coloreadas con su región.
- **Pruebas:** `test_chain_seed_recovers_knee_leg` (pierna distal con rodilla a 85° + cadera a 90° ⇒ `status in {"semilla","barrido"}` y `pct_origen ≥ 80`) · `test_proximal_piece_uses_single_step` · `test_arm_with_detent_registers` · `test_two_angle_sweep_fallback` (rotación sintética de dos ángulos fuera de las semillas) · `test_chain_signs_all_four_tried` · `test_register_is_still_fast` (< 6 s por pieza).
- **Verificación:** `python -I -m pytest tests/test_color_joints.py -q` y R9.
- **Aceptación:** pasan; si el barrido en dos ángulos no alcanza, informar el plan B del RG10 del §17 (registrar giros en `BuildResult`) sin implementarlo.
- **No hacer:** modificar `pipeline.py`.

### T19 · `--sin-soportes`, asimetría y piezas de catálogo
- **Objetivo:** color correcto con base aplanada, con `limbs_right` y con piezas de catálogo sin origen.
- **Bloqueantes:** T15.
- **Leer:** `tests/test_flat_print.py`, `tests/test_asymmetric.py` [V: existe según `PROGRESS.md`], `src/afm/catalog.py` y `tests/test_catalog.py` (cómo se importan piezas), `src/afm/pipeline.py` `flat_for_print` y `build(..., limbs_right=)`, §7.6, §7.8.
- **Tocar:** `src/afm/colorreg.py` si hace falta; **crear** `tests/test_color_cases.py`.
- **Pasos:** (1) `--sin-soportes`: `export(..., flat=…)` aplana la base y gira brazos antes de escribir; confirmar que el `colorize` se llama con la malla **antes** de `T` pero **después** de `flat_for_print`, y que el registro (que usa semillas de `motion`) sigue hallando la pose; si el aplanado recorta la fuente, el `pct_origen` baja y debe informarse; (2) `limbs_right`: la pieza derecha toma el color del lado derecho; con `symmetric=True` copia el izquierdo; (3) catálogo: una pieza importada del catálogo (p. ej. una mano estándar de `catalogo/`) sale `sin_origen` con color plano `#C8C8C8` y el informe lo marca.
- **Pruebas:** `test_flat_print_pieces_still_colored` · `test_flat_print_reports_lower_origin_where_cut` · `test_asymmetric_right_arm_keeps_right_color` (muñeco con brazo derecho más corto: sigue azul) · `test_symmetric_option_overrides_asymmetry` · `test_catalog_piece_is_sin_origen_and_gray` · `test_report_lists_sin_origen_pieces`.
- **Verificación:** `python -I -m pytest tests/test_color_cases.py -q` y R9.
- **Aceptación:** pasan.
- **No hacer:** implementar color propio de catálogo (§7.8).

## 15. Fase 4 — Despliegue por cartas (opcional), modo A, proyecto y GUI

### T20 · Despliegue por cartas (opcional)
- **Objetivo:** mejorar el aprovechamiento del atlas respecto de T10.
- **Bloqueantes:** T16 y su decisión «hacer T20» en `PROGRESS.md`.
- **Leer:** §7.5, `src/afm/unwrap.py`, medidas de T16.
- **Tocar:** `src/afm/unwrap.py`; **crear** `tests/test_unwrap_charts.py`.
- **Contrato:** `unwrap_charts(mesh, texel_mm=0.2, normal_deg=50.0, margin_px=2, max_atlas=4096) -> Unwrapped` (mismo contrato).
- **Pasos:** cartas por crecimiento por adyacencia con umbral de normal; proyección plana de cada carta (PCA: normal media); detección de solapes dentro de la carta (polígonos con `shapely`) y subdivisión de las plegadas; empaquetado por estantes con margen. `check_no_overlap` de T10 sigue valiendo (en versión por píxel); `xatlas` solo opcional y en subproceso.
- **Pruebas:** `test_charts_no_overlap` · `test_charts_atlas_smaller_than_per_triangle` (torso: ≥ 30 % menos píxeles) · `test_charts_area_preserved_within_15pct` (distorsión) · `test_charts_same_interface_as_unwrap_triangles` · `test_charts_deterministic`.
- **Verificación:** `python -I -m pytest tests/test_unwrap_charts.py -q` y R9.
- **Aceptación:** los números anteriores; si no mejora, informar y no activar.
- **No hacer:** hacerla la opción por defecto (flag `--despliegue cartas`).

### T21 · Modo A: FBX como entrada única
- **Objetivo:** `afm build guemes.fbx proyecto.json` genera la figura y el color.
- **Bloqueantes:** T04, T15.
- **Leer:** §7.2, `src/afm/meshio.py` (`load_figure`), `src/afm/cli.py` (`run`), `src/afm/frame.py`.
- **Tocar:** `src/afm/meshio.py`, `src/afm/cli.py`; **crear** `tests/test_mode_a.py`.
- **Pasos:** (1) `load_figure` despacha `.fbx` a `fbxio.load_textured` → `to_trimesh()` (geometría) → mismo flujo (`orient_z_up`, reparar, normalizar), y deja la fuente en `LoadReport.color_source` (inmutable, **sin reparar**); (2) `crown_z` del proyecto en unidades FBX tras `Lcl Scaling` (101,769 para Güemes); (3) `cli.run`: si `LoadReport.color_source` y no `--sin-color`, usar `FigureFrame.from_load(...)` para la fuente y activar el color; (4) si la reparación remalló, subir `eps` (§7.2) y avisar.
- **Pruebas:** `test_load_figure_accepts_fbx` (muñeco exportado a FBX sintético con `_write_fbx` de `test_fbxio`, o el real) · `test_mode_a_matches_mode_b_geometry` (volumen de piezas del muñeco por FBX vs. STL: ± 0,5 %) · `test_mode_a_without_texture_runs_without_color` · `test_remeshed_raises_eps` · `test_stl_flow_unchanged`.
- **Verificación:** `python -I -m pytest tests/test_mode_a.py -q` y R9.
- **Aceptación:** pasan; el flujo STL queda intacto.
- **No hacer:** GUI.

### T22 · Sesión de GUI: abrir FBX
- **Objetivo:** `gui/session.py` acepta `.fbx` y guarda la fuente.
- **Bloqueantes:** T21.
- **Leer:** `src/afm/gui/session.py` (`load`), `tests/test_gui_new_project.py` (cómo se prueba sin GTK), `src/afm/locales/es.json`.
- **Tocar:** `src/afm/gui/session.py`, `src/afm/locales/{es,en,pt}.json`; **crear** `tests/test_gui_session_fbx.py`.
- **Pasos:** `Session.load(path)`: si termina en `.fbx`, llamar al lector y guardar `self.color_source` y un `self.fbx_summary` (`triángulos, islas UV, texturas, avisos`); mostrar el resumen en el controlador (sin widgets nuevos todavía) y avisar si falta la textura base o hay más de un material.
- **Pruebas:** `test_session_loads_fbx_and_summary` · `test_session_warns_missing_base_texture` · `test_session_loads_stl_as_before` · `test_session_fbx_planes_use_figure_frame`.
- **Verificación:** `python -I -m pytest tests/test_gui_session_fbx.py -q` (sin GTK).
- **Aceptación:** pasan sin GTK instalado.
- **No hacer:** widgets (T24, T25).

### T23 · Sección `color` del proyecto
- **Objetivo:** guardar y leer la configuración de color en el `.proyecto`.
- **Bloqueantes:** ninguno.
- **Leer:** `src/afm/project.py` (`Project`, `to_dict`, `from_dict`, `_to_dict_base`, `save`, `load`; cómo se escriben `accesorios` y `limbs_right`), `tests/test_catalog_project.py` (estilo).
- **Tocar:** `src/afm/project.py`; **crear** `tests/test_project_color.py`.
- **Contrato:** `Project.color: ColorSpec | None = None`; `ColorSpec(source_path: str, source_sha256: str, eps: float = 0.15, texel_mm: float = 0.2, fill: str = "extend", symmetric: bool = False)` con `to_dict/from_dict`. Solo se escribe si existe (como `accesorios`); `version` sigue en 1.
- **Pruebas:** `test_project_without_color_roundtrip_unchanged` (mismo JSON que antes) · `test_project_with_color_roundtrip` · `test_color_not_written_when_none` · `test_sha256_helper` (`ColorSpec.from_file(path)` calcula el hash) · `test_missing_fields_use_defaults` · `test_old_project_loads`.
- **Verificación:** `python -I -m pytest tests/test_project_color.py -q` y R9.
- **Aceptación:** los proyectos existentes no cambian de contenido.
- **No hacer:** tocar `cli.py` ni la GUI.

### T24 · Visor con textura
- **Objetivo:** mostrar la fuente (o el GLB) con textura en el visor.
- **Bloqueantes:** T15, T22.
- **Leer:** `src/afm/gui/render.py` (`_flat_vertices`, `u_color`), `src/afm/gui/figure_window.py` (≈ l. 550: carga `ensamblada.stl`), `src/afm/gui/session.py`.
- **Tocar:** `src/afm/gui/render.py`, `src/afm/gui/figure_window.py`; **crear** `tests/test_gui_render_texture.py` (sin GTK: solo la parte de datos).
- **Pasos:** (1) `_flat_vertices_uv(mesh, corner_uv)` → 8 floats por vértice (xyz, normal, uv), 3 por triángulo sin indexar; (2) en el programa de sombreado, `in_uv` + `sampler2D u_tex` y `u_use_tex`; (3) `figure_window` carga el GLB si existe (`<fig>_ensamblada.glb`) y, si no, el STL; (4) planos, líneas y marcadores se siguen dibujando encima.
- **Pruebas (sin GTK):** `test_flat_vertices_uv_layout` (tamaño, orden, UV por esquina) · `test_uv_matches_corner_uv` · `test_texture_toggle_flag_in_state`.
- **Verificación:** `python -I -m pytest tests/test_gui_render_texture.py -q`; **no verificado** en pantalla (este entorno no tiene GTK): decirlo.
- **Aceptación:** pasan.
- **No hacer:** panel (T25).

### T25 · Panel «Color» e informe previo
- **Objetivo:** bloque «Color» en parámetros y informe antes de guardar.
- **Bloqueantes:** T23, T24.
- **Leer:** `src/afm/gui/` (ventana de parámetros; archivo por localizar), `src/afm/project.py` (`ColorSpec`), `src/afm/locales/es.json`.
- **Tocar:** archivos de la GUI, `src/afm/locales/{es,en,pt}.json`; **crear** `tests/test_gui_color_panel.py` (lógica del controlador).
- **Pasos:** campos texel (mm), política (extender / color plano + selector), lado (original / simétrico), ε; botón «Quitar color»; tabla de informe (pieza, % con origen, estado) tomada de `<fig>_color_informe.json`; si el FBX no está en la ruta del proyecto, diálogo para relocalizarlo (hash).
- **Pruebas:** `test_controller_color_spec_roundtrip` · `test_controller_report_rows` · `test_relocate_checks_sha256` · `test_color_off_when_removed`.
- **Verificación:** `python -I -m pytest tests/test_gui_color_panel.py -q`; GUI no verificada en pantalla.
- **Aceptación:** pasan.
- **No hacer:** i18n de otros bloques.

### T26 · i18n completo de la feature
- **Objetivo:** que todo texto de la feature exista en es/en/pt.
- **Bloqueantes:** T25.
- **Leer:** `src/afm/i18n.py`, `src/afm/locales/*.json`, `tests/` (¿hay una prueba de claves pareadas? [V] `grep -l locales tests`).
- **Tocar:** `src/afm/locales/{es,en,pt}.json`; **crear** `tests/test_color_i18n.py`.
- **Pasos:** listar con `grep` los textos nuevos (CLI, GUI, avisos de `fbxio`/`frame`/`colorreg`), asegurar las tres traducciones y que el conjunto de claves sea idéntico.
- **Pruebas:** `test_color_keys_in_three_languages` · `test_no_empty_translations` · `test_help_strings_resolve`.
- **Verificación:** `python -I -m pytest tests/test_color_i18n.py -q`.
- **Aceptación:** pasan.

### T27 · Golden del FBX
- **Objetivo:** congelar el resultado de generar Güemes desde el FBX (modo A).
- **Bloqueantes:** T21.
- **Leer:** `tools/golden.py`, `tests/test_golden.py`, `tests/golden/guemes.json`, §13 del plan de pruebas (tolerancias).
- **Tocar:** `tools/golden.py` (figura `guemes_fbx`), **crear** `tests/golden/guemes_fbx.json`.
- **Pasos:** agregar `guemes_fbx` a `FIGURES` con `guemes.py` pero entrada `.fbx`; `guardar guemes_fbx`; comparar cruzado con `guemes.json` con tolerancia de 0,5 % (el FBX difiere en 2 triángulos y hasta 0,26 mm en unos pocos vértices).
- **Pruebas:** `test_golden_guemes_fbx_matches_frozen` · `test_guemes_fbx_vs_stl_within_half_percent`.
- **Verificación:** `AFM_SLOW=1 python -I -m pytest tests/test_golden.py -q -k guemes_fbx`.
- **Aceptación:** pasan.

## 16. Fases 5–7

### T28 · Web (Pyodide)
- **Objetivo:** el lector y el horneado corren en el navegador.
- **Bloqueantes:** T15.
- **Leer:** `docs/PLAN_port_pyodide.md`, `docs/WEB_mantenimiento.md`, `src/afm/webapi.py`, `web/`.
- **Pasos:** exponer `EngineSession.load_fbx(data: bytes)`; solo la textura de color reducida a 2048²; `unwrap` propio y `cKDTree` (sin `xatlas`/`rtree`); salida del atlas a `three.js`.
- **Pruebas:** `test_websession_load_fbx_bytes` · `test_websession_bake_matches_desktop` (distancia de color media ≤ 4/255 contra `bake_piece` de escritorio con el mismo muñeco).
- **Verificación:** `python -I -m pytest tests/test_webapi_color.py -q`; paridad en navegador **no verificada** salvo que se disponga de `tools/parity_wasm_native.py`.

### T29 · Lectores GLB y OBJ+MTL
- **Objetivo:** `TexturedMesh` desde GLB y OBJ.
- **Bloqueantes:** T02; P2 [P] (basta para empezar, solo se hace si el cliente lo pide).
- **Pasos:** `load_textured` despacha por extensión; GLB/OBJ vía `trimesh.load(..., force="mesh")` con `visual.uv` (UV por vértice ⇒ `corner_uv` por indexación); `info["avisos"]` si hay más de un material.
- **Pruebas:** `test_glb_roundtrip_color` (el GLB de T05 vuelve a dar la misma fuente) · `test_obj_mtl_roundtrip` · `test_same_result_glb_and_fbx` (Güemes: distancia de color media ≤ 4/255).
- **Verificación:** `python -I -m pytest tests/test_texmesh_formats.py -q`.

### T30 · Imprimir en color (3MF / filamentos)
- **Objetivo:** salida para multicolor según P1.
- **Bloqueantes:** T15 **y respuesta del cliente a P1 [P]** (¿AMS?, ¿cuántos filamentos?). Sin P1, esta tarea **no se empieza** (R2).
- **Pasos:** cuantizar el atlas a N colores (k-means en Lab), generar 3MF con partes por color (`trimesh` no exporta 3MF con textura [I]); documentar el flujo en `docs/`.
- **Pruebas:** `test_quantize_n_colors` · `test_3mf_has_n_parts`.
- **Aceptación:** el cliente imprime una figura de prueba (fuera del alcance automático).

## 17. Riesgos (se numeran RG1… para no confundirlos con las reglas R1–R12 del §9)

| # | Riesgo | Mitigación |
|---|---|---|
| RG1 | Marco del FBX distinto del de la geometría | `FigureFrame` único; T03/T04 comparan caja, pies y cara; modo B rechaza con media > 0,1 mm |
| RG2 | UV con costuras | UV de las esquinas de la cara hallada; prueba de costuras en T07 |
| RG3 | Piezas muy juntas (caso Mickey) | Filtro de normales, ε chico; validar con Mickey más adelante |
| RG4 | `repair` remalla y se aparta de la fuente | ε sube al paso de voxel y se avisa (T21) |
| RG5 | Textura 4096² pesa ≈ 50 MB decodificada | Solo la de color; liberar tras precalcular `sample_rgb` (T07) |
| RG6 | Solapes o costuras visibles | `check_no_overlap`; T20 si T16 lo pide |
| RG7 | El slicer no importa textura | OBJ y GLB ya aportan valor; T30 según P1 |
| RG8 | FBX de otra versión o ASCII | Mensaje claro; versiones 7100–7700 |
| RG9 | Normales invertidas | Se recalculan |
| RG10 | Registro falla con cadenas de más de un giro o con `--sin-soportes` | Barrido de dos ángulos (T18); plan B invasivo: registrar giros en `BuildResult` |
| RG11 | Rendimiento en 1 núcleo | Texel 0,2 mm, `workers=-1`, caché de la fuente densa |
| RG12 | Padding borroso contamina bordes de isla | Contraer el muestreo 1–2 texeles (T13) |
| RG13 | El lector no cubre variantes | Probar con otro FBX antes de cerrar la fase 0 [P] |
| RG14 | Una tarea no cabe en un turno | Dejar `git diff` + lista de lo que falta (R11 del §9) |

## 18. Estado de implementación (qué entrega el parche que acompaña a este documento)

- **Hecho:** T01 (`src/afm/fbxio.py`, `tests/test_fbxio.py`, `pillow` en `dependencies`); este documento (`docs/PLAN_fbx_textura_color.md`).
- **No hecho:** T02–T30. Los hechos nuevos medidos para este plan están en §3.1 (mapa de nodos, conexiones, nombre `\x00\x01`).
- **No verificado:** nada de GUI; el resto de la suite del repo (solo se corrieron las 10 pruebas nuevas y se revisó que `pyproject.toml` conserve `pillow` en los extras).
