# Tutorial: interfaz gráfica (`afm-gui`)

Guía de uso de la ventana de escritorio. Cubre lo que la interfaz hace hoy (Fase 2: visor y planos).
Lo que todavía no está en la ventana se explica al final, en [Lo que falta](#lo-que-falta-en-la-interfaz).

> Primera versión, escrita a partir del código. Si algo no se comporta como acá se describe, avisá
> y se corrige el tutorial o el programa.

## 1. Abrir la aplicación

Con el entorno virtual activado (ver el README):

```bash
afm-gui                     # ventana vacía
afm-gui figura.stl          # abre directamente un STL
afm-gui figura.proyecto     # abre un proyecto guardado
afm-gui figura.proyecto --stl nueva/ruta/figura.stl   # si el STL cambió de lugar
```

## 2. La ventana

```
┌───────────────────────────────────────────────────────────────┐
│ [Abrir STL…] [Abrir proyecto…]            [Guardar proyecto…] │
├──────────────────────────────────────────┬────────────────────┤
│                                          │ Figura             │
│                                          │ Planos             │
│            visor 3D                      │ Vista              │
│                                          │ Estado             │
└──────────────────────────────────────────┴────────────────────┘
```

* **Barra superior:** abrir un STL, abrir un proyecto y guardar el proyecto.
* **Visor 3D** (izquierda): la figura y los planos.
* **Barra lateral** (derecha): todos los controles, en secciones: Figura, Planos, Silueta del miembro, Pasador de brazo / cadera, Generar, Vista y Estado.
* **Título de la ventana:** muestra el nombre del archivo y, mientras hay una selección en curso,
  qué se está haciendo (por ejemplo «plano de simetría»).

## 3. Moverse por el visor

| Acción | Cómo |
|---|---|
| Orbitar | Arrastrar con el botón izquierdo |
| Desplazar (pan) | Arrastrar con el botón medio o el derecho, o **Mayús** + arrastrar con el izquierdo |
| Zoom | Rueda del ratón |
| Elegir un punto | **Clic corto** con el botón izquierdo (solo cuando hay una selección en curso, ver §5) |
| Vistas rápidas | Botones **Frente, Espalda, Izquierda, Derecha, Arriba, Iso** (sección Vista) |
| Volver a encuadrar | Botón **Encuadrar figura** |

Un arrastre de menos de unos pocos píxeles cuenta como clic. Si querés orbitar y te marca un punto
sin querer, arrastrá un poco más.

## 4. Preparar la figura

### 4.1 Abrir el STL

**Abrir STL…** y elegí el archivo. La figura se repara si hace falta, se apoya en z = 0 y se escala
para que mida **100 mm** de los pies al punto más alto. El panel **Estado** muestra avisos (por
ejemplo, que la coronilla no se indicó y se usó el punto más alto).

### 4.2 Frente hacia +Y / −Y

La herramienta espera que la figura mire hacia **+Y**. Para saber si es así, apretá **Frente** en
Vista: ese botón mira la figura desde +Y.

* Si ves a la figura **de cara**, dejá **+Y**.
* Si ves la **espalda**, la figura mira hacia −Y: elegí **−Y** en «Frente hacia».

> **Ojo:** cambiar «Frente hacia» **borra la simetría y todos los planos** de los miembros, porque
> dependen de la orientación. Hacelo antes que nada.

### 4.3 Marcar la coronilla (opcional)

Si la figura lleva sombrero, orejas o algo que sobresale de la cabeza, el «punto más alto» no es la
coronilla y la escala sale mal (la figura debe medir 100 mm de los pies a la **coronilla**).

1. Apretá **Marcar coronilla (clic)**.
2. Hacé clic en el punto más alto de la cabeza **sin** el sombrero.
3. La figura se reescala para que esa altura valga 100 mm y se reencuadra la cámara.

La coronilla **solo se puede marcar antes** de definir la simetría y los miembros, porque cambia la
escala y rompería los planos ya creados. Si ya los definiste, el panel Estado lo avisa; quitá
primero los planos.

## 5. Elegir 3 puntos (cómo funcionan los planos)

Los planos se definen con **3 puntos sobre la figura**, como en Onshape. El proceso es el mismo para
todos:

1. Apretás el botón del plano que querés. El panel **Estado** dice qué hacer.
2. Hacés clic en 3 puntos de la figura. Cada punto se marca en rojo en el visor.
3. Al tercer clic se crea el plano.

Detalles útiles:

* Los puntos **se ajustan al vértice más cercano** si hay uno a menos de 1,5 mm del clic, así que
  podés apuntar a esquinas y vértices de la malla con precisión.
* **Retroceso (Backspace)** deshace el último punto.
* **Esc** cancela la selección.
* Si los 3 puntos están alineados no definen un plano: el Estado lo avisa y empezás de nuevo.
* Si el clic no toca la figura, no se cuenta.

## 6. Plano de simetría

Es el plano que parte la figura en izquierda y derecha. Se trabaja un solo lado (el de x positivo) y
el otro se espeja.

**Opción rápida:** **Simetría automática (x = 0)**. La normalización ya centra la figura en X, así
que en figuras casi simétricas alcanza con esto.

**Opción manual:** **Plano de simetría (3 puntos)** y elegí 3 puntos sobre la **línea central**, por
ejemplo 2 en la espalda y 1 en el pecho. El plano tiene que ser **vertical**; si no lo es, se
rechaza con un aviso.

Al definirlo, la figura se gira y se desplaza para que ese plano quede en **x = 0** (el plano
aparece en naranja) y se habilitan el resto de los controles.

## 7. Planos de brazo y pierna

Una vez definida la simetría:

* **Agregar plano de brazo (3 puntos)**
* **Agregar plano de pierna (3 puntos)**

Cada uno pide 3 puntos y crea un miembro con nombre propio: `brazo`, `pierna`, y si agregás más,
`brazo 2`, `pierna 2`, etc. Es el plano **sobre el que se dibujará la silueta** del miembro.

El plano es **el que definen tus 3 puntos**, sea cual sea su orientación: no se valida contra ninguna
dirección. Lo normal es apoyarlos sobre una cara chata del STL (la del hombro, o la de «corte al
medio» para la pierna), con vértices que no estén alineados. La vista es **ortográfica** (sin
perspectiva), así que lo que se ve en pantalla no se agranda ni se achica según la distancia y las
vistas Frente/Izquierda/Arriba se pueden usar para ubicar los puntos con precisión. Los planos de brazo
se dibujan en azul y los de pierna en verde.

Si el programa responde «No sirve: los 3 puntos son colineales», cambiá uno de los clics a un vértice
que no esté sobre la recta de los otros dos.

### 7.1 Dibujar la silueta del miembro

La silueta es el polígono que separa el brazo o la pierna del resto del cuerpo. Se dibuja **sobre el
plano del miembro**, así que primero ponete en la vista **Frente** (el plano se tiene que ver de cara).

1. En **Planos**, apretá el lápiz (**Dibujar / editar la silueta**) del miembro. Aparece, en gris, el
   **contorno de la figura proyectado sobre ese plano** (solo el lado de trabajo, x ≥ 0): es la guía para
   calcar encima.
2. **Clic = vértice.** El clic no busca la malla: cae sobre el plano del miembro. Poné los vértices
   rodeando el miembro, **del lado del torso por donde querés que se corte** (ahí quedará la cara de
   contacto del pasador).
3. **Cerrar:** clic sobre el primer vértice, **Intro** o **Cerrar polígono**. Hacen falta al menos 3.
   Retroceso deshace el último vértice mientras dibujás.
4. Ya cerrado, cada cambio **se guarda en el proyecto al instante**:
   * clic cerca de un vértice lo **elige** (se ve en rojo) y el **siguiente clic en el plano lo mueve**;
     un clic en el mismo vértice lo suelta;
   * clic sobre un **borde** agrega un vértice ahí;
   * con un vértice elegido, **Retroceso** (o Supr) lo borra (mínimo 3).
5. **Empezar desde el contorno de la figura** carga el contorno exterior más grande como polígono base
   (simplificado a ~0,6 mm) para que lo recortes moviendo y borrando vértices.
   **Vaciar y redibujar** lo borra. **Esc** o **Terminar edición** sale del modo.

6. **Proponer silueta automática** reemplaza el borrador por la que calcula el programa a partir de cortes
   horizontales de la figura. Funciona con **el plano que hayas elegido**, sea cual sea su orientación: el
   programa decide qué parte del cuerpo es el miembro y la proyecta sobre tu plano a lo largo de su normal.
   Con el plano lateral del hombro (normal ≈ X) el brazo queda dibujado **visto de costado**, de la mano
   al hombro; con el plano «corte al medio» la pierna queda vista de frente: columna hasta la entrepierna
   más un trozo de cadera (x > 7 mm, 10 mm hacia arriba). Es un **punto de partida**: revisala y mové los
   vértices que haga falta. Avisa si el miembro no queda separado del tronco en ningún tramo (entonces hay
   que dibujarla a mano). **Dónde queda el corte del brazo lo decide la posición de tu plano** (la losa sale
   hacia +X desde él); el programa no lo mueve.
   **Con abrigo o faldón largo** (Belgrano, Azurduy) la pierna propuesta arranca en el borde de la prenda,
   unos 8 mm más abajo que la cadera real: subí los vértices de la cadera a mano.

Si el polígono **se cruza consigo mismo** (un «moño») o queda sin área, el Estado lo avisa y **no se
guarda** hasta que lo corrijas. Si el plano se ve de canto desde la vista actual, el clic no agrega
nada y el Estado te pide girar la cámara. Las siluetas ya guardadas se dibujan en amarillo; el ojo de la
lista de planos las muestra u oculta junto con su plano. «Pendiente» deja de listar al miembro cuando
tiene su silueta.

### 7.1b El 2.º corte (frente + costado)

Con **un solo plano** el miembro no siempre queda bien cortado. Es lo mismo que en el tutorial de Onshape: el brazo
de Mickey necesita **dos** recortes (el plano de costado del hombro y, sobre su cara, un sketch de frente que deja
afuera el torso), y la pierna necesita su silueta de frente. Si con el plano de costado de la pierna ves que **parte
del muslo queda pegado al torso** (el torso arranca mucho más abajo que la cadera, el pasador sobresale, las dos
piernas se pisan en x = 0), te falta el 2.º corte.

1. En la lista de planos, el botón **«2.º»** del miembro abre el dibujo de su segundo corte. La cámara se pone de
   frente al plano **frontal** (el que divide la figura en atrás/adelante, perpendicular al plano del miembro). Si el
   plano del miembro ya fuera frontal, el 2.º corte sería de costado.
2. Se dibuja igual que la silueta (clic, cerrar, mover vértices, **Proponer silueta automática**). Lo que
   dibujes es **lo que cubre al miembro visto de frente**:
   * **pierna**: la columna del muslo desde la simetría (x = 0) hasta el pie, más el **escalón de la cadera**
     (el borde vertical del escalón, a x ≈ 7 mm, es la cara donde va el pasador);
   * **brazo**: el brazo con la mano y un pedacito del hombro; lo que queda del torso se deja afuera.
3. El miembro es **lo que queda dentro de las dos siluetas** (la de costado/del plano y la de frente). El contorno
   del 2.º corte se ve en **cian**; el de la silueta del plano, en amarillo. El botón **−** al lado de «2.º» lo quita.
4. **La altura de la cadera no la limita el 2.º corte.** Su borde superior (el techo del escalón) fija hasta dónde
   llega la cara de contacto y, con ella, la altura del hueco y del pasador: si queda más bajo que la silueta
   de costado, el pasador no puede subir. Con el 2.º corte abierto, **«Subir el borde superior hasta la otra
   silueta»** lleva ese borde a la altura máxima de la silueta de costado (la propuesta automática ya lo hace);
   desde ahí la altura la marca la silueta de costado, y también podés mover a mano los dos vértices del techo.
5. En una **pierna** con plano de costado, al guardar el 2.º corte el programa deja de limitar la silueta de costado
   a «hacia afuera desde el plano» y la hace atravesar todo el cuerpo: la cara de la cadera la fija el escalón de
   frente. En un **brazo** no cambia nada: el plano de costado sigue marcando dónde se separa del torso.

### 7.2 Colocar el pasador (brazo y cadera)

El pasador tipo hongo va sobre la **cara de corte** del miembro: la cara plana que queda donde la silueta lo
separa del torso. Si no hacés nada, el programa lo centra solo (en el círculo más grande que entra en esa cara;
en la pierna lo sube un tercio de ese radio hacia el torso). Cuando esa posición no te sirve, podés **elegirla
vos sobre la propia cara**:

1. El miembro tiene que tener su silueta. En la lista de **Planos**, apretá el marcador
   (**Colocar el pasador sobre la cara de corte**) de ese miembro. Puede tardar unos segundos: se corta solo ese
   miembro para encontrar la cara.
2. La cámara se pone **de frente a la cara de corte** y se **recorta el torso** que la tapa (solo en pantalla).
   La cara se ve en azul con su contorno en blanco, y se dibujan:
   * el **círculo naranja**: el vástago del pasador (Ø de hombro o de cadera, según el miembro);
   * el **círculo amarillo**: la cabeza del hongo, para que veas cuánto ocupa;
   * el **punto rojo**: el centro actual; el **punto verde** aparece cuando ya moviste el pasador y marca dónde
     lo habría puesto el programa.
3. **Clic sobre la cara** = el pasador se mueve ahí. El clic cae sobre el plano de corte, no sobre la malla, y
   se puede elegir **cualquier punto**, incluso con el centro fuera de la cara (el Estado lo anota); solo se rechaza si el vástago ni roza la cara (quedaría suelto).
   Las **flechas** lo ajustan de a 0,1 mm (con **Mayús**, 1 mm), siempre vistas de frente.
4. El Estado informa cada vez el **margen entre el vástago y el borde de la cara**: avisa si el vástago
   **sobresale** de la cara o queda con menos de `min_wall` (1,2 mm) de pared.
5. **Volver a la posición automática** descarta lo elegido. **Mirar la cara de frente** vuelve la cámara a su
   lugar si orbitaste. **Esc** o **Terminar colocación del pasador** sale del modo y **conserva** lo elegido.

Lo que elegís se guarda en el proyecto (`pin_center` del miembro) y se usa al **generar**. Un punto naranja
marca en el visor los pasadores elegidos a mano. Cualquier cambio invalida el resultado: hay que generar de nuevo.
Si después **cambiás la silueta**, la cara de corte puede moverse: volvé a abrir el modo y revisá el pasador.
Al generar, el informe (`pin_margin_mm`) y la validación repiten el aviso si el vástago no entra en la cara.

En la **pierna**, el recorte redondeado del extremo de cadera sigue siendo el automático; mover el pasador no lo cambia.

**Plano de costado = cara de corte.** Si apoyás el plano de un miembro en una cara chata de costado (normal paralela a X, por
ejemplo la cara interna del muslo o la del hombro), ese plano es él mismo la cara donde va el pasador, y la silueta se extruye
**hacia afuera** (también en las piernas). Con una extrusión simétrica el plano atravesaría la pierna y la «cara de contacto» saldría
chica y equivocada: el asistente avisa si la cara es menor que el vástago. Los proyectos guardados con ese defecto se corrigen solos al
abrir el modo pasador (y se descarta el pasador elegido sobre la cara equivocada).

**Plano de trabajo del pasador.** (Los marcadores —borde, silueta, vástago, cabeza y centro— se dibujan siempre por encima de la geometría, así no quedan tapados.) Al entrar al modo pasador de un miembro que ya existe (de brazo o de pierna) el programa arma
internamente un plano **paralelo al de simetría**, puesto en el corte de ese miembro (la cara de contacto), y la cámara lo mira de frente.
No aparece en la lista de planos ni agrega ningún miembro: es solo la vista de trabajo. Sobre él se dibuja, en amarillo, la **silueta que
ya dibujaste** como referencia: si su plano es de costado se ve el contorno entero; si es frontal, se ve su proyección (el tramo de altura
que cubre). El clic y las flechas fijan el pasador sobre ese plano.

**Corte desplazado.** Si el plano de un miembro está sobre el de simetría (x = 0), el corte real va desplazado hacia afuera; el control
**Corte a (mm del plano de simetría)** fija ese desplazamiento para el miembro activo. En la **pierna** el valor por defecto es **7 mm**
(calibrado con Güemes, Belgrano y Azurduy con la figura a 100 mm); en el **brazo** hay que indicarlo (la cara del hombro de Güemes está
a ≈ 9,7 mm). Cambiar el desplazamiento mueve la cara de contacto, así que descarta el pasador elegido y, si estás en el modo pasador,
vuelve a calcular la cara. Ese plano no puede ser el de simetría con desplazamiento 0: esa cara es el corte de la mitad.

**Alcance del pasador.** Si la cara está muy cerca del plano de simetría, el pasador de un lado y el del espejo se cruzan (Güemes:
caras a ±5,28 mm, pasadores de 6,6 mm). Al entrar al modo, el Estado agrega «Aviso: cruza con el del otro lado por X mm» (una sola vez: no depende de dónde pongas el pasador sobre la cara). Se arregla acortando el pasador
(`shaft_len` / `head_h` de `hip_pin`: 2,2 + 1,65 mm alcanzan) o eligiendo un plano más hacia afuera.

### 7.2b Parámetros, perfil de línea y probetas
En la barra lateral, antes de «Generar», está la sección **Parámetros**: holgura miembro–cavidad, rebaba, ángulo de pose sentada y margen del límite de cadera, y las medidas de los pasadores de hombro y de cadera (Ø y largo del vástago, Ø y alto de la cabeza). Cualquier cambio invalida la figura generada: hay que **Generar** de nuevo.

- **Perfil de línea:** escribí un nombre y **Guardar** para dejar estos parámetros en `~/.config/afm/perfiles/`; en la próxima figura elegí el perfil y **Cargar**. Las siluetas y los planos no se tocan.
- **Probetas:** elegí el pasador (cadera u hombro) y **Generar probetas de articulación…**; se escribe `probetas.stl` (una placa con varias articulaciones, cada una con otra holgura y otro Ø de vástago) y `probetas.txt` con la tabla en el orden de la grilla. Imprimilas con tu impresora, elegí la de menor fricción que sostenga la pose, cargá esos valores en Parámetros y guardalos en el perfil.

### 7.2c Rodilla y codo (bisagras)

Después de dibujar la silueta de una pierna o de un brazo (y colocar su pasador de cadera u hombro) se puede
partir el miembro en dos con una bisagra print-in-place: lengüeta en ranura con dos bulones cortos, como la
figura de referencia. La pierna queda en **muslo + pantorrilla** y el brazo en **brazo + antebrazo**.

1. **«Agregar rodilla a una pierna (3 puntos)»** (o **«Agregar codo a un brazo (3 puntos)»**): elegí 3 puntos sobre la
   figura a la altura donde se dobla. **No hace falta que los 3 clics estén a la misma altura**: el plano puede quedar
   inclinado (un brazo que cuelga en diagonal se corta con un plano perpendicular a él) mientras no pase de 60° de la
   horizontal. Lo de arriba es el muslo / brazo. Aparece una fila amarilla («Rodilla» / «Codo») en la lista de planos y
   una línea amarilla con el eje. No confundas este botón con **«Agregar plano del cuello / cabeza»**, que está justo
   arriba: ese crea la fila «Cabeza (cuello)».
   **Alternativa para un brazo que sigue un trayecto: «Agregar codo siguiendo el brazo (2 líneas, de frente)».** Poné
   la vista «Frente» y hacé 4 clics sobre el centro del miembro: (1) arriba, en el centro del brazo; (2) en el codo, donde
   se dobla; (3) en el codo otra vez, en el mismo lugar; (4) abajo, en el centro del antebrazo. Son dos líneas: brazo
   (1→2) y antebrazo (3→4), y solo cuentan x y z. El corte pasa por el codo y sigue la **bisectriz** de las dos, así las
   piezas encajan a ras al estirar el miembro; el eje queda perpendicular a esa bisectriz y **sube desde el codo por la
   línea del brazo**, no en vertical, para quedar centrado. Mientras elegís, las dos líneas se ven **por delante de la figura** (la geometría no las tapa): la del brazo en
   **cian** (con el ángulo en el Estado apenas hay 2 puntos) y la del antebrazo en **magenta**, con un punto blanco en el
   codo. Al terminar quedan dibujadas junto al plano amarillo para que compruebes que siguen el miembro; si no te
   convencen, quitá la fila «Codo» (⊗) y repetí. El Estado informa cuánto se abre cada línea y la inclinación
   resultante. Funciona igual con la pierna (muslo y pantorrilla) desde el controlador; en la ventana por ahora solo
   hay botón para el codo. Esta forma solo mira la vista de frente: un quiebre de costado (adelante/atrás) no entra en
   las líneas. Como de frente se clickea la **cara delantera** del miembro, los 4 puntos se **centran solos en
   profundidad** (dos rayos a lo largo de Y); mirá las guías en la vista **«Costado»**: deben quedar dentro del brazo. Si
   no, usá el botón **«Ajustar guías de costado»** de la fila del codo (icono de espejo): 4 clics en el mismo orden (arriba
   del brazo, codo, codo, abajo del antebrazo) sobre el centro del miembro; se toman altura y profundidad de cada clic (la
   posición en X queda) y se recalculan el corte y el eje.
2. **Eje:** arranca a unos 3,2 mm del plano hacia arriba (lo mínimo para dejar pared alrededor del bulón), verticalmente
   sobre el centro de los 3 puntos, **inclinado como el plano** (el Estado dice cuántos grados). Con el botón del
   **marcador** de la fila hacé clic sobre el costado de la figura, en el centro de la bisagra: se toman la altura y la
   profundidad del clic (la posición en X se conserva: el eje atraviesa el miembro). El eje debe quedar al menos 0,5 mm
   arriba del plano.
3. **Los tres números de la fila:** flexión mínima (negativa = hiperextensión), flexión máxima e **inclinación del
   eje vista de frente** (° alrededor de Y; 0 = a lo largo de X; positiva = el eje sube hacia +X). Un brazo que cuelga
   en diagonal hacia afuera pide una inclinación positiva, y es lo que usa la figura de referencia: ≈ 14° en la rodilla
   y ≈ 9,5° en el codo. Normalmente no hace falta tocarla: ya sale del plano. Por defecto la rodilla va de 0° a 90° (la
   pantorrilla va hacia atrás) y el codo de −10° a 90° (el antebrazo va hacia adelante). **El tope no es una pieza
   aparte:** es el material del muslo / brazo que queda sin tallar fuera de ese rango.
4. **Lengüeta con cuello recto (por defecto):** vista a lo largo del eje, la lengüeta es una «U» invertida: lados rectos
   (ancho = 2 × radio) que bajan hasta el cuerpo y punta redonda alrededor del eje. Antes era un disco que solo tocaba
   el cuerpo en una cuerda corta (débil, y con un voladizo redondo que pedía soportes). En «Parámetros», «cuello recto» =
   0 vuelve al disco.
5. **Bastago pasante (por defecto):** la lengüeta del antebrazo lleva **un bastago soldado** que cruza un orificio del
   brazo y lo sujeta; las puntas quedan **ciegas** dentro del brazo, con `Pared tras el bastago` (1,2 mm) de material hacia
   afuera (0 = orificio abierto de lado a lado). Si el brazo se afina junto al eje (por ejemplo, pegado al torso), el
   bastago se acorta de ese lado para que alrededor del orificio quede pared; el Estado avisa. En «Parámetros»,
   «bastago pasante» = 0 vuelve a los dos bulones cortos de antes.
6. **Bisagra en figuras delgadas:** en «Parámetros» están el **Ø del bulón**, el **largo del bulón**, el **espesor de la
   lengüeta** y el **radio de la lengüeta** de rodilla/codo. Con un brazo de unos 8 mm (Güemes) la bisagra por defecto
   deja la pared del bulón en menos de 0,8 mm (no se imprime): probá Ø bulón 2,0 (el mínimo), largo 1,0, lengüeta 2,0 y
   radio 3,0. Cambiarlos invalida la figura: hay que generar de nuevo.
7. **Generar figura:** el informe de validación revisa el giro libre dentro del rango, el tope fuera de él, la holgura,
   que la pieza no se pueda sacar, la pared alrededor de los bulones y el recorrido de toda la cadena (cadera × rodilla)
   contra el torso y las demás piezas. La **pose sentada en vivo** (§7.5) ya muestra la pierna con su rodilla.
8. **Probetas:** en «Perfil de línea» elegí «Bisagra de rodilla / codo» y «Generar probetas de articulación…».

Una articulación por miembro. Quitar el miembro (o la fila amarilla) la quita. En un brazo, el motor **talla en el
torso** el barrido del antebrazo al flexionar el codo; el informe trae `torso_tallado_mm3` con el material quitado (0 si
el brazo cuelga separado del torso). *(Esta parte de la ventana no se pudo probar con GTK real; la lógica sí tiene tests.)*

### 7.3 Generar y exportar

Con la simetría definida y cada miembro con su silueta, el Estado deja de listar pendientes y se puede:

1. **Generar figura:** construye torso, brazos y piernas (cavidades, pasadores, barrido de cadera,
   espejado) y valida el resultado. **Tarda** (de decenas de segundos a varios minutos según la figura); la
   ventana sigue viva, el progreso aparece en el Estado y la barra lateral queda bloqueada hasta terminar.
2. Al terminar, el Estado resume la validación: piezas, **holgura mínima** contra el objetivo, recorrido
   libre de la cadera y avisos del soldado. Si hay **problemas** (interpenetración, holgura corta,
   interferencia en el giro) los lista.
3. **Exportar piezas…:** elegís una carpeta y se escriben `*_ensamblada.stl`, un STL por pieza en
   `piezas/`, el informe `*_informe.json` y el proyecto. Con la casilla **Apoyar las piezas
   en la cama** todas se exportan **tal como se ven en «Ver la figura generada en la pose de cadera»** (sentada, cabeza
   arriba, sin darla vuelta), solo bajadas hasta z = 0 y con la misma transformación, para que sigan ensambladas en el
   laminador. Con **«Modificar para imprimir sin soportes»** (debajo de esa casilla), antes de exportar se corta con un
   plano horizontal a **1 mm sobre el punto más bajo de la cola** (valor en «Parámetros»: «Sin soportes: corte plano sobre
   la cola») para que **piernas y cola queden chatas**, y los **brazos giran hacia adelante** sobre el pasador del hombro
   lo justo para que su punto más bajo apoye en ese plano (un brazo que cuelga sube al girar). El torso se talla con el
   barrido del brazo para que gire libre. No se tocan los brazos; la cabeza tampoco. El Estado informa el corte, el giro
   (por ejemplo, 23°) y los avisos (choques, piezas partidas, aristas no variedad). Cuanto más corte, más base plana pero
   más material quitado de las piernas. En la línea de comandos: `afm build … --sin-soportes [MM]`. Para **ver y fijar la
   altura del corte**, con la figura generada, el botón **«Ajustar la altura del corte»** muestra la figura **sentada y de
   costado (vista Derecha)**, como en «Ver la figura generada en la pose de cadera», con el plano horizontal dibujado:
   un **clic** a la altura que querés (se toma solo la altura), o **↑ ↓** (Mayús = 1 mm), fijan los mm sobre la cola; Esc
   termina. El rango es el de «Parámetros» (0,2–6 mm) y ajustarlo no obliga a generar de nuevo. La vista de la pose
   y el marcado de voladizos todavía no muestran el resultado del corte (piernas aplanadas, brazos girados). Los **voladizos** (casilla de la barra lateral) usan esa misma orientación: con la figura generada se pintan
   en rojo sobre la vista de la pose de cadera (si estaba apagada, se enciende sola; una pieza que interfiere se
   marca en magenta mientras estén los voladizos); sin generar, sobre la figura de frente, cabeza arriba. En la línea de
   comandos, `--orient` hace lo mismo y `--orient-auto` conserva la orientación de menor voladizo (puede dar vuelta la
   figura).

Cualquier cambio posterior (una silueta, un plano, la simetría) **invalida el resultado**: hay que
generar de nuevo antes de exportar.

### 7.4 Recortes manuales

Para los ajustes propios de cada figura (la «esfera panza» de Mickey, una cola, un faldón que estorba) hay **una sola herramienta**, la misma para todos los casos: una forma → extrusión → **restar** o **unir** sobre una pieza. En la barra lateral, sección **Recortes manuales**:

1. Elegí la **Pieza** (el torso o un miembro), la **Forma** y la **Operación** (restar / unir).
2. **Polígono:** «Dibujar polígono / colocar con clic» pasa a la vista de costado sobre el **plano de simetría**; clic = vértice, cerrá con un clic en el primero o con Intro (igual que una silueta, §7.1: clic en un vértice y otro clic lo mueve, clic sobre un borde agrega uno, Retroceso borra). **Extrusión (mm)** es el espesor: el polígono se extruye hacia ambos lados del plano.
3. **Esfera, caja o cilindro:** elegí el **Radio / arista** y hacé clic en la figura donde va el centro (el clic del lado −X se pasa al +X). La esfera y el cilindro usan ese radio (el cilindro tiene el eje en X y el largo de la **Extrusión**); la caja es un cubo de esa arista.
4. El recorte aparece en la lista (rojo = restar, verde = unir, también dibujado en el visor): el lápiz edita los vértices de un polígono, la flecha cambia entre restar y unir, el tachito lo quita.

Los recortes se aplican al **lado de trabajo (+X)** antes del espejado, así que salen iguales en los dos lados, y se guardan en el `.proyecto`. Cambiar o quitar uno invalida la figura generada. Ojo: un recorte **tiene que llegar a la superficie** de la pieza; si queda totalmente adentro deja un hueco cerrado y el programa lo descarta (no hay ningún efecto). Para ver el resultado hay que **Generar** de nuevo.

### 7.5 Pose sentada en vivo (y colisiones)

Después de **Generar**, la casilla **Ver la figura generada en la pose de cadera** (sección «Espejo y pose sentada») reemplaza el visor por las piezas armadas: el torso con sus cavidades y los cuatro miembros (los de la derecha son el espejo de los de la izquierda). El **Ángulo de cadera** gira las piernas sobre el eje del pasador: 0° = de pie, 90° = sentada (arranca en el ángulo de la pose sentada de los parámetros).

* Las piezas que **chocan con el torso o entre sí** a ese ángulo se pintan de **rojo** y el Estado dice cuáles y por cuántos mm³.
* El Estado avisa cuando el ángulo pasa del **límite de cadera** tallado en el torso (ángulo de la pose sentada + margen): más allá de ahí es normal que la pierna choque; si choca *antes* de ese límite, hay un problema de silueta o de pasador.
* Cualquier edición invalida la figura generada y la vista previa se cierra; generá de nuevo para volver a verla.

## 8. Lista de planos

Debajo de los botones aparece la lista de planos creados: **Simetría** y uno por cada miembro.

* La **casilla** muestra u oculta el plano en el visor.
* El **tachito** lo quita. Quitar la **Simetría** quita también todos los miembros, porque dependen
  de ella.

## 9. Trabajar un solo lado y espejo en vivo

Con la simetría definida, **Trabajar un solo lado (oculta x < 0)** esconde la mitad izquierda de
la figura para ver mejor el lado en el que se trabaja. Es solo una ayuda visual.

**Espejo en vivo** (sección «Espejo y pose sentada») muestra en ese lado oculto el reflejo de lo que
hacés en el lado +X, con un tinte azul para distinguirlo: la figura, las siluetas y el 2.º corte (también
el polígono mientras lo dibujás), los pasadores elegidos y los recortes. Es lo que va a salir al generar,
que construye un solo lado y lo espeja. Al activarlo se pasa a trabajar un solo lado; si volvés a ver
los dos lados se apaga. Durante la colocación del pasador no se dibuja (el plano de recorte ocupa ese lugar).

## 10. Guardar y reabrir

* **Guardar proyecto…** escribe un archivo `.proyecto` (JSON) con la simetría, los planos, la
  coronilla, el frente y los parámetros, más la ruta absoluta del STL de origen.
* **Abrir proyecto…** lo recarga. Si el STL ya no está en esa ruta, la aplicación lo avisa; abrilo
  desde la terminal con `afm-gui proyecto.proyecto --stl nueva/ruta.stl`.

## 11. Recetas rápidas

**Figura nueva que ya mira al frente y viene centrada**

1. Abrir STL → Frente: verificar que se ve de cara (si no, −Y).
2. Marcar coronilla (si lleva sombrero).
3. Simetría automática.
4. Agregar plano de brazo → 3 clics. Agregar plano de pierna → 3 clics.
5. Guardar proyecto.

**Figura levemente girada o descentrada**

1. Abrir STL → Frente.
2. Plano de simetría (3 puntos) sobre la línea central, en lugar de la automática.
3. Seguir desde el paso 4 de la receta anterior.

## 12. Si algo no anda

| Síntoma | Causa y arreglo |
|---|---|
| `afm-gui` dice «Faltan dependencias… No module named 'gi'» | El entorno virtual se creó con un Python distinto del de `python3-gi`. Recrearlo con `/usr/bin/python3 -m venv --system-site-packages .venv` |
| `Namespace Gtk not available` | Falta `gir1.2-gtk-4.0` (Debian/Ubuntu: `sudo apt install gir1.2-gtk-4.0`) |
| Error de OpenGL al abrir | Hace falta OpenGL 3.3 o superior: revisá el driver de video y `libgl1` |
| «El clic no tocó la figura» | El clic cayó en el fondo; apuntá sobre la malla |
| «No sirve: …» al elegir 3 puntos | Puntos alineados (no hacen un plano) o, en la simetría, plano no vertical |
| Al dibujar la silueta el clic no agrega vértices («El plano se ve de canto…») | La cámara mira el plano de canto: usá la vista Frente o girá hasta verlo de cara |
| «…la silueta se cruza consigo mismo… no se guardó» | Mové o borrá el vértice que hace el cruce; mientras no sea válido, el proyecto conserva la silueta anterior |
| «No se puede colocar el pasador de …: dibujá primero la silueta» | El miembro todavía no tiene silueta (§7.1), o la silueta no deja una cara de contacto plana |
| «No se movió: el vástago no toca la cara de contacto…» | El clic cayó a más de un diámetro del borde de la cara azul; acercalo (si el centro queda apenas fuera, se acepta y el Estado lo anota) |
| El Estado dice que el vástago sobresale de la cara | La cara es más angosta que el vástago: movelo hacia la parte más ancha, achicá el vástago (`shoulder_pin` / `hip_pin` en los parámetros) o corré la silueta para que la cara quede más ancha |
| «…el polígono se cruza consigo mismo: corregilo (no se guardó el recorte)» | Igual que con las siluetas: mové o borrá el vértice que hace el cruce |
| Un recorte no cambia nada al generar | Quedó totalmente dentro de la pieza (hueco cerrado, se descarta): llevalo hasta la superficie, o revisá que la pieza elegida sea la que lo contiene |
| La pose sentada no se puede ver | Primero hay que generar la figura; si se edita algo después hay que generar de nuevo |
| La coronilla no se deja marcar | Ya hay simetría o miembros; quitalos primero |
| La figura se ve de espaldas en «Frente» | Cambiar «Frente hacia» a −Y (borra los planos) |

## Lo que falta en la interfaz

Esta versión de la ventana **define planos, dibuja las siluetas, coloca los pasadores, hace recortes manuales, genera la figura,
la muestra en la pose de cadera con sus colisiones, exporta las piezas y guarda el proyecto** (también puede mostrar los
**voladizos** de impresión en rojo sobre la figura entera, o sobre la pose de cadera si ya está generada, con la casilla de la barra lateral). Todavía no:

* ofrece un control gráfico para el recorte redondeado de la cadera (se cambia con `cap_*` en el proyecto);
* acierta solo la altura de la cadera cuando hay abrigo o faldón largo (la silueta propuesta se corrige a mano);
* muestra los voladizos sobre las piezas ya cortadas una por una (sí sobre la pose de cadera generada), ni la pose sentada **sin** haber generado antes;
* detecta solo las láminas finas / rebabas de la malla (se quitan con un recorte).

La línea de comandos sigue disponible (útil para procesar sin ventana):

```bash
afm build figura.stl figura.proyecto --out out/figura --orient
```

Instalación y resumen de una página: [`manual_corto.md`](manual_corto.md). El orden previsto para lo que sigue está en [`PROGRESS.md`](../PROGRESS.md).
