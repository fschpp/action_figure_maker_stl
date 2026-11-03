# Plan: guía de cortes y garantía de compatibilidad con el catálogo

_2026-10-31. Solo plan: no se implementó código. Base: `main` (ed3c1ff) de `fschpp/action_figure_maker_stl`. Continúa `PLAN_piezas_intercambiables_catalogo.md` (cierra su decisión D3, «cortes estándar de la línea») y se apoya en lo que ya existe: `ports.py` (`port_compat`, `diagnose_plane`, `snap_frame_to_face`), `preflight.py`, `compat.py`, `extract.py`, `limbs.contact_preview`, `autosil.py`, `customize.fit_scale`, `tools/medir_interfaces.py` y `docs/MEDIDAS_interfaces.md`._

## 0. Resumen

**Problema.** Hoy una figura nueva se corta a ojo (planos de 3 puntos, siluetas, centro del pasador) y recién al catalogarla o armarla se descubre si sus piezas encajan con las del catálogo. Los números medidos lo muestran: entre las 4 figuras reales, la **cadera** encaja sin avisos en 3 de 6 pares y el **hombro en 0 de 6** (IoU 0,25–0,72; radios de cara de 3,4 a 6,8 mm). Tampoco hay forma de saber de antemano qué parte del catálogo va a aceptar un corte.

**Respuesta corta.** Una guía sola (un semáforo al cortar) informa pero no garantiza: si la anatomía de la figura no da la medida, ningún plano la arregla. Lo que sí se puede es combinar tres capas:

1. **Contrato de interfaz de la línea** (garantía *por construcción*): medidas estándar de cada unión (talle de la cara, posición lateral, centro del pasador, capacidad del anfitrión). Un corte que cumple el contrato encaja con todo lo de su talle sin revisar pieza por pieza. La cadera ya funciona así a medias (x = 7 mm y círculo inscrito); el hombro nunca se estandarizó.
2. **Guía de corte con auto-ajuste** en la GUI: dibuja el *calibre* (el disco del talle) sobre la cara de corte, mide en vivo contra el catálogo real («compatible con 11 de 12 piezas») y ofrece **«Ajustar al estándar»**, que busca el desplazamiento del plano, el radio del extremo redondeado y el centro del pasador dentro de márgenes chicos.
3. **Calibración + certificación con el motor real**: se mide cuánto acierta la compatibilidad barata contra la completa (`build` + `validate`) para poder decir «≈ 9 de cada 10» con datos, y se certifica cada pieza nueva contra un panel chico de anfitriones de referencia, en segundo plano.

**Qué se promete** (§3): N0 informado (barato, instantáneo) · N1 por construcción (cumple el contrato, porcentaje esperado calibrado) · N2 certificado (probado con el motor). Solo N2 es una garantía firme; N1 es una probabilidad con datos detrás.

Las fases (§5) no tocan el motor hasta la 4; la 0 y la 2 ya dan valor solas (calibración y «cobertura» al catalogar).

## 1. Diagnóstico: de qué depende que un corte encaje

### 1.1 Qué define una interfaz y quién lo fija hoy

| Variable de la unión | Quién la fija hoy | Cuánto varía entre figuras | Qué rompe si no coincide |
|---|---|---|---|
| Eje y giro (normal del plano, `ref`) | Plano de 3 puntos; `contact_axis` (X) y `find_contact_face` exigen cara plana ⟂ al eje; `snap_frame_to_face` corrige hasta 3° | Poco: el motor ya asume ejes ±X en hombro y cadera | Cara no coplanar, pieza girada |
| Posición lateral de la cara (x) | Cadera: `DEFAULT_LEG_CUT_X` = 7 mm. Hombro: a mano en cada figura (x del puerto 9,1–15,7 mm) | Cadera: nada entre Güemes, Belgrano y Azurduy (7,66 mm); Mickey 9,0 y el muñeco 13,3. Hombro: mucha | Pared tras la cabeza del pasador, separación de las dos cabezas de cadera, ancho del torso |
| Forma y tamaño de la cara | Silueta + rebaba (`burr`) + extremo redondeado (`cap_radius`: redondea solo el lado del torso del extremo; con `auto` solo se propone para piernas) | Hombro: radio equivalente 3,4–6,8 mm entre las figuras reales (×2). Cadera: 6,2–6,8 (Mickey 9,4) | IoU, escalón visible, pared del pasador |
| Centro del pasador | `pin_center` a mano, o `auto` (polo de inaccesibilidad de la cara) | Depende de la cara | `pin_margin` < `min_wall` |
| Pasador y holgura | `Params` de la línea (único) | Ninguna | — |
| Capacidad del anfitrión | Espesor y ancho del torso / pelvis detrás de la cara | Mucha; **no se mide antes de armar** | `validate`: pared tras la cabeza, separación de cabezas, interferencia en el giro |
| Altura de la figura | `ref_height` | Debe ser la de la línea | Todas las medidas escalan |

### 1.2 Qué se mide hoy y cuándo

| Medición | Dónde | Cuándo | Costo | Limitación |
|---|---|---|---|---|
| Compatibilidad barata (IoU, escalón, margen del pasador, altura) | `ports.port_compat` | Al filtrar o listar compatibles | ms | Mezcla lo funcional con lo estético; los umbrales (IoU 0,80, escalón 1 mm, pared 1,2 mm) vienen de la cabeza y la mano y **no están calibrados contra `validate`**; no mide la capacidad del anfitrión ni el barrido |
| Revisión previa de piezas | `preflight.preview_part` | Al elegir una pieza para un modelo ya cortado | s | Parte de un modelo ya cortado; sugiere escalas (`scale_hint`, `fit_scale`) pero no mueve el corte |
| Compatibilidad completa | `compat.check` (`build` + `validate`, caché `compat.json`) | Al armar una receta | minutos | Solo combinaciones reales; no hay forma de extrapolar |
| Diagnóstico del plano | `ports.diagnose_plane`, `snap_frame_to_face` | Al cortar | ms | Dice si el plano cae sobre una cara plana, no si esa cara sirve para el catálogo |

Todo se mide **después** del corte y ninguna medición está contrastada con las otras.

### 1.3 Datos de partida (`docs/MEDIDAS_interfaces.md`, figuras a 100 mm)

| Figura | r hombro (mm) | r cadera (mm) | Margen pasador hombro / cadera (mm) | x hombro / cadera (mm) |
|---|---|---|---|---|
| muñeco | 7,07 | 6,96 | 0,57 / 1,50 | 15,66 / 13,34 |
| Güemes | 6,84 | 6,82 | 2,85 / 2,98 | 12,66 / 7,66 |
| Belgrano | 5,77 | 6,71 | 1,94 / 2,46 | 11,66 / 7,66 |
| Azurduy | 4,89 | 6,17 | 1,35 / 2,47 | 9,66 / 7,66 |
| Mickey | 3,41 | 9,42 | 0,80 / 4,09 | 9,08 / 8,99 |

Pares entre las 4 figuras reales (6 pares): **hombro 0 ok / 6 aviso** (IoU 0,25–0,72); **cadera 3 ok / 3 aviso** (IoU 0,81–0,86 entre Güemes, Belgrano y Azurduy; 0,43–0,52 con Mickey).

Lo que enseñan:

1. **La cadera ya demuestra que estandarizar funciona.** Las tres figuras de la línea coinciden en x = 7,66 mm (= `DEFAULT_LEG_CUT_X` + rebaba 0,66) y en radio 6,2–6,8 mm; el hombro, que se eligió a mano, no.
2. **Hay un piso físico.** Para que el vástago entre con pared, la cara necesita radio ≥ Ø vástago/2 + `min_wall`: 3,25 mm en el hombro (4,1/2 + 1,2) y 3,70 mm en la cadera (5,0/2 + 1,2). Mickey (3,41 mm) ya está al límite: define el talle más chico posible.
3. **IoU 0,80 es estricto para discos.** Dos discos concéntricos tienen IoU = (r menor / r mayor)²: pedir 0,80 exige relación de radios ≥ 0,89 (≈ ±0,7 mm a r = 6,8; ±0,5 mm a r = 4,9). Si la calibración (§4.6) dice que el IoU es estético y no funcional, alcanzan pocos talles; si es funcional, hacen falta más.
4. **Las mediciones del plano anterior usan centroides.** Con el centro del pasador (como hace `extract`) el IoU entre figuras baja a ≈ 0,5–0,7 (nota (d) de la Fase 3 del plan anterior). La guía debe medir como mide `extract`, no como `medir_interfaces`.
5. **Altura.** Esos números están a 100 mm; la figura de referencia del cliente es de 129 mm (`REFERENCE_FIGURE_HEIGHT`) y los pasadores son mm absolutos (no escalan). Hay que **volver a medir a la altura de la línea** antes de fijar cifras.

### 1.4 Huecos

- **G1.** No hay ninguna ayuda *antes* del corte: se corta, se genera y se mira.
- **G2.** Nadie define qué es «estándar» para el hombro, el cuello, la cintura ni la muñeca.
- **G3.** `port_compat` no mide la capacidad del anfitrión (profundidad detrás de la cara, pared, ancho) aunque `validate` falla justamente por eso.
- **G4.** Los umbrales no se contrastaron con el resultado de `validate`.
- **G5.** No hay un número que diga «este corte sirve para X % del catálogo».
- **G6.** «Catalogar esta figura…» no avisa si la pieza que entra no encaja con nada.

## 2. Opciones consideradas

| Opción | Qué es | Qué garantiza | Costo | Límite |
|---|---|---|---|---|
| **A. Guía pasiva** | Semáforo y mensajes al cortar | Nada (informa) | Bajo | Si la anatomía no da la medida, ningún plano lo arregla |
| **B. Contrato por talles** | Medidas estándar por unión; cortes normalizados (x, disco, pasador) | Por construcción, dentro del talle | Medio | Cierra la cara, no la capacidad del anfitrión; figuras raras quedan fuera de contrato |
| **C. Auto-ajuste** | Busca el corte que mejor cumple contrato y catálogo en pocos grados de libertad | Lleva A y B a la práctica | Medio | Solo dentro de márgenes chicos |
| **D. Adaptadores universales** | Un cubo (hub) estándar soldado a cada miembro | Por construcción, incluso con miembros finos | Alto: cambia el motor y la silueta | Se decide después, solo si B + C no alcanzan (G7) |
| **E. Calibración + certificación** | Contrastar lo barato con `build` + `validate`; certificar contra anfitriones de referencia | Garantía real sobre lo probado, y un porcentaje con datos | Medio (cómputo en segundo plano) | Minutos por pieza; extrapola solo si el comportamiento es monótono (se verifica) |

**Recomendación:** A + B + C + E como un solo paquete. La «guía de corte» es la cara visible de B + C; E le pone el número y la garantía. D queda como decisión G7.

## 3. Qué se puede garantizar

| Nivel | Cómo se obtiene | Qué se le muestra al usuario | Cuándo se calcula |
|---|---|---|---|
| **N0 · Informado** | `port_compat` + capacidad contra cada pieza del catálogo con puerto opuesto | «Compatible con 11 de 12 piezas; ok pleno con 8» | Cada vez que cambia el corte (ms) |
| **N1 · Por construcción** | El corte cumple el contrato (talle, x, eje, centro de pasador, capacidad) | «Cumple el contrato (talle M). Esperada ≈ 88 % (n = 31)» | Igual que N0 |
| **N2 · Certificado** | `build` + `validate` sin `problems` contra el panel de anfitriones de referencia | «Certificada 3/3 anfitriones» (válida hasta que cambie pieza, `Params`, contrato o motor) | A pedido, en segundo plano |

Definiciones:

- **Universo** de una pieza nueva en un puerto de clase *c*: piezas del catálogo (última versión de cada id, sin las de su propia figura) con un puerto de clase *c* y rol complementario; las espejables se cuentan por el lado izquierdo, como `Catalog.compatible_with`.
- **Cobertura** = piezas con nivel distinto de «no» ÷ universo. **Cobertura plena** = solo las «ok».
- **Esperada** = (1/N) · Σⱼ p̂(nivelⱼ, clase), donde p̂ es el **límite inferior de Wilson al 95 %** de la proporción de combinaciones de ese nivel y clase que la completa aceptó en la calibración. Se muestra siempre con su *n*: con pocas figuras (hoy 4–5) el número es orientativo y se dice.
- N2 extrapola a anfitriones no probados **solo** si la calibración confirma monotonía (ningún caso donde un anfitrión con más capacidad falle y otro con menos pase). Si no se confirma, N2 vale únicamente para los anfitriones probados.

## 4. Diseño

### 4.1 Contrato de interfaz (`afm-interfaz-1`)

Clave opcional `interfaz` en `linea.json`, por clase de puerto. **Los valores son ilustrativos**: radios y x salen de `MEDIDAS_interfaces.md` (a 100 mm; a otra altura se reescalan las caras, no los pasadores); capacidad y muñeca son marcadores de posición. Los definitivos salen de la calibración (Fase 0) y de `afm catalogo contrato --calibrar`.

```json
"interfaz": {
  "version": 1, "altura_mm": 100.0,
  "hombro": {
    "eje": [1, 0, 0], "ref": [0, 0, 1], "tol_eje_deg": 1.0,
    "x_cara_mm": {"valor": 11.7, "tol": 1.0},
    "forma": "disco",
    "talles": [{"id": "S", "radio_mm": 4.9, "tol_mm": 0.4},
               {"id": "M", "radio_mm": 5.8, "tol_mm": 0.4},
               {"id": "L", "radio_mm": 6.8, "tol_mm": 0.4}],
    "pasador": "shoulder_pin", "pared_min_mm": 1.2,
    "capacidad_min": {"profundidad_mm": 9.0, "pared_posterior_mm": 1.2}
  },
  "cadera":  {"x_cara_mm": {"valor": 7.7, "tol": 0.5}, "barrido_deg": [0, 95], "...": "..."},
  "muneca":  {"referencia": "mano-estandar", "radio_mm": 3.9, "tol_mm": 0.5},
  "cuello":  {"modo": "escala", "escala_max": 0.10},
  "cintura": {"modo": "escala", "escala_max": 0.10}
}
```

- **Talle** = radio del disco al que se recorta la cara (el `cap_radius`). Cada puerto de pieza lleva `talle` (el más cercano dentro de la tolerancia) o «fuera de contrato».
- **Atención: la cara de talle exacta necesita una opción nueva.** Hoy `cap_radius` solo redondea la parte del extremo del lado del torso (la mitad de la cara hacia el torso); la parte del lado de la punta queda como estaba. Dos piezas del mismo `cap_radius` no tienen entonces la misma cara. Por eso `forma: "disco"` pide un modo nuevo en `LimbSpec` (`cap_mode: "completo"`, un tapón cilíndrico que recorta toda la cara al disco en los primeros *d* mm y deja que el miembro se ensanche después). Se mide primero con el `cap_radius` actual (G10).
- **Mismo talle ⇒ cara compatible por construcción** (IoU ≈ 1, escalón ≤ tolerancia), siempre que la cara se recorte al disco completo (`cap_mode`); con el `cap_radius` actual solo coincide la parte redondeada. Talle distinto ⇒ solo aviso, con la sugerencia de escala que ya calcula `preflight`.
- **Contrato duro** (hombro, cadera, muñeca): talles y posición. **Contrato blando** (cuello, cintura): sin talles; la cabeza se escala por área de base y el torso o la pelvis por `fit_xyz` (ya existen), con tope de ±10 % que la guía avisa.
- `afm catalogo contrato RUTA --calibrar` **propone** el contrato desde el catálogo (agrupa radios, toma la mediana de x) y pide confirmación; queda versionado y su hash entra en la clave de `compat.json`.

### 4.2 Reglas de corte (lo que lee el usuario)

La guía de la GUI es esta lista, aplicada en vivo a cada unión:

1. **Altura.** Antes de cortar, la figura está a la altura de la línea (`ref_height`). Si no, la guía lo dice y ofrece corregirlo.
2. **Eje.** El plano del hombro o la cadera tiene su normal en ±X con desvío ≤ 1° (`tol_eje_deg`). Si hay cara plana cerca, «Ajustar plano a la cara» usa `snap_frame_to_face` (hasta 3°).
3. **Posición lateral.** La cara cae en la x del contrato ± la tolerancia (como ya pasa con la cadera). La guía dice «movelo 1,3 mm hacia afuera» y «Ajustar» lo hace con `cut_offset`.
4. **Disco guía.** Sobre la cara se dibuja el disco del talle más cercano. **Si el disco entra completo en la cara de corte y se recorta al talle (§4.1), la cara queda igual a la de todo su talle.** Si no entra, se muestra por dónde sobresale y cuál es el talle más grande que sí entra.
5. **Pasador.** Centro del pasador = centro del disco (≤ 0,5 mm). La pared alrededor del vástago ≥ `min_wall`.
6. **Anfitrión.** El torso o la pelvis tiene detrás de la cara al menos la profundidad que pide el pasador más la pared (§4.4).
7. **Cuello y cintura.** El factor de escala necesario queda dentro de ±10 %; si no, aviso.
8. **Muñeca.** El plano se corta donde el radio equivalente del antebrazo ≈ radio de la base de la mano estándar (± 0,5 mm). La guía propone ese plano solo (§4.3).
9. **Antes de catalogar.** Mirar el resumen de cobertura por lugar; un rojo funcional se confirma a mano (§4.7).

### 4.3 Motor de la guía (`src/afm/guide.py`, nuevo, sin GTK)

```python
measure_cut(place, project, body, half) -> CutMeasure      # mide la cara real del corte (sin construir la figura)
coverage(measure, catalog, params, calib) -> Coverage       # universo, ok / aviso / no, esperada, lista por pieza
advice(measure, contract, coverage) -> list[Advice]         # acciones concretas con magnitud y signo
fit_cut(place, project, body, half, contract, catalog, budget_s=5) -> FitResult
```

**`CutMeasure`**: `frame` (`PortFrame`), `face` (`PortFace`), `tilt_deg` respecto del eje del contrato, `x_mm`, `r_eq`, `r_inscrito`, `circularidad`, `talle` (o `None`), `margen_pasador`, `capacidad` (§4.4), `altura_ok`. Reutiliza `limbs.contact_preview` (cara y centro de pasador por defecto, sin `build`), `ports.PortFace.from_shape`, `ports.pin_diagnosis` y `Compat.cara_chica` (cara demasiado chica para *cualquier* posición del pasador); para el cuello, `head.split_head`; para la cintura, `waist`.

**`coverage`**: para cada pieza del universo corre `port_compat(nuevo, puerto_j, altura, altura_j, params)` más la comprobación de capacidad. Devuelve conteos, «esperada» (§3) y la lista ordenada con el motivo de cada «no» y de cada aviso. Debe coincidir en el conteo con `Catalog.compatible_with`.

**`advice`**: traduce hallazgos en acciones: «girá el plano 1,8°», «movelo a x = 12,0 mm (hoy 13,4)», «la cara solo admite el talle S (r inscrito 4,7 mm)», «bajá el radio del extremo redondeado de 6,9 a 6,8 mm → talle L (compatible con 9 de 12)», «el centro del pasador está a 1,1 mm del mejor punto».

**`fit_cut`** (el «Ajustar al estándar»):

- Grados de libertad acotados: desplazamiento del plano (`cut_offset`, ±2 mm, paso 0,25 mm), talle (`cap_radius`/`cap_center` y `cap_mode`) y centro del pasador (`pin_center`). No toca la silueta del usuario ni la rebaba.
- Para cada desplazamiento se calcula la cara una vez (`contact_preview`); para cada talle, la cara resultante es una intersección 2D (shapely): cara ∩ (disco ∪ semiplano del lado de la punta) con el `cap_radius` actual, o cara ∩ disco con el modo completo. Es exacta porque el cilindro del extremo es paralelo a la normal de la cara, y hace que el barrido de talles sea casi gratis.
- Objetivo: maximizar la cobertura esperada; empate → menor volumen quitado y menor desplazamiento. Restricciones: radio ≤ r inscrito − margen, pared ≥ `min_wall`, capacidad del anfitrión.
- Muñeca y cuello son 1-D: se barre la posición del plano a lo largo del eje del miembro (secciones de la malla) y se elige donde el radio equivalente ≈ el de la mano estándar o el del cuello del contrato. Devuelve los 3 puntos del plano.
- Resultado `FitResult`: cambios para `LimbSpec` (solo `cut_offset`, `cap_*`, `pin_center`), cobertura antes y después, **volumen recortado y desvío máximo respecto de la superficie original** (para que el usuario vea cuánto cambia la silueta) y el **residuo** (lo que no se pudo cumplir y por qué).

### 4.4 Capacidad del anfitrión

Las fallas de `validate` que dependen del anfitrión: pared tras la cabeza del pasador, separación de las dos cabezas de cadera, interferencia con el torso durante el giro, pared sobre la lengüeta del clic, raíz de la lengüeta respecto del plano de simetría. Hoy ninguna se anticipa.

- **Puerto con rol `toma`** (torso, pelvis): `capacidad` = {`profundidad_mm` (de la cara al plano de simetría o a la piel opuesta, a lo largo de −eje, mínimo dentro de la huella de la cabeza del pasador), `pared_lateral_mm`, `ancho_mm`}. Se mide en `extract.py` al catalogar (la malla está disponible; trazado de rayos sobre una grilla de la huella).
- **Puerto con rol `enchufe`** (brazo, pierna): `demanda` = {`profundidad_mm` = largo del vástago + alto de la cabeza + 2 · holgura axial + `min_wall`, `radio_barrido_mm` en la cadera}.
- **Regla**: `capacidad ≥ demanda` o «no»; hasta 0,5 mm de margen, aviso. La separación de cabezas de cadera es función de x y de los pasadores: con x estandarizada se verifica una sola vez en el contrato.
- Piezas viejas sin estos datos: «sin datos de capacidad» (aviso, no «no»). El formato `afm-pieza-1` solo suma claves opcionales (como `mecanismo`).

### 4.5 Certificación (extiende `compat.py`)

- **Panel de referencia** por clase: el anfitrión original de la pieza más el de menor, mediano y mayor capacidad del catálogo (≤ 4 builds).
- Se sintetiza una receta por anfitrión (la pieza nueva en su lugar) y se corre `compat.check` en un hilo con progreso (`progress.py`); el resultado se guarda en `compat.json` como `certificacion` = {anfitriones, ok, problems, fecha, clave}.
- La clave (`recipe_key`) ya incluye piezas con su hash, `Params`, opciones y motor; se suma el **hash del contrato**. Cualquier cambio invalida solo.
- Insignia en la lista del catálogo: «certificada 3/3», «certificada 2/3 (falla con torso-X: pared de 0,8 mm)» o «sin certificar».

### 4.6 Calibración (`tools/calibrar_compat.py`, nuevo)

1. Catalogar las figuras reales y el muñeco (Güemes, Belgrano, Azurduy, Mickey, muñeco).
2. Generar las combinaciones de a una pieza cambiada (como el MVP de la Fase 5 del plan anterior): brazo de A en el torso de B, pierna de A en el torso/pelvis de B, etc. Para cada una: métricas baratas, nivel y resultado de `build` + `validate` (ok o `problems` con sus mensajes).
3. Salida en `docs/CALIBRACION_compat.md` y `.json`: **matriz de confusión** por clase (nivel barato × resultado completo); clasificación de cada tipo de `problem` en **funcional** (bloquea) o **estético** (informa); umbrales recomendados; **prueba de monotonía**; tabla de p̂ (Wilson) para la «esperada».
4. Se repite al crecer el catálogo y al cambiar el contrato o los `Params`. Es lenta (minutos por combinación): herramienta y prueba marcada como lenta; la corre el cliente.

### 4.7 GUI

**Panel «Guía de corte»** (sección nueva, junto a «Piezas de catálogo»; una fila por lugar: hombro, cadera, muñeca, cuello, cintura):

```
Guía de corte — línea «proceres»                       [Ajustar todo al estándar]
 Hombro  ● verde     talle M (r 5,8 mm; contrato 5,8 ± 0,4)   x 11,8 mm ✔   eje 0,4° ✔
         pared del pasador 2,3 mm ✔   profundidad del torso 9,6 mm ✔
         Compatible con 11 de 12 piezas (92 %) · ok pleno 9 · esperada ≈ 88 % (n = 31)
         [Ajustar]  [Ver compatibles]  [Detalle]
 Cadera  ● amarillo  talle L (r 6,9 mm; el más cercano es 6,8)   x 8,4 mm ✘ (contrato 7,7 ± 0,5)
         «Movelo 0,7 mm hacia adentro» · compatible con 8 de 12 (67 %)
```

- **Superposiciones en el visor**: el disco guía translúcido sobre la cara (verde si entra completo; rojo donde sobresale), el contorno de la cara (ya existe: `PinPreview.rings_world`) y, en el cuadro del pasador, un inserto 2D con la cara propia sobre la cara mediana del catálogo (reutiliza las formas de `PartPreview`).
- **Cuándo se recalcula**: al mover el plano, la silueta o el pasador, con retardo de ≈ 200 ms y fuera del hilo de la interfaz si pasa de ≈ 100 ms (como las demás tareas).
- **«Catalogar esta figura…»**: el diálogo muestra la cobertura por lugar *antes* de guardar. Un «no» funcional pide confirmar «Catalogar igual como pieza propia» (etiqueta `propia`, ya existe); un aviso no bloquea. `Controller.pregenerate_issues` suma las advertencias de la guía.
- **«Armar desde catálogo…»**: las listas por lugar se ordenan por nivel y muestran talle y certificación.
- **«Cortar la mano…» (Administrar catálogo)**: semáforo contra las manos estándar del catálogo y botón «Sugerir plano de muñeca» (la búsqueda 1-D de §4.3).
- Lógica en `Controller` / `Session` (`cut_guide(place)`, `cut_guide_fit(place)`), con pruebas sobre `FakeScene` como el resto; los widgets no se pueden abrir en el sandbox (sin GTK) y los prueba el cliente con una lista de verificación.

### 4.8 CLI

```
afm catalogo contrato RUTA --calibrar | --ver | --validar
afm catalogo cobertura RUTA ID [--puerto hombro]
afm guia FIGURA.proyecto --catalogo RUTA [--ajustar --out ajustado.proyecto]
afm catalogo certificar RUTA ID [--anfitriones auto|id,id]
afm catalogo calibrar RUTA --out docs/
```

### 4.9 Compatibilidad hacia atrás

- Sin `interfaz` en `linea.json` todo funciona como hoy; la guía se limita a N0.
- Las piezas ya catalogadas son inmutables: no se reescriben. Quedan «fuera de contrato» o con talle derivado, y si conviene se re-extraen como versión nueva con el corte ajustado.
- `.proyecto`: nada obligatorio nuevo; `fit_cut` escribe campos que `LimbSpec` ya tiene (`cut_offset`, `cap_radius`, `cap_center`, `pin_center`) y, si se aprueba G10, `cap_mode` (opcional; sin él, el comportamiento de hoy).

## 5. Fases

| Fase | Entrega | Prueba / criterio |
|---|---|---|
| **0. Calibración y línea base** (sin tocar el motor) | `tools/calibrar_compat.py`; re-medir a la altura de la línea; `docs/CALIBRACION_compat.md`; clasificación funcional / estético; prueba de monotonía | Matriz de confusión publicada; dorados idénticos. Con esto se deciden G1–G3 |
| **1. Contrato y talles** | `interface.py`, clave `interfaz` en `linea.json`, `afm catalogo contrato`, `talle` por puerto | `verify` limpio; todas las piezas etiquetadas (talle o «fuera de contrato») |
| **2. Medir y cobertura** | `guide.measure_cut`, `coverage`, `advice`; `afm catalogo cobertura`, `afm guia`; cobertura en `catalogo agregar` | Mismo conteo que `Catalog.compatible_with`; < 50 ms con 100 piezas; pruebas con variantes del muñeco sintético (`testfigure.make_doll` hoy no tiene parámetros: se le agregan radio del brazo y x del hombro) |
| **3. Capacidad del anfitrión** | `capacidad` / `demanda` en `extract.py` y `port_compat` | Casos de `validate` por pared y separación de cabezas anticipados por la barata |
| **4. Auto-ajuste** | `guide.fit_cut` (hombro, cadera, muñeca, cuello) | ≥ 90 % de cortes perturbados vuelven al contrato en ≤ 5 s; reporta el residuo |
| **5. GUI** | Panel «Guía de corte», superposiciones, puerta en «Catalogar», orden por nivel en «Armar desde catálogo…» | Lógica con `FakeScene`; < 300 ms al mover el plano (muñeco); checklist manual del cliente |
| **6. Certificación** | `afm catalogo certificar`, insignia, hash del contrato en la clave | La clave cambia con pieza / `Params` / contrato / motor; sin reutilizar resultados viejos |
| **7. Prueba de punta a punta («dejar uno afuera»)** | Cada figura real tratada como nueva: se normaliza con el contrato usando solo las demás, y se arman las combinaciones cruzadas | Ver criterio 6 de §6; se ajustan umbrales y talles con lo que salga |
| **8. (Opcional) Adaptadores** | Cubo universal para miembros bajo el piso (< 3,25 mm de radio) | Solo si G7 lo pide |

Orden recomendado: **0 → 1 → 2 → 3 → 5 (solo lectura: semáforo) → 4 → 6 → 7.** La Fase 2 ya entrega el «compatible con X de N» al catalogar; la 4 es la que vuelve activa la guía.

## 6. Criterios de aceptación

1. **Línea base publicada**: matriz de confusión por clase y tabla de umbrales; sin cambios de comportamiento (dorados idénticos, tests existentes en verde).
2. **Contrato**: `--calibrar` sobre las figuras de ejemplo da talles razonables y deja todas las piezas etiquetadas; `afm catalogo verificar` limpio.
3. **Cobertura**: coincide en conteo con `Catalog.compatible_with`; < 50 ms con 100 piezas.
4. **Falsos «ok»**: de las combinaciones que la barata (con capacidad) marca «ok», ≥ 95 % pasan `validate` sin `problems` (n ≥ 30); falsos «no» ≤ 5 %. Si no se llega, se ajustan umbrales hasta lograrlo y se documenta.
5. **Auto-ajuste**: con cortes perturbados (hasta ±2 mm en x, ±1,5° en el eje, ±1 mm en el radio) vuelve a un corte dentro del contrato en ≥ 90 % de los casos y en ≤ 5 s; si no puede, el residuo explica por qué.
6. **Punta a punta**: con el contrato aplicado a cada figura real como «nueva», ≥ 90 % de las combinaciones cruzadas **del mismo talle** pasan `validate` sin `problems` (hoy: hombro 0 de 6 pares sin avisos, cadera 3 de 6).
7. **GUI**: el semáforo se actualiza en < 300 ms al mover el plano (muñeco); «Catalogar» muestra la cobertura y pide confirmar ante un «no» funcional; lógica probada sin GTK.
8. **Certificación**: la clave cambia ante cualquier cambio relevante; ninguna certificación vieja sobrevive a una pieza o contrato nuevos.
9. **Silueta**: el ajuste informa volumen recortado y desvío máximo; el usuario puede deshacerlo.

## 7. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Pocas figuras (4–5): umbrales sobreajustados, «esperada» poco firme | Mostrar siempre *n*; recalibrar al crecer el catálogo; lo firme es N2 |
| R2 | El contrato aplana figuras raras (brazos muy finos, capas, levitas) | «Fuera de contrato» permitido (no bloquea); adaptadores (G7) si aparecen muchos casos |
| R3 | El extremo redondeado cambia el aspecto del hombro | Vista previa, volumen y desvío antes de aceptar; es opcional por miembro |
| R4 | La monotonía no se cumple | N2 vale solo para los anfitriones probados; se amplía el panel |
| R5 | Números medidos a 100 mm vs. 129 mm de la línea; los pasadores no escalan | Re-medir a la altura de la línea; el contrato lleva `altura_mm`; la guía exige `ref_height` de la línea |
| R6 | Certificar tarda minutos | Segundo plano con progreso y caché; solo bajo demanda |
| R7 | Los widgets de GTK no se prueban en el sandbox | Lógica fuera de los widgets; lista de verificación manual |
| R8 | Cortes interdependientes (cintura ↔ cadera, cuello ↔ cabeza) | La guía mide por lugar; la cobertura final se confirma en N2 |
| R9 | Demasiado semáforo agota al usuario | Mostrar solo lo que cambia o está en rojo; el resto, colapsado |
| R10 | `cap_radius` actual redondea solo la mitad del extremo: la cara de talle exacta no está garantizada | Medir primero el IoU real entre piezas del mismo talle; si queda < 0,95, agregar `cap_mode: "completo"` (G10) |

## 8. Decisiones abiertas (con recomendación)

| # | Decisión | Recomendación |
|---|---|---|
| G1 | ¿Qué cuenta como «compatible»? | Separar **funcional** (bloquea: el pasador no entra, pared, capacidad, interferencia en el giro) de **estético** (informa: IoU, escalón de la cara). La calibración decide en qué lado cae cada cosa |
| G2 | Cantidad y valores de los talles | Salen de los datos (agrupar radios); esperable 3 de hombro y 2–3 de cadera; el más chico no baja de 3,25 mm (hombro) / 3,70 mm (cadera) |
| G3 | ¿Pasador por talle? | No: un solo perfil (`Params`), salvo que el talle S no cierre |
| G4 | ¿Bloquear al catalogar? | Avisar y pedir confirmación; bloquear solo un «no» funcional, con salida a «pieza propia» |
| G5 | Cuello y cintura | Contrato blando: escala con tope ±10 %, sin talles |
| G6 | Panel de certificación | Anfitrión original + menor, mediano y mayor capacidad; ampliar si la monotonía falla |
| G7 | ¿Adaptadores universales? | Postergar; decidir con datos (cuántas figuras caen bajo el piso) |
| G8 | ¿Contrato fijo o aprendido? | Aprendido del catálogo, confirmado por el cliente y versionado |
| G9 | ¿Qué pasa con piezas ya catalogadas? | No se reescriben; se etiquetan y, si conviene, se re-extraen como versión nueva |
| G10 | ¿Modo nuevo de recorte completo al disco (`cap_mode`)? | Medir primero con `cap_radius` actual; agregarlo (Fase 4) si el IoU entre piezas del mismo talle es < 0,95 o si la cara de talle no queda exacta |

## Apéndice A. Ejemplo de salida de `afm guia` (inventado, para fijar el formato)

```
Figura: nueva.proyecto · línea proceres · catálogo con 12 piezas de brazo, 14 de torso
hombro  talle M  r_eq 5,78 mm  r_inscrito 5,91 mm  x 11,8 mm  eje 0,4°   pared pasador 2,3 mm  profundidad 9,6 mm
        cobertura 11/12 (92 %) · ok pleno 9 · esperada 88 % (n = 31)
        no: brazo-mickey-01@1 (la cara de la pieza es demasiado chica: r 3,4 mm frente al talle M, 5,8 mm)
cadera  talle L  r_eq 6,92 mm  x 8,4 mm (✘ contrato 7,7 ± 0,5)
        acción: mover el corte 0,7 mm hacia adentro → cobertura 8/12 → 12/12
muneca  radio 3,6 mm (mano estándar 3,9 ± 0,5) ✔   plano sugerido a 2,1 mm de la muñeca actual
resumen: 2 lugares listos · 1 con acción sugerida · ajustes aplicables con --ajustar
```

## Apéndice B. Qué se reutiliza y qué es nuevo

| Pieza | Reutiliza | Nuevo |
|---|---|---|
| Medir la cara de corte | `limbs.contact_preview`, `PortFace`, `pin_diagnosis`, `Compat.cara_chica`, `diagnose_plane` | `guide.measure_cut`, talle, circularidad |
| Compatibilidad | `port_compat`, `Catalog.compatible_with`, `preflight` | cobertura, capacidad / demanda, «esperada» |
| Ajuste | `LimbSpec.cut_offset / cap_* / pin_center`, `autosil`, `customize.fit_scale` | `guide.fit_cut`, búsqueda 1-D de muñeca y cuello |
| Verificación completa | `compat.check`, `recipe_key`, `CompatCache`, `progress` | panel de referencia, `certificacion`, hash del contrato |
| Datos | `afm-linea-1`, `afm-pieza-1`, `afm-receta-1` | `interfaz` (opcional), `talle`, `capacidad`, `demanda` (opcionales) |
| Herramientas | `tools/medir_interfaces.py`, `tools/golden.py` | `tools/calibrar_compat.py`, `docs/CALIBRACION_compat.md` |
