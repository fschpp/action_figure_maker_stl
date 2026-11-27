# Protocolo de la probeta de agarre mano / mango

Fase **física**: el motor genera las piezas, el par ganador sale de imprimirlas y probarlas a mano. No hace falta imprimir una mano
entera: la «mano de prueba» es un bloque con el mismo taladro y largo de canal que la mano estándar con agarre (`afm agarre`).

## Generar
`afm probe --tipo agarre --out out/agarre` →
- `probetas.stl` / `probetas.3mf`: **manos** (bloque con taladro pasante; número en relieve en una plaqueta delante) en una fila y
  **mangos** (paleta + guarda + empuñadura dentada; número en relieve sobre la paleta) en renglones debajo. El `.3mf` trae un objeto
  con nombre por pieza («mano 2 taladro 2.4», «mango 3 dentado 3.1»).
- `probetas.txt`: tabla de pares con interferencia por lado, holgura radial y deformación ε de la lengüeta (misma regla que la
  compatibilidad del catálogo, `props.grip_compat`) y su nivel ok / aviso / NO.
- `probetas.json`: lo que lee «Usar esta celda» y `afm probe-resultado`.

Por defecto: 3 manos (taladro de referencia 2,4 ±0,2) × 3 mangos (dentado = referencia + 0,3 / 0,5 / 0,7), vástago = taladro − 0,4,
ranura 0,5, lengüeta 8, canal de 8 mm. Opciones: `--taladros 2.2,2.4,2.6`, `--dentados 2.7,2.9,3.1`, `--vastago`, `--ranura`,
`--largo-lengueta`, `--largo-canal`, y `--mano MANO.stl` para partir del taladro y del canal de una mano ya taladrada. Hasta 6 manos y
6 mangos. Con Ø de taladro ≥ 2,6–2,8 el anillo de la mano de Güemes se abre: la prueba dice qué agarra, no si la mano real lo admite
(eso lo valida `afm agarre`).

## Imprimir
Con las mismas capas y boquilla que las figuras. Manos **paradas** (taladro vertical); mangos **acostados** (las lengüetas flexionan
en el plano de la cama).

## Probar y anotar (por par)
Empujar cada mango por la cara de la plaqueta de cada mano hasta que el dentado pase.

| par | entra sin forzar | baila | al tirar | lengüeta rota | notas |
|---|---|---|---|---|---|

Elegir el par que entra sin forzar, no baila y no se suelta solo.

## Después
- CLI: `afm probe-resultado out/agarre/probetas.json --mano N --mango M` imprime la línea de `afm agarre` con taladro, vástago, ranura,
  lengüeta y dentado del par.
- GUI: Parámetros → «Probetas de articulación» → «Agarre mano + mango», generar; luego elegir el par en la lista y «Usar esta celda»:
  carga esos valores en la sección «Agarre» (no cambia los parámetros de la figura ni el perfil).
