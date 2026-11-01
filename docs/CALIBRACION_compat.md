# Calibración de la compatibilidad barata contra la completa

_2026-11-01 · motor 0.2.0 · línea «prueba» (100 mm) · 24 combinaciones de una pieza cambiada, generado por `tools/calibrar_compat.py`._

Cada fila pone el brazo, la pierna o la cabeza de una figura (**donante**) en el torso de otra (**anfitrión**) y compara el nivel que da la compatibilidad barata (`port_compat`, por metadatos) con el resultado de armar la figura entera y validarla. «Pasa» = la completa no encontró problemas **nuevos** respecto de la receta propia del anfitrión.

## Línea base (cada figura con sus propias piezas)

| Anfitrión | Armado | Problemas | Avisos |
|---|---|---|---|
| azurduy_lq_segmentado.stl | sí | 0 | 4 |
| belgrano_lq_segmentado.stl | sí | 0 | 3 |
| guemes_segmentado_lq_fixed_resized.stl | sí | 0 | 1 |
| mickey_reescalado.stl | sí | 0 | 3 |

## Combinaciones

| Anfitrión | Lugar | Donante | Barata | IoU | Escalón (mm) | Completa | Problemas nuevos |
|---|---|---|---|---|---|---|---|
| azurduy_lq_segmentado.stl | brazo | belgrano_lq_segmentado.stl | aviso | 0,71 | 0,88 | pasa | — |
| azurduy_lq_segmentado.stl | brazo | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,51 | 1,95 | pasa | — |
| azurduy_lq_segmentado.stl | brazo | mickey_reescalado.stl | aviso | 0,48 | 1,48 | pasa | — |
| azurduy_lq_segmentado.stl | pierna | belgrano_lq_segmentado.stl | aviso | 0,89 | 0,34 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| azurduy_lq_segmentado.stl | pierna | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,53 | 1,22 | **falla** | torso: 2 componentes (debería ser 1, soldado); brazo_izq y pierna_izq se interpenetran; br |
| azurduy_lq_segmentado.stl | pierna | mickey_reescalado.stl | aviso | 0,54 | 2,01 | **falla** | torso: 2 componentes (debería ser 1, soldado); brazo_izq y pierna_izq se interpenetran; br |
| belgrano_lq_segmentado.stl | brazo | azurduy_lq_segmentado.stl | aviso | 0,71 | 0,88 | pasa | — |
| belgrano_lq_segmentado.stl | brazo | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,71 | 1,07 | pasa | — |
| belgrano_lq_segmentado.stl | brazo | mickey_reescalado.stl | aviso | 0,35 | 2,36 | pasa | — |
| belgrano_lq_segmentado.stl | pierna | azurduy_lq_segmentado.stl | aviso | 0,89 | 0,34 | pasa | — |
| belgrano_lq_segmentado.stl | pierna | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,54 | 0,89 | pasa | — |
| belgrano_lq_segmentado.stl | pierna | mickey_reescalado.stl | aviso | 0,61 | 1,67 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| guemes_segmentado_lq_fixed_resized.stl | brazo | azurduy_lq_segmentado.stl | aviso | 0,51 | 1,95 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | brazo | belgrano_lq_segmentado.stl | aviso | 0,71 | 1,07 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | brazo | mickey_reescalado.stl | aviso | 0,25 | 3,43 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | pierna | azurduy_lq_segmentado.stl | aviso | 0,53 | 1,22 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | pierna | belgrano_lq_segmentado.stl | aviso | 0,54 | 0,89 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | pierna | mickey_reescalado.stl | aviso | 0,50 | 0,79 | pasa | — |
| mickey_reescalado.stl | brazo | azurduy_lq_segmentado.stl | aviso | 0,48 | 1,48 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| mickey_reescalado.stl | brazo | belgrano_lq_segmentado.stl | aviso | 0,35 | 2,36 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| mickey_reescalado.stl | brazo | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,25 | 3,43 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| mickey_reescalado.stl | pierna | azurduy_lq_segmentado.stl | aviso | 0,54 | 2,01 | pasa | — |
| mickey_reescalado.stl | pierna | belgrano_lq_segmentado.stl | aviso | 0,61 | 1,67 | pasa | — |
| mickey_reescalado.stl | pierna | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,50 | 0,79 | pasa | — |

## Matriz de confusión por clase

Pasaron / total de las combinaciones con ese nivel barato, y el límite inferior de Wilson al 95 % (lo que usa la guía).

| Clase | Nivel barato | Pasaron | Total | Wilson inf. |
|---|---|---|---|---|
| cadera | aviso | 8 | 12 | 39 % |
| hombro | aviso | 9 | 12 | 47 % |

## Problemas nuevos más frecuentes

| Veces | Mensaje (números reemplazados por N) |
|---|---|
| 7 | brazo_izq y pierna_izq se interpenetran |
| 7 | brazo_der y pierna_der se interpenetran |
| 2 | torso: N componentes (debería ser N, soldado) |

## Lectura

- No hay combinaciones «ok» por la barata en esta muestra: no se puede medir cuánto vale un «ok».
- De 24 combinaciones «aviso», 17 pasaron la completa (71 %).
- La monotonía en la capacidad del anfitrión (§3 del plan) se verifica cuando haya medición de capacidad (Fase 3).
