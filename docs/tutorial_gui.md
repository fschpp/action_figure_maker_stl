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
* **Barra lateral** (derecha): todos los controles, en cuatro secciones: Figura, Planos, Vista y Estado.
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

Para las figuras de ejemplo, ese plano es el **plano frontal** (el que separa frente de espalda; en el
tutorial de Onshape se llama «corte al medio»). Los planos de brazo se dibujan en azul y los de
pierna en verde.

**Cómo saber si quedó bien:** en la vista **Frente**, un plano frontal se ve **de cara**, como un
rectángulo translúcido que cubre la figura. Si lo ves como una línea o una cinta fina, está **de
canto** y no sirve para dibujar la silueta: borralo (tachito) y repetilo.

**Cómo obtenerlo con 3 clics:**

1. Poné la vista **Izquierda** (o Derecha). En esa vista el eje horizontal de la pantalla es la
   profundidad de la figura.
2. Hacé los 3 clics sobre la figura **en una misma línea vertical de la pantalla** (misma posición
   horizontal) y a distintas alturas: por ejemplo el costado de la cabeza, el del torso y el de la
   pierna. Los 3 puntos quedan a la misma profundidad y definen el plano frontal.
3. Volvé a la vista **Frente** y comprobá que el plano se ve de cara.

Si el programa responde «No sirve: los 3 puntos son colineales», cambiá uno de los clics a otra
altura o a otra parte del costado.

> Elegir puntos mirando la figura de frente (como en la primera prueba) da planos de canto: es el
> error más común.

## 8. Lista de planos

Debajo de los botones aparece la lista de planos creados: **Simetría** y uno por cada miembro.

* La **casilla** muestra u oculta el plano en el visor.
* El **tachito** lo quita. Quitar la **Simetría** quita también todos los miembros, porque dependen
  de ella.

## 9. Trabajar un solo lado

Con la simetría definida, **Trabajar un solo lado (oculta x < 0)** esconde la mitad izquierda de
la figura para ver mejor el lado en el que se trabaja. Es solo una ayuda visual.

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
| El plano de brazo/pierna se ve como una línea fina en la vista Frente | Está de canto: los puntos se eligieron mirando de frente. Rehacerlo desde la vista Izquierda (ver §7) |
| La coronilla no se deja marcar | Ya hay simetría o miembros; quitalos primero |
| La figura se ve de espaldas en «Frente» | Cambiar «Frente hacia» a −Y (borra los planos) |

## Lo que falta en la interfaz

Esta versión de la ventana **define planos y guarda el proyecto**, pero todavía no:

* **dibuja la silueta** de cada miembro (el polígono que recorta brazo o pierna);
* tiene el botón **Generar** ni exporta las piezas;
* muestra los **voladizos** de impresión ni la pose sentada en vivo.

Mientras tanto, la silueta se escribe a mano en el proyecto (campo `polygon` de cada miembro: puntos
`[x, y, z]` sobre su plano, ver `examples/*.py`) y la figura se genera con la línea de comandos:

```bash
afm build figura.stl figura.proyecto --out out/figura --orient
```

El orden previsto para lo que sigue está en [`PROGRESS.md`](../PROGRESS.md).
