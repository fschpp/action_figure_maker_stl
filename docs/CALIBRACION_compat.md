# Calibración de la compatibilidad barata contra la completa

_2026-11-07 · motor 0.2.0 · línea «prueba» (100 mm) · 40 combinaciones de una pieza cambiada, generado por `tools/calibrar_compat.py`._

Cada fila pone el brazo, la pierna o la cabeza de una figura (**donante**) en el torso de otra (**anfitrión**) y compara el nivel que da la compatibilidad barata (`port_compat`, por metadatos) con el resultado de armar la figura entera y validarla. «Pasa» = la completa no encontró problemas **nuevos** respecto de la receta propia del anfitrión.

## Línea base (cada figura con sus propias piezas)

| Anfitrión | Armado | Problemas | Avisos |
|---|---|---|---|
| azurduy_lq_segmentado.stl | sí | 0 | 4 |
| belgrano_lq_segmentado.stl | sí | 0 | 3 |
| guemes_segmentado_lq_fixed_resized.stl | sí | 0 | 1 |
| mickey_reescalado.stl | sí | 0 | 3 |
| sanmartin.stl | sí | 0 | 3 |

## Combinaciones

| Anfitrión | Lugar | Donante | Barata | IoU | Escalón (mm) | Choque sentada (mm³) | Capacidad (mm) | Completa | Problemas nuevos |
|---|---|---|---|---|---|---|---|---|---|
| azurduy_lq_segmentado.stl | brazo | belgrano_lq_segmentado.stl | aviso | 0,71 | 0,88 | 0,0 | sin dato | pasa | — |
| azurduy_lq_segmentado.stl | brazo | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,51 | 1,95 | 0,0 | sin dato | pasa | — |
| azurduy_lq_segmentado.stl | brazo | mickey_reescalado.stl | aviso | 0,48 | 1,48 | 0,0 | sin dato | pasa | — |
| azurduy_lq_segmentado.stl | brazo | sanmartin.stl | aviso | 0,58 | 1,37 | 0,0 | sin dato | pasa | — |
| azurduy_lq_segmentado.stl | pierna | belgrano_lq_segmentado.stl | no | 0,89 | 0,34 | 4,0 | sin dato | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| azurduy_lq_segmentado.stl | pierna | guemes_segmentado_lq_fixed_resized.stl | no | 0,53 | 1,22 | 1,2 | sin dato | **falla** | torso: 2 componentes (debería ser 1, soldado); brazo_izq y pierna_izq se interpenetran; br |
| azurduy_lq_segmentado.stl | pierna | mickey_reescalado.stl | no | 0,54 | 2,01 | 24,9 | sin dato | **falla** | torso: 2 componentes (debería ser 1, soldado); brazo_izq y pierna_izq se interpenetran; br |
| azurduy_lq_segmentado.stl | pierna | sanmartin.stl | aviso | 0,86 | 0,08 | 0,0 | sin dato | **falla** | torso: 2 componentes (debería ser 1, soldado) |
| belgrano_lq_segmentado.stl | brazo | azurduy_lq_segmentado.stl | aviso | 0,71 | 0,88 | 0,0 | prof. +17.5 | pasa | — |
| belgrano_lq_segmentado.stl | brazo | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,71 | 1,07 | 0,0 | prof. +17.5 | pasa | — |
| belgrano_lq_segmentado.stl | brazo | mickey_reescalado.stl | aviso | 0,35 | 2,36 | 0,0 | prof. +17.5 | pasa | — |
| belgrano_lq_segmentado.stl | brazo | sanmartin.stl | ok | 0,82 | 0,49 | 0,0 | prof. +17.5 | pasa | — |
| belgrano_lq_segmentado.stl | pierna | azurduy_lq_segmentado.stl | aviso | 0,89 | 0,34 | 0,0 | prof. +7.0 · cabezas +1.1 | pasa | — |
| belgrano_lq_segmentado.stl | pierna | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,54 | 0,89 | 0,0 | prof. +7.0 · cabezas +1.1 | pasa | — |
| belgrano_lq_segmentado.stl | pierna | mickey_reescalado.stl | no | 0,61 | 1,67 | 3,7 | prof. +7.0 · cabezas +1.1 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| belgrano_lq_segmentado.stl | pierna | sanmartin.stl | aviso | 0,86 | 0,25 | 0,0 | prof. +7.0 · cabezas +1.1 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | brazo | azurduy_lq_segmentado.stl | aviso | 0,51 | 1,95 | 0,0 | prof. +19.5 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | brazo | belgrano_lq_segmentado.stl | aviso | 0,71 | 1,07 | 0,0 | prof. +19.5 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | brazo | mickey_reescalado.stl | aviso | 0,25 | 3,43 | 0,0 | prof. +19.5 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | brazo | sanmartin.stl | aviso | 0,77 | 0,58 | 0,0 | prof. +19.5 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | pierna | azurduy_lq_segmentado.stl | aviso | 0,53 | 1,22 | 0,0 | prof. +7.0 · cabezas +1.1 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | pierna | belgrano_lq_segmentado.stl | aviso | 0,54 | 0,89 | 0,0 | prof. +7.0 · cabezas +1.1 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | pierna | mickey_reescalado.stl | aviso | 0,50 | 0,79 | 0,0 | prof. +7.0 · cabezas +1.1 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | pierna | sanmartin.stl | aviso | 0,50 | 1,14 | 0,0 | prof. +7.0 · cabezas +1.1 | pasa | — |
| mickey_reescalado.stl | brazo | azurduy_lq_segmentado.stl | no | 0,48 | 1,48 | 123,5 | prof. +12.3 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| mickey_reescalado.stl | brazo | belgrano_lq_segmentado.stl | no | 0,35 | 2,36 | 158,9 | prof. +12.3 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| mickey_reescalado.stl | brazo | guemes_segmentado_lq_fixed_resized.stl | no | 0,25 | 3,43 | 117,9 | prof. +12.3 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| mickey_reescalado.stl | brazo | sanmartin.stl | no | 0,30 | 2,85 | 52,1 | prof. +12.3 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| mickey_reescalado.stl | pierna | azurduy_lq_segmentado.stl | aviso | 0,54 | 2,01 | 0,0 | prof. +9.7 · cabezas +3.8 | pasa | — |
| mickey_reescalado.stl | pierna | belgrano_lq_segmentado.stl | aviso | 0,61 | 1,67 | 0,0 | prof. +9.7 · cabezas +3.8 | pasa | — |
| mickey_reescalado.stl | pierna | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,50 | 0,79 | 0,0 | prof. +9.7 · cabezas +3.8 | pasa | — |
| mickey_reescalado.stl | pierna | sanmartin.stl | aviso | 0,54 | 1,93 | 0,0 | prof. +9.7 · cabezas +3.8 | pasa | — |
| sanmartin.stl | brazo | azurduy_lq_segmentado.stl | aviso | 0,58 | 1,37 | 0,0 | prof. +14.2 | pasa | — |
| sanmartin.stl | brazo | belgrano_lq_segmentado.stl | ok | 0,82 | 0,49 | 0,0 | prof. +14.2 | pasa | — |
| sanmartin.stl | brazo | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,77 | 0,58 | 0,0 | prof. +14.2 | pasa | — |
| sanmartin.stl | brazo | mickey_reescalado.stl | aviso | 0,30 | 2,85 | 0,0 | prof. +14.2 | pasa | — |
| sanmartin.stl | pierna | azurduy_lq_segmentado.stl | no | 0,86 | 0,08 | 0,0 | prof. -1.6 · cabezas +1.1 | pasa | — |
| sanmartin.stl | pierna | belgrano_lq_segmentado.stl | no | 0,86 | 0,25 | 0,0 | prof. -1.6 · cabezas +1.1 | pasa | — |
| sanmartin.stl | pierna | guemes_segmentado_lq_fixed_resized.stl | no | 0,50 | 1,14 | 0,0 | prof. -1.6 · cabezas +1.1 | pasa | — |
| sanmartin.stl | pierna | mickey_reescalado.stl | no | 0,54 | 1,93 | 0,0 | prof. -1.6 · cabezas +1.1 | pasa | — |

## Matriz de confusión por clase

Pasaron / total de las combinaciones con ese nivel barato, y el límite inferior de Wilson al 95 % (lo que usa la guía).

| Clase | Nivel barato | Pasaron | Total | Wilson inf. |
|---|---|---|---|---|
| cadera | aviso | 11 | 12 | 65 % |
| cadera | no | 4 | 8 | 22 % |
| hombro | ok | 2 | 2 | 34 % |
| hombro | aviso | 14 | 14 | 78 % |
| hombro | no | 0 | 4 | 0 % |

## Problemas nuevos más frecuentes

| Veces | Mensaje (números reemplazados por N) |
|---|---|
| 8 | brazo_izq y pierna_izq se interpenetran |
| 8 | brazo_der y pierna_der se interpenetran |
| 3 | torso: N componentes (debería ser N, soldado) |

## Lectura

- De 2 combinaciones «ok» por la barata, 0 fallaron en la completa (0 %).
- De 12 combinaciones «no», 4 pasaron la completa (33 %).
- De 26 combinaciones «aviso», 25 pasaron la completa (96 %).
- El choque brazo–pierna en la pose sentada (`guide.SeatedClash`, sin armar la figura) anticipó 39 de 40 resultados de la completa (0 falsas alarmas, 1 fallas que no vio).
- La capacidad del anfitrión (profundidad detrás de la cara, Fase 3) marcó que falta material en 4 combinaciones y 4 pasaron la completa: `validate` solo mira la pared delante de la cabeza y que las cabezas de cadera no se toquen, no que la cavidad atraviese la piel del torso. Con esos datos no se puede decir si la regla es demasiado estricta o si ve algo que `validate` no ve; hace falta mirar o imprimir esa zona.
- 8 combinaciones no tienen capacidad medida en la toma (pieza catalogada antes de la Fase 3 o torso no estanco, donde los rayos no cruzan el cuerpo): `afm catalogo capacidad --escribir` la mide.
