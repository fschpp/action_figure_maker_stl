# Protocolo de probetas y banco de ciclos del clic (Fases 0 y 7 del plan de clic)

Estas fases son **físicas**: el motor genera las piezas, pero los números salen de imprimirlas y medirlas.

## Fase 0 — probetas
`afm probe --tipo clic --boquilla 0.4 --out out/clic` → `probetas_clic_b0.4_gap{0.2,0.3,0.4}.stl` (9 celdas cada una:
profundidad de muesca 0,6/0,8/1,0 × espesor de lengüeta 1,0/1,2/1,4) y `probetas_clic_b0.4.txt` con ε, fuerza y par estimados por celda.
Repetir con `--boquilla 0.8` (espesores 1,4/1,6/1,8). Imprimir con el eje del disco **horizontal** (como salen en la placa) y sin soportes.

Por celda anotar: (1) ¿se mueve tras el primer giro?; (2) ¿se funde punta/disco?; (3) ¿hace clic en los 4 puntos?;
(4) juego angular (marcar la palanca con un transportador: oscilación libre en cada clic); (5) par para salir del clic
(peso en la palanca a distancia conocida: par = peso × distancia; objetivo 10–60 N·mm).

## Fase 7 — banco de ciclos
5.000 ciclos por probeta (manual con contador, o servo simple): medir el par de retención al inicio, a 1.000 y a 5.000 ciclos.
Criterio del plan: caída ≤ 30 %. Con los resultados ajustar `finger_len`, `finger_t`, `notch_depth` y guardar el perfil (`clic_0.4`/`clic_0.8`).

| celda | ciclos | par (N·mm) | juego (°) | se funde | clic en 4 | notas |
|---|---|---|---|---|---|---|
