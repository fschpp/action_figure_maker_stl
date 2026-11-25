# Plan de factibilidad: plugins que convierten un corte en una articulación

_2026-11-25. Solo plan: no se escribió ni se ejecutó código. Base: `main` (0d421b7) de `fschpp/action_figure_maker_stl`, leído a mano (`planes.py`, `limbs.py`, `pins.py`, `detent.py`, `joints.py`, `waist.py`, `head.py`, `hand.py`, `pipeline.py`, `ports.py`, `capacity.py`, `project.py`, `params.py`). Referencia visual: el despiece «Action Force» que trajo el cliente (cabeza ball-disc, hombro disc, bíceps/muslo/canilla flat plane, codo/rodilla double pin, cintura/cadera ball joint, muñeca disc, tobillo disc rocker)._

Marcas: **[V]** verificado en el código · **[I]** inferencia o estimación del analista, a validar · **[P]** pendiente de decidir con el cliente.

---

## 0. Resumen

**Veredicto: factible, y la mitad del trabajo es ordenar lo que ya existe.** El cliente describe bien el modelo mental: un corte es un plano; de un lado queda una parte, del otro la otra, y en el medio no hay nada salvo la holgura. Lo que hoy decide *qué se pone en ese medio* está escrito a mano en `pipeline.build` (1 956 líneas) con un despacho por `kind == "arm"|"leg"`, por `LimbSpec.mechanism` y por flags de `Params` (`shoulder_detent`, `hip_detent`). Hay **seis constructores** que hacen lo mismo con variantes (hongo en `pipeline`, clic en `detent.py`, bisagra en `joints.py`, cintura en `waist.py`, cuello en `head.py`, muñeca en `hand.py`) y **nueve llamadas** a `mushroom_pin` repartidas en siete módulos (`pipeline`, `detent`, `waist`, `head`, `hand` ×2, `probes` ×2, `joint_preview`). [V]

**Propuesta.** Separar dos cosas:

1. **El corte** (se queda en el núcleo): plano + siluetas → pieza padre, pieza hija y una *cara de contacto* (`ContactFace` / `PortFace`) con su centro, normal, forma 2D y holgura.
2. **El tipo de articulación** (nuevo punto de extensión, el «plugin»): recibe el corte y devuelve la pieza hija con su pasador, lo que hay que **restar** del padre (cavidad con holgura), lo que hay que **sumar** al padre (lengüetas), cómo **se mueve** (para validar y para la pose de impresión) y qué **le exige** a la cara y al padre (para la compatibilidad del catálogo).

**Qué tan lejos queda cada tipo de la imagen** (detalle en §2):

| Tipo de la imagen | Esfuerzo | Por qué |
|---|---|---|
| Disc (hombro, cadera, muñeca) | **ya existe** | hongo + disco de clic (`detent.py`) |
| Double pin / rocker (codo, rodilla, tobillo) | **bajo** | bisagra de `joints.py` con otro rango; el tobillo es un lugar nuevo |
| Flat plane (bíceps, muslo, canilla) | **medio** | es el hongo girando sobre la normal del corte, pero dentro de un miembro (hoy eso solo lo hace la bisagra) |
| Ball (cintura, cadera, cabeza) | **alto** | giro en 3 ejes: el motor entero asume un eje de giro; requiere generalizar el movimiento |
| Varias articulaciones en un miembro (bíceps + codo + muñeca) | **alto, escondido** | `pipeline.build` rechaza hoy más de una por miembro |

**Recomendación.** Empezar por una Fase 0 que meta el registro de tipos y envuelva hongo, clic y bisagra **sin cambiar ni un STL** (se verifica con `tools/golden.py`). Si eso sale limpio, el resto son tipos nuevos sobre una interfaz probada. No publicar la API de plugins de terceros hasta tener dos tipos nuevos funcionando.

---

## 1. Cómo está hoy

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

- Un miembro se define con `LimbSpec` (plano de silueta + polígono + extrusión, 2.º y 3.er corte opcionales `cross`, `cross2`). `separate_limb` lo saca del torso y devuelve `(limb, ContactFace)`. [V]
- `ContactFace` (centro, normal `outward`, área, forma) es **la única interfaz** entre el corte y la articulación. Los puertos del catálogo (`ports.PortFace`, `PortFrame`) son la misma idea serializada. [V]
- Una segunda articulación *dentro* de un miembro (rodilla, codo) es otro plano: `JointSpec` (`joints.py`). [V]

### 1.2 Qué hace falta saber de las articulaciones que existen [V]

| Hoy | Dónde | Eje de giro respecto del plano | Qué construye |
|---|---|---|---|
| Hongo (hombro, cadera) | `pipeline.build`, `pins.mushroom_pin` | **normal** al plano (`_rotation_axis(face)` = `face.outward`) | vástago + cabeza en la hija; cavidad = `dilate(hija + pasador)` barrida por ángulos, restada del torso |
| Clic (hombro, cadera) | `detent.py` (441 líneas) | normal | lo anterior + disco con muescas + 2 lengüetas en el padre |
| Bisagra (rodilla, codo) | `joints.py` (301 líneas) | **dentro** del plano: `build_joint` rechaza `|n·eje| > 0,5` | parte el miembro en proximal/distal con lengüeta en ranura y bastago pasante |
| Cintura | `waist.py` | normal | hongo robusto, parte el torso |
| Cuello | `head.py` | normal | hongo del cuello |
| Muñeca | `hand.py` | normal | pasador fino `hand_pin` |

Es decir: **dos familias geométricas** (eje ⟂ al plano, eje ∥ al plano) con muchas variantes, todas de **un solo eje de giro**.

### 1.3 Dónde está cableado el tipo [V]

- `LimbSpec.mechanism`: `None | "mushroom" | "detent"` (13 usos fuera de la GUI). Es la semilla de un registro y ya viaja en el catálogo (`extract.py:297` → `meta.mecanismo = {"mechanism": …}`; `assembly.py:369` lo lee).
- `Params`: `shoulder_pin`, `hip_pin`, `neck_pin`, `waist_pin`, `hand_pin`, `hinge_pin`, `detent`, `shoulder_detent`, `hip_detent` (un campo por tipo y lugar).
- `joints.KINDS = ("knee", "elbow")`, `JOINT_KIND_BY_LIMB`, `DEFAULT_NAMES`: tipos y nombres de pieza fijos.
- `ports.PORT_CLASSES = ("cuello","hombro","cadera","cintura","muneca","codo","rodilla")` y `ports.PIN_OF_CLASS` (lo usan `capacity.py` y `guide.py`): qué pasador usa cada clase de puerto.
- `pipeline.build`: `by_limb` con `ValueError("por ahora una sola articulación por miembro")`; `res.motion[name] = (limb, centro, eje, (a0, a1))` (un eje); `_validate_*` por tipo (`_validate_detent`, `_validate_joints`, `_validate_waist`, `_validate_hand`, `_validate_chain`).
- Ocurrencias de `kind ==` / `.kind` en `src`: 143 y 177. Se verá cuántas son de articulación y cuántas de miembro en la Fase 0.

---

## 2. La imagen contra el motor

La imagen da un vocabulario de seis familias. Mapeo, con lo que no se puede confirmar marcado:

| Etiqueta | Qué es mecánicamente | Contra el motor |
|---|---|---|
| **Disc** shoulder / wrist | Disco que gira sobre el eje normal al corte | **Ya existe**: hongo (fricción) o clic (`detent`, `n_notches`). Un disco sin muescas es el hongo. |
| **Flat plane** bicep / thigh / shin | Corte plano transversal al miembro; las dos mitades giran una contra otra sobre el eje del miembro (giro de «torsión») | Mismo mecanismo que el hongo del hombro (eje = normal), pero **dentro** de un miembro. Hoy solo `JointSpec` parte miembros y solo sabe hacer bisagra. Se resuelve dándole a `JointSpec` un `type`. La cavidad es más fácil que la del hombro: el pasador es de revolución, así que `dilate(pasador)` ya es el barrido, sin recorrer ángulos. |
| **Double pin** elbow / knee | **[I]** dos pivotes en serie (dos ejes paralelos) para ~150° de flexión; no se ve en la imagen si los dos discos giran juntos | Una bisagra (`HingeSpec`) cubre el caso de un pivote. El doble pivote es una cadena de dos bisagras → exige varias articulaciones por miembro (Fase 3). **[P]** confirmar con el cliente qué rango busca. |
| **Disc rocker** ankle, **rocker** torso | Disco que se balancea en un solo eje con rango corto y tope | Bisagra con rango ±30° y un lugar nuevo, `tobillo`. Hoy el pie no es un corte. |
| **Ball joint** waist / hips, **ball disc** head | Rótula (3 ejes); la cabeza combina rótula + disco | **No existe.** La esfera es invariante a rotaciones, así que la cavidad se resuelve con `dilate(esfera)` + cono del cuello (`detent.revolved_envelope` hace una envolvente de revolución), pero la **validación y la pose** asumen un eje: hay que generalizar `Motion` (§3.3). La cabeza ball-disc es una pila de dos tipos en el mismo corte (Fase 5). |
| **Butterfly / rocker torso** | Torso en piezas con encastre | Fuera de alcance de este plan: es una forma de cortar el torso, no una articulación (ver §7). |

En la imagen el ícono «= customizable» aparece junto a cabeza, cintura, muñeca y canilla (las que alcanzo a ver). Sugiere que esa marca es **parametrizable por el usuario**, lo que apoya el nivel 1 de §3.1 (perfiles sin código).

---

## 3. Diseño propuesto

### 3.1 Tres niveles de extensión

| Nivel | Qué es | Para quién | Código |
|---|---|---|---|
| **N0. Tipos internos** | Hongo, clic, bisagra, giro (swivel), rótula, tobillo, registrados en `afm/joint_types/` | El proyecto | Python, en el repo |
| **N1. Perfiles** | Un JSON que fija el *tipo* y los *parámetros* de cada lugar de una línea de figuras («en la línea X el hombro es `detent` con `disc_d=8`…») | El usuario de la línea | **Sin código** |
| **N2. Plugins** | Un paquete Python que registra un tipo nuevo | Terceros | Python, vía entry point `afm.joint_types` o carpeta de plugins |

N1 sale casi gratis una vez que existe N0, y probablemente cubre lo que el cliente llama «customizable». N2 es lo más caro y lo que más riesgo trae: se hace **último**.

### 3.2 Contrato de un tipo (boceto)

```python
# afm/joint_types/base.py  (nombres a confirmar)

@dataclass(frozen=True)
class JointSite:
    """El corte, tal como lo ve el tipo de articulación. Todo en la pose «de pie» (ángulo 0)."""
    slot: str                      # "hombro", "cadera", "rodilla", "codo", "cintura", "cuello",
                                   # "muneca", "tobillo", "biceps", "muslo", "canilla"
    side: str                      # "izq" | "der" (el motor trabaja +X y espeja)
    child: S.Manifold              # miembro ya cortado, con holgura de rebaba rebajada (`burr`)
    face: ContactFace              # centro, outward, área, forma 2D
    parent: S.Manifold | None      # solo lectura: para medir espacio (`capacity.measure`)
    params: Params                 # globales: clearance, nozzle, min_wall, sweep_step, ref_height
    jp: object                     # parámetros propios del tipo (instancia de `params_cls`)
    flex: tuple | None             # rango pedido (a0, a1) en grados, o None = el del tipo
    print_hint: dict               # ángulo de impresión, arriba de impresión, sentada sí/no
    segments: int = 12             # resolución: la vista previa de la GUI baja este valor

@dataclass
class JointResult:
    pieces: dict[str, S.Manifold]  # {"child": …} o {"prox": …, "dist": …}; el motor los nombra
    cutters: list[S.Manifold]      # se RESTAN del padre (cavidad con holgura)
    parent_adds: list[S.Manifold]  # se SUMAN al padre al final (lengüetas del clic)
    motion: "Motion"               # §3.3
    report: dict                   # lo que hoy va en report["limbs"][n], ["detent"], ["joints"]
    warnings: list[str]

class JointType(Protocol):
    id: str                        # "mushroom", "detent", "hinge", "swivel", "ball", …
    api_version: int
    params_cls: type               # dataclass: sirve para JSON, GUI y validación
    slots: frozenset[str]          # en qué lugares tiene sentido
    def requires(self, jp, params) -> "FaceRequirements": ...   # radio mínimo, profundidad, ángulo eje/normal
    def build(self, site: JointSite) -> JointResult: ...
    def validate(self, result, site, bodies) -> list["Finding"]: ...   # opcional
```

`FaceRequirements` reemplaza a `capacity.demand` y a `ports._pin_for` / `PIN_OF_CLASS`: hoy el catálogo averigua «qué le pide el pasador de la línea al anfitrión» mirando un campo de `Params` por clase de puerto; con el contrato lo pregunta al tipo. [V sobre el estado actual; I sobre el diseño]

**Reglas que el contrato impone al plugin** (para no romper el port a Pyodide, `PLAN_port_pyodide.md`):
- Solo `afm.solids` (única puerta a `manifold3d`, así lo declara `detent.py`), `numpy`, `shapely`. Nunca `import manifold3d`.
- Determinista y sin red ni disco.
- Todo en mm absolutos de impresión (como `PinSpec`, `HingeSpec`, `DetentSpec`), no en unidades de figura.

### 3.3 Movimiento: de «un eje» a `Motion`

Hoy `res.motion[name] = (limb, centro, eje, (a0, a1))` alimenta la validación (`_validate_chain`, `free_range`, pose sentada, `posed_pieces`). Una rótula no cabe ahí. Propuesta:

```python
class Motion(Protocol):
    def poses(self, step_deg: float) -> Iterable[np.ndarray]: ...   # 4x4 de la pieza móvil respecto de su padre
    def print_pose(self) -> np.ndarray: ...
    def clicks(self) -> list[np.ndarray]: ...                       # opcional (clic)

class RevoluteMotion(Motion): ...    # (centro, eje, (a0, a1)): es lo de hoy, bit a bit
class ConeMotion(Motion): ...        # rótula: cono de inclinación × giro propio, malla de poses
```

`RevoluteMotion` reproduce exactamente la tupla actual, de modo que hongo, clic y bisagra no cambian. Las validaciones que reciben una tupla pasan a recibir un `Motion`.

### 3.4 Registro y descubrimiento

```python
# afm/joint_types/__init__.py
REGISTRY: dict[str, JointType] = {}
def register(t: JointType) -> None: ...
def get(id: str) -> JointType: ...          # ValueError claro si no existe
```

- Internos: se registran al importar el paquete.
- N2: entry point `afm.joint_types` (vía `importlib.metadata`) y, opcionalmente, carpeta `~/.config/afm/plugins/` o `$AFM_PLUGINS`.
- **Seguridad** [I]: un plugin es código arbitrario. Se carga solo con consentimiento (`afm --plugins`/casilla en la GUI), nunca en la versión web, y el informe de armado lista qué tipos y de dónde se usaron.
- **Licencia** [I, no es asesoría legal]: el proyecto es GPL-3.0-or-later; un plugin que importa `afm` es obra derivada, así que debe ser compatible con GPL. Anotarlo en la guía de plugins.
- **Web / Pyodide** [I, sin verificar]: en la versión web solo corren los tipos internos y los perfiles (N1); `importlib.metadata` sobre ruedas instaladas en Pyodide hay que probarlo antes de prometer plugins ahí.

### 3.5 Datos del proyecto y compatibilidad hacia atrás

- `LimbSpec.mechanism` ya es una cadena: pasa a ser **el `id` del registro**. Se agrega `mechanism_params: dict | None` (JSON opaco, validado por `params_cls`).
- `mechanism=None` sigue significando «según `Params.shoulder_detent` / `hip_detent`»: un `.proyecto` viejo carga y arma **idéntico**.
- `JointSpec` gana `type: str = "hinge"` (hoy `kind` ∈ `knee|elbow` queda como *lugar*, no como mecánica) y `type_params`.
- `Project`: `"version": 1` → `2` con lectura de ambos (hoy está en `project.py:143`). Los campos nuevos son opcionales.
- Perfil de línea (`profile.py`): `joints: {slot: {type, params}}`. Es el soporte del N1.

### 3.6 Catálogo y compatibilidad (el cruce delicado)

Hoy dos piezas de catálogo encajan si sus caras coinciden (`port_compat`, `interface.py`, `capacity.py`). Si una línea cambia el tipo de un lugar, **dos piezas con tipos distintos en el mismo puerto no se pueden mezclar** aunque la cara coincida. Reglas propuestas:

1. El puerto lleva `joint_type` y un hash de los parámetros críticos (los que fijan geometría de acople: `shaft_d`, `head_d`, `disc_d`…).
2. `port_compat` rechaza con motivo explícito («hombro: `detent` contra `mushroom`») cuando difieren. Los puertos viejos sin campo se leen como el tipo de su `mechanism`.
3. Una línea fija **un tipo por lugar** (perfil de línea). La mezcla libre queda fuera de alcance.
4. `capacity.demand` y `ports._pin_for` pasan a delegar en `JointType.requires`.

---

## 4. Tipos nuevos: cómo se resuelven y qué riesgo traen

### 4.1 `swivel` (flat plane): bíceps, muslo, canilla
- **Geometría** [I]: parte el miembro por el plano transversal; la mitad distal recibe un hongo sobre la normal (`pins.mushroom_pin`), la proximal una cavidad `dilate(pasador, clearance)` con retención axial por la cabeza. Giro 360° libre, o con clic reutilizando `DetentSpec` (el disco de clic es un disco con muescas, vale en cualquier miembro).
- **Cambios en el motor**: `joints.build_joint` hoy lanza error si el eje no es casi perpendicular a la normal (línea `abs(n @ axis) > 0.5`); el `swivel` es justo el caso contrario, así que **no** pasa por `build_joint`: es un tipo aparte que usa la misma partición proximal/distal.
- **Riesgo**: bajo en geometría; medio en imprimibilidad (cabeza de hongo sobre un plano vertical: reusar `probes.py` para calibrar).

### 4.2 `ankle` (rocker) y tobillo
- Bisagra con rango ±30° y lugar nuevo `tobillo`. Necesita que el pie sea un corte (`LimbSpec`/`JointSpec` con `slot="tobillo"`). **[P]** ¿el pie es parte de la pierna o pieza propia en las figuras del cliente?

### 4.3 `ball` (rótula): cintura, cadera, cabeza
- **Geometría** [I]: esfera Ø `ball_d` sobre un cuello, en la hija. El zócalo del padre sale de `dilate(esfera + cono del cuello)`; el labio que queda sobre el ecuador retiene la bola (imprime armada, sin soportes en la bola). `detent.revolved_envelope` da la envolvente del cuello.
- **Necesita** `Motion` general (§3.3) y revisar las validaciones por eje (`_validate_chain`, `free_range`, `posed_pieces`), que recorren un ángulo, no una grilla de poses.
- **Riesgos**: (a) imprimibilidad: la cúpula superior del zócalo es un puente en FDM con boquilla 0,4 (lágrima/chaflán o orientar la pieza); (b) holgura radial 0,5 mm sobre bolas de Ø6–8 mm es mucha: probetas; (c) profundidad: el padre debe tener ≥ `ball_d` + paredes detrás del corte (`capacity.measure` ya mide profundidad detrás de la cara); (d) la pose sentada pide giro en 90° de cadera: confirmar que la rótula la admite.
- Es el tipo más vistoso de la imagen y el de más incertidumbre: conviene un **spike** de una semana con una probeta impresa antes de comprometer el resto.

### 4.4 Pilas de tipos en un corte (ball disc de la cabeza)
- `JointStack([ball, disc])`: dos tipos en serie con un tramo intermedio. Se difiere a la Fase 5; solo si el cliente lo prioriza.

---

## 5. Fases

Esfuerzos **[I]**: días de trabajo de una persona con el repo ya conocido; sin contar impresiones físicas.

| Fase | Entrega | Esfuerzo | Criterio de aceptación |
|---|---|---|---|
| **0. Contrato y registro** | `afm/joint_types/` con `base.py`, `REGISTRY`; hongo, clic y bisagra envueltos como tipos; `LimbSpec.mechanism` = id del registro; `RevoluteMotion` | M, 5–8 d | `tools/golden.py comparar todos`: volúmenes ±0,01 %, holguras ±0,005 mm, mismas claves de `construccion`/`validacion`; suite de `tests/` en verde (hoy falla en main `test_catalog_import::test_parse_builds_the_arguments_of_catalog_ops`: dejarlo anotado, no arrastrarlo) |
| **1. Datos, catálogo, perfiles** | `mechanism_params`, `JointSpec.type`, `Project` v2, `FaceRequirements` reemplaza `capacity.demand`/`_pin_for`, `joint_type` en puertos, perfil de línea con `joints` | M, 3–5 d | proyectos y catálogos viejos cargan y arman idéntico; `port_compat` explica el rechazo por tipo; test de ida y vuelta JSON |
| **2. `swivel` + tobillo** | tipos nuevos, probetas (`probes.py`), un ejemplo en `examples/` | M, 5–8 d | giro libre sin interferencia en el rango; retención (no sale al tirar ≥ 1 mm); pared ≥ `min_wall`; probeta imprime y gira |
| **3. Varias articulaciones por miembro** | `by_limb` pasa de dict a lista ordenada del torso a la punta; la hija de una es el padre de la siguiente; nombres de pieza configurables (hoy `DEFAULT_NAMES`) | L, 8–12 d | bíceps + codo + muñeca en el mismo brazo, validación en cadena; el caso de una sola articulación no cambia (golden) |
| **4. `Motion` general + `ball`** | `ConeMotion`, validación sobre grilla de poses, tipo `ball`, probeta de rótula | L, 8–12 d | zócalo retiene la bola; recorrido libre en el cono declarado; probeta física imprime armada |
| **5. Plugins externos, GUI, docs** | entry point `afm.joint_types`, carga con consentimiento, listas de tipos y formularios de parámetros generados desde `params_cls` en la GUI (hoy `PARAM_FIELDS` en `controller.py`), guía de plugins, plugin de ejemplo | M, 5–8 d | un plugin de ejemplo instalado con `pip` aparece en la GUI y arma una figura; sin consentimiento no se carga |
| **6. Opcional** | `JointStack` (cabeza ball-disc), doble pivote en cadena | M | solo si el cliente lo prioriza |

**Total orientativo [I]: 6–9 semanas** de una persona; la Fase 0 decide si el cálculo es realista (si la salida no queda idéntica a los dorados, el resto se reestima).

**Por qué este orden.** La Fase 0 es el control de calidad de todo lo demás. `swivel` va antes de la cadena porque cabe en el límite actual de una articulación por miembro. `ball` va después de `Motion` porque lo necesita. Los plugins de terceros van al final: la API se congela cuando ya la usaron dos tipos nuevos.

### Pruebas nuevas
`tests/test_joint_registry.py` (registro, ids duplicados, tipo desconocido), `tests/test_joint_types_golden.py` (equivalencia con el motor actual), uno por tipo nuevo (`test_joint_swivel.py`, `test_joint_ball.py`), `tests/test_project_v2.py`, y casos de `port_compat` por tipo.

---

## 6. Riesgos

| # | Riesgo | Probabilidad / impacto [I] | Mitigación |
|---|---|---|---|
| R1 | La refactorización de la Fase 0 cambia una figura sin que se note | media / alto | golden con las 4 figuras reales antes y después, en cada PR |
| R2 | `pipeline.build` es largo y mezcla torso, cabeza, mano, cintura, monturas: extraer el bucle de miembros arrastra efectos laterales | alta / medio | extraer en pasos con golden; empezar solo por el bloque hongo/clic/bisagra |
| R3 | Una rótula no imprime armada con boquilla 0,4 | media / alto para `ball` | spike con probeta antes de la Fase 4 |
| R4 | La compatibilidad del catálogo se vuelve más estricta y deja piezas sin pareja | media / medio | perfil de línea con un tipo por lugar; motivo explícito en el rechazo |
| R5 | Plugins de terceros ejecutan código no confiable | baja / alto | consentimiento, sin plugins en la web, informe de qué se cargó |
| R6 | La GUI (GTK) no se puede probar en el entorno de desarrollo (ya pasa hoy, ver `PROGRESS.md`) | alta / medio | lógica en `Controller` con tests sobre `FakeScene`; la ventana la prueba el cliente |
| R7 | El port a Pyodide (D5, `manifold3d`) cambia la base | media / medio | los plugins usan solo `afm.solids`; N2 queda fuera de la web |

---

## 7. Decisiones para el cliente **[P]**

1. **¿Qué tipos y en qué orden?** Propuesta: swivel y tobillo primero (bajo riesgo), rótula con spike previo. ¿Hay una figura concreta que mande?
2. **«Double pin»**: ¿es un doble pivote (más rango) o dos pasadores en un mismo pivote (más firmeza)? Cambia la Fase 3.
3. **¿Plugins de terceros o solo extensibilidad interna?** Si es solo interna, la Fase 5 se achica a perfiles (N1) y la API puede cambiar sin avisar.
4. **Cintura, cuello y muñeca**: ¿pasan a ser tipos intercambiables (usan `mushroom_pin` por separado) o se dejan fijos? Propuesta: dejarlos para después de la Fase 3; son los de más efectos laterales.
5. **Catálogo**: ¿una línea fija un tipo por lugar (propuesta) o se quiere mezclar tipos entre líneas?
6. **Pie y tobillo**: ¿el pie es parte de la pierna en tus figuras o una pieza aparte?

## 8. Fuera de alcance
- Formas de corte distintas del plano (escalonado, cónico, «butterfly»/«rocker» del torso): sería un segundo punto de extensión (`CutProfile`), a evaluar después.
- Cambiar las holguras o validar la mecánica con elementos finitos.
- Tornillos y tuercas (decisión previa del cliente: no se implementan).
- DSL para describir articulaciones sin Python más allá de los perfiles JSON.

## 9. Primer paso concreto
Fase 0, en una rama `plugins_articulacion`: (1) `tools/golden.py guardar` de las 5 figuras para tener la línea base; (2) crear `afm/joint_types/base.py` con `JointSite`, `JointResult`, `Motion`/`RevoluteMotion`; (3) envolver **solo el hongo** (el más simple) y hacer que `pipeline.build` lo llame por el registro; (4) `golden comparar todos`. Si eso sale idéntico, seguir con clic y bisagra.
