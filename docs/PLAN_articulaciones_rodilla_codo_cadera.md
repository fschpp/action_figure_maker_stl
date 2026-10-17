# Plan: articulaciones de rodilla, codo y cadera (print-in-place)

_2026-10-16. Solo plan: no se implementó código. Base: el sistema actual de planos + pasadores de hongo (`planes.py`, `limbs.py`, `pins.py`, `pipeline.py`) y el análisis de la figura de referencia del cliente (Link, 5 STL en 4 zips + `cadera_adelante.stl`)._

## 1. Qué se aprende de la figura de referencia

La figura de referencia se sostiene con tornillos y tuercas (eso **no** se implementa). De sus STL se toma solo la geometría de articulación y las holguras. Datos medidos con cortes por el eje de cada articulación (escritos en mm reales del STL: 1 unidad = 100 mm, la figura mide ~129 mm con sombrero; precisión ≈ ±0,1–0,2 mm, el paso 0 del plan es repetirlos con un script).

**Convenciones de los STL**
- Mira hacia **−Y** (la punta de la bota está en y = −26). El repo asume +Y: usar «Frente hacia −Y» (`Session.set_front`) o girar 180° antes de cargar.
- Las piezas están **separadas** (despiece), así que no se pueden medir holguras entre piezas comparando posiciones; se miden dentro de cada pieza (agujero vs. pasador, cavidad vs. pasador).
- Por posición, `antebrazo_003` es el **brazo** (hombro → codo, z 75–100) y `brazomano` es el **antebrazo + mano** (codo → mano, z 48–80). Confirmar con el cliente.
- Los ejes de las tres articulaciones son **paralelos a X** (la figura flexiona hacia adelante/atrás en el plano Y–Z).

| Articulación | Eje (y, z) | Lo que tiene |
|---|---|---|
| **Cadera** | (−3,8 ; 60,7) | Pasador del muslo: vástago **Ø4,8**, cabeza **Ø9,25 × 2,44** (x 0,8→3,2). La pelvis (2 mitades, plano de unión y ≈ −3,35) tiene una cavidad central en T: bolsillo de cabeza **9,9 de alto** (≈ 0,33 mm por lado) + canal de vástago **5,0** (≈ 0,1 mm por lado) a través de una pared de **1,3 mm** (|x| 3,7→5,0). Holgura axial cabeza↔pared ≈ 0,46, pared↔muslo ≈ 0,5. Las dos cabezas (izq/der) quedan enfrentadas en el centro. |
| **Rodilla** | (−6,2 ; 37,2) | **Orejita (lug) en la bota**: espesor ≈ 2,35, contorno redondo Ø≈5,1 alrededor del agujero **Ø2,56**. **Ranura (clevis) en el muslo**: ancho ≈ 3,1 → ≈ 0,35 mm de holgura axial por lado; fondo de la ranura en arco alrededor del eje; paredes laterales de ≈ 2,3–3,7. Agujero del muslo Ø2,34 (centro 1,2 mm más abajo que el de la bota: el muslo está modelado desplazado; ignorar en el motor, que usa un solo eje). |
| **Codo** | (−2,2 ; 77,3) | Misma topología que la rodilla: lengüeta en el antebrazo (agujero Ø≈2,6) dentro de una ranura del brazo. Ancho de ranura ≈ 4,7 vs. lengüeta ≈ 2,5–3,2: **medir de nuevo**, los cortes inclinados dan valores poco confiables. |

**Topes y rango de giro (corrección del cliente, verificada).** En la figura de referencia rodilla y codo **no giran libres**: `muslo_0002` tiene un tope en la parte frontal de la rodilla y `antebrazo_003` (el brazo) lo tiene donde va el codo, y limitan el giro del segmento distal. Se simuló: se llevó el agujero de la bota/antebrazo al eje del agujero del muslo/brazo y se giró alrededor de X de −120° a +120° (pasos de 10°), midiendo el volumen de interferencia entre las mallas (mallas decimadas a 30 k triángulos; la interferencia chica ≲ 1 mm³ es ruido de la decimación). Convención: flexión = la pantorrilla va hacia atrás (rodilla) / el antebrazo va hacia adelante (codo).

| | Extensión (tope) | Flexión (tope) | Rango útil | Nota |
|---|---|---|---|---|
| **Rodilla** | ≈ 0° a −10° (hiperextensión casi nula, valor poco claro: la interferencia crece suave desde −10°) | ≈ **+90–95°** (de 90° a 100° la interferencia pasa de 8 a 34 mm³) | ≈ 0→90° | de «abajo hasta atrás», como describe el cliente |
| **Codo** | ≈ **+10°** (a +20° ya interfiere 1,2 mm³ y crece lineal: tope de hiperextensión) | ≈ **−90 a −95°** (de −90° a −100° pasa de 1,1 a 8,5 mm³) | ≈ 100° en total | de «adentro hasta abajo» |

Para el motor significa que **el tope no se agrega aparte**: es lo que queda de material del padre fuera del barrido. En la cadera actual el límite ya sale así (`sweep` de 0 a `sit_angle + hip_margin`); para rodilla y codo se usa el mismo mecanismo con un rango propio, de modo que el cuerpo de la pieza proximal queda sin tallar más allá de los ángulos de tope. Ojo: la cavidad lleva `clearance` de holgura, por lo que el tope real queda unos grados más allá del rango declarado.

Conclusión de diseño: rodilla y codo son **bisagras de lengüeta en ranura con eje transversal**; la cadera es un **pasador con cabeza grande** (relación cabeza/vástago 1,9 vs. 1,3 del hongo actual) retenido por una pared fina entre dos bolsillos.

## 2. Qué tiene hoy el motor y qué falta

Tiene: miembro cortado por prismas de silueta (`LimbSpec`, 2.º corte `LimbCut`), cara de contacto, `mushroom_pin` con eje = normal de la cara, cavidad = `dilate(miembro + pasador, clearance)` barrida por ángulos (`pipeline.build`), pose sentada, espejado, `validate` (holgura, interpenetración, recorrido), probetas, colocación asistida del pasador sobre un plano de trabajo paralelo al de simetría.

No tiene, y por eso hace falta código nuevo:
1. **Cortar un miembro en dos segmentos** (muslo/pantorrilla, brazo/antebrazo). Hoy el padre de todo es el torso.
2. **Padre ≠ torso**: la cavidad de la pantorrilla se talla en el muslo, la del antebrazo en el brazo (cadena torso → muslo → pantorrilla).
3. **Pasador transversal**: en el hombro/cadera el pasador sale perpendicular a la cara de contacto; en rodilla/codo el eje es **paralelo** al plano de corte (cara ≈ horizontal, eje X). Se necesitan 2 bulones cortos (uno a cada lado de la lengüeta) en vez de un hongo.
4. Validación y pose con cadena de rotaciones.

## 3. Diseño propuesto (usa los planos y pines existentes)

**Plano de articulación** (3 puntos, como el plano de miembro): corta el miembro en proximal y distal. Se agrega a la lista de planos de la GUI como un tipo nuevo, «Articulación».

**Eje y centro**: se elige sobre el mismo *plano de trabajo paralelo al de simetría* que ya usa «Colocar pasador» (clic sobre la silueta de costado), con dirección = `contact_axis` (X). El centro (y, z) es el del bulón.

**Lengüeta + bulones (pieza distal)**: sobre el corte, el extremo distal se recorta a un cilindro de eje X y radio R (reutiliza `round_end` / `cap_radius`) y se le deja una lengüeta de espesor `t`; a cada lado salen bulones cilíndricos Ø `d`, largo `L`, con chaflán de 0,3–0,5 mm. **Ranura (pieza proximal)**: se obtiene igual que hoy: `difference(proximal, barrido(dilate(distal + bulones, clearance)))`, y el extremo proximal también se redondea al radio R + holgura para que el giro no la muerda. Los bulones quedan en agujeros ciegos de las dos paredes: la pieza distal no puede salir ni axial (una pared) ni radialmente (agujero cerrado), no hace falta cabeza de hongo.

**Parámetros nuevos** (en `Params`, mm absolutos como `PinSpec`; valores iniciales tomados de la figura de referencia y a confirmar con probetas):
- `hinge_pin: HingeSpec(pin_d=2.4, pin_len=1.2, tongue_t=2.4, end_r=2.8)` para rodilla y codo (override por articulación).
- `axial_clearance=0.35` (la figura usa 0,35–0,5) y `clearance` radial se mantiene en 0,50 (lo medido en Mickey) hasta que las probetas digan otra cosa.
- `knee_range=(0,90)`, `elbow_range=(0,90)` en convención de **flexión positiva** (rodilla: pantorrilla hacia atrás; codo: antebrazo hacia adelante), más `stop_margin` (grados de material que se deja sin tallar como tope, por defecto 5° de la figura de referencia) y `*_hyperext` (rodilla ≈ 0–10°, codo ≈ 10°). Los valores vienen de la simulación de la figura del cliente (§1), no son libres. `knee_print_angle`, `elbow_print_angle` (en la pose sentada: rodilla 90°, que cae justo en el tope: imprimir a ≈ 85° para no apoyar la pieza contra el tope).
- **Cadera**: preset `hip_pin` ← `PinSpec(shaft_d=4.8, shaft_len≈2.3, head_d=9.25, head_h=2.44)` (ver aviso de escala abajo) + chequeos nuevos de pared detrás de la cabeza (≥ 1,2 = `min_wall`; la figura usa 1,3) y de que las dos cabezas enfrentadas no se toquen (separación ≥ 2·holgura).

**Aviso de escala (importante)**: los valores salen de una figura de 129 mm. El repo normaliza a 100 mm de pies a coronilla (×0,775 si la coronilla es la punta del sombrero). Las dimensiones de articulación se deben tratar como **mm absolutos de impresión**, no escalarse: con ×0,775 el bulón quedaría Ø1,9 y la lengüeta 1,9 mm, en el límite de lo imprimible con boquilla 0,4. Se agrega validación `min_pin_d` y `min_tongue` y la decisión (normalizar a 100 mm o dejar 129 mm) la toma el cliente.

## 4. Fases de implementación

**Fase 0: medición reproducible (1 tarde).** `tools/medir_articulaciones.py` que reproduzca la tabla de §1 a partir de los STL (cortes por el eje, diámetros de agujeros, anchos de ranura/lengüeta, holguras). Corrige los valores inciertos del codo. Entrega: tabla en `docs/` + test que compara contra ella (tolerancia 0,2 mm).

**Fase 1: modelo de datos.** `JointSpec` (nombre, miembro, `plane`, `axis_center`, `axis_dir`, `HingeSpec`, `range_deg`, `end_r`) en nuevo `joints.py`; `Params.hinge_pin/axial_clearance/ranges`; `Project.joints` con carga/guardado (los `.proyecto` viejos cargan igual); `PARAM_FIELDS` para la GUI.

**Fase 2: motor.** `joints.split_limb(limb, plane)`, `make_tongue`, `make_hinge_pins`, `carve_joint(parent, child, joint, p)` (barrido de `range_deg` alrededor de (centro, eje) con `rotate_about_axis`, ya existente). `pipeline.build`: orden torso → proximal → distal; piezas nuevas `muslo_izq/der`, `pantorrilla_izq/der`, `brazo_*`, `antebrazo_*`; `res.motion` pasa a cadena (transformaciones compuestas).

**Fase 3: cadera.** Preset nuevo de `hip_pin`, validación de pared detrás de la cabeza y de cabezas enfrentadas, test con los valores de la figura (head/shaft 1,9, pared 1,3). El barrido hasta `sit_angle + hip_margin` no cambia.

**Fase 4: validación.** `validate`: holgura mínima hijo–padre por articulación; **retención** (extender el test «subir 1 mm choca» a ± Y y ± Z de la pantorrilla/antebrazo); recorrido libre con la cadena rotada (cada 2,5°) incluyendo pantorrilla vs. pierna opuesta y antebrazo vs. torso; pared mínima del padre alrededor de la ranura (hoy no se mide la pared del cuerpo); `min_pin_d`, `min_tongue`.

**Fase 5: GUI.** `Mode.JOINT_PLANE` (3 clics), `Mode.JOINT_AXIS` (clic en el plano de trabajo, reutiliza `PinPreview`/`silhouette_on_plane`), fila «Articulación» en la lista de planos, controles de rango y pose (la «Pose sentada» ya existente compone cadera + rodilla), campos nuevos en «Parámetros». Lógica en `Controller` con tests sobre `FakeScene`.

**Fase 6: probetas y calibración.** `probes.py`: probeta de bisagra (grilla holgura radial × Ø bulón, 3 anchos de ranura) y de cadera con el preset nuevo. Imprimir, elegir valores, guardarlos como «perfil de línea».

**Fase 7: caso real y documentación.** Armar la figura de referencia (o una figura sintética equivalente en `testfigure.py`) con las 3 articulaciones, regenerar, comparar con la tabla de §1 y con la figura impresa; actualizar `docs/tutorial_gui.md`, `PROGRESS.md`.

## 5. Criterios de aceptación
- Rodilla y codo: giro libre **dentro de su rango declarado** (sin interferencia < 1e-4 mm³, medido cada 2,5°) y **tope efectivo fuera de él**: la pieza distal debe chocar con el padre a ≤ `stop_margin` + 5° del límite, tanto en extensión como en flexión (sin esto la articulación gira «libre» y no es la de la figura). Holgura mínima ≥ 0,9·`clearance` dentro del rango; pieza distal retenida (no sale al desplazarla ≥ 1 mm en ± Y/± Z/± X). Comparar con los topes medidos de la figura de referencia (rodilla ≈ 0→90°, codo ≈ +10→−90°) con tolerancia ±10°.
- Cada pieza variedad estanca; torso 1 componente.
- Cadera: cabeza/vástago ≥ 1,6, pared ≥ `min_wall`, sin choque entre las cabezas.
- Probetas imprimen con giro suave sin romper (decisión del cliente).

## 6. Riesgos y decisiones abiertas
1. **Escala** (ver §3) — **decidido por el cliente (2026-10-17): 13 cm reales**, sin normalizar a 100 mm (`ref_height = 129`, `Params.reference_figure()`); los bulones y holguras son mm absolutos.
2. **Tiempos de cómputo**: dos barridos encadenados con `dilate` sobre mallas de ~300 k triángulos; probablemente hay que decimar antes (como ya se hace con el torso).
3. **Bulones horizontales** imprimen con puentes; si fallan, alternativa: bulón en forma de gota (rombo) o imprimir la figura acostada (`orient`).
4. **Topes**: la medición de §1 es una simulación con piezas despiezadas y alineadas por el centro del agujero (hay un desfase de 1,2 mm en la rodilla); los ángulos de tope son ±10°. Hay que confirmarlos con la figura armada o con probetas. Además la validación actual (`recorrido`) solo comprueba que no haya choque en el rango; hay que sumar el chequeo inverso (que **sí** choque fuera de él).
5. **Pared del padre**: el barrido de la pantorrilla puede dejar el fondo del muslo muy fino; solución prevista: aumentar `end_r` o acotar `range_deg`.
5. Nombres — **confirmados por el cliente (2026-10-17):** el brazo es `antebrazo_003.stl` y el antebrazo es `brazomano.stl`.
7. **Ejes inclinados** — el cliente confirmó (2026-10-17) que la inclinación de rodilla (≈ 14°) y codo (≈ 9,5°) es **intencional**: se conserva (ver `docs/MEDIDAS_figura_referencia.md` §5). La GUI (Fase 5) debe permitir fijar la inclinación del eje.
6. **No se modela** el pasador/tornillo ni las tuercas de la figura de referencia ni la división de la pelvis en dos mitades (lo pidió explícitamente el cliente).
