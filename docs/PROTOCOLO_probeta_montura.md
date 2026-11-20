# Protocolo de la probeta de la montura de armas (M09 del plan de enganche)

Fase **física**: el motor genera las piezas, los números salen de imprimirlas y medirlas con calibre. Sirve además para cerrar M00
(medidas reales del puente y de la hoja).

## Generar
`afm probe --tipo montura --out out/montura` →
- `probeta_montura.stl`: placa plana con un puente `lazo` por celda (el mismo que `build_lazo` pone en la figura). Holguras por defecto
  0,15 / 0,25 / 0,35 / 0,45 mm por lado; la celda *n* lleva *n* puntos en relieve del lado de la punta. Hasta 5 celdas.
- `tira_hoja.stl`: hoja de prueba (4 × 0,8 mm) con guarda de 10 × 2 mm y una lengüeta de agarre.
- `probeta_montura.txt`: tabla celda → holgura → abertura (ancho × alto).

Opciones: `--clearances 0.2,0.3,0.4`, `--hoja ANCHO,ESPESOR` (la sección real de la hoja de la espada, medida con calibre) y `--pared MM`.

## Imprimir
Placa y tira **acostadas**, con las mismas capas y boquilla que la espada. El techo del puente cruza unos 4,5 mm: anotar si hizo falta soporte.

## Anotar (por celda)
1. ¿La tira (hoja primero) entra sin forzar, entra forzada o no entra?
2. Una vez dentro, ¿queda floja (se mueve a mano) o firme?
3. ¿La guarda apoya en la cara del puente (no pasa)?
4. Medidas reales con calibre: abertura (ancho × alto) y pared del puente. Compararlas con la tabla: la diferencia es el error de la impresora.

| celda | holgura (mm) | entra | juego | guarda apoya | abertura real (mm) | pared real (mm) | notas |
|---|---|---|---|---|---|---|---|

## Después
Elegir la celda donde entra sin forzar y no queda floja, y pasar su holgura como `holgura` de la montura. Si ninguna sirve, repetir con
`--clearances` más abiertas o más cerradas. Para la espada de Link, repetir con `--hoja` igual a la sección de su hoja (si es más gruesa
que ≈ 1,2 mm el puente deja de caber en el flanco: plan §10).
