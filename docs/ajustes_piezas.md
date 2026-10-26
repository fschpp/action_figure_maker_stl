# Pelvis independiente y escala por pieza (uniforme o por eje X, Y, Z)

## Uso en la GUI

1. Abrí el proyecto de la figura. Conservá sus planos y siluetas de brazo/pierna.
2. Para separar torso y pelvis, definí el plano de cintura. Sin él, el torso es una sola pieza.
3. En **Piezas de catálogo**, elegí una pelvis y dejá el torso en **(la del modelo)**. Se conservan también los brazos,
   las piernas y la cabeza originales, salvo los reemplazos que elijas expresamente. La pelvis se alinea por su puerto
   de cintura y las piernas se colocan en los puertos de cadera de la pelvis nueva.
4. **Dividir figura actual y ajustar piezas…** muestra las piezas crudas antes de generar. No hace falta abrir un
   catálogo para dividir y escalar.
5. En cada fila, fijá la **escala de la pieza** con tres campos, **X** (ancho, lateral), **Y** (profundidad,
   adelante-atrás) y **Z** (alto), y pulsá **Aplicar**. Con «igual» marcado los tres ejes se mueven juntos (escala
   uniforme); desmarcalo para escalar cada eje por separado. Es un factor absoluto: `1` = original, `1.10` = 10 % mayor,
   `0.95` = 5 % menor. Podés cambiar torso, pelvis, cabeza, brazo y pierna por separado: cada pieza lleva su propia
   escala y no mueve a las demás. Los miembros mantienen el par espejado que usa el motor. La escala vale para la pieza que
   ocupa el lugar, sea la del modelo o la elegida del catálogo.
6. Pulsá **Revisar las piezas elegidas (sin generar)**. La vista previa y la compatibilidad se recalculan con las
   proporciones nuevas. **Ajustar sólo torso / pelvis** usa el radio de la cara de catálogo como referencia para
   escalar de forma uniforme la toma del modelo, sin cambiar la altura global. **Ajustar ejes (X, Y, Z)** mide el ancho y
   el alto de las dos caras y propone una escala distinta por eje para la pieza del modelo que lleva esa unión (el
   espesor no se toca). Igualar medidas no garantiza igual forma: revisá IoU y escalón.
7. Generá la figura y revisá la validación completa antes de exportar. **Restablecer escalas por pieza** elimina los
   ajustes y vuelve a usar la geometría original del STL.

Los ajustes se guardan en `part_scales` del `.proyecto`; reabrirlo reproduce las mismas proporciones desde el STL
original. «Catalogar esta figura» guarda sus piezas originales con estas escalas; como antes, no recataloga los reemplazos
seleccionados. Las medidas de pasadores, holguras y bisagras siguen en milímetros de impresión: se regeneran después del
escalado. Los puertos, planos de articulación y centros de codo/rodilla acompañan la pieza.

Cada pieza se escala alrededor de su unión principal, que queda fija: torso (arriba) y pelvis, alrededor de la cintura; el
torso entero y la cabeza, alrededor del cuello; brazo y pierna, alrededor de hombro y cadera. Torso, pelvis y cabeza son
simétricos, así que su escala en X se hace respecto del plano de simetría (x = 0) y no lo mueve. Después de escalar, las
demás piezas se vuelven a acoplar a los puertos nuevos (los brazos siguen al hombro del torso, las piernas a la cadera
de la pelvis, la cabeza al cuello), y los puertos, las caras de unión y los centros de rodilla/codo acompañan a su pieza.
Lo que NO se escala son las medidas de impresión: pasadores, holguras, lengüetas y bulones.

Ejemplo: Güemes → Belgrano. El brazo de Güemes pide ×2,3 y la pierna ×2,4 sobre la cara del torso de Belgrano, pero la
pelvis ya encaja con ×1,06. Antes sólo se podía escalar todo el modelo (uno de los lugares quedaba bien y los otros se
desarmaban). Ahora el torso (parte de arriba) se escala para el hombro, la pelvis para la cadera y la cabeza para el
cuello, cada una con sus propios X, Y, Z, y la unión de cintura entre torso y pelvis se mide aparte. Un torso de catálogo sigue activando el armado completo desde
catálogo; en ese modo los ajustes del modelo quedan inactivos.

La escala no garantiza un encastre imprimible: puede crear interferencias, dejar escalones o separar fragmentos de una
pieza. El programa conserva los controles de pared, componentes soldados y giro libre y registra los problemas.

## CLI

Los proyectos guardados en la GUI funcionan con `afm build`. También se pueden fijar escalas desde la línea de comandos,
un número (uniforme) o tres separados por coma, `FX,FY,FZ` (X lateral, Y adelante-atrás, Z alto):

```bash
afm build figura.stl figura.proyecto --out out/ajustada \
  --catalogo /ruta/catalogo --pieza pelvis=pelvis-ejemplo-01@1 \
  --escala-pieza torso=1,1.15,1 --escala-pieza cabeza=1.1 --escala-pieza brazo=0.98
```

Los nombres de brazo y pierna son los de `LimbSpec.name` en el proyecto. La pelvis independiente requiere cintura y la
cabeza, un plano de cuello. En el `.proyecto` queda en `part_scales`: un número (uniforme) o `[sx, sy, sz]`.
`ajustes_piezas` en el informe de construcción registra las escalas y qué lugares vienen del modelo o del catálogo.

## Alcance y validación

Se conserva el flujo previo para proyectos sin pelvis independiente ni ajustes por pieza. La extracción y el armado
mixto usan las operaciones de corte y puertos existentes, sin modificar el catálogo ni el STL de entrada. Por ahora se
admite un brazo y una pierna de trabajo, con su par por espejo, al dividir el modelo.

Las pruebas automáticas cubren la escala de mallas y puertos, preservación de articulaciones, pelvis desplazada en su
archivo de origen, conservación del torso y miembros, guardado/reapertura, CLI y detección de resultados incompatibles.
La GUI se prueba mediante Session/Controller sin depender de GTK; verificar visualmente el panel en el equipo del usuario.

## Armar desde catálogo: abrir receta, mano y escalas

En la ventana **Armar desde catálogo** (sin STL ni proyecto):

- **Abrir receta…** carga una `*.receta.json` (por defecto se busca en `<catálogo>/figuras/`): piezas, clic de hombro y cadera,
  ajustes de la mano y escalas. Tiene que ser de la línea del catálogo abierto; si una pieza ya no existe, ese lugar queda vacío y
  la ventana lo avisa. Retocá y usá «Guardar receta» con **otro nombre** (la receta abierta no se modifica sola).
- **Mano: giro y unión**: giro extra en grados alrededor de la muñeca, unión «solape» (la mano se hunde `solape` mm en el brazo y
  queda soldada) o «giro» (pieza aparte con pasador, gira libre).
- **Escala de cada pieza**: X (ancho), Y (profundidad) y Z (alto) por torso, pelvis, cabeza, brazo y pierna, con «igual» para escala
  uniforme. Se escala alrededor de la unión principal de cada pieza (igual que en el modelo) y las demás se acoplan a los puertos
  nuevos. La mano no se escala.

En la receta (`afm-receta-1`) queda así, y `afm armar` lo respeta:

```json
"ajustes": {
  "mano": {"yaw": 25, "union": "solape", "solape": 0.8},
  "escalas": {"brazo": [1, 1, 1.15], "torso": 1.04}
}
```
