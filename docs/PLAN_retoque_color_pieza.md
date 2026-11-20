# Plan: retoque manual del color de las piezas del catálogo (pinceladas por rol)

**Versión:** 0.1 (borrador) — 2026-11-20
**Repo:** `fschpp/action_figure_maker_stl`, sobre `main` @ `4ab6123`
**Continúa:** `docs/PLAN_paleta_color_linea.md` (T38–T51: roles, paleta de línea, paquete `.color.npz`). Las tareas de este plan son **R01–R10**.
**Marcas:** **[C]** dicho por el cliente · **[V]** verificado leyendo el código · **[I]** inferido, a validar · **[P]** pendiente de decidir.

---

## 1. Qué se quiere

1. **[C]** Poder corregir a mano el color de una pieza del catálogo donde la clasificación automática por roles falló (en la captura de Sucre: manchas negras, blancas y rojas sueltas junto a los botones, las medallas y el cuello del torso).
2. **[C]** El retoque **vale para la pieza del catálogo** (su versión `id@n`), no solo para la figura que se está armando: toda figura que use esa pieza lo hereda.
3. **[C]** Se pinta **solo con los roles de la paleta de la línea** (azul, dorado, piel…). No hay colores libres. Por eso un cambio de color de la paleta sigue recoloreando también lo retocado.

## 2. Alternativas que se descartaron

| Opción | Decisión | Por qué |
|---|---|---|
| Pintar «cara → rol» sobre las caras del STL | **No** | Las caras del STL cambian al armar (cavidades, cortes, subdivisión de la vista previa). El horneado no consulta caras del STL sino un `ColorSource` por posición |
| Editar el atlas de etiquetas (UV) | **No en v1** | El atlas es de la pieza, pero una isla UV mezcla zonas distantes, y no hay forma cómoda de pintar en UV desde la vista 3D |
| Editar RGB de la textura | **No** | Rompe la paleta: el color quedaría fijo en vez de seguir al rol |
| **Pinceladas en 3D: centro, radio, rol (+ normal)** | **Sí** | Vive en el marco del STL de la pieza, se transforma con la misma matriz que el color (`T · A`), funciona con el espejo del lado derecho y con el horneado sin tocarlo |

## 3. Diseño

### 3.1 Pincelada
`{"c": [x, y, z], "r": mm, "rol": "azul" | null, "n": [nx, ny, nz] | null}`

* `c`, `r`, `n` en el **marco del STL de la pieza** (mm reales). El orden de la lista es el orden de aplicación: la última gana.
* `rol` = id de la paleta de la línea. **`null` = borrador:** devuelve las muestras al rol que traía el paquete (sin retoque).
* `n` (normal de la superficie en el punto pintado) es opcional. Con `n`, solo se pintan las muestras cuya normal forma menos de ~72° con ella (`min_dot = 0,3`, igual que el horneado): el pincel **no atraviesa** una solapa fina y no pinta la cara de atrás. Sin `n`, pinta toda la esfera.
* Límites: radio 0,05–15 mm; hasta 20 000 pinceladas por pieza.

### 3.2 Cómo se aplica (sin tocar el bake)
`ColorSource` ya guarda por muestra la etiqueta de rol (`sample_label`, T41/T45) y `recolor(lut)` rehace el color sin rehacer el árbol. Se suma `ColorSource.paint(strokes, to_host)`:

1. lleva cada pincelada al marco de la fuente con la misma 4×4 que la fuente (`T · A` → solo `T`, porque la pincelada ya está en el marco del STL; para el lado derecho, `M · T`), el radio se escala por `∛|det|`;
2. busca las muestras dentro de la esfera (`cKDTree`), filtra por normal y escribe la etiqueta del rol (o la original, si es borrador);
3. recalcula `sample_rgb = lut[sample_label]`.

Para que un rol que el paquete no usa (p. ej. «azul» sobre una pieza que solo trae «piel») tenga etiqueta, `RolesPackage.with_roles(extra)` agrega al final los roles nuevos: las etiquetas existentes no cambian y la LUT los incluye. Con eso `Colorizer.recolor` (T45) también recolorea lo retocado.

### 3.3 Dónde se guarda
* `<id>@<v>.retoque.json` (`afm-retoque-1`) junto al STL y al `.color.npz`. Escritura atómica.
* Ficha: `color.retoque = {"fuente": "<id>@<v>.retoque.json", "sha256": "…", "n": 123, "roles": ["azul", "dorado_o_amarillo"]}` (aditivo: sin retoque, la ficha no cambia ni un byte).
* **Es lo único mutable del color de una pieza**, como el estado (`set_state`): el STL, el paquete y los puertos siguen inmutables. Una versión nueva de la pieza **no hereda** el retoque (igual que no hereda el color).
* Solo piezas en modo `roles`. Las `rgb` (FBX entero, T35) no se retocan.
* `Catalog.retouch(ref)`, `Catalog.set_retouch(ref, retouch | None)`; `verify` revisa archivo, hash y que los roles estén en la paleta; `palette_usage` cuenta también los roles retocados; el borrado/papelera mueve el archivo con la pieza.

### 3.4 Dónde se ve
* **Armado** (`colorpiece.catalog_color_sources`): el proveedor de cada pieza aplica el retoque después de armar la fuente; informe: `piezas[clave]["retoque"] = n`.
* **Vista previa de la ventana de Catálogo** (`colorpreview`): `color_parts(..., retouch=)`.
* **Recolor rápido y cambios de paleta por figura** siguen valiendo.

## 4. Herramienta de pincel (GUI, fase 2)

En la vista previa de «Administrar catálogo»: modo **Retocar**, selector de rol (los de la paleta, con su muestra de color), radio, **Pintar / Borrar**, **Deshacer/Rehacer** (pila de trazos; un trazo = muchas pinceladas separadas ≈ radio/2), «Descartar retoques», «Aplicar» (escribe el archivo). Necesita *picking* 3D (rayo → STL de la pieza → punto y normal) en el visor GL; la lógica de trazos y la pila de deshacer van en el controlador y se prueban sin GTK. **No se puede verificar en pantalla en el entorno de desarrollo (sin GTK/GL)**: se marca *no verificado* hasta que el cliente la pruebe.

## 5. Fichas de tarea

### Fase 1 — Datos y motor (sin GUI)

| # | Tarea | Archivos | Pruebas principales |
|---|---|---|---|
| **R01** | Modelo: `Stroke`, `Retouch` (validar, `to_dict/from_dict`, `save/load`, `roles_used`, `digest`) | `retouch.py` | formato, límites, ida y vuelta, rechazo de rol/forma inválidos |
| **R02** | `ColorSource.paint` y `RolesPackage.with_roles` | `colorsrc.py`, `colorroles.py` | pinta dentro del radio y no fuera; la normal impide atravesar; borrador restaura; el orden gana; transformación con escala/espejo; `recolor` sigue andando |
| **R03** | Catálogo: archivo, ficha, `verify`, papelera, `palette_usage`, CLI `afm catalogo retoque` | `catalog.py`, `parts.py`, `catalog_cli.py` | ficha sin retoque idéntica; `set_retouch` atómico; rol ausente en la paleta rechazado; hash alterado detectado; papelera y restaurar |
| **R04** | Armado: aplicar el retoque en `catalog_color_sources` (también `_der`) | `colorpiece.py` | la figura armada tiene el rol pintado; STL idénticos con y sin retoque; sin retoque, salida idéntica |
| **R05** | Vista previa: `color_parts(retouch=)`, `piece_color_parts` | `colorpreview.py` | la parte de color del rol retocado aparece; sin retoque, igual que antes |

### Fase 2 — GUI

| # | Tarea | Nota |
|---|---|---|
| **R06** | Controlador de retoque (trazos, pila deshacer, radio, rol) | probable sin GTK |
| **R07** | Picking en el visor y modo «Retocar» en la ventana de Catálogo | **no verificable aquí** |
| **R08** | Textos es/en/pt y auditoría de compatibilidad (fichas viejas no se reescriben al abrirlas) | `test_i18n` |

### Fase 3 — Cierre

| # | Tarea |
|---|---|
| **R09** | Caso real: Sucre (torso, brazo): retocar las manchas, armar y comparar con la vista previa; medir tiempo de `paint` con miles de pinceladas |
| **R10** | Documentación (`tutorial_gui.md`, `manual_corto.md`) y bitácora |

## 6. Reglas (heredan las de `PLAN_paleta_color_linea.md` §8)
Sin retoque, comportamiento y bytes de salida idénticos (R2); los **STL no cambian nunca por el color**; importaciones perezosas de `scipy`/`PIL` (R3); informes serializables (R5); semillas y orden deterministas (R10); la GUI sin GTK se marca no verificada (R9).

## 7. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| 1 | Pintar muchas pinceladas es lento (consulta al árbol de ~1,6 M de muestras por pincelada) | Límite de 20 000; `query_ball_point` por pincelada; medir en R09; si pesa, agrupar por rol y por celda |
| 2 | El pincel pinta «a través» de una solapa o cuello | Filtro por normal (`n`); R09 lo mira con Sucre |
| 3 | La muestra del FBX y el STL no coinciden exactamente (alineación 0,02–1,4 mm) | El radio absorbe la diferencia; el filtro de normal usa la del FBX |
| 4 | Una pieza retocada se rearma con una paleta sin ese rol | `PaletteRolesError` antes de armar (igual que los roles del paquete) |
| 5 | Retoque pegado a una versión: al clasificar de nuevo (versión nueva) se pierde | Es lo esperado (la versión nueva no hereda color); la ventana avisa cuántas pinceladas hay antes de reclasificar (R07) |
| 6 | Una pincelada guardada en el marco del STL no sirve si la pieza se reescala al armar | Las piezas `scaled` ya quedan sin color propio (T36); se informan como omitidas |

## 8. Preguntas abiertas
1. **[P]** ¿«Rellenar región» (todas las muestras conectadas del mismo rol que el punto)? Resolvería botones y medallas de un clic; es una segunda herramienta, después del pincel.
2. **[P]** ¿Querés poder **exportar/importar** retoques entre piezas (p. ej. del brazo izquierdo al derecho)? El derecho ya sale espejado del izquierdo.
3. **[P]** ¿Un retoque debe invalidar el estado `validada`/`impresa` de la pieza? Hoy el color no entra en ese estado.
