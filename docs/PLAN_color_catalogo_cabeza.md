# Plan: color en piezas de catálogo y cabeza reemplazada con su propio FBX

**Versión:** 0.1 (borrador) — 2026-11-19
**Repo:** `fschpp/action_figure_maker_stl`, sobre `main` `f314f1c` (ubicar en `docs/PLAN_color_catalogo_cabeza.md`)
**Continúa:** `docs/PLAN_fbx_textura_color.md` (T01–T30). Las tareas de este plan siguen la numeración: **T31–T37**.
**Marcas:** **[C]** dicho por el cliente · **[V]** verificado leyendo el código de `main` · **[I]** inferido, a validar · **[P]** pendiente de aclarar.

---

## 1. Qué se quiere

1. Que una pieza de catálogo pueda **llevar su propio color** (`PieceMeta.color`, opcional). **[C]**
2. Que el **armado desde el catálogo** (`figure_builder` / `figure_window`) pase `colorize` y entregue las piezas a color. **[C]**
3. Que una **cabeza reemplazada** venga con **su STL + su FBX con color** y salga coloreada con ese FBX, no con el de la figura base. **[C]**

## 2. Punto de partida (verificado en `main`)

- `Colorizer` tiene **una sola fuente** `self.src` para todas las piezas (`colorexport.py`, `__init__`). **[V]**
- `pipeline.export` llama `colorize(nombre, malla, res)` por cada pieza, con la malla en su pose y antes de la orientación de impresión. La cabeza es una pieza más (`"cabeza"`). **[V]**
- `register_piece` busca una transformación **rígida** (identidad, giros sobre el eje de la articulación, barrido). No maneja escala ni traslación libres. **[V]**
- Cabeza reemplazada: `HeadSpec.replacement_path` (STL suelto) pasa por `_oriented_replacement` (pre-escala a 30 mm, giros `up/rx/ry/yaw`, plano de corte) y `fit_replacement` (escala uniforme *k* y traslación a la base del cuello). Esa transformación **no se devuelve** hoy. **[V]**
- Cabeza de catálogo: `split.placed` es un `assembly.PlacedPart` con la matriz `T` (marco de la pieza → marco del huésped, en mm reales). **[V]**
- La cabeza **no se espeja** (`fit_replacement`: «no se espeja ni simetriza nada»): no hay pieza `cabeza_der`. **[V]**
- `PieceMeta` (`parts.py`) no tiene campo de color; `Catalog.add_piece` fija `id@version` inmutable y guarda `stl` + `sha256` junto al `.pieza.json`. **[V]**
- `figure_builder.py` / `figure_window.py` no mencionan `colorize`; el armado no genera GLB. **[V]**
- Una pieza de catálogo sin fuente sale `sin_origen` y de color plano `#C8C8C8` (`colorbake.DEFAULT_FLAT`). **[V]**

## 3. Idea de diseño

**Una fuente de color por pieza, siempre llevada al marco de la pieza en reposo.** Así `register_piece`, `unwrap` y `bake_piece` **no cambian**.

1. **Fuente por lugar.** `Colorizer(src, …, sources: dict[str, ColorSource] = None)`: para la pieza `nombre` usa `sources[clave]` si existe y, si no, `src`. La clave es el nombre sin sufijo `_izq/_der` (la misma que ya usa `seeds_for_piece`). **[I]**
2. **Llevar la fuente al marco del huésped, una sola vez.** Para una pieza que viene con su FBX se calcula la similitud `S` (escala + rotación + traslación) que lleva el FBX al marco de la pieza ya colocada y se **aplica a los vértices del `TexturedMesh`** (los UV no se tocan). Luego se registra con el mecanismo rígido de siempre. **[I]**
3. **`S` se compone de dos tramos:**
   - **FBX → STL de la pieza** (alineación de origen): se calcula al importar con el mismo método del modo B (`FigureFrame.from_similarity`, que ya alinea un FBX contra su STL). **[V]** existe para la figura entera; **[I]** sirve igual para una cabeza.
   - **STL de la pieza → marco del huésped:** para el catálogo, `PlacedPart.T`; para la cabeza importada suelta, `T_raw` de `_oriented_replacement` más *k* y la traslación de `fit_replacement` (hay que exponerlos).
4. **Si la composición exacta no calza** (reparación de la malla, corte de base), se **refina con ICP con escala** sobre puntos de la superficie; es el respaldo, no el camino principal. **[I]**
5. **Lo que no tiene origen** (base cortada, relleno de cavidad, pasador, agujeros reparados) cae en la política ya existente: `extend` por defecto, o color plano. **[V]**

## 4. Datos nuevos

### 4.1 `PieceMeta.color` (opcional, retrocompatible)
```json
"color": {
  "fuente": "cabeza-x@1.fbx",
  "sha256": "…",
  "similitud": [[…4×4…]],
  "texturas": {"base": "4096x4096"},
  "estado": "alineada"
}
```
- Ausente ⇒ la pieza se comporta como hoy. Los `.pieza.json` existentes cargan sin cambios. **[I]**
- El FBX vive junto al `.stl` de la pieza. **[P]** decidir si se copia entero (≈ 26 MB por pieza, con 4 texturas de 4096²) o si se guarda un paquete reducido solo con malla + UV + textura base a 2048² (necesita lector GLB, T29).
- Un cambio de color **no cambia la geometría**: no debería exigir una versión nueva de la pieza, pero `ref = id@version` es inmutable. **[P]** ¿versión nueva o campo editable sin versionar?

### 4.2 `HeadSpec` (cabeza importada suelta, fuera del catálogo)
Campos nuevos: `replacement_color_path: str | None`, `replacement_color_sha256: str | None`. Entran en `to_dict/from_dict` con valores por defecto, igual que `replacement_*`. **[I]**

## 5. Tareas

> Cada una con su criterio de aceptación. Las pruebas nuevas usan el muñeco texturizado de T06 (`doll_textured()`), sin necesitar FBX reales. Las que usan el FBX real llevan `skipif`.

### T31 · `Colorizer` con una fuente por pieza
- **Hacer:** parámetro `sources` y resolución por clave; el informe de color por pieza agrega `fuente` (`"figura"`, `"pieza"` o `"plano"`).
- **Aceptación:** sin `sources`, los bytes de salida y el `colorexport` actual no cambian (R3 del plan base); con `sources`, dos piezas del muñeco se hornean con fuentes distintas y cada una devuelve su color.

### T32 · Alinear un FBX contra el STL de una pieza y llevarlo al marco del huésped
- **Hacer:** función que, dado `TexturedMesh` + STL de la pieza + transformación al huésped, devuelve un `ColorSource` ya en el marco del huésped (`TexturedMesh.transformed(S)`).
- **Aceptación (sintética):** cabeza del muñeco escalada ×1,3, girada 30° sobre Z y trasladada, con su `TexturedMesh` transformada igual; el registro devuelve `identidad` o `semilla`, y `pct_origen` **≥ 85 %** (umbral propuesto **[I]**, a fijar con la medida real).

### T33 · Exponer la transformación de la cabeza importada
- **Hacer:** que `fit_replacement` incluya en su `info` la matriz 4×4 archivo→marco del cuello (`T_raw` × *k* × traslación). Solo agrega una clave al diccionario; la geometría no cambia.
- **Aceptación:** `test_head_replacement.py` y el golden pasan sin tocar; una prueba nueva aplica la matriz a los vértices del STL y compara contra la cabeza producida (distancia máxima ≤ 0,05 mm en la zona no cortada).

### T34 · Cabeza reemplazada con FBX (flujo suelto)
- **Hacer:** `HeadSpec.replacement_color_path`; `Session.make_colorizer` arma `sources["cabeza"]` con T32 + T33; la GUI de cabeza suma «Color de la cabeza (FBX)» con validación de hash y relocalización (como `project.color`).
- **Aceptación:** figura de prueba con cabeza importada + FBX: la pieza `cabeza` sale con el color de **su** FBX y el resto con el de la figura; el informe lista `cabeza: fuente = pieza`.

### T35 · `PieceMeta.color` y catalogar con FBX
- **Hacer:** campo opcional en `parts.PieceMeta` (`to_dict/from_dict`, `problems()`); `Catalog.add_piece(..., color_fbx=…)` calcula y guarda la similitud FBX→STL y el hash; el diálogo de catalogar ofrece «Agregar color (FBX)». Valida que el hash coincida al cargar.
- **Aceptación:** una cabeza catalogada con FBX recarga con `color` completo; todas las piezas de `catalogo/ejemplo` cargan con `color = None` y el catálogo pasa `verify` sin cambios.

### T36 · Armado desde el catálogo con color
- **Hacer:** `figure_builder` / `figure_window._start_build` arman el `Colorizer` con la fuente del huésped (si tiene `project.color`) más la de cada pieza elegida que traiga `PieceMeta.color`, usando `PlacedPart.T`; las piezas `_der` de miembros espejados se registran reflejadas (M = diag(−1, 1, 1)), como ya hace `seeds_for_piece`. Genera GLB para que aparezca el botón «Textura» del visor.
- **Aceptación:** armar una figura con cabeza catalogada a color + el resto de la figura base: GLB con las piezas; cabeza con el color de su FBX; piezas de catálogo sin color en `sin_origen` con `#C8C8C8` y marcadas en el informe.

### T37 · Pruebas con figuras reales y documentación
- **Hacer:** una cabeza real con su FBX (la que el cliente traiga) sobre Güemes; actualizar `docs/PLAN_fbx_textura_color.md` §7.8 (deja de ser «no se implementa»), `tutorial_gui.md` y `PROGRESS.md`.
- **Aceptación:** render comparativo (`tools/render_color_compare.py`) de la cabeza original contra la pieza horneada; medidas reales de `pct_origen`, tiempo por pieza y tamaño del atlas anotadas en `PROGRESS.md` (R8: no se relaja ningún umbral).

**Grafo:** T31 → T32; T33 → T34; T32 + T33 → T34; T31 + T32 → T35 → T36; T34 + T36 → T37.
Órdenes posibles: T31, T33 y T32 pueden empezar en paralelo.

## 6. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | La composición FBX→STL→huésped no calza (el STL de la cabeza no es exactamente la malla del FBX, o `_repaired` la modificó) | Alineación en el momento de importar (ya con el método del modo B); ICP con escala como respaldo; informe de `pct_origen` y del puntaje de registro |
| R2 | Pre-escala, giros y corte de base de `_oriented_replacement` dejan zonas nuevas sin origen | Política `extend` existente; la base y el pasador son superficies ocultas dentro del cuello |
| R3 | FBX de cabeza de otro proveedor con otra escala o ejes (Y arriba, `Lcl Scaling`) | Ya lo resuelve el lector FBX y `FigureFrame`; probar con un FBX real distinto de Güemes antes de cerrar T34 |
| R4 | Cada pieza con FBX pesa ≈ 26 MB y 4 texturas de 4096² | Cargar solo la textura base; decidir en §4.1 si se guarda un paquete reducido |
| R5 | La cabeza gira 0–90° libre sobre el cuello: el registro puede dar cualquier ángulo (pieza casi de revolución) | `TIE_MARGIN` ya cubre ese caso (T17); con la similitud conocida las semillas exactas bastan |
| R6 | Cambiar la fuente de una pieza altera la salida geométrica | No puede: el color solo se hornea al final; la prueba de regresión de T31 lo confirma byte a byte |
| R7 | Piezas de catálogo sin color mezcladas con color real se ven «apagadas» | Informe claro por pieza; color de relleno configurable (ya existe `--color-relleno`) |

## 7. Preguntas abiertas

1. **[P] Formato de guardado del color en el catálogo:** ¿FBX completo junto a la pieza o paquete reducido (malla + UV + textura base ≤ 2048²)? Define si T29 (lector GLB) pasa a ser requisito.
2. **[P] Versionado:** ¿agregar color a una pieza existente crea `@2` o edita la misma versión?
3. **[P] ¿El STL de la cabeza es exactamente la malla del FBX** (como en Güemes) o una versión re-malla/decimada? Si es la misma, la alineación es 1:1; si no, hay que medir la distancia entre ambas.
4. **[P] Alcance de T36:** ¿solo cabeza o también manos, brazos y piernas de catálogo con su FBX? El diseño de §3 sirve para todas; la v1 puede limitarse a la cabeza.
5. **[P] Impresión:** esto no resuelve cómo se imprime el color (T30 / P1, filamentos o pintado a mano). Las piezas salen con textura en OBJ/GLB, como el resto.
6. **[P] Estructura real de `src/afm/`:** este plan se escribió leyendo `parts.py`, `head.py`, `assembly.py`, `colorexport.py`, `colorreg.py` y `pipeline.export`. No leí `catalog.py` ni el flujo completo de `figure_builder.py`; hay que confirmar contra ellos los puntos de T35 y T36 antes de empezar.
