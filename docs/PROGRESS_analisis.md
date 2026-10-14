# PROGRESS — Herramienta para convertir un STL en piezas articulables (estilo Kenner)

_Última actualización: 2026-10-14 03:10 (America/Argentina/Buenos_Aires)_

## Current Focus
Entrevista de análisis de sistemas (Claude = analista, usuario = cliente) y documentación del proceso manual en Onshape para después automatizarlo. El usuario entrega el tutorial paso a paso (`auto_act_fig.md` + capturas; copia en `./proceso_onshape/`). Ya están documentados cabeza, cuerpo, brazo, pierna, mitad de cuerpo, cola y conectores de brazo y cadera (pasos 001–038). Falta: **cuello, cierre del torso (frente/espalda), cola/agujeros, espejado** y el resto del árbol (88 features).

## Todo List

### [ ] Todo
- [ ] Recibir pasos del conector del cuello ("sketch base cuello", "conector cuello", "extrude base conector cuello")
- [ ] Recibir pasos de cierre del torso: "trabe torso", "pared que separa la cola", agujero de la cola, Fillet, Extrude 3/4/5
- [ ] Recibir cómo se espeja (derecho/izquierdo, torso atrás derecha) y cómo se exporta cada STL
- [ ] Verificar sobre los STL finales las medidas de pasadores y holguras (los offsets de Onshape son 0.1–0.3 mm pero las holguras medidas en el STL son ≈0.5 mm)
- [ ] Aclarar: ¿10 cm = piso→coronilla? (medido: 100 mm caen en la coronilla; orejas ~108, sombrero 123.7)
- [ ] Preguntar: impresora/material/tolerancias reales; segunda figura (soldado) para validar qué se repite
- [ ] Definir entradas/salidas, algoritmo, tecnología, riesgos y redactar el plan `.md` final

### [/] In Progress
- [/] Recepción incremental del tutorial de Onshape (pasos 001–038 leídos)

### [x] Done
- [x] Rol de entrevista acordado y flujo general relevado
- [x] STL de Mickey analizados (original + 8 piezas): dimensiones, simetría, holguras
- [x] Tutorial pasos 001–015 (cabeza, cuerpo sin cabeza, brazo izquierdo)
- [x] Tutorial pasos 016–038 (planos de corte, pierna, hueco de cadera, mitad del cuerpo, cola, conectores brazo y cadera) leídos con todas las capturas

## Latest Blockers/Discoveries
- Numeración del md del usuario: los pasos 021 y 022 aparecen duplicados (hay dos "021" y dos "022"); en este archivo se numeran 021–038 según el orden de aparición, con las repeticiones como 021b y 022b.
- En el paso 030 se ve en pantalla el aviso "Sketch could not be solved" (el sketch del círculo de 2.5 mm sobre la cara del brazo); no aclaró cómo lo resolvió.
- El árbol de features todavía muestra pasos sin documentar: conector cuello, "sketch agujero de la cola", "Fillet agujero de la cola 1", "sketch pared que separa la cola", "Extrude 3/4/5", "sketch trabe torso", "articulación cadera / brazo d…", etc.
- Nombres confusos en el tutorial: "articulacion brazo derecho" se refiere en realidad al brazo izquierdo; el 2º STL "brazo_derecho_1" es el espejo.
- Los offsets del tutorial (0.1 mm cadera inicial, 0.3 mm brazo y cadera) no coinciden con las holguras medidas en los STL finales (≈0.5 mm p5, mínimo 0.164 mm): hay pasos posteriores o ajustes que todavía no conocemos.

---

# CONOCIMIENTO ACUMULADO DEL PROCESO

## 1. Entrada y supuestos
- Figura low poly (~6.4k triángulos), simétrica, pose neutra: de pie, mirando al frente, brazos relajados a los costados. Se importa a Onshape como malla.
- Altura objetivo 10 cm "sin contar el sombrero". Medido en Mickey: 100 mm ≈ coronilla del cráneo; orejas ~108 mm; total con sombrero 123.7 × 62.1 × 46.3 mm (alto × ancho × profundidad).
- Original en unidades ~1.13 de alto, Z arriba, no watertight, con 1 triángulo suelto. Piezas finales en mm, Y arriba (factor ≈109.5×).
- **Escala (aclarado por el usuario 02:58):** al principio dimensionó a Mickey en 10 cm desde los pies hasta el tope del sombrero; al final del proceso lo reescaló para que 10 cm sean pies→coronilla. Verificado: 123.74 mm (pies→sombrero en las piezas finales) / 100 mm (pies→coronilla) → factor de reescala ≈ **1.237**. Consecuencia: todas las medidas del tutorial (Ø100, 35, 53.3, 12, 25, 0.4 mm; pasadores Ø2.5/3/4; offsets 0.1/0.3) están en la escala "10 cm hasta el sombrero"; en los STL finales valen ×1.237 (p. ej. holgura 0.4 mm → 0.495 mm, que coincide con la holgura típica medida; 0.1 mm → 0.124 mm). Pasador Ø2.5 → ≈3.1 mm, Ø4 → ≈4.95 mm. La herramienta debe fijar una única altura de referencia y expresar los parámetros en mm finales.
- En los STL finales no encontré pasadores tipo hongo claros en brazo ni pierna (extremos redondeados, sin vástago Ø≈3–5 mm); pendiente de aclarar dónde quedan los conectores finales (¿en el torso? ¿otra versión?).
- Se trabaja con **un lado/medio cuerpo** (corte simétrico) y se espeja después; por eso las piezas finales son espejos exactos (brazos 2088 caras, piernas 2268, torso trasero 3100).

## 2. Salida (Mickey ya hecho): 8 STL
cabeza, torso_adelante_unido (una sola pieza), torso_atras_izquierda + torso_atras_derecha, brazo_derecho + brazo_derecho_1 (espejo), pierna_derecha, pierna_izquierda.
- Estructura tipo Kenner: frente del torso y las dos mitades traseras se cierran "en sándwich" y atrapan brazos y piernas con pasadores tipo hongo (vástago + cabeza).
- pierna_izquierda trae 2 triángulos sueltos (limpieza necesaria).

## 3. Planos de referencia (todos por 3 puntos de malla)
| Nombre | Orientación | Puntos | Uso |
|---|---|---|---|
| plano cabeza cuerpo | oblicuo (inclinado con la cabeza) | 2 en la nuca + 1 bajo el mentón | separa cabeza de cuerpo |
| plano brazo izquierdo | frontal, junto a la unión brazo-torso | 3 puntos del torso (hombro/axila) | sketch de la silueta del brazo |
| corte al medio | frontal/coronal (separa atrás de adelante) | 3 puntos alineados al costado de la esfera de la cabeza | sketch de pierna; split torso en frente/espalda |
| plano corte al medio simetría | sagital (línea central) | 2 en la espalda + 1 en el pecho, sobre la línea central | dividir el cuerpo en mitades |

## 4. Técnica general
**Intersect con herramientas sólidas** (cilindros, losas, polígonos extruidos), `keep tools`, borrar restos; luego **Subtract con offset** de un miembro sobre el torso para hacer su cavidad con holgura; luego **conectores** (hongo) añadidos al miembro.

## 5. Receta Onshape documentada (pasos 001–038)
| Paso | Operación | Parámetros / notas |
|---|---|---|
| 001 | Plano "plano cabeza cuerpo" | 3 puntos (nuca ×2 + mentón) |
| 002 | Sketch "sketch corte cabeza" | círculo Ø100 mm centrado en el cuello |
| 003 | Extrude New "cilindro corte cabeza" | 35 mm, mentón→punta del sombrero |
| 004 | Boolean Intersect (keep tools) × "entero" | → "cabeza" |
| 005 | Extrude New "cilindro corte cuerpo" desde el mismo sketch | 53.3 mm, cuello→pies |
| 006 | Boolean Intersect (keep tools) × "entero" | → cuerpo sin cabeza ("torso atrás izquierda") |
| 007 | "plano brazo izquierdo" | 3 puntos de torso |
| 008 | Sketch "sketch corte brazo izq" | polígono que cubre toda la silueta del brazo (visto de frente) |
| 009 | Extrude New "corte brazo izquierdo" → "recorte brazo izq" | 12 mm |
| 010 | "Sketch 1" en la cara posterior de esa losa | círculo que contiene el torso + polígono que sigue brazo/mano/torso |
| 011 | Extrude Remove (de Sketch 1) | 25 mm blind; evita llevarse torso (corte limpio en hombro) |
| 012 | Boolean Intersect (keep tools) cuerpo × "recorte brazo izq" | → "brazo izquierdo" + "Part 8" |
| 013 | Delete part (Part 8) | |
| 014 | Extrude Remove "Extrude 1" en la cara de contacto del brazo | 0.4 mm; elimina rebaba, contacto ≈ circular; crea "Part 7" |
| 015 | Delete part (Part 7) | |
| 016 | Plano "corte al medio" | 3 puntos alineados al costado de la esfera de la cabeza |
| 017 | Sketch "sketch pierna izq" sobre "corte al medio" | polígono visto de frente que cubre la pierna + parte de la cadera (zona del agarre) |
| 018 | Extrude New "recorte pierna izquierda" | 25 mm **simétrico**, cubre toda la circunferencia de la cadera |
| 019 | Boolean Intersect (keep tools) "entero" × "recorte pierna izquierda" | → "pierna izquierda con articulación" + "pierna geometría ok" (resto) |
| 020 | Delete part (pierna geometría ok) | |
| 021 | Boolean Subtract "hacer hueco cadera en cuerpo" | tool: pierna izq. con articulación; target: torso atrás izq.; **offset 0.1 mm**, keep tools → cuerpo sin pierna con gap |
| 022 | Boolean Subtract "sacar brazo del cuerpo" | tool: brazo izq.; target: torso atrás izq.; offset all **0.3 mm**, keep tools |
| 021b | Plano "plano corte al medio simetría" | 2 puntos espalda + 1 pecho |
| 022b | Split Part "borrar la mitad del cuerpo" | part: torso atrás izq.; plano de simetría; keep tools; se descarta una mitad |
| 023 | Sketch "sketch para borrar cola" | polígono sobre la cara de corte donde quedó un resto de la cola |
| 024 | Extrude New "tool para sacar cola" | 2 mm, cubre toda la cola |
| 025 | Extrude Add blind "recorte cola" | 2 mm |
| 026 | Boolean Subtract "boolean para sacar la cola" | tool: recorte cola; target: torso atrás izq.; keep tools (paso específico de Mickey) |
| 027 | Sketch sobre el plano del hueco de la cadera, en la pierna | círculo que cubre el ancho del pantalón, totalmente dentro de la pierna (equivale al paso 014 del brazo) |
| 028 | Extrude Remove "recorte pierna ingreso cadera" | 25 mm hacia afuera |
| 029 | Split Part "separar torso en dos" | part: torso atrás izq.; entidad: "corte al medio"; keep tools; keep both sides → "torso atrás izquierda" + "torso adelante unido" |
| 030 | Sketch en la cara lisa del torso donde va el brazo | centro hallado con vértices del recorte; círculo Ø2.5 mm |
| 031 | Extrude New "extrude base brazo" → pieza "articulación brazo" | 1.5 mm hacia adentro del torso |
| 032 | Sketch "sketch base conector brazo" sobre la cara del extrude | círculo Ø4 mm centrado |
| 033 | Extrude Add | 1 mm → cabeza del hongo |
| 034 | Sketch en la cadera | centro por vértices; círculo Ø3 mm en el medio del segmento base→afuera |
| 035 | Extrude New | 2.5 mm (vástago) |
| 036 | Sketch sobre la tapa del cilindro | círculo Ø4 mm centrado |
| 037 | Extrude Add blind | 1.5 mm (cabeza del hongo) |
| 038 | Boolean Subtract (ahuecar torso para la articulación de cadera) | tool: pierna izq.; targets: torso atrás izq. + torso adelante unido; offset all 0.3 mm; keep tools |

### Conectores (hongo) documentados
- **Hombro:** vástago Ø2.5 × 1.5 mm hacia adentro del torso + cabeza Ø4 × 1 mm. Cavidad en el torso por Subtract con offset 0.3 mm.
- **Cadera:** vástago Ø3 × 2.5 mm + cabeza Ø4 × 1.5 mm. Cavidad por Subtract con offset 0.3 mm (en ambas mitades del torso).
- Holguras de offset usadas: 0.1 mm (hueco inicial de cadera), 0.3 mm (brazo, cadera).

### Features todavía sin documentar (aparecen en el árbol)
sketch/extrude base conector cuello, conector cuello, sketch base cuello, "sacar del torso el conector…", sketch agujero de la cola, Fillet agujero de la cola, sketch pared que separa la cola, Extrude 3/4/5, sketch trabe torso, articulación cadera / brazo derecho, panel corte piernas, plano brazo derecho, etc. (88 features en total).

## 6. Lo que requiere juicio humano hoy (candidatos a automatizar o parametrizar)
1. Los 3 puntos de cada plano (cuello, brazo, corte al medio, simetría).
2. Polígonos de silueta (brazo de frente, pierna de frente) y polígono/círculo que protege el torso en el hombro.
3. Centro de cada conector: se busca con vértices de la malla (hombro) o a mitad del segmento base→afuera (cadera).
4. Profundidades y espesores que dependen de la figura (35, 53.3, 12, 25 mm...).
5. Limpieza específica de cada personaje (la cola de Mickey).
6. Pendiente: cuello y cierre del torso.

## 7. Ideas de automatización (provisorias, a validar al terminar el tutorial)
- Pipeline en Python (trimesh/manifold) o Blender: normalizar altura → alinear planos (cuello por sección mínima; simetría por plano medio; frente/espalda por plano coronal) → separar partes por booleanos con herramientas generadas (cilindros, losas, siluetas).
- Segmentación automática de brazo/pierna como regiones conectadas lateral/inferior al torso, o aceptar polígonos de silueta como entrada del usuario.
- **Módulo de conectores paramétrico**: hongo = vástago (Ø, largo) + cabeza (Ø, alto), posicionado en el centro de la cara plana del miembro; cavidad = Subtract(miembro + conector, offset = holgura). Parámetros: holgura (0.3 mm), diámetros 2.5/3/4 mm, largos 1.5/2.5 mm, cabeza 1/1.5 mm.
- Simetría: construir un lado y espejar; limpiar triángulos sueltos y reparar malla no watertight.

## 8. Archivos de trabajo
- Tutorial del usuario copiado en `./proceso_onshape/` (md + 116 imágenes).
- Scripts de análisis y renders en el scratchpad de la sesión (no persisten entre sesiones).
- Se instaló trimesh, scipy, networkx, rtree con pip (`--break-system-packages`).
