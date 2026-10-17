# Medidas de la figura de referencia (Link, ≈129 mm)

_2026-10-17. Generado con `tools/medir_articulaciones.py` sobre los STL del cliente (`torso.zip`, `miembro_inferior.zip`,
`miembro_superior.zip`, `cabeza.zip`); salida completa en `docs/medidas_figura_referencia.json`. Para repetir:
`python tools/medir_articulaciones.py <carpeta con los STL> --out medidas.json`. Con esto **no hace falta volver a pasar los
archivos** (≈ 80 MB; el torso de adelante tiene 954 k triángulos)._

Reemplaza los valores aproximados del plan (`PLAN_articulaciones_rodilla_codo_cadera.md` §1), que se tomaron a ojo sobre
cortes inclinados. **Dos correcciones importantes** (ver §5): los ejes de rodilla y codo **no son paralelos a X**, y la
holgura axial de las bisagras es ≈ 0,14–0,2 mm por lado, no 0,35.

## 1. Convenciones y método

- 1 unidad del STL = 100 mm (todo se informa en mm). La figura **mira hacia −Y**, Z arriba, X a lo ancho.
- Las piezas están **despiezadas**: no se pueden medir holguras entre piezas, solo dentro de cada una (agujero vs. pasador,
  ranura vs. lengüeta) y comparando con la pieza que la acompaña.
- Círculos: cortes `x = const` cada 0,25 mm, ajuste de círculo, distinción agujero/pasador por anidamiento. El eje es la recta
  `z = z₀ + m·x` por los centros (y constante). El **Ø verdadero** es la extensión en Y (la inclinación es solo sobre Y).
- Espesores, ranuras y paredes: **rayos paralelos al eje** (a 2 mm de él, en 8 direcciones) o a X / Z según el caso.
  Precisión ≈ ±0,05 mm donde las caras son planas; donde la lengüeta se ensancha se informa el rango.
- `muslo_0002` tiene **59 cáscaras** (geometría interna superpuesta): su volumen y la clasificación agujero/macizo de los
  cortes internos no son confiables; las medidas externas sí.

## 2. Piezas (mm)

| Pieza | Triáng. | Estanca | Extensión X × Y × Z | Z mín → máx | Notas |
|---|---|---|---|---|---|
| `torso_adelante_v4` | 954 360 | sí | 29,2 × 15,6 × 37,2 | 70,1 → 107,3 | y ≤ 0,06 |
| `torso_atras_v4` | 94 836 | sí | 28,9 × 8,6 × 36,3 | 71,0 → 107,3 | y ≥ −0,21 |
| `cadera_adelante_v1` | 270 428 | sí | 22,7 × 8,3 × 24,1 | 53,0 → 77,1 | y −11,55 → −3,27 |
| `cadera_atras_v1` | 338 096 | sí | 25,2 × 7,3 × 24,1 | 53,0 → 77,1 | y −3,43 → 3,88 |
| `cabeza_v2` | 201 832 | sí | 25,1 × 38,4 × 38,0 | 90,2 → **128,2** | 9 cáscaras |
| `muslo_0002` | 362 396 | sí | 18,3 × 14,1 × 37,2 | 32,7 → 70,0 | 59 cáscaras; x 0,79 → 19,12 |
| `bota_volume_0005` | 42 644 | sí | 17,4 × 28,3 × 40,3 | −0,8 → 39,5 | punta de la bota en y = −26,2 |
| `antebrazo_003` (**brazo**) | 27 988 | sí | 14,6 × 14,1 × 25,4 | 74,9 → 100,3 | hombro → codo |
| `brazomano` (**antebrazo + mano**) | 70 488 | sí | 11,0 × 12,2 × 31,3 | 48,5 → 79,8 | codo → mano |

Altura total pies → tope del sombrero: 128,2 − (−0,8) = **129,0 mm**; el torso llega a 107,3. Normalizar a 100 mm pies → sombrero
da ×0,775. **Decisión del cliente (2026-10-17): la figura es de 13 cm reales** (no se normaliza a 100 mm; `Params.reference_figure()` usa `ref_height = 129`) y las medidas de articulación son mm absolutos.

**Nombres confirmados por el cliente:** el **brazo** es `antebrazo_003.stl` (hombro → codo) y el **antebrazo** es `brazomano.stl` (codo → mano).

## 3. Articulaciones

### 3.1 Rodilla (bota ↔ muslo): lengüeta en ranura con tornillo

| Dato | Bota (lengüeta) | Muslo (ranura) |
|---|---|---|
| Eje: inclinación α (z sube con x) | **14,5°** (pendiente 0,25) | **13,5°** (pendiente 0,24) |
| Punto del eje (x, y, z) | (13,18; −6,22; 37,14) | (13,16; −6,22; 36,59) |
| Ø del agujero | **2,56** | **2,31** |
| Largo del tramo con agujero (en x) | 12,4 → 13,9 (una sola lengüeta) | 9,8 → 11,3 y 15,0 → 16,5 (dos paredes) |
| Espesor / ancho (a lo largo del eje) | lengüeta **2,79** (rango 2,65–2,81) | ranura **3,06** (constante) |
| Contorno | extremo redondo Ø≈4,9 (radio 2,15–2,75) | fondo de la ranura en arco, radio ≈ 2,9–3,4 |

- Holgura axial: (3,06 − 2,79)/2 = **0,135 mm por lado** (0,27 mm en total).
- Holgura radial lengüeta ↔ fondo de la ranura: ≈ 0,4–0,5 mm.
- Paredes de la ranura (a lo largo del eje, a 2 mm del eje): 2,0–3,3 mm a un lado y 1,5–3,7 mm al otro.
- Los ejes de bota y muslo difieren ≈ 0,5 mm en z a igual x: es el «desfase de 1,2 mm» del plan, que en realidad era la
  inclinación leída en cortes a distinto x. Con el eje inclinado ajustado el desfase real queda ≈ 0,4–0,5 mm (piezas despiezadas).
- Inferencia: el agujero de la lengüeta (2,56) deja pasar un tornillo ≈ M2,5 y el de las paredes (2,31) es de rosca/agarre.

### 3.2 Codo (antebrazo ↔ brazo): misma topología

| Dato | `brazomano` (lengüeta) | `antebrazo_003` (ranura) |
|---|---|---|
| Inclinación α del eje | **9,4°** | **9,6°** (los dos tramos dan 9,2° y 15,3°: ruidoso) |
| Punto del eje (x, y, z) | (17,64; −2,23; 77,41) | (16,82; −2,21; 77,27) |
| Ø del agujero | **2,58** | **2,31** |
| Largo del tramo con agujero (en x) | 16,4 → 18,9 | 14,6 → 15,4 y 20,4 → 20,6 |
| Espesor / ancho | lengüeta **3,15** (rango 2,67–3,57: se ensancha) | ranura **3,55** (rango 3,08–3,88) |

- Holgura axial ≈ (3,55 − 3,15)/2 ≈ **0,2 mm por lado**, con incertidumbre ±0,3 porque ni la lengüeta ni la ranura tienen
  caras paralelas. El plan decía «ranura 4,7 / lengüeta 2,5–3,2»: el 4,7 era una lectura inclinada y **no es válido**.

### 3.3 Cadera (muslo ↔ pelvis en dos mitades)

Eje **exactamente paralelo a X** (centros constantes: y −3,81, z 60,66–60,73).

| Dato | Muslo (pasador) | Pelvis (cavidad, ambas mitades iguales) |
|---|---|---|
| Cabeza | Ø**9,26**, x 0,79 → 3,225 (espesor **2,44**) | bolsillo de cabeza: alto **9,99** en el plano de unión = cilindro Ø**10,0** (radio 4,995), hasta \|x\| = 3,69 |
| Vástago | Ø**4,80**; libre entre la cabeza y el cuerpo del muslo: x 3,225 → 5,5 (**2,275**) | canal: alto **4,89** (≈ Ø4,9), a través de una pared de **1,30** (\|x\| 3,69 → 4,99) |
| Eje (y, z) | (−3,81; 60,66–60,73) | (≈ −3,4; 60,70): el eje cae sobre el plano de unión (y ≈ −3,35 → −3,43) |
| Interior | hueco Ø4,8 en la cabeza (x 1,82 → 3,225) y otro dentro del muslo (x 5,5 → 9,25): tuerca / tornillo | los bolsillos izquierdo y derecho se unen en el centro (x = 0): 7,38 de ancho para dos cabezas de 2,44 |

- Cabeza / vástago = 9,26 / 4,80 = **1,93** (hongo actual: 6,6 / 4,1 = 1,6).
- Holguras: cabeza ↔ bolsillo **0,37 mm radial**; vástago ↔ canal **0,045 mm radial** (casi ajustado); el vástago libre
  (2,275) = pared 1,30 + ≈ 0,97 entre cabeza, pared y cuerpo del muslo (≈ 0,46 + ≈ 0,5, como estimaba el plan).
- Cuerda del bolsillo vs. y (comprobación del radio): 9,99 (y = −3,4), 9,73 (−4,5), 8,55 (−6,0) en `cadera_adelante`;
  9,98 / 9,54 / 8,04 en `cadera_atras` (y = −3,3 / −2,0 / −0,5).

## 4. Valores de diseño que salen de esto (mm absolutos)

| Parámetro | Plan (a ojo) | Medido | Efecto en el código |
|---|---|---|---|
| Cadera: vástago / cabeza Ø | 4,8 / 9,25 | **4,80 / 9,26** | `HIP_PIN_REFERENCE` ya coincide |
| Cadera: alto de cabeza | 2,44 | **2,44** | ídem |
| Cadera: vástago libre | ≈ 2,3 | **2,275** | `shaft_len` ≈ 2,3 |
| Cadera: pared tras la cabeza | 1,3 | **1,30** | `min_wall` 1,2 se cumple |
| Cadera: holgura radial cabeza / vástago | 0,33 / 0,1 | **0,37 / 0,045** | el vástago casi no tiene holgura: con boquilla 0,4 hay que ensayar con probetas |
| Rodilla: espesor lengüeta | 2,35 | **2,79** | `HingeSpec.tongue_t` 2,4 queda fino |
| Rodilla: ancho de ranura | 3,1 | **3,06** | — |
| Rodilla: holgura axial por lado | 0,35 | **0,135** | `axial_clearance` 0,35 es mayor que la referencia |
| Codo: ancho ranura / lengüeta | 4,7 / 2,5–3,2 | **3,55 / 3,15** | idem (≈ 0,2) |
| Ø agujero lengüeta / pared | 2,56 / 2,34 | **2,56–2,58 / 2,31** | referencia de tornillo, no se modela |
| Inclinación del eje (rodilla / codo / cadera) | 0° (∥ X) | **14° / 9,5° / 0°** | `JointSpec.axis_dir` ya admite un eje inclinado |

## 5. Correcciones al plan y puntos abiertos

1. **Ejes inclinados.** Rodilla y codo tienen el eje girado sobre Y (z sube con x, ≈ 14° y ≈ 9,5°); la cadera no. El plan
   suponía los tres paralelos a X. El motor ya acepta `axis_dir` arbitrario, pero la GUI (clic sobre el plano de trabajo)
   solo pondría X. **Pregunta para el cliente:** ¿la inclinación es intencional (p. ej. para que la pierna quede con un
   ángulo natural) o es de cómo exportó las piezas? **Respuesta del cliente (2026-10-17): la inclinación es intencional**, así que se
   conserva: `JointSpec.axis_dir` oblicuo (p. ej. `(cos 14,5°, 0, sen 14,5°)` en la rodilla) ya funciona y tiene test. Consecuencia: al
   flexionar, el miembro se desplaza hacia el centro ≈ L·sen α·cos α·(1 − cos θ) (≈ 4,8 mm con L = 20 mm y α = 14,5°), por lo que
   las piernas del muñeco, muy juntas, chocan con la rodilla a 85°; verificarlo con la figura real. La GUI todavía solo coloca ejes ∥ X.
2. **Holgura axial.** La referencia usa 0,135 mm por lado en la rodilla y ≈ 0,2 en el codo (con tornillo que centra la
   pieza). El valor 0,35 del plan era la suma de otras lecturas inclinadas. Para print-in-place sin tornillo conviene
   mantener ≥ 0,3 hasta ver las probetas; queda como parámetro.
3. **Espesor de lengüeta.** La referencia mide 2,8 (rodilla) y 3,15 (codo); el valor inicial de `HingeSpec.tongue_t` (2,4)
   es menor. Revisar con probetas.
4. **Frente −Y** de los STL del cliente: usar «Frente hacia −Y» (`Session.set_front`) o girar 180° sobre Z.
5. **Pendientes de medir:** la cara de contacto del torso con la cadera y el diámetro exacto de los agujeros en el torso;
   topes (se simularon en el plan con piezas despiezadas, ±10°); `torso_*` y `cabeza_v2` solo se midieron por dimensiones
   (no tienen articulación de bisagra).
