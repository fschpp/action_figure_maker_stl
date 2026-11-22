# Plan: la textura de la cabeza de reemplazo sale al revés (cara en la nuca)

**Versión:** 0.1 (borrador) — 2026-11-22
**Repo:** `fschpp/action_figure_maker_stl`, sobre `main` `581793d` (ubicar en `docs/PLAN_orientacion_color_cabeza.md`)
**Continúa:** `PLAN_color_catalogo_cabeza.md` (T31–T37) y el «Current Focus» de `PROGRESS.md` del 2026-11-22 (*Color por roles en cabezas de reemplazo*). Las tareas de este plan son **O01–O10**.
**Marcas:** **[C]** dicho por el cliente · **[V]** verificado con los archivos de la pieza o leyendo el código · **[I]** inferido, a validar · **[P]** pendiente de aclarar.

---

## 1. Síntoma

Pieza `cabeza-simon-bolivar-cuerpo-fixed-01@2` («bolivar-cabeza», catálogo `proceres-127`), color por roles desde `bolivar_cabeza_lq.fbx`. En «Administrar catálogo» la piel (cara y orejas) aparece del lado de la nuca y el pelo negro del lado de la cara; el amarillo y el rojo caen sobre la mandíbula. **[C]** (3 capturas).

El usuario también señala que «se tomaron los 10 colores por defecto» (§5).

## 2. Qué se verificó

Archivos usados: `…_pieza.json` (ficha), `…_color.npz` (paquete de roles guardado) y `bolivar_cabeza_lq.fbx`. **No se tuvo el STL de la pieza** (`cabeza-simon-bolivar-cuerpo-fixed-01@2.stl`); ver §7.

| # | Hallazgo | Marca |
|---|---|---|
| 1 | El FBX **no es solo la cabeza**: es un **busto** (cabeza + hombros + uniforme con bordados dorados y paño rojo/azul). 13 851 vértices, textura 4096², extensión 92,5 × 75,2 × 51,5 unidades (Y arriba). La cabeza es la parte con `y ≥ 30` (≈ 41,6 % del área). La pieza es esa cabeza **cortada en la base del cuello** (15,4 × 19,8 × 22,5 mm, 3007 mm³). | V |
| 2 | La **cara del FBX mira a +Z** (convención habitual de Y arriba): el centroide de los triángulos de piel de la cabeza está 14,3 unidades delante del centro de la cabeza, y la punta de la nariz (el punto de mayor z) está en `(−2, 50, 17,5)`. | V |
| 3 | La similitud guardada en la ficha (`color.similitud`) es una rotación propia (det > 0, **sin espejo**) con escala **0,4065** (el ancho de la cabeza da 15,3 mm contra 15,4 de la pieza: escala, posición vertical y altura están bien). Lleva los ejes del FBX a: `x → (0,999; 0,040; 0,002)`, `y → (0,011; −0,325; 0,946)`, `z → (0,039; −0,945; −0,325)`. | V |
| 4 | Por eso **la cara del FBX (+Z) cae sobre −Y de la pieza**. La nariz del FBX queda en `y = −10,8 mm`, pegada al extremo trasero de la caja de la pieza (`y_min = −11,25`). | V |
| 5 | **El frente de la pieza es +Y.** El marco del motor deja siempre el frente en +Y (`frame.py`: giro de 180° sobre Z si `front == "-Y"`); el puerto `cuello` tiene `ref = (0; 0,925; −0,379)` y el eje del cuello se inclina hacia −Y; y en la vista de perfil (captura 2) la mandíbula sale del lado derecho, que es +Y (el cilindro naranja del cuello se inclina hacia la izquierda = −Y). | V / I |
| 6 | Resumen: la rotación guardada es la conversión Y-arriba → Z-arriba **sin** el giro de 180° (≈ Rx +109°, es decir 90° más 19° de cabeceo). **El error es de giro (yaw) de 180°**; posición, escala y «arriba» están bien. | V |
| 7 | **Las posiciones no delatan el error.** El anillo de unión del cuello (la tapa de la pieza) queda a 0,45 mm de la superficie del FBX con la similitud guardada. El cuello es casi un cilindro: girarlo sobre su eje lo deja en el mismo lugar. Un chequeo de «el cuello coincide» no sirve para detectar el error. | V |
| 8 | **Prueba sintética.** Se tomó la cabeza del propio FBX (`y ≥ 29`), se la puso en la pose **correcta** como «pieza» y se midió la pose **girada 180° (la guardada)** con la métrica del motor (`_icp_trimmed` + `_polish_trimmed`, `CUT_KEEP = 0,8`):  media **0,277** · p95 0,735 · máx 0,855 · cubierta 0,869 mm. El informe real de la ficha dice media **0,301** · p95 0,752 · máx 0,887 · cubierta 0,835. Es el mismo número. La tolerancia que se exige es 0,5188 mm y cubierta ≥ 0,6: **la pose al revés pasa como «ok»**. | V |
| 9 | El ajuste no rígido (`colorwarp.warp_package`) **tapó el problema**: desplazó los vértices del FBX 2,18 mm de media y hasta 4,55 mm (en una cabeza de 20 mm) y subió la cobertura de 0,334 a 0,696; solo exige +0,02 de ganancia y no mira el tamaño del desplazamiento. La ficha quedó `estado: "alineada"`. | V |
| 10 | La segmentación en roles **no es la causa**: el atlas de etiquetas (cara, orejas = piel; pelo = negro) coincide con la textura. Con cualquier paleta el resultado sale al revés si la pose lo está. | V |

## 3. Causa raíz

La alineación `FigureFrame.from_cut_piece_similarity` (commits `423f7d5` y `d257225`, 2026-11-22) es **solo geométrica y ciega al sentido adelante/atrás**:

1. **Mide con el 80 % que mejor calza (`CUT_KEEP`).** Una cabeza con mucho pelo es casi igual de redonda por delante y por detrás; lo único que las distingue (nariz, mentón, frente) es un puñado de área, justo lo que el recorte tira. La cabeza girada 180° da 0,28 mm: dentro de la tolerancia.
2. **Los 24 arranques por ejes principales son todos iguales para ella.** No hay ninguna preferencia por «arriba» ni por «el frente de la figura es +Y» (el motor lo sabe, la alineación no).
3. **`ok` solo mira `media ≤ tolerancia` y `cubierta ≥ 0,6`.** Con `tolerancia = 0,75 × arista` (0,52 mm) la pose girada pasa. No se compara contra la pose girada ni se mira hacia dónde mira la cara.
4. **El FBX es un busto y la pieza una cabeza**, así que los momentos de inercia de arranque no coinciden y la elección entre los dos giros queda librada al azar de los arranques. (Con un FBX idéntico a la pieza la pose correcta ganaría por 0,000; con una cabeza «v2» re-mallada empatan.)
5. **El ajuste no rígido disimula una pose mala** (hallazgo 9) en lugar de avisar: una pose correcta de la misma cabeza necesita décimas de mm de corrección, no milímetros.

Existe la información que desempata y hoy no se usa: el **frente del marco de la figura (+Y)**, el **puerto `cuello.ref`**, y la **dirección de la cara en el FBX** (el rol «piel»).

## 4. Lo que se quiere

1. Que la cara del FBX quede del lado del frente de la pieza, y que si no se puede decidir **se avise** en vez de guardar una pose al revés. **[C]**
2. Poder **corregir a mano** (botón «Girar 180°») sin rehacer los roles. **[I]**
3. Que los clusters de color sirvan a la cabeza y no al busto entero (§5). **[C]** *(a confirmar, §8)*

## 5. «Los 10 colores por defecto» — qué es y qué no es

Interpreto «se tomó los 10 colores por defecto» como **`RolesWizard.load(fbx, k=10)`**: el asistente calcula 10 clusters de color y la paleta de la línea los sugiere como roles. **[I][P]** Se midió con el FBX real (misma semilla que el asistente):

- Los 10 clusters se calculan sobre **todo el busto**. La cabeza es el 41,6 % del área.
- **3 clusters casi no existen en la cabeza** (dorado `#C09C5E` 0,1 %, bronce `#997948` 0,1 %, rojo `#A23B2B` 0,0 %): se gastan en los hombros. Dos clusters de pelo suman el 72 % de la cabeza. Solo 6 de 10 clusters pasan del 2 % de la cabeza.
- La piel de la cara queda repartida en tonos (`#9A6E51`, `#C3997E`, `#6F3F2C`) y las transiciones piel→pelo caen en tonos bronce que terminan en `dorado_o_amarillo` **[I]**: es el **aro amarillo** que rodea la cara en el atlas y, probablemente, las manchas amarillas de la mandíbula.
- Los roles `dorado_o_amarillo` y `rojo` quedan guardados en la pieza aunque son del uniforme.
- El color que se ve en la vista previa es el de la **paleta de la línea** (planos por rol), no el de la textura: así está diseñado (el color final se hornea al armar).

**Conclusión:** los 10 colores por defecto explican el aro y las manchas amarillas, **no** la cara al revés. Son dos defectos independientes; este plan arregla los dos (O10 para el color).

## 6. Plan de solución

Orden: primero la prueba que falla (O01), después la orientación (O02–O06), después el color (O10).

**O01 · Prueba de regresión con la cabeza real.** `tests/test_cut_piece_orientation.py` + fixture `tests/data/bolivar_cabeza_roles.npz` (el `.color.npz` guardado pesa 659 KB y trae vértices, caras, UV y atlas; no hace falta el FBX de 50 MB). Casos: (a) cabeza del FBX cortada en el cuello en pose correcta → debe devolver esa pose, **y** la girada 180° no debe pasar como `ok`; (b) lo mismo con la cabeza rotada 90° y con cabeceo ±25°; (c) el caso real: media 0,301 con la cara hacia −Y debe fallar. *Criterio:* la prueba falla hoy y pasa tras O02–O05.

**O02 · Dirección del frente de la pieza.** `colorpiece.piece_front(meta, mesh) → vector` (+Y del marco de la figura, comprobado contra `puertos.cuello.ref` cuando la pieza lo trae; la proyección horizontal de `ref` si el cuello está inclinado). *Criterio:* para Bolívar da `(0, 1, 0)` ± 5°.

**O03 · Dirección de la cara en el FBX.** `colorroles.face_direction(pkg, palette_roles)`: sobre la parte del FBX que cubre la pieza (O04), vector del centro de la cabeza al centroide ponderado por área de los triángulos del rol «piel»; sin rol «piel», respaldo por el cuello (el cuello queda detrás del centro de la cabeza). Devuelve vector y confianza. *Criterio:* Bolívar → `+Z` FBX, ángulo con la nariz medida < 25°.

**O04 · Recortar el FBX con el plano del puerto.** Para pesar la alineación y el color solo con lo que está por encima del corte del cuello (la pieza no tiene hombros ni uniforme). Usa `puertos.cuello` (centro, eje) ya guardado. Evita que los momentos del busto sesguen los arranques. *Criterio:* los momentos del FBX recortado se parecen a los de la pieza (cociente de trazas ∈ [0,7; 1,4]).

**O05 · Desempate adelante/atrás en la alineación.** En `from_cut_piece_similarity` agregar `front_hint=(f_fbx, f_pieza, up_pieza)`:
  1. Entre los arranques, **descartar** los que no cumplen `∠(R·up_fbx, up_pieza) ≤ 45°` y `∠(R·f_fbx, f_pieza) ≤ 60°`.
  2. Evaluar también la hipótesis opuesta (`R` girada 180° sobre el eje del cuello) y guardar la **diferencia de media** entre ambas en el informe (`yaw180: {media_alt_mm, margen_mm}`).
  3. Sin pista (sin piel, sin puerto): si `margen < 0,15 mm` el resultado queda **`ambigua: True`** y `ok = False` (no se guarda como «alineada»).
  *Criterio:* O01 pasa; el informe trae `frente: {fbx, pieza, angulo_deg}`.

**O06 · Validar el resultado, no solo el calce.** `align_fbx_to_piece` levanta `PieceColorError("la cara del FBX quedó mirando hacia atrás (ángulo N°)…")` si `∠ > 90°`, y propone el giro de 180°. La ficha guarda `color.alineacion.frente_ok` y `angulo_frente_deg`. `ok` pasa a exigir `frente_ok`.

**O07 · Compuerta del ajuste no rígido.** En `warp_package`: no aplicar (y marcar `pose_sospechosa: True`) si `cobertura_antes < 0,5`, o `desplazamiento_medio > 0,5 × arista_pieza`, o `desplazamiento_max > 0,15 × diagonal`. Para Bolívar: medio 2,18 mm y cobertura previa 0,33 → **no se aplica** y se avisa. *Criterio:* una pose al revés ya no termina en «alineada».

**O08 · Botón «Girar 180°» y reparar piezas ya guardadas.** En el asistente «Roles de color» y en «Retocar color…»: vista previa con una flecha del frente de la pieza y otra de la cara del FBX, y el botón «Girar 180°» (compone `Rz(180°)` sobre el eje del cuello con la similitud y recalcula). Para piezas ya guardadas, `afm catalogo color-girar <ref> --grados 180`: **vuelve a leer el FBX original** (el `.color.npz` guarda los vértices ya deformados por el ajuste no rígido, por eso no se puede girar desde el paquete) y guarda una versión nueva (`@3`). *Criterio:* `cabeza-simon-bolivar-cuerpo-fixed-01@3` sale con la cara hacia +Y.

  *Estado:* hecha la parte de catálogo — `afm catalogo color-girar REF [--fbx F] [--grados 180]` y el botón «Girar color 180°…» de «Administrar catálogo»; vuelve a leer el FBX original (`color.origen.sha256`), reusa los roles y el atlas guardados, y no guarda si la cara sigue hacia atrás. Con los archivos reales: cara a 2,6° del frente, misma pose que la alineación automática. Pendiente (con O09): el botón y las flechas de la vista previa dentro del asistente «Roles de color» y de «Retocar color…».

**O09 · Mostrar el resultado antes de guardar.** El asistente muestra la cabeza con el color real y el aviso «la cara quedó hacia adelante/atrás» y **no deja guardar** con `frente_ok = False` sin confirmación explícita.
  *Hecho (2026-11-22):* `RolesWizard.check_piece` + `save_piece(confirm_front=)`, botón «Probar en la pieza», vista «Pieza con el color» y diálogo «Guardar igual»; ver `PROGRESS.md`. Falta probar la ventana a mano.

**O10 · Clusters sobre lo que sirve a la pieza.** `colorroles.propose(tm, palette, k, region=mask)`: las muestras salen solo del FBX recortado (O04) tras alinear; `k` por defecto 8 para una pieza recortada (10 para figura entera); se funden los clusters con < 1 % de la región; los roles que cubren < 0,5 % de la pieza no se guardan. Para el aro amarillo: filtro de mayoría de ventana mayor (7 px) o eliminar franjas de `dorado_o_amarillo` de menos de 3 px entre `piel` y `negro`. El asistente pasa a **alinear primero** (necesita el STL de la pieza) y **agrupar después**. *Criterio:* para Bolívar, ≥ 8 clusters con ≥ 2 % de la cabeza o `k` reducido; sin `dorado`/`rojo` en la ficha.
  *Estado (hecho):* `colorroles.propose(region=, min_share=)` (muestras solo de la región; fusión de clusters < 1 %), `role_shares` / `prune_small_roles` (roles < 0,5 % de la pieza no se guardan), `colorpiece.piece_region` (= `head_region` + a ≤ 3 mm de la superficie de la pieza: con solo `head_region` entraba el 13 % del área del busto que no es de la pieza, incluido el paño rojo del cuello del uniforme) y `RolesWizard.load(fbx, k=None, piece_ref=ref)`: alinea primero **sin** pista de la cara (la región casi no cambia con el giro de 180° sobre el cuello) y agrupa después; `k` 8 con pieza, 10 sin ella; filtro de mayoría de 7 px con pieza. La ventana usa la pieza elegida al pulsar «Analizar colores» (si no hay similitud FBX → figura). No se hizo la variante «eliminar franjas de `dorado_o_amarillo`»: el filtro de 7 px y la fusión alcanzan para el caso. Con Bolívar real (`k=8`, pose alineada): 8 clusters, todos ≥ 3 % de la cabeza, sin el rojo `#A23B2B`; queda un tono dorado `#A78B50` (3 %) que es el aro amarillo real del atlas: lo decide la persona (rol o «sin rol»).

## 7. Cómo se verifica

- **Datos reales:** con el STL de la pieza, `align_fbx_to_piece` debe dar `frente_ok = True`, ángulo de la cara con +Y < 30°, la punta de la nariz con `y ≥ +7 mm` (hoy `−10,8`), el centroide de la piel por delante del centro de la cabeza, y `desplazamiento_medio` del ajuste < 1 mm.
- **Regresión:** `tests/test_cut_piece_orientation.py` (O01), `test_colorpiece`, `test_colorwarp`, `test_roles_wizard`, `test_i18n`.
- **Visual:** la misma vista 3/4 de la captura 1 con la cara del lado de la mandíbula y la piel sobre el mentón.
- **No verificable aquí:** la ventana GTK/EGL (sin GTK en este entorno). Hay que probarla a mano.

## 8. Pendiente / a confirmar

1. **[P] Falta el STL real** (`cabeza-simon-bolivar-cuerpo-fixed-01@2.stl`). La prueba del hallazgo 8 usa como «pieza» la cabeza del propio FBX; las cifras coinciden con las del informe real (0,277 contra 0,301 mm), pero la confirmación final con la malla real (re-mallada «v2») queda para cuando esté el STL.
2. **[P] ¿Qué eran «los 10 colores por defecto»?** Tomé `k = 10` clusters del asistente (§5). Si era otra cosa (p. ej. la paleta por defecto de la línea), se ajusta O10.
3. **[P] ¿Se corrige solo o se pregunta?** Propuesta: si hay pista de piel y puerto, se corrige solo y se avisa; si no, se bloquea y se pide confirmar con «Girar 180°» (O05, O09).
4. **[I]** La cara del FBX mira a +Z porque así lo muestra la piel de la cabeza; un FBX exportado con otra orientación se resuelve igual por O03 (no se supone la convención, se mide).

## 9. Anexo — comprobación rápida con la ficha y el `.npz`

```python
import numpy as np, json
z = np.load("cabeza-simon-bolivar-cuerpo-fixed-01_2_color.npz", allow_pickle=True)
d = json.load(open("cabeza-simon-bolivar-cuerpo-fixed-01_2_pieza.json"))
V, F, cuv, lab = z["vertices"].astype(float), z["faces"], z["corner_uv"].astype(float), z["labels"]
roles = json.loads(bytes(z["meta"]).decode())["roles"]
S = np.array(d["color"]["similitud"]); R = S[:3, :3] / np.cbrt(np.linalg.det(S[:3, :3]))
uv = cuv.mean(1); h, w = lab.shape
fl = lab[np.clip(((1 - uv[:, 1]) * h).astype(int), 0, h - 1), np.clip((uv[:, 0] * w).astype(int), 0, w - 1)]
tri = V[F]; area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1); cen = tri.mean(1)
head = cen[:, 1] > 35                                   # cabeza del busto (Y arriba)
piel = (fl == roles.index("piel")) & head
face = (cen[piel] * area[piel, None]).sum(0) / area[piel].sum() - (cen[head] * area[head, None]).sum(0) / area[head].sum()
print("cara en el FBX (x,y,z):", face.round(1))
print("cara en la pieza      :", (R @ face).round(1), " <- el frente de la pieza es +Y")
```

Resultado con los archivos recibidos: cara FBX `(−0,5; −6,9; 14,3)` → pieza `(0; −11,2; −11,2)`: la proyección horizontal apunta a **−Y** (atrás).


## 10. Resultado de O07 con el FBX original (2026-11-22)

Con `examples/bolivar_cabeza_lq.fbx` y la pieza real, `align_fbx_to_piece(front=)` deja la cara a 2,6° del frente (`frente_ok`, `yaw180.margen_mm` 0,11). El ajuste no rígido pide, **también con la pose correcta**, 2,11 mm de desplazamiento medio y parte de 32 % de cobertura (la girada: 2,18 mm y 33 %): la cabeza v2 es otro modelado. La suposición de la §3.5 («una pose correcta necesita décimas de mm») no vale para este par. O07 queda así: los umbrales frenan solo si el frente no está verificado; con el frente verificado se aplica y se avisa (`desplazamiento_alto`). Cobertura tras el ajuste: 32 % → 64 %. Abierto para O10: 64 % es poco; la causa es el modelado, no la pose.
