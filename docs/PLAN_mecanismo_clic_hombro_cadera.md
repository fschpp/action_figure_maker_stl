# Plan: mecanismo de clic para hombros y caderas (print-in-place, PLA)

_2026-10-18. Solo plan: no se implementó código. Surge de una entrevista cliente/analista. Se apoya en el sistema actual de planos + pasador de hongo (`planes.py`, `limbs.py`, `pins.py`, `pipeline.py`) y en `PLAN_articulaciones_rodilla_codo_cadera.md`. Los nombres de módulos salen de ese plan: confirmarlos contra el código antes de empezar._

Convención de este documento: **[cliente]** = lo dijo el cliente en la entrevista; **[estimación]** = valor o conclusión del analista, a calibrar con probetas; **[supuesto]** = decisión tomada sin confirmación, listada en §7.

## 1. Resultado de la entrevista

| Tema | Respuesta del cliente |
|---|---|
| Qué debe hacer | Mantener la pose con **posiciones discretas (clic)**, no solo fricción |
| Grados de libertad | **Un solo eje** por articulación (bisagra, un plano) |
| Hombro | Gira **libre 360°** en el plano y hace clic en **4 posiciones**: abajo, adelante, arriba, atrás (cada 90°) |
| Cadera | **2 posiciones fijas**: abajo (0°) y adelante (90°, sentada) |
| Durabilidad | **Uso intenso** (miles de cambios de pose); no debe desgastarse ni aflojarse |
| Fabricación | **PLA, FDM, print-in-place, sin pieza extra** (ni resortes, ni goma, ni tornillos) |
| Impresión | Boquilla **0,4 a 0,8 mm**, capa **0,24 mm** |
| Tamaño | Figura de **13 cm** (no 10 cm) |
| Pose de impresión | **Sentada** (cadera a 90°, piernas hacia adelante), **brazos ligeramente inclinados** |

## 2. Análisis: qué impone el requisito

1. **Sin resorte, el resorte es una lengüeta impresa.** Un clic print-in-place es una lengüeta en voladizo con una punta que cae en muescas de un disco. Toda la durabilidad depende de cuánto se flexa esa lengüeta.
2. **Límite de deformación.** Para un voladizo recto, ε ≈ 3·t·δ / (2·L²) (t espesor, δ flexión de la punta, L largo). Para miles de ciclos en PLA se fija **ε ≤ 1 %** [estimación]. Ejemplos con δ = 0,5 mm: boquilla 0,4 → t = 1,2, L = 10 → 0,9 %; boquilla 0,8 → t = 1,6, L = 12 → 0,83 %. Una lengüeta corta y gruesa (por ejemplo L = 6) se pasa del límite: hay que darle largo.
3. **Lengüeta relajada en la muesca.** En el clic la lengüeta no debe estar precargada: solo se flexa al pasar de una muesca a otra. Una precarga permanente hace que el PLA ceda (creep) y el clic se afloje con los meses.
4. **Dirección de las capas.** La tensión de flexión va a lo largo de la lengüeta, y el PLA es débil entre capas. La lengüeta tiene que quedar **horizontal al imprimir** (larga en Y, delgada en Z). Por eso las lengüetas van en el **padre** (fijas, arriba y abajo del eje) y no en la pieza que gira: si giraran con el brazo, a veces quedarían verticales y se romperían entre capas.
5. **El juego de impresión se convierte en juego angular.** Como la pieza se imprime ya ensamblada, entre la punta y la muesca queda una holgura `detent_gap`. Eso da una banda muerta alrededor de cada clic de ±(gap·tanα)/R; con gap 0,3, flanco 30° y R 4 mm son ≈ ±2–3° [estimación]. Es aceptable para una figura, pero hay que medirlo y no se puede eliminar del todo.
6. **La pose de impresión tiene que ser un clic.** Si la punta está en el flanco o sobre el borde del disco al imprimir, se fusiona con él. De acá sale el tema de los brazos inclinados (ver §3.4).
7. **Hombro 360° = sin tope mecánico.** La cavidad del padre ya no se arma con un barrido corto. Hay que calcular cuánto del giro es realmente libre (cabeza, cuello, torso y otras piezas pueden estorbar) y descartar clics que caigan fuera de ese rango.

## 3. Diseño propuesto

### 3.1 Estructura (misma para hombro y cadera)

- **Se mantiene el pasador de hongo** actual como retención axial: el brazo o la pierna no sale por tirón.
- **Disco de clic (`detent_disc`)**, nuevo, en la pieza que gira (brazo/pierna): cilindro de eje = `contact_axis`, Ø `disc_d`, alto `disc_h`, ubicado entre la cara de contacto y el vástago del hongo. En su borde lleva `n_notches` muescas redondeadas (V suave, flancos α).
- **Bolsillo en el padre (torso/pelvis)**: cilindro de Ø `disc_d` + 2·`clearance`, obtenido con la cavidad barrida que ya existe.
- **Dos lengüetas en el padre**, una arriba y otra abajo del bolsillo (±Z del mundo según la orientación de impresión): voladizo recto, largo en Y, flexa en Z. Cada una nace de la pared (raíz a `finger_len` del eje), tiene punta redondeada que apunta al centro y está separada del cuerpo por una ranura `slit_w` por el lado interno, por debajo y en la punta, y por una ranura más ancha `flex_gap` por arriba (donde la lengüeta se levanta al pasar sobre el disco). Al estar enfrentadas, las fuerzas sobre el disco se compensan (lo centran).
- **Orientación automática**: el motor calcula arriba/abajo a partir de la orientación de impresión (hoy `Session.set_front`/`orient`), de modo que las lengüetas queden siempre horizontales.
- **Espejado** izquierda/derecha: ya existe; las lengüetas arriba/abajo no cambian.

### 3.2 Hombro

- `n_notches = 4`, a 90° entre sí; sin tope (giro libre).
- Barrido de 360° para la cavidad. Riesgo: si la parte interna del brazo cae dentro de la silueta del torso, el barrido talla un disco entero. La cara de contacto del hombro debe ser plana y lateral, y la validación (§6) reporta el rango libre real.
- Si algún clic cae fuera del rango libre, se descarta y se avisa.

### 3.3 Cadera

- Mismo disco con `n_notches = 4`, pero con **rango 0 a 90°**: solo se usan las muescas de 0° y de 90°. El tope sale de dejar sin tallar el material del padre más allá de `hip_range` + `stop_margin` (mecanismo que el motor ya usa en la cadera).
- La **pose de impresión sentada es el clic de 90°**: no hay conflicto de fase.
- La pared detrás de la cabeza y la separación entre las cabezas izquierda/derecha se validan como en el plan anterior (pared ≥ `min_wall` 1,2 mm).

### 3.4 Brazos inclinados vs. clics nominales [supuesto, decidir]

Como la pose de impresión tiene que ser un clic (§2.6), y el cliente imprime con los brazos ligeramente inclinados, no pueden coexistir los clics en 0°/90°/180°/270° exactos y el brazo impreso inclinado. Opciones:

- **A (por defecto).** Las muescas se calculan desde la pose de impresión: `phase = ángulo de impresión del brazo`. Los 4 clics quedan en `phase + k·90°` (por ejemplo 10°, 100°, 190°, 280°). El «abajo» queda levemente separado del cuerpo, lo que suele verse natural.
- **B.** Imprimir los brazos exactamente colgando (0°), con la holgura lateral contra el torso. Los clics quedan nominales.

### 3.5 Parámetros nuevos (`DetentSpec`, mm absolutos, no escalar con la figura)

Valores iniciales para 13 cm, **[estimación]**, a confirmar con las probetas:

| Parámetro | Valor inicial | Nota |
|---|---|---|
| `n_notches` | 4 | hombro 4; cadera 4 (usa 2) |
| `disc_d` / `disc_h` | 8,0 / 2,0 | cadera puede usar el Ø de cabeza de la figura de referencia (9,25) |
| `notch_depth` | 0,8 | flexión de punta δ = `notch_depth` − `detent_gap` = 0,5 |
| `flank_deg` (α) | 30° | más empinado = clic más firme y más esfuerzo |
| `tip_r` | 0,7 | punta redondeada: sin aristas que se desgasten |
| `detent_gap` | 0,30 | holgura radial de impresión punta/muesca; define el juego angular |
| `slit_w` | 0,45 | ranura lateral, inferior y de punta; igual a `clearance` radial para no fusionarse |
| `flex_gap` | 1,0 | ranura superior = flexión δ (0,5) + `slit_w`; si fuera solo 0,45 la lengüeta chocaría contra el cuerpo al flexar (detectado al dibujar las vistas) |
| `finger_t` / `finger_len` | 1,2 / 10 (boquilla 0,4); 1,6 / 12 (boquilla 0,8) | cumplen ε ≤ `max_strain` |
| `finger_h` (largo en X) | = `disc_h` | |
| `max_strain` | 0,01 | validación |
| `min_wall` | 1,2 | ya existe |
| `hold_torque_min` | 10 N·mm | el peso del brazo ronda 1 N·mm [estimación]; confirmar con medición |
| `move_torque_max` | 60 N·mm | que se mueva con los dedos sin forzar la figura [estimación] |

Se guardan como **perfiles de línea** `0.4` y `0.8`, porque las lengüetas cambian con la boquilla. El motor toma el perfil desde un parámetro `nozzle`.

## 4. Qué tiene el motor y qué falta

Tiene: planos, cara de contacto, `mushroom_pin`, cavidad `dilate(miembro + pasador, clearance)` barrida por ángulos, pose sentada, espejado, `validate`, probetas, colocación asistida del pasador.

Falta (código nuevo):
1. `DetentSpec` y el generador del disco con muescas (`detent_disc`).
2. Generador de lengüetas + ranuras en el padre (`make_detent_fingers`), orientado por la dirección «arriba» de impresión.
3. Cálculo de la fase de las muescas desde la pose de impresión.
4. Barrido de 360° y cálculo del rango libre real (hoy el barrido es parcial).
5. Validaciones nuevas: deformación, pared alrededor de las ranuras, clic en la pose de impresión, rango libre, juego angular.
6. Selector de mecanismo por articulación (`mushroom` o `detent`) en `Params`/`Project`.
7. Probetas de clic y banco de ciclos.

## 5. Fases

**Fase 0: probeta de clic, antes de tocar el motor (1–2 días).** En `probes.py`: disco + bolsillo + 2 lengüetas, en una grilla de 3×3 con `notch_depth` × `finger_t` × `detent_gap`, para boquilla 0,4 y 0,8. Imprimir y medir: ¿se mueve?, ¿se fusiona?, ¿hace clic?, ¿cuánto juego angular queda? Es la fase que más riesgo elimina.

**Fase 1: modelo de datos.** `DetentSpec` en `joints.py`; campos en `Params`/`PARAM_FIELDS`; `mechanism` por articulación; perfiles de línea 0,4/0,8; carga y guardado (los `.proyecto` viejos siguen cargando con `mushroom`).

**Fase 2: motor.** `detent_disc`, `make_detent_fingers`, cálculo de fase y de «arriba», integración en `pipeline.build`: bolsillo + lengüetas en el padre, disco unido al hongo del hijo.

**Fase 3: cadera.** Rango 0–90°, tope, pose sentada a 90° = clic. Probar primero acá (rango y fase más simples).

**Fase 4: hombro.** Barrido 360°, rango libre real, descarte de clics inalcanzables, fase desde el ángulo de impresión del brazo (A o B de §3.4).

**Fase 5: validación.** Ver §6: tests automáticos en `validate` y en `tests/`.

**Fase 6: GUI.** Selector de mecanismo por articulación, campos de `DetentSpec`, vista previa del disco y las lengüetas, ángulo de impresión del brazo, aviso de clics descartados.

**Fase 7: desgaste.** Banco de ciclos (manual o con un servo sencillo): 5.000 ciclos por probeta, medir el par de retención al inicio, a 1.000 y a 5.000. Con los resultados se ajustan `finger_len`, `finger_t` y `notch_depth`.

**Fase 8: caso real y documentación.** Figura de 13 cm completa con hombros y caderas, comparar con las probetas; actualizar `docs/tutorial_gui.md` y `PROGRESS.md`.

## 6. Criterios de aceptación

- **Clic**: la punta entra en la muesca en cada posición declarada; en la pose de impresión no hay fusión (pieza giratoria libre tras el primer movimiento).
- **Hombro**: giro libre sin interferencia en su rango libre (medido cada 5°); clics en `phase + k·90°` que caen dentro del rango libre; sin tope en el rango libre.
- **Cadera**: clics en 0° y 90°; tope efectivo a ≤ `stop_margin` + 5° fuera del rango; sin choque entre las cabezas izquierda/derecha.
- **Deformación**: ε calculada ≤ `max_strain` (1 %) en todas las lengüetas.
- **Pared**: ≥ `min_wall` alrededor del bolsillo y las ranuras; sin ranuras < `slit_w`.
- **Retención**: el hijo no sale al desplazarlo ≥ 1 mm en ±X/±Y/±Z (se mantiene el test del hongo).
- **Juego angular**: ≤ ±3° medido en probeta (objetivo [estimación]).
- **Par de retención**: entre `hold_torque_min` y `move_torque_max`, y que a los 5.000 ciclos no haya caído más de 30 % [estimación, confirmar con el cliente].
- Cada pieza estanca (variedad cerrada); torso 1 componente.

## 7. Riesgos y decisiones abiertas

1. **Pendiente de confirmar con el cliente**: ¿el mecanismo nuevo **reemplaza** al hongo en hombros y caderas o es **una opción** por articulación? **[supuesto]**: opción por articulación (`mechanism`), con el nuevo por defecto en hombros y caderas.
2. **Brazos inclinados vs. clics nominales** (§3.4): elegir A o B. **[supuesto]**: A.
3. **Boquilla 0,8 mm**: la lengüeta se vuelve gruesa y necesita más largo (12 mm). Puede no entrar en un hombro de 13 cm. Si no entra, habrá que usar boquilla 0,4 para las articulaciones o aceptar menos ciclos.
4. **Fluencia del PLA**: aunque la lengüeta repose relajada, el PLA se ablanda al calor (auto, ventana). Puede recomendarse PETG o nylon como alternativa; el cliente pidió PLA.
5. **Cilindros horizontales**: el disco y el bolsillo tienen eje horizontal al imprimir sentado, con arcos en voladizo en la parte alta. Con Ø ≤ 8–9 mm se espera que salga bien, pero hay que verlo en la probeta (alternativa: perfil en gota/chaflán a 45° solo en la mitad superior del bolsillo).
6. **Espacio en el torso**: bolsillo + lengüetas (10–12 mm de largo en Y) + cabeza del hongo necesitan profundidad y largo; validar que la pared al plano de simetría siga ≥ `min_wall`.
7. **Desgaste**: aun bien diseñada, la fricción PLA-PLA de miles de pasadas pule la punta. Mitigación en el diseño: punta redondeada, sin aristas, lengüeta relajada. Opcional para el usuario final: una gota de lubricante seco (grafito o PTFE). No es una pieza del modelo.
8. **Rango libre del hombro**: cabeza, sombrero o cuello pueden estorbar a 360°. El motor tiene que informarlo, no ocultarlo.
9. **Cómputo**: el barrido de 360° cada 5° con `dilate` sobre mallas de ~300 k triángulos puede ser lento; decimar antes, como ya se hace con el torso.

## Apéndice A: resumen de la entrevista

1. *¿Cómo debe mantener la posición: fricción, clic o regulable?* → Clic (posiciones discretas).
2. *¿En cuántas direcciones se mueve?* → Un solo eje.
3. *¿Qué rango y qué paso?* → Hombro: gira libre y clic en abajo, adelante, arriba y atrás. Cadera: fija abajo y adelante.
4. *¿Qué durabilidad esperás y aceptás una pieza extra?* → Uso intenso; todo PLA, print-in-place, sin pieza extra.
5. *¿Impresora, capa y tamaño?* → FDM, boquilla 0,4–0,8, capa 0,24, figura de 13 cm.
6. *¿Cómo se imprime?* → Sentada, con los brazos ligeramente inclinados. *(Quedó sin responder: reemplazo vs. opción, ver §7.1.)*

## Apéndice B: bloque para PROGRESS.md

```
## Current Focus
Mecanismo de clic print-in-place para hombro (4 posiciones, 360°) y cadera (2 posiciones). Plan hecho (PLAN_mecanismo_clic_hombro_cadera.md); falta implementar.

## Todo List
[x] Done: entrevista y plan
[ ] Todo: Fase 0 probeta de clic (0,4 y 0,8)
[ ] Todo: Fases 1-4 modelo de datos, motor, cadera, hombro
[ ] Todo: Fases 5-8 validación, GUI, banco de ciclos, documentación

## Latest Blockers/Discoveries
- Pendiente: reemplazo vs. opción frente al hongo; ángulo de impresión del brazo (A/B de §3.4)
- La pose de impresión debe coincidir con un clic (evita fusión punta/disco)
- Lengüetas en el padre y horizontales al imprimir (tensión a lo largo de las capas)
```
