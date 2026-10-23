# Pelvis independiente y escala por pieza

## Uso en la GUI

1. Abrí el proyecto de la figura. Conservá sus planos y siluetas de brazo/pierna.
2. Para separar torso y pelvis, definí el plano de cintura. Sin él, el torso es una sola pieza.
3. En **Piezas de catálogo**, elegí una pelvis y dejá el torso en **(la del modelo)**. Se conservan también los brazos,
   las piernas y la cabeza originales, salvo los reemplazos que elijas expresamente. La pelvis se alinea por su puerto
   de cintura y las piernas se colocan en los puertos de cadera de la pelvis nueva.
4. **Dividir figura actual y ajustar piezas…** muestra las piezas crudas antes de generar. No hace falta abrir un
   catálogo para dividir y escalar.
5. En cada fila, fijá la **escala de la pieza del modelo** y pulsá **Aplicar**. Es un factor absoluto: `1` = original,
   `1.10` = 10 % mayor, `0.95` = 5 % menor. Podés cambiar torso, pelvis, brazo y pierna por separado. Los miembros
   mantienen el par espejado que usa el motor. La escala queda inactiva si elegís una pieza de catálogo en esa fila.
6. Pulsá **Revisar las piezas elegidas (sin generar)**. La vista previa y la compatibilidad se recalculan con las
   proporciones nuevas. **Ajustar sólo torso / pelvis** usa el radio de la cara de catálogo como referencia para
   escalar la toma del modelo, sin cambiar la altura global. Igualar radios no garantiza igual forma: revisá IoU y escalón.
7. Generá la figura y revisá la validación completa antes de exportar. **Restablecer escalas por pieza** elimina los
   ajustes y vuelve a usar la geometría original del STL.

Los ajustes se guardan en `part_scales` del `.proyecto`; reabrirlo reproduce las mismas proporciones desde el STL
original. «Catalogar esta figura» guarda sus piezas originales con estas escalas; como antes, no recataloga los reemplazos
seleccionados. Las medidas de pasadores, holguras y bisagras siguen en milímetros de impresión: se regeneran después del
escalado. Los puertos, planos de articulación y centros de codo/rodilla acompañan la pieza.

El escalado es uniforme en los tres ejes. Torso y pelvis se escalan alrededor de la cintura; el torso entero, alrededor
del cuello si existe (en caso contrario, alrededor del origen). Brazo y pierna se escalan alrededor de hombro y cadera.
La cabeza se conserva y se recoloca en el cuello nuevo. Un torso de catálogo sigue activando el armado completo desde
catálogo; en ese modo los ajustes del modelo quedan inactivos.

La escala no garantiza un encastre imprimible: puede crear interferencias, dejar escalones o separar fragmentos de una
pieza. El programa conserva los controles de pared, componentes soldados y giro libre y registra los problemas.

## CLI

Los proyectos guardados en la GUI funcionan con `afm build`. También se pueden fijar escalas desde la línea de comandos:

```bash
afm build figura.stl figura.proyecto --out out/ajustada \
  --catalogo /ruta/catalogo --pieza pelvis=pelvis-ejemplo-01@1 \
  --escala-pieza torso=1.05 --escala-pieza brazo=0.98
```

Los nombres de brazo y pierna son los de `LimbSpec.name` en el proyecto. La pelvis independiente requiere cintura.
`ajustes_piezas` en el informe de construcción registra las escalas y qué lugares vienen del modelo o del catálogo.

## Alcance y validación

Se conserva el flujo previo para proyectos sin pelvis independiente ni ajustes por pieza. La extracción y el armado
mixto usan las operaciones de corte y puertos existentes, sin modificar el catálogo ni el STL de entrada. Por ahora se
admite un brazo y una pierna de trabajo, con su par por espejo, al dividir el modelo.

Las pruebas automáticas cubren la escala de mallas y puertos, preservación de articulaciones, pelvis desplazada en su
archivo de origen, conservación del torso y miembros, guardado/reapertura, CLI y detección de resultados incompatibles.
La GUI se prueba mediante Session/Controller sin depender de GTK; verificar visualmente el panel en el equipo del usuario.
