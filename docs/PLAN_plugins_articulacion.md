# Plan de factibilidad: plugins que convierten un corte en una articulación (v2)

_2026-11-25 · v2. Solo plan: no se escribió código del motor. Base: `main` **00e2ab9** de `fschpp/action_figure_maker_stl` (la v1 se apoyaba en 0d421b7; `main` avanzó 5 commits: UX de Parámetros y catálogos en línea D05–D07). Leído a mano: `pipeline.py`, `limbs.py`, `joints.py`, `detent.py`, `pins.py`, `ports.py`, `capacity.py`, `interface.py`, `guide.py`, `compat.py`, `recipe.py`, `catalog.py`, `project.py`, `params.py`, `profile.py`, `assembly.py`, `extract.py`, `paquete.py`, `instalar.py`, `probes.py`, `joint_preview.py`, `joint_edit.py`, `colorreg.py`, `equestrian.py`, `gui/controller.py`, `tools/golden.py`. Ejecutado: `tools/golden.py comparar muneco` y dos archivos de tests (Apéndice A). Referencias visuales (3): despiece «Action Force»; infografía «Anatomy of an Action Figure» de DC Collectibles; render de un brazo con disco, vástago y pasador suelto._

Marcas: **[V]** verificado en el código o ejecutando · **[I]** inferencia o estimación del analista, a validar · **[P]** pendiente de decidir con el cliente.

---

## 0. Resumen

**Veredicto: factible, y la mitad del trabajo es ordenar lo que ya existe.** El cliente describe bien el modelo mental: un corte es un plano; de un lado queda una parte, del otro la otra, y en el medio no hay nada salvo la holgura. Lo que hoy decide *qué se pone en ese medio* está escrito a mano en `pipeline.build` (1 956 líneas) con un despacho por `kind == "arm"|"leg"`, por `LimbSpec.mechanism` y por flags de `Params` (`shoulder_detent`, `hip_detent`). Hay **seis constructores** que hacen lo mismo con variantes (hongo en `pipeline`, clic en `detent.py`, bisagra en `joints.py`, cintura en `waist.py`, cuello en `head.py`, muñeca en `hand.py`) y **nueve llamadas** a `mushroom_pin` repartidas en siete módulos. [V]

**Propuesta.** Separar dos cosas:

1. **El corte** (se queda en el núcleo): plano + siluetas → pieza padre, pieza hija y una *cara de contacto* (`ContactFace` / `PortFace`) con su centro, normal, forma 2D y holgura.
2. **El tipo de articulación** (el punto de extensión, el «plugin»): recibe el corte y devuelve la pieza hija con su pasador, lo que hay que **restar** del padre y del anfitrión (cavidad con holgura), lo que hay que **sumar** al padre (lengüetas), cómo **se mueve** (para validar y para la pose de impresión) y qué **le exige** a la cara y al anfitrión (para la compatibilidad del catálogo).

**«Mejorar un corte» pasa a ser una operación con nombre** (§3.5): dado un corte ya hecho, el motor lista qué tipos de articulación entran (ok / aviso / no, con el motivo) y deja elegir uno. Un plugin que se registra aparece en esa lista sin tocar el resto.

### Qué cambió respecto de la v1

| # | Cambio | Por qué |
|---|---|---|
| 1 | Base `0d421b7` → `00e2ab9` | `main` avanzó; los catálogos en línea (`paquete.py`, `instalar.py`) obligan a decidir cómo viaja un tipo de articulación en un paquete (§3.7) |
| 2 | **Total corregido**: 6–9 semanas → **8–12 semanas** (39–59 días) | La suma de las fases de la v1 daba 34–53 días (7–11 semanas), no 6–9; y se agregó trabajo verificado (puntos 4–8) |
| 3 | La línea base dorada **ya existe**: 5 dorados en `tests/golden/` y `comparar muneco` da «idéntica a la dorada» hoy [V] | El primer paso es `comparar`, no `guardar`. No hay dorado de Azurduy (su torso no es estanco, `docs/PENDIENTES.md`) |
| 4 | El contrato tiene **dos destinos de corte** (`parent` y `host`) y **piezas sueltas** (`extras`) | La bisagra del brazo talla el torso con `jb.sweep` además del padre [V `pipeline.py` ~273–291]; el render del brazo trae un pasador aparte |
| 5 | `RevoluteMotion` es **compatible con la tupla de hoy** | `res.motion` aparece en 29 sitios, no solo en la validación (se desempaqueta como tupla de 4): `pipeline.py` (21 menciones), `colorreg.py`, `equestrian.py`, `gui/controller.py` [V] |
| 6 | `FaceRequirements` reemplaza más cosas de las que decía la v1 | `PIN_OF_CLASS` está en 5 módulos (`capacity`, `guide`, `interface`, `pin_aid`, `ports`); `capacity.MIRROR_CLASSES` y `pin_shift` tienen lógica por tipo cableada [V]. Fase 1 sube a 5–8 d |
| 7 | Los perfiles N1 viven **dentro de `Params`** (`Params.joints`) | `linea.json` guarda `Params.to_dict()`, `compat.recipe_key` lo hashea y `export_package` lo exporta: viaja a catálogo, caché de compatibilidad y paquetes sin cableado nuevo [V el flujo, I el diseño] |
| 8 | Sección nueva de **catálogos en línea** (§3.7) | Un paquete nunca trae código: declara qué tipos necesita (`tipos_articulacion`) y `instalar.plan_install` avisa |
| 9 | Secciones nuevas: oferta de tipos por corte (§3.5), kit de conformidad y ejemplo mínimo (§3.8) | Es lo que hace que «mejorar cortes vía plugins» sea algo que un tercero pueda hacer y probar |
| 10 | Las 3 imágenes mapeadas (§2) | La v1 no usaba la infografía DC (aporta *pivot* y *T-crotch*) ni el render del brazo (pasador suelto) |
| 11 | Hilo físico paralelo (§5) | `probes.py` ya genera probetas y devuelve el resultado al perfil: cada tipo nuevo trae la suya, y la impresión es lo que más calendario consume |

**Qué tan lejos queda cada tipo** (detalle en §2 y §4):

| Tipo | Esfuerzo | Por qué |
|---|---|---|
| Disc (hombro, cadera, muñeca) | **ya existe** | hongo + disco de clic (`detent.py`) |
| Hinge / rocker (codo, rodilla, tobillo) | **bajo** | bisagra de `joints.py` con otro rango; el tobillo es un lugar nuevo |
| Pivot / flat plane (bíceps, muslo, canilla) | **medio** | es el hongo girando sobre la normal del corte, pero dentro de un miembro (hoy eso solo lo hace la bisagra) |
| Ball (cintura, cadera, cabeza) y T-crotch | **alto** | giro en 3 ejes: el motor entero asume un eje de giro; requiere generalizar el movimiento |
| Varias articulaciones en un miembro (bíceps + codo + muñeca) | **alto, escondido** | `pipeline.build` rechaza hoy más de una por miembro (`pipeline.py:122`) [V] |
| Pasador cruzado suelto (render del brazo) | **depende de una decisión** | es una pieza extra: choca con «todo PLA, print-in-place, sin pieza extra» (§7, P3) |

**Recomendación.** Empezar por una Fase 0 que meta el registro de tipos y envuelva hongo, clic y bisagra **sin cambiar ni un STL** (se verifica con `tools/golden.py`, que ya tiene la línea base). Si eso sale limpio, el resto son tipos nuevos sobre una interfaz probada. No publicar la API de plugins de terceros hasta tener dos tipos nuevos funcionando.

---

## 1. Cómo está hoy (verificado en 00e2ab9)

### 1.1 Qué es un «corte» [V]

```
                 plano (3 puntos)  ── Plane (planes.py)
                        │
   ┌────────────┐       │       ┌────────────┐
   │   PADRE    │  ←gap→│←gap→  │    HIJA    │
   │ (torso /   │       │       │ (brazo /   │
   │  proximal) │       │       │  pierna)   │
   └────────────┘       │       └────────────┘
                        │
        En el medio no hay nada. Lo que se pone ahí es la articulación:
        pasador + cavidad barrida con holgura (`clearance` = 0,5 mm)
```

- Un miembro se define con `LimbSpec` (plano de silueta + polígono + extrusión; 2.º y 3.er corte opcionales `cross`, `cross2`). `separate_limb` lo saca del torso y devuelve `(limb, ContactFace)`.
- `ContactFace` (centro, normal `outward`, área, triángulos) es **la única interfaz** entre el corte y la articulación. Los puertos del catálogo (`ports.PortFace`, `PortFrame`) son la misma idea serializada.
- Una segunda articulación *dentro* de un miembro (rodilla, codo) es otro plano: `JointSpec` (`joints.py`). `build_joint` recibe el miembro **entero** y lo parte con `S.halfspace(plano)` en proximal y distal.

### 1.2 Las articulaciones que existen [V]

| Hoy | Dónde | Eje de giro respecto del plano | Qué construye |
|---|---|---|---|
| Hongo (hombro, cadera) | `pipeline.build` (inline), `pins.mushroom_pin` | **normal** al plano (`_rotation_axis(face)` = `face.outward`) | vástago + cabeza en la hija; cavidad = `dilate(hija + pasador)` barrida por ángulos, restada del torso |
| Clic (hombro, cadera) | `detent.py` (441 líneas; `build_detent_limb`) | normal | lo anterior + disco con muescas + 2 lengüetas en el padre |
| Bisagra (rodilla, codo) | `joints.py` (301 líneas; `build_joint`) | **dentro** del plano: rechaza `|n·eje| > 0,5` (línea 230) | parte el miembro en proximal/distal; lengüeta en ranura y bastago pasante; su barrido talla el padre y, en brazos, el torso |
| Cintura | `waist.py` (237) | normal | hongo robusto, parte el torso |
| Cuello | `head.py` (618) | normal | hongo del cuello; su movimiento (0–90°) entra en `res.motion` |
| Muñeca | `hand.py` (510) | normal | pasador fino `hand_pin`, `fit_hand_pin` lo achica hasta que entra |

Es decir: **dos familias geométricas** (eje ⟂ al plano, eje ∥ al plano) con muchas variantes, todas de **un solo eje de giro**.

### 1.3 Dónde está cableado el tipo [V]

- `LimbSpec.mechanism`: `None | "mushroom" | "detent"` (13 usos fuera de la GUI). Es la semilla de un registro y ya viaja en el catálogo (`extract.py:297` → `meta.mecanismo = {"mechanism": …}`; `assembly.py:369` lo lee).
- `Params`: `shoulder_pin`, `hip_pin`, `neck_pin`, `waist_pin`, `hand_pin`, `hinge_pin`, `detent`, `shoulder_detent`, `hip_detent` (un campo por tipo y lugar). `Params.from_dict` reconstruye cada uno a mano y termina en `cls(**d)`: **es estricto**, un campo desconocido lanza `TypeError`.
- `joints.KINDS = ("knee", "elbow")`, `JOINT_KIND_BY_LIMB`, `DEFAULT_NAMES`: tipos y nombres de pieza fijos.
- `ports.PORT_CLASSES` (7 clases) y `ports.PIN_OF_CLASS` (solo cuello, hombro, cadera, cintura): este último lo importan **5 módulos** (`capacity`, `guide`, `interface`, `pin_aid`, `ports`). `ports._pin_for` lo usa `port_compat`.
- `capacity.py`: `demand` devuelve `None` para clases sin pasador; `MIRROR_CLASSES = ("cadera",)` y `pin_shift` (que mira `params.hip_detent`) tienen lógica de tipo adentro. `Catalog` mide y guarda la `capacidad` del puerto solo si `pin_of(clase, params)` existe (`catalog.py` ~859–869): **la capacidad guardada depende del pasador de la línea**.
- `pipeline.build`: `by_limb` con `ValueError("por ahora una sola articulación por miembro")` (líneas 119–125 y 201–206); `res.motion[name] = (limb, centro, eje, (a0, a1))`; `_validate_hip_pin` (820), `_validate_detent` (849), `posed_pieces` (925), `_validate_chain` (960), `_validate_hand` (1004), `_validate_waist` (1068), `_validate_joints` (1350).
- `kind ==` / `.kind` y afines: 242 líneas en el código (135 en 24 módulos sin la GUI; 78 con los literales `arm|leg|knee|elbow`). Método en el Apéndice A; la v1 daba 143 y 177 con otra búsqueda.
- Vista previa y edición de la GUI llaman `build_joint` por su cuenta (`joint_preview.preview_joint`, `joint_edit`): también tienen que pasar por el registro.

### 1.4 Lo que ya existe y conviene reutilizar [V]

| Pieza | Para qué sirve acá |
|---|---|
| `probes.py`, `probe_files`, `profile.apply_probe_cell` | Probetas de hongo, bisagra y clic con celdas numeradas, 3MF y `probetas.json`; el resultado vuelve al perfil. Cada tipo nuevo trae su grilla |
| `catalog_ops.COMMANDS` + su test | Precedente de **registro con test de coherencia** (subcomando ↔ ventana). Ese test **falla hoy** en `main` (`retoque` y `guardar-montura` sin registrar): sirve de aviso de lo que pasa si el registro de tipos no tiene su test en CI |
| `capacity.measure`, `ports.port_compat`, `guide.py` | La compatibilidad **barata** y la capacidad del anfitrión: base de `fits()` (§3.5) |
| `compat.recipe_key` | Clave de la caché de compatibilidad completa: hash de piezas + `Params` + opciones + ajustes + versión del motor |
| `paquete.py` / `instalar.py` / `ziplector` | Manifiesto `afm-paquete-1` con `motor`, `requiere`, `conflicts`; instalación atómica; lector de zip endurecido. El esquema admite claves nuevas (evolución aditiva) |
| `JointSpec.transformed`, `PieceMeta.articulaciones`, `meta.mecanismo` | Una pieza de catálogo ya lleva sus articulaciones y su mecanismo, y se transforman al colocarla |
| `web/` + `docs/PLAN_port_pyodide.md` | El port a Pyodide ya empezó: el motor viaja como wheel puro Python instalado con `micropip` (D4) |

---

## 2. Las imágenes contra el motor

### 2.1 Despiece «Action Force»

| Etiqueta | Qué es mecánicamente | Contra el motor |
|---|---|---|
| **Disc** shoulder / wrist | Disco que gira sobre el eje normal al corte | **Ya existe**: hongo (fricción) o clic (`detent`, `n_notches`). Un disco sin muescas es el hongo |
| **Flat plane** bicep / thigh / shin | Corte plano transversal al miembro; las dos mitades giran una contra otra sobre el eje del miembro (giro de «torsión») | Mismo mecanismo que el hongo del hombro (eje = normal), pero **dentro** de un miembro. Hoy solo `JointSpec` parte miembros y solo sabe hacer bisagra. Se resuelve dándole a `JointSpec` un `type`. Es en el DC el *pivot* (§2.2) |
| **Double pin** elbow / knee | **[I]** dos pivotes en serie (dos ejes paralelos) para ~150° de flexión; no se ve en la imagen si los dos discos giran juntos | Una bisagra cubre un pivote. El doble pivote es una cadena de dos bisagras → exige varias articulaciones por miembro (Fase 3). **[P]** confirmar qué rango busca |
| **Disc rocker** ankle, **rocker** torso | Disco que se balancea en un solo eje con rango corto y tope | Bisagra con rango ±30° y un lugar nuevo, `tobillo`. Hoy el pie no es un corte |
| **Ball joint** waist / hips, **ball disc** head | Rótula (3 ejes); la cabeza combina rótula + disco | **No existe.** La esfera es invariante a rotaciones, así que la cavidad se resuelve con `dilate(esfera)` + cono del cuello (`detent.revolved_envelope` hace una envolvente de revolución), pero la **validación y la pose** asumen un eje: hay que generalizar `Motion` (§3.3). La cabeza ball-disc es una pila de dos tipos en el mismo corte (Fase 6) |
| **Butterfly / rocker torso** | Torso en piezas con encastre | Fuera de alcance: es una forma de cortar el torso, no una articulación (§8) |

El ícono «= customizable» aparece junto a cabeza, cintura, muñeca y canilla (las que alcanzo a ver). Sugiere que esa marca es **parametrizable por el usuario**, lo que apoya el nivel N1 de §3.1 (perfiles sin código).

### 2.2 Infografía DC Collectibles 101

Define cuatro tipos con un ícono de corte cada uno, y marca con flechas circulares los giros sobre el eje del miembro (cuello, cintura, brazo, muñeca, muslo, tobillo).

| DC | Qué es | Contra el motor |
|---|---|---|
| **B** Ball joint | Esfera en zócalo | = `ball` (§4.3) |
| **P** Pivot joint | Espiga cilíndrica en un taladro: giro sobre el eje del miembro | = *flat plane* de Action Force → `swivel` (§4.1). **La v1 no lo mapeaba como tipo propio** |
| **H** Hinge joint | Lengüeta en ranura, un eje transversal | **Ya existe** (`joints.py`) |
| **T** T-crotch | Pelvis con una barra en T: la pierna balancea adelante/atrás y se abre hacia el costado | **[I]** una rótula con cono asimétrico (mucho swing, poca abertura). Se resuelve como variante de `ball` con `ConeMotion` (§4.3); no antes de tener `ball` |

### 2.3 Render del brazo (disco + vástago + pasador amarillo)

Lo que **se ve**: un disco verde con un agujero chico en la cara; un vástago corto con una cabeza ensanchada en la punta; un cilindro amarillo suelto; un brazo rojo con un taladro redondo en un costado de la parte superior y una ranura en la cima.

- **[I]** El disco con vástago y cabeza ensanchada es exactamente lo que el motor ya genera: disco de clic (`detent.disc_polygon`) + hongo (`mushroom_pin`). Ese corte **ya está cubierto**.
- **[P]** Lo que **no se puede confirmar**: para qué sirve el pasador amarillo ni la ranura superior (¿retención cruzada del vástago, tope, pasador del codo?). Es una **pieza suelta**: choca con la decisión previa del cliente de que la figura sea print-in-place, todo PLA y sin pieza extra. Se deja como pregunta (§7, P3) y el contrato la admite como `extras` con `assembly = "post_assembly"` (§3.2), apagada por defecto.

### 2.4 Vocabulario unificado

| `id` propuesto | Se ve en | Eje vs. plano | Estado |
|---|---|---|---|
| `mushroom` | disc (AF), hongo | normal | **existe** (`LimbSpec.mechanism = "mushroom"`) |
| `detent` | disc con clic | normal | **existe** (`"detent"`) |
| `hinge` | hinge (DC), rocker, elbow/knee | dentro | **existe** (`joints.py`); *rocker* = `hinge` con rango corto (perfil, no tipo) |
| `swivel` | flat plane (AF), pivot (DC) | normal, dentro de un miembro | nuevo, Fase 2 |
| `ball` | ball joint, ball disc | 3 ejes | nuevo, Fase 4 |
| `tcrotch` | T-crotch (DC) | 2 ejes, cono asimétrico | variante de `ball`, Fase 6 |
| `double_hinge` | double pin | dos ejes ∥ en serie | cadena de dos `hinge`, Fase 6 |
| `disc_pin` | pasador cruzado suelto (render) | — | solo si el cliente acepta pieza extra (P3) |

---

## 3. Diseño propuesto

### 3.1 Tres niveles de extensión

| Nivel | Qué es | Para quién | Código |
|---|---|---|---|
| **N0. Tipos internos** | Hongo, clic, bisagra, swivel, rótula, registrados en `afm/joint_types/` | El proyecto | Python, en el repo |
| **N1. Perfiles** | Qué *tipo* y qué *parámetros* lleva cada lugar de una línea («en la línea X el hombro es `detent` con `disc_d=8`…»). Se guarda en `Params.joints` | El usuario de la línea | **Sin código** |
| **N2. Plugins** | Un paquete Python que registra un tipo nuevo | Terceros | Python, vía entry point `afm.joint_types` o carpeta de plugins |

N1 sale casi gratis una vez que existe N0 y probablemente cubre lo que el cliente llama «customizable». N2 es lo más caro y lo que más riesgo trae: se hace **último**.

### 3.2 Contrato de un tipo (boceto)

```python
# afm/joint_types/base.py  (nombres a confirmar)

@dataclass(frozen=True)
class JointSite:
    """El corte, tal como lo ve el tipo de articulación. Todo en la pose «de pie» (ángulo 0)."""
    slot: str                      # "hombro", "cadera", "rodilla", "codo", "cintura", "cuello",
                                   # "muneca", "tobillo", "biceps", "muslo", "canilla"
    side: str                      # "izq" | "der" (el motor trabaja +X y espeja con `S.mirror_x`)
    child: S.Manifold              # la hija ya cortada, con la rebaba rebajada (`burr`).
                                   # Si `plane` no es None, es el miembro ENTERO y el tipo lo parte
    plane: Plane | None            # solo en articulaciones dentro de un miembro (rodilla, codo, swivel):
                                   # el plano que parte `child` en proximal y distal (hoy lo hace `build_joint`)
    face: ContactFace              # centro, outward, área, triángulos
    parent: S.Manifold | None      # solo lectura: mide espacio (`capacity.measure`); None en la vista previa
    host: S.Manifold | None        # el torso/anfitrión, solo lectura; es el mismo que `parent` en hombro y cadera
    params: Params                 # globales: clearance, nozzle, min_wall, sweep_step, ref_height
    jp: object                     # parámetros propios del tipo (instancia de `params_cls`)
    flex: tuple | None             # rango pedido (a0, a1) en grados, o None = el del tipo
    print_hint: dict               # ángulo de impresión, arriba de impresión, sentada sí/no
    segments: int = 12             # resolución: la vista previa de la GUI baja este valor (como `build_joint(segments=…)`)

@dataclass
class JointResult:
    pieces: dict[str, S.Manifold]            # {"child": …} o {"prox": …, "dist": …}; el motor los nombra
    cutters: dict[str, list[S.Manifold]]     # se RESTAN. Destinos: "parent" y "host"
                                             # (la bisagra del brazo talla el padre y el torso: `JointBuild.sweep`)
    parent_adds: list[S.Manifold]            # se SUMAN al padre al final (lengüetas del clic)
    motion: "Motion"                         # §3.3
    report: dict                             # lo que hoy va en report["limbs"][n], ["detent"], ["joints"]
    warnings: list[str]
    extras: dict[str, S.Manifold] = field(default_factory=dict)   # piezas SUELTAS (un pasador aparte)
    assembly: str = "print_in_place"         # | "post_assembly" si `extras` no está vacío

class JointType(Protocol):
    id: str                        # "mushroom", "detent", "hinge", "swivel", "ball", …
    labels: dict[str, str]         # nombre por idioma (es/en/pt); distinto del `id` (hoy la vista previa muestra «hongo»)
    api_version: int
    params_cls: type               # dataclass: sirve para JSON, GUI y validación
    slots: frozenset[str]          # en qué lugares tiene sentido
    def requires(self, jp, params) -> "FaceRequirements": ...   # radio mínimo, profundidad, ángulo eje/normal, cabezas enfrentadas
    def build(self, site: JointSite) -> JointResult: ...
    def validate(self, result, site, bodies) -> list["Finding"]: ...   # opcional
    def probe_cells(self, params) -> list: ...                          # opcional: grilla de probeta (§5, hilo físico)
```

`FaceRequirements` reemplaza a `capacity.demand`, a `capacity.MIRROR_CLASSES` / `pin_shift` y a `ports._pin_for` / `PIN_OF_CLASS`. Mantiene la salida de hoy (`profundidad_mm`, `pared_min_mm`, `huella_radio_mm`, `separacion_min_mm`) y el `None` de las clases sin pasador, para que `capacity.check` y las `capacidad` ya guardadas en las piezas sigan leyéndose. [V sobre el estado actual; I sobre el diseño]

**Reglas que el contrato impone al plugin** (para no romper el port a Pyodide, `PLAN_port_pyodide.md`):
- Superficie pública permitida: `afm.solids` (única puerta a `manifold3d`: el único `import manifold3d` del código está en `solids.py` [V]), `afm.pins`, `afm.params` (`PinSpec`, `Params`), `afm.joint_types.*`, `numpy`, `shapely`. Nunca `import manifold3d`. [I, a confirmar]
- Determinista y sin red ni disco. No modifica sus entradas.
- Todo en mm absolutos de impresión (como `PinSpec`, `HingeSpec`, `DetentSpec`), no en unidades de figura.
- **El motor no confía en el plugin**: después de `build` revisa que cada pieza sea estanca (`mf.status()` sin error y, tras `S.to_printable_trimesh`, sin aristas de más de 2 caras), con volumen > 0 y sin NaN, y que los cortadores no se lleven más de un porcentaje del padre. Es una red de seguridad contra errores, no un aislamiento contra código malicioso (§3.4).

### 3.3 Movimiento: de «un eje» a `Motion`

Hoy `res.motion[name] = (limb, centro, eje, (a0, a1))` alimenta la validación (`_validate_chain`, `_validate_detent`, `posed_pieces`, pose sentada) **y además** a `colorreg.py` (semillas de color por pieza), `equestrian.py:278` y `gui/controller.py:2426` [V]. Una rótula no cabe en una tupla de un eje. Propuesta:

```python
class Motion(Protocol):
    def poses(self, step_deg: float) -> Iterable[np.ndarray]: ...   # 4x4 de la pieza móvil respecto de su padre
    def print_pose(self) -> np.ndarray: ...                         # pose impresa (la figura se imprime sentada)
    def clicks(self) -> list[np.ndarray]: ...                       # opcional (clic)
    def revolute(self) -> "RevoluteMotion | None": ...              # None si no es de un solo eje

class RevoluteMotion(NamedTuple):    # idéntica a la tupla de hoy: se desempaqueta igual
    limb: S.Manifold; center: np.ndarray; axis: np.ndarray; range: tuple   # (a0, a1)
class ConeMotion: ...                # rótula: cono de inclinación × giro propio, malla de poses
```

- **`RevoluteMotion` es una `NamedTuple`**, así que `_l, c, ax, (a0, a1) = res.motion[n]` y `owner.motion[key][1]` siguen funcionando. En la Fase 0 no se toca ninguno de esos 29 sitios y los dorados no pueden moverse por este lado.
- Los consumidores que solo saben de un eje (`colorreg`, clic, `equestrian`) pasan a llamar `.revolute()` y **saltean** lo que devuelva `None`. Se anota en cada uno qué pierde (p. ej. semillas de color de una cadera de rótula).
- `pipeline.py:413` (`_compact(v[0]) + tuple(v[1:])`) pasa a `motion._replace(limb=…)`.
- `guide.SeatedClash` pone la pierna a `sit_angle` girando alrededor del pasador: con una cadera de rótula esa pose sale de `Motion.print_pose()`.

### 3.4 Registro y descubrimiento

```python
# afm/joint_types/__init__.py
REGISTRY: dict[str, JointType] = {}
def register(t: JointType) -> None: ...
def get(id: str) -> JointType: ...          # ValueError claro si no existe
```

- Internos: se registran al importar el paquete.
- N2: entry point `afm.joint_types` (vía `importlib.metadata`) y, opcionalmente, carpeta `$AFM_PLUGINS` o `~/.config/afm/plugins/` (mismo estilo que `profile.profiles_dir()`, que usa `XDG_CONFIG_HOME`).
- **Coherencia**: un test recorre `REGISTRY` y exige `params_cls`, `slots`, etiquetas en es/en/pt y pasar el kit de §3.8; el mismo estilo que `catalog_ops.COMMANDS`, con la lección de que ese test hoy falla.
- **Seguridad** [I]: un plugin es código arbitrario. Se carga solo con consentimiento explícito (`afm --plugins` / casilla en la GUI), registrado en `~/.config/afm/plugins.json` con el `sha256` del paquete (si cambia, vuelve a pedir). Nunca en la versión web. El informe de armado lista qué tipos y de dónde se usaron. **Un paquete de catálogo nunca trae código** (§3.7).
- **Licencia** [I, no es asesoría legal]: el proyecto es GPL-3.0-or-later; un plugin que importa `afm` es obra derivada, así que debe ser compatible con GPL. Anotarlo en la guía de plugins.
- **Web / Pyodide** [V el mecanismo, I la conclusión]: el motor se instala en el navegador como wheel puro Python con `micropip` (D4); técnicamente un wheel de plugin entraría igual, pero ejecutaría código de terceros dentro de la página sin ningún modelo de permisos. En la versión web solo corren los tipos internos y los perfiles N1.

### 3.5 Mejorar un corte: oferta de tipos

Es lo que da sentido a «los cortes se mejoran vía plugins»: el corte es el dato, y la articulación se **elige** entre lo que ese corte admite.

1. Cada tipo declara `requires(jp, params) → FaceRequirements(min_face_radius, min_depth_behind, face_shape, axis_vs_normal, mirror_gap)`.
2. `joint_types.offer(face, host_capacity, params, slot) → [Offer(type_id, nivel, motivos, margen_pared_mm)]` con `nivel` en `ok / aviso / no`. Usa lo que ya existe: `limbs.pin_fit` (margen de pared del pasador en la cara), `capacity.measure` (profundidad detrás de la cara; cabezas enfrentadas) y `ports.port_compat`. **Es barata** (milisegundos, sin armar la figura), como la compatibilidad barata de hoy.
3. Dónde se ve: columna «Tipos posibles» en la guía de cortes (`guide.py`, ventana «Guía de cortes»), en `afm guia` y en `afm catalogo explicar`. Un plugin recién instalado aparece solo.
4. Elegir un tipo para un corte = escribir `LimbSpec.mechanism` / `JointSpec.type` (§3.6) o, para toda la línea, `Params.joints[slot]`.

### 3.6 Datos del proyecto y compatibilidad hacia atrás

- `LimbSpec.mechanism` ya es una cadena: pasa a ser **el `id` del registro**. Se agrega `mechanism_params: dict | None` (JSON opaco, validado por `params_cls`). `LimbSpec.to_dict` / `from_dict` listan los campos a mano (`limbs.py` ~160–205): se suman ahí.
- `mechanism=None` sigue significando «según `Params.shoulder_detent` / `hip_detent`»: un `.proyecto` viejo carga y arma **idéntico**.
- `JointSpec` gana `type: str = "hinge"` y `type_params`; hoy `kind` ∈ `knee|elbow` queda como *lugar*, no como mecánica. `JointSpec.to_dict` / `from_dict` también son explícitos: una clave ausente se lee como `"hinge"`.
- `Project`: `"version": 1` → `2` con lectura de ambos (hoy está en `project.py:143`). Los campos nuevos son opcionales.
- **Perfil de línea N1**: `Params.joints: dict` = `{slot: {"type": id, "params": {…}}}`. Como `linea.json` guarda `Params.to_dict()`, el perfil viaja al catálogo, a `compat.recipe_key` y a los paquetes exportados sin cableado extra.
- **Riesgo propio de este diseño**: `Params.from_dict` es estricto (`cls(**d)`), así que un motor viejo no puede leer una línea guardada por uno nuevo. Mitigación: agregar el campo con valor por defecto vacío, y que `paquete.json` declare `motor: ">=<versión>"` cuando use `joints`.

### 3.7 Catálogo, compatibilidad y paquetes en línea

Hoy dos piezas de catálogo encajan si sus caras coinciden (`port_compat`, `interface.py`, `capacity.py`). Si una línea cambia el tipo de un lugar, **dos piezas con tipos distintos en el mismo puerto no se pueden mezclar** aunque la cara coincida. Reglas propuestas:

1. El puerto y `PieceMeta` llevan `joint_type` y un hash de los parámetros críticos (los que fijan geometría de acople: `shaft_d`, `head_d`, `disc_d`…). Los puertos viejos sin campo se leen como el tipo de su `mechanism`.
2. `port_compat` rechaza con motivo explícito («hombro: `detent` contra `mushroom`») cuando difieren.
3. Una línea fija **un tipo por lugar** (`Params.joints`). La mezcla libre queda fuera de alcance.
4. `capacity.demand`, `capacity.pin_shift`, `ports._pin_for` e `interface` (`pared_min_mm`, criterio de talle) delegan en `JointType.requires`. El contrato de interfaz sigue siendo **por clase** (la línea fija un tipo por clase).
5. **La `capacidad` guardada en cada pieza se mide con el pasador de la línea** (`catalog.py` ~864). Se guarda con qué tipo y parámetros se midió; si la línea cambia el tipo, `afm catalogo capacidad --escribir` la recalcula y `port_compat` marca `sin_capacidad` mientras no se haga (es el comportamiento de hoy para piezas viejas).
6. **Caché de compatibilidad completa**: `compat.recipe_key` ya hashea `Params`, así que cambiar el tipo o sus parámetros invalida solo. Falta lo otro: actualizar un plugin sin tocar `Params` cambia la geometría. La clave suma `"tipos": {id: versión}` (y el `sha256` del módulo si es un plugin).
7. **Paquetes en línea**: `paquete.json` suma la clave opcional `tipos_articulacion` (`[{"id": "ball", "api": 1}]`); el esquema admite claves nuevas sin subir `afm-paquete-1`. `instalar.plan_install` avisa «requiere el tipo `ball` y no está instalado» (como hoy con `requiere`) y **nunca instala código**: el lector de zip endurecido solo extrae datos, y eso no cambia.

### 3.8 Kit de conformidad y ejemplo mínimo

Para que un tercero pueda *comprobar* que su tipo sirve, `afm.joint_types.testing.check_type(t)` (y `afm joint-type check <id>`) corre sobre un corte sintético del muñeco (`testfigure.make_doll`) y verifica:

| # | Verificación | Origen del criterio |
|---|---|---|
| 1 | `build` es determinista (dos corridas dan los mismos volúmenes) | regla del contrato |
| 2 | piezas estancas, volumen > 0, sin NaN | `Manifold.status()` y `S.to_printable_trimesh` (`S.to_manifold` lanza `MeshNotManifold` con una malla abierta) |
| 3 | giro libre: interferencia hija–(padre − cortadores) < 1e-4 mm³ en todas las poses de `motion.poses(2,5)` | criterio del plan de rodilla/codo |
| 4 | retención: desplazar la hija ≥ 1 mm en ±X, ±Y, ±Z choca | prueba «subir 1 mm choca» de `validate` |
| 5 | pared del padre alrededor de la cavidad ≥ `min_wall` | `Params.min_wall` |
| 6 | espejo: el lado `der` es `S.mirror_x` del `izq` (mismo volumen) | regla de espejado del motor |
| 7 | `requires()` coherente: una cara que no cumple lo pedido lanza un error claro o avisa, nunca devuelve basura | §3.5 |
| 8 | solo importa la superficie pública permitida (revisión estática) | §3.2 |

Un test del repo corre el kit sobre **cada** tipo de `REGISTRY` (también los internos), de modo que Fase 0 ya lo ejercita con hongo, clic y bisagra.

Ejemplo mínimo de un plugin (**boceto**: `joint_types` todavía no existe, así que el conjunto no corre; lo que sí se ejecutó en 00e2ab9 es `PinSpec` con `default_factory`, `S.halfspace`, `mushroom_pin` y `S.dilate` con estos argumentos):

```python
from dataclasses import dataclass, field
from afm import solids as S
from afm.params import PinSpec
from afm.pins import mushroom_pin
from afm.joint_types import FaceRequirements, JointResult, RevoluteMotion, register

@dataclass(frozen=True)
class SwivelParams:
    pin: PinSpec = field(default_factory=lambda: PinSpec(3.0, 2.0, 5.0, 1.2))   # vástago Ø, largo, cabeza Ø, alto

class Swivel:
    id, api_version = "swivel", 1
    slots = frozenset({"biceps", "muslo", "canilla"})
    params_cls = SwivelParams

    def requires(self, jp, params):
        return FaceRequirements(min_face_radius=jp.pin.head_d / 2 + params.min_wall,
                                min_depth_behind=jp.pin.shaft_len + jp.pin.head_h + params.clearance)

    def build(self, site):
        up = S.halfspace(site.plane)                          # `site.child` llega entero: el tipo lo parte
        prox, dist = S.intersection(site.child, up), S.difference(site.child, up)
        pin = mushroom_pin(site.face.center, site.face.outward, site.jp.pin)
        cavity = S.dilate(pin, site.params.clearance, site.segments)   # el pasador es de revolución: ya es el barrido
        return JointResult(pieces={"prox": prox, "dist": S.union(dist, pin)},
                           cutters={"parent": [cavity], "host": []}, parent_adds=[],
                           motion=RevoluteMotion(site.child, site.face.center, site.face.outward, (0.0, 360.0)),
                           report={}, warnings=[])

register(Swivel())
```

---

## 4. Tipos nuevos: cómo se resuelven y qué riesgo traen

### 4.1 `swivel` (flat plane / pivot): bíceps, muslo, canilla
- **Geometría** [I]: parte el miembro por el plano transversal; la mitad distal recibe un hongo sobre la normal (`pins.mushroom_pin`), la proximal una cavidad `dilate(pasador, clearance)` con retención axial por la cabeza. Como solo el pasador entra en el padre, la cavidad es de revolución y no hace falta barrer ángulos (el hombro sí los barre porque todo el miembro entra en el torso). Giro 360° libre, o con clic reutilizando `DetentSpec` (el disco de clic es un disco con muescas y vale en cualquier miembro).
- **Cambios en el motor**: `joints.build_joint` lanza error si el eje no es casi perpendicular a la normal (línea 230); el `swivel` es justo el caso contrario, así que **no** pasa por `build_joint`: es un tipo aparte. La partición por plano (hoy dentro de `build_joint`, líneas ~245–247) se extrae a un helper público.
- **Riesgo**: bajo en geometría; medio en imprimibilidad (cabeza de hongo sobre un plano vertical: calibrar con probeta, §5).
- **Pared**: el corte transversal deja la cara del bíceps o la canilla; con una figura de 129 mm [V `REFERENCE_FIGURE_HEIGHT`] el vástago puede quedar cerca de `min_pin_d`: `requires()` lo informa antes de armar.

### 4.2 `hinge` como *rocker* y tobillo
- Bisagra con rango ±30° y lugar nuevo `tobillo`. Necesita que el pie sea un corte (`LimbSpec`/`JointSpec` con `slot="tobillo"`). **[P]** ¿el pie es parte de la pierna o pieza propia en las figuras del cliente?

### 4.3 `ball` (rótula) y `tcrotch`: cintura, cadera, cabeza
- **Geometría** [I]: esfera Ø `ball_d` sobre un cuello, en la hija. El zócalo del padre sale de `dilate(esfera + cono del cuello)`; el labio que queda sobre el ecuador retiene la bola (imprime armada, sin soportes en la bola). `detent.revolved_envelope` da la envolvente del cuello.
- **Necesita** `Motion` general (§3.3) y revisar las validaciones por eje (`_validate_chain`, `_validate_detent`, `posed_pieces`, `guide.SeatedClash`), que recorren un ángulo, no una grilla de poses.
- **Riesgos**: (a) imprimibilidad: la cúpula superior del zócalo es un puente en FDM con boquilla 0,4 (lágrima/chaflán o reorientar la pieza); (b) holgura radial 0,5 mm sobre bolas de Ø6–8 mm es mucha: probetas; (c) profundidad: el padre debe tener ≥ `ball_d` + paredes detrás del corte (`capacity.measure` ya mide profundidad detrás de la cara); (d) la pose sentada pide giro en 90° de cadera: confirmar que la rótula la admite.
- `tcrotch` **[I]** es `ball` con cono asimétrico: no se toca hasta que `ball` esté probado.
- Es el tipo más vistoso de la imagen y el de más incertidumbre: conviene la **probeta de rótula** del hilo físico (§5) antes de comprometer la Fase 4.

### 4.4 Pilas de tipos en un corte (ball disc de la cabeza) y doble pivote
- `JointStack([ball, disc])`: dos tipos en serie con un tramo intermedio. El doble pivote es una cadena de dos `hinge` (exige Fase 3). Se difieren a la Fase 6; solo si el cliente lo prioriza.

### 4.5 `disc_pin` (pasador suelto), solo si el cliente lo acepta
- Un tipo con `extras = {"pasador": …}` y `assembly = "post_assembly"`. El motor lo rechaza si la línea no lo habilita (campo `Params.allow_loose_pieces`, apagado por defecto), y el informe y `flat_for_print` ubican la pieza suelta en la cama. Ver P3.

---

## 5. Fases

Esfuerzos **[I]**: días de trabajo de una persona con el repo ya conocido; sin contar impresiones físicas.

| Fase | Entrega | Esfuerzo | Criterio de aceptación |
|---|---|---|---|
| **0. Contrato y registro** | `afm/joint_types/` con `base.py`, `REGISTRY`; hongo, clic y bisagra envueltos como tipos; `LimbSpec.mechanism` = id del registro; `RevoluteMotion` (`NamedTuple`); kit de conformidad corriendo sobre los tipos internos | **6–9 d** | `tools/golden.py comparar todos`: volúmenes ±0,01 %, holguras ±0,005 mm, mismas claves de `construccion`/`validacion` (`guemes_fbx` ±0,5 %); suite en verde salvo los 2 fallos que ya tiene `main` (Apéndice A: dejarlos anotados, no arrastrarlos) |
| **1. Datos, catálogo, perfiles, paquetes** | `mechanism_params`, `JointSpec.type`, `Params.joints`, `Project` v2, `FaceRequirements` reemplaza `capacity.demand`/`pin_shift`/`_pin_for`/`PIN_OF_CLASS` en los 5 módulos, `joint_type` en puertos y `PieceMeta`, `capacidad` con el tipo medido, `recipe_key` con `tipos`, `paquete.json` con `tipos_articulacion` | **5–8 d** | proyectos y catálogos viejos cargan y arman idéntico; `port_compat` explica el rechazo por tipo; ida y vuelta JSON de `Params.joints`; `instalar` avisa por un tipo ausente |
| **2. `swivel` + tobillo + oferta de tipos** | tipos nuevos, `offer()` en la guía de cortes y en `afm guia`, probetas (`probes.py`), un ejemplo en `examples/` | **5–8 d** | giro libre sin interferencia en el rango; retención (no sale al tirar ≥ 1 mm); pared ≥ `min_wall`; la probeta imprime y gira |
| **3. Varias articulaciones por miembro** | `by_limb` pasa de dict a lista ordenada del torso a la punta; la hija de una es el padre de la siguiente; nombres de pieza configurables (hoy `DEFAULT_NAMES`); `joint_preview` y `joint_edit` pasan por el registro | **8–12 d** | bíceps + codo + muñeca en el mismo brazo, validación en cadena; el caso de una sola articulación no cambia (golden) |
| **4. `Motion` general + `ball`** | `ConeMotion`, `.revolute()` en `colorreg`/`equestrian`/`controller`, validación sobre grilla de poses, `SeatedClash` con `print_pose`, tipo `ball`, probeta de rótula | **9–13 d** | zócalo retiene la bola; recorrido libre en el cono declarado; la probeta imprime armada |
| **5. Plugins externos, GUI, docs** | entry point `afm.joint_types`, carga con consentimiento y `plugins.json` con hash, formularios de parámetros generados desde `params_cls` (hoy `PARAM_FIELDS` en `controller.py:183` es una tupla fija), `afm joint-type check`, guía de plugins, plugin de ejemplo | **6–9 d** | un plugin de ejemplo instalado con `pip` aparece en la GUI y arma una figura; sin consentimiento no se carga; con el `sha256` cambiado vuelve a pedirlo |
| **6. Opcional** | `JointStack` (cabeza ball-disc), `double_hinge`, `tcrotch`, `disc_pin` | M | solo si el cliente lo prioriza |

**Total orientativo [I]: 39–59 días hábiles ≈ 8–12 semanas** de una persona (Fases 0–5; la 6 no está incluida). La v1 decía 6–9 semanas, pero sus propias fases sumaban 34–53 días. **Primer hito usable (Fases 0–2): 16–25 días ≈ 3–5 semanas**, con `swivel`, tobillo, oferta de tipos y perfiles N1. La Fase 0 decide si el cálculo es realista: si la salida no queda idéntica a los dorados, el resto se reestima.

**Hilo físico (en paralelo; no suma días de ingeniería pero sí calendario).**
- **H1 (durante F0–F2)**: probeta de `swivel`: grilla holgura × Ø de vástago, con el flujo que ya existe (`probe_files` → `probetas.3mf` + `probetas.json` → `afm probe-resultado … --perfil X`).
- **H2 (al terminar F0, antes de F4)**: probeta de rótula (el spike de la v1, ahora con fecha): decide si la Fase 4 va como está o se reestima (R3).
- **[P]** Ciclado: el cliente pidió miles de cambios de pose para el clic; confirmar si el mismo criterio vale para `swivel` y `ball`.

**Por qué este orden.** La Fase 0 es el control de calidad de todo lo demás. `swivel` va antes de la cadena porque cabe en el límite actual de una articulación por miembro. `ball` va después de `Motion` porque lo necesita. Los plugins de terceros van al final: la API se congela cuando ya la usaron dos tipos nuevos.

### Pruebas nuevas
`tests/test_joint_registry.py` (registro, ids duplicados, tipo desconocido, etiquetas es/en/pt), `tests/test_joint_conformance.py` (el kit de §3.8 sobre cada tipo del registro), `tests/test_joint_types_golden.py` (equivalencia con el motor actual), uno por tipo nuevo (`test_joint_swivel.py`, `test_joint_ball.py`), `tests/test_project_v2.py`, `tests/test_params_joints.py` (ida y vuelta y lectura de un perfil sin `joints`), y casos de `port_compat`, `instalar` y `recipe_key` por tipo.

---

## 6. Riesgos

| # | Riesgo | Probabilidad / impacto [I] | Mitigación |
|---|---|---|---|
| R1 | La refactorización de la Fase 0 cambia una figura sin que se note | media / alto | golden con los 5 dorados que existen (muneco, guemes, guemes_fbx, belgrano, mickey) antes y después, en cada PR. Azurduy queda sin dorado hasta reparar su torso |
| R2 | `pipeline.build` es largo y mezcla torso, cabeza, mano, cintura, monturas: extraer el bucle de miembros arrastra efectos laterales | alta / medio | extraer en pasos con golden; empezar solo por el bloque hongo/clic/bisagra |
| R3 | Una rótula no imprime armada con boquilla 0,4 | media / alto para `ball` | probeta H2 antes de la Fase 4 |
| R4 | La compatibilidad del catálogo se vuelve más estricta y deja piezas sin pareja | media / medio | un tipo por lugar (`Params.joints`); motivo explícito en el rechazo |
| R5 | Plugins de terceros ejecutan código no confiable | baja / alto | consentimiento con hash, sin plugins en la web, sin código en paquetes de catálogo, informe de lo cargado |
| R6 | La GUI (GTK) no se puede probar en el entorno de desarrollo (ya pasa hoy, ver `PROGRESS.md`) | alta / medio | lógica en `Controller` con tests sobre `FakeScene`; la ventana la prueba el cliente |
| R7 | El port a Pyodide (D5, `manifold3d` sin wheel) cambia la base | media / medio | los plugins usan solo `afm.solids`; N2 queda fuera de la web |
| R8 | El registro se desincroniza de sus consumidores (hoy `test_catalog_ops` falla por `retoque` y `guardar-montura`) | media / medio | test de coherencia sobre `REGISTRY` en CI desde la Fase 0 |
| R9 | `Params.from_dict` es estricto: un motor viejo no abre una línea nueva | media / medio | campo con valor por defecto vacío; `motor` en `paquete.json`; prueba de lectura de un perfil sin `joints` |
| R10 | Actualizar un plugin deja caché de compatibilidad vieja (la clave solo hashea `Params`) | media / medio | `recipe_key` con `tipos: {id: versión}` y `sha256` del módulo |
| R11 | Un plugin lento o inestable congela el armado (los `build` ya tardan minutos) | media / medio | `segments` bajo en vista previa; el motor mide el tiempo de cada tipo y lo informa; tope configurable |
| R12 | Un tipo con pieza suelta contradice print-in-place sin que el cliente lo note | baja / medio | `allow_loose_pieces` apagado por defecto; el informe lo declara |

---

## 7. Decisiones para el cliente **[P]**

1. **¿Qué tipos y en qué orden?** Propuesta: `swivel` y tobillo primero (bajo riesgo), rótula con probeta previa. ¿Hay una figura concreta que mande?
2. **«Double pin»**: ¿es un doble pivote (más rango) o dos pasadores en un mismo pivote (más firmeza)? Cambia la Fase 3 y la 6.
3. **Pasador suelto del render del brazo**: ¿qué hace el cilindro amarillo y la ranura superior? ¿Se acepta una pieza extra (no print-in-place, no todo en una impresión) o los tipos con pieza suelta quedan fuera?
4. **¿Plugins de terceros o solo extensibilidad interna?** Si es solo interna, la Fase 5 se achica a perfiles (N1) y la API puede cambiar sin avisar.
5. **Cintura, cuello y muñeca**: ¿pasan a ser tipos intercambiables (usan `mushroom_pin` por separado) o se dejan fijos? Propuesta: dejarlos para después de la Fase 3; son los de más efectos laterales.
6. **Catálogo**: ¿una línea fija un tipo por lugar (propuesta) o se quiere mezclar tipos entre líneas?
7. **Pie y tobillo**: ¿el pie es parte de la pierna en tus figuras o una pieza aparte?
8. **Durabilidad**: ¿el criterio de «miles de cambios de pose» del clic aplica también a `swivel` y rótula? Define si hace falta una probeta de ciclado.
9. **Orden entre este plan y el de catálogos en línea**: hoy D05–D07 ya están en `main`; si se quiere que los paquetes viajen con `tipos_articulacion` desde el primer día, la Fase 1 conviene antes de publicar paquetes con perfiles nuevos.

## 8. Fuera de alcance
- Formas de corte distintas del plano (escalonado, cónico, «butterfly»/«rocker» del torso): sería un segundo punto de extensión (`CutProfile`), a evaluar después. Si llega, produce la misma `ContactFace`, así que el contrato de §3.2 no cambia.
- Cambiar las holguras o validar la mecánica con elementos finitos.
- Tornillos y tuercas (decisión previa del cliente: no se implementan).
- DSL para describir articulaciones sin Python más allá de los perfiles JSON.
- Plugins en la versión web.

## 9. Primer paso concreto
Fase 0, en una rama `plugins_articulacion` desde `main`: (1) `python tools/golden.py comparar todos` para confirmar la línea base en la máquina del cliente (los dorados ya están en `tests/golden/`; **no** hace falta `guardar`; en el entorno de análisis `comparar muneco` dio «idéntica a la dorada»); (2) crear `afm/joint_types/base.py` con `JointSite`, `JointResult`, `Motion`/`RevoluteMotion` (`NamedTuple`) y el kit de conformidad; (3) envolver **solo el hongo** (el más simple) y hacer que `pipeline.build` lo llame por el registro; (4) `golden comparar todos`. Si eso sale idéntico, seguir con clic y bisagra. En paralelo, pedir al cliente imprimir la probeta de rótula (H2) y responder P1, P3 y P7.

---

## Apéndice A: Qué se verificó y cómo (00e2ab9)

| Qué | Cómo | Resultado |
|---|---|---|
| Base del repo | `git log` | `main` = `00e2ab9`; 5 commits sobre `0d421b7` (UX de Parámetros, catálogos en línea D05–D07) |
| `mushroom_pin` | `grep` de las llamadas | 9 llamadas en 7 módulos: `pipeline`, `detent`, `waist`, `head`, `hand` ×2, `probes` ×2, `joint_preview` (más una mención en la docstring de `pin_ghost`) |
| Una articulación por miembro | `grep by_limb` | `pipeline.py:122` y `:203` |
| `LimbSpec.mechanism` | `grep -rn mechanism … --exclude-dir=gui` | 13 usos fuera de la GUI |
| `PIN_OF_CLASS` | `grep -rl` | 5 módulos: `capacity`, `pin_aid`, `interface`, `ports`, `guide`; `_pin_for` 2 usos |
| `kind` | `grep -rnE "kind ==\|\.kind\b\|kind in\|kind !="` | 242 líneas (135 en 24 módulos sin `gui/`); 78 con literales `arm\|leg\|knee\|elbow` |
| `res.motion` | `grep` | 21 menciones en `pipeline.py`; fuera: `gui/controller.py:2426`, `equestrian.py:278`, `colorreg.py` (123–190) |
| `Params.from_dict` estricto | lectura | termina en `cls(**d)` |
| Dorados | `ls tests/golden` | `belgrano`, `guemes`, `guemes_fbx`, `mickey`, `muneco`; no hay `azurduy` |
| `tools/golden.py comparar muneco` | ejecutado (venv con las dependencias del `pyproject`) | «idéntica a la dorada» |
| Tests | ejecutados: `test_catalog_import.py` (con `-x`) y `test_catalog_ops.py` | **fallan 2**: `test_catalog_import::test_parse_builds_the_arguments_of_catalog_ops` (`KeyError: 'bore_d'`) y `test_catalog_ops::test_every_catalog_subcommand_is_registered_for_the_window` (el segundo coincide con `PROGRESS.md`). En `test_catalog_ops.py` pasan 17 y falla 1; en `test_catalog_import.py` el primer fallo cortó la corrida (14 pasaron antes) |
| Suite | `pytest --collect-only` | 2 899 tests recogidos |
| Esfuerzo de la v1 | suma de sus fases | 5+3+5+8+8+5 = 34 d y 8+5+8+12+12+8 = 53 d → 7–11 semanas, no 6–9 |

**No se verificó:** la suite completa (solo los dos archivos de arriba), los dorados de las figuras reales (tardan minutos cada uno y necesitan sus STL), nada de la GUI (GTK), y ninguna de las estimaciones [I].

## Apéndice B: Mapa de consumidores (00e2ab9)

| Cambio del plan | Dónde toca |
|---|---|
| Registro / despacho por tipo | `pipeline.build` (bucle de miembros ~225–335; hongo inline ~258; clic `DT.build_detent_limb`), `_sweep_range` (77), `_pin_spec` (85), `_rotation_axis` (89); `joints.KINDS` / `JOINT_KIND_BY_LIMB` / `DEFAULT_NAMES` (27–29); `joint_preview.py`, `joint_edit.py` |
| `by_limb` → lista ordenada | `pipeline.py` 119–125, 201–206, 273, 314, 331, 380, 434, 546, 935–942 |
| `Motion` | `pipeline.py` 295, 306, 395, 413, 751, 937, 1170, 1480–1529, 1670–1731, 1904; `colorreg.py`; `equestrian.py:278`; `gui/controller.py:2426`; `guide.SeatedClash` |
| `FaceRequirements` | `ports.py` (`PIN_OF_CLASS` 33, `_pin_for` 376, `port_compat` 429), `capacity.py` (`MIRROR_CLASSES` 26, `demand` 37, `pin_shift` 49, `measure` 107), `interface.py` (`ClassContract` 68; `PIN_OF_CLASS` 99, 191, 391), `guide.py` (403), `pin_aid.py` (153), `catalog.py` (~864) |
| Datos y compatibilidad | `limbs.py` (`mechanism` 150, `to_dict`/`from_dict` ~160–205), `joints.py` (`JointSpec.to_dict`/`from_dict`), `params.py` (`from_dict` ~120–140), `project.py:143`, `assembly.py:369`, `extract.py:297` |
| Caché y paquetes | `compat.py` (`recipe_key`), `recipe.py:285` (`recipe_params`), `catalog.py:149` (`linea.json`), `paquete.py` (364–367, 521–549), `instalar.py` (428–451), `schemas/afm-paquete-1.schema.json` |
| Probetas | `probes.py`, `profile.py` (`apply_probe_cell`) |
| GUI | `gui/controller.py:183` (`PARAM_FIELDS`), `gui/param_diagram.py` (diagrama de parámetros fijo), ventana de guía de cortes |
