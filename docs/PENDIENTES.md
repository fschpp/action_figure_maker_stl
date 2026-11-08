# Qué falta hacer (2026-11-08)

Estado: el plan de compatibilidad (`PLAN_compatibilidad_cortes_catalogo.md`) tiene hechas las fases 0 a 7 en el motor y la
lógica de la interfaz. Lo que sigue son pruebas con figuras reales, decisiones tuyas y cosas que solo se pueden probar en la
ventana del programa.

## 1. Lo que depende de vos

- [ ] **Probar `afm ajustar-cintura-muneca` con San Martín** después de `desde-patron`:
      `afm ajustar-cintura-muneca RUTA/sanmartin_nuevo.proyecto RUTA/sanmartin.stl --catalogo CATALOGO` (sin `--guardar` no
      modifica nada); pasame el texto que imprime. Con Güemes como patrón el catálogo tiene que tener su pelvis y su mano.
- [ ] **Probar `afm desde-patron` con San Martín** (y después con Belgrano), usando Güemes como patrón:
      `afm desde-patron RUTA/guemes.proyecto RUTA/sanmartin.stl --out RUTA/sanmartin_nuevo.proyecto`
      Abrir la imagen `*_revision.png` y decir si los planos (cintura, cuello, rodillas, codos, muñeca) caen bien. Con el
      sombrero o las plumas, agregar `--corona`; con abrigo largo, `--entrepierna`.
- [ ] **Decidir la tolerancia de los talles.** Con ±0,4 mm cada figura queda en su propio talle de hombro; con ±0,9 mm los
      tres hombros (Güemes, Belgrano, San Martín) caben en un solo talle M (7,51 mm). Falta saber si piezas con esa diferencia
      realmente se arman.
- [ ] **Correr con tus figuras reales:**
      `afm catalogo certificar RUTA` y `python tools/dejar_uno_afuera.py CATALOGO --figuras CARPETA`
      (cada figura como `<figura>.stl` más `<figura>.proyecto` en la carpeta). Pasar las salidas.
- [ ] **Torso de Azurduy:** no es estanco (4 aristas abiertas), así que la capacidad no se puede medir ahí. Repararlo en el
      modelo o aceptarlo así.
- [ ] **Probar en la ventana real (GTK)** lo que no se puede abrir fuera de tu equipo: botones «Ajustar» / «Ver compatibles» /
      «Detalle» de la guía de cortes, listas de «Armar desde catálogo…» con talle y certificación, semáforo de «Cortar la
      mano…», sección del tutorial 7.6.

## 2. Lo que puedo hacer yo (sin que intervengas)

- [x] **Ajustar cintura y muñeca contra el patrón** (2026-11-08, `src/afm/ajuste.py`, `afm ajustar-cintura-muneca`): mueve
      el plano de la cintura y el de la muñeca hasta igualar el radio de la pelvis y de la mano del patrón y dice la escala que
      sobra. **Falta probarlo con tus figuras reales** (ver sección 1).
- [x] **Escalar la pelvis** con la escala restante (2026-11-08): `afm ajustar-cintura-muneca --guardar --escalar-pelvis` o la
      casilla de la ventana escriben `part_scales["pelvis"]` = inversa de la escala del héroe. **La mano no se reescala** en el
      pipeline (`HandSpec`): si la muñeca no calza, solo se puede mover el plano o cambiar de mano.
- [ ] **Conectar `desde-patron` a la ventana:** botón «Nuevo héroe desde el patrón…» que muestre la imagen de revisión.
- [ ] **Capacidad en pelvis y cabeza:** hoy se juzga la profundidad para brazos y piernas al generar; falta `place_pelvis` y el
      cuello.
- [ ] **Certificar torso, pelvis y mano** (hoy solo brazo, pierna y cabeza).
- [ ] **Pared sobre la lengüeta del clic y raíz de la lengüeta** respecto del plano de simetría (§4.4 del plan).
- [ ] **Pieza adaptadora** (si el ajuste automático no alcanza con héroes de proporciones muy distintas): un anillo con medidas
      fijas, iguales a las de Güemes, que se pega al torso del héroe.

## 3. Datos a tener en cuenta

- Con tres figuras (Güemes, Belgrano, San Martín) la métrica «equivalente» da 3 talles de hombro y 2 de cadera; la «inscrita»
  da 3 y 3 y más problemas. Se queda **«equivalente»**.
- La cadera de las piernas de Belgrano y San Martín (r 2,5–2,9 mm) queda bajo el piso físico de 3,70 mm. No importa mientras
  reutilices las piernas de Güemes: lo que cruza entre figuras es la cintura y la muñeca.
- Los números de las compatibilidades son geométricos (mm del modelo). Las holguras reales de impresión solo se conocen
  imprimiendo y midiendo con calibre.
- La extrusión «symmetric» de las figuras reales hace que `--ajustar` (mover el plano de corte) no cambie nada; lo que cambia
  el tamaño de la cara es el recorte cilíndrico (`cap_radius`), que usa `--estandar`.

## 4. Pruebas de impresión pendientes (de notas anteriores)

- [ ] Imprimir la placa de probetas del clic y ver qué dentado sujeta sin romper la lengüeta (PETG recomendado).
- [ ] Probar el proyecto real que daba 70 MB y ver qué pieza pesa (`triangulos_reducidos` en el informe).
- [ ] Volver a catalogar Belgrano con el proyecto correcto si sus piezas se hicieron con el proyecto viejo.

El detalle técnico y el historial están en `PROGRESS.md`.
