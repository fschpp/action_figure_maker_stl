# Calibración de la compatibilidad barata contra la completa

_2026-11-02 · motor 0.2.0 · línea «prueba» (100 mm) · 24 combinaciones de una pieza cambiada, generado por `tools/calibrar_compat.py`._

Cada fila pone el brazo, la pierna o la cabeza de una figura (**donante**) en el torso de otra (**anfitrión**) y compara el nivel que da la compatibilidad barata (`port_compat`, por metadatos) con el resultado de armar la figura entera y validarla. «Pasa» = la completa no encontró problemas **nuevos** respecto de la receta propia del anfitrión.

## Línea base (cada figura con sus propias piezas)

| Anfitrión | Armado | Problemas | Avisos |
|---|---|---|---|
| azurduy_lq_segmentado.stl | sí | 0 | 4 |
| belgrano_lq_segmentado.stl | sí | 0 | 3 |
| guemes_segmentado_lq_fixed_resized.stl | sí | 0 | 1 |
| mickey_reescalado.stl | sí | 0 | 3 |

## Combinaciones

| Anfitrión | Lugar | Donante | Barata | IoU | Escalón (mm) | Choque sentada (mm³) | Completa | Problemas nuevos |
|---|---|---|---|---|---|---|---|---|
| azurduy_lq_segmentado.stl | brazo | belgrano_lq_segmentado.stl | aviso | 0,71 | 0,88 | 0,0 | pasa | — |
| azurduy_lq_segmentado.stl | brazo | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,51 | 1,95 | 0,0 | pasa | — |
| azurduy_lq_segmentado.stl | brazo | mickey_reescalado.stl | aviso | 0,48 | 1,48 | 0,0 | pasa | — |
| azurduy_lq_segmentado.stl | pierna | belgrano_lq_segmentado.stl | no | 0,89 | 0,34 | 4,0 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| azurduy_lq_segmentado.stl | pierna | guemes_segmentado_lq_fixed_resized.stl | no | 0,53 | 1,22 | 1,2 | **falla** | torso: 2 componentes (debería ser 1, soldado); brazo_izq y pierna_izq se interpenetran; br |
| azurduy_lq_segmentado.stl | pierna | mickey_reescalado.stl | no | 0,54 | 2,01 | 24,9 | **falla** | torso: 2 componentes (debería ser 1, soldado); brazo_izq y pierna_izq se interpenetran; br |
| belgrano_lq_segmentado.stl | brazo | azurduy_lq_segmentado.stl | aviso | 0,71 | 0,88 | 0,0 | pasa | — |
| belgrano_lq_segmentado.stl | brazo | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,71 | 1,07 | 0,0 | pasa | — |
| belgrano_lq_segmentado.stl | brazo | mickey_reescalado.stl | aviso | 0,35 | 2,36 | 0,0 | pasa | — |
| belgrano_lq_segmentado.stl | pierna | azurduy_lq_segmentado.stl | aviso | 0,89 | 0,34 | 0,0 | pasa | — |
| belgrano_lq_segmentado.stl | pierna | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,54 | 0,89 | 0,0 | pasa | — |
| belgrano_lq_segmentado.stl | pierna | mickey_reescalado.stl | no | 0,61 | 1,67 | 3,7 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| guemes_segmentado_lq_fixed_resized.stl | brazo | azurduy_lq_segmentado.stl | aviso | 0,51 | 1,95 | 0,0 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | brazo | belgrano_lq_segmentado.stl | aviso | 0,71 | 1,07 | 0,0 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | brazo | mickey_reescalado.stl | aviso | 0,25 | 3,43 | 0,0 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | pierna | azurduy_lq_segmentado.stl | aviso | 0,53 | 1,22 | 0,0 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | pierna | belgrano_lq_segmentado.stl | aviso | 0,54 | 0,89 | 0,0 | pasa | — |
| guemes_segmentado_lq_fixed_resized.stl | pierna | mickey_reescalado.stl | aviso | 0,50 | 0,79 | 0,0 | pasa | — |
| mickey_reescalado.stl | brazo | azurduy_lq_segmentado.stl | no | 0,48 | 1,48 | 123,5 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| mickey_reescalado.stl | brazo | belgrano_lq_segmentado.stl | no | 0,35 | 2,36 | 158,9 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| mickey_reescalado.stl | brazo | guemes_segmentado_lq_fixed_resized.stl | no | 0,25 | 3,43 | 117,9 | **falla** | brazo_izq y pierna_izq se interpenetran; brazo_der y pierna_der se interpenetran |
| mickey_reescalado.stl | pierna | azurduy_lq_segmentado.stl | aviso | 0,54 | 2,01 | 0,0 | pasa | — |
| mickey_reescalado.stl | pierna | belgrano_lq_segmentado.stl | aviso | 0,61 | 1,67 | 0,0 | pasa | — |
| mickey_reescalado.stl | pierna | guemes_segmentado_lq_fixed_resized.stl | aviso | 0,50 | 0,79 | 0,0 | pasa | — |

## Matriz de confusión por clase

Pasaron / total de las combinaciones con ese nivel barato, y el límite inferior de Wilson al 95 % (lo que usa la guía).

| Clase | Nivel barato | Pasaron | Total | Wilson inf. |
|---|---|---|---|---|
| cadera | aviso | 8 | 8 | 68 % |
| cadera | no | 0 | 4 | 0 % |
| hombro | aviso | 9 | 9 | 70 % |
| hombro | no | 0 | 3 | 0 % |

## Problemas nuevos más frecuentes

| Veces | Mensaje (números reemplazados por N) |
|---|---|
| 7 | brazo_izq y pierna_izq se interpenetran |
| 7 | brazo_der y pierna_der se interpenetran |
| 2 | torso: N componentes (debería ser N, soldado) |

## Lectura

- No hay combinaciones «ok» por la barata en esta muestra: no se puede medir cuánto vale un «ok».
- De 7 combinaciones «no», 0 pasaron la completa (0 %).
- De 17 combinaciones «aviso», 17 pasaron la completa (100 %).
- El choque brazo–pierna en la pose sentada (`guide.SeatedClash`, sin armar la figura) anticipó 24 de 24 resultados de la completa (0 falsas alarmas, 0 fallas que no vio).
- La monotonía en la capacidad del anfitrión (§3 del plan) se verifica cuando haya medición de capacidad (Fase 3).
