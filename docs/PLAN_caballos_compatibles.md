# Plan: caballos estáticos compatibles con figuras articuladas

**Versión:** 0.3 — revisada contra `main` (099f188, 2026-11-21) y contra `horse_baseless.stl`  
**Repositorio:** `fschpp/action_figure_maker_stl`, rama `main`  
**Alcance:** importar caballos estáticos y montar sobre ellos una figura articulada construida desde el catálogo, con la pose sentada actual **sin cambios**.

> No se rehace el motor de articulaciones ni se articula el caballo. **La pose sentada actual es un contrato fijo** (sin pose «cabalgando», sin perfil ecuestre, sin grados de libertad nuevos). Lo que cambió en esta versión es *qué se mide primero* (ver §1.4) y *cómo se parte el trabajo* (ver §9).

---

## Qué cambió respecto de la v0.2

| # | Cambio | Por qué |
|---|---|---|
| 1 | La Fase 0 pasa a ser una **puerta G0 con número** y con un script repetible (`tools/ecuestre_fase0.py`), no un informe a mano. | Una medición preliminar con las piernas reales de Güemes (§1.4) da un tallado chico y localizado, pero sobre un asiento muy adelantado; hay que fijarlo con el caballo y la escala definitivos antes de construir nada más. |
| 2 | El alivio del caballo se calcula con la **pierna completa** (muslo + rodilla + bota), no solo con los muslos. | Lo que choca con el barril son las botas. |
| 3 | Nuevo modo **«tallado»** (booleana con holgura sobre el caballo, sin silla) antes que la silla. Es la idea original de la figura sentada sobre un caballo con asiento tallado. | Es lo más barato de construir y es, a la vez, la medición de viabilidad con el motor real. La silla pasa a ser un agregado. |
| 4 | Tareas renombradas **E00–E18** (antes T00–T18). | `PLAN_fbx_textura_color.md` ya usa T01–T37; los otros planes usan M (armas) y R (retoque). |
| 5 | Cada tarea es una ficha con **depende de / paralelo con / leer / pruebas / listo cuando** (§9). | Para que cada turno de Claude gratuito avance sin releer todo, y para que se vea qué se puede hacer en paralelo. |
| 6 | Se agregan restricciones que el repo hoy impone: portabilidad (`manifold3d` solo en `solids.py`), textos es/en/pt, costo de `min_gap` y de `dilate`, tope de triángulos al exportar, convención del frente (+Y en la salida del motor), interacción con las monturas de armas ya horneadas. | Estaban fuera del plan y rompen pruebas o tiempos si se ignoran. |
| 7 | Numeración de secciones corregida (había dos «§9» y dos «8.» en las pruebas) y fases de GUI alineadas. | Orden. |

---

## 0. Resumen de la propuesta

Tres objetos separados:

1. **Figura jinete:** se arma con `Recipe` + `recipe.assemble()` como hoy; no se toca su pose.
2. **Caballo:** malla estática (STL en el MVP), escalada y orientada aparte. Sin cortes ni articulaciones.
3. **Asiento:** de dos tipos, que pueden combinarse:
   - **Tallado:** se resta del caballo la geometría real del jinete dilatada por la holgura (copia derivada; el STL original nunca se toca).
   - **Silla:** pieza generada con base adaptada al lomo y canales para la pelvis y las piernas.

Una capa nueva `src/afm/equestrian.py` alinea los objetos, valida y exporta. **No es una `montura`:** `mount.py` ya significa soporte de armas.

**Decisión arquitectónica:** el jinete se arma y valida primero con el motor actual; después todo el conjunto se mueve con **una única matriz rígida**. No se recalculan pasadores ni holguras tras moverlo, no se escala al jinete y no se toca `sit_angle`, `knee_print_angle`, `JointSpec` ni los ejes.

---

## 1. Hallazgos de la revisión

### 1.1 Lo que existe y se reutiliza (verificado en `main`)

| Área | Dónde | Uso en este proyecto |
|---|---|---|
| Armado desde catálogo | `recipe.assemble(recipe, catalog_root, progress, sit=True)` → `Assembled` (`res: BuildResult`, `params`, `limbs`, `joints`, `host`) | Fuente del jinete. `BuildResult.pieces` = `{nombre: Manifold}` en la pose. |
| Piezas colocadas | `assembly.PlacedPart`, `ports.PortFrame`, `ports.coupling_transform`, `BuildResult.placed` | Marcos de cadera y piezas. |
| Pose sentada | `Params.sit_angle = 90`, `knee_print_angle = 85`, `clearance = 0.50`, `min_wall = 1.2` (`params.py`) | Se **leen** de la línea; no se copian. |
| Booleanas | `solids.py`: `to_manifold` (lanza `MeshNotManifold`), `union`, `difference`, `intersection`, `dilate`, `halfspace`, `prism_from_polygon`, `largest_component`, `drop_small_components`, `decimate`, `to_printable_trimesh` | Única vía a `manifold3d` (ver 1.3). |
| Distancias | `Manifold.min_gap(otro, radio)` (nativo de `manifold3d`; `pipeline.py` lo usa con radio acotado) | Holguras. Ver costo en 1.3. |
| Choque en pose | `guide.SeatedClash` (brazo vs. pierna, sin armar) | Referencia de estilo; **no** sirve para caballo/jinete. |
| Clic sobre la vista | `gui/mount_pick.py` (`pick_anchor`, `pick_from_widget`, `direction_from_drag`, `group_camera`), `gui/mount_preview.py` (`mount_preview`, `zone_map`), `mount_map.py` | Precedente reciente y probado para elegir punto + dirección y pintar mapas. Más al día que `gui/picking.py`. |
| Receta con campos opcionales | `Recipe.accesorios` y `Recipe.paleta_override`: campos de nivel superior que `to_dict()` escribe «solo si hay», fuera de `ajustes` (`AJUSTES = ("mano", "escalas", "monturas")` es una tupla cerrada) | Mismo patrón para `cabalgadura`. |
| Clave de compat | `compat.recipe_key` (hash de piezas, params, opciones, `key_ajustes()`, monturas, contrato) | No debe cambiar con el caballo. |
| Herramientas de medición | `tools/medir_articulaciones.py`, `tools/medir_interfaces.py` | Precedente para `tools/ecuestre_fase0.py`. |

### 1.2 Lo que NO existe

- Nada ecuestre en el código (se buscó «caballo», «horse», «cabalg», «ecuestr» en `src/`, `tests/`, `docs/`: solo aparece como idea en notas).
- `Recipe` no tiene `cabalgadura`; `Recipe.accesorios` coloca accesorios **al costado** (`accessories.place_beside`), no sobre la figura.
- `meshio.load_figure()` normaliza a `ref_height`: no sirve para el caballo.
- `pipeline.export()` reconstruye el `BuildResult` (pierde `mounts`, `placed`, `host_bare`), decima a `Params.export_max_tris` = 150 000 triángulos por pieza, y con `orient`/`flat` mueve o deforma solo al jinete. La exportación ecuestre necesita su propio camino.

### 1.3 Restricciones del repo que el plan anterior no recogía

1. **Portabilidad.** `tests/test_engine_portability.py` falla si algún módulo distinto de `solids.py` importa `manifold3d`. `equestrian.py` solo puede usar `solids.*` (si falta una operación, se agrega a `solids.py`). Aunque la web esté fuera del MVP, esa prueba corre hoy.
2. **Costo.** `pipeline.py` documenta que `min_gap` crece muy rápido con el radio (figura real de 190–430 k triángulos: ≈296 s nativo y ≈1545 s en el navegador). `solids.dilate` es una suma de Minkowski con esfera: es cara sobre piernas de cientos de miles de triángulos. Consecuencia: radios chicos (2–5 mm), recorte del caballo a una ventana alrededor del asiento y herramientas de corte decimadas.
3. **Frente.** El motor trabaja con la figura mirando a **+Y** (`frame.py`: gira 180° sobre Z si la fuente declara `front = "-Y"`). Se verificó armando `catalogo/ejemplo`: las piernas salen hacia +Y (y de −4 a 38 mm). Los STL de origen (Güemes, y `horse_baseless.stl`) miran a −Y. Entonces el STL del caballo entra con **guiño de 180°** en su `HorseSpec`, y `RiderFrame` debe **leer** hacia dónde mira el jinete (centroide de piernas menos centroide de pelvis, en XY), no suponerlo.
4. **Textos.** Todo texto de interfaz debe existir en es/en/pt (`python-i18n`, `tests/test_i18n.py`).
5. **GTK.** En el entorno de las sesiones no hay GTK4 ni moderngl/EGL: la ventana no se puede ejecutar. Las tareas de GUI se entregan con la lógica en módulos sin GTK probada y la ventana marcada «sin probar», como se hizo en M13–M17. La vista previa es una imagen que dibuja `PreviewRenderer` fuera de pantalla, no un visor 3D vivo.
6. **Armas ya horneadas.** `PieceMeta.montura` y `Recipe.ajustes["monturas"]` (M10–M17) hacen que el armado a veces traiga puentes de montura; una pieza con montura exige `torso_mirror = false`. La exportación ecuestre debe llevar esas piezas con el jinete (forman parte de él). Los accesorios sueltos no.
7. **Documentos.** Siguen vigentes las advertencias de v0.2: algunos planes antiguos dicen «no implementado» sobre cosas que el código ya tiene; manda el código y su batería de pruebas.

8. **Mallas exportadas vs. `Manifold`.** `solids.to_printable_trimesh` de la pelvis armada con cintura dio una malla que `trimesh.boolean` rechaza («Not all meshes are volumes»). El perfil del jinete y las booleanas deben partir de los `Manifold` de `BuildResult.pieces`, no de STL exportados. Además, `afm armar` con la validación completa de la receta de Belgrano fue terminado por falta de memoria en un entorno de 4 GB (el armado con `recipe.assemble` sí sale): E00 debe llamar a `assemble`, no a la CLI.

### 1.4 Mediciones preliminares (catálogo `proceres-127` + `horse_baseless.stl`)

**El caballo adjunto** (leído con `trimesh`):

| Dato | Valor |
|---|---|
| Triángulos / cáscaras | 64 324 / 1 (estanco, orientación consistente) |
| Volumen | 6 499,9 u³ |
| Extensión X × Y × Z | 13,93 × 50,81 × 44,23 u |
| Orientación | cabeza hacia **−Y** (cuello sube a z≈21 en y≈−18), cola hacia +Y (z≈3,5 en y≈26) |
| Patas | pezuñas en z = −22,3 (delanteras y≈−10…−6, traseras y≈16…20) |
| Dorso | cima en z≈9–10 entre y≈−2 y y≈20 |
| Ancho del barril | 12–14 u entre y≈−8 e y≈16; a z=8 (≈2 u bajo la cima) mide 7,3 u |
| Unidades | **desconocidas** (44 u de alto no son 13 cm): hay que escalar |

A 3× el caballo mide ≈152 mm de largo, ≈42 mm de ancho, ≈97 mm de dorso al suelo y ≈133 mm hasta la cabeza; volumen ≈175 cm³.

**La figura real.** Se armó `figuras/belgrano.receta.json` de `proceres-127` (pelvis y pierna de Güemes `@1`, `cintura = true`) con `recipe.assemble(sit=True)`. Pose sentada, mirando a +Y:

| Pieza | X (mm) | Y (mm) | Z (mm) | Volumen |
|---|---|---|---|---|
| muslo (por lado) | 0,6 … 14,5 | −5,4 … 29,3 | 54,9 … 70,1 | 3,0 cm³ |
| pantorrilla (por lado) | 3,5 … 16,7 | 19,5 … 41,3 | 22,2 … 64,2 | 2,7 cm³ |

Hueco entre muslos 1,3 mm; ancho total de los muslos 29,0 mm; la pantorrilla cuelga 32,7 mm bajo la base del muslo.

**Medición de E00 (preliminar).** Caballo con guiño de 180° (cabeza a +Y) a 3×, muslos apoyados sobre la cima del dorso bajo ellos, solo traslación; volumen de las pantorrillas que cae dentro del caballo:

| y de la pelvis sobre el caballo | Pantorrillas dentro del caballo (las dos) | Por pierna |
|---|---|---|
| −6 mm | 1 727 mm³ (32 %) | ≈860 mm³ |
| +3 mm | 1 291 mm³ (24 %) | ≈640 mm³ |
| +12 mm | 1 163 mm³ (21 %) | ≈590 mm³ |
| +21 mm | 753 mm³ (14 %) | ≈390 mm³ |
| +30 mm | 942 mm³ (17 %) | ≈470 mm³ |

(Por delante de y≈+48 mm el jinete queda sobre la cabeza del caballo y la cifra de 0 % no significa nada.)

**Lectura:**
- El tallado necesario es chico y localizado: 0,4–0,9 cm³ por pierna, ≈0,5 % del volumen del caballo. Los muslos apoyan sobre el dorso sin chocar.
- Con los asientos medidos (y de −6 a +30 mm) las pantorrillas cuelgan sobre la **cruz y la base del cuello**, y el tallado entra unos 5 mm por cada lado (de x≈4 a x≈9 mm, donde el semiancho del caballo es ≈9 mm): el cuello quedaría con ≈8 mm de ancho en esa zona. Cumple `min_wall` pero hay que mirarlo con el modelo en pantalla.
- El asiento queda bastante adelantado respecto del centro del lomo. Es una consecuencia de la pose fija (muslos de 34 mm hacia adelante y pantorrillas colgando), no un defecto del caballo.
- **Corrección de la v0.3 inicial:** una estimación anterior con cajas (botas de 13 cm³ y muslos de 38 mm de ancho) daba 19–75 % de las botas dentro del caballo. Sobreestimaba cinco veces el volumen de las botas y el ancho de los muslos; esta medición con la pieza real la reemplaza.
- **Limitaciones:** no incluye la pelvis (su malla exportada no es un volumen para `trimesh`; ver 1.3-8), no se probó la silla, la escala 3× es una elección y la receta es la de Belgrano (misma pelvis y pierna que Güemes, torso y brazos distintos, que no entran en la medición). Con 2,5×, 3,5× y 4× el barrido no encontró un asiento sobre el caballo mejor que los de 3×.

---

## 2. Requisitos funcionales

- **RF-01 · Importar caballo estático.** STL, orientación y escala física elegidas por el usuario; una sola malla, sin articulaciones.
- **RF-02 · Reutilizar caballos.** Biblioteca de caballos de la línea, separada del catálogo de piezas; no son `PieceMeta`.
- **RF-03 · Usar el jinete real de catálogo.** El asiento sale de la pelvis y las piernas **realmente** elegidas y de su pose efectiva.
- **RF-04 · Definir el asiento.** Punto sobre el lomo y dirección hacia la cabeza; el sistema deduce el marco y deja ajustar altura, inclinación y posición.
- **RF-05 · Adaptar el caballo.** Dos modos: **tallado** (resta de las piernas completas dilatadas) y **silla** (pieza generada con canales). Límites obligatorios de profundidad, pared residual y número de componentes; si no se cumplen, la combinación se declara incompatible.
- **RF-06 · Validar el conjunto**, distinguiendo uniones intencionales (silla–caballo) de choques (piernas–caballo, piernas–silla, pelvis–silla).
- **RF-07 · Exportar como conjunto**, con la posición relativa intacta, en STL ensamblado y STL por componente.

---

## 3. Decisiones de diseño

- **D1 · Módulo propio.** `src/afm/equestrian.py`; modelos `HorseSpec`, `HorseSeatSpec`, `RiderFrame`, `SaddleBuild`, `RidingAssembly`. No se usa `mount.py` ni `MountSpec`. Nombres de UI: **Caballo**, **Asiento**, **Silla**, **Montar figura**.
- **D2 · El caballo no se normaliza.** Se conserva su tamaño y se pide una medida física (por ejemplo, alto del dorso en mm). Se sugiere una escala inicial a partir del jinete (ancho de cadera y largo de muslo), que el usuario puede cambiar. Escalado no uniforme: solo como herramienta avanzada y con aviso.
- **D3 · Marco canónico.** `Z` arriba, `Y` hacia la cabeza del caballo, `X` lateral. `HorseSpec` guarda un guiño (`yaw`) para llevar el STL a ese marco: **`horse_baseless.stl` necesita 180°** (cabeza en −Y). El jinete sale del motor mirando a +Y; `RiderFrame` lo comprueba (1.3-3).
- **D4 · Pose fija.** Se aplica una sola matriz rígida a todas las piezas del jinete. No se tocan matrices de piernas, ángulos ni holguras. Si G0 no cierra, la combinación es incompatible (ver §13, D-P1 para lo que queda por decidir).
- **D5 · El jinete se mueve como conjunto.** Se mueven por igual `BuildResult.pieces` y, con ellas, las monturas de armas que traigan. Los accesorios sueltos (`Recipe.accesorios`) se siguen exportando al costado, fuera del conjunto. *(Decisión mía, fácil de cambiar.)*
- **D6 · Tallado primero, silla después.** El tallado se construye antes porque es la medición de G0 con el motor real y no agrega una pieza para armar. La silla agrega canales/faldones que ocultan el tallado y permiten un apoyo más cómodo de la pelvis.
- **D7 · Clave de compat intacta.** `cabalgadura` va fuera de `ajustes` y nunca entra en `compat.recipe_key`. La combinación caballo + jinete tiene su propia clave.
- **D8 · Primero escritorio.** Web/Pyodide y color/textura del caballo, fuera del MVP. Sí se respeta hoy la regla de portabilidad (1.3-1).
- **D9 · Todo texto en es/en/pt desde el primer botón.**

---

## 4. Flujo esperado del usuario

1. Armar la figura desde catálogo como hoy.
2. Activar **Cabalgadura** y elegir un caballo guardado o importar uno.
3. Ajustar escala y orientación del caballo (con una medida física de referencia).
4. Clic sobre el lomo para el centro del asiento y arrastre hacia la cabeza (mismo gesto que `mount_pick`).
5. Elegir modo (tallado, silla o ambos) y, para la silla, altura/inclinación/dimensiones iniciales (el programa propone valores desde el ancho de cadera).
6. **Calcular y comprobar pose.**
7. Ver el conjunto con colores de diagnóstico; corregir y revalidar.
8. Guardar la receta y exportar.

No se intenta detectar automáticamente el asiento: la primera versión pide el punto y la dirección.

---

## 5. Geometría

### 5.1 Perfil del jinete (`RiderProfile`)

Función pura sobre `Assembled`: centro y malla de la pelvis, marcos de los dos puertos de cadera (si faltan, error claro), separación entre caderas, **piernas completas** (muslo, rodilla, bota, tal como salen en `BuildResult.pieces`), caja envolvente de la parte inferior en la pose. No se infiere nada desde una caja envolvente.

### 5.2 Marco de asiento

El punto elegido es `seat_origin`; el segundo punto da la dirección. `+Z` queda arriba. La normal del lomo sirve para adaptar la base de la silla, **no** para acostar al jinete; la inclinación del torso solo cambia si el usuario pide un ángulo.

### 5.3 Tallado del caballo (modo «tallado»)

`caballo_tallado = caballo − (piernas completas dilatadas por holgura)`, sobre una copia.

1. Herramienta de corte = unión de las dos piernas del jinete ya colocadas, **dilatadas** por la holgura ecuestre. Se dilata sobre una copia decimada y recortada a una ventana alrededor del asiento (1.3-2).
2. Se limita a la zona de contacto y a una profundidad máxima configurable.
3. Se mide la pared residual del caballo en todo el entorno del corte, el número de componentes y que no se abrieron túneles.
4. Simetría solo si el choque es simétrico; no se fuerza.
5. Si no se cumplen los límites, **no se recorta más**: se informa «este caballo no admite esta figura en la pose estándar».

### 5.4 Silla (modo «silla»)

1. Volumen base simple (almohadilla) en el marco de asiento; ancho, largo y alto parametrizables.
2. Recorte contra el caballo para que la cara inferior se adapte al lomo, con una penetración controlada si se va a fusionar.
3. Volumen de despeje = pelvis + piernas completas colocadas, dilatado por la holgura, restado de la silla: apoyo de pelvis y canales laterales.
4. Faldones que cubran los canales. Pared ≥ `min_wall`, una sola componente, no flotante.
5. Silla y caballo no se fusionan automáticamente si eso elimina el montaje/desmontaje.

El modo «silla» puede necesitar además el tallado del caballo (5.3) si el barril sigue chocando con las botas; la silla sola no lo resuelve (ver 8.2).

### 5.5 Impresión

`Params.clearance` como holgura base jinete–silla; holgura aparte para silla–caballo si son piezas independientes. El caballo derivado se rehace siempre desde el original. Las piezas del jinete exportadas pasan por el tope de triángulos de la línea; el caballo y la silla, por su propio tope (el caballo de ejemplo tiene 64 k, lejos de 150 k).

---

## 6. Validación ecuestre

`validate_riding(assembly, params)`: función separada que no reemplaza ni modifica `pipeline.validate()`. El informe nombra la pareja exacta (`pierna_izq ↔ caballo`, `pelvis ↔ silla`). Se usa la misma tolerancia que `pipeline.validate` para filtrar ruido numérico (leer su constante, no inventar otra).

| Comprobación | Tipo |
|---|---|
| Caballo convertible a `Manifold` | Error |
| Silla unida al caballo según la estrategia elegida; sin flotar | Error |
| Silla de una sola componente | Error |
| Pared mínima de silla y de caballo tallado (también detrás de los rebajes) | Error |
| Piernas completas contra silla y contra caballo tallado: holgura prevista | Error |
| Profundidad y extensión de los rebajes dentro de la zona permitida (no invaden patas, cuello, etc.) | Error |
| Pelvis contra asiento: apoyo sin interpenetración | Advertencia/error |
| Distancia pelvis–silla (flotando) | Advertencia |
| Altura del jinete y escala del caballo | Informativo |

Las uniones intencionales se declaran explícitamente. Una validación ecuestre certifica la geometría de la pose, no la durabilidad de las articulaciones.

---

## 7. Datos y persistencia

### 7.1 Biblioteca de caballos

```text
<catalogo-de-linea>/caballos/<id>/caballo.json  +  modelo.stl
```

La ficha guarda: `id`, nombre, versión, archivo y SHA-256, unidades/escala aplicada, orientación (guiño y eje arriba), medida física de referencia y formato de ficha. Versiones inmutables o por hash; no se sobrescribe un archivo usado por una receta. No se usa `PieceMeta` ni `Catalog.add_piece()`.

### 7.2 Receta

Campo opcional `cabalgadura`, **solo si hay**, fuera de `ajustes` (mismo patrón que `accesorios` y `paleta_override`): referencia `id@versión` y SHA-256 del caballo, punto y dirección de asiento, modo (`tallado`, `silla`, `ambos`), parámetros de silla, holguras, límites del tallado y hash de la geometría derivada. Cambios necesarios en `recipe.py`: `to_dict` (solo si hay), `from_dict` (el constructor es posicional: agregar el campo al final con valor por defecto), `problems()`. Sin `cabalgadura` todo se comporta exactamente como hoy; con el hash del caballo distinto se avisa y se pide revalidar.

---

## 8. Referencias de juguetes

### 8.1 Evolución del caballo Playmobil
Una reseña de coleccionistas describe tres etapas: el modelo clásico (desde 1974) y el de 2001 tenían lomo afinado y recortes en el abdomen para que la silla encastrara; el de 2011 ganó anchura en hombros y caderas, perdió el rebaje visible y la silla pasó a sujetarse por debajo del abdomen. Fuente secundaria: [EarlyModernPlaymobil](https://earlymodernplaymobil.wordpress.com/2021/10/11/playmobil-horses/); no es documentación técnica oficial y las «generaciones» pueden variar según el coleccionista.

### 8.2 Patente US4044498A (BIG Spielwarenfabrik, no Playmobil)
[US4044498A](https://patents.google.com/patent/US4044498A/en) (resumen en [Justia](https://patents.justia.com/patent/4044498)) propone un lomo central estrecho que se completa con una pieza intercambiable o se reemplaza por una silla que deja pasar las piernas de una figura de piernas cercanas.

### 8.3 Lección para este proyecto
La interfaz silla–caballo puede resolverse de varias maneras y depende del caballo. Si el cuerpo del animal invade el espacio de las piernas, la silla sola no alcanza: hace falta tallar el caballo o declarar incompatible la combinación. Con las piernas reales de Güemes el choque es pequeño (§1.4), pero está en la cruz y la base del cuello, donde el modelo es más angosto: la lección se aplica ahí.

---

## 9. Tareas (E00–E18)

### Cómo leer las fichas
**Depende de** = tiene que estar hecha. **Paralelo con** = se puede hacer en otro turno sin esperarla. **Bloqueo** = algo que no se resuelve con código. Cada tarea cierra con pruebas; las pruebas nuevas van en `tests/test_equestrian_*.py` salvo que se indique otra cosa.

### Orden y paralelismo

```text
E00 ──► G0 (puerta)
          │
 E01 ─┬─► E02 ─┬─► E05 (tallado) ─► E08 ─► E09 ─► E10
 E03 ─┤        └─► E06 ─► E07 ──────┘
 E04 ◄┘ (E04 necesita E03)
 E11 (necesita E01)   E12 (necesita E01 y E04)   E13 → E14 → E15 → E16 → E17 → E18
```

E01, E02, E03 y E11 se pueden trabajar en paralelo entre sí. E05 y E06 también, una vez hechas E02 y E04. G0 bloquea **E05 en adelante** (se puede avanzar con E01–E04 y E11 mientras se decide G0).

### Fase 0 — Medir con la figura real (puerta G0)

**E00 · `tools/ecuestre_fase0.py`.** *Depende de:* nada. *Sin bloqueo:* el catálogo `proceres-127` ya está disponible; usar `recipe.assemble`, no `afm armar` (1.3-8).
- Leer: `recipe.assemble`, `pipeline.validate`, `tools/medir_articulaciones.py` (estilo), `ecuestre_e00_prelim.py` (semilla, ya medido con `proceres-127`).
- Hacer: armar la receta; escribir en un JSON las mallas de pelvis y piernas en la pose, los ángulos efectivos y los marcos de cadera; cargar el caballo con escala y guiño; barrer posiciones de asiento a lo largo del lomo y, para cada una, apoyar el jinete (solo traslación + guiño) y medir el volumen de **cada pierna completa** que cae dentro del caballo, la profundidad del tallado, la pared residual y el ancho del barril bajo los muslos; incluir la pelvis (desde su `Manifold`). Salida: tabla y JSON.
- Pruebas: una prueba con la figura demo (`catalogo/ejemplo`) y un caballo sintético que verifica que el script corre y que el volumen medido de un cubo atravesado es el esperado.
- Listo cuando: existe la tabla para Güemes + el caballo definitivo con su escala, incluida la pelvis, y se ve en pantalla cuánto se talla (la medición preliminar de §1.4 ya dio 0,4–0,9 cm³ por pierna a 3×).

**G0 · Puerta.** Decide la usuaria/el usuario con la tabla de E00 delante. Criterio *propuesto* (a confirmar): para al menos un asiento y una escala razonables, existe un tallado con pared residual ≥ `min_wall`, una sola componente y profundidad por debajo del máximo configurado. Si G0 falla: ver §13 (D-P1). **No se avanza a E05 sin G0.**

### Fase 1 — Datos y carga (motor puro)

**E01 · `HorseSpec` y `HorseMeta`.** *Paralelo con:* E02, E03, E11.
- Leer: `parts.PieceMeta` (estilo de serialización; no reutilizar la clase).
- Hacer: dataclasses con JSON de ida y vuelta: archivo, SHA-256, escala, guiño, medida de referencia. Validaciones de escala > 0 y de guiño.
- Pruebas: ida y vuelta, hash distinto, escala inválida.
- Listo cuando: `HorseSpec.from_dict(spec.to_dict()) == spec`.

**E02 · `load_horse_mesh`.** *Depende de:* E01.
- Leer: `meshio` (reparación), `solids.to_manifold`.
- Hacer: carga con escala y guiño; sin `load_figure`. Reparar y verificar que sea `Manifold`; informar abierta / no manifold / conteo de componentes. Capa opcional decimada para la vista previa y para las booleanas (la malla de 64 k triángulos entra, pero las de 200 k+ no).
- Pruebas: escala conocida preserva dimensiones; STL abierto falla con mensaje; STL estanco pasa; archivo faltante.
- Listo cuando: `horse_baseless.stl` carga con guiño 180° y a escala 3× mide ≈152 × 42 × 133 mm.

**E03 · `RiderProfile`.** *Paralelo con:* E01, E02.
- Leer: `recipe.Assembled`, `BuildResult.pieces/placed/joint_specs`, `ports.PortFrame`.
- Hacer: función pura que devuelve pelvis, puertos de cadera, piernas completas, caja envolvente y la dirección a la que mira el jinete (1.3-3). Rechaza recetas sin pelvis/puertos con un mensaje útil.
- Pruebas: figura demo; receta sin pelvis → error; la dirección de frente es +Y para `catalogo/ejemplo`.
- Listo cuando: devuelve el perfil sin llamar a `manifold3d` fuera de `solids.py`.

**E04 · `RiderFrame` y matriz rígida.** *Depende de:* E03.
- Hacer: marco de asiento a partir de origen, dirección y vertical; matriz 4×4 que se aplica a todas las piezas. Comprobar `RᵀR ≈ I`, `det ≈ +1`.
- Pruebas: distancias entre piezas y puertos invariantes; el resultado de `pipeline.validate` no cambia; guiño de 180° correcto.
- Listo cuando: mover y deshacer el movimiento devuelve las mallas originales.

### Fase 2 — Tallado, silla y validación

**E05 · Tallado del caballo.** *Depende de:* E02, E04 y **G0**. *Paralelo con:* E06.
- Leer: `solids.dilate/difference/halfspace`, 1.3-2, §5.3.
- Hacer: `carve_horse(horse, rider, clearance, limits)` con herramienta decimada y ventana; informe de volumen retirado, profundidad máxima, zonas y paredes mínimas; el original queda intacto.
- Pruebas: caballo sintético ancho se adapta; demasiado ancho se rechaza antes de atravesarse; STL fuente idéntico; mutación (holgura fuera de la herramienta) detectada.
- Listo cuando: las piernas dejan de intersectar el caballo y el informe cumple los límites.

**E06 · Volumen base de silla.** *Depende de:* E02, E04. *Paralelo con:* E05.
- Hacer: silla mínima paramétrica; recorte contra el caballo; detectar flotante, vacíos y fragmentos.
- Pruebas: sobre caballo sintético cerrado; una sola componente.

**E07 · Relieve de pelvis y canales.** *Depende de:* E06, E04.
- Hacer: despeje de pelvis + piernas completas dilatado, restado de la silla; faldones.
- Pruebas: pared ≥ `min_wall`, una componente, cavidad coherente con el jinete real; nada de la geometría humana cambia.

**E08 · `validate_riding()`.** *Depende de:* E05 (puede arrancar solo con el tallado). *Paralelo con:* E07.
- Leer: `pipeline.validate`, 1.3-2.
- Hacer: las comprobaciones de §6 con `min_gap` de radio acotado y filtro por caja envolvente; `problems` y `avisos` con nombres y mm/mm³.
- Pruebas: casos sintéticos que pasan y casos deliberados que fallan (caballo demasiado ancho, asiento bajo, pierna atravesando la silla, rebaje que perfora el torso).

**E09 · Ajuste iterativo.** *Depende de:* E08.
- Hacer: recalcular tallado/silla al cambiar punto, inclinación o tamaño sin regenerar las articulaciones; nunca aceptar un resultado con choques ni subir la profundidad por encima del máximo seguro.

### Fase 3 — Exportación

**E10 · Exportación agrupada.** *Depende de:* E04, E05.
- Leer: `pipeline.export` (qué reconstruye y qué decima), 1.2.
- Hacer: exportar jinete con la misma matriz que el caballo/silla; ensamblado conjunto y STL por componente; apoyar las patas en `z = 0` con un único marco; `flat_for_print` y `--sin-soportes` desactivados en este modo; `orient="auto"` solo si evalúa el conjunto entero; las piezas con montura de arma viajan con el jinete.
- Pruebas: todas las piezas con la misma matriz; el conjunto apoya en `z = 0`; la ruta sin caballo no cambia.

### Fase 4 — Persistencia

**E11 · Biblioteca `caballos/`.** *Depende de:* E01. *Paralelo con:* Fase 2.
- Hacer: agregar / listar / abrir / verificar caballo; copia administrada del STL; versiones por hash.

**E12 · `Recipe.cabalgadura`.** *Depende de:* E01, E04.
- Leer: `recipe.py` (`to_dict`, `from_dict`, `problems`, `key_ajustes`), `compat.recipe_key`.
- Hacer: campo opcional fuera de `ajustes`; clave propia del armado ecuestre.
- Pruebas: receta vieja carga igual; receta nueva ida y vuelta; **cambiar de caballo no cambia `compat.recipe_key`**.

### Fase 5 — CLI y escritorio

**E13 · CLI.** *Depende de:* E10, E11, E12. Importar/listar caballos; `afm armar` genera la escena si la receta trae `cabalgadura`; mensajes claros si faltan archivos.

**E14 · `FigureBuilder`.** *Depende de:* E12. Selección de caballo, asiento y ajustes sin GTK, probada sin interfaz.

**E15 · Ventana.** *Depende de:* E14 y G0. Sección **Caballo**, asiento por clic y arrastre reutilizando `mount_pick`, vista conjunta y colores de diagnóstico; textos es/en/pt. La vista previa rápida (`preview_parts`) no sirve para validar: la validación usa el `BuildResult` real. *No verificable en este entorno (sin GTK).*

### Fase 6 — Caso real, impresión y documentación

**E16 · Caso real.** Güemes + caballo real; comparar la vista previa con el STL en el slicer; comprobar que no se funden las articulaciones.
**E17 · Probeta.** *Bloqueo: impresión y calibre del usuario.* Probeta de separación silla–pelvis y de pared mínima; ajustar holgura solo con medidas.
**E18 · Documentación.** `tutorial_gui.md`, `manual_corto.md`, `PROGRESS.md`, con el caso reproducible y sus medidas.

---

## 10. Pruebas obligatorias

1. **No regresión:** receta sin caballo da la misma lista de piezas y geometría.
2. **Persistencia:** receta vieja carga sin campo ecuestre; receta nueva ida y vuelta.
3. **Compat:** cambiar de caballo no cambia `compat.recipe_key`.
4. **Escala:** la escala del caballo no altera pasadores ni holguras del jinete.
5. **Transformación:** matriz rígida; distancias internas invariantes; izquierda/derecha conservados.
6. **Silla:** una componente, geometría válida, pared mínima, sin flotar.
7. **Colisiones:** casos que pasan y casos que deben fallar.
8. **Tallado:** un caballo ancho se adapta con rebajes limitados; uno demasiado ancho se rechaza antes de atravesarse; el STL fuente queda idéntico.
9. **Exportación:** misma transformación global; apoyo en `z = 0`.
10. **Orientación:** el caballo no se invierte por la rutina de orientación.
11. **Portabilidad:** `tests/test_engine_portability.py` sigue pasando.
12. **i18n:** `tests/test_i18n.py` sigue pasando con los textos nuevos.
13. **Integración:** receta real con pelvis y piernas de Güemes y un caballo real.

No se marca una fase como terminada solo porque compila. Se corren las pruebas nuevas y las regresiones relevantes (`test_recipe_mount`, `test_figure_builder*`, `test_compat`, `test_engine_portability`, `test_i18n`).

---

## 11. Reglas de trabajo para Claude Free

1. **Una ficha por turno.** Leer la ficha, las funciones que cita y las pruebas relacionadas antes de editar.
2. **Cambios chicos.** Primero `equestrian.py` y el motor puro; `recipe.py`, `cli.py` y GUI solo cuando el motor esté probado.
3. **No tocar el flujo sin caballo.** Pruebas de equivalencia antes de modificar `run_armar()` o `pipeline.export()`.
4. **No inventar interfaces.** Confirmar nombres y firmas de `BuildResult`, `Assembled`, `PortFrame`, `Params` y `Recipe` en el `main` vigente.
5. **Registrar el estado en `PROGRESS.md`** al cerrar cada tarea: qué se hizo, pruebas corridas y su resultado, **qué no se verificó** y decisiones tomadas (formato «Current Focus / Todo List / Latest Blockers» que ya usa el archivo).
6. **Pruebas rojas heredadas:** decir si la falla es previa o nueva; nada de `skip` ni `except: pass` para taparlas.
7. **Primero el motor nativo.** Nada de Pyodide, Worker ni `web/`.
8. **Sin IA para detectar el lomo.** El asiento lo elige el usuario.
9. **Entregar el parche apenas esté** y después correr las pruebas, como ya se hace en los otros planes.

Contexto mínimo por tarea: `README.md`, el encabezado de `PROGRESS.md` (no cargarlo entero: pesa ≈525 kB), la ficha Eyy y los módulos y pruebas que cita.

---

## 12. Fuera del MVP

Caballos articulados o con pose; detección automática del lomo; figuras arbitrarias sin receta articulada válida; recorte *no acotado* del caballo; monturas ornamentales; color/textura del caballo; salida web; certificación de ciclos de articulación.

---

## 13. Riesgos y decisiones pendientes

### D-P1 · ¿Qué se hace si G0 falla? *(decide el usuario)*

Con las piernas reales (§1.4) G0 probablemente pasa: el tallado es chico. Aun así conviene dejar elegida la salida antes de E05, sin tocar el contrato de pose:

- **A. Elegir o preparar un caballo para este contrato:** cuello y cruz más anchos o un modelo con el hueco para las pantorrillas hecho en el propio modelo (la lección de §8). El motor solo mide y talla dentro de límites.
- **B. Mover el asiento** o cambiar la escala del caballo y aceptar una apariencia distinta.
- **C. Abrir una excepción a D4** con un perfil de pose para jinetes de caballo. Hoy está descartado; se lista para que la decisión quede escrita, no para implementarlo.

### Otros riesgos

| Riesgo | Tratamiento |
|---|---|
| Caballos de fuentes distintas con unidades y orientación distintas | Medida real y referencia de frente; guardar transformación y hash. |
| Malla de caballo abierta o no manifold | Reparar y verificar; bloquear booleanas si no es apta. |
| Booleanas lentas | Silla simple, ventana espacial y herramientas decimadas (1.3-2). |
| Orientación de impresión del caballo | MVP: orientación natural y traslado del conjunto a `z = 0`. |
| STL del caballo cambia | SHA-256; avisar. |
| Regresión de recetas viejas | Campos opcionales y pruebas de ida y vuelta. |
| Conflicto de nombres con la montura de armas | `equestrian`, `HorseSpec`, `SaddleBuild`, `cabalgadura`; `mount.py` queda para armas. |
| Ventana sin poder ejecutarse en las sesiones | Lógica sin GTK probada; ventana marcada «sin probar». |

---

## 14. MVP terminado cuando

- Se puede agregar y volver a elegir un caballo STL desde la biblioteca de la línea.
- Una receta válida con pelvis y piernas estándar se arma con el motor actual y se coloca con una matriz rígida sobre el caballo.
- El usuario define punto y dirección del asiento sin editar el STL.
- El programa talla el caballo y/o genera la silla, con los límites de §5 cumplidos.
- La validación detecta choques reales entre caballo, silla y jinete e informa holguras y paredes.
- El STL conjunto mantiene las posiciones relativas y las articulaciones del jinete.
- Las recetas sin caballo y sus exportaciones no cambian.
- Hay al menos un caso real documentado y revisado en un slicer; la validación geométrica no sustituye la prueba de impresión.

**Recomendación final:** empezar por **E00** con la receta de Güemes y el caballo definitivo (escala incluida), y mirar el resultado en pantalla: el asiento medido queda sobre la cruz y el tallado afina el cuello. Esa tabla decide si el plan sigue tal cual o por A, B o C (D-P1).
