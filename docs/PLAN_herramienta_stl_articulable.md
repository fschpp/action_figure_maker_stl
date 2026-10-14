# Plan: herramienta de escritorio para convertir un STL en una figura articulable

**Versión:** 0.2 (borrador) — 2026-10-14
**Origen:** entrevista de análisis de sistemas con el cliente (proceso manual en Onshape, STL de Mickey antes/después, tres figuras nuevas, capturas del tutorial).
**Marcas usadas en este documento:** **[C]** confirmado por el cliente o medido en sus archivos · **[I]** inferido por el analista, a validar · **[P]** pendiente de aclarar.

---

## 1. Resumen

Hoy el cliente convierte una figura de acción sin articular (STL de una sola pieza, 10 cm de alto) en una figura articulada al estilo de los Kenner de los 80 a mano en Onshape, con unas 40 operaciones por personaje. Cada personaje nuevo es "un parto". La figura sale **sentada y armada en una sola impresión** (print-in-place): el torso se cierra con dos mitades (frente y espalda) soldadas entre sí, y los brazos y las piernas quedan atrapados en cavidades, girando sobre pasadores tipo hongo, con ≈0,5 mm de holgura. **[C]**

La herramienta propuesta es una **aplicación de escritorio para Linux (Python + GTK4)** en la que el cliente carga un STL, marca planos con 3 puntos sobre el modelo, trabaja un solo lado (la aplicación espeja sola) y obtiene una figura sentada, lista para imprimir, con cada parte como un STL separado en su posición de impresión. **[C]**

## 2. Alcance

**Incluye (versión 1):**
- Brazos y piernas articulables. **[C]**
- Torso construido en dos mitades (frente / espalda) que la herramienta **entrega ya unidas en una sola pieza soldada**. **[C]**
- Pasadores tipo hongo en hombro y cadera, con cavidades y holgura parametrizables. **[C]**
- Límite de giro de la cadera (solo hacia adelante) y pose sentada. **[C]**
- Trabajo con un lado y espejado automático. **[C]**
- Salida: modelo pre-armado y sentado + un STL por parte en posición de impresión. **[C]**
- Reutilización para **≈20 personajes distintos** de la misma línea que Güemes (próceres, estilo Kenner Ghostbusters); Mickey fue el primer caso de estudio. **[C]**
- **Perfil de línea** reutilizable (parámetros comunes a todos los personajes de la línea, ver §8.12). **[I]**
- **Generador de probetas de articulación** para calibrar holgura y diámetros de pasador (ver §8.11). **[C]**
- **Herramienta genérica de recorte manual** (polígono sobre el plano de simetría → extrusión → booleano) para los ajustes propios de cada personaje (ver §8.8). **[C]**

**Fuera de alcance por ahora:**
- **Cuello y cabeza articulados** (el cliente los omitió por ahora). **[C]**
- Soluciones automáticas para cola, pelo, capas y otros apéndices específicos de cada personaje: el cliente no quiere enfocarse en eso; se resuelven con la herramienta de recorte manual (§8.8). **[C]**
- Generar poses distintas de la sentada, o articulaciones adicionales (codos, rodillas, muñecas). **[I]**

## 3. Cómo se hace hoy (proceso manual en Onshape)

Resumen de las ≈40 operaciones documentadas (detalle completo en el Anexo A).

1. **Preparación:** figura low poly, simétrica, de pie, de frente y con los brazos a los costados. Se escala. **[C]**
2. **Planos por 3 puntos de la malla:** cabeza/cuerpo (oblicuo), brazo, "corte al medio" (separa frente y espalda) y simetría (línea central). **[C]**
3. **Separar partes por intersección** de la malla con sólidos auxiliares (cilindros, losas y polígonos extruidos), siempre con "keep tools". Quedan restos que se borran. **[C]**
4. **Limpieza del contacto** de cada miembro con el torso (quitar la "rebaba", dejar una cara casi circular). **[C]**
5. **Cavidades con holgura:** Subtract del miembro sobre el torso con offset (0,1 mm en la cadera inicial, 0,3 mm en brazo y cadera, escala del modelo). **[C]**
6. **Mitad del cuerpo:** se corta por el plano de simetría y se trabaja solo un lado. **[C]**
7. **Pasadores tipo hongo** en hombro y cadera (vástago + cabeza) y sus cavidades. **[C]**
8. **Límite de cadera:** se gira la pierna hasta el máximo ángulo hacia adelante que se quiere, se dibuja sobre la cara plana del hueco de cadera la región que interfiere (con un margen) y se resta del torso (Extrude Remove, 25 mm). **[C]**
9. **Recortes específicos del personaje** (p. ej. la "esfera de la panza" de Mickey, que se resta del pantalón; la cola). **[C]**
10. **Mirror** de las piezas terminadas al lado opuesto. **[C]**
11. **Pose sentada:** se giran las piernas sobre el eje del pasador de cadera. **[C]**
12. **Exportar todo armado**; en el slicer las mitades del torso, que se tocan sin gap, quedan soldadas (en la herramienta salen ya unidas en una sola pieza). **[C]**

**Qué requiere criterio humano hoy:** elegir los 3 puntos de cada plano, dibujar las siluetas de brazos y piernas, ubicar el centro de cada pasador, elegir profundidades y espesores, y decidir los recortes propios de cada personaje. **[C]**

## 4. Entradas y salidas

### Entradas
| Dato | Detalle |
|---|---|
| STL del personaje | Una sola malla, pose neutra, casi simétrica en el eje izquierda-derecha (desvío mediano ≈0 % de la altura y p95 ≤ 0,5 % en las 4 figuras analizadas). **[C]** |
| Altura de referencia | Pies → coronilla = 100 mm (el cliente la define así; sin sombrero ni orejas). **[C]** |
| Puntos de los planos | 3 puntos sobre la malla por cada plano (cabeza/cuerpo, brazo, pierna, corte al medio, simetría). **[C]** |
| Parámetros | Holgura, medidas de pasadores, ángulo de la pose sentada, margen del límite de cadera, boquilla (ver §5). |

### Salidas
| Archivo | Contenido |
|---|---|
| `<figura>_ensamblada.stl` | Todas las partes en su posición de impresión (sentada), en un solo STL con las piezas como cáscaras separadas. **[C]** |
| `piezas/<figura>_<parte>.stl` | Un STL por parte (**torso en una sola pieza soldada**, brazo izquierdo/derecho, pierna izquierda/derecha; cabeza fuera de alcance en v1), todos en el **mismo sistema de coordenadas** que el ensamblado, para que el slicer los cargue como un objeto con varias partes. **[C]** |
| `<figura>.proyecto` | Archivo del proyecto (planos, puntos, parámetros) para reabrir y ajustar. **[I]** |
| Informe de validación | Distancias mínimas entre partes, partes no estancas (non-manifold), zonas voladizas. **[I]** |

**[C]** El torso puede salir como **una sola pieza soldada** (frente + espalda unidos); la herramienta hace la unión.

## 5. Parámetros y escalas

### 5.1 Regla de escala
El modelo de Onshape medía **74,849 mm** de los pies al tope del sombrero; las piezas finales miden **123,74 mm**. Factor de reescala k ≈ **1,653**. **[C]** (Medido en Onshape y en los STL finales; los pasadores y las holguras medidos en el STL final coinciden con las del tutorial multiplicadas por k.) Todas las cifras del tutorial están en la escala del modelo de Onshape; en los STL finales valen ×1,653.

> Decisión de diseño: la herramienta trabaja siempre en **milímetros finales**, con la figura normalizada a 100 mm de los pies a la coronilla. Como todas las figuras se imprimen a 10 cm, los valores absolutos (pasadores, holguras) valen igual para todas.

### 5.2 Valores por defecto (Mickey, en mm finales)
| Parámetro | Tutorial (Onshape) | Por defecto (mm finales) | Verificación en STL final |
|---|---|---|---|
| Holgura típica (cavidad vs. miembro) | 0,3 mm | **0,50** | 0,495 medido **[C]** |
| Holgura inicial del hueco de cadera | 0,1 mm | **0,165** | 0,164 medido **[C]** |
| Hombro: vástago Ø / largo | 2,5 / 1,5 mm | **4,1 / 2,5** | Ø4,18 medido **[C]** |
| Hombro: cabeza Ø / alto | 4 / 1 mm | **6,6 / 1,65** | Ø6,6 medido **[C]** |
| Cadera: vástago Ø / largo | 3 / 2,5 mm | **5,0 / 4,1** | — |
| Cadera: cabeza Ø / alto | 4 / 1,5 mm | **6,6 / 2,5** | Ø6,6 medido **[C]** |
| Rebaba a rebajar en la cara de contacto | 0,4 mm | **0,66** | — |
| Ángulo de la pose sentada | "idealmente 90°" | **90°** (hacia adelante, sobre el eje del pasador de cadera) | **[C]** dicho por el cliente |
| Margen del límite de cadera | "deja un margen" | **5°** más allá de los 90° (recorte hasta 95°) | **[C]** dicho por el cliente |

### 5.3 Impresión
Bambu Lab A1, PLA, capa de 0,4 mm; las figuras finales se imprimen con **boquilla de 0,4 mm** (la de 0,8 mm también existe). **[C]** Con 0,4 mm la holgura de 0,5 mm equivale a ≈1 línea de extrusión (0,42 mm). La holgura queda como parámetro con valor por defecto según boquilla; El cliente imprimirá **probetas con distintos tamaños** (holguras y diámetros de pasador) para garantizar una **mínima fricción** que sostenga la pose; la herramienta incluirá un generador de probetas (§8.11). **[C]**

## 6. Arquitectura propuesta

**Lenguaje y toolkit: Python 3 + GTK4** (PyGObject), por la preferencia del cliente por Linux + GTK + Python. **[C]**

| Capa | Tecnología | Rol |
|---|---|---|
| Interfaz | GTK4 (+ libadwaita opcional) | Ventana principal, lista de planos, panel de parámetros, barra de progreso |
| Visor 3D | `Gtk.GLArea` + OpenGL (moderngl) | Mostrar malla y planos, orbitar, recortar, seleccionar puntos |
| Selección de puntos | Ray-casting (trimesh / embree) con ajuste al vértice más cercano | Pedir 3 puntos y generar el plano |
| Geometría | `trimesh` + `manifold3d` + `numpy` / `scipy` | Booleanos robustos, secciones, simetría, reparación |
| Offset 3D | Campo de distancia con marching cubes (scikit-image u OpenVDB) o, si está disponible, suma de Minkowski de manifold3d | Cavidades con holgura |
| Proyecto | JSON | Guardar planos, puntos y parámetros |
| Empaquetado | Instalable con `pip`/`pipx` y, más adelante, Flatpak | Entregar al cliente una instalación simple |

**Por qué no FeatureScript:** obliga a trabajar dentro de Onshape, depende de poder seleccionar puntos sobre mallas importadas y de la cuenta del cliente, y complica la pose sentada y la salida de piezas separadas en posición de impresión. **[I]** Se considera solo como plan B.

El cliente **no programa**: yo escribo y entrego la aplicación. **[C]**

## 7. Interfaz y flujo de uso

1. **Abrir STL** → la aplicación repara lo básico (triángulos sueltos, no-estanco), lo orienta (Z arriba) y lo escala a 100 mm de los pies a la coronilla (pide confirmar la coronilla con un clic).
2. **Agregar plano** (botón por cada tipo: *brazo*, *pierna*, *corte al medio*, *simetría*) → el visor pide **3 puntos sobre el modelo**; al tercero **solo genera el plano** y lo muestra. Se puede mover o rehacer. **[C]**
3. **Trabajar un solo lado:** la aplicación oculta o atenúa la mitad opuesta; todo lo que se defina se **espeja automáticamente** al otro lado. **[C]**
4. **Siluetas y centros de pasador:** la aplicación propone automáticamente la silueta del brazo y de la pierna y el centro de cada pasador; el cliente puede **ajustarlos** (arrastrar vértices de un polígono, mover el centro). **[I]**
5. **Parámetros** (holgura, pasadores, ángulo de sentada, margen del límite de cadera, recortes por esfera).
6. **Vista previa de la pose:** control deslizante del ángulo de cadera, con detección de colisión y del recorrido libre.
7. **Generar:** produce las partes, valida y exporta (§4). El informe de validación se muestra antes de guardar.

## 8. Módulos y algoritmos

### 8.1 Importación y normalización
- Cargar STL, unir vértices, quitar componentes menores (por ejemplo, los 2 triángulos sueltos en la pierna izquierda de Mickey), reparar agujeros y normales. En Azurduy hay que reparar antes de usar (no es estanca). **[C]**
- Normalizar unidades: Mickey viene en ≈1,13 de alto, Belgrano en 1,159, Azurduy en 1,135 y Güemes en 114 mm. La aplicación escala a 100 mm pies→coronilla.
- Eje de simetría: se calcula el plano medio del cuerpo y se verifica la simetría (en las 4 figuras es casi exacta: desvío mediano ≈0 % de la altura, p95 ≤ 0,5 %). Como no es perfecta, la herramienta construye un solo lado y espeja, así el resultado queda perfectamente simétrico.

### 8.2 Planos por 3 puntos
- Cada plano es un objeto con nombre, tipo, 3 puntos, normal y origen; es editable y se guarda en el proyecto.
- Tipos y uso **[C]**: *cabeza/cuerpo* (oblicuo; fuera de alcance en v1), *brazo* (separa el brazo del torso), *corte al medio* (frente/espalda, usado también como plano de la pierna), *simetría* (línea central).

### 8.3 Separación de cuerpo, brazos y piernas
Se replica la técnica del cliente: **intersección con sólidos auxiliares**.
- Brazo: losa extruida de la silueta del brazo (de espesor suficiente para cubrirlo) restada con la región de torso/hombro, intersectada con el cuerpo → brazo + resto; se descarta el resto. **[C]**
- Pierna: polígono sobre el plano de corte al medio, extruido simétrico para cubrir toda la circunferencia de la cadera, intersectado con el cuerpo. **[C]**
- Limpieza de la cara de contacto: se rebaja ~0,66 mm para dejar una cara casi circular y se eliminan los restos. **[C]**
- **Automatización propuesta [I]:** las siluetas se derivan de la malla por **componentes conectados lateral/inferior al torso**, apoyándose en el plano del brazo y en el plano de corte al medio. En Güemes, Belgrano y Azurduy hay 2 contornos horizontales por debajo de ≈0,35 H (las piernas) y 3 entre ≈0,40 y 0,65 H (torso y dos brazos separados). En Mickey la sección a la altura del pecho es de un solo contorno (el brazo está pegado al cuerpo), por lo que la separación **no puede depender solo de contar contornos**. Se ofrece un editor de polígono para corregir.

### 8.4 Cavidades con holgura
Subtract del miembro (más el pasador) sobre ambas mitades del torso, con offset = holgura. Se valida que la distancia mínima miembro–torso sea ≥ holgura − tolerancia, y que la unión de las dos mitades en una sola pieza sea estanca (sin non-manifold). **[C]**

### 8.5 Pasadores tipo hongo
- Ubicación del centro: sobre la cara plana del miembro, a partir de vértices de la malla (hombro) o a la mitad del segmento base→afuera (cadera). **[C]** Se propone calcularlo automáticamente (centroide de la cara de contacto) y dejar el ajuste manual.
- Geometría: cilindro (vástago) + cilindro (cabeza) con borde achaflanado, según §5.2; eje perpendicular a la cara de contacto, hacia el centro del cuerpo. **[C]**
- En los STL finales de Mickey el eje del pasador es el eje X (de izquierda a derecha). **[C]**

### 8.6 Límite de cadera y pose sentada
- La pierna gira sobre el eje del pasador; hacia adelante se talla en el torso un recorte (Extrude Remove) hasta el ángulo máximo deseado, dejando un margen. **[C]** Que el límite hacia atrás surja de la cavidad original sin recortar es una interpretación del analista. **[I]**
- **Automatización propuesta [I]:** en vez de dibujar la región a mano, la aplicación calcula el **volumen barrido** de la pierna (más la holgura) al girar de 0° al ángulo máximo + margen, y lo resta de ambas mitades del torso.
- **Pose sentada:** rotación de ambas piernas por el ángulo elegido alrededor de su pasador; luego se calcula la orientación de impresión (la figura apoyada sobre la cola/asiento y los pies; ver §8.7). Ángulo: **90°** (ideal, según el cliente). **[C]**

### 8.7 Orientación e impresión
- Apoyar la figura sentada sobre la cama: se parte de la orientación "sentada" y se busca el apoyo estable de menor área voladiza; el cliente puede ajustar con un control de inclinación.
- Previsualizar zonas voladizas (caras con ángulo > 45°) para decidir soportes. **[I]**

### 8.8 Recortes específicos del personaje (herramienta de recorte manual)
Mickey necesitó una esfera que se resta del pantalón (la "esfera panza") y una limpieza de la cola. **[C]** El cliente no quiere enfocar la herramienta en esos casos, pero aclaró que **el método es siempre el mismo**: trabaja con la mitad del torso, dibuja un sketch (polígono) sobre el plano de simetría, lo extruye y hace un booleano. **[C]**
Se implementa como **una sola herramienta genérica**:
1. Elegir la pieza (torso, pierna, brazo) y el plano (por defecto el de simetría).
2. Dibujar un polígono sobre el plano, con vista alineada al plano y ajuste a los vértices de la malla.
3. Extruir (distancia, simétrica o hacia un lado; opcional "hasta atravesar").
4. Elegir el booleano (**restar** o **unir**) y aplicarlo; el recorte queda en una lista y se puede editar o quitar.
Opcional: formas predefinidas (esfera, caja, cilindro) para casos como la panza. Los recortes se espejan junto con el resto del lado de trabajo.

### 8.9 Espejado y ensamblado
Se construye solo el lado izquierdo y se espeja por el plano de simetría. El espejado se hace por reflexión de los vértices con inversión del orden de los triángulos (como el Mirror de Onshape). Se validan las piezas espejadas (estanqueidad, sin intersecciones).

### 8.10 Exportación
STL binario, mm, sistema de coordenadas común. Se genera el ensamblado y las piezas separadas en el mismo marco.

### 8.11 Generador de probetas de articulación
El cliente imprimirá probetas para elegir los valores con su impresora (Bambu Lab A1, boquilla 0,4 mm) y busca una **mínima fricción** que sostenga la pose. **[C]**
- La herramienta genera un STL de prueba con **varias articulaciones idénticas a las de la figura** (pasador + cavidad), cada una con una combinación distinta de **holgura** y de **diámetro de vástago/cabeza**, con etiqueta en relieve.
- Rangos por defecto **[I]**: holgura 0,3 / 0,4 / 0,5 / 0,6 mm; vástago Ø±0,2 mm respecto del valor por defecto. Editables.
- El resultado del ensayo se guarda en el **perfil de línea** (§8.12).

### 8.12 Perfil de línea (para ≈20 personajes)
Un perfil guarda los parámetros comunes a una línea de figuras (altura de referencia, holgura, medidas de pasadores, ángulo de pose sentada = 90°, margen = 5°, boquilla). Al abrir un personaje nuevo se elige el perfil y solo se definen los planos y siluetas propios de la figura. **[I]** Objetivo: que el 2.º y siguientes personajes lleven mucho menos trabajo que el primero.

## 9. Validaciones automáticas
- Todas las piezas son estancas (watertight) y sin non-manifold edges (Bambu Studio avisa "non-manifold edge"; hoy pasa con la pierna izquierda de Mickey).
- Distancia mínima entre miembros y torso ≥ holgura − tolerancia.
- El torso unido (frente + espalda) es una sola pieza estanca y sin non-manifold edges; las cavidades de los miembros conservan la holgura después de la unión.
- Los miembros no colisionan entre sí en todo el recorrido (0° → ángulo máximo).
- Espesor mínimo de pared ≥ 1,2 mm (≈ 3 líneas de 0,4 mm). **[I]**
- Diámetro mínimo de pasadores ≥ 3 mm. **[I]**
- Informe visual (qué se movió, qué se restó, dónde hay contacto).

## 10. Riesgos y mitigaciones
| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Offset 3D (cavidades con holgura) sobre mallas con ≈10 000 triángulos: lento o inexacto | Prototipo temprano con SDF + marching cubes a 0,05–0,1 mm; comparar con el resultado de Onshape en Mickey |
| R2 | La segmentación automática de brazos y piernas falla con ropa (casacas, faldones, pelo) | Editor de polígonos como respaldo; probar con Güemes, Belgrano y Azurduy desde la primera fase |
| R3 | Mallas no estancas (Azurduy) rompen los booleanos | Reparación previa y verificación antes de cada operación |
| R4 | Holgura de 0,5 mm se fusiona al imprimir | Parámetro por boquilla; imprimir probetas de articulación antes de cerrar valores |
| R5 | Detalles finos de la ropa (≈ 0,4 mm) se funden con la boquilla de 0,4 mm | Avisar al cliente en el informe; no se corrige en la herramienta |
| R6 | Brazos más pegados al torso en las figuras realistas dejan poco lugar para la holgura y el pasador | Validar espesores; permitir desplazar el centro del pasador |
| R7 | El visor GTK + OpenGL y la selección de puntos son los componentes más pesados de hacer | Empezar con un visor mínimo; construir primero el motor y probarlo con scripts |
| R8 | El cliente no puede mantener el código | El código queda documentado y con tests; el cliente usa la aplicación, no la modifica |
| R9 | Con ≈20 personajes aparecerán casos no previstos (pelo, capas, accesorios) | Herramienta de recorte manual genérica (§8.8) y perfiles de línea; probar con Güemes, Belgrano y Azurduy antes de abrir el resto |

## 11. Fases propuestas
| Fase | Entregable | Criterio de aceptación |
|---|---|---|
| **0. Prototipo del motor** (sin interfaz) | Script que, dado el STL de Mickey y los planos, genere las piezas | Piezas comparables con las del cliente (distancia máxima entre mallas ≤ 0,3 mm; holguras medidas ≈ 0,5 mm) |
| **1. Repetibilidad** | Mismo script probado con Güemes, Belgrano y Azurduy | Las 3 salen en la primera pasada con ajustes de silueta, sin recurrir a Onshape |
| **2. Visor y planos** | Aplicación GTK con visor, selección de 3 puntos y planos | El cliente define los planos de una figura en menos de 5 minutos |
| **3. Un lado + espejado + pasadores** | Flujo completo de un lado con vista previa | El espejado es idéntico al Mirror de Onshape |
| **4. Límite de cadera, pose sentada, recortes manuales y exportación** | Salida final (ensamblado + piezas), herramienta de recorte manual (§8.8) y generador de probetas (§8.11) | La figura se imprime sentada y las articulaciones se mueven sin romper; las probetas permiten elegir holgura y diámetros |
| **5. Pulido, perfiles de línea y empaquetado** | Instalable, manual corto, ejemplos, perfil de línea (§8.12) | El cliente repite el proceso solo con una figura nueva; el tiempo por personaje baja claramente respecto del proceso manual (a medir con los primeros 3–5 personajes de la línea) |

## 12. Preguntas abiertas y decisiones cerradas
**Cerradas con el cliente:**
1. Pose sentada: **90°** (ideal); se gira con una rotación sobre el eje del pasador de cadera. **[C]**
2. Torso: **una sola pieza soldada** (frente + espalda unidos). **[C]**
3. Margen del límite de cadera: **5°** (recorte hasta 95°). **[C]**
4. Los personajes de la línea de Güemes llevan **los mismos pasadores en hombros y caderas** que Mickey. **[C]** (El cliente escribió "hombros y brazos"; se interpreta como hombros y caderas, **[I]** confirmar.)
5. Recortes específicos (cola, panza): no son el foco; se hace con el mismo método (polígono sobre el plano de simetría → extruir → booleano). **[C]**
6. Probetas: sí, con distintos tamaños, buscando mínima fricción. **[C]**
7. Alcance de uso: **≈20 personajes distintos** en el estilo de Güemes. **[C]**

**Abiertas:**
1. **[P]** Cabeza: ¿se une al torso con cuello fijo o se imprime aparte? Fuera de alcance por ahora, pero define cómo se corta el torso.
2. **[P]** Rangos de holgura y de diámetros de pasador para las probetas (¿le sirven los valores por defecto de §8.11?).
3. **[P]** Confirmar que "brazos" en la respuesta 5 significa "caderas".
4. **[P]** Ver capturas de la feature de rotación de las piernas, el Mirror y la exportación, para replicar el orden exacto de operaciones.

---

## Anexo A. Equivalencia entre el tutorial (Onshape) y la herramienta

| Paso del tutorial | Operación en Onshape (escala del modelo) | Módulo de la herramienta |
|---|---|---|
| 001–004 | Plano de 3 puntos en el cuello, círculo Ø100, extrude 35 mm, intersect → cabeza | §8.2 / §8.3 (cabeza: fuera de alcance v1) |
| 005–006 | Extrude 53,3 mm desde el mismo sketch, intersect → cuerpo sin cabeza | §8.3 |
| 007–013 | Plano del brazo, silueta, losa 12 mm, sketch con círculo + polígono, remove 25 mm, intersect, borrar resto | §8.3 |
| 014–015 | Rebajar 0,4 mm la cara de contacto del brazo y borrar el resto | §8.3 |
| 016–020 | Plano "corte al medio", silueta de la pierna, extrude 25 mm simétrico, intersect, borrar resto | §8.2 / §8.3 |
| 021 | Subtract del hueco de cadera con offset 0,1 mm | §8.4 |
| 022 | Subtract del brazo del cuerpo con offset 0,3 mm | §8.4 |
| 021b–022b | Plano de simetría y Split para trabajar medio cuerpo | §8.2 / §8.9 |
| 023–026 | Limpieza de la cola | §8.8 |
| 027–028 | Círculo en el hueco de cadera, extrude remove 25 mm | §8.3 |
| 029 | Split del torso con "corte al medio" → frente y espalda | §8.2 / §8.3 |
| 030–033 | Pasador del hombro (Ø2,5 × 1,5 + Ø4 × 1) | §8.5 |
| 034–037 | Pasador de la cadera (Ø3 × 2,5 + Ø4 × 1,5) | §8.5 |
| 038 | Subtract de la cadera sobre ambas mitades, offset 0,3 mm | §8.4 |
| Capturas 03:34 | Extrude Remove 25 mm para el límite de cadera, con margen | §8.6 |
| Capturas 03:37 | Revolve "panza" + Subtract "pierna sin esfera panza" | §8.8 |
| Respuesta 16:28 | Recorte manual: polígono sobre el plano de simetría → extrude → booleano | §8.8 |
| (sin documentar) | Mirror, giro de piernas a la pose sentada (rotación sobre el eje del pasador), exportación | §8.9 / §8.6 / §8.10 |

## Anexo B. Datos medidos en los archivos del cliente
- **Mickey, 8 piezas:** cabeza, torso adelante unido, torso atrás derecha/izquierda, 2 brazos, 2 piernas. Ensamblado 62,1 × 123,7 × 46,3 mm. Simetría exacta (brazos de 2088 caras, piernas de 2268, torso trasero de 3100). Holgura típica ≈0,495 mm, mínima ≈0,164 mm. **[C]**
- **Mickey original** (`bajo_poligonos.stl`): 6447 triángulos, no estanco, un triángulo suelto. **[C]**
- **Pasadores medidos:** hombro (eje X): cabeza Ø≈6,6 y vástago Ø≈4,2; cadera: cilindro Ø6,6. **[C]**
- **Güemes / Belgrano / Azurduy:** una sola malla cada una (≈10 700 triángulos), casi exactamente simétricas (desvío mediano ≈0 %, p95 ≤ 0,4 % de la altura), de pie con los brazos a los costados; Azurduy no es estanca. Mickey (original) también es casi simétrico (mediano 0,1 %, p95 0,5 %), pero su brazo está más pegado al torso que en las tres figuras nuevas. **[C]**
- **Impresión:** Bambu Lab A1, PLA, capa 0,4 mm; boquilla 0,4 mm para las figuras finales (0,8 mm disponible). **[C]**
