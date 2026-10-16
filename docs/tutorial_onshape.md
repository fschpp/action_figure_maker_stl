# auto act fig
Creado martes 13 octubre 2026

PASO 001) hago plano a partir de 3 puntos, tomados sobre dos en la parte trasera del cuello y uno sobre punto debajo del menton en el torso, en el stl:
![](./auto_act_fig/pasted_image_261013-001.png)![](./auto_act_fig/pasted_image_261013-003.png)![](./auto_act_fig/pasted_image_261013-004.png)![](./auto_act_fig/pasted_image_261013-002.png)
PASO 002) sobre el plano "plano cabeza cuerpo" hago un circulo de radio 100mm con centro tomando el lugar donde iria el centro del cuello del stl

![](./auto_act_fig/pasted_image_261013.png)

PASO 003) sobre ese circulo con nombre "sketch corte cabeza", lo tomo como base de un Extrude tipo new, sobre face of sketch corte cabeza de tipo solido, de la altura suficiente para que cubra toda la cabeza (desde menton hasta la punta del sombrero) en este caso es de 35mm
![](./auto_act_fig/pasted_image_261013-005.png)

PASO 004) se hace una operacion boolean de tipo intersect entre el extrude del "cilindro corte cabeza" y el stl original, con opcion "keep tools", el resultado se renombra como "cabeza" que es la cabeza serparada de entero

![](./auto_act_fig/pasted_image_261013-006.png)
![](./auto_act_fig/pasted_image_261013-008.png)

PASO 005) a partir de sketch corte cabeza, se hace un new solid extrude con nombre "cilindro corte cuerpo" de un cilindro de 53.3mm  de tal manera que cubra el cilindro el volumen desde el cuello hasta los pies, el resultado 
![](./auto_act_fig/pasted_image_261013-009.png)

PASO 006)  se hace una operacion boolean de tipo intersect entre el extrude del "recorte cabeza cuerpo" y el stl original, con opcion "keep tools", el resultado se renombra como inicialmente como "cuerpo sin cabeza" pero en la imagen aparece como "torso atras izquierda"  que es el cuerpo separada del stl entero
![](./auto_act_fig/pasted_image_261013-010.png)
![](./auto_act_fig/pasted_image_261013-011.png)

PASO 007) el plano "plano brazo izquierdo" esta definido por un plano que pasa por tres puntos en el torso del stl:
![](./auto_act_fig/pasted_image_261013-023.png)![](./auto_act_fig/pasted_image_261013-019.png)![](./auto_act_fig/pasted_image_261013-020.png)![](./auto_act_fig/pasted_image_261013-022.png)

PASO 008)  sobre el plano "plano brazo izquierdo" creo un sketch con nombre "sketch corte brazo izq", usando lineas cierro un poligono que cumpla la condicion de que viendo desde el costado cubra totalemente el contorno del brazo izquierdo 
![](./auto_act_fig/pasted_image_261013-012.png)![](./auto_act_fig/pasted_image_261013-013.png)![](./auto_act_fig/pasted_image_261013-014.png)![](./auto_act_fig/pasted_image_261013-015.png)


PASO 009)  a partir de sketch "sketch corte brazo izq", se hace un new solid extrude con nombre "corte brazo izquierdo" sobre el poligono del sketch de 12mm de tal manera que el volumen cubra el todo el brazo viendo desde el brente y el costado izquierdo, el resultado es un nueva parte llamada "recorte brazo izq"
![](./auto_act_fig/pasted_image_261013-016.png)![](./auto_act_fig/pasted_image_261013-017.png)![](./auto_act_fig/pasted_image_261013-018.png)


PASO 010)  sobre la cara posterior del extrude de "recorte brazo izq" se la usa como plano de sketch para nuevo sketch con nombre "Sketch 1" en el mismo  se hacer un circulo que contenga el contorno del cuerpo y un poligono que siga aproximadamente el contorno del brazo y mano izquierdo y el torso del cuerpo para poder separar ambas partes
![](./auto_act_fig/pasted_image_261013-027.png)![](./auto_act_fig/pasted_image_261013-026.png)![](./auto_act_fig/pasted_image_261013-024.png)
![](./auto_act_fig/pasted_image_261013-028.png)

PASO 011) sobre parte del dibujo de "sketch 1"  lo tomo como base de un Extrude tipo remove de 25mm de la altura suficiente para que haga un corte ciego, sto olo hago para que al hacer el futuro boolean para separar no saque parte del torso de mickey (corte limpio sobre el hombro)
![](./auto_act_fig/pasted_image_261013-029.png)![](./auto_act_fig/pasted_image_261013-030.png)

PASO 012) se hace una operacion boolean de tipo intersect entre el extrude del "recorte brazo izq" (que es el cuerpo sin cabeza separada del stl entero), con opcion "keep tools", el resultado se renombra como "brazo izquiero" que es el brazo sin el cuerpo, y "Part 8" que es un pedacito suelto que no es util para el proceso
![](./auto_act_fig/pasted_image_261013-031.png)![](./auto_act_fig/pasted_image_261013-032.png)![](./auto_act_fig/pasted_image_261013-033.png)

PASO 013) se hace una operación de eliminación para borrar "Part 8" que fue un error y no lo necesitamos
![](./auto_act_fig/pasted_image_261013-034.png)

PASO 014) se hace un extrude con nombre "Extrude 1" de tipo remove de depth 0.4mm en dirección del plano hacia el brazo sobre la cara del brazo donde haría contacto con el cuerpo, se lo hace para eliminar la "rebaba" para que en vez de ser irregular el contatcto sea parecido a un circulo, el resultado es una pieza con mejor terminación pero se genera una parte nueva "Part 7" en amarillo que no la queremos tener
![](./auto_act_fig/pasted_image_261013-035.png)![](./auto_act_fig/pasted_image_261013-036.png)![](./auto_act_fig/pasted_image_261013-037.png)![](./auto_act_fig/pasted_image_261013-038.png)


PASO 015)  se selecciona el pedacito sobrante  "Part 7"  y se hace una operación de delete:
![](./auto_act_fig/pasted_image_261013-039.png)

PASO 016) para poder hacer la separacion de la pierna, se debe hacer primero el plano de tres punto "corte al medio" tomando los tres puntos al costado de la esfera de la cabeza que son tres puntos alineados![](./auto_act_fig/pasted_image_261013-041.png)![](./auto_act_fig/pasted_image_261013-042.png)
![](./auto_act_fig/pasted_image_261013-040.png)


PASO 017) sobre el plano "corte al medio" creo un sketch con nombre "sketch pierna izq", usando lineas cierro un poligono que cumpla la condicion de que viendo desde el frente cubra totalemente el contorno de la pierna incluyendo tambien parte de la cadera donde se va a hacer el agarre
![](./auto_act_fig/pasted_image_261013-043.png)![](./auto_act_fig/pasted_image_261013-045.png)![](./auto_act_fig/pasted_image_261013-046.png)

PASO 018) a partir de sketch "sketch pierna izq", se hace un new solid extrude con nombre "recorte pierna izquierda" sobre el poligono del sketch de 25mm simetrico de tal manera que el volumen cubra el todo la circunferencia de la cadera, se lo nombre "recorte pierna izquierda"
![](./auto_act_fig/pasted_image_261013-047.png)![](./auto_act_fig/pasted_image_261013-048.png)![](./auto_act_fig/pasted_image_261013-049.png)

PASO 019) se hace una operacion boolean de tipo intersect entre el extrude del "sketch pierna izq" (que es el cuerpo sin cabeza separada del stl entero), con opcion "keep tools", el resultado se renombra como "pierna izquierda con articulacion" que es el  la pierna sin el cuerpo, y "pierna geometria ok" que es un pedacito suelto que no es util para el proceso
 ![](./auto_act_fig/pasted_image_261013-050.png)![](./auto_act_fig/pasted_image_261013-051.png)![](./auto_act_fig/pasted_image_261013-053.png)![](./auto_act_fig/pasted_image_261013-054.png)

PASO 020)  se selecciona el pedacito sobrante "pierna geometria ok" y se hace una operación de delete:
![](./auto_act_fig/pasted_image_261013-055.png)

PASO 021) se hace una operacion boolean de tipo substract llamado "hacer hueco cadera en cuerpo" con tools pierna izquerda con articulacion y targets torso atras izquierda con un offset 0.1mm para dejar un espacio y la opcion de keep tools, el resultado es el cuerpo sin la pierna y la pierna sin cuerpo y un gap en el medio de los dos
![](./auto_act_fig/pasted_image_261013-056.png)![](./auto_act_fig/pasted_image_261013-057.png)![](./auto_act_fig/pasted_image_261014.png)

PASO 022) el siguiente paso es eliminar del cuerpo el brazo, se hace una operacion boolean con un substract llamada "sacar brazo del cuerpo", con toools brazo izquierdo y targets torso atras izquierda, con un offset en true, offset all y un offset distance de 0.3mm y la opcion keep tools, el resultado es el torso sin el brazo
![](./auto_act_fig/pasted_image_261014-002.png)![](./auto_act_fig/pasted_image_261014-003.png)![](./auto_act_fig/pasted_image_261014-004.png)

PASO 021) se define el plano "plano corte al medio simetria" a partir de 3 puntos del stl original, sobre una linea central donde hay simetria, dos en el lado de la espalda y el tercer punto en el pecho
![](./auto_act_fig/pasted_image_261014-006.png)![](./auto_act_fig/pasted_image_261014-007.png)![](./auto_act_fig/pasted_image_261014-008.png)![](./auto_act_fig/pasted_image_261014-009.png)
![](./auto_act_fig/pasted_image_261014-010.png)![](./auto_act_fig/pasted_image_261014-011.png)


PASO 022) tomando el plano "plano corte al medio simetria" se utiliza la herramienta Split con Part, con parts "torso atras izquierda" y keep tools para borrar la mitad del cuerpo para trabajar solo con la mitad del modelo y luego duplicar los cambios al otro lado con simetria en el futuro
![](./auto_act_fig/pasted_image_261014-012.png)![](./auto_act_fig/pasted_image_261014-013.png)![](./auto_act_fig/pasted_image_261014-014.png)![](./auto_act_fig/pasted_image_261014-015.png)

PASO  023) sobre el plano que se acaba de hacer eliminar la mitad del torso quedó una parte de la cola, con un poligono se dibuja en un sketch sobre la cara llamado "sketch para borrar cola"
![](./auto_act_fig/pasted_image_261014-017.png)
![](./auto_act_fig/pasted_image_261014-016.png)


PASO  024) a partir de sketch "sketch para borrar cola", se hace un new solid extrude con nombre "tool para sacar cola" sobre el poligono del sketch de 2mm de tal manera que el volumen cubra el todo el volumen de la cola 
![](./auto_act_fig/pasted_image_261014-018.png)![](./auto_act_fig/pasted_image_261014-019.png)

PASO  025) sobre la cara de sacar cola se hace un extrude de tipo solid add blind de 2mm para sacar la cola del torso
![](./auto_act_fig/pasted_image_261014-021.png)![](./auto_act_fig/pasted_image_261014-022.png)![](./auto_act_fig/pasted_image_261014-023.png)


PASO 026) se hace un boolean con nombre "boolean para sacar la cola" tipo subtract con tool: "recorte cola" y targets "torso atras izquierda" y keep tools en true,
![](./auto_act_fig/pasted_image_261014-024.png)![](./auto_act_fig/pasted_image_261014-025.png)![](./auto_act_fig/pasted_image_261014-026.png)![](./auto_act_fig/pasted_image_261014-027.png)


PASO 027) se hace un sketch nuevo sobre el plano formado por el hueco de la pierna en la parte de la pierna, formado por un circulo de diametro que cubra el ancho del pantalon y centrado para que el circulo este completamente dentro de la pierna, esto se lo hace para darle prolijad a la pierna como se hizo con PASO 014) con el brazo izquierdo
![](./auto_act_fig/pasted_image_261014-031.png)


PASO 028) se hace un extrude de solid remove con nombre "recorte pierna ingreso cadera" con caras la parte superior del no circulo del PASO 027) blind de 25mm hacia afuera 
![](./auto_act_fig/pasted_image_261014-033.png)![](./auto_act_fig/pasted_image_261014-034.png)![](./auto_act_fig/pasted_image_261014-035.png)![](./auto_act_fig/pasted_image_261014-036.png)![](./auto_act_fig/pasted_image_261014-037.png)![](./auto_act_fig/pasted_image_261014-038.png)

![](./auto_act_fig/pasted_image_261014-039.png)

PASO 029) se hace un split con nombre "separar torso en dos" de tipo part, con part: "torso atras izquierda" y entity to split with "corte al medio", keep tools en true, keep both sides = true, esto se hace para poder trabajar mejor en los huecos que se va a hacer sobre torso, las nuevas partes se llaman "torso atras izquierda" y "torso adelante unido"
![](./auto_act_fig/pasted_image_261014-040.png)

PASO 030) se hace un sketch sobre la parte lisa donde va el brazo en torso, eligiendo vertices del recorte del stl para encontrar el centro para hacer el conector, y tomando la interseccion para hacer un circulo de diametro 2.5mm
![](./auto_act_fig/pasted_image_261014-050.png)

PASO 031) se hace un extrude con nombre "extrude base brazo" de tipo solid new,  sobre el cirulo del sketch del PASO 030) hacia adentro del torso con una profundidad de 1.5mm, la nueva parte se le da el nombre articulacion brazo derecho
![](./auto_act_fig/pasted_image_261014-051.png)


PASO 032) sobre el plano del extrude del PASO031) se hace un sketch de 4mm con nombre "sketch base conector brazo"
![](./auto_act_fig/pasted_image_261014-052.png)

PASO 033) se hace un extrude sobre el circulo de 4mm centrado en el cilindro del paso anterior, con una altura de 1mm en solid add 
![](./auto_act_fig/pasted_image_261014-048.png)

PASO 034) se hace un paso similar a PASO0 30) pero en la cadera, se busca el centro, sobre ese centro se hace un circulo de 3mm con centro en la mitad del segmento de recta que va desde la base hacia afuera
![](./auto_act_fig/pasted_image_261014-053.png)
# auto act fig
Creado martes 13 octubre 2026

PASO 001) hago plano a partir de 3 puntos, tomados sobre dos en la parte trasera del cuello y uno sobre punto debajo del menton en el torso, en el stl:
![](./auto_act_fig/pasted_image_261013-001.png)![](./auto_act_fig/pasted_image_261013-003.png)![](./auto_act_fig/pasted_image_261013-004.png)![](./auto_act_fig/pasted_image_261013-002.png)
PASO 002) sobre el plano "plano cabeza cuerpo" hago un circulo de radio 100mm con centro tomando el lugar donde iria el centro del cuello del stl

![](./auto_act_fig/pasted_image_261013.png)

PASO 003) sobre ese circulo con nombre "sketch corte cabeza", lo tomo como base de un Extrude tipo new, sobre face of sketch corte cabeza de tipo solido, de la altura suficiente para que cubra toda la cabeza (desde menton hasta la punta del sombrero) en este caso es de 35mm
![](./auto_act_fig/pasted_image_261013-005.png)

PASO 004) se hace una operacion boolean de tipo intersect entre el extrude del "cilindro corte cabeza" y el stl original, con opcion "keep tools", el resultado se renombra como "cabeza" que es la cabeza serparada de entero

![](./auto_act_fig/pasted_image_261013-006.png)
![](./auto_act_fig/pasted_image_261013-008.png)

PASO 005) a partir de sketch corte cabeza, se hace un new solid extrude con nombre "cilindro corte cuerpo" de un cilindro de 53.3mm  de tal manera que cubra el cilindro el volumen desde el cuello hasta los pies, el resultado 
![](./auto_act_fig/pasted_image_261013-009.png)

PASO 006)  se hace una operacion boolean de tipo intersect entre el extrude del "recorte cabeza cuerpo" y el stl original, con opcion "keep tools", el resultado se renombra como inicialmente como "cuerpo sin cabeza" pero en la imagen aparece como "torso atras izquierda"  que es el cuerpo separada del stl entero
![](./auto_act_fig/pasted_image_261013-010.png)
![](./auto_act_fig/pasted_image_261013-011.png)

PASO 007) el plano "plano brazo izquierdo" esta definido por un plano que pasa por tres puntos en el torso del stl:
![](./auto_act_fig/pasted_image_261013-023.png)![](./auto_act_fig/pasted_image_261013-019.png)![](./auto_act_fig/pasted_image_261013-020.png)![](./auto_act_fig/pasted_image_261013-022.png)

PASO 008)  sobre el plano "plano brazo izquierdo" creo un sketch con nombre "sketch corte brazo izq", usando lineas cierro un poligono que cumpla la condicion de que viendo desde el costado cubra totalemente el contorno del brazo izquierdo 
![](./auto_act_fig/pasted_image_261013-012.png)![](./auto_act_fig/pasted_image_261013-013.png)![](./auto_act_fig/pasted_image_261013-014.png)![](./auto_act_fig/pasted_image_261013-015.png)


PASO 009)  a partir de sketch "sketch corte brazo izq", se hace un new solid extrude con nombre "corte brazo izquierdo" sobre el poligono del sketch de 12mm de tal manera que el volumen cubra el todo el brazo viendo desde el brente y el costado izquierdo, el resultado es un nueva parte llamada "recorte brazo izq"
![](./auto_act_fig/pasted_image_261013-016.png)![](./auto_act_fig/pasted_image_261013-017.png)![](./auto_act_fig/pasted_image_261013-018.png)


PASO 010)  sobre la cara posterior del extrude de "recorte brazo izq" se la usa como plano de sketch para nuevo sketch con nombre "Sketch 1" en el mismo  se hacer un circulo que contenga el contorno del cuerpo y un poligono que siga aproximadamente el contorno del brazo y mano izquierdo y el torso del cuerpo para poder separar ambas partes
![](./auto_act_fig/pasted_image_261013-027.png)![](./auto_act_fig/pasted_image_261013-026.png)![](./auto_act_fig/pasted_image_261013-024.png)
![](./auto_act_fig/pasted_image_261013-028.png)

PASO 011) sobre parte del dibujo de "sketch 1"  lo tomo como base de un Extrude tipo remove de 25mm de la altura suficiente para que haga un corte ciego, sto olo hago para que al hacer el futuro boolean para separar no saque parte del torso de mickey (corte limpio sobre el hombro)
![](./auto_act_fig/pasted_image_261013-029.png)![](./auto_act_fig/pasted_image_261013-030.png)

PASO 012) se hace una operacion boolean de tipo intersect entre el extrude del "recorte brazo izq" (que es el cuerpo sin cabeza separada del stl entero), con opcion "keep tools", el resultado se renombra como "brazo izquiero" que es el brazo sin el cuerpo, y "Part 8" que es un pedacito suelto que no es util para el proceso
![](./auto_act_fig/pasted_image_261013-031.png)![](./auto_act_fig/pasted_image_261013-032.png)![](./auto_act_fig/pasted_image_261013-033.png)

PASO 013) se hace una operación de eliminación para borrar "Part 8" que fue un error y no lo necesitamos
![](./auto_act_fig/pasted_image_261013-034.png)

PASO 014) se hace un extrude con nombre "Extrude 1" de tipo remove de depth 0.4mm en dirección del plano hacia el brazo sobre la cara del brazo donde haría contacto con el cuerpo, se lo hace para eliminar la "rebaba" para que en vez de ser irregular el contatcto sea parecido a un circulo, el resultado es una pieza con mejor terminación pero se genera una parte nueva "Part 7" en amarillo que no la queremos tener
![](./auto_act_fig/pasted_image_261013-035.png)![](./auto_act_fig/pasted_image_261013-036.png)![](./auto_act_fig/pasted_image_261013-037.png)![](./auto_act_fig/pasted_image_261013-038.png)


PASO 015)  se selecciona el pedacito sobrante  "Part 7"  y se hace una operación de delete:
![](./auto_act_fig/pasted_image_261013-039.png)

PASO 016) para poder hacer la separacion de la pierna, se debe hacer primero el plano de tres punto "corte al medio" tomando los tres puntos al costado de la esfera de la cabeza que son tres puntos alineados![](./auto_act_fig/pasted_image_261013-041.png)![](./auto_act_fig/pasted_image_261013-042.png)
![](./auto_act_fig/pasted_image_261013-040.png)


PASO 017) sobre el plano "corte al medio" creo un sketch con nombre "sketch pierna izq", usando lineas cierro un poligono que cumpla la condicion de que viendo desde el frente cubra totalemente el contorno de la pierna incluyendo tambien parte de la cadera donde se va a hacer el agarre
![](./auto_act_fig/pasted_image_261013-043.png)![](./auto_act_fig/pasted_image_261013-045.png)![](./auto_act_fig/pasted_image_261013-046.png)

PASO 018) a partir de sketch "sketch pierna izq", se hace un new solid extrude con nombre "recorte pierna izquierda" sobre el poligono del sketch de 25mm simetrico de tal manera que el volumen cubra el todo la circunferencia de la cadera, se lo nombre "recorte pierna izquierda"
![](./auto_act_fig/pasted_image_261013-047.png)![](./auto_act_fig/pasted_image_261013-048.png)![](./auto_act_fig/pasted_image_261013-049.png)

PASO 019) se hace una operacion boolean de tipo intersect entre el extrude del "sketch pierna izq" (que es el cuerpo sin cabeza separada del stl entero), con opcion "keep tools", el resultado se renombra como "pierna izquierda con articulacion" que es el  la pierna sin el cuerpo, y "pierna geometria ok" que es un pedacito suelto que no es util para el proceso
 ![](./auto_act_fig/pasted_image_261013-050.png)![](./auto_act_fig/pasted_image_261013-051.png)![](./auto_act_fig/pasted_image_261013-053.png)![](./auto_act_fig/pasted_image_261013-054.png)

PASO 020)  se selecciona el pedacito sobrante "pierna geometria ok" y se hace una operación de delete:
![](./auto_act_fig/pasted_image_261013-055.png)

PASO 021) se hace una operacion boolean de tipo substract llamado "hacer hueco cadera en cuerpo" con tools pierna izquerda con articulacion y targets torso atras izquierda con un offset 0.1mm para dejar un espacio y la opcion de keep tools, el resultado es el cuerpo sin la pierna y la pierna sin cuerpo y un gap en el medio de los dos
![](./auto_act_fig/pasted_image_261013-056.png)![](./auto_act_fig/pasted_image_261013-057.png)![](./auto_act_fig/pasted_image_261014.png)

PASO 022) el siguiente paso es eliminar del cuerpo el brazo, se hace una operacion boolean con un substract llamada "sacar brazo del cuerpo", con toools brazo izquierdo y targets torso atras izquierda, con un offset en true, offset all y un offset distance de 0.3mm y la opcion keep tools, el resultado es el torso sin el brazo
![](./auto_act_fig/pasted_image_261014-002.png)![](./auto_act_fig/pasted_image_261014-003.png)![](./auto_act_fig/pasted_image_261014-004.png)

PASO 021) se define el plano "plano corte al medio simetria" a partir de 3 puntos del stl original, sobre una linea central donde hay simetria, dos en el lado de la espalda y el tercer punto en el pecho
![](./auto_act_fig/pasted_image_261014-006.png)![](./auto_act_fig/pasted_image_261014-007.png)![](./auto_act_fig/pasted_image_261014-008.png)![](./auto_act_fig/pasted_image_261014-009.png)
![](./auto_act_fig/pasted_image_261014-010.png)![](./auto_act_fig/pasted_image_261014-011.png)


PASO 022) tomando el plano "plano corte al medio simetria" se utiliza la herramienta Split con Part, con parts "torso atras izquierda" y keep tools para borrar la mitad del cuerpo para trabajar solo con la mitad del modelo y luego duplicar los cambios al otro lado con simetria en el futuro
![](./auto_act_fig/pasted_image_261014-012.png)![](./auto_act_fig/pasted_image_261014-013.png)![](./auto_act_fig/pasted_image_261014-014.png)![](./auto_act_fig/pasted_image_261014-015.png)

PASO  023) sobre el plano que se acaba de hacer eliminar la mitad del torso quedó una parte de la cola, con un poligono se dibuja en un sketch sobre la cara llamado "sketch para borrar cola"
![](./auto_act_fig/pasted_image_261014-017.png)
![](./auto_act_fig/pasted_image_261014-016.png)


PASO  024) a partir de sketch "sketch para borrar cola", se hace un new solid extrude con nombre "tool para sacar cola" sobre el poligono del sketch de 2mm de tal manera que el volumen cubra el todo el volumen de la cola 
![](./auto_act_fig/pasted_image_261014-018.png)![](./auto_act_fig/pasted_image_261014-019.png)

PASO  025) sobre la cara de sacar cola se hace un extrude de tipo solid add blind de 2mm para sacar la cola del torso
![](./auto_act_fig/pasted_image_261014-021.png)![](./auto_act_fig/pasted_image_261014-022.png)![](./auto_act_fig/pasted_image_261014-023.png)


PASO 026) se hace un boolean con nombre "boolean para sacar la cola" tipo subtract con tool: "recorte cola" y targets "torso atras izquierda" y keep tools en true,
![](./auto_act_fig/pasted_image_261014-024.png)![](./auto_act_fig/pasted_image_261014-025.png)![](./auto_act_fig/pasted_image_261014-026.png)![](./auto_act_fig/pasted_image_261014-027.png)


PASO 027) se hace un sketch nuevo sobre el plano formado por el hueco de la pierna en la parte de la pierna, formado por un circulo de diametro que cubra el ancho del pantalon y centrado para que el circulo este completamente dentro de la pierna, esto se lo hace para darle prolijad a la pierna como se hizo con PASO 014) con el brazo izquierdo
![](./auto_act_fig/pasted_image_261014-031.png)


PASO 028) se hace un extrude de solid remove con nombre "recorte pierna ingreso cadera" con caras la parte superior del no circulo del PASO 027) blind de 25mm hacia afuera 
![](./auto_act_fig/pasted_image_261014-033.png)![](./auto_act_fig/pasted_image_261014-034.png)![](./auto_act_fig/pasted_image_261014-035.png)![](./auto_act_fig/pasted_image_261014-036.png)![](./auto_act_fig/pasted_image_261014-037.png)![](./auto_act_fig/pasted_image_261014-038.png)

![](./auto_act_fig/pasted_image_261014-039.png)

PASO 029) se hace un split con nombre "separar torso en dos" de tipo part, con part: "torso atras izquierda" y entity to split with "corte al medio", keep tools en true, keep both sides = true, esto se hace para poder trabajar mejor en los huecos que se va a hacer sobre torso, las nuevas partes se llaman "torso atras izquierda" y "torso adelante unido"
![](./auto_act_fig/pasted_image_261014-040.png)

PASO 030) se hace un sketch sobre la parte lisa donde va el brazo en torso, eligiendo vertices del recorte del stl para encontrar el centro para hacer el conector, y tomando la interseccion para hacer un circulo de diametro 2.5mm
![](./auto_act_fig/pasted_image_261014-050.png)

PASO 031) se hace un extrude con nombre "extrude base brazo" de tipo solid new,  sobre el cirulo del sketch del PASO 030) hacia adentro del torso con una profundidad de 1.5mm, la nueva parte se le da el nombre articulacion brazo derecho
![](./auto_act_fig/pasted_image_261014-051.png)


PASO 032) sobre el plano del extrude del PASO031) se hace un sketch de 4mm con nombre "sketch base conector brazo"
![](./auto_act_fig/pasted_image_261014-052.png)

PASO 033) se hace un extrude sobre el circulo de 4mm centrado en el cilindro del paso anterior, con una altura de 1mm en solid add 
![](./auto_act_fig/pasted_image_261014-048.png)

PASO 034) se hace un paso similar a PASO0 30) pero en la cadera, se busca el centro, sobre ese centro se hace un circulo de 3mm con centro en la mitad del segmento de recta que va desde la base hacia afuera
![](./auto_act_fig/pasted_image_261014-053.png)

PASO 035) se hace un extrude solid new sobre el cilindro del paso anterior con un alto de 2.5mm

![](./auto_act_fig/pasted_image_261014-054.png)

PASO 036) sobre la superficie de la tapa del cilindro se hace un sketch con un circulo de circunferencia 4mm con centro en el centro del cilindro
![](./auto_act_fig/pasted_image_261014-055.png)

PASO 037) se hace un extrude solid add blind de 1.5mm
![](./auto_act_fig/pasted_image_261014-056.png)

PASO 038) Se ahueca el torso para que entre la articulacion de la cadera, por medio de un boolean substract de pierna izquierda a targets (torso atras izquierda, torno adelante unido) con offset true, offset all, de 0.3mm con keep tools true
![](./auto_act_fig/pasted_image_261014-057.png)


PASO 035) se hace un extrude solid new sobre el cilindro del paso anterior con un alto de 2.5mm

![](./auto_act_fig/pasted_image_261014-054.png)

PASO 036) sobre la superficie de la tapa del cilindro se hace un sketch con un circulo de circunferencia 4mm con centro en el centro del cilindro
![](./auto_act_fig/pasted_image_261014-055.png)

PASO 037) se hace un extrude solid add blind de 1.5mm
![](./auto_act_fig/pasted_image_261014-056.png)

PASO 038) Se ahueca el torso para que entre la articulacion de la cadera, por medio de un boolean substract de pierna izquierda a targets (torso atras izquierda, torno adelante unido) con offset true, offset all, de 0.3mm con keep tools true
![](./auto_act_fig/pasted_image_261014-057.png)

