# Plan: enganche de armas al cuerpo de la figura (montura generada)

**Versión:** 0.4 (borrador; revisado contra el código y las fotos de las piezas impresas) — 2026-11-20
**Repo:** `fschpp/action_figure_maker_stl`, sobre `main` (ubicar en `docs/PLAN_enganche_armas_cuerpo.md`)
**Tareas:** **M00–M17** (numeración propia, no continúa a T01–T48).
**Marcas:** **[C]** dicho por el cliente · **[V]** verificado leyendo el código o los archivos · **[I]** inferido, a validar · **[P]** pendiente de aclarar.
**Referencias de código:** números de línea de `main` (3ebce04). Pueden moverse; los nombres de función no.

## 0. Qué cambió respecto de la v0.3

| v0.3 decía | Lo que hay en el código | Consecuencia |
|---|---|---|
| Reusar `holguras_brazos` | **No existe** (0 coincidencias). Lo real: `Manifold.min_gap`, la intersección `a ^ b`, `posed_pieces`, `swept_offset`, `_validate_waist`, `_validate_chain`, `_validate_detent` **[V]** | §6.4 reescrito con funciones reales |
| Sumar la montura al huésped «antes de cortar», «como la mano» | La mano se pega a la **mitad +X**, que después se **espeja** (`pipeline.py` ≈353–366). Una montura de un solo lado puesta ahí saldría **en los dos lados** **[V]** | La montura se suma **después** del espejado y de la cintura (§6.2) |
| «La pelvis» como una pieza | Con `cintura` hay dos piezas (`torso` = pecho, `pelvis`); sin cintura todo es `torso` (≈429) **[V]** | `lugar: pelvis` se resuelve según la receta |
| Compatible con «sin soportes» (un renglón) | `flat_for_print` corta todo lo que no sea brazo o cabeza con un plano a `depth` sobre `body_base_z`, y `_extend_thighs` talla la pelvis **[V]** | Riesgo real, tarea propia (M06) |
| Guardar `monturas` solo en la receta | `recipe_key` **no** incluye `accesorios` y sí `ajustes` y `params` **[V]** | Si la montura no entra en la clave, las certificaciones de `compat.json` quedan mal (§6.3) |
| «Se reusa el picking y el fantasma del pasador» | `Picker` existe (controller, catalog_manager); `ghost_meshes` es **del pasador**. No verifiqué que la ventana de armado tenga vista 3D **[P]** | §7 corregido |
| Regresión «byte a byte» | Lo que existe es `tests/test_golden.py` (tolerancia 0,01 % en volumen) **[V]** | §8 usa los dorados |
| Nada sobre el espacio disponible | En la figura de ejemplo, entre el flanco de la pelvis y el brazo colgando hay **≈ 3–4 mm** (§5) | Hallazgo central de esta versión |

## 1. Qué se quiere

1. Dado un **arma** (STL o accesorio catalogado) y una **parte del cuerpo**, que el programa **genere la geometría del enganche** para llevarla. **[C]**
2. El usuario **dice dónde** (zona y, si se puede, punto sobre la superficie) y el programa resuelve la forma. **[C]**
3. Caso de partida: la espada de Link, hoy con un enganche **hecho a mano** midiendo la espada. **[C]**
4. **Primer objetivo de uso: el costado de la pelvis**, como en un cinturón. Otras partes del cuerpo, como ampliación. **[C]**
5. La espada **se saca hacia arriba**. **[C]**
6. La **correa diagonal es solo pintura**: no entra en la geometría. **[C]**
7. La figura armada mide **10 cm** de alto. **[C]**

## 2. Punto de partida

- `weapon.py`: importa un STL, lo escala, le corta el mango por un plano de 3 puntos y le pone la empuñadura de la línea (`WeaponSpec`, `grip_stub`). **Marco del accesorio:** eje de la empuñadura = +X, origen en el centro de la sección de corte, el arma queda en x < 0. **[V]**
- Los accesorios viajan en `Recipe.accesorios` (`id@versión`) y salen como **STL aparte** en pose de impresión (`print_pose`). No tienen relación geométrica con el cuerpo. **[V]**
- Las piezas de catálogo se acoplan por **puertos** (`ports.py`); no hay puerto de «montura». **[V]**
- No existe soporte para enfundar o colgar armas del cuerpo. **[V]**
- `props.make_prop` es una espada de prueba con medidas del motor: hoja 34 × 4 × 0,8 mm, guarda 10 × 2 mm. Sirve de referencia de orden de magnitud para el ensayo sintético. **[V]**
- `weapon.suggest_length` propone un arma de 0,40 × la altura de la figura (40 mm para 100 mm). La espada de Link es probablemente más larga: **[P]** (pregunta 1).

## 3. Lo que muestran los archivos y las fotos

- **`espada.stl`**: malla cerrada, 3 componentes, ~408 000 triángulos; caja orientada 0,069 × 0,229 × 0,864 (largo ≈ 0,86, ancho de guarda ≈ 0,23). El 0,069 puede ser la guarda o la empuñadura, **no** el espesor de la hoja. **[V]/[I]**
- **`torso_atras_v4.stl`**: malla cerrada, 1 componente, ~95 000 triángulos; orificio circular a media espalda (el pivote) y franja rectangular rehundida arriba. Es una versión **anterior**; no sirve de referencia exacta. **[V]/[C]**
- **Sin unidades en mm** en ninguno de los dos archivos. La escala sale de **una medida real** (largo de la espada impresa, o la altura de 100 mm de la figura). **[C]/[P]**
- **Piezas impresas (fotos del 2026-11-20):** (a) espalda del torso con el orificio del pivote y un **puente rectangular** parado sobre la superficie; (b) **placa de prueba** con el mismo puente sobre base plana; (c) la **espada** (hoja larga y delgada con guarda, empuñadura azul). **[V]** (por las fotos)
- El enganche es un **puente rectangular de paredes finas**, abertura más ancha que alta (hoja plana apoyada sobre la superficie), no un aro redondo. **[I]**
- Los **STL del enganche final se perdieron**: quedan las fotos. **[C]**
- En las fotos la espada va en diagonal sobre la espalda, **empuñadura arriba**, punta abajo, y pasa por un lazo en la parte alta. **[I]**

## 4. Lo que hay que respetar del motor

Mirado en `pipeline.py`, `waist.py`, `recipe.py`, `compat.py`:

1. **Orden de `build`:** mitad +X → miembros y tallados de barrido → `pieces["torso"] = union(mitad, espejo)` (≈366) → compactar/decimar → lengüetas de clic → cintura (≈429: `pieces["torso"], pieces[PELVIS_NAME] = waist_build.upper, waist_build.lower`). **[V]**
2. **Lados:** `_izq` = +X (lado de trabajo), `_der` = −X, por espejo (`S.mirror_x`) o por el lado derecho construido aparte en figuras asimétricas. **[V]**
3. **`body_base_z`** (punto más bajo de torso/pelvis) fija el plano de corte de `flat_for_print`. **[V]**
4. **`body_ref`** une torso y pelvis; contra eso se miden los miembros. **[V]**
5. **Validaciones** siguen el patrón `_validate_xxx(res, p, out)` que agrega a `out["problems"]`, `out["avisos"]`, `out["avisos_pasador"]`; la holgura se mide con `a.min_gap(b, radio)` y el choque con `(a ^ b).volume()`; criterio vigente de holgura: `gap < 0.9 · p.clearance` es problema (`_validate_waist`). **[V]**
6. **`compat.record(asm, val, …)`** guarda el resultado de `validate` bajo la clave `recipe_key`. **[V]**
7. Hay **un puerto web (Pyodide)**: `webapi.py`, `PLAN_port_pyodide.md`, `tests/test_engine_portability.py`, y `S.can_decimate()` para degradar sin `fast_simplification`. **[V]** (qué exige exactamente la prueba de portabilidad: **[P]**).

## 5. Viabilidad geométrica en el costado de la pelvis

**Datos medidos** sobre `examples/guemes_segmentado_lq_fixed_resized.stl` (114 mm de alto, la figura de ejemplo, **no** Link; la pelvis es la misma pieza de la línea **[C]**, pero los brazos de Link pueden diferir **[P]**). Secciones horizontales, lado +X; `x` = extremos de la sección:

| z (mm) | flanco del torso/pelvis (x máx) | brazo colgando (x mín) | **hueco** | y del flanco | y del brazo |
|---|---|---|---|---|---|
| 52 | 12,6 | 16,5 | **3,9** | −6,7 … 9,3 | −2,5 … 4,3 |
| 58 | 11,7 | 15,9 | **4,2** | −8,2 … 9,2 | −0,9 … 6,4 |
| 64 | 11,0 | 14,5 | **3,5** | −9,0 … 8,1 | −0,2 … 8,7 |
| 70 | 10,4 | 13,1 | **2,7** | −9,1 … 7,4 | 0,4 … 9,9 |
| 76 | 10,9 | 11,7 | **0,8** | −9,8 … 8,7 | −0,6 … 9,1 |

Por debajo de z ≈ 50 solo está la pierna (x hasta 13–13,5; y ≈ 0 … 9 en z = 10–22). A 100 mm de figura, multiplicar por ≈ 0,88.

**Presupuesto radial de un puente** (hoja plana sobre la superficie), con números a confirmar:

| Concepto | mm |
|---|---|
| Hueco = espesor de hoja `t` + 2 × holgura de hoja (0,25) | `t` + 0,5 (con `t` = 0,8 → 1,3) |
| Techo del puente (≥ `min_wall` = 1,2) | 1,2 |
| **Altura sobre el flanco** | **≈ 2,5** |
| Holgura al brazo exigida (0,9 × `clearance` 0,5) | 0,45 |
| **Hueco necesario en total** | **≈ 3,0** |

**Lectura [I]:**
- Entre z ≈ 50 y 66 el hueco (3,5–4,2) **alcanza con ≈ 0,5–1 mm de margen**. Por encima de z ≈ 70 **no alcanza**. La montura solo puede ir en esa franja de ≈ 16 mm.
- La espada, para salir hacia arriba, tiene que **pasar lejos del hombro**. En z = 70–76 el brazo casi toca el torso: por ahí no pasa una guarda de 2 mm de espesor.
- Hacia abajo, la hoja se topa con la **pierna** (x hasta 13,5, y ≥ 0 en z < 40). La solución geométrica es llevar la espada a la **parte trasera del flanco (y < ≈ −1)**, donde no hay brazo ni pierna, e **inclinarla hacia atrás**. Es coherente con la espada en diagonal sobre la espalda de las fotos. («Atrás» = −y es una inferencia: hay un componente aislado en y = −9,4 … −5,2 a z = 94, detrás de la cabeza. **[P]** confirmar la orientación.)
- **Una espada larga no entra colgando recta**: con el puente a z ≈ 60 y la guarda apoyada arriba, una hoja de más de ≈ 55 mm llega al suelo. Con espada larga, la inclinación hacia atrás no es opcional.

Esto convierte la pregunta 2 (de qué lado y con qué inclinación) en la primera a cerrar, y justifica una herramienta que muestre el espacio libre antes de armar (M08).

## 6. Diseño

**La montura es geometría derivada del arma y de un anclaje.** Se **genera al armar** y se suma a la pieza del cuerpo que corresponda. No es una pieza de catálogo nueva (esa es la opción B, §6.5).

### 6.1 Datos de entrada (`MountSpec`)

| Campo | Significado |
|---|---|
| `arma` | `id@versión` de un accesorio **que figure en `Recipe.accesorios`** (se valida) |
| `lugar` | `pelvis`, `torso`, `brazo`, `pierna`, `cabeza` |
| `lado` | `izq` (+X) o `der` (−X). La montura se construye en +X y, si es `der`, se espeja |
| `ancla` | punto sobre la superficie de la pieza, en su marco |
| `direccion` | eje del arma (empuñadura → punta) en el marco de la pieza; define la inclinación |
| `tipo` | `lazo` (puente/aro por el que pasa la hoja), `clip`, `cuna` |
| `holgura` | juego hoja–hueco (mm), por defecto 0,25 |
| `pared` | espesor del puente (mm), por defecto `p.min_wall` (1,2) |
| `estaciones` | 1 o 2 puentes a lo largo de la hoja |

**Defectos como constantes de `mount.py`, no como campos de `Params`:** ver §6.3.

### 6.2 Dónde y cuándo se suma (en `pipeline.build`)

1. **Después** de `pieces["torso"] = union(mitad, espejo)` y de la cintura. Nunca sobre `torso_half`: se duplicaría en ambos lados.
2. **Resolver `lugar: pelvis`:** `pieces[PELVIS_NAME]` si hay cintura; si no, `pieces["torso"]`.
3. **Después de decimar** (`_decimate_keeping_clearance`) para que la decimación no deforme el puente, y **después de los tallados de barrido** (si no, `_carve_torso_for_arm` lo comería).
4. **Hueco de la hoja:** restar el arma ensanchada de la montura ya unida al cuerpo, así el hueco queda exacto aunque el pie penetre la superficie.
5. Guardar en `BuildResult.mounts[nombre]` (nuevo campo, como `waist`/`detent`): sólido de la montura, pieza huésped, pose del arma, avisos. En figuras asimétricas, también en el `BuildResult` del lado derecho.
6. **Propuesta [I]:** conservar además la pieza huésped **sin** montura (`BuildResult.host_bare`), para `body_base_z` y `flat_for_print` (§6.4).

### 6.3 Receta y clave de compatibilidad

- **Dónde:** `Recipe.ajustes["monturas"]` (lista de `MountSpec.to_dict()`), con `"monturas"` agregado a `AJUSTES` y su validación en `problems()`. **Razón:** `ajustes` ya entra en `recipe_key`, así que cambiar una montura invalida la certificación sin tocar `compat.py`. **[I]**
- **Alternativa descartada por riesgo:** un campo propio tipo `accesorios`. `accesorios` **no** está en la clave (no cambia los STL); copiar ese patrón dejaría **certificaciones viejas válidas sobre una pelvis que cambió**. Si se prefiere el campo propio, hay que agregarlo a `recipe_key` solo-si-hay (como `contrato`) y probarlo.
- **No agregar campos a `Params`.** `recipe_key` hashea `recipe_params(...).to_dict()`; si `to_dict` vuelca todos los campos, cada figura ya certificada quedaría «vencida» por un parámetro que no usa. **[I]** (comprobar `Params.to_dict`). Los defectos de holgura/pared viven en `mount.py`, y la calibración real (§M01) se guarda **por montura**.
- Prueba obligatoria: la clave de una receta sin `monturas` es **idéntica** a la de hoy; con montura cambia, y cambia si se mueve `ancla`.

### 6.4 Verificaciones (con las funciones reales)

`_validate_mount(res, p, out)` con el mismo patrón que `_validate_waist`. Resultado en `res.report["montura"]` y avisos en el formato de `validate`.

| Verificación | Cómo |
|---|---|
| **Extracción libre** | Trasladar el arma entera a lo largo de su eje, de a ~0,5 mm, hasta que la **punta libra el puente** (distancia = tramo de hoja bajo el puente); en cada paso `(arma ^ (cuerpo ∪ miembros)).volume() ≈ 0`. Detecta guarda/empuñadura chocando con el brazo o el hombro |
| **Holgura a miembros** | `montura.min_gap(miembro, 5.0) ≥ 0,9 · p.clearance` (criterio de la cintura), para: brazo colgando; brazo girando su recorrido (`rotate_about_axis` sobre `res.motion[n]`, paso como `_validate_detent`); **pierna del mismo lado en todo el giro de cadera** 0 → `sit_angle` (`posed_pieces`, paso 15° como `_validate_chain`); mano |
| **Retención** | La guarda apoya sobre el puente: ancho y espesor de la guarda **mayores** que la abertura (+ pared). Si no, avisar: «la espada se desliza» |
| **Estabilidad** | El centro de gravedad del arma enfundada cae dentro del apoyo (geometría, no simulación) |
| **Una sola pieza** | La pelvis/torso con montura sigue con 1 componente (la validación actual ya lo exige, ≈703–711) |
| **Imprimible** | Paredes ≥ `p.min_wall`; sin voladizos > 45° en la pose de impresión con `orient.overhang_mask` sobre la montura; **inclinación del eje del arma respecto de la vertical de impresión ≤ 45°** |
| **Mano libre** | No se toca el puño ni el agarre (el accesorio sigue sirviendo en la mano) |

**Imprimibilidad, nota [I]:** la figura se imprime en pose sentada, torso vertical. Con el eje del arma casi vertical, el túnel del puente es una extrusión vertical: sin voladizos. Cuanto más se incline hacia atrás, más voladizo; a 45° es el límite.

### 6.5 «Sin soportes», color, web, catálogo

- **`flat_for_print`:** el plano sale de `body_base_z`, y `_extend_thighs` talla la pelvis. Reglas propuestas: (1) `body_base_z` se calcula **sin** la montura (`host_bare`); (2) la montura se **vuelve a unir al final**; (3) si queda por debajo del plano, aviso «la montura queda cortada por el plano»; (4) si el relleno de cadera talla su pie, aviso. Con los brazos girados hacia adelante, el flanco queda libre: se verifica.
- **Color (T36/T43):** la geometría nueva no tiene fuente de color. Hay que comprobar qué hace el relleno de `colorbake` (`test_colorbake_fill.py`) y fijar una regla: que herede el color de la superficie más cercana de la pelvis, o un rol de paleta `montura`. **[P]**
- **Web (Pyodide):** `mount.py` sin dependencias nuevas; reusar `solids`/`trimesh`. `swept_offset` usa `scipy` y `skimage`: **evitarlo** para la extracción (un barrido por traslación en pasos no lo necesita).
- **Opción B (guardar como pieza):** torso con el enganche incluido, como el `torso_atras_v4` hecho a mano. Obliga a una versión por combinación arma × torso; se ofrece **después** (M17).

## 7. Interfaz

En la ventana de armado por catálogo, un bloque plegable **«Armas en el cuerpo»**:

1. Elegir el accesorio (de los de `Recipe.accesorios` con `agarre`), el lado y el tipo.
2. **Clic sobre la figura** para el ancla. `Picker(mesh)` y `ray_first_hit` ya existen (`gui/picking.py`) y el controlador los usa. `figure_window` **no** tiene visor 3D vivo: la vista previa es una imagen que dibuja `PreviewRenderer` con `preview_camera`; el clic se resuelve con esa misma cámara y `widget_to_image` (`gui/mount_pick.py`) **[V]**.
3. Vista previa del arma y de la montura en otro color; **rojo** si no sale, choca o no imprime.
4. **Mapa de zonas posibles** (M08): colorea el flanco según el hueco libre a miembros, para que se vea dónde cabe antes de elegir.
5. Valores: holgura, pared, inclinación. Estado con los avisos de §6.4.

La lógica va en un módulo propio sin GTK, probado sin ventana (como `palette_editor` o `roles_wizard`). Recordar la limitación R9 del repo: sin GTK no se prueba la ventana real.

## 8. Tareas

### Fase 0 — Datos reales (sin código)
- **M00** Con calibre: puente (ancho × alto × largo × pared), hoja (ancho × espesor), guarda (ancho × espesor), largo total, largo del tramo bajo el puente. Anotar si la hoja entraba sin forzar en la placa de prueba. Sale la tabla de holguras reales. *Hecha cuando:* los valores están en §9.

### Fase 1 — Motor (sin interfaz)
- **M01** `mount.py`: `MountSpec` (`to_dict`/`from_dict`), `MountError`, constantes de defecto. *Prueba:* ida y vuelta, campos desconocidos rechazados.
- **M02** Escala y pose de transporte: escala por medida real (`WeaponSpec.scale`), `ancla` + `direccion` → matriz en el marco de la pieza, trabajando en el **marco del accesorio** (eje +X).
- **M03** Sección transversal de la hoja y de la guarda en 1 o 2 estaciones (`trimesh.section`).
- **M04** Generar el `lazo` (puente rectangular o aro): pie conformado a la superficie, techo, resta del arma ensanchada. La abertura sigue la sección de la hoja.
- **M05** Integración en `build` según §6.2: `BuildResult.mounts`, `lado`, resolución de `lugar`, espejado para `der`. *Primer caso:* costado de la pelvis de Güemes.
- **M06** Compatibilidad con `flat_for_print` según §6.5 (`host_bare`, plano, reunión al final, avisos). *Prueba:* figura con `flat` y montura; la montura sigue completa. **Hecha** (`BuildResult.host_bare`, `pipeline._reattach_mounts`; si el plano corta el puente se lo corta y se avisa).
- **M07** `_validate_mount`: extracción, holguras, retención, estabilidad, imprimibilidad, componentes (§6.4).
- **M08** Mapa de espacio libre del flanco (también sirve a la GUI). **Hecha** (`mount_map.py`, `pipeline.mount_flank_map`, `afm montura-mapa`).
- **M09** Probeta de la montura: `probes.probe_files(tipo="montura")`, el puente solo sobre base plana con varias holguras en la misma placa, siguiendo el estilo de las probetas del clic (`PROTOCOLO_probetas_clic.md`, `afm probe --clearances`). Ya hay una hecha a mano que sirve de referencia. La holgura elegida se guarda en la `MountSpec`.

### Fase 2 — Receta y CLI
- **M10** `Recipe.ajustes["monturas"]`, validación en `problems()`, coherencia con `accesorios`, **clave de compat** (§6.3). Se guarda solo si hay. **Hecha** (`Recipe.mount_problems/mount_specs/key_ajustes`, `recipe.load_mounts`, `assemble` pasa `mounts=` a `build`; la clave suma el sha256 de cada arma).
- **M11** CLI: `afm armar` ya lee la receta, así que **no** hace falta la sintaxis `--montura ARMA:LUGAR:x,y,z:dx,dy,dz` de la v0.3. Se agrega `afm probe --tipo montura` y, opcional, `--montura ARCHIVO.json`.
- **M12** Informe: la montura y sus avisos en `info["construccion"]` y en la lista de piezas; el nivel de `compat` refleja los problemas de la montura. **Hecha** (`mount.report_by_mount/level_of`, `pipeline.mounts_report`, sección «Monturas» en la lista de piezas, `monturas` en `compat.json`).

### Fase 3 — Interfaz
- **M13** Bloque «Armas en el cuerpo» en `figure_window`. **Hecha** (lógica en `FigureBuilder`, probada; el bloque de la ventana no se pudo ejecutar sin GTK).
- **M14** Ancla por clic y orientación con el ratón (según §7, punto 2). *Hecha:* `gui/mount_pick.py` + botones «Ancla» y «Dirección» en la ventana; la profundidad de la dirección sale en el plano de la vista.
- **M15** Vista previa, mapa de zonas y estado con los avisos. *Hecha:* `gui/mount_preview.py` + «Ver arma y montura» y «Mapa de zonas» en la ventana; el mapa es con la figura de pie.

### Fase 4 — Más tipos y catálogo
- **M16** Tipos `clip` y `cuna`.
- **M17** «Guardar como pieza» (opción B).

**Orden:** M00 → M01–M04 → M05 → M07 → M09 → M10–M12 → M06 y M08 (en paralelo a partir de M05) → Fase 3. No depende de T49–T51 (paleta).

## 9. Pruebas

- **Sintéticas:** torso = caja, arma = cilindro con guarda (o `make_prop`). El hueco del puente es el esperado, el arma sale por su eje, y poner otra pieza en el camino dispara el aviso. Caso `lado: der` (espejo) y caso sin cintura / con cintura.
- **Caso real:** armar a Link con `espada.stl` y la montura al costado de la pelvis. **Aceptación:** (1) la espada se saca hacia arriba sin chocar; (2) con el brazo colgando y la pierna en todo su giro no hay contacto (`min_gap` ≥ 0,45 mm); (3) imprime sin soportes extra; (4) con `flat` la montura sigue completa. **No hay STL de referencia** (se perdió): se compara con las fotos y con las medidas de M00. Con `skipif` si falta el archivo.
- **Regresión:** una figura sin `monturas` da la **misma `recipe_key`** y pasa los dorados (`tests/test_golden.py`, `AFM_GOLDEN_FULL=1`; tolerancia 0,01 % en volumen). «Byte a byte» solo si ya hoy es determinista. **[P]**
- **Clave:** cambiar `ancla`, `holgura` o `lado` cambia `recipe_key`.
- **Portabilidad:** pasa `tests/test_engine_portability.py`.

## 10. Riesgos

- **Falta de espacio (el mayor):** hueco de 3–4 mm entre flanco y brazo en la figura de ejemplo; solo cabe entre z ≈ 50 y 66, hacia la parte trasera. Si los brazos de Link son más gruesos, puede no caber. Mitigación: M00 y M08 antes de programar el resto.
- **Hoja gruesa:** con `t` ≥ 1,2 mm el presupuesto radial sube a ≈ 3,5 mm y deja de caber. Dato a medir (M00).
- **Armas con formas difíciles** (hojas curvas, guardas grandes): una estación no las representa. Dos estaciones y tipo `clip`.
- **Espada larga:** llega al suelo si cuelga recta (§5). Exige inclinación y verificación contra la pierna.
- **Certificaciones:** si la montura no entra en `recipe_key`, quedan certificaciones viejas válidas sobre una pelvis cambiada.
- **`flat_for_print`:** plano y relleno de cadera pueden cortar o tallar la montura (M06).
- **Pared mínima y holgura de impresión** son números de modelo: hace falta probar imprimiendo y ajustar con calibre.
- **Peso del archivo:** `espada.stl` tiene ~408 000 triángulos; se reduce al importar.
- **Color:** geometría nueva sin fuente de color (§6.5).

## 11. Decisiones y preguntas abiertas

**Resueltas por el cliente (2026-11-20):**
1. El STL del enganche final se perdió; solo quedan las fotos. `torso_atras_v4.stl` es una versión previa.
2. La figura armada de Link mide 10 cm de alto.
3. La espada se saca hacia arriba.
4. La correa diagonal es solo pintura.
5. Se puede fijar en cualquier lugar; el primero es al costado de la pelvis.

**Propuestas mías (fáciles de cambiar):**
- `monturas` dentro de `ajustes` (clave gratis) en vez de un campo propio (§6.3).
- Sin campos nuevos en `Params` (§6.3).
- La montura se suma **después** del espejado y de la cintura, y se guarda `host_bare` (§6.2).
- Extracción por traslación en pasos, no por `swept_offset` (portabilidad).
- Dejar fuera la sintaxis `--montura …` de la v0.3 (M11).

**Abiertas:**
1. **[P]** ¿Cuánto mide la espada impresa de punta a punta, en mm? Sin eso no hay escala. Con calibre, además: **abertura del puente** (ancho × alto), **sección de la hoja** (ancho × espesor) y **guarda** (M00).
2. **[P]** ¿En qué lado de la pelvis (izquierdo o derecho) va y **hacia dónde apunta la punta**: recta hacia abajo, hacia atrás? Según §5, hacia abajo recta choca con la pierna y la franja útil está **detrás del brazo**. Define `direccion`.
3. **[P]** ¿La espada entraba por el puente de la placa de prueba sin forzar, o quedó floja o apretada? Define la holgura por defecto.
4. **[P]** ¿Qué sostiene la espada en el puente de las fotos: la **guarda apoyada arriba** o solo el rozamiento? Define la retención (§6.4).
5. **[P]** ¿El arma tiene que poder **sacarse y ponerse con la mano de la figura** (el brazo debe alcanzar la empuñadura) o solo sostenerse?
6. **[P]** ¿Los brazos de Link son los mismos que los de la figura de ejemplo (Güemes)? Si no, hay que repetir las secciones de §5 con su STL.
7. **[P]** ¿«Atrás» es −y en el marco de la figura? (§5, inferido.)
